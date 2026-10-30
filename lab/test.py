#!/usr/bin/env python3
"""Run the desk tests (a fake robot: no Pupper, no ROS, no Gemini needed).

    python3 lab/test.py            # everything: the lab's checks, then deaf mode's
    python3 lab/test.py todo3      # one TODO (todo1 ... todo11)
    python3 lab/test.py part2      # one part of the lab (part1 ... part7; part6, the spider, is the notebook)
    python3 lab/test.py robot      # the provided robot (should always pass)
    python3 lab/test.py deaf       # deaf mode (lab/deaf_mode.py)

The lab's tests are in robot/test_tools.py, deaf mode's in robot/test_deaf_mode.py.
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
if args:
    sys.exit(run("test_tools.py", *args))
lab = run("test_tools.py")
print("=" * 60)
deaf = run("test_deaf_mode.py")
sys.exit(lab or deaf)
