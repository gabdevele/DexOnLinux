# Cleanup, Architecture and Generic-Host Hardening Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the live-accepted v1.0.2 working tree into a small, readable, generically-written package: no throwaway spike, no chip-specific assumptions baked into flow or wording, typed boundaries, honest comments, ruff/pytest/CI gates.

**Architecture:** Keep the current process model (user CLI + one root helper over JSON-lines stdin/stdout; user-side RTSP sink; scrcpy over adb). Reorganize the library surface only: user-interaction and logging are split out of `utils.py`; P2P types get a role enum and one group-network definition; the doctor validates the requested P2P frequency against the driver instead of assuming 2.4 GHz channel 6 works. Historical specs, plans and research docs are records and are not rewritten; only current README/CLI text is updated.

**Tech Stack:** Python ≥ 3.9 (stdlib only at runtime, plus `colorama`), wpa_supplicant ctrl_iface, `iw`/`ip`, NetworkManager `nmcli`/signals, pytest + ruff, uv.

**Spec:** supersedes the stale parts of `docs/superpowers/specs/2026-10-20-miraclecast-free-wifi-link-design.md`; live behavior evidence in `docs/research/2026-10-21-live-acceptance.md`.

## Global Constraints

- Python ≥ 3.9; no new runtime dependencies; `colorama` stays (pin relaxed to a range).
- Never stop, restart or replace NetworkManager or wpa_supplicant; Wi-Fi internet must remain usable; NM is only `SIGSTOP`-ed between `P2P_CONNECT` and group start and always resumed.
- PBC only; the CLI drives the laptop as P2P **GO** (`go_intent=15`); the helper keeps a `client` role for diagnostics but the product path is GO.
- No behavior may depend on one chipset or one machine: comments may cite the observing hardware as evidence, never as the reason the flow only handles that hardware.
- Comments explain **why**, only where the code cannot; no narration of the obvious.
- `pytest` and `ruff check src tests` green after every task; build + `twine check` green at the end.
- Public CLI/UX stays: same commands, same flags (`--display-id`, `--fullscreen`, `--doctor`, `--debug`, `--no-color`, `--no-banner`, `--yes`, `--log-file`, `--interface`, `--device`, `--backend`), plus the new `--p2p-freq`.
- LLM-generated prose (README, comments) must not claim behavior that has no test or live evidence.

## Review Focus

The failure modes below are not covered by existing tests; each line gets the named test in the owning task.

1. Driver supports P2P but not the default channel 6 → the CLI must fail in `--doctor` with an actionable reason instead of failing mid-session (T10).
2. `iw dev <if> info` on an unusual host omits `wiphy` → doctor must skip the frequency check, not crash and not report a false negative (T10).
3. A helper from an older install emits the old `group_started` keys → `DexSession` must reject it with a clear `P2PError`, never unpack garbage (T9).
4. A stale `dexonlinux-linkd` earlier in `PATH` → the helper must always run from the same interpreter/package as the CLI (T5).
5. DHCP REQUEST from a peer that asks for a different address → the server still answers with the configured lease and derived mask/broadcast (T6).

---

### Task 1: Repo hygiene — drop the spike, single-source the version, add ruff/pytest config and CI

**Files:**
- Delete: `spike/` (tracked tree plus caches)
- Modify: `README.md:159`, `pyproject.toml`, `.gitignore`
- Create: `.github/workflows/ci.yml`
- Test: none new; whole suite must stay green

**Interfaces:**
- Produces: `dexonlinux.__version__` stays the single source of the package version (`pyproject.toml` reads it dynamically).

- [ ] **Step 1: Remove the spike tree**

```bash
git rm -r spike
rm -rf spike
```

Verify nothing under `src/` or `tests/` imports or reads it:

```bash
grep -rn "spike" src tests scripts pyproject.toml README.md || echo clean
```

Only `README.md` (the paragraph at line 159) may match; fix it in Step 3.

- [ ] **Step 2: Single-source the version and configure ruff/pytest**

In `pyproject.toml`: replace the static `version = "1.0.2"` with

```toml
dynamic = ["version"]

[tool.setuptools.dynamic]
version = { attr = "dexonlinux.__version__" }
```

Relax `colorama==0.4.6` to `colorama>=0.4.6`. Add:

