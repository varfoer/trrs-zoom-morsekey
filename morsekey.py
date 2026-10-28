#!/usr/bin/env python3
"""Morse key on a laptop combo jack -> sidetone on a virtual microphone.

Series wiring: the headphone outputs play two probe tones (L = dit, R = dah);
a closed paddle passes its probe through resistors into the mic input. The
tones' presence is detected, keyed, and turned into a sidetone that goes to a
PipeWire sink (zoom_mix) and, optionally, to the headphones. A lost probe reads
as key up, so a fault never produces a stuck tone.

Runs as a JACK client; on PipeWire start it with `pw-jack`.
"""

import argparse
import re
import sys
import time

import jack
import numpy as np

from dsp import ContactTracker, Envelope, Oscillator, ProbeDetector, window_size
from keyer import IambicKeyer, StraightKeyer


class MorseKey:
    def __init__(self, client, args):
        fs = client.samplerate
        self.args = args
        self.n = window_size(fs, args.dit_freq, args.dah_freq)
        window_ms = 1000.0 * self.n / fs

        self.detector = ProbeDetector((args.dit_freq, args.dah_freq), fs, self.n)
        self.contacts = (ContactTracker(window_ms), ContactTracker(window_ms))
        if args.mode == "straight":
            self.keyer = StraightKeyer()
        else:
            self.keyer = IambicKeyer(args.wpm, window_ms, args.mode)

        self.probes = (Oscillator(args.dit_freq, fs), Oscillator(args.dah_freq, fs))
        self.tone = Oscillator(args.tone, fs)
        self.env = Envelope(fs, args.rise)
        # Key state per output frame, produced one window late by the detector.
        self.targets = np.zeros(0, dtype=bool)
        self.last_target = False

        self.mic = client.inports.register("mic")
        self.hp = (client.outports.register("hp_left"), client.outports.register("hp_right"))
        self.out = client.outports.register("sidetone")

        # Snapshot for the status display, written by the audio thread.
        self.levels = np.zeros(2)
        self.paddles = (False, False)
        self.key = False

        client.set_process_callback(self.process)

    def process(self, frames):
        levels = self.detector.process(self.mic.get_array())
        states = np.zeros(len(levels), dtype=bool)
        for i, (dit_level, dah_level) in enumerate(levels):
            dit = self.contacts[0].update(dit_level)
            dah = self.contacts[1].update(dah_level)
            if self.args.swap:
                dit, dah = dah, dit
            states[i] = self.keyer.step(dit, dah)
            self.paddles = (dit, dah)
        if len(levels):
            self.levels = levels[-1]
            self.last_target = bool(states[-1])

        self.targets = np.concatenate((self.targets, np.repeat(states, self.n)))
        if len(self.targets) < frames:
            pad = np.full(frames - len(self.targets), self.last_target)
            self.targets = np.concatenate((self.targets, pad))
        take, self.targets = self.targets[:frames], self.targets[frames:]
        if len(self.targets) > 2 * self.n:
            # Never let latency build up if the graph hiccups.
            self.targets = self.targets[-self.n:]
        self.key = bool(take[-1])

        sidetone = self.tone.render(frames) * self.env.render(take)
        for port, probe in zip(self.hp, self.probes):
            port.get_array()[:] = probe.render(frames) * self.args.probe_level + sidetone * self.args.local_level
        self.out.get_array()[:] = sidetone * self.args.level


def find_ports(client, pattern, *, is_output, physical_fallback):
    ports = [p for p in client.get_ports(is_audio=True, is_output=is_output, is_input=not is_output)
             if not p.name.startswith(client.name + ":")]
    if pattern:
        return [p for p in ports if re.search(pattern, p.name, re.IGNORECASE)]
    if physical_fallback:
        return [p for p in ports if p.is_physical]
    return []


