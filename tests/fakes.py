"""Shared test doubles.

Keep fakes at public seams: tests should not depend on private implementation
details.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from dexonlinux.system import CommandResult


class FakeRunner:
    def __init__(self, outputs: Mapping[tuple[str, ...], str] | None = None) -> None:
        self.outputs = dict(outputs or {})
        self.calls: list[tuple[tuple[str, ...], bool, bool]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        sudo: bool = False,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        command = tuple(args)
        self.calls.append((command, sudo, check))
        return CommandResult(command, 0, self.outputs.get(command, ""), "")

    def popen(self, args, *, sudo=False, env=None):
        raise NotImplementedError
