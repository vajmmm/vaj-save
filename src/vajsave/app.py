import sys
from typing import Optional

from .app_state import AppState


def main(state: Optional[AppState] = None) -> int:
    """启动 vaj-save 的 Qt 桌面应用。"""
    from .qt_ui import run_app

    app_state = state or AppState()
    try:
        return run_app(app_state)
    finally:
        app_state.stop_watch(timeout=0.5)


if __name__ == "__main__":
    sys.exit(main())
