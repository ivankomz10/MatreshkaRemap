"""Hard-coded facts about the Matreshka remap project.

Everything here was read out of the .blend directly; if the .blend changes,
this is the only file that has to follow.

Paths are never absolute. The application locates itself on disk, finds the
.blend next to it, and every other path is expressed relative to that folder --
so the whole project (blend + ToRemap + OUT + the executable) can be moved or
copied to another drive and still work.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Matreshka Remap Renderer"
APP_VERSION = "0.3"


def app_dir() -> Path:
    """Folder the app itself lives in (next to the .blend once compiled)."""
    if getattr(sys, "frozen", False):  # PyInstaller build
        executable = Path(sys.executable).resolve()
        # In a macOS bundle the binary sits in Foo.app/Contents/MacOS/, so the
        # folder the user sees the app in is the one holding the .app itself.
        for parent in executable.parents:
            if parent.suffix == ".app":
                return parent.parent
        return executable.parent
    return Path(__file__).resolve().parent


def _looks_translocated(path: os.PathLike[str] | str) -> bool:
    """Whether this is one of macOS's read-only launch copies.

    A quarantined app that has not been moved in Finder is run from a random
    read-only place -- Gatekeeper's "app translocation" -- and anything under
    the per-user temporary tree is read-only and impermanent too. Writing the
    project beside the app there lands in a folder that cannot be written to
    and vanishes when the app quits.
    """
    text = str(path)
    return "AppTranslocation" in text or "/private/var/folders/" in text


def _is_writable_dir(path: Path) -> bool:
    """Whether files can be made in `path`, now or after creating its parents."""
    try:
        probe = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return probe.exists() and os.access(probe, os.W_OK)
    except OSError:
        return False


def _fallback_project_dir() -> Path:
    """A stable, writable, per-user home for the working folders when the app's
    own location cannot serve -- read-only like /Applications, or a translocated
    copy. Kept somewhere a person can actually find, not a hidden support dir."""
    home = Path.home()
    if sys.platform == "darwin":
        documents = home / "Documents"
        return (documents if documents.is_dir() else home) / APP_NAME
    return home / APP_NAME


def _usable(path: Path) -> bool:
    return not _looks_translocated(path) and _is_writable_dir(path)


def _find_project_dir() -> Path:
    """Where ToRemap, OUT, Snapshots and Logs live.

    Beside the app when that is a real, writable place -- the portable layout
    the tool is built around, and what happens on Windows and in development.
    On macOS "beside the app" usually is not writable: /Applications belongs to
    the system, and a quarantined app that was not moved in Finder launches
    from a read-only translocated copy under /private/var/folders. In either
    case the work goes to a stable per-user folder rather than to the temporary
    one the app happened to be run from.
    """
    here = app_dir()
    for candidate in (here, here.parent):
        if any(candidate.glob("*.blend")) and _usable(candidate):
            return candidate
    if _usable(here):
        return here
    return _fallback_project_dir()


PROJECT_DIR = _find_project_dir()

# Relative layout of the project, all anchored at PROJECT_DIR.
BLEND_NAME = "zaryadye_remap_v1.blend"
SOURCE_DIR_REL = "ToRemap"
OUTPUT_DIR_REL = "OUT"
SNAPSHOT_DIR_REL = "Snapshots"


def resolve(path: str | os.PathLike[str]) -> Path:
    """Turn a possibly relative path into an absolute one, anchored at the project."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_DIR / candidate
    return Path(os.path.normpath(candidate))


def display(path: str | os.PathLike[str]) -> str:
    """Show a path relative to the project folder whenever that is readable."""
    absolute = resolve(path)
    try:
        relative = Path(os.path.relpath(absolute, PROJECT_DIR))
    except ValueError:  # different drive on Windows
        return str(absolute)
    if str(relative).count("..") > 2:
        return str(absolute)
    return str(relative)


def project_dir_problem() -> str | None:
    """Why the working folder cannot be used, if that is the case.

    The folder is now chosen to be writable (see `_find_project_dir`), which on
    macOS steps around both the translocated launch copy and a read-only
    /Applications by falling back to a per-user folder. The only thing left to
    report is a genuinely stuck machine where even that cannot be made.
    """
    try:
        PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return f"Cannot create the working folder {PROJECT_DIR}: {error}"
    if not os.access(PROJECT_DIR, os.W_OK):
        return f"The folder {PROJECT_DIR} is read-only -- nothing can be unpacked or rendered there."
    return None


