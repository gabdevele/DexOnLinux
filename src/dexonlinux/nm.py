"""Session-scoped NetworkManager suspension.

NetworkManager owns the P2P device and removes P2P groups it did not create,
so an externally created group cannot outlive it. Stopping or restarting
NetworkManager is not acceptable, so it is frozen with SIGSTOP for the P2P
session and resumed on every exit path. SIGSTOP keeps the established station
connection and wpa_supplicant running, so Wi-Fi internet stays usable.

A separate watchdog process resumes NetworkManager if the helper is killed
before it can clean up.
"""

import atexit
import logging
import subprocess
import sys
import time
from pathlib import Path
from types import TracebackType

import psutil

LOG = logging.getLogger(__name__)

DEFAULT_PIDFILE = Path("/run/NetworkManager/NetworkManager.pid")
_DEFAULT_TIMEOUT = 2.0
_DEFAULT_INTERVAL = 0.05


class NetworkManagerError(RuntimeError):
    """NetworkManager could not be suspended or resumed safely."""


class NetworkManagerSuspension:
    """Freeze NetworkManager while a P2P group is owned by the helper.

    ``suspend()`` is intentionally explicit: the helper calls it only right
    before sending ``P2P_CONNECT``, so merely waiting for a phone does not
    freeze the user's network manager.
    """

    def __init__(
        self,
        enabled: bool = False,
        *,
        pidfile: Path = DEFAULT_PIDFILE,
        timeout: float = _DEFAULT_TIMEOUT,
        interval: float = _DEFAULT_INTERVAL,
    ) -> None:
        self.enabled = enabled
        self._pidfile = Path(pidfile)
        self._timeout = timeout
        self._interval = interval
        self._pid: int | None = None
        self._watchdog: subprocess.Popen[bytes] | None = None
        self._atexit_registered = False

    @property
    def suspended(self) -> bool:
        return self._pid is not None

    def suspend(self) -> None:
        if not self.enabled or self._pid is not None:
            return

        pid = _read_pid(self._pidfile)
        if pid is None:
            LOG.info("NetworkManager is not running; no suspension needed")
            return

        proc = _process(pid)
        if proc is None:
            raise NetworkManagerError(f"NetworkManager pid {pid} does not exist")
        if _process_name(proc) != "NetworkManager":
            raise NetworkManagerError(
                f"refusing to suspend pid {pid}: it is not NetworkManager"
            )
        try:
            created = proc.create_time()
        except psutil.Error as exc:
            raise NetworkManagerError(f"cannot inspect pid {pid}: {exc}") from exc

        # Start the watchdog before suspending. If this process dies between
        # the two operations, closing the watchdog pipe still sends SIGCONT.
        self._watchdog = _start_watchdog(pid, created)
        try:
            if not _status_is_stopped(proc):
                proc.suspend()
                if not _wait_for_state(proc, stopped=True, timeout=self._timeout, interval=self._interval):
                    raise NetworkManagerError("NetworkManager did not enter the stopped state")
            else:
                LOG.warning("NetworkManager was already stopped; taking ownership of its resume")
        except psutil.Error as exc:
            self._stop_watchdog()
            raise NetworkManagerError(f"could not suspend NetworkManager: {exc}") from exc
        except NetworkManagerError:
            self._stop_watchdog()
            raise

        self._pid = pid
        self._register_atexit()
        LOG.info("suspended NetworkManager (pid %d) for the P2P session", pid)

    def resume(self) -> None:
        pid = self._pid
        if pid is None:
            return
        self._pid = None

        try:
            proc = _process(pid)
            if proc is None:
                return
            proc.resume()
            if not _wait_for_state(proc, stopped=False, timeout=self._timeout, interval=self._interval):
                LOG.warning("NetworkManager (pid %d) did not leave the stopped state", pid)
            LOG.info("resumed NetworkManager (pid %d)", pid)
        except psutil.Error as exc:
            LOG.warning("could not resume NetworkManager: %s", exc)
        finally:
            self._unregister_atexit()
            self._stop_watchdog()

    def __enter__(self) -> "NetworkManagerSuspension":
        self.suspend()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.resume()
        return False

    def _register_atexit(self) -> None:
        # Register once per process: repeated suspend/resume cycles must not
        # stack exit handlers.
        if self._atexit_registered:
            return
        atexit.register(self.resume)
        self._atexit_registered = True

    def _unregister_atexit(self) -> None:
        if not self._atexit_registered:
            return
        atexit.unregister(self.resume)
        self._atexit_registered = False

    def _stop_watchdog(self) -> None:
        watchdog = self._watchdog
        self._watchdog = None
        if watchdog is None:
            return
        try:
            if watchdog.stdin is not None:
                watchdog.stdin.close()
            watchdog.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            watchdog.kill()
            watchdog.wait(timeout=1.0)


def _read_pid(pidfile: Path) -> int | None:
    try:
        raw = pidfile.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return _find_pid_by_name("NetworkManager")
    except OSError as exc:
        raise NetworkManagerError(f"cannot read {pidfile}: {exc}") from exc
    try:
        return int(raw)
    except ValueError as exc:
        raise NetworkManagerError(f"invalid NetworkManager pid in {pidfile}: {raw!r}") from exc


def _find_pid_by_name(name: str) -> int | None:
    """Fallback for distributions that do not write the NM pidfile."""
    try:
        processes = psutil.process_iter(["pid", "name"])
    except OSError:
        return None
    for proc in processes:
        try:
            if proc.info["name"] == name:
                return int(proc.info["pid"])
        except (psutil.Error, KeyError, TypeError, ValueError):
            continue
    return None


def _process(pid: int) -> psutil.Process | None:
    """Return a live process, or None when the pid no longer exists.

    ``psutil`` verifies the pid while sending signals, which protects against
    pid reuse between the pidfile read and the suspend/resume call.
    """
    try:
        proc = psutil.Process(pid)
        proc.name()
        return proc
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied as exc:
        raise NetworkManagerError(f"cannot inspect pid {pid}: {exc}") from exc


def _process_name(proc: psutil.Process) -> str | None:
    try:
        return proc.name()
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied as exc:
        raise NetworkManagerError(f"cannot inspect pid {proc.pid}: {exc}") from exc


def _status_is_stopped(proc: psutil.Process) -> bool:
    try:
        return proc.status() in {psutil.STATUS_STOPPED, psutil.STATUS_TRACING_STOP}
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied as exc:
        raise NetworkManagerError(f"cannot inspect pid {proc.pid}: {exc}") from exc


def _wait_for_state(proc: psutil.Process, *, stopped: bool, timeout: float, interval: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _status_is_stopped(proc) == stopped:
            return True
        time.sleep(interval)
    return False


def _start_watchdog(pid: int, created: float) -> subprocess.Popen[bytes]:
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "dexonlinux.nm_watchdog", str(pid), repr(created)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise NetworkManagerError(f"could not start NetworkManager watchdog: {exc}") from exc
