"""wpa_supplicant D-Bus transport.

The system supplicant is owned by NetworkManager and is never stopped or
reconfigured here. This module only talks to the P2P device object that
already exists, reads and writes the WFD properties, and turns the P2P
signals into Dex events.

``DBusFastClient`` owns one asyncio loop in a daemon thread. The rest of the
project is synchronous, so every public method is a blocking wrapper around a
coroutine scheduled on that loop.
"""

import asyncio
import concurrent.futures
import contextlib
import ipaddress
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from dbus_fast import BusType, DBusError, Message, MessageType, Variant
from dbus_fast.aio import MessageBus

from dexonlinux.p2p.base import GroupInfo, P2PError, P2PRole, Peer
from dexonlinux.p2p.control import SupplicantError, p2p_ctrl_name
from dexonlinux.p2p.events import EventKind, P2PEvent
from dexonlinux.p2p.wfd import WFD_IES

LOG = logging.getLogger(__name__)

BUS_NAME = "fi.w1.wpa_supplicant1"
ROOT_PATH = "/fi/w1/wpa_supplicant1"
ROOT_IFACE = "fi.w1.wpa_supplicant1"
INTERFACE_IFACE = "fi.w1.wpa_supplicant1.Interface"
P2P_IFACE = "fi.w1.wpa_supplicant1.Interface.P2PDevice"
WPS_IFACE = "fi.w1.wpa_supplicant1.Interface.WPS"
GROUP_IFACE = "fi.w1.wpa_supplicant1.Group"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

P2P_METHODS = ("Connect", "ExtendedListen", "Disconnect")
P2P_SIGNALS = (
    "GroupStarted",
    "GroupFinished",
    "GONegotiationSuccess",
    "ProvisionDiscoveryPBCRequest",
)


class DBusUnavailable(SupplicantError):
    """The system bus or the supplicant service is not reachable."""


class DBusPermissionDenied(SupplicantError):
    """The caller is not allowed to talk to the supplicant."""


class P2PDeviceMissing(SupplicantError):
    """The supplicant has no P2P device for the requested interface."""


class WfdUnsupported(SupplicantError):
    """wpa_supplicant was built without CONFIG_WIFI_DISPLAY."""


class DBusApiIncomplete(SupplicantError):
    """The supplicant D-Bus API lacks a method, property or signal we need."""


class DBusClient(Protocol):
    def connect(self) -> None: ...

    def close(self) -> None: ...

    def find_p2p_device(self, interface: str) -> str: ...

    def probe(self, path: str) -> None: ...

    def subscribe(self, path: str, callback: Callable[[str, tuple[object, ...]], None]) -> None: ...

    def get_property(
        self, path: str, interface: str, name: str, *, timeout: float | None = None
    ) -> object: ...

    def set_property(
        self,
        path: str,
        interface: str,
        name: str,
        signature: str,
        value: object,
        *,
        timeout: float | None = None,
    ) -> None: ...

    def call(
        self, path: str, interface: str, method: str, *args: object, timeout: float | None = None
    ) -> object: ...


