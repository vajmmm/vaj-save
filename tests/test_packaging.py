from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "vaj-save.spec"
ENTRY = ROOT / "packaging" / "entrypoint.py"
BUILD_SCRIPT = ROOT / "scripts" / "build-macos-app.sh"


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
