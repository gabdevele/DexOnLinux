# tests/p2p/test_events.py
from dexonlinux.p2p.events import EventKind, parse_event


def test_parses_device_found_with_wfd():
    event = parse_event(
        "P2P-DEVICE-FOUND ce:f9:f0:2b:8a:cc p2p_dev_addr=ce:f9:f0:2b:8a:cc "
        "name='S24 di Gabriele' config_methods=0x188 wfd_dev_info=0x01101c440032 new=1"
    )
    assert event.kind is EventKind.DEVICE_FOUND
    assert event.peer.mac == "ce:f9:f0:2b:8a:cc"
    assert event.peer.name == "S24 di Gabriele"
    assert event.peer.wfd is True


def test_parses_pbc_request():
    event = parse_event("P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc p2p_dev_addr=ce:f9:f0:2b:8a:cc")
    assert event.kind is EventKind.PBC_REQUEST
    assert event.peer.mac == "ce:f9:f0:2b:8a:cc"


def test_parses_go_negotiation_password_id():
    event = parse_event("P2P-GO-NEG-REQUEST ce:f9:f0:2b:8a:cc dev_passwd_id=4 go_intent=15")
    assert event.kind is EventKind.GO_NEGOTIATION
    assert event.peer.mac == "ce:f9:f0:2b:8a:cc"
    assert event.passwd_id == 4


def test_parses_go_negotiation_success_without_leading_mac():
    event = parse_event(
        "P2P-GO-NEG-SUCCESS role=client freq=2437 ht40=0 "
        "peer_dev=ce:f9:f0:2b:8a:cc peer_iface=ce:f9:f0:2b:0a:cc wps_method=PBC"
    )
    assert event.kind is EventKind.GO_NEGOTIATION
    assert event.peer.mac == "ce:f9:f0:2b:0a:cc"
    assert event.passwd_id is None
    assert event.raw.startswith("P2P-GO-NEG-SUCCESS")


def test_parses_invitation_as_actionable_failure():
    event = parse_event(
        "P2P-INVITATION-RECEIVED ce:f9:f0:2b:8a:cc sa=ce:f9:f0:2b:8a:cc persistent=1"
    )
    assert event.kind is EventKind.INVITATION
    assert event.peer.mac == "ce:f9:f0:2b:8a:cc"


def test_parses_client_group_with_wps_address():
    event = parse_event(
        'P2P-GROUP-STARTED p2p-wlp0s20-7 client ssid="DIRECT-cf-S24" freq=2437 '
        "go_dev_addr=ce:f9:f0:2b:0a:cc ip_addr=192.168.49.200 "
        "ip_mask=255.255.255.0 go_ip_addr=192.168.49.1"
    )
    assert event.kind is EventKind.GROUP_STARTED
    assert event.group.ifname == "p2p-wlp0s20-7"
    assert event.group.role == "client"
    assert event.group.client_ip == "192.168.49.200"
    assert event.group.client_prefix == 24
    assert event.group.go_ip == "192.168.49.1"
    assert event.group.peer_mac == "ce:f9:f0:2b:0a:cc"
    assert event.group.frequency == 2437


def test_parses_go_role_group():
    event = parse_event('P2P-GROUP-STARTED p2p-wlp0s20-8 GO ssid="x" freq=2437')
    assert event.group.role == "GO"


def test_rejects_malformed_and_ignores_unknown():
    assert parse_event("noise") is None
    assert parse_event("P2P-GROUP-STARTED broken") is None
