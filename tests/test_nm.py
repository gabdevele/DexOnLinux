import io

import psutil
import pytest

from dexonlinux import nm, nm_watchdog
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


class FakeProcess:
    def __init__(self, pid=4321, name="NetworkManager", status=psutil.STATUS_RUNNING):
        self.pid = pid
        self._name = name
        self._status = status
        self.suspended = False
        self.resumed = False

    def name(self):
        return self._name

    def status(self):
        return self._status

    def suspend(self):
        self.suspended = True
        self._status = psutil.STATUS_STOPPED

    def resume(self):
        self.resumed = True
        self._status = psutil.STATUS_RUNNING


@pytest.fixture
def fake_nm(monkeypatch):
    proc = FakeProcess()
    watchdogs = []

    def start_watchdog(pid):
        watchdog = FakeWatchdog()
        watchdogs.append((pid, watchdog))
        return watchdog

    monkeypatch.setattr(nm, "_read_pid", lambda path: proc.pid)
    monkeypatch.setattr(nm, "_process", lambda pid: proc if pid == proc.pid else None)
    monkeypatch.setattr(nm, "_start_watchdog", start_watchdog)
    monkeypatch.setattr(nm.atexit, "register", lambda func: None)
    return proc, watchdogs


def test_suspend_calls_process_suspend_and_waits_for_stopped_status(fake_nm):
    proc, watchdogs = fake_nm
    suspension = NetworkManagerSuspension(enabled=True)

    suspension.suspend()

    assert proc.suspended is True
    assert proc.status() == psutil.STATUS_STOPPED
    assert len(watchdogs) == 1
    assert suspension.suspended is True

    suspension.resume()

    assert proc.resumed is True
    assert proc.status() == psutil.STATUS_RUNNING
    assert watchdogs[0][1].waited is True
    assert suspension.suspended is False


def test_suspension_is_a_noop_when_disabled(fake_nm):
    proc, watchdogs = fake_nm
    suspension = NetworkManagerSuspension(enabled=False)

    suspension.suspend()
    suspension.resume()

    assert proc.suspended is False
    assert proc.resumed is False
    assert watchdogs == []


def test_suspension_adopts_an_already_stopped_network_manager(fake_nm):
    proc, _watchdogs = fake_nm
    proc._status = psutil.STATUS_STOPPED
    suspension = NetworkManagerSuspension(enabled=True)

    suspension.suspend()
    assert proc.suspended is False

    suspension.resume()
    assert proc.resumed is True


def test_suspension_does_nothing_when_network_manager_is_absent(fake_nm, monkeypatch):
    _proc, watchdogs = fake_nm
    monkeypatch.setattr(nm, "_read_pid", lambda path: None)
    suspension = NetworkManagerSuspension(enabled=True)

    suspension.suspend()
    suspension.resume()

    assert watchdogs == []


def test_missing_process_raises_network_manager_error(fake_nm, monkeypatch):
    proc, watchdogs = fake_nm
    monkeypatch.setattr(nm, "_process", lambda pid: None)
    suspension = NetworkManagerSuspension(enabled=True)

    with pytest.raises(NetworkManagerError, match="does not exist"):
        suspension.suspend()

    assert proc.suspended is False
    assert watchdogs == []


def test_suspension_refuses_a_non_network_manager_pid(fake_nm):
    proc, watchdogs = fake_nm
    proc._name = "something-else"
    suspension = NetworkManagerSuspension(enabled=True)

    with pytest.raises(NetworkManagerError, match="not NetworkManager"):
        suspension.suspend()

    assert proc.suspended is False
    assert watchdogs == []


def test_suspension_registers_atexit_once_and_unregisters_on_resume(fake_nm, monkeypatch):
    _proc, _watchdogs = fake_nm
    registered = []
    monkeypatch.setattr(nm.atexit, "register", registered.append)
    monkeypatch.setattr(nm.atexit, "unregister", registered.remove)
    suspension = NetworkManagerSuspension(enabled=True)

    suspension.suspend()
    suspension.resume()
    suspension.suspend()
    suspension.resume()

    assert registered == []


def test_suspension_resume_without_suspend_is_a_noop(fake_nm):
    proc, watchdogs = fake_nm
    suspension = NetworkManagerSuspension(enabled=True)

    suspension.resume()
    suspension.resume()

    assert proc.resumed is False
    assert watchdogs == []


def test_find_pid_by_name_uses_process_iter(monkeypatch):
    class Entry:
        info = {"pid": 99, "name": "NetworkManager"}

    monkeypatch.setattr(nm.psutil, "process_iter", lambda attrs: iter([Entry()]))

    assert nm._find_pid_by_name("NetworkManager") == 99


def test_find_pid_by_name_ignores_disappeared_processes(monkeypatch):
    class Entry:
        @property
        def info(self):
            raise psutil.NoSuchProcess(99)

    monkeypatch.setattr(nm.psutil, "process_iter", lambda attrs: iter([Entry()]))

    assert nm._find_pid_by_name("NetworkManager") is None


def test_watchdog_resumes_with_psutil(monkeypatch):
    proc = FakeProcess(pid=777)
    monkeypatch.setattr(nm_watchdog.psutil, "Process", lambda pid: proc)
    monkeypatch.setattr(nm_watchdog.sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))

    assert nm_watchdog.main([str(proc.pid)]) == 0
    assert proc.resumed is True
