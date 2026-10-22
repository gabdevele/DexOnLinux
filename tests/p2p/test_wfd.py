import pytest

from dexonlinux.p2p.base import P2PError
from dexonlinux.p2p.supplicant import SupplicantError
from dexonlinux.p2p.wfd import WfdConfigurator, WfdState, build_device_info_ie
from dexonlinux.wfd.rtsp import RTSP_PORT


def test_build_device_info_ie_encodes_the_session_port():
    assert build_device_info_ie() == f"0000060011{RTSP_PORT:04x}00c8"
    assert build_device_info_ie(session_port=9000) == "0000060011232800c8"


class FakeCtrl:
    def __init__(self):
        self.commands = []
        self.replies = {
            "GET wifi_display": "0",
            "GET p2p_go_intent": "7",
            "GET config_methods": "0x188",
            "GET device_name": "old-hostname",
        }

    def command(self, command, timeout=2.0):
        self.commands.append(command)
        return self.replies.get(command, "OK")


class FailingCtrl(FakeCtrl):
    def __init__(self, fail_on):
        super().__init__()
        self.fail_on = fail_on

    def command(self, command, timeout=2.0):
        if command == self.fail_on:
            raise SupplicantError("boom")
        return super().command(command, timeout)


def test_apply_enables_wfd_before_setting_ies():
    ctrl = FakeCtrl()
    state = WfdConfigurator().apply(ctrl)
    assert state == WfdState("0", "7", "0x188", "old-hostname")
    assert ctrl.commands[:4] == [
        "GET wifi_display",
        "GET p2p_go_intent",
        "GET config_methods",
        "GET device_name",
    ]
    assert "SET wifi_display 1" in ctrl.commands
    assert "WFD_SUBELEM_SET all 00000600111c4400c8" in ctrl.commands
    assert ctrl.commands.index("SET wifi_display 1") < ctrl.commands.index(
        "WFD_SUBELEM_SET all 00000600111c4400c8"
    )
    assert "SET p2p_go_intent 0" in ctrl.commands
    assert "SET config_methods 0x80" in ctrl.commands


def test_apply_restores_the_supplicant_when_a_required_set_fails():
    ctrl = FailingCtrl("SET config_methods 0x80")
    with pytest.raises(P2PError, match="config_methods"):
        WfdConfigurator().apply(ctrl)

    assert ctrl.commands[-6:] == [
        "P2P_EXT_LISTEN",
        "WFD_SUBELEM_SET 0 ",
        "SET wifi_display 0",
        "SET p2p_go_intent 7",
        "SET config_methods 0x188",
        "SET device_name old-hostname",
    ]


def test_restore_clears_and_restores():
    ctrl = FakeCtrl()
    WfdConfigurator().restore(ctrl, WfdState("0", "7", "0x188", "old-hostname"))
    assert ctrl.commands == [
        "P2P_EXT_LISTEN",
        "WFD_SUBELEM_SET 0 ",
        "SET wifi_display 0",
        "SET p2p_go_intent 7",
        "SET config_methods 0x188",
        "SET device_name old-hostname",
    ]
