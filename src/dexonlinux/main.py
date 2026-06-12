import argparse
import getpass
import sys

from dexonlinux.commands import CommandError, Commands
from dexonlinux.runtime import run_dex_session
from dexonlinux.utils import (
    colored,
    configure_logger,
    confirm,
    get_logger,
    print_adb_instructions,
    print_ascii_art,
    select_from_list,
)
from colorama import Fore

logger = get_logger()


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
    parser.add_argument("--yes", action="store_true", help="Do not ask before preparing the network interface.")
    parser.add_argument("--log-file", help="Write debug logs to a file.")
    parser.add_argument(
        "--network-mode",
        choices=["auto", "scoped", "full-stop"],
        default="auto",
        help="How DexOnLinux prepares the Wi-Fi interface for Miraclecast.",
    )
    return parser


def choose_interface(commands, requested):
    interfaces = get_available_interfaces(commands)
    if requested:
        names = interface_names(interfaces)
        if requested not in names:
            available = ", ".join(names) if names else "none"
            raise CommandError(f"Interface '{requested}' is not available or not P2P-capable. Available: {available}")
        selected = next(interface for interface in interfaces if interface_name(interface) == requested)
        ensure_interface_available(selected)
        return requested

    if not interfaces:
        raise CommandError("No P2P-capable Wi-Fi interfaces found.")

    selected = select_from_list(
        interfaces,
        "Select a P2P-capable interface:",
        formatter=interface_label,
    )
    if selected is None:
        raise CommandError("No interface selected.")
    ensure_interface_available(selected)
    return interface_name(selected)


def get_available_interfaces(commands):
    if commands.network_mode in ("auto", "scoped"):
        return commands.get_interface_infos()
    return commands.get_p2p_interfaces()


def interface_names(interfaces):
    return [interface_name(interface) for interface in interfaces]


def interface_name(interface):
    if hasattr(interface, "name"):
        return interface.name
    return interface


def interface_label(interface):
    if hasattr(interface, "label"):
        return interface.label()
    return interface


def ensure_interface_available(interface):
    if hasattr(interface, "is_available") and not interface.is_available:
        raise CommandError(
            f"Interface '{interface.name}' is unavailable. Enable Wi-Fi and retry: nmcli radio wifi on"
        )


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


def prepare_commands(network_mode):
    probe = Commands("", validate=False, network_mode=network_mode)
    missing = probe.missing_dependencies()
    if missing:
        raise CommandError("Missing dependencies: " + ", ".join(missing))

    sudo_password = getpass.getpass(f"[sudo] password for {getpass.getuser()}: ")
    return Commands(sudo_password, validate=True, network_mode=network_mode)


def resolve_network_mode(commands, interface, requested_mode):
    if requested_mode != "auto":
        return requested_mode

    default_interfaces = commands.get_default_route_interfaces()
    other_default_interfaces = [name for name in default_interfaces if name != interface]
    if default_interfaces and not other_default_interfaces:
        logger.info("Auto network mode selected full-stop: %s is the only default network route.", interface)
        return "full-stop"

    if other_default_interfaces:
        logger.info("Auto network mode selected scoped: another default route is available.")
    else:
        logger.info("Auto network mode selected scoped: no default network route was detected.")
    return "scoped"


def confirm_network_mode(commands, interface, network_mode, args):
    if args.yes:
        return

    if network_mode == "full-stop":
        message = "DexOnLinux will stop NetworkManager and wpa_supplicant. Continue?"
        if not confirm(message, default=False):
            raise CommandError("Aborted before disabling network services.")
        return

    device = commands.get_nmcli_device(interface)
    if device.is_connected:
        print()
        print(f"{interface} appears to be connected to {device.connection or 'a network'}.")
        print("DexOnLinux will disconnect it and stop wpa_supplicant so Miraclecast can use Wi-Fi Direct.")
        print("Wi-Fi internet will be unavailable until the session ends; non-Wi-Fi routes like Ethernet can stay online.")
    else:
        print()
        print("DexOnLinux will stop wpa_supplicant so Miraclecast can use Wi-Fi Direct.")
        print("Network connections that depend on wpa_supplicant may be unavailable until the session ends.")
    message = f"DexOnLinux will temporarily prepare {interface} for Miraclecast. Continue?"
    if not confirm(message, default=False):
        raise CommandError("Aborted before preparing the network interface.")


def run(args):
    if not args.no_banner:
        print_ascii_art()

    commands = prepare_commands(args.network_mode)
    selected_interface = choose_interface(commands, args.interface)
    selected_device = choose_adb_device(commands, args.device)
    network_mode = resolve_network_mode(commands, selected_interface, args.network_mode)

    confirm_network_mode(commands, selected_interface, network_mode, args)
    args.network_mode = network_mode

    return run_dex_session(commands, selected_interface, selected_device, args)


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
