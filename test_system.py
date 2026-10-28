import subprocess

from system import SINK, SOURCE, SystemState

AUTOMUTE_ENABLED = """Simple mixer control 'Auto-Mute Mode',0
  Capabilities: enum
  Items: 'Disabled' 'Enabled'
  Item0: 'Enabled'
"""


class FakeSystem:
    """Minimal pactl/amixer stand-in recording every call."""

    def __init__(self, sinks=(), sources=(), automute_cards=(0,)):
        self.calls = []
        self.sinks = list(sinks)
        self.sources = list(sources)
        self.modules = {}
        self.automute = {c: "Enabled" for c in automute_cards}
        self.next_id = 500

    def __call__(self, *cmd):
        self.calls.append(cmd)
        if cmd[:3] == ("pactl", "list", "short"):
            kind = cmd[3]
            if kind == "sinks":
                return "".join(f"1\t{n}\tmodule\n" for n in self.sinks)
            if kind == "sources":
                return "".join(f"2\t{n}\tmodule\n" for n in self.sources)
            return "".join(f"{i}\t{name}\t{args}\n" for i, (name, args) in self.modules.items())
        if cmd[:2] == ("pactl", "load-module"):
            self.next_id += 1
            self.modules[self.next_id] = (cmd[2], " ".join(cmd[3:]))
            if cmd[2] == "module-null-sink":
                self.sinks.append(SINK)
            else:
                self.sources.append(SOURCE)
            return f"{self.next_id}\n"
        if cmd[:2] == ("pactl", "unload-module"):
            name, args = self.modules.pop(int(cmd[2]))
            (self.sinks if "sink_name" in args else self.sources).clear()
            return ""
        if cmd[0] == "amixer":
            card = int(cmd[2])
            if cmd[4] == "Auto-Mute Mode":
                if card not in self.automute:
                    raise subprocess.CalledProcessError(1, cmd)
                if cmd[3] == "sget":
                    return AUTOMUTE_ENABLED.replace("'Enabled'\n", f"'{self.automute[card]}'\n")
                self.automute[card] = cmd[5]
            return ""
        raise AssertionError(f"unexpected command {cmd}")


def make(tmp_path, fake):
    return SystemState(run=fake, cards=lambda: [0, 1], path=tmp_path / "restore.json")


def test_creates_devices_and_removes_them_on_restore(tmp_path):
    fake = FakeSystem()
    s = make(tmp_path, fake)
    s.ensure_devices()
    assert fake.sinks == [SINK] and fake.sources == [SOURCE]
    s.restore()
    assert fake.sinks == [] and fake.sources == [] and fake.modules == {}
    assert not (tmp_path / "restore.json").exists()


def test_existing_devices_are_left_alone(tmp_path):
    fake = FakeSystem(sinks=[SINK], sources=[SOURCE])
    s = make(tmp_path, fake)
    s.ensure_devices()
    s.restore()
    assert fake.sinks == [SINK] and fake.sources == [SOURCE]
    assert not any(c[1] in ("load-module", "unload-module") for c in fake.calls if c[0] == "pactl")


def test_automute_disabled_and_restored(tmp_path):
    fake = FakeSystem(automute_cards=(1,))
    s = make(tmp_path, fake)
    assert s.speakers_on()
    assert fake.automute == {1: "Disabled"}
    s.speakers_restore()
    assert fake.automute == {1: "Enabled"}


def test_no_automute_control_reports_false(tmp_path):
    s = make(tmp_path, FakeSystem(automute_cards=()))
    assert not s.speakers_on()


def test_crashed_run_is_undone_on_next_start(tmp_path):
    fake = FakeSystem()
    s = make(tmp_path, fake)
    s.ensure_devices()
    s.speakers_on()
    # Process dies here: no restore() call. Next start:
    SystemState.recover(run=fake, cards=lambda: [0, 1], path=tmp_path / "restore.json")
    assert fake.sinks == [] and fake.sources == []
    assert fake.automute == {0: "Enabled"}
    assert not (tmp_path / "restore.json").exists()
