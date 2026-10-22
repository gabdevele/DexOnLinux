import asyncio

import pytest
from dbus_fast import Variant
from dbus_fast import introspection as intr
from dbus_fast.constants import PropertyAccess
from dbus_fast.errors import DBusError

from dexonlinux.p2p.dbus import (
    DBusApiIncomplete,
    DBusFastClient,
    P2PDeviceMissing,
    SupplicantError,
    WfdUnsupported,
    _snake,
)

BUS_NAME = "fi.w1.wpa_supplicant1"
ROOT = "/fi/w1/wpa_supplicant1"
ROOT_IFACE = "fi.w1.wpa_supplicant1"
IFACE = "fi.w1.wpa_supplicant1.Interface"
P2P_IFACE = "fi.w1.wpa_supplicant1.Interface.P2PDevice"
WPS_IFACE = "fi.w1.wpa_supplicant1.Interface.WPS"
PROPERTIES = "org.freedesktop.DBus.Properties"


class FakeInterface:
    def __init__(self, path, name, bus):
        self.path = path
        self.name = name
        self.bus = bus
        self.calls = []
        self.signal_handlers = {}

    async def call_get(self, interface, prop):
        self.calls.append(("get", interface, prop))
        key = (self.path, interface, prop)
        if key not in self.bus.properties:
            raise DBusError("org.freedesktop.DBus.Error.UnknownProperty", prop)
        value = self.bus.properties[key]
        if isinstance(value, list):
            return Variant("as", value)
        if isinstance(value, bytes):
            return Variant("ay", value)
        return Variant("s", value)

    async def call_set(self, interface, prop, variant):
        self.calls.append(("set", interface, prop, variant))
        self.bus.properties[(self.path, interface, prop)] = variant.value

    def __getattr__(self, name):
        if name.startswith("on_"):
            key = name[3:]

            def register(fn):
                self.signal_handlers[key] = fn

            return register
        if not name.startswith("call_"):
            raise AttributeError(name)
        method = name[5:]

        async def call(*args):
            self.bus.method_calls.append((method, args))
            if method == "slow":
                await asyncio.sleep(0.2)

        return call


class FakeProxy:
    def __init__(self, bus, path):
        self.bus = bus
        self.path = path

    def get_interface(self, name):
        if name == P2P_IFACE:
            self.bus.signal_path = self.path
        return self.bus.interface(self.path, name)


class FakeBus:
    def __init__(self):
        self.nodes = {}
        self.properties = {}
        self.method_calls = []
        self.handlers = []
        self.connected = False
        self.disconnected = False
        self.signal_path = DEVICE
        self._interfaces = {}

    async def connect(self):
        self.connected = True

    def disconnect(self):
        self.disconnected = True

    def add_message_handler(self, handler):
        self.handlers.append(handler)

    async def introspect(self, name, path):
        node = self.nodes.get(path)
        if node is None:
            raise DBusError("org.freedesktop.DBus.Error.UnknownObject", path)
        return node

    def get_proxy_object(self, name, path, node):
        return FakeProxy(self, path)

    def interface(self, path, name):
        key = (path, name)
        if key not in self._interfaces:
            self._interfaces[key] = FakeInterface(path, name, self)
        return self._interfaces[key]

    def emit(self, member, body=()):
        from dexonlinux.p2p.dbus import _snake

        target = self.interface(self.signal_path, P2P_IFACE)
        handler = target.signal_handlers.get(_snake(member))
        if handler is not None:
            handler(*body)


def node(*interfaces):
    return intr.Node(interfaces=list(interfaces))


def interface(name, methods=(), properties=(), signals=()):
    return intr.Interface(
        name=name,
        methods=[intr.Method(method) for method in methods],
        properties=[intr.Property(prop, "s", PropertyAccess.READWRITE) for prop in properties],
        signals=[intr.Signal(signal) for signal in signals],
    )


def full_p2p_interface():
    return interface(
        P2P_IFACE,
        methods=["Connect", "ExtendedListen", "Disconnect", "Slow"],
        properties=["P2PDeviceConfig"],
        signals=[
            "GroupStarted",
            "GroupFinished",
            "GONegotiationSuccess",
            "ProvisionDiscoveryPBCRequest",
        ],
    )


