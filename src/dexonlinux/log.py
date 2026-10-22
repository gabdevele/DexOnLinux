"""Logging configuration for the user-facing CLI.

The "dexonlinux" logger is configured once from the CLI arguments; library
modules only call ``logging.getLogger(__name__)`` and never add handlers.
"""

import logging
from pathlib import Path

from colorama import Fore

from dexonlinux.console import colored, set_color

USER_LOG_FORMAT = "[%(levelname)s] %(message)s"
DEBUG_LOG_FORMAT = "[%(levelname)s] (%(name)s) - %(message)s (%(filename)s:%(lineno)d)"
LOGGER_NAME = "dexonlinux"

_LEVEL_COLORS = {
    logging.DEBUG: Fore.LIGHTBLACK_EX,
    logging.WARNING: Fore.LIGHTYELLOW_EX,
    logging.ERROR: Fore.LIGHTRED_EX,
    logging.CRITICAL: Fore.RED,
}


class LevelColorFormatter(logging.Formatter):
    """Color the whole user-facing line according to the record level."""

    def __init__(self, color: bool) -> None:
        super().__init__(USER_LOG_FORMAT)
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        color = _LEVEL_COLORS.get(record.levelno, "")
        if not self._color or not color:
            return text
        return colored(text, color)


def setup_logging(*, debug: bool = False, color: bool = True, log_file: str | Path | None = None) -> None:
    """Configure the package logger; safe to call more than once."""
    set_color(color)
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(DEBUG_LOG_FORMAT) if debug else LevelColorFormatter(color))
    logger.addHandler(stream)

    if log_file is not None:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(DEBUG_LOG_FORMAT))
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)
