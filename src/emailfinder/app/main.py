import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from PySide6.QtWidgets import QApplication
    from emailfinder.ui.main_window import MainWindow
    app = QApplication(sys.argv)
    window = MainWindow(project_root())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

