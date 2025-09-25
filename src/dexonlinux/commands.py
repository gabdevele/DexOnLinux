import sh, os
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
        self.ffmpeg = self._get_command("ffmpeg", need_sudo=False)

        if not all([self.systemctl, self.miracle_sinkctl, self.miracle_wifi, self.scrcpy, self.adb, self.pkill, self.ffmpeg]):
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

    def start_miracle_sinkctl(self, interface_index: int, background: bool = True) -> Optional[sh.RunningCommand]:
        def interact(commands: Queue, stdin: Queue):
            while not commands.empty():
                command = commands.get()
                stdin.put(command + "\n")
                logger.debug(f"Sent command to miracle-sinkctl: {command}")

        commands = Queue()
        commands.put(f"set-managed {interface_index} yes")
        commands.put(f"run {interface_index}")
        try:
            process = self.miracle_sinkctl("--external-player", "true", "--port", "1991", "--audio", "1", 
                                          _bg=background,
                                          _out=lambda _, stdin: interact(commands, stdin),
                                          _tty_in=True, _err_to_out=True)
            process.process.stdin.put(self.sudo_password)
            logger.debug("miracle-sinkctl started.")
            return process
        except Exception as e:
            logger.error(f"Error starting miracle-sinkctl: {e}")
            return None


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
        #could just use pkill("miracle", _bg=True) but what if
        #there are other processes with miracle in their name?
        try:
            self.pkill("miracle-wifid")
            self.pkill("miracle-sinkctl")
        except:
            pass
        logger.debug("Killed miracle-wifid and miracle-sinkctl processes.")

    def run_scrcpy(self, selected_device: str) -> Optional[sh.RunningCommand]:
        #handles multiple devices: https://github.com/Genymobile/scrcpy/issues/400
        try:
            os.environ["SCRCPY_ICON_PATH"] = os.path.join(get_app_path(), "assets/icon.png") #TODO: currently not working on my ubuntu, could be cache issue?
            return self.scrcpy("-s", selected_device, "--display-id", "2", "--window-title", "DexOnLinux",
                            "--fullscreen", "--mouse-bind=++++", _bg=True, _err_to_out=True, _out=logger.debug)
        except Exception as e:
            logger.error(f"Error starting scrcpy: {e}")
            return None
        
    def run_ffmpeg(self) -> Optional[sh.RunningCommand]:
        #TODO: just to keep the rtp stream alive, I'm working on a better solution
        #TODO: error sh.ErrorReturnCode_255 when exiting the script
        try:
            return self.ffmpeg("-i", "rtp://127.0.0.1:1991", "-f", "null", "-", _bg=True, _err_to_out=True)
        except Exception as e:
            logger.error(f"Error starting ffmpeg: {e}")
            return None
        