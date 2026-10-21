# WFD Sink Product Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace miraclecast with a modular, stdlib-first WFD sink that starts Samsung DeX while keeping Wi-Fi internet usable, with NetworkManager left running.

**Architecture:** A small privileged helper owns the wpa_supplicant control socket and the P2P lifecycle. The user process owns the CLI, RTSP client, ADB and scrcpy. Backends are selected by a doctor: NetworkManager-owned P2P is the primary path when the live gate passes; direct wpa_supplicant is the no-NetworkManager path. PBC is used (no PIN UI).

**Tech Stack:** Python ≥ 3.9, stdlib (`socket`, `queue`, `threading`, `subprocess`, `json`, `dataclasses`, `typing`), `colorama` (existing), `pytest` + `ruff` for development. No `dbus-next` dependency: the proven ctrl_iface protocol is used for P2P/WFD and `nmcli` for NM profile activation.

**Spec:** `docs/superpowers/specs/2026-10-20-miraclecast-free-wifi-link-design.md`, corrected by `docs/research/2026-10-20-wfd-sink-spike-report.md` and `docs/research/2026-10-21-network-stack-support-matrix.md`.

---

## Global Constraints

- Never stop, restart, freeze or replace NetworkManager or wpa_supplicant in the product.
- Never run a private supplicant.
- Use PBC only; no PIN UI, no PIN notification.
- Phone = P2P GO; laptop = P2P client. `go_intent=0` on the direct path.
- P2P operating frequency: `2437` MHz (channel 6).
- WFD IEs: `00000600111c4400c8` (Device Info `0x0011`, RTSP `7236`, throughput `0x00c8`).
- Client address from WPS: `192.168.49.200/24`; GO: `192.168.49.1`.
- Never install a default route on the P2P interface.
- Python ≥ 3.9; runtime dependencies stay minimal (`colorama` only).
- New modules use `from __future__ import annotations`; do not evaluate PEP 604 unions at runtime on Python 3.9.
- No `shell=True`; all subprocess arguments are lists.
- No busy polling loops; blocking sockets/queues with timeouts. Poll only where no event exists, at ≥ 100 ms intervals and bounded duration. The only exception is the short, bounded P2P group netdev watcher (50 ms) while a connect is pending; see Task 3.
- One privileged helper process per session; it writes JSON-lines events to stdout, logs to stderr, and cleans up on SIGINT/SIGTERM and exceptions.
- Start the helper in its own process group (`start_new_session=True`); stop it with `SIGTERM` to the group, not just to `sudo`.
- Tests must not require root, a phone, NetworkManager or a real Wi-Fi device. Mark manual tests as live and document them.
- Every task ends with a focused commit.

## Review Focus

These are the failure modes most likely to bite a real user. Each has a test in the task that owns it.

1. **NM-owned P2P gate:** DeX rejects PBC or the laptop becomes GO. The product must stop with a clear error, not continue with a broken group.
2. **Phone-side stale/persistent state:** an invitation or saved-device entry arrives instead of PBC provision discovery. The session must fail with an actionable message, not hang.
3. **Address/link race:** `P2P-GROUP-STARTED` arrives without a usable address, or the netdev is down. The direct backend must either configure it or fail clearly.
4. **RTSP liveness:** M16 keepalives, TEARDOWN and TCP close must end the session cleanly without leaking the RTP socket or threads.
5. **Crash cleanup:** Ctrl-C, SIGTERM, helper crash or stale `p2p-*` link must not leave WFD settings or a P2P group behind.
6. **Stack mismatch:** NetworkManager + iwd, ConnMan, no P2P ctrl, or a driver without STA+P2P must be rejected by the doctor before any mutation.

---

## Prerequisite Gate 0: NM-owned PBC live probe

**This gate decides whether Task 6 is implementable. Do not start Task 6 until it passes.**

**Files:**
- Create: `docs/research/2026-10-21-nm-p2p-probe.md`

- [ ] **Step 1: Prepare the P2P device**

```bash
# The netdev name is truncated to IFNAMSIZ; discover it instead of guessing.
CTRL=$(basename "$(ls /run/wpa_supplicant/p2p-dev-* | head -n1)")
sudo wpa_cli -p /run/wpa_supplicant -i "$CTRL" set device_name DexOnLinux
sudo wpa_cli -p /run/wpa_supplicant -i "$CTRL" set wifi_display 1
sudo wpa_cli -p /run/wpa_supplicant -i "$CTRL" WFD_SUBELEM_SET all 00000600111c4400c8
```

Also verify PBC-only is accepted before relying on it:

```bash
sudo wpa_cli -p /run/wpa_supplicant -i "$CTRL" get config_methods
sudo wpa_cli -p /run/wpa_supplicant -i "$CTRL" set config_methods 0x80
sudo wpa_cli -p /run/wpa_supplicant -i "$CTRL" get config_methods  # must show 0x80
# restore the previous value captured by the first command
```

- [ ] **Step 2: Create and activate an NM-owned P2P profile**

Use the phone's current P2P device MAC (the current phone is `ce:f9:f0:2b:8a:cc`; verify from `P2P-DEVICE-FOUND` if it changed):

```bash
nmcli connection delete dex-nm-probe 2>/dev/null
nmcli connection add type wifi-p2p con-name dex-nm-probe \
  wifi-p2p.peer ce:f9:f0:2b:8a:cc \
  ipv4.method auto autoconnect no
nmcli connection modify dex-nm-probe \
  wifi-p2p.wfd-ies '00:00:06:00:11:1c:44:00:c8' \
  wifi-p2p.wps-method pbc
nmcli connection up dex-nm-probe
```

On the phone: open Smart View / DeX, but let the laptop initiate. Do not select the sink if that starts a PIN flow.

- [ ] **Step 3: Observe for 60 seconds**

```bash
iw dev | grep -A3 P2P-client
ip -brief addr show | grep p2p
ip route show dev p2p-wlp0s20-*
ping -c 3 192.168.49.1
journalctl -u NetworkManager -u wpa_supplicant -f
```

Pass criteria:

- `P2P-GO-NEG-SUCCESS role=client`;
- group interface up with `192.168.49.200/24`;
- `ping 192.168.49.1` succeeds;
- group stays for at least 60 seconds;
- no NM `RemoveInterface`/deauth for the group.

- [ ] **Step 4: Clean up**

```bash
nmcli connection down dex-nm-probe
nmcli connection delete dex-nm-probe
sudo wpa_cli -p /run/wpa_supplicant -i "$CTRL" P2P_EXT_LISTEN
sudo wpa_cli -p /run/wpa_supplicant -i "$CTRL" WFD_SUBELEM_SET 0 ''
sudo wpa_cli -p /run/wpa_supplicant -i "$CTRL" set wifi_display 0
sudo wpa_cli -p /run/wpa_supplicant -i "$CTRL" set config_methods <previous>
iw dev | grep -A2 P2P-client
```

