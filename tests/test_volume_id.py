from vajsave.volume_id import format_windows_serial, volume_id_for


def test_format_windows_serial_is_stable_hex():
    assert format_windows_serial(0xABCD1234) == "win:ABCD1234"


def test_volume_id_for_missing_returns_none(tmp_path):
    assert volume_id_for(tmp_path) is None


def test_volume_id_for_uses_extra_without_usb_adapter_fields(tmp_path):
    assert volume_id_for(tmp_path, extra={"volume_id": "win:DEADBEEF", "usb": "vid:pid"}) == "win:DEADBEEF"
