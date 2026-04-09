import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence
from utils import get_logger, get_app_path

logger = get_logger()


class Commands:
    REQUIRED_COMMANDS = ["sudo", "systemctl", "miracle-wifid", "miracle-sinkctl", "scrcpy", "adb", "pkill", "iw"]
    NETWORK_SERVICES = ["NetworkManager", "wpa_supplicant"]
    SUDO_PREFIX = ["sudo", "-S"]

    def __init__(self, sudo_password):
        self.sudo_password = sudo_password + "\n"
        self._sinkctl_master_fd: Optional[int] = None
        if not self._check_sudo_password():
            exit(1)
        if not self._check_dependencies():
            exit(1)

    def _build_command(self, command: Sequence[str], sudo: bool = False) -> List[str]:
        return [*self.SUDO_PREFIX, *command] if sudo else list(command)

    def _run_command(
        self,
        command: Sequence[str],
        *,
        sudo: bool = False,
        env: Optional[dict] = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self._build_command(command, sudo=sudo),
            input=self.sudo_password if sudo else None,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def _run_checked(
        self,
        command: Sequence[str],
        *,
        sudo: bool = False,
        env: Optional[dict] = None,
    ) -> subprocess.CompletedProcess[str]:
        result = self._run_command(command, sudo=sudo, env=env)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
        return result

    def _start_sudo_background_process(
        self,
        command: Sequence[str],
        *,
        stdout,
        stderr,
        env: Optional[dict] = None,
    ) -> subprocess.Popen:
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

    def _combine_output(self, result: subprocess.CompletedProcess[str]) -> str:
        return (result.stdout or "") + (result.stderr or "")

    def _close_sinkctl_fd(self) -> None:
        if self._sinkctl_master_fd is None:
            return
        try:
            os.close(self._sinkctl_master_fd)
        except OSError:
            pass
        self._sinkctl_master_fd = None

    def _write_sinkctl_commands(self, master_fd: int, commands: Iterable[str], delay: float = 0.1) -> None:
        for command in commands:
            os.write(master_fd, f"{command}\n".encode())
            time.sleep(delay)
            logger.debug(f"Sent command to miracle-sinkctl: {command}")

    def _check_dependencies(self) -> bool:
        missing = [cmd for cmd in self.REQUIRED_COMMANDS if shutil.which(cmd) is None]
        for cmd in missing:
            logger.error(f"Dependency '{cmd}' not found. Please install it before running the script.")
        return not missing

    def _check_sudo_password(self) -> bool:
        try:
            result = self._run_command(["-k", "true"], sudo=True)
            if result.returncode == 0:
                return True
            logger.error("Incorrect sudo password.")
            return False
        except FileNotFoundError:
            logger.error("Dependency 'sudo' not found. Please install it before running the script.")
            return False

    def disable_network_services(self) -> bool:
        try:
            self._run_checked(["systemctl", "stop", *self.NETWORK_SERVICES], sudo=True)
            logger.debug("Network services stopped.")
            return True
        except Exception as e:
            logger.error(f"Error stopping network services: {e}")
            return False

    def enable_network_services(self) -> bool:
        try:
            self._run_checked(["systemctl", "start", *self.NETWORK_SERVICES], sudo=True)
            logger.debug("Network services started.")
            return True
        except Exception as e:
            logger.error(f"Error starting network services: {e}")
            return False
        
    def start_miracle_wifi(self, interface: str, background: bool = True) -> Optional[subprocess.Popen]:
        try:
            command = ["miracle-wifid", "--interface", interface]
            if not background:
                self._run_checked(command, sudo=True)
                logger.debug("miracle-wifid started in foreground.")
                return None

            process = self._start_sudo_background_process(command, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            logger.debug("miracle-wifid started.")
            return process
        except Exception as e:
            logger.error(f"Error starting miracle-wifid: {e}")
            return None

    def start_miracle_sinkctl(self, interface_index: int, background: bool = True, port: int = 1991) -> Optional[subprocess.Popen]:
        commands = [f"set-managed {interface_index} yes", f"run {interface_index}"]
        try:
            command = ["miracle-sinkctl", "--external-player", "true", "--port", str(port), "--audio", "1"]

            if not background:
                self._run_checked(command, sudo=True)
                logger.debug("miracle-sinkctl started in foreground.")
                return None

            master_fd, slave_fd = os.openpty()
            process = subprocess.Popen(self._build_command(command, sudo=True), stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)
            os.close(slave_fd)
            self._sinkctl_master_fd = master_fd

            threading.Thread(target=self._drain_sinkctl_output, args=(master_fd,), daemon=True).start()

            os.write(master_fd, self.sudo_password.encode())
            time.sleep(0.2)
            self._write_sinkctl_commands(master_fd, commands)


            if process.poll() is not None:
                raise RuntimeError("miracle-sinkctl exited immediately after startup.")

            logger.debug("miracle-sinkctl started.")
            return process
        except Exception as e:
            logger.error(f"Error starting miracle-sinkctl: {e}")
            self._close_sinkctl_fd()
            return None

    def _get_port(self) -> int:
        #os automatically assigns an available port when binding to port 0, so we can use that to find a free port
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', 0))
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                port = s.getsockname()[1]
                return port
        except Exception as e:
            raise RuntimeError(f"Could not find an available port: {e}")

    def get_p2p_interfaces(self) -> List[str]:
        #revisited method taken from: 
        #https://github.com/semarainc/TuxDex/blob/main/capabilitiesCheck.py#L48
        p2pwifi = []
        base_dir = Path("/sys/class/net")

        for iface_path in base_dir.iterdir():
            if (iface_path / "wireless").exists():
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

                    iw_phy = self._combine_output(iw_phy_result)
                    
                    if "P2P" in iw_phy:
                        p2pwifi.append(iface)   
                except Exception:
                    continue
        return p2pwifi

    def list_adb_devices(self) -> List[str]:
        try:
            result = self._run_checked(["adb", "devices"], sudo=True)
            output = self._combine_output(result)
            lines = output.strip().splitlines()
            devices = [line.split()[0] for line in lines if "device" in line and not "List" in line]
            return devices
        except Exception as e:
            logger.error(f"Error listing ADB devices: {e}")
            return []

    def kill_miracle(self) -> None:
        try:
            for process_name in ("miracle-wifid", "miracle-sinkctl"):
                self._run_command(["pkill", process_name], sudo=True)
            self._close_sinkctl_fd()
        except Exception:
            pass
        logger.debug("Killed miracle-wifid and miracle-sinkctl processes.")

    def run_scrcpy(self, selected_device: str) -> Optional[subprocess.Popen]:
        #handles multiple devices: https://github.com/Genymobile/scrcpy/issues/400
        try:
            icon_path = os.path.join(get_app_path(), "assets", "icon.png")
            scrcpy_env = os.environ.copy()
            if os.path.isfile(icon_path):
                scrcpy_env["SCRCPY_ICON_PATH"] = icon_path
                logger.debug(f"Using SCRCPY_ICON_PATH: {icon_path}")
            else:
                logger.warning(f"Scrcpy icon not found at: {icon_path}")

            display_id = self._get_display_id(selected_device)

            #TODO: make user choose if fullscreen or not
            args = ["-s", selected_device, "--window-title", "DexOnLinux", "--mouse-bind=++++"]
            
            if display_id:
                args.extend(["--display-id", display_id])
            else:
                #TODO: handle if display id is 0 and errors
                logger.error("No display ID found for scrcpy. Defaulting to primary display.")

            process = subprocess.Popen(
                ["scrcpy", *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=scrcpy_env,
            )

            if process.stdout:
                threading.Thread(target=self._stream_scrcpy_output, args=(process,), daemon=True).start()

            return process
        except Exception as e:
            logger.error(f"Error starting scrcpy: {e}")
            return None

    def _get_display_id(self, selected_device: str) -> Optional[str]:
        #TODO: fix if is only --display-id=0 or some errors
        try:
            result = self._run_command(["scrcpy", "-s", selected_device, "--list-displays"])
            output = self._combine_output(result)
            pattern = re.compile(r"--display-id=(\d+)")
            matches = pattern.findall(output)
            display_ids = [int(did) for did in matches]
            if not display_ids:
                return None

            selected_display = max(display_ids)
            logger.debug(f"Selected display with largest id: {selected_display}")
            return str(selected_display)
        except Exception as e:
            logger.error(f"Unable to list scrcpy displays: {e}")
            return None

    def _stream_scrcpy_output(self, process: subprocess.Popen) -> None:
        if not process.stdout:
            return

        for line in iter(process.stdout.readline, ""):
            text = line.rstrip("\n")
            if text:
                logger.debug("STREAM: %s", text)

        process.stdout.close()

    def _drain_sinkctl_output(self, master_fd: int) -> None:
        while True:
            try:
                chunk = os.read(master_fd, 4096)
                if not chunk:
                    break
                text = chunk.decode(errors="replace").strip()
                if text:
                    logger.debug("DRAIN: %s", text)
            except OSError:
                break