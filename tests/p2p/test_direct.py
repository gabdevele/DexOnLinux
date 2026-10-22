import threading
from dataclasses import replace

import pytest

from dexonlinux.p2p.base import GroupNetwork, P2PError, P2PRole
from dexonlinux.p2p.control import SupplicantError
from dexonlinux.p2p.direct import DirectBackend
from dexonlinux.p2p.events import parse_event


class FakeCtrl:
    def __init__(self, events=None):
        self.events = list(events or [])
        self.opened = False
        self.closed = False
        self.wfd_state = object()
        self.prepared = None
        self.restored = None
        self.connected = []
        self.disconnected = False
        self.listening = None
        self.stopped_listen = False

    def open(self):
        self.opened = True

    def close(self):
        self.closed = True

    def prepare_wfd(self, *, go_intent, device_name):
        self.prepared = (go_intent, device_name)
        return self.wfd_state

    def restore_wfd(self, state):
        self.restored = state

    def start_extended_listen(self, period_ms, interval_ms):
        self.listening = (period_ms, interval_ms)

    def stop_extended_listen(self):
        self.stopped_listen = True

    def connect(self, peer, *, frequency, go_intent):
        self.connected.append((peer, frequency, go_intent))

    def disconnect(self):
        self.disconnected = True

    def pump(self, timeout):
        if self.events:
            return [self.events.pop(0)]
        return []


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


def event(line):
    parsed = parse_event(line)
    assert parsed is not None
    return parsed


def group_event(role="client", client_ip="192.168.49.200"):
    address = f"ip_addr={client_ip} ip_mask=255.255.255.0 " if client_ip else ""
    return (
        f'P2P-GROUP-STARTED p2p-wlp0s20-7 {role} ssid="x" freq=2437 '
        f"go_dev_addr=ce:f9:f0:2b:0a:cc {address}go_ip_addr=192.168.49.1"
    )


def test_prepare_opens_and_starts_extended_listen():
    ctrl = FakeCtrl()
    link = FakeLink()
    backend = DirectBackend("wlp0s20f3", ctrl, link)

    backend.prepare()

    assert ctrl.opened is True
    assert ctrl.prepared == (0, "DexOnLinux")
    assert ctrl.listening == (1000, 1000)
    assert link.stale_removed is True


def test_pbc_request_connects_through_control():
    ctrl = FakeCtrl(
        events=[
            event("P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc"),
            event(
                "P2P-GO-NEG-SUCCESS role=client freq=2437 peer_dev=ce:f9:f0:2b:8a:cc "
                "peer_iface=ce:f9:f0:2b:0a:cc wps_method=PBC"
            ),
            event(group_event()),
        ]
    )
    link = FakeLink()
    backend = DirectBackend("wlp0s20f3", ctrl, link)

    group = backend.connect(stop=threading.Event(), timeout=1.0)

    assert len(ctrl.connected) == 1
    peer, frequency, go_intent = ctrl.connected[0]
    assert peer.mac == "ce:f9:f0:2b:8a:cc"
    assert (frequency, go_intent) == (2437, 0)
    assert link.watcher_started is True
    assert link.configured == [("p2p-wlp0s20-7", "192.168.49.200/24")]
    assert group.role is P2PRole.CLIENT
    assert group.peer_ip == "192.168.49.1"


def test_duplicate_pbc_request_connects_once():
    ctrl = FakeCtrl(
        events=[
            event("P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc"),
            event("P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc"),
            event(group_event()),
        ]
    )
    backend = DirectBackend("wlp0s20f3", ctrl, FakeLink())

    backend.connect(stop=threading.Event(), timeout=1.0)

    assert len(ctrl.connected) == 1


