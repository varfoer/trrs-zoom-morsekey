#!/usr/bin/env python3
"""GUI for morsekey: pick devices once, start automatically, undo all system changes on exit."""

import json
import os
import signal
import subprocess
import tkinter as tk
from argparse import Namespace
from pathlib import Path
from tkinter import messagebox, ttk

import jack
import numpy as np

from morsekey import MorseKey
from system import SINK, SOURCE, SystemState

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "morsekey" / "config.json"
NONE = "(none)"
DEFAULTS = dict(
    key_input="", probe_output="", voice_input="", listen_output="", keep_speakers=False,
    mode="B", wpm=20.0, tone=650.0, level=0.5, local_level=0.2, swap=False, buffer=128,
    dit_freq=17000.0, dah_freq=19000.0, probe_level=0.3, rise=5.0,
)
ENGINE_KEYS = ("mode", "wpm", "swap", "tone", "rise", "level", "local_level",
               "dit_freq", "dah_freq", "probe_level")
HIDDEN = ("morsekey", SINK, SOURCE)  # clients that are never offered as devices


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))


def device_of(port):
    return port.name.rsplit(":", 1)[0]


def device_ports(client, device, *, capture):
    """A device's capture ports (capture=True) or playback ports, without monitors."""
    return [p for p in client.get_ports(is_audio=True, is_output=capture, is_input=not capture)
            if device_of(p) == device and not p.shortname.startswith("monitor")]


def devices(client, *, capture):
    names = []
    for p in client.get_ports(is_audio=True, is_output=capture, is_input=not capture):
        d = device_of(p)
        if d not in names and not d.startswith(HIDDEN) and not p.shortname.startswith("monitor"):
            names.append(d)
    return names


