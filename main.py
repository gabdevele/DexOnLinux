from commands import Commands
import time, logging
from dbus import MiracleDbus
from utils import (get_logger, error_exit, select_from_list, print_ascii_art, print_instructions)
import getpass
from connection_handler import ConnectionHandler

logging.basicConfig(level=logging.WARNING, format='[%(levelname)s] (%(name)s) - %(message)s (%(filename)s:%(lineno)d)')

logger = get_logger()

print_ascii_art()

sudo_password = getpass.getpass(f"[sudo] password for {getpass.getuser()}: ")
commands = Commands(sudo_password)

interfaces = commands.get_p2p_interfaces()
if not interfaces:
    error_exit("No P2P-capable interfaces found.")

selected_interface = select_from_list(interfaces, "Select a P2P-capable interface:")
if not selected_interface:
    error_exit("No interface selected.")

logger.debug(f"Selected interface: {selected_interface}")

if not commands.disable_network_services():
    error_exit("Failed to disable network services.")

time.sleep(1.5)

miracle_wifi = commands.start_miracle_wifi(selected_interface)
if miracle_wifi is None: #using "is None" with sh process 'cause otherwise it internally runs process.wait()
    error_exit("Failed to start miracle-wifid.", enable_network=True, commands=commands)

time.sleep(0.5)

dbus = MiracleDbus()
if not dbus.bus_exists():
    error_exit("Miracle Dbus does not exist.", enable_network=True, commands=commands)

links = dbus.get_links()
interface_index = dbus.get_interface_index(links[0]) #TODO: arbitrarily choosing the first one, this should be reviewed

time.sleep(1) #necessary otherwise I get this issue: https://github.com/albfan/miraclecast/issues/211

miracle_sinkctl = commands.start_miracle_sinkctl(interface_index)

if miracle_sinkctl is None:
    error_exit("Failed to start miracle-sinkctl.", enable_network=True, commands=commands)

adb_devices = commands.list_adb_devices()
if not adb_devices:
    error_exit("No ADB devices found. Exiting...", enable_network=True, commands=commands)

selected_device = select_from_list(adb_devices, "Select an ADB device:")
if not selected_device:
    error_exit("No device selected. Exiting...", enable_network=True, commands=commands)

logger.debug(f"Selected ADB device: {selected_device}")

handler = ConnectionHandler(commands, selected_device)
dbus.subscribe_properties_changed(handler.handle_connection)

try:
    dbus.run_loop()
except KeyboardInterrupt:
    logger.debug("Interrupted by user. Exiting...")
finally:
    logger.debug("Stopping miracle services...")
    commands.kill_miracle()
    logger.debug("Re-enabling network services...")
    commands.enable_network_services()

