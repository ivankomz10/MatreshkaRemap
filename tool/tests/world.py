"""What every check needs: the tables, a window, and a way to press things.

Everything here is built once and handed out. Loading a table is twenty
milliseconds and building the main window is a second and a half, and a suite
that pays that per check is a suite nobody waits for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

TOOL = Path(__file__).resolve().parent.parent
if str(TOOL) not in sys.path:
    sys.path.insert(0, str(TOOL))

PROJECT = TOOL.parent
SCRATCH = Path(__file__).resolve().parent / "_scratch"

import constants                                     # noqa: E402
import gizmo                                         # noqa: E402
import remap_engine                                  # noqa: E402
import transform as xf                               # noqa: E402

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import (QColor, QDragEnterEvent, QDropEvent,          # noqa: E402
                           QKeyEvent, QMouseEvent, QPixmap, QWheelEvent)
from PySide6.QtWidgets import QApplication                               # noqa: E402

_app = None
_tables: dict[str, remap_engine.Table] = {}
_maps: dict[str, gizmo.WindowMap] = {}
_window = None


# -- the application ------------------------------------------------------

def app() -> QApplication:
    """One application for the whole run, themed the way the app themes it."""
    global _app
    if _app is None:
        import main as m
        _app = QApplication.instance() or QApplication(sys.argv[:1])
        _app.setStyle("Fusion")
        _app.setPalette(m.dark_palette())
        _app.setStyleSheet(m.STYLESHEET)
    return _app


def window():
    """The real main window, built once.

    Checks that change it should put it back. The ones here either only read,
    or drive a widget that the next check re-seeds anyway.
    """
    global _window
    if _window is None:
        import main as m
        app()
        _window = m.MainWindow()
        _window.resize(1400, 880)
        _window.show()
        pump()
    return _window


def pump(times: int = 3) -> None:
    for _ in range(times):
        app().processEvents()


# -- the baked geometry ---------------------------------------------------

def table(name: str = "Full") -> remap_engine.Table:
    if name not in _tables:
        _tables[name] = remap_engine.Table(constants.table_path(name))
    return _tables[name]


def viewer_table() -> remap_engine.Table:
    if "__viewer__" not in _tables:
        path = constants.viewer_table_path()
        if path is None:
            raise FileNotFoundError("no baked camera view beside the project")
        _tables["__viewer__"] = remap_engine.Table(path)
    return _tables["__viewer__"]


def window_map(name: str = "Full", viewer: bool = False) -> gizmo.WindowMap:
    key = f"{name}{'-viewer' if viewer else ''}"
    if key not in _maps:
        _maps[key] = gizmo.WindowMap(table(name),
                                     viewer_table() if viewer else None)
    return _maps[key]


BOTH_VIEWS = (("flat", False), ("camera view", True))


def framing(scale: float = 0.6) -> xf.Transform:
    """A clip sitting inside the window, which is what aiming looks like.

    Not the full-window fit: at that size the corners land on the folds of the
    wall, where the map is stationary and a handle cannot track a cursor no
    matter how the drag is written. That is a property of the screen, and a
    check that fails on it is a check measuring the wall.
    """
    placement = xf.Transform().fitted(1920, 1080, False)
    placement.scale_x *= scale
    placement.scale_y *= scale
    return placement


# -- a preview to press on ------------------------------------------------

def preview(shape: gizmo.WindowMap, placement=None, size=(1000, 560)):
    """An ImageView showing a frame of the given map's size, fitted."""
    import main as m
    app()
    view = m.ImageView()
    view.resize(*size)
    view.show()
    view.set_pixmap(QPixmap(shape.width, shape.height))
    view.show_gizmo(placement if placement is not None else framing(), shape, True)
    view.fit()
    pump()
    return view


def press(view, at) -> None:
    _mouse(view, QEvent.Type.MouseButtonPress, at, Qt.MouseButton.NoButton)


