import threading

import pytest

from dexonlinux.helper import service
from dexonlinux.helper.service import serve
from dexonlinux.p2p.base import GroupInfo, P2PError, P2PRole
from dexonlinux.p2p.control import SupplicantError
from dexonlinux.p2p.direct import DirectBackend


def client_group() -> GroupInfo:
    return GroupInfo(
        ifname="p2p-test",
        role=P2PRole.CLIENT,
        local_ip="192.168.49.200",
        local_prefix=24,
        peer_ip="192.168.49.1",
        frequency=2437,
    )


class FakeBackend:
    def __init__(self, error=None, end_reason=None):
        self.prepared = False
        self.closed = False
        self.error = error
        self.end_reason = end_reason

    def prepare(self):
        self.prepared = True

    def connect(self, stop):
        if self.error:
            raise self.error
        stop.set()
        return client_group()

    def wait_for_group_end(self, stop):
        return self.end_reason

    def close(self):
        self.closed = True


def test_serve_emits_ready_and_group_then_closes():
    backend = FakeBackend()
    events = []
    stop = threading.Event()
    rc = serve(backend, stop, lambda kind, data: events.append((kind, data)), "direct", "wlp-test", "dbus")

    assert rc == 0
    assert [kind for kind, _ in events] == ["ready", "group_started"]
    assert events[0][1] == {"backend": "direct", "interface": "wlp-test", "transport": "dbus"}
    assert events[1][1]["ifname"] == "p2p-test"
    assert backend.prepared is True
    assert backend.closed is True


def test_serve_emits_group_finished_from_wait_for_group_end():
    backend = FakeBackend(end_reason="P2P-GROUP-REMOVED p2p-test reason=test")
    backend.connect = lambda stop: client_group()
    events = []
    rc = serve(
        backend,
        threading.Event(),
        lambda kind, data: events.append((kind, data)),
        "direct",
        "wlp-test",
        "ctrl",
    )

    assert rc == 0
    assert [kind for kind, _ in events] == ["ready", "group_started", "group_finished"]
    assert events[-1][1]["reason"] == "P2P-GROUP-REMOVED p2p-test reason=test"
    assert backend.closed is True


def test_serve_reports_backend_error():
    backend = FakeBackend(error=P2PError("phone requested a PIN"))
    events = []
    rc = serve(
        backend,
        threading.Event(),
        lambda kind, data: events.append((kind, data)),
        "direct",
        "wlp-test",
        "dbus",
    )

    assert rc == 1
    assert events[-1][0] == "error"
    assert "PIN" in events[-1][1]["message"]
    assert backend.closed is True


def test_open_control_prefers_dbus(monkeypatch):
    class FakeDBus:
        def __init__(self, interface):
            self.interface = interface
            self.opened = False

        def open(self):
            self.opened = True

    monkeypatch.setattr(service, "WpaSupplicantDBus", FakeDBus)
    monkeypatch.setattr(service, "SupplicantControl", lambda path: pytest.fail("ctrl must not be used"))

    control, name = service.open_control("wlan0", "auto")

    assert name == "dbus"
    assert isinstance(control, FakeDBus)
    assert control.opened is True


def test_open_control_falls_back_to_ctrl(monkeypatch):
    class FailingDBus:
        def __init__(self, interface):
            pass

        def open(self):
            raise SupplicantError("no WFDIEs")

    class FakeCtrl:
        def __init__(self, path):
            self.path = path

        def open(self):
            pass

    monkeypatch.setattr(service, "WpaSupplicantDBus", FailingDBus)
    monkeypatch.setattr(service, "find_ctrl_socket", lambda interface: "/tmp/p2p-dev")
    monkeypatch.setattr(service, "SupplicantControl", FakeCtrl)

    control, name = service.open_control("wlan0", "auto")

    assert name == "ctrl"
    assert isinstance(control, FakeCtrl)


def test_open_control_reports_both_errors(monkeypatch):
    class FailingDBus:
        def __init__(self, interface):
            pass

        def open(self):
            raise SupplicantError("no WFDIEs")

    class FailingCtrl:
        def __init__(self, path):
            pass

        def open(self):
            raise SupplicantError("no socket")

    monkeypatch.setattr(service, "WpaSupplicantDBus", FailingDBus)
    monkeypatch.setattr(service, "find_ctrl_socket", lambda interface: "/tmp/p2p-dev")
    monkeypatch.setattr(service, "SupplicantControl", FailingCtrl)

    with pytest.raises(SupplicantError, match="D-Bus: no WFDIEs; control socket: no socket"):
        service.open_control("wlan0", "auto")


def test_explicit_dbus_does_not_fall_back(monkeypatch):
    class FailingDBus:
        def __init__(self, interface):
            pass

        def open(self):
            raise SupplicantError("no WFDIEs")

    monkeypatch.setattr(service, "WpaSupplicantDBus", FailingDBus)

    with pytest.raises(SupplicantError, match="no WFDIEs"):
        service.open_control("wlan0", "dbus")


def test_main_builds_the_backend_from_the_selected_transport(monkeypatch):
    from dexonlinux.helper import service

    control = object()
    captured = {}

    def open_control(interface, transport):
        captured["transport"] = transport
        return control, "dbus"

    def serve(backend, stop, emitter, backend_name, interface, transport):
        captured["backend"] = backend
        captured["interface"] = interface
        return 0

    monkeypatch.setattr(service, "open_control", open_control)
    monkeypatch.setattr(service, "serve", serve)

    assert service.main(["--interface", "wlan0", "--p2p-transport", "dbus"]) == 0
    assert captured["transport"] == "dbus"
    assert captured["interface"] == "wlan0"
    assert isinstance(captured["backend"], DirectBackend)
    assert captured["backend"]._control is control


def test_build_backend_uses_the_supplied_control_and_role(monkeypatch):
    from dexonlinux.helper import service
    from dexonlinux.nm import NetworkManagerSuspension

    control = object()
    suspension = NetworkManagerSuspension(enabled=False)

    backend = service.build_backend(
        "wlan0",
        control,
        role=P2PRole.GO,
        frequency=2437,
        suspension=suspension,
    )

    assert backend._control is control
    assert backend._frequency == 2437
    assert backend._dhcp is not None
    assert backend._before_group == suspension.suspend


def test_build_backend_has_no_dhcp_for_the_client_role():
    from dexonlinux.helper import service
    from dexonlinux.nm import NetworkManagerSuspension

    backend = service.build_backend(
        "wlan0",
        object(),
        role=P2PRole.CLIENT,
        frequency=2437,
        suspension=NetworkManagerSuspension(enabled=False),
    )

    assert backend._dhcp is None


def test_main_reports_an_unexpected_failure_as_an_event(monkeypatch):
    from dexonlinux.helper import service

    monkeypatch.setattr(service, "open_control", lambda interface, transport: (object(), "dbus"))

    def broken_serve(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "serve", broken_serve)
    events = []
    monkeypatch.setattr(service, "emit", lambda kind, data: events.append((kind, data)))

    assert service.main(["--interface", "wlan0"]) == 1
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "RuntimeError"
    assert "boom" in events[-1][1]["message"]
