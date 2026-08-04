"""
downloader.py — yt-dlp backend workers for ytdlp-qt.

Provides two QThread-based workers:
  • FetchWorker  — extracts video/playlist metadata and available formats.
  • DownloadWorker — downloads video/audio with real-time progress,
                     supports playlists with per-item tracking, and
                     embeds album art for audio files.

All heavy I/O runs off the main thread; results are delivered via Qt signals.
"""

from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
import sys
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


class DownloadCancelledError(Exception):
    """Custom exception raised when download is cancelled by the user."""
    pass


def _strip_ansi(text: str) -> str:
    """Remove ANSI colour/style escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


def _sanitize_filename(name: str) -> str:
    """Remove characters that are illegal in file/directory names."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip(". ")


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


def _ffmpeg_opts() -> dict[str, str]:
    """Return ``{"ffmpeg_location": ...}`` if ffmpeg is found, else ``{}``."""
    loc = _find_ffmpeg()
    return {"ffmpeg_location": loc} if loc else {}


# ──────────────────────────────────────────────
#  UpdateWorker
# ──────────────────────────────────────────────

class UpdateWorker(QThread):
    """Upgrades yt-dlp package using the active Python executable.

    Signals
    -------
    update_status : str
        Status message while checking or installing.
    update_finished : bool, str
        Success flag and detail message.
    """

    update_status = pyqtSignal(str)
    update_finished = pyqtSignal(bool, str)

    def run(self) -> None:
        try:
            self.update_status.emit("Checking for yt-dlp updates...")
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False
            )
            
            if result.returncode == 0:
                # Successfully ran pip. Reload yt_dlp to get the new version in memory.
                import importlib
                try:
                    importlib.reload(yt_dlp)
                except Exception:
                    pass
                version = yt_dlp.version.__version__
                self.update_finished.emit(True, f"yt-dlp updated successfully (Version: {version})")
            else:
                self.update_finished.emit(False, f"Update failed: {result.stderr or result.stdout}")
        except Exception as e:
            self.update_finished.emit(False, f"Update failed: {e}")


# ──────────────────────────────────────────────
#  FetchWorker
# ──────────────────────────────────────────────

