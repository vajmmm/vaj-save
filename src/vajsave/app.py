import sys
from typing import Optional

from .app_state import AppState
from .app_ui import build_app


def main(state: Optional[AppState] = None) -> int:
    """Launch the vaj-save Tkinter desktop application."""
    app_state = state or AppState()
    app = build_app(state=app_state)
    try:
        app.root.mainloop()
    finally:
        app_state.stop_watch(timeout=0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