DEVICE = "/fi/w1/wpa_supplicant1/Interfaces/1"


def build_bus(*, wfd=True, p2p=None, wps=True):
    bus = FakeBus()
    bus.nodes[ROOT] = node(
        interface(
            ROOT_IFACE,
            methods=["GetInterface"],
            properties=["Interfaces", "WFDIEs"] if wfd else ["Interfaces"],
            signals=["InterfaceAdded"],
        )
    )
    bus.nodes["/fi/w1/wpa_supplicant1/Interfaces/0"] = node(interface(IFACE, properties=["Ifname"]))
    bus.nodes[DEVICE] = node(
        interface(IFACE, properties=["Ifname"]),
        p2p if p2p is not None else full_p2p_interface(),
        interface(WPS_IFACE, properties=["ConfigMethods"] if wps else []),
    )
    bus.properties[(ROOT, ROOT_IFACE, "Interfaces")] = [
        "/fi/w1/wpa_supplicant1/Interfaces/0",
        DEVICE,
    ]
    bus.properties[("/fi/w1/wpa_supplicant1/Interfaces/0", IFACE, "Ifname")] = "wlan0"
    bus.properties[(DEVICE, IFACE, "Ifname")] = "p2p-dev-wlan0"
    return bus


def make_client(bus):
    return DBusFastClient(bus_factory=lambda: bus)


def test_find_p2p_device_matches_the_interface_name():
    bus = build_bus()
    client = make_client(bus)
    client.connect()
    try:
        assert client.find_p2p_device("wlan0") == DEVICE
    finally:
        client.close()


def test_find_p2p_device_falls_back_to_the_only_p2p_device():
    bus = build_bus()
    client = make_client(bus)
    client.connect()
    try:
        assert client.find_p2p_device("other0") == DEVICE
    finally:
        client.close()


def test_find_p2p_device_reports_an_ambiguous_device():
    bus = build_bus()
    second = "/fi/w1/wpa_supplicant1/Interfaces/9"
    bus.nodes[second] = node(interface(IFACE, properties=["Ifname"]), full_p2p_interface())
    bus.properties[(DEVICE, IFACE, "Ifname")] = "wlan0"
    bus.properties[(second, IFACE, "Ifname")] = "wlan1"
    bus.properties[(ROOT, ROOT_IFACE, "Interfaces")] = [
        "/fi/w1/wpa_supplicant1/Interfaces/0",
        DEVICE,
        second,
    ]
    client = make_client(bus)
    client.connect()
    try:
        with pytest.raises(P2PDeviceMissing, match="multiple P2P devices"):
            client.find_p2p_device("other0")
    finally:
        client.close()


def test_probe_accepts_a_complete_interface():
    bus = build_bus()
    client = make_client(bus)
    client.connect()
    try:
        client.probe(DEVICE)
    finally:
        client.close()


def test_probe_rejects_missing_wfd_ies():
    bus = build_bus(wfd=False)
    client = make_client(bus)
    client.connect()
    try:
        with pytest.raises(WfdUnsupported, match="WFDIEs"):
            client.probe(DEVICE)
    finally:
        client.close()


def test_probe_rejects_missing_p2p_methods():
    bus = build_bus(
        p2p=interface(
            P2P_IFACE,
            methods=["ExtendedListen"],
            properties=["P2PDeviceConfig"],
            signals=["GroupStarted"],
        )
    )
    client = make_client(bus)
    client.connect()
    try:
        with pytest.raises(DBusApiIncomplete, match="Connect"):
            client.probe(DEVICE)
    finally:
        client.close()


def test_probe_rejects_missing_config_methods():
    bus = build_bus(wps=False)
    client = make_client(bus)
    client.connect()
    try:
        with pytest.raises(DBusApiIncomplete, match="ConfigMethods"):
            client.probe(DEVICE)
    finally:
        client.close()


def test_get_property_unwraps_the_variant():
    bus = build_bus()
    client = make_client(bus)
    client.connect()
    try:
        assert client.get_property(ROOT, ROOT_IFACE, "Interfaces") == [
            "/fi/w1/wpa_supplicant1/Interfaces/0",
            DEVICE,
        ]
    finally:
        client.close()


