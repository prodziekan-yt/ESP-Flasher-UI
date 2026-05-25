"""ESP Flasher UI - entry point."""
import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from app import ICON_PATH
from app.i18n import I18n
from app.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("ESP Flasher UI")
    app.setOrganizationName("ESP-Flasher-UI")
    app.setDesktopFileName("esp-flasher-ui")

    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    i18n = I18n()
    window = MainWindow(i18n)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