def test_preup_waits_for_go_negotiation_success():
    ctrl = FakeCtrl(
        events=[
            event("P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc"),
            event(
                "P2P-GO-NEG-SUCCESS role=client freq=2437 peer_dev=ce:f9:f0:2b:8a:cc "
                "peer_iface=ce:f9:f0:2b:0a:cc wps_method=PBC"
            ),
            event(group_event()),
        ]
    )
    link = FakeLink()
    backend = DirectBackend("wlp0s20f3", ctrl, link)

    backend._send_connect(event("P2P-PROV-DISC-PBC-REQ aa:bb:cc:dd:ee:ff").peer)
    assert link.watcher_started is False

    backend.connect(stop=threading.Event(), timeout=1.0)
    assert link.watcher_started is True


def test_before_group_hook_runs_once_before_connect():
    calls = []
    ctrl = FakeCtrl()
    backend = DirectBackend(
        "wlp0s20f3",
        ctrl,
        FakeLink(),
        before_group=lambda: calls.append("suspend"),
    )

    backend._send_connect(event("P2P-PROV-DISC-PBC-REQ aa:bb:cc:dd:ee:01").peer)
    backend._send_connect(event("P2P-PROV-DISC-PBC-REQ aa:bb:cc:dd:ee:02").peer)

    assert calls == ["suspend"]
    assert len(ctrl.connected) == 2


def test_connect_uses_the_configured_frequency():
    ctrl = FakeCtrl()
    backend = DirectBackend("wlp0s20f3", ctrl, FakeLink(), frequency=2412)

    backend._send_connect(event("P2P-PROV-DISC-PBC-REQ aa:bb:cc:dd:ee:01").peer)

    assert ctrl.connected[0][1] == 2412


