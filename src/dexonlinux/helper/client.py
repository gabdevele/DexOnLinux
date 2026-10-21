"""User-side client for the privileged P2P helper."""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass

from dexonlinux.p2p.base import P2PError

LOG = logging.getLogger(__name__)
_MAX_EVENT = 64 * 1024


@dataclass(frozen=True)
class HelperEvent:
    kind: str
    data: dict[str, object]


class HelperClient:
    def __init__(
        self,
        process: subprocess.Popen[str],
        events: queue.Queue[HelperEvent],
        stdout_thread: threading.Thread,
        stderr_thread: threading.Thread,
    ) -> None:
        self.process = process
        self._events = events
        self._stdout_thread = stdout_thread
        self._stderr_thread = stderr_thread

    @classmethod
    def start(
        cls,
        interface: str,
        backend: str = "direct",
        command: Sequence[str] | None = None,
    ) -> HelperClient:
        if command is None:
            executable = shutil.which("dexonlinux-linkd")
            if executable:
                command = ["sudo", "-n", executable]
            else:
                command = ["sudo", "-n", sys.executable, "-m", "dexonlinux.helper"]
            command = [*command, "--interface", interface, "--backend", backend]

        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        events: queue.Queue[HelperEvent] = queue.Queue()
        stdout_thread = threading.Thread(
            target=_read_stdout,
            args=(process.stdout, events),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_read_stderr,
            args=(process.stderr,),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        return cls(process, events, stdout_thread, stderr_thread)

    def wait_for(self, kind: str, timeout: float) -> HelperEvent:
        deadline = _now() + timeout
        while True:
            remaining = deadline - _now()
            if remaining <= 0:
                raise P2PError(f"helper did not emit {kind!r} within {timeout:.1f}s")
            try:
                event = self._events.get(timeout=remaining)
            except queue.Empty as exc:
                raise P2PError(f"helper did not emit {kind!r} within {timeout:.1f}s") from exc
            if event.kind == kind:
                return event
            if event.kind == "error":
                raise P2PError(str(event.data.get("message", "helper error")))

    def stop(self) -> None:
        if self.process.poll() is None:
            _signal_group(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                _signal_group(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=3.0)

        self._stdout_thread.join(timeout=1.0)
        self._stderr_thread.join(timeout=1.0)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()

def _read_stdout(stream, events: queue.Queue[HelperEvent]) -> None:
    if stream is None:
        return
    try:
        while True:
            line = stream.readline(_MAX_EVENT)
            if not line:
                return
            if len(line) >= _MAX_EVENT and not line.endswith("\n"):
                events.put(HelperEvent("error", {"message": "helper event exceeded 64 KiB"}))
                return
            try:
                payload = json.loads(line)
                kind = payload["event"]
                data = payload.get("data", {})
                if not isinstance(kind, str) or not isinstance(data, dict):
                    raise TypeError("invalid event shape")
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                LOG.warning("invalid helper event: %s", exc)
                continue
            events.put(HelperEvent(kind, data))
    finally:
        stream.close()


def _read_stderr(stream) -> None:
    if stream is None:
        return
    try:
        for line in stream:
            text = line.rstrip("\n")
            if text:
                LOG.debug("helper: %s", text)
    finally:
        stream.close()


def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(os.getpgid(pid), sig)
    except ProcessLookupError:
        pass


def _now() -> float:
    return time.monotonic()
