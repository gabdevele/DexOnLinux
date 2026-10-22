import pytest

from dexonlinux.p2p.base import P2PError, Peer
from dexonlinux.p2p.control import SupplicantError
from dexonlinux.p2p.dbus import (
    P2P_IFACE,
    ROOT_IFACE,
    ROOT_PATH,
    WPS_IFACE,
    DBusWfdState,
    WpaSupplicantDBus,
)
from dexonlinux.p2p.wfd import WFD_IES

from .fake_dbus import DEVICE, GROUP_DEVICE, FakeDBusClient


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