```toml
[tool.ruff]
line-length = 120
target-version = "py39"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM", "C4", "RET"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Run `.venv/bin/python -m ruff check src tests` and fix the 11 existing findings (2× E501, 2× W291 in `utils.py`; SIM105 in `helper/client.py` ×1, `nm.py`, `dhcp.py`, `nm_watchdog.py`; SIM108 in `helper/client.py`) with `contextlib.suppress` or equivalent readable form — no blanket ignores.

- [ ] **Step 3: Update `.gitignore` and README**

Add `.ruff_cache/` to `.gitignore`. In `README.md`, replace the `spike/` reference paragraph with a pointer to `docs/research/` (the research reports stay as the historical record).

- [ ] **Step 4: Add CI**

Create `.github/workflows/ci.yml` running on push/PR: matrix Python 3.9 + 3.12, `astral-sh/setup-uv` pinned like `publish.yml`, then `uv run --only-group dev ruff check src tests` and `uv run --only-group dev pytest -q`.

- [ ] **Step 5: Refresh the lockfile and verify**

```bash
uv lock && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src tests && .venv/bin/python -m build && .venv/bin/python -m twine check dist/*
```

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "chore: drop the spike, gate quality with ruff, pytest and CI"
```

---

### Task 2: Split `utils.py` into `console.py` (interaction) and `log.py` (logging)

**Files:**
- Create: `src/dexonlinux/console.py`, `src/dexonlinux/log.py`, `tests/test_console.py`
- Delete: `src/dexonlinux/utils.py`
- Modify: `src/dexonlinux/main.py`, `src/dexonlinux/scrcpy.py` (imports only)

**Interfaces:**
- Produces:
  - `console.colored(text: str, color: str = "", style: str = "") -> str`
  - `console.confirm(prompt: str, default: bool = False) -> bool`
  - `console.select_from_list(items: Sequence[T], prompt: str = "Select an item:", input_prompt: str = "> ", formatter: Callable[[T], str] = str, allow_refresh: bool = False) -> T | Literal["refresh"] | None`
  - `console.asset_path(name: str) -> Path`
  - `console.print_ascii_art() -> None`, `console.print_adb_instructions() -> None`, `console.print_dex_instructions() -> None`
  - `log.setup_logging(*, debug: bool = False, color: bool = True, log_file: str | Path | None = None) -> None`
- Consumes: `doctor.StackReport` is **not** imported here; `print_report` stays in `main.py`.

- [ ] **Step 1: Write the failing tests**

`tests/test_console.py`:

```python
def test_colored_is_identity_when_disabled(monkeypatch): ...
def test_asset_path_resolves_the_packaged_icon(): ...   # asserts .is_file() and name == "icon.png"
def test_confirm_accepts_yes_and_default(monkeypatch): ...  # monkeypatch builtins.input
def test_select_from_list_returns_the_item_and_quit(monkeypatch): ...
```

`tests/test_log.py` (same file is fine): `setup_logging(debug=False, color=False)` then assert `logging.getLogger("dexonlinux").handlers` contains exactly one stream handler and the formatted output of an ERROR record contains no `\x1b[` escape.

- [ ] **Step 2: Implement**

- `log.py` builds formatters **at call time** from the `color` flag; no class-level colored `FORMATS` dict (that bug made `--no-color` depend on import order). Attach handlers to the `"dexonlinux"` logger only; `propagate = False`; idempotent (clear previous handlers first so tests and repeated calls are safe).
- `console.py`: `colored` consults a module level `_COLOR_ENABLED` set by `setup_logging` (single source of the color decision); `asset_path` uses `importlib.resources.files("dexonlinux").joinpath("assets", name)`.
- Standardize every module on `LOG = logging.getLogger(__name__)` (`main.py` included); delete `get_logger`.
- `main.py` calls `setup_logging(debug=args.debug, color=not args.no_color, log_file=args.log_file)`.

- [ ] **Step 3: Verify and commit**

```bash
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src tests
git add -A && git commit -m "refactor: split console interaction from logging setup"
```

---

### Task 3: Typed models and device discovery

**Files:**
- Modify: `src/dexonlinux/models.py`, `src/dexonlinux/device.py`
- Test: `tests/test_device.py`

**Interfaces:**
- Produces: `AdbDevice(serial: str, state: str, description: str = "")` and `ScrcpyDisplay(display_id: int, description: str = "", width: int | None = None, height: int | None = None)` as **frozen dataclasses** with `is_authorized` property and the existing `label()`/`hint()` methods; `device.parse_adb_devices(output: str) -> list[AdbDevice]`, `device.parse_scrcpy_displays(output: str) -> list[ScrcpyDisplay]` unchanged in behavior.

- [ ] **Step 1: Add a test pinning dataclass semantics** — frozen (assigning raises `FrozenInstanceError`), positional construction kept (`ScrcpyDisplay(0, "1080x2340", 1080, 2340)`), `AdbDevice("x", "device").is_authorized is True`.
- [ ] **Step 2: Implement** — `from __future__ import annotations`, `@dataclass(frozen=True)`, module-level compiled regexes in `device.py`, remove the redundant `parts[2] if len(parts) > 2 else ""` branch.
- [ ] **Step 3: Run** `.venv/bin/python -m pytest tests/test_device.py tests/test_scrcpy.py -q`, then full suite + ruff; commit `refactor: type the device models`.

---

### Task 4: P2P core types — role enum, one group network, honest field names

**Files:**
- Modify: `src/dexonlinux/p2p/base.py`, `src/dexonlinux/p2p/events.py`
- Test: `tests/p2p/test_events.py`

**Interfaces:**
- Produces:
  - `class P2PRole(str, Enum): CLIENT = "client"; GO = "GO"` with property `go_intent -> int` (`15` for GO, `0` for CLIENT; comment: WFA `go_intent` 15/0 force the role).
  - `@dataclass(frozen=True) class GroupNetwork: server_ip: str = "192.168.49.1"; peer_ip: str = "192.168.49.2"; prefix: int = 24` with properties `netmask -> str` and `broadcast -> str` (derived with `ipaddress`, no hardcoded `192.168.49.255`).
  - `DEFAULT_P2P_FREQ = 2437` (module comment: 2.4 GHz channel 6; this is the default, `--p2p-freq` overrides and `doctor` validates) and `GO_NETWORK = GroupNetwork()`.
  - `GroupInfo`: `ifname: str, role: P2PRole, local_ip: str | None, peer_ip: str | None, local_prefix: int = 24, frequency: int | None = None, go_dev_addr: str = ""`. `peer_ip` is the other side for **both** roles (GO: DHCP lease; client: `go_ip_addr`), so consumers stop branching on `go_ip`/`client_ip`.
  - `EventKind.GO_NEGOTIATION_SUCCESS`; the parser returns it for `P2P-GO-NEG-SUCCESS` (no more `raw.startswith` in the backend).

- [ ] **Step 1: Update/extend `tests/p2p/test_events.py`** — GO-NEG-SUCCESS → `GO_NEGOTIATION_SUCCESS`; client group maps `ip_addr`→`local_ip`, `go_ip_addr`→`peer_ip`, `go_dev_addr`→`go_dev_addr`; GO group has `peer_ip is None`; malformed lines still `None`.
- [ ] **Step 2: Implement** the types and parser mapping.
- [ ] **Step 3: Fix the compile breakage in the rest of the tree** (`direct.py`, `session.py` and their tests) with the minimal mechanical change; full suite + ruff green; commit `refactor: give P2P a role enum and one group network definition`.

---

### Task 5: Direct backend and helper wiring — role, network, frequency

**Files:**
- Modify: `src/dexonlinux/p2p/direct.py`, `src/dexonlinux/helper/__main__.py`, `src/dexonlinux/helper/client.py`
- Test: `tests/p2p/test_direct.py`, `tests/helper/test_client.py`

**Interfaces:**
- Produces:
  - `DirectBackend(interface: str, ctrl: SupplicantControl, wfd: WfdConfigurator, link: LinkConfigurator, *, role: P2PRole = P2PRole.CLIENT, network: GroupNetwork = GO_NETWORK, frequency: int = DEFAULT_P2P_FREQ, dhcp: DhcpServer | None = None, before_group: Callable[[], None] | None = None)`
  - `helper.client.build_helper_command(interface: str, *, role: P2PRole, frequency: int, suspend_nm: bool, sudo_password: str | None) -> list[str]` — always `[sys.executable, "-m", "dexonlinux.helper", ...]` (comment: the helper must come from the same package/interpreter as the CLI, never from a possibly stale `dexonlinux-linkd` in `PATH`), appending `--role <role.value> --p2p-freq <frequency>`, then `--suspend-nm` when requested.
  - `HelperClient.start(interface: str, *, role: P2PRole = P2PRole.GO, frequency: int = DEFAULT_P2P_FREQ, suspend_nm: bool = False, sudo_password: str | None = None, command: Sequence[str] | None = None)`
  - Helper CLI: `--role {go,client}` default `go`, `--p2p-freq` default `DEFAULT_P2P_FREQ`; `go_intent = role.go_intent` computed once.
- Role-mismatch errors in `_group_started` name both sides, e.g. `"phone negotiated the laptop as client; GO role was requested"`.

- [ ] **Step 1: Update tests first** — `test_direct.py`: role enum kwargs, `--role` in the connect command, GO branch uses `network.peer_ip`, GO without DHCP raises, role mismatch both directions. `test_client.py`: `build_helper_command(role=P2PRole.GO, frequency=2437, ...)` contains `--role go` and never `dexonlinux-linkd` even when `shutil.which` finds one (monkeypatch it).
- [ ] **Step 2: Implement** backend + helper + client; delete the `--p2p-go` boolean and the `go_intent` int parameter.
- [ ] **Step 3: Full suite + ruff + commit** `refactor: make the P2P role and frequency explicit wiring`.

---

### Task 6: DHCP server — configured network, derived mask/broadcast, no socket race

**Files:**
- Modify: `src/dexonlinux/p2p/dhcp.py`
- Test: `tests/p2p/test_dhcp.py`

**Interfaces:**
- Produces: `DhcpServer(network: GroupNetwork = GO_NETWORK)` with `peer_ip` property; `_reply(request: bytes, message_type: int, network: GroupNetwork) -> bytes`; module constants for the DHCP message types (`DHCPDISCOVER = 1`, `DHCPOFFER = 2`, `DHCPREQUEST = 3`, `DHCPACK = 5`); `MAGIC` kept.
- `stop()` sets the flag, joins the thread (timeout 2 s), then closes the socket — same ordering that fixed the RTSP EBADF race.

- [ ] **Step 1: Update tests** — build replies from a `GroupNetwork("10.13.37.1", "10.13.37.2", 24)` and assert yiaddr/siaddr/router/mask/broadcast are derived; keep a test that a non-DHCP packet yields `None`.
- [ ] **Step 2: Implement** with `ipaddress`-derived values and the ordered `stop()`.
- [ ] **Step 3: Full suite + ruff + commit** `refactor: derive DHCP answers from the configured group network`.

---

### Task 7: Link and WFD configurables — generic rationale, self-documenting IE

**Files:**
- Modify: `src/dexonlinux/p2p/link.py`, `src/dexonlinux/p2p/wfd.py`
- Test: `tests/p2p/test_link.py` (unchanged assertions), `tests/p2p/test_wfd.py`

**Interfaces:**
- Produces: `wfd.build_device_info_ie(device_information: int = 0x0011, session_port: int = 7236, max_throughput: int = 0x00C8) -> str` returning `"00000600111c4400c8"`-style hex; `WFD_IES` becomes that string via the function; `WfdConfigurator.apply(..., device_name: str = "DexOnLinux")` unchanged.
- Deletes: dead `CLIENT_CIDR` from `link.py`.

- [ ] **Step 1: Add the failing builder test** — default equals `"00000600111c4400c8"`; a different port changes bytes 7–10 of the hex.
- [ ] **Step 2: Implement and rewrite the docstrings/comments generically** — `link.py`: the watcher exists because bringing a group netdev up *after* association makes some drivers deauth the client; `configure()` must not touch `IFF_UP` for the same reason. `wfd.py`: the IE comment decodes `id=0x00, len=0x0006, device-info bitmap, RTSP port, max throughput`; the old “miraclecast's proven defaults” wording goes.
- [ ] **Step 3: Full suite + ruff + commit** `refactor: document the WFD advertisement and generalize the link notes`.

---

### Task 8: NetworkManager suspension, supplicant client, watchdog — small hardening

**Files:**
- Modify: `src/dexonlinux/nm.py`, `src/dexonlinux/p2p/supplicant.py`, `src/dexonlinux/nm_watchdog.py`
- Test: `tests/test_nm.py`, `tests/p2p/test_supplicant.py`

**Interfaces:**
- `NetworkManagerSuspension` keeps its public API; `suspend()` registers `atexit` **once** (`self._atexit_registered`), `resume()` unregisters it.
- `nm.py` module docstring: NetworkManager owns the P2P device and removes groups it did not create; the tested NM 1.46 behavior is cited as evidence in one sentence, not as the reason the code only works there.

- [ ] **Step 1: Add a test** that `suspend(); resume(); suspend(); resume()` leaves no growing `atexit` registration (assert the guard flag flips and that double resume is a no-op).
- [ ] **Step 2: Implement** the guard, `contextlib.suppress` cleanups, and the generic wording. No functional change in the supplicant client beyond typing/readability.
- [ ] **Step 3: Full suite + ruff + commit** `refactor: harden NM suspension lifecycle and helper docs`.

---

### Task 9: RTSP sink — named timeouts, cleanup order, typed boundary

**Files:**
- Modify: `src/dexonlinux/wfd/rtsp.py`, `src/dexonlinux/session.py`
- Test: `tests/wfd/test_rtsp.py`, `tests/test_session.py`

**Interfaces:**
- Produces: `RTSP_PORT = 7236`, `READ_TIMEOUT = 1.0`, `JOIN_TIMEOUT = 2.0`, `RTP_BUFFER = 2048`; `_cleanup()` order: set stop → join RTP drain → close RTP socket → close RTSP socket (no socket closed under a blocked reader).
- `session.DexSession` takes Protocols: `HelperLike.wait_for/stop`, `ScrcpyLike.start/wait/stop`, `SinkLike.on_event/run/stop`; the factory is `Callable[[str], SinkLike]`.
- A single go/no-go field on the wire: `peer_ip` for both roles; a missing/unknown role or missing `peer_ip` raises `P2PError` with a message naming the field (Review Focus #3).
- `start_delay` keeps its 1.0 s default with a one-line why: give the source a moment to publish the DeX display before scrcpy lists displays.

- [ ] **Step 1: Update tests first** — session tests build `GroupInfo` with the new fields and assert old-key dicts (`{"role": "client", "go_ip": "..."}`) are rejected with a message containing `peer`.
- [ ] **Step 2: Implement**; keep the existing stop-race behavior (test `test_stop_closes_the_session_without_a_socket_race` stays green).
- [ ] **Step 3: Full suite + ruff + commit** `refactor: tighten the RTSP/session boundary`.

---

### Task 10: Doctor — validate the P2P frequency instead of assuming it, CLI `--p2p-freq`

**Files:**
- Modify: `src/dexonlinux/doctor.py`, `src/dexonlinux/main.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Produces: `doctor.parse_phy_frequencies(output: str) -> list[int]`; `probe_stack(runner: Runner, interface: str, *, freq: int | None = None, ctrl_dir: Path = CTRL_DIR, which: Callable[[str], str | None] = shutil.which) -> StackReport`.
- Behavior: resolve the interface phy via `iw dev <if> info`; read `iw phy <phy> info`; if frequencies were parsed and `freq or DEFAULT_P2P_FREQ` is not in them, fail with `"frequency X MHz is not supported by this adapter; pass --p2p-freq with one of: ..."`. If the phy cannot be resolved, skip the check (Review Focus #2) — never a false negative.
- CLI: `--p2p-freq` (int, MHz, default `DEFAULT_P2P_FREQ`); passed to `probe_stack` and to `HelperClient.start`. `print_report` stays; `choose_adb_device` gets a return type.

- [ ] **Step 1: Update test fixtures** — add `("iw", "dev", "wlp0s20f3", "info")` and `("iw", "phy", "phy2", "info")` entries with a `Frequencies:` block; add failing cases: channel 6 absent → unsupported with `--p2p-freq` in the reason; missing wiphy line → no frequency reason.
- [ ] **Step 2: Implement** doctor + CLI flag.
- [ ] **Step 3: Full suite + ruff + commit** `feat: validate the P2P frequency in doctor and expose it in the CLI`.

---

### Task 11: README and current docs — generic language, new flag, no spike

**Files:**
- Modify: `README.md`
- Test: none (prose)

- [ ] **Step 1: Rewrite the chip-specific paragraphs** — “on the Intel AX201 the reverse role crashed” becomes a known-issue note with a link to the live-acceptance report; the requirements section mentions that `--p2p-freq` defaults to 2437 MHz and that `--doctor` validates it against the adapter.
- [ ] **Step 2: Document the flags** (`--p2p-freq`, helper `--role` for diagnostics) and remove the spike pointer.
- [ ] **Step 3: Commit** `docs: describe the generic frequency validation and drop the spike`.

---

### Task 12: End-to-end verification

- [ ] **Step 1:** `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check src tests`
- [ ] **Step 2:** `.venv/bin/python -m build && .venv/bin/python -m twine check dist/*`
- [ ] **Step 3:** `.venv/bin/dexonlinux --doctor` (read-only; must report `Supported: yes` on this host and mention the validated frequency path).
- [ ] **Step 4:** `.venv/bin/dexonlinux --help` and `.venv/bin/python -m dexonlinux.helper --help` smoke.
- [ ] **Step 5:** Update `dexonlinux-handoff.md` status; commit any remaining changes; report the commit list.

## Self-Review

- Spec coverage: cleanup/architecture → T2–T9; why-comments → T7–T9; generic host flow → T6–T10; spike removal → T1; best practices → T1–T3; docs → T11; verification → T12.
- Every renamed interface is defined in exactly one task and consumed by name in later tasks (T4 → T5/T9; T6 → T5; T7 → T5).
- No task changes live networking behavior of the GO path except the doctor gate and the frequency flag; the live session must be re-run by the user after the refactor before release.
