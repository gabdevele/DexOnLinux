from dexonlinux.p2p.base import GroupNetwork, P2PRole


def test_role_go_intent_forces_the_outcome():
    assert P2PRole.GO.go_intent == 15
    assert P2PRole.CLIENT.go_intent == 0


def test_group_network_derives_mask_and_broadcast():
    network = GroupNetwork("10.13.37.1", "10.13.37.2", 24)
    assert network.netmask == "255.255.255.0"
    assert network.broadcast == "10.13.37.255"
