import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from dexonlinux.models import AdbDevice, ScrcpyDisplay, SinkctlEvent
from dexonlinux.network import InterfaceInfo, NmcliDevice
from dexonlinux.utils import get_asset_path, get_logger

logger = get_logger()


class CommandError(RuntimeError):
    pass


class Commands:
    BASE_REQUIRED_COMMANDS = ["sudo", "miracle-wifid", "miracle-sinkctl", "scrcpy", "adb", "iw"]
    NETWORK_SERVICES = ["NetworkManager", "wpa_supplicant"]
    SUDO_PREFIX = ["sudo", "-S", "-p", ""]
    SUDO_NON_INTERACTIVE_PREFIX = ["sudo", "-n"]

    def __init__(self, sudo_password, validate=True, network_mode="auto"):
        self.sudo_password = sudo_password + "\n" if sudo_password else ""
        self._sinkctl_master_fd = None
        self.network_mode = network_mode
        if validate:
            self.validate_environment()

    def validate_environment(self):
        missing = self.missing_dependencies()
        if missing:
            raise CommandError("Missing dependencies: " + ", ".join(missing))
        if not self.check_sudo_password():
            raise CommandError("Incorrect sudo password.")

    def missing_dependencies(self):
        commands = list(self.BASE_REQUIRED_COMMANDS)
        if self.network_mode in ("auto", "scoped"):
            commands.append("nmcli")
            commands.append("ip")
            commands.append("systemctl")
        if self.network_mode == "full-stop":
            commands.append("systemctl")
        return [cmd for cmd in commands if shutil.which(cmd) is None]

    def _build_command(self, command, sudo=False):
        return [*self.SUDO_PREFIX, *command] if sudo else list(command)

    def _build_non_interactive_sudo_command(self, command):
        return [*self.SUDO_NON_INTERACTIVE_PREFIX, *command]

    def _run_command(self, command, *, sudo=False, env=None):
        return subprocess.run(
            self._build_command(command, sudo=sudo),
            input=self.sudo_password if sudo else None,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def _run_checked(self, command, *, sudo=False, env=None):
        result = self._run_command(command, sudo=sudo, env=env)
        if result.returncode != 0:
            output = self._combine_output(result).strip()
            raise CommandError(output or f"Command failed: {' '.join(command)}")
        return result

    def _start_sudo_background_process(self, command, *, stdout, stderr, env=None):
        process = subprocess.Popen(
            self._build_command(command, sudo=True),
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            text=True,
            env=env,
        )
        if process.stdin:
            process.stdin.write(self.sudo_password)
            process.stdin.flush()
        return process

    def _combine_output(self, result):
        return (result.stdout or "") + (result.stderr or "")

    def _close_sinkctl_fd(self):
        if self._sinkctl_master_fd is None:
            return
        try:
            os.close(self._sinkctl_master_fd)
        except OSError:
            pass
        self._sinkctl_master_fd = None

    def _write_sinkctl_commands(self, master_fd, commands, delay=0.1):
        for command in commands:
            os.write(master_fd, f"{command}\n".encode())
            time.sleep(delay)
            logger.debug("Sent command to miracle-sinkctl: %s", command)

    def check_sudo_password(self):
        try:
            result = self._run_command(["-k", "true"], sudo=True)
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def disable_network_services(self):
        self._run_checked(["systemctl", "stop", *self.NETWORK_SERVICES], sudo=True)
        logger.info("Network services disabled.")

    def enable_network_services(self):
        self._run_checked(["systemctl", "start", *self.NETWORK_SERVICES], sudo=True)
        logger.info("Network services restored.")

    def is_service_active(self, service):
        result = self._run_command(["systemctl", "is-active", service])
        return result.stdout.strip() == "active"

    def stop_wpa_supplicant(self):
        self._run_checked(["systemctl", "stop", "wpa_supplicant"], sudo=True)
        logger.info("wpa_supplicant stopped.")

    def start_wpa_supplicant(self):
        self._run_checked(["systemctl", "start", "wpa_supplicant"], sudo=True)
        logger.info("wpa_supplicant restored.")

    def start_miracle_wifi(self, interface):
        command = ["miracle-wifid", "--interface", interface]
        process = self._start_sudo_background_process(command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        time.sleep(0.2)
        if process.poll() is not None:
            raise CommandError("miracle-wifid exited immediately after startup.")
        logger.info("miracle-wifid started on %s.", interface)
        return process

    def start_miracle_sinkctl(self, interface_index, port, event_callback=None):
        if interface_index is None:
            raise CommandError("No Miraclecast interface index available.")

        sinkctl_commands = [f"set-managed {interface_index} yes", f"run {interface_index}"]
        command = ["miracle-sinkctl", "--external-player", "true", "--port", str(port), "--audio", "1"]

        master_fd, slave_fd = os.openpty()
        try:
            process = subprocess.Popen(
                self._build_non_interactive_sudo_command(command),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
            )
        finally:
            os.close(slave_fd)

        self._sinkctl_master_fd = master_fd
        threading.Thread(target=self._drain_sinkctl_output, args=(master_fd, event_callback), daemon=True).start()

        time.sleep(0.2)
        self._write_sinkctl_commands(master_fd, sinkctl_commands)

        if process.poll() is not None:
            self._close_sinkctl_fd()
            raise CommandError("miracle-sinkctl exited immediately after startup.")

        logger.info("miracle-sinkctl started on UDP port %s.", port)
        return process

    def get_available_udp_port(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.bind(("", 0))
                return int(s.getsockname()[1])
        except OSError as exc:
            raise CommandError(f"Could not find an available UDP port: {exc}") from exc

    def get_p2p_interfaces(self):
        p2pwifi = []
        base_dir = Path("/sys/class/net")

        for iface_path in base_dir.iterdir():
            if not (iface_path / "wireless").exists():
                continue
            iface = iface_path.name
            try:
                iw_dev_result = self._run_command(["iw", "dev", iface, "info"])
                if iw_dev_result.returncode != 0:
                    continue

                iw_dev = self._combine_output(iw_dev_result)
                wiphy_line = next((l for l in iw_dev.splitlines() if l.strip().startswith("wiphy")), None)
                if not wiphy_line:
                    continue

                idxphy = wiphy_line.split()[1]
                iw_phy_result = self._run_command(["iw", "phy", f"phy{idxphy}", "info"])
                if iw_phy_result.returncode != 0:
                    continue

                if "P2P" in self._combine_output(iw_phy_result):
                    p2pwifi.append(iface)
            except Exception as exc:
                logger.debug("Unable to inspect interface %s: %s", iface, exc)
        return p2pwifi

    def get_interface_infos(self):
        devices = {device.device: device for device in self.get_nmcli_devices()}
        return [InterfaceInfo(iface, devices.get(iface)) for iface in self.get_p2p_interfaces()]

    def get_nmcli_devices(self):
        result = self._run_checked(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
        return self.parse_nmcli_devices(self._combine_output(result))

    def get_nmcli_device(self, interface):
        for device in self.get_nmcli_devices():
            if device.device == interface:
                return device
        return NmcliDevice(interface)

    def get_active_connection_uuid(self, interface):
        result = self._run_checked(["nmcli", "-t", "-f", "UUID,TYPE,DEVICE", "connection", "show", "--active"])
        for line in self._combine_output(result).splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[1] == "802-11-wireless" and parts[2] == interface:
                return parts[0]
        return ""

    def get_default_route_interfaces(self):
        result = self._run_checked(["ip", "route", "show", "default"])
        interfaces = []
        for line in self._combine_output(result).splitlines():
            parts = line.split()
            if "dev" not in parts:
                continue
            index = parts.index("dev")
            if index + 1 < len(parts):
                interfaces.append(parts[index + 1])
        return interfaces

    @staticmethod
    def parse_nmcli_devices(output):
        devices = []
        for line in output.splitlines():
            if not line.strip():
                continue
            parts = line.split(":", 3)
            while len(parts) < 4:
                parts.append("")
            devices.append(NmcliDevice(parts[0], parts[1], parts[2], parts[3].replace("\\:", ":")))
        return devices

    def nmcli_disconnect(self, interface):
        self._run_checked(["nmcli", "device", "disconnect", interface], sudo=True)
        logger.info("Disconnected %s from NetworkManager.", interface)

    def nmcli_connect(self, interface):
        self._run_checked(["nmcli", "device", "connect", interface])

    def nmcli_connection_up(self, connection_uuid, interface):
        self._run_checked(["nmcli", "connection", "up", "uuid", connection_uuid, "ifname", interface])

    def nmcli_set_managed(self, interface, managed):
        value = "yes" if managed else "no"
        self._run_checked(["nmcli", "device", "set", interface, "managed", value], sudo=True)

    def ip_link_up(self, interface):
        self._run_checked(["ip", "link", "set", "dev", interface, "up"], sudo=True)
        logger.debug("Brought %s up for Miraclecast.", interface)

    def get_interface_index(self, interface):
        try:
            return int((Path("/sys/class/net") / interface / "ifindex").read_text().strip())
        except OSError as exc:
            raise CommandError(f"Could not read interface index for {interface}: {exc}") from exc

    def list_adb_devices(self):
        result = self._run_checked(["adb", "devices", "-l"])
        return self.parse_adb_devices(self._combine_output(result))

    @staticmethod
    def parse_adb_devices(output):
        devices = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue
            if line.startswith("*") or "daemon" in line.lower():
                continue
            parts = line.split(maxsplit=2)
            if len(parts) < 2:
                continue
            description = parts[2] if len(parts) > 2 else ""
            devices.append(AdbDevice(serial=parts[0], state=parts[1], description=description))
        return devices

    def list_scrcpy_displays(self, selected_device):
        result = self._run_checked(["scrcpy", "-s", selected_device, "--list-displays"])
        return self.parse_scrcpy_displays(self._combine_output(result))

    @staticmethod
    def parse_scrcpy_displays(output):
        displays = []
        pattern = re.compile(r"--display-id=(\d+)(?:\s+\((.*?)\))?")
        resolution_pattern = re.compile(r"(\d{3,5})x(\d{3,5})")
        for line in output.splitlines():
            match = pattern.search(line)
            if not match:
                continue
            description = match.group(2) or line.strip()
            resolution = resolution_pattern.search(description)
            width = int(resolution.group(1)) if resolution else None
            height = int(resolution.group(2)) if resolution else None
            displays.append(ScrcpyDisplay(int(match.group(1)), description, width, height))
        return displays

    @staticmethod
    def parse_sinkctl_events(text):
        events = []
        clean_text = re.sub(r"\x1b\[[0-9;]*m", "", text.replace("\r", "\n")) #remove ANSI color codes
        
        resolution = re.search(r"SINK set resolution\s+(\d{3,5})x(\d{3,5})", clean_text)
        if resolution:
            width = int(resolution.group(1))
            height = int(resolution.group(2))
            events.append(SinkctlEvent("resolution", width=width, height=height, resolution=f"{width}x{height}"))

        PEER_CONNECTED_STRINGS = [
            "[CONNECT] Peer:",
            "now running on peer",
        ]
        DISCONNECTED_STRINGS = [
            "NOTICE: SINK disconnected",
            "[DISCONNECT] Peer:",
            "no longer running on peer",
            "no longer running on link",
        ]

        if any(s in clean_text for s in PEER_CONNECTED_STRINGS):
            events.append(SinkctlEvent("peer_connected"))
        
        if "NOTICE: SINK connected" in clean_text:
            events.append(SinkctlEvent("connected"))

        if (any(s in clean_text for s in DISCONNECTED_STRINGS)):
            events.append(SinkctlEvent("disconnected"))
        return events

    def terminate_process(self, process, name, timeout=2.0):
        if process is None or process.poll() is not None:
            return
        logger.debug("Stopping %s.", name)
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.debug("%s did not exit after terminate; killing.", name)
            process.kill()
            process.wait(timeout=timeout)

    def close_sinkctl(self):
        self._close_sinkctl_fd()

    def run_scrcpy(self, selected_device, *, display_id=None, fullscreen=False):
        icon_path = get_asset_path("icon.png")
        scrcpy_env = os.environ.copy()
        if os.path.isfile(icon_path):
            scrcpy_env["SCRCPY_ICON_PATH"] = icon_path
            scrcpy_env["SCRCPY_ICON_DIR"] = os.path.dirname(icon_path)
            logger.debug("Using scrcpy icon: %s", icon_path)

        args = ["-s", selected_device, "--window-title", "DexOnLinux", "--mouse-bind=++++"]
        if fullscreen:
            args.append("--fullscreen")
        if display_id is not None:
            args.extend(["--display-id", str(display_id)])

        process = subprocess.Popen(
            ["scrcpy", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=scrcpy_env,
        )

        if process.stdout:
            threading.Thread(target=self._stream_scrcpy_output, args=(process,), daemon=True).start()

        time.sleep(0.2)
        if process.poll() is not None:
            raise CommandError("scrcpy exited immediately after startup.")
        return process

    def _stream_scrcpy_output(self, process):
        if not process.stdout:
            return

        for line in iter(process.stdout.readline, ""):
            text = line.rstrip("\n")
            if text:
                logger.debug("scrcpy: %s", text)

        process.stdout.close()

    def _sanitize_sinkctl_output(self, text):
        password = self.sudo_password.strip()
        if password:
            text = text.replace(password, "[sudo password hidden]")
        return text

    def _drain_sinkctl_output(self, master_fd, event_callback=None):
        while True:
            try:
                chunk = os.read(master_fd, 4096)
                if not chunk:
                    break
                text = self._sanitize_sinkctl_output(chunk.decode(errors="replace")).strip()
                if text:
                    logger.debug("miracle-sinkctl: %s", text)
                    if event_callback:
                        for event in self.parse_sinkctl_events(text):
                            event_callback(event)
            except OSError:
                break
