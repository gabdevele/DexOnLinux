"""ADB device and scrcpy display discovery."""

from __future__ import annotations

import re

from dexonlinux.models import AdbDevice, ScrcpyDisplay
from dexonlinux.system import Runner


def parse_adb_devices(output: str) -> list[AdbDevice]:
    devices = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        if line.startswith("*") or "daemon" in line.lower():
            continue
        parts = line.split(maxsplit=2)
        if len(parts) < 2:
            continue
        description = parts[2] if len(parts) > 2 else ""
        devices.append(AdbDevice(serial=parts[0], state=parts[1], description=description))
    return devices


def parse_scrcpy_displays(output: str) -> list[ScrcpyDisplay]:
    displays = []
    pattern = re.compile(r"--display-id=(\d+)(?:\s+\((.*?)\))?")
    resolution_pattern = re.compile(r"(\d{3,5})x(\d{3,5})")
    for line in output.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        description = match.group(2) or line.strip()
        resolution = resolution_pattern.search(description)
        width = int(resolution.group(1)) if resolution else None
        height = int(resolution.group(2)) if resolution else None
        displays.append(ScrcpyDisplay(int(match.group(1)), description, width, height))
    return displays


class Device:
    def __init__(self, runner: Runner) -> None:
        self._runner = runner

    def list_adb_devices(self) -> list[AdbDevice]:
        result = self._runner.run(["adb", "devices", "-l"])
        return parse_adb_devices(result.stdout + result.stderr)

    def list_scrcpy_displays(self, device: str) -> list[ScrcpyDisplay]:
        result = self._runner.run(["scrcpy", "-s", device, "--list-displays"])
        return parse_scrcpy_displays(result.stdout + result.stderr)
