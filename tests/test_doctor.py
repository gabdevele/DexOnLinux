from dexonlinux.doctor import StackReport, list_p2p_interfaces, parse_phy_frequencies, probe_stack
from dexonlinux.main import print_report
from dexonlinux.p2p.dbus import DBusProbe
from tests.fakes import FakeRunner


def outputs(
    *,
    nm="active",
    iwd="inactive",
    connman="inactive",
    modes=True,
    frequencies=(2412, 2437, 2462),
    wiphy=True,
):
    commands = {
        ("systemctl", "is-active", "NetworkManager"): f"{nm}\n",
        ("systemctl", "is-active", "iwd"): f"{iwd}\n",
        ("systemctl", "is-active", "connman"): f"{connman}\n",
        ("iw", "dev"): (
            "phy#2\n"
            "\tUnnamed/non-netdev interface\n"
            "\t\ttype P2P-device\n"
            "\tInterface wlp0s20f3\n"
            "\t\ttype managed\n"
        ),
        ("iw", "list"): (
            "Supported interface modes:\n * P2P-client\n * P2P-GO\n"
            if modes
            else "Supported interface modes:\n * managed\n"
        ),
    }
    if wiphy:
        commands[("iw", "dev", "wlp0s20f3", "info")] = "Interface wlp0s20f3\n\twiphy 2\n"
        capabilities = " * P2P-client\n * P2P-GO\n" if modes else " * managed\n"
        if frequencies is not None:
            capabilities += "Frequencies:\n" + "".join(
                f"\t\t* {value} MHz [{index}] (20.0 dBm)\n" for index, value in enumerate(frequencies, start=1)
            )
        commands[("iw", "phy", "phy2", "info")] = capabilities
    return commands


def which_all(name):
    return f"/usr/bin/{name}"


def stack(runner, interface, **kwargs):
    """Run probe_stack without touching the real system bus."""
    return probe_stack(runner, interface, dbus_probe=lambda _: DBusProbe(False, "no bus"), **kwargs)


