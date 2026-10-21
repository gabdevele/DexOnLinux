"""Core P2P types shared by the backends and the helper."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class Peer:
    mac: str
    name: str = ""
    wfd: bool = False


@dataclass(frozen=True)
class GroupInfo:
    ifname: str
    role: Literal["client", "GO"]
    client_ip: str | None
    client_prefix: int
    go_ip: str | None
    peer_mac: str
    frequency: int | None


class P2PError(RuntimeError):
    """A P2P operation failed or produced an unsupported state."""


class P2PBackend(Protocol):
    def prepare(self) -> None:
        """Advertise the sink and make the backend ready to connect."""

    def connect(self, stop: threading.Event, timeout: float = 90.0) -> GroupInfo:
        """Wait for the phone and return a client-role group."""

    def close(self) -> None:
        """Remove the group and restore the settings touched by prepare()."""
