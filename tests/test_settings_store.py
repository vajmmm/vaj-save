from vajsave.settings_store import SettingsStore


def test_settings_store_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("VAJSAVE_CONFIG_PATH", str(tmp_path / "config.json"))
    store = SettingsStore()
    store.update(library_root=str(tmp_path / "lib"), auto_backup_on_insert=False)
    loaded = SettingsStore().load()
    assert loaded["library_root"] == str(tmp_path / "lib")
    assert loaded["auto_backup_on_insert"] is False
