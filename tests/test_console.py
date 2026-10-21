import logging

import pytest

from dexonlinux import console, log


@pytest.fixture(autouse=True)
def _restore_color():
    yield
    console.set_color(True)


def test_colored_is_identity_when_disabled():
    console.set_color(False)
    assert console.colored("text", console.Fore.RED) == "text"


def test_colored_wraps_with_reset_when_enabled():
    console.set_color(True)
    assert "\x1b[" in console.colored("text", console.Fore.RED)


def test_asset_path_resolves_the_packaged_icon():
    path = console.asset_path("icon.png")
    assert path.name == "icon.png"
    assert path.is_file()


def test_confirm_uses_default_and_yes(monkeypatch):
    answers = iter(["", "y", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    assert console.confirm("go?", default=True) is True
    assert console.confirm("go?", default=False) is True
    assert console.confirm("go?", default=True) is False


def test_select_from_list_returns_item_or_none(monkeypatch):
    items = ["a", "b"]
    answers = iter(["2", "q"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    assert console.select_from_list(items, "pick:") == "b"
    assert console.select_from_list(items, "pick:") is None


def _stream_handlers():
    return [
        handler
        for handler in logging.getLogger("dexonlinux").handlers
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
    ]


def test_setup_logging_is_idempotent_and_colorless(tmp_path):
    logger = logging.getLogger("dexonlinux")
    log_file = tmp_path / "dex.log"
    log.setup_logging(debug=False, color=False, log_file=log_file)
    log.setup_logging(debug=False, color=False, log_file=log_file)

    streams = _stream_handlers()
    assert len(streams) == 1
    record = logging.LogRecord("dexonlinux.test", logging.ERROR, __file__, 1, "boom", None, None)
    assert streams[0].formatter.format(record) == "[ERROR] boom"

    logger.warning("hello file")
    for handler in logger.handlers:
        handler.flush()
    assert "hello file" in log_file.read_text(encoding="utf-8")


def test_setup_logging_colors_levels_when_enabled():
    log.setup_logging(debug=False, color=True)
    record = logging.LogRecord("dexonlinux.test", logging.ERROR, __file__, 1, "boom", None, None)
    assert "\x1b[" in _stream_handlers()[0].formatter.format(record)