- [ ] **Step 5: Record the result**

Write the outcome, role, PBC behavior, IP and any failure in
`docs/research/2026-10-21-nm-p2p-probe.md`. If the gate fails, stop before
Task 6 and update the design; the direct backend remains valid only where
NetworkManager is absent.

- [ ] **Step 6: Commit**

```bash
git add docs/research/2026-10-21-nm-p2p-probe.md
git commit -m "docs: record NM-owned P2P probe result"
```

---

### Task 1: P2P protocol core (models, events, supplicant client)

**Files:**
- Create: `src/dexonlinux/p2p/__init__.py`
- Create: `src/dexonlinux/p2p/base.py`
- Create: `src/dexonlinux/p2p/events.py`
- Create: `src/dexonlinux/p2p/supplicant.py`
- Create: `tests/__init__.py`
- Create: `tests/fakes.py`
- Create: `tests/p2p/test_events.py`
- Create: `tests/p2p/test_supplicant.py`
- Modify: `pyproject.toml` (add `pytest` and `ruff` to `dependency-groups.dev`)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Peer(mac: str, name: str = "", wfd: bool = False)`
  - `GroupInfo(ifname: str, role: Literal["client", "GO"], client_ip: str | None, client_prefix: int, go_ip: str | None, peer_mac: str, frequency: int | None)`
  - `P2PError(RuntimeError)`
  - `P2PBackend` protocol with `prepare() -> None`, `connect(stop: threading.Event, timeout: float = 90.0) -> GroupInfo`, `close() -> None`.
  - `P2PEvent(kind: EventKind, peer: Peer | None = None, group: GroupInfo | None = None, passwd_id: int | None = None, raw: str = "")`
  - `parse_event(line: str) -> P2PEvent | None`
  - `SupplicantControl(path: Path)` with `open()`, `close()`, `command(command: str, timeout: float = 2.0) -> str`, `pump(timeout: float = 0.5) -> list[str]`.

- [ ] **Step 1: Write the failing event tests**

```python
# tests/p2p/test_events.py
from dexonlinux.p2p.events import EventKind, parse_event


def test_parses_device_found_with_wfd():
    event = parse_event(
        "P2P-DEVICE-FOUND ce:f9:f0:2b:8a:cc p2p_dev_addr=ce:f9:f0:2b:8a:cc "
        "name='S24 di Gabriele' config_methods=0x188 wfd_dev_info=0x01101c440032 new=1"
    )
    assert event.kind is EventKind.DEVICE_FOUND
    assert event.peer.mac == "ce:f9:f0:2b:8a:cc"
    assert event.peer.name == "S24 di Gabriele"
    assert event.peer.wfd is True


def test_parses_pbc_request():
    event = parse_event("P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc p2p_dev_addr=ce:f9:f0:2b:8a:cc")
    assert event.kind is EventKind.PBC_REQUEST
    assert event.peer.mac == "ce:f9:f0:2b:8a:cc"


def test_parses_go_negotiation_password_id():
    event = parse_event("P2P-GO-NEG-REQUEST ce:f9:f0:2b:8a:cc dev_passwd_id=4 go_intent=15")
    assert event.kind is EventKind.GO_NEGOTIATION
    assert event.peer.mac == "ce:f9:f0:2b:8a:cc"
    assert event.passwd_id == 4


def test_parses_invitation_as_actionable_failure():
    event = parse_event(
        "P2P-INVITATION-RECEIVED ce:f9:f0:2b:8a:cc sa=ce:f9:f0:2b:8a:cc "
        "persistent=1"
    )
    assert event.kind is EventKind.INVITATION
    assert event.peer.mac == "ce:f9:f0:2b:8a:cc"


def test_parses_client_group_with_wps_address():
    event = parse_event(
        'P2P-GROUP-STARTED p2p-wlp0s20-7 client ssid="DIRECT-cf-S24" freq=2437 '
        "go_dev_addr=ce:f9:f0:2b:0a:cc ip_addr=192.168.49.200 "
        "ip_mask=255.255.255.0 go_ip_addr=192.168.49.1"
    )
    assert event.kind is EventKind.GROUP_STARTED
    assert event.group.ifname == "p2p-wlp0s20-7"
    assert event.group.role == "client"
    assert event.group.client_ip == "192.168.49.200"
    assert event.group.client_prefix == 24
    assert event.group.go_ip == "192.168.49.1"
    assert event.group.peer_mac == "ce:f9:f0:2b:0a:cc"
    assert event.group.frequency == 2437


def test_parses_go_role_group():
    event = parse_event("P2P-GROUP-STARTED p2p-wlp0s20-8 GO ssid=\"x\" freq=2437")
    assert event.group.role == "GO"


def test_rejects_malformed_and_ignores_unknown():
    assert parse_event("noise") is None
    assert parse_event("P2P-GROUP-STARTED broken") is None
```

- [ ] **Step 2: Run the event tests to verify they fail**

Run: `python -m pytest tests/p2p/test_events.py -q`
Expected: FAIL with `ModuleNotFoundError` or missing `parse_event`.

- [ ] **Step 3: Implement `base.py` and `events.py`**

`base.py` contains the frozen dataclasses, `P2PError`, and the `P2PBackend` protocol. `events.py` parses:

- `P2P-DEVICE-FOUND`: peer MAC, `name='...'`, `wfd_dev_info=...` → `wfd=True`;
- `P2P-PROV-DISC-PBC-REQ`: peer MAC;
- `P2P-PROV-DISC-SHOW-PIN`: `EventKind.PIN_REQUEST` (unsupported path);
- `P2P-INVITATION-RECEIVED`: `EventKind.INVITATION` with the peer MAC; the direct backend must fail with a stale-group message;
- `P2P-GO-NEG-REQUEST` / `P2P-GO-NEG-SUCCESS`: peer, optional `dev_passwd_id`, and optional frequency;
- `P2P-GROUP-STARTED`: ifname, role (`client` or `role=client`), `ip_addr`, `ip_mask`, `go_ip_addr`, `go_dev_addr`, `freq`;
- `P2P-GROUP-REMOVED`: group removed;
- `P2P-GO-NEG-FAILURE` / `P2P-GROUP-FORMATION-FAILURE`: failure.

Use `re` with named groups; do not split on spaces blindly. `ip_mask` converts to prefix by counting bits. Default prefix is 24. Return `None` for unknown or malformed lines.

- [ ] **Step 4: Run the event tests**

Run: `python -m pytest tests/p2p/test_events.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing supplicant tests**

Create `tests/fakes.py` with a `FakeRunner` that records commands and returns queued `CommandResult`s. For the supplicant, use a UNIX datagram fixture:

