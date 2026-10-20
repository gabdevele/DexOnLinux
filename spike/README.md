# WFD sink spike (throwaway)

This spike validates the riskiest assumptions of
[`docs/superpowers/specs/2026-10-20-miraclecast-free-wifi-link-design.md`](../docs/superpowers/specs/2026-10-20-miraclecast-free-wifi-link-design.md)
before building the real implementation:

1. the phone discovers the laptop as a Wi-Fi Display **sink** (WFD IEs advertised by the
   *system* wpa_supplicant);
2. the phone's **display-PIN** provision discovery is answered and the laptop joins as
   P2P **client** (`go_intent=0`, phone becomes GO);
3. the P2P interface gets the address carried in the WPS credentials and the laptop
   connects to the phone's RTSP server on TCP 7236;
4. the M1–M7 handshake completes and DeX stays alive while the RTP stream is discarded;
5. **Wi-Fi internet keeps working** on the same adapter for the whole session.

It is intentionally small (one helper + one runner), with no doctor, no packaging and no
migration logic. Do not build on top of this code.

## What it does to the system

- Talks to the **running** wpa_supplicant (the one NetworkManager uses) through its
  control socket. It does **not** stop NetworkManager or wpa_supplicant.
- Temporarily enables Wi-Fi Display (`SET wifi_display 1`), sets `p2p_go_intent=0`,
  `device_name=DexOnLinux`, the WFD subelements (`WFD_SUBELEM_SET all ...`) and
  continuous listen (`P2P_EXT_LISTEN 1000 1000`).
- On exit it clears the subelements, disables WFD if it was previously off, restores
  `p2p_go_intent=7` and removes the P2P group. `device_name` stays `DexOnLinux` until
  the supplicant restarts (cosmetic; see *Cleanup*).
- Creates a P2P group interface (`p2p-*`) and adds the address supplied by the phone's
  WPS credentials (DHCP is only a fallback). It never installs a default route, so the
  home Wi-Fi keeps carrying internet traffic.
- Requests a **2.4 GHz** operating channel (`freq=2437`) for the P2P group. With a 5 GHz
  STA, iwlwifi + wpa_supplicant cannot keep a second 5 GHz channel alive (especially with
  a 160 MHz STA); the group is torn down immediately. 2.4 GHz + 5 GHz concurrency works.
- If killed with `SIGKILL`, the supplicant settings can remain set; see *Cleanup* below.

## Requirements

- Linux with wpa_supplicant ≥ 2.10 built with `CONFIG_P2P` + `CONFIG_WIFI_DISPLAY`
  (Debian/Ubuntu/Arch/Fedora packages qualify).
- A Wi-Fi adapter/driver supporting P2P and, for this spike, concurrent STA + P2P client
  (`iw list` shows the combination).
- `adb` + `scrcpy` with an authorized USB device (scrcpy mirrors the DeX display).
- A Samsung phone with wireless DeX, unlocked when selecting the sink (it asks for a PIN).

## Run

```bash
cd spike
python3 selftest_rtsp.py         # RTSP state machine, no phone needed
python3 selftest_supplicant.py   # ctrl_iface replies/event queue, no root needed
python3 selftest_events.py       # P2P event -> supplicant command mapping, no root needed
python3 run.py --interface wlp0s20f3
# optional: --p2p-freq 2412|2437|2462 if 2.4 GHz channel 6 is congested
```

`run.py` asks for the sudo password once, then starts the root helper. On the phone:
**Samsung DeX → Connessione wireless → DexOnLinux**.

The phone then asks for the **PIN shown on DexOnLinux**. The runner prints it in a box
(and raises a desktop notification with `notify-send`); enter it on the phone within the
PIN lifetime (~2 minutes).

Expected events (order may vary):

```
helper ready on wlp0s20f3
phone is negotiating (aa:bb:cc:dd:ee:ff)
============================================================
PIN RICHIESTO DAL TELEFONO: 12345678
Inseriscilo ORA nella schermata 'Connessione wireless'.
============================================================
P2P group up (p2p-wlp0s20-0, role client, client 192.168.49.200, phone 192.168.49.1)
address 192.168.49.200/24 on p2p-wlp0s20-0; phone 192.168.49.1
RTSP connected to the phone
negotiated video formats: ...
DeX session established
```

Then scrcpy starts (or run it manually with `--no-scrcpy`). Keep a terminal open and check:

```bash
ping -c 5 1.1.1.1      # must stay at 0% loss during the DeX session
```

## Success criteria

- `DeX session established` in the runner.
- The DeX desktop appears in scrcpy.
- `ping 1.1.1.1` keeps working for the whole session.
- After `Ctrl+C`, `p2p_go_intent` is back to 7, the WFD subelement is cleared,
  `wifi_display` is back to its previous value and no P2P group is left behind.

