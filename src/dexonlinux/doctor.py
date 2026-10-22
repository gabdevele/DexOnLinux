"""Read-only host probe for the direct WFD backend."""

import logging
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dexonlinux.p2p.base import DEFAULT_P2P_FREQ
from dexonlinux.system import CommandResult, Runner

LOG = logging.getLogger(__name__)

CTRL_DIR = Path("/run/wpa_supplicant")
NET_DIR = Path("/sys/class/net")
REQUIRED_COMMANDS = ("sudo", "iw", "ip", "adb", "scrcpy")
_MAX_SUGGESTED_FREQUENCIES = 12
_FREQUENCY = re.compile(r"^\s*\*\s*(\d+)\s*MHz")


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
        phy = _phy_from_info(info)
        if phy is None:
            continue
        modes = _run(runner, ["iw", "phy", phy, "info"])
        if modes is None:
            continue
        if "P2P-client" in modes.stdout and "P2P-GO" in modes.stdout:
            interfaces.append(iface)
    return interfaces


def parse_phy_frequencies(output: str) -> list[int]:
    """Return the usable channels (MHz) listed by ``iw phy <phy> info``.

    Disabled channels and channels flagged "no IR" cannot host a P2P GO.
    """
    frequencies: list[int] = []
    for line in output.splitlines():
        match = _FREQUENCY.match(line)
        if match is None:
            continue
        lowered = line.lower()
        if "disabled" in lowered or "no ir" in lowered:
            continue
        frequencies.append(int(match.group(1)))
    return frequencies


def probe_stack(
    runner: Runner,
    interface: str,
    *,
    freq: int | None = None,
    ctrl_dir: Path = CTRL_DIR,
    which: Callable[[str], str | None] = shutil.which,
) -> StackReport:
    """Inspect the host without changing it.

    ``freq`` is the P2P frequency the session would request; when omitted the
    default (DEFAULT_P2P_FREQ) is validated instead. The frequency check is
    skipped when the adapter cannot be resolved, so unusual ``iw`` output
    never produces a false negative.
    """
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

    requested = freq if freq is not None else DEFAULT_P2P_FREQ
    phy = _wiphy(runner, interface)
    frequencies = _frequencies(runner, phy) if phy else []
    if phy and not frequencies:
        LOG.debug("could not parse any channel from 'iw phy %s info'; skipping the frequency check", phy)
    if frequencies and requested not in frequencies:
        suggestions = ", ".join(str(value) for value in frequencies[:_MAX_SUGGESTED_FREQUENCIES])
        reasons.append(
            f"frequency {requested} MHz is not supported by this adapter; "
            f"pass --p2p-freq with one of: {suggestions}"
        )

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


def _wiphy(runner: Runner, interface: str) -> str | None:
    info = _run(runner, ["iw", "dev", interface, "info"])
    return _phy_from_info(info) if info is not None else None


def _phy_from_info(info: CommandResult) -> str | None:
    for line in info.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("wiphy "):
            value = stripped.split()[1]
            return value if value.startswith("phy") else f"phy{value}"
    return None


def _frequencies(runner: Runner, phy: str) -> list[int]:
    info = _run(runner, ["iw", "phy", phy, "info"])
    return parse_phy_frequencies(info.stdout) if info is not None else []


def _run(runner: Runner, args: list[str]) -> CommandResult | None:
    try:
        return runner.run(args, check=False)
    except (OSError, RuntimeError):
        return None


def _service_active(runner: Runner, service: str) -> bool:
    result = _run(runner, ["systemctl", "is-active", service])
    return result is not None and result.stdout.strip() == "active"
