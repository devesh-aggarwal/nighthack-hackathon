"""Collapse watch: if Pupper sees someone collapsed on the floor, it goes to them and raises the alarm.

On from the moment the robot server starts. Every CHECK_EVERY_S the server's watcher
(watch_for_collapse) hands Pupper's latest camera frame to robot.vision (vision/gemini_vision.py),
which asks Gemini whether a person is lying or slumped on the floor. Seen CONFIRM times in a row,
Pupper:

  1. walks up to them, carefully: one short turn or one short step (at most MAX_STEP_M, slowly) at
     a time, looking again after each, and stops about STOP_AT_M from the nearest part of anyone's
     body (feet and legs too; Gemini's estimate, hence the margin). A step forward needs both
     Gemini saying the floor ahead is clear and blind mode's obstacle sense (lab/vision.py: the
     eyes service's depth and object detector) seeing right now; that sense is checked on every
     tick of the step, which stops the moment anything is close ahead. Without it Pupper only
     turns to face them. move's limits, fall checks and stop all apply.
  2. asks "Are you okay?": the screen turns red and flashes, and Gemini is told to ask out loud,
  3. sounds its alarm for ALARM_S: the web app plays a siren while the screen keeps flashing,
  4. if nobody has said they are fine by then, says it is calling 911 and their emergency
     contacts (the screen says CALLING 911). It only says so: no call is placed.

"I'm okay" (Gemini calls emergency_response with okay=true) stops it at any point and clears the
screen; "help me" (okay=false) goes straight to step 4. collapse_watch(enabled=false) turns the
watch off. The screen gets it all as events, the same way as deaf mode's sounds (deaf_mode.py).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time

import vision  # blind mode's senses: the obstacle rule its own walks use
from deaf_mode import DISPLAY, EVENTS, URGENT_COLOR, clear_display
from part2_walk import check_move, walk
from part3_follow import prepare_to_move
from robot_api import FELL_MESSAGE, LLM_CMD_RATE, LOCOMOTION, LOCOMOTION_CONTROLLER, Result, Robot
from tool_registry import Param, tool

log = logging.getLogger("collapse")

CHECK_EVERY_S = 3.0  # how often the camera is checked (each check is one Gemini call)
CONFIRM = 2  # sightings in a row before Pupper acts: one odd frame isn't enough
CAMERA_HFOV_DEG = 180.0  # the fisheye (blind_mode.HFOV_DEG); rough is fine: each turn looks again
TURN_IF_OFF_DEG = 12.0  # someone less than this off-centre is straight ahead: no turn
TURN_SPEED = 60.0  # deg/s, move's default
APPROACH_SPEED = 0.2  # m/s, the slowest walk move allows
STOP_AT_M = 1.0  # stop about this far from the nearest part of anyone's body (feet and legs too)
CLOSE_ENOUGH_M = 0.15  # ... give or take this
MAX_STEP_M = 0.4  # walk at most this far before looking again
APPROACH_STEPS = 10  # turns and steps in all
SETTLE_S = 0.5  # after a step, before the next frame (no motion blur)
EYES_WAIT_S = 3.0  # how long a step waits for the obstacle sense to see (it idles until woken)
ASK_S = 3.0  # "Are you okay?" is asked before the siren starts
ALARM_S = 10.0  # the siren, while the screen flashes
CALLING_SHOW_S = 60.0  # CALLING 911 stays on the screen this long
QUIET_AFTER_OK_S = 300.0  # after "I'm okay", don't check on anyone for this long
QUIET_AFTER_CALL_S = 120.0

ASK = ("[Emergency, from your camera] Someone is lying on the floor and may have collapsed; you have walked up"
       " to them. Ask them right now, out loud, in one short sentence: \"Are you okay?\" If they say they are"
       " fine, call emergency_response with okay true. If they ask for help or say they are hurt, call it with"
       " okay false. Your alarm will sound for 10 seconds; if nobody says they are fine, you call 911.")
CALLING = ("[Emergency] Nobody said they are okay. Say out loud, in one short sentence: \"I'm calling 911 and"
           " your emergency contacts now.\"")

WATCHING = True  # on by default
_stage = ""  # "" (watching), "approach", "ask", "alarm" or "calling"
_task: asyncio.Task | None = None
_seen_in_row = 0
_quiet_until = 0.0  # robot.now() time: no checks until then


# ============================================================ what the robot does: the screen
def show_emergency(text: str, detail: str, seconds: float, *, siren: bool, for_gemini: str = "") -> dict:
    """The whole screen red and flashing; with siren=True the app sounds its siren too."""
    DISPLAY.update(text=text, detail=detail, color=URGENT_COLOR, until=time.time() + seconds, kind="emergency",
                   flash=True, siren=siren)
    payload = {"text": text, "detail": detail, "color": URGENT_COLOR, "seconds": seconds, "kind": "emergency",
               "flash": True, "siren": siren}
    return EVENTS.emit("emergency", f"screen: {text}" + (" (siren)" if siren else ""), payload, for_gemini)


def end_emergency(why: str, quiet_s: float, robot: Robot) -> None:
    """Back to watching (after quiet_s), with the screen cleared."""
    global _stage, _quiet_until, _seen_in_row
    _stage, _seen_in_row = "", 0
    _quiet_until = robot.now() + quiet_s
    if DISPLAY["kind"] == "emergency":
        clear_display()
    EVENTS.emit("emergency_end", f"emergency over: {why}")


# ============================================================ what the robot does: watching
def latest_frame(robot: Robot) -> bytes | None:
    """The newest camera JPEG (left in place for the app's camera view), or None."""
    try:
        msg = robot.latest_image.get_nowait()
    except Exception:
        return None
    try:
        robot.latest_image.put_nowait(msg)
    except Exception:  # a newer frame arrived meanwhile: fine
        pass
    return bytes(msg.data)


async def look(robot: Robot) -> dict | None:
    vision = getattr(robot, "vision", None)
    jpeg = latest_frame(robot) if vision is not None else None
    return await vision.look(jpeg) if jpeg else None


async def look_once(robot: Robot) -> str:
    """One check of the camera; starts the emergency on the CONFIRM-th sighting in a row."""
    global _seen_in_row, _task
    if not WATCHING:
        return "not watching: collapse watch is off"
    if getattr(robot, "vision", None) is None:
        return "not watching: no vision checker"
    if _stage:
        return f"busy: emergency in progress ({_stage})"
    if robot.now() < _quiet_until:
        return "quiet for now"
    seen = await look(robot)
    if seen is None:
        return "no camera frame, or Gemini couldn't be asked"
    if not seen.get("down"):
        _seen_in_row = 0
        return f"nobody down ({seen.get('why', '')})"
    _seen_in_row += 1
    if _seen_in_row < CONFIRM:
        return f"someone may be down ({_seen_in_row}/{CONFIRM}): {seen.get('why', '')}"
    _seen_in_row = 0
    _task = asyncio.ensure_future(emergency(robot, seen))
    return f"someone is down: going to help ({seen.get('why', '')})"


async def watch_for_collapse(robot: Robot) -> None:
    """The robot server runs this for as long as it runs."""
    while True:
        await robot.sleep(CHECK_EVERY_S)
        try:
            what = await look_once(robot)
            if what.startswith(("someone", "busy")):
                log.info("collapse watch: %s", what)
        except Exception:
            log.exception("collapse watch failed; still watching")


# ============================================================ what the robot does: the emergency
async def _done(robot: Robot, stops: int, queued: Result) -> Result:
    """Wait until the queue has run what was just queued; how it went."""
    if not queued.ok:
        return queued
    while robot.current is not None or not robot.commands.empty():
        await robot.sleep(0.1)
    if robot.stops != stops:
        return Result(False, "a person stopped me")
    if robot.fallen:
        return Result(False, "fell over")
    return LAST_STEP.pop("result", Result(True, "done"))


LAST_STEP: dict = {}  # the guarded step's outcome, for _done


async def _turn(robot: Robot, deg: float) -> Result:
    """Turn in place through move's walk (its limits and fall checks), and wait until done."""
    stops = robot.stops
    LAST_STEP.clear()
    return await _done(robot, stops, await walk(robot, 0.0, 0.0, math.copysign(TURN_SPEED, deg), abs(deg) / TURN_SPEED))


async def _eyes_seeing(robot: Robot) -> str | None:
    """None if the obstacle sense (the eyes service's depth) is seeing right now, else why not."""
    if not vision.eyes_alive():
        return "my obstacle sense (the eyes service) isn't running"
    vision.wake()
    end = robot.now() + EYES_WAIT_S
    while vision.fresh_freespace() is None:
        if robot.now() >= end:
            return "my obstacle sense isn't seeing right now"
        await robot.sleep(0.2)
    return None


async def _step(robot: Robot, metres: float) -> Result:
    """Walk ``metres`` straight ahead, slowly, like move's walk but checking the obstacle rule on every
    tick: anything close ahead, or the obstacle sense going blind, stops it at once. Waits until done."""
    seconds = metres / APPROACH_SPEED
    speeds = robot.speeds()
    refused = check_move(APPROACH_SPEED, 0.0, 0.0, seconds, speeds.limits)
    if refused:
        return Result(False, refused + speeds.where)
    stops = robot.stops
    await prepare_to_move(robot)
    if robot.fallen:
        return Result(False, FELL_MESSAGE)
    state = robot.state_after_queue
    walker = robot.walking_controller(state)
    then = state if walker != LOCOMOTION_CONTROLLER else LOCOMOTION

    async def job() -> Result:
        res = await robot.switch_to(walker)
        if not res.ok:
            return res
        end = robot.now() + seconds
        while robot.now() < end - 1e-6:
            why = (FELL_MESSAGE if robot.fallen else vision.path_blocked(APPROACH_SPEED, 0.0)
                   or ("my obstacle sense went blind" if vision.fresh_freespace() is None else None))
            if why:
                robot.send_speed(0, 0, 0)
                LAST_STEP["result"] = Result(False, why)
                return LAST_STEP["result"]
            robot.send_speed(APPROACH_SPEED, 0.0, 0.0)
            await robot.sleep(1.0 / LLM_CMD_RATE)
        robot.send_speed(0, 0, 0)
        return Result(True, f"stepped {metres:.2f} m toward them")

    LAST_STEP.clear()
    return await _done(robot, stops, await robot.queue("step toward them", job, seconds, then=then))


async def approach(robot: Robot, seen: dict) -> str:
    """Walk up to the person: one turn or one short step at a time, each decided on a fresh look
    (a turn changes what is ahead). Says how it went."""
    for _ in range(APPROACH_STEPS):
        far = seen.get("nearest_m", 0.0) - STOP_AT_M  # to the nearest part of anyone's body
        if far < CLOSE_ENOUGH_M:
            return "close to them"
        angle = (0.5 - seen.get("x", 0.5)) * CAMERA_HFOV_DEG  # left of centre: turn left (positive)
        if abs(angle) >= TURN_IF_OFF_DEG:
            res = await _turn(robot, angle)
            if not res.ok:
                return f"couldn't turn to them ({res.message}); asking from here"
        elif not seen.get("path_clear"):
            return "something is in my way, maybe part of them; asking from here"
        else:
            blind = await _eyes_seeing(robot)
            if blind:
                return f"{blind}, so I won't walk up blind; asking from here"
            res = await _step(robot, min(far, MAX_STEP_M))
            if not res.ok:
                return f"stopped walking ({res.message}); asking from here"
        await robot.sleep(SETTLE_S)
        again = await look(robot)
        if not again or not again.get("down"):
            return "lost sight of them; asking from here"
        seen = again
    return "as close as I could get in a few steps"


async def emergency(robot: Robot, seen: dict) -> None:
    """Walk up, ask, sound the alarm, then (no answer) say it is calling 911. Cancelled by emergency_response."""
    global _stage
    _stage = "approach"
    EVENTS.emit("collapse", f"someone looks collapsed ({seen.get('why', '')}): going to them")
    how = await approach(robot, seen)
    log.info("collapse: %s", how)
    _stage = "ask"
    detail = "Say \"I'm okay\" if you are. If not, Pupper calls 911."
    show_emergency("ARE YOU OKAY?", detail, ASK_S + ALARM_S, siren=False, for_gemini=ASK)
    await robot.sleep(ASK_S)
    _stage = "alarm"
    show_emergency("ARE YOU OKAY?", detail, ALARM_S, siren=True)
    await robot.sleep(ALARM_S)
    await call_for_help(robot)


async def call_for_help(robot: Robot) -> None:
    """Say it is calling 911 and the emergency contacts (on the screen, and Gemini says it). No call is placed."""
    global _stage
    _stage = "calling"
    show_emergency("CALLING 911", "and your emergency contacts", CALLING_SHOW_S, siren=False, for_gemini=CALLING)
    await robot.sleep(CALLING_SHOW_S)
    end_emergency("911 shown for its time", QUIET_AFTER_CALL_S, robot)


async def _cancel(robot: Robot) -> None:
    """Stop the emergency where it is (and Pupper, if it was walking up)."""
    global _task
    walking = _stage == "approach"
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    _task = None
    if walking:
        await robot.immediate_stop()


async def emergency_response(robot: Robot, okay: bool) -> Result:
    global _task
    if not _stage:
        return Result(False, "no emergency is going on: I haven't seen anyone collapsed")
    if okay:
        await _cancel(robot)
        end_emergency("they said they are okay", QUIET_AFTER_OK_S, robot)
        return Result(True, "alarm off and screen cleared; nobody is called. I won't check on anyone for"
                            f" {QUIET_AFTER_OK_S / 60:.0f} minutes")
    if _stage == "calling":
        return Result(True, "already calling 911 and their emergency contacts (it is on the screen)")
    await _cancel(robot)
    _task = asyncio.ensure_future(call_for_help(robot))
    await asyncio.sleep(0)  # let it put CALLING 911 on the screen
    return Result(True, "the screen now says CALLING 911; tell them you are calling 911 and their emergency contacts")


# ============================================================ what Gemini reads
@tool(
    "emergency_response",
    "Only after you have asked someone who collapsed \"Are you okay?\" (you are told when): okay=true"
    " when they say they are fine (stops the alarm, nobody is called); okay=false when they ask for"
    " help, say they are hurt, or can't answer properly (you say you are calling 911 at once).",
    okay=Param("boolean", "true: they said they are fine. false: they need help."),
)
async def emergency_response_tool(robot: Robot, okay: bool) -> Result:
    return await emergency_response(robot, okay)


@tool(
    "collapse_watch",
    "Collapse watch, on from the start: if your camera sees someone collapsed on the floor, you walk"
    " up to them, ask if they are okay and sound an alarm; with no answer, you say you are calling 911."
    " Turn it off only when someone asks you to stop watching for falls; on again when they ask.",
    enabled=Param("boolean", "true to watch for people who collapse, false to stop."),
)
async def collapse_watch_tool(robot: Robot, enabled: bool) -> Result:
    global WATCHING, _quiet_until
    if not enabled:
        WATCHING = False
        if _stage:
            await _cancel(robot)
            end_emergency("collapse watch turned off", 0.0, robot)
        return Result(True, "collapse watch off: I no longer watch for people who collapse")
    if getattr(robot, "vision", None) is None:
        return Result(False, "collapse watch can't start: there is no Gemini key on the robot for my camera"
                             " checks (~/.config/gemini-pupper/api_key)")
    WATCHING, _quiet_until = True, 0.0
    return Result(True, "collapse watch on: if I see someone collapsed on the floor, I go to them and raise the alarm")
