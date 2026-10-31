"""Gemini Pupper robot server: HTTP + WebSocket API on port 8000, and the web app.

Run on the Pupper after the robot stack is up (see start.sh):
    python3 robot/server.py

The web app gets Gemini's instructions and tools from GET /tools (lab/prompt.md
and the @tool functions in lab/) and sends every tool call to POST /tools/<name>. Every call
returns {"success": bool, "message": str}, which goes straight back to Gemini,
so Gemini hears about refused speeds, unknown tricks and failed controller
switches. The other endpoints are for the debug panel and for testing by hand.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import re
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import mirror
import tool_registry as gemini_tools
from robot_api import LIMITS, Robot

behaviors = gemini_tools.load_lab()  # lab/: every tool, and the behaviors they call

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("server")

REPO = Path(__file__).resolve().parent.parent
WEB_DIST = REPO / "web" / "dist"
PROMPT_FILE = REPO / "lab" / "prompt.md"
BATTERY_SCRIPT = Path("/home/pi/pupperv3-monorepo/robot/utils/check_batt_voltage.py")

tools: Robot | None = None


class Detections:
    """Forwards person detections (ZMQ on localhost:5556) to browser WebSockets.

    The follower publishes them while it follows (it keeps the Hailo off
    otherwise); the monorepo detector, when used instead, publishes them always.
    """

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def run(self) -> None:
        try:
            import zmq
            import zmq.asyncio
        except ImportError:
            log.warning("pyzmq missing; eye tracking from robot detections disabled")
            return
        sock = zmq.asyncio.Context().socket(zmq.SUB)
        sock.connect("tcp://127.0.0.1:5556")
        sock.subscribe(b"")
        while True:
            payload = (await sock.recv_multipart())[-1].decode("utf-8", errors="ignore")
            if payload.startswith("{"):
                for ws in list(self.clients):
                    try:
                        await ws.send_text(payload)
                    except Exception:
                        self.clients.discard(ws)


detections = Detections()

def make_hearing(robot: Robot):
    """The calibrated sound detector (hearing/listener.py). It starts off, and only the
    deaf_mode tool turns it on; what it hears goes to lab/deaf_mode.py."""
    try:
        sys.path.insert(0, str(REPO / "hearing"))
        from listener import Hearing
    except Exception as e:  # numpy/scipy/sounddevice missing: no deaf mode
        log.warning("hearing unavailable (%s); deaf mode can't start", e)
        return None
    return Hearing(on_event=lambda ev: behaviors.hearing_detected(robot, ev),
                   on_tick=lambda scores: behaviors.hearing_tick(scores))


def make_vision():
    """The camera check for collapse watch (vision/gemini_vision.py): needs the Gemini key."""
    sys.path.insert(0, str(REPO / "vision"))
    from gemini_vision import GeminiVision, api_key
    key = api_key()
    if key is None:
        log.warning("no Gemini key (GEMINI_API_KEY or ~/.config/gemini-pupper/api_key): collapse watch is off")
        return None
    return GeminiVision(key)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tools
    tools = Robot()
    tools.hearing = make_hearing(tools)
    tools.vision = make_vision()
    tasks = [asyncio.create_task(detections.run()), asyncio.create_task(behaviors.watch_for_collapse(tools))]
    yield
    for task in tasks:
        task.cancel()
    if tools.hearing is not None and tools.hearing.enabled:
        await tools.hearing.stop()


app = FastAPI(title="Gemini Pupper robot server", lifespan=lifespan)
# The web app is served from this same origin; also allow a dev server on the laptop.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


def T() -> Robot:
    if tools is None:
        raise HTTPException(503, "tool server not ready")
    return tools


# ------------------------------------------------------------------ models
class MoveVelocity(BaseModel):
    forward_backward_velocity: float = Field(..., description="m/s, positive = forward")
    right_left_velocity: float = Field(..., description="m/s, positive = right")
    turning_velocity: float = Field(..., description="deg/s, positive = turn right")
    duration: float = Field(..., description="seconds")


class MoveDirection(BaseModel):
    heading: float = Field(..., description="degrees, 0 = forward, 90 = right")
    speed: float
    duration: float


class Trick(BaseModel):
    animation_name: str


class TrickAction(BaseModel):
    action: str


class Wait(BaseModel):
    duration: float


class Volume(BaseModel):
    volume: int = Field(..., ge=0, le=150)


# ------------------------------------------------------------------ Gemini's tools
def system_prompt() -> str:
    """lab/prompt.md without its <!-- comments -->."""
    text = PROMPT_FILE.read_text() if PROMPT_FILE.exists() else ""
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()


@app.get("/tools")
def tool_list():
    """What Gemini is told: the system prompt and the tool declarations."""
    return {"prompt": system_prompt(), "tools": gemini_tools.declarations(T())}


@app.post("/tools/{name}")
async def tool_call(name: str, args: dict = Body(default={})):
    res = await gemini_tools.call(T(), name, args)
    log.info("tool %s(%s) -> %s: %s", name, args, "ok" if res.ok else "FAILED", res.message)
    return res.as_dict()


# ------------------------------------------------------------------ commands
@app.post("/walking/activate")
async def walking_activate():
    return (await behaviors.stand_up(T())).as_dict()


@app.post("/walking/deactivate")
async def walking_deactivate():
    return (await behaviors.lie_down(T())).as_dict()


@app.post("/move/velocity")
async def move_velocity(req: MoveVelocity):
    # The browser uses right/clockwise-positive; ROS uses left/counter-clockwise-positive.
    res = await behaviors.walk(T(), req.forward_backward_velocity, -req.right_left_velocity, -req.turning_velocity, req.duration)
    return res.as_dict()


@app.post("/move/direction")
async def move_direction(req: MoveDirection):
    turn = await behaviors.walk(T(), 0.0, 0.0, -90.0 if req.heading > 0 else 90.0, abs(req.heading) / 90.0) if req.heading else None
    if turn and not turn.ok:
        return turn.as_dict()
    return (await behaviors.walk(T(), req.speed, 0.0, 0.0, req.duration)).as_dict()


@app.post("/animation")
async def animation(req: Trick):
    return (await behaviors.play_trick(T(), req.animation_name)).as_dict()


@app.post("/interactive_action")
async def interactive_action(req: TrickAction):
    return (await behaviors.interactive_action(T(), req.action)).as_dict()


@app.get("/interactive_actions")
def interactive_actions():
    """Actions of interactive tricks (e.g. shake a paw while sitting): id -> trick and description."""
    return {aid: {"trick": t.name, "description": t.actions[clip]} for aid, (t, clip) in T().action_ids().items()}


@app.get("/animations")
def animations():
    return available_tricks()


def available_tricks() -> dict:
    """The tricks in <repo>/tricks, every one played by a policy."""
    return {
        name: {"description": t.description, "duration_s": round(t.total_time, 1), "kind": t.kind}
        for name, t in T().tricks.items()
    }


@app.post("/queue/wait")
async def queue_wait(req: Wait):
    return (await T().wait(req.duration)).as_dict()


@app.post("/queue/reset")
async def queue_reset():
    return (await T().clear_queue()).as_dict()


@app.post("/queue/stop")
@app.post("/stop/immediate")
async def stop_immediate():
    return (await T().immediate_stop(relax_playing_trick=True)).as_dict()


@app.get("/queue/status")
def queue_status():
    return T().status()


@app.post("/following/activate")
async def following_on():
    return (await T().set_following(True)).as_dict()


@app.post("/following/deactivate")
async def following_off():
    return (await T().set_following(False)).as_dict()


@app.get("/system/mode")
async def system_mode():
    return (await T().mode()).as_dict()


@app.get("/limits")
def limits():
    return LIMITS.__dict__


@app.get("/camera/image")
def camera_image():
    try:
        msg = T().latest_image.get_nowait()
    except Exception:
        return Response(status_code=503, content="no camera frame yet")
    T().latest_image.put_nowait(msg)  # keep it for the next poll
    return Response(content=bytes(msg.data), media_type="image/jpeg")


@app.websocket("/ws/detections")
async def ws_detections(ws: WebSocket):
    await ws.accept()
    detections.clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        detections.clients.discard(ws)


@app.get("/system/status")
def system_status():
    pct = volts = None
    if BATTERY_SCRIPT.exists():
        try:
            out = subprocess.run(["python3", str(BATTERY_SCRIPT)], capture_output=True, text=True, timeout=2).stdout
            m = re.search(r"(\d+(?:\.\d+)?)V", out)
            volts = float(m.group(1)) if m else None
            out = subprocess.run(["python3", str(BATTERY_SCRIPT), "--percentage_only"], capture_output=True, text=True, timeout=2).stdout
            pct = float(out.strip())
        except Exception:
            pass
    temps = psutil.sensors_temperatures()
    cpu_temp = temps["cpu_thermal"][0].current if "cpu_thermal" in temps else None
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=1).close()
        internet = "online"
    except OSError:
        internet = "offline"
    return {
        "battery": {"percentage": pct, "is_charging": False, "voltage": volts},
        "cpu": {"usage": psutil.cpu_percent(), "temperature": cpu_temp},
        "services": {"robot": "active" if tools else "inactive", "internet": internet},
    }


@app.post("/system/volume")
def system_volume(req: Volume):
    level = max(0.0, min(req.volume / 100.0, 1.5))
    r = subprocess.run(["wpctl", "set-volume", "@DEFAULT_SINK@", str(level)], capture_output=True, text=True)
    return {"success": r.returncode == 0, "message": f"volume set to {req.volume}%" if r.returncode == 0 else r.stderr}


# ------------------------------------------------------------------ deaf mode: events, the screen, hearing
@app.get("/events")
def events(after: int = -1):
    """Things that happened on the robot (urgent sounds): the app polls this once a second and
    shows each event's display part. after=-1 only asks for the last id, so a fresh session
    doesn't replay history."""
    bus = behaviors.EVENTS
    return {"events": bus.since(after) if after >= 0 else [], "last": bus.last_id}


