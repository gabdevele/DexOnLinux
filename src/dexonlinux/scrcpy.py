"""scrcpy process lifecycle and display selection."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time

from dexonlinux.device import Device
from dexonlinux.system import CommandError, Runner
from dexonlinux.utils import get_asset_path, select_from_list

LOG = logging.getLogger(__name__)


class ScrcpySession:
    def __init__(
        self,
        device: str,
        runner: Runner,
        *,
        display_id: int | None = None,
        fullscreen: bool = False,
    ) -> None:
        self.device = device
        self._runner = runner
        self.display_id = display_id
        self.fullscreen = fullscreen
        self._process: subprocess.Popen[str] | None = None
        self._drain_thread: threading.Thread | None = None

    def start(self) -> subprocess.Popen[str]:
        display_id = self._resolve_display_id()
        env = os.environ.copy()
        icon_path = get_asset_path("icon.png")
        if os.path.isfile(icon_path):
            env["SCRCPY_ICON_PATH"] = icon_path
            env["SCRCPY_ICON_DIR"] = os.path.dirname(icon_path)

        args = ["-s", self.device, "--window-title", "DexOnLinux", "--mouse-bind=++++"]
        if self.fullscreen:
            args.append("--fullscreen")
        if display_id is not None:
            args.extend(["--display-id", str(display_id)])

        process = subprocess.Popen(
            ["scrcpy", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        time.sleep(0.2)
        if process.poll() is not None:
            raise CommandError("scrcpy exited immediately after startup")
        self._process = process
        self._drain_thread = threading.Thread(target=self._drain, args=(process,), daemon=True)
        self._drain_thread.start()
        return process

    def wait(self) -> None:
        if self._process is not None:
            self._process.wait()

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)

    def _resolve_display_id(self) -> int | None:
        if self.display_id is not None:
            return self.display_id

        displays = Device(self._runner).list_scrcpy_displays(self.device)
        if not displays:
            LOG.warning("No scrcpy display id found; scrcpy will use its default display.")
            return None

        print()
        print("Hint: DeX is usually the landscape 16:9 display; the phone screen is usually portrait 9:16.")
        selected = select_from_list(
            displays,
            "Select the display to open with scrcpy:",
            formatter=lambda display: display.label(),
        )
        if selected is None:
            raise CommandError("No scrcpy display selected.")
        return selected.display_id

    @staticmethod
    def _drain(process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in iter(process.stdout.readline, ""):
            text = line.rstrip("\n")
            if text:
                LOG.debug("scrcpy: %s", text)
        process.stdout.close()
