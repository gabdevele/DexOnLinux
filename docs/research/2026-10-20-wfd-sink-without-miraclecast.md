# WFD Sink without miraclecast — feasibility research (2026-10-20)

Feasibility research for replacing miraclecast with a self-contained Wi-Fi Display (Miracast)
SINK built on the SYSTEM wpa_supplicant (D-Bus) + a small RTSP server, keeping the laptop's
Wi-Fi STA internet connection alive.

Repo: `DexOnLinux` (working dir `/home/gabdevele/Projects/DexOnLinux`).
Research date: 2026-10-20. Claims labelled `[CONFIRMED-source]`, `[INFERRED]`,
`[UNKNOWN-needs-test]` with source URL + version/date.

Already confirmed locally (not re-researched here):
- Intel AX201/iwlwifi: interface combinations allow managed(1) + P2P-GO/client(1) + P2P-device(1), up to 2 channels.
- Ubuntu wpa_supplicant 2.10 built WITH WFD support (strings: "WFD IE for Probe Response", "P2P: SD Request for WFD", "org.wi-fi.wfds", "wfd_subelems", D-Bus "WFDIEs").
- NM 1.46 P2P API is source-oriented (StartFind/StopFind + peers; connect via wifi-p2p profile with `peer` property).
- miraclecast spawns a PRIVATE wpa_supplicant + DHCP, which is why it kills NM/wpa_supplicant.

Primary-source method note: hostap/salsa/Arch/Fedora/GNOME web UIs are behind bot-protection
(Anubis/Salsa challenge). Everything below was fetched either via the raw content endpoints
(`sources.debian.org`, `git.launchpad.net`), or by cloning the actual git repos
(`git.w1.fi/hostap.git`, `gitlab.archlinux.org`, `src.fedoraproject.org`, `gitlab.gnome.org`,
`github.com`), or via `android.googlesource.com`. Kernel file references are `torvalds/linux`
master fetched 2026-10-20 (tested against v6.8 where noted).

---

## Verdict

**FEASIBLE — yes, with caveats.** `[CONFIRMED-source + INFERRED]`

- Every distro checked (Debian, Ubuntu, Arch, Fedora) builds wpa_supplicant with both
  `CONFIG_P2P` and `CONFIG_WIFI_DISPLAY`; the user's Ubuntu 24.04 binary (2.10) is verified
  to contain the WFD code paths. No distro patch disables them.
- wpa_supplicant can advertise WFD sink capability purely at runtime (ctrl_iface
  `WFD_SUBELEM_SET`, or D-Bus global `WFDIEs` property) and will auto-accept P2P GO
  negotiation; GO intent is settable at runtime (`P2PDeviceConfig.GOIntent` / `set p2p_go_intent`).
  It will also auto-answer WFD P2P service-discovery requests when WFD is enabled.
- What wpa_supplicant does *not* do: DHCP, RTSP, WFD session layers. Those stay in the app
  (small: one RTSP client state machine, no media handling required).
- The DeX/Android source is the P2P GO and the RTSP server; the laptop sink only needs to
  join the group (or be GO) and open one TCP connection to `<phone>:7236`. miraclecast's
  proven role defaults (GO intent 0 → phone is GO) can be reproduced exactly.
- Keeping STA internet alive is a driver-concurrency question, not a wpa_supplicant question:
  iwlwifi is fine (locally confirmed), ath11k/ath12k/rtw89 are fine per source, mt792x is
  firmware-feature dependent (`MT792x_FW_CAP_CNM`), rtw88 has **no P2P at all**, brcmfmac is
  firmware-feature dependent.

