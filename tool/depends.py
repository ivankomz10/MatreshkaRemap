"""What the application needs from the machine, and how to get it.

Everything Python is inside the executable. The one thing that is not is
ffmpeg: it is the render pipeline's decoder and encoder, it is large, and its
licence makes shipping a build inside someone else's binary a question better
left alone. So it is looked for, and offered.

A downloaded copy lands in a folder beside the application rather than anywhere
system-wide -- nothing is installed, nothing is put on PATH, and deleting the
folder undoes it completely.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import constants

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

TOOLS_DIR = "ffmpeg"                     # beside the application
BINARY = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

# Direct archives, one per platform. Both are the builds their communities
# point at; both are plain zips holding the binary and nothing to install.
DOWNLOADS = {
    "win32": (
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
        "ffmpeg-master-latest-win64-gpl.zip",
        163,
    ),
    "darwin": ("https://evermeet.cx/ffmpeg/getrelease/zip", 30),
}

# Where there is no single archive worth hard-coding, say so instead.
ADVICE = {
    "linux": "install ffmpeg with the package manager, e.g. apt install ffmpeg",
}


@dataclass
class Requirement:
    """One thing that was looked for."""

    name: str
    ok: bool
    detail: str
    required: bool = True
    fixable: bool = False


def tools_dir() -> Path:
    # In the writable working folder, not beside the executable: on macOS the
    # app's own location is often read-only, so a download beside it would fail.
    return constants.PROJECT_DIR / TOOLS_DIR


def ffmpeg_command() -> str:
    """The ffmpeg to use: the downloaded one first, then whatever is on PATH."""
    local = tools_dir() / BINARY
    if local.is_file():
        return str(local)
    return shutil.which("ffmpeg") or "ffmpeg"


def ffmpeg_version(command: str | None = None) -> str:
    """The first line of `ffmpeg -version`, or an empty string if it will not run."""
    try:
        first = subprocess.run(
            [command or ffmpeg_command(), "-hide_banner", "-version"],
            capture_output=True, text=True, timeout=20,
            creationflags=NO_WINDOW).stdout.splitlines()
        return first[0].strip() if first else ""
    except Exception:  # noqa: BLE001 -- absence is the answer
        return ""


def gpu_description() -> str:
    """The adapter wgpu would render on, or an empty string if there is none."""
    try:
        import wgpu

        adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
        if adapter is None:
            return ""
        info = adapter.info
        return f"{info.get('device', 'unknown')} ({info.get('backend_type', '')})".strip()
    except Exception:  # noqa: BLE001 -- any failure means the CPU path
        return ""


def can_download() -> bool:
    return sys.platform in DOWNLOADS


def download_size_mb() -> int:
    return DOWNLOADS.get(sys.platform, ("", 0))[1]


def check() -> list[Requirement]:
    """Look for everything, in the order it matters."""
    found = []

    command = ffmpeg_command()
    version = ffmpeg_version(command)
    if version:
        where = "in the project folder" if command != "ffmpeg" \
            and str(tools_dir()) in command else command
        found.append(Requirement("ffmpeg", True, f"{version[:70]}   [{where}]"))
    else:
        found.append(Requirement(
            "ffmpeg", False,
            ADVICE.get(sys.platform, "needed to read and write video"),
            fixable=can_download()))

    adapter = gpu_description()
    found.append(Requirement(
        "GPU", bool(adapter),
        adapter or "none found -- rendering will fall back to the CPU, several "
                   "times slower",
        required=False))

    tables = constants.tables_present()
    found.append(Requirement(
        "lookup tables", tables,
        "full, half, quarter" if tables
        else "missing -- bake them with tool/bake_tables.py"))

    return found


def install_ffmpeg(on_progress=None, should_stop=None) -> Path:
    """Fetch the archive for this platform and keep only the binary.

    Returns the path it was written to. Everything happens under a temporary
    name and is moved into place at the end, so an interrupted download cannot
    leave something half-written that looks usable.
    """
    import tempfile

    if not can_download():
        raise RuntimeError(f"no download for {sys.platform}")
    try:
        import urllib.request
        import ssl  # noqa: F401 -- present or not, that is the question
    except ImportError as error:
        raise RuntimeError(
            f"this build cannot reach the network ({error}); "
            f"install ffmpeg yourself and put it on PATH") from error

    url, _ = DOWNLOADS[sys.platform]
    folder = tools_dir()
    folder.mkdir(parents=True, exist_ok=True)

    request = urllib.request.Request(
        url, headers={"User-Agent": "MatreshkaRemapRenderer"})
    with urllib.request.urlopen(request, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False,
                                         dir=str(folder)) as archive:
            temporary = Path(archive.name)
            done = 0
            while True:
                if should_stop is not None and should_stop():
                    raise RuntimeError("cancelled")
                block = response.read(1 << 20)
                if not block:
                    break
                archive.write(block)
                done += len(block)
                if on_progress is not None:
                    on_progress(done, total)

    try:
        with zipfile.ZipFile(temporary) as bundle:
            wanted = next(
                (item for item in bundle.namelist()
                 if Path(item).name.lower() == BINARY.lower()), None)
            if wanted is None:
                raise RuntimeError(f"{BINARY} is not in the archive")
            with bundle.open(wanted) as source:
                landing = folder / (BINARY + ".part")
                landing.write_bytes(source.read())
    finally:
        temporary.unlink(missing_ok=True)

    target = folder / BINARY
    target.unlink(missing_ok=True)
    landing.rename(target)
    if sys.platform != "win32":
        target.chmod(target.stat().st_mode | 0o755)

    if not ffmpeg_version(str(target)):
        target.unlink(missing_ok=True)
        raise RuntimeError("the downloaded ffmpeg would not run")
    return target


def summary() -> str:
    """One line for the log."""
    parts = []
    for item in check():
        parts.append(f"{item.name}: {'ok' if item.ok else 'MISSING'}")
    return f"{platform.system()} {platform.release()}   " + "   ".join(parts)
