import signal
import threading
import time

from dexonlinux.commands import CommandError
from dexonlinux.stream_handler import StreamHandler
from dexonlinux.network import NetworkManager
from dexonlinux.utils import get_logger, print_dex_instructions

logger = get_logger()


class DexRuntime:
    def __init__(self, commands, network_mode):
        self.commands = commands
        self.network_manager = NetworkManager(commands, network_mode)
        self.miracle_wifi = None
        self.miracle_sinkctl = None
        self.connection_handler = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.cleanup()
        return False

    def prepare_network(self, interface):
        self.network_manager.prepare(interface)

    def cleanup(self):
        if self.connection_handler:
            self.connection_handler.stop()
        self.commands.terminate_process(self.miracle_sinkctl, "miracle-sinkctl")
        self.commands.close_sinkctl()
        self.commands.terminate_process(self.miracle_wifi, "miracle-wifid")
        try:
            self.network_manager.restore()
        except Exception as exc:
            logger.error("Network state could not be restored automatically: %s", exc)


class StopSignalHandler:
    def __init__(self, stop_requested):
        self.stop_requested = stop_requested
        self.interrupted = False
        self.previous_sigint = None
        self.previous_sigterm = None

    def __enter__(self):
        self.previous_sigint = signal.signal(signal.SIGINT, self.request_stop)
        self.previous_sigterm = signal.signal(signal.SIGTERM, self.request_stop)
        return self

    def __exit__(self, exc_type, exc, traceback):
        signal.signal(signal.SIGINT, self.previous_sigint)
        signal.signal(signal.SIGTERM, self.previous_sigterm)
        return False

    def request_stop(self, signum, frame):
        self.interrupted = True
        self.stop_requested.set()


def run_dex_session(commands, selected_interface, selected_device, args):
    with DexRuntime(commands, args.network_mode) as runtime:
        runtime.prepare_network(selected_interface)
        runtime.miracle_wifi = commands.start_miracle_wifi(selected_interface)

        interface_index = commands.get_interface_index(selected_interface)
        port = args.port or commands.get_available_udp_port()
        stop_requested = threading.Event()

        handler = StreamHandler(
            commands,
            selected_device.serial,
            port,
            display_id=args.display_id,
            fullscreen=args.fullscreen,
            on_scrcpy_closed=stop_requested.set,
        )
        runtime.connection_handler = handler
        runtime.miracle_sinkctl = commands.start_miracle_sinkctl(
            interface_index,
            port,
            event_callback=handler.handle_sinkctl_event,
        )

        print_dex_instructions()
        return wait_for_session(runtime, stop_requested)


def wait_for_session(runtime, stop_requested):
    with StopSignalHandler(stop_requested) as signal_handler:
        logger.info("Waiting for DeX connection. Press CTRL+C to exit.")
        while not stop_requested.is_set():
            if runtime.miracle_wifi.poll() is not None:
                raise CommandError("miracle-wifid stopped unexpectedly.")
            if runtime.miracle_sinkctl.poll() is not None:
                raise CommandError("miracle-sinkctl stopped unexpectedly.")
            time.sleep(0.25)
        return 130 if signal_handler.interrupted else 0
