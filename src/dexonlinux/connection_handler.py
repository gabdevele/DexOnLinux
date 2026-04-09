import socket
import threading
import signal
import subprocess
from commands import Commands
import time
from typing import Optional
from utils import get_logger, error_exit

logger = get_logger()

class ConnectionHandler:
    def __init__(self, commands: Commands, device: str, port: int = 1991):
        self.commands = commands
        self.device = device
        self.port = port
        self.scrcpy_process: Optional[subprocess.Popen] = None
        self.device_name: str = "unknown"
        self._stop_drain = threading.Event()
        self._drain_thread = None

    def _start_scrcpy(self):
        self._stop_drain.clear()
        self._drain_thread = threading.Thread(target=self._drain_stream, daemon=True)
        self._drain_thread.start()

        logger.debug("Starting scrcpy...")
        self.scrcpy_process = self.commands.run_scrcpy(self.device)
        
        if self.scrcpy_process is None:
            self._stop_drain.set()
            error_exit("Scrcpy error", enable_network=True, commands=self.commands)

    def _stop_scrcpy(self):
        logger.debug("Stopping scrcpy and drainer...")
        
        self._stop_drain.set()
        if self._drain_thread and self._drain_thread.is_alive():
            self._drain_thread.join(timeout=1.0)

        if self.scrcpy_process:
            try:
                self.scrcpy_process.kill()
                self.scrcpy_process.wait(timeout=0.5)
            except Exception as e:
                logger.debug(f"Scrcpy process already dead or error killing: {e}")
            finally:
                self.scrcpy_process = None

    def _drain_stream(self):
        # consume rtp stream to prevent miraclecast from closing the connection
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(('0.0.0.0', self.port))
                s.settimeout(1.0)
                logger.debug(f"RTP Drainer started on UDP port {self.port}")
                
                while not self._stop_drain.is_set():
                    try:
                        s.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
            except Exception as e:
                logger.error(f"Drainer bind failed: {e}")
        logger.debug("Drainer thread stopped")

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
