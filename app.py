"""Meta Zone — entry point.

Run this file directly (`python app.py`) or build it with PyInstaller
(see .github/workflows/build.yml). All real logic lives in core/,
engine/, and ui/ — this file just wires the theme up and starts the
main window.
"""
from ui.theme import init_ctk_theme
from ui.main_window import App


def main():
    init_ctk_theme()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
