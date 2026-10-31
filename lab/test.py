#!/usr/bin/env python3
"""Run the desk tests (a fake robot: no Pupper, no ROS, no Gemini needed).

    python3 lab/test.py            # everything: the lab's checks, then deaf mode's, blind mode's and collapse watch's
    python3 lab/test.py todo3      # one TODO (todo1 ... todo11)
    python3 lab/test.py part2      # one part of the lab (part1 ... part7; part6, the spider, is the notebook)
    python3 lab/test.py robot      # the provided robot (should always pass)
    python3 lab/test.py deaf       # deaf mode (lab/deaf_mode.py)
    python3 lab/test.py blind      # blind mode (lab/blind_mode.py); or one group: blind find (vision, walk, find, beacon, debug, declarations)
    python3 lab/test.py collapse   # collapse watch (lab/collapse_watch.py)
    python3 lab/test.py emergency  # the emergency contact call (lab/emergency.py)

The lab's tests are in robot/test_tools.py, deaf mode's in robot/test_deaf_mode.py, blind mode's in
robot/test_blind_mode.py, collapse watch's in robot/test_collapse.py, the emergency call's in
robot/test_emergency.py.
Each file runs in its own interpreter (they each install a fake ROS).
"""
import subprocess
import sys
from pathlib import Path

ROBOT = Path(__file__).resolve().parent.parent / "robot"


def run(script: str, *args: str) -> int:
    return subprocess.call([sys.executable, str(ROBOT / script), *args])


args = sys.argv[1:]
if args == ["deaf"]:
    sys.exit(run("test_deaf_mode.py"))
if args[:1] == ["blind"]:
    sys.exit(run("test_blind_mode.py", *args[1:]))
if args == ["collapse"]:
    sys.exit(run("test_collapse.py"))
if args == ["emergency"]:
    sys.exit(run("test_emergency.py"))
if args:
    sys.exit(run("test_tools.py", *args))
lab = run("test_tools.py")
print("=" * 60)
deaf = run("test_deaf_mode.py")
print("=" * 60)
blind = run("test_blind_mode.py")
print("=" * 60)
collapse = run("test_collapse.py")
print("=" * 60)
emergency = run("test_emergency.py")
sys.exit(lab or deaf or blind or collapse or emergency)
