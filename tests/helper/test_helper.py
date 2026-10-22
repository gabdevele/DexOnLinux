import threading
from pathlib import Path

from dexonlinux.helper.service import serve
from dexonlinux.p2p.base import GroupInfo, P2PError, P2PRole
from dexonlinux.p2p.events import parse_event


class FakeCtrl:
    path = Path("/tmp/fake-ctrl")

    def __init__(self, lines=None):
        self.lines = list(lines or [])

    def pump(self, timeout):
        if self.lines:
            return [parse_event(self.lines.pop(0))]
        return []


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
    def __init__(self, error=None):
        self.prepared = False
        self.closed = False
        self.error = error

    def prepare(self):
        self.prepared = True

    def connect(self, stop):
        if self.error:
            raise self.error
        stop.set()
        return client_group()

    def close(self):
        self.closed = True


def test_serve_emits_ready_and_group_then_closes():
    backend = FakeBackend()
    events = []
    stop = threading.Event()
    rc = serve(backend, FakeCtrl(), stop, lambda kind, data: events.append((kind, data)), "direct", "wlp-test")
    assert rc == 0
    assert [kind for kind, _ in events] == ["ready", "group_started"]
    assert events[1][1]["ifname"] == "p2p-test"
    assert backend.prepared is True
    assert backend.closed is True


def test_serve_emits_group_finished_and_closes():
    backend = FakeBackend()
    # connect() sets stop, so run the removal path without that fake behavior.
    backend.connect = lambda stop: client_group()
    events = []
    rc = serve(
        backend,
        FakeCtrl(["P2P-GROUP-REMOVED p2p-test reason=test"]),
        threading.Event(),
        lambda kind, data: events.append((kind, data)),
        "direct",
        "wlp-test",
    )
    assert rc == 0
    assert [kind for kind, _ in events] == ["ready", "group_started", "group_finished"]
    assert backend.closed is True


def test_serve_reports_backend_error():
    backend = FakeBackend(error=P2PError("phone requested a PIN"))
    events = []
    rc = serve(
        backend,
        FakeCtrl(),
        threading.Event(),
        lambda kind, data: events.append((kind, data)),
        "direct",
        "wlp-test",
    )
    assert rc == 1
    assert events[-1][0] == "error"
    assert "PIN" in events[-1][1]["message"]
    assert backend.closed is True
