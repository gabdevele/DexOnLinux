import sh, time
import logging
import commands
from dbus import MiracleDbus
from queue import Queue
from typing import Optional, List

logging.basicConfig(level=logging.WARNING, format='[%(levelname)s] - %(message)s')

logger = logging.getLogger("dexonlinux")
logger.setLevel(logging.DEBUG)

def select_menu(interfaces: List[str]) -> Optional[str]:
    print("Select a P2P-capable interface:")
    for idx, iface in enumerate(interfaces):
        print(f"{idx + 1}. {iface}")
    choice = input("Enter the number of the interface (or 'q' to quit): ")
    if choice.lower() == 'q':
        return None
    try:
        choice_idx = int(choice) - 1
        if 0 <= choice_idx < len(interfaces):
            return interfaces[choice_idx]
        else:
            logger.debug("Invalid selection.")
            return None
    except ValueError:
        logger.error("Please enter a valid number.")
        return None

def error_exit(message: str, enable_network: bool = False):
    logger.error(message)
    if enable_network:
        commands.kill_miracle()
        commands.enable_network_services()
    exit(1)

def print_logs_dbus(path, iface, props, invalid=None):
    print(f"[CALLBACK/PROP CHANGED] Path: {path}, Interface: {iface}, Properties/Changed: {props}")


#TODO: check if all dependencies are installed

interfaces = commands.get_p2p_interfaces()
if not interfaces:
    error_exit("No P2P-capable interfaces found.")

selected_interface = select_menu(interfaces)
if not selected_interface:
    error_exit("No interface selected.")

logger.debug(f"Selected interface: {selected_interface}")

if not commands.disable_network_services():
    error_exit("Failed to disable network services.")

time.sleep(1.5)
miracle_wifi = commands.start_miracle_wifi(selected_interface)
assert miracle_wifi != None
time.sleep(0.5)
dbus = MiracleDbus()
if not dbus.bus_exists():
    error_exit("Miracle Dbus does not exist.")

dbus.on_peer_event(print_logs_dbus)
dbus.subscribe_properties_changed(print_logs_dbus)

links = dbus.get_links()
interface_index = dbus.get_interface_index(links[0])

time.sleep(1) #necessary otherwise I get this issue: https://github.com/albfan/miraclecast/issues/211

stdin = Queue()
stdin.put(f"set-managed {interface_index} yes")
stdin.put(f"run {interface_index}")
miracle_sinkctl = commands.start_miracle_sinkctl(stdin)

print("Open Dex on your device and connect to Miracle.")
print("Connect your device to your PC via USB and enable USB Debugging in the developer options.")
print("After connecting, press Enter and check if it is listed.")

input("Press Enter to continue...")

adb_devices = commands.list_adb_devices()
if not adb_devices:
    error_exit("No ADB devices found. Exiting...", enable_network=True)
print("Found ADB devices:", adb_devices)

try:
    print("Press Ctrl+C to stop and exit.")
    scrcpy = commands.run_scrcpy()
    scrcpy.wait()
except KeyboardInterrupt:
    logger.debug("Interrupted by user. Exiting...")
finally:
    logger.debug("Stopping miracle services...")
    commands.kill_miracle()
    logger.debug("Re-enabling network services...")
    commands.enable_network_services()

