"""Signal processing: probe/sidetone oscillators, probe detection, keying envelope."""

import math

import numpy as np


class Oscillator:
    """Phase-continuous sine oscillator."""

    def __init__(self, freq, fs):
        self.fs = fs
        self.set_freq(freq)
        self.phase = 0.0

    def set_freq(self, freq):
        self.step = 2 * math.pi * freq / self.fs

    def render(self, n):
        ph = self.phase + self.step * np.arange(n)
        self.phase = (self.phase + self.step * n) % (2 * math.pi)
        return np.sin(ph).astype(np.float32)


def window_size(fs, f1, f2, min_ms=1.0):
    """Frames per detection window.

    A multiple of fs / |f1 - f2| makes the two probes orthogonal over one window,
    so the dit probe never leaks into the dah measurement and vice versa.
    """
    if f1 == f2:
        raise ValueError("probe frequencies must differ")
    base = fs / abs(f1 - f2)
    k = max(1, math.ceil(min_ms * fs / 1000 / base))
    return int(round(base * k))


class ProbeDetector:
    """Measures the amplitude of each probe frequency per window of n frames.

    The window sum's magnitude does not depend on the start phase, so every
    window uses the same demodulation table and leftover frames carry over.
    A second-difference high-pass first suppresses sidetone, voice and hum,
    which would otherwise leak into the short rectangular windows.
    """

    def __init__(self, freqs, fs, n):
        self.n = n
        steps = 2 * np.pi * np.asarray(freqs, dtype=float) / fs
        self.table = np.exp(-1j * np.outer(np.arange(n), steps))
        self.gain = (2 * np.sin(steps / 2)) ** 2
        self.prev = np.zeros(2, dtype=np.float32)
        self.buf = np.zeros(0, dtype=np.float32)

    def process(self, x):
        """Return an array (windows, len(freqs)) of probe amplitudes."""
        ext = np.concatenate((self.prev, x))
        self.prev = ext[-2:].copy()
        hp = ext[2:] - 2 * ext[1:-1] + ext[:-2]
        buf = np.concatenate((self.buf, hp))
        windows = len(buf) // self.n
        used = windows * self.n
        self.buf = buf[used:].copy()
        if windows == 0:
            return np.zeros((0, self.table.shape[1]))
        frames = buf[:used].reshape(windows, self.n)
        return 2 * np.abs(frames @ self.table) / self.n / self.gain


class ContactTracker:
    """Turns probe amplitudes into a contact state: probe present means contact closed.

    The noise floor is learned during warmup (key must be up) and tracked slowly
    while the contact is open; the contact closes when the probe rises well above
    it. A lost probe (unplugged, muted) therefore reads as open, never as a stuck key.
    """

    NOISE_MIN = 1e-5

    def __init__(self, window_ms, warmup_ms=300, track_ms=2000,
                 close_snr=8.0, open_snr=4.0, debounce=2):
        self.warmup = max(1, int(warmup_ms / window_ms))
        self.alpha = min(1.0, window_ms / track_ms)
        self.close_snr = close_snr
        self.open_snr = open_snr
        self.debounce = debounce
        self.noise = 0.0
        self.samples = 0
        self.closed = False
        self.pending = 0

    @property
    def calibrated(self):
        return self.warmup == 0

    @property
    def floor(self):
        return max(self.noise, self.NOISE_MIN)

    def update(self, level):
        if self.warmup > 0:
            self.warmup -= 1
            self.samples += 1
            self.noise += (level - self.noise) / self.samples
            return False

        snr = self.open_snr if self.closed else self.close_snr
        candidate = level > snr * self.floor
        if candidate != self.closed:
            self.pending += 1
            if self.pending >= self.debounce:
                self.closed = candidate
                self.pending = 0
        else:
            self.pending = 0

        if not self.closed and self.pending == 0:
            self.noise += self.alpha * (level - self.noise)
        return self.closed


class Envelope:
    """Raised-cosine keying envelope; avoids key clicks."""

    def __init__(self, fs, rise_ms=5.0):
        self.step = 1000.0 / (rise_ms * fs)
        self.x = 0.0

    def render(self, targets):
        """targets: per-frame bool array (key down). Returns per-frame gain 0..1."""
        out = np.empty(len(targets))
        starts = np.concatenate(([0], np.flatnonzero(np.diff(targets)) + 1))
        ends = np.concatenate((starts[1:], [len(targets)]))
        for s, e in zip(starts, ends):
            if s == e:
                continue
            direction = self.step if targets[s] else -self.step
            ramp = np.clip(self.x + direction * np.arange(1, e - s + 1), 0.0, 1.0)
            out[s:e] = ramp
            self.x = ramp[-1]
        return (0.5 - 0.5 * np.cos(np.pi * out)).astype(np.float32)
