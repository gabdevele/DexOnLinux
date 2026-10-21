# tests/helper/fake_helper.py
import json
import signal
import sys
import time

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
print(json.dumps({"event": "ready", "data": {"backend": "direct"}}), flush=True)
print(
    json.dumps(
        {
            "event": "group_started",
            "data": {
                "ifname": "p2p-test",
                "role": "client",
                "client_ip": "192.168.49.200",
                "client_prefix": 24,
                "go_ip": "192.168.49.1",
                "peer_mac": "aa:bb:cc:dd:ee:ff",
                "frequency": 2437,
            },
        }
    ),
    flush=True,
)
while True:
    time.sleep(1)
