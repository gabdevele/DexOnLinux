import logging
from pydbus import SystemBus
from gi.repository import GLib
from utils import get_logger

logger = get_logger()

class MiracleDbus:
    def __init__(self):
        self.bus = SystemBus()
        self.root_path = "/org/freedesktop/miracle/wifi"
        self.service = "org.freedesktop.miracle.wifi"
        self.loop = GLib.MainLoop()

    def bus_exists(self):
        try:
            self.bus.get(self.service, self.root_path)
            return True
        except Exception:
            return False

    def get_links(self):
        try:
            wifi = self.bus.get(self.service, self.root_path)
            objects = wifi.GetManagedObjects()
            links = [p for p in objects.keys() if "/link/" in p]
            return links
        except Exception:
            return []
        
    def get_interface_index(self, link_path):
        try:
            link = self.bus.get(self.service, link_path)
            return link.InterfaceIndex
        except Exception as e:
            logger.debug(f"Error reading InterfaceIndex of {link_path}: {e}")
            return None
        
    def on_peer_event(self, callback):
        def handle_added(path, ifaces_and_props):
            for iface, props in ifaces_and_props.items():
                if "Peer" in iface:
                    callback(path, iface, props)

        self.bus.subscribe(
            sender=self.service,
            object=self.root_path,
            iface="org.freedesktop.DBus.ObjectManager",
            signal="InterfacesAdded",
            signal_fired=lambda s, o, i, sig, params: handle_added(*params)
        )

    def subscribe_properties_changed(self, callback):
        def handle_props(interface_name, changed, invalidated, path=None):
            callback(path, interface_name, changed, invalidated)

        self.bus.subscribe(
            sender=self.service,
            iface="org.freedesktop.DBus.Properties",
            signal="PropertiesChanged",
            signal_fired=lambda s, o, i, sig, params: handle_props(*params, path=o)
        )

    def run_loop(self):
        logger.info("Listening for DBus events from miracle-wifi (CTRL+C to exit)...")
        self.loop.run()
