![DexOnLinux Logo](https://raw.githubusercontent.com/gabdevele/DexOnLinux/main/assets/banner.png)

# DexOnLinux

DexOnLinux streams Samsung DeX to a Linux desktop and lets you control it with your mouse and keyboard through scrcpy.

The package is available on PyPI:

```bash
pip install dexonlinux
```

## Requirements

DexOnLinux needs:

- Python 3.9 or newer
- Miraclecast (`miracle-wifid`, `miracle-sinkctl`)
- scrcpy
- adb
- a Wi-Fi adapter with P2P support
- sudo access, because the CLI temporarily stops NetworkManager and wpa_supplicant while the DeX session is running

The project is currently tested on Ubuntu 24.04.

## Installation

### Ubuntu 24.04

The helper script installs the system dependencies used by DexOnLinux and installs the `dexonlinux` package from PyPI. It uses `uv` when available and falls back to `pip`.

Do not run the script with `sudo`; it will ask for sudo only when system packages need to be installed.

```bash
git clone https://github.com/gabdevele/DexOnLinux.git
cd DexOnLinux
chmod +x ./scripts/install.sh
./scripts/install.sh
source .venv/bin/activate
dexonlinux
```

### Other Linux distributions

Install Miraclecast and scrcpy using your distribution packages or their upstream instructions:

- [Miraclecast build instructions](https://github.com/albfan/miraclecast/wiki/Building)
- [scrcpy installation instructions](https://github.com/Genymobile/scrcpy/blob/master/README.md#installation)

Then install DexOnLinux from PyPI:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install dexonlinux
dexonlinux
```

If you use uv:

```bash
uv venv
uv pip install dexonlinux
source .venv/bin/activate
dexonlinux
```

## Usage

Run:

```bash
dexonlinux
```

Useful options (these are not required, DexOnLinux works with no arguments):

```bash
dexonlinux --help
dexonlinux --interface wlan0 --device R5CN1234567
dexonlinux --fullscreen
dexonlinux --display-id 2
dexonlinux --debug --log-file dexonlinux.log
dexonlinux --no-banner --no-color
```

DexOnLinux checks dependencies, Wi-Fi P2P support, sudo, and adb before disabling network services. Unless `--yes` is passed, it asks for confirmation before stopping NetworkManager and wpa_supplicant.

On exit, Ctrl+C, or startup failure, DexOnLinux attempts to stop the Miraclecast processes it started and restore network services.

## How it works

DexOnLinux uses:

- Miraclecast for the Wi-Fi Direct sink
- scrcpy for display and input control
- adb to select the Android device
- miracle-sinkctl output to detect DeX connection, disconnection, and stream resolution

## Development setup

If you want to contribute or run the latest code, clone the repository and install the dependencies with uv:

```bash
git clone https://github.com/gabdevele/DexOnLinux.git
cd DexOnLinux
uv sync --group dev --locked
uv run dexonlinux --help
```

## Bugs and feature requests

Please report any bugs or feature requests on the GitHub issue tracker.