# Notes for coding agents (Pupper: Lab 6 + deaf mode)

This repository is Gemini Pupper, a finished copy of Stanford CS 123 Lab 6
(every part of the lab is done) that Gemini Live talks through, plus
**deaf mode**. When a person says they are deaf or hard of
hearing, Pupper keeps listening, and an urgent sound (siren, alarm, doorbell,
knocking, crash, shouting, a baby crying) turns its whole screen red with what
it was. And **blind mode**: for someone who is blind or has low vision, `find`
looks for a thing, walks up to it and beeps beside it (`beacon`). `README.md` says what the robot does and how to run it; read it first.
Keep it that small: don't add features nobody asked for. Explain what you
change and why, and keep changes small enough to review.

## The CS 123 course site: check it first

<https://cs123-stanford.readthedocs.io/en/latest/index.html> is the CS 123
homepage. It explains everything about this robot: the theory from the
lectures (kinematics, control, locomotion, learned policies, perception) and
the practical skills from the labs (setup, ROS 2, the controllers, training
and deploying policies, Lab 6 itself). **If you are ever confused or need
clarification about how Pupper works, check this site first**, before
guessing or changing shared code.

## Keep the README current

`README.md` is the one place a person reads to learn what this repo does and
how to run it. Whenever you add or change important functionality (a new tool
or trick, a retrained policy, a new script or setup step, a changed command,
a moved folder), update `README.md` in the same change: what it does, how to
run or test it, and anything someone needs to know before using it on the
robot. Describe how things work now; keep it accurate.

## What this repo is

Pupper is a real robot dog: 12 motors, a Raspberry Pi 5, a fisheye camera.
Gemini Live (in the browser, `web/`) talks with people and calls **tools**. The
tools are Python, in `lab/`: the web app gets Gemini's prompt (`lab/prompt.md`)
and tool list from the robot server (`robot/server.py`, port 8000), and sends
every tool call back to it. Each `lab/` file has two halves: behaviors (what
the robot does: check the request, queue a job, return a Result) and the
`@tool`s Gemini calls. `robot/robot_api.py` runs the jobs one at a time and
drives the ROS 2 controllers. Trick policies are trained in pupper-mjlab
(`notebooks/training/`) and fetched from W&B with `robot/get_trick.py`.

| Where | What |
| --- | --- |
| `lab/prompt.md`, `basics.py`, `part2_walk.py` ... `part5_interactive_action.py` | the Lab 6 tools: walking (`move`, `turn`, `arc`), `follow`, tricks, `interactive_action`. The `TODO n` cards in these files are the lab's numbering; every TODO is written, and `python3 lab/test.py todo3` tests one |
| `lab/deaf_mode.py` | deaf mode: the `deaf_mode` tool, the urgent-sound list, the event bus and the red alert screen |
| `hearing/` | the sound detector deaf mode uses: the meter, the detector and YAMNet |
| `lab/blind_mode.py`, `lab/vision.py`, `lab/fisheye_geom.py`, `lab/debug_view.py` | blind mode: the `find` and `beacon` tools, the senses and the obstacle rule `find` walks by, Lab 5's lens model, and what the debug page (`GET /debug`) shows |
| `vision/eyes.py` | blind mode's eyes service: the object detector, depth and Gemma, posting to the robot server |
| `lab/collapse_watch.py`, `vision/` | collapse watch (on by default): someone collapsed in view -> walk up, "Are you okay?", siren and flashing screen, then "calling 911" (only said, never placed) while it really phones the emergency contact (`lab/emergency.py`) |
| `robot/` | the Robot API, the tool registry, the robot server, the tests, the launch files and scripts |
| `web/` | the web app (TypeScript): the face, the debug view and the Gemini Live client |
| `tricks/` | one folder per trick (`policy.json` + `trick.yaml`; `robot/tricks.py` lists the fields) |

`robot/` and `web/` are the shared base everything runs on. Change them
deliberately, say why, and keep every test passing; don't patch them to make a
symptom go away, and never weaken a test to make it pass.

## Safety rules (these override everything else)

1. **Never move the robot without an explicit OK from the person at the
   robot, right then.** That includes `robot/start.sh`, `./run_pupper.sh`,
   `python3 lab/chat.py --robot`, `curl` to any POST endpoint of the robot
   server, `ros2 topic pub`, `ros2 control switch_controllers`, and anything
   that activates a controller. Say what will happen and wait. Someone must be
   at the robot with a hand near the e-stop.