```python
# tests/p2p/test_supplicant.py
import socket
import threading
import time
from pathlib import Path

import pytest

from dexonlinux.p2p.supplicant import SupplicantControl, SupplicantError


@pytest.fixture
def fake_ctrl(tmp_path):
    path = tmp_path / "p2p-dev-test"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(path))
    server.settimeout(2.0)
    yield path, server
    server.close()


def test_attach_command_and_event(fake_ctrl):
    path, server = fake_ctrl
    ctrl = SupplicantControl(path)

    def serve():
        data, addr = server.recvfrom(4096)
        assert data == b"ATTACH"
        server.sendto(b"OK\n", addr)
        data, addr = server.recvfrom(4096)
        assert data == b"GET wifi_display"
        server.sendto(b"1\n", addr)
        # A priority-tagged event arrives after the command reply.
        time.sleep(0.05)
        server.sendto(b"<3>P2P-DEVICE-FOUND aa:bb:cc:dd:ee:ff name='x'\n", addr)

    server_thread = threading.Thread(target=serve)
    server_thread.start()
    ctrl.open()
    assert ctrl.command("GET wifi_display") == "1"
    server_thread.join(1.0)
    assert ctrl.pump(0.5) == ["P2P-DEVICE-FOUND aa:bb:cc:dd:ee:ff name='x'"]
    ctrl.close()


def test_missing_reply_raises(fake_ctrl):
    path, server = fake_ctrl
    ctrl = SupplicantControl(path)

    def serve():
        data, addr = server.recvfrom(4096)
        assert data == b"ATTACH"
        server.sendto(b"OK\n", addr)
        data, addr = server.recvfrom(4096)
        assert data == b"GET x"
        # Deliberately do not reply.

    server_thread = threading.Thread(target=serve)
    server_thread.start()
    ctrl.open()
    with pytest.raises(SupplicantError):
        ctrl.command("GET x", timeout=0.1)
    server_thread.join(1.0)
```

- [ ] **Step 6: Run the supplicant tests to verify they fail**

Run: `python -m pytest tests/p2p/test_supplicant.py -q`
Expected: FAIL with missing module/class.

- [ ] **Step 7: Implement `supplicant.py`**

Port the spike client with these changes:

- `find_ctrl_socket(interface: str, ctrl_dir: Path = Path("/run/wpa_supplicant")) -> Path`;
- `SupplicantControl` takes the resolved path, opens `AF_UNIX`/`SOCK_DGRAM`, binds an abstract address containing the PID, sends `ATTACH`, and requires `OK`;
- `command()` sends one line and reads until a non-event reply or timeout;
- `pump()` returns queued events, waiting at most `timeout` for one datagram;
- strip `<N>` syslog priority prefixes; split multi-line datagrams;
- `SupplicantError` on missing socket, closed socket, timeout or `ATTACH` failure.

- [ ] **Step 8: Run tests, static checks and commit**

Run:

```bash
python -m pytest tests/p2p/test_events.py tests/p2p/test_supplicant.py -q
python -m compileall -q src/dexonlinux/p2p
ruff check src/dexonlinux/p2p tests/p2p
```

Expected: PASS and no lint errors.

```bash
git add pyproject.toml src/dexonlinux/p2p tests
git commit -m "feat: add p2p event model and supplicant client"
```

---

### Task 2: System runner, WFD advertisement and link configuration

**Files:**
- Create: `src/dexonlinux/system.py`
- Create: `src/dexonlinux/p2p/wfd.py`
- Create: `src/dexonlinux/p2p/link.py`
- Create: `tests/test_system.py`
- Create: `tests/p2p/test_wfd.py`
- Create: `tests/p2p/test_link.py`

**Interfaces:**
- Consumes: `SupplicantControl`, `P2PError`.
- Produces:
  - `CommandResult(args: tuple[str, ...], returncode: int, stdout: str, stderr: str)`
  - `CommandError(RuntimeError)`
  - `Runner` protocol: `run(args, *, sudo=False, check=True, timeout=None) -> CommandResult`, `popen(args, *, sudo=False, env=None) -> subprocess.Popen[str]`.
  - `SubprocessRunner(sudo_password: str = "")` with `ensure_sudo() -> None` (runs `sudo -v` once; raises `CommandError` on failure).
  - `WfdState(wifi_display: str, go_intent: str, config_methods: str | None, device_name: str)`
  - `WfdConfigurator.apply(ctrl, device_name="DexOnLinux") -> WfdState`
  - `WfdConfigurator.restore(ctrl, state) -> None`
  - `LinkConfigurator(runner).configure(ifname: str, cidr: str) -> None`
  - `LinkConfigurator(runner).remove(ifname: str) -> None` (ignore ENODEV/not-found)

- [ ] **Step 1: Write the failing WFD tests**

```python
# tests/p2p/test_wfd.py
from dexonlinux.p2p.wfd import WfdConfigurator, WfdState


class FakeCtrl:
    def __init__(self):
        self.commands = []
        self.replies = {
            "GET wifi_display": "0",
            "GET p2p_go_intent": "7",
            "GET config_methods": "0x188",
            "GET device_name": "old-hostname",
        }

    def command(self, command, timeout=2.0):
        self.commands.append(command)
        return self.replies.get(command, "OK")


def test_apply_enables_wfd_before_setting_ies():
    ctrl = FakeCtrl()
    state = WfdConfigurator().apply(ctrl)
    assert state == WfdState("0", "7", "0x188", "old-hostname")
    assert ctrl.commands[:4] == [
        "GET wifi_display",
        "GET p2p_go_intent",
        "GET config_methods",
        "GET device_name",
    ]
    assert "SET wifi_display 1" in ctrl.commands
    assert "WFD_SUBELEM_SET all 00000600111c4400c8" in ctrl.commands
    assert ctrl.commands.index("SET wifi_display 1") < ctrl.commands.index(
        "WFD_SUBELEM_SET all 00000600111c4400c8"
    )
    assert "SET p2p_go_intent 0" in ctrl.commands
    assert "SET config_methods 0x80" in ctrl.commands


def test_restore_clears_and_restores():
    ctrl = FakeCtrl()
    WfdConfigurator().restore(ctrl, WfdState("0", "7", "0x188", "old-hostname"))
    assert ctrl.commands == [
        "P2P_EXT_LISTEN",
        "WFD_SUBELEM_SET 0 ",
        "SET wifi_display 0",
        "SET p2p_go_intent 7",
        "SET config_methods 0x188",
        "SET device_name old-hostname",
    ]
```

- [ ] **Step 2: Run WFD tests to verify they fail**

Run: `python -m pytest tests/p2p/test_wfd.py -q`
Expected: FAIL with missing module.

- [ ] **Step 3: Implement `wfd.py`**

- `WFD_IES = "00000600111c4400c8"`.
- `apply()` reads the four previous values (ignore failures and default `"0"`, `"7"`, `None`, `""`), then sends:
  `SET wifi_display 1`, `WFD_SUBELEM_SET all <WFD_IES>`, `SET p2p_go_intent 0`, `SET device_name DexOnLinux`, `SET config_methods 0x80`.
