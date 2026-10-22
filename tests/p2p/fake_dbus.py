"""In-memory ``DBusClient`` used by the P2P D-Bus tests.

It models the small part of the supplicant API the project uses: properties
keyed by object path, method calls, and a signal callback.
"""

from dexonlinux.p2p.control import SupplicantError
from dexonlinux.p2p.dbus import (
    P2P_IFACE,
    ROOT_IFACE,
    ROOT_PATH,
    WPS_IFACE,
)

DEVICE = "/fi/w1/wpa_supplicant1/Interfaces/1"
GROUP_DEVICE = "/fi/w1/wpa_supplicant1/Interfaces/2"


class FakeDBusClient:
    def __init__(self):
        self.device = DEVICE
        self.properties = {
            (ROOT_PATH, ROOT_IFACE, "WFDIEs"): b"\x00\x00\x06\x00\x11\x1c\x44\x00\xc8",
            (DEVICE, P2P_IFACE, "P2PDeviceConfig"): {"GOIntent": 7, "DeviceName": "old-host"},
            (DEVICE, WPS_IFACE, "ConfigMethods"): "0x188",
        }
        self.calls = []
        self.set_calls = []
        self.subscribed = []
        self.callback = None
        self.connected = False
        self.closed = False
        self.find_error = None
        self.probe_error = None
        self.fail_set = None

    def connect(self):
        self.connected = True

    def close(self):
        self.closed = True

    def find_p2p_device(self, interface):
        if self.find_error is not None:
            raise self.find_error
        return self.device

    def probe(self, path):
        if self.probe_error is not None:
            raise self.probe_error

    def subscribe(self, path, callback):
        self.subscribed.append(path)
        self.callback = callback

    def get_property(self, path, interface, name, *, timeout=None):
        if (path, interface, name) not in self.properties:
            raise SupplicantError(f"{interface}.{name} is not available")
        return self.properties[(path, interface, name)]

    def set_property(self, path, interface, name, signature, value, *, timeout=None):
        if self.fail_set is not None and (interface, name) == self.fail_set:
            raise SupplicantError("boom")
        self.set_calls.append((path, interface, name, signature, value))
        self.properties[(path, interface, name)] = value

    def call(self, path, interface, method, *args, timeout=None):
        self.calls.append((path, interface, method, args))
        return

    def emit(self, member, args=()):
        if self.callback is not None:
            self.callback(member, args)
