"""Link and address operations through netlink.

The project talks to the kernel through pyroute2 instead of parsing the text
output of ``ip``. The CLI's wording and column layout differ between iproute2
releases; netlink attributes do not.

Only addresses, link state and interface enumeration live here. P2P group
interfaces are still removed with ``iw dev <ifname> del``: that is an nl80211
operation and has no RTNL equivalent.
"""

from collections.abc import Callable
from typing import Protocol

from pyroute2 import IPRoute

IFF_UP = 0x1


class LinkError(RuntimeError):
    """A link or address operation failed."""


class LinkOps(Protocol):
    def add_address(self, ifname: str, address: str, prefixlen: int) -> None: ...

    def set_up(self, ifname: str) -> None: ...

    def is_up(self, ifname: str) -> bool: ...

    def list_links(self) -> list[str]: ...


class Pyroute2LinkOps:
    """``LinkOps`` backed by a fresh ``IPRoute`` socket per operation.

    The P2P watcher runs in its own thread while the session thread configures
    the group, so a shared socket would need locking. Netlink sockets are cheap
    and a fresh one per call keeps the class thread-safe.
    """

    def __init__(self, iproute_factory: Callable[[], IPRoute] = IPRoute) -> None:
        self._iproute_factory = iproute_factory

    def add_address(self, ifname: str, address: str, prefixlen: int) -> None:
        ipr = self._iproute_factory()
        try:
            ipr.addr("replace", index=self._index(ipr, ifname), address=address, prefixlen=prefixlen)
        finally:
            ipr.close()

    def set_up(self, ifname: str) -> None:
        ipr = self._iproute_factory()
        try:
            ipr.link("set", index=self._index(ipr, ifname), state="up")
        finally:
            ipr.close()

    def is_up(self, ifname: str) -> bool:
        ipr = self._iproute_factory()
        try:
            link = self._get(ipr, ifname)
        finally:
            ipr.close()
        return bool(link.get("flags", 0) & IFF_UP)

    def list_links(self) -> list[str]:
        ipr = self._iproute_factory()
        try:
            return [link["IFLA_IFNAME"] for link in ipr.link("dump") if link.get("IFLA_IFNAME")]
        finally:
            ipr.close()

    @staticmethod
    def _index(ipr: IPRoute, ifname: str) -> int:
        try:
            return int(ipr.link_lookup(ifname=ifname)[0])
        except (IndexError, KeyError) as exc:
            raise LinkError(f"interface {ifname} does not exist") from exc

    @classmethod
    def _get(cls, ipr: IPRoute, ifname: str) -> dict:
        try:
            return ipr.link("get", index=cls._index(ipr, ifname))[0]
        except (IndexError, KeyError) as exc:
            raise LinkError(f"interface {ifname} does not exist") from exc
