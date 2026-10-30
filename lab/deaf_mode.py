"""Deaf mode: Pupper listens for urgent sounds and shows them on its screen.

When the person says they are deaf or hard of hearing (or asks for deaf mode), Gemini calls
deaf_mode. The robot then opens its mic to the calibrated detector (hearing/listener.py, run by
the robot server as robot.hearing): YAMNet names sirens, alarms, doorbells, crashes and a baby
crying, and the meter catches bangs, shouts and knocking. Each urgent sound turns the whole
screen red with what it was, in big letters, for URGENT_SECONDS. Gemini is not told: Pupper
shows sounds, it doesn't talk about them.

The screen gets it as an *event*: the web app polls GET /events?after=<id> once a second and
paints each event's ``display`` part, even while Pupper sleeps.

Deaf mode is off every time the robot starts. Without a mic (none plugged in, and no laptop mic
streamed in with ./run_pupper.sh --laptop) it refuses and says why.
"""

from __future__ import annotations

import collections
import logging
import time

from robot_api import Result, Robot
from tool_registry import Param, tool

log = logging.getLogger("events")

URGENT = {  # the detector's class -> (big words on the screen, the line under them)
    "siren": ("SIREN", "an ambulance, police car or fire truck"),
    "alarm": ("ALARM", "a smoke alarm, fire alarm or timer"),
    "doorbell": ("DOORBELL", "someone may be at the door"),
    "bang": ("LOUD BANG", "something dropped, slammed or fell"),
    "crash": ("CRASH", "something broke"),
    "shout": ("SHOUTING", "someone is shouting"),
    "knock": ("KNOCKING", "someone may be at the door"),
    "baby": ("BABY CRYING", "a baby is crying"),
}
# A hard knock or a crash also trips the meter's bang detector, often first: the more specific
# name replaces LOUD BANG on the screen instead of being dropped as "already alerting".
MORE_SPECIFIC_THAN_BANG = {"knock", "doorbell", "crash"}
URGENT_COLOR = "#ef4444"  # red
URGENT_SECONDS = 10.0
MIN_SOUND_GAP_S = 4.0  # one alert at a time
MOTION_QUIET_S = 1.5  # after a motion job: the motors are loud, and so is a landing

DEAF_MODE = False  # never on at start
_last_alert = 0.0
_last_cls = ""


# ============================================================ what the robot does: events and the screen
class EventBus:
    def __init__(self, keep: int = 200) -> None:
        self.events: collections.deque[dict] = collections.deque(maxlen=keep)
        self.last_id = 0

    def emit(self, kind: str, text: str, display: dict | None = None) -> dict:
        self.last_id += 1
        ev = {"id": self.last_id, "time": time.time(), "clock": time.strftime("%H:%M"), "kind": kind,
              "text": text, "for_gemini": "", "display": display, "data": {}}
        self.events.append(ev)
        log.info("event %s: %s", kind, text)
        return ev

    def since(self, after: int) -> list[dict]:
        return [e for e in self.events if e["id"] > after]


EVENTS = EventBus()
DISPLAY: dict = {"text": "", "detail": "", "color": URGENT_COLOR, "until": 0.0, "kind": ""}


def show_alert(text: str, detail: str) -> dict:
    """Turn the whole screen red with ``text`` for URGENT_SECONDS, and tell the app now."""
    DISPLAY.update(text=text, detail=detail, color=URGENT_COLOR, until=time.time() + URGENT_SECONDS, kind="sound")
    payload = {"text": text, "detail": detail, "color": URGENT_COLOR, "seconds": URGENT_SECONDS, "kind": "sound"}
    return EVENTS.emit("sound", f"screen: {text} ({detail})", payload)


def display_state() -> dict | None:
    """What is on the screen right now (the app asks when it (re)connects)."""
    if time.time() >= DISPLAY["until"] or not DISPLAY["text"]:
        return None
    return {"text": DISPLAY["text"], "detail": DISPLAY["detail"], "color": DISPLAY["color"],
            "seconds": round(DISPLAY["until"] - time.time(), 1), "kind": DISPLAY["kind"]}


def clear_display() -> None:
    DISPLAY.update(text="", detail="", until=0.0)


# ============================================================ what the robot does: hearing
def _moving(robot: Robot) -> bool:
    return bool(robot.following or robot.current is not None or robot.now() < robot.busy_until + MOTION_QUIET_S)


def hearing_detected(robot: Robot, event: dict) -> Result:
    """The detector (robot.hearing) heard something: if it is urgent, show it, red, on the screen."""
    global _last_alert, _last_cls
    if not DEAF_MODE:
        return Result(True, "ignored: deaf mode is off")
    if _moving(robot):
        return Result(True, "ignored: that was probably my own motors (I was moving)")
    cls = event.get("cls", "bang")
    if cls not in URGENT:
        return Result(True, f"ignored: {cls} is not an urgent sound")
    now = time.time()
    upgrade = _last_cls == "bang" and cls in MORE_SPECIFIC_THAN_BANG
    if now - _last_alert < MIN_SOUND_GAP_S and not upgrade:
        return Result(True, "ignored: already alerting")
    _last_alert, _last_cls = now, cls
    label, what = URGENT[cls]
    info = event.get("info", {})
    how = (f"{info['label']}, {100 * info.get('confidence', 0):.0f}% sure" if info.get("label")   # YAMNet
           else f"{event.get('score', 0):.0f} dB above the room")                                  # the meter
    show_alert(label, f"{time.strftime('%H:%M:%S')}  {what}")
    log.info("urgent sound: %s (%s)", label, how)
    return Result(True, f"{label}: shown on the screen ({how})")


# ============================================================ what Gemini reads
@tool(
    "deaf_mode",
    "Deaf mode: keep listening for urgent sounds (siren, alarm, doorbell, knocking, a crash or bang,"
    " shouting, a baby crying) for a person who can't hear them. Turn it on when the person says they"
    " are deaf or hard of hearing, or asks for deaf mode; off when they ask to stop. Only for people"
    " who can't hear: never for someone who is blind or has low vision. Urgent sounds then turn the"
    " screen red with what they were, in big letters; you are not told about them.",
    enabled=Param("boolean", "true to start deaf mode, false to stop it."),
)
async def deaf_mode_tool(robot: Robot, enabled: bool) -> Result:
    global DEAF_MODE
    hearing = getattr(robot, "hearing", None)
    if not enabled:
        DEAF_MODE = False
        if hearing is not None:
            await hearing.set_enabled(False)
        return Result(True, "deaf mode off: I no longer listen for urgent sounds")
    if hearing is None:
        return Result(False, "deaf mode stays off: this robot has no sound detector (it needs numpy, scipy and"
                             " sounddevice, as on the Pupper)")
    ok, how = await hearing.set_enabled(True)
    if not ok:
        return Result(False, f"deaf mode stays off: {how}")
    DEAF_MODE = True
    return Result(True, f"deaf mode on: urgent sounds turn my screen red with what they were ({how})")