- `restore()` sends `P2P_EXT_LISTEN`, `WFD_SUBELEM_SET 0 `, restores `wifi_display` only if it was not `"1"`, restores `p2p_go_intent`, restores `config_methods` if recorded, and restores `device_name` if recorded and non-empty.
- Never raise if a restore command fails; log at debug/warning.
- Before relying on PBC-only, live-check that the installed supplicant accepts `SET config_methods 0x80` and that `GET config_methods` returns it; if not, stop and reassess the PIN fallback rather than shipping a sink the phone will offer PIN to.

- [ ] **Step 4: Write the failing link tests**

```python
# tests/p2p/test_link.py
from dexonlinux.p2p.link import LinkConfigurator


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, args, *, sudo=False, check=True, timeout=None):
        self.calls.append((tuple(args), sudo))

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()


def test_configure_sets_link_up_then_address():
    runner = FakeRunner()
    LinkConfigurator(runner).configure("p2p-wlp0s20-7", "192.168.49.200/24")
    assert runner.calls == [
        (("ip", "link", "set", "dev", "p2p-wlp0s20-7", "up"), False),
        (("ip", "addr", "replace", "192.168.49.200/24", "dev", "p2p-wlp0s20-7"), False),
    ]


def test_remove_uses_iw_dev_del():
    runner = FakeRunner()
    LinkConfigurator(runner).remove("p2p-wlp0s20-7")
    assert runner.calls == [(("iw", "dev", "p2p-wlp0s20-7", "del"), False)]
```

- [ ] **Step 5: Implement `system.py` and `link.py`**

`SubprocessRunner`:

- `run()` builds `["sudo", "-S", "-p", "", *args]` when `sudo=True`, passes the password as stdin, captures text output, enforces a timeout, and raises `CommandError` when `check=True` and the return code is non-zero.
- `popen()` builds the same prefix; when sudo is used, it opens stdin and writes the password once.
- `ensure_sudo()` runs `["true"]` with `sudo=True`; `main.py` calls it before `HelperClient.start()` so the helper can use `sudo -n`.
- No shell, no string concatenation.

`LinkConfigurator.configure()` calls `ip link set dev <if> up` then `ip addr replace <cidr> dev <if>`. `remove()` calls `iw dev <if> del` and ignores a non-zero exit when the interface is already gone. Do not add a default route.

- [ ] **Step 6: Run all tests and commit**

```bash
python -m pytest tests/p2p/test_wfd.py tests/p2p/test_link.py tests/test_system.py -q
ruff check src/dexonlinux tests
git add src/dexonlinux/system.py src/dexonlinux/p2p/wfd.py src/dexonlinux/p2p/link.py tests
git commit -m "feat: add system runner, WFD config and link setup"
```

---

### Task 3: Direct wpa_supplicant backend

**Files:**
- Create: `src/dexonlinux/p2p/direct.py`
- Create: `tests/p2p/test_direct.py`

**Interfaces:**
- Consumes: `SupplicantControl`, `WfdConfigurator`, `LinkConfigurator`, `parse_event`, `GroupInfo`, `P2PError`.
- Produces: `DirectBackend(interface, ctrl, wfd, link, frequency=2437)` implementing `P2PBackend`.

- [ ] **Step 1: Write failing backend tests**

Use a fake ctrl with a queue of event lines:

```python
def test_pbc_request_starts_client_connect_and_returns_group():
    ctrl = FakeCtrl(
        events=[
            "P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc",
            'P2P-GROUP-STARTED p2p-wlp0s20-7 client ssid="x" freq=2437 '
            "go_dev_addr=ce:f9:f0:2b:0a:cc ip_addr=192.168.49.200 "
            "ip_mask=255.255.255.0 go_ip_addr=192.168.49.1",
        ]
    )
    link = FakeLink()
    backend = DirectBackend("wlp0s20f3", ctrl, FakeWfd(), link)
    group = backend.connect(stop=threading.Event(), timeout=1.0)
    assert "P2P_CONNECT ce:f9:f0:2b:8a:cc pbc freq=2437 go_intent=0" in ctrl.commands
    assert link.configured == [("p2p-wlp0s20-7", "192.168.49.200/24")]
    assert group.role == "client"
    assert group.go_ip == "192.168.49.1"


def test_go_role_is_rejected():
    ...
    with pytest.raises(P2PError, match="client"):
        backend.connect(...)


def test_pin_request_is_rejected_with_actionable_message():
    ...
    with pytest.raises(P2PError, match="PBC"):
        backend.connect(...)


def test_invitation_is_rejected_with_stale_group_message():
    ctrl = FakeCtrl(events=["P2P-INVITATION-RECEIVED ce:f9:f0:2b:8a:cc persistent=1"])
    backend = DirectBackend("wlp0s20f3", ctrl, FakeWfd(), FakeLink())
    with pytest.raises(P2PError, match="previously connected"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_go_negotiation_with_non_pbc_password_id_is_rejected():
    ctrl = FakeCtrl(events=["P2P-GO-NEG-REQUEST ce:f9:f0:2b:8a:cc dev_passwd_id=1 go_intent=15"])
    backend = DirectBackend("wlp0s20f3", ctrl, FakeWfd(), FakeLink())
    with pytest.raises(P2PError, match="PBC"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_group_without_client_ip_fails_clearly():
    ctrl = FakeCtrl(
        events=[
            "P2P-PROV-DISC-PBC-REQ ce:f9:f0:2b:8a:cc",
            'P2P-GROUP-STARTED p2p-wlp0s20-7 client ssid="x" freq=2437 '
            "go_dev_addr=ce:f9:f0:2b:0a:cc go_ip_addr=192.168.49.1",
        ]
    )
    backend = DirectBackend("wlp0s20f3", ctrl, FakeWfd(), FakeLink())
    with pytest.raises(P2PError, match="WPS client address"):
        backend.connect(stop=threading.Event(), timeout=1.0)


def test_close_removes_group_and_restores_supplicant():
    ctrl = FakeCtrl()
    link = FakeLink()
    wfd = FakeWfd()
    backend = DirectBackend("wlp0s20f3", ctrl, wfd, link)
    backend.group_ifname = "p2p-wlp0s20-7"
    backend.close()
    assert "P2P_GROUP_REMOVE p2p-wlp0s20-7" in ctrl.commands
    assert wfd.restored is True
    assert ctrl.closed is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/p2p/test_direct.py -q`
Expected: FAIL with missing `DirectBackend`.

- [ ] **Step 3: Implement `DirectBackend`**

`prepare()`:

1. `ctrl.open()`;
2. `wfd.apply(ctrl)`;
3. `ctrl.command("P2P_EXT_LISTEN 1000 1000")`.

`connect(stop, timeout=90.0)`:

