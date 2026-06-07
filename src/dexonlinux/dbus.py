from gi.repository import GLib
from pydbus import SystemBus

from dexonlinux.utils import get_logger

logger = get_logger()


class MiraclePeer:
    def __init__(self, path, interface, properties):
        self.path = path
        self.interface = interface
        self.properties = properties


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

    def _managed_objects(self):
        wifi = self.bus.get(self.service, self.root_path)
        return wifi.GetManagedObjects()

    def get_links(self):
        try:
            return [p for p in self._managed_objects().keys() if "/link/" in p]
        except Exception as exc:
            logger.debug("Unable to read Miraclecast links: %s", exc)
            return []

    def get_link_for_interface(self, interface_name):
        try:
            objects = self._managed_objects()
        except Exception as exc:
            logger.debug("Unable to inspect Miraclecast objects: %s", exc)
            return None

        links = []
        for path, ifaces in objects.items():
            if "/link/" not in path:
                continue
            links.append(path)
            props = self._flatten_properties(ifaces)
            candidates = {
                props.get("InterfaceName"),
                props.get("Name"),
                props.get("DeviceName"),
            }
            if interface_name in candidates:
                return path

        if len(links) == 1:
            logger.debug("Using the only available Miraclecast link: %s", links[0])
            return links[0]
        return None

    def get_interface_index(self, link_path):
        try:
            link = self.bus.get(self.service, link_path)
            return int(link.InterfaceIndex)
        except Exception as exc:
            logger.debug("Error reading InterfaceIndex of %s: %s", link_path, exc)
            return None

    def get_connected_peers(self):
        peers = []
        try:
            objects = self._managed_objects()
        except Exception as exc:
            logger.debug("Unable to inspect connected Miraclecast peers: %s", exc)
            return peers

        for path, ifaces in objects.items():
            if "/peer/" not in path:
                continue
            for iface, props in ifaces.items():
                if "Peer" in iface and props.get("Connected") is True:
                    peers.append(MiraclePeer(path=path, interface=iface, properties=props))
        return peers

    def subscribe_properties_changed(self, callback):
        def handle_props(interface_name, changed, invalidated, path=None):
            if "/peer/" not in str(path) or "Peer" not in interface_name:
                return
            callback(path, interface_name, changed, invalidated)

        self.bus.subscribe(
            sender=self.service,
            iface="org.freedesktop.DBus.Properties",
            signal="PropertiesChanged",
            signal_fired=lambda s, o, i, sig, params: handle_props(*params, path=o),
        )

    def run_loop(self):
        logger.info("Waiting for DeX connection. Press CTRL+C to exit.")
        self.loop.run()

    def stop_loop(self):
        GLib.idle_add(self.loop.quit)

    def _flatten_properties(self, ifaces):
        props = {}
        for values in ifaces.values():
            if isinstance(values, dict):
                props.update(values)
        return props
