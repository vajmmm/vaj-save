"""Frozen desktop entrypoint for macOS .app and Windows .exe."""

from vajsave.app import main


if __name__ == "__main__":
    raise SystemExit(main())
