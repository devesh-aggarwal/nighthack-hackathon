#!/usr/bin/env python3
"""Desk tests for deaf mode (lab/deaf_mode.py). No robot, no ROS, no Gemini, no mic.

    python3 lab/test.py deaf     # just these
    python3 lab/test.py          # the lab's checks, then these

They run on test_tools.py's fake robot and fake clock, with a fake detector in place of
hearing/listener.py.
"""

from __future__ import annotations

import asyncio
import math
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
    deaf_mode._showing = ""  # start each check with nothing on the screen
    info = {"label": label, "confidence": 0.8} if label else {}
    return deaf_mode.hearing_detected(rig.ts, {"cls": cls, "score": 50.0, "info": info})


async def deaf_tests() -> None:
    print("Deaf mode: urgent sounds on the screen")
    rig = Rig()
    deaf_mode.DEAF_MODE = False
    deaf_mode.clear_display()
    rig.ts.hearing = FakeHearing()
    await call(rig, "lie_down")  # lying down Pupper only shows sounds (spin_tests: standing, it turns too)
    rig.ts.busy_until = rig.ts.now() - 10.0  # long after lying down: not its own motors

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
    for cls in ("shout", "bang"):
        res = heard(rig, cls)
        check(f"a {cls} is not urgent any more (too many false alarms)", "not an urgent" in res.message, res.message)

    # The alert lasts as long as the sound: hearing_tick gets YAMNet's scores every 0.5 s.
    heard(rig, "siren", "Siren")
    before = deaf_mode.EVENTS.last_id
    res = deaf_mode.hearing_detected(rig.ts, {"cls": "siren", "score": 70.0})
    check("the same sound again while it shows is one alert, not a new one",
          "already" in res.message and deaf_mode.EVENTS.since(before) == [], res.message)
    deaf_mode.hearing_tick({"siren": 0.6})
    check("while YAMNet still hears the siren, SIREN stays", deaf_mode.display_state()["text"] == "SIREN")
    deaf_mode.hearing_tick({"siren": 0.02})
    check("... and a moment of quiet within MIN_SHOW_S doesn't clear it yet", deaf_mode.display_state() is not None)
    deaf_mode._last_heard -= deaf_mode.MIN_SHOW_S
    deaf_mode.hearing_tick({"siren": 0.02})
    evs = deaf_mode.EVENTS.since(before)
    check("once the siren stops, the screen clears right away, and the app is told (sound_end)",
          deaf_mode.display_state() is None and [e["kind"] for e in evs] == ["sound_end"], str(evs))
    heard(rig, "knock")
    res = deaf_mode.hearing_detected(rig.ts, {"cls": "crash", "score": 60.0, "info": {"label": "Glass", "confidence": 0.5}})
    check("a different urgent sound replaces the one on the screen at once", deaf_mode.display_state()["text"] == "CRASH",
          res.message)
    rig.ts.busy_until = rig.ts.now() + 3.0  # a motion job is running
    res = heard(rig, "knock")
    check("while Pupper moves, a knock is its own motors: ignored", "motors" in res.message, res.message)
    rig.ts.busy_until = rig.ts.now() - 10.0
    deaf_mode.DISPLAY["until"] = time.time() - 1
    check("with no word from YAMNet, the screen clears after MAX_SHOW_S", deaf_mode.display_state() is None)

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


async def spin_tests() -> None:
    print("Deaf mode: a full slow circle so the red screen can be seen")
    rig = Rig()  # standing in locomotion mode
    deaf_mode._spin = None  # a spin task from deaf_tests' event loop is gone
    deaf_mode._spin_until = 0.0
    deaf_mode.DEAF_MODE = True
    deaf_mode.clear_display()
    await rig.settle()
    rig.robot.published.clear()
    res = heard(rig, "siren", "Siren")
    again = heard(rig, "doorbell", "Doorbell")  # before the spin has started
    await deaf_mode._spin
    await rig.settle()
    turns = [(t, m) for t, m in rig.robot.twists() if abs(m.angular.z) > 1e-6]
    rates = sorted({round(math.degrees(m.angular.z), 1) for _, m in turns})
    spun = (turns[-1][0] - turns[0][0]) * math.degrees(turns[0][1].angular.z) if turns else 0.0
    check("standing, an urgent sound also turns Pupper left, slowly", "turning a full circle" in res.message
          and rates == [deaf_mode.SPIN_DEG_S], f"{res.message}; turn rates sent: {rates} deg/s")
    check("... one full circle", abs(spun - 360) < 10 and abs(deaf_mode.SPIN_DEG_S * deaf_mode.SPIN_S - 360) < 1e-9,
          f"turned about {spun:.0f} deg")
    check("... and stands still after", not rig.robot.twists()[-1][1].angular.z)
    check("a second sound before the spin ends doesn't queue another", "turning" not in again.message
          and spun < 370, again.message)

    deaf_mode._showing, deaf_mode._last_heard = "siren", time.time() - 5.0  # the siren stopped 5 s ago
    deaf_mode._spin_until = time.time() + 3.0
    deaf_mode.hearing_tick({})
    check("the screen stays red until the circle is done, even after the sound stops",
          deaf_mode.display_state() is not None and deaf_mode._showing == "siren", str(deaf_mode.display_state()))
    deaf_mode._spin_until = time.time() - 0.1
    deaf_mode.hearing_tick({})
    check("... and clears once it is", deaf_mode.display_state() is None)
    deaf_mode._spin_until = 0.0

    await call(rig, "lie_down")
    rig.ts.busy_until = rig.ts.now() - 10.0  # long after lying down: not its own motors
    rig.robot.published.clear()
    res = heard(rig, "siren", "Siren")
    await rig.settle()
    check("lying down, the screen goes red but Pupper doesn't move", "turning" not in res.message
          and not rig.robot.twists() and deaf_mode.display_state()["text"] == "SIREN", res.message)
    deaf_mode.DEAF_MODE = False
    rig.close()


if __name__ == "__main__":
    asyncio.run(deaf_tests())
    asyncio.run(spin_tests())
    if tt.FAIL:
        print(f"{tt.FAIL} check(s) failed, {tt.PASS} passed.")
        sys.exit(1)
    print(f"All {tt.PASS} deaf mode checks passed.")
