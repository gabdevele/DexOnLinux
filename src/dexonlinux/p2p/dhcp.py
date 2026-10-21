"""Minimal DHCP server for a P2P GO interface.

The phone is the P2P client and needs an IPv4 lease before its Wi-Fi Display
source can expose the RTSP server.  A full DHCP daemon would add a dependency
and another process; this server only handles the DISCOVER/REQUEST exchange
for the single P2P peer.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading

from dexonlinux.p2p.base import P2PError

LOG = logging.getLogger(__name__)

MAGIC = b"\x63\x82\x53\x63"
SERVER_IP = "192.168.49.1"
PEER_IP = "192.168.49.2"
PREFIX = 24
LEASE_SECONDS = 3600


def _option(code: int, value: bytes) -> bytes:
    return bytes((code, len(value))) + value


def _reply(request: bytes, message_type: int, server_ip: str, peer_ip: str) -> bytes:
    """Build a BOOTP/DHCP OFFER (2) or ACK (5) for one request."""
    xid = request[4:8]
    flags = request[10:12]
    chaddr = request[28:44]

    yiaddr = socket.inet_aton(peer_ip)
    siaddr = socket.inet_aton(server_ip)
    router = socket.inet_aton(server_ip)
    broadcast = socket.inet_aton("192.168.49.255")
    mask = socket.inet_aton("255.255.255.0")
    options = b"".join(
        (
            _option(53, bytes((message_type,))),
            _option(54, siaddr),
            _option(51, struct.pack("!I", LEASE_SECONDS)),
            _option(1, mask),
            _option(3, router),
            _option(28, broadcast),
            b"\xff",
        )
    )
    header = struct.pack(
        "!BBBBIHH4s4s4s4s16s64s128s",
        2,  # BOOTREPLY
        1,  # Ethernet
        6,  # MAC length
        0,  # hops
        struct.unpack("!I", xid)[0],
        0,  # secs
        struct.unpack("!H", flags)[0],
        b"\x00" * 4,  # ciaddr
        yiaddr,
        siaddr,
        b"\x00" * 4,  # giaddr
        chaddr,
        b"\x00" * 64,  # sname
        b"\x00" * 128,  # file
    )
    return header + MAGIC + options


def _message_type(packet: bytes) -> int | None:
    if len(packet) < 240 or packet[236:240] != MAGIC:
        return None
    index = 240
    while index < len(packet):
        code = packet[index]
        if code == 0:
            index += 1
            continue
        if code == 255 or index + 1 >= len(packet):
            return None
        length = packet[index + 1]
        value = packet[index + 2 : index + 2 + length]
        if code == 53 and len(value) == 1:
            return value[0]
        index += 2 + length
    return None


class DhcpServer:
    """Serve one fixed lease to the P2P client on ``interface``."""

    def __init__(self, server_ip: str = SERVER_IP, peer_ip: str = PEER_IP) -> None:
        self._server_ip = server_ip
        self._peer_ip = peer_ip
        self._interface = ""
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def peer_ip(self) -> str:
        return self._peer_ip

    def start(self, interface: str) -> None:
        if self._thread is not None:
            return
        self._interface = interface
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b"\0")
            sock.bind(("0.0.0.0", 67))
            sock.settimeout(1.0)
        except OSError as exc:
            sock.close()
            raise P2PError(f"cannot start DHCP server on {interface}: {exc}") from exc

        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="dexonlinux-dhcp", daemon=True)
        self._thread.start()
        LOG.info("DHCP server for %s/%d on %s", self._peer_ip, PREFIX, interface)

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                packet, _ = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            kind = _message_type(packet)
            if kind not in (1, 3):  # DISCOVER, REQUEST
                continue
            reply_type = 2 if kind == 1 else 5  # OFFER, ACK
            reply = _reply(packet, reply_type, self._server_ip, self._peer_ip)
            try:
                self._sock.sendto(reply, ("255.255.255.255", 68))
            except OSError as exc:
                LOG.debug("DHCP reply failed: %s", exc)
