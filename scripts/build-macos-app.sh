#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script builds a macOS .app on Darwin only." >&2
  exit 1
fi

"$PYTHON" -m pip install -e ".[packaging]"
"$PYTHON" -m PyInstaller --noconfirm --clean "$ROOT/packaging/vaj-save.spec"

APP="$ROOT/dist/vaj-save.app"
if [[ ! -d "$APP" ]]; then
  echo "Build failed: $APP not found" >&2
  exit 1
fi

echo "Built: $APP"
echo "Open with: open \"$APP\""
