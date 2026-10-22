"""Privileged helper process: owns the P2P control connection and the group.

It reports machine-readable JSON events on stdout and never stops or
reconfigures the system supplicant service.
"""

import argparse
import json
import logging
import signal
import sys
import threading
from collections.abc import Callable
from dataclasses import asdict

from dexonlinux.netlink import Pyroute2LinkOps
from dexonlinux.nm import NetworkManagerSuspension
from dexonlinux.p2p.base import DEFAULT_P2P_FREQ, GroupInfo, P2PBackend, P2PError, P2PRole
from dexonlinux.p2p.control import P2PControl
from dexonlinux.p2p.dbus import WpaSupplicantDBus
from dexonlinux.p2p.dhcp import DhcpServer
from dexonlinux.p2p.direct import DirectBackend
from dexonlinux.p2p.link import LinkConfigurator
from dexonlinux.p2p.supplicant import SupplicantControl, SupplicantError, find_ctrl_socket
from dexonlinux.system import CommandError, SubprocessRunner

LOG = logging.getLogger(__name__)
Emitter = Callable[[str, dict[str, object]], None]


def emit(event: str, data: dict[str, object]) -> None:
    print(json.dumps({"event": event, "data": data}, separators=(",", ":")), flush=True)


def open_control(interface: str, transport: str) -> tuple[P2PControl, str]:
    """Open the requested P2P transport and return it with its name.

    ``auto`` prefers D-Bus and only falls back to the control socket when the
    D-Bus API is missing or unusable. ``dbus`` never falls back, so a caller
    that explicitly asked for it sees the real error.
    """
    dbus_error: SupplicantError | None = None
    if transport != "ctrl":
        dbus = WpaSupplicantDBus(interface)
        try:
            dbus.open()
            return dbus, "dbus"
        except SupplicantError as exc:
            if transport == "dbus":
                raise
            dbus_error = exc
            LOG.warning("D-Bus transport unavailable (%s); falling back to the control socket", exc)

    ctrl = SupplicantControl(find_ctrl_socket(interface))
    try:
        ctrl.open()
    except SupplicantError as exc:
        if dbus_error is not None:
            raise SupplicantError(f"D-Bus: {dbus_error}; control socket: {exc}") from exc
        raise
    return ctrl, "ctrl"


def serve(
    backend: P2PBackend,
    stop: threading.Event,
    emitter: Emitter,
    backend_name: str,
    interface: str,
    transport: str,
) -> int:
    try:
        backend.prepare()
        emitter(
            "ready",
            {"backend": backend_name, "interface": interface, "transport": transport},
        )
        group: GroupInfo = backend.connect(stop)
        emitter("group_started", asdict(group))
        reason = backend.wait_for_group_end(stop)
        if reason is not None:
            emitter("group_finished", {"reason": reason})
        return 0
    except (P2PError, SupplicantError, CommandError) as exc:
        if not stop.is_set():
            emitter("error", {"code": exc.__class__.__name__, "message": str(exc)})
            return 1
        return 0
    except Exception as exc:  # noqa: BLE001 - report unexpected helper failures
        if not stop.is_set():
            LOG.exception("unexpected helper failure")
            emitter("error", {"code": exc.__class__.__name__, "message": str(exc)})
            return 1
        return 0
    finally:
        try:
            backend.close()
        except Exception as exc:  # noqa: BLE001 - cleanup must not hide the result
            LOG.warning("helper cleanup failed: %s", exc)


def build_backend(
    interface: str,
    control: P2PControl,
    *,
    role: P2PRole,
    frequency: int,
    suspension: NetworkManagerSuspension,
) -> DirectBackend:
    """Assemble the backend for one session.

    Kept separate from ``main`` so the wiring is exercised without a real
    supplicant: a signature drift here used to reach the user as a helper
    crash before the first event.
    """
    return DirectBackend(
        interface,
        control,
        LinkConfigurator(Pyroute2LinkOps(), SubprocessRunner()),
        role=role,
        frequency=frequency,
        dhcp=DhcpServer() if role is P2PRole.GO else None,
        before_group=suspension.suspend,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DexOnLinux privileged P2P helper")
    parser.add_argument("--interface", required=True)
    parser.add_argument("--backend", choices=["direct"], default="direct")
    parser.add_argument(
        "--p2p-transport",
        choices=["auto", "dbus", "ctrl"],
        default="auto",
        help="P2P transport: auto prefers D-Bus and falls back to the control socket.",
    )
    parser.add_argument(
        "--role",
        choices=[role.value for role in P2PRole],
        default=P2PRole.GO.value,
        help="requested P2P role: go is the product path, client is for diagnostics",
    )
    parser.add_argument(
        "--p2p-freq",
        type=int,
        default=DEFAULT_P2P_FREQ,
        help=f"P2P operating frequency in MHz (default: {DEFAULT_P2P_FREQ}, channel 6)",
    )
    parser.add_argument(
        "--suspend-nm",
        action="store_true",
        help="SIGSTOP NetworkManager only while the P2P group is being formed; "
        "resume it automatically on exit (Wi-Fi stays connected).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    stop = threading.Event()

    def handle_signal(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    suspension = NetworkManagerSuspension(args.suspend_nm)
    try:
        role = P2PRole(args.role)
        control, transport = open_control(args.interface, args.p2p_transport)
        backend = build_backend(
            args.interface,
            control,
            role=role,
            frequency=args.p2p_freq,
            suspension=suspension,
        )
        return serve(backend, stop, emit, args.backend, args.interface, transport)
    except (SupplicantError, CommandError) as exc:
        emit("error", {"code": exc.__class__.__name__, "message": str(exc)})
        return 1
    except Exception as exc:  # noqa: BLE001 - never exit with a bare traceback
        LOG.exception("unexpected helper failure")
        emit("error", {"code": exc.__class__.__name__, "message": str(exc)})
        return 1
    finally:
        suspension.resume()


if __name__ == "__main__":
    sys.exit(main())
