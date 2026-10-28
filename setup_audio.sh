#!/usr/bin/env bash
# Creates the zoom_mix sink (sidetone + voice go in) and the zoom_mic virtual
# source (select it as microphone in Zoom). Undo with teardown_audio.sh.
set -euo pipefail

state="${XDG_RUNTIME_DIR:-/tmp}/morsekey-modules"
if [[ -s "$state" ]]; then
    echo "already set up (modules $(tr '\n' ' ' < "$state")); run teardown_audio.sh first" >&2
    exit 1
fi

pactl load-module module-null-sink sink_name=zoom_mix \
    sink_properties=device.description=zoom_mix > "$state"
pactl load-module module-remap-source master=zoom_mix.monitor source_name=zoom_mic \
    source_properties=device.description=zoom_mic >> "$state"
echo "created zoom_mix sink and zoom_mic source"
