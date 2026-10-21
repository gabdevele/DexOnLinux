"""Wi-Fi Display advertisement and supplicant state restoration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dexonlinux.p2p.base import P2PError

LOG = logging.getLogger(__name__)

# Device Info: primary sink + session available; RTSP 7236; 100 Mbps.
WFD_IES = "00000600111c4400c8"


@dataclass(frozen=True)
class WfdState:
    wifi_display: str
    go_intent: str
    config_methods: str | None
    device_name: str


class WfdConfigurator:
    def apply(self, ctrl, device_name: str = "DexOnLinux") -> WfdState:
        state = WfdState(
            wifi_display=self._get(ctrl, "GET wifi_display", "0"),
            go_intent=self._get(ctrl, "GET p2p_go_intent", "7"),
            config_methods=self._get(ctrl, "GET config_methods", "") or None,
            device_name=self._get(ctrl, "GET device_name", ""),
        )

        # WFD_SUBELEM_SET only stores IEs; they are dropped while wifi_display
        # is false, so enable WFD first.
        self._send(ctrl, "SET wifi_display 1", required=True)
        self._send(ctrl, f"WFD_SUBELEM_SET all {WFD_IES}", required=True)
        self._send(ctrl, "SET p2p_go_intent 0")
        self._send(ctrl, f"SET device_name {device_name}")
        self._send(ctrl, "SET config_methods 0x80", required=True)
        return state

    def restore(self, ctrl, state: WfdState) -> None:
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
    def _get(ctrl, command: str, default: str) -> str:
        try:
            return ctrl.command(command).strip() or default
        except Exception as exc:  # noqa: BLE001 - supplicant versions differ; keep defaults
            LOG.debug("%s failed: %s", command, exc)
            return default

    @staticmethod
    def _send(ctrl, command: str, *, required: bool = False) -> str:
        try:
            reply = ctrl.command(command)
        except Exception as exc:
            if required:
                raise P2PError(f"{command} failed: {exc}") from exc
            LOG.debug("%s failed: %s", command, exc)
            return ""
        if required and reply.strip() != "OK":
            raise P2PError(f"{command} failed: {reply.strip()!r}")
        return reply
