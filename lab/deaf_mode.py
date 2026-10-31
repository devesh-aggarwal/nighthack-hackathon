"""Deaf mode: Pupper listens for urgent sounds and shows them on its screen.

When the person says they are deaf or hard of hearing (or asks for deaf mode), Gemini calls
deaf_mode. The robot then opens its mic to the calibrated detector (hearing/listener.py, run by
the robot server as robot.hearing): YAMNet names sirens, alarms, doorbells, crashes and a baby
crying, and the meter catches knocking. Each urgent sound turns the whole screen red with what
it was, in big letters, for as long as the sound goes on (hearing_tick: YAMNet's scores every
0.5 s), and the screen clears as soon as it stops. Gemini is not told: Pupper shows sounds, it
doesn't talk about them. Shouts and bangs are not urgent: the meter's loudness alone gave too
many false alarms.

The screen gets it as *events*: the web app polls GET /events?after=<id> four times a second,
paints a "sound" event's ``display`` and clears the screen on "sound_end", even while Pupper sleeps.

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
    "crash": ("CRASH", "something broke"),
    "knock": ("KNOCKING", "someone may be at the door"),
    "baby": ("BABY CRYING", "a baby is crying"),
}
URGENT_COLOR = "#ef4444"  # red
STILL_SCORE = 0.15  # YAMNet still scores the sound this high: it is still going on
MIN_SHOW_S = 1.0  # a short sound (a knock, a crash) stays up this long, to be read
MAX_SHOW_S = 10.0  # with no word from YAMNet (not installed), the screen clears after this
MOTION_QUIET_S = 1.5  # after a motion job: the motors are loud, and so is a landing

DEAF_MODE = False  # never on at start
OWN_SOUND_UNTIL = 0.0  # time.time() until which what the mic hears is Pupper's own beacon (lab/blind_mode.py)
_showing = ""  # the class on the screen now ("" = nothing)
_last_heard = 0.0  # when that sound was last heard


# ============================================================ what the robot does: events and the screen
class EventBus:
    def __init__(self, keep: int = 200) -> None:
        self.events: collections.deque[dict] = collections.deque(maxlen=keep)
        self.last_id = 0

    def emit(self, kind: str, text: str, display: dict | None = None, for_gemini: str = "") -> dict:
        self.last_id += 1
        ev = {"id": self.last_id, "time": time.time(), "clock": time.strftime("%H:%M"), "kind": kind,
              "text": text, "for_gemini": for_gemini, "display": display, "data": {}}
        self.events.append(ev)
        log.info("event %s: %s", kind, text)
        return ev

    def since(self, after: int) -> list[dict]:
        return [e for e in self.events if e["id"] > after]


EVENTS = EventBus()
# kind "sound" (deaf mode) or "emergency" (collapse_watch.py: flashes, and the app sounds a siren)
DISPLAY: dict = {"text": "", "detail": "", "color": URGENT_COLOR, "until": 0.0, "kind": "", "flash": False, "siren": False}


def show_alert(text: str, detail: str) -> dict:
    """Turn the whole screen red with ``text`` (until the sound stops, at most MAX_SHOW_S), and tell the app now."""
    DISPLAY.update(text=text, detail=detail, color=URGENT_COLOR, until=time.time() + MAX_SHOW_S, kind="sound",
                   flash=False, siren=False)
    payload = {"text": text, "detail": detail, "color": URGENT_COLOR, "seconds": MAX_SHOW_S, "kind": "sound"}
    return EVENTS.emit("sound", f"screen: {text} ({detail})", payload)


def end_alert() -> dict:
    """The sound stopped: clear the screen now."""
    text = DISPLAY["text"]
    clear_display()
    return EVENTS.emit("sound_end", f"screen: {text} cleared")


def display_state() -> dict | None:
    """What is on the screen right now (the app asks when it (re)connects)."""
    if time.time() >= DISPLAY["until"] or not DISPLAY["text"]:
        return None
    return {"text": DISPLAY["text"], "detail": DISPLAY["detail"], "color": DISPLAY["color"],
            "seconds": round(DISPLAY["until"] - time.time(), 1), "kind": DISPLAY["kind"],
            "flash": DISPLAY["flash"], "siren": DISPLAY["siren"]}


def clear_display() -> None:
    DISPLAY.update(text="", detail="", until=0.0, kind="", flash=False, siren=False)


def emergency_showing() -> bool:
    """Is the collapse emergency (collapse_watch.py) on the screen? It wins over sounds."""
    return DISPLAY["kind"] == "emergency" and display_state() is not None


# ============================================================ what the robot does: hearing
def _moving(robot: Robot) -> bool:
    return bool(robot.following or robot.current is not None or robot.now() < robot.busy_until + MOTION_QUIET_S)


def hearing_detected(robot: Robot, event: dict) -> Result:
    """The detector (robot.hearing) heard something: if it is urgent, show it, red, on the screen."""
    global _showing, _last_heard
    if not DEAF_MODE:
        return Result(True, "ignored: deaf mode is off")
    if _moving(robot):
        return Result(True, "ignored: that was probably my own motors (I was moving)")
    if time.time() < OWN_SOUND_UNTIL:
        return Result(True, "ignored: that was my own beacon beeping")
    if emergency_showing():
        return Result(True, "ignored: my own emergency alarm is sounding")
    cls = event.get("cls", "")
    if cls not in URGENT:
        return Result(True, f"ignored: {cls} is not an urgent sound")
    label, what = URGENT[cls]
    if _showing == cls and display_state() is not None:
        _last_heard = time.time()
        return Result(True, f"{label}: already on the screen")
    _showing, _last_heard = cls, time.time()
    info = event.get("info", {})
    how = (f"{info['label']}, {100 * info.get('confidence', 0):.0f}% sure" if info.get("label")   # YAMNet
           else f"{event.get('score', 0):.0f} dB above the room")                                  # the meter
    show_alert(label, f"{time.strftime('%H:%M:%S')}  {what}")
    log.info("urgent sound: %s (%s)", label, how)
    return Result(True, f"{label}: shown on the screen ({how})")


def hearing_tick(scores: dict) -> None:
    """Every 0.5 s, YAMNet's group scores: keep the alert while its sound goes on, clear it once it stops."""
    global _showing, _last_heard
    if not _showing:
        return
    if display_state() is None or DISPLAY["kind"] != "sound":  # cleared or replaced (MAX_SHOW_S, off, an emergency)
        _showing = ""
        return
    now = time.time()
    if scores.get(_showing, 0.0) >= STILL_SCORE:
        _last_heard = now
    elif now - _last_heard >= MIN_SHOW_S:
        _showing = ""
        end_alert()


# ============================================================ what Gemini reads
@tool(
    "deaf_mode",
    "Deaf mode: keep listening for urgent sounds (siren, alarm, doorbell, knocking, a crash, a baby"
    " crying) for a person who can't hear them. Turn it on when the person says they"
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
        if display_state() is not None and DISPLAY["kind"] == "sound":
            end_alert()
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
