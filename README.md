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

The GO role is the v1 product path: a negotiation that would put the laptop in
the client role fails the session with a clear error. The reverse role crashed
the iwlwifi firmware of the reference Intel AX201 during the post-WPS client
reauthentication (recorded in `docs/research/2026-10-21-live-acceptance.md`);
other adapters may fare better, but v1 keeps this single tested path. The
station band is left to NetworkManager and is never changed.

No reboot, no private supplicant, no Miraclecast.

## Requirements

- Python 3.11 or newer
- a Wi-Fi adapter and driver with P2P GO support
  (the P2P group runs on 2437 MHz by default; `--doctor` validates the
  frequency against the adapter's channel list and `--p2p-freq <MHz>` overrides
  it when needed; the station can stay on any band)
- NetworkManager + wpa_supplicant built with D-Bus, P2P and Wi-Fi Display
  support (tested on Ubuntu 24.04, NetworkManager 1.46, Intel AX201; standalone
  wpa_supplicant is also supported)
- `iw`, `adb`, `scrcpy`, `sudo`
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
dexonlinux --p2p-freq 5180
dexonlinux --p2p-transport ctrl
dexonlinux --debug --log-file dexonlinux.log
dexonlinux --no-banner --no-color
```

`dexonlinux --doctor` is read-only and prints the detected network stack, the
selected P2P backend and transport, whether NetworkManager will be paused, and
any reasons the host is unsupported. It also checks that the requested P2P
frequency (`--p2p-freq`, default 2437 MHz) is in the adapter's channel list,
and suggests usable channels when it is not.

The helper prefers the wpa_supplicant D-Bus API and falls back to the
`/run/wpa_supplicant/p2p-dev-*` control socket when the D-Bus API is missing
(a supplicant built without `CONFIG_WIFI_DISPLAY`, or without P2P). Use
`--p2p-transport dbus` or `--p2p-transport ctrl` to force one.

On the phone:

1. Open Smart View / DeX.
2. Keep the phone unlocked and select `DexOnLinux`.
3. The connection uses Wi-Fi Direct PBC; there is no PIN prompt.
4. If the phone shows `DexOnLinux` as previously connected, forget it and retry.

## How it works

The privileged helper owns only the P2P device through the existing system
wpa_supplicant, over D-Bus when the build exposes the P2P/WFD API:

1. It configures the WFD information elements and listens for PBC.
2. Immediately before `P2P_CONNECT`, it sends `SIGSTOP` to NetworkManager.
   This prevents NetworkManager from removing an externally created P2P group.
3. It creates the group as GO on the configured frequency (2437 MHz by
   default), pre-ups the group interface after GO negotiation, configures
   `192.168.49.1/24`, and serves the phone the `192.168.49.2` DHCP lease.
4. The user process runs the RTSP sink against the phone's DHCP address and
   starts scrcpy when streaming begins.
5. On exit, Ctrl-C or a crash, the helper removes the group and sends
   `SIGCONT`; a separate watchdog also resumes NetworkManager if the helper dies.

The station Wi-Fi and its routes are not modified, so internet stays available.
wpa_supplicant and NetworkManager are never stopped or reconfigured.

## Troubleshooting

- `dexonlinux --doctor` reports `Supported: no`: fix every listed reason before
  starting a session.
- `NM + iwd` or ConnMan: switch to NetworkManager with wpa_supplicant (v1 does
  not implement iwd/ConnMan P2P).
- `P2P transport: none`: the supplicant D-Bus API is missing and there is no
  P2P control socket. Enable Wi-Fi Direct and check that wpa_supplicant is
  running under NetworkManager. `--p2p-transport ctrl` forces the control
  socket when D-Bus is incomplete.
- `P2P transport: unknown`: the doctor could not read the supplicant as this
  user; the privileged helper verifies it before starting.
- `built without CONFIG_WIFI_DISPLAY`: the distribution's wpa_supplicant lacks
  the Wi-Fi Display API; install a build with WFD support or use the control
  socket if it is available.
- Phone shows `DexOnLinux` as previously connected: forget the device in Smart
  View and retry.
- `frequency 2437 MHz is not supported by this adapter`: the doctor lists the
  usable channels; pass one of them with `--p2p-freq <MHz>`. A phone that
  refuses the chosen band still cannot use this v1 path.
- The phone-GO/laptop-client role is not used in v1: it crashed the reference
  adapter's firmware during the post-WPS client reauthentication.
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

The product CLI always starts the helper with `--role go`; the helper module
also accepts `--role client` for diagnostics (`python -m
dexonlinux.helper.service --help`).

The research reports and decision records live under `docs/research/`; the
product code is under `src/dexonlinux/`.