def test_network_manager_direct_stack_is_supported_with_suspend(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()
    report = stack(
        FakeRunner(outputs()),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
    )

    assert report.supported is True
    assert report.backend == "direct"
    assert report.manager == "NetworkManager"
    assert report.suspend_nm is True
    assert report.reasons == ()


def test_standalone_p2p_is_supported_without_suspend(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()
    report = stack(
        FakeRunner(outputs(nm="inactive")),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
    )

    assert report.supported is True
    assert report.manager == "standalone"
    assert report.suspend_nm is False


def test_nm_iwd_is_unsupported(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()
    report = stack(
        FakeRunner(outputs(iwd="active")),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
    )

    assert report.supported is False
    assert "iwd" in " ".join(report.reasons).lower()


def test_connman_is_unsupported(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()
    report = stack(
        FakeRunner(outputs(nm="inactive", connman="active")),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
    )

    assert report.supported is False
    assert "connman" in " ".join(report.reasons).lower()


def test_missing_p2p_ctrl_socket_is_unsupported(tmp_path):
    report = stack(
        FakeRunner(outputs()),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
    )

    assert report.supported is False
    assert "control socket" in " ".join(report.reasons)


def test_missing_p2p_modes_is_unsupported(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()
    report = stack(
        FakeRunner(outputs(modes=False)),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
    )

    assert report.supported is False
    assert "P2P-client" in " ".join(report.reasons)


def test_another_phy_supporting_p2p_does_not_mask_the_selected_interface(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()
    commands = outputs()
    commands[("iw", "phy", "phy2", "info")] = " * managed\n"
    commands[("iw", "list")] = "Supported interface modes:\n * P2P-client\n * P2P-GO\n"
    report = stack(FakeRunner(commands), "wlp0s20f3", ctrl_dir=tmp_path, which=which_all)

    assert report.supported is False
    assert "P2P-client" in " ".join(report.reasons)


def test_missing_session_commands_is_unsupported(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()

    def which(name):
        return None if name == "adb" else f"/usr/bin/{name}"

    report = stack(
        FakeRunner(outputs()),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which,
    )

    assert report.supported is False
    assert "adb" in " ".join(report.reasons)


def test_unsupported_frequency_is_reported(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()
    report = stack(
        FakeRunner(outputs(frequencies=(5180, 5200))),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
    )

    assert report.supported is False
    reasons = " ".join(report.reasons)
    assert "2437" in reasons
    assert "--p2p-freq" in reasons


def test_requested_frequency_is_validated(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()
    report = stack(
        FakeRunner(outputs(frequencies=(5180, 5200))),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
        freq=5180,
    )

    assert report.supported is True


def test_missing_wiphy_skips_the_frequency_check(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()
    report = stack(
        FakeRunner(outputs(frequencies=(5180,), wiphy=False)),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
    )

    assert report.supported is True


def test_parse_phy_frequencies_reads_only_usable_channels():
    parsed = parse_phy_frequencies(
        "Frequencies:\n"
        "\t\t* 2412 MHz [1] (20.0 dBm)\n"
        "\t\t* 2437 MHz [6] (20.0 dBm)\n"
        "\t\t* 2472 MHz [13] (disabled)\n"
        "\t\t* 5180 MHz [36] (20.0 dBm)\n"
        "\t\t* 5260 MHz [52] (no IR)\n"
    )
    assert parsed == [2412, 2437, 5180]


def test_unparseable_channel_list_skips_the_check_with_a_note(tmp_path, monkeypatch):
    notes = []
    monkeypatch.setattr("dexonlinux.doctor.LOG.debug", lambda template, *args: notes.append(template % args))
    (tmp_path / "p2p-dev-wlp0s20").touch()
    outputs_map = outputs(frequencies=None)
    outputs_map[("iw", "phy", "phy2", "info")] = (
        " * P2P-client\n * P2P-GO\nFrequencies:\n\t* 2402 - 2482 MHz (20.0 dBm)\n"
    )
    report = stack(FakeRunner(outputs_map), "wlp0s20f3", ctrl_dir=tmp_path, which=which_all)

    assert report.supported is True
    assert any("frequency check" in note for note in notes)


def test_list_p2p_interfaces_checks_the_phy_modes(tmp_path):
    (tmp_path / "wlp0s20f3").mkdir()
    (tmp_path / "wlp0s20f3" / "wireless").mkdir()
    (tmp_path / "lo").mkdir()
    runner = FakeRunner(
        outputs={
            ("iw", "dev", "wlp0s20f3", "info"): "Interface wlp0s20f3\n\twiphy 2\n",
            ("iw", "phy", "phy2", "info"): " * P2P-client\n * P2P-GO\n",
        }
    )

    assert list_p2p_interfaces(runner, net_dir=tmp_path) == ["wlp0s20f3"]


def test_list_p2p_interfaces_ignores_non_wireless_and_unsupported_phys(tmp_path):
    (tmp_path / "eth0").mkdir()
    (tmp_path / "wlp-test").mkdir()
    (tmp_path / "wlp-test" / "wireless").mkdir()
    runner = FakeRunner(
        outputs={
            ("iw", "dev", "wlp-test", "info"): "Interface wlp-test\n\twiphy 3\n",
            ("iw", "phy", "phy3", "info"): " * managed\n",
        }
    )

    assert list_p2p_interfaces(runner, net_dir=tmp_path) == []


def test_doctor_reports_dbus_transport(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()
    report = probe_stack(
        FakeRunner(outputs()),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
        dbus_probe=lambda _: DBusProbe(True),
    )

    assert report.transport == "dbus"
    assert report.supported is True
    assert report.notes == ()


def test_doctor_falls_back_to_ctrl_with_note(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()
    report = probe_stack(
        FakeRunner(outputs()),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
        dbus_probe=lambda _: DBusProbe(False, "no WFDIEs"),
    )

    assert report.transport == "ctrl"
    assert report.supported is True
    assert any("WFDIEs" in note for note in report.notes)


def test_doctor_reports_unknown_when_permission_denied(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()
    report = probe_stack(
        FakeRunner(outputs()),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
        dbus_probe=lambda _: DBusProbe(None, "AccessDenied"),
    )

    assert report.transport == "unknown"
    assert report.supported is True
    assert any("AccessDenied" in note for note in report.notes)


def test_doctor_reports_missing_wfd(tmp_path):
    (tmp_path / "p2p-dev-wlp0s20").touch()
    report = probe_stack(
        FakeRunner(outputs()),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
        dbus_probe=lambda _: DBusProbe(False, "built without CONFIG_WIFI_DISPLAY"),
    )

    assert report.transport == "ctrl"
    assert "CONFIG_WIFI_DISPLAY" in " ".join(report.notes)


def test_doctor_fails_when_no_transport_is_usable(tmp_path):
    report = probe_stack(
        FakeRunner(outputs()),
        "wlp0s20f3",
        ctrl_dir=tmp_path,
        which=which_all,
        dbus_probe=lambda _: DBusProbe(False, "no bus"),
    )

    assert report.transport == "none"
    assert report.supported is False
    assert "no usable P2P transport" in " ".join(report.reasons)


def test_print_report_shows_transport_and_notes(capsys):
    report = StackReport(
        manager="NetworkManager",
        backend="direct",
        supported=True,
        reasons=(),
        transport="dbus",
        notes=("a note",),
    )

    print_report("wlan0", report)

    output = capsys.readouterr().out
    assert "P2P transport: dbus" in output
    assert "a note" in output


def test_parse_phy_frequencies_accepts_decimal_mhz():
    parsed = parse_phy_frequencies(
        "\t\t\tFrequencies:\n"
        "\t\t\t\t* 2412.0 MHz [1] (22.0 dBm)\n"
        "\t\t\t\t* 2437.0 MHz [6] (22.0 dBm)\n"
        "\t\t\t\t* 5180.0 MHz [36] (22.0 dBm) (no IR)\n"
    )
    assert parsed == [2412, 2437]
