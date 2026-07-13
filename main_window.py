"""
main_window.py — PyQt6 user interface for ytdlp-qt.

Single-window layout with URL input, mode toggle, dynamic format/quality
selectors, output directory picker, download button, progress bar, and
status log.  Supports playlist detection with a folder-creation dialog.
Uses a dark theme via QSS.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from downloader import DownloadWorker, FetchWorker

# ──────────────────────────────────────────────
#  Dark-theme stylesheet
# ──────────────────────────────────────────────

DARK_STYLE = """
/* ── Global ─────────────────────────────── */
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: "Segoe UI", "SF Pro Text", "Cantarell", "Noto Sans", sans-serif;
}

/* ── Main Window ────────────────────────── */
QMainWindow {
    background-color: #1e1e2e;
}

/* ── Group Boxes ────────────────────────── */
QGroupBox {
    border: 1px solid #45475a;
    border-radius: 8px;
    margin-top: 12px;
    padding: 14px 10px 10px 10px;
    font-weight: bold;
    color: #cba6f7;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
}

/* ── Line Edits ─────────────────────────── */
QLineEdit {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px 12px;
    color: #cdd6f4;
    selection-background-color: #cba6f7;
}
QLineEdit:focus {
    border: 1px solid #cba6f7;
}
QLineEdit:read-only {
    background-color: #2a2a3c;
    color: #a6adc8;
}

/* ── Combo Boxes ────────────────────────── */
QComboBox {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 6px 12px;
    color: #cdd6f4;
}
QComboBox:hover {
    border: 1px solid #cba6f7;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #cba6f7;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 4px;
    selection-background-color: #45475a;
    color: #cdd6f4;
    padding: 4px;
}

/* ── Buttons ────────────────────────────── */
QPushButton {
    background-color: #cba6f7;
    color: #1e1e2e;
    border: none;
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #b48af0;
}
QPushButton:pressed {
    background-color: #9d6ee6;
}
QPushButton:disabled {
    background-color: #45475a;
    color: #6c7086;
}

/* ── Secondary Buttons ──────────────────── */
QPushButton#btn_browse, QPushButton#btn_fetch {
    background-color: #45475a;
    color: #cdd6f4;
}
QPushButton#btn_browse:hover, QPushButton#btn_fetch:hover {
    background-color: #585b70;
}

/* ── Progress Bar ───────────────────────── */
QProgressBar {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 6px;
    text-align: center;
    color: #cdd6f4;
}
QProgressBar::chunk {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #cba6f7, stop:1 #89b4fa
    );
    border-radius: 5px;
}

/* ── Status Log ─────────────────────────── */
QPlainTextEdit#status_log {
    background-color: #181825;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px;
    color: #a6adc8;
    font-family: "Cascadia Mono", "JetBrains Mono", "Consolas", "Menlo", monospace;
}

/* ── Labels ─────────────────────────────── */
QLabel#lbl_title {
    font-weight: bold;
    color: #cdd6f4;
}
QLabel#lbl_duration {
    color: #a6adc8;
}
QLabel#lbl_header {
    font-size: 14pt;
    font-weight: bold;
    color: #cba6f7;
}
QLabel#lbl_status {
    color: #89b4fa;
    padding: 2px 0px;
}
QLabel#lbl_ffmpeg_notice {
    color: #f9e2af;
    padding: 4px 0px;
}

