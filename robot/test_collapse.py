#!/usr/bin/env python3
"""Desk tests for collapse watch (lab/collapse_watch.py). No robot, no ROS, no Gemini, no camera.

    python3 lab/test.py collapse     # just these
    python3 lab/test.py              # everything

They run on test_tools.py's fake robot and fake clock, with a fake vision checker in place of
vision/gemini_vision.py: it answers from a script instead of asking Gemini. A temporary contact
file and a fake _place_call stand in for the emergency call: nobody is phoned.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_tools as tt  # noqa: E402  a fake ROS, and lab/ loaded
from test_tools import Rig, _Msg, _real_sleep, check, tools  # noqa: E402

import collapse_watch as cw  # noqa: E402
import deaf_mode  # noqa: E402
import emergency  # noqa: E402
import vision  # noqa: E402  blind mode's senses: the obstacle rule the approach's steps use

NOBODY = {"down": False, "x": 0.5, "nearest_m": 99.0, "path_clear": True, "why": "an empty room"}
DOWN_LEFT = {"down": True, "x": 0.25, "nearest_m": 2.0, "path_clear": True, "why": "a person face down on the rug"}
CLOSE = {"down": True, "x": 0.5, "nearest_m": 0.9, "path_clear": False, "why": "a person lying on the floor, close"}


def eyes(center_m: float | None = 2.0) -> None:
    """The eyes service posts its depth sense: nothing close (or something at center_m straight ahead).
    None: no eyes service at all."""
    vision.POSTERS.clear()
    vision.FREESPACE.update(time=0.0, strips=[])
    vision.update_detections([])
    if center_m is not None:
        vision.update_freespace([{"name": n, "closest_m": center_m if n == "center" else 2.0} for n in vision.STRIPS],
                                "metric", source="test")


def ahead(nearest_m: float, clear: bool = True) -> dict:
    """Someone down straight ahead, the nearest part of them nearest_m away."""
    return {"down": True, "x": 0.5, "nearest_m": nearest_m, "path_clear": clear, "why": "a person on the floor"}


CALLS: list[str] = []  # what the emergency contact would hear, one per call


def _fake_call(contact: dict, message: str) -> tuple[bool, str]:
    CALLS.append(message)
    return True, "queued"


emergency._place_call = _fake_call  # before anything runs: the real contact file may be on this machine
emergency.CONTACT_FILE = Path(tempfile.mkdtemp()) / "emergency_contact.json"
emergency.CONTACT_FILE.write_text(json.dumps({"name": "Sam", "to": "+15551234567", "from": "+15557654321",
                                              "account_sid": "ACtest", "auth_token": "not-a-real-token"}))

MOVES: list[tuple[str, float]] = []  # what the approach asked walk for: ("turn", deg) or ("walk", metres)
_walk = cw.walk


async def _recording_walk(robot, vx, vy, wz_deg, duration):
    MOVES.append(("walk", round(vx * duration, 2)) if vx else ("turn", round(wz_deg * duration, 1)))
    return await _walk(robot, vx, vy, wz_deg, duration)


_step = cw._step


async def _recording_step(robot, metres):
    MOVES.append(("walk", round(metres, 2)))
    return await _step(robot, metres)


cw.walk, cw._step = _recording_walk, _recording_step


def moves(rig: Rig) -> list[tuple[str, float]]:
    """The approach's moves since fresh(), and only those the fake robot really started walking."""
    return list(MOVES) if any(m.linear.x or m.angular.z for _, m in rig.robot.twists()) else []


class FakeVision:
    """Answers look() from a script; the last answer repeats."""

    def __init__(self, *answers: dict) -> None:
        self.answers, self.calls = list(answers), 0

    async def look(self, jpeg: bytes) -> dict | None:
        self.calls += 1
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def fresh(*answers: dict) -> Rig:
    rig = Rig()
    MOVES.clear()
    CALLS.clear()
    emergency._last_call = 0.0
    cw.WATCHING, cw._stage, cw._task, cw._seen_in_row, cw._quiet_until = True, "", None, 0, 0.0
    deaf_mode.clear_display()
    eyes()
    rig.ts.vision = FakeVision(*answers)
    rig.ts.latest_image.put_nowait(_Msg(data=b"\xff\xd8 a jpeg"))
    return rig


async def until(cond, limit: int = 200_000) -> bool:
    for _ in range(limit):
        if cond():
            return True
        await _real_sleep(0)
    return False


async def spot(rig: Rig) -> list[str]:
    """Look CONFIRM times: someone is down, and the emergency starts."""
    return [await cw.look_once(rig.ts) for _ in range(cw.CONFIRM)]


