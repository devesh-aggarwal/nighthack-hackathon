"""Blind mode: Pupper finds a thing, walks up to it, and beeps beside it.

Two tools, for a person who can't see well:

  find(what)       "where is my cup?", "find my keys". Pupper looks (the detector's word for a thing
                   with a COCO class, else Gemma's yes or no through the eyes service), turns a full
                   circle in steps looking after each, then roams the room in legs toward open floor
                   it hasn't been on. Found: it walks up to the thing (walk_up) and stops at arm's
                   length, then beeps there.
  beacon(enabled)  the beep: a short rising chirp once a second from the robot's own speaker, so the
                   person walks toward the sound. find starts it on arrival; "got it" stops it
                   (enabled=false), and so do any motion (prepare_to_move), stop, the gamepad e-stop
                   and its time running out. "Beep so I can find you" starts it by hand.

The senses are lab/vision.py (the eyes service's detector and depth). Pupper has no map: find is a
smart Roomba, not a Matic. It moves in legs and looks at every stop; "somewhere new" is a 1 m grid
kept by dead reckoning from the speeds sent, a preference, never a claim about where the robot is.
Every walk here runs the obstacle rule (vision.path_blocked) on every tick and only ever sends the
speeds walk and turn use, within check_move's limits; a stop cancels it like any job.

The brain is slow (a decision a second) and the legs need a speed twenty times a second, so the
brain never steers: it names the thing, and walk_up closes the loop, the way the Lab 5 person
follower does. Results go on deaf mode's event bus with a line for Gemini (``for_gemini``), so
Pupper says what happened without anyone asking.

The beep plays on the robot, not in the browser: with ./run_pupper.sh --laptop the app's speaker is
the laptop's, and the sound has to come from where the thing is. While it beeps, deaf mode ignores
what it hears (deaf_mode.OWN_SOUND_UNTIL), so the beacon can't set off a sound alert.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import shutil
import struct
import tempfile
import time
import wave
from pathlib import Path
from typing import Awaitable, Callable

import deaf_mode as deaf
import fisheye_geom
import vision
from part3_follow import prepare_to_move
from robot_api import FELL_MESSAGE, LLM_CMD_RATE, LOCOMOTION, LOCOMOTION_CONTROLLER, Result, Robot, with_note
from tool_registry import Param, tool

log = logging.getLogger("vision")

GREEN, ORANGE = "#22c55e", "#f97316"

# walk_up: steering on the target
HFOV_DEG = 180.0  # the fisheye: a box at the frame's edge is about 90 degrees off
KP_TURN = 1.5  # deg/s of turn per degree of bearing
AIM_DEG = 5.0  # facing it well enough: don't turn
WALK_WITHIN_DEG = 20.0  # walk forward only when roughly facing it; otherwise turn first
# Arrival is Lab 5's rule, calibrated on the robot (lab/fisheye_geom.py): the row of the thing's base in
# the undistorted image. STOP_NEARNESS (Lab 5's STOP_BOTTOM 454 of 572) is the follower's polite distance
# at a person's feet; NEAR_NEARNESS is "about there": a thing whose base is this low while the way ahead
# is blocked at the robot's feet counts as reached. The debug page can move STOP_BOTTOM (lab/debug_view.py).
ARRIVE_NEARNESS = fisheye_geom.STOP_NEARNESS
NEAR_NEARNESS = vision.BLOCK_NEARNESS - 0.03
SLOW_NEARNESS = fisheye_geom.SLOW_NEARNESS
ARRIVE_TICKS = 8  # ... for this many ticks in a row (0.4 s): one noisy frame is not an arrival
SLOW_M = 0.9  # a bottom-unknown target, ranged by its depth strip: half speed from here on
SEE_S = 15.0  # how long to stand and wait for the eyes to find the thing at all (Gemma's first box takes 5-8 s, more on the Pi)
LOST_S = 10.0  # how long to keep going on the last box before giving up (Gemma answers every 1-3 s, and holds back boxes that don't agree)
TARGET_FRESH_S = 4.0  # a box older than this is not steered on
# A walk-up may move for as long as one move may (move's limits: about 18 s, 7 m), but standing still to
# look doesn't count: with Gemma on the Pi the first box takes several seconds and each look after a turn
# a few more, and counting them ended walks 2 s in, the thing still ahead (seen on the robot). This caps
# the whole walk-up, standing included.
WALK_UP_MAX_S = 60.0
# Close to the thing the eyes give out: it fills the view at the edge of the lens, and Gemma stops answering.
# On the robot the walk-up then stood at the stop row waiting for a second box to confirm, and called it
# "lost". So: never walk more than BLIND_WALK_M on a box no newer one has replaced (stop and look instead),
# and once the last box put the thing close (its base at the slow-down row or lower), CLOSE_LOST_S without a
# new one means it is right in front, too close to see: arrived. Far away, losing it means looking again.
BLIND_WALK_M = 0.6  # counted from when the box arrived (a 2 s old box has already been walked on 0.8 m)
CLOSE_LOST_S = 3.0  # at least; it is twice the longest recent gap between boxes, at most CLOSE_LOST_MAX_S
CLOSE_LOST_MAX_S = 10.0  # (with Gemma on the Pi boxes came 4-5 s apart, and a fixed 3 s fired a metre early)
RETRIES = 1  # far from it and lost: look again by itself this many times before telling the person
# What walk_up wants tracked (the eyes service polls GET /vision/wanted), and the latest box from
# Gemma (POST /vision/target). box = [x, y, w, h] as fractions of the frame, or None (looked, not there).
WANTED: dict = {"name": "", "since": 0.0, "stops": -1}
TARGET: dict = {"time": 0.0, "name": "", "box": None, "score": 0.0, "source": "", "bottom_known": True}

# find: the search
LEG_M = 1.5  # one leg at most; then stop and look again
CELL_M = 1.0  # the visited grid's cell
MAX_S_DEFAULT = 120.0
MAX_S_LIMIT = 300.0
LOOK_S = 0.6  # stand still at each stop so the depth sense and detector settle
OPEN_M = 2.0  # a strip with nothing in it counts as this open
BLOCKED_M = vision.STOP_M + 0.1  # a direction this short is not worth turning to
VISITED_WEIGHT = 0.35  # a direction leading to a visited cell keeps this much of its score
STRAIGHT_BONUS = 1.15  # carrying on is a little preferred (longer legs, fewer turns)
TURN_AROUND_DEG = 150.0  # when nothing ahead is worth it: turn about, with a little randomness
STUCK_LEGS = 4  # legs in a row that could not start: boxed in, give up
SWEEP_STEP_DEG = 60.0  # the sweep in place: a full circle in steps, a look after each
SWEEP_STEPS = 5  # 5 turns + the look before the first one = 6 looks around the circle
SECTORS = [("left", -60.0), ("center-left", -30.0), ("center", 0.0), ("center-right", 30.0), ("right", 60.0)]
MIN_LEG_M = 0.1  # a leg shorter than this did not start
EXPLORING: dict = {"name": "", "since": 0.0, "stops": -1}
rng = random.Random()

# beacon: the beep
BEACON_S = 30.0  # how long it beeps when nobody says how long
BEACON_MAX_S = 120.0
# After find's walk-up starts the beep, beacon(enabled=false) is refused for this long: Gemini read "say
# 'got it' when they have it; then call beacon with enabled=false" as "call it now", and the beep stopped
# before anyone heard it (seen on the robot). Nobody walks over to it that fast. The stop tool still ends it.
AUTO_GRACE_S = 6.0
BEEP_EVERY_S = 1.0  # one chirp a second: often enough to walk toward, rare enough to talk over
QUIET_AFTER_S = 1.0  # deaf mode ignores sound for this long after each chirp
TONES_HZ = (1000.0, 1500.0)  # rising two-tone chirp: easy to place by ear, unlike a pure tone
TONE_S = 0.12
GAP_S = 0.04
RATE = 22050
PLAYERS = (["pw-play"], ["paplay"], ["aplay", "-q"])  # the first one installed plays the beep
BEACON: dict = {"name": "", "until": 0.0, "task": None, "beeps": 0, "started": 0.0}


# ============================================================ what the robot does: telling the person
def announce(kind: str, text: str, for_gemini: str, big: str = "", detail: str = "", color: str = GREEN,
             seconds: float = 12.0) -> dict:
    """Put a result on deaf mode's event bus: a card on the screen (when ``big``) and a line Gemini
    hears as a turn, so Pupper says what happened. Deaf mode's own events never talk to Gemini."""
    display = ({"text": big.strip()[:80], "detail": detail.strip()[:160], "color": color, "seconds": seconds, "kind": kind}
               if big else None)
    ev = deaf.EVENTS.emit(kind, text, display)
    ev["for_gemini"] = for_gemini
    return ev