def move_to(view, at) -> None:
    _mouse(view, QEvent.Type.MouseMove, at, Qt.MouseButton.LeftButton)


def release(view, at) -> None:
    _mouse(view, QEvent.Type.MouseButtonRelease, at, Qt.MouseButton.NoButton)


def _mouse(view, kind, at, held) -> None:
    spot = QPointF(at)
    event = QMouseEvent(kind, spot, view.viewport().mapToGlobal(spot),
                        Qt.MouseButton.LeftButton, held,
                        Qt.KeyboardModifier.NoModifier)
    if kind == QEvent.Type.MouseButtonPress:
        view._on_press(event)
    elif kind == QEvent.Type.MouseMove:
        view.mouseMoveEvent(event)
    else:
        view.mouseReleaseEvent(event)


def drag(view, start, dx, dy, steps=(0.3, 0.6, 1.0)):
    """Press, move in several steps the way a hand does, release."""
    press(view, start)
    held = view.gizmo._grab
    for part in steps:
        move_to(view, QPointF(start.x() + dx * part, start.y() + dy * part))
    release(view, QPointF(start.x() + dx, start.y() + dy))
    return held


def wheel(widget, at, notches: int) -> None:
    event = QWheelEvent(QPointF(at), widget.mapToGlobal(QPointF(at)),
                        QPoint(0, 0), QPoint(0, 120 * notches),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    widget.wheelEvent(event)


def key(widget, code, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, code, modifiers))


def drop_file(widget, path: Path, at=None) -> None:
    """A real drag and drop of one file onto one widget."""
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(path))])
    spot = QPointF(at if at is not None else widget.rect().center())
    widget.dragEnterEvent(QDragEnterEvent(
        spot.toPoint(), Qt.DropAction.CopyAction, data,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    widget.dropEvent(QDropEvent(
        spot, Qt.DropAction.CopyAction, data,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


# -- pictures to feed it --------------------------------------------------

def scratch() -> Path:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    return SCRATCH


def holed_png(name: str = "holed.png", size=(320, 240)) -> Path:
    """A picture with a genuine hole in the middle of it.

    Straight alpha, opaque red frame, transparent centre -- the shape of the
    ProRes 4444 clip that once came back with a black square in it.
    """
    from PySide6.QtGui import QImage
    width, height = size
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[..., 0] = 220
    pixels[..., 1] = 40
    pixels[..., 2] = 40
    pixels[..., 3] = 255
    pixels[height // 4: 3 * height // 4, width // 4: 3 * width // 4, 3] = 0
    path = scratch() / name
    picture = QImage(bytes(pixels.tobytes()), width, height, width * 4,
                     QImage.Format.Format_RGBA8888)
    picture.save(str(path))
    return path


def flat_png(name: str, colour=(120, 180, 240, 255), size=(320, 240)) -> Path:
    from PySide6.QtGui import QImage
    width, height = size
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[...] = colour
    path = scratch() / name
    picture = QImage(bytes(pixels.tobytes()), width, height, width * 4,
                     QImage.Format.Format_RGBA8888)
    picture.save(str(path))
    return path


# -- reading colours off widgets -----------------------------------------

def contrast(first: QColor, second: QColor) -> float:
    """The WCAG contrast ratio, 1 for identical and 21 for black on white."""
    def light(colour: QColor) -> float:
        parts = []
        for value in (colour.redF(), colour.greenF(), colour.blueF()):
            parts.append(value / 12.92 if value <= 0.04045
                         else ((value + 0.055) / 1.055) ** 2.4)
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]
    one, two = sorted((light(first), light(second)))
    return (two + 0.05) / (one + 0.05)


def painted(widget) -> np.ndarray:
    """What the widget actually draws, as pixels."""
    pump()
    picture = widget.grab().toImage().convertToFormat(
        QPixmap(1, 1).toImage().Format.Format_RGBA8888)
    width, height = picture.width(), picture.height()
    raw = picture.constBits().tobytes()[: width * height * 4]
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4).copy()
