"""Read-only host probe for the direct WFD backend."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dexonlinux.system import CommandResult, Runner

CTRL_DIR = Path("/run/wpa_supplicant")
NET_DIR = Path("/sys/class/net")
REQUIRED_COMMANDS = ("sudo", "iw", "ip", "adb", "scrcpy")


@dataclass(frozen=True)
class StackReport:
    manager: str
    backend: str
    supported: bool
    reasons: tuple[str, ...]
    suspend_nm: bool = False


def list_p2p_interfaces(
    runner: Runner,
    *,
    net_dir: Path = NET_DIR,
) -> list[str]:
    """Return wireless interfaces whose phy supports P2P client and GO."""
    try:
        entries = sorted(Path(net_dir).iterdir())
    except OSError:
        return []

    interfaces = []
    for path in entries:
        if not path.is_dir() or not (path / "wireless").exists():
            continue
        iface = path.name
        info = _run(runner, ["iw", "dev", iface, "info"])
        if info is None:
            continue
        wiphy = next(
            (line.split()[1] for line in info.stdout.splitlines() if line.strip().startswith("wiphy ")),
            None,
        )
        if not wiphy:
            continue
        phy = wiphy if wiphy.startswith("phy") else f"phy{wiphy}"
        modes = _run(runner, ["iw", "phy", phy, "info"])
        if modes is None:
            continue
        if "P2P-client" in modes.stdout and "P2P-GO" in modes.stdout:
            interfaces.append(iface)
    return interfaces


def probe_stack(
    runner: Runner,
    interface: str,
    *,
    ctrl_dir: Path = CTRL_DIR,
    which: Callable[[str], str | None] = shutil.which,
) -> StackReport:
    """Inspect the host without changing it."""
    nm_active = _service_active(runner, "NetworkManager")
    iwd_active = _service_active(runner, "iwd")
    connman_active = _service_active(runner, "connman")

    reasons: list[str] = []
    manager = "NetworkManager" if nm_active else "standalone"
    suspend_nm = nm_active

    if iwd_active:
        reasons.append("network stack: NetworkManager + iwd is not supported in v1")
    if connman_active:
        reasons.append("network stack: ConnMan is not supported in v1")

    ctrl_path = Path(ctrl_dir)
    if not ctrl_path.exists():
        reasons.append(f"no P2P control directory at {ctrl_dir}; is Wi-Fi Direct enabled?")
    elif os.access(ctrl_path, os.R_OK | os.X_OK):
        ctrl_sockets = [path for path in ctrl_path.glob("p2p-dev-*") if path.exists()]
        if not ctrl_sockets:
            reasons.append(f"no P2P control socket in {ctrl_dir}; is Wi-Fi Direct enabled?")
    # If the directory is root-only, the unprivileged doctor cannot inspect it;
    # the privileged helper will validate the socket. Do not report a false
    # negative just because the CLI is not running as root.

    dev = _run(runner, ["iw", "dev"])
    if dev is None:
        reasons.append("cannot read Wi-Fi devices with 'iw dev'")
    else:
        if f"Interface {interface}" not in dev.stdout:
            reasons.append(f"interface {interface} is not present in 'iw dev'")
        if "type P2P-device" not in dev.stdout:
            reasons.append("the selected interface has no P2P device")

    modes = _run(runner, ["iw", "list"])
    if modes is None:
        reasons.append("cannot read Wi-Fi capabilities with 'iw list'")
    else:
        if "P2P-client" not in modes.stdout:
            reasons.append("driver does not advertise P2P-client mode")
        if "P2P-GO" not in modes.stdout:
            reasons.append("driver does not advertise P2P-GO mode")

    missing = [name for name in REQUIRED_COMMANDS if which(name) is None]
    if missing:
        reasons.append("missing commands: " + ", ".join(missing))

    return StackReport(
        manager=manager,
        backend="direct",
        supported=not reasons,
        reasons=tuple(reasons),
        suspend_nm=suspend_nm,
    )


def _run(runner: Runner, args: list[str]) -> CommandResult | None:
    try:
        return runner.run(args, check=False)
    except (OSError, RuntimeError):
        return None


def _service_active(runner: Runner, service: str) -> bool:
    result = _run(runner, ["systemctl", "is-active", service])
    return result is not None and result.stdout.strip() == "active"