- loop while not stop and before deadline:
  - `for line in ctrl.pump(0.5)`: parse; handle:
    - `PBC_REQUEST`: `P2P_CONNECT <mac> pbc freq=2437 go_intent=0` once per peer; reset pending after 60 s;
    - `PIN_REQUEST`: raise `P2PError("phone requested a PIN; PBC is required")`;
    - `INVITATION`: raise `P2PError("phone sent a previously connected group invitation; forget DexOnLinux on the phone and retry")`;
    - `GO_NEGOTIATION`: if `passwd_id` is present and not `4` (PBC), raise `P2PError("phone requested a non-PBC WPS method; PBC is required")`; if no pending connect, send PBC connect;
    - `GROUP_STARTED`: if role != `"client"`, raise `P2PError("phone negotiated the laptop as GO; client role required")`; if `client_ip` is missing, raise `P2PError("group started without a WPS client address; DHCP fallback is not implemented in v1")`; otherwise `link.configure(ifname, f"{client_ip}/{client_prefix}")` and return group;
    - `FAILURE`: raise `P2PError(raw)`;
    - `GROUP_REMOVED`: reset group state and continue.
- on timeout: raise `P2PError("P2P connection timed out")`.

`close()`:

- `P2P_GROUP_REMOVE <ifname>` if a group was formed;
- `link.remove(ifname)` as fallback only if the interface still exists;
- `wfd.restore()`;
- `ctrl.close()`.

The spike's 20 ms permanent watcher is not ported. Keep the proven post-group `ip link set up` + `ip addr replace`. If live testing shows the association deauths before `P2P-GROUP-STARTED`, add a **bounded** watcher: start it when `P2P_CONNECT` is sent, poll `/sys/class/net` every 50 ms for at most 10 s, stop it on group/failure/stop, and remove it from the code if post-group configuration is sufficient. Do not restore the permanent 20 ms loop.

- [ ] **Step 4: Run tests and commit**

```bash
python -m pytest tests/p2p/test_direct.py -q
ruff check src/dexonlinux/p2p/direct.py tests/p2p/test_direct.py
git add src/dexonlinux/p2p/direct.py tests/p2p/test_direct.py
git commit -m "feat: add direct wpa_supplicant P2P backend"
```

---

### Task 4: RTSP WFD sink

**Files:**
- Create: `src/dexonlinux/wfd/__init__.py`
- Create: `src/dexonlinux/wfd/rtsp.py`
- Create: `tests/wfd/test_rtsp.py`
- Create: `tests/wfd/fake_source.py`

**Interfaces:**
- Consumes: stdlib only.
- Produces: `WfdSink(host, port=7236, on_event=None, connect_timeout=5.0)` with `run()`, `stop()`, `rtp_port`.

- [ ] **Step 1: Port the spike tests first**

Copy `spike/selftest_rtsp.py`, `spike/selftest_rtsp_parser.py`, and `spike/fake_source.py` into `tests/wfd/`, converting prints to pytest assertions. Keep the exact M1–M7 and keepalive fixtures.

Key assertions:

- fragmented response parses;
- two messages in one buffer parse;
- `GET_PARAMETER` with `Session` returns a bare `200`;
- `wfd_presentation_URL` with a trailing ` none` uses only the first token;
- SETUP is sent with `client_port=<rtp-port>` and no RTCP port;
- TEARDOWN returns `200` and stops;
- TCP close ends the session.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/wfd/test_rtsp.py -q`
Expected: FAIL with missing module.

- [ ] **Step 3: Implement `wfd/rtsp.py`**

Port `spike/rtsp.py` with these changes:

- logger name `dexonlinux.wfd.rtsp`;
- no spike-specific imports;
- `WfdSink` binds its RTP socket before connecting, drains it in one bounded thread (`recvfrom(2048)`), and closes it in `finally`;
- `run()` returns cleanly on TEARDOWN, TCP close, or `stop()`;
- `on_event` receives `connected`, `video_formats`, `streaming`, `disconnected`;
- keepalive M16 remains a bare `200`;
- `wfd_presentation_URL` is split on whitespace;
- no RTCP socket.

- [ ] **Step 4: Run tests and commit**

```bash
python -m pytest tests/wfd/test_rtsp.py -q
ruff check src/dexonlinux/wfd tests/wfd
git add src/dexonlinux/wfd tests/wfd
git commit -m "feat: add WFD RTSP sink"
```

---

### Task 5: Privileged helper and client

**Files:**
- Create: `src/dexonlinux/helper/__init__.py`
- Create: `src/dexonlinux/helper/__main__.py`
- Create: `src/dexonlinux/helper/client.py`
- Create: `tests/helper/test_client.py`
- Create: `tests/helper/fake_helper.py`

**Interfaces:**
- Consumes: `DirectBackend`, `SupplicantControl`, `WfdConfigurator`, `LinkConfigurator`, `GroupInfo`.
- Produces:
  - `HelperEvent(kind: str, data: dict[str, object])`
  - `HelperClient.start(interface: str, backend: str = "direct", command: Sequence[str] | None = None) -> HelperClient`
  - `HelperClient.process: subprocess.Popen[str]`
  - `HelperClient.wait_for(kind: str, timeout: float) -> HelperEvent`
  - `HelperClient.stop() -> None`
  - helper events: `ready`, `peer_found`, `group_started`, `group_finished`, `error`.

- [ ] **Step 1: Write the failing client test with a fake helper**

`tests/helper/fake_helper.py` prints JSON lines and exits on SIGTERM:

```python
#!/usr/bin/env python3
import json
import signal
import sys
import time

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
print(json.dumps({"event": "ready", "data": {"backend": "direct"}}), flush=True)
print(json.dumps({"event": "group_started", "data": {
    "ifname": "p2p-test", "role": "client",
    "client_ip": "192.168.49.200", "client_prefix": 24,
    "go_ip": "192.168.49.1", "peer_mac": "aa:bb:cc:dd:ee:ff",
    "frequency": 2437,
}}), flush=True)
while True:
    time.sleep(1)
