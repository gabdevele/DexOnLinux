import pytest

from dexonlinux.netlink import LinkError, Pyroute2LinkOps


class FakeIPRoute:
    def __init__(self, links=None):
        # name -> {"index": int, "flags": int}
        self.links = dict(links or {})
        self.calls = []
        self.closed = False

    def link_lookup(self, ifname=None):
        for name, link in self.links.items():
            if name == ifname:
                return [link["index"]]
        return []

    def addr(self, command, **kwargs):
        self.calls.append(("addr", command, kwargs))

    def link(self, command, **kwargs):
        self.calls.append(("link", command, kwargs))
        if command == "dump":
            return [
                {"index": link["index"], "flags": link["flags"], "IFLA_IFNAME": name}
                for name, link in self.links.items()
            ]
        if command == "get":
            for name, link in self.links.items():
                if link["index"] == kwargs["index"]:
                    return [{"index": link["index"], "flags": link["flags"], "IFLA_IFNAME": name}]
            raise KeyError(kwargs["index"])
        return []

    def close(self):
        self.closed = True


def ops_with(links):
    route = FakeIPRoute(links)
    return Pyroute2LinkOps(iproute_factory=lambda: route), route


def test_add_address_replaces_the_address_on_the_link():
    ops, route = ops_with({"p2p-wlan0-0": {"index": 7, "flags": 0x1002}})

    ops.add_address("p2p-wlan0-0", "192.168.49.1", 24)

    assert ("addr", "replace", {"index": 7, "address": "192.168.49.1", "prefixlen": 24}) in route.calls
    assert route.closed is True


def test_set_up_sets_the_link_state():
    ops, route = ops_with({"p2p-wlan0-0": {"index": 7, "flags": 0x1002}})

    ops.set_up("p2p-wlan0-0")

    assert ("link", "set", {"index": 7, "state": "up"}) in route.calls


def test_is_up_reads_the_iff_up_flag():
    ops, _route = ops_with(
        {
            "up": {"index": 1, "flags": 0x1003},
            "down": {"index": 2, "flags": 0x1002},
        }
    )

    assert ops.is_up("up") is True
    assert ops.is_up("down") is False


def test_list_links_returns_interface_names():
    ops, _route = ops_with(
        {
            "p2p-wlan0-0": {"index": 7, "flags": 0x1002},
            "wlan0": {"index": 3, "flags": 0x1003},
        }
    )

    assert ops.list_links() == ["p2p-wlan0-0", "wlan0"]


def test_missing_link_raises_a_link_error():
    ops, route = ops_with({})

    with pytest.raises(LinkError, match="does not exist"):
        ops.add_address("p2p-wlan0-0", "192.168.49.1", 24)

    assert route.closed is True


def test_is_up_on_a_missing_link_raises_a_link_error():
    ops, _route = ops_with({})

    with pytest.raises(LinkError, match="does not exist"):
        ops.is_up("p2p-wlan0-0")
