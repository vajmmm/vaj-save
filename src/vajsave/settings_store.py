"""Read and write the app-level ``config.json``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .library import config_path, load_app_config, save_app_config


class SettingsStore:
    """Thin wrapper around ``load_app_config`` / ``save_app_config``.

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
