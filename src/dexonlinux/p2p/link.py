"""P2P group interface link/address setup without a default route."""

from __future__ import annotations

from dexonlinux.system import Runner


class LinkConfigurator:
    def __init__(self, runner: Runner) -> None:
        self._runner = runner

    def configure(self, ifname: str, cidr: str) -> None:
        self._runner.run(["ip", "link", "set", "dev", ifname, "up"])
        self._runner.run(["ip", "addr", "replace", cidr, "dev", ifname])

    def remove(self, ifname: str) -> None:
        # The phone may have removed the group first; that is not an error.
        self._runner.run(["iw", "dev", ifname, "del"], check=False)