def connect_all(client, args, app):
    mic = find_ports(client, args.mic, is_output=True, physical_fallback=True)
    hp = find_ports(client, args.headphones, is_output=False, physical_fallback=True)
    sink = find_ports(client, args.sink, is_output=False, physical_fallback=False)
    if not mic:
        sys.exit("no mic capture port found (see --list-ports, --mic)")
    if len(hp) < 2:
        sys.exit("need two headphone playback ports (see --list-ports, --headphones)")

    client.connect(mic[0], app.mic)
    client.connect(app.hp[0], hp[0])
    client.connect(app.hp[1], hp[1])
    print(f"mic   {mic[0].name}\nhp    {hp[0].name}, {hp[1].name}")

    if not sink:
        print(f"warning: no ports match --sink {args.sink!r}; sidetone goes nowhere (run setup_audio.sh)")
        return
    for port in sink:
        client.connect(app.out, port)
    print("sink  " + ", ".join(p.name for p in sink))

    if args.voice:
        voice = find_ports(client, args.voice, is_output=True, physical_fallback=False)
        if not voice:
            print(f"warning: no ports match --voice {args.voice!r}")
        for i, port in enumerate(sink):
            if voice:
                client.connect(voice[i % len(voice)], port)
        if voice:
            print("voice " + ", ".join(p.name for p in voice))


def list_ports():
    with jack.Client("morsekey-list") as client:
        for port in client.get_ports(is_audio=True):
            kind = "capture/source" if port.is_output else "playback/sink"
            phys = " (physical)" if port.is_physical else ""
            print(f"{kind:15} {port.name}{phys}")


def status_line(app):
    def one(name, tracker, level, closed):
        if not tracker.calibrated:
            return f"{name} calibrating"
        snr = 20 * np.log10(max(level, 1e-12) / tracker.floor)
        return f"{name} {snr:+5.1f} dB {'DOWN' if closed else 'up  '}"
    dit = one("dit", app.contacts[0], app.levels[0], app.paddles[0])
    dah = one("dah", app.contacts[1], app.levels[1], app.paddles[1])
    return f"\r{dit} | {dah} | key {'#' if app.key else '.'} "


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list-ports", action="store_true", help="print audio ports and exit")
    p.add_argument("--mode", choices=("A", "B", "straight"), default="B", help="iambic mode or straight key (default B)")
    p.add_argument("--wpm", type=float, default=20, help="iambic speed (default 20)")
    p.add_argument("--swap", action="store_true", help="swap dit and dah paddles")
    p.add_argument("--tone", type=float, default=650, help="sidetone Hz (default 650)")
    p.add_argument("--rise", type=float, default=5, help="sidetone rise/fall ms (default 5)")
    p.add_argument("--level", type=float, default=0.5, help="sidetone level to the sink (default 0.5)")
    p.add_argument("--local-level", type=float, default=0.2, help="sidetone level in headphones, 0 = off (default 0.2)")
    p.add_argument("--dit-freq", type=float, default=17000, help="probe on headphone L / tip (default 17000)")
    p.add_argument("--dah-freq", type=float, default=19000, help="probe on headphone R / ring1 (default 19000)")
    p.add_argument("--probe-level", type=float, default=0.3, help="probe amplitude (default 0.3)")
    p.add_argument("--mic", help="regex for the mic capture port (default: first physical)")
    p.add_argument("--headphones", help="regex for headphone playback ports (default: first two physical)")
    p.add_argument("--sink", default="zoom_mix", help="regex for sink ports receiving the sidetone (default zoom_mix)")
    p.add_argument("--voice", help="regex for voice mic capture ports to mix into the sink")
    p.add_argument("--quiet", action="store_true", help="no live status line")
    return p.parse_args()


def main():
    args = parse_args()
    if args.list_ports:
        list_ports()
        return

    with jack.Client("morsekey") as client:
        fs = client.samplerate
        if max(args.dit_freq, args.dah_freq) >= fs / 2:
            sys.exit(f"probe frequencies must stay below {fs / 2:.0f} Hz at {fs} Hz")
        app = MorseKey(client, args)
        client.activate()
        connect_all(client, args, app)
        print(f"{fs} Hz, block {client.blocksize}, window {app.n} frames; keep the key up for the first 0.3 s")
        try:
            while True:
                time.sleep(0.05)
                if not args.quiet:
                    print(status_line(app), end="", flush=True)
        except KeyboardInterrupt:
            print()


if __name__ == "__main__":
    main()
