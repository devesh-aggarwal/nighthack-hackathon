# Gemini Pupper

Talk to Pupper, and it listens, looks and does what you ask. Gemini Live is the brain: a
foundation model that hears you, sees through the camera and talks back. Trained policies are
the muscles. In between are the tools: the only way Gemini can move the robot, and the only way
it learns what really happened.

The pupper features **deaf mode**, for people who are deaf or hard of hearing (see
[Deaf mode](#deaf-mode)), **blind mode**, for people who are blind or have low vision (see
[Blind mode](#blind-mode)), **collapse watch**, for someone who collapses on the floor (see
[Collapse watch](#collapse-watch)), and can **phone one emergency contact** (see
[Calling an emergency contact](#calling-an-emergency-contact)).

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
  keeps listening. An urgent sound (siren, alarm, doorbell, knocking, crash, a baby crying) turns
  the whole screen red with what it was, in big letters, for as long as it lasts, and everything
  Pupper says is captioned on its face. If Pupper is standing, it also turns one slow circle so
  the screen can be seen from anywhere in the room. See [Deaf mode](#deaf-mode).
- **Blind mode** (`find`, `beacon`): "where is my cup?", "find my keys". Pupper looks, turns a full
  circle, roams the room if it must, walks up to the thing, and beeps beside it once a second so
  the person can walk to the sound. "Got it" stops the beep. "Beep so I can find you" beeps on its
  own. See [Blind mode](#blind-mode).
- **Collapse watch** (`collapse_watch`, `emergency_response`), on from the start: if the camera
  sees someone collapsed on the floor, Pupper walks up to them, asks "Are you okay?", and sounds
  an alarm for 10 seconds while its screen flashes red. With no "I'm okay", it says it is calling
  911 and their emergency contacts. **It only says so: no call is placed.** See
  [Collapse watch](#collapse-watch).
- **Call for help** (`call_contact`): "call for help", "call Sam": Pupper phones the one emergency
  contact set up on the robot (through Twilio) and a voice reads them what is happening. It can
  never call 911. See [Calling an emergency contact](#calling-an-emergency-contact).

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
│   ├── deaf_mode.py     deaf_mode: urgent sounds turn the screen red (and Pupper turns a circle)
│   ├── blind_mode.py    find and beacon: find a thing, walk up to it, beep beside it
│   ├── vision.py        blind mode's senses: the eyes service's detector and depth, the obstacle rule
│   ├── fisheye_geom.py  Lab 5's lens calibration: where in the world a pixel looks
│   ├── debug_view.py    what the debug page (GET /debug) shows, and its STOP_BOTTOM tuning
│   ├── collapse_watch.py  someone collapses in view: walk up, ask, alarm
│   ├── emergency.py     call_contact: phone the emergency contact (never 911)
│   ├── test.py          python3 lab/test.py      (tests on a fake robot: the lab's and every mode's)
│   └── chat.py          python3 lab/chat.py      (talk to Gemini in text)
├── tricks/              one folder per trick (policy.json + trick.yaml)
│   ├── sit_stay_shake/  trained here; covers sit and stand_sit_shake_sit_stand, so it hides them
│   └── spider_walk/     trained here: clip policy + walk_policy.json; covers spider, so it hides it
├── robot/               the Robot API, robot server, launch files, setup/start/stop scripts
│                        (test_tools.py: the lab's tests; test_deaf_mode.py, test_blind_mode.py,
│                        test_collapse.py: the modes'; test_emergency.py: the call's; debug.html: the debug page)
├── hearing/             the sound detector deaf mode uses: the meter, YAMNet, calibration
├── vision/              eyes.py: blind mode's eyes service (object detector, depth, Gemma), beside the robot server;
│                        gemini_vision.py: collapse watch's camera check
├── web/                 the web app (face + debug view, Gemini Live client; services/robotEvents.ts
│                        paints the alerts, services/siren.ts is collapse watch's alarm)
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
git clone https://github.com/devesh-aggarwal/nighthack-hackathon.git ~/nighthack-hackathon
cd ~/nighthack-hackathon
robot/setup.sh
```

`setup.sh` installs Node in your home folder, builds the web app, checks the Python packages and
asks for your Gemini API key (from [Google AI Studio](https://aistudio.google.com/apikey)). The
key is saved in `~/.config/gemini-pupper/api_key`, never in this repo. It is safe to run again.

Keep `~/follow_me_pupper` (Lab 5) on the Pupper: follow mode runs it. The ROS 2 workspace that
`start.sh` sources is `~/pupperv3-monorepo`.

To update the Pupper later: `git pull` in `~/nighthack-hackathon` (or `./tools/pup sync` from a
laptop), then restart. If your clone on the Pupper has another name, set `PUPPER_REPO` to it on the
laptop: `tools/pup sync`, `tools/pup start` and `./run_pupper.sh --laptop` all use that folder.

## Running

On the Pupper, on the ground, with clear space around it, the quickest way is one command:

```bash
./run_pupper.sh           # kiosk on Pupper's screen, volume 30
./run_pupper.sh 50        # speaker volume 0-100
./run_pupper.sh --laptop  # from a laptop: the same, but hearing through the laptop's mic, plus a mirror of the screen
```

The same command works from a laptop with this repo checked out: off the Pupper it syncs the repo
to `~/nighthack-hackathon` on the Pupper (`tools/pup sync`) and runs `./run_pupper.sh` there over
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

### Slow Wi-Fi

If Pupper answers seconds after you stop talking while deaf mode's sound alerts (which never leave
the Pi) stay quick, the Wi-Fi uplink is the bottleneck. At a venue Pupper's uplink measured
2-4 Mbit/s with 5-15 % packet loss, and three things share it while Pupper listens:

- **Gemini Live**: your voice (16 kHz PCM, continuous) and one camera frame a second, over one
  WebSocket, so a big frame delays the words behind it. The app scales frames to 512 px on the long
  side before sending (`LIVE_FRAME_PX` in `web/services/geminiLive.ts`); **Settings → Enable Vision**
  off stops the frames altogether. The session also ends a turn after about 0.6 s without speech
  (`realtimeInputConfig` there, end sensitivity high) rather than waiting for a quiet a noisy room
  never gives.
- **Collapse watch**: one frame every 3 s to `gemini-flash-latest`, shrunk to 768 px (`MAX_PX` in
  `vision/gemini_vision.py`). "Stop watching for falls" turns it off for the session.
- **The eyes service**: a frame every 5 s while idle, about one a second while a search or follow
  runs, to the eyes' model through Google's API. `PUPPER_EYES_GEMMA=off ./run_pupper.sh` keeps the
  detector and depth on the Pi and sends nothing.

To see it: `curl -s localhost:8000/camera/image | wc -c` is one raw frame; `cat
/sys/class/net/wlan0/statistics/tx_bytes` twice, 10 s apart, is what actually leaves (with the app
connected and nobody talking, expect tens of KB/s, not hundreds); `ping <router>` with loss or swings over 100 ms
is the Wi-Fi itself, which no setting fixes: move Pupper nearer an access point or onto a phone's
hotspot.

## Testing without the robot

```bash
python3 -m pip install --user google-genai pyyaml
python3 lab/test.py            # every check, on a fake robot: the lab's, then each mode's
python3 lab/test.py todo9      # one TODO
python3 lab/test.py deaf       # deaf mode (robot/test_deaf_mode.py)
python3 lab/test.py blind      # blind mode (robot/test_blind_mode.py); one group: blind find
python3 lab/test.py collapse   # collapse watch (robot/test_collapse.py)
python3 lab/test.py emergency  # the emergency call (robot/test_emergency.py; no network, no Twilio)
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
./tools/pup sync                           # rsync this repo to ~/nighthack-hackathon on the Pi
./tools/pup put ./file ~/dest  /  ./tools/pup get ~/file .
./tools/pup kill hello_pygame              # stop a runaway process
```

`tools/examples/` has display demos: `hello_pygame.py` (a 10-second smoke test) and
`crying_eyes.py`. Set `PUPPER_HOST` to target a different Pupper.

## Deaf mode

`lab/deaf_mode.py`, `hearing/`, and the red alert screen in the web app. It is added to Lab 6, like
blind mode, collapse watch and the [emergency call](#calling-an-emergency-contact).

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
- **Knocking:** the calibrated meter's rhythm detector (`hearing/meter.py`, `hearing/detect.py`),
  with thresholds in dB above the room's noise floor; YAMNet drops knocks that were really speech.
  The meter also reports bangs and shouts, but they are not urgent: on loudness alone they gave
  too many false alarms.

**Showing.** Each urgent sound (the `URGENT` list in `lab/deaf_mode.py`) becomes an event; the web
app polls `GET /events` four times a second and turns the whole screen red with what it was
(SIREN, ALARM, DOORBELL, KNOCKING, CRASH, BABY CRYING) in big letters, even while Pupper sleeps.
It stays as long as the sound goes on: every 0.5 s YAMNet's scores go to `hearing_tick`, and once
the sound stops (after at least 1 s on the screen, so a knock can be read) a `sound_end` event
clears the screen. YAMNet listens to the last second of audio, so the screen clears about a
second after the sound stops. A different urgent sound replaces the one on the screen at once. Everyday
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

**Turning to show it.** If Pupper is standing still in locomotion mode when an alert comes, it
also turns one slow full circle to the left (36 deg/s for 10 s, `SPIN_S`: a normal queued turn,
so `stop` cancels it and the fall checks apply) so the red screen faces everyone in the room. The
screen stays red until the circle is done, even after a short sound (a knock) has stopped. Lying
down, in a trick, following or already moving, it doesn't move: deaf mode never stands Pupper up
on its own. While it turns, other sounds are taken for its motors and ignored, so a second alert
can be missed for those 10 s. Before the first
alert on the robot, make sure there is a clear circle around Pupper and a hand near the e-stop.

## Calling an emergency contact

`lab/emergency.py`: the `call_contact` tool. When someone asks Pupper to call for help or to call
their contact, Gemini calls `call_contact(reason)`; the robot server asks
[Twilio](https://www.twilio.com/docs/voice/api/call-resource) to place a real phone call, and when
the contact picks up a voice reads "Pupper here. <reason>" twice. The tool has no number
parameter: it can only call the one contact in the file below, so Gemini can't dial 911 or anyone
else, and the prompt tells it never to say it called 911 (it tells the person to call 911
themselves). One call a minute at most.

Set it up on the Pupper (the file holds a Twilio token, so it lives outside the repo; never
commit it):

```bash
mkdir -p ~/.config/gemini-pupper && nano ~/.config/gemini-pupper/emergency_contact.json
chmod 600 ~/.config/gemini-pupper/emergency_contact.json
```

```json
{"name": "Sam", "to": "+15551234567", "from": "+15557654321",
 "account_sid": "AC...", "auth_token": "..."}
```

- `to` is the contact's phone, `from` your Twilio number, both in +country format. The SID and
  token are on the Twilio console's front page.
- On a **Twilio trial** account, `to` must first be added as a verified caller ID in the console,
  and the call starts with a short trial announcement (the contact presses a key, then hears
  Pupper's message). Caller ID shows the Twilio number, not "Pupper".
- The Pupper needs internet. The tool needs no new packages (it uses Python's `urllib`).
- The file is read on every call: editing it needs no restart. Changing `lab/emergency.py` does
  (`robot/stop.sh && robot/start.sh`); then reconnect in the app to pick up the new prompt.
- `python3 lab/chat.py` uses the same tool: on a machine that has the file, it places a real call.

## Blind mode

`lab/blind_mode.py` (the `find` and `beacon` tools), `lab/vision.py` (the senses and the obstacle
rule), `vision/eyes.py` (the eyes service) and the debug page. For someone who is blind or has
low vision: Pupper finds a thing, walks up to it, and beeps beside it.

**Finding** (`find(what, max_seconds)`). "Where is my cup?", "find my keys". Cheapest step first:
look (the object detector's word for a thing it has a class for, else Gemma's yes or no through
the eyes service's `POST /exists`), then a full circle in place in 60-degree steps looking after
each, then the roam. Pupper has no map, so the roam is a smart Roomba: at each stop it reads the
depth sense, scores each direction by how open it is and whether it leads somewhere new (a 1 m grid
kept by dead reckoning from the speeds sent), turns that way, walks one leg of at most 1.5 m and
looks again, until found, out of time (`max_seconds`, default 120, at most 300) or boxed in. Found,
it says so (a `[search result]` Gemini hears) and walks up to the thing: it turns toward it, walks,
slows near it, and stops at arm's length, when the thing's base reaches Lab 5's stop row
(`STOP_BOTTOM` 454 of 572, `lab/fisheye_geom.py`). Every walk checks the obstacle rule on every tick
and stops for anything close in the way: a thing the detector can name ("a chair is right in front
of me"), or anything the depth sense sees ("something low"). The rule runs only inside `find`; the
lab's `move`, `turn` and `arc` are unchanged. `stop` cancels a search like any job. Every box the eyes post
carries its frame's age (`frame_age_s`): on the Pi a box arrives 1-3 s after its frame was taken, and
steering on it as if it were current turned the robot past the thing and then the wrong way. The
walk-up keeps what it sent tick by tick and moves each new box on by the turning and walking done
since its frame. The debug page shows each target box's age. For a thing only Gemma can find (no detector
class), the eyes service asks about each new frame and sends a box only when two answers in a row
agree; a disagreement (common while the robot turns) or a failed request (busy, rate-limited) keeps
the robot's last box instead of clearing it, and only "not there" twice in a row clears it
(`TargetTracker` in `vision/eyes.py`; its log, `/tmp/gemini-pupper/eyes.log`, says which happened).
The walk-up turns, then stands still until a box from after the turn arrives, and gives up only
after 10 s without one. Only time spent moving counts against one move's limit (about 18 s, 7 m);
standing still to look doesn't, since on the Pi it once used up the whole 18 s and the walk gave up
2 s in. The whole walk-up, standing included, is capped at 60 s (`WALK_UP_MAX_S`). Each new box is
logged (`walk-up <thing>: box from gemma, 1.8 s old, now -23 deg ...`) in run_pupper's live view.

Close to the thing the eyes give out (it fills the view at the edge of the lens, and Gemma stops
answering), so the walk-up never walks more than 0.6 m on a box no newer one has replaced
(`BLIND_WALK_M`: it stops and looks), and once the last box put the thing close (its base at the
slow-down row or lower), a while without a new one (twice the longest recent gap between boxes, 3-10 s: with Gemma on the Pi they come 4-5 s apart) counts as arrived: "it should be right in front of me,
too close for my eyes to see now", and it beeps. Losing it far away is not an arrival: the walk-up
searches again by itself once (`RETRIES`) and tells Gemini not to ask the person. `find` runs one
search at a time: called again while one is running or queued, it says it is already looking (judged
by the robot's queue, so after a `stop` a new search is fine). The desk tests run on the fake clock
(`Wall` in `robot/test_blind_mode.py`), so boxes go stale in them as they do on the robot.

**The beacon** (`beacon(enabled, seconds)`). On arrival Pupper beeps a short rising two-tone chirp
once a second for 30 s, from **its own speaker** (`pw-play`, else `paplay`, else `aplay`): with
`--laptop` the app's speaker is the laptop's, and the sound has to come from where the thing is.
Gemini tells the person to walk toward the beeping and, when they say "got it", calls
`beacon(enabled=false)`. For the first 6 s after `find` starts it, that call is refused (Gemini once
read its instructions as "stop it now", and the beep ended before anyone heard it). Any motion,
`stop`, the gamepad e-stop or the time running out also end it.
"Beep so I can find you" starts it by hand (5-120 s). While it beeps, deaf mode ignores what it hears.

**The eyes service** (`vision/eyes.py`). It fetches the camera frame the robot server serves
(`GET /camera/image`) and posts to it: the COCO object detector's boxes (EfficientDet-Lite0 through
MediaPipe, 80 everyday classes: cup, bottle, cell phone, chair, backpack ...) to
`POST /vision/detections`, and a depth summary (Depth Anything V2 Small, ONNX) to
`POST /vision/freespace`: for five strips of the view cut by bearing, where the nearest thing
standing on the floor meets the floor, and the fisheye's image disc. For a thing the detector has
no class for (keys, a wallet, glasses) it asks Gemma. `robot/start.sh` starts it on the Pi, where it
is slow (about 1 Hz, Gemma through Google's API with the Gemini key; `PUPPER_EYES_GEMMA=off` for
detector and depth only, `PUPPER_EYES=off` to skip it; log `/tmp/gemini-pupper/eyes.log`).
`run_pupper.sh` installs its packages the first time. For a faster demo, run it on the laptop (about
5 Hz; its posts take over from the Pi's):

```bash
python3 -m pip install -r vision/requirements.txt mediapipe
ollama pull gemma3:4b                                  # optional: Gemma on the laptop
ssh -N -L 8000:localhost:8000 -R 8100:localhost:8100 pi@pupper5.local
python3 vision/eyes.py --fetch-depth-model --fetch-detector-model   # first time; then python3 vision/eyes.py
```

Two fixes to the detector, both found on the debug page: "ahead" is a box's bearing within 45
degrees of straight on (`AHEAD_DEG`, the same as the depth sense), since on this fisheye the middle
half of the frame's width spans about 90 degrees each side and a person beside the robot blocked
every walk; and a box only counts if at least 55% of it is inside the image disc (`MIN_IN_LENS`),
since on the black border around the disc the detector sometimes sees one huge "person" (false
boxes were 36-45% inside, real things 62-100%).

**The debug page.** Open **http://localhost:8000/debug** (on a laptop, through the same SSH tunnel)
to see the camera live with what the rules see drawn on it: each detection's box (red: blocks a
forward walk; orange: close but off to the side; grey dashed: ignored, with the reason), the lens
disc, the depth strips (red when blocking), the stop row (green), the block row (red dashed), the
slow-down row, the edges of "ahead" (yellow) and what `find` is walking up to. Beside it: the verdict
a forward walk would get right now and which sense gave it, and tables of detections and strips.
`STOP_BOTTOM` can be changed there (380-520; lower stops farther from the thing) until the server
restarts; to keep a new value change it in `lab/fisheye_geom.py`. Keeping the page open keeps the
eyes awake. Nothing on the page moves the robot.

**Before using it on the robot.** `find` and its walk-up move the robot: clear floor, a person
watching with a hand near the e-stop, and start with `max_seconds=30` and a cup a metre away. The
one hazard the depth sense cannot see is a drop (stairs, a stage edge). Tests:
`python3 lab/test.py blind` (the rules, the walk-up and the search in simulated rooms, the beacon,
the debug page).

## Collapse watch

`lab/collapse_watch.py`, `vision/gemini_vision.py`, and the flashing screen and siren in the web
app. **It moves the robot on its own**, so it falls under the safety rules: try it with Pupper on
the ground and a hand near the e-stop.

**Watching.** It is on from the moment the robot server starts (`collapse_watch(enabled=false)`
turns it off; the prompt has Gemini do that when someone asks it to stop watching for falls).
Every 3 s the server's watcher sends Pupper's latest camera frame to Gemini
(`gemini-flash-latest`, or `$PUPPER_VISION_MODEL`) and asks whether a person is lying or slumped
on the floor and may have collapsed: not sitting, not on a bed or sofa, not exercising. It needs
internet and the Gemini key (`~/.config/gemini-pupper/api_key` or `$GEMINI_API_KEY`); without a
key it stays off and the server log says so. Each check is one Gemini API call, about 1,200 an
hour, and the frames go to Google, shrunk to 768 px on the long side first (`MAX_PX` in
`vision/gemini_vision.py`: the camera's 1400 x 1050 frames were crowding out Gemini Live's voice
stream on slow Wi-Fi; see [Slow Wi-Fi](#slow-wi-fi)).

**What happens.** Seen twice in a row (one odd frame isn't enough):

1. **Walk up, carefully.** One short turn or one short step at a time (at most 0.4 m, at
   0.2 m/s), each decided on a fresh camera check, stopping about 1 m from the nearest part of
   anyone's body, feet and legs included (Gemini's estimate from one frame, hence the margin). A
   step forward needs two things: Gemini saying the floor straight ahead is clear, and blind
   mode's obstacle sense (`vision.path_blocked`: the eyes service's depth and object detector)
   seeing right now. That sense is checked on every tick of the step, which stops the moment
   anything is close ahead, as `find`'s walks do. Without the eyes service, Pupper only turns to
   face them. Turns use the `move` code; the move limits, fall checks and `stop` apply to both.
   If the way isn't clear, or it can't walk (fallen over, stopped, lost sight of them), it asks
   from where it is.
2. **Ask.** The screen turns red and flashes "ARE YOU OKAY?", and Gemini is told to ask out loud.
3. **Alarm.** After 3 s, a two-tone siren (Web Audio, `web/services/siren.ts`) sounds for 10 s
   while the screen keeps flashing.
4. **"Calling 911".** If nobody has said they are fine, the screen says CALLING 911 and Gemini
   says it is calling 911 and their emergency contacts. **This is only a message: Pupper places
   no call and contacts no one.** Don't rely on it for a real emergency.

"I'm okay" at any point (Gemini calls `emergency_response(okay=true)`) stops everything, clears the
screen, and Pupper doesn't check on anyone for 5 minutes. "Help me" (`okay=false`) skips straight
to step 4. After step 4, it waits 2 minutes before watching again.

Notes:

- Gemini only speaks the question if the app is connected (Pupper awake); the flashing screen and
  the siren come either way.
- Deaf mode ignores sounds while the emergency is on the screen, so the siren isn't shown as a
  SIREN alert.
- Turns aim from where the person is across the picture, taking the fisheye as 180 degrees wide
  (`CAMERA_HFOV_DEG`); each turn looks again, so they correct themselves.
- **Checks:** `python3 lab/test.py collapse` (on a fake robot, with a fake camera check).

## Safety

- Test on the ground, never on a table, with a hand near the e-stop.
- Coding agents must not move the robot (`start.sh`, `chat.py --robot`, POST `curl`,
  `ros2 topic pub`), raise limits, or weaken tests without explicit approval at the robot.
- Never commit an API key. The Gemini key lives in `~/.config/gemini-pupper/api_key`.

## More

- [Peng Xu's write-up: Interactive Robot Dog with Gemini Live](docs/interactive-robot-dog.md)
- [System architecture](docs/architecture.md)
- [Notes for coding agents](AGENTS.md)
