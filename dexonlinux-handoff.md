# DexOnLinux - Handoff & Architecture Strategy Document

> **Status update — 2026-10-21: implemented and live-accepted on the v1.0.2
> tree; the cleanup landed on top; the post-cleanup live re-run is pending.**
> The plan below was executed in this workspace:
> - `miraclecast` is gone; the product uses a modular Python direct backend
>   (`src/dexonlinux/p2p/`) that talks to `wpa_supplicant` over the P2P
>   ctrl_iface and freezes `NetworkManager` with `SIGSTOP` before the group is
>   created.
> - The laptop acts as the Wi-Fi Direct **GO** (`go_intent=15`); the phone is the
>   P2P client. Role and P2P frequency are explicit settings: the CLI passes
>   `--p2p-freq` (default 2437 MHz) and `--doctor` validates it against the
>   adapter's channel list. The reverse role crashed the reference Intel AX201
>   iwlwifi firmware during post-WPS client reauthentication and is not used by
>   the product path.
> - A minimal internal DHCP server (`p2p/dhcp.py`) derives mask/broadcast from a
>   single `GroupNetwork` definition and hands the phone `192.168.49.2`.
> - NetworkManager is resumed on every exit path, with a crash watchdog; the
>   station connection stays up the whole time and wpa_supplicant is never
>   restarted.
> - `dexonlinux` drives the helper, the RTSP WFD handshake and scrcpy
>   end-to-end; the DeX display is auto-selected (landscape 16:9).
> - 100 unit tests, ruff (E,F,W,I,UP,B,SIM,C4,RET @ 120 cols) clean, pytest
>   configured; the CI workflow (Python 3.9/3.12) runs the same commands,
>   verified locally; `python -m build` + `twine check` pass.
> - The throwaway `spike/` tree is deleted; interaction, logging, models and P2P
>   types live in focused modules.
> - Live evidence (pre-cleanup tree): `docs/research/2026-10-21-live-acceptance.md`.
>   Its helper `--p2p-go` flag and old `group_started` keys are historical: the
>   CLI now passes `--role go --p2p-freq <MHz>`. Re-run `dexonlinux --doctor`
>   and one phone session on this tree before release; that re-run is the
>   remaining release gate.
>
> Remaining ideas from section 3 that are still open: `rich`-based UI, polkit
> instead of sudo stdin, wireless ADB, broader chipset matrix.

## 1. Executive Summary & Context
**DexOnLinux** is an open-source tool designed to bridge Samsung DeX to Linux desktop environments.
- **Author:** gabdevele
- **Current Version:** v1.0.2 (Alpha / Functional MVP)
- **Stack:** Python (>= 3.9), uv, `miraclecast`, `scrcpy`, `adb`, `NetworkManager` / `wpa_supplicant`.
- **Core Concept:** A pragmatic "hacker workaround":
  1. Uses `miracle-wifid` and `miracle-sinkctl` to emulate a Miracast Wi-Fi Direct sink, tricking Samsung One UI into initiating a native wireless DeX session.
  2. Discards the raw MPEG-TS video stream sent over UDP via a local socket drain thread (`StreamHandler._drain_stream`).
  3. Uses `scrcpy` over `adb` to stream and control the virtual DeX display with ultra-low latency, hardware acceleration, and native mouse/keyboard input.

---

## 2. Review of Questions & Answers Discussed

### Q1: Honest assessment of the project's current state and niche potential
- **Potential:** High. Samsung discontinued official DeX for PC on Windows/macOS, never supported Linux natively, and AOSP desktop mode (`scrcpy --new-display`) lacks the polish and features of Samsung One UI DeX.
- **Current Codebase State:**
  - **Strengths:** Clean modular separation (`commands.py`, `network.py`, `runtime.py`, `stream_handler.py`). Robust cleanup on `SIGINT`/`SIGTERM`. Multiple network restoration modes (`auto`, `scoped`, `full-stop`).
  - **Weaknesses / Friction:**
    - Heavy dependency on `miraclecast` (legacy C codebase, hard to compile/package, prone to runtime crashes).
    - Requires `sudo` and temporarily disrupts host network connections.
    - Missing automated test suites, type annotations, and automated linting.