def test_go_group_is_rejected_when_client_role_was_requested():
    backend = DirectBackend("wlp0s20f3", FakeCtrl(events=[event(group_event(role="GO"))]), FakeLink())

    with pytest.raises(P2PError, match="client role"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_client_group_is_rejected_when_go_role_was_requested():
    backend = DirectBackend(
        "wlp0s20f3",
        FakeCtrl(events=[event(group_event())]),
        FakeLink(),
        role=P2PRole.GO,
        dhcp=FakeDhcp(),
    )

    with pytest.raises(P2PError, match="GO role"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_go_role_uses_the_go_network_and_dhcp():
    ctrl = FakeCtrl(
        events=[
            event("P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc"),
            event(
                "P2P-GO-NEG-SUCCESS role=GO freq=2437 peer_dev=ce:f9:f0:2b:8a:cc "
                "peer_iface=ce:f9:f0:2b:0a:cc wps_method=PBC"
            ),
            event(group_event(role="GO", client_ip="")),
        ]
    )
    link = FakeLink()
    dhcp = FakeDhcp(peer_ip="10.13.37.2")
    backend = DirectBackend(
        "wlp0s20f3",
        ctrl,
        link,
        role=P2PRole.GO,
        network=GroupNetwork("10.13.37.1", "10.13.37.2", 24),
        dhcp=dhcp,
    )

    group = backend.connect(stop=threading.Event(), timeout=1.0)

    assert group.role is P2PRole.GO
    assert group.local_ip == "10.13.37.1"
    assert group.peer_ip == "10.13.37.2"
    assert ctrl.connected[0][2] == 15
    assert link.configured == [("p2p-wlp0s20-7", "10.13.37.1/24")]
    assert dhcp.started == ["p2p-wlp0s20-7"]


def test_go_role_without_dhcp_server_fails():
    backend = DirectBackend(
        "wlp0s20f3",
        FakeCtrl(events=[event(group_event(role="GO"))]),
        FakeLink(),
        role=P2PRole.GO,
    )

    with pytest.raises(P2PError, match="DHCP"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_pin_request_is_rejected_with_actionable_message():
    backend = DirectBackend(
        "wlp0s20f3",
        FakeCtrl(events=[event("P2P-PROV-DISC-SHOW-PIN ce:f9:f0:2b:8a:cc 12345678")]),
        FakeLink(),
    )

    with pytest.raises(P2PError, match="PBC"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_invitation_is_rejected_with_stale_group_message():
    backend = DirectBackend(
        "wlp0s20f3",
        FakeCtrl(events=[event("P2P-INVITATION-RECEIVED ce:f9:f0:2b:8a:cc persistent=1")]),
        FakeLink(),
    )

    with pytest.raises(P2PError, match="previously connected"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_go_negotiation_with_non_pbc_password_id_is_rejected():
    backend = DirectBackend(
        "wlp0s20f3",
        FakeCtrl(events=[event("P2P-GO-NEG-REQUEST ce:f9:f0:2b:8a:cc dev_passwd_id=1 go_intent=15")]),
        FakeLink(),
    )

    with pytest.raises(P2PError, match="PBC"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_group_without_client_ip_fails_clearly():
    ctrl = FakeCtrl(
        events=[
            event("P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc"),
            event(group_event(client_ip="")),
        ]
    )
    backend = DirectBackend("wlp0s20f3", ctrl, FakeLink())

    with pytest.raises(P2PError, match="WPS client address"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_close_disconnects_and_restores_wfd():
    ctrl = FakeCtrl()
    link = FakeLink()
    backend = DirectBackend("wlp0s20f3", ctrl, link)
    backend.prepare()
    backend.group_ifname = "p2p-wlp0s20-7"

    backend.close()

    assert ctrl.disconnected is True
    assert link.removed == ["p2p-wlp0s20-7"]
    assert link.watcher_stopped is True
    assert ctrl.restored is ctrl.wfd_state
    assert ctrl.closed is True


def test_wait_for_group_end_returns_the_reason():
    ctrl = FakeCtrl(events=[event("P2P-GROUP-REMOVED p2p-wlp0s20-7 reason=test")])
    backend = DirectBackend("wlp0s20f3", ctrl, FakeLink())

    assert backend.wait_for_group_end(threading.Event()) == "P2P-GROUP-REMOVED p2p-wlp0s20-7 reason=test"


def test_wait_for_group_end_returns_none_when_stopped():
    ctrl = FakeCtrl()
    backend = DirectBackend("wlp0s20f3", ctrl, FakeLink())
    stop = threading.Event()
    stop.set()

    assert backend.wait_for_group_end(stop) is None


def test_go_group_with_a_conflicting_address_fails_clearly():
    ctrl = FakeCtrl(
        events=[
            event(
                'P2P-GROUP-STARTED p2p-wlp0s20-7 GO ssid="x" freq=2437 '
                "ip_addr=192.168.49.1 ip_mask=255.255.255.0"
            )
        ]
    )
    backend = DirectBackend(
        "wlp0s20f3",
        ctrl,
        FakeLink(),
        role=P2PRole.GO,
        network=GroupNetwork("10.13.37.1", "10.13.37.2", 24),
        dhcp=FakeDhcp(),
    )

    with pytest.raises(P2PError, match="does not match"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_duplicate_pbc_request_from_a_different_path_connects_once():
    first = event("P2P-PROV-DISC-PBC-REQ aa:bb:cc:dd:ee:ff")
    second = replace(first, peer=replace(first.peer, path=f"{first.peer.mac.replace(':', '_')}"))
    ctrl = FakeCtrl(events=[first, second, event(group_event())])
    backend = DirectBackend("wlp0s20f3", ctrl, FakeLink())

    backend.connect(stop=threading.Event(), timeout=1.0)

    assert len(ctrl.connected) == 1


def test_failed_connect_is_retried():
    ctrl = FakeCtrl()
    attempts = []

    def connect(peer, *, frequency, go_intent):
        attempts.append(peer)
        if len(attempts) == 1:
            raise SupplicantError("busy")

    ctrl.connect = connect
    backend = DirectBackend("wlp0s20f3", ctrl, FakeLink())
    peer = event("P2P-PROV-DISC-PBC-REQ aa:bb:cc:dd:ee:ff").peer

    with pytest.raises(SupplicantError, match="busy"):
        backend._connect_pbc(peer)
    backend._connect_pbc(peer)

    assert len(attempts) == 2