def wanted_links(client, own, cfg):
    """(source, destination) port pairs for the current configuration."""
    links = []
    mic = device_ports(client, cfg["key_input"], capture=True)
    if mic:
        links.append((mic[0].name, own + "mic"))
    hp = device_ports(client, cfg["probe_output"], capture=False)
    if len(hp) >= 2:
        links += [(own + "hp_left", hp[0].name), (own + "hp_right", hp[1].name)]
    sink = device_ports(client, SINK, capture=False)
    links += [(own + "sidetone", p.name) for p in sink]
    if cfg["voice_input"]:
        voice = device_ports(client, cfg["voice_input"], capture=True)
        if voice:
            links += [(voice[i % len(voice)].name, p.name) for i, p in enumerate(sink)]
    if cfg["listen_output"]:
        links += [(own + "listen", p.name) for p in device_ports(client, cfg["listen_output"], capture=False)]
    return set(links)


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.system = SystemState.recover()
        self.ui = jack.Client("morsekey-ui", no_start_server=True)
        self.client = None
        self.engine = None
        self.links = set()
        self.vars = {}
        self.available = {True: [], False: []}  # capture / playback device names
        self.closing = False

        root.title("morsekey")
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.build()
        self.refresh_devices()
        if self.cfg["keep_speakers"]:
            self.set_speakers(True)
        if self.cfg["key_input"] and self.cfg["probe_output"]:
            self.start()
        self.tick_status()
        self.tick_devices()

    # ---- layout

    def build(self):
        pad = dict(padx=6, pady=3)
        frame = ttk.Frame(self.root, padding=10)
        frame.grid(sticky="nsew")

        dev = ttk.LabelFrame(frame, text="Devices", padding=6)
        dev.grid(row=0, column=0, sticky="ew", **pad)
        self.combos = {}
        rows = (("key_input", "Key input (combo jack mic)", True, False),
                ("probe_output", "Probe output (combo jack)", False, False),
                ("voice_input", "Voice mic for Zoom", True, True),
                ("listen_output", "Hear sidetone on", False, True))
        for r, (key, label, capture, optional) in enumerate(rows):
            ttk.Label(dev, text=label).grid(row=r, column=0, sticky="w", **pad)
            combo = ttk.Combobox(dev, state="readonly", width=40)
            combo.grid(row=r, column=1, sticky="ew", **pad)
            combo.bind("<<ComboboxSelected>>", lambda e, k=key: self.on_device(k))
            self.combos[key] = (combo, capture, optional)
        self.vars["keep_speakers"] = tk.BooleanVar(value=self.cfg["keep_speakers"])
        ttk.Checkbutton(dev, text="Keep laptop speakers on (disable auto-mute)",
                        variable=self.vars["keep_speakers"],
                        command=lambda: self.set_speakers(self.vars["keep_speakers"].get())
                        ).grid(row=len(rows), column=0, columnspan=2, sticky="w", **pad)

        key = ttk.LabelFrame(frame, text="Keyer", padding=6)
        key.grid(row=1, column=0, sticky="ew", **pad)
        self.vars["mode"] = tk.StringVar(value=self.cfg["mode"])
        for c, (value, text) in enumerate((("B", "Iambic B"), ("A", "Iambic A"), ("straight", "Straight key"))):
            ttk.Radiobutton(key, text=text, value=value, variable=self.vars["mode"],
                            command=self.on_keyer).grid(row=0, column=c, sticky="w", **pad)
        self.vars["swap"] = tk.BooleanVar(value=self.cfg["swap"])
        ttk.Checkbutton(key, text="Swap paddles", variable=self.vars["swap"],
                        command=lambda: self.set_value("swap", self.vars["swap"].get())
                        ).grid(row=0, column=3, sticky="w", **pad)
        self.slider(key, 1, "wpm", "Speed (WPM)", 5, 50, "{:.0f}", self.on_keyer)
        self.slider(key, 2, "tone", "Tone (Hz)", 300, 1200, "{:.0f}", self.on_tone)
        self.slider(key, 3, "level", "Level to Zoom", 0, 1, "{:.2f}")
        self.slider(key, 4, "local_level", "Level for you", 0, 1, "{:.2f}")
        ttk.Label(key, text="Buffer (frames)").grid(row=5, column=0, sticky="w", **pad)
        buf = ttk.Combobox(key, state="readonly", width=6, values=(64, 128, 256, 512))
        buf.set(self.cfg["buffer"])
        buf.grid(row=5, column=1, sticky="w", **pad)
        buf.bind("<<ComboboxSelected>>", lambda e: self.on_buffer(int(buf.get())))

        st = ttk.LabelFrame(frame, text="Status", padding=6)
        st.grid(row=2, column=0, sticky="ew", **pad)
        self.bars, self.bar_labels = [], []
        for r, name in enumerate(("dit", "dah")):
            ttk.Label(st, text=name).grid(row=r, column=0, sticky="w", **pad)
            bar = ttk.Progressbar(st, maximum=60, length=260)
            bar.grid(row=r, column=1, **pad)
            lbl = ttk.Label(st, width=14)
            lbl.grid(row=r, column=2, sticky="w", **pad)
            self.bars.append(bar)
            self.bar_labels.append(lbl)
        self.lamp = tk.Canvas(st, width=28, height=28, highlightthickness=0)
        self.lamp.grid(row=0, column=3, rowspan=2, **pad)
        self.lamp_dot = self.lamp.create_oval(4, 4, 24, 24, fill="grey")
        self.status = ttk.Label(st, text="")
        self.status.grid(row=2, column=0, columnspan=4, sticky="w", **pad)
        ttk.Label(st, text=f'In Zoom choose "{SOURCE}" as microphone.').grid(
            row=3, column=0, columnspan=4, sticky="w", **pad)

        btns = ttk.Frame(frame)
        btns.grid(row=3, column=0, sticky="e", **pad)
        self.start_btn = ttk.Button(btns, text="Start", command=self.start)
        self.start_btn.grid(row=0, column=0, **pad)
        self.stop_btn = ttk.Button(btns, text="Stop", command=self.stop)
        self.stop_btn.grid(row=0, column=1, **pad)
        ttk.Button(btns, text="Quit", command=self.quit).grid(row=0, column=2, **pad)

    def slider(self, parent, row, key, label, lo, hi, fmt, command=None):
        pad = dict(padx=6, pady=3)
        var = tk.DoubleVar(value=self.cfg[key])
        self.vars[key] = var
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", **pad)
        value = ttk.Label(parent, width=6, text=fmt.format(var.get()))
        value.grid(row=row, column=3, sticky="w", **pad)

        def changed(_):
            value.config(text=fmt.format(var.get()))
            self.set_value(key, var.get())
            if command:
                command()
        ttk.Scale(parent, from_=lo, to=hi, variable=var, command=changed, length=260).grid(
            row=row, column=1, columnspan=2, sticky="ew", **pad)

    # ---- settings

    def set_value(self, key, value):
        self.cfg[key] = value
        if self.engine and key in ENGINE_KEYS:
            setattr(self.engine.args, key, value)
        save_config(self.cfg)

    def on_keyer(self):
        self.set_value("mode", self.vars["mode"].get())
        if self.engine:
            self.engine.set_keyer(self.cfg["mode"], self.cfg["wpm"])

    def on_tone(self):
        if self.engine:
            self.engine.tone.set_freq(self.cfg["tone"])

    def on_buffer(self, frames):
        self.set_value("buffer", frames)
        if self.engine:
            self.stop()
            self.start()

    def on_device(self, key):
        combo, _, _ = self.combos[key]
        value = combo.get()
        self.set_value(key, "" if value == NONE else value)
        if key in ("key_input", "probe_output"):
            # The probe calibration belongs to the old device: start over.
            self.stop()
            self.start()
        else:
            self.sync_links()

    def set_speakers(self, on):
        self.set_value("keep_speakers", on)
        try:
            if on and not self.system.speakers_on():
                messagebox.showwarning("morsekey", "No sound card with an auto-mute setting found.")
            elif not on:
                self.system.speakers_restore()
        except (OSError, subprocess.CalledProcessError) as e:
            messagebox.showerror("morsekey", f"amixer failed: {e}")

    # ---- engine

    def start(self):
        if self.engine:
            return
        if not (self.cfg["key_input"] and self.cfg["probe_output"]):
            self.status.config(text="Choose key input and probe output first.")
            return
        try:
            self.system.ensure_devices()
        except (OSError, subprocess.CalledProcessError) as e:
            messagebox.showerror("morsekey", f"Could not create {SINK}/{SOURCE} (pactl): {e}")
            return
        # pipewire-jack reads this when the client opens; it asks the graph for small buffers.
        os.environ["PIPEWIRE_LATENCY"] = f"{self.cfg['buffer']}/48000"
        try:
            client = jack.Client("morsekey", no_start_server=True)
        except jack.JackOpenError as e:
            messagebox.showerror("morsekey", f"Could not open a JACK client (is pipewire-jack installed?): {e}")
            return
        if max(self.cfg["dit_freq"], self.cfg["dah_freq"]) >= client.samplerate / 2:
            client.close()
            messagebox.showerror("morsekey", f"Probe frequencies too high for {client.samplerate} Hz.")
            return
        self.engine = MorseKey(client, Namespace(**{k: self.cfg[k] for k in ENGINE_KEYS}))
        client.activate()
        self.client = client
        self.sync_links()

    def stop(self):
        if not self.client:
            return
        for src, dst in self.links:
            try:
                self.client.disconnect(src, dst)
            except jack.JackError:
                pass
        self.links = set()
        self.client.deactivate(ignore_errors=True)
        self.client.close(ignore_errors=True)
        self.client = self.engine = None

    def sync_links(self):
        """Make the graph match the configuration; picks up devices that appear later."""
        if not self.client:
            return
        wanted = wanted_links(self.client, self.client.name + ":", self.cfg)
        for src, dst in self.links - wanted:
            try:
                self.client.disconnect(src, dst)
            except jack.JackError:
                pass
        for src, dst in wanted:
            try:
                if dst not in [p.name for p in self.client.get_all_connections(src)]:
                    self.client.connect(src, dst)
            except jack.JackError:
                pass
        self.links = wanted

    # ---- periodic updates

    def refresh_devices(self):
        self.available = {c: devices(self.ui, capture=c) for c in (True, False)}
        for key, (combo, capture, optional) in self.combos.items():
            names = list(self.available[capture])
            current = self.cfg[key]
            if current and current not in names:
                names.append(current)  # remembered device that is currently absent
            combo["values"] = ([NONE] if optional else []) + names
            combo.set(current or (NONE if optional else ""))

    def tick_devices(self):
        self.refresh_devices()
        self.sync_links()
        self.root.after(1000, self.tick_devices)

    def tick_status(self):
        e = self.engine
        if e:
            for i, (bar, lbl) in enumerate(zip(self.bars, self.bar_labels)):
                tracker = e.contacts[i]
                if not tracker.calibrated:
                    bar["value"] = 0
                    lbl.config(text="calibrating")
                    continue
                snr = 20 * np.log10(max(e.levels[i], 1e-12) / tracker.floor)
                bar["value"] = max(0.0, min(60.0, snr))
                lbl.config(text=f"{snr:+5.1f} dB {'DOWN' if e.paddles[i] else 'up'}")
            self.lamp.itemconfig(self.lamp_dot, fill="red" if e.key else "grey")
            missing = [self.cfg[k] for k in ("key_input", "probe_output")
                       if self.cfg[k] not in self.available[k == "key_input"]]
            self.status.config(text=(f"waiting for {', '.join(missing)}" if missing
                                     else f"running, {e.n * 1000 / self.client.samplerate:.0f} ms windows, "
                                          f"buffer {self.client.blocksize} frames"))
        else:
            self.lamp.itemconfig(self.lamp_dot, fill="grey")
            text = self.status.cget("text")
            if not text or text.startswith(("running", "waiting")):
                self.status.config(text="stopped")
        self.start_btn.state(["disabled"] if e else ["!disabled"])
        self.stop_btn.state(["!disabled"] if e else ["disabled"])
        self.root.after(50, self.tick_status)

    def quit(self):
        if self.closing:
            return
        self.closing = True
        self.stop()
        self.system.restore()
        self.ui.close(ignore_errors=True)
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        app = App(root)
    except jack.JackOpenError as e:
        root.withdraw()
        messagebox.showerror("morsekey", f"Could not connect to PipeWire's JACK interface: {e}")
        return
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: root.after(0, app.quit))
    root.mainloop()


if __name__ == "__main__":
    main()
