from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "vaj-save.spec"
ENTRY = ROOT / "packaging" / "entrypoint.py"
BUILD_SCRIPT = ROOT / "scripts" / "build-macos-app.sh"
WIN_SPEC = ROOT / "packaging" / "vaj-save-windows.spec"
WIN_PS1 = ROOT / "scripts" / "build-windows-app.ps1"
WIN_BAT = ROOT / "scripts" / "build-windows-app.bat"


def test_macos_app_spec_is_windowed_bundle():
    text = SPEC.read_text(encoding="utf-8")
    assert 'name="vaj-save.app"' in text
    assert "console=False" in text
    assert "BUNDLE(" in text
    assert "local.vajsave.app" in text


def test_freeze_entrypoint_uses_desktop_main():
    source = ENTRY.read_text(encoding="utf-8")
    assert "from vajsave.app import main" in source
    assert "raise SystemExit(main())" in source


def test_build_script_invokes_pyinstaller_spec():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "PyInstaller" in text
    assert "packaging/vaj-save.spec" in text
    assert "dist/vaj-save.app" in text


def test_windows_spec_is_windowed_onedir():
    text = WIN_SPEC.read_text(encoding="utf-8")
    assert "console=False" in text
    assert "BUNDLE(" not in text
    assert 'name="vaj-save"' in text
    assert "COLLECT(" in text


def test_windows_build_scripts_invoke_windows_spec():
    ps1 = WIN_PS1.read_text(encoding="utf-8")
    bat = WIN_BAT.read_text(encoding="utf-8")
    assert "vaj-save-windows.spec" in ps1
    assert "vaj-save-windows.spec" in bat
    assert "vaj-save.exe" in ps1
    assert "vaj-save.exe" in bat


def test_specs_bundle_the_metadata_index():
    for spec in (SPEC, WIN_SPEC):
        text = spec.read_text(encoding="utf-8")
        assert '"data"' in text
        assert 'src" / "vajsave" / "data' in text


def test_bundled_index_is_readable_as_a_package_resource():
    from importlib import resources

    data = resources.files("vajsave.data").joinpath("libretro")
    text = data.joinpath("gba.json").read_text(encoding="utf-8")
    assert "vajsave.libretro-index" in text
    assert data.joinpath("provenance.json").is_file()
    assert data.joinpath("TERMS-No-Intro.txt").is_file()


def test_wheel_contains_bundled_index(tmp_path):
    import subprocess
    import sys
    import zipfile

    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    wheels = list(tmp_path.glob("vajsave-*.whl"))
    assert wheels, "wheel was not produced"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
    assert "vajsave/data/libretro/gba.json" in names
    assert "vajsave/data/libretro/nds.json" in names
    assert "vajsave/data/libretro/provenance.json" in names
    assert "vajsave/data/libretro/TERMS-No-Intro.txt" in names
