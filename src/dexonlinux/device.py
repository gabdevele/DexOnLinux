import re

from dexonlinux.models import AdbDevice, ScrcpyDisplay
from dexonlinux.system import Runner

_DISPLAY_ID = re.compile(r"--display-id=(\d+)(?:\s+\((.*?)\))?")
_RESOLUTION = re.compile(r"(\d{3,5})x(\d{3,5})")


def parse_adb_devices(output: str) -> list[AdbDevice]:
    devices: list[AdbDevice] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        if line.startswith("*") or "daemon" in line.lower():
            continue
        parts = line.split(maxsplit=2)
        if len(parts) < 2:
            continue
        serial, state, *rest = parts
        devices.append(AdbDevice(serial=serial, state=state, description=rest[0] if rest else ""))
    return devices


def parse_scrcpy_displays(output: str) -> list[ScrcpyDisplay]:
    displays: list[ScrcpyDisplay] = []
    for line in output.splitlines():
        match = _DISPLAY_ID.search(line)
        if match is None:
            continue
        description = match.group(2) or line.strip()
        resolution = _RESOLUTION.search(description)
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
