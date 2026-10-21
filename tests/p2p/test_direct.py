import threading

import pytest

from dexonlinux.p2p.base import P2PError
from dexonlinux.p2p.direct import DirectBackend


class FakeCtrl:
    def __init__(self, events=None):
        self.events = list(events or [])
        self.commands = []
        self.opened = False
        self.closed = False

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def command(self, command, timeout=2.0):
        self.commands.append(command)
        return "OK"

    def pump(self, timeout):
        if self.events:
            return [self.events.pop(0)]
        return []


class FakeWfd:
    def __init__(self):
        self.applied = False
        self.restored = False

    def apply(self, ctrl, device_name="DexOnLinux"):
        self.applied = True
        return object()

    def restore(self, ctrl, state):
        self.restored = True


class FakeLink:
    def __init__(self):
        self.configured = []
        self.removed = []

    def configure(self, ifname, cidr):
        self.configured.append((ifname, cidr))

    def remove(self, ifname):
        self.removed.append(ifname)


def group_event(role="client", client_ip="192.168.49.200"):
    address = f"ip_addr={client_ip} ip_mask=255.255.255.0 " if client_ip else ""
    return (
        f'P2P-GROUP-STARTED p2p-wlp0s20-7 {role} ssid="x" freq=2437 '
        f"go_dev_addr=ce:f9:f0:2b:0a:cc {address}go_ip_addr=192.168.49.1"
    )


def test_prepare_opens_and_advertises():
    ctrl = FakeCtrl()
    wfd = FakeWfd()
    backend = DirectBackend("wlp0s20f3", ctrl, wfd, FakeLink())
    backend.prepare()
    assert ctrl.opened is True
    assert wfd.applied is True
    assert "P2P_EXT_LISTEN 1000 1000" in ctrl.commands


def test_pbc_request_starts_client_connect_and_returns_group():
    ctrl = FakeCtrl(
        events=[
            "P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc",
            group_event(),
        ]
    )
    link = FakeLink()
    backend = DirectBackend("wlp0s20f3", ctrl, FakeWfd(), link)
    group = backend.connect(stop=threading.Event(), timeout=1.0)
    assert "P2P_CONNECT ce:f9:f0:2b:8a:cc pbc freq=2437 go_intent=0" in ctrl.commands
    assert link.configured == [("p2p-wlp0s20-7", "192.168.49.200/24")]
    assert group.role == "client"
    assert group.go_ip == "192.168.49.1"


def test_go_role_is_rejected():
    backend = DirectBackend("wlp0s20f3", FakeCtrl(events=[group_event(role="GO")]), FakeWfd(), FakeLink())
    with pytest.raises(P2PError, match="client"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_pin_request_is_rejected_with_actionable_message():
    event = "P2P-PROV-DISC-SHOW-PIN ce:f9:f0:2b:8a:cc 12345678"
    backend = DirectBackend("wlp0s20f3", FakeCtrl(events=[event]), FakeWfd(), FakeLink())
    with pytest.raises(P2PError, match="PBC"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_invitation_is_rejected_with_stale_group_message():
    event = "P2P-INVITATION-RECEIVED ce:f9:f0:2b:8a:cc persistent=1"
    backend = DirectBackend("wlp0s20f3", FakeCtrl(events=[event]), FakeWfd(), FakeLink())
    with pytest.raises(P2PError, match="previously connected"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_go_negotiation_with_non_pbc_password_id_is_rejected():
    event = "P2P-GO-NEG-REQUEST ce:f9:f0:2b:8a:cc dev_passwd_id=1 go_intent=15"
    backend = DirectBackend("wlp0s20f3", FakeCtrl(events=[event]), FakeWfd(), FakeLink())
    with pytest.raises(P2PError, match="PBC"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_group_without_client_ip_fails_clearly():
    ctrl = FakeCtrl(
        events=[
            "P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc",
            group_event(client_ip=""),
        ]
    )
    backend = DirectBackend("wlp0s20f3", ctrl, FakeWfd(), FakeLink())
    with pytest.raises(P2PError, match="WPS client address"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_close_removes_group_and_restores_supplicant():
    ctrl = FakeCtrl()
    link = FakeLink()
    wfd = FakeWfd()
    backend = DirectBackend("wlp0s20f3", ctrl, wfd, link)
    backend.prepare()
    backend.group_ifname = "p2p-wlp0s20-7"
    backend.close()
    assert "P2P_GROUP_REMOVE p2p-wlp0s20-7" in ctrl.commands
    assert link.removed == ["p2p-wlp0s20-7"]
    assert wfd.restored is True
    assert ctrl.closed is True
