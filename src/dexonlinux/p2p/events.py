import ipaddress
import re
from dataclasses import dataclass
from enum import Enum

from dexonlinux.p2p.base import GroupInfo, P2PRole, Peer

_MAC = r"[0-9a-fA-F:]{17}"
_PRIORITY = re.compile(r"^<\d+>")
_NAME = re.compile(r"\bname='([^']*)'")
_WFD = re.compile(r"\bwfd_dev_info=")
_PASSWD_ID = re.compile(r"\bdev_passwd_id=(\d+)")
_FREQ = re.compile(r"\bfreq=(\d+)")
_IP_ADDR = re.compile(r"\bip_addr=([^\s\]]+)")
_IP_MASK = re.compile(r"\bip_mask=([^\s\]]+)")
_GO_IP = re.compile(r"\bgo_ip_addr=([^\s\]]+)")
_GO_DEV = re.compile(r"\bgo_dev_addr=([^\s\]]+)")
_PEER_DEV = re.compile(r"\bpeer_dev=([^\s]+)")


class EventKind(Enum):
    DEVICE_FOUND = "device_found"
    PBC_REQUEST = "pbc_request"
    PIN_REQUEST = "pin_request"
    INVITATION = "invitation"
    GO_NEGOTIATION = "go_negotiation"
    GO_NEGOTIATION_SUCCESS = "go_negotiation_success"
    GROUP_STARTED = "group_started"
    GROUP_REMOVED = "group_removed"
    FAILURE = "failure"


@dataclass(frozen=True)
class P2PEvent:
    kind: EventKind
    peer: Peer | None = None
    group: GroupInfo | None = None
    passwd_id: int | None = None
    raw: str = ""


def _mac_at(parts: list[str], index: int = 1) -> str | None:
    if len(parts) <= index or re.fullmatch(_MAC, parts[index]) is None:
        return None
    return parts[index]


def _prefix(mask: str) -> int:
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except ValueError:
        return 24


def _group(line: str, parts: list[str]) -> GroupInfo | None:
    if len(parts) < 3:
        return None
    ifname = parts[1]
    role_token = parts[2].removeprefix("role=").lower()
    if role_token not in {"client", "go"}:
        return None
    role = P2PRole.CLIENT if role_token == "client" else P2PRole.GO

    ip_match = _IP_ADDR.search(line)
    mask_match = _IP_MASK.search(line)
    go_ip_match = _GO_IP.search(line)
    go_dev_match = _GO_DEV.search(line)
    freq_match = _FREQ.search(line)
    return GroupInfo(
        ifname=ifname,
        role=role,
        local_ip=ip_match.group(1) if ip_match else None,
        peer_ip=go_ip_match.group(1) if go_ip_match else None,
        local_prefix=_prefix(mask_match.group(1)) if mask_match else 24,
        frequency=int(freq_match.group(1)) if freq_match else None,
        go_dev_addr=go_dev_match.group(1) if go_dev_match else "",
    )


def parse_event(line: str) -> P2PEvent | None:
    """Return a typed event, or None for an unknown/malformed line."""
    line = _PRIORITY.sub("", line.strip())
    if not line:
        return None
    parts = line.split()

    if line.startswith("P2P-DEVICE-FOUND "):
        mac = _mac_at(parts)
        if mac is None:
            return None
        name_match = _NAME.search(line)
        return P2PEvent(
            EventKind.DEVICE_FOUND,
            peer=Peer(mac=mac, name=name_match.group(1) if name_match else "", wfd=bool(_WFD.search(line))),
            raw=line,
        )

    if line.startswith("P2P-PROV-DISC-PBC-REQ "):
        mac = _mac_at(parts)
        if mac is None:
            return None
        return P2PEvent(EventKind.PBC_REQUEST, peer=Peer(mac=mac), raw=line)

    if line.startswith(("P2P-PROV-DISC-SHOW-PIN ", "P2P-PROV-DISC-ENTER-PIN ")):
        mac = _mac_at(parts)
        if mac is None:
            return None
        return P2PEvent(EventKind.PIN_REQUEST, peer=Peer(mac=mac), raw=line)

    if line.startswith("P2P-INVITATION-RECEIVED "):
        mac = _mac_at(parts)
        if mac is None:
            return None
        return P2PEvent(EventKind.INVITATION, peer=Peer(mac=mac), raw=line)

    if line.startswith("P2P-GO-NEG-REQUEST "):
        mac = _mac_at(parts)
        if mac is None:
            return None
        passwd_match = _PASSWD_ID.search(line)
        return P2PEvent(
            EventKind.GO_NEGOTIATION,
            peer=Peer(mac=mac),
            passwd_id=int(passwd_match.group(1)) if passwd_match else None,
            raw=line,
        )

    if line.startswith("P2P-GO-NEG-SUCCESS "):
        peer_match = _PEER_DEV.search(line)
        if peer_match is None:
            return None
        return P2PEvent(
            EventKind.GO_NEGOTIATION_SUCCESS,
            peer=Peer(mac=peer_match.group(1)),
            raw=line,
        )

    if line.startswith("P2P-GROUP-STARTED "):
        group = _group(line, parts)
        if group is None:
            return None
        return P2PEvent(EventKind.GROUP_STARTED, group=group, raw=line)

    if line.startswith("P2P-GROUP-REMOVED "):
        return P2PEvent(EventKind.GROUP_REMOVED, raw=line)

    if line.startswith(("P2P-GO-NEG-FAILURE", "P2P-GROUP-FORMATION-FAILURE")):
        return P2PEvent(EventKind.FAILURE, raw=line)

    return None
