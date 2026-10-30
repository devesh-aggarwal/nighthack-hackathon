#!/usr/bin/env python3
"""Desk tests for deaf mode (lab/deaf_mode.py). No robot, no ROS, no Gemini, no mic.

    python3 lab/test.py deaf     # just these
    python3 lab/test.py          # the lab's checks, then these

They run on test_tools.py's fake robot and fake clock, with a fake detector in place of
hearing/listener.py.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_tools as tt  # noqa: E402  a fake ROS, and lab/ loaded
from test_tools import Rig, check, tools  # noqa: E402

import deaf_mode  # noqa: E402


class FakeHearing:
    """The robot server's detector, without a mic. mic=False: it can't start, as with none plugged in."""

    def __init__(self, mic: bool = True) -> None:
        self.mic, self.enabled, self.calls = mic, False, []

    async def set_enabled(self, on: bool, sensitivity: str = "medium") -> tuple[bool, str]:
        self.calls.append(on)
        if on and not self.mic:
            return False, "couldn't open the mic: Pupper has no mic"
        self.enabled = on
        return True, "listening with the calibrated detector"


async def call(rig: Rig, tool: str, **args):
    res = await tools.call(rig.ts, tool, args)
    await rig.settle()
    return res


def heard(rig: Rig, cls: str, label: str = "") -> object:
    deaf_mode._last_alert = 0.0  # one alert at a time: start each check fresh
    info = {"label": label, "confidence": 0.8} if label else {}
    return deaf_mode.hearing_detected(rig.ts, {"cls": cls, "score": 50.0, "info": info})


async def deaf_tests() -> None:
    print("Deaf mode: urgent sounds on the screen")
    rig = Rig()
    deaf_mode.DEAF_MODE = False
    deaf_mode.clear_display()
    rig.ts.hearing = FakeHearing()

    res = heard(rig, "doorbell", "Doorbell")
    check("deaf mode is off at start: a doorbell is ignored", "off" in res.message and deaf_mode.display_state() is None,
          res.message)
    res = await call(rig, "deaf_mode", enabled=True)
    check("deaf_mode(true) turns it on and starts the detector", res.ok and "deaf mode on" in res.message
          and rig.ts.hearing.enabled, res.message)

    before = deaf_mode.EVENTS.last_id
    res = heard(rig, "doorbell", "Doorbell")
    shown, evs = deaf_mode.display_state(), deaf_mode.EVENTS.since(before)
    check("a doorbell turns the whole screen red with DOORBELL", res.ok and shown and shown["text"] == "DOORBELL"
          and shown["color"] == deaf_mode.URGENT_COLOR and shown["kind"] == "sound", str(shown))
    check("... the app gets one event carrying it, and Gemini is not told",
          len(evs) == 1 and evs[0]["display"]["text"] == "DOORBELL" and evs[0]["for_gemini"] == "", str(evs))
    check("since(last) is empty: nothing is delivered twice", deaf_mode.EVENTS.since(deaf_mode.EVENTS.last_id) == [])
    for cls, words in deaf_mode.URGENT.items():
        heard(rig, cls)
        check(f"a {cls} shows {words[0]}", deaf_mode.display_state()["text"] == words[0], str(deaf_mode.display_state()))

    res = heard(rig, "speech", "Speech")
    check("speech is not an urgent sound", "not an urgent" in res.message, res.message)
    deaf_mode._last_alert = 0.0
    deaf_mode.hearing_detected(rig.ts, {"cls": "bang", "score": 50.0})
    res = deaf_mode.hearing_detected(rig.ts, {"cls": "siren", "score": 80.0})
    check("a second sound right after is ignored (one alert at a time)", "already" in res.message, res.message)
    res = deaf_mode.hearing_detected(rig.ts, {"cls": "knock", "score": 60.0})
    check("... but a knock right after a LOUD BANG replaces it with KNOCKING",
          deaf_mode.display_state()["text"] == "KNOCKING", res.message)
    rig.ts.busy_until = rig.ts.now() + 3.0  # a motion job is running
    res = heard(rig, "bang")
    check("while Pupper moves, a bang is its own motors: ignored", "motors" in res.message, res.message)
    rig.ts.busy_until = rig.ts.now() - 10.0
    deaf_mode.DISPLAY["until"] = time.time() - 1
    check("after URGENT_SECONDS the screen is clear", deaf_mode.display_state() is None)

    res = await call(rig, "deaf_mode", enabled=False)
    check("deaf_mode(false) turns it off and stops the detector", res.ok and not deaf_mode.DEAF_MODE
          and not rig.ts.hearing.enabled, res.message)
    rig.ts.hearing = FakeHearing(mic=False)
    res = await call(rig, "deaf_mode", enabled=True)
    check("with no mic, deaf mode is refused and says why", not res.ok and "no mic" in res.message
          and not deaf_mode.DEAF_MODE, res.message)
    rig.ts.hearing = None
    res = await call(rig, "deaf_mode", enabled=True)
    check("with no detector at all, deaf mode is refused and says why", not res.ok and "no sound detector" in res.message,
          res.message)

    prompt = tt.system_prompt()
    check("the prompt tells Gemini when to use deaf mode", "deaf_mode" in prompt and "hard of hearing" in prompt)
    rig.close()


if __name__ == "__main__":
    asyncio.run(deaf_tests())
    if tt.FAIL:
        print(f"{tt.FAIL} check(s) failed, {tt.PASS} passed.")
        sys.exit(1)
    print(f"All {tt.PASS} deaf mode checks passed.")
