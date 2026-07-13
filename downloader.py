"""
downloader.py — yt-dlp backend workers for ytdlp-qt.

Provides two QThread-based workers:
  • FetchWorker  — extracts video metadata and available formats.
  • DownloadWorker — downloads video/audio with real-time progress.

All heavy I/O runs off the main thread; results are delivered via Qt signals.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import traceback
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

import yt_dlp


# ──────────────────────────────────────────────
#  Utilities
# ──────────────────────────────────────────────

# Regex to strip ANSI / VT100 escape codes from yt-dlp error strings.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Remove ANSI colour/style escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


def _prepare_ffmpeg_shim(source_exe: str) -> str | None:
    """Create a cache directory with a standard-named ``ffmpeg`` binary.

    ``imageio-ffmpeg`` ships a binary with a versioned name like
    ``ffmpeg-win-x86_64-v7.1.exe``.  yt-dlp's ``ffmpeg_location``
    expects ``ffmpeg.exe`` (Windows) or ``ffmpeg`` (Unix).  This
    function creates a directory with a correctly-named hard-link
    (or copy) so yt-dlp can discover it.
    """
    import tempfile

    system = platform.system()
    target_name = "ffmpeg.exe" if system == "Windows" else "ffmpeg"

    cache_dir = Path(tempfile.gettempdir()) / "ytdlp-qt-ffmpeg"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / target_name

    # Only create once; if the target already exists and is valid, reuse it.
    if target.is_file():
        return str(cache_dir)

    try:
        os.link(source_exe, str(target))          # hard-link (fast, no copy)
    except OSError:
        try:
            shutil.copy2(source_exe, str(target))  # fallback: full copy
        except OSError:
            return None

    return str(cache_dir)


def _find_ffmpeg() -> str | None:
    """Try to locate an ffmpeg binary and return its directory, or None.

    Search order:
    1. Already on PATH (shutil.which).
    2. imageio-ffmpeg pip package (bundled static binary).
    3. Common platform-specific install locations.
    4. Bundled alongside the yt-dlp package itself.
    """
    # 1. PATH
    which = shutil.which("ffmpeg")
    if which:
        return str(Path(which).parent)

    # 2. imageio-ffmpeg pip package (bundles a static ffmpeg binary)
    #    Its binary has a non-standard name, so we create a shim dir
    #    with a properly-named link that yt-dlp can discover.
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg_exe and Path(ffmpeg_exe).is_file():
            shim_dir = _prepare_ffmpeg_shim(ffmpeg_exe)
            if shim_dir:
                return shim_dir
    except (ImportError, RuntimeError):
        pass

    # 3. Common locations (platform-dependent)
    candidates: list[Path] = []
    system = platform.system()
    if system == "Windows":
        for base in [
            Path(os.environ.get("LOCALAPPDATA", "")),
            Path(os.environ.get("ProgramFiles", "C:/Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")),
            Path("C:/ffmpeg"),
            Path.home() / "scoop" / "shims",
        ]:
            candidates.append(base / "ffmpeg" / "bin")
            candidates.append(base / "ffmpeg")
    elif system == "Darwin":
        candidates += [
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
        ]
    else:  # Linux / other Unix
        candidates += [
            Path("/usr/bin"),
            Path("/usr/local/bin"),
            Path("/snap/bin"),
        ]

    for cand in candidates:
        ffmpeg_name = "ffmpeg.exe" if system == "Windows" else "ffmpeg"
        if (cand / ffmpeg_name).is_file():
            return str(cand)

    # 4. Bundled with yt-dlp package
    yt_dlp_dir = Path(yt_dlp.__file__).parent
    for sub in ["", "bin"]:
        check = yt_dlp_dir / sub / ("ffmpeg.exe" if system == "Windows" else "ffmpeg")
        if check.is_file():
            return str(check.parent)

    return None


# ──────────────────────────────────────────────
#  FetchWorker
# ──────────────────────────────────────────────

class FetchWorker(QThread):
    """Fetches video metadata and available formats for a given URL.

    Signals
    -------
    info_fetched : dict
        Payload with keys: title, duration, thumbnail, video_qualities, audio_bitrates.
    error_occurred : str
        Human-readable error message.
    """

    info_fetched = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, url: str, parent=None) -> None:
        super().__init__(parent)
        self._url = url

    # ── helpers ────────────────────────────────

    @staticmethod
    def _parse_video_qualities(formats: list[dict]) -> list[str]:
        """Return a sorted, deduplicated list of video height labels (e.g. '1080p')."""
        heights: set[int] = set()
        for fmt in formats:
            h = fmt.get("height")
            vcodec = fmt.get("vcodec", "none")
            if h and vcodec != "none":
                heights.add(h)
        labels = sorted(heights, reverse=True)
        result = ["Best"] + [f"{h}p" for h in labels]
        return result

    @staticmethod
    def _parse_audio_bitrates(formats: list[dict]) -> list[str]:
        """Return a sorted, deduplicated list of audio bitrate labels (e.g. '192kbps')."""
        bitrates: set[int] = set()
        for fmt in formats:
            abr = fmt.get("abr")
            acodec = fmt.get("acodec", "none")
            if abr and acodec != "none":
                bitrates.add(int(abr))
        labels = sorted(bitrates, reverse=True)
        result = ["Best"] + [f"{b}kbps" for b in labels]
        return result

    @staticmethod
    def _format_duration(seconds: int | float | None) -> str:
        if seconds is None:
            return "Unknown"
        seconds = int(seconds)
        h, remainder = divmod(seconds, 3600)
        m, s = divmod(remainder, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    # ── thread entry ──────────────────────────

    def run(self) -> None:  # noqa: D401
        try:
            ydl_opts: dict[str, Any] = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
            }
            ffmpeg_dir = _find_ffmpeg()
            if ffmpeg_dir:
                ydl_opts["ffmpeg_location"] = ffmpeg_dir

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info: dict = ydl.extract_info(self._url, download=False)

            formats = info.get("formats") or []
            payload = {
                "title": info.get("title", "Unknown Title"),
                "duration": self._format_duration(info.get("duration")),
                "thumbnail": info.get("thumbnail", ""),
                "video_qualities": self._parse_video_qualities(formats),
                "audio_bitrates": self._parse_audio_bitrates(formats),
            }
            self.info_fetched.emit(payload)
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(f"Fetch failed: {_strip_ansi(str(exc))}")


# ──────────────────────────────────────────────
#  DownloadWorker
# ──────────────────────────────────────────────

class DownloadWorker(QThread):
    """Downloads a video or audio file via yt-dlp.

    Signals
    -------
    progress_updated : int
        Download percentage (0–100).
    status_updated : str
        Human-readable status string.
    download_finished : str
        Absolute path of the saved file.
    error_occurred : str
        Human-readable error message.
    """

    progress_updated = pyqtSignal(int)
    status_updated = pyqtSignal(str)
    download_finished = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        mode: str,
        fmt: str,
        quality: str,
        output_dir: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._url = url
        self._mode = mode          # "Video" or "Audio"
        self._fmt = fmt            # e.g. "mp4", "mp3"
        self._quality = quality    # e.g. "Best", "1080p", "320kbps"
        self._output_dir = output_dir
        self._final_path: str = ""

    # ── progress hook ─────────────────────────

    def _progress_hook(self, d: dict) -> None:
        status = d.get("status", "")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0:
                pct = int(downloaded / total * 100)
                self.progress_updated.emit(min(pct, 100))
                speed = d.get("_speed_str", "N/A").strip()
                eta = d.get("_eta_str", "N/A").strip()
                self.status_updated.emit(f"Downloading… {pct}%  |  {speed}  |  ETA {eta}")
            else:
                self.status_updated.emit("Downloading…")
        elif status == "finished":
            self._final_path = d.get("filename", "")
            self.progress_updated.emit(100)
            self.status_updated.emit("Post-processing…")

    # ── option builders ───────────────────────

    def _build_video_opts(self) -> dict[str, Any]:
        """Build yt-dlp options for video download."""
        quality = self._quality
        container = self._fmt.lower()  # mp4 / mkv / webm

        if quality == "Best":
            fmt_selector = "bestvideo+bestaudio/best"
        else:
            # Extract height number, e.g. "1080p" → 1080
            height = re.sub(r"\D", "", quality)
            fmt_selector = (
                f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
            )

        return {
            "format": fmt_selector,
            "merge_output_format": container,
            "outtmpl": str(Path(self._output_dir) / "%(title)s.%(ext)s"),
            "progress_hooks": [self._progress_hook],
            "quiet": True,
            "no_warnings": True,
            **({"ffmpeg_location": d} if (d := _find_ffmpeg()) else {}),
        }

    def _build_audio_opts(self) -> dict[str, Any]:
        """Build yt-dlp options for audio extraction."""
        quality = self._quality
        codec = self._fmt.lower()  # mp3 / m4a / wav

        if quality == "Best":
            preferred_quality = "0"  # best available
        else:
            preferred_quality = re.sub(r"\D", "", quality)  # "320kbps" → "320"

        return {
            "format": "bestaudio/best",
            "outtmpl": str(Path(self._output_dir) / "%(title)s.%(ext)s"),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": codec,
                    "preferredquality": preferred_quality,
                }
            ],
            "progress_hooks": [self._progress_hook],
            "quiet": True,
            "no_warnings": True,
            **({"ffmpeg_location": d} if (d := _find_ffmpeg()) else {}),
        }

    # ── thread entry ──────────────────────────

    def run(self) -> None:  # noqa: D401
        try:
            self.status_updated.emit("Preparing download…")
            if self._mode == "Video":
                opts = self._build_video_opts()
            else:
                opts = self._build_audio_opts()

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([self._url])

            self.download_finished.emit(self._final_path)
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(f"Download failed: {_strip_ansi(str(exc))}")
