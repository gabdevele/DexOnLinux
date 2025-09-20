import logging, os
from typing import List, Optional
from colorama import Fore, Style


def colored(text: str, color: str, style: str = "") -> str:
    return color + style + text + Style.RESET_ALL

class CustomFormatter(logging.Formatter):
    #straight from: https://stackoverflow.com/questions/384076/
    grey = Fore.LIGHTBLACK_EX
    yellow = Fore.LIGHTYELLOW_EX
    red = Fore.LIGHTRED_EX
    bold_red = Fore.RED
    reset = Style.RESET_ALL
    format = "[%(levelname)s] (%(name)s) - %(message)s (%(filename)s:%(lineno)d)"

    FORMATS = {
        logging.DEBUG: colored(format, grey),
        logging.INFO: colored(format, grey),
        logging.WARNING: colored(format, yellow),
        logging.ERROR: colored(format, red),
        logging.CRITICAL: colored(format, bold_red)
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

handler = logging.StreamHandler()
handler.setFormatter(CustomFormatter())

logger = logging.getLogger("dexonlinux")
logger.setLevel(logging.DEBUG)
logger.addHandler(handler)
logger.propagate = False

def get_logger() -> logging.Logger:
    return logger

def print_ascii_art() -> None:
    art = r"""
      ____             ___        _     _                  
     |  _ \  _____  __/ _ \ _ __ | |   (_)_ __  _   ___  __
     | | | |/ _ \ \/ / | | | '_ \| |   | | '_ \| | | \ \/ /
     | |_| |  __/>  <| |_| | | | | |___| | | | | |_| |>  < 
     |____/ \___/_/\_\\___/|_| |_|_____|_|_| |_|\__,_/_/\_\
    """
    print(colored(art.center(90), Fore.LIGHTYELLOW_EX))
    message = (
        "You will be asked for your sudo password to run some commands\n"
        "such as 'systemctl', 'miracle-wifid', etc...\n"
    )
    print(colored(message, Fore.LIGHTWHITE_EX))

def select_from_list(items: List[str], prompt: str = "Select an item:", input_prompt: str = "> ") -> Optional[str]:
    print()
    print(colored(prompt, Fore.LIGHTWHITE_EX))
    for idx, item in enumerate(items):
        print(colored(f"{idx + 1}.", Fore.YELLOW) + f" {item}")
    print(colored("q. ", Fore.YELLOW) + "Quit")
    print()
    choice = input(input_prompt)
    if choice.lower() == 'q':
        return None
    try:
        choice_idx = int(choice) - 1
        if 0 <= choice_idx < len(items):
            return items[choice_idx]
        else:
            logger.error("Invalid choice.")
            return None
    except ValueError:
        logger.error("Please enter a valid number.")
        return None

def error_exit(message: str, enable_network: bool = False, commands = None) -> None:
    logger.error(message)
    if enable_network and commands:
        commands.kill_miracle()
        commands.enable_network_services()
    exit(1)

def get_app_path() -> str:
    return os.path.dirname(os.path.realpath(__file__))
