import pytest

from dexonlinux.p2p.base import P2PError, P2PRole, Peer
from dexonlinux.p2p.control import SupplicantError
from dexonlinux.p2p.dbus import (
    GROUP_IFACE,
    INTERFACE_IFACE,
    P2P_IFACE,
    ROOT_IFACE,
    ROOT_PATH,
    WPS_IFACE,
    DBusWfdState,
    WpaSupplicantDBus,
    _ipv4,
    _prefix,
)
from dexonlinux.p2p.events import EventKind
from dexonlinux.p2p.wfd import WFD_IES

from .fake_dbus import DEVICE, GROUP_DEVICE, GROUP_OBJECT, FakeDBusClient


def make_control(client=None):
    client = client or FakeDBusClient()
    control = WpaSupplicantDBus("wlan0", client)
    return control, client


def test_open_finds_device_probes_and_subscribes():
    control, client = make_control()

    control.open()

    assert client.connected is True
    assert client.subscribed == [DEVICE]
    assert control.device == DEVICE


def test_open_closes_the_client_when_the_device_is_missing():
    client = FakeDBusClient()
    client.find_error = SupplicantError("no P2P device")
    control = WpaSupplicantDBus("wlan0", client)

    with pytest.raises(SupplicantError, match="no P2P device"):
        control.open()

    assert client.closed is True


def test_prepare_wfd_saves_and_applies_state():
    control, client = make_control()
    control.open()

    state = control.prepare_wfd(go_intent=15, device_name="DexOnLinux")

    assert state == DBusWfdState(
        wfd_ies=b"\x00\x00\x06\x00\x11\x1c\x44\x00\xc8",
        go_intent=7,
        device_name="old-host",
        config_methods="0x188",
    )
    assert client.properties[(ROOT_PATH, ROOT_IFACE, "WFDIEs")] == WFD_IES
    config = client.properties[(DEVICE, P2P_IFACE, "P2PDeviceConfig")]
    assert config["GOIntent"].value == 15
    assert config["DeviceName"].value == "DexOnLinux"
    assert client.properties[(DEVICE, WPS_IFACE, "ConfigMethods")] == "0x80"


def test_prepare_wfd_restores_on_failure():
    client = FakeDBusClient()
    client.fail_set = (WPS_IFACE, "ConfigMethods")
    control = WpaSupplicantDBus("wlan0", client)
    control.open()

    with pytest.raises(P2PError, match="Wi-Fi Display"):
        control.prepare_wfd(go_intent=15, device_name="DexOnLinux")

    assert client.properties[(ROOT_PATH, ROOT_IFACE, "WFDIEs")] == b"\x00\x00\x06\x00\x11\x1c\x44\x00\xc8"
    config = client.properties[(DEVICE, P2P_IFACE, "P2PDeviceConfig")]
    assert config["GOIntent"].value == 7
    assert config["DeviceName"].value == "old-host"


def test_restore_wfd_clears_ies_when_previous_empty():
    client = FakeDBusClient()
    client.properties[(ROOT_PATH, ROOT_IFACE, "WFDIEs")] = b""
    control = WpaSupplicantDBus("wlan0", client)
    control.open()
    state = control.prepare_wfd(go_intent=15, device_name="DexOnLinux")

    control.restore_wfd(state)

    assert client.properties[(ROOT_PATH, ROOT_IFACE, "WFDIEs")] == b""
    assert client.properties[(DEVICE, WPS_IFACE, "ConfigMethods")] == "0x188"


def test_start_and_stop_extended_listen():
    control, client = make_control()
    control.open()

    control.start_extended_listen(1000, 1000)
    control.stop_extended_listen()

    assert client.calls == [
        (DEVICE, P2P_IFACE, "ExtendedListen", ({"period": 1000, "interval": 1000},)),
        (DEVICE, P2P_IFACE, "ExtendedListen", ({},)),
    ]


