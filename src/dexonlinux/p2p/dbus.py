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
import logging
import threading
from collections.abc import Callable
from typing import Protocol

from dbus_fast import BusType, DBusError, Message, MessageType, Variant
from dbus_fast.aio import MessageBus

from dexonlinux.p2p.control import SupplicantError, p2p_ctrl_name

LOG = logging.getLogger(__name__)

BUS_NAME = "fi.w1.wpa_supplicant1"
ROOT_PATH = "/fi/w1/wpa_supplicant1"
ROOT_IFACE = "fi.w1.wpa_supplicant1"
INTERFACE_IFACE = "fi.w1.wpa_supplicant1.Interface"
P2P_IFACE = "fi.w1.wpa_supplicant1.Interface.P2PDevice"
WPS_IFACE = "fi.w1.wpa_supplicant1.Interface.WPS"
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
            raise SupplicantError(f"{exc.type}: {exc.text}") from exc
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