def bundled_path(name: str) -> Path | None:
    """A file shipped with the application: inside the bundle, else beside it."""
    roots = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        roots.append(Path(bundle))
    roots.append(app_dir())
    for root in roots:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def ensure_project_files() -> list[str]:
    """Make the folder self-sufficient: source and output folders, and the
    bundled .blend and settings if they are not there yet.

    An existing file is never touched -- only what is missing gets unpacked, so
    a newer .blend sitting next to the executable always wins over the snapshot
    baked in at build time.
    """
    global BLEND_FILE
    import shutil

    done: list[str] = []

    problem = project_dir_problem()
    if problem:
        return [problem]

    for folder in (SOURCE_DIR_REL, OUTPUT_DIR_REL):
        path = PROJECT_DIR / folder
        if not path.exists():
            try:
                path.mkdir(parents=True, exist_ok=True)
                done.append(f"created {folder}{os.sep}")
            except OSError as error:
                done.append(f"could not create {folder}: {error}")

    for name in (SETTINGS_FILE,):
        target = PROJECT_DIR / name
        if target.exists():
            continue
        source = bundled_path(name)
        if source is None or source == target:
            continue
        try:
            shutil.copy2(source, target)
            done.append(f"unpacked {name}")
        except OSError as error:
            done.append(f"could not unpack {name}: {error}")

    return done


TABLES_DIR = "tables"


def table_path(resolution: str) -> Path:
    """The baked lookup table for one resolution.

    Bundled into the executable when compiled, in tool/tables during
    development. Never an absolute path written down anywhere.
    """
    name = f"table_{resolution.lower()}.npz"
    bundled = bundled_path(name)
    if bundled is not None:
        return bundled
    for root in (app_dir() / TABLES_DIR, PROJECT_DIR / "tool" / TABLES_DIR):
        candidate = root / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"lookup table {name} not found next to the application")


GEOMETRY_NAME = "screen_geometry.npz"
VIEWER_TABLE_NAME = "viewer_table.npz"


def viewer_table_path() -> Path | None:
    """The baked view from the projection camera, if it shipped with the app."""
    bundled = bundled_path(VIEWER_TABLE_NAME)
    if bundled is not None:
        return bundled
    for root in (app_dir() / TABLES_DIR, PROJECT_DIR / "tool" / TABLES_DIR):
        candidate = root / VIEWER_TABLE_NAME
        if candidate.is_file():
            return candidate
    return None


def geometry_path() -> Path | None:
    """The baked screen for the 3D preview, if it travelled with the app."""
    bundled = bundled_path(GEOMETRY_NAME)
    if bundled is not None:
        return bundled
    for root in (app_dir() / TABLES_DIR, PROJECT_DIR / "tool" / TABLES_DIR):
        candidate = root / GEOMETRY_NAME
        if candidate.is_file():
            return candidate
    return None


def tables_present() -> bool:
    try:
        return all(table_path(name).is_file() for name, _ in RESOLUTION_PRESETS)
    except FileNotFoundError:
        return False


