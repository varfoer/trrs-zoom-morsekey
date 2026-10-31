"""wanted_links: which ports the GUI connects, with a fake JACK client."""

import sys
import types

import pytest

sys.modules.setdefault("jack", types.ModuleType("jack"))
tk = pytest.importorskip("tkinter")
from morsekey_gui import DEFAULTS, wanted_links  # noqa: E402

OWN = "morsekey:"


class FakePort:
    def __init__(self, name, is_output):
        self.name = name
        self.shortname = name.rsplit(":", 1)[1]
        self.is_output = is_output


class FakeClient:
    def __init__(self, ports):
        self.ports = ports

    def get_ports(self, is_audio=True, is_output=False, is_input=False):
        return [p for p in self.ports if (is_output and p.is_output) or (is_input and not p.is_output)]


PORTS = [
    FakePort("Built-in:playback_FL", False),
    FakePort("Built-in:playback_FR", False),
    FakePort("USB Headset:playback_FL", False),
    FakePort("USB Headset:playback_FR", False),
    FakePort("HDMI:playback_FL", False),
]


def listen_links(**cfg):
    links = wanted_links(FakeClient(PORTS), OWN, dict(DEFAULTS, **cfg))
    return {dst for src, dst in links if src == OWN + "listen"}


def test_no_listen_device():
    assert listen_links() == set()


def test_one_listen_device():
    assert listen_links(listen_output="USB Headset") == {
        "USB Headset:playback_FL", "USB Headset:playback_FR"}


def test_two_listen_devices():
    assert listen_links(listen_output="USB Headset", listen_output2="HDMI") == {
        "USB Headset:playback_FL", "USB Headset:playback_FR", "HDMI:playback_FL"}


def test_second_listen_device_alone():
    assert listen_links(listen_output2="HDMI") == {"HDMI:playback_FL"}
