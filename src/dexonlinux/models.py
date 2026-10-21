"""Small data types for ADB and scrcpy discovery."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    description: str = ""

    @property
    def is_authorized(self) -> bool:
        return self.state == "device"

    def label(self) -> str:
        suffix = f" {self.description}" if self.description else ""
        return f"{self.serial} ({self.state}){suffix}"


@dataclass(frozen=True)
class ScrcpyDisplay:
    display_id: int
    description: str = ""
    width: int | None = None
    height: int | None = None

    def label(self) -> str:
        details = []
        if self.description:
            details.append(self.description)
        hint = self.hint()
        if hint:
            details.append(hint)
        if not details:
            details.append("unknown display")
        suffix = f" - {' | '.join(details)}" if details else ""
        return f"display {self.display_id}{suffix}"

    def hint(self) -> str:
        if not self.width or not self.height:
            return ""
        if self.width > self.height:
            return "likely DeX / external display (recommended)"
        if self.height > self.width:
            return "likely phone screen"
        return ""
