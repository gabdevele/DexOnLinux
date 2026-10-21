"""DexOnLinux CLI: Wi-Fi Direct P2P, WFD RTSP and scrcpy."""

from __future__ import annotations

import argparse
import getpass
import sys

from colorama import Fore

from dexonlinux.device import Device
from dexonlinux.doctor import StackReport, list_p2p_interfaces, probe_stack
from dexonlinux.helper.client import HelperClient
from dexonlinux.p2p.base import P2PError
from dexonlinux.scrcpy import ScrcpySession
from dexonlinux.session import DexSession
from dexonlinux.system import CommandError, SubprocessRunner
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
from dexonlinux.wfd.rtsp import WfdSink

logger = get_logger()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stream Samsung DeX to Linux over Wi-Fi Direct."
    )
    parser.add_argument("--interface", help="P2P-capable Wi-Fi interface to use.")
    parser.add_argument("--device", help="ADB device serial to use.")
    parser.add_argument("--display-id", type=int, help="scrcpy display id to open.")
    parser.add_argument("--fullscreen", action="store_true", help="Start scrcpy in fullscreen mode.")
    parser.add_argument(
        "--backend",
        choices=["auto", "direct"],
        default="auto",
        help="P2P backend (auto selects the supported direct backend).",
    )
    parser.add_argument("--doctor", action="store_true", help="Check the host stack and exit.")
    parser.add_argument("--debug", action="store_true", help="Show debug logs.")
    parser.add_argument("--no-color", action="store_true", help="Disable colored terminal output.")
    parser.add_argument("--no-banner", action="store_true", help="Do not print the ASCII banner.")
    parser.add_argument("--yes", action="store_true", help="Skip the final confirmation.")
    parser.add_argument("--log-file", help="Write debug logs to a file.")
    return parser


def choose_interface(interfaces: list[str], requested: str | None) -> str:
    if requested:
        if requested not in interfaces:
            available = ", ".join(interfaces) if interfaces else "none"
            raise CommandError(
                f"Interface '{requested}' is not P2P-capable. Available: {available}"
            )
        return requested

    if not interfaces:
        raise CommandError(
            "No P2P-capable Wi-Fi interface found. Enable Wi-Fi and check that the driver supports P2P."
        )
    if len(interfaces) == 1:
        return interfaces[0]

    selected = select_from_list(interfaces, "Select a P2P-capable interface:")
    if selected is None:
        raise CommandError("No interface selected.")
    return selected


def choose_adb_device(device: Device, requested: str | None):
    if not requested:
        print_adb_instructions()

    while True:
        devices = device.list_adb_devices()
        if requested:
            match = next((item for item in devices if item.serial == requested), None)
            if match is None:
                raise CommandError(f"ADB device '{requested}' was not found.")
            if not match.is_authorized:
                raise CommandError(f"ADB device '{requested}' is {match.state}. Authorize it and retry.")
            return match

        authorized = [item for item in devices if item.is_authorized]
        blocked = [item for item in devices if not item.is_authorized]
        for item in blocked:
            logger.warning("ADB device %s is %s.", item.serial, item.state)

        if authorized:
            if len(authorized) == 1:
                logger.info("Using ADB device %s.", authorized[0].serial)
                return authorized[0]
            selected = select_from_list(
                authorized,
                "Select an ADB device:",
                formatter=lambda item: item.label(),
                allow_refresh=True,
            )
            if selected == "refresh":
                continue
            if selected is not None:
                return selected
            raise CommandError("No ADB device selected.")

        logger.error("No authorized ADB devices found.")
        choice = input(
            colored(
                "Press Enter to refresh ADB devices or type q to quit: ",
                Fore.LIGHTYELLOW_EX,
            )
        )
        if choice.strip().lower() == "q":
            raise CommandError("No ADB device selected.")


def print_report(interface: str, report: StackReport) -> None:
    print()
    print(f"Interface: {interface}")
    print(f"Network stack: {report.manager}")
    print(f"P2P backend: {report.backend}")
    print(f"Pause NetworkManager: {'yes' if report.suspend_nm else 'no'}")
    if report.supported:
        print(colored("Supported: yes", Fore.LIGHTGREEN_EX))
    else:
        print(colored("Supported: no", Fore.LIGHTRED_EX))
        for reason in report.reasons:
            print(f"  - {reason}")


def doctor(runner: SubprocessRunner, interface: str | None) -> int:
    interfaces = [interface] if interface else list_p2p_interfaces(runner)
    if not interfaces:
        logger.error("No P2P-capable Wi-Fi interface found.")
        return 1

    supported = 0
    for name in interfaces:
        report = probe_stack(runner, name)
        print_report(name, report)
        supported += int(report.supported)
    return 0 if supported == len(interfaces) else 1


def confirm_session(report: StackReport, skip: bool) -> None:
    if skip:
        return
    if report.suspend_nm:
        message = (
            "DexOnLinux will temporarily pause NetworkManager while the P2P group is active. "
            "Wi-Fi internet stays connected. Continue?"
        )
    else:
        message = "DexOnLinux will create a Wi-Fi Direct P2P group. Continue?"
    if not confirm(message, default=False):
        raise CommandError("Aborted before starting the session.")


def prepare_sudo() -> SubprocessRunner:
    try:
        password = getpass.getpass(f"[sudo] password for {getpass.getuser()}: ")
    except (EOFError, KeyboardInterrupt) as exc:
        raise CommandError("sudo authentication is required to start the P2P helper.") from exc
    runner = SubprocessRunner(password)
    runner.ensure_sudo()
    return runner


def run(args: argparse.Namespace) -> int:
    if not args.no_banner:
        print_ascii_art()

    runner = SubprocessRunner()
    if args.doctor:
        return doctor(runner, args.interface)

    interfaces = list_p2p_interfaces(runner)
    interface = choose_interface(interfaces, args.interface)
    report = probe_stack(runner, interface)
    print_report(interface, report)
    if not report.supported:
        raise CommandError("The host stack is not supported. Fix the items above and retry.")

    device = choose_adb_device(Device(runner), args.device)
    confirm_session(report, args.yes)
    prepare_sudo()

    logger.info("Starting Wi-Fi Direct P2P on %s.", interface)
    helper = HelperClient.start(
        interface,
        backend="direct",
        suspend_nm=report.suspend_nm,
    )
    try:
        scrcpy = ScrcpySession(
            device.serial,
            runner,
            display_id=args.display_id,
            fullscreen=args.fullscreen,
        )
        session = DexSession(
            helper,
            scrcpy,
            lambda host: WfdSink(host, 7236, connect_timeout=5.0),
            start_delay=1.0,
        )
        print_dex_instructions()
        return session.run()
    finally:
        helper.stop()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logger(debug=args.debug, no_color=args.no_color, log_file=args.log_file)
    try:
        return run(args)
    except CommandError as exc:
        logger.error("%s", exc)
        return 1
    except P2PError as exc:
        logger.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logger.info("Interrupted.")
        return 130
    except Exception:
        logger.exception("Unexpected error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
