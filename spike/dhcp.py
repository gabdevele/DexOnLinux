"""Minimal DHCP client for the P2P group interface (spike only).

When the phone is the P2P GO it also runs the DHCP server. We only need a
lease on the P2P interface: the default route must stay on the home Wi-Fi, so
this client never installs a default route (it only adds the interface
address, which creates the connected route to the phone).
"""

from __future__ import annotations

import errno
import logging
import random
import socket
import struct
import subprocess
import time
from dataclasses import dataclass

LOG = logging.getLogger("spike.dhcp")

MAGIC_COOKIE = b"\x63\x82\x53\x63"
DISCOVER = 1
OFFER = 2
REQUEST = 3
ACK = 5

BROADCAST = "255.255.255.255"
# Android's P2P GO always uses 192.168.49.1/24. The limited broadcast has no
# route on a freshly created group interface; this subnet broadcast does once
# linkd seeds 192.168.49.2/24, and the GO's DHCP server listens on both.
SUBNET_BROADCAST = "192.168.49.255"
SERVER_PORT = 67
CLIENT_PORT = 68


@dataclass
class Lease:
    ip: str
    prefix: int
    router: str
    server: str


class DhcpError(RuntimeError):
    pass


@dataclass
class _Reply:
    message_type: int
    ip: str
    server: str
    prefix: int
    router: str


def _iface_mac(ifname: str) -> bytes:
    with open(f"/sys/class/net/{ifname}/address", "r", encoding="ascii") as handle:
        return bytes.fromhex(handle.read().strip().replace(":", ""))


def _wait_for_carrier(ifname: str, timeout: float = 5.0) -> None:
    """Wait until the P2P client interface is associated (carrier up)."""
    deadline = time.monotonic() + timeout
    path = f"/sys/class/net/{ifname}/carrier"
    while time.monotonic() < deadline:
        try:
            with open(path, "r", encoding="ascii") as handle:
                if handle.read().strip() == "1":
                    return
        except OSError:
            pass
        time.sleep(0.1)
    LOG.warning("%s has no carrier after %.1fs; trying DHCP anyway", ifname, timeout)


def _build_packet(xid: int, msg_type: int, mac: bytes, requested_ip: str = "", server_id: str = "") -> bytes:
    header = struct.pack(
        "!BBBBIHHIIII16s64s128s",
        1,  # BOOTREQUEST
        1,  # Ethernet
        6,  # MAC length
        0,
        xid,
        0,
        0x8000,  # broadcast flag: we have no address yet
        0,
        0,
        0,
        0,
        mac + b"\x00" * 10,
        b"\x00" * 64,
        b"\x00" * 128,
    )

    options = bytearray()
    options += bytes([53, 1, msg_type])
    options += bytes([55, 2, 1, 3])  # subnet mask + router
    options += bytes([57, 2]) + struct.pack("!H", 1500)
    if requested_ip:
        options += bytes([50, 4]) + socket.inet_aton(requested_ip)
    if server_id:
        options += bytes([54, 4]) + socket.inet_aton(server_id)
    options += b"\xff"

    packet = header + MAGIC_COOKIE + bytes(options)
    if len(packet) < 300:
        packet += b"\x00" * (300 - len(packet))
    return packet


def _parse_options(payload: bytes) -> dict[int, bytes]:
    if len(payload) < 240 or payload[236:240] != MAGIC_COOKIE:
        return {}
    options: dict[int, bytes] = {}
    index = 240
    while index < len(payload):
        code = payload[index]
        index += 1
        if code == 0:
            continue
        if code == 255:
            break
        if index >= len(payload):
            break
        length = payload[index]
        index += 1
        value = payload[index:index + length]
        if len(value) != length:
            break
        options[code] = value
        index += length
    return options


