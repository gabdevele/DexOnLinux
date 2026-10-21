import time

from dexonlinux.p2p.link import LinkConfigurator
from dexonlinux.system import CommandResult


class FakeRunner:
    def __init__(self, output=""):
        self.calls = []
        self.output = output

    def run(self, args, *, sudo=False, check=True, timeout=None):
        self.calls.append((tuple(args), sudo))
        return CommandResult(tuple(args), 0, self.output, "")


class LinkFakeRunner:
    def __init__(self, flags_path):
        self.flags_path = flags_path
        self.calls = []

    def run(self, args, *, sudo=False, check=True, timeout=None):
        command = tuple(args)
        self.calls.append((command, sudo))
        if command[:4] == ("ip", "link", "set", "dev"):
            self.flags_path.write_text("0x1003\n", encoding="ascii")
        return CommandResult(command, 0, "", "")


def test_configure_only_adds_address():
    runner = FakeRunner()
    LinkConfigurator(runner).configure("p2p-wlp0s20-7", "192.168.49.200/24")
    assert runner.calls == [
        (("ip", "addr", "replace", "192.168.49.200/24", "dev", "p2p-wlp0s20-7"), False),
    ]


def test_remove_uses_iw_dev_del():
    runner = FakeRunner()
    LinkConfigurator(runner).remove("p2p-wlp0s20-7")
    assert runner.calls == [(("iw", "dev", "p2p-wlp0s20-7", "del"), False)]


def test_remove_stale_deletes_only_group_links():
    runner = FakeRunner(
        "phy#0\n"
        "\tInterface p2p-wlp0s20-1\n"
        "\t\ttype P2P-client\n"
        "\tInterface p2p-dev-wlp0s20\n"
        "\t\ttype P2P-device\n"
        "\tInterface wlp0s20f3\n"
        "\t\ttype managed\n"
    )
    LinkConfigurator(runner).remove_stale()
    assert [call[0] for call in runner.calls] == [
        ("iw", "dev"),
        ("iw", "dev", "p2p-wlp0s20-1", "del"),
    ]


def test_watcher_pre_ups_new_group_link_and_skips_p2p_dev(tmp_path):
    group = tmp_path / "p2p-wlp0s20-9"
    group.mkdir()
    flags = group / "flags"
    flags.write_text("0x1002\n", encoding="ascii")
    (tmp_path / "p2p-dev-wlp0s20").mkdir()

    runner = LinkFakeRunner(flags)
    link = LinkConfigurator(runner, net_dir=str(tmp_path), interval=0.005)
    link.start_watcher(timeout=1.0)
    try:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if any(call[0][:4] == ("ip", "link", "set", "dev") for call in runner.calls):
                break
            time.sleep(0.005)
    finally:
        link.stop_watcher()

    commands = [call[0] for call in runner.calls]
    assert ("ip", "link", "set", "dev", "p2p-wlp0s20-9", "up") in commands
    assert all("p2p-dev-wlp0s20" not in command for command in commands)
    assert all(command[:3] != ("ip", "addr", "replace") for command in commands)
