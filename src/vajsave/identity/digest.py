"""Stable, streaming digests used for ROM identity keys.

SHA-1 is the canonical identity key; CRC32 is kept alongside because users
recognise it and it is cheap to compute in the same streaming pass.
"""

from __future__ import annotations

import hashlib
import zlib
from pathlib import Path
from typing import Tuple, Union

_CHUNK_SIZE = 1024 * 1024


def digest_file(path: Union[Path, str], chunk_size: int = _CHUNK_SIZE) -> Tuple[str, str]:
    """Return ``(sha1_hex, crc32_hex)`` for a file, read in bounded chunks."""
    sha1 = hashlib.sha1()
    crc = 0
    with Path(path).open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            sha1.update(block)
            crc = zlib.crc32(block, crc)
    return sha1.hexdigest(), format(crc & 0xFFFFFFFF, "08x")


def sha1_file(path: Union[Path, str], chunk_size: int = _CHUNK_SIZE) -> str:
    return digest_file(path, chunk_size)[0]


def crc32_file(path: Union[Path, str], chunk_size: int = _CHUNK_SIZE) -> str:
    return digest_file(path, chunk_size)[1]