class DBusFastClient:
    def __init__(
        self,
        bus_factory: Callable[[], MessageBus] | None = None,
        *,
        timeout: float = 5.0,
    ) -> None:
        self._bus_factory = bus_factory or (lambda: MessageBus(bus_type=BusType.SYSTEM))
        self._timeout = timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._bus: MessageBus | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._signal_path: str | None = None
        self._signal_callback: Callable[[str, tuple[object, ...]], None] | None = None

    def connect(self) -> None:
        if self._bus is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="dbus-fast", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=self._timeout):
            self._stop_loop()
            raise DBusUnavailable("D-Bus connection timed out")
        if self._error is not None:
            error = self._error
            self._error = None
            raise DBusUnavailable(f"cannot connect to the system bus: {error}") from error

    def close(self) -> None:
        self._signal_callback = None
        self._signal_path = None
        bus = self._bus
        self._bus = None
        loop = self._loop
        self._loop = None
        if loop is None:
            return
        if bus is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(bus.disconnect)
        try:
            shutdown = asyncio.run_coroutine_threadsafe(self._cancel_pending(), loop)
            shutdown.result(timeout=1.0)
        except (RuntimeError, concurrent.futures.TimeoutError):
            pass
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)
        if not loop.is_closed():
            loop.close()

    def find_p2p_device(self, interface: str) -> str:
        return self._call(self._find_p2p_device(interface))

    def probe(self, path: str) -> None:
        self._call(self._probe(path))

    def subscribe(self, path: str, callback: Callable[[str, tuple[object, ...]], None]) -> None:
        # The message handler is installed once in _open(); swapping these two
        # attributes is all a subscription needs and stays thread-safe.
        self._signal_path = path
        self._signal_callback = callback

    def get_property(
        self, path: str, interface: str, name: str, *, timeout: float | None = None
    ) -> object:
        return self._call(self._get_property(path, interface, name), timeout)

    def set_property(
        self,
        path: str,
        interface: str,
        name: str,
        signature: str,
        value: object,
        *,
        timeout: float | None = None,
    ) -> None:
        self._call(self._set_property(path, interface, name, signature, value), timeout)

    def call(
        self, path: str, interface: str, method: str, *args: object, timeout: float | None = None
    ) -> object:
        return self._call(self._call_method(path, interface, method, *args), timeout)

    # -- loop ---------------------------------------------------------------

    def _run(self) -> None:
        loop = self._loop
        if loop is None:
            return
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._open())
        except BaseException as exc:  # noqa: BLE001 - reported to the caller
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        loop.run_forever()

    async def _open(self) -> None:
        bus = self._bus_factory()
        await bus.connect()
        bus.add_message_handler(self._on_message)
        self._bus = bus

    async def _cancel_pending(self) -> None:
        current = asyncio.current_task()
        pending = [task for task in asyncio.all_tasks() if task is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _stop_loop(self) -> None:
        loop = self._loop
        if loop is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)

    def _call(self, coroutine, timeout: float | None = None):
        loop = self._loop
        if loop is None or self._bus is None:
            coroutine.close()
            raise DBusUnavailable("D-Bus connection is not open")
        effective = self._timeout if timeout is None else timeout
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(effective)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise SupplicantError(f"D-Bus call timed out after {effective:.1f}s") from exc
        except DBusError as exc:
            error_name = str(exc.type)
            if error_name in {
                "org.freedesktop.DBus.Error.AccessDenied",
                "org.freedesktop.DBus.Error.AuthFailed",
            }:
                raise DBusPermissionDenied(
                    f"{error_name}: this user cannot read the supplicant properties"
                ) from exc
            if error_name in {
                "org.freedesktop.DBus.Error.ServiceUnknown",
                "org.freedesktop.DBus.Error.NameHasNoOwner",
                "org.freedesktop.DBus.Error.FileNotFound",
            }:
                raise DBusUnavailable(f"{error_name}: {exc.text}") from exc
            raise SupplicantError(f"{error_name}: {exc.text}") from exc
        except OSError as exc:
            raise SupplicantError(f"D-Bus I/O error: {exc}") from exc

    # -- D-Bus operations ---------------------------------------------------

    def _on_message(self, message: Message) -> bool:
        if message.message_type is not MessageType.SIGNAL:
            return False
        if message.interface != P2P_IFACE:
            return False
        if self._signal_path is None or message.path != self._signal_path:
            return False
        callback = self._signal_callback
        if callback is None:
            return False
        try:
            callback(message.member, tuple(message.body))
        except Exception:  # noqa: BLE001 - a bad listener must not kill the bus
            LOG.exception("P2P signal listener failed")
        return False

    async def _interface(self, path: str, name: str):
        node = await self._bus.introspect(BUS_NAME, path)
        proxy = self._bus.get_proxy_object(BUS_NAME, path, node)
        return proxy.get_interface(name)

    async def _get_property(self, path: str, interface: str, name: str):
        props = await self._interface(path, PROPERTIES_IFACE)
        value = await props.call_get(interface, name)
        return value.value if isinstance(value, Variant) else value

    async def _set_property(
        self, path: str, interface: str, name: str, signature: str, value: object
    ) -> None:
        props = await self._interface(path, PROPERTIES_IFACE)
        await props.call_set(interface, name, Variant(signature, value))

    async def _call_method(self, path: str, interface: str, method: str, *args: object):
        target = await self._interface(path, interface)
        handler = getattr(target, f"call_{_snake(method)}", None)
        if handler is None:
            raise SupplicantError(f"{interface}.{method} is not available")
        return await handler(*args)

    async def _find_p2p_device(self, interface: str) -> str:
        paths = await self._get_property(ROOT_PATH, ROOT_IFACE, "Interfaces")
        wanted = {f"p2p-dev-{interface}", p2p_ctrl_name(interface)}
        if interface.startswith("p2p-dev-"):
            wanted.add(interface)

        p2p_paths: list[tuple[str, str]] = []
        for path in paths:
            try:
                ifname = await self._get_property(path, INTERFACE_IFACE, "Ifname")
            except SupplicantError:
                continue
            if not isinstance(ifname, str):
                continue
            if ifname in wanted:
                return path
            if ifname.startswith("p2p-dev-"):
                p2p_paths.append((ifname, path))

        if len(p2p_paths) == 1:
            return p2p_paths[0][1]
        if not p2p_paths:
            raise P2PDeviceMissing(
                f"no P2P device for {interface}; is Wi-Fi Direct enabled?"
            )
        names = ", ".join(name for name, _path in p2p_paths)
        raise P2PDeviceMissing(f"multiple P2P devices ({names}); specify the interface")

    async def _probe(self, path: str) -> None:
        node = await self._bus.introspect(BUS_NAME, path)
        interfaces = {entry.name: entry for entry in node.interfaces}

        p2p = interfaces.get(P2P_IFACE)
        if p2p is None:
            raise P2PDeviceMissing(f"{path} has no {P2P_IFACE} interface")

        missing_methods = set(P2P_METHODS) - {method.name for method in p2p.methods}
        if missing_methods:
            raise DBusApiIncomplete(
                f"{P2P_IFACE} is missing methods: {', '.join(sorted(missing_methods))}"
            )
        missing_signals = set(P2P_SIGNALS) - {signal.name for signal in p2p.signals}
        if missing_signals:
            raise DBusApiIncomplete(
                f"{P2P_IFACE} is missing signals: {', '.join(sorted(missing_signals))}"
            )
        if "P2PDeviceConfig" not in {prop.name for prop in p2p.properties}:
            raise DBusApiIncomplete(f"{P2P_IFACE} is missing P2PDeviceConfig")

        root_node = await self._bus.introspect(BUS_NAME, ROOT_PATH)
        root = {entry.name: entry for entry in root_node.interfaces}.get(ROOT_IFACE)
        if root is None or "WFDIEs" not in {prop.name for prop in root.properties}:
            raise WfdUnsupported(
                "wpa_supplicant was built without CONFIG_WIFI_DISPLAY (no WFDIEs property)"
            )

        wps = interfaces.get(WPS_IFACE)
        if wps is None or "ConfigMethods" not in {prop.name for prop in wps.properties}:
            raise DBusApiIncomplete(f"{path} has no WPS ConfigMethods property")