/* ── Message Box (dark-themed) ──────────── */
QMessageBox {
    background-color: #1e1e2e;
}
QMessageBox QLabel {
    color: #cdd6f4;
}
QMessageBox QPushButton {
    min-width: 90px;
}
"""

# ──────────────────────────────────────────────
#  MainWindow
# ──────────────────────────────────────────────

VIDEO_FORMATS = ["MP4", "MKV", "WebM"]
AUDIO_FORMATS = ["MP3", "M4A", "WAV"]

DEFAULT_VIDEO_QUALITIES = ["Best", "2160p", "1440p", "1080p", "720p", "480p", "360p"]
DEFAULT_AUDIO_QUALITIES = ["Best", "320kbps", "256kbps", "192kbps", "128kbps"]


class MainWindow(QMainWindow):
    """Primary application window for ytdlp-qt."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ytdlp-qt")
        self.resize(720, 580)
        self.setMinimumSize(480, 420)

        # State
        self._fetched_info: dict | None = None
        self._fetch_worker: FetchWorker | None = None
        self._download_worker: DownloadWorker | None = None

        self._build_ui()
        self._connect_signals()
        self._update_options_for_mode()

    # ──────────────────────────────────────────
    #  UI Construction
    # ──────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(20, 16, 20, 16)
        root_layout.setSpacing(12)

        # Header
        lbl_header = QLabel("ytdlp-qt")
        lbl_header.setObjectName("lbl_header")
        lbl_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root_layout.addWidget(lbl_header)

        # ── URL Group ────────────────────────
        url_group = QGroupBox("URL")
        url_layout = QHBoxLayout(url_group)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube URL here…")
        url_layout.addWidget(self.url_input, stretch=1)

        self.btn_fetch = QPushButton("Fetch")
        self.btn_fetch.setObjectName("btn_fetch")
        url_layout.addWidget(self.btn_fetch)

        root_layout.addWidget(url_group)

        # ── Video Info ───────────────────────
        info_group = QGroupBox("Video Info")
        info_layout = QVBoxLayout(info_group)

        self.lbl_title = QLabel("—")
        self.lbl_title.setObjectName("lbl_title")
        self.lbl_title.setWordWrap(True)
        info_layout.addWidget(self.lbl_title)

        self.lbl_duration = QLabel("")
        self.lbl_duration.setObjectName("lbl_duration")
        info_layout.addWidget(self.lbl_duration)

        root_layout.addWidget(info_group)

        # ── Options Group ────────────────────
        opts_group = QGroupBox("Options")
        opts_layout = QHBoxLayout(opts_group)
        opts_layout.setSpacing(16)

        # Mode
        mode_col = QVBoxLayout()
        mode_col.addWidget(QLabel("Mode"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Video", "Audio"])
        mode_col.addWidget(self.combo_mode)
        opts_layout.addLayout(mode_col)

        # Format
        fmt_col = QVBoxLayout()
        fmt_col.addWidget(QLabel("Format"))
        self.combo_format = QComboBox()
        fmt_col.addWidget(self.combo_format)
        opts_layout.addLayout(fmt_col)

        # Quality
        qual_col = QVBoxLayout()
        qual_col.addWidget(QLabel("Quality"))
        self.combo_quality = QComboBox()
        qual_col.addWidget(self.combo_quality)
        opts_layout.addLayout(qual_col)

        root_layout.addWidget(opts_group)

        # ── Output Directory ─────────────────
        dir_group = QGroupBox("Output Directory")
        dir_layout = QHBoxLayout(dir_group)

        default_downloads = str(Path.home() / "Downloads")
        self.dir_input = QLineEdit(default_downloads)
        self.dir_input.setReadOnly(True)
        dir_layout.addWidget(self.dir_input, stretch=1)

        self.btn_browse = QPushButton("Browse…")
        self.btn_browse.setObjectName("btn_browse")
        dir_layout.addWidget(self.btn_browse)

        root_layout.addWidget(dir_group)

        # ── Download Button ──────────────────
        self.btn_download = QPushButton("Download")
        self.btn_download.setEnabled(False)
        self.btn_download.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        font = self.btn_download.font()
        font.setPointSize(12)
        self.btn_download.setFont(font)
        root_layout.addWidget(self.btn_download)

        # ── Progress ─────────────────────────
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        root_layout.addWidget(self.progress_bar)

        # ── Live Status Label ────────────────
        # Dedicated single-line label for real-time speed / ETA.
        # Updated on every progress tick without touching the log.
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("lbl_status")
        root_layout.addWidget(self.lbl_status)

        # ── Status Log ───────────────────────
        self.status_log = QPlainTextEdit()
        self.status_log.setObjectName("status_log")
        self.status_log.setReadOnly(True)
        self.status_log.setPlaceholderText("Status messages will appear here…")
        root_layout.addWidget(self.status_log, stretch=1)  # stretches with window

        # ── ffmpeg notice (shown on non-Windows platforms) ──
        if platform.system() != "Windows":
            lbl_ffmpeg = QLabel(
                "ℹ  ffmpeg / ffprobe required for audio extraction "
                "and high-quality format merging.  "
                "Install via: brew install ffmpeg  (macOS) · "
                "sudo apt install ffmpeg  (Debian/Ubuntu)"
            )
            lbl_ffmpeg.setObjectName("lbl_ffmpeg_notice")
            lbl_ffmpeg.setWordWrap(True)
            root_layout.addWidget(lbl_ffmpeg)

    # ──────────────────────────────────────────
    #  Signal / Slot Wiring
    # ──────────────────────────────────────────

    def _connect_signals(self) -> None:
        self.btn_fetch.clicked.connect(self._on_fetch)
        self.url_input.returnPressed.connect(self._on_fetch)
        self.btn_browse.clicked.connect(self._on_browse)
        self.btn_download.clicked.connect(self._on_download)
        self.combo_mode.currentTextChanged.connect(self._update_options_for_mode)

    # ──────────────────────────────────────────
    #  Slot Implementations
    # ──────────────────────────────────────────

    def _log(self, msg: str) -> None:
        self.status_log.appendPlainText(msg)

    def _on_browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Output Directory",
            self.dir_input.text(),
        )
        if directory:
            self.dir_input.setText(directory)

    def _update_options_for_mode(self) -> None:
        """Repopulate Format and Quality combo boxes based on the selected mode."""
        mode = self.combo_mode.currentText()

        self.combo_format.clear()
        self.combo_quality.clear()

        if mode == "Video":
            self.combo_format.addItems(VIDEO_FORMATS)
            qualities = (
                self._fetched_info["video_qualities"]
                if self._fetched_info
                else DEFAULT_VIDEO_QUALITIES
            )
        else:
            self.combo_format.addItems(AUDIO_FORMATS)
            qualities = (
                self._fetched_info["audio_bitrates"]
                if self._fetched_info
                else DEFAULT_AUDIO_QUALITIES
            )

        self.combo_quality.addItems(qualities)

    # ── Fetch ─────────────────────────────────

    def _on_fetch(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            self._log("⚠  Please enter a URL.")
            return

        self._set_ui_busy(True, "Fetching metadata…")
        self._fetched_info = None
        self.btn_download.setEnabled(False)
        self.progress_bar.setValue(0)

        self._fetch_worker = FetchWorker(url, parent=self)
        self._fetch_worker.info_fetched.connect(self._on_info_fetched)
        self._fetch_worker.error_occurred.connect(self._on_fetch_error)
        self._fetch_worker.finished.connect(lambda: self._set_ui_busy(False))
        self._fetch_worker.start()

    def _on_info_fetched(self, info: dict) -> None:
        self._fetched_info = info
        self.lbl_title.setText(info["title"])
        self.lbl_duration.setText(f"Duration: {info['duration']}")
        self._update_options_for_mode()
        self.btn_download.setEnabled(True)

        if info.get("is_playlist"):
            count = info.get("entry_count", 0)
            self._log(f"✓  Playlist detected: {info['playlist_title']}  ·  {count} items")
        else:
            self._log(f"✓  Fetched: {info['title']}")

    def _on_fetch_error(self, msg: str) -> None:
        self.lbl_title.setText("—")
        self.lbl_duration.setText("")
        self._log(f"✗  {msg}")

    # ── Playlist Folder Dialog ────────────────

    def _show_playlist_dialog(self, playlist_title: str) -> str | None:
        """Show a dialog asking how to handle playlist output directory.

        Returns:
            The resolved output directory path, or None if the user cancelled.
        """
        current_dir = self.dir_input.text().strip()

        msg = QMessageBox(self)
        msg.setWindowTitle("Playlist Detected")
        msg.setText(
            f'The URL is a playlist: "{playlist_title}".\n\n'
            f"Where would you like to save the files?"
        )
        msg.setInformativeText(
            f'• "Create New Folder" → {current_dir}/{playlist_title}/\n'
            f'• "Save to Selected Folder" → {current_dir}/'
        )

        btn_create = msg.addButton("Create New Folder", QMessageBox.ButtonRole.AcceptRole)
        btn_save = msg.addButton("Save to Selected Folder", QMessageBox.ButtonRole.ActionRole)
        btn_cancel = msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)

        msg.setDefaultButton(btn_create)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == btn_create:
            new_dir = str(Path(current_dir) / playlist_title)
            os.makedirs(new_dir, exist_ok=True)
            self.dir_input.setText(new_dir)
            self._log(f"📁  Created folder: {new_dir}")
            return new_dir
        elif clicked == btn_save:
            self._log(f"📁  Saving to: {current_dir}")
            return current_dir
        else:
            self._log("⚠  Download cancelled.")
            return None

    # ── Download ──────────────────────────────

    def _on_download(self) -> None:
        url = self.url_input.text().strip()
        output_dir = self.dir_input.text().strip()
        if not url or not output_dir:
            self._log("⚠  URL and output directory are required.")
            return

        is_playlist = bool(self._fetched_info and self._fetched_info.get("is_playlist"))
        entry_count = (self._fetched_info or {}).get("entry_count", 1)
        playlist_title = (self._fetched_info or {}).get("playlist_title", "")

        # ── Playlist folder dialog ──
        if is_playlist:
            resolved_dir = self._show_playlist_dialog(playlist_title)
            if resolved_dir is None:
                return  # user cancelled
            output_dir = resolved_dir

        mode = self.combo_mode.currentText()
        fmt = self.combo_format.currentText()
        quality = self.combo_quality.currentText()

        self._set_ui_busy(True, "Preparing download…")
        self.progress_bar.setValue(0)

        self._download_worker = DownloadWorker(
            url=url,
            mode=mode,
            fmt=fmt,
            quality=quality,
            output_dir=output_dir,
            is_playlist=is_playlist,
            entry_count=entry_count,
            parent=self,
        )
        self._download_worker.progress_updated.connect(self.progress_bar.setValue)
        self._download_worker.status_updated.connect(self.lbl_status.setText)
        self._download_worker.download_finished.connect(self._on_download_finished)
        self._download_worker.error_occurred.connect(self._on_download_error)
        self._download_worker.finished.connect(lambda: self._set_ui_busy(False))
        self._download_worker.start()

    def _on_download_finished(self, path: str) -> None:
        self.progress_bar.setValue(100)
        self.lbl_status.setText("Done!")
        self._log(f"✓  Download complete: {path}")

    def _on_download_error(self, msg: str) -> None:
        self.progress_bar.setValue(0)
        self.lbl_status.setText("")
        self._log(f"✗  {msg}")

    # ── Helpers ───────────────────────────────

    def _set_ui_busy(self, busy: bool, message: str = "") -> None:
        """Enable/disable interactive elements while a worker is active."""
        self.btn_fetch.setEnabled(not busy)
        self.url_input.setEnabled(not busy)
        self.btn_download.setEnabled(not busy and self._fetched_info is not None)
        self.combo_mode.setEnabled(not busy)
        self.combo_format.setEnabled(not busy)
        self.combo_quality.setEnabled(not busy)
        if message:
            self._log(message)
