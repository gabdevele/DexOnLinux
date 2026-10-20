"""Minimal wpa_supplicant control-interface client (spike only).

The system supplicant is owned by NetworkManager; we only *talk* to it and
never stop it. P2P commands go to the dedicated P2P-device control socket.
The command vocabulary used here is documented in wpa_supplicant/README-P2P
and wpa_supplicant/wifi_display.c.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import socket
import time

LOG = logging.getLogger("spike.supplicant")

CTRL_DIR = "/run/wpa_supplicant"
IFNAMSIZ = 16
_PRIORITY_RE = re.compile(r"^<\d+>")


class SupplicantError(RuntimeError):
    pass


def p2p_ctrl_name(ifname: str) -> str:
    """Name of the dedicated P2P-device ctrl socket (kernel truncates to 15)."""
    return ("p2p-dev-" + ifname)[: IFNAMSIZ - 1]


def find_ctrl_socket(ifname: str, ctrl_dir: str = CTRL_DIR) -> str:
    candidate = os.path.join(ctrl_dir, p2p_ctrl_name(ifname))
    if os.path.exists(candidate):
        return candidate

    try:
        p2p_sockets = sorted(name for name in os.listdir(ctrl_dir) if name.startswith("p2p-dev-"))
    except OSError as exc:
        raise SupplicantError(f"cannot read {ctrl_dir}: {exc}") from exc

    if len(p2p_sockets) == 1:
        return os.path.join(ctrl_dir, p2p_sockets[0])

    fallback = os.path.join(ctrl_dir, ifname)
    if os.path.exists(fallback):
        return fallback

    raise SupplicantError(
        f"no P2P control socket for {ifname} in {ctrl_dir} "
        f"(found: {', '.join(p2p_sockets) or 'none'})"
    )


class SupplicantControl:
    """ATTACH-ed control connection with a small event queue."""

    def __init__(self, ifname: str, ctrl_dir: str = CTRL_DIR) -> None:
        self.ifname = ifname
        self.path = find_ctrl_socket(ifname, ctrl_dir)
        self._sock: socket.socket | None = None
        self._events: queue.Queue[str] = queue.Queue()

    def open(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        # Abstract address: lets the supplicant deliver replies/events to us.
        sock.bind(f"\0dexonlinux-{os.getpid()}-{id(sock)}")
        sock.connect(self.path)
        sock.settimeout(2.0)
        self._sock = sock
        reply = self.command("ATTACH")
        if reply.strip() != "OK":
            self.close()
            raise SupplicantError(f"ATTACH failed: {reply.strip()!r}")
        LOG.info("attached to %s", self.path)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def command(self, cmd: str, timeout: float = 2.0) -> str:
        if self._sock is None:
            raise SupplicantError("control connection is not open")
        self._sock.send(cmd.encode("utf-8"))
        LOG.debug("CTRL >> %s", cmd)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self._recv(max(0.05, deadline - time.monotonic()))
            if data is None:
                break
            text = data.decode("utf-8", "replace").strip()
            if not text:
                continue
            if _looks_like_event(text):
                self._queue_events(text)
                continue
            LOG.debug("CTRL << %s", text)
            return text
        raise SupplicantError(f"no reply to {cmd!r} within {timeout:.1f}s")

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

    # -- internals ----------------------------------------------------------

    def _recv(self, timeout: float) -> bytes | None:
        if self._sock is None:
            return None
        self._sock.settimeout(max(0.01, timeout))
        try:
            return self._sock.recv(4096)
        except socket.timeout:
            return None
        except OSError as exc:
            raise SupplicantError(f"control socket error: {exc}") from exc

    def _queue_events(self, text: str) -> None:
        for raw in text.splitlines():
            line = _PRIORITY_RE.sub("", raw).strip()
            if line:
                self._events.put(line)

    def _drain_events(self) -> list[str]:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._events.get_nowait())
            except queue.Empty:
                return lines


def _looks_like_event(text: str) -> bool:
    first = text.splitlines()[0] if text else ""
    return bool(_PRIORITY_RE.match(first)) or first.startswith(("CTRL-EVENT-", "P2P-"))
