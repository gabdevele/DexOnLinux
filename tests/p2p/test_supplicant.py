# tests/p2p/test_supplicant.py
import socket
import threading
import time

import pytest

from dexonlinux.p2p.base import Peer
from dexonlinux.p2p.events import EventKind
from dexonlinux.p2p.supplicant import SupplicantControl, SupplicantError


@pytest.fixture
def fake_ctrl(tmp_path):
    path = tmp_path / "p2p-dev-test"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(path))
    server.settimeout(2.0)
    yield path, server
    server.close()


def test_attach_command_and_event(fake_ctrl):
    path, server = fake_ctrl
    ctrl = SupplicantControl(path)

    def serve():
        data, addr = server.recvfrom(4096)
        assert data == b"ATTACH"
        server.sendto(b"OK\n", addr)
        data, addr = server.recvfrom(4096)
        assert data == b"GET wifi_display"
        server.sendto(b"1\n", addr)
        # A priority-tagged event arrives after the command reply.
        time.sleep(0.05)
        server.sendto(b"<3>P2P-DEVICE-FOUND aa:bb:cc:dd:ee:ff name='x'\n", addr)

    server_thread = threading.Thread(target=serve)
    server_thread.start()
    ctrl.open()
    assert ctrl.command("GET wifi_display") == "1"
    server_thread.join(1.0)
    events = ctrl.pump(0.5)
    assert [event.kind for event in events] == [EventKind.DEVICE_FOUND]
    assert events[0].peer is not None
    assert events[0].peer.mac == "aa:bb:cc:dd:ee:ff"
    ctrl.close()


def test_missing_reply_raises(fake_ctrl):
    path, server = fake_ctrl
    ctrl = SupplicantControl(path)

    def serve():
        data, addr = server.recvfrom(4096)
        assert data == b"ATTACH"
        server.sendto(b"OK\n", addr)
        data, addr = server.recvfrom(4096)
        assert data == b"GET x"
        # Deliberately do not reply.

    server_thread = threading.Thread(target=serve)
    server_thread.start()
    ctrl.open()
    with pytest.raises(SupplicantError):
        ctrl.command("GET x", timeout=0.1)
    server_thread.join(1.0)


def make_control(tmp_path):
    ctrl = SupplicantControl(tmp_path / "p2p-dev-test")
    commands = []

    def command(text, timeout=2.0):
        commands.append(text)
        return "OK"

    ctrl.command = command
    return ctrl, commands


def test_prepare_wfd_sets_and_restores_state(tmp_path):
    ctrl, commands = make_control(tmp_path)
    replies = {
        "GET wifi_display": "1",
        "GET p2p_go_intent": "7",
        "GET config_methods": "0x188",
        "GET device_name": "host",
    }

    def command(text, timeout=2.0):
        commands.append(text)
        return replies.get(text, "OK")

    ctrl.command = command

    state = ctrl.prepare_wfd(go_intent=15, device_name="DexOnLinux")

    assert "SET wifi_display 1" in commands
    assert "SET p2p_go_intent 15" in commands
    ctrl.restore_wfd(state)
    assert "SET p2p_go_intent 7" in commands


def test_pump_returns_typed_events_and_tracks_the_group(tmp_path):
    ctrl, _commands = make_control(tmp_path)
    ctrl._events.put('P2P-GROUP-STARTED p2p-wlp0s20-7 GO ssid="x" freq=2437')

    events = ctrl.pump(0)

    assert [event.kind for event in events] == [EventKind.GROUP_STARTED]
    assert events[0].group is not None
    assert events[0].group.ifname == "p2p-wlp0s20-7"
    assert ctrl._group_ifname == "p2p-wlp0s20-7"


def test_connect_uses_the_peer_mac_and_pbc(tmp_path):
    ctrl, commands = make_control(tmp_path)

    ctrl.connect(Peer(mac="aa:bb:cc:dd:ee:ff"), frequency=2437, go_intent=15)

    assert commands == ["P2P_CONNECT aa:bb:cc:dd:ee:ff pbc freq=2437 go_intent=15"]


def test_disconnect_removes_the_tracked_group(tmp_path):
    ctrl, commands = make_control(tmp_path)
    ctrl._group_ifname = "p2p-wlp0s20-7"

    ctrl.disconnect()

    assert commands == ["P2P_GROUP_REMOVE p2p-wlp0s20-7"]
    assert ctrl._group_ifname is None


def test_extended_listen_start_and_stop(tmp_path):
    ctrl, commands = make_control(tmp_path)

    ctrl.start_extended_listen(1000, 1000)
    ctrl.stop_extended_listen()

    assert commands == ["P2P_EXT_LISTEN 1000 1000", "P2P_EXT_LISTEN"]