@app.get("/display")
def display():
    """What is on Pupper's screen right now (a sound alert), or null."""
    return {"display": behaviors.display_state()}


@app.get("/hearing")
def hearing_status():
    """The sound detector: on or off, thresholds, how many sounds it has heard."""
    h = getattr(T(), "hearing", None)
    return {"available": h is not None, **(h.status() if h else {"enabled": False})}


@app.get("/hearing/now")
def hearing_now():
    """What Pupper hears right now (YAMNet's top label, or null for silence), for the screen's corner."""
    h = getattr(T(), "hearing", None)
    return h.now() if h is not None else {"label": None, "confidence": 0.0}


# ------------------------------------------------------------------ blind mode: what the eyes see (lab/vision.py)
class DetectionsIn(BaseModel):
    detections: list[dict]  # [{label, score, box: [x, y, w, h] as fractions of the frame}]
    width: int = 0
    height: int = 0
    frame_age_s: float = 0.0  # how long ago their frame was taken


class FreeSpaceIn(BaseModel):
    strips: list[dict]  # [{name, nearness | closest_m | near_frac}] left to right (lab/vision.py)
    mode: str = "relative"
    eyes_url: str = ""  # where the eyes service answers /exists (so find can ask it)
    circle: list | None = None  # the fisheye's disc [cx, cy, r, r_of_height] as fractions of the frame
    frame_age_s: float = 0.0  # how old the frame behind this summary is (a slow poster says so)
    source: str = ""  # which eyes service (hostname): a slow one stands down for a fast one


