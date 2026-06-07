import socket
import threading
import time

from dexonlinux.commands import CommandError, Commands
from dexonlinux.utils import get_logger, select_from_list

logger = get_logger()


class ConnectionHandler:
    def __init__(
        self,
        commands,
        device,
        port,
        *,
        display_id=None,
        fullscreen=False,
        on_scrcpy_closed=None,
    ):
        self.commands = commands
        self.device = device
        self.port = port
        self.display_id = display_id
        self.fullscreen = fullscreen
        self.on_scrcpy_closed = on_scrcpy_closed
        self.scrcpy_process = None
        self.device_name = "unknown"
        self._stop_drain = threading.Event()
        self._drain_thread = None
        self._monitor_thread = None
        self._lock = threading.Lock()

    def stop(self):
        self._stop_scrcpy()

    def _start_scrcpy(self):
        with self._lock:
            if self.scrcpy_process and self.scrcpy_process.poll() is None:
                logger.debug("scrcpy is already running; ignoring duplicate connection event.")
                return

            self._stop_drain.clear()
            self._drain_thread = threading.Thread(target=self._drain_stream, daemon=True)
            self._drain_thread.start()

            logger.info("Starting scrcpy for %s.", self.device)
            display_id = self._resolve_display_id()
            self.scrcpy_process = self.commands.run_scrcpy(
                self.device,
                display_id=display_id,
                fullscreen=self.fullscreen,
            )
            self._monitor_thread = threading.Thread(target=self._monitor_scrcpy, daemon=True)
            self._monitor_thread.start()

    def _stop_scrcpy(self):
        self._stop_drain.set()
        if self._drain_thread and self._drain_thread.is_alive():
            self._drain_thread.join(timeout=1.0)

        with self._lock:
            process = self.scrcpy_process
            self.scrcpy_process = None

        self.commands.terminate_process(process, "scrcpy", timeout=1.0)

    def _monitor_scrcpy(self):
        process = self.scrcpy_process
        if process is None:
            return
        process.wait()
        if self._stop_drain.is_set():
            return
        logger.warning("scrcpy closed; ending DexOnLinux session.")
        self._stop_drain.set()
        if self.on_scrcpy_closed:
            self.on_scrcpy_closed()

    def _resolve_display_id(self):
        if self.display_id is not None:
            return self.display_id

        displays = self.commands.list_scrcpy_displays(self.device)
        if not displays:
            logger.warning("No scrcpy display id found; scrcpy will use its default display.")
            return None
        if len(displays) == 1:
            return displays[0].display_id

        print()
        print("Hint: DeX is usually the landscape 16:9 display; the phone screen is usually portrait 9:16.")
        selected = select_from_list(
            displays,
            "Select the display to open with scrcpy:",
            formatter=lambda display: display.label(),
        )
        if selected is None:
            raise CommandError("No scrcpy display selected.")
        return selected.display_id

    def _drain_stream(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", self.port))
                s.settimeout(1.0)
                logger.debug("RTP drainer started on UDP port %s.", self.port)

                while not self._stop_drain.is_set():
                    try:
                        s.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
            except OSError as exc:
                logger.error("RTP drainer bind failed on UDP port %s: %s", self.port, exc)
        logger.debug("RTP drainer stopped.")

    def _set_device_name(self, changed):
        friendly_name = changed.get("FriendlyName")
        if friendly_name:
            self.device_name = friendly_name

    def handle_connection(self, path, interface, changed, invalid):
        self._set_device_name(changed)
        connected = changed.get("Connected")
        if connected is None:
            return
        if connected:
            logger.info("Device %s connected via DeX.", self.device_name)
            time.sleep(1.0)
            try:
                self._start_scrcpy()
            except Exception as exc:
                logger.error("Unable to start scrcpy: %s", exc)
                self._stop_scrcpy()
                if self.on_scrcpy_closed:
                    self.on_scrcpy_closed()
        else:
            logger.info("Device %s disconnected from DeX.", self.device_name)
            self._stop_scrcpy()
