from __future__ import annotations

import os
import sys



def resource_base() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from app.config import ConfigStore
    from app.ui.main_window import MainWindow
    from app.version import APP_NAME, APP_AUTHOR, APP_VERSION

    config_store = ConfigStore()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_AUTHOR)

    base = resource_base()
    resources = os.path.join(base, "resources")
    icon_path = os.path.join(resources, "icon.ico")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    style_path = os.path.join(resources, "style.qss")
    if os.path.isfile(style_path):
        with open(style_path, "r", encoding="utf-8") as handle:
            app.setStyleSheet(handle.read())

    window = MainWindow(resources, config_store=config_store)
    if "--startup" not in sys.argv[1:]:
        window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
