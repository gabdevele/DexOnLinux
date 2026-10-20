"""Self-test for the wpa_supplicant control-interface client (no root).

Uses a fake Unix-datagram ctrl socket to check command replies and event
queueing, including events that arrive before a command's OK reply.
"""

from __future__ import annotations

import socket
import sys
import tempfile
import threading
from pathlib import Path

from supplicant import SupplicantControl, p2p_ctrl_name


def run_fake_ctrl(path: str, ready: threading.Event) -> None:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(path)
    ready.set()
    while True:
        try:
            data, address = server.recvfrom(4096)
        except OSError:
            return
        command = data.decode("utf-8", "replace")
        if command == "ATTACH":
            server.sendto(b"OK", address)
        elif command.startswith("WFD_SUBELEM_SET"):
            server.sendto(
                b"<3>P2P-GO-NEG-REQUEST aa:bb:cc:dd:ee:ff dev_passwd_id=4 go_intent=15",
                address,
            )
            server.sendto(b"OK", address)
        elif command.startswith("P2P_EXT_LISTEN"):
            server.sendto(
                b'<3>P2P-GROUP-STARTED p2p-test-0 client ssid="DIRECT-x" freq=2412 '
                b"go_dev_addr=aa:bb:cc:dd:ee:ff",
                address,
            )
            server.sendto(b"OK", address)
        else:
            server.sendto(b"FAIL", address)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / p2p_ctrl_name("wlp0s20f3"))
        ready = threading.Event()
        threading.Thread(target=run_fake_ctrl, args=(path, ready), daemon=True).start()
        ready.wait(2.0)

        ctrl = SupplicantControl("wlp0s20f3", ctrl_dir=tmp)
        ctrl.open()

        if ctrl.command("WFD_SUBELEM_SET all 00000600111c4400c8") != "OK":
            print("FAIL: WFD_SUBELEM_SET was not accepted")
            return 1
        events = ctrl.pump(0.2)
        if not any("P2P-GO-NEG-REQUEST" in line for line in events):
            print(f"FAIL: event sent before OK was lost: {events}")
            return 1

        if ctrl.command("P2P_EXT_LISTEN 200 1000") != "OK":
            print("FAIL: P2P_EXT_LISTEN was not accepted")
            return 1
        events = ctrl.pump(0.2)
        if not any("P2P-GROUP-STARTED" in line for line in events):
            print(f"FAIL: group event not parsed: {events}")
            return 1

        if ctrl.command("NOPE") != "FAIL":
            print("FAIL: rejection was not propagated")
            return 1

        ctrl.close()

    print("PASS: ctrl_iface replies and event queueing behave as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
