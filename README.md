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
- **ffmpeg**: A powerful multimedia framework, used for consume the video stream (PLANNING TO REMOVE THIS DEPENDENCY).


## How to install and run

> **This project requires python 3.8 or higher, pip, and git to be installed on your system and root privileges to install some dependencies.**

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
- [ffmpeg](https://ffmpeg.org/download.html)

After installing these dependencies you will need to install python dependencies:

```bash
pip install -r requirements.txt
```

---

### 4. Run DexOnLinux
> Keep in mind that your network connection will be temporarily disabled while this tool is running.
```bash
./scripts/run.sh
```
and that's it!

