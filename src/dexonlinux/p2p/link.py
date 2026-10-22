"""P2P group link setup.

Association happens while the group netdev is still down. Some drivers drop
the client if the netdev is brought up only after association, so a short
bounded watcher raises it during GO negotiation; the address is added only
after GROUP-STARTED.
"""

import ipaddress
import logging
import threading
import time

from dexonlinux.netlink import LinkError, LinkOps
from dexonlinux.system import Runner

LOG = logging.getLogger(__name__)


class LinkConfigurator:
    def __init__(
        self,
        ops: LinkOps,
        runner: Runner,
        *,
        interval: float = 0.05,
    ) -> None:
        self._ops = ops
        self._runner = runner
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def configure(self, ifname: str, cidr: str) -> None:
        # Do not touch IFF_UP here: the watcher owns it, and toggling the link
        # after association can make some drivers drop the client.
        interface = ipaddress.ip_interface(cidr)
        self._ops.add_address(ifname, str(interface.ip), interface.network.prefixlen)

    def remove(self, ifname: str) -> None:
        # The phone may have removed the group first; that is not an error.
        self._runner.run(["iw", "dev", ifname, "del"], check=False)

    def remove_stale(self) -> None:
        """Remove P2P group links left by a previous failed session."""
        try:
            names = self._ops.list_links()
        except LinkError as exc:
            LOG.warning("could not inspect P2P interfaces: %s", exc)
            return

        for ifname in names:
            if not ifname.startswith("p2p-") or ifname.startswith("p2p-dev-"):
                continue
            LOG.info("removing stale P2P group interface %s", ifname)
            self._runner.run(["iw", "dev", ifname, "del"], check=False)

    def start_watcher(self, timeout: float = 10.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._watch,
            args=(time.monotonic() + timeout,),
            name="p2p-link-watcher",
            daemon=True,
        )
        self._thread.start()

    def stop_watcher(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None

    def _watch(self, deadline: float) -> None:
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                names = self._ops.list_links()
            except LinkError as exc:
                LOG.debug("link watcher could not list interfaces: %s", exc)
                names = []
            for ifname in names:
                if not ifname.startswith("p2p-") or ifname.startswith("p2p-dev-"):
                    continue
                try:
                    if self._ops.is_up(ifname):
                        continue
                    self._ops.set_up(ifname)
                except LinkError as exc:
                    # The group may disappear while it is being negotiated;
                    # keep watching the remaining interfaces.
                    LOG.debug("link watcher skipped %s: %s", ifname, exc)
                    continue
                LOG.info("pre-upped P2P group interface %s", ifname)
            self._stop.wait(self._interval)
