# Live acceptance — direct WFD sink with NetworkManager pause

Date: 2026-10-21

## Environment

- Ubuntu 24.04, NetworkManager 1.46, wpa_supplicant 2.10
- Intel AX201 (`wlp0s20f3`), phone Samsung S24
- Base Wi-Fi STA: `TIM-76794745`, 5200 MHz / 160 MHz
- No reboot and no NetworkManager/wpa_supplicant restart during the test

## Role: laptop is P2P GO

The product uses the laptop as Wi-Fi Direct group owner. The phone joins as a
P2P client and receives a DHCP lease from the helper. The earlier client-role
path was rejected: on this AX201 firmware, the second authentication after the
normal WPS `EAP-FAILURE` crashed iwlwifi (`LINK_CONFIG_CMD`, `ADVANCED_SYSASSERT`)
in cross-band STA 5 GHz + P2P 2.4 GHz operation. The GO path never executes that
client reauthentication and stayed stable.

## Helper command

```bash
sudo .venv/bin/python -m dexonlinux.helper \
  --interface wlp0s20f3 \
  --backend direct \
  --p2p-freq 2437 \
  --suspend-nm \
  --p2p-go
```

## Observed sequence

```text
INFO dexonlinux.p2p.direct: advertising as a WFD sink on wlp0s20f3
{"event":"ready","data":{"backend":"direct","interface":"wlp0s20f3","ctrl":"/run/wpa_supplicant/p2p-dev-wlp0s20"}}
INFO dexonlinux.nm: suspended NetworkManager (pid 1458) for the P2P session
INFO dexonlinux.p2p.link: pre-upped P2P group interface p2p-wlp0s20-1
INFO dexonlinux.p2p.dhcp: DHCP server for 192.168.49.2/24 on p2p-wlp0s20-1
INFO dexonlinux.p2p.direct: P2P GO p2p-wlp0s20-1 up with 192.168.49.1/24, peer lease 192.168.49.2
{"event":"group_started","data":{"ifname":"p2p-wlp0s20-1","role":"GO","client_ip":"192.168.49.1","client_prefix":24,"go_ip":null,"peer_mac":"f4:7b:09:31:44:54","frequency":2437,"peer_ip":"192.168.49.2"}}
```

RTSP sink (the phone's source runs the RTSP server on its DHCP address):

```text
EVENT disconnected {}
EVENT connected {'peer': '192.168.49.2', 'rtp_port': 58103}
EVENT video_formats {'value': '00 00 02 04 00000080 00000000 00000000 00 0000 0000 00 none none'}
EVENT streaming {'session': '1804289383'}
```

scrcpy was opened on the DeX display:

```text
--display-id=17    (1920x1080)
```

## Metrics

After 60 seconds:

- NetworkManager state: `T` (stopped), PID 1458
- `p2p-wlp0s20-1`: `UP`, `192.168.49.1/24`, P2P-GO, 2437 MHz
- Phone lease: `192.168.49.2` (DHCP), reachable by ping
- `ping -c 2 192.168.49.2`: 0% loss at every 10 s check
- `ping -c 2 1.1.1.1`: 0% loss at every 10 s check
- Base STA still on `wlp0s20f3` at 5200 MHz / 160 MHz
- RTSP remained in `streaming`; scrcpy process remained alive
- No new iwlwifi firmware errors after the session started

## Cleanup

After closing scrcpy and stopping the helper with `Ctrl-C`:

- `P2P-GROUP-REMOVED p2p-wlp0s20-1 GO reason=REQUESTED`
- Helper and NetworkManager watchdog exited
- DHCP server stopped
- NetworkManager state: `S` (running), same PID
- `ping -c 3 1.1.1.1`: 3/3 received, 0% loss; DNS resolution works
- No `p2p-*` group interface
- No new iwlwifi firmware errors

## Product CLI acceptance (pending)

The helper path above passed. The user-facing `dexonlinux --doctor` reports:

```text
Interface: wlp0s20f3
Network stack: NetworkManager
P2P backend: direct
Pause NetworkManager: yes
Supported: yes
```

The CLI now starts the helper with `--p2p-go`, uses the GO peer address for the
RTSP client, and starts scrcpy when streaming begins. The full `dexonlinux` CLI
run (ADB selection, scrcpy display selection, close scrcpy, repeat twice) still
needs to be executed on the live host.
