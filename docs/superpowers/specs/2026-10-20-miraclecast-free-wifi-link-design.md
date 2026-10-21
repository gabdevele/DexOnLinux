# DexOnLinux — miraclecast-free Wi-Fi link + Wi-Fi Display sink (design)

Date: 2026-10-20 · Status: draft, pending approval

> **Implementation amendment (2026-10-21):** Gate 0 rejected NM-owned P2P
> (5 GHz AX201 firmware crash) and no runtime detach worked without a reboot.
> The accepted implementation uses the direct wpa_supplicant P2P client backend
> with a session-scoped NetworkManager `SIGSTOP`/`SIGCONT`, plus a crash
> watchdog. The product does not stop or restart NetworkManager or
> wpa_supplicant, and the station Wi-Fi/internet stay usable. The NM-owned
> primary path and the PIN fallback described below are superseded. See
> `docs/research/2026-10-21-nm-p2p-probe.md` and
> `docs/research/2026-10-21-live-acceptance.md`.

Companion research (all factual claims referenced below live there, with primary sources):
[`docs/research/2026-10-20-wfd-sink-without-miraclecast.md`](../../research/2026-10-20-wfd-sink-without-miraclecast.md).

---

## 1. Intent

DexOnLinux triggers Samsung wireless DeX by pretending to be a Miracast/Wi-Fi Display sink
with miraclecast, then streams the DeX desktop with scrcpy over adb. miraclecast starts a
**private** wpa_supplicant and DHCP, which forces the app to stop NetworkManager and
wpa_supplicant and take the Wi-Fi adapter exclusively. Consequences: Wi-Fi internet dies for
the whole DeX session, and the project depends on an unstable, hard-to-package C daemon.

This design replaces miraclecast with:

1. the **system wpa_supplicant** (already present on every target distro, built with P2P +
   Wi-Fi Display), driven through D-Bus by a small privileged helper;
2. a small **in-project RTSP client** that performs the Wi-Fi Display sink handshake
   (the *phone* is the RTSP server — the laptop connects to it);
3. the existing **scrcpy/adb** display path, unchanged.

Result: DeX works while Wi-Fi internet stays up on the same radio, with no external P2P
daemon and no network services stopped.

## 2. Goals

- **G1 — No miraclecast, no private supplicant.** The default path never stops NetworkManager
  or wpa_supplicant and never takes the adapter exclusively.
- **G2 — Internet preserved.** The Wi-Fi STA connection to the router stays connected and
  usable for the entire DeX session on the same radio.
- **G3 — Portable.** First-class on Debian, Ubuntu, Arch, Fedora; works with distro-built
  wpa_supplicant ≥ 2.10; chipset support detected and reported, not assumed.
- **G4 — Well-architected.** Explicit layers (link / WFD / orchestration / device); the RTSP
  state machine is unit-testable without hardware; behavior is not inferred from subprocess
  text scraping.
- **G5 — Same UX.** Same commands, same start/stop semantics, Ctrl+C cleanup guarantees,
  scrcpy behavior (display picker, `--fullscreen`, `--display-id`).

## 3. Non-goals

- Generic Miracast sink (arbitrary source devices, media playback, UIBC, HDCP, EDID).
- Rendering the Wi-Fi Display media stream (RTP is drained and discarded — proven safe:
  session liveness is RTSP-only, research §5d).
- UI modernization (`rich`/`textual`), packaging (AUR/deb/Flatpak), polkit, systemd service.
- Windows/macOS; non-DeX Android desktop modes.
- Chipsets/drivers without P2P support at all (e.g. rtw88): impossible today and after.

## 4. Anti-goals

- **A1** Never degrade a working internet connection silently.
- **A2** Never leave modified network state behind after exit.
- **A3** No patched/private supplicant builds; no system config edits required.
- **A4** No silent half-works: if the environment cannot support the design, `doctor` says so
  *before* anything starts.
- **A5** No behavioral regressions in cleanup, reconnect and error reporting.

## 5. Constraints

