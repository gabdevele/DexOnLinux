"""Offline tests for RTSP framing (partial reads and coalesced messages)."""

from __future__ import annotations

import sys

from rtsp import Message, build_request, build_response, parse_message

BODY = (
    b"wfd_video_formats: 00 00 03 10 0001ffff 1fffffff 00001fff 00 0000 0000 10 none none\r\n"
    b"wfd_audio_codecs: AAC 00000007 00\r\n"
)


def test_fragmented_response() -> None:
    raw = build_response("1", 200, "OK", {"Session": "12345678"}, BODY)
    for split in range(1, len(raw)):
        message, rest = parse_message(raw[:split])
        assert message is None and rest == raw[:split], f"parsed incomplete prefix at {split}"
        message, rest = parse_message(raw[:split] + raw[split:])
        assert isinstance(message, Message)
        assert message.status == 200 and message.cseq == "1"
        assert message.header("session") == "12345678"
        assert message.body == BODY
        assert rest == b""
    print("PASS: an RTSP response parses from every byte split")


def test_coalesced_messages() -> None:
    first = build_response("1", 200, "OK")
    second = build_request("GET_PARAMETER", "rtsp://192.168.49.1/wfd1.0/streamid=0", 2)
    message, rest = parse_message(first + second)
    assert message is not None and message.status == 200 and message.cseq == "1"
    message2, rest = parse_message(rest)
    assert message2 is not None and message2.is_request
    assert message2.method == "GET_PARAMETER"
    assert message2.cseq == "2"
    assert rest == b""
    print("PASS: two RTSP messages in one buffer are split correctly")


def test_request_with_body() -> None:
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
    print("PASS: request bodies and Content-Length are handled")


def main() -> int:
    test_fragmented_response()
    test_coalesced_messages()
    test_request_with_body()
    print("PASS: all RTSP framing tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
