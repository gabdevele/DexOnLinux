import sys
from pathlib import Path

import pytest

from dexonlinux.helper.client import HelperClient, build_helper_command
from dexonlinux.p2p.base import P2PError, P2PRole


def test_build_helper_command_uses_role_frequency_and_suspend():
    command = build_helper_command("wlp-test", role=P2PRole.GO, frequency=2412, suspend_nm=True, sudo_password="x")
    assert command[:3] == ["sudo", "-S", "-p"]
    assert command[4:6] == [sys.executable, "-m"]
    assert command[6] == "dexonlinux.helper.service"
    assert command[command.index("--role") + 1] == "go"
    assert command[command.index("--p2p-freq") + 1] == "2412"
    assert command[-1] == "--suspend-nm"


def test_build_helper_command_without_password_uses_non_interactive_sudo():
    command = build_helper_command(
        "wlp-test",
        role=P2PRole.CLIENT,
        frequency=2437,
        suspend_nm=False,
        sudo_password=None,
    )
    assert command[:2] == ["sudo", "-n"]
    assert "--suspend-nm" not in command


def test_client_reads_events_and_stops():
    client = HelperClient.start(
        "wlp-test",
        command=[sys.executable, str(Path(__file__).with_name("fake_helper.py"))],
    )
    try:
        assert client.wait_for("ready", timeout=2).data["backend"] == "direct"
        group = client.wait_for("group_started", timeout=2)
        assert group.data["ifname"] == "p2p-test"
        assert group.data["local_ip"] == "192.168.49.200"
    finally:
        client.stop()
    assert client.process.poll() is not None


def test_client_raises_on_helper_error_event():
    script = (
        "import json,time; "
        "print(json.dumps({'event':'error','data':{'message':'boom'}}), flush=True); "
        "time.sleep(30)"
    )
    client = HelperClient.start("wlp-test", command=[sys.executable, "-c", script])
    try:
        with pytest.raises(P2PError, match="boom"):
            client.wait_for("ready", timeout=2)
    finally:
        client.stop()


def test_client_reports_an_early_helper_exit():
    client = HelperClient.start(
        "wlp-test",
        command=[sys.executable, "-c", "raise SystemExit(7)"],
    )
    try:
        with pytest.raises(P2PError, match="exited with code 7"):
            client.wait_for("ready", timeout=2)
    finally:
        client.stop()