def _snake(name: str) -> str:
    """Turn a D-Bus member name into the dbus-fast generated method suffix."""
    out: list[str] = []
    for char in name:
        if char.isupper() and out:
            out.append("_")
        out.append(char.lower())
    return "".join(out)


@dataclass(frozen=True)
class DBusWfdState:
    """WFD-related supplicant state saved before ``prepare_wfd``."""

    wfd_ies: bytes
    go_intent: int
    device_name: str
    config_methods: str


class WpaSupplicantDBus:
    """``P2PControl`` backed by the official wpa_supplicant D-Bus API."""

    def __init__(self, interface: str, client: DBusClient | None = None) -> None:
        self.interface = interface
        self._client = client or DBusFastClient()
        self._device: str | None = None
        self._group_interface: str | None = None
        self._negotiated_peer: Peer | None = None
        self._signals: queue.Queue[tuple[str, tuple[object, ...]]] = queue.Queue()
        self._open = False

    @property
    def device(self) -> str | None:
        return self._device

    def open(self) -> None:
        if self._open:
            return
        self._client.connect()
        try:
            self._device = self._client.find_p2p_device(self.interface)
            self._client.probe(self._device)
            self._client.subscribe(self._device, self._on_signal)
        except SupplicantError:
            self._client.close()
            raise
        self._open = True

    def close(self) -> None:
        self._client.close()
        self._open = False
        self._device = None
        self._group_interface = None
        self._negotiated_peer = None

    def prepare_wfd(self, *, go_intent: int, device_name: str) -> DBusWfdState:
        device = self._require_device()
        config = self._client.get_property(device, P2P_IFACE, "P2PDeviceConfig")
        if not isinstance(config, dict):
            raise SupplicantError("P2PDeviceConfig is not a dictionary")
        ies = self._client.get_property(ROOT_PATH, ROOT_IFACE, "WFDIEs")
        methods = self._client.get_property(device, WPS_IFACE, "ConfigMethods")
        state = DBusWfdState(
            wfd_ies=ies if isinstance(ies, bytes) else b"",
            go_intent=int(self._config_value(config, "GOIntent", 0)),
            device_name=str(self._config_value(config, "DeviceName", "")),
            config_methods=methods if isinstance(methods, str) else "",
        )

        try:
            self._client.set_property(ROOT_PATH, ROOT_IFACE, "WFDIEs", "ay", WFD_IES)
            self._client.set_property(
                device,
                P2P_IFACE,
                "P2PDeviceConfig",
                "a{sv}",
                {
                    "GOIntent": Variant("u", go_intent),
                    "DeviceName": Variant("s", device_name),
                },
            )
            self._client.set_property(device, WPS_IFACE, "ConfigMethods", "s", "0x80")
        except SupplicantError as exc:
            # A partial apply can leave WFD enabled or the name changed; put
            # the supplicant back before the error reaches the caller.
            self.restore_wfd(state)
            raise P2PError(f"could not configure Wi-Fi Display: {exc}") from exc
        return state

    def restore_wfd(self, state: object) -> None:
        if not isinstance(state, DBusWfdState):
            LOG.debug("ignoring an unknown WFD state: %r", state)
            return
        device = self._device
        if device is None:
            return

        self._best_effort(self.stop_extended_listen, "stop extended listen")
        # An empty array disables Wi-Fi Display in the supplicant setter.
        self._best_effort(
            lambda: self._client.set_property(ROOT_PATH, ROOT_IFACE, "WFDIEs", "ay", state.wfd_ies),
            "restore WFDIEs",
        )
        self._best_effort(
            lambda: self._client.set_property(
                device,
                P2P_IFACE,
                "P2PDeviceConfig",
                "a{sv}",
                {
                    "GOIntent": Variant("u", state.go_intent),
                    "DeviceName": Variant("s", state.device_name),
                },
            ),
            "restore P2PDeviceConfig",
        )
        if state.config_methods:
            self._best_effort(
                lambda: self._client.set_property(
                    device, WPS_IFACE, "ConfigMethods", "s", state.config_methods
                ),
                "restore ConfigMethods",
            )

    def start_extended_listen(self, period_ms: int, interval_ms: int) -> None:
        self._client.call(
            self._require_device(),
            P2P_IFACE,
            "ExtendedListen",
            {"period": period_ms, "interval": interval_ms},
        )

    def stop_extended_listen(self) -> None:
        self._client.call(self._require_device(), P2P_IFACE, "ExtendedListen", {})

    def connect(self, peer: Peer, *, frequency: int, go_intent: int) -> None:
        if not peer.path:
            raise SupplicantError(
                f"peer {peer.mac} has no D-Bus object path; the signal did not carry one"
            )
        self._client.call(
            self._require_device(),
            P2P_IFACE,
            "Connect",
            {
                "peer": peer.path,
                "wps_method": "pbc",
                "frequency": frequency,
                "go_intent": go_intent,
            },
        )

    def disconnect(self) -> None:
        interface = self._group_interface
        if interface is None:
            return
        try:
            self._client.call(interface, P2P_IFACE, "Disconnect")
        finally:
            self._group_interface = None

    def pump(self, timeout: float) -> list[P2PEvent]:
        events: list[P2PEvent] = []
        try:
            member, args = self._signals.get(timeout=timeout)
        except queue.Empty:
            return events
        self._map_signal(member, args, events)
        while True:
            try:
                member, args = self._signals.get_nowait()
            except queue.Empty:
                return events
            self._map_signal(member, args, events)

    def _on_signal(self, member: str, args: tuple[object, ...]) -> None:
        # Runs on the D-Bus loop thread: only queue, never call back into D-Bus.
        self._signals.put((member, args))

    def _map_signal(self, member: str, args: tuple[object, ...], events: list[P2PEvent]) -> None:
        if member == "ProvisionDiscoveryPBCRequest":
            peer = _peer_from_path(args[0] if args else None)
            if peer is not None:
                events.append(P2PEvent(EventKind.PBC_REQUEST, peer=peer, raw="ProvisionDiscoveryPBCRequest"))
        elif member in {"ProvisionDiscoveryRequestDisplayPin", "ProvisionDiscoveryRequestEnterPin"}:
            peer = _peer_from_path(args[0] if args else None)
            events.append(P2PEvent(EventKind.PIN_REQUEST, peer=peer, raw=member))
        elif member == "GONegotiationRequest":
            peer = _peer_from_path(args[0] if args else None)
            passwd_id = int(args[1]) if len(args) > 1 else None
            events.append(
                P2PEvent(EventKind.GO_NEGOTIATION, peer=peer, passwd_id=passwd_id, raw=member)
            )
        elif member == "GONegotiationSuccess":
            props = _properties(args[0] if args else None)
            peer = _peer_from_path(props.get("peer_object"))
            self._negotiated_peer = peer
            events.append(P2PEvent(EventKind.GO_NEGOTIATION_SUCCESS, peer=peer, raw=member))
        elif member == "GONegotiationFailure":
            props = _properties(args[0] if args else None)
            events.append(
                P2PEvent(
                    EventKind.FAILURE,
                    raw=f"GONegotiationFailure status={props.get('status', 'unknown')}",
                )
            )
        elif member == "GroupStarted":
            events.append(self._group_started(_properties(args[0] if args else None)))
        elif member == "GroupFinished":
            self._group_interface = None
            self._negotiated_peer = None
            events.append(P2PEvent(EventKind.GROUP_REMOVED, raw="P2P group finished"))
        elif member == "InvitationReceived":
            props = _properties(args[0] if args else None)
            mac = _mac(props.get("go_dev_addr")) or _mac(props.get("sa"))
            events.append(
                P2PEvent(
                    EventKind.INVITATION,
                    peer=Peer(mac=mac) if mac else None,
                    raw="P2P invitation received",
                )
            )
        elif member == "DeviceFoundProperties":
            path = args[0] if args else None
            props = _properties(args[1] if len(args) > 1 else None)
            peer = Peer(
                mac=_mac_from_path(path) if isinstance(path, str) else "",
                name=str(props.get("DeviceName", "")),
                wfd=bool(props.get("IEs")),
                path=path if isinstance(path, str) else "",
            )
            events.append(P2PEvent(EventKind.DEVICE_FOUND, peer=peer, raw=member))
        else:
            LOG.debug("ignoring P2P signal %s", member)

    def _group_started(self, props: dict[str, object]) -> P2PEvent:
        try:
            interface = props.get("interface_object")
            group_object = props.get("group_object")
            role = P2PRole.GO if props.get("role") == "GO" else P2PRole.CLIENT
            if not isinstance(interface, str):
                raise ValueError("GroupStarted has no interface object")
            ifname = self._client.get_property(interface, INTERFACE_IFACE, "Ifname")
            if not isinstance(ifname, str) or not ifname:
                raise ValueError("GroupStarted interface has no name")
            frequency = (
                self._client.get_property(group_object, GROUP_IFACE, "Frequency")
                if isinstance(group_object, str)
                else None
            )

            if role is P2PRole.CLIENT:
                local_ip = _ipv4(props.get("IpAddr"))
                peer_ip = _ipv4(props.get("IpAddrGo"))
                prefix = _prefix(props.get("IpAddrMask"))
                go_dev_addr = self._negotiated_peer.mac if self._negotiated_peer is not None else ""
            else:
                local_ip = None
                peer_ip = None
                prefix = 24
                go_dev_addr = _mac(self._client.get_property(self._require_device(), P2P_IFACE, "DeviceAddress"))

            group = GroupInfo(
                ifname=ifname,
                role=role,
                local_ip=local_ip,
                peer_ip=peer_ip,
                local_prefix=prefix,
                frequency=int(frequency) if isinstance(frequency, int) else None,
                go_dev_addr=go_dev_addr,
            )
        except (SupplicantError, ValueError, TypeError) as exc:
            return P2PEvent(EventKind.FAILURE, raw=f"could not read the started group: {exc}")

        self._group_interface = interface
        return P2PEvent(EventKind.GROUP_STARTED, group=group, raw="P2P group started")

    def _require_device(self) -> str:
        if self._device is None:
            raise SupplicantError("P2P control is not open")
        return self._device

    @staticmethod
    def _config_value(config: dict[str, object], key: str, default: object) -> object:
        value = config.get(key, default)
        return value.value if isinstance(value, Variant) else value

    @staticmethod
    def _best_effort(action: Callable[[], None], description: str) -> None:
        try:
            action()
        except SupplicantError as exc:
            LOG.debug("%s failed: %s", description, exc)