# ============================================================ what the robot does: the beacon
def beep_file() -> Path:
    """The chirp as a WAV file, made once (16-bit mono, with short fades so it doesn't click)."""
    path = Path(tempfile.gettempdir()) / "gemini-pupper-beacon.wav"
    if path.exists():
        return path
    fade = int(0.005 * RATE)
    frames = bytearray()
    for i, hz in enumerate(TONES_HZ):
        n = int(TONE_S * RATE)
        for k in range(n):
            env = min(1.0, k / fade, (n - k) / fade)
            frames += struct.pack("<h", int(0.6 * 32767 * env * math.sin(2 * math.pi * hz * k / RATE)))
        if i < len(TONES_HZ) - 1:
            frames += b"\0\0" * int(GAP_S * RATE)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return path


def player() -> list[str] | None:
    """The command that plays a WAV file on this machine, or None if there is none."""
    return next((cmd for cmd in PLAYERS if shutil.which(cmd[0])), None)


async def play_beep() -> bool:
    """Play one chirp on the robot's speaker; False if it couldn't."""
    cmd = player()
    if cmd is None:
        return False
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, str(beep_file()), stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.DEVNULL)
        return await proc.wait() == 0
    except OSError:
        log.exception("beacon: %s failed", cmd[0])
        return False


# The tests swap these for fakes (no speaker on a desk).
PLAY: Callable[[], Awaitable[bool]] = play_beep
CAN_PLAY: Callable[[], bool] = lambda: player() is not None


