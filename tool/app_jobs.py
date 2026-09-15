"""What the window needs from the renderer: previews, probing, render jobs.

Preview is synchronous -- a frame costs a few milliseconds, so it can happen
straight on the UI thread while the timeline handle is being dragged. Only the
full render needs a thread, and only because it runs for minutes.

Decoding here goes through PyAV rather than a process: for a single frame there
is nothing to parallelise, and spawning ffmpeg would cost more than the decode.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QThread, Signal

import avio
import constants
import depends
import framecache
import remap_engine
import remap_render


def source_for(item, first_file: int, fps: float = 0.0) -> remap_render.Source:
    """Turn a scanned sequence or movie into something ffmpeg can read.

    `fps` is how fast the material is meant to run. A movie usually says so
    itself and a sequence never can, so the window settles it and passes the
    answer down rather than letting each layer guess again.
    """
    if item.kind == "movie":
        return remap_render.Source(str(item.path), False, item.first,
                                   item.width, item.height, fps,
                                   float(getattr(item, "fps", 0.0) or 0.0))
    pattern = str(item.directory / f"{item.prefix}%0{item.padding}d{item.extension}")
    return remap_render.Source(pattern, True, first_file, item.width, item.height,
                               fps)


def probe_movie(path: Path) -> tuple[int, int, int, float]:
    """Frames, width, height and rate, from the container rather than decoded."""
    return avio.probe(path)


def read_frame(item, number: int, keep_alpha: bool = False):
    """One source frame as YUV planes, plus the object that owns their memory.

    With `keep_alpha` the planes come back as None and the frame is RGBA
    instead: 4:2:0 has nowhere to put transparency.
    """
    if item.kind == "movie":
        return avio.read_single_frame(str(item.path), False, number, item.first,
                                      keep_alpha)
    return avio.read_single_frame(str(item.path_for(number)), True, 0, 0,
                                  keep_alpha)


def _to_rgba(frame) -> np.ndarray:
    """A decoded frame as RGBA, for the renderer that cannot take planes."""
    return frame.to_ndarray(format="rgba")


class PreviewRenderer:
    """Keeps one engine alive per table and source size.

    Building a GPU device and uploading a table takes about half a second, so
    it must not happen per frame -- but it does have to happen again when the
    resolution or the source changes shape.
    """

    def __init__(self) -> None:
        self._engines: dict[tuple[str, int, int], object] = {}
        self._tables: dict[str, remap_engine.Table] = {}
        self.last_error = ""
        # Scrubbing back and forth over the same frame at different
        # resolutions should not decode it again.
        self._cache_key: tuple | None = None
        self._cache_value = None

    def table(self, resolution: str) -> remap_engine.Table:
        cached = self._tables.get(resolution)
        if cached is None:
            cached = remap_engine.Table(constants.table_path(resolution))
            self._tables[resolution] = cached
        return cached

    def engine(self, resolution: str, source_size: tuple[int, int]):
        key = (resolution, *source_size)
        engine = self._engines.get(key)
        if engine is None:
            engine = remap_engine.make_remapper(self.table(resolution), source_size)
            self._engines[key] = engine
        return engine

    def decode(self, item, number: int, keep_alpha: bool = False):
        key = (str(item.source_path), number, keep_alpha)
        if key != self._cache_key:
            self._cache_value = read_frame(item, number, keep_alpha)
            self._cache_key = key
        return self._cache_value

    def viewer_engine(self, frame_size: tuple[int, int]):
        """The renderer that turns a flat frame into the view from the camera.

        It is the same machinery as the reprojection -- a table and a gather --
        because the camera and the screen are both fixed, so where each pixel
        of that view comes from never changes either.
        """
        key = ("__viewer__", *frame_size)
        engine = self._engines.get(key)
        if engine is None:
            path = constants.viewer_table_path()
            if path is None:
                raise FileNotFoundError("no baked view beside the application")
            engine = remap_engine.make_remapper(remap_engine.Table(path), frame_size)
            engine.set_premultiplied(True)
            if hasattr(engine, "set_output_yuv"):
                engine.set_output_yuv(False)
            self._engines[key] = engine
        return engine

    def render(self, item, number: int, resolution: str,
               supersample: bool = False,
               alpha_mode: int = remap_engine.ALPHA_IGNORE,
               placement=None) -> np.ndarray:
        keep = alpha_mode != remap_engine.ALPHA_IGNORE
        owner, planes = self.decode(item, number, keep)
        engine = self.engine(resolution, (item.width, item.height))
        engine.set_premultiplied(True)
        if hasattr(engine, "set_source_alpha"):
            engine.set_source_alpha(alpha_mode)
        if hasattr(engine, "set_transform"):
            engine.set_transform(placement)
        if hasattr(engine, "set_supersample"):
            engine.set_supersample(supersample)
        if hasattr(engine, "set_output_yuv"):
            engine.set_output_yuv(False)      # the window wants pixels, not planes
        if planes is None or engine.name != "GPU":
            return engine.render(_to_rgba(owner))
        return engine.render(planes)

    @property
    def description(self) -> str:
        for engine in self._engines.values():
            adapter = getattr(engine, "adapter_name", "")
            backend = getattr(engine, "backend", "")
            return f"{engine.name} {adapter} ({backend})".strip()
        return ""


def held_from(owner, planes, keep_alpha: bool):
    """One decoded frame in a shape the cache can own.

    libav's planes point into its own buffer and die with the frame that made
    them, so what is kept is a copy. Copies of the planes rather than of the
    pixels wherever the engine will take planes: a third of the bytes for the
    same picture.
    """
    if keep_alpha or planes is None:
        return framecache.Held(pixels=np.ascontiguousarray(
            owner.to_ndarray(format="rgba")))
    kept = [(np.frombuffer(bytes(plane.data), dtype=np.uint8),
             plane.stride, plane.width, plane.height) for plane in planes]
    return framecache.Held(planes=kept)


def planes_of(held) -> list:
    """The cache's copies, dressed as the planes the engine expects."""
    return [avio.Plane(memoryview(data), stride, width, height)
            for data, stride, width, height in held.planes]