def _unwrap(value: object) -> object:
    return value.value if isinstance(value, Variant) else value


def _properties(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: _unwrap(item) for key, item in value.items()}


def _mac(value: object) -> str:
    if isinstance(value, (bytes, bytearray)) and len(value) == 6:
        return ":".join(f"{byte:02x}" for byte in value)
    if isinstance(value, str) and len(value) == 17 and value.count(":") == 5:
        return value
    return ""


def _mac_from_path(path: object) -> str:
    if not isinstance(path, str):
        return ""
    segment = path.rsplit("/", 1)[-1]
    if len(segment) == 17 and segment.count("_") == 5:
        return segment.replace("_", ":")
    return ""


def _peer_from_path(path: object) -> Peer | None:
    if not isinstance(path, str) or not path:
        return None
    mac = _mac_from_path(path)
    if not mac:
        return None
    return Peer(mac=mac, path=path)


def _ipv4(value: object) -> str | None:
    if isinstance(value, (bytes, bytearray)) and len(value) == 4:
        return str(ipaddress.IPv4Address(bytes(value)))
    return None


def _prefix(mask: object) -> int:
    if isinstance(mask, (bytes, bytearray)) and len(mask) == 4:
        netmask = ipaddress.IPv4Address(bytes(mask))
        return ipaddress.IPv4Network(f"0.0.0.0/{netmask}").prefixlen
    return 24


@dataclass(frozen=True)
class DBusProbe:
    """Result of a read-only D-Bus capability check.

    ``available`` is ``None`` when the check could not be performed, for
    example when an unprivileged doctor is not allowed to read the
    supplicant's properties. The privileged helper then makes the call.
    """

    available: bool | None
    detail: str = ""


def probe_dbus(interface: str) -> DBusProbe:
    control = WpaSupplicantDBus(interface)
    try:
        control.open()
    except DBusPermissionDenied as exc:
        return DBusProbe(None, str(exc))
    except SupplicantError as exc:
        return DBusProbe(False, str(exc))
    finally:
        control.close()
    return DBusProbe(True)
