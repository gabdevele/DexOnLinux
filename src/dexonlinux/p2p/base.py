import ipaddress
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class P2PRole(str, Enum):
    """Role this side plays in the P2P group.

    The WFA ``go_intent`` value forces the negotiated outcome: 15 means
    "become group owner", 0 means "become client".
    """

    CLIENT = "client"
    GO = "go"

    @property
    def go_intent(self) -> int:
        return 15 if self is P2PRole.GO else 0


@dataclass(frozen=True)
class GroupNetwork:
    """IPv4 network of the group owner, served by the GO-side DHCP server."""

    server_ip: str = "192.168.49.1"
    peer_ip: str = "192.168.49.2"
    prefix: int = 24

    @property
    def netmask(self) -> str:
        return str(self._network().netmask)

    @property
    def broadcast(self) -> str:
        return str(self._network().broadcast_address)

    def _network(self) -> ipaddress.IPv4Network:
        return ipaddress.IPv4Network(f"{self.server_ip}/{self.prefix}", strict=False)


# MHz, 2.4 GHz channel 6. This is only the default: the CLI exposes --p2p-freq
# and `doctor` fails fast when the adapter does not support the chosen channel.
DEFAULT_P2P_FREQ = 2437
GO_NETWORK = GroupNetwork()


@dataclass(frozen=True)
class Peer:
    mac: str
    name: str = ""
    wfd: bool = False


@dataclass(frozen=True)
class GroupInfo:
    """A started P2P group.

    ``local_ip`` is this side's address; ``peer_ip`` is the other side's
    address when it is known: the DHCP lease for the GO side, the GO address
    for the client side.
    """

    ifname: str
    role: P2PRole
    local_ip: str | None
    peer_ip: str | None
    local_prefix: int = 24
    frequency: int | None = None
    go_dev_addr: str = ""


class P2PError(RuntimeError):
    """A P2P operation failed or produced an unsupported state."""


class P2PBackend(Protocol):
    def prepare(self) -> None:
        """Advertise the sink and make the backend ready to connect."""

    def connect(self, stop: threading.Event, timeout: float = 90.0) -> GroupInfo:
        """Wait for the phone and return the started group (either role)."""

    def close(self) -> None:
        """Remove the group and restore the settings touched by prepare()."""