```

Test:

```python
def test_client_reads_events_and_stops(tmp_path):
    client = HelperClient.start(
        "wlp-test",
        backend="direct",
        command=[sys.executable, str(Path(__file__).with_name("fake_helper.py"))],
    )
    try:
        assert client.wait_for("ready", timeout=2).data["backend"] == "direct"
        group = client.wait_for("group_started", timeout=2)
        assert group.data["ifname"] == "p2p-test"
        assert group.data["client_ip"] == "192.168.49.200"
    finally:
        client.stop()
    assert client.process.poll() is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/helper/test_client.py -q`
Expected: FAIL with missing module.

- [ ] **Step 3: Implement the helper**

`helper/__main__.py`:

- arguments: `--interface`, `--backend {direct}` (Task 6 extends this to `nm`), `--p2p-freq` (default 2437);
- resolve the ctrl socket, create `SupplicantControl`, `WfdConfigurator`, `LinkConfigurator`, and the selected backend;
- install SIGINT/SIGTERM handlers that set a `threading.Event`;
- `backend.prepare()`, emit `ready` with backend/interface/ctrl;
- `group = backend.connect(stop)`; emit `group_started` with all `GroupInfo` fields;
- then pump the supplicant in a loop until stop; forward `P2P-GROUP-REMOVED` as `group_finished` and break;
- on `P2PError`/`SupplicantError`/`CommandError`, emit `error` and exit 1;
- `finally`: `backend.close()`.

`helper/client.py`:

- `start()` uses `sudo -n` plus the installed `dexonlinux-linkd` console script when available, falling back to `<sys.executable> -m dexonlinux.helper`; `command` is injectable for tests;
- `start()` sets `start_new_session=True`, so `sudo` and the root helper share a process group;
- `stdout` reader thread parses bounded JSON lines (max 64 KiB) into a `queue.Queue`;
- `stderr` is logged line by line at debug;
- `wait_for()` blocks with a deadline and raises `P2PError` on timeout or `error` event;
- `stop()` sends SIGTERM to the process group via `os.killpg`, waits 3 s, kills the group on timeout, then joins the reader. Never leave the root helper orphaned when `sudo` exits.
- `main.py` calls `SubprocessRunner.ensure_sudo()` before starting the helper; if `sudo -n` still fails, tell the user to rerun and authenticate sudo.

- [ ] **Step 4: Test helper integration with a fake backend**

Add a unit test that imports `helper.__main__` with a fake backend factory (inject via a module-level `make_backend` or a small `_run(backend, ...)` function) and captures JSON events. Assert `ready` then `group_started`, and that `close()` was called.

- [ ] **Step 5: Run tests and commit**

```bash
python -m pytest tests/helper -q
ruff check src/dexonlinux/helper tests/helper
git add src/dexonlinux/helper tests/helper
git commit -m "feat: add privileged P2P helper and client"
```

---

### Task 6: NetworkManager-owned backend (gated by Gate 0)

**Do not start this task unless Gate 0 passed.**

**Files:**
- Create: `src/dexonlinux/p2p/nm.py`
- Create: `tests/p2p/test_nm.py`
- Modify: `src/dexonlinux/helper/__main__.py` (select `nm` backend)

**Interfaces:**
- Consumes: `SupplicantControl`, `WfdConfigurator`, `Runner`, `parse_event`, `GroupInfo`.
- Produces: `NetworkManagerBackend(interface, ctrl, runner, wfd, peer_timeout=30.0)` implementing `P2PBackend`.

- [ ] **Step 1: Write failing NM backend tests**

```python
def test_creates_profile_and_returns_client_group():
    runner = FakeRunner(
        results={
            ("nmcli", "connection", "up", "dexonlinux-p2p"): ok(),
        }
    )
    ctrl = FakeCtrl(events=[
        "P2P-DEVICE-FOUND ce:f9:f0:2b:8a:cc name='S24' wfd_dev_info=0x01101c440032",
        'P2P-GROUP-STARTED p2p-wlp0s20-7 client ssid="x" freq=2437 '
        "go_dev_addr=ce:f9:f0:2b:0a:cc ip_addr=192.168.49.200 "
        "ip_mask=255.255.255.0 go_ip_addr=192.168.49.1",
    ])
    backend = NetworkManagerBackend("wlp0s20f3", ctrl, runner, FakeWfd())
    group = backend.connect(stop=threading.Event(), timeout=1.0)
    assert group.role == "client"
    assert ("nmcli", "connection", "add", "type", "wifi-p2p", "con-name", "dexonlinux-p2p",
            "wifi-p2p.peer", "ce:f9:f0:2b:8a:cc", "ipv4.method", "auto",
            "autoconnect", "no") in runner.calls
```

Also test:

- `wfd-ies` and `wps-method pbc` are set;
- role `GO` raises;
- `nmcli connection up` failure raises with its stderr;
- `close()` runs `connection down` and `connection delete`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/p2p/test_nm.py -q`
Expected: FAIL with missing module.

- [ ] **Step 3: Implement `NetworkManagerBackend`**

`prepare()`:

- `ctrl.open()`;
- `wfd.apply(ctrl)`;
- `ctrl.command("P2P_EXT_LISTEN 1000 1000")` to discover the phone.

`connect()`:

1. wait up to `peer_timeout` for `DEVICE_FOUND` with `wfd=True`; store MAC;
2. delete stale `dexonlinux-p2p` profile (`nmcli connection delete` with `check=False`);
3. add profile: `nmcli connection add type wifi-p2p con-name dexonlinux-p2p wifi-p2p.peer <mac> ipv4.method auto autoconnect no`;
4. modify profile: `nmcli connection modify dexonlinux-p2p wifi-p2p.wfd-ies 00:00:06:00:11:1c:44:00:c8 wifi-p2p.wps-method pbc`;
5. `nmcli connection up dexonlinux-p2p` with a 45 s timeout;
6. pump ctrl for `GROUP_STARTED` (10 s); reject GO role; return group;
7. do not configure the link/address: NM owns the group and IP.

`close()`:

- `nmcli connection down dexonlinux-p2p` (ignore failure);
- `nmcli connection delete dexonlinux-p2p` (ignore failure);
- `wfd.restore()`;
- `ctrl.close()`.

Set `P2P_EXT_LISTEN` off in `close()` and do not call `P2P_GROUP_REMOVE`: NM owns the group.

- [ ] **Step 4: Wire `--backend nm` in the helper**

The helper selects `NetworkManagerBackend` when `--backend nm`; otherwise `DirectBackend`. Extend the `--backend` choices to `{direct,nm}`. The doctor will pass the choice. If Gate 0 failed, keep the helper restricted to `direct` and do not add `nm`.

- [ ] **Step 5: Run tests and commit**

```bash
python -m pytest tests/p2p/test_nm.py tests/helper -q
ruff check src/dexonlinux/p2p/nm.py tests/p2p/test_nm.py
git add src/dexonlinux/p2p/nm.py src/dexonlinux/helper/__main__.py tests/p2p/test_nm.py
git commit -m "feat: add NetworkManager-owned P2P backend"
```

---

### Task 7: ADB, scrcpy and session orchestration

**Files:**
- Create: `src/dexonlinux/device.py`
- Create: `src/dexonlinux/scrcpy.py`
- Create: `src/dexonlinux/session.py`
- Create: `tests/test_device.py`
- Create: `tests/test_session.py`

**Interfaces:**
- Consumes: `HelperClient`, `WfdSink`, `Runner`, `ScrcpyDisplay`, `AdbDevice`.
- Produces:
  - `parse_adb_devices(output: str) -> list[AdbDevice]`
  - `parse_scrcpy_displays(output: str) -> list[ScrcpyDisplay]`
  - `Device(runner)` with `list_adb_devices() -> list[AdbDevice]` and `list_scrcpy_displays(device: str) -> list[ScrcpyDisplay]`.
  - `ScrcpySession(device, runner, *, display_id=None, fullscreen=False)` with `start() -> subprocess.Popen`, `stop()`, `wait()`; if `display_id` is None, `start()` lists displays and selects one via `select_from_list` after DeX is active.
  - `DexSession(helper, scrcpy, sink_factory, start_delay=1.0).run() -> int`.

