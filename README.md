![DexOnLinux Logo](https://raw.githubusercontent.com/gabdevele/DexOnLinux/main/assets/banner.png)

# DexOnLinux

DexOnLinux streams Samsung DeX to a Linux desktop and lets you control it with your mouse and keyboard through scrcpy.

## Installation

DexOnLinux is available on PyPI.

Recommended:

```bash
uv tool install dexonlinux
dexonlinux
```

Fallback:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install dexonlinux
dexonlinux
```

## Requirements

DexOnLinux needs:

- Python 3.9 or newer
- Miraclecast (`miracle-wifid`, `miracle-sinkctl`)
- scrcpy
- adb
- a Wi-Fi adapter with P2P support
- NetworkManager with `nmcli`
- sudo access for Wi-Fi Direct setup

The project is currently tested on Ubuntu 24.04.

## Ubuntu 24.04 dependency helper

The helper script installs the system dependencies used by DexOnLinux and installs the PyPI package. It uses `uv tool install` when uv is available. If uv is not installed, it creates `.venv` and installs DexOnLinux there with pip.

Do not run the script with `sudo`; it will ask for sudo only when system packages need to be installed.

```bash
git clone https://github.com/gabdevele/DexOnLinux.git
cd DexOnLinux
chmod +x ./scripts/install.sh
./scripts/install.sh
```

If uv is available, run:

```bash
dexonlinux
```

If the script used the pip fallback, run:

```bash
source .venv/bin/activate
dexonlinux
```

## Other Linux distributions

Install Miraclecast and scrcpy using your distribution packages or their upstream instructions:

- [Miraclecast build instructions](https://github.com/albfan/miraclecast/wiki/Building)
- [scrcpy installation instructions](https://github.com/Genymobile/scrcpy/blob/master/README.md#installation)

Then install DexOnLinux from PyPI:

```bash
uv tool install dexonlinux
dexonlinux
```

or:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install dexonlinux
dexonlinux
```

## Usage

Run:

```bash
dexonlinux
```

Useful options:

```bash
dexonlinux --help
dexonlinux --interface wlan0 --device R5CN1234567
dexonlinux --network-mode auto
dexonlinux --network-mode scoped
dexonlinux --network-mode full-stop
dexonlinux --fullscreen
dexonlinux --display-id 2
dexonlinux --debug --log-file dexonlinux.log
dexonlinux --no-banner --no-color
```

By default, DexOnLinux uses `--network-mode auto`.

In auto mode, DexOnLinux checks the selected Wi-Fi interface against the current default route:

- if that Wi-Fi interface is the only default route, DexOnLinux uses `full-stop`
- if another default route is available, DexOnLinux uses `scoped`

This keeps the common single-Wi-Fi setup simple and reliable. Miraclecast needs direct control of Wi-Fi Direct/P2P, so if your internet connection uses the same Wi-Fi adapter, it will go offline until DexOnLinux exits.

`full-stop` stops both NetworkManager and `wpa_supplicant` while the session is running. This is the most reliable mode for single-adapter systems.

`scoped` keeps NetworkManager running, releases the selected Wi-Fi interface, and stops `wpa_supplicant`. It can preserve internet through Ethernet, USB tethering, or another network path that does not depend on `wpa_supplicant`.

## How it works

DexOnLinux uses:

- Miraclecast for the Wi-Fi Direct sink
- scrcpy for display and input control
- adb to select the Android device
- miracle-sinkctl output to detect DeX connection, disconnection, and stream resolution

On exit, Ctrl+C, or startup failure, DexOnLinux stops the Miraclecast processes it started and restores the network state it changed.

## Development setup

```bash
git clone https://github.com/gabdevele/DexOnLinux.git
cd DexOnLinux
uv sync --group dev --locked
uv run dexonlinux --help
```

Build and check the package:

```bash
uv run --only-group dev python -m build
uv run --only-group dev python -m twine check dist/*
```

## Troubleshooting

- `unauthorized` adb device: accept the USB debugging prompt on the phone.
- `offline` adb device: reconnect USB, then run `adb kill-server && adb start-server`.
- Wrong display: reconnect DeX and choose the display marked as likely DeX, or pass `--display-id`.
- Network not restored: run `sudo systemctl start NetworkManager wpa_supplicant`, or reconnect the Wi-Fi interface from NetworkManager.
