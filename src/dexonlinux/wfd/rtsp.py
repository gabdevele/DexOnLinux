"""Minimal RTSP plumbing for a Wi-Fi Display sink.

Topology (see docs/research/2026-10-20-wfd-sink-without-miraclecast.md): the
phone is the WFD source and runs the RTSP server; the laptop is the sink and
the RTSP client. Only the control path matters here: RTP packets are drained
and dropped.
"""

from __future__ import annotations

import contextlib
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

LOG = logging.getLogger(__name__)

RTSP_PORT = 7236
READ_TIMEOUT = 1.0
JOIN_TIMEOUT = 2.0
RTP_BUFFER = 2048
RECV_CHUNK = 4096

EventCallback = Callable[[str, dict[str, object]], None]


@dataclass
class Message:
    is_request: bool
    method: str = ""
    url: str = ""
    status: int = 0
    reason: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def cseq(self) -> str:
        return self.headers.get("cseq", "")

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def text_body(self) -> str:
        return self.body.decode("utf-8", "replace")


def parse_message(buffer: bytes) -> tuple[Message | None, bytes]:
    """Parse one RTSP message from ``buffer``.

    Returns ``(message, remaining_bytes)`` or ``(None, buffer)`` when the
    buffer does not contain a complete message yet.
    """
    end = buffer.find(b"\r\n\r\n")
    if end < 0:
        return None, buffer

    head = buffer[:end].decode("iso-8859-1")
    rest = buffer[end + 4:]
    lines = head.split("\r\n")
    first = lines[0]

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()

    try:
        content_length = int(headers.get("content-length", "0"))
    except ValueError:
        content_length = 0

    if len(rest) < content_length:
        return None, buffer

    body, rest = rest[:content_length], rest[content_length:]
    parts = first.split(" ", 2)

    if first.startswith("RTSP/"):
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        reason = parts[2] if len(parts) > 2 else ""
        return Message(False, status=status, reason=reason, headers=headers, body=body), rest

    method = parts[0] if parts else ""
    url = parts[1] if len(parts) > 1 else ""
    return Message(True, method=method, url=url, headers=headers, body=body), rest


