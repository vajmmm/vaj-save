"""Frozen macOS .app entrypoint. Do not run this as a library module."""

from vajsave.app import main


if __name__ == "__main__":
    raise SystemExit(main())
