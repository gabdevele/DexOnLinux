# tests/p2p/test_supplicant.py
import socket
import threading
import time

import pytest

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
    assert ctrl.pump(0.5) == ["P2P-DEVICE-FOUND aa:bb:cc:dd:ee:ff name='x'"]
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