async def collapse_tests() -> None:
    print("Collapse watch: someone collapses in view")
    check("collapse watch is on by default", cw.WATCHING is True)

    rig = fresh(NOBODY)
    res = await cw.look_once(rig.ts)
    check("an empty room: nothing happens", res.startswith("nobody down") and not cw._stage, res)
    rig.close()

    # The whole thing, with no answer: walk up, ask, alarm, "calling 911".
    rig = fresh(DOWN_LEFT, DOWN_LEFT, ahead(2.0), ahead(1.6), ahead(1.2), ahead(1.0))
    before = deaf_mode.EVENTS.last_id
    first, second = await spot(rig)
    check("one sighting isn't enough (CONFIRM in a row)", "may be down" in first and "going to help" in second,
          f"{first} / {second}")
    await asyncio.wait_for(cw._task, timeout=10)
    await rig.settle()
    did = moves(rig)
    walks = [m.linear.x for _, m in rig.robot.twists() if m.linear.x]
    check("it first turns toward someone on the left of the picture (a left turn)",
          did and did[0][0] == "turn" and did[0][1] > 0, str(did))
    check("... then walks slowly, in short steps (at most MAX_STEP_M), looking again after each",
          [k for k, _ in did[1:]] == ["walk"] * 3 and all(a <= cw.MAX_STEP_M + 1e-6 for _, a in did[1:])
          and all(abs(v - cw.APPROACH_SPEED) < 1e-6 for v in walks), str(did))
    check("... and stops about STOP_AT_M from the nearest part of them",
          abs(sum(a for k, a in did if k == "walk") - (2.0 - cw.STOP_AT_M)) < 0.05, str(did))
    evs = deaf_mode.EVENTS.since(before)
    shown = [(e["kind"], (e["display"] or {}).get("text"), (e["display"] or {}).get("siren")) for e in evs]
    check("then: ARE YOU OKAY? flashing, then with the siren, then CALLING 911 and the call, then cleared",
          shown == [("collapse", None, None), ("emergency", "ARE YOU OKAY?", False),
                    ("emergency", "ARE YOU OKAY?", True), ("emergency", "CALLING 911", False),
                    ("emergency_call", None, None), ("emergency_end", None, None)], str(shown))
    ask, calling = evs[1], evs[3]
    check("the app asks \"Are you okay?\" out loud itself", ask["display"].get("say") == "Are you okay?",
          str(ask["display"]))
    check("Gemini is told it asked \"Are you okay?\" and how to answer", "Are you okay?" in ask["for_gemini"]
          and "emergency_response" in ask["for_gemini"], ask["for_gemini"])
    check("the screen flashes red", ask["display"]["flash"] and ask["display"]["color"] == deaf_mode.URGENT_COLOR)
    check("with no answer, Gemini is told to say it is calling 911 and the emergency contacts",
          "911" in calling["for_gemini"] and "emergency contacts" in calling["for_gemini"], calling["for_gemini"])
    check("... and the emergency contact is phoned, once, without anyone asking: someone collapsed and didn't answer",
          len(CALLS) == 1 and "collapsed" in CALLS[0] and "didn't answer" in CALLS[0], str(CALLS))
    check("... and the event says who was called", "calling Sam" in evs[4]["text"], evs[4]["text"])
    res = await cw.look_once(rig.ts)
    check("afterwards it is quiet for a while (no alarm again at once)", res == "quiet for now", res)
    rig.close()

    # Not running people over: it only walks on a fresh look that says the way is clear.
    rig = fresh(DOWN_LEFT, DOWN_LEFT, ahead(2.0, clear=False))
    await spot(rig)
    await until(lambda: cw._stage in ("ask", "alarm", "calling"))
    did = moves(rig)
    check("after turning it looks again: legs in the way -> no step forward, it asks from there",
          [k for k, _ in did] == ["turn"], str(did))
    await tools.call(rig.ts, "emergency_response", {"okay": True})
    rig.close()
    rig = fresh(DOWN_LEFT, DOWN_LEFT, ahead(2.0))
    eyes(center_m=0.3)
    await spot(rig)
    await until(lambda: cw._stage in ("ask", "alarm", "calling"))
    forward = [m for _, m in rig.robot.twists() if m.linear.x > 0]
    check("the obstacle sense sees something close ahead: the step is tried but never sends a speed forward",
          ("walk", cw.MAX_STEP_M) in MOVES and not forward, f"{MOVES} {rig.outcomes()[-3:]}")
    await tools.call(rig.ts, "emergency_response", {"okay": True})
    rig.close()
    rig = fresh(DOWN_LEFT, DOWN_LEFT, ahead(2.0))
    eyes(center_m=None)
    await spot(rig)
    await until(lambda: cw._stage in ("ask", "alarm", "calling"))
    did = moves(rig)
    check("no eyes service (no obstacle sense): it turns to face them, but doesn't walk up blind",
          [k for k, _ in did] == ["turn"], str(did))
    await tools.call(rig.ts, "emergency_response", {"okay": True})
    rig.close()
    rig = fresh(dict(DOWN_LEFT, nearest_m=1.05), dict(DOWN_LEFT, nearest_m=1.05))
    await spot(rig)
    await until(lambda: cw._stage in ("ask", "alarm", "calling"))
    check("someone's feet already within STOP_AT_M: it doesn't move at all, not even to turn",
          moves(rig) == [], str(moves(rig)))
    await tools.call(rig.ts, "emergency_response", {"okay": True})
    rig.close()

    # "I'm okay" during the alarm.
    rig = fresh(CLOSE)
    await spot(rig)
    check("someone right in front: the alarm comes without walking", await until(lambda: cw._stage == "alarm"),
          cw._stage)
    check("the siren sounds while it asks", deaf_mode.display_state()["siren"] is True, str(deaf_mode.display_state()))
    deaf_mode.DEAF_MODE = True
    res = deaf_mode.hearing_detected(rig.ts, {"cls": "siren", "score": 70.0, "info": {}})
    deaf_mode.DEAF_MODE = False
    check("deaf mode doesn't show Pupper's own siren", "own emergency alarm" in res.message, res.message)
    before = deaf_mode.EVENTS.last_id
    res = await tools.call(rig.ts, "emergency_response", {"okay": True})
    await _real_sleep(0)
    evs = deaf_mode.EVENTS.since(before)
    check("\"I'm okay\": the alarm stops, the screen clears, and nobody is called",
          res.ok and "nobody is called" in res.message and deaf_mode.display_state() is None and not cw._stage
          and [e["kind"] for e in evs] == ["emergency_end"] and not CALLS, f"{res.message} {evs} {CALLS}")
    res = await cw.look_once(rig.ts)
    check("... and it doesn't check on them again for a while", res == "quiet for now", res)
    rig.close()

    # "Help me" during the alarm.
    rig = fresh(CLOSE)
    await spot(rig)
    await until(lambda: cw._stage == "alarm")
    res = await tools.call(rig.ts, "emergency_response", {"okay": False})
    check("\"help me\": CALLING 911 right away", res.ok and "911" in res.message
          and deaf_mode.display_state()["text"] == "CALLING 911", f"{res.message} {deaf_mode.display_state()}")
    await asyncio.wait_for(cw._task, timeout=10)
    check("... and the emergency contact is phoned: someone collapsed and asked for help",
          len(CALLS) == 1 and "asked for help" in CALLS[0], str(CALLS))
    rig.close()

    # No contact set up: it still says so, and nobody is phoned.
    rig = fresh(CLOSE)
    contact = emergency.CONTACT_FILE.read_text()
    emergency.CONTACT_FILE.unlink()
    before = deaf_mode.EVENTS.last_id
    await spot(rig)
    await until(lambda: cw._stage == "alarm")
    await tools.call(rig.ts, "emergency_response", {"okay": False})
    await asyncio.wait_for(cw._task, timeout=10)
    texts = [(e["display"] or {}).get("text") or e["text"] for e in deaf_mode.EVENTS.since(before)]
    check("with no contact file: CALLING 911 is still shown, nobody is phoned, and the event says why",
          "CALLING 911" in texts and not CALLS and any("no emergency contact" in t for t in texts), str(texts))
    emergency.CONTACT_FILE.write_text(contact)
    rig.close()

    # A fallen robot doesn't walk, but still asks.
    rig = fresh(DOWN_LEFT)
    rig.ts.fallen = tt.LOCOMOTION_CONTROLLER
    before = deaf_mode.EVENTS.last_id
    await spot(rig)
    await until(lambda: cw._stage in ("ask", "alarm", "calling"))
    moved = [m for _, m in rig.robot.twists() if m.linear.x or m.angular.z]
    texts = [(e["display"] or {}).get("text") for e in deaf_mode.EVENTS.since(before)]
    check("fallen over: it doesn't walk, and still asks", not moved and "ARE YOU OKAY?" in texts, f"{moved} {texts}")
    await tools.call(rig.ts, "emergency_response", {"okay": True})
    rig.ts.fallen = None
    rig.close()

    # The switches.
    rig = fresh(DOWN_LEFT)
    res = await tools.call(rig.ts, "emergency_response", {"okay": True})
    check("emergency_response with no emergency is refused, and says why", not res.ok and "no emergency" in res.message,
          res.message)
    res = await tools.call(rig.ts, "collapse_watch", {"enabled": False})
    check("collapse_watch(false) turns it off", res.ok and not cw.WATCHING, res.message)
    res = await cw.look_once(rig.ts)
    check("... and nothing is checked", res.startswith("not watching") and rig.ts.vision.calls == 0, res)
    res = await tools.call(rig.ts, "collapse_watch", {"enabled": True})
    check("collapse_watch(true) turns it back on", res.ok and cw.WATCHING, res.message)
    rig.ts.vision = None
    res = await tools.call(rig.ts, "collapse_watch", {"enabled": True})
    check("with no vision checker (no Gemini key), it is refused and says why", not res.ok and "key" in res.message,
          res.message)
    rig.close()

    prompt = tt.system_prompt()
    check("the prompt tells Gemini about emergency_response", "emergency_response" in prompt and "911" in prompt)


if __name__ == "__main__":
    asyncio.run(collapse_tests())
    if tt.FAIL:
        print(f"{tt.FAIL} check(s) failed, {tt.PASS} passed.")
        sys.exit(1)
    print(f"All {tt.PASS} collapse watch checks passed.")