Caveats: the P2P group formation path must keep working under NetworkManager ownership
(NM never kills wpa_supplicant, but NM's wifi-p2p API is what it is); Samsung One UI behavior
is only proven against miraclecast today, so interop testing is mandatory; and no existing
maintained Linux WFD *sink* library can be borrowed — the RTSP sink has to be written
(the AOSP and miraclecast code give a full, small spec).

---

## 1. Distro build flags for wpa_supplicant

**Upstream default (defconfig)** — `[CONFIRMED-source]`
`wpa_supplicant/defconfig` in hostap git (https://git.w1.fi/hostap.git) has **both options
enabled, uncommented**, in every currently relevant tag:

| tag | tag date | `CONFIG_P2P=y` | `CONFIG_WIFI_DISPLAY=y` |
|---|---|---|---|
| hostap_2_10 | 2022-01-16 | line 506 | line 514 |
| hostap_2_11 | 2024-07-20 | line 544 | line 552 |
| hostap_2_12 | 2026-08-07 (commit 831364bf02710ad09c2f27d3efa92abeeb5634c0) | line 540 | line 548 |

(defconfig text: "P2P (Wi-Fi Direct) … CONFIG_P2P=y" and "Wi-Fi Display … CONFIG_WIFI_DISPLAY=y".)

**Debian** — `[CONFIRMED-source]`
- Package `wpa`; current: `2:2.11-2` (experimental), `2:2.10-25` (sid/forky), `2:2.10-24` (trixie)
  (https://sources.debian.org/api/src/wpa/, fetched 2026-10-20).
- `debian/rules` copies `debian/config/wpasupplicant/$(DEB_HOST_ARCH_OS)` verbatim to
  `wpa_supplicant/.config` before building
  (https://sources.debian.org/data/main/w/wpa/2%3A2.11-2/debian/rules, lines 27, 54).
- That config file contains `CONFIG_P2P=y` and `CONFIG_WIFI_DISPLAY=y`:
  - 2.11-2: https://sources.debian.org/data/main/w/wpa/2%3A2.11-2/debian/config/wpasupplicant/linux lines 545, 553
  - 2.10-25: https://sources.debian.org/data/main/w/wpa/2%3A2.10-25/debian/config/wpasupplicant/linux lines 506, 514
- Also enables D-Bus: `CONFIG_CTRL_IFACE_DBUS_NEW=y`, `CONFIG_CTRL_IFACE_DBUS_INTRO=y`.

**Ubuntu** — `[CONFIRMED-source + local evidence]`
- Ubuntu carries the Debian package; launchpad git tip is `2:2.11-2ubuntu2` (changelog dated
  2026-07-23, "Fix FTBFS due to Openssl 4.0 transition")
  (https://git.launchpad.net/ubuntu/+source/wpa/plain/debian/changelog).
- Same `debian/config/wpasupplicant/linux` with `CONFIG_P2P=y` (line 545) and
  `CONFIG_WIFI_DISPLAY=y` (line 553)
  (https://git.launchpad.net/ubuntu/+source/wpa/plain/debian/config/wpasupplicant/linux).
- Ubuntu 24.04 ships wpa_supplicant **v2.10**. Verified on this very machine
  (2026-10-20): `/usr/sbin/wpa_supplicant` contains the strings `WFD_SUBELEM_SET`,
  `WFD: Update WFD IE`, `WFD IE for Probe Response`, `P2P: SD Request for WFD`,
  `org.wi-fi.wfds`, `wfd_subelems`, `WFDIEs` → WFD code compiled in. `[CONFIRMED-source]`

**Arch** — `[CONFIRMED-source]`
- `gitlab.archlinux.org/archlinux/packaging/packages/wpa_supplicant` PKGBUILD (cloned 2026-10-20),
  `pkgver=2.12`, source `git+https://git.w1.fi/hostap.git#tag=hostap_2_12` (signed tag).
- Arch uses its own `wpa_supplicant_config` file copied to `wpa_supplicant/.config`
  (PKGBUILD `prepare()`), which has `CONFIG_P2P=y` (line 544) and `CONFIG_WIFI_DISPLAY=y` (line 552),
  plus `CONFIG_CTRL_IFACE_DBUS_NEW=y`.

**Fedora** — `[CONFIRMED-source (spec+upstream defconfig), no runtime check]`
- `src.fedoraproject.org/rpms/wpa_supplicant` (cloned 2026-10-20): rawhide spec `Version: 2.11`,
  `Release: 13%{?dist}`, last commit 2026-07-17 (Fedora 45 mass rebuild).
- The spec builds from **upstream defconfig**: `cp defconfig .config` (line 111) and applies
  Fedora patches. None of the Fedora defconfig patches touch `CONFIG_P2P`/`CONFIG_WIFI_DISPLAY`
  (checked every patch in the repo). Since upstream 2.11 defconfig already sets both `=y`,
  Fedora ships P2P+WFD. `[INFERRED from spec + upstream defconfig]`

**How a user can VERIFY at runtime** — `[CONFIRMED-source + commands]`
```bash
# 1. Binary compiled with WFD/P2P support (works in any distro)
strings -a "$(command -v wpa_supplicant)" | grep -E 'WFD_SUBELEM_SET|WFD IE for Probe Response|WFDIEs|P2P: SD Request for WFD'
# 2. Global capability list over system D-Bus (needs root; expect 'p2p' in the list)
sudo busctl get-property fi.w1.wpa_supplicant1 /fi/w1/wpa_supplicant1 \
     fi.w1.wpa_supplicant1 Capabilities
# 3. WFD IEs property exists on the global object (needs root)
sudo busctl introspect fi.w1.wpa_supplicant1 /fi/w1/wpa_supplicant1 | grep -i wfd
# 4. ctrl_iface subelements (interface name shown by 'iw dev'; p2p-dev-* exists on Ubuntu)
sudo wpa_cli -i p2p-dev-wlp0s20f3 WFD_SUBELEM_GET all
# 5. Driver concurrency actually advertised
iw list | sed -n '/valid interface combinations/,/^$/p'
```
Note: D-Bus property reads are access-controlled (this machine returned `Access denied` for the
global `Capabilities` read as a normal user; root is required).

---

## 2. wpa_supplicant as WFD sink

All line references: hostap git tag `hostap_2_12` (2026-08-07) unless noted; source obtained via
`git clone --branch hostap_2_12 https://git.w1.fi/hostap.git`.

### 2a. `wfd_subelems`: where it lives and which frames get WFD IEs

- `wfd_subelems` is **not** a wpa_supplicant.conf option in 2.12 (no such config item exists in
  `wpa_supplicant/config.c`). The string `wfd_subelems=` appears only in the *peer* hexdump
  produced by `p2p_peer` output (`src/p2p/p2p.c:4643`, e.g. `wfd_subelems=...` for a discovered
  device). `[CONFIRMED-source]`
- Local WFD IEs are configured **at runtime**, two equivalent ways:
  - ctrl_iface: `WFD_SUBELEM_SET all <hex>` / `WFD_SUBELEM_SET <id> <hex>` and
    `WFD_SUBELEM_GET all|<id>` (`wpa_supplicant/ctrl_iface.c:13900-13906`) — only compiled with
    `CONFIG_WIFI_DISPLAY`. `[CONFIRMED-source]`
  - D-Bus: global property **`WFDIEs` (ay, read/write) on `fi.w1.wpa_supplicant1`, object
    `/fi/w1/wpa_supplicant1`** (`wpa_supplicant/dbus/dbus_new.c:2764`; getter/setter
    `wpas_dbus_getter_global_wfd_ies` / `wpas_dbus_setter_global_wfd_ies` in
    `wpa_supplicant/dbus/dbus_new_handlers_p2p.c:3120-3185`). Setting an empty array disables WFD
    and clears subelements. This is a **global** property (not on P2PDevice). `[CONFIRMED-source]`
- The byte array is the **concatenated WFD subelement list** (`[id][len u16 BE][value]`), parsed by
  `wifi_display_subelem_set_from_ies()` (`wpa_supplicant/wifi_display.c:295-345`); the supplicant
  does not synthesize content from user-friendly config — the app builds the bytes.
- Which frames carry the WFD IE (and which subelements) is hard-coded in
  `wifi_display_update_wfd_ie()`, `wpa_supplicant/wifi_display.c:67-243`; the frame→subelement
  matrix is documented in that file's comment:
  - Beacon, (Re)Association Request, GO-Neg Req/Resp/Conf, Provision-Discovery Request:
    Device Info [+ Associated BSSID] [+ Coupled Sink Info]
  - Probe Request: Device Info [+ Associated BSSID] [+ Coupled Sink Info] + Extended Capability
  - Probe Response: Device Info [+ Associated BSSID] [+ Coupled Sink Info] + Extended Capability + Session Info
  - (Re)Association Response, P2P Invitation Req/Resp, Provision-Discovery Resp: Device Info [+ …] + Session Info
  The subelement IDs are in `src/common/ieee802_11_defs.h:1959-1969`
  (`WFD_SUBELEM_DEVICE_INFO=0`, `…ASSOCIATED_BSSID=1`, `…COUPLED_SINK=6`, `…EXT_CAPAB=7`,
  `…SESSION_INFO=9`, `…R2_DEVICE_INFO=11`). `[CONFIRMED-source]`

### 2b. Device Info encoding for a sink

`[CONFIRMED-source]` Android's framework decoder/encoder defines the exact bitmap
(`packages/modules/Wifi/framework/java/android/net/wifi/p2p/WifiP2pWfdInfo.java`, main branch,
fetched 2026-10-20):

- bytes 0-1: Device Information bitmap
  - bits 0-1: Device Type — `0`=WFD Source, `1`=WFD Primary Sink, `2`=WFD Secondary Sink,
    `3`=WFD Source or Primary Sink (dual role)
  - bit 2: Coupled Sink Support at Source; bit 3: Coupled Sink Support at Sink
  - bits 4-5: Session Availability (`01b` = available for session; mask `0x30`)
  - bit 6: WFD Service Discovery support; bit 7: Preferred Connectivity
- bytes 2-3: session management control port (u16 BE; e.g. `0x1c44` = 7236)
- bytes 4-5: maximum throughput (u16 BE, in units of 0.5 Mbps)
- Android formats it as `"%04x%04x%04x"` (device info, ctrl port, throughput).

Worked example — GNOME Network Displays ships a static "WFD source" IE blob `00 00 06 00 90 1c 44 00 c8`
in `src/nd-wfd-p2p-sink.c` (line ~555): subelement 0, length 6, device info `0x0090`
(type 0 source, session-available `01b`, preferred-connectivity), ctrl port `0x1c44` = 7236,
throughput `0x00c8` = 200×0.5 = 100 Mbps. A Linux **sink** blob would set bits 0-1 = `01`
(primary sink; consider `11` dual-role so the phone also lists it as a possible source), bit 4
session available, optionally bit 3/6. The Device-Info subelement must be the full
`id=0, len=6, 6 bytes` TLV inside the `WFDIEs`/`WFD_SUBELEM_SET all` payload, e.g.
`0000 0600 900? 1c44 00c8` (device info word chosen for a sink). `[INFERRED from constants]`

### 2c. D-Bus P2P API relevant to a sink

`[CONFIRMED-source]`, `wpa_supplicant/dbus/dbus_new.c` (property/method/signal tables) and
`wpa_supplicant/dbus/dbus_new_handlers_p2p.c`:

- P2PDevice interface (`fi.w1.wpa_supplicant1.P2PDevice`) **properties**:
  `P2PDeviceConfig` (a{sv}, read/write; keys include `GOIntent` u32 ≤15, `PersistentReconnect`,
  `ListenRegClass/ListenChannel/OperRegClass/OperChannel`, `SsidPostfix`, `IntraBss`, `GroupIdle`,
  WPS vendor extensions — setter at `dbus_new_handlers_p2p.c:1173-1180`, getter at ~1050),
  `DeviceAddress`, `Peers` (ao), `Role` (s), `Group` (o), `PeerGO` (o), `PersistentGroups` (ao).
- **methods**: `Find`, `StopFind`, `Listen`, `ExtendedListen`, `PresenceRequest`,
  `ProvisionDiscoveryRequest`, `Connect`, **`GroupAdd`** (arg dict: `peer`, `join`, `persistent`,
  `auto_join`, `authorize_only`, `frequency`, **`go_intent`** 0-15, `wps_method`, `pin` —
  `dbus_new_handlers_p2p.c:620-720`), `Cancel`, `Invite`, `Disconnect`, `RejectPeer`,
  `RemoveClient`, `Flush`, `AddService`, `DeleteService`, `FlushService`,
  `ServiceDiscoveryRequest/Response/CancelRequest`, `ServiceUpdate`,
  `ServiceDiscoveryExternal`, `AddPersistentGroup`, `RemovePersistentGroup`,
  `RemoveAllPersistentGroups`.
- **signals**: `GONegotiationRequest` (peer path, dev_passwd_id, device_go_intent — informational),
  `GONegotiationSuccess/Failure`, **`GroupStarted`** (dict: `interface_object`, `role` = "GO"/"client",
  `persistent`, `group_object`, and when the supplicant assigned IPs: `IpAddr`, `IpAddrMask`,
  `IpAddrGo` byte arrays — `dbus_new.c:1665-1705`), `GroupFinished`, `GroupFormationFailure`,
  `InvitationReceived`, `InvitationResult`, `ProvisionDiscoveryPBCRequest/Response`,
  `ProvisionDiscoveryEnterPin/DisplayPin/ShowPin`, `ProvisionDiscoveryFailure`,
  `ServiceDiscoveryResponse`, `WpsFailed`, `WpsAPSuccess`… (`doc/dbus.doxygen` documents all).

### 2d. Can wpa_supplicant (a) advertise sink capability and (b) auto-accept GO negotiation as GO?

(a) **Yes.** Set `WFDIEs` (D-Bus) or `WFD_SUBELEM_SET all <hex>` (ctrl_iface) with a Device-Info
subelement encoding Primary Sink (see 2b). Setting a non-empty value also enables Wi-Fi Display
in the supplicant (`global->wifi_display`), which turns on the automatic WFD SD responder (below).
`[CONFIRMED-source]`

(b) **Yes.** GO intent is a normal config item `p2p_go_intent` (`INT_RANGE(p2p_go_intent, 0, 15)`,
`wpa_supplicant/config.c:5872`; default `DEFAULT_P2P_GO_INTENT 7`, `wpa_supplicant/config.h:28`).
It is settable at runtime via:
- D-Bus: `P2PDeviceConfig` set with `GOIntent` (`dbus_new_handlers_p2p.c:1177-1180`), or as the
  `go_intent` arg of `GroupAdd`/`Connect` dict;
- ctrl_iface: `set p2p_go_intent <0..15>` (documented in `wpa_supplicant/README-P2P:722`).
GO negotiation itself is executed inside wpa_supplicant; `GONegotiationRequest` is informational
and the app only needs to act if it wants to reject (`RejectPeer`). `[CONFIRMED-source]`
[INFERRED]: the value used by miraclecast for the DeX sink is 0 (client) — `wpa_supplicant`
config `p2p_go_intent=0` reproduces that; 15 would make the laptop GO (then the laptop must run
DHCP; miraclecast's own issue tracker shows DHCP is the weak point in that mode, #529).

WFD **service discovery**: when `global->wifi_display` is on, the supplicant automatically answers
incoming WFD SD requests (Service Protocol Type `P2P_SERV_WIFI_DISPLAY`) without app involvement:
`wpas_sd_req_wfd()` in `wpa_supplicant/p2p_supplicant_sd.c:477-520` (`"P2P: SD Request for WFD"`).
Outbound WFD SD requests are available as `P2P_SD_REQUEST <addr> wifi-display [source]|[pri-sink]|
[sec-sink]|[source+sink] <subelems>` (`wpa_supplicant/ctrl_iface.c:6794-6797` →
`wpas_p2p_sd_request_wifi_display()`, `wpa_supplicant/p2p_supplicant_sd.c` ~line 1055). `[CONFIRMED-source]`

> **Erratum (2026-10-20, builder review):** the "auto-accepts GO negotiation" claim in (b) is
> **wrong for an unknown peer**: `p2p_process_go_neg_req` (2.10/2.12) answers
> `Fail: information currently unavailable` when `wps_method == WPS_NOT_READY` and only notifies
> the app via `GONegotiationRequest`. The application must react by initiating the client
> connect itself (`P2P_CONNECT <mac> pbc`, go_intent 0), as miraclecast's
> `ctl_fn_peer_go_neg_request` does (60 s timeout). GO-intent configuration is otherwise as
> described.

### 2e. Session Info / RSSI updates — supplicant or app?

- **Session Info**: when the supplicant is GO, it **automatically builds** the WFD Session Info
  subelement for the *group* IEs (beacon/probe-response) listing group members and their device-info
  descriptors: `wifi_display_build_go_ie()` in `src/p2p/p2p_group.c:381-425`. Outside that, the
  sink's own session-info/ext-capab values are static pass-through set by the app. `[CONFIRMED-source]`
- **No RSSI-related WFD handling exists in wpa_supplicant** (no code path updates WFD subelements
  from radio measurements). Any dynamic WFD content (updated session info, UIBC, etc.) must be
  re-pushed by the app via `WFDIEs`/`WFD_SUBELEM_SET`. `[CONFIRMED-source (absence)]`
- DHCP and all RTSP/WFD session layers are app-side by design (the WFD code's own comment:
  "using an external program to control the additional information exchanges in the messages",
  `wpa_supplicant/defconfig` WFD section). `[CONFIRMED-source]`

---

## 3. Driver concurrency evidence (STA + P2P-GO on one radio)

Kernel source: `torvalds/linux` master, files fetched 2026-10-20 (exact paths in table). iwlwifi
checked against the local AX201 `iw list` on this machine.

| Driver / chips | STA + P2P-GO concurrent? | Evidence (file:line, master unless noted) |
|---|---|---|
| **iwlwifi / iwlwifi-mvm** (AX200/AX201/AX210/AX211, BE200) | **Yes** — `#{STA}≤1 + #{P2P-CL,P2P-GO,AP}≤1 + #{P2P-DEV}≤1, total 3`; 2 different channels for the non-AP combo, 1 channel when AP is in the set | `drivers/net/wireless/intel/iwlwifi/mvm/mac80211.c:34-71` (`IWL_MVM_LIMITS`, `iwl_mvm_iface_combinations`); matches local `iw list` on AX201 2026-10-20 |
| **MediaTek mt792x** (MT7921/MT7922 — common AMD laptops; shared by MT7925) | **Firmware-dependent.** If firmware reports `MT792x_FW_CAP_CNM`: MCC combo `STA/P2P-client ≤2 + P2P-GO ≤1 + P2P-DEVICE ≤1, 2 channels` and SCC combo. Without CNM: only `STA ≤4 + AP ≤1, 1 channel` — **no P2P types at all**, so adding P2P interfaces is refused (`cfg80211_check_combinations()` returns `-EBUSY`, `net/wireless/util.c:2680-2695`) | `drivers/net/wireless/mediatek/mt76/mt792x_core.c:10-160, 770-800` (master; identical logic in v6.8: `if_limits_chanctx`/`if_comb_chanctx` used when `MT792x_FW_CAP_CNM`, 2 channels, STA+P2P-client ≤2 / AP+P2P-GO ≤1) |
| **Qualcomm ath11k** (QCA6390, **WCN6855** = ThinkPad AMD Wi-Fi 6E, QCA2066) | **Yes** — `STA≤1 + #{AP,P2P-CL,P2P-GO}≤16 + P2P-DEV≤1`, 1 channel; chips with `support_dual_stations` get a second combo with **2 channels** (qca6390 hw2.0, wcn6855 hw2.0/hw2.1, qca2066, qca6698aq) | `drivers/net/wireless/ath/ath11k/mac.c:10298-10370`; hw flags in `ath11k/core.c` (`.support_dual_stations=true` at lines 304, 482, 572, 834, 923) |
| **Qualcomm ath12k** (WCN7850, QCN9274; not ath9k) | **Yes (per radio)** — build combo `STA≤1 + (AP|P2P-CL|P2P-GO)≤16 + P2P-DEV≤1`, 1 channel; a global combination sums limits across radios | `drivers/net/wireless/ath/ath12k/mac.c:14443-14560` |
| **Realtek rtw88** (RTL8821CE/8822CE/8723DE…) | **No P2P at all** — `interface_modes = STATION | AP | ADHOC` (no P2P_CLIENT/P2P_GO/P2P_DEVICE). STA+AP combo only exists for 8822C, 1 channel | `drivers/net/wireless/realtek/rtw88/main.c:107-127, 2286-2302` |
| **Realtek rtw89** (RTL8852AE/BE, 8922AE…) | **Yes** — `STA + {P2P-CL,P2P-GO,AP}`, 1 channel; plus **MCC combo** `STA + {P2P-CL,P2P-GO}, 2 channels` when the chip supports chanctx (`chip->support_chanctx_num > 1`, not disabled) | `drivers/net/wireless/realtek/rtw89/core.c:168-215, 7690-7697` |
| **Broadcom brcmfmac** (fullmac; most MacBooks, some laptops) | **Firmware-dependent.** Combinations are built from firmware feature bits P2P/RSDB/MCHAN/MBSS. With P2P+RSDB: `STA≤1 + P2P-DEV≤1 + {P2P-CL,P2P-GO}≤2 + AP≤2, 2 channels, total 4`. With P2P without RSDB: 1 channel (MCHAN relaxes for GO+STA) | `drivers/net/wireless/broadcom/brcm80211/brcmfmac/cfg80211.c:7490-7580+` (`brcmf_setup_ifmodes`, comment block above) |

Notes:
- "No combinations advertised" is not benign: `cfg80211_check_combinations()` fails with `-EBUSY`
  when no combination matches (`net/wireless/util.c`), which is exactly why rtw88's missing P2P
  types matter.
- Real-world validation for mt792x CNM and brcmfmac feature bits: **thin**. No mailing-list/bugzilla
  evidence was collected in this pass; per-device behaviour should be verified with
  `iw list` on the target machine and an actual STA+P2P-GO attempt.
  `[UNKNOWN-needs-test]`
- The iwlwifi result is the only one double-confirmed on real hardware in this study
  (local `iw list`, AX201).

---

## 4. Reference implementations

**gnome-network-displays (GND)** — `[CONFIRMED-source]`
Repo: https://gitlab.gnome.org/GNOME/gnome-network-displays (cloned 2026-10-20).
- Maintenance: latest release **0.99.0, 2026-01-23** (GitLab releases API); last commit
  `c305023` "Add Norwegian Nynorsk translation", **2026-10-10** → actively maintained.
- Role: it is a **display-source app** ("sink" in its code = the remote cast *target*). There is no
  Miracast *sink/receive* implementation here; but its P2P + WFD plumbing is the best available
  reference for the NetworkManager path.
- P2P backend: **NetworkManager**, not direct wpa_supplicant D-Bus.
  `src/nd-wfd-p2p-provider.c`: `NMDeviceWifiP2P`, `nm_device_wifi_p2p_start_find/stop_find`,
  `nm_wifi_p2p_peer_get_wfd_ies()` (peers with no WFD IEs are ignored — comment "It may also mean
  that wpa_supplicant is not compiled with [WFD]").
  `src/nd-wfd-p2p-sink.c`: builds an `NMConnection` with `NMSettingWifiP2P`
  (`NM_SETTING_WIFI_P2P_WFD_IES`, static blob), `ipv4.method=auto` + `never-default=TRUE`,
  `ipv6.method=auto may-fail`, and activates it via
  `nm_client_add_and_activate_connection2(..., peer_object_path, ...)` — i.e. NM performs group
  formation and DHCP on the P2P interface.
- Unprivileged: yes. Flatpak `finish-args` = `--share=network`, `--system-talk-name` for
  `org.freedesktop.NetworkManager`, Avahi, FirewallD1 — no root; it runs as a normal user app.
- WFD negotiation code: `src/wfd/` — `wfd-server.c` is a `GstRtspServer` subclass with
  `gst_rtsp_server_set_service(..., "7236")` (the WFD RTSP server = source role);
  `wfd-client.c` implements the message state machine (`INIT_STATE_M1_SOURCE_QUERY_OPTIONS`,
  `M2_SINK_QUERY_OPTIONS`, …), sends `SET_PARAMETER` with `wfd_video_formats`,
  `wfd_audio_codecs`, `wfd_presentation_URL`, `wfd_client_rtp_ports`, and does keepalive
  (`wfd_client_timeout_session_filter_func` sends `GET_PARAMETER`, plus a "keep-alive" source;
  NEWS 0.99.0: "wfd: Fix keep-alive and RTCP port handling for Roku/Hisense sinks").
  For a **sink** on Linux, the code that must be written is the mirror image of `wfd-client.c`,
  which can be studied nearly verbatim (same M-flow), but replaced by plain RTSP handling.
- NetworkManager API surface relevant here: `NMSettingWifiP2P` properties are only
  `peer`, `wps-method`, `wfd-ies`, `wps-pin`, `wps-pin-flags`
  (https://gitlab.freedesktop.org/NetworkManager/NetworkManager/-/raw/main/src/libnm-core-impl/nm-setting-wifi-p2p.c,
  main branch, fetched 2026-10-20) — **no GO-intent property**, so GO intent must come from
  wpa_supplicant's config/ctrl_iface/D-Bus when using NM.

**MiracleCast** (albfan fork) — `[CONFIRMED-source]`
Repo: https://github.com/albfan/miraclecast (cloned 2026-10-20).
- Maintenance: last commit **2026-03-10** ("src/dhcp: Avoid reading invalid data in
  dhcp_get_option"), 4348 stars, 94 open issues, no GitHub releases, not archived → it is
  *slowly but still maintained*; the only maintained Linux Miracast **sink** found.
- Why it breaks NM: it starts its own wpa_supplicant + DHCP and takes over the adapter (project
  premise; confirmed by its architecture: `src/wifi/wifid-supplicant.c`, `src/dhcp/`).
- Sink implementation details used below in §5: `src/ctl/ctl-sink.c`, `src/ctl/sinkctl.c`.
- Defaults: `go_intent = 0` (`src/wifi/wifid.c:48`, settable `-g/--go-intent`, config `go-intent`)
  → the phone becomes GO; sink as GC (`[CONFIRMED-source]`, consistent with issue #529's
  maintainer comment that DHCP misbehaves when miracle-sink is GO).

**Other candidates** — `[INFERRED-thin]`
A web search for maintained Windows/Linux Miracast sinks (2025/2026) surfaced only miraclecast;
GNOME Network Displays is source-only; AOSP's `WifiDisplaySink.cpp` (see §5) is the reference
Android sink but was removed from AOSP long ago (exists at `android-4.2.2_r1.2`; by
`android-9.0.0_r61` the whole `wifi-display/` dir is gone from `frameworks/av`, and even
`android-7.1.2_r36` has only `source/`). No other maintained sink implementation was found.

---

## 5. RTSP handshake & Samsung specifics

Sources: AOSP `frameworks/av/media/libstagefright/wifi-display/` at
`android-4.2.2_r1.2` (sink: `sink/WifiDisplaySink.cpp`, 644 lines — the only AOSP sink) and
`android-7.1.2_r36` (source: `source/WifiDisplaySource.cpp`, 1739 lines) — both fetched
2026-10-20; miraclecast master `src/ctl/ctl-sink.c` (fetched 2026-10-20).

### 5a. Topology (who listens, who connects)

- The **Source (phone) listens on TCP 7236** (`WifiDisplaySource.cpp:149` `createRTSPServer`,
  `kWifiDisplayDefaultPort = 7236`, `source/WifiDisplaySource.h:40`).
- The **Sink (laptop) is the TCP client**: AOSP sink `createRTSPClient` to
  `mRTSPHost:mRTSPPort` (`sink/WifiDisplaySink.cpp:141`); miraclecast `ctl_sink_connect()` does
  `addr.sin_port = htons(7236)` (`src/ctl/ctl-sink.c:588`).
- Over that single TCP connection **both directions send RTSP requests**: the source sends
  M1/M3/M5/keepalive; the sink sends M2/SETUP/PLAY. (A sink that only listens would never see the
  phone; a sink that never answers the phone's requests dies after 30 s — see 5d.) `[CONFIRMED-source]`

### 5b. Message flow a sink must implement (AOSP source ↔ sink function references)

1. **M1** Source→Sink: `OPTIONS * RTSP/1.0` + `Require: org.wfa.wfd1.0`
   (`sendM1`, `WifiDisplaySource.cpp:513-534`).
2. Sink→Source: `200 OK`, header `Public: org.wfa.wfd1.0, GET_PARAMETER, SET_PARAMETER`
   (`WifiDisplaySink.cpp:456-465`; miraclecast `ctl-sink.c:36-55`).
3. **M2** Sink→Source: its own `OPTIONS *` + `Require: org.wfa.wfd1.0`
   (`WifiDisplaySink.cpp:243-264`; miraclecast same). Source replies `200`.
4. **M3** Source→Sink: `GET_PARAMETER rtsp://localhost/wfd1.0` with body listing
   `wfd_content_protection`, `wfd_video_formats`, `wfd_audio_codecs`, `wfd_client_rtp_ports`
   (`sendM3`, `WifiDisplaySource.cpp:536-565`).
5. **M4** Sink→Source: `200 OK` + `Content-Type: text/parameters` with the sink's capabilities.
   This is the message the sink must get right (see 5c). The AOSP 4.2 sample sink answers with
   literal placeholders (`WifiDisplaySink.cpp:470-500`); miraclecast answers with real values.
6. **M4b** Source→Sink: `SET_PARAMETER rtsp://localhost/wfd1.0` with the *chosen*
   `wfd_video_formats`, `wfd_audio_codecs` (AOSP picks `LPCM 00000002 00` when
   `mUsingPCMAudio`, else `AAC 00000001 00`), `wfd_presentation_URL: rtsp://<source-ip>/wfd1.0/streamid=0 none`,
   and `wfd_client_rtp_ports` (`sendM4`, `WifiDisplaySource.cpp:566-638`).
7. **M5** Source→Sink: `SET_PARAMETER` + `wfd_trigger_method: SETUP`
   (`sendTrigger`, `WifiDisplaySource.cpp:640-685`; sent after M4b is acked, `:885`).
   Sink answers `200 OK` **first**, then: (`ctl-sink.c:330-361`, `sink_handle_set_parameter`)
8. **M6** Sink→Source: `SETUP <wfd_presentation_URL> RTSP/1.0` +
   `Transport: RTP/AVP/UDP;unicast;client_port=<rtp-port>` (`WifiDisplaySink.cpp:530-560`;
   miraclecast uses `client_port=7236` — note: **no second (RTCP) port**, `ctl-sink.c:346`).
   Source replies `200` + `Session: <id>;timeout=30` (`WifiDisplaySource.cpp:1459`,
   `kPlaybackSessionTimeoutSecs = 30`, `source/WifiDisplaySource.h:113`).
9. **M7** Sink→Source: `PLAY <url>` + `Session: <id>`
   (`WifiDisplaySink.cpp:563-585`, sent straight after the SETUP response; miraclecast
   `sink_setup_fn` `ctl-sink.c:181-210` does the same). Source replies `200` and the RTP stream
   starts flowing to the sink's UDP port. The source may also send its own multicast-ish
   `wfd_trigger_method: PLAY`, which is harmless. `[CONFIRMED-source]`

### 5c. Which `wfd_*` parameters matter (values that are known to work with Samsung)

From miraclecast's working sink (`ctl-sink.c:85-160`) and its defaults (`sinkctl.c:82-84`):

```
wfd_content_protection: none                       # skip HDCP entirely
wfd_video_formats: 00 00 03 10 <CEA> <VESA> <HH> 00 0000 0000 10 none none
     defaults: CEA=0x0001ffff, VESA=0x1fffffff, HH=0x00001fff   # "everything"
     AOSP byte layout comment: native(1) pref-display-mode(1),
       then per AVC codec: profile(1) level(1) CEA-mask(4) VESA(4) HH(4)
       latency(1) min-slice(2) slice-enc(2) framerate-ctl(1) max-hres max-vres
wfd_audio_codecs: AAC 00000007 00                  # LPCM optional, see below
wfd_client_rtp_ports: RTP/AVP/UDP;unicast <sink-udp-port> 0 mode=play
     # "0" = no second RTP port; SETUP's client_port=<port> without "-<rtcp>"
     #    → AOSP source sets clientRtcp = -1 ("No RTCP", WifiDisplaySource.cpp:1088,1112)
```
- HDCP/`wfd_content_protection`: DeX works with `none`; the AOSP source only inits HDCP if the
  sink advertises it. `[CONFIRMED-source]`
- LPCM: AOSP source can stream LPCM (`LPCM 00000002 00`, 2ch 48 kHz) if the *sink* lists it in M4
  (the source picks PCM when `mUsingPCMAudio`; `sendM4` reads mUsingPCMAudio from its own setting).
  miraclecast's default M4 does **not** include LPCM (AAC only) and DeX connects anyway;
  the community extension file `res/sinkctl.protocol-extension.example` adds
  `LPCM 00000003 00, AAC 0000000f 00, AC3 00000007 00` for other devices. So LPCM is *not required*
  for Samsung DeX. `[CONFIRMED-source + practice]`
- `wfd_display_edid`: optional; only settable through miraclecast's protocol-extension mechanism
  (example file has a full EDID blob + `wfd_connector_type=05`). Default miraclecast (no EDID)
  works with DeX → the sink may omit it. `[INFERRED]`
- `wfd_coupled_sink`: encoded in Device Info/Coupled-Sink subelements (section 2b) and/or the
  RTSP capability list; not needed for a plain DeX sink; keep "none"/absent. `[INFERRED]`
- `wfd_uibc_capability`: optional; miraclecast supports it (`--uibc`, advertisement includes
  `input_category_list=GENERIC;generic_cap_list=Mouse,SingleTouch;hidc_cap_list=none;port=none`), but
  issue #482 shows UIBC+gst pipeline problems with S22 DeX. Not needed for the DexOnLinux design
  (input comes via scrcpy/adb). `[CONFIRMED-source]`
- `wfd_trigger_method`: SETUP/PLAY/PAUSE/TEARDOWN handling (see M5); TEARDOWN must end the session.
  `[CONFIRMED-source]`

### 5d. RTSP keepalive / RTCP RR — why DexOnLinux's "discard the stream" works

`[CONFIRMED-source for AOSP; INFERRED for Samsung One UI]`

- The source enforces liveness on **RTSP control traffic only**:
  `kPlaybackSessionTimeoutSecs = 30`, reaper every `kReaperIntervalUs = 1 s`
  (`source/WifiDisplaySource.h:105,113`); the reaper destroys the session if
  `getLastLifesignUs() + 30s < now` (`WifiDisplaySource.cpp:339-357`).
- `updateLiveness()` is called **only** on: the M16 response (`:909`), an incoming
  `GET_PARAMETER` with Session (`:1029`), and incoming `SET_PARAMETER` with Session
  (`:1401`, `:1428`). RTCP is not consulted anywhere.
- The source itself drives the keepalive: it sends **M16 `GET_PARAMETER` with `Session:` every
  25 s** (`scheduleKeepAlive`, `:926-933` = 30 s − 5 s; `sendM16`, `:686+`) and only requires a
  `200` response. RTCP RR from the sink is never expected — AOSP even has an explicit
  "No RTCP" path when the client's `client_port` has a single port (`:1088`, `:1112`).
- DexOnLinux today runs `miracle-sinkctl --external-player true --port <udp> --audio 1`
  (`src/dexonlinux/commands.py` `start_miracle_sinkctl`) and binds that UDP port in
  `src/dexonlinux/stream_handler.py` `_drain_stream()` where packets are read and dropped.
  The session survives because miraclecast's RTSP layer keeps answering the phone's requests
  (OPTIONS/GET_PARAMETER/SET_PARAMETER + M16 keepalive) while the media path is ignored.
  → **The session's lifetime is bound to the RTSP control connection (+ keepalive), not to the
  RTP/RTCP data path. No RTCP receiver reports are needed; the RTP stream can be discarded (or
  not received at all).** This is why the current architecture works, and a minimal replacement
  sink must replicate exactly this: answer M16, answer triggers, optionally answer TEARDOWN.

### 5e. Samsung-specific notes (tell Samsung apart from generic WFD)

From miraclecast's issue tracker (GitHub API, fetched 2026-10-20):
- **#529 "Wi-Fi Direct connection Fail" (opened 2025-01-21)** — with a Samsung phone, plain
  Wi-Fi Direct connection stops at `P2P-GO-NEG-REQUEST`/`connect ... pbc` unless SmartView/DeX has
  been used once; user set `p2p_go_intent=0`. Maintainer: "there's a problem with miraclecast where
  miracle-dhcp cannot create correctly the connection when miracle-sink is the group owner".
  → Keep the **phone as GO** (sink as client), which is miraclecast's default and the proven path.
- **#407 "About P2P-INVITATION_RECEIVED handling" (2021-03-22)** — some clients (Windows; and a
  DeX user on **2025-06-13**) send a **P2P invitation to a persistent group instead of GO
  negotiation**; then nothing happens on the sink side. Workarounds in-thread: join via
  `p2p_connect <MAC> pbc persistent join` (ValdikSS, 2023-10-01), or on the phone "Reset Wi-Fi and
  Bluetooth settings". → The new sink must handle `InvitationReceived` (or use a persistent
  group / auto-join) as well as GO negotiation. `[CONFIRMED-source (issue reports)]`
- **#482 (2023)** — DeX from Galaxy S22 works with `miracle-sinkctl`, but `--uibc` breaks the
  video path. UIBC is optional; skip it for v1. `[CONFIRMED-source (issue report)]`
- No Samsung/DeX-specific WFD parameter requirements were found in the trackers beyond the generic
  working defaults of §5c (CEA all-1080p, AAC, no HDCP, no EDID). Samsung's Smart View/DeX peer
  also announces itself with standard WFD device info; GNOME Network Displays' issues about
  Samsung are TV-side (cast *to* Samsung TVs), not DeX sink-side. `[INFERRED]`

---

## Open questions

1. **Samsung One UI behavior drift** — DeX interop evidence is through 2025 (miraclecast #529/#407);
   today's One UI (2026) behaviour with a non-Samsung sink is `[UNKNOWN-needs-test]`.
2. **mt792x firmware CNM bit** — does the fleet's MT7921/MT7922 firmware set `MT792x_FW_CAP_CNM`
   (P2P combos) or not? Must be checked per device with `iw list`. `[UNKNOWN-needs-test]`
3. **brcmfmac firmware feature bits** (P2P + RSDB/MCHAN) — per-device check with `iw list`.
   `[UNKNOWN-needs-test]`
4. **NM-owned P2P + STA concurrency in practice** — NM will use wpa_supplicant's P2P device while
   managing the STA interface; verify on hardware that `StartFind`/P2P connection does not disturb
   the active Wi-Fi connection (Linux's reg/offchannel scanning during P2P discovery may add
   latency). `[UNKNOWN-needs-test]`
5. **Sink IP discovery when phone is GC** — if the phone ever became GC, the sink (GO) would need
   DHCP + peer IP; when the phone is GO (recommended), the sink needs the GO's IP
   (192.168.49.1 on Android; or read NM's DHCP result / `IpAddrGo` from `GroupStarted` when the
   supplicant assigns). Verify which of these works with a Samsung phone. `[UNKNOWN-needs-test]`
6. **Does Samsung's source require a specific M7 ordering?** miraclecast sends `PLAY` immediately
   after the `SETUP` response, without waiting for the phone's own `wfd_trigger_method: PLAY`;
   AOSP sends its PLAY trigger asynchronously. Works with Samsung in miraclecast today
   `[CONFIRMED-practice]`, but the new implementation should tolerate both orders. Low risk.
7. **WFD IE refresh timing** — whether wpa_supplicant re-advertises updated `WFDIEs` in probe
   responses while the phone is already scanning; if not, the app must set the IEs before starting
   discovery. `[UNKNOWN-needs-test]`
8. **Session-info / coupled-sink value acceptance** — miraclecast ships without session-info
   subelement and works with DeX; confirm a minimal sink (Device Info only) is accepted by the
   phone in 2026. `[UNKNOWN-needs-test]`