def beaconing() -> bool:
    task = BEACON["task"]
    return task is not None and not task.done()


def start_beacon(robot: Robot, name: str, seconds: float = BEACON_S) -> Result:
    """Beep from where the robot stands, once a second, until stopped or for ``seconds``."""
    if not CAN_PLAY():
        return Result(False, "can't beep: no sound player on the robot (pw-play, paplay or aplay)")
    stop_beacon()
    seconds = max(5.0, min(float(seconds), BEACON_MAX_S))
    stops = robot.stops  # a stop or e-stop after this point ends it
    BEACON.update(name=name, until=robot.now() + seconds, beeps=0, started=robot.now())

    async def beep_loop() -> None:
        while robot.now() < BEACON["until"] and robot.stops == stops:
            deaf.OWN_SOUND_UNTIL = time.time() + TONE_S * len(TONES_HZ) + QUIET_AFTER_S
            if not await PLAY():
                log.warning("beacon: the beep did not play")
            BEACON["beeps"] += 1
            await robot.sleep(BEEP_EVERY_S)
        BEACON.update(name="", until=0.0)

    BEACON["task"] = asyncio.get_running_loop().create_task(beep_loop())
    what = f"the {name}" if name else "where I am"
    return Result(True, f"beeping once a second at {what} for up to {seconds:.0f} s, so the person can walk to the sound;"
                        " it stops when they say they have it (beacon enabled=false), on stop, or on any move")


def stop_beacon() -> bool:
    """Stop beeping. True if it was beeping."""
    task = BEACON["task"]
    was = beaconing()
    if was:
        task.cancel()
    BEACON.update(name="", until=0.0, task=None)
    return was


# ============================================================ what the robot does: walking up to a thing
def update_target(name: str, box: list | None, score: float = 1.0, source: str = "eyes", bottom_known: bool = True,
                  frame_age_s: float = 0.0) -> bool:
    """The eyes service's latest fix on the wanted thing (box None: it looked and didn't see it). ``frame_age_s``:
    how long ago the frame it is about was taken (the detector on the Pi, or Gemma, takes 1-3 s to answer), so
    TARGET["time"] is when the thing was there, not when the answer came."""
    clean = None
    if box is not None:
        try:
            clean = [max(0.0, min(1.0, float(v))) for v in box][:4]
            if len(clean) != 4 or clean[2] <= 0 or clean[3] <= 0:
                clean = None
        except (TypeError, ValueError):
            clean = None
    try:
        age = max(0.0, min(10.0, float(frame_age_s or 0.0)))
    except (TypeError, ValueError):
        age = 0.0
    TARGET.update(time=time.time() - age, name=str(name or "").lower()[:60], box=clean,
                  score=float(score or 0.0), source=str(source or "")[:20], bottom_known=bool(bottom_known))
    return clean is not None


def target_box(name: str) -> tuple[list, str, bool] | None:
    """The freshest box for ``name``: the eyes service's, else the COCO detector's.
    (box, source, bottom_known) or None. A detector's box always has a real bottom edge."""
    if TARGET["box"] is not None and TARGET["name"] == name and time.time() - TARGET["time"] <= TARGET_FRESH_S:
        return TARGET["box"], TARGET["source"] or "eyes", bool(TARGET.get("bottom_known", True))
    hit = vision.visible(name)
    if hit:
        return hit["box"], "detector", True
    return None


def _det(box: list) -> dict:
    """A box as the detection dict vision's side_of / how_close read."""
    return {"label": "target", "score": 1.0, "box": box}


def bearing_deg(box: list) -> float:
    """Where the box is, in degrees from straight ahead (+ right): the longitude of its centre
    through Lab 5's calibrated lens model."""
    return fisheye_geom.box_bearing_deg(box)


def strip_at(bearing: float) -> str:
    """Which free-space strip a bearing falls in (the eyes service cuts them at -45, -15, 15, 45)."""
    return ("left" if bearing < -45 else "center-left" if bearing < -15 else "center" if bearing <= 15
            else "center-right" if bearing <= 45 else "right")


def strip_range_m(bearing: float) -> float | None:
    """The depth sense's range in the strip at ``bearing``: the nearest surface there, which is a
    chair leg as often as the thing aimed at, so it only ever slows a walk, never ends one."""
    for st in vision.fresh_freespace() or []:
        if st["name"] == strip_at(bearing) and st.get("closest_m") is not None:
            return st["closest_m"]
    return None


def steer(b: float, walk: float, turn: float, wz_min: float, vx_min: float, dist: float | None = None,
          near: float | None = None) -> tuple[float, float]:
    """The speed command (vx, wz_deg) toward a thing at bearing ``b`` (degrees, + right), ``dist``
    metres away (a depth-strip range) or at Lab 5 row ``near``, within the state's speeds."""
    if abs(b) <= AIM_DEG:
        wz = 0.0
    else:
        wz = max(-turn, min(turn, -KP_TURN * b))  # a target on the right (b > 0) needs a right turn (wz < 0)
        if abs(wz) < wz_min:
            wz = math.copysign(wz_min, wz)
    vx = walk if abs(b) <= WALK_WITHIN_DEG else 0.0
    if vx and ((near is not None and near >= SLOW_NEARNESS) or (near is None and dist is not None and dist <= SLOW_M)):
        vx = max(vx_min, walk / 2)
    return vx, wz


