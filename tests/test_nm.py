import io
import signal

import pytest

from dexonlinux import nm
from dexonlinux.nm import NetworkManagerError, NetworkManagerSuspension


class FakeWatchdog:
    def __init__(self):
        self.stdin = io.BytesIO()
        self.killed = False
        self.waited = False

    def wait(self, timeout=None):
        self.waited = True
        return 0

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_nm(monkeypatch):
    state = {"value": "S", "pid": 4321}
    kills = []

    def process_state(pid):
        return state["value"] if pid == state["pid"] else None

    def process_name(pid):
        return "NetworkManager" if pid == state["pid"] else None

    def kill(pid, sig):
        kills.append((pid, sig))
        if sig == signal.SIGSTOP:
            state["value"] = "T"
        elif sig == signal.SIGCONT:
            state["value"] = "S"

    watchdogs = []

    def start_watchdog(pid):
        watchdog = FakeWatchdog()
        watchdogs.append((pid, watchdog))
        return watchdog

    monkeypatch.setattr(nm, "_read_pid", lambda path: state["pid"])
    monkeypatch.setattr(nm, "_process_state", process_state)
    monkeypatch.setattr(nm, "_process_name", process_name)
    monkeypatch.setattr(nm.os, "kill", kill)
    monkeypatch.setattr(nm, "_start_watchdog", start_watchdog)
    monkeypatch.setattr(nm.atexit, "register", lambda func: None)
    return state, kills, watchdogs


def test_suspension_stops_and_resumes_network_manager(fake_nm):
    state, kills, watchdogs = fake_nm
    suspension = NetworkManagerSuspension(enabled=True)

    suspension.suspend()
    assert state["value"] == "T"
    assert kills == [(4321, signal.SIGSTOP)]
    assert len(watchdogs) == 1
    assert suspension.suspended is True

    suspension.resume()
    assert state["value"] == "S"
    assert kills[-1] == (4321, signal.SIGCONT)
    assert watchdogs[0][1].waited is True
    assert suspension.suspended is False


def test_suspension_is_a_noop_when_disabled(fake_nm):
    state, kills, watchdogs = fake_nm
    suspension = NetworkManagerSuspension(enabled=False)

    suspension.suspend()
    suspension.resume()

    assert state["value"] == "S"
    assert kills == []
    assert watchdogs == []


def test_suspension_adopts_an_already_stopped_network_manager(fake_nm):
    state, kills, _watchdogs = fake_nm
    state["value"] = "T"
    suspension = NetworkManagerSuspension(enabled=True)

    suspension.suspend()
    assert kills == []

    suspension.resume()
    assert state["value"] == "S"
    assert kills == [(4321, signal.SIGCONT)]


def test_suspension_does_nothing_when_network_manager_is_absent(fake_nm, monkeypatch):
    _state, kills, watchdogs = fake_nm
    monkeypatch.setattr(nm, "_read_pid", lambda path: None)
    suspension = NetworkManagerSuspension(enabled=True)

    suspension.suspend()
    suspension.resume()

    assert kills == []
    assert watchdogs == []


def test_suspension_refuses_a_non_network_manager_pid(fake_nm, monkeypatch):
    _state, kills, watchdogs = fake_nm
    monkeypatch.setattr(nm, "_process_name", lambda pid: "something-else")
    suspension = NetworkManagerSuspension(enabled=True)

    with pytest.raises(NetworkManagerError, match="not NetworkManager"):
        suspension.suspend()

    assert kills == []
    assert watchdogs == []
