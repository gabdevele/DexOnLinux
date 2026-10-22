import logging
import struct
from dataclasses import dataclass
from typing import Protocol

from dexonlinux.p2p.base import P2PError
from dexonlinux.p2p.control import SupplicantError
from dexonlinux.wfd.rtsp import RTSP_PORT

LOG = logging.getLogger(__name__)

_DEVICE_INFORMATION = 0x0011  # primary sink, session available
_MAX_THROUGHPUT = 0x00C8  # units of 0.5 Mbps


class CommandControl(Protocol):
    """The single method the WFD configurator needs from a transport."""

    def command(self, command: str, timeout: float = 2.0) -> str: ...


def build_device_info_ie(
    device_information: int = _DEVICE_INFORMATION,
    session_port: int = RTSP_PORT,
    max_throughput: int = _MAX_THROUGHPUT,
) -> bytes:
    """Encode the WFD Device Information subelement.

    Layout: ``[id u8][length u16 BE][device info u16][RTSP port u16][max
    throughput u16]``.
    """
    value = struct.pack("!HHH", device_information, session_port, max_throughput)
    return b"\x00" + struct.pack("!H", 6) + value


WFD_IES = build_device_info_ie()


@dataclass(frozen=True)
class WfdState:
    wifi_display: str
    go_intent: str
    config_methods: str | None
    device_name: str


class WfdConfigurator:
    def apply(self, ctrl: CommandControl, *, go_intent: int, device_name: str) -> WfdState:
        state = WfdState(
            wifi_display=self._get(ctrl, "GET wifi_display", "0"),
            go_intent=self._get(ctrl, "GET p2p_go_intent", "7"),
            config_methods=self._get(ctrl, "GET config_methods", "") or None,
            device_name=self._get(ctrl, "GET device_name", ""),
        )

        try:
            # WFD_SUBELEM_SET only stores IEs; they are dropped while
            # wifi_display is false, so enable WFD first.
            self._send(ctrl, "SET wifi_display 1", required=True)
            self._send(ctrl, f"WFD_SUBELEM_SET all {WFD_IES.hex()}", required=True)
            self._send(ctrl, f"SET p2p_go_intent {go_intent}")
            self._send(ctrl, f"SET device_name {device_name}")
            self._send(ctrl, "SET config_methods 0x80", required=True)
        except P2PError:
            # A failed apply can leave wifi_display/IEs modified; put the
            # supplicant back before the error reaches the caller.
            self.restore(ctrl, state)
            raise
        return state

    def restore(self, ctrl: CommandControl, state: WfdState) -> None:
        self._send(ctrl, "P2P_EXT_LISTEN")
        self._send(ctrl, "WFD_SUBELEM_SET 0 ")
        if state.wifi_display != "1":
            self._send(ctrl, f"SET wifi_display {state.wifi_display}")
        self._send(ctrl, f"SET p2p_go_intent {state.go_intent}")
        if state.config_methods:
            self._send(ctrl, f"SET config_methods {state.config_methods}")
        if state.device_name:
            self._send(ctrl, f"SET device_name {state.device_name}")

    @staticmethod
    def _get(ctrl: CommandControl, command: str, default: str) -> str:
        try:
            return ctrl.command(command).strip() or default
        except SupplicantError as exc:
            LOG.debug("%s failed: %s", command, exc)
            return default

    @staticmethod
    def _send(ctrl: CommandControl, command: str, *, required: bool = False) -> str:
        try:
            reply = ctrl.command(command)
        except SupplicantError as exc:
            if required:
                raise P2PError(f"{command} failed: {exc}") from exc
            LOG.debug("%s failed: %s", command, exc)
            return ""
        if required and reply.strip() != "OK":
            raise P2PError(f"{command} failed: {reply.strip()!r}")
        return reply
