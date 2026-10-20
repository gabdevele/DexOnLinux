"""User-facing runner for the WFD spike.

Pre-authenticates sudo once, spawns the root helper, prints its events and
launches scrcpy when the DeX session is up.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPER = HERE / "linkd.py"


def log(message: str) -> None:
    print(f"[spike] {message}", flush=True)


def detect_interface() -> str:
    output = subprocess.run(["iw", "dev"], capture_output=True, text=True, check=True).stdout
    interfaces = re.findall(r"^Interface (\S+)$", output, flags=re.MULTILINE)
    if not interfaces:
        raise SystemExit("no wireless interface found; pass --interface")
    return interfaces[0]


def sudo_authenticate() -> None:
    password = getpass.getpass(f"[sudo] password for {getpass.getuser()}: ")
    result = subprocess.run(
        ["sudo", "-S", "-p", "", "-v"],
        input=password + "\n",
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit("sudo authentication failed")


def find_adb_serial(requested: str | None) -> str | None:
    result = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True, check=False)
    devices = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    if requested:
        return requested if requested in devices else None
    return devices[0] if devices else None


def list_displays(serial: str) -> list[tuple[int, str]]:
    result = subprocess.run(
        ["scrcpy", "-s", serial, "--list-displays"],
        capture_output=True,
        text=True,
        check=False,
    )
    displays = []
    for line in result.stdout.splitlines():
        match = re.search(r"--display-id=(\d+)(?:\s+\((.*?)\))?", line)
        if match:
            displays.append((int(match.group(1)), match.group(2) or ""))
    return displays


def pick_display(serial: str, preferred: int | None) -> int | None:
    if preferred is not None:
        return preferred

    displays = list_displays(serial)
    if not displays:
        log("no display reported by scrcpy; starting without --display-id")
        return None

    def landscape(item: tuple[int, str]) -> bool:
        match = re.search(r"(\d{3,5})x(\d{3,5})", item[1])
        return bool(match) and int(match.group(1)) > int(match.group(2))

    landscapes = [item for item in displays if landscape(item)]
    if len(landscapes) == 1:
        return landscapes[0][0]

    print()
    for display_id, description in displays:
        print(f"  {display_id}: {description}")
    choice = input("display id to mirror (empty = scrcpy default): ").strip()
    return int(choice) if choice.isdigit() else None


def launch_scrcpy(serial: str, display_id: int | None, fullscreen: bool) -> subprocess.Popen:
    command = ["scrcpy", "-s", serial, "--window-title", "DexOnLinux (spike)"]
    if display_id is not None:
        command.extend(["--display-id", str(display_id)])
    if fullscreen:
        command.append("--fullscreen")
    return subprocess.Popen(command)


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.helper: subprocess.Popen | None = None
        self.helper_pid: int | None = None
        self.scrcpy: subprocess.Popen | None = None
        self.stopping = threading.Event()

    # -- helper lifecycle ---------------------------------------------------

    def start_helper(self) -> None:
        command = [
            "sudo",
            "-n",
            sys.executable,
            str(HELPER),
            "--interface",
            self.args.interface,
            "--device-info",
            self.args.device_info,
        ]
        if self.args.debug:
            command.append("--debug")
        self.helper = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def stop_helper(self) -> None:
        if self.helper is None:
            return
        try:
            if self.helper.stdin is not None and not self.helper.stdin.closed:
                self.helper.stdin.write("shutdown\n")
                self.helper.stdin.flush()
                self.helper.stdin.close()
        except OSError:
            pass

        try:
            self.helper.wait(timeout=8.0)
        except subprocess.TimeoutExpired:
            self._sudo_kill("TERM")
            try:
                self.helper.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._sudo_kill("KILL")

    def _sudo_kill(self, signal_name: str) -> None:
        if self.helper_pid is not None:
            subprocess.run(
                ["sudo", "-n", "kill", f"-{signal_name}", str(self.helper_pid)],
                capture_output=True,
                check=False,
            )

    # -- scrcpy -------------------------------------------------------------

    def start_scrcpy(self) -> None:
        if self.args.no_scrcpy or self.scrcpy is not None:
            return

        serial = find_adb_serial(self.args.device)
        if serial is None:
            log("no authorized ADB device; start scrcpy manually when DeX is up")
            return

        display_id = pick_display(serial, self.args.display_id)
        log(f"starting scrcpy (device={serial}, display={display_id})")
        self.scrcpy = launch_scrcpy(serial, display_id, self.args.fullscreen)
        threading.Thread(target=self._monitor_scrcpy, daemon=True).start()

    def _monitor_scrcpy(self) -> None:
        assert self.scrcpy is not None
        self.scrcpy.wait()
        if not self.stopping.is_set():
            log("scrcpy closed; ending the session")
            self.stopping.set()
            self.stop_helper()

    def stop_scrcpy(self) -> None:
        if self.scrcpy is not None and self.scrcpy.poll() is None:
            self.scrcpy.terminate()
            try:
                self.scrcpy.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.scrcpy.kill()
        self.scrcpy = None

    # -- event loop ---------------------------------------------------------

    def run(self) -> int:
        self.start_helper()
        assert self.helper is not None and self.helper.stdout is not None
        try:
            for line in self.helper.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    log(f"helper: {line}")
                    continue
                self._handle_event(event.get("event", ""), event.get("data", {}))
                if self.stopping.is_set():
                    break
        except KeyboardInterrupt:
            log("interrupted")
        finally:
            self.stopping.set()
            self.stop_scrcpy()
            self.stop_helper()
        return 0

    def _handle_event(self, name: str, data: dict) -> None:
        if name == "ready":
            self.helper_pid = data.get("pid")
            log(f"helper ready on {data.get('interface')} (ctrl {data.get('ctrl')})")
            log("on the phone: Smart View / Cast -> select DexOnLinux -> connect")
        elif name == "go_negotiation_requested":
            log(f"phone is negotiating ({data.get('peer')})")
        elif name == "pin_required":
            pin = data.get("pin", "?")
            log("=" * 60)
            log(f"PIN RICHIESTO DAL TELEFONO: {pin}")
            log("Inseriscilo ORA nella schermata 'Connessione wireless'.")
            log("=" * 60)
            if shutil.which("notify-send"):
                subprocess.run(
                    [
                        "notify-send",
                        "-u",
                        "critical",
                        "-t",
                        "0",
                        "DexOnLinux: PIN",
                        f"Inserisci {pin} sul telefono",
                    ],
                    check=False,
                )
        elif name == "pin_entry_required":
            log("il telefono mostra un PIN: inseriscilo sul telefono (flusso non supportato)")
        elif name == "invitation_received":
            log("il telefono ha inviato un invito P2P; se fallisce, rimuovi DexOnLinux dai dispositivi salvati")
        elif name == "group_started":
            log(f"P2P group up ({data.get('ifname')}, role {data.get('role')})")
        elif name == "ip_acquired":
            log(f"address {data.get('ip')}/{data.get('prefix')} on {data.get('ifname')}; phone {data.get('router')}")
        elif name == "session_connected":
            log("RTSP connected to the phone")
        elif name == "video_formats":
            log(f"negotiated video formats: {data.get('value')}")
        elif name == "streaming":
            log("DeX session established")
            self.start_scrcpy()
        elif name in {"session_disconnected", "group_finished"}:
            log("DeX session ended")
            self.stop_scrcpy()
        elif name == "formation_failure":
            log(f"P2P formation failed: {data.get('reason')}")
        elif name == "error":
            log(f"ERROR ({data.get('code')}): {data.get('message')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DexOnLinux WFD sink spike")
    parser.add_argument("--interface", help="wireless interface (default: first from iw dev)")
    parser.add_argument("--device", help="ADB serial for scrcpy")
    parser.add_argument("--display-id", type=int, help="scrcpy display id")
    parser.add_argument("--fullscreen", action="store_true", help="start scrcpy fullscreen")
    parser.add_argument("--no-scrcpy", action="store_true", help="do not start scrcpy automatically")
    parser.add_argument("--device-info", default="0011", help="WFD Device Info bitmap (hex)")
    parser.add_argument("--debug", action="store_true", help="verbose helper logs")
    args = parser.parse_args(argv)

    if not HELPER.exists():
        raise SystemExit(f"helper not found: {HELPER}")
    if not args.interface:
        args.interface = detect_interface()

    log(f"using interface {args.interface}")
    sudo_authenticate()

    return Runner(args).run()


if __name__ == "__main__":
    sys.exit(main())