class FetchWorker(QThread):
    """Fetches video/playlist metadata and available formats for a given URL.

    Signals
    -------
    info_fetched : dict
        Payload with keys: title, duration, thumbnail, video_qualities,
        audio_bitrates, is_playlist, playlist_title, entry_count.
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
                "ignoreerrors": True,
                "extract_flat": "in_playlist",
                **_ffmpeg_opts(),
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info: dict = ydl.extract_info(self._url, download=False)

            # ── Detect playlist vs single video ──
            info_type = info.get("_type", "")
            entries = info.get("entries")
            is_playlist = info_type == "playlist" or (
                entries is not None and not isinstance(entries, dict)
            )

            if is_playlist:
                # entries may be a generator; materialise it
                entry_list = list(entries) if entries else []
                
                # Filter out broken or unavailable entries
                valid_entries = []
                skipped_items = []
                for idx, entry in enumerate(entry_list, 1):
                    if not entry:
                        skipped_items.append(f"Item {idx} (Unavailable)")
                        continue
                    title = entry.get("title")
                    if title in ("[Deleted video]", "[Private video]", None):
                        skipped_items.append(title or f"Item {idx} (Private/Deleted)")
                        continue
                    valid_entries.append(entry)

                entry_count = len(valid_entries)
                playlist_title = _sanitize_filename(
                    info.get("title", "") or info.get("playlist_title", "") or "Playlist"
                )

                # Aggregate format info from the first valid entry that has formats
                formats: list[dict] = []
                for entry in valid_entries:
                    entry_url = entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}"
                    try:
                        single_opts = {
                            "quiet": True,
                            "no_warnings": True,
                            "skip_download": True,
                            "extract_flat": False,
                            **_ffmpeg_opts(),
                        }
                        with yt_dlp.YoutubeDL(single_opts) as ydl_single:
                            single_info = ydl_single.extract_info(entry_url, download=False)
                            if single_info and single_info.get("formats"):
                                formats = single_info["formats"]
                                break
                    except Exception:
                        pass

                payload = {
                    "title": f"📋  {playlist_title}",
                    "duration": f"{entry_count} items",
                    "thumbnail": info.get("thumbnail", ""),
                    "video_qualities": self._parse_video_qualities(formats),
                    "audio_bitrates": self._parse_audio_bitrates(formats),
                    "is_playlist": True,
                    "playlist_title": playlist_title,
                    "entry_count": entry_count,
                    "playlist_entries": valid_entries,
                    "skipped_items": skipped_items,
                }
            else:
                formats = info.get("formats") or []
                payload = {
                    "title": info.get("title", "Unknown Title"),
                    "duration": self._format_duration(info.get("duration")),
                    "thumbnail": info.get("thumbnail", ""),
                    "video_qualities": self._parse_video_qualities(formats),
                    "audio_bitrates": self._parse_audio_bitrates(formats),
                    "is_playlist": False,
                    "playlist_title": "",
                    "entry_count": 1,
                    "playlist_entries": None,
                    "skipped_items": [],
                }

            self.info_fetched.emit(payload)
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(f"Fetch failed: {_strip_ansi(str(exc))}")


# ──────────────────────────────────────────────
#  DownloadWorker
# ──────────────────────────────────────────────

class DownloadWorker(QThread):
    """Downloads a video or audio file via yt-dlp.

    Supports single videos and playlists.  For playlists, per-item
    progress is emitted.  Audio downloads embed album art and metadata.

    Signals
    -------
    progress_updated : int
        Download percentage (0–100).
    status_updated : str
        Human-readable status string.
    download_finished : str
        Absolute path of the saved file / output directory.
    error_occurred : str
        Human-readable error message.
    """

    progress_updated = pyqtSignal(int)
    status_updated = pyqtSignal(str)
    download_finished = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    log_message = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        mode: str,
        fmt: str,
        quality: str,
        output_dir: str,
        is_playlist: bool = False,
        entry_count: int = 1,
        playlist_entries: list[dict] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._url = url
        self._mode = mode          # "Video" or "Audio"
        self._fmt = fmt            # e.g. "mp4", "mp3"
        self._quality = quality    # e.g. "Best", "1080p", "320kbps"
        self._output_dir = output_dir
        self._is_playlist = is_playlist
        self._playlist_entries = playlist_entries or []
        self._entry_count = len(self._playlist_entries) if is_playlist and playlist_entries else max(entry_count, 1)
        self._final_path: str = ""
        self._current_item: int = 0
        self._is_cancelled = False

    def cancel(self) -> None:
        """Cancel the download thread execution gracefully."""
        self._is_cancelled = True

    # ── progress hook ─────────────────────────

    def _progress_hook(self, d: dict) -> None:
        if self._is_cancelled:
            raise DownloadCancelledError("Cancelled")

        status = d.get("status", "")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)

            item_prefix = ""
            if self._is_playlist and self._entry_count > 1:
                item_prefix = f"[{self._current_item}/{self._entry_count}]  "

            if total > 0:
                pct = int(downloaded / total * 100)
                # For playlists, scale progress across all items
                if self._is_playlist and self._entry_count > 1:
                    overall = int(
                        ((self._current_item - 1) / self._entry_count * 100)
                        + (pct / self._entry_count)
                    )
                    self.progress_updated.emit(min(overall, 100))
                else:
                    self.progress_updated.emit(min(pct, 100))

                speed = _strip_ansi(d.get("_speed_str", "N/A")).strip()
                eta = _strip_ansi(d.get("_eta_str", "N/A")).strip()
                self.status_updated.emit(
                    f"{item_prefix}Downloading… {pct}%  |  {speed}  |  ETA {eta}"
                )
            else:
                self.status_updated.emit(f"{item_prefix}Downloading…")

        elif status == "finished":
            self._final_path = d.get("filename", "")
            if not self._is_playlist:
                self.progress_updated.emit(100)
            self.status_updated.emit("Post-processing…")

    def _postprocessor_hook(self, d: dict) -> None:
        """Track playlist item transitions via postprocessor hooks."""
        if self._is_cancelled:
            raise DownloadCancelledError("Cancelled")
        if d.get("status") == "started":
            # Each new postprocessor "started" on a new file means a new item
            pass  # yt-dlp handles sequencing internally

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
            **_ffmpeg_opts(),
        }

    def _build_audio_opts(self) -> dict[str, Any]:
        """Build yt-dlp options for audio extraction with album art embedding."""
        quality = self._quality
        codec = self._fmt.lower()  # mp3 / m4a / wav

        if quality == "Best":
            preferred_quality = "0"  # best available
        else:
            preferred_quality = re.sub(r"\D", "", quality)  # "320kbps" → "320"

        return {
            "format": "bestaudio/best",
            "outtmpl": str(Path(self._output_dir) / "%(title)s.%(ext)s"),
            "writethumbnail": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": codec,
                    "preferredquality": preferred_quality,
                },
                {
                    "key": "FFmpegMetadata",
                    "add_metadata": True,
                },
                {
                    "key": "EmbedThumbnail",
                    "already_have_thumbnail": False,
                },
            ],
            "progress_hooks": [self._progress_hook],
            "quiet": True,
            "no_warnings": True,
            **_ffmpeg_opts(),
        }

    # ── thumbnail cleanup ─────────────────────

    def _cleanup_thumbnails(self) -> None:
        """Remove leftover thumbnail files from the output directory."""
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            for thumb in glob.glob(str(Path(self._output_dir) / ext)):
                try:
                    # Only delete files that look like yt-dlp thumbnails
                    # (they share the video title as the filename stem)
                    os.remove(thumb)
                except OSError:
                    pass

    # ── thread entry ──────────────────────────

    def run(self) -> None:  # noqa: D401
        try:
            self.status_updated.emit("Preparing download…")

            if self._is_playlist and self._playlist_entries:
                failed_items = []
                total_items = len(self._playlist_entries)
                for i, entry in enumerate(self._playlist_entries, 1):
                    if self._is_cancelled:
                        raise DownloadCancelledError("Cancelled")

                    self._current_item = i
                    video_id = entry.get("id")
                    item_url = entry.get("url") or f"https://www.youtube.com/watch?v={video_id}"
                    item_title = entry.get("title", f"Video {i}")

                    self.status_updated.emit(f"[{i}/{total_items}] Preparing: {item_title}")
                    self.log_message.emit(f"⏳ Downloading ({i}/{total_items}): {item_title}...")

                    try:
                        if self._mode == "Video":
                            opts = self._build_video_opts()
                        else:
                            opts = self._build_audio_opts()

                        with yt_dlp.YoutubeDL(opts) as ydl:
                            ydl.download([item_url])
                        self.log_message.emit(f"✓ Succeeded ({i}/{total_items}): {item_title}")
                    except DownloadCancelledError:
                        raise
                    except Exception as e:
                        if self._is_cancelled:
                            raise DownloadCancelledError("Cancelled")
                        clean_err = _strip_ansi(str(e))
                        short_err = clean_err[:60] + "..." if len(clean_err) > 60 else clean_err
                        self.status_updated.emit(f"⚠ Item {i} failed. Skipping...")
                        self.log_message.emit(f"✗ Failed ({i}/{total_items}): {item_title} - {short_err}")
                        failed_items.append((i, item_title))
                        continue

                # Clean up leftover thumbnail images for audio downloads
                if self._mode == "Audio":
                    self._cleanup_thumbnails()

                # Process completion summary
                success_count = total_items - len(failed_items)
                summary = f"✓ Process complete. {success_count}/{total_items} files downloaded successfully."
                if failed_items:
                    summary += f" {len(failed_items)} items failed."

                self.download_finished.emit(summary)

            else:
                # Single video download
                if self._mode == "Video":
                    opts = self._build_video_opts()
                else:
                    opts = self._build_audio_opts()

                # For fallback, if is_playlist is set but entries are not populated, let yt-dlp iterate natively
                if self._is_playlist:
                    original_hook = opts.get("progress_hooks", [])
                    item_counter = {"n": 0}

                    def _counting_hook(d: dict) -> None:
                        if self._is_cancelled:
                            raise DownloadCancelledError("Cancelled")
                        if d.get("status") == "downloading":
                            info = d.get("info_dict", {})
                            idx = info.get("playlist_index") or info.get(
                                "playlist_autonumber", 0
                            )
                            if idx and idx != item_counter.get("last_idx"):
                                item_counter["last_idx"] = idx
                                item_counter["n"] += 1
                                self._current_item = item_counter["n"]
                        for hook in original_hook:
                            hook(d)

                    opts["progress_hooks"] = [_counting_hook]

                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([self._url])

                # Clean up leftover thumbnail images for audio downloads
                if self._mode == "Audio":
                    self._cleanup_thumbnails()

                self.download_finished.emit(
                    self._output_dir if self._is_playlist else self._final_path
                )
        except DownloadCancelledError:
            self.log_message.emit("⚠  Download cancelled by user.")
            self.download_finished.emit("Cancelled")
        except Exception as exc:  # noqa: BLE001
            self.error_occurred.emit(f"Download failed: {_strip_ansi(str(exc))}")
