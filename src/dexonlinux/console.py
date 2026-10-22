import logging
from collections.abc import Callable, Sequence
from importlib import resources
from pathlib import Path
from typing import Literal, TypeVar

from colorama import Fore, Style

LOG = logging.getLogger(__name__)
T = TypeVar("T")

_COLOR_ENABLED = True


def set_color(enabled: bool) -> None:
    """Enable or disable ANSI output for the whole process."""
    global _COLOR_ENABLED
    _COLOR_ENABLED = enabled


def colored(text: str, color: str = "", style: str = "") -> str:
    if not _COLOR_ENABLED or not (color or style):
        return text
    return color + style + text + Style.RESET_ALL


def asset_path(name: str) -> Path:
    """Return the packaged path of an asset shipped in ``dexonlinux/assets``."""
    return Path(str(resources.files("dexonlinux").joinpath("assets", name)))


def select_from_list(
    items: Sequence[T],
    prompt: str = "Select an item:",
    input_prompt: str = "> ",
    formatter: Callable[[T], str] = str,
    allow_refresh: bool = False,
) -> T | Literal["refresh"] | None:
    while True:
        print()
        print(colored(prompt, Fore.LIGHTWHITE_EX))
        for idx, item in enumerate(items):
            print(colored(f"{idx + 1}.", Fore.YELLOW) + f" {formatter(item)}")
        if allow_refresh:
            print(colored("r. ", Fore.YELLOW) + "Refresh")
        print(colored("q. ", Fore.YELLOW) + "Quit")
        print()
        choice = input(input_prompt).strip().lower()
        if choice == "q":
            return None
        if allow_refresh and choice == "r":
            return "refresh"
        try:
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(items):
                return items[choice_idx]
            LOG.error("Invalid choice.")
        except ValueError:
            LOG.error("Please enter a valid number.")


def confirm(prompt: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    choice = input(f"{prompt} [{suffix}] ").strip().lower()
    if not choice:
        return default
    return choice in ("y", "yes")


def print_adb_instructions() -> None:
    print(colored("ADB Connection Instructions:", Fore.LIGHTWHITE_EX, Style.BRIGHT))
    print()
    print("1. Connect your device to your PC via USB and enable USB Debugging in the developer options.")
    print("2. Allow debugging from this PC on your device if prompted (you will see the RSA key fingerprint).")
    print("3. After connecting your device, select it from the list shown below.")
    print()
    input(colored("Press Enter when you are ready to continue...\n", Fore.LIGHTYELLOW_EX))


def print_dex_instructions() -> None:
    print(colored("DeX Connection Instructions:", Fore.LIGHTWHITE_EX, Style.BRIGHT))
    print()
    print("1. Open Smart View / DeX on your phone.")
    print("2. Keep the phone unlocked and select DexOnLinux when it appears.")
    print("3. The laptop will connect using Wi-Fi Direct PBC (no PIN).")
    print("4. If the phone shows DexOnLinux as previously connected, forget it and retry.")
    print()
    print(colored("If you get disconnected, reconnect from Smart View / DeX.", Fore.LIGHTYELLOW_EX))
    print("If you encounter issues, run again with --debug and include the log when reporting it.")


def print_ascii_art() -> None:
    art = r"""
      ____             ___        _     _
     |  _ \  _____  __/ _ \ _ __ | |   (_)_ __  _   ___  __
     | | | |/ _ \ \/ / | | | '_ \| |   | | '_ \| | | \ \/ /
     | |_| |  __/>  <| |_| | | | | |___| | | | | |_| |>  <
     |____/ \___/_/\_\\___/|_| |_|_____|_|_| |_|\__,_/_/\_\
    """
    border = "═" * 70
    print(colored(f"\n╔{border}╗", Fore.LIGHTYELLOW_EX))
    for line in art.splitlines():
        if line.strip():
            centered = colored(line.center(70), Fore.LIGHTYELLOW_EX)
            print(colored("║", Fore.LIGHTYELLOW_EX) + centered + colored("║", Fore.LIGHTYELLOW_EX))
    print(colored(f"╚{border}╝\n", Fore.LIGHTYELLOW_EX))

    message = (
        "You will be asked for your sudo password to run some commands\n"
        "that require elevated privileges.\n"
        "DexOnLinux may briefly pause NetworkManager during the P2P session;\n"
        "the existing Wi-Fi connection and internet stay active.\n"
        "For help or issues, visit: https://github.com/gabdevele/DexOnLinux\n"
    )
    print(colored(message, Fore.LIGHTWHITE_EX, Style.BRIGHT))
