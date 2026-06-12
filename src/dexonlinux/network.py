import time

from dexonlinux.utils import get_logger

logger = get_logger()


class NmcliDevice:
    def __init__(self, device, device_type="", state="", connection=""):
        self.device = device
        self.device_type = device_type
        self.state = state
        self.connection = connection

    @property
    def is_wifi(self):
        return self.device_type == "wifi"

    @property
    def is_connected(self):
        return self.state.startswith("connected")


class InterfaceInfo:
    def __init__(self, name, nmcli_device=None, p2p_capable=True):
        self.name = name
        self.nmcli_device = nmcli_device
        self.p2p_capable = p2p_capable

    @property
    def connection(self):
        if self.nmcli_device:
            return self.nmcli_device.connection
        return ""

    @property
    def state(self):
        if self.nmcli_device:
            return self.nmcli_device.state
        return "unknown"

    @property
    def is_connected(self):
        return bool(self.nmcli_device and self.nmcli_device.is_connected)

    @property
    def is_available(self):
        return self.state != "unavailable"

    def label(self):
        parts = [self.name]
        if self.connection:
            parts.append(f"connected to {self.connection}")
        else:
            parts.append(self.state)
        if self.is_connected:
            parts.append("[internet active]")
        elif not self.is_available:
            parts.append("[enable Wi-Fi first]")
        else:
            parts.append("[recommended]")
        return "  ".join(parts)


class NetworkState:
    def __init__(self, interface, nmcli_device=None, connection_uuid=""):
        self.interface = interface
        self.nmcli_device = nmcli_device
        self.was_connected = bool(nmcli_device and nmcli_device.is_connected)
        self.connection = nmcli_device.connection if nmcli_device else ""
        self.connection_uuid = connection_uuid
        self.was_wpa_supplicant_active = False
        self.prepared = False


class NetworkManager:
    def __init__(self, commands, mode):
        self.commands = commands
        self.mode = mode
        self.state = None

    def prepare(self, interface):
        if self.mode == "full-stop":
            self.state = NetworkState(interface)
            self.state.prepared = True
            self.commands.disable_network_services()
            return

        device = self.commands.get_nmcli_device(interface)
        connection_uuid = ""
        if device.is_connected:
            connection_uuid = self.commands.get_active_connection_uuid(interface)

        self.state = NetworkState(interface, device, connection_uuid)
        self.state.prepared = True
        self.state.was_wpa_supplicant_active = self.commands.is_service_active("wpa_supplicant")
        if self.state.was_connected:
            self.commands.nmcli_disconnect(interface)
        self.commands.nmcli_set_managed(interface, False)
        if self.state.was_wpa_supplicant_active:
            self.commands.stop_wpa_supplicant()
        self.commands.ip_link_up(interface)
        logger.info("Released %s from NetworkManager.", interface)

    def restore(self):
        if not self.state or not self.state.prepared:
            return

        if self.mode == "full-stop":
            self.commands.enable_network_services()
            return

        if self.state.was_wpa_supplicant_active:
            self.commands.start_wpa_supplicant()
        self.commands.nmcli_set_managed(self.state.interface, True)
        time.sleep(0.5)
        if self.state.was_connected:
            try:
                if self.state.connection_uuid:
                    self.commands.nmcli_connection_up(self.state.connection_uuid, self.state.interface)
                else:
                    self.commands.nmcli_connect(self.state.interface)
            except Exception as exc:
                logger.warning("Could not reconnect %s automatically: %s", self.state.interface, exc)
        logger.info("Restored NetworkManager control for %s.", self.state.interface)
