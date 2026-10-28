"""System changes made for a session, undone on exit.

Creates the zoom_mix sink and zoom_mic source, and optionally disables ALSA
auto-mute so the laptop speakers keep playing while the key occupies the
combo jack. Every change is written to a state file before it is made, so a
crashed run is undone on the next start.
"""

import json
import os
import re
import subprocess
from pathlib import Path

SINK, SOURCE = "zoom_mix", "zoom_mic"
STATE_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "morsekey" / "restore.json"
AUTO_MUTE = "Auto-Mute Mode"


def run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def alsa_cards():
    try:
        text = Path("/proc/asound/cards").read_text()
    except OSError:
        return []
    return [int(m.group(1)) for m in re.finditer(r"^\s*(\d+) \[", text, re.MULTILINE)]


class SystemState:
    def __init__(self, run=run, cards=alsa_cards, path=STATE_FILE):
        self.run = run
        self.cards = cards
        self.path = path
        self.state = {"modules": [], "automute": []}

    @classmethod
    def recover(cls, **kw):
        """Undo whatever a previous run left behind, then return a clean instance."""
        s = cls(**kw)
        if s.path.exists():
            try:
                s.state = json.loads(s.path.read_text())
            except ValueError:
                pass
            s.restore()
        return s

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state))

    def _short(self, kind):
        return [line.split("\t") for line in self.run("pactl", "list", "short", kind).splitlines() if line]

    def _exists(self, kind, name):
        return any(len(f) > 1 and f[1] == name for f in self._short(kind))

    def ensure_devices(self):
        """Create zoom_mix and zoom_mic unless they exist already (e.g. from setup_audio.sh)."""
        if not self._exists("sinks", SINK):
            self.state["modules"].append(SINK)
            self._save()
            self.run("pactl", "load-module", "module-null-sink", f"sink_name={SINK}",
                     f"sink_properties=device.description={SINK}")
        if not self._exists("sources", SOURCE):
            self.state["modules"].append(SOURCE)
            self._save()
            self.run("pactl", "load-module", "module-remap-source", f"master={SINK}.monitor",
                     f"source_name={SOURCE}", f"source_properties=device.description={SOURCE}")

    def _unload(self, name):
        key = f"sink_name={name}" if name == SINK else f"source_name={name}"
        for f in self._short("modules"):
            if len(f) > 2 and key in f[2].split():
                self.run("pactl", "unload-module", f[0])

    def speakers_on(self):
        """Disable auto-mute on every card that has it. Returns False if no card has it."""
        if self.state["automute"]:
            return True
        for card in self.cards():
            try:
                m = re.search(r"Item0: '([^']*)'", self.run("amixer", "-c", str(card), "sget", AUTO_MUTE))
            except subprocess.CalledProcessError:
                continue
            if not m:
                continue
            self.state["automute"].append({"card": card, "mode": m.group(1)})
            self._save()
            self.run("amixer", "-c", str(card), "sset", AUTO_MUTE, "Disabled")
            try:
                # PipeWire mutes the speaker element on the headphone route; auto-mute
                # restored later mutes it again while the jack is in use.
                self.run("amixer", "-c", str(card), "sset", "Speaker", "unmute")
            except subprocess.CalledProcessError:
                pass
        return bool(self.state["automute"])

    def speakers_restore(self):
        for entry in self.state["automute"]:
            try:
                self.run("amixer", "-c", str(entry["card"]), "sset", AUTO_MUTE, entry["mode"])
            except subprocess.CalledProcessError:
                pass
        self.state["automute"] = []
        self._save()

    def restore(self):
        """Undo every recorded change and forget the state file."""
        self.speakers_restore()
        for name in reversed(self.state["modules"]):
            try:
                self._unload(name)
            except (OSError, subprocess.CalledProcessError):
                pass
        self.state["modules"] = []
        self.path.unlink(missing_ok=True)
