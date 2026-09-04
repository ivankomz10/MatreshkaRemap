"""Filesystem scanning: image sequences on disk and Blender installations.

This part is real even in the prototype -- it is what has to survive contact
with a folder holding 2665 files and a stray .mp4 next to them.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import constants

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff",
    ".tga", ".dpx", ".bmp", ".webp",
}
# Movies are sources in their own right; Blender reads them directly.
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".mxf"}

_TRAILING_NUMBER = re.compile(r"^(.*?)(\d+)$")


@dataclass
class Sequence:
    kind = "sequence"

    directory: Path
    prefix: str
    extension: str
    padding: int
    numbers: list[int] = field(default_factory=list)
    width: int = 0
    height: int = 0

    @property
    def first(self) -> int:
        return self.numbers[0]

    @property
    def last(self) -> int:
        return self.numbers[-1]

    @property
    def count(self) -> int:
        return len(self.numbers)

    @property
    def name(self) -> str:
        """Human name: the prefix without its trailing separators."""
        return self.prefix.rstrip("_-. ") or self.directory.name

    @property
    def missing(self) -> list[int]:
        return sorted(set(range(self.first, self.last + 1)) - set(self.numbers))

    @property
    def is_contiguous(self) -> bool:
        return self.last - self.first + 1 == self.count

    def path_for(self, number: int) -> Path:
        return self.directory / f"{self.prefix}{number:0{self.padding}d}{self.extension}"

    @property
    def resolution_label(self) -> str:
        return f"{self.width}x{self.height}" if self.width and self.height else "-"

    @property
    def range_label(self) -> str:
        return f"{self.first} - {self.last}"

    @property
    def pattern_label(self) -> str:
        return f"{self.prefix}{'#' * self.padding}{self.extension}"

    @property
    def count_label(self) -> str:
        return str(self.count)

    @property
    def source_path(self) -> Path:
        return self.path_for(self.first)


@dataclass
class Movie:
    """A single video file used as the source.

    Exposes the same surface as Sequence so the window does not care which it
    is holding. Frame count and size stay at zero until Blender has probed the
    file -- nothing else can read them without pulling in another dependency.
    """

    kind = "movie"
    padding = 0

    path: Path
    frames: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0            # what the container says, once probed

    @property
    def directory(self) -> Path:
        return self.path.parent

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()

    @property
    def first(self) -> int:
        return 1

    @property
    def last(self) -> int:
        return max(1, self.frames)

    @property
    def count(self) -> int:
        return self.frames

    @property
    def count_label(self) -> str:
        return str(self.frames) if self.frames else "?"

    @property
    def missing(self) -> list[int]:
        return []

    @property
    def is_contiguous(self) -> bool:
        return True

    @property
    def is_probed(self) -> bool:
        return self.frames > 0

    def path_for(self, number: int) -> Path:
        return self.path

    @property
    def source_path(self) -> Path:
        return self.path

    @property
    def resolution_label(self) -> str:
        return f"{self.width}x{self.height}" if self.width and self.height else "-"

    @property
    def range_label(self) -> str:
        return f"1 - {self.frames}" if self.frames else "?"

    @property
    def pattern_label(self) -> str:
        return self.path.name


def probe_size(path: Path) -> tuple[int, int]:
    """Image dimensions, read from the header rather than by decoding."""
    try:
        import imagefile

        return imagefile.read_size(path)
    except Exception:
        return 0, 0


def _scan_movies(directory: Path) -> list[Movie]:
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return []
    return [
        Movie(Path(entry.path))
        for entry in entries
        if entry.is_file() and os.path.splitext(entry.name)[1].lower() in VIDEO_EXTENSIONS
    ]


def _scan_one_directory(directory: Path) -> list[Sequence]:
    groups: dict[tuple[str, str, int], list[int]] = {}
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return []

    for entry in entries:
        if not entry.is_file():
            continue
        stem, extension = os.path.splitext(entry.name)
        extension = extension.lower()
        if extension not in IMAGE_EXTENSIONS:
            continue
        match = _TRAILING_NUMBER.match(stem)
        if not match:
            continue
        prefix, digits = match.group(1), match.group(2)
        groups.setdefault((prefix, extension, len(digits)), []).append(int(digits))

    sequences = []
    for (prefix, extension, padding), numbers in groups.items():
        if len(numbers) < 2:
            continue  # a lone file is not a sequence
        numbers.sort()
        sequence = Sequence(directory, prefix, extension, padding, numbers)
        sequence.width, sequence.height = probe_size(sequence.path_for(sequence.first))
        sequences.append(sequence)
    return sequences


def scan_folder(root: Path) -> list:
    """Scan `root` and its immediate subdirectories for sequences and movies."""
    if not root.is_dir():
        return []

    found = _scan_one_directory(root) + _scan_movies(root)
    try:
        for entry in os.scandir(root):
            if entry.is_dir():
                child = Path(entry.path)
                found.extend(_scan_one_directory(child))
                found.extend(_scan_movies(child))
    except OSError:
        pass

    # Longest first; movies that have not been probed yet have no count and
    # sort to the bottom rather than pretending to be empty.
    found.sort(key=lambda source: (-source.count, source.name.lower()))
    return found


# ---------------------------------------------------------------------------
# Blender discovery


@dataclass
class BlenderInstall:
    path: Path
    version: tuple[int, ...]

    @property
    def label(self) -> str:
        return ".".join(str(part) for part in self.version) if self.version else self.path.name

    @property
    def is_blocked(self) -> bool:
        return bool(self.version) and self.version < constants.BLOCKED_BELOW_VERSION

    @property
    def is_risky(self) -> bool:
        return bool(self.version) and not self.is_blocked and \
            self.version < constants.MIN_BLENDER_VERSION


_VERSION_IN_NAME = re.compile(r"(\d+)\.(\d+)")


def _version_from_name(name: str) -> tuple[int, ...]:
    match = _VERSION_IN_NAME.search(name)
    return (int(match.group(1)), int(match.group(2))) if match else ()


def find_blender_installs() -> list[BlenderInstall]:
    installs: list[BlenderInstall] = []

    if sys.platform.startswith("win"):
        for root in constants.BLENDER_SEARCH_WINDOWS:
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                executable = child / "blender.exe"
                if executable.is_file():
                    installs.append(BlenderInstall(executable, _version_from_name(child.name)))
    elif sys.platform == "darwin":
        for root in constants.BLENDER_SEARCH_MACOS:
            if not root.is_dir():
                continue
            for child in sorted(root.glob("Blender*.app")):
                executable = child / "Contents" / "MacOS" / "Blender"
                if executable.is_file():
                    installs.append(BlenderInstall(executable, _version_from_name(child.stem)))
    else:
        from shutil import which

        found = which("blender")
        if found:
            installs.append(BlenderInstall(Path(found), ()))

    installs.sort(key=lambda install: install.version, reverse=True)
    return installs