- **C1** Python ≥ 3.9, same packaging (`uv tool install` / pip).
- **C2** Minimal new runtime deps: `dbus-next` (pure Python, asyncio) as primary.
  The supplicant ctrl_iface text protocol (`WFD_SUBELEM_SET`, `P2P_*`) is the documented
  fallback if the dependency proves problematic.
- **C3** System deps already required today: `adb`, `scrcpy`, `iw`; running `wpa_supplicant`
  (under NM is fine); `sudo` for one helper invocation.
- **C4** Privileged surface = exactly one small helper process; its API is enumerated in §7.3.
- **C5** Distro supplicants ship `CONFIG_P2P` + `CONFIG_WIFI_DISPLAY` (verified in
  Debian/Ubuntu/Arch/Fedora packaging, research §1); runtime verification is part of `doctor`.

## 6. Technical foundation (facts this design stands on)

- **F1 — Roles**: the phone (source) is the P2P GO and hosts the RTSP server on TCP 7236;
  the laptop (sink) is the P2P client and the RTSP client. `go_intent=0` reproduces
  miraclecast's proven role split; the phone then runs DHCP (research §4, §5a).
- **F2 — Liveness**: the session lives on RTSP control traffic only. The source sends an
  M16 `GET_PARAMETER` every 25 s and reaps the session after 30 s without a lifeline;
  RTCP is never consulted. Therefore the RTP stream may be discarded entirely (research §5d).
- **F3 — wpa_supplicant does the P2P/WFD heavy lifting**: runtime WFD IE advertisement
  (global `WFDIEs` D-Bus property / `WFD_SUBELEM_SET`), GO negotiation execution,
  invitation handling, automatic WFD service-discovery responder (research §2).
  **Correction (builder review):** for an *unknown* peer the supplicant does **not**
  auto-accept the GO negotiation — it answers `Fail: information currently unavailable`
  and only reports `GONegotiationRequest`; the app must react by initiating a client
  connect (`Connect {peer, wps_method=pbc, go_intent=0}`), exactly like miraclecast's sink
  (`ctl_fn_peer_go_neg_request` → `P2P_CONNECT <mac> pbc`, 60 s timeout). See §7.4.3.
- **F4 — The sink must still implement the RTSP conversation**: answer M1/OPTIONS,
  M3/GET_PARAMETER, M4b/SET_PARAMETER, M5 trigger, M16 keepalives, TEARDOWN; send
  M2/OPTIONS, M6/SETUP, M7/PLAY (research §5b, §5c).
- **F5 — Concurrency is a driver property.** iwlwifi / ath11k / ath12k / rtw89 allow
  STA + P2P on one radio (iwlwifi even on two channels); mt792x and brcmfmac depend on
  firmware features; rtw88 has no P2P at all (research §3).
