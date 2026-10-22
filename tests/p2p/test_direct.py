import threading

import pytest

from dexonlinux.p2p.base import GroupNetwork, P2PError, P2PRole
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
        self.stale_removed = False
        self.watcher_started = False
        self.watcher_stopped = False

    def configure(self, ifname, cidr):
        self.configured.append((ifname, cidr))

    def remove(self, ifname):
        self.removed.append(ifname)

    def remove_stale(self):
        self.stale_removed = True

    def start_watcher(self):
        self.watcher_started = True

    def stop_watcher(self):
        self.watcher_stopped = True


class FakeDhcp:
    def __init__(self, peer_ip="192.168.49.2"):
        self.started = []
        self.stopped = False
        self.peer_ip = peer_ip

    def start(self, interface):
        self.started.append(interface)

    def stop(self):
        self.stopped = True


def group_event(role="client", client_ip="192.168.49.200"):
    address = f"ip_addr={client_ip} ip_mask=255.255.255.0 " if client_ip else ""
    return (
        f'P2P-GROUP-STARTED p2p-wlp0s20-7 {role} ssid="x" freq=2437 '
        f"go_dev_addr=ce:f9:f0:2b:0a:cc {address}go_ip_addr=192.168.49.1"
    )


def test_prepare_opens_and_advertises():
    ctrl = FakeCtrl()
    wfd = FakeWfd()
    link = FakeLink()
    backend = DirectBackend("wlp0s20f3", ctrl, wfd, link)
    backend.prepare()
    assert ctrl.opened is True
    assert wfd.applied is True
    assert link.stale_removed is True
    assert "P2P_EXT_LISTEN 1000 1000" in ctrl.commands


def test_pbc_request_starts_client_connect_and_returns_group():
    ctrl = FakeCtrl(
        events=[
            "P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc",
            (
                "P2P-GO-NEG-SUCCESS role=client freq=2437 peer_dev=ce:f9:f0:2b:8a:cc "
                "peer_iface=ce:f9:f0:2b:0a:cc wps_method=PBC"
            ),
            group_event(),
        ]
    )
    link = FakeLink()
    backend = DirectBackend("wlp0s20f3", ctrl, FakeWfd(), link)
    group = backend.connect(stop=threading.Event(), timeout=1.0)
    assert link.watcher_started is True
    assert "P2P_CONNECT ce:f9:f0:2b:8a:cc pbc freq=2437 go_intent=0" in ctrl.commands
    assert link.configured == [("p2p-wlp0s20-7", "192.168.49.200/24")]
    assert group.role is P2PRole.CLIENT
    assert group.peer_ip == "192.168.49.1"


def test_preup_waits_for_go_negotiation_success():
    ctrl = FakeCtrl(
        events=[
            "P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc",
            (
                "P2P-GO-NEG-SUCCESS role=client freq=2437 peer_dev=ce:f9:f0:2b:8a:cc "
                "peer_iface=ce:f9:f0:2b:0a:cc wps_method=PBC"
            ),
            group_event(),
        ]
    )
    link = FakeLink()
    backend = DirectBackend("wlp0s20f3", ctrl, FakeWfd(), link)

    backend._send_connect("ce:f9:f0:2b:8a:cc")
    assert link.watcher_started is False

    backend.connect(stop=threading.Event(), timeout=1.0)
    assert link.watcher_started is True


def test_before_group_hook_runs_once_before_p2p_connect():
    calls = []
    ctrl = FakeCtrl()
    backend = DirectBackend(
        "wlp0s20f3",
        ctrl,
        FakeWfd(),
        FakeLink(),
        before_group=lambda: calls.append("suspend"),
    )

    backend._send_connect("aa:bb:cc:dd:ee:01")
    backend._send_connect("aa:bb:cc:dd:ee:02")

    assert calls == ["suspend"]
    assert ctrl.commands[0].startswith("P2P_CONNECT aa:bb:cc:dd:ee:01")


def test_connect_uses_the_configured_frequency():
    ctrl = FakeCtrl()
    backend = DirectBackend("wlp0s20f3", ctrl, FakeWfd(), FakeLink(), frequency=2412)
    backend._send_connect("aa:bb:cc:dd:ee:01")
    assert "freq=2412" in ctrl.commands[0]


def test_go_group_is_rejected_when_client_role_was_requested():
    backend = DirectBackend("wlp0s20f3", FakeCtrl(events=[group_event(role="GO")]), FakeWfd(), FakeLink())
    with pytest.raises(P2PError, match="client role"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_client_group_is_rejected_when_go_role_was_requested():
    backend = DirectBackend(
        "wlp0s20f3",
        FakeCtrl(events=[group_event()]),
        FakeWfd(),
        FakeLink(),
        role=P2PRole.GO,
        dhcp=FakeDhcp(),
    )
    with pytest.raises(P2PError, match="GO role"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_go_role_uses_the_go_network_and_dhcp():
    ctrl = FakeCtrl(
        events=[
            "P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc",
            (
                "P2P-GO-NEG-SUCCESS role=GO freq=2437 peer_dev=ce:f9:f0:2b:8a:cc "
                "peer_iface=ce:f9:f0:2b:0a:cc wps_method=PBC"
            ),
            group_event(role="GO", client_ip=""),
        ]
    )
    link = FakeLink()
    dhcp = FakeDhcp(peer_ip="10.13.37.2")
    backend = DirectBackend(
        "wlp0s20f3",
        ctrl,
        FakeWfd(),
        link,
        role=P2PRole.GO,
        network=GroupNetwork("10.13.37.1", "10.13.37.2", 24),
        dhcp=dhcp,
    )
    group = backend.connect(stop=threading.Event(), timeout=1.0)
    assert group.role is P2PRole.GO
    assert group.local_ip == "10.13.37.1"
    assert group.peer_ip == "10.13.37.2"
    assert "P2P_CONNECT ce:f9:f0:2b:8a:cc pbc freq=2437 go_intent=15" in ctrl.commands
    assert link.configured == [("p2p-wlp0s20-7", "10.13.37.1/24")]
    assert dhcp.started == ["p2p-wlp0s20-7"]


def test_go_role_without_dhcp_server_fails():
    backend = DirectBackend(
        "wlp0s20f3",
        FakeCtrl(events=[group_event(role="GO")]),
        FakeWfd(),
        FakeLink(),
        role=P2PRole.GO,
    )
    with pytest.raises(P2PError, match="DHCP"):
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
    assert link.watcher_stopped is True
    assert wfd.restored is True
    assert ctrl.closed is True


def test_go_group_with_a_conflicting_address_fails_clearly():
    ctrl = FakeCtrl(
        events=[
            'P2P-GROUP-STARTED p2p-wlp0s20-7 GO ssid="x" freq=2437 '
            "ip_addr=192.168.49.1 ip_mask=255.255.255.0"
        ]
    )
    backend = DirectBackend(
        "wlp0s20f3",
        ctrl,
        FakeWfd(),
        FakeLink(),
        role=P2PRole.GO,
        network=GroupNetwork("10.13.37.1", "10.13.37.2", 24),
        dhcp=FakeDhcp(),
    )
    with pytest.raises(P2PError, match="does not match"):
        backend.connect(stop=threading.Event(), timeout=1.0)
