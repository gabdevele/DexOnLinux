import struct

from dexonlinux.p2p.dhcp import MAGIC, PEER_IP, SERVER_IP, _message_type, _reply


def discover() -> bytes:
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
    return header + MAGIC + bytes((53, 1, 1, 255))


def test_dhcp_offer_has_peer_lease_and_server_identity():
    packet = discover()
    assert _message_type(packet) == 1

    reply = _reply(packet, 2, SERVER_IP, PEER_IP)
    assert reply[0] == 2
    assert reply[4:8] == packet[4:8]
    assert reply[16:20] == bytes((192, 168, 49, 2))
    assert reply[236:240] == MAGIC
    assert bytes((53, 1, 2)) in reply
    assert bytes((54, 4)) + bytes((192, 168, 49, 1)) in reply
    assert bytes((1, 4)) + bytes((255, 255, 255, 0)) in reply


def test_non_dhcp_packet_is_ignored():
    assert _message_type(b"not a dhcp packet") is None