def load_settings() -> dict:
    """Small preferences file kept next to the .blend."""
    path = PROJECT_DIR / SETTINGS_FILE
    if not path.is_file():
        return {}
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(values: dict) -> None:
    """Write the settings, keeping the last two versions beside them.

    This file holds every framing anyone has aimed, keyed by file name, and
    those took real time to make. Two rolling copies cost nothing -- the whole
    file is a few kilobytes -- and they are the difference between a bad write
    being an annoyance and being an afternoon.
    """
    import json
    import shutil

    live = PROJECT_DIR / SETTINGS_FILE
    try:
        if live.is_file():
            older = live.with_suffix(live.suffix + ".bak2")
            newer = live.with_suffix(live.suffix + ".bak")
            if newer.is_file():
                shutil.copy2(newer, older)
            shutil.copy2(live, newer)
    except OSError:
        pass                        # a missing backup must not stop a save

    try:
        live.write_text(json.dumps(values, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    except OSError:
        pass


def find_overlay() -> Path | None:
    """The layout map drawn over the preview.

    Check.png travels with the application: beside the script when run from
    source, inside the bundle once compiled. A file picked by hand in the UI
    overrides it.
    """
    remembered = load_settings().get("overlay")
    if remembered and "_MEI" not in remembered and resolve(remembered).is_file():
        return resolve(remembered)

    candidates = [app_dir() / OVERLAY_NAME, PROJECT_DIR / "tool" / OVERLAY_NAME]
    bundle = getattr(sys, "_MEIPASS", None)  # PyInstaller one-file extraction
    if bundle:
        candidates.insert(0, Path(bundle) / OVERLAY_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def find_blend_file() -> Path | None:
    """The .blend to render: the expected name, else the only one in the folder."""
    expected = PROJECT_DIR / BLEND_NAME
    if expected.is_file():
        return expected
    candidates = sorted(PROJECT_DIR.glob("*.blend"))
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    return None


BLEND_FILE = find_blend_file()

# Names inside the .blend. Validated before every Blender call.
SCENE_NAME = "Scene"
CAMERA_NAME = "Render_Reprojection"
MATERIAL_NAME = "bottom_screen_LED"
TEXTURE_NODE_NAME = "Image Texture.004"
# The only mesh the camera actually sees; the other one sits far outside frame.
OBJECT_NAME = "SCREEN_BOTTOM_Remaped"
# Samples used when baking the lookup tables -- the scene renders with 2,
# but the coverage in the table has to be smooth, and this happens once.
BAKE_SAMPLES = 256

# Full render resolution of the screen. Note the .blend itself is currently set
# to 2304x792, which is Half here -- the wrapper writes resolution_x/y from this
# constant and then scales with resolution_percentage.
FULL_RESOLUTION = (4608, 1584)
# What the material may be running at, and what it may be written at. The
# input list is the gentleman's set; the output is what the wall takes.
SCENE_FPS = 30              # the default on both sides
INPUT_RATES = (24, 25, 30, 48, 50, 60)
OUTPUT_RATES = (30, 60)
OUTPUT_CONTAINER = "MPEG-4 / H.264 / CRF High"
CRF = "HIGH"

# Colour passthrough. OCIO is stripped from the child process environment, so
# Blender falls back to its own config where these two names exist.
VIEW_TRANSFORM = "Standard"
INPUT_COLORSPACE = "sRGB"
DISPLAY_DEVICE = "sRGB"

SOURCE_PRESETS = [
    ("ToRemap", SOURCE_DIR_REL),
]

# Composition-check overlay: the screen layout map drawn over the preview.
# It ships with the tool (4608x1584, the same frame as a Full render), so it is
# looked for beside the application, never by absolute path.
OVERLAY_NAME = "Check.png"
SETTINGS_FILE = "remap_tool.json"

# How a source frame's own transparency is read. It is a property of the file,
# not a preference: colour that never exceeds its own alpha was written
# premultiplied, and undoing the wrong convention is visible along every soft
# edge. Auto asks the first frame of the range and says what it found.
SOURCE_ALPHA_CHOICES = [
    ("Auto", "auto"),
    ("Premultiplied", "premultiplied"),
    ("Straight", "straight"),
    ("Ignore", "ignore"),
]

# Output formats. Alpha only exists where the container can carry it; H.264
# always composites over black, exactly as the Blender renders did.
OUTPUT_FORMATS = [
    ("H.264 mp4", "h264", ".mp4"),
    ("HEVC mp4", "hevc", ".mp4"),
    ("PNG sequence", "png", ".png"),
    ("ProRes mov", "prores", ".mov"),
]

# How the video is encoded. Measured on this footage, 200 frames at 4608x1584,
# whole pipeline:
#   libx264 medium      57 fps,  9.75 MB
#   hevc_nvenc p4 cq26  91 fps,  9.45 MB   <- same size, encoder stops costing
#   libx264 veryfast    ~62 fps, 9.3 MB
# NVENC cannot encode H.264 wider than 4096, so at Full it is only reachable
# through HEVC; at Half and Quarter it works for H.264 too.
ENCODER_CHOICES = [
    ("Auto (the card's own, where it fits)", "auto"),
    ("libx264 medium", "medium"),
    ("libx264 veryfast", "veryfast"),
]

# Settings chosen so an NVENC file lands on the same size as libx264 at crf 18.
NVENC_PRESET = "p4"
NVENC_CQ = 26

# macOS equivalent. Untested here -- there is no Mac on this machine -- so if
# the picture comes out too soft on that side, this is the number to raise.
VIDEOTOOLBOX_QUALITY = 60

# Apple Silicon encodes ProRes on a media engine of its own from the M1 Pro
# onwards. Plain M1 and M2 have none and fall back to prores_ks, which the
# probe in remap_render works out by asking rather than by model name.

RESOLUTION_PRESETS = [
    ("Full", 100),
    ("Half", 50),
    ("Quarter", 25),
]

# Where Blender might live, per platform.
BLENDER_SEARCH_WINDOWS = [
    Path(r"C:\Program Files\Blender Foundation"),
    Path(r"C:\Program Files (x86)\Blender Foundation"),
]
BLENDER_SEARCH_MACOS = [Path("/Applications")]

# The .blend was written by Blender 5.2.44 -- 4.5 cannot open it at all and
# 5.0 opens it with "expect loss of data".
MIN_BLENDER_VERSION = (5, 2)
BLOCKED_BELOW_VERSION = (5, 0)
