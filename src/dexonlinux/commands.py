import sh, os, re, socket
from pathlib import Path
from typing import Optional, List
from queue import Queue
from utils import get_logger, get_app_path

logger = get_logger()

class Commands:
    def __init__(self, sudo_password):
        self.sudo_password = sudo_password + "\n"
        if not self._check_sudo_password():
            exit(1)
        self.systemctl = self._get_command("systemctl")
        self.miracle_wifi = self._get_command("miracle-wifid")
        self.miracle_sinkctl = self._get_command("miracle-sinkctl", auto_psw=False) #need interactive input
        self.scrcpy = self._get_command("scrcpy", need_sudo=False) #does not work with sudo
        self.adb = self._get_command("adb")
        self.pkill = self._get_command("pkill")

        if not all([self.systemctl, self.miracle_sinkctl, self.miracle_wifi, self.scrcpy, self.adb, self.pkill]):
            exit(1)

    def _check_sudo_password(self) -> bool:
        try:
            sh.sudo("-k", "-S", "true", _in=self.sudo_password) #-k to invalid previous authentications
            return True
        except sh.ErrorReturnCode:
            logger.error("Incorrect sudo password.")
            return False
        
    def _get_command(self, cmd_name: str, need_sudo: bool = True, auto_psw: bool = True, **kwargs) -> Optional[sh.Command]:
        try:
            cmd = sh.Command(cmd_name, **kwargs)
            if not need_sudo:
                return cmd
            sudo_cmd = sh.Command("sudo").bake("-S",_in=self.sudo_password) if auto_psw else sh.Command("sudo").bake("-S")
            return sudo_cmd.bake(cmd)
        except sh.CommandNotFound:
            logger.error(f"Dependency '{cmd_name}' not found. Please install it before running the script.")
            return None

    def disable_network_services(self) -> bool:
        try:
            self.systemctl("stop", "NetworkManager", "wpa_supplicant")
            logger.debug("Network services stopped.")
            return True
        except Exception as e:
            logger.error(f"Error stopping network services: {e}")
            return False

    def enable_network_services(self) -> bool:
        try:
            self.systemctl("start", "NetworkManager", "wpa_supplicant")
            logger.debug("Network services started.")
            return True
        except Exception as e:
            logger.error(f"Error starting network services: {e}")
            return False
        
    def start_miracle_wifi(self, interface: str, background: bool = True) -> Optional[sh.RunningCommand]:
        try:
            process = self.miracle_wifi("--interface", interface, _bg=background, _err_to_out=True)
            logger.debug("miracle-wifid started.")
            return process
        except Exception as e:
            logger.error(f"Error starting miracle-wifid: {e}")
            return None

    def start_miracle_sinkctl(self, interface_index: int, background: bool = True, port: int = 1991) -> Optional[sh.RunningCommand]:
        def interact(commands: Queue, stdin: Queue):
            while not commands.empty():
                command = commands.get()
                stdin.put(command + "\n")
                logger.debug(f"Sent command to miracle-sinkctl: {command}")

        commands = Queue()
        commands.put(f"set-managed {interface_index} yes")
        commands.put(f"run {interface_index}")
        try:
            process = self.miracle_sinkctl("--external-player", "true", "--port", str(port), "--audio", "1", 
                                          _bg=background,
                                          _out=lambda _, stdin: interact(commands, stdin),
                                          _tty_in=True, _err_to_out=True)
            process.process.stdin.put(self.sudo_password)
            logger.debug("miracle-sinkctl started.")
            return process
        except Exception as e:
            logger.error(f"Error starting miracle-sinkctl: {e}")
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
                    iw_dev = sh.iw("dev", iface, "info", _err_to_out=True)
                    wiphy_line = next((l for l in iw_dev.splitlines() if l.strip().startswith("wiphy")), None)
                    if not wiphy_line:
                        continue
                    idxphy = wiphy_line.split()[1]
                    iw_phy = sh.iw("phy", f"phy{idxphy}", "info", _err_to_out=True)
                    
                    if "P2P" in iw_phy:
                        p2pwifi.append(iface)   
                except sh.ErrorReturnCode:
                    continue
        return p2pwifi

    def list_adb_devices(self) -> List[str]:
        try:
            result = self.adb("devices", _err_to_out=True)
            lines = result.strip().splitlines()
            devices = [line.split()[0] for line in lines if "device" in line and not "List" in line]
            return devices
        except Exception as e:
            logger.error(f"Error listing ADB devices: {e}")
            return []

    def kill_miracle(self) -> None:
        try:
            self.pkill("miracle-wifid")
            self.pkill("miracle-sinkctl")
        except:
            pass
        logger.debug("Killed miracle-wifid and miracle-sinkctl processes.")

    def run_scrcpy(self, selected_device: str) -> Optional[sh.RunningCommand]:
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

            return self.scrcpy(*args, _bg=True, _err_to_out=True, _out=logger.debug, _env=scrcpy_env)
        except Exception as e:
            logger.error(f"Error starting scrcpy: {e}")
            return None

    def _get_display_id(self, selected_device: str) -> Optional[str]:
        #TODO: fix if is only --display-id=0 or some errors
        try:
            output = self.scrcpy("-s", selected_device, "--list-displays", _err_to_out=True)
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