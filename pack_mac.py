"""Builds the archive the Mac is built from.

Two things this does that a right-click "compress" does not.

Line endings are forced to Unix. Everything here is written on Windows, so the
files carry CRLF, and `build_mac.sh` with CRLF fails on macOS in a way that
reads as nonsense -- `set -o pipefail\r` becomes "invalid option name" and a
bare `\r` becomes "command not found" with no name attached.

Entry names are written with forward slashes and the shell script keeps its
executable bit. PowerShell's Compress-Archive writes backslashes, which a Mac
unpacks into a file called `tables\\table_full.npz` rather than a folder.
"""
from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE / "tool"
MAC = HERE / "mac_build"
ARCHIVE = HERE / "MatreshkaRemapRenderer_mac.zip"

# What the application itself reaches, worked out from main.py's imports.
APP = ["main", "app_jobs", "avio", "constants", "depends", "imagefile",
       "logfile", "preview3d", "remap_engine", "remap_render", "scan"]
# Not built in, but they are how the tables were made and belong beside them.
BAKES = ["bake_geometry", "bake_tables"]
TABLES = ["table_full.npz", "table_half.npz", "table_quarter.npz",
          "screen_geometry.npz", "viewer_table.npz"]

BINARY = {".npz", ".png", ".zip", ".exr"}


def sync() -> None:
    """Bring the Mac folder up to date with the sources beside it."""
    for name in APP + BAKES:
        shutil.copy2(TOOL / f"{name}.py", MAC / f"{name}.py")
    shutil.copy2(TOOL / "requirements.txt", MAC / "requirements.txt")
    (MAC / "tables").mkdir(exist_ok=True)
    for name in TABLES:
        shutil.copy2(TOOL / "tables" / name, MAC / "tables" / name)


def to_unix() -> int:
    """CRLF to LF everywhere it matters. Returns how many files were changed."""
    changed = 0
    for path in sorted(MAC.rglob("*")):
        if not path.is_file() or path.suffix.lower() in BINARY:
            continue
        raw = path.read_bytes()
        if b"\r\n" not in raw:
            continue
        path.write_bytes(raw.replace(b"\r\n", b"\n"))
        changed += 1
    return changed


def pack() -> None:
    files = sorted(p for p in MAC.rglob("*") if p.is_file())
    with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(MAC).as_posix(),
                                   date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if path.suffix == ".sh" else 0o644) << 16
            info.external_attr |= stat.S_IFREG << 16
            zf.writestr(info, path.read_bytes())
    print(f"{ARCHIVE.name}  {ARCHIVE.stat().st_size / 1e6:.2f} MB  "
          f"{len(files)} files")


def check() -> None:
    """Say plainly whether the two things that break a Mac build are gone."""
    with zipfile.ZipFile(ARCHIVE) as zf:
        crlf = [n for n in zf.namelist()
                if Path(n).suffix.lower() not in BINARY and b"\r\n" in zf.read(n)]
        slashes = [n for n in zf.namelist() if "\\" in n]
        script = zf.getinfo("build_mac.sh")
        print(f"  windows line endings: {crlf or 'none'}")
        print(f"  backslashes in names: {slashes or 'none'}")
        print(f"  build_mac.sh mode   : {oct(script.external_attr >> 16)}")


if __name__ == "__main__":
    sync()
    print(f"line endings fixed in {to_unix()} files")
    pack()
    check()