async def walk_up(robot: Robot, name: str, *, beacon: bool = True, retries: int = RETRIES) -> Result:
    """Queue a walk up to ``name`` (already seen by find): turn toward it, walk, slow near it, stop at
    arm's length, and (beacon) beep there. Stops when it is lost, something else is in the way, or
    at the move limits. Its result goes to Gemini as an event."""
    from part2_walk import check_move  # here, not at the top: the lab's walk, for its limits

    speeds = robot.speeds()
    limits = speeds.limits
    max_s = min(limits.duration_max, limits.distance_max / speeds.walk)  # within one move's time and distance
    refused = check_move(speeds.walk, 0.0, speeds.turn, max_s, limits)
    if refused:
        return Result(False, f"can't walk there: {refused}{speeds.where}")
    note = await prepare_to_move(robot)
    if robot.fallen:
        return Result(False, FELL_MESSAGE)
    state = robot.state_after_queue
    walker = robot.walking_controller(state)
    then = state if walker != LOCOMOTION_CONTROLLER else LOCOMOTION
    WANTED.update(name=name, since=time.time(), stops=robot.stops)
    vision.wake()

    def done(ok: bool, text: str) -> Result:
        robot.send_speed(0, 0, 0)
        WANTED.update(name="", since=0.0)
        beeping = start_beacon(robot, name) if ok and beacon else None
        if beeping is not None and beeping.ok:
            text += f"; {beeping.message}"
            announce("goto", text, (
                f"[robot, not the person speaking] {text}. Tell the person in one short sentence that the {name} is"
                " right next to you and to walk toward the beeping. Keep beeping: don't call beacon now. Only later,"
                " when the person says they have it ('got it', 'found it'), call beacon with enabled=false."),
                name.upper(), "here: walk toward the beeping", GREEN, BEACON_S)
            return Result(ok, text)
        if beeping is not None:
            text += f" ({beeping.message})"
        announce("goto", text, f"[robot, not the person speaking] {text}. Tell the person in one short sentence;"
                 " don't ask them a question.", name.upper(), "reached" if ok else text[:60], GREEN if ok else ORANGE)
        return Result(ok, text)

    async def look_again(why: str) -> Result:
        """Far from it and lost: search again by itself (once), instead of asking the person."""
        WANTED.update(name="", since=0.0)
        if retries <= 0:
            return done(False, f"{why}; I looked again and still can't find it, so I stopped where I am")
        robot.send_speed(0, 0, 0)
        text = f"{why}; looking again by myself"
        announce("search", text, f"[search result, not the person speaking] {text}. Say in a few words that you are"
                 " looking again, and don't ask the person anything: you will be told the result.")
        await find(robot, name, MAX_S_DEFAULT / 2, retries=retries - 1, again=True)  # queued right after this job
        return Result(False, text)

    async def job() -> Result:
        if robot.fallen:
            return Result(False, FELL_MESSAGE)
        res = await robot.switch_to(walker)
        if not res.ok:
            WANTED.update(name="", since=0.0)
            return res
        start = robot.now()
        moving = 0.0  # seconds spent sending a non-zero speed: what counts against max_s
        walked = 0.0
        last_box: list | None = None
        last_seen: float | None = None
        source = ""
        bottom_known = True
        fix_time = -1.0  # when the box in hand was taken; a fix is only new when this changes
        walked_since = 0.0  # metres walked since that fix: the fix's row is pushed down the image by it (dead reckoning)
        aim = 0.0  # the thing's bearing now: a fix sets it, and the turns sent since move it (dead reckoning)
        near_ticks = 0  # how many ticks in a row the thing's own bottom edge has put it within arm's length
        near_fixes: set = set()  # ... and from how many different fixes (one fix can be wrong; two in a row less so)
        # What was sent, tick by tick: (robot time, degrees turned, metres walked). A fix describes its frame,
        # taken 1-3 s before it arrives (the Pi's detector, Gemma); steering on it as if it were now turned past
        # the thing and then the wrong way, on the robot. So a new fix is moved on by what was sent since its frame.
        sent: list[tuple[float, float, float]] = []
        # Turn, then look: after a turn in place, stand still until a box from a frame taken after the turn
        # ended. Boxes from frames taken mid-turn are blurred and late, and two of them rarely agree.
        frame_at = float("-inf")  # the newest fix's frame, on the robot's clock
        fix_arrived = robot.now()  # when the newest fix came in (robot time)
        gaps: list[float] = []  # the last few times between fixes coming in: how long "no new fix" is normal here
        look_after: float | None = None  # robot time a turn in place ended, until a fix from after it arrives
        turning = False
        while moving < max_s - 1e-6 and robot.now() - start < WALK_UP_MAX_S - 1e-6:
            if robot.fallen:
                robot.send_speed(0, 0, 0)
                WANTED.update(name="", since=0.0)
                return Result(False, FELL_MESSAGE)
            hit = target_box(name)
            if hit:
                last_box, source, bottom_known = hit
                last_seen = robot.now()
                taken = TARGET["time"] if source != "detector" else vision.VISION["time"]
                if taken != fix_time:  # a new fix: aim at it, as it is now
                    fix_time = taken
                    frame_at = robot.now() - max(0.0, time.time() - taken)  # its frame, on the robot's clock
                    if look_after is not None and frame_at >= look_after:
                        look_after = None  # a look from after the turn: carry on
                    since = [(dw, dx) for t, dw, dx in sent if t >= frame_at]
                    aim = bearing_deg(last_box) + sum(dw for dw, _ in since)  # a left turn (+) swings a thing on the left (-) toward ahead
                    walked_since = sum(dx for _, dx in since)
                    if fix_time != -1.0 and last_seen is not None:
                        gaps = (gaps + [robot.now() - fix_arrived])[-3:]
                    fix_arrived = robot.now()
                    log.info("walk-up %s: box from %s, %.1f s old, at %+.0f deg in its frame, now %+.0f deg (%s);"
                             " %.1f s moving, %.1f s in all", name, source, max(0.0, time.time() - taken),
                             bearing_deg(last_box), aim,
                             "looking again after a turn" if look_after is not None else "steering",
                             moving, robot.now() - start)
            if last_seen is None:  # nothing yet: stand and give the eyes a moment
                if robot.now() - start >= SEE_S:
                    return await look_again(f"I couldn't see the {name} from where I stood (waited {SEE_S:.0f} s)")
                robot.send_speed(0, 0, 0)
                await robot.sleep(1.0 / LLM_CMD_RATE)
                continue
            assert last_box is not None
            near = fisheye_geom.box_nearness(last_box) if bottom_known else None  # Lab 5's row of its base, at the fix
            if near is not None and walked_since > 0.0:  # pushed down the image by the walking since the fix
                near = fisheye_geom.row_after_walking(near, walked_since)
            dist = None if bottom_known else strip_range_m(aim)  # a pointed-at thing: the depth strip, for slowing only
            close = near is not None and near >= SLOW_NEARNESS  # the last box, walked on, puts it close
            blind_walk = sum(dx for t, _, dx in sent if t >= fix_arrived) >= BLIND_WALK_M  # walked far on this box
            close_wait = max(CLOSE_LOST_S, min(CLOSE_LOST_MAX_S, 2.0 * max(gaps, default=0.0)))
            if close and robot.now() - fix_arrived >= close_wait and (near_ticks or blind_walk or not hit):
                return done(True, f"reached the {name}: it should be right in front of me, too close for my eyes"
                                  f" to see now ({vision.how_near(near)} when I last saw it)")
            if robot.now() - last_seen > LOST_S:
                return await look_again(f"lost sight of the {name} on the way")
            # Arrived: the thing's own bottom edge is at Lab 5's stop row, on several ticks and two
            # different fixes. Never from the depth strips: they see the nearest surface in that
            # direction, a chair leg as often as the thing.
            if near is not None and near >= ARRIVE_NEARNESS:
                near_ticks += 1
                near_fixes.add(fix_time)
            else:
                near_ticks, near_fixes = 0, set()
            if near_ticks >= ARRIVE_TICKS and len(near_fixes) >= 2:
                return done(True, f"reached the {name}: it is right in front of me, {vision.how_near(near)}")
            if near_ticks:  # hold still until the next fix confirms it
                robot.send_speed(0, 0, 0)
                await robot.sleep(1.0 / LLM_CMD_RATE)
                continue
            if look_after is not None or blind_walk:  # turned, or walked far on an old box: stand still and look
                robot.send_speed(0, 0, 0)
                sent.append((robot.now(), 0.0, 0.0))
                await robot.sleep(1.0 / LLM_CMD_RATE)
                continue
            vx, wz = steer(aim, speeds.walk, speeds.turn, limits.wz_min, limits.vx_min, dist, near)
            if turning and not (wz and not vx):  # a turn in place just ended
                turning = False
                look_after = robot.now()
                robot.send_speed(0, 0, 0)
                sent.append((robot.now(), 0.0, 0.0))
                await robot.sleep(1.0 / LLM_CMD_RATE)
                continue
            turning = bool(wz and not vx)
            if vx:
                blocked = vision.path_blocked(vx, 0.0)
                if blocked:
                    # The thing in the way is the target only if the target itself is about there: its
                    # own base is at the blocking row, or (no base known) it is dead ahead. A far
                    # target behind a near obstacle is a stop, not an arrival.
                    about_there = (near is not None and near >= NEAR_NEARNESS) or (near is None and abs(aim) <= AIM_DEG * 2)
                    if abs(aim) <= WALK_WITHIN_DEG and about_there:
                        return done(True, f"reached the {name}: stopped right in front of it ({blocked.split(';')[0]})")
                    return done(False, f"stopped on the way to the {name}: {blocked}")
            if walked + vx / LLM_CMD_RATE > limits.distance_max:
                return done(False, f"stopped after {walked:.1f} m (at most {limits.distance_max} m in one go): the {name}"
                                   " is still ahead")
            robot.send_speed(vx, 0.0, wz)
            sent.append((robot.now(), wz / LLM_CMD_RATE, vx / LLM_CMD_RATE))
            if vx or wz:
                moving += 1.0 / LLM_CMD_RATE
            if len(sent) > 15 * LLM_CMD_RATE:  # nothing older than a fix can be is needed
                del sent[:len(sent) - int(15 * LLM_CMD_RATE)]
            walked += vx / LLM_CMD_RATE
            walked_since += vx / LLM_CMD_RATE
            aim += wz / LLM_CMD_RATE  # a left turn (wz > 0) swings a thing on the left (aim < 0) toward ahead
            await robot.sleep(1.0 / LLM_CMD_RATE)
        how_long = (f"{max_s:.0f} s of walking" if moving >= max_s - 1e-6
                    else f"{WALK_UP_MAX_S:.0f} s (mostly standing and looking: my eyes were slow)")
        return done(False, f"gave up after {how_long}: the {name} is still ahead but I didn't reach it"
                           " (seen through " + (source or "nothing") + ")")

    seen = target_box(name)
    if seen:
        box, src, known = seen
        how = vision.how_near(fisheye_geom.box_nearness(box)) if known else "at an unknown distance"
        intro = f"Walking to the {name}: I see it {vision.side_of(_det(box))} ({bearing_deg(box):+.0f} degrees), {how} ({src})."
    else:
        intro = f"Walking to the {name} once my eyes find it (I don't see it yet; I wait up to {SEE_S:.0f} s)."
    intro += (f" I turn toward it and walk at up to {speeds.walk:g} m/s, stop at arm's length or if something"
              f" is in the way, and give up after {max_s:.0f} s of walking. You will be told the result.")
    return with_note(f"{note} {intro}", await robot.queue(f"go to {name}", job, 10.0, then=then))


