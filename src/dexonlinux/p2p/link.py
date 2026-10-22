"""P2P group link setup.

Association happens while the group netdev is still down. Some drivers drop
the client if the netdev is brought up only after association, so a short
bounded watcher raises it during GO negotiation; the address is added only
after GROUP-STARTED.
"""

import logging
import os
import threading
import time

from dexonlinux.system import Runner

LOG = logging.getLogger(__name__)


class LinkConfigurator:
    def __init__(
        self,
        runner: Runner,
        *,
        net_dir: str = "/sys/class/net",
        interval: float = 0.05,
    ) -> None:
        self._runner = runner
        self._net_dir = net_dir
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def configure(self, ifname: str, cidr: str) -> None:
        # Do not touch IFF_UP here: the watcher owns it, and toggling the link
        # after association can make some drivers drop the client.
        self._runner.run(["ip", "addr", "replace", cidr, "dev", ifname])

    def remove(self, ifname: str) -> None:
        # The phone may have removed the group first; that is not an error.
        self._runner.run(["iw", "dev", ifname, "del"], check=False)

    def remove_stale(self) -> None:
        """Remove P2P group links left by a previous failed session."""
        result = self._runner.run(["iw", "dev"], check=False)
        if result.returncode != 0:
            LOG.warning("could not inspect P2P interfaces: %s", result.stderr.strip())
            return

        current = ""
        stale: list[str] = []
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Interface "):
                current = stripped.split()[1]
            elif (
                stripped in {"type P2P-client", "type P2P-GO"}
                and current.startswith("p2p-")
                and not current.startswith("p2p-dev-")
            ):
                stale.append(current)
        for ifname in stale:
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
                names = os.listdir(self._net_dir)
            except OSError:
                names = []
            for ifname in names:
                if not ifname.startswith("p2p-") or ifname.startswith("p2p-dev-"):
                    continue
                if self._is_link_up(ifname):
                    continue
                self._runner.run(["ip", "link", "set", "dev", ifname, "up"], check=False)
                if self._is_link_up(ifname):
                    LOG.info("pre-upped P2P group interface %s", ifname)
            self._stop.wait(self._interval)

    def _is_link_up(self, ifname: str) -> bool:
        try:
            with open(f"{self._net_dir}/{ifname}/flags", encoding="ascii") as handle:
                return bool(int(handle.read().strip(), 16) & 0x1)
        except (OSError, ValueError):
            return False
