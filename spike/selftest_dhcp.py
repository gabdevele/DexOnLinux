"""Offline tests for the DHCP codec and receive loop (no root/interface needed)."""

from __future__ import annotations

import socket
import struct
import sys
import time

import dhcp


def make_reply(
    xid: int, msg_type: int, yiaddr: str, mask: str, router: str, server: str
) -> bytes:
    header = struct.pack(
        "!BBBBIHHIIII16s64s128s",
        2,  # BOOTREPLY
        1,
        6,
        0,
        xid,
        0,
        0,
        0,
        int.from_bytes(socket.inet_aton(yiaddr), "big"),
        int.from_bytes(socket.inet_aton(server), "big"),
        0,
        b"\x00" * 16,
        b"\x00" * 64,
        b"\x00" * 128,
    )
    options = bytearray()
    options += bytes([53, 1, msg_type])
    options += bytes([1, 4]) + socket.inet_aton(mask)
    options += bytes([3, 4]) + socket.inet_aton(router)
    options += bytes([54, 4]) + socket.inet_aton(server)
    options += b"\xff"
    packet = header + dhcp.MAGIC_COOKIE + bytes(options)
    return packet + b"\x00" * max(0, 300 - len(packet))


def test_build_packet() -> None:
    mac = bytes.fromhex("001122334455")
    packet = dhcp._build_packet(0x12345678, dhcp.DISCOVER, mac)
    assert len(packet) >= 300
    assert packet[0] == 1 and packet[1] == 1 and packet[2] == 6
    assert struct.unpack("!I", packet[4:8])[0] == 0x12345678
    assert packet[236:240] == dhcp.MAGIC_COOKIE
    assert packet[240:243] == bytes([53, 1, dhcp.DISCOVER])
    assert bytes([55, 2, 1, 3]) in packet

    request = dhcp._build_packet(1, dhcp.REQUEST, mac, "192.168.49.2", "192.168.49.1")
    assert bytes([50, 4]) + socket.inet_aton("192.168.49.2") in request
    assert bytes([54, 4]) + socket.inet_aton("192.168.49.1") in request
    print("PASS: DHCP DISCOVER/REQUEST packets are well formed")


def test_parse_reply() -> None:
    reply = dhcp._parse_reply(
        make_reply(
            7, dhcp.OFFER, "192.168.49.2", "255.255.255.0", "192.168.49.1", "192.168.49.1"
        )
    )
    assert reply.message_type == dhcp.OFFER
    assert reply.ip == "192.168.49.2"
    assert reply.prefix == 24
    assert reply.router == "192.168.49.1"
    assert reply.server == "192.168.49.1"
    print("PASS: DHCP OFFER/ACK fields are parsed")


def test_truncated_options_are_safe() -> None:
    packet = make_reply(
        7, dhcp.ACK, "192.168.49.2", "255.255.255.0", "192.168.49.1", "192.168.49.1"
    )
    truncated = packet[:240] + bytes([53, 1, dhcp.ACK, 54, 3, 1, 2]) + b"\xff"
    reply = dhcp._parse_reply(truncated)
    assert reply.message_type == dhcp.ACK
    assert reply.server == ""
    print("PASS: truncated DHCP options are ignored safely")


def test_await_filters_xid_and_times_out() -> None:
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    address = receiver.getsockname()
    try:
        wrong = make_reply(
            1, dhcp.OFFER, "10.0.0.2", "255.255.255.0", "10.0.0.1", "10.0.0.1"
        )
        right = make_reply(
            2, dhcp.OFFER, "192.168.49.2", "255.255.255.0", "192.168.49.1", "192.168.49.1"
        )
        sender.sendto(wrong, address)
        sender.sendto(right, address)
        reply = dhcp._await(receiver, 2, dhcp.OFFER, time.monotonic() + 1.0)
        assert reply is not None and reply.ip == "192.168.49.2"
    finally:
        sender.close()
        receiver.close()

    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    try:
        assert dhcp._await(receiver, 9, dhcp.ACK, time.monotonic() + 0.2) is None
    finally:
        receiver.close()
    print("PASS: _await ignores other transactions and times out")


def main() -> int:
    test_build_packet()
    test_parse_reply()
    test_truncated_options_are_safe()
    test_await_filters_xid_and_times_out()
    print("PASS: all DHCP tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
