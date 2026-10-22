"""Shared test doubles.

Keep fakes at public seams: tests should not depend on private implementation
details.
"""

from collections.abc import Mapping, Sequence

from dexonlinux.system import CommandResult


class FakeRunner:
    def __init__(
        self,
        outputs: Mapping[tuple[str, ...], str] | None = None,
        returncodes: Mapping[tuple[str, ...], int] | None = None,
    ) -> None:
        self.outputs = dict(outputs or {})
        self.returncodes = dict(returncodes or {})
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
        return CommandResult(
            command,
            self.returncodes.get(command, 0),
            self.outputs.get(command, ""),
            "",
        )

    def popen(self, args, *, sudo=False, env=None):
        raise NotImplementedError
