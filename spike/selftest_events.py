"""Offline tests for linkd's P2P event -> supplicant command mapping."""

from __future__ import annotations

import sys

import linkd as linkd_module

MAC = "aa:bb:cc:dd:ee:ff"


class DummyCtrl:
    def __init__(self, wifi_display: str = "0") -> None:
        self.path = "/tmp/fake-ctrl"
        self.wifi_display = wifi_display
        self.commands: list[str] = []
        self.closed = False

    def command(self, cmd: str) -> str:
        self.commands.append(cmd)
        if cmd == "GET wifi_display":
            return self.wifi_display
        return "OK"

    def close(self) -> None:
        self.closed = True


class CapturingEmitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event: str, **data: object) -> None:
        self.events.append((event, data))


def make_linkd(wifi_display: str = "0") -> tuple[linkd_module.Linkd, DummyCtrl, CapturingEmitter]:
    ctrl = DummyCtrl(wifi_display)
    linkd_module.SupplicantControl = lambda interface: ctrl
    emitter = CapturingEmitter()
    return linkd_module.Linkd("wlp-test", 0x0011, emitter), ctrl, emitter


def test_apply_settings_enables_wfd_first() -> None:
    linkd, ctrl, _ = make_linkd()
    linkd._apply_settings()
    commands = ctrl.commands
    assert "SET wifi_display 1" in commands, commands
    assert "WFD_SUBELEM_SET all 00000600111c4400c8" in commands, commands
    assert commands.index("SET wifi_display 1") < commands.index(
        "WFD_SUBELEM_SET all 00000600111c4400c8"
    ), commands
    assert "P2P_EXT_LISTEN 1000 1000" in commands, commands
    print("PASS: WFD is enabled before advertising the sink IEs")


def test_show_pin_starts_display_connect() -> None:
    linkd, ctrl, emitter = make_linkd()
    linkd._handle_event(
        f"P2P-PROV-DISC-SHOW-PIN {MAC} 12345678 p2p_dev_addr={MAC} name='phone'"
    )
    assert linkd._last_pin == "12345678"
    assert f"P2P_CONNECT {MAC} 12345678 display go_intent=0" in ctrl.commands, ctrl.commands
    assert ("pin_required", {"peer": MAC, "pin": "12345678"}) in emitter.events
    print("PASS: display PIN starts the PIN-method GO negotiation")


def test_go_neg_without_pin_uses_pbc() -> None:
    linkd, ctrl, _ = make_linkd()
    linkd._handle_event(f"P2P-GO-NEG-REQUEST {MAC} dev_passwd_id=4 go_intent=15")
    assert f"P2P_CONNECT {MAC} pbc go_intent=0" in ctrl.commands, ctrl.commands
    print("PASS: GO negotiation without a PIN falls back to PBC")


def test_go_neg_with_pin_reuses_display_pin() -> None:
    linkd, ctrl, _ = make_linkd()
    linkd._handle_event(f"P2P-PROV-DISC-SHOW-PIN {MAC} 87654321 p2p_dev_addr={MAC}")
    linkd._pending_connect_until = 0.0
    linkd._handle_event(f"P2P-GO-NEG-REQUEST {MAC} dev_passwd_id=5 go_intent=15")
    assert f"P2P_CONNECT {MAC} 87654321 display go_intent=0" in ctrl.commands, ctrl.commands
    print("PASS: GO negotiation reuses the display PIN")


def test_pbc_request_starts_pbc_connect() -> None:
    linkd, ctrl, _ = make_linkd()
    linkd._handle_event(f"P2P-PROV-DISC-PBC-REQ {MAC} p2p_dev_addr={MAC}")
    assert linkd._last_pin is None
    assert f"P2P_CONNECT {MAC} pbc go_intent=0" in ctrl.commands, ctrl.commands
    print("PASS: PBC provision discovery starts a PBC connect")


def test_cleanup_restores_wfd_and_go_intent() -> None:
    linkd, ctrl, _ = make_linkd()
    linkd._cleanup()
    assert "WFD_SUBELEM_SET 0 " in ctrl.commands, ctrl.commands
    assert "SET wifi_display 0" in ctrl.commands, ctrl.commands
    assert "SET p2p_go_intent 7" in ctrl.commands, ctrl.commands
    assert ctrl.closed
    print("PASS: cleanup clears and disables WFD, restores go_intent")

    linkd2, ctrl2, _ = make_linkd(wifi_display="1")
    linkd2._apply_settings()
    linkd2._cleanup()
    assert "SET wifi_display 0" not in ctrl2.commands, ctrl2.commands
    print("PASS: cleanup leaves a pre-existing WFD enabled")


def main() -> int:
    tests = [
        test_apply_settings_enables_wfd_first,
        test_show_pin_starts_display_connect,
        test_go_neg_without_pin_uses_pbc,
        test_go_neg_with_pin_reuses_display_pin,
        test_pbc_request_starts_pbc_connect,
        test_cleanup_restores_wfd_and_go_intent,
    ]
    for test in tests:
        test()
    print("PASS: all linkd event tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
