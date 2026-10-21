import struct

from dexonlinux.p2p.base import GroupNetwork
from dexonlinux.p2p.dhcp import (
    DHCPACK,
    DHCPDISCOVER,
    DHCPOFFER,
    MAGIC,
    DhcpServer,
    _message_type,
    _reply,
)

NETWORK = GroupNetwork("10.13.37.1", "10.13.37.2", 24)


def packet(message_type: int, requested_ip: bytes | None = None) -> bytes:
    header = struct.pack(
        "!BBBBIHH4s4s4s4s16s64s128s",
        1,
        1,
        6,
        0,
        0x12345678,
        0,
        0x8000,
        b"\x00" * 4,
        b"\x00" * 4,
        b"\x00" * 4,
        b"\x00" * 4,
        bytes.fromhex("aabbccddeeff") + b"\x00" * 10,
        b"\x00" * 64,
        b"\x00" * 128,
    )
    options = bytes((53, 1, message_type))
    if requested_ip is not None:
        options += bytes((50, 4)) + requested_ip
    return header + MAGIC + options + b"\xff"


def test_dhcp_offer_contains_the_configured_lease():
    request = packet(DHCPDISCOVER)
    assert _message_type(request) == DHCPDISCOVER

    reply = _reply(request, DHCPOFFER, NETWORK)
    assert reply[0] == 2
    assert reply[4:8] == request[4:8]
    assert reply[16:20] == bytes((10, 13, 37, 2))
    assert reply[236:240] == MAGIC
    assert bytes((53, 1, DHCPOFFER)) in reply
    assert bytes((54, 4)) + bytes((10, 13, 37, 1)) in reply
    assert bytes((1, 4)) + bytes((255, 255, 255, 0)) in reply
    assert bytes((3, 4)) + bytes((10, 13, 37, 1)) in reply
    assert bytes((28, 4)) + bytes((10, 13, 37, 255)) in reply


def test_request_for_another_address_still_gets_the_configured_lease():
    request = packet(DHCPDISCOVER, requested_ip=bytes((10, 13, 37, 99)))
    reply = _reply(request, DHCPACK, NETWORK)
    assert reply[16:20] == bytes((10, 13, 37, 2))


def test_server_exposes_the_configured_peer_ip():
    assert DhcpServer(NETWORK).peer_ip == "10.13.37.2"


def test_non_dhcp_packet_is_ignored():
    assert _message_type(b"not a dhcp packet") is None
