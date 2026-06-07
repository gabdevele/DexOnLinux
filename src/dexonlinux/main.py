import argparse
import getpass
import signal
import sys
import time

from dexonlinux.commands import CommandError, Commands
from dexonlinux.connection_handler import ConnectionHandler
from dexonlinux.dbus import MiracleDbus
from dexonlinux.utils import (
    colored,
    configure_logger,
    confirm,
    get_logger,
    print_adb_instructions,
    print_ascii_art,
    print_dex_instructions,
    select_from_list,
)
from colorama import Fore

logger = get_logger()


class DexRuntime:
    def __init__(self, commands):
        self.commands = commands
        self.network_disabled = False
        self.miracle_wifi = None
        self.miracle_sinkctl = None
        self.connection_handler = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.cleanup()
        return False

    def disable_network(self):
        self.commands.disable_network_services()
        self.network_disabled = True

    def cleanup(self):
        if self.connection_handler:
            self.connection_handler.stop()
        self.commands.terminate_process(self.miracle_sinkctl, "miracle-sinkctl")
        self.commands.close_sinkctl()
        self.commands.terminate_process(self.miracle_wifi, "miracle-wifid")
        if self.network_disabled:
            try:
                self.commands.enable_network_services()
            except Exception as exc:
                logger.error("Network services could not be restored automatically: %s", exc)


def build_parser():
    parser = argparse.ArgumentParser(description="Stream Samsung DeX to Linux with Miraclecast and scrcpy.")
    parser.add_argument("--interface", help="P2P-capable Wi-Fi interface to use.")
    parser.add_argument("--device", help="ADB device serial to use.")
    parser.add_argument("--port", type=int, help="UDP port used by miracle-sinkctl external player.")
    parser.add_argument("--display-id", type=int, help="scrcpy display id to open.")
    parser.add_argument("--fullscreen", action="store_true", help="Start scrcpy in fullscreen mode.")
    parser.add_argument("--debug", action="store_true", help="Show debug logs.")
    parser.add_argument("--no-color", action="store_true", help="Disable colored terminal output.")
    parser.add_argument("--no-banner", action="store_true", help="Do not print the ASCII banner.")
    parser.add_argument("--yes", action="store_true", help="Do not ask before temporarily disabling network services.")
    parser.add_argument("--log-file", help="Write debug logs to a file.")
    return parser


def wait_until(label, predicate, timeout=8.0, interval=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    logger.debug("Timed out waiting for %s.", label)
    return False


def choose_interface(commands, requested):
    interfaces = commands.get_p2p_interfaces()
    if requested:
        if requested not in interfaces:
            available = ", ".join(interfaces) if interfaces else "none"
            raise CommandError(f"Interface '{requested}' is not available or not P2P-capable. Available: {available}")
        return requested

    if not interfaces:
        raise CommandError("No P2P-capable Wi-Fi interfaces found.")

    selected = select_from_list(interfaces, "Select a P2P-capable interface:")
    if selected is None:
        raise CommandError("No interface selected.")
    return selected


def choose_adb_device(commands, requested):
    if not requested:
        print_adb_instructions()
    while True:
        devices = commands.list_adb_devices()
        if requested:
            match = next((device for device in devices if device.serial == requested), None)
            if match is None:
                raise CommandError(f"ADB device '{requested}' was not found.")
            if not match.is_authorized:
                raise CommandError(f"ADB device '{requested}' is {match.state}. Authorize it and retry.")
            return match

        authorized = [device for device in devices if device.is_authorized]
        blocked = [device for device in devices if not device.is_authorized]
        for device in blocked:
            logger.warning("ADB device %s is %s.", device.serial, device.state)

        if authorized:
            if len(authorized) == 1:
                logger.info("Using ADB device %s.", authorized[0].serial)
                return authorized[0]
            selected = select_from_list(
                authorized,
                "Select an ADB device:",
                formatter=lambda device: device.label(),
                allow_refresh=True,
            )
            if selected == "refresh":
                continue
            if selected is not None:
                return selected
            raise CommandError("No ADB device selected.")
        else:
            logger.error("No authorized ADB devices found.")

        choice = input(colored("Press Enter to refresh ADB devices or type q to quit: ", Fore.LIGHTYELLOW_EX))
        if choice.strip().lower() == "q":
            raise CommandError("No ADB device selected.")


def prepare_commands():
    probe = Commands("", validate=False)
    missing = probe.missing_dependencies()
    if missing:
        raise CommandError("Missing dependencies: " + ", ".join(missing))

    sudo_password = getpass.getpass(f"[sudo] password for {getpass.getuser()}: ")
    return Commands(sudo_password, validate=True)


def run(args):
    if not args.no_banner:
        print_ascii_art()

    commands = prepare_commands()
    selected_interface = choose_interface(commands, args.interface)
    selected_device = choose_adb_device(commands, args.device)

    if not args.yes:
        ok = confirm("DexOnLinux will temporarily disable NetworkManager and wpa_supplicant. Continue?", default=False)
        if not ok:
            raise CommandError("Aborted before disabling network services.")

    with DexRuntime(commands) as runtime:
        runtime.disable_network()
        runtime.miracle_wifi = commands.start_miracle_wifi(selected_interface)

        dbus = MiracleDbus()
        if not wait_until("Miraclecast DBus service", dbus.bus_exists, timeout=8.0):
            raise CommandError("Miraclecast DBus service did not become available.")

        link_path = None
        if wait_until("Miraclecast link", lambda: bool(dbus.get_links()), timeout=8.0):
            link_path = dbus.get_link_for_interface(selected_interface)
        if link_path is None:
            raise CommandError(f"Could not find a Miraclecast link for interface '{selected_interface}'.")

        interface_index = dbus.get_interface_index(link_path)
        if interface_index is None:
            raise CommandError(f"Could not read Miraclecast interface index for {link_path}.")

        port = args.port or commands.get_available_udp_port()
        runtime.miracle_sinkctl = commands.start_miracle_sinkctl(interface_index, port)

        print_dex_instructions()
        handler = ConnectionHandler(
            commands,
            selected_device.serial,
            port,
            display_id=args.display_id,
            fullscreen=args.fullscreen,
            on_scrcpy_closed=dbus.stop_loop,
        )
        runtime.connection_handler = handler
        dbus.subscribe_properties_changed(handler.handle_connection)

        for peer in dbus.get_connected_peers():
            handler.handle_connection(peer.path, peer.interface, peer.properties, {})

        stop_requested = False

        def request_stop(signum, frame):
            nonlocal stop_requested
            stop_requested = True
            dbus.stop_loop()

        previous_sigint = signal.signal(signal.SIGINT, request_stop)
        previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
        try:
            dbus.run_loop()
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
            signal.signal(signal.SIGTERM, previous_sigterm)

        return 130 if stop_requested else 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logger(debug=args.debug, no_color=args.no_color, log_file=args.log_file)
    try:
        return run(args)
    except CommandError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
