"""Session-scoped NetworkManager suspension.

On the tested Intel AX201 + NetworkManager 1.46 setup, NetworkManager removes
an externally created P2P group immediately after ``P2P-GROUP-STARTED``. The
direct backend works reliably when NetworkManager is stopped. Stopping or
restarting NetworkManager is not acceptable, so we freeze it with SIGSTOP for
the duration of the P2P group and resume it on every exit path.

A separate watchdog process resumes NetworkManager if the helper is killed
before it can clean up. SIGSTOP keeps the established station connection and
wpa_supplicant running, so Wi-Fi internet remains usable.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

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

        state = _process_state(pid)
        if state is None:
            raise NetworkManagerError(f"NetworkManager pid {pid} does not exist")
        if _process_name(pid) != "NetworkManager":
            raise NetworkManagerError(
                f"refusing to suspend pid {pid}: it is not NetworkManager"
            )

        # Start the watchdog before SIGSTOP. If this process dies between the
        # two operations, closing the watchdog pipe still sends SIGCONT.
        self._watchdog = _start_watchdog(pid)
        try:
            if state not in {"T", "t"}:
                os.kill(pid, signal.SIGSTOP)
                if not _wait_for_state(pid, stopped=True, timeout=self._timeout, interval=self._interval):
                    raise NetworkManagerError("NetworkManager did not enter the stopped state")
            else:
                LOG.warning("NetworkManager was already stopped; taking ownership of its resume")
        except (OSError, NetworkManagerError) as exc:
            self._stop_watchdog()
            if isinstance(exc, NetworkManagerError):
                raise
            raise NetworkManagerError(f"could not suspend NetworkManager: {exc}") from exc

        self._pid = pid
        atexit.register(self.resume)
        LOG.info("suspended NetworkManager (pid %d) for the P2P session", pid)

    def resume(self) -> None:
        pid = self._pid
        if pid is None:
            return
        self._pid = None

        try:
            os.kill(pid, signal.SIGCONT)
            _wait_for_state(pid, stopped=False, timeout=self._timeout, interval=self._interval)
        except ProcessLookupError:
            pass
        except OSError as exc:
            LOG.warning("could not resume NetworkManager: %s", exc)
        finally:
            self._stop_watchdog()
            LOG.info("resumed NetworkManager (pid %d)", pid)

    def __enter__(self):
        self.suspend()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.resume()
        return False

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
        entries = Path("/proc").iterdir()
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text(encoding="ascii").strip()
        except OSError:
            continue
        if comm == name:
            return int(entry.name)
    return None


def _process_state(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise NetworkManagerError(f"cannot inspect pid {pid}: {exc}") from exc
    # The comm field can contain spaces and parentheses. The state follows the
    # last ')'.
    try:
        return stat.rsplit(")", 1)[1].split()[0]
    except (IndexError, AttributeError):
        return None


def _process_name(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise NetworkManagerError(f"cannot inspect pid {pid}: {exc}") from exc


def _wait_for_state(pid: int, *, stopped: bool, timeout: float, interval: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _process_state(pid)
        if state is None:
            return False
        is_stopped = state in {"T", "t"}
        if is_stopped == stopped:
            return True
        time.sleep(interval)
    return False


def _start_watchdog(pid: int) -> subprocess.Popen[bytes]:
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "dexonlinux.nm_watchdog", str(pid)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise NetworkManagerError(f"could not start NetworkManager watchdog: {exc}") from exc
