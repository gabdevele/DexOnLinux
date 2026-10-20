# WFD sink spike (throwaway)

This spike validates the riskiest assumptions of
[`docs/superpowers/specs/2026-10-20-miraclecast-free-wifi-link-design.md`](../docs/superpowers/specs/2026-10-20-miraclecast-free-wifi-link-design.md)
before building the real implementation:

1. the phone discovers the laptop as a Wi-Fi Display **sink** (WFD IEs advertised by the
   *system* wpa_supplicant);
2. the phone's GO negotiation is answered and the laptop joins as P2P **client**
   (`go_intent=0`, phone becomes GO);
3. the P2P interface gets an address via DHCP and the laptop connects to the phone's RTSP
   server on TCP 7236;
4. the M1–M7 handshake completes and DeX stays alive while the RTP stream is discarded;
5. **Wi-Fi internet keeps working** on the same adapter for the whole session.

It is intentionally small (one helper + one runner), with no doctor, no packaging and no
migration logic. Do not build on top of this code.

## What it does to the system

- Talks to the **running** wpa_supplicant (the one NetworkManager uses) through its
  control socket. It does **not** stop NetworkManager or wpa_supplicant.
- Temporarily sets `p2p_go_intent=0`, `device_name=DexOnLinux` and the WFD subelements
  (`WFD_SUBELEM_SET`); restores `p2p_go_intent=7` and clears the subelements on exit.
- Creates a P2P group interface (`p2p-*`) and adds an address on it. It never installs a
  default route, so the home Wi-Fi keeps carrying internet traffic.
- If killed with `SIGKILL`, the supplicant settings can remain set; see *Cleanup* below.

## Requirements

- Linux with wpa_supplicant ≥ 2.10 built with `CONFIG_P2P` + `CONFIG_WIFI_DISPLAY`
  (Debian/Ubuntu/Arch/Fedora packages qualify).
- A Wi-Fi adapter/driver supporting P2P and, for this spike, concurrent STA + P2P client
  (`iw list` shows the combination).
- `adb` + `scrcpy` with an authorized USB device (scrcpy mirrors the DeX display).
- A Samsung phone with wireless DeX.

## Run

```bash
cd spike
python3 selftest_rtsp.py      # no phone needed: validates the RTSP state machine
python3 run.py --interface wlp0s20f3
```

`run.py` asks for the sudo password once, then starts the root helper. On the phone:
**Smart View / Cast → select "DexOnLinux"**.

Expected events:

```
helper ready on wlp0s20f3
phone is negotiating (aa:bb:cc:dd:ee:ff)
P2P group up (p2p-wlp0s20f3-0, role client)
address 192.168.x.y/24 on p2p-wlp0s20f3-0; phone 192.168.49.1
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
- After `Ctrl+C`, `p2p_go_intent` is back to 7, the WFD subelement is cleared and no P2P
  group is left behind.

## Troubleshooting

- **`no P2P control socket`** — check `ls /run/wpa_supplicant/`; the P2P socket is
  `p2p-dev-<first 7 chars of the interface>`. Pass the right `--interface`.
- **Phone does not list `DexOnLinux`** — the WFD IEs were rejected or not advertised.
  Try the other Device Info variants: `--device-info 0091` (preferred connectivity),
  `--device-info 0013` (dual role), `--device-info 0093`.
- **Group formed but role is `GO`** — the phone negotiated us as group owner; this spike
  only supports the client role (and its DHCP client). Report the log line.
- **`RTSP connect ... failed`** — check the phone address (`ip addr` on the `p2p-*`
  interface, DHCP lease log) and that `ping <phone-ip>` works.
- **DHCP failed** — the spike falls back to `192.168.49.2/24` and phone `192.168.49.1`.
  Report whether the fallback was used.

## Cleanup (if the process was killed hard)

```bash
CTRL=/run/wpa_supplicant/p2p-dev-wlp0s20   # adjust to your interface
sudo wpa_cli -p /run/wpa_supplicant -i "$(basename "$CTRL")" p2p_group_remove p2p-wlp0s20f3-0
sudo wpa_cli -p /run/wpa_supplicant -i "$(basename "$CTRL")" p2p_ext_listen
sudo wpa_cli -p /run/wpa_supplicant -i "$(basename "$CTRL")" WFD_SUBELEM_SET 0
sudo wpa_cli -p /run/wpa_supplicant -i "$(basename "$CTRL")" set p2p_go_intent 7
```

## Layout

| file | role |
|---|---|
| `rtsp.py` | RTSP messages + `WfdSink` state machine (M1–M7, keepalive, teardown) |
| `fake_source.py` | minimal WFD source used by the self-test (no phone) |
| `selftest_rtsp.py` | asserts the full handshake against the fake source |
| `supplicant.py` | wpa_supplicant control-interface client (P2P + WFD commands) |
| `dhcp.py` | minimal DHCP client for the P2P interface (never adds a default route) |
| `linkd.py` | root helper: supplicant + DHCP + RTSP, JSON events on stdout |
| `run.py` | user runner: sudo auth, helper lifecycle, scrcpy |
