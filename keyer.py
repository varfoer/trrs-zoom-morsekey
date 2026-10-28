"""Keyer logic: turns paddle contact states into key-down/key-up, stepped in fixed ticks."""

DIT, DAH = "dit", "dah"
IDLE, ELEMENT, SPACE = "idle", "element", "space"


class StraightKeyer:
    """Key is down while either contact is closed."""

    def step(self, dit, dah):
        return dit or dah


class IambicKeyer:
    """Iambic keyer.

    Mode A: only paddles held at the end of an element space count.
    Mode B: the opposite paddle is latched while an element and its space run,
    so releasing a squeeze still sends one more opposite element.
    """

    def __init__(self, wpm, tick_ms, mode="B"):
        if mode not in ("A", "B"):
            raise ValueError(f"unknown iambic mode {mode!r}")
        self.mode = mode
        self.tick_ms = tick_ms
        self.set_wpm(wpm)
        self.state = IDLE
        self.element = None
        self.remaining = 0.0
        self.mem = {DIT: False, DAH: False}

    def set_wpm(self, wpm):
        # PARIS standard: one dit lasts 1200 / wpm milliseconds.
        self.dit_ms = 1200.0 / wpm

    def _start(self, element, carry):
        self.state = ELEMENT
        self.element = element
        self.remaining = self.dit_ms * (3 if element == DAH else 1) + carry
        self.mem = {DIT: False, DAH: False}

    def _next(self, dit, dah):
        want_dit = dit or self.mem[DIT]
        want_dah = dah or self.mem[DAH]
        if want_dit and want_dah:
            return DAH if self.element == DIT else DIT
        if want_dit:
            return DIT
        if want_dah:
            return DAH
        return None

    def step(self, dit, dah):
        """Advance one tick with the current contact states; return True while key is down."""
        if self.mode == "B" and self.state != IDLE:
            if self.element == DIT and dah:
                self.mem[DAH] = True
            elif self.element == DAH and dit:
                self.mem[DIT] = True

        if self.state == IDLE:
            nxt = self._next(dit, dah)
            if nxt is None:
                return False
            self._start(nxt, 0.0)

        on = self.state == ELEMENT
        self.remaining -= self.tick_ms
        if self.remaining <= 0:
            if self.state == ELEMENT:
                self.state = SPACE
                self.remaining += self.dit_ms
            else:
                nxt = self._next(dit, dah)
                if nxt is None:
                    self.state = IDLE
                else:
                    self._start(nxt, self.remaining)
        return on