- [ ] **Step 1: Move and test the parsers and add `Device`**

Copy `parse_adb_devices` and `parse_scrcpy_displays` from `commands.py` into `device.py`. Add `Device(runner)` with `list_adb_devices()` (`adb devices -l`) and `list_scrcpy_displays(device)` (`scrcpy -s <device> --list-displays`), using `Runner.run`. Tests use the current outputs:

```python
def test_parse_scrcpy_displays_prefers_landscape_hint():
    displays = parse_scrcpy_displays(
        "--display-id=13 (1920x1080) ...\n--display-id=0 (1080x2400) ..."
    )
    assert displays[0].display_id == 13
    assert displays[0].hint().startswith("likely DeX")
```

- [ ] **Step 2: Write failing session tests**

Use fakes:

```python
def test_session_starts_scrcpy_on_streaming_and_stops_on_exit():
    helper = FakeHelper(group=GroupInfo(...))
    sink = FakeSink(events=["streaming"])
    scrcpy = FakeScrcpy()
    session = DexSession(helper, scrcpy, lambda host: sink)
    assert session.run() == 0
    assert scrcpy.started is True
    helper.stopped is True
    sink.stopped is True
```

Also test: helper error → exit 1; sink error → cleanup; scrcpy exits → sink stopped and helper stopped.

- [ ] **Step 3: Implement `ScrcpySession`**

- `start()` uses `scrcpy -s <device> --window-title DexOnLinux --mouse-bind=++++`, optional `--fullscreen`, optional `--display-id`;
- sets `SCRCPY_ICON_PATH`/`SCRCPY_ICON_DIR` when the asset exists;
- if `display_id` is None, lists displays *after* the RTSP stream is up (DeX display exists only then), prints the existing landscape hint, and selects one; an empty list falls back to scrcpy's default display;
- drains scrcpy output in a bounded thread and logs at debug;
- `stop()` terminates, waits, kills on timeout;
- no RTP drainer: `WfdSink` owns RTP.

- [ ] **Step 4: Implement `DexSession`**

- `run()`: wait for `group_started`; create `WfdSink(go_ip, 7236, on_event=...)`; run it in the current thread;
- on `streaming`: start scrcpy once, after `start_delay` (default 1.0 s), so DeX has created the display;
- on `disconnected`: stop scrcpy;
- when scrcpy exits: call `sink.stop()`; return 0;
- `finally`: stop scrcpy, stop sink, helper.stop().
- If the group role is not client or `go_ip` is empty, raise `P2PError`.
- Display selection is `ScrcpySession`'s job, not `DexSession`'s; do not preselect before DeX is active.

- [ ] **Step 5: Run tests and commit**

```bash
python -m pytest tests/test_device.py tests/test_session.py -q
ruff check src/dexonlinux/device.py src/dexonlinux/scrcpy.py src/dexonlinux/session.py tests
git add src/dexonlinux/device.py src/dexonlinux/scrcpy.py src/dexonlinux/session.py tests
git commit -m "feat: add scrcpy device layer and session orchestration"
```

---

### Task 8: Doctor, CLI and removal of the miraclecast path

**Files:**
- Create: `src/dexonlinux/doctor.py`
- Create: `tests/test_doctor.py`
- Modify: `src/dexonlinux/main.py`
- Modify: `src/dexonlinux/utils.py` (instructions, no PIN)
- Delete: `src/dexonlinux/runtime.py`
- Delete: `src/dexonlinux/stream_handler.py`
- Delete: `src/dexonlinux/network.py`
- Delete: `src/dexonlinux/commands.py` (its device/process helpers moved to `device.py`, `scrcpy.py`, `system.py`)
- Modify: `src/dexonlinux/models.py` (remove `SinkctlEvent`; keep `AdbDevice`, `ScrcpyDisplay`)
- Modify: `pyproject.toml` (no miraclecast keywords/requirements)

**Interfaces:**
- Consumes: `Runner`, `device.py`, `HelperClient`, `DexSession`.
- Produces:
  - `StackReport(manager: str, backend: str, supported: bool, reasons: tuple[str, ...])`
  - `list_p2p_interfaces(runner: Runner) -> list[str]` (port the current `/sys/class/net` + `iw` probe)
  - `probe_stack(runner: Runner, interface: str, ctrl_dir: Path = Path("/run/wpa_supplicant")) -> StackReport`
  - CLI flags: `--interface`, `--device`, `--display-id`, `--fullscreen`, `--backend {auto,nm,direct}`, `--doctor`, `--debug`, `--no-color`, `--no-banner`, `--yes`, `--log-file`.

- [ ] **Step 1: Write failing doctor tests**

`FakeRunner` in `tests/fakes.py` maps command tuples to stdout and supports non-zero return codes; it must return a `CommandResult`, not a bare string.

```python
def test_nm_with_p2p_ctrl_selects_nm_even_if_wpa_service_is_not_a_unit(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20f3").touch()
    runner = FakeRunner(
        outputs={
            ("systemctl", "is-active", "NetworkManager"): "active\n",
            ("systemctl", "is-active", "iwd"): "inactive\n",
            ("nmcli", "--version"): "nmcli tool, version 1.46.0\n",
            ("iw", "dev"): "Unnamed/non-netdev interface\n type P2P-device\n",
            ("iw", "list"): "Supported interface modes:\n * P2P-client\n * P2P-GO\n",
        },
    )
    report = probe_stack(runner, "wlp0s20f3", ctrl_dir=tmp_path)
    assert report.supported is True
    assert report.backend == "nm"


def test_nm_iwd_is_unsupported_in_v1():
    runner = FakeRunner(
        outputs={
            ("systemctl", "is-active", "NetworkManager"): "active\n",
            ("systemctl", "is-active", "iwd"): "active\n",
        }
    )
    report = probe_stack(runner, "wlp0s20f3")
    assert report.supported is False
    assert "iwd" in " ".join(report.reasons).lower()


def test_nm_below_146_is_unsupported(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20f3").touch()
    runner = FakeRunner(
        outputs={
            ("systemctl", "is-active", "NetworkManager"): "active\n",
            ("nmcli", "--version"): "nmcli tool, version 1.44.0\n",
            ("iw", "dev"): "Unnamed/non-netdev interface\n type P2P-device\n",
            ("iw", "list"): "Supported interface modes:\n * P2P-client\n * P2P-GO\n",
        }
    )
    report = probe_stack(runner, "wlp0s20f3", ctrl_dir=tmp_path)
    assert report.supported is False
    assert "1.46" in " ".join(report.reasons)
```

