import sh, logging
from pathlib import Path
from typing import Optional, List
from queue import Queue
from getpass import getpass

logger = logging.getLogger("dexonlinux")
logger.setLevel(logging.DEBUG)

sudo_password = getpass("[sudo psw]: ") + "\n"

def get_command(cmd_name: str, need_sudo: bool = True, auto_psw: bool = True) -> Optional[sh.Command]:
    try:
        cmd = sh.Command(cmd_name)
        if not need_sudo: 
            return cmd
        sudo_cmd = sh.Command("sudo").bake("-S",_in=sudo_password) if auto_psw else sh.Command("sudo").bake("-S")
        return sudo_cmd.bake(cmd)
    except sh.CommandNotFound:
        logger.error(f"Dependency '{cmd_name}' not found. Please install it before running the script.")
        return None

systemctl = get_command("systemctl")
miracle_wifi = get_command("miracle-wifid")
miracle_sinkctl = get_command("miracle-sinkctl", auto_psw=False)
scrcpy = get_command("scrcpy", need_sudo=False)
adb = get_command("adb")
pkill = get_command("pkill")

if not all([systemctl, miracle_sinkctl, miracle_wifi, scrcpy, adb, pkill]):
    exit(1)

def disable_network_services() -> bool:
    try:
        systemctl("stop", "NetworkManager", "wpa_supplicant")
        logger.debug("Network services stopped.")
        return True
    except Exception as e:
        logger.error(f"Error stopping network services: {e}")
        return False

def enable_network_services() -> bool:
    try:
        systemctl("start", "NetworkManager", "wpa_supplicant")
        logger.debug("Network services started.")
        return True
    except Exception as e:
        logger.error(f"Error starting network services: {e}")
        return False
    
def start_miracle_wifi(interface: str, background: bool = True) -> Optional[sh.RunningCommand]:
    try:
        process = miracle_wifi("--interface", interface, _bg=background, _out=logger.debug, _err_to_out=True)
        logger.debug("miracle-wifid started.")
        return process
    except Exception as e:
        logger.error(f"Error starting miracle-wifid: {e}")
        return None
    
def sinkctl_interact(input_queue: Queue, out: str, stdin: Queue):
    while not input_queue.empty():
        command = input_queue.get()
        stdin.put(command + "\n")
        logger.debug(f"Sent command to miracle-sinkctl: {command}")

def start_miracle_sinkctl(input_queue: Queue, background: bool = True) -> Optional[sh.RunningCommand]:
    try:
        process = miracle_sinkctl("--external-player", "true", "--port", "1991", "--audio", "1", 
                                  _bg=background,
                                  _out=lambda out, stdin: sinkctl_interact(input_queue, out, stdin),
                                  _tty_in=True, _err_to_out=True)
        process.process.stdin.put(sudo_password)
        logger.debug("miracle-sinkctl started.")
        return process
    except Exception as e:
        logger.error(f"Error starting miracle-sinkctl: {e}")
        return None


def get_p2p_interfaces() -> List[str]:
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

def list_adb_devices() -> List[str]:
    try:
        result = adb("devices", _err_to_out=True)
        lines = result.strip().splitlines()[1:]
        devices = [line.split()[0] for line in lines if "device" in line]
        return devices
    except Exception as e:
        logger.error(f"Error listing ADB devices: {e}")
        return []

def kill_miracle() -> None:
    pkill("miracle")

def run_scrcpy() -> Optional[sh.RunningCommand]:
    try:
        return scrcpy("-d", "--display-id", "2", "--window-title", "DexOnLinux",
                        "--fullscreen", "--mouse-bind=++++", _bg=True, _err_to_out=True, _out=logger.debug)
    except Exception as e:
        logger.error(f"Error starting scrcpy: {e}")
        return None
    
#ffmpeg -i rtp://127.0.0.1:1991 -f null -