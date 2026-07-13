# ytdlp-qt

`ytdlp-qt` is a lightweight, modern, and simple desktop wrapper for the powerful `yt-dlp` YouTube downloader. Built using Python 3.10+ and PyQt6, the application provides a clean graphical interface without requiring any system-wide installations of the `yt-dlp` CLI binary.

---

## Features

- **Responsive Non-Blocking UI:** Network requests, metadata extraction, and downloads run in background worker threads (`QThread`) keeping the GUI smooth and interactive at all times.
- **Dynamic Quality Selection:** Automatically fetches all available formats, video resolutions, and audio qualities when a valid URL is input.
- **Video & Audio Downloads:** 
  - Download high-definition video formats (MP4, MKV, WebM) merged with best-available audio.
  - Extract and convert audio into popular formats (MP3, M4A, WAV) with custom bitrate selection (e.g., 320kbps, 192kbps).
- **Embedded Cover Art & Metadata:** Audio downloads automatically retrieve the highest quality video thumbnail and embed it as the album artwork along with standard metadata tags (title and artist).
- **Intelligent Playlist Detection:** Detects YouTube playlists upon fetching metadata and prompts you with options to organize files (e.g., create a dedicated subdirectory named after the playlist or save directly in the selected output folder).
- **Clean Dark Aesthetics:** Features a modern, custom Catppuccin Mocha-inspired dark theme stylesheet that scales beautifully across high-resolution displays.

---

## Crucial Prerequisites (The "Gotchas")

Before running the application, make sure to review these critical platform settings:

### 1. External Multimedia Backend (`ffmpeg` & `ffprobe`)
While `yt-dlp` functions as a Python library to download raw streams, **merging high-definition video streams (like 1080p, 1440p, or 4K)** and **extracting audio formats (like MP3, M4A)** requires system-level installation of `ffmpeg` and `ffprobe`.

*If system binaries are missing, the application automatically tries to download and shim a static build via the `imageio-ffmpeg` package, but installing `ffmpeg` globally is highly recommended for full stability.*

#### Copy-Paste Installation Commands:

- **macOS:**
  ```bash
  brew install ffmpeg
  ```
- **Ubuntu / Debian:**
  ```bash
  sudo apt update && sudo apt install -y ffmpeg
  ```
- **Fedora / RHEL:**
  ```bash
  sudo dnf install ffmpeg
  ```
- **Windows:**
  ```powershell
  winget install Gyan.FFmpeg
  ```
  *(Or download from [Gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add its `/bin` directory to your system Environment Variables).*

### 2. High-DPI Screen Scaling
PyQt6 applications can scale poorly on 4K laptops or scaled high-resolution monitors. To address text clipping, micro-fonts, and misaligned boxes:
- The app configures the Environment Variables `QT_ENABLE_HIGHDPI_SCALING=1` and `QT_SCALE_FACTOR_ROUNDING_POLICY=PassThrough` directly in `main.py` before initialising the `QApplication`.
- The interface styling avoids hardcoded pixel dimensions (`px`) for fonts and layouts, using point-based sizes (`pt`) and responsive stretch factors instead.

---

## Installation & Usage

Follow these steps to configure and run the application locally on any Windows, macOS, or Linux device:

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/ytdlp-qt.git
cd ytdlp-qt
```

### 2. Set Up a Virtual Environment
- **Unix (macOS / Linux):**
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```
- **Windows (PowerShell):**
  ```powershell
  python -m venv .venv
  .venv\Scripts\Activate.ps1
  ```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the Application
```bash
python main.py
```
