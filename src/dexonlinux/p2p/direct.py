"""Direct wpa_supplicant P2P backend."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from dexonlinux.p2p.base import DEFAULT_P2P_FREQ, GO_NETWORK, GroupInfo, GroupNetwork, P2PError, P2PRole
from dexonlinux.p2p.dhcp import DhcpServer
from dexonlinux.p2p.events import EventKind, parse_event
from dexonlinux.p2p.link import LinkConfigurator
from dexonlinux.p2p.supplicant import SupplicantControl
from dexonlinux.p2p.wfd import WfdConfigurator

LOG = logging.getLogger(__name__)

CONNECT_TIMEOUT = 90.0
_RETRY_WINDOW = 60.0  # ignore duplicate GO negotiation requests for this long


class DirectBackend:
    def __init__(
        self,
        interface: str,
        ctrl: SupplicantControl,
        wfd: WfdConfigurator,
        link: LinkConfigurator,
        *,
        role: P2PRole = P2PRole.CLIENT,
        network: GroupNetwork = GO_NETWORK,
        frequency: int = DEFAULT_P2P_FREQ,
        dhcp: DhcpServer | None = None,
        before_group: Callable[[], None] | None = None,
    ) -> None:
        self.interface = interface
        self._ctrl = ctrl
        self._wfd = wfd
        self._link = link
        self._role = role
        self._network = network
        self._frequency = frequency
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

    def connect(self, stop: threading.Event, timeout: float = CONNECT_TIMEOUT) -> GroupInfo:
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
                    if event.peer is not None and not self._connect_pending():
                        self._send_connect(event.peer.mac)
                elif event.kind is EventKind.GO_NEGOTIATION_SUCCESS:
                    # The group netdev appears during GO negotiation. Bring it
                    # up after the negotiation has settled but before auth;
                    # doing it earlier exposes an iwlwifi link-config race.
                    self._start_preup()
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

        if group.role is P2PRole.GO:
            if self._role is not P2PRole.GO:
                raise P2PError("phone negotiated the laptop as GO; client role was requested")
            if self._dhcp is None:
                raise P2PError("laptop is the GO but no DHCP server is configured")
            local_ip = group.local_ip or self._network.server_ip
            prefix = group.local_prefix if group.local_ip else self._network.prefix
            self._link.configure(group.ifname, f"{local_ip}/{prefix}")
            self._dhcp.start(group.ifname)
            LOG.info("P2P GO %s up with %s/%d, peer lease %s", group.ifname, local_ip, prefix, self._dhcp.peer_ip)
            return replace(group, local_ip=local_ip, peer_ip=self._dhcp.peer_ip)

        if self._role is P2PRole.GO:
            raise P2PError("phone negotiated the laptop as client; GO role was requested")
        if not group.local_ip:
            raise P2PError("group started without a WPS client address")
        if not group.peer_ip:
            raise P2PError("group started without the GO address")
        self._link.configure(group.ifname, f"{group.local_ip}/{group.local_prefix}")
        LOG.info("P2P group %s up with %s/%d", group.ifname, group.local_ip, group.local_prefix)
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
        self._pending_until = time.monotonic() + _RETRY_WINDOW
        self._ctrl.command(f"P2P_CONNECT {mac} pbc freq={self._frequency} go_intent={self._role.go_intent}")

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
