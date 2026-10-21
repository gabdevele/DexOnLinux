import threading

from dexonlinux.helper.client import HelperEvent
from dexonlinux.p2p.base import GroupInfo, P2PError
from dexonlinux.session import DexSession


def group_info(**overrides):
    values = {
        "ifname": "p2p-test",
        "role": "client",
        "local_ip": "192.168.49.200",
        "local_prefix": 24,
        "peer_ip": "192.168.49.1",
        "frequency": 2437,
    }
    values.update(overrides)
    return GroupInfo(**values)


class FakeHelper:
    def __init__(self, group=None, error=None):
        self.group = group
        self.error = error
        self.stopped = False

    def wait_for(self, kind, timeout):
        if self.error:
            raise self.error
        return HelperEvent(kind, self.group.__dict__)

    def stop(self):
        self.stopped = True


class FakeSink:
    def __init__(self, events=("streaming", "disconnected"), error=None, block=False):
        self.events = list(events)
        self.error = error
        self.block = block
        self.stopped = False
        self.on_event = None
        self._release = threading.Event()

    def run(self):
        if self.error:
            raise self.error
        for name in self.events:
            self.on_event(name, {})
        if self.block:
            self._release.wait(timeout=1.0)

    def stop(self):
        self.stopped = True
        self._release.set()


class FakeScrcpy:
    def __init__(self):
        self.started = 0
        self.stopped = False
        self._release = threading.Event()

    def start(self):
        self.started += 1

    def wait(self):
        self._release.wait(timeout=1.0)

    def stop(self):
        self.stopped = True
        self._release.set()


def test_session_starts_scrcpy_on_streaming_and_stops_on_exit():
    helper = FakeHelper(group_info())
    sink = FakeSink()
    scrcpy = FakeScrcpy()
    session = DexSession(helper, scrcpy, lambda host: sink, start_delay=0)
    assert session.run() == 0
    assert scrcpy.started == 1
    assert helper.stopped is True
    assert sink.stopped is True


def test_session_returns_error_when_helper_fails():
    helper = FakeHelper(error=P2PError("no phone"))
    scrcpy = FakeScrcpy()
    session = DexSession(helper, scrcpy, lambda host: FakeSink())
    assert session.run() == 1
    assert helper.stopped is True


def test_session_returns_error_and_cleans_up_when_sink_fails():
    helper = FakeHelper(group_info())
    sink = FakeSink(error=OSError("RTSP refused"))
    scrcpy = FakeScrcpy()
    session = DexSession(helper, scrcpy, lambda host: sink, start_delay=0)
    assert session.run() == 1
    assert helper.stopped is True
    assert scrcpy.stopped is True


def test_scrcpy_exit_stops_the_sink():
    helper = FakeHelper(group_info())
    sink = FakeSink(events=("streaming",), block=True)
    scrcpy = FakeScrcpy()
    session = DexSession(helper, scrcpy, lambda host: sink, start_delay=0)
    assert session.run() == 0
    assert sink.stopped is True
    assert helper.stopped is True


def test_go_role_without_peer_ip_is_rejected():
    helper = FakeHelper(group_info(role="go", peer_ip=None))
    session = DexSession(helper, FakeScrcpy(), lambda host: FakeSink())
    assert session.run() == 1
    assert helper.stopped is True


def test_go_role_uses_peer_ip_for_rtsp():
    helper = FakeHelper(group_info(role="go", local_ip="192.168.49.1", peer_ip="192.168.49.2"))
    hosts = []
    session = DexSession(helper, FakeScrcpy(), lambda host: hosts.append(host) or FakeSink(), start_delay=0)
    assert session.run() == 0
    assert hosts == ["192.168.49.2"]


def test_client_role_without_peer_ip_is_rejected():
    helper = FakeHelper(group_info(peer_ip=None))
    session = DexSession(helper, FakeScrcpy(), lambda host: FakeSink())
    assert session.run() == 1
    assert helper.stopped is True
