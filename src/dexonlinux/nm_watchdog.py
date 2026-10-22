"""Resume NetworkManager when the helper disappears.

This module runs in a separate session so that the user-side helper cleanup
(which signals the helper's process group) cannot kill it before it resumes
NetworkManager.
"""

import contextlib
import sys

import psutil


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        return 2
    try:
        pid = int(args[0])
    except ValueError:
        return 2

    # The helper keeps the write end of this pipe open for the session. EOF
    # means the helper exited or crashed, so NetworkManager must not stay
    # stopped.
    with contextlib.suppress(AttributeError, OSError):
        sys.stdin.buffer.read()

    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
        psutil.Process(pid).resume()
    return 0


if __name__ == "__main__":
    sys.exit(main())