def test_set_property_sends_a_variant():
    bus = build_bus()
    client = make_client(bus)
    client.connect()
    try:
        client.set_property(ROOT, ROOT_IFACE, "WFDIEs", "ay", b"\x01\x02\x03")
    finally:
        client.close()

    variant = bus.interface(ROOT, PROPERTIES).calls[-1][3]
    assert isinstance(variant, Variant)
    assert variant.signature == "ay"
    assert variant.value == b"\x01\x02\x03"


def test_call_maps_dbus_error_to_supplicant_error():
    bus = build_bus()
    client = make_client(bus)
    client.connect()
    try:
        with pytest.raises(SupplicantError, match="DoesNotExist"):
            client.get_property(ROOT, ROOT_IFACE, "DoesNotExist")
    finally:
        client.close()


def test_call_times_out_as_supplicant_error():
    bus = build_bus()
    client = make_client(bus)
    client.connect()
    try:
        with pytest.raises(SupplicantError, match="timed out"):
            client.call(DEVICE, P2P_IFACE, "Slow", timeout=0.01)
    finally:
        client.close()


def test_subscribe_registers_and_dispatches_signals():
    bus = build_bus()
    client = make_client(bus)
    client.connect()
    received = []
    try:
        client.subscribe(DEVICE, lambda member, args: received.append((member, args)))
        bus.emit("ProvisionDiscoveryPBCRequest", ("/peers/aa_bb",))
    finally:
        client.close()

    assert received == [("ProvisionDiscoveryPBCRequest", ("/peers/aa_bb",))]


def test_find_p2p_device_uses_the_object_exposing_p2p_device(tmp_path=None):
    bus = build_bus()
    # Live case: the P2P device is a non-netdev object whose Ifname is the
    # main interface, not "p2p-dev-*".
    bus.properties[(DEVICE, IFACE, "Ifname")] = "wlan0"
    client = make_client(bus)
    client.connect()
    try:
        assert client.find_p2p_device("wlan0") == DEVICE
    finally:
        client.close()


def test_find_p2p_device_ignores_empty_interface_objects():
    bus = build_bus()
    bus.nodes["/fi/w1/wpa_supplicant1/Interfaces/0"] = node()
    bus.properties[(DEVICE, IFACE, "Ifname")] = "wlan0"
    client = make_client(bus)
    client.connect()
    try:
        assert client.find_p2p_device("wlan0") == DEVICE
    finally:
        client.close()


def test_find_p2p_device_prefers_the_named_device_when_several():
    bus = build_bus()
    second = "/fi/w1/wpa_supplicant1/Interfaces/9"
    bus.nodes[second] = node(interface(IFACE, properties=["Ifname"]), full_p2p_interface())
    bus.properties[(second, IFACE, "Ifname")] = "p2p-dev-other0"
    bus.properties[(DEVICE, IFACE, "Ifname")] = "p2p-dev-wlan0"
    client = make_client(bus)
    client.connect()
    try:
        assert client.find_p2p_device("wlan0") == DEVICE
    finally:
        client.close()


def test_find_p2p_device_reports_when_no_object_exposes_p2p_device():
    bus = build_bus()
    bus.nodes[DEVICE] = node(
        interface(IFACE, properties=["Ifname"]),
        interface(WPS_IFACE, properties=["ConfigMethods"]),
    )
    client = make_client(bus)
    client.connect()
    try:
        with pytest.raises(P2PDeviceMissing, match="no P2P device"):
            client.find_p2p_device("wlan0")
    finally:
        client.close()


def test_snake_matches_the_generated_dbus_fast_names():
    assert _snake("ProvisionDiscoveryPBCRequest") == "provision_discovery_pbc_request"
    assert _snake("GONegotiationRequest") == "go_negotiation_request"
    assert _snake("ExtendedListen") == "extended_listen"


def test_subscribe_registers_the_generated_signal_handlers():
    bus = build_bus()
    client = make_client(bus)
    client.connect()
    try:
        client.subscribe(DEVICE, lambda *_args: None)
    finally:
        client.close()

    handlers = bus.interface(DEVICE, P2P_IFACE).signal_handlers
    assert "provision_discovery_pbc_request" in handlers
    assert "group_started" in handlers
    assert "go_negotiation_success" in handlers
