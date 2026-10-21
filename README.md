![DexOnLinux Logo](https://raw.githubusercontent.com/gabdevele/DexOnLinux/main/assets/banner.png)

# DexOnLinux

DexOnLinux starts Samsung DeX on a Linux desktop over Wi-Fi Direct and opens the
DeX desktop with scrcpy. It replaces the old Miraclecast path with a small
stdlib-first Wi-Fi Display sink.

## What it does

- The laptop is the Wi-Fi Direct group owner (GO); the phone joins as a P2P
  client and receives `192.168.49.2` from a small DHCP server inside the helper.
- The laptop advertises a Wi-Fi Display sink and uses PBC (no PIN).
- A minimal RTSP client performs the WFD handshake; scrcpy shows the DeX desktop
  over USB/adb.
- The existing Wi-Fi connection stays usable. NetworkManager is paused with
  `SIGSTOP` only while the P2P group is being created and is resumed
  automatically when the session ends. wpa_supplicant is never stopped,
  restarted or replaced.
- A watchdog resumes NetworkManager even if the helper crashes.

The GO role is deliberate: on the Intel AX201, the reverse role (phone GO,
laptop client) triggers an iwlwifi firmware crash during the client
reauthentication after WPS. The GO path was stable with the STA on 5 GHz and the
P2P group on 2437 MHz; the station band is not changed.

No reboot, no private supplicant, no Miraclecast.

## Requirements

- Python 3.9 or newer
- a Wi-Fi adapter and driver with P2P GO support and 2.4 GHz operation
  (the P2P group is created on 2437 MHz; the station can stay on 2.4 or 5 GHz)
- NetworkManager + wpa_supplicant (tested on Ubuntu 24.04, NetworkManager 1.46,
  Intel AX201; standalone wpa_supplicant is also supported)
- `iw`, `ip`, `adb`, `scrcpy`, `sudo`
- Debian/Ubuntu, Arch, or Fedora

NetworkManager + iwd and ConnMan are not supported in v1. The doctor reports
them before doing anything.

## Installation

From PyPI:

```bash
uv tool install dexonlinux
dexonlinux --doctor
dexonlinux
```

Or with a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install dexonlinux
dexonlinux --doctor
dexonlinux
```

### Dependency helper

The repository installer detects Debian/Ubuntu, Arch and Fedora, installs the
system dependencies, and installs the package with `uv` when available. Do not
run it as root; it asks for sudo only for system packages.

```bash
git clone https://github.com/gabdevele/DexOnLinux.git
cd DexOnLinux
chmod +x ./scripts/install.sh
./scripts/install.sh
```

Install `adb` and `scrcpy` from your distribution. `scrcpy` must be new enough
to support `--list-displays`; otherwise install it from the upstream release.

## Usage

```bash
dexonlinux --doctor
dexonlinux
dexonlinux --interface wlan0 --device R5CN1234567
dexonlinux --display-id 2
dexonlinux --fullscreen
dexonlinux --debug --log-file dexonlinux.log
dexonlinux --no-banner --no-color
```

`dexonlinux --doctor` is read-only and prints the detected network stack, the
selected P2P backend, whether NetworkManager will be paused, and any reasons the
host is unsupported.

On the phone:

1. Open Smart View / DeX.
2. Keep the phone unlocked and select `DexOnLinux`.
3. The connection uses Wi-Fi Direct PBC; there is no PIN prompt.
4. If the phone shows `DexOnLinux` as previously connected, forget it and retry.

## How it works

The privileged helper owns only the P2P device through the system
wpa_supplicant control socket:

1. It configures the WFD information elements and listens for PBC.
2. Immediately before `P2P_CONNECT`, it sends `SIGSTOP` to NetworkManager.
   This prevents NetworkManager from removing an externally created P2P group.
3. It creates the group as GO on `freq=2437`, pre-ups the group interface after
   GO negotiation, configures `192.168.49.1/24`, and serves the phone the
   `192.168.49.2` DHCP lease.
4. The user process runs the RTSP sink against the phone's DHCP address and
   starts scrcpy when streaming begins.
5. On exit, Ctrl-C or a crash, the helper removes the group and sends
   `SIGCONT`; a separate watchdog also resumes NetworkManager if the helper dies.

The station Wi-Fi and its routes are not modified, so internet stays available.

## Troubleshooting

- `dexonlinux --doctor` reports `Supported: no`: fix every listed reason before
  starting a session.
- `NM + iwd` or ConnMan: switch to NetworkManager with wpa_supplicant (v1 does
  not implement iwd/ConnMan P2P).
- No P2P control socket: enable Wi-Fi and Wi-Fi Direct; make sure
  wpa_supplicant is running under NetworkManager.
- Phone shows `DexOnLinux` as previously connected: forget the device in Smart
  View and retry.
- The P2P group is created on 2437 MHz. A 5 GHz-only adapter or a phone that
  refuses 2.4 GHz P2P cannot use this v1 path; the station band itself is not
  changed.
- The old phone-GO/laptop-client role is not used: it crashed the AX201 firmware
  during the post-WPS client reauthentication.
- Wrong display: reconnect DeX and choose the display marked as likely DeX, or
  pass `--display-id`.
- Verify internet during a session with `ping -c 3 1.1.1.1`; it should stay at
  0% loss.
- If a session is interrupted and NetworkManager looks unresponsive, wait for
  the watchdog or run `sudo kill -CONT $(pidof NetworkManager)`.

## Development

```bash
git clone https://github.com/gabdevele/DexOnLinux.git
cd DexOnLinux
uv sync --group dev --locked
uv run dexonlinux --help
uv run pytest -q
uv run ruff check src tests
```

Build and check the package:

```bash
uv run --only-group dev python -m build
uv run --only-group dev python -m twine check dist/*
```

The research reports and decision records live under `docs/research/`; the
product code is under `src/dexonlinux/`.
