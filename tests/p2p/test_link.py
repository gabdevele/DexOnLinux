import time

from dexonlinux.netlink import LinkError
from dexonlinux.p2p.link import LinkConfigurator
from dexonlinux.system import CommandResult


class FakeRunner:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def run(self, args, *, sudo=False, check=True, timeout=None):
        self.calls.append((tuple(args), sudo))
        return CommandResult(tuple(args), self.returncode, "", "")


class FakeLinkOps:
    def __init__(self, links=None, *, fail_list=False):
        self.links = {
            name: {"index": index, "flags": flags}
            for index, (name, flags) in enumerate((links or {}).items(), start=1)
        }
        self.fail_list = fail_list
        self.addresses = []
        self.ups = []

    def add_address(self, ifname, address, prefixlen):
        if ifname not in self.links:
            raise LinkError(f"interface {ifname} does not exist")
        self.addresses.append((ifname, address, prefixlen))

    def set_up(self, ifname):
        if ifname not in self.links:
            raise LinkError(f"interface {ifname} does not exist")
        self.ups.append(ifname)
        self.links[ifname]["flags"] |= 0x1

    def is_up(self, ifname):
        if ifname not in self.links:
            raise LinkError(f"interface {ifname} does not exist")
        return bool(self.links[ifname]["flags"] & 0x1)

    def list_links(self):
        if self.fail_list:
            raise LinkError("netlink unavailable")
        return list(self.links)


def test_configure_adds_address_with_pyroute2():
    ops = FakeLinkOps({"p2p-wlp0s20-7": 0x1002})
    LinkConfigurator(ops, FakeRunner()).configure("p2p-wlp0s20-7", "192.168.49.200/24")

    assert ops.addresses == [("p2p-wlp0s20-7", "192.168.49.200", 24)]


def test_remove_still_uses_iw_dev_del():
    runner = FakeRunner()
    LinkConfigurator(FakeLinkOps(), runner).remove("p2p-wlp0s20-7")

    assert runner.calls == [(("iw", "dev", "p2p-wlp0s20-7", "del"), False)]


def test_remove_stale_deletes_only_p2p_group_links():
    ops = FakeLinkOps(
        {
            "p2p-wlp0s20-1": 0x1002,
            "p2p-dev-wlp0s20": 0x1003,
            "wlp0s20f3": 0x1003,
        }
    )
    runner = FakeRunner()
    LinkConfigurator(ops, runner).remove_stale()

    assert runner.calls == [(("iw", "dev", "p2p-wlp0s20-1", "del"), False)]


def test_remove_stale_tolerates_an_unavailable_netlink():
    ops = FakeLinkOps(fail_list=True)
    runner = FakeRunner()
    LinkConfigurator(ops, runner).remove_stale()

    assert runner.calls == []


def test_watcher_pre_ups_new_group_link_and_skips_p2p_dev():
    ops = FakeLinkOps({"p2p-wlp0s20-9": 0x1002, "p2p-dev-wlp0s20": 0x1003})
    link = LinkConfigurator(ops, FakeRunner(), interval=0.005)
    link.start_watcher(timeout=1.0)
    try:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and not ops.ups:
            time.sleep(0.005)
    finally:
        link.stop_watcher()

    assert ops.ups == ["p2p-wlp0s20-9"]
    assert ops.addresses == []