2. **Simulate first.** A new or changed motion goes through the motion report
   and the physics replay (`play_motion.py`, verdict HOLDS UP / WOBBLES /
   FALLS) before it goes anywhere near the robot. A trained policy is scored
   the same way (`play_motion.py --checkpoint`) and watched in the viewer.
3. **New trained policies need a TA's OK** before their first run on the
   robot. A badly trained contact-heavy trick can slam a paw or the body into
   the ground.
4. **Respect the limits.** Don't raise `MoveLimits`, `max_body_angle_deg`,
   joint limits, speed caps or gains to make something
   "work". Motion design reports should show IK error under 2 mm, nothing below
   the ground, joint speeds under about 15 rad/s and no joint at its limit.
5. **Heat.** The motors and the Pi get hot. If the robot is hot or a motor
   faults, stop testing and let it cool; don't retry in a loop.
6. **No secrets in files.** Never write an API key, a password or a token into
   the repo, a commit, a log or the web app. The Gemini key lives in
   `~/.config/gemini-pupper/api_key` on the robot (and in the browser's
   Settings); W&B uses `wandb login`. Hearing calibration lives outside the
   repo (`~/.config/gemini-pupper/hearing/`).

## Starting the robot

One command, from the Pupper or from a laptop: `./run_pupper.sh` (see
`README.md`, "Running"). On a laptop it syncs this repo to the Pupper with
`tools/pup` and runs itself there (`--laptop`, run on the laptop: the same, but
Pupper hears through the laptop's mic and the laptop mirrors Pupper's screen). It starts the robot, so it falls under
safety rule 1: only with an OK from the person at the robot.

## How to check work without the robot

- `python3 lab/test.py`: every check, on a fake robot. Run it after every
  change in `lab/` or `robot/`. It checks what the tools do, not how they are
  written. Groups: `todo1` ... `todo11`, `part1` ... `part7`, `robot`,
  `deaf` (deaf mode, `robot/test_deaf_mode.py`), `blind` (blind mode,
  `robot/test_blind_mode.py`; one of its groups: `python3 lab/test.py blind find`) and
  `collapse` (collapse watch, `robot/test_collapse.py`).
- `python3 hearing/test_signal.py` (needs numpy and scipy): the sound detector.
- `python3 lab/chat.py`: talk to Gemini in text with the prompt and tools, on
  the fake robot (nothing moves). It shows each tool call, the reply Gemini
  hears, and what the fake robot did. Never `--robot` without an OK (see the
  safety rules).
- `./run_local.sh`: the whole app on a laptop, with the fake robot.
- In pupper-mjlab: `uv run python -m mjlab.tasks.tracking.config.pupper.tricks.sit_stay_shake`
  and `...tricks.spider` check the clip code and build the library;
  `uv run python -m mjlab.tasks.tracking.config.pupper.play_motion <motion>.npz`
  replays a motion with physics (add `--checkpoint` to score a trained policy).

## Conventions

- Every tool returns a Result (`{success, message}`), and Gemini reads the
  message word for word: it is all Gemini learns. Say what happened, or what
  was wrong *and* the limit or the real choices (the tests check for these:
  "too fast: |vx|<=1.5 m/s", "unknown trick 'x'; available: [...]"). A bare
  "too fast" leaves Gemini guessing.
- Rules are enforced on the robot, not only in the prompt: the prompt and the
  tool descriptions tell Gemini what to do; the code makes sure it happens.
- Motion goes through the queue (`robot.queue`): check now, queue a job, and
  the job checks again, acts and reports. Wait with `await robot.sleep(...)`,
  never `time.sleep`.
- Things the robot notices on its own (an urgent sound, a search result) go on
  the event bus (`lab/deaf_mode.py`), and the web app paints them on the screen.
  Blind mode's events also carry a line for Gemini (`for_gemini`), so Pupper
  says how a search went.
- Units: metres, seconds, degrees. Directions use the robot's convention:
  +y is left, and a positive turn is to the left.
- After changing a `.py` file in `lab/`, restart the server
  (`robot/stop.sh`, then `robot/start.sh`). After changing `prompt.md`,
  reconnect in the app.
- The robot is called Pupper. Gemini Pupper was created by Peng Xu (Google
  DeepMind); keep his credit in the README and the app.
