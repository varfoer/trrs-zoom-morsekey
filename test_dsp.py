import numpy as np

from dsp import ContactTracker, Envelope, Oscillator, ProbeDetector, window_size

FS = 48000
F1, F2 = 17000, 19000


def test_window_size_makes_probes_orthogonal():
    n = window_size(FS, F1, F2)
    assert n == 48
    assert (F2 - F1) * n / FS == int((F2 - F1) * n / FS)


def test_oscillator_is_phase_continuous():
    a = Oscillator(1000, FS)
    joined = np.concatenate((a.render(100), a.render(37)))
    ref = np.sin(2 * np.pi * 1000 * np.arange(137) / FS)
    assert np.allclose(joined, ref, atol=1e-5)


def test_detector_separates_probes_across_odd_blocks():
    n = window_size(FS, F1, F2)
    det = ProbeDetector((F1, F2), FS, n)
    t = np.arange(FS // 10) / FS
    x = 0.01 * np.sin(2 * np.pi * F1 * t + 0.3) + 0.2 * np.sin(2 * np.pi * 650 * t)
    levels = np.concatenate([det.process(x[i:i + 129].astype(np.float32)) for i in range(0, len(x), 129)])
    assert np.allclose(levels[:, 0], 0.01, rtol=0.1)
    assert np.all(levels[:, 1] < 0.002)


def test_contact_tracker_detects_close_and_open():
    tr = ContactTracker(window_ms=1.0, warmup_ms=10, debounce=2)
    seq = [1e-4] * 50 + [0.01] * 20 + [1e-4] * 20
    out = [tr.update(v) for v in seq]
    assert not any(out[:50])
    assert all(out[52:70])
    assert not any(out[72:])


def test_contact_tracker_ignores_single_spike():
    tr = ContactTracker(window_ms=1.0, warmup_ms=10, debounce=2)
    out = [tr.update(v) for v in [1e-4] * 30 + [0.01] + [1e-4] * 10]
    assert not any(out)


def test_contact_tracker_without_probe_stays_open():
    tr = ContactTracker(window_ms=1.0, warmup_ms=10)
    assert not any(tr.update(v) for v in np.abs(np.random.default_rng(0).normal(0, 1e-4, 5000)))
    assert tr.calibrated


def test_contact_tracker_key_down_at_start_fails_silent():
    # Calibrating on the probe itself puts the floor at the probe level: nothing keys.
    tr = ContactTracker(window_ms=1.0, warmup_ms=10)
    assert not any(tr.update(0.01) for _ in range(200))


def test_envelope_ramps_smoothly_and_settles():
    env = Envelope(FS, rise_ms=5)
    targets = np.array([True] * 480 + [False] * 480)
    g = env.render(targets)
    assert g[0] < 0.01
    assert np.isclose(g[239], 1.0)
    assert np.isclose(g[479], 1.0)
    assert g[-1] == 0.0
    assert np.max(np.abs(np.diff(g))) < 2 / 240