Also test: NM+iwd unsupported; ConnMan unsupported; no NM + P2P ctrl → `direct`; no P2P → unsupported; driver without P2P-client/GO → unsupported; missing `adb`/`scrcpy` → unsupported.

- [ ] **Step 2: Implement `doctor.py`**

- `systemctl is-active NetworkManager/iwd/connman` (ignore a missing unit); do **not** require a separate active `wpa_supplicant` unit: NetworkManager can own the supplicant process, and many systems do not expose it as its own unit;
- if NM + iwd is active → unsupported (`network stack: NM+iwd is Tier 2; not implemented`);
- if ConnMan is active → unsupported;
- if NM is active and a P2P device plus `/run/wpa_supplicant/p2p-dev-*` ctrl socket exists → candidate `nm`; require `nmcli --version` ≥ 1.46 for `wifi-p2p.wfd-ies`, otherwise unsupported with the version reason;
- if no NM and a P2P device plus P2P ctrl socket exists → `direct`;
- check `iw list` for `P2P-client`/`P2P-GO`; if absent → unsupported;
- check `adb` and `scrcpy` with `shutil.which`;
- `list_p2p_interfaces()` ports the current `/sys/class/net` + `iw dev` + `iw phy` probe and returns interface names;
- never mutate anything.

- [ ] **Step 3: Rewrite `main.py`**

- `--doctor` prints the report and exits 0/1;
- `--backend` overrides doctor only for diagnostics; default `auto`; a forced `nm` still fails fast if Gate 0 did not pass or the stack is unsupported;
- choose interface from `list_p2p_interfaces()` (keep the current selection logic but simplify labels); do not use the old NM `InterfaceInfo` labels;
- choose ADB device with `Device.list_adb_devices()` (keep current behavior);
- prompt for the sudo password once (as the current CLI does), construct `SubprocessRunner(password)`, and call `ensure_sudo()` before `HelperClient.start(interface, backend=report.backend)`;
- call `DexSession`;
- `--yes` skips only the final confirmation;
- remove `--network-mode`, `--port` and all miraclecast text.

- [ ] **Step 4: Update user instructions**

`print_dex_instructions()` says:

```text
1. Open Smart View / DeX on the phone.
2. Keep the phone unlocked.
3. The laptop will connect using Wi-Fi Direct PBC.
4. If the phone still shows DexOnLinux as "previously connected", forget it and retry.
```

No PIN box, no notification.

- [ ] **Step 5: Remove the old path**

- Delete `runtime.py`, `stream_handler.py`, `network.py`, `commands.py`.
- Remove `SinkctlEvent` from `models.py`; keep `AdbDevice` and `ScrcpyDisplay`.
- Remove miraclecast from `pyproject.toml` keywords and README.
- Do not delete the `spike/` tree; it remains the reference implementation.

- [ ] **Step 6: Run tests and commit**

```bash
python -m pytest -q
ruff check src tests
git add src pyproject.toml README.md
git commit -m "feat: replace miraclecast link path with WFD session"
```

---

### Task 9: Packaging, documentation and live acceptance

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `scripts/install.sh`
- Create: `docs/research/2026-10-21-live-acceptance.md`
- Modify: `docs/superpowers/specs/2026-10-20-miraclecast-free-wifi-link-design.md`
- Modify: `docs/research/2026-10-20-wfd-sink-spike-report.md`

- [ ] **Step 1: Packaging**

- Add `dexonlinux-linkd = "dexonlinux.helper.__main__:main"` to `[project.scripts]`;
- keep `dexonlinux = "dexonlinux.main:main"`;
- confirm `pytest` and `ruff` are in `[dependency-groups].dev`;
- remove miraclecast from README requirements and install script;
- add `wpa_supplicant`, `iw`, `ip`, NetworkManager/`nmcli`, `adb`, `scrcpy` to requirements;
- rewrite `scripts/install.sh` with package-manager branches for Debian/Ubuntu (`apt`), Arch (`pacman`), and Fedora (`dnf`); install Python, `wpa_supplicant`, `iw`, `iproute2`, NetworkManager, `android-tools`/`adb`, and `scrcpy` from the distro or print a clear manual instruction if the distro package is too old; do not install miraclecast build dependencies;
- keep Python ≥ 3.9.

- [ ] **Step 2: README**

Document:

- what DeXOnLinux does and does not do (WFD triggers DeX; display is scrcpy over USB);
- supported stacks: NM+wpa_supplicant (primary), standalone wpa_supplicant; iwd not in v1;
- PBC, no PIN;
- `dexonlinux --doctor`;
- troubleshooting for stale phone entries, unsupported stack, P2P channel;
- `ping 1.1.1.1` check.

- [ ] **Step 3: Build and test**

```bash
python -m pytest -q
ruff check src tests
python -m build
python -m twine check dist/*
```

- [ ] **Step 4: Live acceptance**

On the AX201 laptop:

1. `dexonlinux --doctor` → supported, backend `nm`.
2. Start `dexonlinux`; phone open Smart View.
3. Confirm DeX appears in scrcpy.
4. In a second terminal: `ping -c 10 1.1.1.1` → 0% loss.
5. Close scrcpy / Ctrl-C; confirm no `p2p-*` group, `wifi_display` restored, `p2p_go_intent` restored.
6. Repeat twice with no reboot.

Record exact output and metrics in `docs/research/2026-10-21-live-acceptance.md`.

- [ ] **Step 5: Update docs and commit**

- Mark the spec's PIN section as superseded by the PBC decision;
- link the spike report and stack matrix;
- commit:

```bash
git add pyproject.toml README.md scripts docs
git commit -m "docs: document WFD product path and acceptance"
```

---

## Self-Review Checklist

- [ ] Gate 0 is run before Task 6; if PBC/role fails, Task 6 is not implemented.
- [ ] PBC-only (`config_methods 0x80`) is accepted by the installed supplicant before the direct backend ships.
- [ ] No task stops or freezes NetworkManager or wpa_supplicant.
- [ ] No task introduces `dbus-next`; ctrl_iface + `nmcli` cover v1.
- [ ] Every public interface named in a task is defined before a later task consumes it.
- [ ] Every backend test covers role=GO, PIN request, invitation/stale group, non-PBC password id, and timeout.
- [ ] RTSP tests cover M16, TEARDOWN and TCP close.
- [ ] Cleanup is tested for Ctrl-C/SIGTERM and stale group removal.
- [ ] The doctor rejects NM+iwd, ConnMan and missing P2P before mutation.
- [ ] README no longer claims miraclecast or PIN.
- [ ] Live acceptance verifies DeX + 0% internet loss.

---

## Execution Notes

- Gate 0 and all live tests require the phone and sudo; unit tests do not.
- Do not run `nmcli` while NetworkManager is frozen; the product never freezes it.
- Keep the spike branch untouched as the reference implementation.
- If Gate 0 fails, stop and update the design before implementing Task 6; the direct backend is still useful for standalone wpa_supplicant validation, but it is not a product solution on NetworkManager desktops.
