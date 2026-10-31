#!/bin/bash
# One command to run Pupper:   ./run_pupper.sh [volume 0-100] [--laptop]
#
# Run it on the Pupper, or on a laptop: there it syncs this repo to the Pupper
# (tools/pup sync, after a one-time tools/pup setup) and runs itself there over SSH.
#
# Does every setup step for you: puts Node on the PATH, rebuilds the
# web app if its sources are newer than the build, installs missing Python packages,
# then starts the robot stack, the robot server and the app full-screen on Pupper's own
# screen with its mic and speaker. Then it shows every tool call, event (deaf mode's sound
# alerts) and outcome live. Ctrl+C stops everything (robot/stop.sh).
#
# --laptop (from a laptop): everything the same, except Pupper hears through the laptop's mic,
# streamed over SSH (needs ffmpeg: brew install ffmpeg), and the laptop's browser shows a live
# mirror of Pupper's screen (http://localhost:8000/mirror) that takes clicks and typing.
#
# Motors stay idle until someone asks Pupper to move. Keep it on the floor, hand near the
# e-stop (the right stick on the gamepad relaxes the motors).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
VOLUME=30; LAPTOP=0
for arg in "$@"; do
  case "$arg" in
    --laptop) LAPTOP=1 ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *[!0-9]*) echo "unknown argument: $arg (volume 0-100, or --laptop)"; exit 2 ;;
    *) VOLUME=$arg ;;
  esac
