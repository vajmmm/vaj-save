from pathlib import Path
from vajsave.backend import MountedVolumeBackend, FakeStorageBackend
from vajsave.volume import VolumeInfo, FakeVolumeProvider
from vajsave.scanner import scan


def test_fake_storage_backend(tmp_path: Path):
    path1 = tmp_path / "root1"
    path2 = tmp_path / "root2"
    path1.mkdir()
    path2.mkdir()

    backend = FakeStorageBackend([path1, path2])
    roots = list(backend.iter_roots())
    assert len(roots) == 2
    assert roots[0] == path1
    assert roots[1] == path2


def test_mounted_volume_backend(tmp_path: Path):
    vol1 = VolumeInfo(name="V1", mount_point=tmp_path / "vol1")
    vol2 = VolumeInfo(name="V2", mount_point=tmp_path / "vol2")
    provider = FakeVolumeProvider([vol1, vol2])
    backend = MountedVolumeBackend(provider=provider)

    roots = list(backend.iter_roots())
    assert len(roots) == 2
    assert roots[0] == tmp_path / "vol1"
    assert roots[1] == tmp_path / "vol2"
