"""Self-test for the WFD sink RTSP state machine (no phone required).

Runs ``rtsp.WfdSink`` against ``fake_source.FakeSource`` and checks that the
full M1-M7 handshake, keepalive and teardown complete cleanly.
"""

from __future__ import annotations

import logging
import sys
import threading

from fake_source import FakeSource
from rtsp import WfdSink


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    source = FakeSource()
    thread = threading.Thread(target=source.serve, daemon=True)
    thread.start()

    events = []
    sink = WfdSink(source.host, source.port, on_event=lambda name, data: events.append((name, data)))
    sink.run()
    thread.join(timeout=5)

    if source.error is not None:
        print(f"FAIL: fake source error: {source.error!r}")
        return 1

    names = [name for name, _ in events]
    expected = ["connected", "video_formats", "streaming", "disconnected"]
    if names != expected:
        print(f"FAIL: unexpected event sequence: {names} (expected {expected})")
        return 1

    m4 = source.m4_body
    required = [
        "wfd_content_protection: none",
        "wfd_video_formats:",
        "wfd_audio_codecs: AAC",
        f"wfd_client_rtp_ports: RTP/AVP/UDP;unicast {sink.rtp_port} 0 mode=play",
    ]
    missing = [item for item in required if item not in m4]
    if missing:
        print(f"FAIL: M4 body missing {missing!r}\n--- M4 ---\n{m4}")
        return 1

    if not any(m.is_request and m.method.upper() == "SETUP" for m in source.received):
        print("FAIL: sink never sent SETUP")
        return 1
    if not any(m.is_request and m.method.upper() == "PLAY" for m in source.received):
        print("FAIL: sink never sent PLAY")
        return 1

    print("PASS: M1-M7 handshake, keepalive and teardown completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
