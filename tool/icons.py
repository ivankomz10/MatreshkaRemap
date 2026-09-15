"""The window's icons, kept as shapes and coloured when they are drawn.

The set comes out of Houdini's, rendered once by `_make_icons.py` into flat
alpha masks. Nothing about the original colour survives, which is the point:
this window follows whatever theme the machine is set to, and an icon drawn
light on a light background is worse than no icon at all.

Colour is taken from the widget the icon is going on, not from the application,
because a button and a label do not always agree about what their text colour
is. Every icon handed out is remembered, so a theme that changes after the
window is built repaints them all rather than leaving them in yesterday's ink.

Resolve was the first place looked. Its interface icons are compiled into the
binary; the only images on disk are labels for the hardware panel and a handful
of Qt style bitmaps, so there was nothing there to take.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QAbstractButton, QApplication

FOLDER = "icons"
SIZE = 18                    # big enough to read next to nine-point type

_cache: dict[tuple[str, int, str], QIcon] = {}
_missing: set[str] = set()
# Everything an icon was put on, so a change of theme can reach all of it.
_applied: list[tuple[object, str, int]] = []


def _where() -> Path | None:
    """Beside this file when run from source, inside the bundle when frozen."""
    here = Path(__file__).resolve().parent / FOLDER
    bundle = getattr(sys, "_MEIPASS", None)
    for candidate in ([Path(bundle) / FOLDER] if bundle else []) + [here]:
        if candidate.is_dir():
            return candidate
    return None


def ink(widget=None) -> QColor:
    """The colour this widget's own text is drawn in.

    Not softened towards the background: an icon at eighteen pixels is mostly
    thin strokes, and a quarter less contrast is the difference between reading
    it and not.
    """
    if widget is not None:
        palette = widget.palette()
        role = (QPalette.ColorRole.ButtonText if isinstance(widget, QAbstractButton)
                else QPalette.ColorRole.WindowText)
        return palette.color(QPalette.ColorGroup.Active, role)
    app = QApplication.instance()
    if app is None:
        return QColor("#303030")
    return app.palette().color(QPalette.ColorRole.WindowText)


def _shape(name: str, size: int, tint: QColor) -> QIcon:
    key = (name, size, tint.name())
    found = _cache.get(key)
    if found is not None:
        return found

    folder = _where()
    path = None if folder is None else folder / f"{name}.png"
    if path is None or not path.is_file():
        if name not in _missing:
            _missing.add(name)
            print(f"icon missing: {name}")
        _cache[key] = QIcon()
        return _cache[key]

    drawn = QImage(str(path)).scaled(
        size, size, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation)
    painter = QPainter(drawn)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(drawn.rect(), tint)
    painter.end()
    icon = QIcon(QPixmap.fromImage(drawn))
    _cache[key] = icon
    return icon


def get(name: str, size: int = SIZE, colour: QColor | None = None,
        widget=None) -> QIcon:
    """One icon, tinted and cached. An unknown name gives an empty icon."""
    return _shape(name, size, colour or ink(widget))


def put(button, name: str, size: int = SIZE, colour: QColor | None = None) -> None:
    """Give a button an icon and remember it, so a new theme can repaint it."""
    icon = _shape(name, size, colour or ink(button))
    if icon.isNull():
        return
    button.setIcon(icon)
    button.setIconSize(QSize(size, size))
    if colour is None:
        _applied.append((button, name, size))


def mark(label, name: str, size: int = SIZE) -> None:
    """The same for a plain label standing in for a heading."""
    icon = _shape(name, size, ink(label))
    if icon.isNull():
        return
    label.setPixmap(icon.pixmap(QSize(size, size)))
    _applied.append((label, name, size))


def refresh() -> None:
    """Repaint every icon handed out, in whatever colour the theme now wants."""
    _cache.clear()
    for widget, name, size in list(_applied):
        try:
            if isinstance(widget, QAbstractButton):
                widget.setIcon(_shape(name, size, ink(widget)))
                widget.setIconSize(QSize(size, size))
            else:
                widget.setPixmap(_shape(name, size, ink(widget)).pixmap(QSize(size, size)))
        except RuntimeError:
            _applied.remove((widget, name, size))   # the widget is gone
