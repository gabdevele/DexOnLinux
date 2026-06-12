import logging
from pathlib import Path
from colorama import Fore, Style

COLOR_ENABLED = True
USER_LOG_FORMAT = "[%(levelname)s] %(message)s"
DEBUG_LOG_FORMAT = "[%(levelname)s] (%(name)s) - %(message)s (%(filename)s:%(lineno)d)"

def colored(text: str, color: str, style: str = "") -> str:
    if not COLOR_ENABLED:
        return text
    return color + style + text + Style.RESET_ALL

class CustomFormatter(logging.Formatter):
    #straight from: https://stackoverflow.com/questions/384076/
    grey = Fore.LIGHTBLACK_EX
    yellow = Fore.LIGHTYELLOW_EX
    red = Fore.LIGHTRED_EX
    bold_red = Fore.RED
    reset = Style.RESET_ALL

    FORMATS = {
        logging.DEBUG: colored(USER_LOG_FORMAT, grey),
        logging.INFO: USER_LOG_FORMAT,
        logging.WARNING: colored(USER_LOG_FORMAT, yellow),
        logging.ERROR: colored(USER_LOG_FORMAT, red),
        logging.CRITICAL: colored(USER_LOG_FORMAT, bold_red)
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

logger = logging.getLogger("dexonlinux")
logger.setLevel(logging.INFO)
logger.propagate = False
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(CustomFormatter())
logger.addHandler(_console_handler)

def get_logger():
    return logger

def configure_logger(debug=False, no_color=False, log_file=None):
    global COLOR_ENABLED
    COLOR_ENABLED = not no_color
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    if debug:
        _console_handler.setFormatter(logging.Formatter(DEBUG_LOG_FORMAT))
    else:
        _console_handler.setFormatter(CustomFormatter() if COLOR_ENABLED else logging.Formatter(USER_LOG_FORMAT))

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(DEBUG_LOG_FORMAT))
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

def print_ascii_art():
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
            print(colored("║", Fore.LIGHTYELLOW_EX) + colored(line.center(70), Fore.LIGHTYELLOW_EX) + colored("║", Fore.LIGHTYELLOW_EX))
    print(colored(f"╚{border}╝\n", Fore.LIGHTYELLOW_EX))

    message = (
        "You will be asked for your sudo password to run some commands\n"
        "that require elevated privileges.\n"
        "DexOnLinux may temporarily take over the selected Wi-Fi interface.\n"
        "For help or issues, visit: https://github.com/gabdevele/DexOnLinux\n"
    )
    print(colored(message, Fore.LIGHTWHITE_EX, Style.BRIGHT))

def select_from_list(
    items,
    prompt: str = "Select an item:",
    input_prompt: str = "> ",
    formatter=str,
    allow_refresh: bool = False,
):
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
            logger.error("Invalid choice.")
        except ValueError:
            logger.error("Please enter a valid number.")

def confirm(prompt, default=False):
    suffix = "Y/n" if default else "y/N"
    choice = input(f"{prompt} [{suffix}] ").strip().lower()
    if not choice:
        return default
    return choice in ("y", "yes")

def print_adb_instructions():
    print(colored("ADB Connection Instructions:", Fore.LIGHTWHITE_EX, Style.BRIGHT))
    print()
    print(f"1. Connect your device to your PC via USB and enable USB Debugging in the developer options.")
    print(f"2. Allow debugging from this PC on your device if prompted (you will see the RSA key fingerprint).")
    print(f"3. After connecting your device, select it from the list shown below.")
    print()
    input(colored("Press Enter when you are ready to continue...\n", Fore.LIGHTYELLOW_EX))

def print_dex_instructions():
    print(colored("DeX Connection Instructions:", Fore.LIGHTWHITE_EX, Style.BRIGHT))
    print()
    print(f"1. Open DeX on your device.")
    print(f"2. Select 'Miracle' from the list of available displays.")
    print(f"3. The program will automatically detect when your device connects via DeX.")
    print()
    print(colored("If you get disconnected, simply reconnect your device via DeX.", Fore.LIGHTYELLOW_EX))
    print("If you encounter issues, run again with --debug and include the log when reporting it.")

def get_asset_path(name):
    package_asset = Path(__file__).resolve().parent / "assets" / name
    if package_asset.is_file():
        return str(package_asset)

    repo_asset = Path(__file__).resolve().parents[2] / "assets" / name
    return str(repo_asset)