def test_connect_uses_peer_path_and_pbc():
    control, client = make_control()
    control.open()
    peer = Peer(mac="aa:bb:cc:dd:ee:ff", path=f"{DEVICE}/Peers/aa_bb_cc_dd_ee_ff")

    control.connect(peer, frequency=2437, go_intent=15)

    assert client.calls == [
        (
            DEVICE,
            P2P_IFACE,
            "Connect",
            (
                {
                    "peer": peer.path,
                    "wps_method": "pbc",
                    "frequency": 2437,
                    "go_intent": 15,
                },
            ),
        )
    ]


def test_connect_without_peer_path_fails_clearly():
    control, _client = make_control()
    control.open()

    with pytest.raises(SupplicantError, match="object path"):
        control.connect(Peer(mac="aa:bb:cc:dd:ee:ff"), frequency=2437, go_intent=15)


def test_disconnect_uses_the_group_interface():
    control, client = make_control()
    control.open()
    control._group_interface = GROUP_DEVICE

    control.disconnect()

    assert client.calls == [(GROUP_DEVICE, P2P_IFACE, "Disconnect", ())]
    assert control._group_interface is None


def started_control(client):
    control = WpaSupplicantDBus("wlan0", client)
    control.open()
    return control


def test_pbc_request_maps_to_event():
    client = FakeDBusClient()
    control = started_control(client)
    peer_path = f"{DEVICE}/Peers/aa_bb_cc_dd_ee_ff"

    client.emit("ProvisionDiscoveryPBCRequest", (peer_path,))
    events = control.pump(0)

    assert [event.kind for event in events] == [EventKind.PBC_REQUEST]
    assert events[0].peer is not None
    assert events[0].peer.mac == "aa:bb:cc:dd:ee:ff"
    assert events[0].peer.path == peer_path


def test_pin_request_maps_to_event():
    client = FakeDBusClient()
    control = started_control(client)

    client.emit("ProvisionDiscoveryRequestDisplayPin", (f"{DEVICE}/Peers/aa_bb_cc_dd_ee_ff", "12345678"))
    events = control.pump(0)

    assert [event.kind for event in events] == [EventKind.PIN_REQUEST]


def test_go_negotiation_request_maps_password_id():
    client = FakeDBusClient()
    control = started_control(client)

    client.emit("GONegotiationRequest", (f"{DEVICE}/Peers/aa_bb_cc_dd_ee_ff", 4, 15))
    events = control.pump(0)

    assert [event.kind for event in events] == [EventKind.GO_NEGOTIATION]
    assert events[0].passwd_id == 4
    assert events[0].peer is not None


def test_go_negotiation_success_maps_peer():
    client = FakeDBusClient()
    control = started_control(client)

    client.emit(
        "GONegotiationSuccess",
        (
            {
                "peer_object": f"{DEVICE}/Peers/aa_bb_cc_dd_ee_ff",
                "role_go": "client",
                "frequency": 2437,
            },
        ),
    )
    events = control.pump(0)

    assert [event.kind for event in events] == [EventKind.GO_NEGOTIATION_SUCCESS]
    assert events[0].peer is not None
    assert events[0].peer.mac == "aa:bb:cc:dd:ee:ff"


def test_group_started_client_reads_ip_mask_and_go_address():
    client = FakeDBusClient()
    control = started_control(client)
    client.properties[(GROUP_DEVICE, INTERFACE_IFACE, "Ifname")] = "p2p-wlan0-0"
    client.properties[(GROUP_OBJECT, GROUP_IFACE, "Frequency")] = 2437
    client.emit(
        "GONegotiationSuccess",
        ({"peer_object": f"{DEVICE}/Peers/aa_bb_cc_dd_ee_ff", "role_go": "client"},),
    )

    client.emit(
        "GroupStarted",
        (
            {
                "interface_object": GROUP_DEVICE,
                "role": "client",
                "group_object": GROUP_OBJECT,
                "IpAddr": bytes([192, 168, 49, 200]),
                "IpAddrMask": bytes([255, 255, 255, 0]),
                "IpAddrGo": bytes([192, 168, 49, 1]),
            },
        ),
    )
    events = control.pump(0)

    started = [event for event in events if event.kind is EventKind.GROUP_STARTED]
    assert len(started) == 1
    group = started[0].group
    assert group is not None
    assert group.ifname == "p2p-wlan0-0"
    assert group.role is P2PRole.CLIENT
    assert group.local_ip == "192.168.49.200"
    assert group.peer_ip == "192.168.49.1"
    assert group.local_prefix == 24
    assert group.frequency == 2437
    assert group.go_dev_addr == "aa:bb:cc:dd:ee:ff"


