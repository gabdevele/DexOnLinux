from commands import Commands
import sh, time
from utils import get_logger, error_exit

logger = get_logger()

class ConnectionHandler:
    def __init__(self, commands: Commands, device: str):
        self.commands = commands
        self.device = device
        self.scrcpy_process: sh.RunningCommand = None
        self.device_name: str = "unknown"

    def _start_scrcpy(self):
        logger.debug("Starting scrcpy...")
        self.scrcpy_process = self.commands.run_scrcpy(self.device)
        if self.scrcpy_process is None:
            error_exit("Scrcpy error", enable_network=True, commands=self.commands)

    def _stop_scrcpy(self):
        logger.debug("Stopping scrcpy...")
        self.scrcpy_process.process.terminate()
        self.scrcpy_process = None

    def _set_device_name(self, changed: dict):
        friendly_name = changed.get("FriendlyName")
        if friendly_name:
            self.device_name = friendly_name

    def handle_connection(self, path: str, interface: str, changed: dict, invalid: dict):
        self._set_device_name(changed)
        connected = changed.get("Connected", None)
        if connected is None: 
            return
        if connected:
            logger.info(f"New device {self.device_name} connected.")
            time.sleep(2) #TODO: make sure this is an appropriate timing
            self._start_scrcpy()
        else:
            logger.info(f"Device {self.device_name} disconnected.")
            self._stop_scrcpy()