## The PIN flow (important)

- Samsung DeX does **not** use PBC. It sends a P2P provision-discovery request with the
  WPS **display** method; the supplicant generates a PIN and emits
  `P2P-PROV-DISC-SHOW-PIN`. The phone then shows a PIN entry dialog.
- The helper reacts immediately with
  `P2P_CONNECT <mac> <pin> display freq=2437 go_intent=0`.
  Waiting for `P2P-GO-NEG-REQUEST` and using PBC fails: the phone rejects the WPS method.
- Every new attempt generates a **new PIN**. Always enter the latest one printed; an old
  PIN is rejected.
- After a failed attempt the phone may keep a "Connesso in precedenza" entry. If
  "Avvia ora" fails immediately and no new PIN appears, remove DexOnLinux from the
  phone's saved Wi-Fi Direct devices and select it again.

## Troubleshooting

- **`no P2P control socket`** — check `ls /run/wpa_supplicant/`; the P2P socket is
  `p2p-dev-<first 7 chars of the interface>`. Pass the right `--interface`.
- **Phone does not list `DexOnLinux`** — the helper fails fast if
  `SET wifi_display 1`/`WFD_SUBELEM_SET` is rejected; check its stderr. Otherwise try the
  other Device Info variants: `--device-info 0091` (preferred connectivity),
  `--device-info 0013` (dual role), `--device-info 0093`.
- **Sink listed but no PIN prompt / connection fails** — retry: continuous listen is
  configured, but the phone may have used a stale scan entry. If the PIN expired, retry to
  generate a new one.
- **`Avvia ora` fails immediately** — stale cached device/persistent group on the phone.
  Forget DexOnLinux in Settings → Wi-Fi → Wi-Fi Direct (or the DeX connectivity screen),
  then select it again.
- **Group formed but role is `GO`** — the phone negotiated us as group owner; this spike
  only supports the client role (and its DHCP client). Report the log line.
- **`RTSP connect ... failed`** — check the address on the `p2p-*` interface and that
  `ping 192.168.49.1` works. If the group interface is DOWN, the phone removed the group
  (often a channel conflict; see below).
- **Group disappears immediately / interface DOWN** — the P2P channel conflicts with the
  STA. The helper already requests 2.4 GHz (`freq=2437`). If the phone still picks a 5 GHz
  channel, try another 2.4 GHz channel (`--p2p-freq 2412` or `2462`) and check
  `journalctl -b -u wpa_supplicant` for `FREQ_CONFLICT`/`Assoc conflicting freq`.
- **DHCP failed** — only occurs if the group event has no WPS address; the spike falls
  back to `192.168.49.2/24` and phone `192.168.49.1`. Report whether the fallback was used.
- **`pin_entry_required`** — the phone displays a PIN that the laptop is supposed to
  enter. This spike has no PIN input UI; report it (Samsung uses the opposite direction).

## Cleanup (if the process was killed hard)

```bash
I=p2p-dev-wlp0s20   # adjust: p2p-dev-<first 7 chars of the interface>
sudo wpa_cli -p /run/wpa_supplicant -i "$I" p2p_group_remove p2p-wlp0s20f3-0
sudo wpa_cli -p /run/wpa_supplicant -i "$I" p2p_ext_listen
sudo wpa_cli -p /run/wpa_supplicant -i "$I" WFD_SUBELEM_SET 0 ""   # trailing space clears subelement 0
sudo wpa_cli -p /run/wpa_supplicant -i "$I" set wifi_display 0
sudo wpa_cli -p /run/wpa_supplicant -i "$I" set p2p_go_intent 7
# device_name: restart wpa_supplicant (or reboot) to restore the configured name
```

## Layout

| file | role |
|---|---|
| `rtsp.py` | RTSP messages + `WfdSink` state machine (M1–M7, keepalive, teardown) |
| `fake_source.py` | minimal WFD source used by the self-test (no phone) |
| `selftest_rtsp.py` | asserts the full handshake against the fake source |
| `supplicant.py` | wpa_supplicant control-interface client (P2P + WFD commands) |
| `selftest_supplicant.py` | fake ctrl socket: replies + event queueing |
| `dhcp.py` | minimal DHCP client for the P2P interface (never adds a default route) |
| `linkd.py` | root helper: supplicant + address + RTSP, JSON events on stdout |
| `selftest_events.py` | offline tests for the P2P event → command mapping (WFD/PIN) |
| `run.py` | user runner: sudo auth, helper lifecycle, PIN display, scrcpy |
