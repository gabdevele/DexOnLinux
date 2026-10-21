# Live acceptance — direct WFD sink with NetworkManager pause

Date: 2026-10-21

## Environment

- Ubuntu 24.04, NetworkManager 1.46, wpa_supplicant 2.10
- Intel AX201 (`wlp0s20f3`), phone Samsung S24
- Base Wi-Fi STA: `TIM-76794745`, 5200 MHz / 160 MHz
- No reboot and no NetworkManager/wpa_supplicant restart during the test

## Helper command

```bash
sudo .venv/bin/python -m dexonlinux.helper \
  --interface wlp0s20f3 \
  --backend direct \
  --p2p-freq 2437 \
  --suspend-nm
```

## Observed sequence

```text
INFO dexonlinux.p2p.direct: advertising as a WFD sink on wlp0s20f3
{"event":"ready","data":{"backend":"direct","interface":"wlp0s20f3","ctrl":"/run/wpa_supplicant/p2p-dev-wlp0s20"}}
INFO dexonlinux.nm: suspended NetworkManager (pid 1458) for the P2P session
INFO dexonlinux.p2p.link: pre-upped P2P group interface p2p-wlp0s20-5
INFO dexonlinux.p2p.direct: P2P group p2p-wlp0s20-5 up with 192.168.49.200/24
{"event":"group_started","data":{"ifname":"p2p-wlp0s20-5","role":"client","client_ip":"192.168.49.200","client_prefix":24,"go_ip":"192.168.49.1","peer_mac":"ce:f9:f0:2b:8a:cc","frequency":2437}}
```

RTSP sink:

```text
EVENT connected {'peer': '192.168.49.1', 'rtp_port': 43877}
EVENT video_formats {'value': '00 00 02 04 00000080 00000000 00000000 00 0000 0000 00 none none'}
EVENT streaming {'session': '1804289383'}
```

## Metrics

After 60 seconds:

- NetworkManager state: `T` (stopped), PID 1458
- `p2p-wlp0s20-5`: `UP`, `192.168.49.200/24`, P2P-client, 2437 MHz, width 20 MHz
- `ping -c 3 192.168.49.1`: 3/3 received, 0% loss
- `ping -c 3 1.1.1.1`: 3/3 received, 0% loss
- Base STA still on `wlp0s20f3` at 5200 MHz / 160 MHz

## Cleanup

After `Ctrl-C`:

- `P2P-GROUP-REMOVED p2p-wlp0s20-5 client reason=REQUESTED`
- NetworkManager state: `S` (running), same PID
- `ping -c 3 1.1.1.1`: 0% loss
- No `p2p-*` group interface, no helper or watchdog process

## Product CLI acceptance (pending)

The helper path above passed. The user-facing `dexonlinux --doctor` reports:

```text
Interface: wlp0s20f3
Network stack: NetworkManager
P2P backend: direct
Pause NetworkManager: yes
Supported: yes
```

The full `dexonlinux` CLI run (ADB selection, scrcpy display selection, close
scrcpy, repeat twice) still needs to be executed on the live host.
