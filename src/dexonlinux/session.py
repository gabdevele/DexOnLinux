"""User-side session orchestration: helper group -> RTSP -> scrcpy."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from dexonlinux.p2p.base import P2PError

LOG = logging.getLogger(__name__)
SinkFactory = Callable[[str], object]


class DexSession:
    def __init__(
        self,
        helper,
        scrcpy,
        sink_factory: SinkFactory,
        start_delay: float = 1.0,
    ) -> None:
        self._helper = helper
        self._scrcpy = scrcpy
        self._sink_factory = sink_factory
        self._start_delay = start_delay
        self._sink = None
        self._timer: threading.Timer | None = None
        self._monitor: threading.Thread | None = None
        self._scrcpy_started = False
        self._scrcpy_failed = False

    def run(self) -> int:
        try:
            event = self._helper.wait_for("group_started", timeout=120.0)
            data = event.data
            role = data.get("role")
            if role == "client":
                host = data.get("go_ip")
                if not host:
                    raise P2PError("P2P group has no GO address")
            elif role == "GO":
                host = data.get("peer_ip")
                if not host:
                    raise P2PError("P2P GO group has no peer address")
            else:
                raise P2PError("P2P group has an unknown role")
            sink = self._sink_factory(str(host))
            self._sink = sink
            sink.on_event = self._on_sink_event
            sink.run()
            return 1 if self._scrcpy_failed else 0
        except (P2PError, OSError) as exc:
            LOG.error("%s", exc)
            return 1
        finally:
            self._shutdown()

    def _on_sink_event(self, name: str, data: dict[str, object]) -> None:
        if name == "streaming":
            self._schedule_scrcpy()
        elif name == "disconnected":
            self._stop_scrcpy()

    def _schedule_scrcpy(self) -> None:
        if self._scrcpy_started or self._timer is not None:
            return
        if self._start_delay > 0:
            self._timer = threading.Timer(self._start_delay, self._start_scrcpy)
            self._timer.daemon = True
            self._timer.start()
        else:
            self._start_scrcpy()

    def _start_scrcpy(self) -> None:
        self._timer = None
        if self._scrcpy_started:
            return
        self._scrcpy_started = True
        try:
            self._scrcpy.start()
        except Exception as exc:  # noqa: BLE001 - scrcpy failures end the session
            LOG.error("Unable to start scrcpy: %s", exc)
            self._scrcpy_failed = True
            if self._sink is not None:
                self._sink.stop()
            return
        self._monitor = threading.Thread(target=self._monitor_scrcpy, daemon=True)
        self._monitor.start()

    def _monitor_scrcpy(self) -> None:
        self._scrcpy.wait()
        if self._sink is not None:
            self._sink.stop()

    def _stop_scrcpy(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._scrcpy.stop()

    def _shutdown(self) -> None:
        self._stop_scrcpy()
        if self._monitor is not None and self._monitor is not threading.current_thread():
            self._monitor.join(timeout=1.0)
        if self._sink is not None:
            self._sink.stop()
        self._helper.stop()