# ============================================================ what the robot does: the search
class DeadReckoning:
    """Where Pupper roughly is, from the speeds sent: x, y in metres, heading in degrees (+ left),
    and the grid cells it has been in."""

    def __init__(self) -> None:
        self.x = self.y = 0.0
        self.heading = 0.0
        self.visited: set[tuple[int, int]] = {(0, 0)}

    def move(self, vx: float, wz_deg: float, dt: float) -> None:
        self.heading = (self.heading + wz_deg * dt + 180.0) % 360.0 - 180.0
        self.x += vx * math.cos(math.radians(self.heading)) * dt
        self.y += vx * math.sin(math.radians(self.heading)) * dt
        self.visited.add(self.cell(self.x, self.y))

    @staticmethod
    def cell(x: float, y: float) -> tuple[int, int]:
        return (math.floor(x / CELL_M + 0.5), math.floor(y / CELL_M + 0.5))

    def ahead(self, bearing_deg: float, dist: float) -> tuple[int, int]:
        """The cell ``dist`` metres away at ``bearing_deg`` (+ right of the heading)."""
        h = math.radians(self.heading - bearing_deg)
        return self.cell(self.x + dist * math.cos(h), self.y + dist * math.sin(h))


def sector_ranges(strips: list[dict] | None) -> dict[str, float]:
    """How far each strip is open, in metres (OPEN_M when nothing is in it; 0 without a depth sense)."""
    out = {}
    for name, _ in SECTORS:
        st = next((s for s in (strips or []) if s["name"] == name), None)
        if st is None:
            out[name] = 0.0 if strips is None else OPEN_M
        elif st.get("nearness") is not None:  # Lab 5's row of the nearest base: metres for a level camera 0.22 m up
            lat = st["nearness"] * 180.0 - 90.0
            out[name] = OPEN_M if lat < 1.0 else min(OPEN_M, 0.22 / math.tan(math.radians(lat)))
        elif st.get("closest_m") is not None:
            out[name] = min(OPEN_M, st["closest_m"])
        elif st.get("near_frac") is not None and st["near_frac"] >= vision.NEAR_FRAC_BLOCK:
            out[name] = 0.0
        else:
            out[name] = OPEN_M
    return out


