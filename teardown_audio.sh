#!/usr/bin/env bash
# Removes the modules created by setup_audio.sh.
set -euo pipefail

state="${XDG_RUNTIME_DIR:-/tmp}/morsekey-modules"
[[ -f "$state" ]] || { echo "nothing to tear down" >&2; exit 0; }

# Unload in reverse order: the remap source depends on the sink's monitor.
tac "$state" | while read -r id; do
    pactl unload-module "$id" || true
done
rm -f "$state"
echo "removed zoom_mic and zoom_mix"
