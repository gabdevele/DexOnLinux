import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from dexonlinux.p2p.base import (
    DEFAULT_P2P_FREQ,
    GO_NETWORK,
    GroupInfo,
    GroupNetwork,
    P2PError,
    P2PRole,
    Peer,
)
from dexonlinux.p2p.control import P2PControl, SupplicantError
from dexonlinux.p2p.dhcp import DhcpServer
from dexonlinux.p2p.events import EventKind
from dexonlinux.p2p.link import LinkConfigurator

LOG = logging.getLogger(__name__)

CONNECT_TIMEOUT = 90.0
_RETRY_WINDOW = 60.0  # ignore duplicate GO negotiation requests for this long


class DirectBackend:
    def __init__(
        self,
        interface: str,
        control: P2PControl,
        link: LinkConfigurator,
        *,
        role: P2PRole = P2PRole.CLIENT,
        network: GroupNetwork = GO_NETWORK,
        frequency: int = DEFAULT_P2P_FREQ,
        dhcp: DhcpServer | None = None,
        before_group: Callable[[], None] | None = None,
    ) -> None:
        self.interface = interface
        self._control = control
        self._link = link
        self._role = role
        self._network = network
        self._frequency = frequency
        self._dhcp = dhcp
        self._before_group = before_group
        self._before_group_done = False
        self._wfd_state: object | None = None
        self.group_ifname: str | None = None
        self._pending_peer: str | None = None
        self._pending_until = 0.0
        self._preup_started = False

    def prepare(self) -> None:
        self._control.open()
        self._link.remove_stale()
        self._wfd_state = self._control.prepare_wfd(
            go_intent=self._role.go_intent,
            device_name="DexOnLinux",
        )
        self._control.start_extended_listen(1000, 1000)
        LOG.info("advertising as a WFD sink on %s", self.interface)

    def connect(self, stop: threading.Event, timeout: float = CONNECT_TIMEOUT) -> GroupInfo:
        deadline = time.monotonic() + timeout
        while not stop.is_set() and time.monotonic() < deadline:
            for event in self._control.pump(0.5):
                if event.kind is EventKind.PBC_REQUEST:
                    if event.peer is not None:
                        self._connect_pbc(event.peer)
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
                        self._send_connect(event.peer)
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

    def wait_for_group_end(self, stop: threading.Event) -> str | None:
        while not stop.is_set():
            for event in self._control.pump(0.5):
                if event.kind is EventKind.GROUP_REMOVED:
                    self.group_ifname = None
                    return event.raw or "P2P group removed"
        return None

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
            if group.local_ip is not None and group.local_ip != self._network.server_ip:
                raise P2PError(
                    f"negotiated GO address {group.local_ip} does not match the configured network "
                    f"{self._network.server_ip}/{self._network.prefix}"
                )
            local_ip = self._network.server_ip
            prefix = self._network.prefix
            self._link.configure(group.ifname, f"{local_ip}/{prefix}")
            self._dhcp.start(group.ifname)
            LOG.info(
                "P2P GO %s up with %s/%d, peer lease %s",
                group.ifname,
                local_ip,
                prefix,
                self._dhcp.peer_ip,
            )
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

    def _connect_pbc(self, peer: Peer) -> None:
        # The D-Bus peer path and the ctrl-iface MAC identify the same phone;
        # deduplicate by MAC so a path change cannot trigger a second Connect.
        key = peer.mac or peer.path
        now = time.monotonic()
        if key == self._pending_peer and now < self._pending_until:
            return
        self._send_connect(peer)

    def _send_connect(self, peer: Peer) -> None:
        if self._before_group is not None and not self._before_group_done:
            self._before_group()
            self._before_group_done = True
        self._pending_peer = peer.mac or peer.path
        self._pending_until = time.monotonic() + _RETRY_WINDOW
        try:
            self._control.connect(peer, frequency=self._frequency, go_intent=self._role.go_intent)
        except (P2PError, SupplicantError):
            # A failed attempt must not suppress the retry that follows.
            self._pending_peer = None
            self._pending_until = 0.0
            raise

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
            try:
                self._control.disconnect()
            except (P2PError, SupplicantError) as exc:
                LOG.debug("could not ask the supplicant to remove %s: %s", group, exc)
            if group:
                try:
                    self._link.remove(group)
                except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                    LOG.debug("could not remove %s: %s", group, exc)
        finally:
            try:
                if self._wfd_state is not None:
                    self._control.restore_wfd(self._wfd_state)
            finally:
                self._control.close()
                self._wfd_state = None
                self.group_ifname = None
