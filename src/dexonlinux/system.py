import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandError(RuntimeError):
    def __init__(self, message: str, result: CommandResult | None = None) -> None:
        super().__init__(message)
        self.result = result


class Runner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        sudo: bool = False,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult: ...

    def popen(
        self,
        args: Sequence[str],
        *,
        sudo: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.Popen[str]: ...


class SubprocessRunner:
    """Run commands without a shell; sudo reads a password from stdin."""

    def __init__(self, sudo_password: str = "") -> None:
        self._sudo_password = f"{sudo_password}\n" if sudo_password else ""

    def run(
        self,
        args: Sequence[str],
        *,
        sudo: bool = False,
        check: bool = True,
        timeout: float | None = None,
    ) -> CommandResult:
        command = self._command(args, sudo)
        completed = subprocess.run(
            command,
            input=self._sudo_password if sudo else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        result = CommandResult(
            tuple(args),
            completed.returncode,
            completed.stdout or "",
            completed.stderr or "",
        )
        if check and completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise CommandError(
                f"{' '.join(args)} failed ({completed.returncode}): {detail}",
                result,
            )
        return result

    def popen(
        self,
        args: Sequence[str],
        *,
        sudo: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            self._command(args, sudo),
            stdin=subprocess.PIPE if sudo else None,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if sudo and process.stdin is not None:
            process.stdin.write(self._sudo_password)
            process.stdin.flush()
            process.stdin.close()
        return process

    def ensure_sudo(self) -> None:
        try:
            self.run(["true"], sudo=True, timeout=10.0)
        except CommandError as exc:
            raise CommandError("sudo authentication failed; run the command again and enter the password") from exc

    @staticmethod
    def _command(args: Sequence[str], sudo: bool) -> list[str]:
        prefix = ["sudo", "-S", "-p", ""] if sudo else []
        return [*prefix, *args]