class WarmJob(QThread):
    """Fill the cache from the first frame of the range, straight through.

    One reader for the whole run and one pass through it. Opening a container
    costs far more than decoding a frame out of one already open, and a
    decoder that is already running at the right place is the cheapest frame
    there is -- so the fastest way to hold two hundred frames is to ask for
    them in the order they are stored.

    This was briefly done outwards from the playhead instead, in blocks of
    forty-eight, so that the stretch around whatever was being looked at
    arrived first. It filled the right frames in the right order and it was
    slower to fill them, because every block is another open and another seek,
    and on a long clip that is most of the work. Warming is something you set
    going and leave; the order it arrives in is worth less than how soon it is
    all there.

    What made the playhead matter was never the warming. It was that Play
    began at the start of the warm run rather than where the hand was, and
    that is fixed where it belongs, in the player.
    """

    warmed = Signal(int, int)        # frames held, bytes spent
    finished_warming = Signal(str)   # why it ended, for the note in the window

    def __init__(self, item, cache, first: int, last: int, keep_alpha: bool,
                 parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.cache = cache
        # Not `start`: QThread already has one of those, and shadowing it
        # turns `job.start()` into an integer nobody can call.
        self.first = first
        self.last = last
        self.keep_alpha = keep_alpha
        self._stop = False

    def cancel(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D102 -- QThread entry point
        wanted = max(0, self.last - self.first + 1)
        if wanted <= 0:
            self.finished_warming.emit("")
            return
        why = "full"
        try:
            reader = avio.Reader(
                str(self.item.path) if self.item.kind == "movie"
                else str(self.item.directory / f"{self.item.prefix}"
                         f"%0{self.item.padding}d{self.item.extension}"),
                self.item.kind != "movie", self.first, wanted,
                getattr(self.item, "first", 0), self.keep_alpha)
        except Exception as error:  # noqa: BLE001 -- reported, never raised
            self.finished_warming.emit(str(error))
            return

        number = self.first
        try:
            for owner, planes in reader.frames():
                if self._stop:
                    why = "stopped"
                    break
                if not self.cache.put(number, held_from(owner, planes,
                                                        self.keep_alpha)):
                    why = "memory"
                    break
                number += 1
                if number % 8 == 0:
                    self.warmed.emit(self.cache.count, self.cache.spent)
            else:
                why = "done"
        except Exception as error:  # noqa: BLE001 -- half a warm run is still useful
            why = str(error)
        finally:
            reader.close()
        self.warmed.emit(self.cache.count, self.cache.spent)
        self.finished_warming.emit(why)


class RenderJob(QThread):
    """A full render, off the UI thread."""

    progress = Signal(int, int, float, float)   # done, total, fps, eta
    info = Signal(str)
    failed = Signal(str)
    finished_ok = Signal(dict)

    def __init__(self, item, first_file: int, last_file: int,
                 table_path: Path, output: remap_render.Output, parent=None,
                 viewer_table_path: Path | None = None,
                 overlay=None, overlay_opacity: float = 0.0,
                 source_fps: float = 0.0, alpha_mode: str = "auto",
                 placement=None) -> None:
        super().__init__(parent)
        self.item = item
        self.source_fps = source_fps
        self.alpha_mode = alpha_mode
        self.placement = placement
        self.first_file = first_file
        self.last_file = last_file
        self.table_path = table_path
        self.output = output
        # A flipbook: the second lookup and the layout map the window is
        # showing, so the file comes out as what was on screen.
        self.viewer_table_path = viewer_table_path
        self.overlay = overlay
        self.overlay_opacity = overlay_opacity
        self._stop = False

    def cancel(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D102 -- QThread entry point
        try:
            source = source_for(self.item, self.first_file, self.source_fps)
            source.alpha_mode = self.alpha_mode
            source.placement = self.placement
            result = remap_render.render(
                source, self.table_path, self.output,
                self.first_file, self.last_file,
                on_progress=lambda p: self.progress.emit(
                    p.frames_done, p.total, p.fps, p.eta),
                should_stop=lambda: self._stop,
                viewer_table_path=self.viewer_table_path,
                overlay=self.overlay,
                overlay_opacity=self.overlay_opacity,
            )
            if result["cancelled"]:
                self.failed.emit("cancelled")
            else:
                self.finished_ok.emit(result)
        except Exception as error:  # noqa: BLE001 -- surfaced in the window
            self.failed.emit(str(error))


class DownloadJob(QThread):
    """Fetching ffmpeg, off the UI thread."""

    progress = Signal(int, int)                 # bytes done, bytes total
    failed = Signal(str)
    finished_ok = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop = False

    def cancel(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D102 -- QThread entry point
        try:
            path = depends.install_ffmpeg(
                on_progress=lambda done, total: self.progress.emit(done, total),
                should_stop=lambda: self._stop,
            )
            self.finished_ok.emit(str(path))
        except Exception as error:  # noqa: BLE001 -- shown in the dialog
            self.failed.emit(str(error))
