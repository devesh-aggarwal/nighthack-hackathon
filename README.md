<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://github.com/user-attachments/assets/0aa67016-6eaf-458a-adb2-6e31a0763ed6" />
</div>

# Gemini Pupper (CS 123 Lab 6, finished)

Talk to Pupper, and it listens, looks and does what you ask. Gemini Live is the brain: a
foundation model that hears you, sees through the camera and talks back. Trained policies are
the muscles. In between are the tools: the only way Gemini can move the robot, and the only way
it learns what really happened.

This repo is a working copy of CS 123 Lab 6 (*Bringing Your Pupper to Life*) with every part
done: all 11 TODOs written, and the sit-stay-shake and spider tricks trained and installed. It
also has **deaf mode**, for people who are deaf or hard of hearing (see
[Deaf mode](#deaf-mode)).

How the robot works, in theory (lectures) and in practice (labs), is on the
[CS 123 course site](https://cs123-stanford.readthedocs.io/en/latest/index.html): check there first
when something about Pupper is unclear.

**Gemini Pupper was created by [Peng Xu](https://scholar.google.com/citations?user=460NWeQAAAAJ&hl=en)
(Google DeepMind)**, original repo [sippeyxp/gemini-pupper](https://github.com/sippeyxp/gemini-pupper).
The CS 123 version is [cs123-stanford/gemini_pupper](https://github.com/cs123-stanford/gemini_pupper);
the lab handout is
[here](https://cs123-stanford.readthedocs.io/en/latest/schedule/labs/fall-26/lab-6.html).

## What it can do

By voice (or text, with `lab/chat.py`):

- **Walk, turn, run, sidestep and curve** (`move`, `turn`, `arc`), including shapes like squares
  and circles. Unsafe requests are refused with the limit that applies ("too fast: |vx|<=1.5 m/s").
- **Follow a person** (`follow`), using the Lab 5 follower in `~/follow_me_pupper` on the Pupper.
  Any motion ends follow mode, and Pupper says so first.
- **Play tricks** (`play_trick`): push-ups, swim, sneeze, twerk, downward dog and more.
- **Sit, stay and shake** (`sit_stay_shake`, trained here): sits and stays as long as you like,
  shakes either paw on request (`interactive_action`), and stands up when asked.
- **Be a spider** (`spider_walk`, trained here): lies down low with legs splayed, creeps around
  with `move`/`turn`/`arc` using its own walking policy, and wiggles back up to standing.
- **Report honestly**: every tool reply says what was queued or why it was refused, and
  `robot_status` reports what really happened.

- **Deaf mode** (`deaf_mode`): say "I'm deaf", "I'm hard of hearing" or "deaf mode", and Pupper
  keeps listening. An urgent sound (siren, alarm, doorbell, knocking, crash, shouting, a baby
  crying) turns the whole screen red with what it was, in big letters, and everything Pupper says
  is captioned on its face. See [Deaf mode](#deaf-mode).

## What's where

```
.
├── lab/                 the Lab 6 code (all TODOs done)
│   ├── prompt.md        Pupper's personality and rules
│   ├── basics.py        stand_up, lie_down, stop, robot_status
│   ├── part2_walk.py    check_move, walk, turn, move, arc
│   ├── part3_follow.py  prepare_to_move, follow
│   ├── part4_tricks.py  play_trick, play_policy_trick
│   ├── part5_interactive_action.py  interactive_action
│   ├── deaf_mode.py     deaf_mode: urgent sounds turn the screen red
│   ├── test.py          python3 lab/test.py      (tests on a fake robot: the lab's and deaf mode's)
│   └── chat.py          python3 lab/chat.py      (talk to Gemini in text)
├── tricks/              one folder per trick (policy.json + trick.yaml)
│   ├── sit_stay_shake/  trained here; covers sit and stand_sit_shake_sit_stand, so it hides them
│   └── spider_walk/     trained here: clip policy + walk_policy.json; covers spider, so it hides it
├── robot/               the Robot API, robot server, launch files, setup/start/stop scripts
│                        (test_tools.py: the lab's tests; test_deaf_mode.py: deaf mode's)
├── hearing/             the sound detector deaf mode uses: the meter, YAMNet, calibration
├── web/                 the web app (face + debug view, Gemini Live client; services/robotEvents.ts
│                        paints deaf mode's alerts)
├── notebooks/           Meet the Loop, plus training/ (the code used to train the tricks)
├── docs/                architecture notes and Peng Xu's write-up
├── run_pupper.sh        one command: build if needed, start the kiosk, watch tool calls and events live
└── tools/               dev tools: ./tools/pup and display examples
```

Each file in `lab/` has two halves: first **what the robot does** (check the request, queue a
job, report what happened), then **what Gemini reads** (the `@tool` name, description and
parameters). The Robot API (`robot.queue`, `robot.switch_to`, ...) is documented at the top of
`robot/robot_api.py`.

## Setup on the Pupper (once)

Clone this whole repo onto the Pupper and run the setup script from its root:

```bash
git clone https://github.com/codergautam/pupper-hackathon-setup.git ~/pupper-hackathon-setup
cd ~/pupper-hackathon-setup
robot/setup.sh
```

`setup.sh` installs Node in your home folder, builds the web app, checks the Python packages and
asks for your Gemini API key (from [Google AI Studio](https://aistudio.google.com/apikey)). The
key is saved in `~/.config/gemini-pupper/api_key`, never in this repo. It is safe to run again.

Keep `~/follow_me_pupper` (Lab 5) on the Pupper: follow mode runs it. The ROS 2 workspace that
`start.sh` sources is `~/pupperv3-monorepo`.

To update the Pupper later: `git pull` in `~/pupper-hackathon-setup` (or `./tools/pup sync` from a
laptop), then restart.

## Running

On the Pupper, on the ground, with clear space around it, the quickest way is one command:

```bash
./run_pupper.sh           # kiosk on Pupper's screen, volume 30
./run_pupper.sh 50        # speaker volume 0-100
./run_pupper.sh --laptop  # from a laptop: the same, but hearing through the laptop's mic, plus a mirror of the screen
```

The same command works from a laptop with this repo checked out: off the Pupper it syncs the repo
to `~/pupper-hackathon-setup` on the Pupper (`tools/pup sync`) and runs `./run_pupper.sh` there over
SSH, so you see the live log on your laptop and Ctrl+C stops the robot. Run `tools/pup setup` once
first (it installs an SSH key), and set `PUPPER_HOST` if your Pupper isn't `pi@pupper5.local`.

`./run_pupper.sh --laptop` (from a Mac) runs everything the same as without it (the app on Pupper's
screen, its speaker, one Gemini session), except for two things:

- **The laptop's mic is Pupper's mic.** ffmpeg records the Mac's default input and sends it over
  SSH to `robot/laptop_mic.sh` on the Pupper, which turns it into a PipeWire source, `laptop_mic`,
  and makes it the default mic, so the app and the hearing service hear through it like a mic
  plugged into Pupper. When the command ends (Ctrl+C), the laptop disconnects, or `robot/stop.sh`
  runs, the source goes away and the Pupper's old default mic comes back.
- **The laptop shows Pupper's screen.** Once the robot server answers, the laptop's browser opens
  http://localhost:8000/mirror: a live copy of Pupper's screen (`robot/mirror.py` relays the kiosk's
  screencast through Chromium's debugging port on the Pupper). Clicks and typing on it go to
  Pupper's screen, so both show the same thing and either can work it. It reconnects by itself
  while the app restarts. Port 8000 comes through the same SSH session, or through a forward you
  already have (VS Code's Remote-SSH forwards it by itself); stop `./run_local.sh` first.

Needs ffmpeg on the Mac (`brew install ffmpeg`); the first time, macOS asks to let your terminal use
the mic. Keep the laptop near Pupper: it is where Pupper hears from, and sound thresholds were
calibrated for Pupper's own mic.

It does every setup step for you: puts Node on the PATH, rebuilds the web app if a
source file is newer than the build (or runs `robot/setup.sh` if there is no Node yet), installs
missing Python packages, checks the mic (a warning, not a stop), sets the volume, starts
everything, prints things to try, and then shows every tool call, event (deaf mode's sound
alerts) and outcome live. Ctrl+C stops everything (`robot/stop.sh`).

Or run the pieces yourself:

```bash
robot/start.sh --kiosk    # robot + the app on Pupper's own screen (its mic and speaker)
robot/start.sh            # robot only; open the app from your laptop (below)
robot/stop.sh             # relax the motors and stop everything (also if Pupper overheats)
```

Wait for `[3/3] ready`. From your laptop: `ssh -N -L 8000:localhost:8000 pi@pupper5.local`, open
http://localhost:8000, open **Settings**, paste your key and press **WAKE PUPPER**. The debug view
shows every tool call and its result.

The e-stop is the gamepad's right stick (press it): it relaxes the motors, whatever Gemini is
doing.

Things to try: "sit, and give me your paw", "other paw", "stand up", "be a spider, walk forward a
bit, turn around, now stand up", "run as a spider" (refused: the spider can't run), "walk in a
square", "follow me", "I'm deaf" (then knock on a table).

Debugging: `tail -f /tmp/gemini-pupper/server.log` (your `lab/` code) and
`/tmp/gemini-pupper/robot.log` (ROS 2); `curl localhost:8000/system/mode` shows the real state.

## Testing without the robot

```bash
python3 -m pip install --user google-genai pyyaml
python3 lab/test.py            # every check, on a fake robot: the lab's 166, then deaf mode's 22
python3 lab/test.py todo9      # one TODO
python3 lab/test.py deaf       # deaf mode (robot/test_deaf_mode.py)
python3 hearing/test_signal.py # the sound detector's signal chain (needs numpy and scipy)
python3 lab/chat.py            # your prompt and tools, on a fake robot: nothing moves
python3 lab/chat.py --show     # print every tool description as Gemini reads it, then chat
python3 lab/chat.py --robot    # your tools on the real Pupper (robot server running): it moves!
```

`chat.py` needs a Gemini key in `GEMINI_API_KEY` or `~/.config/gemini-pupper/api_key`.

### The whole app on your laptop (voice and face), with a fake robot

```bash
./run_local.sh                 # then open http://localhost:8000
PORT=8080 ./run_local.sh       # if 8000 is taken
./run_local.sh --build         # force a rebuild of the web app
```

This runs the real robot server, your `lab/` tools and prompt, and the web app on
your laptop; only ROS is replaced by the fake robot from the tests, so **nothing moves**: every
motion is printed instead (`[fake robot] legs: gemini_walk took over`, `[fake robot] walking
vx=0.4 ...`). Open the page, go to **Settings**, paste your Gemini key, press **WAKE PUPPER**, and
talk through your laptop's mic (allow mic and camera when Chrome asks). It builds the web app
first if needed (needs Node.js), and needs `fastapi uvicorn psutil pydantic pyyaml`; on NixOS,
without python3/npm, it reruns itself in a `nix-shell` that has them. Deaf mode needs the Pupper's
sound detector (PipeWire), so here it says it can't start.

| You changed | Then |
| --- | --- |
| `lab/prompt.md` | try it in text, then reconnect in the app (no restart) |
| a `.py` file in `lab/` | `python3 lab/test.py`, then `robot/stop.sh && robot/start.sh` |
| a trick (`get_trick.py` or a `trick.yaml`) | `robot/stop.sh && robot/start.sh` |

## Tricks and how they were trained

Every trick is a policy, so the robot is always in one simple state: locomotion, one trick, motors
idle, or fallen. Each folder under `tricks/` with a `trick.yaml` is a trick Gemini can play
(`robot/tricks.py` lists the fields). A playback trick plays one motion once; an interactive
trick is a state Pupper stays in, with `actions` Gemini can request and an `exit_clip` that
stands back up. A `replaced_by` field hides a trick while another installed trick covers it.

`sit_stay_shake` and `spider_walk` were trained in Colab on a G4 GPU with
[pupper-mjlab](https://github.com/cs123-stanford/pupper-mjlab) (the code is in
`notebooks/training/`) and fetched from W&B with `robot/get_trick.py`:

| Trick | Policy | Physics check | W&B run |
| --- | --- | --- | --- |
| `sit_stay_shake` | tracking policy on 5 clips: `sit_down`, `sit_idle` (loops), `shake_left`, `shake_right` (the left shake mirrored), `stand_up` | plain replay falls; policy stays up (worst 3.7 cm / 3.2 deg) | `nru36bv1` |
| `spider_walk` clips | tracking policy on `spider_down`, `spider_idle` (loops), `spider_up` (`spider_down` reversed) | policy stays up (worst 4.0 cm / 6.2 deg) | `lkio9x6h` |
| `spider_walk` walking | `Mjlab-SpiderWalk-Flat-Pupper-v3`, body at 4.2 cm; capped at 0.12 m/s forward, 0.08 sideways, 25 deg/s | holds height, knees off the ground | `a0tpt3sl` |

New trick policies should get a TA's OK (score + simulation viewer) before their first run on
the robot. To fetch a different run:

```bash
python3 robot/get_trick.py <entity>/mjlab/<run-id> --name sit_stay_shake
python3 robot/get_trick.py <clip-run> --walk <spider-walk-run> --name spider_walk
```

The Pupper must be logged into W&B (`wandb login`) with the account the runs are on.

## Connecting to the Pupper

- Host: `pupper5.local` (hostname `pupper5`, Raspberry Pi 5, Debian Bookworm aarch64)
- User: `pi` (the password is the one set when the Pupper was imaged; ask your TA)

```sh
ssh pi@pupper5.local
```

The laptop and the Pupper must be on the same network for `.local` (mDNS) to resolve. To skip the
password from then on, set up key-based login once: `ssh-copy-id pi@pupper5.local` (or
`./tools/pup setup`).

## `./tools/pup`: quick testing on the Pupper

`./tools/pup` runs commands on the Pupper, copies files, and launches scripts, including GUI apps
on its 720×720 screen. Run `./tools/pup setup` once per laptop: it creates `~/.ssh/pupper_ed25519`
and installs it on the Pi. The user `pi` has passwordless `sudo`.

```sh
./tools/pup setup                          # one-time key setup (asks for the Pupper's password)
./tools/pup                                # interactive shell
./tools/pup sh 'free -h; uptime'           # run any command
./tools/pup run my_test.py arg1            # copy a local script to /tmp/pup on the Pi and run it
./tools/pup gui tools/examples/hello_pygame.py   # run it on Pupper's display
./tools/pup sync                           # rsync this repo to ~/pupper-hackathon-setup on the Pi
./tools/pup put ./file ~/dest  /  ./tools/pup get ~/file .
./tools/pup kill hello_pygame              # stop a runaway process
```

`tools/examples/` has display demos: `hello_pygame.py` (a 10-second smoke test) and
`crying_eyes.py`. Set `PUPPER_HOST` to target a different Pupper.

## Deaf mode

`lab/deaf_mode.py`, `hearing/`, and the red alert screen in the web app. It is the one feature
added to Lab 6.

**Turning it on.** The prompt has Gemini call `deaf_mode(enabled=true)` when the person says they
are deaf or hard of hearing, or asks for deaf mode, and `deaf_mode(enabled=false)` when they ask
to stop. It is only for people who can't hear: someone who is blind or has low vision hears the
doorbell themselves, so the prompt and the tool description tell Gemini not to turn it on for them.
It is off at every start: tell Pupper again after a restart.

**Listening.** Deaf mode starts the robot server's sound detector (`hearing/listener.py`), which
listens through PipeWire next to the kiosk:

- **Sirens, alarms, doorbells, crashes and a baby crying:** Google's YAMNet
  (`hearing/classify.py`, AudioSet's 521 classes through LiteRT, on-device, about 1.5 ms per
  0.5 s).
- **Bangs, shouts and knocking:** the calibrated meter (`hearing/meter.py`, `hearing/detect.py`),
  with thresholds in dB above the room's noise floor; YAMNet drops knocks that were really speech.

**Showing.** Each urgent sound (the `URGENT` list in `lab/deaf_mode.py`) becomes an event; the web
app polls `GET /events` once a second and turns the whole screen red with what it was (SIREN,
DOORBELL, KNOCKING, BABY CRYING...) in big letters for 10 s, even while Pupper sleeps. Everyday
sounds show as a small label in the top-left corner (`GET /hearing/now`). Gemini is not told
about sounds: Pupper shows them, it doesn't talk about them. Sounds while Pupper moves are taken
for its own motors and ignored.

**The mic.** Plug one into Pupper, or use the laptop's: `./run_pupper.sh --laptop`, **run on the
laptop**, makes the laptop's mic Pupper's default source (`laptop_mic`), so the detector hears
through it like a mic on Pupper. Started on the Pupper itself, it can't reach the laptop's mic.
With no mic at all (PipeWire's default source is then the speaker's monitor, Pupper's own voice)
the detector refuses to start and `deaf_mode` says so, rather than listen to Pupper's own speaker.

Setup and tuning:

- **Install YAMNet (once, on the Pupper):** `python3 hearing/classify.py --install` puts LiteRT in
  its own folder and the model in `~/.config/gemini-pupper/hearing/yamnet/`, so the robot's
  Python packages don't change.
- **Thresholds:** they live in `~/.config/gemini-pupper/hearing/calibration.json`, made with the
  meter page, `python3 hearing/mic_lab.py` (kiosk stopped; open http://localhost:8001 through an
  SSH tunnel). It shows the loudness, the detections and what YAMNet hears.
- **Checks:** `python3 lab/test.py deaf` (the tool and the screen, on a fake robot), `GET /hearing`
  (the detector's state on the robot), and `python3 hearing/test_signal.py` (the signal chain).
- **Mic gain:** the kiosk browser starts with `--disable-features=WebRtcAllowInputVolumeAdjustment`,
  so its auto gain can't move the mic volume.

Nothing in deaf mode moves the robot: it only listens and paints the screen.

## Safety

- Test on the ground, never on a table, with a hand near the e-stop.
- Coding agents must not move the robot (`start.sh`, `chat.py --robot`, POST `curl`,
  `ros2 topic pub`), raise limits, or weaken tests without explicit approval at the robot.
- Never commit an API key. The Gemini key lives in `~/.config/gemini-pupper/api_key`.

## More

- [Peng Xu's write-up: Interactive Robot Dog with Gemini Live](docs/interactive-robot-dog.md)
- [System architecture](docs/architecture.md)
- [Notes for coding agents](AGENTS.md)