class TargetIn(BaseModel):
    name: str
    box: list | None = None  # [x, y, w, h] as fractions of the frame, or null: looked, not there
    score: float = 1.0
    source: str = "eyes"
    bottom_known: bool = True  # false: a model that only pointed (a bearing, no range)
    frame_age_s: float = 0.0  # how long ago its frame was taken: find's walk-up takes off the turning done since


class TuningIn(BaseModel):
    stop_bottom: float


@app.post("/vision/detections")
def vision_detections(req: DetectionsIn):
    """The eyes service's object detector: what is in the camera frame."""
    n = behaviors.update_detections(req.detections, req.width, req.height, req.frame_age_s)
    return {"success": True, "message": f"{n} detections"}


@app.post("/vision/freespace")
def vision_freespace(req: FreeSpaceIn):
    """The eyes service's depth sense: where the nearest thing on the floor is in each strip of the view."""
    n = behaviors.update_freespace(req.strips, req.mode, req.eyes_url, req.circle, req.frame_age_s, req.source)
    return {"success": True, "message": f"{n} strips", "wanted": behaviors.WANTED["name"] or None,
            "cam_height_m": behaviors.camera_height_m(T()), "attention": behaviors.attention_now(T()),
            "others_posting": behaviors.others_fresh(req.source)}


@app.get("/vision/wanted")
def vision_wanted():
    """What find is walking up to, for the eyes service to track (null: nothing)."""
    return {"name": behaviors.WANTED["name"] or None, "since": behaviors.WANTED["since"]}


@app.post("/vision/target")
def vision_target(req: TargetIn):
    """The eyes service's latest fix on the wanted thing (lab/blind_mode.py steers on it)."""
    seen = behaviors.update_target(req.name, req.box, req.score, req.source, req.bottom_known, req.frame_age_s)
    return {"success": True, "message": "box" if seen else "not seen", "wanted": behaviors.WANTED["name"] or None}


@app.get("/vision")
def vision(source: str = ""):
    """Everything the eyes posted, plus what an eyes service called ``source`` should do now: run at
    full rate (attention) or stand down (another service is posting)."""
    return {"detections": behaviors.VISION, "freespace": behaviors.FREESPACE, "target": behaviors.TARGET,
            "wanted": behaviors.WANTED, "attention": behaviors.attention_now(T()),
            "others_posting": behaviors.others_fresh(source) if source else False}


@app.get("/debug")
def debug_page():
    """What Pupper sees, with what find's obstacle rule and walk-up are working from drawn on it."""
    return Response(content=(Path(__file__).resolve().parent / "debug.html").read_text(), media_type="text/html")


@app.get("/debug/state")
def debug_state():
    return behaviors.debug_state(tools)


@app.post("/debug/tuning")
def debug_tuning(req: TuningIn):
    """Move the stop row (Lab 5's STOP_BOTTOM) until the server restarts. Moves nothing."""
    return behaviors.set_stop_bottom(req.stop_bottom).as_dict()


@app.get("/mirror")
def mirror_page():
    """A live copy of Pupper's screen for a laptop, clicks and typing included (robot/mirror.py)."""
    return Response(mirror.PAGE, media_type="text/html")


@app.websocket("/ws/mirror")
async def mirror_ws(ws: WebSocket):
    await mirror.relay(ws)


@app.get("/health")
def health():
    return {"status": "running", "tools": tools is not None}


# The built web app (npm run build in web/ -> web/dist/) is served at /, so the robot's own
# browser, or a laptop through an SSH tunnel, opens everything from one address.
if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")


if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
