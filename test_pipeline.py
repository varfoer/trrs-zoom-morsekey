"""End-to-end test of MorseKey.process with a fake JACK client and simulated wiring."""

import sys
import types
from argparse import Namespace

import numpy as np

sys.modules.setdefault("jack", types.ModuleType("jack"))
from morsekey import MorseKey  # noqa: E402

FS = 48000
BLOCK = 256


class FakePort:
    def __init__(self):
        self.data = np.zeros(BLOCK, dtype=np.float32)

    def get_array(self):
        return self.data


class FakePorts:
    def register(self, name):
        return FakePort()


class FakeClient:
    samplerate = FS

    def __init__(self):
        self.inports = FakePorts()
        self.outports = FakePorts()

    def set_process_callback(self, cb):
        self.callback = cb


def args(**kw):
    base = dict(mode="B", wpm=20, swap=False, tone=650, rise=5, level=0.5, local_level=0.2,
                dit_freq=17000, dah_freq=19000, probe_level=0.3)
    base.update(kw)
    return Namespace(**base)


def simulate(app, client, paddles_at, seconds, coupling=0.03):
    """paddles_at(t) -> (dit_closed, dah_closed). Returns the sidetone output."""
    rng = np.random.default_rng(1)
    out = []
    hp = [np.zeros(BLOCK, dtype=np.float32)] * 2
    for b in range(int(seconds * FS / BLOCK)):
        dit, dah = paddles_at(b * BLOCK / FS)
        # Series wiring: a headphone channel reaches the mic only while its paddle is closed.
        mic = coupling * (dit * hp[0] + dah * hp[1]) + 1e-4 * rng.standard_normal(BLOCK)
        app.mic.data[:] = mic
        client.callback(BLOCK)
        hp = [app.hp[0].data.copy(), app.hp[1].data.copy()]
        out.append(app.out.data.copy())
    return np.concatenate(out)


def keyed_ms(out):
    """Lengths in ms of the sidetone bursts."""
    env = np.convolve(np.abs(out) > 0.05, np.ones(80), "same") > 0
    edges = np.flatnonzero(np.diff(env.astype(int)))
    return [round((e - s) * 1000 / FS) for s, e in zip(edges[::2], edges[1::2])]


def test_dit_tap_sends_one_dit():
    client = FakeClient()
    app = MorseKey(client, args())
    out = simulate(app, client, lambda t: (0.5 <= t < 0.52, False), 1.0)
    bursts = keyed_ms(out)
    assert len(bursts) == 1
    assert abs(bursts[0] - 60) <= 5


def test_squeeze_sends_alternating_elements():
    client = FakeClient()
    app = MorseKey(client, args())
    out = simulate(app, client, lambda t: (0.5 <= t < 0.85, 0.5 <= t < 0.85), 1.5)
    bursts = keyed_ms(out)
    assert len(bursts) >= 3
    assert abs(bursts[0] - 60) <= 5 and abs(bursts[1] - 180) <= 5


def test_no_output_while_paddles_idle():
    client = FakeClient()
    app = MorseKey(client, args())
    out = simulate(app, client, lambda t: (False, False), 1.0)
    assert np.max(np.abs(out)) < 1e-3


def test_lost_probe_never_keys():
    # Cable pulled or headphone output muted while the paddle is held.
    client = FakeClient()
    app = MorseKey(client, args(mode="straight"))
    out = simulate(app, client, lambda t: (0.5 <= t < 0.9, False), 1.2, coupling=0.0)
    assert np.max(np.abs(out)) < 1e-3


def test_straight_mode_follows_key():
    client = FakeClient()
    app = MorseKey(client, args(mode="straight"))
    out = simulate(app, client, lambda t: (0.5 <= t < 0.8, False), 1.2)
    bursts = keyed_ms(out)
    assert len(bursts) == 1
    assert abs(bursts[0] - 300) <= 8