def test_group_started_go_has_no_ip_properties():
    client = FakeDBusClient()
    control = started_control(client)
    client.properties[(GROUP_DEVICE, INTERFACE_IFACE, "Ifname")] = "p2p-wlan0-0"
    client.properties[(GROUP_OBJECT, GROUP_IFACE, "Frequency")] = 2437
    client.properties[(DEVICE, P2P_IFACE, "DeviceAddress")] = bytes([0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff])

    client.emit(
        "GroupStarted",
        (
            {
                "interface_object": GROUP_DEVICE,
                "role": "GO",
                "group_object": GROUP_OBJECT,
            },
        ),
    )
    events = control.pump(0)

    group = events[0].group
    assert group is not None
    assert group.role is P2PRole.GO
    assert group.local_ip is None
    assert group.peer_ip is None
    assert group.local_prefix == 24
    assert group.go_dev_addr == "aa:bb:cc:dd:ee:ff"


def test_group_finished_clears_group_state():
    client = FakeDBusClient()
    control = started_control(client)
    client.properties[(GROUP_DEVICE, INTERFACE_IFACE, "Ifname")] = "p2p-wlan0-0"
    client.properties[(GROUP_OBJECT, GROUP_IFACE, "Frequency")] = 2437
    client.emit(
        "GroupStarted",
        ({"interface_object": GROUP_DEVICE, "role": "GO", "group_object": GROUP_OBJECT},),
    )
    control.pump(0)

    client.emit(
        "GroupFinished",
        ({"interface_object": GROUP_DEVICE, "role": "GO", "group_object": GROUP_OBJECT},),
    )
    events = control.pump(0)

    assert [event.kind for event in events] == [EventKind.GROUP_REMOVED]
    assert control._group_interface is None


def test_invitation_maps_to_event():
    client = FakeDBusClient()
    control = started_control(client)

    client.emit("InvitationReceived", ({"go_dev_addr": bytes([0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff])},))
    events = control.pump(0)

    assert [event.kind for event in events] == [EventKind.INVITATION]
    assert events[0].peer is not None
    assert events[0].peer.mac == "aa:bb:cc:dd:ee:ff"


def test_failure_maps_to_failure_event():
    client = FakeDBusClient()
    control = started_control(client)

    client.emit("GONegotiationFailure", ({"status": 1},))
    events = control.pump(0)

    assert [event.kind for event in events] == [EventKind.FAILURE]
    assert "status=1" in events[0].raw


def test_device_found_properties_maps_to_event():
    client = FakeDBusClient()
    control = started_control(client)
    peer_path = f"{DEVICE}/Peers/aa_bb_cc_dd_ee_ff"

    client.emit(
        "DeviceFoundProperties",
        (peer_path, {"DeviceName": "Phone", "IEs": bytes([0x00, 0x00, 0x06])}),
    )
    events = control.pump(0)

    assert [event.kind for event in events] == [EventKind.DEVICE_FOUND]
    assert events[0].peer is not None
    assert events[0].peer.name == "Phone"
    assert events[0].peer.wfd is True


def test_ipv4_and_prefix_helpers():
    assert _ipv4(bytes([192, 168, 49, 1])) == "192.168.49.1"
    assert _ipv4(None) is None
    assert _prefix(bytes([255, 255, 255, 0])) == 24
    assert _prefix(None) == 24
