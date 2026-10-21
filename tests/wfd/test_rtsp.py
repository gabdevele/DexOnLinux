import socket
import threading
import time

from dexonlinux.wfd.rtsp import (
    Message,
    WfdSink,
    build_request,
    build_response,
    parse_message,
)

from .fake_source import FakeSource

BODY = (
    b"wfd_video_formats: 00 00 03 10 0001ffff 1fffffff 00001fff 00 0000 0000 10 none none\r\n"
    b"wfd_audio_codecs: AAC 00000007 00\r\n"
)


def test_fragmented_response_parses():
    raw = build_response("1", 200, "OK", {"Session": "12345678"}, BODY)
    for split in range(1, len(raw)):
        message, rest = parse_message(raw[:split])
        assert message is None and rest == raw[:split]
        message, rest = parse_message(raw[:split] + raw[split:])
        assert isinstance(message, Message)
        assert message.status == 200 and message.cseq == "1"
        assert message.header("session") == "12345678"
        assert message.body == BODY
        assert rest == b""


def test_coalesced_messages_parse():
    first = build_response("1", 200, "OK")
    second = build_request("GET_PARAMETER", "rtsp://192.168.49.1/wfd1.0/streamid=0", 2)
    message, rest = parse_message(first + second)
    assert message is not None and message.status == 200 and message.cseq == "1"
    message2, rest = parse_message(rest)
    assert message2 is not None and message2.is_request
    assert message2.method == "GET_PARAMETER"
    assert message2.cseq == "2"
    assert rest == b""


def test_request_with_body_parses():
    raw = build_request(
        "SET_PARAMETER",
        "rtsp://192.168.49.1/wfd1.0/streamid=0",
        3,
        body=b"wfd_idr_request\r\n",
    )
    message, rest = parse_message(raw)
    assert message is not None and message.is_request
    assert message.method == "SET_PARAMETER"
    assert message.body == b"wfd_idr_request\r\n"
    assert rest == b""


def test_full_handshake_keepalive_and_teardown():
    source = FakeSource()
    server_thread = threading.Thread(target=source.serve, daemon=True)
    server_thread.start()

    events = []
    sink = WfdSink(
        source.host,
        source.port,
        on_event=lambda name, data: events.append((name, data)),
    )
    sink.run()
    server_thread.join(timeout=5)

    assert source.error is None
    assert [name for name, _ in events] == ["connected", "video_formats", "streaming", "disconnected"]
    assert "wfd_content_protection: none" in source.m4_body
    assert "wfd_video_formats:" in source.m4_body
    assert "wfd_audio_codecs: AAC" in source.m4_body
    assert f"wfd_client_rtp_ports: RTP/AVP/UDP;unicast {sink.rtp_port} 0 mode=play" in source.m4_body

    setup = next(m for m in source.received if m.is_request and m.method.upper() == "SETUP")
    assert setup.url == "rtsp://127.0.0.1/wfd1.0/streamid=0"
    assert f"client_port={sink.rtp_port}" in setup.header("transport")
    assert "rtcp" not in setup.header("transport").lower()
    assert any(m.is_request and m.method.upper() == "PLAY" for m in source.received)
    assert source.keepalive_reply is not None and source.keepalive_reply.body == b""


def test_tcp_close_ends_session_cleanly():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    host, port = server.getsockname()

    def close_client():
        client, _ = server.accept()
        time.sleep(0.05)
        client.close()
        server.close()

    thread = threading.Thread(target=close_client, daemon=True)
    thread.start()
    events = []
    sink = WfdSink(host, port, on_event=lambda name, data: events.append((name, data)))
    sink.run()
    thread.join(timeout=2)
    assert [name for name, _ in events] == ["connected", "disconnected"]
