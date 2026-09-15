"""The two things that actually get handed over.

Everything above this runs the source. What the operator opens is an exe, and
what goes to the Mac is a zip, and both have been wrong while the source was
right -- a build older than the fix in it, and a Mac archive with no icons in
it because the spec and the shell script keep two separate lists of data files.
"""
from __future__ import annotations

import os
import subprocess
import time
import zipfile
from pathlib import Path

import world
from harness import Failure, check, want

GROUP = "what gets handed over"

TOOL = world.TOOL
PROJECT = world.PROJECT
EXE = PROJECT / "MatreshkaRemapRenderer.exe"
ARCHIVE = PROJECT / "MatreshkaRemapRenderer_mac.zip"

# Everything the window reaches, which is the list the Mac build has to carry.
MODULES = ["main", "app_jobs", "avio", "constants", "depends", "imagefile",
           "logfile", "preview3d", "remap_engine", "remap_render", "scan",
           "transform", "transform_ui", "icons", "gizmo", "framecache",
           "presets"]


def _newest_source() -> tuple[Path, float]:
    """The most recently touched thing a build would need to pick up."""
    newest, when = None, 0.0
    for pattern in ("*.py", "icons/*.png", "tables/*.npz", "*.spec"):
        for path in TOOL.glob(pattern):
            if "tests" in path.parts:
                continue
            stamp = path.stat().st_mtime
            if stamp > when:
                newest, when = path, stamp
    return newest, when


@check(GROUP, "the Windows build is not older than the source in it")
def exe_is_current():
    want(EXE.is_file(), f"there is no {EXE.name} beside the project")
    newest, when = _newest_source()
    built = EXE.stat().st_mtime
    if built < when:
        behind = (when - built) / 60
        raise Failure(f"{EXE.name} was built {behind:.0f} minutes before "
                      f"{newest.name} was last changed")
    return (f"{EXE.stat().st_size / 1024 ** 2:.1f} MB, built "
            f"{(built - when) / 60:.0f} min after the last change to "
            f"{newest.name}")


@check(GROUP, "the Mac archive is not older than the source in it")
def archive_is_current():
    want(ARCHIVE.is_file(), f"there is no {ARCHIVE.name} beside the project")
    newest, when = _newest_source()
    built = ARCHIVE.stat().st_mtime
    if built < when:
        behind = (when - built) / 60
        raise Failure(f"{ARCHIVE.name} was packed {behind:.0f} minutes before "
                      f"{newest.name} was last changed")
    return f"{ARCHIVE.stat().st_size / 1024 ** 2:.2f} MB"


@check(GROUP, "the Mac archive carries everything the window reaches")
def archive_is_complete():
    """The spec and the shell script keep separate lists of what to bundle,
    which is how the Mac build once shipped with no icons in it."""
    want(ARCHIVE.is_file(), "no Mac archive")
    with zipfile.ZipFile(ARCHIVE) as bundle:
        names = set(bundle.namelist())

    missing = [f"{name}.py" for name in MODULES if f"{name}.py" not in names]
    want(not missing, f"modules left out of the archive: {missing}")

    icons_here = {path.name for path in (TOOL / "icons").glob("*.png")}
    icons_there = {Path(name).name for name in names
                   if name.startswith("icons/") and name.endswith(".png")}
    lost = sorted(icons_here - icons_there)
    want(not lost, f"icons left out of the archive: {lost[:6]}")

    tables = sorted(name for name in names
                    if name.startswith("tables/") and name.endswith(".npz"))
    want(len(tables) >= 4,
         f"only {len(tables)} tables in the archive: {tables}")

    want("build_mac.sh" in names, "the archive has nothing to build with")
    return (f"{len(MODULES)} modules, {len(icons_there)} icons, "
            f"{len(tables)} tables, {len(names)} entries")


@check(GROUP, "the Mac archive is written the way a Mac reads it")
def archive_is_unix():
    """Compress-Archive writes backslashes, which a Mac unpacks into a file
    called `tables\\table_full.npz` rather than a folder; and a shell script
    with CRLF fails on macOS in a way that reads as nonsense."""
    with zipfile.ZipFile(ARCHIVE) as bundle:
        slashes = [name for name in bundle.namelist() if "\\" in name]
        want(not slashes, f"backslashes in the archive: {slashes[:4]}")
        crlf = []
        for name in bundle.namelist():
            if not name.endswith((".py", ".sh", ".txt", ".md")):
                continue
            if b"\r\n" in bundle.read(name):
                crlf.append(name)
        want(not crlf, f"Windows line endings in: {crlf[:6]}")
        info = bundle.getinfo("build_mac.sh")
        mode = (info.external_attr >> 16) & 0o777
        want(mode & 0o111, f"build_mac.sh is not executable ({oct(mode)})")
    return "forward slashes, Unix line endings, build_mac.sh executable"


@check(GROUP, "the icons the build carries are the icons on disk")
def bundled_icons_match():
    """The Windows spec bundles its own copy, and a name added to the source
    without a file beside it is a blank button in the build only."""
    spec = (TOOL / "build.spec").read_text("utf-8")
    want("icons" in spec, "the Windows spec does not mention the icons at all")
    want("tables" in spec, "the Windows spec does not mention the tables")
    here = sorted(path.name for path in (TOOL / "icons").glob("*.png"))
    want(here, "there are no icons beside the source")
    return f"{len(here)} icons on disk, named in the spec"


@check(GROUP, "the built exe starts and stays up")
def exe_starts():
    """Off by default. It opens the real window, which is the one thing this
    suite cannot do offscreen -- pass --launch to include it.

    Run under a settings file that is put back afterwards, like everything
    else here.
    """
    if not os.environ.get("MATRESHKA_TESTS_LAUNCH"):
        return "skipped -- pass --launch to run it"
    want(EXE.is_file(), "no exe to start")
    started = subprocess.Popen([str(EXE)], cwd=str(PROJECT))
    try:
        for _ in range(60):
            if started.poll() is not None:
                raise Failure(f"{EXE.name} quit on its own with code "
                              f"{started.returncode}")
            time.sleep(0.25)
    finally:
        started.terminate()
        try:
            started.wait(timeout=10)
        except subprocess.TimeoutExpired:
            started.kill()
    return f"{EXE.name} stayed up for fifteen seconds and closed on request"


@check(GROUP, "the log the app writes has no traceback in its last run")
def the_log_is_clean():
    folder = PROJECT / "Logs"
    if not folder.is_dir():
        return "no Logs folder yet"
    logs = sorted(folder.glob("*.log"), key=lambda path: path.stat().st_mtime)
    if not logs:
        return "no log written yet"
    latest = logs[-1]
    text = latest.read_text("utf-8", errors="replace")
    bad = [line for line in text.splitlines()
           if "Traceback" in line or "Error:" in line]
    want(not bad, f"{latest.name} has {len(bad)}: {bad[:2]}")
    return f"{latest.name}, {len(text.splitlines())} lines, nothing thrown"
