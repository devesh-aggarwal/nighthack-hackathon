#!/bin/bash
# Make a laptop's mic Pupper's mic. Raw audio on stdin (s16le, 48 kHz, mono) becomes a PipeWire
# source, "laptop_mic", set as the default, so the kiosk app on Pupper's screen hears through it.
# run_pupper.sh --laptop runs this over SSH:  ffmpeg (laptop mic) | ssh pi robot/laptop_mic.sh
# When stdin ends (the laptop stops or disconnects), or on robot/stop.sh, it removes the source and
# puts the old default back.
export XDG_RUNTIME_DIR=/run/user/$(id -u)
FIFO=/tmp/laptop_mic.fifo
# a source left over from an earlier run that didn't clean up
pactl list short modules | awk '/source_name=laptop_mic/{print $1}' | xargs -r -n1 pactl unload-module
OLD=$(pactl get-default-source)
rm -f $FIFO
# node.always-process: keep reading the pipe while nothing records (before the app asks for the mic,
# and after it closes), so the audio never backs up and blocks the writer below.
MOD=$(pactl load-module module-pipe-source source_name=laptop_mic file=$FIFO format=s16le rate=48000 channels=1 \
  "source_properties='device.description=Laptop_mic node.always-process=true'") \
  || { echo "laptop mic: could not create the PipeWire source" >&2; exit 1; }
cleanup() {
  [ -n "${CAT:-}" ] && kill "$CAT" 2>/dev/null
  pactl unload-module "$MOD" 2>/dev/null
  [ -n "$OLD" ] && [ "$OLD" != laptop_mic ] && pactl set-default-source "$OLD" 2>/dev/null
  rm -f $FIFO
}
trap cleanup EXIT
trap 'exit 0' HUP INT TERM
pactl set-default-source laptop_mic
echo "laptop mic: ready (Pupper's default mic is now the laptop's)" >&2
# in the background, so a signal runs cleanup right away instead of after cat returns
# (through fd 3: a background job's own stdin would be /dev/null)
exec 3<&0
cat <&3 > $FIFO & CAT=$!
wait $CAT
