from keyer import IambicKeyer, StraightKeyer

WPM = 20  # dit = 60 ms
TICK = 1.0


def run(keyer, paddles):
    """paddles: list of (dit, dah) per tick. Returns the key output as a string of '#'/'.'."""
    return "".join("#" if keyer.step(d, a) else "." for d, a in paddles)


def runs(trace):
    """Collapse a trace into (char, length) pairs."""
    out = []
    for c in trace:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return [tuple(x) for x in out]


def hold(dit, dah, ms):
    return [(dit, dah)] * int(ms / TICK)


def test_held_dit_repeats_dits():
    trace = run(IambicKeyer(WPM, TICK), hold(True, False, 300))
    assert runs(trace)[:4] == [("#", 60), (".", 60), ("#", 60), (".", 60)]


def test_held_dah_repeats_dahs():
    trace = run(IambicKeyer(WPM, TICK), hold(False, True, 400))
    assert runs(trace)[:3] == [("#", 180), (".", 60), ("#", 160)]


def test_squeeze_alternates():
    trace = run(IambicKeyer(WPM, TICK), hold(True, True, 800))
    assert [n for c, n in runs(trace) if c == "#"][:4] == [60, 180, 60, 180]


def test_mode_b_sends_extra_element_after_squeeze_release():
    paddles = hold(True, True, 10) + hold(False, False, 500)
    trace = run(IambicKeyer(WPM, TICK, "B"), paddles)
    assert [n for c, n in runs(trace) if c == "#"] == [60, 180]


def test_mode_a_stops_after_squeeze_release():
    paddles = hold(True, True, 10) + hold(False, False, 500)
    trace = run(IambicKeyer(WPM, TICK, "A"), paddles)
    assert [n for c, n in runs(trace) if c == "#"] == [60]


def test_short_tap_sends_full_element():
    trace = run(IambicKeyer(WPM, TICK), hold(False, True, 5) + hold(False, False, 300))
    assert [n for c, n in runs(trace) if c == "#"] == [180]


def test_timing_stays_exact_with_fractional_ticks():
    keyer = IambicKeyer(WPM, 0.7)
    on = sum(keyer.step(True, False) for _ in range(int(1200 / 0.7)))
    # 1200 ms of held dit = 10 dits of 60 ms each.
    assert abs(on * 0.7 - 600) <= 0.7 * 10


def test_straight_keyer_follows_contacts():
    k = StraightKeyer()
    assert [k.step(*p) for p in [(False, False), (True, False), (False, True), (False, False)]] == [
        False, True, True, False]
