"""Transport seam between the P2P policy and the supplicant.

``DirectBackend`` drives group formation through this protocol. The default
implementation talks to wpa_supplicant over D-Bus; the control socket is kept
as a fallback for hosts whose supplicant exposes an incomplete D-Bus API.

The protocol is deliberately expressed in Dex terms (peer, group, WFD state)
so the backend never sees command strings or D-Bus object paths.
"""

from typing import Protocol

from dexonlinux.p2p.base import Peer
from dexonlinux.p2p.events import P2PEvent


class SupplicantError(RuntimeError):
    """The supplicant could not be reached or refused a request."""


class P2PControl(Protocol):
    def open(self) -> None:
        """Attach to the supplicant and validate the required API."""

    def close(self) -> None:
        """Detach; must be safe to call more than once."""

    def prepare_wfd(self, *, go_intent: int, device_name: str) -> object:
        """Advertise this host as a WFD sink and return the previous state."""

    def restore_wfd(self, state: object) -> None:
        """Put back the WFD state returned by ``prepare_wfd``."""

    def start_extended_listen(self, period_ms: int, interval_ms: int) -> None:
        """Become discoverable while waiting for the phone."""

    def stop_extended_listen(self) -> None:
        """Stop the extended listen timing."""

    def connect(self, peer: Peer, *, frequency: int, go_intent: int) -> None:
        """Start PBC group formation with ``peer``."""

    def disconnect(self) -> None:
        """Tear down the group tracked by the transport, if any."""

    def pump(self, timeout: float) -> list[P2PEvent]:
        """Return the events received within ``timeout``."""
