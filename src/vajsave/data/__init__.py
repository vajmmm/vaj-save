"""Bundled data files for the metadata layer.

The only data the package ships by default is the (empty) ``libretro`` folder,
whose presence guarantees a stable location inside a frozen application.
Real libretro / No-Intro ``.dat`` indexes are large and are supplied by the user
(see ``README.md`` next to this file and ``vajsave.metadata.paths``).
"""

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent

__all__ = ["DATA_DIR"]
