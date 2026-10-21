import subprocess

import pytest

from dexonlinux.system import CommandError, CommandResult, SubprocessRunner


def test_run_builds_sudo_command_and_passes_password(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setattr("dexonlinux.system.subprocess.run", fake_run)
    result = SubprocessRunner("secret").run(["nmcli", "x"], sudo=True)

    assert captured["command"] == ["sudo", "-S", "-p", "", "nmcli", "x"]
    assert captured["kwargs"]["input"] == "secret\n"
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["timeout"] is None
    assert result == CommandResult(("nmcli", "x"), 0, "ok\n", "")


def test_run_check_raises_with_command_error(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "nope\n")

    monkeypatch.setattr("dexonlinux.system.subprocess.run", fake_run)
    with pytest.raises(CommandError, match="nope"):
        SubprocessRunner().run(["false"])
    assert SubprocessRunner().run(["false"], check=False).returncode == 1


def test_ensure_sudo_uses_a_sudo_probe(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("dexonlinux.system.subprocess.run", fake_run)
    SubprocessRunner("secret").ensure_sudo()
    assert captured["command"] == ["sudo", "-S", "-p", "", "true"]
