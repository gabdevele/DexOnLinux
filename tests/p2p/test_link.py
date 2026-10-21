# tests/p2p/test_link.py
from dexonlinux.p2p.link import LinkConfigurator


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, args, *, sudo=False, check=True, timeout=None):
        self.calls.append((tuple(args), sudo))

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()


def test_configure_sets_link_up_then_address():
    runner = FakeRunner()
    LinkConfigurator(runner).configure("p2p-wlp0s20-7", "192.168.49.200/24")
    assert runner.calls == [
        (("ip", "link", "set", "dev", "p2p-wlp0s20-7", "up"), False),
        (("ip", "addr", "replace", "192.168.49.200/24", "dev", "p2p-wlp0s20-7"), False),
    ]


def test_remove_uses_iw_dev_del():
    runner = FakeRunner()
    LinkConfigurator(runner).remove("p2p-wlp0s20-7")
    assert runner.calls == [(("iw", "dev", "p2p-wlp0s20-7", "del"), False)]
