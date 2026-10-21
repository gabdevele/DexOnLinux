from dexonlinux.models import ScrcpyDisplay
from dexonlinux.scrcpy import choose_display

PHONE = ScrcpyDisplay(0, "1080x2340", 1080, 2340)
DEX = ScrcpyDisplay(19, "1920x1080", 1920, 1080)
TV = ScrcpyDisplay(21, "1920x1080", 1920, 1080)


def test_unique_landscape_display_is_auto_selected():
    selected = choose_display([PHONE, DEX], explicit=None)
    assert selected is DEX


def test_stale_explicit_id_falls_back_to_landscape():
    selected = choose_display([PHONE, DEX], explicit=17)
    assert selected is DEX


def test_valid_explicit_id_wins():
    selected = choose_display([PHONE, DEX], explicit=PHONE.display_id)
    assert selected is PHONE


def test_multiple_landscape_displays_need_a_prompt():
    assert choose_display([PHONE, DEX, TV], explicit=None) is None


def test_no_landscape_display_needs_a_prompt():
    assert choose_display([PHONE], explicit=None) is None