done
# Not on the Pupper (no ROS 2 here): sync and run this script on the Pupper instead.
if [ ! -d /opt/ros/jazzy ]; then
  echo "not on the Pupper: syncing and starting it on ${PUPPER_HOST:-pi@pupper5.local}"
  if [ $LAPTOP = 1 ]; then
    # This laptop's mic -> ssh -> robot/laptop_mic.sh, which makes it Pupper's default mic. It has to
    # be there before the kiosk asks for a mic, so it starts first; it stops when this script ends.
    command -v ffmpeg >/dev/null || { echo "--laptop needs ffmpeg on this laptop (brew install ffmpeg)"; exit 1; }
    "$HERE/tools/pup" sync || exit 1
    MIC_PID=$(mktemp); MIC_ERR=$(mktemp)
    stop_mic() { kill "$(cat "$MIC_PID" 2>/dev/null)" ${OPENER:-} 2>/dev/null; rm -f "$MIC_PID" "$MIC_ERR"; }
    trap stop_mic EXIT; trap 'exit 130' INT TERM
    echo "laptop mic: streaming this laptop's input to Pupper (macOS may ask to let your terminal use the mic)"
    ( { ffmpeg -hide_banner -loglevel error -f avfoundation -i ":default" -ac 1 -ar 48000 -f s16le -flush_packets 1 - 2>"$MIC_ERR" &
        echo $! >"$MIC_PID"; wait; } \
      | "$HERE/tools/pup" sh "bash ~/pupper-hackathon-setup/robot/laptop_mic.sh" ) &
    sleep 4
    kill -0 "$(cat "$MIC_PID")" 2>/dev/null || { echo "laptop mic did not start: $(cat "$MIC_ERR")"; exit 1; }

    # The live mirror of Pupper's screen (robot/mirror.py), through port 8000: tunnelled in the same
    # SSH session, unless something here already has the port (VS Code's Remote-SSH forwards Pupper's
    # 8000 by itself; that works too). The page reconnects by itself while the app restarts.
    if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
      echo "port 8000 is already forwarded or served on this laptop: using that for the mirror (stop ./run_local.sh if it is that)"
    else
      export PUP_TUNNEL=1
    fi
    (for i in $(seq 1 300); do
       curl -s -o /dev/null -m 2 localhost:8000/health && { echo "opening http://localhost:8000/mirror"; open http://localhost:8000/mirror 2>/dev/null || xdg-open http://localhost:8000/mirror >/dev/null 2>&1; break; }
       sleep 2
     done) &
    OPENER=$!
    # Not exec: ssh -t passes Ctrl+C to the Pupper, so the mic has to be stopped here once it returns.
    "$HERE/tools/pup" start "$@"
    exit $?
  fi
  exec "$HERE/tools/pup" start "$@"
fi
LOG=/tmp/gemini-pupper/server.log
# --laptop here, on the Pupper: the laptop's mic can only be sent from the laptop (ffmpeg there, above),
# so it has to have been started there. A terminal on the Pupper (VS Code's Remote-SSH too) can't.
if [ $LAPTOP = 1 ] && ! XDG_RUNTIME_DIR=/run/user/$(id -u) pactl list short sources 2>/dev/null | grep -q laptop_mic; then
  echo "--laptop has to be run on the laptop, not on the Pupper: only the laptop can send its own mic."
  echo "This terminal is on the Pupper ($(hostname)). Open a terminal on your laptop (not VS Code's Remote-SSH"
  echo "one), cd to your checkout of this repo there, and run:  ./run_pupper.sh --laptop"
  exit 1
fi
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export PATH=$HOME/.local/node/bin:$PATH   # robot/setup.sh installs Node there

stop_all() {
  echo; echo "stopping..."
  "$HERE/robot/stop.sh"
  exit 0
}
trap stop_all INT TERM

# --- 1. the web app build (web/dist), rebuilt when a source file is newer
need_build=0
if [ ! -f web/dist/index.html ]; then need_build=1
elif [ -n "$(find web -newer web/dist/index.html -type f -not -path 'web/node_modules/*' -not -path 'web/dist/*' -print -quit)" ]; then need_build=1
fi
if [ $need_build = 1 ]; then
  if ! command -v npm >/dev/null; then
    echo "web app not built and no npm: running robot/setup.sh first"; "$HERE/robot/setup.sh" || exit 1
  else
    echo "building the web app (sources changed)..."
    (cd web && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent 2>&1 | grep -vE "chunks are larger|dynamic import|manualChunks|chunkSizeWarningLimit|^$") || { echo "web build failed"; exit 1; }
  fi
fi

# --- 2. Python packages for the robot server
if ! python3 -c "import fastapi, uvicorn, psutil, pydantic, websockets, yaml" 2>/dev/null; then
  echo "installing the robot server's Python packages..."
  python3 -m pip install --user --break-system-packages -q fastapi uvicorn psutil pydantic websockets pyyaml || exit 1
fi

# --- 2b. blind mode's eyes service (vision/eyes.py): the depth model and the object detector
if ! python3 -c "import onnxruntime, mediapipe, numpy, PIL" 2>/dev/null; then
  echo "installing the eyes service's Python packages (blind mode: depth + detector)..."
  python3 -m pip install --user --break-system-packages -q -r "$HERE/vision/requirements.txt" mediapipe \
    || echo "WARNING: eyes packages failed to install; find will only turn, not walk"
fi

# --- 3. sound
if [ $LAPTOP = 1 ]; then
  echo "mic: the laptop's (streamed over SSH)"
elif arecord -l 2>/dev/null | grep -q card; then
  echo "mic: $(arecord -l | grep card | head -1 | sed -E 's/.*\[([^]]+)\].*\[.*/\1/')"
else
  echo "WARNING: no microphone found (plug in the webcam or a USB mic); Pupper will not hear you"
fi
wpctl set-volume @DEFAULT_AUDIO_SINK@ "$(awk "BEGIN{print $VOLUME/100}")" 2>/dev/null && echo "volume: $VOLUME%"

# --- 4. start
"$HERE/robot/start.sh" --kiosk || { echo "start failed; see /tmp/gemini-pupper/*.log"; "$HERE/robot/stop.sh"; exit 1; }
echo "state: $(curl -s localhost:8000/system/mode)"
cat <<'EOF'

Talk to Pupper from about an arm's length. Try, in order:
  "Stand up."  ->  "Sit." (wait 10 s)  ->  "Shake your left paw."  ->  "Stand up."
  "I'm deaf."  ->  knock on a table or play a doorbell sound (the screen turns red: KNOCKING, DOORBELL)
  "Beep so I can find you."  ->  "Got it."   (blind mode's beacon; nothing moves)
  A cup on the floor 1 m away, clear floor:  "Find my cup."  (it walks up to it and beeps; http://localhost:8000/debug shows what it sees)
Say "stop" (or use the gamepad's right stick) to stop right away. Ctrl+C here shuts everything down.

--- live tool calls and events ---
EOF
tail -n 0 -F "$LOG" 2>/dev/null | grep --line-buffered -E "server INFO tool |robot_api (INFO|WARNING|ERROR)|(events|hearing|vision) (INFO|WARNING|ERROR)" \
  | sed -u -E 's/^[0-9-]+ ([0-9:]+),[0-9]+ (server|robot_api|events|hearing|vision) (INFO|WARNING|ERROR) /\1  /'