### Q2: What competitors and alternatives exist?
1. **Official Samsung DeX for PC:** Discontinued / deprecated on desktop OSes; never existed for Linux.
2. **scrcpy (`--new-display`):** Creates an AOSP desktop secondary screen, but does not trigger the Samsung DeX desktop UI.
3. **Hardware dummy plugs (USB-C dummy HDMI):** Tricks the phone into physical desktop mode, captured via scrcpy; requires extra hardware dongles.
4. **Reverse-engineered Samsung USB DeX protocol:** Previous community attempts failed to yield a stable, cross-device client due to proprietary Samsung handshake changes.
5. **DexOnLinux Position:** The only software-only solution bringing true Samsung DeX to Linux.

### Q3: How far can we take this project? (Architectural Evolution)
- **Phase 1 (Refinement):** Terminal UI transition to `rich`/`textual`, `dexonlinux doctor` pre-flight checks, automatic ADB-over-Wi-Fi pairing for a 100% wireless experience.
- **Phase 2 (Privilege & System Integration):** Polkit / D-Bus integration to remove plaintext `sudo` prompts; native packaging (AUR, PPA/Deb, Flatpak).
- **Phase 3 (Self-contained Miracast Sink):** Replace `miraclecast` with a minimal embedded Python or Rust RTSP handshake daemon. The tool only needs to negotiate RTSP M1–M7 and accept the connection; `scrcpy` handles the actual interactive display.

### Q4: The Wi-Fi Problem — Can we use Wi-Fi on the PC while DeX is active?
- **Root Cause of Internet Loss:** `miraclecast` requires exclusive adapter control and forcibly terminates/releases `NetworkManager` and `wpa_supplicant`.
- **Hardware Reality:** Modern Wi-Fi NICs (Intel, Qualcomm, Realtek, Mediatek) support simultaneous Multi-VIF (Multi-Channel Concurrency / `valid interface combinations` in `iw list`):
  - 1x `managed` (STA) connected to the local home router for Internet.
  - 1x `P2P-client` / `P2P-GO` for the direct Wi-Fi link with the Samsung device.
- **Engineering Solution:**
  - Communicate with `wpa_supplicant` via its **D-Bus interface** (`fi.w1.wpa_supplicant1.Interface.P2PDevice`) or `NetworkManager` P2P APIs.
  - Maintain the active internet connection on `wlan0` while creating dynamic virtual sub-interfaces (e.g. `p2p-wlan0-0`) for DeX on the same operating channel.
  - Eliminates the need to stop `NetworkManager` or `wpa_supplicant`.

---

## 3. Recommended Next Implementation Steps

1. **CLI / UX Modernization:**
   - Migrate from `colorama` to `rich` for structured tables, spinners, and log formatting.
   - Implement `dexonlinux doctor` to inspect Wi-Fi P2P support, open ports, and installed dependencies (`adb`, `scrcpy`, `iw`, etc.).
2. **Codebase Quality:**
   - Add type annotations across `src/dexonlinux/*.py`.
   - Configure `ruff` as the primary linter and formatter.
   - Add unit/mock tests for device discovery and command parsing.
3. **Network Concurrency (Zero-Disconnect Wi-Fi):**
   - Research and prototype a non-disruptive D-Bus P2P session via `wpa_supplicant` to obsolete `full-stop` / `scoped` network modes.
4. **True Wireless ADB:**
   - Detect phone IP during P2P negotiation and automatically run `adb connect <phone_ip>:5555`.

---

## 4. Suggested Skills for Next Agent
- **`systematic-debugging`**: When diagnosing P2P socket communication or `wpa_supplicant` D-Bus calls.
- **`ui-styling`** or **`design`**: If expanding into a native GTK/Qt/Textual user interface.
- **`writing-plans`**: Prior to executing the transition away from `miraclecast`.
