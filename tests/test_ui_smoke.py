import os
from pathlib import Path


def test_desktop_window_constructs_offscreen(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "ui.db"))
    from PySide6.QtWidgets import QApplication
    from emailfinder.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(Path(__file__).parents[1])
    assert window.windowTitle() == "EmailFinder — Phase 1 Mock"
    assert window.table.columnCount() == 10
    window.close()
    app.processEvents()

