"""Direct wpa_supplicant P2P client backend."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from dexonlinux.p2p.base import GroupInfo, P2PError
from dexonlinux.p2p.events import EventKind, parse_event

LOG = logging.getLogger(__name__)


class DirectBackend:
    def __init__(
        self,
        interface,
        ctrl,
        wfd,
        link,
        frequency: int = 2437,
        go_intent: int = 0,
        dhcp=None,
        before_group: Callable[[], None] | None = None,
    ) -> None:
        self.interface = interface
        self._ctrl = ctrl
        self._wfd = wfd
        self._link = link
        self._frequency = frequency
        self._go_intent = go_intent
        self._dhcp = dhcp
        self._before_group = before_group
        self._before_group_done = False
        self._state = None
        self.group_ifname: str | None = None
        self._pending_peer: str | None = None
        self._pending_until = 0.0
        self._preup_started = False

    def prepare(self) -> None:
        self._ctrl.open()
        self._link.remove_stale()
        self._state = self._wfd.apply(self._ctrl)
        self._ctrl.command("P2P_EXT_LISTEN 1000 1000")
        LOG.info("advertising as a WFD sink on %s", self.interface)

    def connect(self, stop: threading.Event, timeout: float = 90.0) -> GroupInfo:
        deadline = time.monotonic() + timeout
        while not stop.is_set() and time.monotonic() < deadline:
            for line in self._ctrl.pump(0.5):
                event = parse_event(line)
                if event is None:
                    continue
                if event.kind is EventKind.PBC_REQUEST:
                    if event.peer is not None:
                        self._connect_pbc(event.peer.mac)
                elif event.kind is EventKind.PIN_REQUEST:
                    raise P2PError("phone requested a PIN; PBC is required")
                elif event.kind is EventKind.INVITATION:
                    raise P2PError(
                        "phone sent a previously connected group invitation; "
                        "forget DexOnLinux on the phone and retry"
                    )
                elif event.kind is EventKind.GO_NEGOTIATION:
                    if event.passwd_id is not None and event.passwd_id != 4:
                        raise P2PError("phone requested a non-PBC WPS method; PBC is required")
                    if event.raw.startswith("P2P-GO-NEG-SUCCESS "):
                        # The group netdev appears during GO negotiation. Bring it
                        # up after the negotiation has settled but before auth;
                        # doing it earlier exposes an iwlwifi link-config race.
                        self._start_preup()
                    elif event.peer is not None and not self._connect_pending():
                        self._send_connect(event.peer.mac)
                elif event.kind is EventKind.GROUP_STARTED:
                    return self._group_started(event.group)
                elif event.kind is EventKind.FAILURE:
                    raise P2PError(event.raw)
                elif event.kind is EventKind.GROUP_REMOVED:
                    self.group_ifname = None
                    self._pending_until = 0.0

        if stop.is_set():
            raise P2PError("P2P session stopped before a group was formed")
        raise P2PError("P2P connection timed out")

    def _group_started(self, group: GroupInfo | None) -> GroupInfo:
        if group is None:
            raise P2PError("malformed P2P group event")
        self.group_ifname = group.ifname
        self._pending_until = 0.0
        if group.role == "GO":
            if self._go_intent == 0:
                raise P2PError("phone negotiated the laptop as GO; client role required")
            local_ip = group.client_ip or "192.168.49.1"
            prefix = group.client_prefix if group.client_ip else 24
            self._link.configure(group.ifname, f"{local_ip}/{prefix}")
            peer_ip = "192.168.49.2"
            if self._dhcp is not None:
                self._dhcp.start(group.ifname)
                peer_ip = self._dhcp.peer_ip
            LOG.info("P2P GO %s up with %s/%d, peer lease %s", group.ifname, local_ip, prefix, peer_ip)
            return replace(group, client_ip=local_ip, peer_ip=peer_ip)
        if not group.client_ip:
            raise P2PError("group started without a WPS client address; DHCP fallback is not implemented in v1")
        self._link.configure(group.ifname, f"{group.client_ip}/{group.client_prefix}")
        LOG.info("P2P group %s up with %s/%d", group.ifname, group.client_ip, group.client_prefix)
        return group

    def _connect_pbc(self, mac: str) -> None:
        now = time.monotonic()
        if mac == self._pending_peer and now < self._pending_until:
            return
        self._send_connect(mac)

    def _send_connect(self, mac: str) -> None:
        if self._before_group is not None and not self._before_group_done:
            self._before_group()
            self._before_group_done = True
        self._pending_peer = mac
        self._pending_until = time.monotonic() + 60.0
        self._ctrl.command(f"P2P_CONNECT {mac} pbc freq={self._frequency} go_intent={self._go_intent}")

    def _start_preup(self) -> None:
        if self._preup_started:
            return
        self._preup_started = True
        # Set the group netdev up after GO negotiation and before association;
        # doing it as soon as the netdev appears can race iwlwifi's link setup.
        self._link.start_watcher()

    def _connect_pending(self) -> bool:
        return time.monotonic() < self._pending_until

    def close(self) -> None:
        group = self.group_ifname
        try:
            self._link.stop_watcher()
            if self._dhcp is not None:
                self._dhcp.stop()
            if group:
                self._ctrl.command(f"P2P_GROUP_REMOVE {group}")
                try:
                    self._link.remove(group)
                except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                    LOG.debug("could not remove %s: %s", group, exc)
        finally:
            if self._state is not None:
                self._wfd.restore(self._ctrl, self._state)
            self._ctrl.close()
            self._state = None
            self.group_ifname = None