def choose_direction(ranges: dict[str, float], pose: DeadReckoning, chooser: random.Random = rng) -> tuple[float, float] | None:
    """(bearing to turn to, how far to walk) or None when nothing ahead is worth it.

    Score = openness (0..1) x novelty (1 for a cell not yet visited, VISITED_WEIGHT otherwise)
    x a bonus for straight on x a little noise, so two runs don't trace the same path."""
    best = None
    for name, bearing in SECTORS:
        r = ranges.get(name, 0.0)
        if r <= BLOCKED_M:
            continue
        leg = min(LEG_M, r - vision.STOP_M)
        novelty = 1.0 if pose.ahead(bearing, leg) not in pose.visited else VISITED_WEIGHT
        score = (r / OPEN_M) * novelty * (STRAIGHT_BONUS if bearing == 0.0 else 1.0) * chooser.uniform(0.85, 1.15)
        if best is None or score > best[0]:
            best = (score, bearing, leg)
    if best is None or best[0] < 0.12:
        return None
    return best[1], best[2]


def thing_name(what: str) -> str:
    name = " ".join(what.split()).lower().strip(".!?")
    for lead in ("the ", "a ", "an ", "my ", "some "):
        if name.startswith(lead):
            name = name[len(lead):]
    return name


async def find(robot: Robot, looking_for: str, max_seconds: float = MAX_S_DEFAULT, *, retries: int = RETRIES,
               again: bool = False) -> Result:
    name = thing_name(looking_for)
    if not name:
        return Result(False, "what should I look for? e.g. find(what='the red mug')")
    # One search at a time: on the robot Gemini called find three times while one ran, and the queue grew to
    # 413 s of searches and walk-ups. Judged by the queue itself, not by EXPLORING/WANTED: a stop that clears
    # a queued search before it starts leaves those set. (again: the walk-up looking again by itself.)
    busy = next((d["name"] for d in (EXPLORING, WANTED) if d["name"] and d.get("stops") == robot.stops), "")  # a stop since: stale
    if not again and busy and (robot.current is not None or not robot.commands.empty()):
        return Result(True, f"already looking for the {busy}; you will be told the result, so just wait. To look"
                            " for something else instead, call stop first")
    try:
        max_s = float(max_seconds)
    except (TypeError, ValueError):
        return Result(False, f"max_seconds must be a number of seconds, at most {MAX_S_LIMIT:g}")
    if not 5.0 <= max_s <= MAX_S_LIMIT:
        return Result(False, f"max_seconds must be between 5 and {MAX_S_LIMIT:g} s (asked {max_s:g})")
    from part2_walk import check_move  # here, not at the top: the lab's walk, for its limits

    speeds = robot.speeds()
    limits = speeds.limits
    refused = check_move(speeds.walk, 0.0, speeds.turn, LEG_M / speeds.walk, limits)
    if refused:
        return Result(False, f"can't roam: {refused}{speeds.where}")
    note = await prepare_to_move(robot)
    if robot.fallen:
        return Result(False, FELL_MESSAGE)
    if not vision.eyes_alive():
        note = f"{note} Warning: my eyes service is not running (vision/eyes.py), so I have no depth sense and will only ever turn, not walk."
    vision.wake()
    state = robot.state_after_queue
    walker = robot.walking_controller(state)
    then = state if walker != LOCOMOTION_CONTROLLER else LOCOMOTION
    EXPLORING.update(name=name, since=time.time(), stops=robot.stops)
    cannot = (f" My detector doesn't know '{name}'; my eyes ask Gemma for it at each stop, and your eyes on the camera matter too."
              if not vision.detector_knows(name) else "")

    def done(ok: bool, text: str, *, found: dict | None = None) -> Result:
        robot.send_speed(0, 0, 0)
        EXPLORING.update(name="", since=0.0)
        if found:
            announce("found", text, (
                f"[search result, not the person speaking] You found the {name}: {text}. You are now walking up to it;"
                " you will be told when you get there. Tell the person in one short sentence."), name.upper(), "found", GREEN)
        else:
            announce("search", text, (
                f"[search result, not the person speaking] The search ended: {text}. Tell the person in one short sentence."))
        return Result(ok, text)

    async def tick(vx: float, wz: float, pose: DeadReckoning) -> None:
        robot.send_speed(vx, 0.0, wz)
        pose.move(vx, wz, 1.0 / LLM_CMD_RATE)
        await robot.sleep(1.0 / LLM_CMD_RATE)

    async def job() -> Result:
        pose = DeadReckoning()
        try:
            if robot.fallen:
                return Result(False, FELL_MESSAGE)
            res = await robot.switch_to(walker)
            if not res.ok:
                return res
            start = robot.now()
            legs = failed = sweeps = 0
            while robot.now() - start < max_s:
                # 1. stop and look
                end = robot.now() + LOOK_S
                while robot.now() < end - 1e-6:
                    if robot.fallen:
                        return done(False, FELL_MESSAGE)
                    await tick(0.0, 0.0, pose)
                hit = await vision.seen_now(name)
                if hit:
                    bearing = bearing_deg(hit["box"]) if hit.get("box") else None
                    where = f"{bearing:+.0f} degrees from straight ahead" if bearing is not None else "in view"
                    how = (f"after turning {sweeps * SWEEP_STEP_DEG:.0f} degrees" if legs == 0 and sweeps else
                           f"after {legs} legs" if legs else "right away")
                    res = done(True, f"found the {name} {how}: {where} ({hit['source']})", found=hit)
                    await walk_up(robot, name, retries=retries)  # queued: it runs right after this job, and beeps there
                    return res
                # 2. the sweep first: a full circle in place, looking after each step (cheap, and most things are near)
                if sweeps < SWEEP_STEPS:
                    sweeps += 1
                    end = robot.now() + SWEEP_STEP_DEG / speeds.turn
                    while robot.now() < end - 1e-6:
                        if robot.fallen:
                            return done(False, FELL_MESSAGE)
                        await tick(0.0, speeds.turn, pose)  # left, like turn()
                    robot.send_speed(0, 0, 0)
                    continue
                # 3. then roam: choose a direction
                choice = choose_direction(sector_ranges(vision.fresh_freespace()), pose)
                if choice is None:  # nothing ahead worth it: turn about and look again (a dead end counts as a failed leg)
                    bearing, leg = (TURN_AROUND_DEG + rng.uniform(-30.0, 30.0)) * rng.choice((-1.0, 1.0)), 0.0
                    failed += 1
                    if failed >= STUCK_LEGS:
                        return done(False, f"boxed in: nothing open in any direction {STUCK_LEGS} times in a row; no {name}"
                                           f" seen in {legs} legs. Ask the person to clear some floor, or say where to look")
                else:
                    bearing, leg = choice
                # 4. turn to it (turning in place is always allowed)
                turn_s = abs(bearing) / speeds.turn
                wz = math.copysign(speeds.turn, -bearing)  # a thing on the right (bearing > 0) needs a right turn (wz < 0)
                end = robot.now() + turn_s
                while robot.now() < end - 1e-6:
                    if robot.fallen:
                        return done(False, FELL_MESSAGE)
                    await tick(0.0, wz, pose)
                if leg <= 0.0:
                    continue  # turned about: look again before walking
                # 5. walk the leg, with the obstacle rule every tick, looking out for the thing cheaply
                walked = 0.0
                t = 0
                while walked < leg:
                    if robot.fallen:
                        return done(False, FELL_MESSAGE)
                    if vision.path_blocked(speeds.walk, 0.0):
                        break
                    await tick(speeds.walk, 0.0, pose)
                    walked += speeds.walk / LLM_CMD_RATE
                    t += 1
                    if t % 10 == 0 and vision.visible(name):  # the detector sees it mid-leg: stop and look properly
                        break
                robot.send_speed(0, 0, 0)
                legs += 1
                failed = failed + 1 if walked < MIN_LEG_M else 0
                if failed >= STUCK_LEGS:
                    return done(False, f"boxed in: {STUCK_LEGS} legs in a row could not start; no {name} seen in {legs} legs."
                                       " Ask the person to clear some floor, or say where to look")
            return done(False, f"no {name} found in {max_s:.0f} s and {legs} legs; I have seen about"
                               f" {len(pose.visited)} square metres. Ask me to keep looking, or say where it might be")
        except asyncio.CancelledError:
            EXPLORING.update(name="", since=0.0)
            raise

    intro = (f"Finding the {name}: I look, then turn a full circle in {SWEEP_STEP_DEG:.0f}-degree steps looking after each,"
             f" then roam: the most open direction that leads somewhere new, up to {LEG_M:g} m at a time, for up to"
             f" {max_s:.0f} s in all. Anything in the way stops a leg. When I find it I walk up to it and beep there,"
             " so you can walk to the sound. You will be told the result." + cannot)
    return with_note(f"{note} {intro}", await robot.queue(f"find {name}", job, max_s, then=then))


