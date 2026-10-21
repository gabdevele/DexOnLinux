from dexonlinux.device import Device, parse_adb_devices, parse_scrcpy_displays
from tests.fakes import FakeRunner


def test_parse_adb_devices_skips_header_and_daemon_noise():
    devices = parse_adb_devices(
        "List of devices attached\n"
        "R58M1234\tdevice product:dreamlte model:SM_G950F device:dreamlte\n"
        "emulator-5554\toffline\n"
        "* daemon not running; starting now at tcp:5037\n"
    )
    assert [device.serial for device in devices] == ["R58M1234", "emulator-5554"]
    assert devices[0].is_authorized is True
    assert devices[1].is_authorized is False


def test_parse_scrcpy_displays_prefers_landscape_hint():
    displays = parse_scrcpy_displays(
        "--display-id=13 (1920x1080) ...\n--display-id=0 (1080x2400) ..."
    )
    assert displays[0].display_id == 13
    assert displays[0].hint().startswith("likely DeX")
    assert displays[1].display_id == 0
    assert displays[1].hint() == "likely phone screen"


def test_device_lists_adb_and_scrcpy_outputs():
    runner = FakeRunner(
        outputs={
            ("adb", "devices", "-l"): "R58M1234\tdevice\n",
            ("scrcpy", "-s", "R58M1234", "--list-displays"): "--display-id=13 (1920x1080)\n",
        }
    )
    device = Device(runner)
    assert device.list_adb_devices()[0].serial == "R58M1234"
    assert device.list_scrcpy_displays("R58M1234")[0].display_id == 13
    assert [call[0] for call in runner.calls] == [
        ("adb", "devices", "-l"),
        ("scrcpy", "-s", "R58M1234", "--list-displays"),
    ]
