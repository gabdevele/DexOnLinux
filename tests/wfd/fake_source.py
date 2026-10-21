"""Fake Wi-Fi Display source used to exercise the sink without a phone.

Implements just enough of the AOSP source state machine to drive
``dexonlinux.wfd.rtsp.WfdSink`` through M1-M7, keepalives and teardown.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable

from dexonlinux.wfd.rtsp import Message, RtspConnection, build_request, build_response

M4B_BODY = (
    "wfd_video_formats: 00 00 02 10 00000001 00000000 00000000 00 0000 0000 00 0000 0000\r\n"
    "wfd_audio_codecs: AAC 00000001 00\r\n"
    "wfd_presentation_URL: rtsp://127.0.0.1/wfd1.0/streamid=0 none\r\n"
    "wfd_client_rtp_ports: RTP/AVP/UDP;unicast 5000 0 mode=play\r\n"
)


class FakeSource:
    """Drives one sink connection through the full WFD handshake."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((host, port))
        self._server.listen(1)
        self.host, self.port = self._server.getsockname()
        self.session_id = "12345678"
        self.received: list[Message] = []
        self.m4_body = ""
        self.keepalive_reply: Message | None = None
        self.error: BaseException | None = None
        self._pending: list[Message] = []
        self._done = threading.Event()

    def close(self) -> None:
        try:
            self._server.close()
        except OSError:
            pass

    def wait(self, timeout: float = 15.0) -> None:
        self._done.wait(timeout)

    # -- internals ----------------------------------------------------------

    def _send(
        self,
        conn: RtspConnection,
        method: str,
        url: str,
        headers=None,
        body: bytes = b"",
    ) -> int:
        self._cseq += 1
        conn.send(build_request(method, url, self._cseq, headers, body))
        return self._cseq

    def _auto_reply(self, conn: RtspConnection, message: Message) -> None:
        headers = {}
        if message.method.upper() == "SETUP":
            headers["Session"] = f"{self.session_id};timeout=30"
        conn.send(build_response(message.cseq, headers=headers))

    def _wait(
        self,
        conn: RtspConnection,
        predicate: Callable[[Message], bool],
        timeout: float = 10.0,
    ) -> Message:
        for index, message in enumerate(self._pending):
            if predicate(message):
                return self._pending.pop(index)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = conn.read(max(0.1, deadline - time.monotonic()))
            if message is None:
                continue
            self.received.append(message)
            if message.is_request:
                self._auto_reply(conn, message)
            if predicate(message):
                return message
            self._pending.append(message)
        raise TimeoutError("fake source timed out waiting for sink message")

    def _send_and_wait_reply(self, conn, method, url, headers=None, body=b"") -> Message:
        cseq = self._send(conn, method, url, headers, body)
        return self._wait(conn, lambda m: not m.is_request and m.cseq == str(cseq))

    # -- scenario -----------------------------------------------------------

    def serve(self) -> None:
        self._cseq = 0
        try:
            client, _ = self._server.accept()
            client.settimeout(10.0)
            conn = RtspConnection(client)
            try:
                self._run_scenario(conn)
            finally:
                conn.close()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the self-test
            self.error = exc
        finally:
            self._done.set()
            self.close()

    def _run_scenario(self, conn: RtspConnection) -> None:
        # M1: source -> sink OPTIONS
        self._send_and_wait_reply(
            conn,
            "OPTIONS",
            "*",
            {"Require": "org.wfa.wfd1.0"},
        )

        # M2: sink -> source OPTIONS (auto-replied by _wait when it arrives)
        self._wait(conn, lambda m: m.is_request and m.method.upper() == "OPTIONS")

        # M3: source -> sink GET_PARAMETER; the sink answers with M4 capabilities
        m3 = self._send_and_wait_reply(
            conn,
            "GET_PARAMETER",
            "rtsp://localhost/wfd1.0",
            body=(
                "wfd_content_protection\r\nwfd_video_formats\r\n"
                "wfd_audio_codecs\r\nwfd_client_rtp_ports\r\n"
            ).encode("ascii"),
        )
        self.m4_body = m3.text_body()

        # M4b: chosen formats + presentation URL
        self._send_and_wait_reply(conn, "SET_PARAMETER", "rtsp://localhost/wfd1.0", body=M4B_BODY.encode("ascii"))

        # M5: trigger SETUP
        self._send_and_wait_reply(
            conn,
            "SET_PARAMETER",
            "rtsp://localhost/wfd1.0",
            body=b"wfd_trigger_method: SETUP\r\n",
        )

        setup = self._wait(conn, lambda m: m.is_request and m.method.upper() == "SETUP")
        assert "client_port=" in setup.header("transport"), "SETUP without client_port"

        play = self._wait(conn, lambda m: m.is_request and m.method.upper() == "PLAY")
        assert play.header("session").startswith(self.session_id), "PLAY without session"

        # M16 keepalive
        self.keepalive_reply = self._send_and_wait_reply(
            conn,
            "GET_PARAMETER",
            "rtsp://localhost/wfd1.0",
            {"Session": self.session_id},
        )

        # Teardown
        self._send_and_wait_reply(conn, "TEARDOWN", "rtsp://localhost/wfd1.0", {"Session": self.session_id})
