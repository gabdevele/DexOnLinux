"""Minimal wpa_supplicant control-interface client.

The system supplicant is owned by NetworkManager; this client only talks to
the dedicated P2P-device socket. It never stops or reconfigures the supplicant
service.
"""

import logging
import os
import queue
import re
import socket
import time
from pathlib import Path

LOG = logging.getLogger(__name__)

CTRL_DIR = Path("/run/wpa_supplicant")
IFNAMSIZ = 16
DEFAULT_TIMEOUT = 2.0
MAX_REPLY_BYTES = 4096
_PRIORITY = re.compile(r"^<\d+>")


class SupplicantError(RuntimeError):
    """The control socket is missing, closed, or did not answer."""


def p2p_ctrl_name(interface: str) -> str:
    if interface.startswith("p2p-dev-"):
        return interface[: IFNAMSIZ - 1]
    return ("p2p-dev-" + interface)[: IFNAMSIZ - 1]


def find_ctrl_socket(interface: str, ctrl_dir: Path = CTRL_DIR) -> Path:
    """Resolve the P2P-device ctrl socket for an interface.

    The kernel truncates interface names to IFNAMSIZ, while some supplicant
    builds expose the untruncated name as the ctrl socket. Try both, then fall
    back to the sole P2P socket when there is only one.
    """
    ctrl_dir = Path(ctrl_dir)
    candidates = []
    if interface.startswith("p2p-dev-"):
        candidates.append(interface)
    else:
        candidates.extend(
            [
                f"p2p-dev-{interface}",
                p2p_ctrl_name(interface),
            ]
        )
    for name in dict.fromkeys(candidates):
        candidate = ctrl_dir / name
        if candidate.exists():
            return candidate

    try:
        sockets = sorted(path for path in ctrl_dir.iterdir() if path.name.startswith("p2p-dev-"))
    except OSError as exc:
        raise SupplicantError(f"cannot read {ctrl_dir}: {exc}") from exc

    if len(sockets) == 1:
        return sockets[0]
    if not sockets:
        raise SupplicantError(
            f"no P2P control socket in {ctrl_dir}; is Wi-Fi Direct enabled?"
        )
    names = ", ".join(path.name for path in sockets)
    raise SupplicantError(f"multiple P2P control sockets ({names}); specify the interface")


class SupplicantControl:
    """ATTACH-ed control connection with a small event queue."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._sock: socket.socket | None = None
        self._events: queue.Queue[str] = queue.Queue()

    def open(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(f"\0dexonlinux-{os.getpid()}-{id(sock)}")
        sock.connect(str(self.path))
        sock.settimeout(DEFAULT_TIMEOUT)
        self._sock = sock
        reply = self.command("ATTACH")
        if reply.strip() != "OK":
            self.close()
            raise SupplicantError(f"ATTACH failed: {reply.strip()!r}")
        LOG.debug("attached to %s", self.path)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def command(self, command: str, timeout: float = DEFAULT_TIMEOUT) -> str:
        if self._sock is None:
            raise SupplicantError("control connection is not open")
        self._sock.send(command.encode("utf-8"))
        LOG.debug("CTRL >> %s", command)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self._recv(max(0.01, deadline - time.monotonic()))
            if data is None:
                continue
            text = data.decode("utf-8", "replace").strip()
            if not text:
                continue
            if _looks_like_event(text):
                self._queue_events(text)
                continue
            LOG.debug("CTRL << %s", text)
            return text
        raise SupplicantError(f"no reply to {command!r} within {timeout:.1f}s")

    def pump(self, timeout: float) -> list[str]:
        """Return queued events, waiting at most ``timeout`` for new ones."""
        lines = self._drain_events()
        if lines:
            return lines

        data = self._recv(timeout)
        if data is None:
            return []
        text = data.decode("utf-8", "replace")
        if _looks_like_event(text):
            self._queue_events(text)
        else:
            LOG.debug("CTRL (unsolicited reply) << %s", text.strip())
        return self._drain_events()

    def _recv(self, timeout: float) -> bytes | None:
        if self._sock is None:
            return None
        self._sock.settimeout(max(0.01, timeout))
        try:
            return self._sock.recv(MAX_REPLY_BYTES)
        except TimeoutError:
            return None
        except OSError as exc:
            raise SupplicantError(f"control socket error: {exc}") from exc

    def _queue_events(self, text: str) -> None:
        for raw in text.splitlines():
            line = _PRIORITY.sub("", raw).strip()
            if line and _looks_like_event(line):
                self._events.put(line)

    def _drain_events(self) -> list[str]:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._events.get_nowait())
            except queue.Empty:
                return lines


def _looks_like_event(text: str) -> bool:
    stripped = _PRIORITY.sub("", text.strip())
    if not stripped:
        return False
    first = stripped.split(maxsplit=1)[0]
    return first.startswith(("P2P-", "WFD-", "AP-"))
