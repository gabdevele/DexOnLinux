"""Root helper for the WFD spike: supplicant P2P + DHCP + RTSP sink.

Protocol: newline-delimited JSON on stdout, human logs on stderr. The helper
is spawned by ``run.py`` via sudo for one session and restores the supplicant
settings it touches on exit.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time

import dhcp
from rtsp import WfdSink
from supplicant import SupplicantControl, SupplicantError

LOG = logging.getLogger("spike.linkd")

RTSP_PORT = 7236
WFD_THROUGHPUT = 0x00C8  # 100 Mbps
ANDROID_GO_IP = "192.168.49.1"
ANDROID_CLIENT_IP = "192.168.49.2"
DEFAULT_GO_INTENT = 7

GO_NEG_RE = re.compile(
    r"P2P-GO-NEG-REQUEST (?P<mac>[0-9a-fA-F:]{17})"
    r"(?: dev_passwd_id=(?P<passwd>\d+))?"
    r"(?: go_intent=(?P<intent>\d+))?"
)

WFD_SINK_BITS = 0x0011  # primary sink (bits 0-1 = 01) + session available (bits 4-5)


class Emitter:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def emit(self, event: str, **data: object) -> None:
        payload = json.dumps({"event": event, "data": data}, ensure_ascii=False)
        with self._lock:
            print(payload, flush=True)


class Linkd:
    def __init__(self, interface: str, device_info: int, emitter: Emitter) -> None:
        self.interface = interface
        self.device_info = device_info
        self.emitter = emitter
        self.ctrl = SupplicantControl(interface)
        self.stop_event = threading.Event()

        self.group_ifname = ""
        self.group_role = ""
        self._pending_connect_until = 0.0
        self._session_thread: threading.Thread | None = None
        self._sink: WfdSink | None = None
        self._lock = threading.Lock()

    # -- setup / teardown ---------------------------------------------------

    def run(self) -> int:
        self.ctrl.open()
        self.emitter.emit(
            "ready",
            interface=self.interface,
            ctrl=self.ctrl.path,
            wfd_ies=self._wfd_ies_hex(),
            go_intent=0,
            pid=os.getpid(),
        )
        try:
            self._apply_settings()
            self._loop()
        except SupplicantError as exc:
            self.emitter.emit("error", code="supplicant", message=str(exc))
            LOG.error("%s", exc)
            return 1
        finally:
            self._cleanup()
        return 0

    def _wfd_ies_hex(self) -> str:
        return f"000006{self.device_info:04x}{RTSP_PORT:04x}{WFD_THROUGHPUT:04x}"

    def _apply_settings(self) -> None:
        # Required: without WFD subelements the phone cannot see a sink.
        if not self._cmd_ok(f"WFD_SUBELEM_SET all {self._wfd_ies_hex()}", required=True):
            return
        self._cmd_ok("SET p2p_go_intent 0")
        self._cmd_ok("SET device_name DexOnLinux")
        self._cmd_ok("P2P_EXT_LISTEN 200 1000")
        LOG.info("advertising as WFD sink (IEs %s)", self._wfd_ies_hex())

    def _cleanup(self) -> None:
        LOG.info("cleaning up")
        with self._lock:
            sink = self._sink
        if sink is not None:
            sink.stop()
        if self._session_thread is not None:
            self._session_thread.join(timeout=2.0)
        if self.group_ifname:
            self._cmd_ok(f"P2P_GROUP_REMOVE {self.group_ifname}")
        self._cmd_ok("P2P_EXT_LISTEN")  # no args = disable
        self._cmd_ok("WFD_SUBELEM_SET 0 ")  # clear Device Info subelement
        self._cmd_ok(f"SET p2p_go_intent {DEFAULT_GO_INTENT}")
        self.ctrl.close()

    # -- main loop ----------------------------------------------------------

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            for line in self.ctrl.pump(0.5):
                self._handle_event(line)

    def _handle_event(self, line: str) -> None:
        if line.startswith("P2P-GO-NEG-REQUEST "):
            self._handle_go_negotiation(line)
            return

        if line.startswith("P2P-GROUP-STARTED "):
            self._handle_group_started(line)
            return

        if line.startswith("P2P-GROUP-REMOVED "):
            LOG.info("group removed: %s", line)
            self.emitter.emit("group_finished", reason=line)
            self.group_ifname = ""
            self.group_role = ""
            return

        if line.startswith(("P2P-GO-NEG-FAILURE", "P2P-GROUP-FORMATION-FAILURE")):
            LOG.warning("%s", line)
            self.emitter.emit("formation_failure", reason=line)
            self._pending_connect_until = 0.0
            return

        LOG.debug("event: %s", line)

    def _handle_go_negotiation(self, line: str) -> None:
        match = GO_NEG_RE.search(line)
        if match is None:
            LOG.warning("unparsed GO negotiation event: %s", line)
            return

        mac = match.group("mac")
        passwd = int(match.group("passwd") or 0)
        intent = int(match.group("intent") or 0)
        LOG.info("GO negotiation request from %s (passwd_id=%d intent=%d)", mac, passwd, intent)
        self.emitter.emit("go_negotiation_requested", peer=mac, passwd_id=passwd, intent=intent)

        if self.group_ifname:
            LOG.info("group already active; ignoring negotiation request")
            return
        if time.monotonic() < self._pending_connect_until:
            LOG.info("connect already pending; ignoring duplicate request")
            return
        if passwd != 0:
            # 4 == PBC; anything else would need a PIN flow (out of spike scope).
            LOG.warning("unsupported WPS method id %d; trying PBC anyway", passwd)

        self._pending_connect_until = time.monotonic() + 60.0
        self._cmd_ok(f"P2P_CONNECT {mac} pbc go_intent=0")

    def _handle_group_started(self, line: str) -> None:
        match = re.match(r"P2P-GROUP-STARTED (\S+) (\S+)", line)
        if match is None:
            LOG.warning("unparsed group event: %s", line)
            return

        ifname, role = match.group(1), match.group(2).lower()
        self.group_ifname, self.group_role = ifname, role
        self._pending_connect_until = 0.0
        LOG.info("group started: ifname=%s role=%s", ifname, role)
        self.emitter.emit("group_started", ifname=ifname, role=role)

        if role != "client":
            LOG.error("phone negotiated us as GO; this spike only supports the client role")
            self.emitter.emit("error", code="go-role", message="sink became GO; client role required")
            return

        self._session_thread = threading.Thread(target=self._run_session, args=(ifname,), daemon=True)
        self._session_thread.start()

    # -- WFD session --------------------------------------------------------

    def _run_session(self, ifname: str) -> None:
        peer_ip = self._configure_address(ifname)
        deadline = time.monotonic() + 25.0
        while not self.stop_event.is_set() and time.monotonic() < deadline:
            sink = WfdSink(peer_ip, RTSP_PORT, on_event=self._on_wfd_event)
            with self._lock:
                self._sink = sink
            try:
                sink.run()
                with self._lock:
                    self._sink = None
                return
            except OSError as exc:
                LOG.info("RTSP connect to %s:%d failed (%s); retrying", peer_ip, RTSP_PORT, exc)
                time.sleep(1.0)
            finally:
                with self._lock:
                    self._sink = None

        self.emitter.emit("error", code="rtsp-unreachable", message=f"could not reach {peer_ip}:{RTSP_PORT}")

    def _configure_address(self, ifname: str) -> str:
        try:
            lease = dhcp.acquire(ifname, timeout=12.0)
            peer_ip = lease.router or ANDROID_GO_IP
            self.emitter.emit(
                "ip_acquired",
                ifname=ifname,
                ip=lease.ip,
                prefix=lease.prefix,
                router=peer_ip,
                source="dhcp",
            )
            return peer_ip
        except (dhcp.DhcpError, OSError, subprocess.SubprocessError) as exc:
            LOG.warning("DHCP failed on %s (%s); trying static defaults", ifname, exc)
            subprocess.run(
                ["ip", "addr", "replace", f"{ANDROID_CLIENT_IP}/24", "dev", ifname],
                check=False,
                capture_output=True,
            )
            self.emitter.emit(
                "ip_acquired",
                ifname=ifname,
                ip=ANDROID_CLIENT_IP,
                prefix=24,
                router=ANDROID_GO_IP,
                source="static",
            )
            return ANDROID_GO_IP

    def _on_wfd_event(self, name: str, data: dict) -> None:
        if name == "connected":
            self.emitter.emit("session_connected", **data)
        elif name == "video_formats":
            self.emitter.emit("video_formats", **data)
        elif name == "streaming":
            self.emitter.emit("streaming", **data)
        elif name == "disconnected":
            self.emitter.emit("session_disconnected", **data)

    # -- helpers ------------------------------------------------------------

    def _cmd_ok(self, command: str, required: bool = False) -> bool:
        try:
            reply = self.ctrl.command(command)
        except SupplicantError as exc:
            LOG.warning("%s failed: %s", command, exc)
            if required:
                raise
            return False
        if reply.strip() != "OK":
            LOG.warning("%s -> %s", command, reply.strip())
            if required:
                raise SupplicantError(f"{command} rejected: {reply.strip()}")
            return False
        return True

    def stop(self) -> None:
        self.stop_event.set()


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="DexOnLinux WFD sink spike helper (root)")
    parser.add_argument("--interface", required=True)
    parser.add_argument("--device-info", default=f"{WFD_SINK_BITS:04x}", help="WFD Device Info bitmap (hex)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    emitter = Emitter()
    linkd = Linkd(args.interface, int(args.device_info, 16), emitter)

    def _stop(_signum, _frame):
        linkd.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    threading.Thread(target=_watch_stdin, args=(linkd,), daemon=True).start()
    return linkd.run()


def _watch_stdin(linkd: Linkd) -> None:
    """Stop when the parent closes stdin (or asks explicitly)."""
    for line in sys.stdin:
        if line.strip().lower() in {"shutdown", "quit", "exit"}:
            linkd.stop()
            return
    linkd.stop()


if __name__ == "__main__":
    sys.exit(main())