def build_request(
    method: str,
    url: str,
    cseq: int,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> bytes:
    lines = [f"{method} {url} RTSP/1.0", f"CSeq: {cseq}"]
    for name, value in (headers or {}).items():
        lines.append(f"{name}: {value}")
    if body:
        lines.append("Content-Type: text/parameters")
        lines.append(f"Content-Length: {len(body)}")
    lines.append("")
    lines.append("")
    return "\r\n".join(lines).encode("iso-8859-1") + body


def build_response(
    cseq: str,
    status: int = 200,
    reason: str = "OK",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> bytes:
    lines = [f"RTSP/1.0 {status} {reason}", f"CSeq: {cseq}"]
    for name, value in (headers or {}).items():
        lines.append(f"{name}: {value}")
    if body:
        lines.append("Content-Type: text/parameters")
        lines.append(f"Content-Length: {len(body)}")
    lines.append("")
    lines.append("")
    return "\r\n".join(lines).encode("iso-8859-1") + body


class RtspConnection:
    """Buffered RTSP message reader/writer over a TCP socket."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buffer = b""

    def send(self, payload: bytes) -> None:
        self._sock.sendall(payload)

    def read(self, timeout: float) -> Message | None:
        while True:
            message, self._buffer = parse_message(self._buffer)
            if message is not None:
                return message

            self._sock.settimeout(max(timeout, 0.01))
            try:
                chunk = self._sock.recv(RECV_CHUNK)
            except socket.timeout:
                return None
            if not chunk:
                raise ConnectionError("RTSP connection closed by peer")
            self._buffer += chunk

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.shutdown(socket.SHUT_RDWR)
        self._sock.close()


def _parse_parameters(body: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        name, _, value = line.partition(":")
        params[name.strip().lower()] = value.strip()
    return params


class WfdSink:
    """Wi-Fi Display sink: connects to the phone's RTSP server and runs M1-M7."""

    # M4 capability payload. wfd_video_formats fields: native (1B),
    # preferred-display-mode-supported (1B), profiles (0x03 = CBP), levels
    # (0x10 = level 4.1), CEA / VESA / HH resolution bitmaps (4B each),
    # latency (1B), min-slice-size (2B), slice-enc-params (2B),
    # frame-rate-control-support (1B), max-hres (2B), max-vres (2B).
    VIDEO_FORMATS = "00 00 03 10 0001ffff 1fffffff 00001fff 00 0000 0000 10 none none"
    AUDIO_CODECS = "AAC 00000007 00"  # LPCM mode bitmap, no AAC modes

    def __init__(
        self,
        host: str,
        port: int = RTSP_PORT,
        on_event: EventCallback | None = None,
        connect_timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.on_event = on_event or (lambda name, data: None)
        self.connect_timeout = connect_timeout
        self.rtp_port = 0

        self._stop = threading.Event()
        self._conn: RtspConnection | None = None
        self._rtp_sock: socket.socket | None = None
        self._rtp_thread: threading.Thread | None = None
        self._cseq = 0
        self._sent_m2 = False
        self._setting_up = False
        self._session = ""
        self._presentation_url = ""

    # -- public API ---------------------------------------------------------

    def stop(self) -> None:
        # Do not close the connection here: the RTSP reader owns the socket
        # and will notice the flag on its next read timeout.  Closing it from
        # this thread races the reader and surfaces as EBADF.
        self._stop.set()

    def run(self) -> None:
        self._bind_rtp_socket()
        try:
            if self._stop.is_set():
                return
            sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._conn = RtspConnection(sock)
            LOG.info("RTSP connected to %s:%d (RTP port %d)", self.host, self.port, self.rtp_port)
            self.on_event("connected", {"peer": self.host, "rtp_port": self.rtp_port})
            self._loop()
        except (ConnectionError, OSError) as exc:
            if self._stop.is_set():
                LOG.debug("RTSP session closed")
            else:
                LOG.info("RTSP session ended: %s", exc)
        finally:
            self._cleanup()
            self.on_event("disconnected", {})

    # -- internals ----------------------------------------------------------

    def _bind_rtp_socket(self) -> None:
        self._rtp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rtp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._rtp_sock.bind(("0.0.0.0", 0))
        self._rtp_sock.settimeout(READ_TIMEOUT)
        self.rtp_port = int(self._rtp_sock.getsockname()[1])
        self._rtp_thread = threading.Thread(target=self._drain_rtp, daemon=True)
        self._rtp_thread.start()

    def _drain_rtp(self) -> None:
        sock = self._rtp_sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                sock.recvfrom(RTP_BUFFER)
            except socket.timeout:
                continue
            except OSError:
                break

    def _loop(self) -> None:
        assert self._conn is not None
        while not self._stop.is_set():
            message = self._conn.read(READ_TIMEOUT)
            if message is None:
                continue
            if message.is_request:
                self._handle_request(message)
            else:
                LOG.debug("unexpected RTSP response: %s %s", message.status, message.reason)

    def _next_cseq(self) -> int:
        self._cseq += 1
        return self._cseq

    def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 5.0,
    ) -> Message:
        assert self._conn is not None
        cseq = self._next_cseq()
        self._conn.send(build_request(method, url, cseq, headers))
        LOG.debug(">> %s %s (CSeq %d)", method, url, cseq)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._stop.is_set():
            message = self._conn.read(max(0.1, deadline - time.monotonic()))
            if message is None:
                continue
            if message.is_request:
                self._handle_request(message)
                continue
            if message.cseq == str(cseq):
                LOG.debug("<< %d %s (CSeq %s)", message.status, message.reason, message.cseq)
                return message
            LOG.debug("ignoring unmatched response CSeq %s", message.cseq)
        raise TimeoutError(f"no RTSP response to {method} within {timeout:.1f}s")

    def _reply(self, request: Message, headers: dict[str, str] | None = None, body: bytes = b"") -> None:
        assert self._conn is not None
        self._conn.send(build_response(request.cseq, headers=headers, body=body))
        LOG.debug(">> 200 OK (CSeq %s)", request.cseq)

    def _handle_request(self, message: Message) -> None:
        method = message.method.upper()
        if method == "OPTIONS":
            self._reply(message, {"Public": "org.wfa.wfd1.0, GET_PARAMETER, SET_PARAMETER"})
            if not self._sent_m2:
                self._sent_m2 = True
                self._request("OPTIONS", "*", {"Require": "org.wfa.wfd1.0"})
            return

        if method == "GET_PARAMETER":
            # M16 keepalive carries a Session header and expects a bare 200.
            # M3 (capability query) has no Session and expects the M4 body.
            if message.header("session"):
                self._reply(message)
                return
            body = (
                f"wfd_content_protection: none\r\n"
                f"wfd_video_formats: {self.VIDEO_FORMATS}\r\n"
                f"wfd_audio_codecs: {self.AUDIO_CODECS}\r\n"
                f"wfd_client_rtp_ports: RTP/AVP/UDP;unicast {self.rtp_port} 0 mode=play\r\n"
            ).encode("ascii")
            self._reply(message, body=body)
            return

        if method == "SET_PARAMETER":
            self._reply(message)
            self._handle_parameters(_parse_parameters(message.text_body()))
            return

        if method == "TEARDOWN":
            self._reply(message)
            self._stop.set()
            return

        LOG.debug("ignoring RTSP method %s", method)
        self._reply(message)

    def _handle_parameters(self, params: dict[str, str]) -> None:
        raw_url = params.get("wfd_presentation_url", "")
        # The source appends a secondary URL (" none") after the primary;
        # RTSP SETUP/PLAY must use only the first token.
        url = raw_url.split()[0] if raw_url else ""
        if url:
            self._presentation_url = url
            LOG.info("presentation URL: %s", url)

        if "wfd_video_formats" in params:
            self.on_event("video_formats", {"value": params["wfd_video_formats"]})

        trigger = params.get("wfd_trigger_method", "").upper()
        if trigger == "SETUP" and not self._setting_up:
            self._setting_up = True
            try:
                self._setup_and_play()
            finally:
                self._setting_up = False

    def _setup_and_play(self) -> None:
        if not self._presentation_url:
            LOG.warning("trigger SETUP without presentation URL; ignoring")
            return

        transport = f"RTP/AVP/UDP;unicast;client_port={self.rtp_port}"
        response = self._request("SETUP", self._presentation_url, {"Transport": transport})
        if response.status != 200:
            LOG.warning("SETUP failed: %d %s", response.status, response.reason)
            return

        self._session = response.header("session").split(";")[0]
        LOG.info("SETUP accepted, session %r", self._session)
        play = self._request("PLAY", self._presentation_url, {"Session": self._session})
        if play.status != 200:
            LOG.warning("PLAY failed: %d %s", play.status, play.reason)
            return
        LOG.info("streaming")
        self.on_event("streaming", {"session": self._session})

    def _cleanup(self) -> None:
        # Order matters: set the flag, let the RTP drain exit on its own
        # timeout, then close the sockets. Closing a socket under a blocked
        # recv() races the reader and surfaces as EBADF.
        self._stop.set()
        if self._rtp_thread is not None:
            if self._rtp_thread is not threading.current_thread():
                self._rtp_thread.join(timeout=JOIN_TIMEOUT)
            self._rtp_thread = None
        if self._rtp_sock is not None:
            with contextlib.suppress(OSError):
                self._rtp_sock.close()
            self._rtp_sock = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None
