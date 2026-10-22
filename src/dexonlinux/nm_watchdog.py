"""Resume NetworkManager when the helper disappears.

This module runs in a separate session so that the user-side helper cleanup
(which signals the helper's process group) cannot kill it before it resumes
NetworkManager.

The pid alone is not enough: if NetworkManager is killed while stopped, the
kernel can reuse its pid before this process wakes up. The helper passes the
process creation time, and the watchdog refuses to signal a pid whose name or
creation time no longer match.
"""

import contextlib
import sys

import psutil

_CREATE_TIME_TOLERANCE = 1e-6


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        return 2
    try:
        pid = int(args[0])
        created = float(args[1])
    except ValueError:
        return 2

    # The helper keeps the write end of this pipe open for the session. EOF
    # means the helper exited or crashed, so NetworkManager must not stay
    # stopped.
    with contextlib.suppress(AttributeError, OSError):
        sys.stdin.buffer.read()

    try:
        proc = psutil.Process(pid)
        if proc.name() != "NetworkManager":
            return 0
        if abs(proc.create_time() - created) > _CREATE_TIME_TOLERANCE:
            return 0
        proc.resume()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
