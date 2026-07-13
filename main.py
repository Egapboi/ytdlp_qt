"""
main.py — Entry point for ytdlp-qt.

Creates the QApplication with High-DPI scaling configured, applies
the global dark stylesheet, and shows the main window.
"""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from main_window import DARK_STYLE, MainWindow


def main() -> None:
    # ── High-DPI scaling (must be set BEFORE QApplication) ────
    # PyQt6 enables AA_UseHighDpiPixmaps by default, but we
    # explicitly set the env-var rounding policy so fractional
    # scaling (125 %, 150 %) produces correct widget geometry
    # on every platform.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

    app = QApplication(sys.argv)
    app.setApplicationName("ytdlp-qt")
    app.setStyle("Fusion")  # cross-platform base for consistent QSS

    # Cross-platform font: try platform-native families first, then
    # fall back to a generic sans-serif so Linux/macOS look native.
    font = QFont()
    font.setFamilies(["Segoe UI", "SF Pro Text", "Cantarell", "Noto Sans", "sans-serif"])
    font.setPointSize(10)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)

    # Apply dark theme
    app.setStyleSheet(DARK_STYLE)

    window = MainWindow()
    window.showMaximized()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