# ============================================================ what Gemini reads
@tool(
    "find",
    "Blind mode: find a thing for a person who can't see it, walk up to it, and beep beside it so they"
    " can walk to the sound. 'Where is my cup', 'find my keys', 'look for the white container'. Pupper"
    " looks (its detector for everyday things, Gemma for anything you can name), turns a full circle"
    " looking, then roams the room, stopping for anything in the way, for up to max_seconds. You are told"
    " when it finds the thing (a [search result]) and when it gets there. Only when a person asks."
    " If follow mode is on, this ends it: say so first.",
    what=Param("string", "What to find, in a few words: 'the red mug', 'my keys', 'the white container'."),
    max_seconds=Param("number", f"How long at most, 5 to {MAX_S_LIMIT:g} s (default {MAX_S_DEFAULT:g}); a turn in place is about 20 s of it.", required=False),
)
async def find_tool(robot: Robot, what: str, max_seconds: float = MAX_S_DEFAULT) -> Result:
    return await find(robot, what, max_seconds)


@tool(
    "beacon",
    "Beep from where Pupper stands, once a second, so a person who can't see can walk to the sound."
    " After find walks up to a thing it starts by itself; call it with enabled=true when a person"
    " asks you to beep or make a sound so they can find you or the thing. Call it with enabled=false"
    " as soon as they say they have it ('got it', 'found it', 'stop beeping'). Any move or stop also ends it.",
    enabled=Param("boolean", "true to start beeping, false to stop."),
    seconds=Param("number", f"How long at most, 5 to {BEACON_MAX_S:g} s (default {BEACON_S:g}).", required=False),
)
async def beacon_tool(robot: Robot, enabled: bool, seconds: float = BEACON_S) -> Result:
    if not enabled:
        if beaconing() and BEACON["name"] and robot.now() - BEACON["started"] < AUTO_GRACE_S:
            return Result(False, f"still beeping at the {BEACON['name']}: I only just started, and the person needs the"
                                 " sound to walk to it. Call beacon with enabled=false only after they say they have it"
                                 " ('got it'); if they want it to stop right now, call stop")
        return Result(True, "stopped beeping" if stop_beacon() else "I wasn't beeping")
    res = start_beacon(robot, "", seconds)
    if res.ok:
        announce("beacon", "beeping", "", "BEEPING", "walk toward the sound; say 'got it' to stop", GREEN,
                 min(float(seconds), BEACON_MAX_S))
    return res