def _parse_reply(payload: bytes) -> _Reply:
    options = _parse_options(payload)
    msg_type = options[53][0] if options.get(53) else 0
    prefix = 0
    if 1 in options and len(options[1]) == 4:
        prefix = bin(struct.unpack("!I", options[1])[0]).count("1")
    router = ""
    if 3 in options and len(options[3]) >= 4:
        router = socket.inet_ntoa(options[3][:4])
    server = ""
    if 54 in options and len(options[54]) == 4:
        server = socket.inet_ntoa(options[54])
    return _Reply(
        message_type=msg_type,
        ip=socket.inet_ntoa(payload[16:20]),
        server=server,
        prefix=prefix,
        router=router,
    )


def _send(sock: socket.socket, packet: bytes) -> None:
    """Send a DHCP packet, working around the missing limited-broadcast route."""
    try:
        sock.sendto(packet, (BROADCAST, SERVER_PORT))
        return
    except OSError as exc:
        if exc.errno != errno.ENETUNREACH:
            raise
        LOG.info("no route for %s; using %s", BROADCAST, SUBNET_BROADCAST)
        sock.sendto(packet, (SUBNET_BROADCAST, SERVER_PORT))


def _await(sock: socket.socket, xid: int, wanted_type: int, deadline: float) -> _Reply | None:
    while time.monotonic() < deadline:
        sock.settimeout(max(0.1, min(2.0, deadline - time.monotonic())))
        try:
            payload, _ = sock.recvfrom(2048)
        except socket.timeout:
            return None
        if len(payload) < 240 or struct.unpack("!I", payload[4:8])[0] != xid:
            continue
        reply = _parse_reply(payload)
        if reply.message_type == wanted_type:
            return reply
    return None


def acquire(ifname: str, timeout: float = 12.0) -> Lease:
    """Run DISCOVER/REQUEST and configure the interface; no default route."""
    mac = _iface_mac(ifname)
    subprocess.run(["ip", "link", "set", "dev", ifname, "up"], check=True, capture_output=True)
    _wait_for_carrier(ifname)
    xid = random.getrandbits(32)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, ifname.encode("ascii"))
    try:
        sock.bind(("0.0.0.0", CLIENT_PORT))
    except OSError as exc:
        sock.close()
        raise DhcpError(f"cannot bind DHCP client port on {ifname}: {exc}") from exc

    deadline = time.monotonic() + timeout
    lease: Lease | None = None
    try:
        while time.monotonic() < deadline and lease is None:
            try:
                _send(sock, _build_packet(xid, DISCOVER, mac))
            except OSError as exc:
                raise DhcpError(f"cannot send DHCPDISCOVER on {ifname}: {exc}") from exc
            LOG.info("DHCPDISCOVER on %s (xid %08x)", ifname, xid)

            offer = _await(sock, xid, OFFER, deadline)
            if offer is None:
                continue

            request = _build_packet(xid, REQUEST, mac, requested_ip=offer.ip, server_id=offer.server)
            for _ in range(3):
                try:
                    _send(sock, request)
                except OSError as exc:
                    raise DhcpError(f"cannot send DHCPREQUEST on {ifname}: {exc}") from exc
                ack = _await(sock, xid, ACK, deadline)
                if ack is not None:
                    lease = Lease(
                        ip=ack.ip,
                        prefix=ack.prefix or offer.prefix or 24,
                        router=ack.router or offer.router,
                        server=ack.server or offer.server,
                    )
                    break
            if lease is None:
                LOG.warning("no DHCPACK for %s", offer.ip)
    finally:
        sock.close()

    if lease is None:
        raise DhcpError(f"DHCP failed on {ifname} within {timeout:.0f}s")

    LOG.info("lease %s/%d via %s (router %s)", lease.ip, lease.prefix, lease.server, lease.router or "unknown")
    subprocess.run(
        ["ip", "addr", "replace", f"{lease.ip}/{lease.prefix}", "dev", ifname],
        check=True,
        capture_output=True,
    )
    return lease
