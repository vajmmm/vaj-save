import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

APP_CONFIG_NAME = "config.json"
APP_DIR_NAME = "vaj-save"
APP_CONFIG_ENV = "VAJSAVE_CONFIG_PATH"


def default_library_root() -> Path:
    return Path.home() / "Documents" / "vaj-save"


def config_path() -> Path:
    """Location of the application config file (never inside the library root).

    ``VAJSAVE_CONFIG_PATH`` overrides everything (used by tests).
    """
    override = os.environ.get(APP_CONFIG_ENV)
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        base_path = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return base_path / APP_DIR_NAME / APP_CONFIG_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME / APP_CONFIG_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base_path = Path(xdg) if xdg else Path.home() / ".config"
    return base_path / APP_DIR_NAME / APP_CONFIG_NAME


def load_app_config() -> Dict[str, Any]:
    """Read the app config. Missing/corrupt/unreadable files degrade to {}."""
    path = config_path()
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_app_config(config: Dict[str, Any]) -> bool:
    """Persist the app config atomically. Returns False on write failure."""
    path = config_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except (OSError, TypeError, ValueError):
        # TypeError/ValueError guard against a non-serialisable caller payload;
        # drop any half-written temp file so it cannot linger next to the config.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


class SettingsStore:
    """Wrapper around ``load_app_config`` / ``save_app_config``.

    Unknown keys pass through. ``update`` merges into the existing dict and
    persists it, including keys AppState does not yet read (e.g.
    ``auto_backup_on_insert``).
    """

    def load(self) -> dict:
        return load_app_config()

    def save(self, data: dict) -> bool:
        return save_app_config(data)

    def config_dir(self) -> Path:
        return config_path().parent

    def update(self, **keys: Any) -> dict:
        data = self.load()
        data.update(keys)
        self.save(data)
        return data
