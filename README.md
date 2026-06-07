![DexOnLinux Logo](/assets/banner.png)

# DexOnLinux

Got Samsung DeX on your device? What if you could stream directly on your Linux PC and use your mouse and keyboard to control it? DexOnLinux makes this possible in the easiest way possible.


## What is DexOnLinux?

DexOnLinux is a tool that allows you to stream Samsung DeX from your Samsung device to your Linux PC, controlling it with your mouse and keyboard, and interacting with it as if it were a native application on your desktop. 

<img width="978" height="746" alt="image" src="https://github.com/user-attachments/assets/41a5a25b-ad00-4d06-a7af-d62083f295b3" />



## How does it work?

DexOnLinux uses several different components to achieve this such as:

- **miraclecast**: A tool for Wi-Fi Direct connections, allowing your Linux PC to connect to your Samsung device.
- **scrcpy**: A display and control application for Android devices, enabling screen mirroring and input control.
- **pydbus**: A Python library for D-Bus, which makes it easier to read data or catch events from Miraclecast services.

## How to install and run

> **This project requires Python 3.9 or higher, pip, and git to be installed on your system and root privileges to install some dependencies.**

The installation process is pretty easy, just follow the steps below. At the moment everything has been tested only on Ubuntu 24.04, but it should work on other distributions as well.

---

### 1. Clone the repository

```bash
git clone https://github.com/gabdevele/DexOnLinux.git
```

---

### 2. Navigate to the project directory

```bash
cd DexOnLinux    
```

---

### 3. Installation options

Now you have two options, either install it using the provided script or manually install the dependencies and run it directly from the source code.

**Installation script only works on Ubuntu 24.04.**

#### Option 1: Using the installation script (Ubuntu 24.04 only)

1. **Make the scripts executable**:
    ```bash
    chmod +x ./scripts/install.sh
    chmod +x ./scripts/run.sh
    ```
2. **Run the installation script**:
    ```bash
    sudo ./scripts/install.sh
    ```

---

#### Option 2: Manual installation

Since the manual installation relies on external dependencies, I'll link the official installation guides for each of them.

- [miraclecast](https://github.com/albfan/miraclecast/wiki/Building)
- [scrcpy](https://github.com/Genymobile/scrcpy/blob/master/README.md#installation)
- [PyGObject](https://pygobject.gnome.org/getting_started.html)

After installing these dependencies you will need to install Python dependencies with uv:

```bash
uv sync --no-dev --locked
```

For development tools:

```bash
uv sync --group dev --locked
```

---

### 4. Run DexOnLinux
> Keep in mind that your network connection will be temporarily disabled while this tool is running.
```bash
./scripts/run.sh
```
or, after activating the virtual environment:

```bash
dexonlinux
```

From the repository you can also run:

```bash
uv run dexonlinux
```

When DexOnLinux is published on PyPI, the Python package installation will be:

```bash
pip install dexonlinux
dexonlinux
```

Useful CLI options:

```bash
dexonlinux --interface wlan0 --device R5CN1234567
dexonlinux --fullscreen
dexonlinux --display-id 2
dexonlinux --debug --log-file dexonlinux.log
dexonlinux --no-banner --no-color
```

## CLI behavior

DexOnLinux now performs dependency, Wi-Fi P2P, sudo, and ADB checks before disabling network services. Before the network is disabled, the CLI asks for confirmation unless you pass `--yes`.

If the session exits normally, on Ctrl+C, or after a startup error, DexOnLinux attempts to stop the Miraclecast processes it started and restore NetworkManager and wpa_supplicant.

## Troubleshooting

- If your phone appears as `unauthorized`, accept the ADB authorization prompt on the device and refresh.
- If your phone appears as `offline`, reconnect USB and restart ADB with `adb kill-server && adb start-server`.
- If scrcpy opens the wrong display, reconnect DeX and pass `--display-id <id>` after checking the display list shown by the CLI.
- If networking is not restored, run `sudo systemctl start NetworkManager wpa_supplicant`.