- **F6 — Invitation flow must be handled**: some clients send a P2P invitation to a
  persistent group instead of GO negotiation (miraclecast #407, research §5e).

## 7. Architecture

```
phone (One UI)                                laptop (Linux)
  │ 1. Smart View scan (P2P probe / WFD SD)     │
  │  ◄────────── WFD sink IEs ────────────►    │  wpa_supplicant (system, NM-managed)
  │ 2. GO negotiation → phone becomes GO        │
  │  ◄─────────── GroupStarted ──────────────   │  linkd (helper, root) via D-Bus
  │ 3. DHCP (phone serves)  ─────────────────►  │  linkd runs DHCP client → IP
  │ 4. TCP :7236  ────────────────────────────► │  WfdSession (RTSP client): M2..M7
  │  ◄── RTSP M1/M3/M4b/M5/M16, TEARDOWN ────   │  answers; discards RTP
  │ 5. DeX active on the phone                   │  scrcpy over adb = the DeX desktop
```

### 7.1 Processes and privilege model

- **`dexonlinux` (user process, existing entry point)** — CLI, orchestration, scrcpy,
  user-facing output.
- **`dexonlinux-linkd` (root helper, new entry point)** — child process of the CLI,
  communicates over **stdio, JSON-lines** (no sockets on disk). Owns: supplicant D-Bus
  session, WFDIEs/GOIntent manipulation with save/restore, P2P listen/group state,
  interface IP configuration (DHCP client), cleanup.

Privilege bootstrap: the CLI pre-authenticates once with
`sudo -S -p '' -v` (password from `getpass`, as today), then starts the helper with
`sudo -n <abs-path-to-dexonlinux-linkd> --interface <if>` so the helper's stdin/stdout stay
free for the JSON protocol. If `sudo -n` fails (expired cache), re-prompt and retry once.
Rationale for stdio over a socket/service: zero deployment footprint, no world-accessible
endpoint, lifetime bound to the session. Polkit + systemd service is a later phase reusing
the same helper executable.

### 7.2 Package layout (target)

```
src/dexonlinux/
  main.py               # CLI entry (existing name kept)
  cli/                  # arg parsing + wiring (extracted from main.py)
  session.py            # orchestration state machine + app events
  doctor.py             # preflight checks and verdicts
  link/
    __init__.py         # LinkBackend ABC + data types
    linkd_client.py     # CLI-side client of the helper protocol (spawn, JSON-lines)
  helper/
    __main__.py         # dexonlinux-linkd entry point
    supplicant.py       # D-Bus drive of wpa_supplicant (P2P + WFDIEs)
    dhcp.py             # minimal DHCP client (server only if GO mode ever lands)
  wfd/
    rtsp.py             # RTSP message encode/decode
    params.py           # wfd_* parameter build/parse
    session.py          # sink state machine (M-flow, keepalive, teardown)
    rtp.py              # UDP drain
    wfd_ies.py          # WFD subelement blob builder (used by helper)
  device.py             # adb/scrcpy wrappers (from today's commands.py)
  network.py            # retained for the future takeover mode; unused by default path
  models.py, utils.py   # keep
```

### 7.3 Helper protocol (stdlib JSON-lines; v1)

Commands (CLI → linkd), one JSON object per line:

| command | fields | meaning |
|---|---|---|
| `prepare` | `wfd_ies_hex`, `go_intent` (0), `ifname`, `device_name` ("DexOnLinux") | save current supplicant values, set WFDIEs + GOIntent + DeviceName |
| `advertise` | `duty_ms` (optional) | start/refresh ExtendedListen loop; idempotent |
| `stop_advertising` | — | leave listen state |
| `group_remove` | — | if a group is active, remove/disconnect it |
| `status` | — | emit a full status snapshot |
| `shutdown` | — | restore saved values, clean interfaces, exit 0 |

Events (linkd → CLI):

| event | fields | meaning |
|---|---|---|
| `ready` | `supplicant_version`, `capabilities` | helper up, environment validated |
| `peer_found` | `address`, `name`, `wfd_ies` (hex, may be empty) | informational (diagnostics) |
| `go_negotiation_requested` | `peer`, `wps_method` | informational; helper auto-responds with a client connect (§7.4.3) |
| `group_started` | `role` (`client`/`go`), `ifname`, `ssid`, `peer` | group formed |
| `ip_acquired` | `ifname`, `ip`, `gateway`, `source` (`dhcp`/`static`/`supplicant`) | interface usable |
| `invitation_received` | `peer`, `persistent` | helper auto-attempts join; informational |
| `group_finished` | `reason` | group gone (any cause) |
| `error` | `code`, `message` | fatal for the current phase |

Hard rules for the helper: validate `ifname` against `/sys/class/net`; bound every hex/blob
and string length; never invoke a shell; only the enumerated D-Bus methods on
`fi.w1.wpa_supplicant1`; restore saved supplicant values on `shutdown` and on crash
(signal handler + try/finally). The saved originals are also persisted to a root-owned
state file (`/run/dexonlinux/linkd-state.json`) **before** mutating anything, so a crashed
session can be repaired by the next run or reported by `doctor` (§7.4.7).

### 7.4 Link layer details

1. **Preflight (helper)**: verify P2P device exists (`iw dev` → `P2P-device`), the global
   `WFDIEs` property is present (proves WFD build), no other P2P group is active
   (`P2PDevice.Group` set → disruptive groups must be refused; informative only in v1).
2. **Advertise**: `prepare` sets the WFD IEs blob (§7.5) and `P2PDeviceConfig.GOIntent=0`
   (saving previous values). Then `ExtendedListen` with a modest duty cycle; re-arm after
   `GroupFinished`.
3. **Formation — GO negotiation.** The phone initiates with a GO Negotiation Request;
   the supplicant cannot accept it on its own for an unknown peer (F3 correction) and
   reports `GONegotiationRequest(peer, dev_passwd_id, go_intent)`. The helper's
   auto-response (internal, no user I/O): if no group and no pending connect exists, call
   `P2PDevice.Connect {peer, wps_method: pbc, go_intent: 0}` (WPS method derived from
   `dev_passwd_id`; a single pending connect at a time; 60 s timeout — same behavior as
   miraclecast's sink). Success → `GroupStarted(role=client)`; failure → log and keep
   advertising. `go_negotiation_requested` is emitted for observability only.
   **Formation — invitation.** On `InvitationReceived`, the helper attempts a
   persistent-group join (`P2P_CONNECT <addr> pbc persistent join` semantics); failures
   are logged, not fatal.
4. **IP**: after `GroupStarted` with role `client`, linkd brings the group interface up and
   runs a minimal **DHCP client** (DISCOVER/REQUEST, bounded retries). Lease obtained →
   `ip_acquired`. If the supplicant already reports assigned addresses (`IpAddr`,
   `IpAddrGo` in the `GroupStarted` payload), use them (`source=supplicant`). If DHCP fails
   after ~8 s, fall back to Android GO defaults (static `192.168.49.2/24`, gw
   `192.168.49.1`, `source=static`) and continue best-effort — logged clearly, never silent.
5. **Role guard**: if the negotiated role is `go` (unexpected), v1 aborts the session with a
   clear `error` event (no DHCP server implemented; miraclecast's own tracker shows the GO
   path is the flaky one). GO support is a possible phase-2 item.
6. **Cleanup**: `group_remove` + restore WFDIEs/GOIntent/DeviceName + remove any static IP.
7. **Crash repair**: `prepare` first checks for a leftover state file from a previous
   crashed session; if present, restore the recorded originals before applying new values
   (the next run self-heals). `doctor` also reports a `stale-state` warning when the file
   exists.

### 7.5 WFD IEs blob (sink advertisement)

Subelement list (`[id u8][len u16 BE][value]`), built by `wfd/wfd_ies.py`:

- Subelement **0 (Device Information)**, len 6:
  - u16 device-info bitmap — primary sink (`bits0-1 = 01b`), session available
    (`bits4-5 = 01b`), preferred connectivity optional;
  - u16 session-management control port = `0x1c44` (7236);
  - u16 max throughput (units of 0.5 Mbps; start with `0x00c8` = 100 Mbps).
- Candidate values to try at bring-up (spike S1): `0x0011` (plain primary sink),
  `0x0091` (with preferred connectivity), `0x0013`/`0x0093` (dual-role) — pick whichever
  the Samsung phone discovers. No other subelements in v1 (no coupled sink, no UIBC).

### 7.6 WFD RTSP client (`wfd/session.py`)

One TCP connection to `<peer-ip>:7236`; the sink's UDP RTP port is bound **before** the
handshake so it can be advertised (bind `0.0.0.0:0`, keep socket).

State machine: `CONNECT → M1_M2 → M3_M4 → AWAIT_TRIGGER → SETUP → PLAY → STREAMING →
STOPPED`, plus `FAILED(reason)`.

Behaviors (details in research §5b–5d):

- Answer inbound `OPTIONS` with `Public: org.wfa.wfd1.0, GET_PARAMETER, SET_PARAMETER`.
- Send M2 (`OPTIONS` + `Require: org.wfa.wfd1.0`).
- Answer M3 (`GET_PARAMETER`) with M4 capabilities:
  `wfd_content_protection: none`,
  `wfd_video_formats: 00 00 03 10 0001ffff 1fffffff 00001fff 00 0000 0000 10 none none`
  (miraclecast's proven Samsung defaults: all CEA/VESA/HH),
  `wfd_audio_codecs: AAC 00000007 00`,
  `wfd_client_rtp_ports: RTP/AVP/UDP;unicast <udp-port> 0 mode=play`
  (single port → "no RTCP" path on the source).
- Parse M4b (`SET_PARAMETER`, chosen formats + presentation URL); derive the resolution
  hint best-effort from the chosen video-format descriptor (cosmetic only — the UI already
  has display auto-detection).
- On M5 (`wfd_trigger_method: SETUP`): answer `200` **first**, then send
  `SETUP <presentation-url>` with `Transport: RTP/AVP/UDP;unicast;client_port=<udp-port>`
  (no RTCP port), then `PLAY` with `Session` as soon as SETUP is answered. Tolerate a
  source-side PLAY trigger arriving later; ignore it if already playing.
- Keepalive: answer every `GET_PARAMETER`/`SET_PARAMETER` carrying a `Session` header
  (`M16`) immediately. No sink-originated RTCP/RTP traffic.
- RTP: `rtp.py` drains and drops datagrams for the lifetime of the session.
- TEARDOWN (or TCP close): answer `200`, stop, report `disconnected`.
- Liveness watchdog: TCP closed, group finished, or 60 s without any RTSP traffic → session
  dead → cleanup, return to `ADVERTISING` **indefinitely** (matches today's UX: any clean
  disconnect — TEARDOWN, phone-side stop, screen off — just means "wait for the next
  connection"; the docs even tell users to simply reconnect).
- Failure counter: only *failures* count — handshake errors, protocol violations, connect
  attempts that never reach `STREAMING`. The counter resets on reaching `STREAMING`; after
  3 consecutive failures the app aborts with a diagnostics dump (log file).
- Protocol errors: log full transcript (no secrets involved), teardown group, same retry
  policy.

### 7.7 Orchestration (`session.py`)

States: `DOCTOR → PREPARE → ADVERTISING → LINK_UP → WFD_HANDSHAKE → STREAMING →
TEARDOWN`, with `ERROR(msg)` terminal except for the retry loop above.

Event mapping keeps the current consumer (`stream_handler.py`) almost untouched:

| app event | trigger | current equivalent |
|---|---|---|
| `peer_connected` | `group_started` | `[CONNECT] Peer: …` |
| `resolution` | M4b parsed | `SINK set resolution WxH` |
| `connected` | `PLAY` sent / first RTP datagram | `NOTICE: SINK connected` |
| `disconnected` | TEARDOWN / group finished | `SINK disconnected` |

scrcpy lifecycle unchanged: start ~2 s after `connected` (or immediately on `resolution`),
display picker unless `--display-id`, `--fullscreen` passthrough, scrcpy exit ends the
session.

### 7.8 `doctor`

Checks (each with a pass/fail/info verdict):

1. distro/kernel/session (informational).
2. supplicant: binary has WFD strings (`WFD_SUBELEM_SET|WFDIEs|WFD IE for Probe Response`);
   `iw dev` shows a `P2P-device`; global `WFDIEs` readable (via helper).
3. driver concurrency: parse `iw list` interface combinations for
   `managed(1) + {P2P-GO|P2P-client}(1) + P2P-device(1)`; record channel count; match
   against the known-good chipset matrix (research §3).
4. presence of `adb`, `scrcpy`; ADB device authorization (existing logic).
5. UDP port availability; no conflicting P2P group (`P2PDevice.Group` empty).
6. stale state: leftover state file from a crashed session (next `prepare` auto-repairs;
   `doctor` only reports).
7. verdict: `ok` | `unsupported-no-p2p` | `unsupported-no-concurrency` (v1 fails fast with
   a clear message and remediation notes; takeover mode is a later phase and reuses
   `network.py`'s existing modes).

## 8. Compatibility & fallbacks

| chipset family | verdict | note |
|---|---|---|
| iwlwifi (AX2xx/BE2xx) | concurrent OK (2 ch) | locally verified on AX201 |
| ath11k / ath12k | OK (1 ch; 2 with dual-stations) | source-verified |
| rtw89 | OK (1 ch; 2 with chanctx) | source-verified |
| mt792x | firmware-dependent (`MT792x_FW_CAP_CNM`) | doctor decides via `iw list` |
| brcmfmac | firmware-dependent (P2P + RSDB) | doctor decides via `iw list` |
| rtw88 | unsupported | no P2P types at all — same limit applies to miraclecast |

v1 policy: unsupported machines get a clear `doctor` failure, not a degraded mode.
Phase 2 candidates: takeover mode via a private supplicant (reuses `network.py`), GO-role
support (DHCP server), polkit/systemd packaging of the helper.

## 9. Testing strategy

- **Unit (no hardware)**: `wfd/rtsp.py` encode/decode against fixtures copied verbatim from
  AOSP `WifiDisplaySink.cpp`/`WifiDisplaySource.cpp` and miraclecast `ctl-sink.c`
  (M1, M2, M3, M4, M4b, M5, M16, TEARDOWN); `wfd/params.py` parsing;
  `wfd/wfd_ies.py` byte blobs; `doctor` combination parser against captured `iw list`
  outputs (AX201, mt7921, brcmfmac, rtw88); helper JSON framing.
- **Component (no hardware)**: an asyncio fake source (TCP server replaying the M-flow)
  drives `WfdSession` through a full handshake, keepalives and teardown.
- **Hardware-in-the-loop (manual, documented procedure)**: AX201 (this laptop) first;
  then volunteer machines per chipset family; Samsung phones (at least S21/S22-class and a
  2025/2026 One UI model). Success criteria: DeX desktop appears in scrcpy, `ping 1.1.1.1`
  stays at 0% loss throughout, Ctrl+C leaves supplicant settings restored and Wi-Fi
  untouched (byte-for-byte compare of `WFDIEs`/GOIntent before/after).
- **Regression**: Ctrl+C cleanup, scrcpy reconnect on phone re-initiation, display picker,
  `--fullscreen`, log file.

## 10. Migration plan

1. Land `doctor` + `link/` + `wfd/` with the RTSP and helper internals behind
   `--transport wfd|miraclecast` (temp flag; miraclecast path untouched, default stays
   `miraclecast`).
2. Hardware validation with real phones on ≥2 chipset families.
3. Flip the default to `wfd`; keep the flag one release.
4. Remove miraclecast code path, `--network-mode` semantics and its sudo-heavy network
   takeover from the default UX (takeover later re-appears as an explicit phase-2 mode).

## 11. Decisions

| # | decision | owner |
|---|---|---|
| D1 | Replace miraclecast; keep Wi-Fi internet during DeX | user |
| D2 | Portable (Debian/Ubuntu/Arch/Fedora first), well-architected | user |
| D3 | One small privileged helper, single sudo prompt; no polkit in v1 | builder call |
| D4 | Drive the system supplicant directly via D-Bus; NM untouched | builder call |
| D5 | Phone = GO, laptop = P2P client (`go_intent=0`) | builder call (evidence: F1) |
| D6 | v1 = client role only; GO role aborts with a clear error | builder call |
| D7 | In-project RTSP client; RTP drained/discarded | builder call (evidence: F2) |
| D8 | `dbus-next` as the only new runtime dependency (ctrl_iface as fallback) | builder call |
| D9 | v1 fails fast on unsupported hardware; takeover mode = phase 2 | builder call |
| D10 | Scope: Samsung DeX only; no UIBC/HDCP/EDID/generic sink | builder call |
| D11 | Temporary `--transport` flag until hardware parity, then miraclecast removal | builder call |
| D12 | Set supplicant `DeviceName` to "DexOnLinux" while running (saved/restored like the other values) | builder call |
| D13 | Persist originals in a root-owned state file to self-heal stale supplicant state after crashes | builder call |
| D14 | Retry policy: clean disconnects → wait indefinitely; only failures feed a 3-consecutive counter | builder call |

## 12. Open questions & spikes (before or during implementation)

- **S1** WFD Device-Info bitmap variant Samsung accepts (0x0011 vs 0x0091 vs dual-role).
  *0x0011 (primary sink + session available) is accepted for discovery on the S24; see §14.*
- **S2** NM coexistence in practice: NM tolerates direct supplicant P2P ops + `p2p-*`
  group interface while STA stays connected (watch NM logs; verify STA quality/latency).
- **S3** Phone IP discovery: DHCP lease vs `GroupStarted` addresses vs static fallback.
- **S4** Invitation-flow specifics (persistent group join semantics on One UI 2026).
  *The S24 used a fresh KEYPAD/display-PIN connect, not an invitation; stale saved-device
  behavior remains to handle; see §14.*
- **S5** If `dbus-next` is a problem: ctrl_iface-only implementation feasibility.
- **S6** Volunteer hardware for mt792x/brcmfmac/ath/rtw89 verification.

## 13. What is left to the builder

Internal module structure, listen duty-cycle parameters, DHCP client retry/backoff details,
the exact video-formats string variants to attempt, logging format, test layout, and the
`--transport` flag plumbing. Everything that changes *what is built* is decided above.

## 14. Spike findings (2026-10-20, first live run)

The throwaway spike on branch `spike/wfd-sink` validated discovery and the pairing flow
against a Galaxy S24 (Android 16 / One UI 8) and an Intel AX201 on Ubuntu 24.04. It found
corrections that change the implementation brief:

1. **WFD must be explicitly enabled.** On the ctrl_iface path, `WFD_SUBELEM_SET all ...`
   stores the IEs but does not set `global->wifi_display`, so
   `wifi_display_update_wfd_ie()` discards them. Send `SET wifi_display 1` first. On the
   D-Bus path, setting the global `WFDIEs` property already enables WFD; no extra step.
   The helper must restore the previous `wifi_display` value on exit.
2. **DeX pairs with a display PIN, not PBC.** The phone sends a P2P provision-discovery
   request with the WPS display method; the sink must surface the generated PIN to the
   user immediately and call `P2P_CONNECT <mac> <pin> display go_intent=0` in the
   `P2P-PROV-DISC-SHOW-PIN` handler. Waiting for `P2P-GO-NEG-REQUEST` and calling PBC
   fails. This adds a **PIN UI/notification requirement** to the product: the session
   cannot be fully headless; the PIN must be shown within the WPS lifetime, and each
   attempt generates a new PIN.
3. **Continuous listen is required.** `P2P_EXT_LISTEN 200 1000` loses the single
   provision-discovery frame; use `P2P_EXT_LISTEN 1000 1000` (or an equivalent
   continuous listen) while waiting for the phone.
4. **Stale phone-side device state is a UX case.** After a failed PIN attempt the phone
   keeps "DexOnLinux — Connesso in precedenza"; "Avvia ora" may fail without sending a
   frame. The product must offer a retry path and document forgetting the saved device.

Impact on the design:

- The `linkd` helper protocol gains a `pin_required` event (pin + peer) and sends
  `P2P_CONNECT ... display` immediately; the runner/UI displays the PIN (desktop
  notification + terminal in the spike; the shipped app should use a modal dialog).
- The persisted state file must include the previous `wifi_display` and `device_name`
  values (D12/D13): the spike restores the former but cannot query `device_name` over
  ctrl_iface; the product should read it from `P2PDeviceConfig` (D-Bus) or the config.
- The doctor must check the WFD *runtime* behavior, not just the binary strings: a
  supplicant that accepts `WFD_SUBELEM_SET` but is not WFD-enabled is a silent failure.
- S1 is answered for discovery: Device Info `0x0011` (primary sink + session available)
  makes the S24 list the sink; `0x0091`/dual-role variants were not needed.
- Still open: DHCP, RTSP M1–M7 with the phone, internet coexistence, role=GO case, and
  invitation/persistent-group handling (S4).