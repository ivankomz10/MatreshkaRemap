"""Matreshka Remap Renderer -- window and wiring.

Scanning and path handling live in scan.py / constants.py; the renderer is
remap_engine.py behind app_jobs.py, and the frame pipeline is remap_render.py.
Blender is not involved at run time -- it only ever baked the lookup tables.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QImage,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QDialog, QFileDialog,
    QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QScrollArea,
    QSizePolicy, QSlider, QSpinBox, QSplitter, QStackedWidget, QTableWidget,
    QTableWidgetItem,
    QVBoxLayout, QWidget,
)

import app_jobs
import constants
import depends
import imagefile
import logfile
import preview3d
import remap_engine
import remap_render
import scan

IDLE_NOTE = "Ready. Drop a frame on the preview, or scrub the timeline."

# Font families in preference order -- Qt takes the first one present, so the
# same code looks native on Windows and on macOS.
UI_FONTS = ["Segoe UI", "Helvetica Neue", "Arial"]
MONO_FONTS = ["Consolas", "Menlo", "Monaco", "Courier New"]


class CheckBox(QCheckBox):
    """A checkbox that paints its own box, tick and label.

    Qt's stylesheet can colour an indicator but cannot put a tick inside it
    without an image file, and the platform's own indicator ignores the
    disabled colour used here -- so a disabled box looked live and a checked
    one was just a filled square. Painting it by hand settles both.
    """

    BOX = 14

    def paintEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        enabled, checked = self.isEnabled(), self.isChecked()
        top = (self.height() - self.BOX) / 2.0
        box = QRectF(0.5, top + 0.5, self.BOX - 1, self.BOX - 1)

        if checked:
            fill = QColor("#7fb2d9") if enabled else QColor("#3f4c56")
            border = QColor("#a9d0ea") if enabled else QColor("#3a3a3a")
        else:
            fill = QColor("#1b1b1b") if enabled else QColor("#262626")
            border = QColor("#4a4a4a") if enabled else QColor("#333333")

        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 3, 3)

        if checked:
            tick = QPainterPath()
            tick.moveTo(box.left() + 3.2, box.center().y() + 0.2)
            tick.lineTo(box.center().x() - 0.6, box.bottom() - 3.4)
            tick.lineTo(box.right() - 2.8, box.top() + 3.6)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#14232d") if enabled else QColor("#6a6a6a"),
                                2.0, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(tick)

        painter.setPen(QColor("#dcdcdc") if enabled else QColor("#5a5a5a"))
        painter.drawText(
            QRectF(self.BOX + 6, 0, self.width() - self.BOX - 6, self.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self.text())

    def sizeHint(self) -> QSize:  # noqa: N802
        width = self.BOX + 6 + self.fontMetrics().horizontalAdvance(self.text()) + 4
        return QSize(width, max(super().sizeHint().height(), self.BOX + 4))


def _unique_path(folder: Path, name: str) -> Path:
    """The given name, or the next free _2, _3 ... beside it."""
    candidate = folder / name
    stem, suffix = candidate.stem, candidate.suffix
    index = 2
    while candidate.exists():
        candidate = folder / f"{stem}_{index}{suffix}"
        index += 1
    return candidate



class DependencyDialog(QDialog):
    """What the machine has, and an offer to fetch what it has not.

    Everything Python is inside the executable; ffmpeg is not, because it is
    large and its licence makes shipping a copy inside someone else's binary a
    question better left alone. So it is looked for once, and offered.
    """

    def __init__(self, found, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("What this machine has")
        self.setMinimumWidth(680)
        self.job: app_jobs.DownloadJob | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        intro = QLabel(
            "Checked once, on the first run. Nothing is installed and nothing "
            "goes on PATH: a downloaded copy lands in a folder beside this "
            "application, and deleting that folder undoes it.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(8)
        self.grid.setColumnMinimumWidth(1, 60)
        self.grid.setColumnStretch(2, 1)
        layout.addLayout(self.grid)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        buttons = QHBoxLayout()
        self.get_button = QPushButton()
        self.get_button.clicked.connect(self._start_download)
        buttons.addWidget(self.get_button)
        buttons.addStretch(1)
        self.close_button = QPushButton("Continue")
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self._show(found)

    # -- the list ----------------------------------------------------------

    def _show(self, found) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        for row, item in enumerate(found):
            name = QLabel(item.name)
            name.setFont(QFont("", -1, QFont.Weight.Bold))
            if item.ok:
                mark, colour = "found", "#8fbf8f"
            elif item.required:
                mark, colour = "missing", "#e06c6c"
            else:
                mark, colour = "absent", "#d9a441"
            status = QLabel(mark)
            status.setStyleSheet(f"color:{colour};")
            detail = QLabel(item.detail)
            detail.setWordWrap(True)
            detail.setStyleSheet("color:#9a9a9a;")
            for column, widget in enumerate((name, status, detail)):
                self.grid.addWidget(widget, row, column)

        wanted = next((item for item in found
                       if not item.ok and item.required and item.fixable), None)
        self.get_button.setVisible(wanted is not None)
        if wanted is not None:
            self.get_button.setText(
                f"Download {wanted.name} ({depends.download_size_mb()} MB)")

        missing = [item.name for item in found if item.required and not item.ok]
        if missing:
            self._note(f"Rendering needs {', '.join(missing)}.", "warn")
        else:
            self._note("Everything needed is here.")

    def _note(self, text: str, level: str = "") -> None:
        colours = {"error": "color:#e06c6c;", "warn": "color:#d9a441;"}
        self.note.setText(text)
        self.note.setStyleSheet(colours.get(level, "color:#8fbf8f;"))

    # -- fetching ----------------------------------------------------------

    def _start_download(self) -> None:
        if self.job is not None:
            return
        url = depends.DOWNLOADS[sys.platform][0]
        self._note(f"downloading from {url.split('/')[2]} ...")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.get_button.setEnabled(False)
        self.close_button.setEnabled(False)

        self.job = app_jobs.DownloadJob(self)
        self.job.progress.connect(self._on_progress)
        self.job.failed.connect(self._on_failed)
        self.job.finished_ok.connect(self._on_finished)
        self.job.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setValue(int(100 * done / total))
        self.progress.setFormat(
            f"{done / 1e6:.0f} / {total / 1e6:.0f} MB" if total
            else f"{done / 1e6:.0f} MB")

    def _on_failed(self, message: str) -> None:
        self.job = None
        self.progress.setVisible(False)
        self.get_button.setEnabled(True)
        self.close_button.setEnabled(True)
        self._note(f"could not fetch it: {message}", "error")

    def _on_finished(self, path: str) -> None:
        self.job = None
        self.progress.setVisible(False)
        self.get_button.setEnabled(True)
        self.close_button.setEnabled(True)
        remap_render.refresh_ffmpeg()
        self._show(depends.check())
        self._note(f"ready: {constants.display(Path(path))}")

    def reject(self) -> None:  # noqa: D102 -- Esc must not orphan the download
        if self.job is not None:
            self.job.cancel()
        super().reject()


class ImageView(QGraphicsView):
    """Preview surface: wheel to zoom, drag to pan, drop a file to render it.

    The view has to handle drags itself: QGraphicsView swallows them at the
    viewport, so a drop here would never reach the window underneath.
    """

    fileDropped = Signal(str)
    modeChanged = Signal(int)

    MODES = ["Flat", "Viewer"]

    def __init__(self) -> None:
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = QGraphicsPixmapItem()
        self._scene.addItem(self._item)
        self._overlay = QGraphicsPixmapItem()
        self._overlay.setZValue(1)
        self._overlay.setVisible(False)
        self._scene.addItem(self._overlay)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#141414"))
        self.setMinimumSize(QSize(420, 260))
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self._normal_frame = self.styleSheet()

        self.modes = QComboBox(self)
        self.modes.addItems(self.MODES)
        self.modes.setToolTip(
            "Flat is the frame that goes to the screen. Viewer is that frame "
            "seen from the projection camera, baked the same way the "
            "reprojection is."
        )
        self.modes.setFixedWidth(84)
        self.modes.move(10, 10)
        self.modes.currentIndexChanged.connect(self.modeChanged)

        # Black content and no content look the same over a dark background,
        # which is exactly the pair worth telling apart when a frame carries
        # its own matte. Two mid greys read against both.
        self._checker = QPixmap(24, 24)
        self._checker.fill(QColor("#4a4a4a"))
        brush = QPainter(self._checker)
        brush.fillRect(0, 0, 12, 12, QColor("#5f5f5f"))
        brush.fillRect(12, 12, 12, 12, QColor("#5f5f5f"))
        brush.end()
        self._show_checker = True

    def set_checker(self, on: bool) -> None:
        self._show_checker = bool(on)
        self.viewport().update()

    def drawBackground(self, painter, rect) -> None:  # noqa: N802 -- Qt naming
        """The frame's own footprint, tiled, so transparency is visible.

        Drawn in the viewport rather than the scene, so the squares stay the
        same size however far in someone has zoomed -- a ruler, not part of
        the picture.
        """
        super().drawBackground(painter, rect)
        if not getattr(self, "_show_checker", False) or self._item.pixmap().isNull():
            return
        painter.save()
        painter.resetTransform()
        area = self.mapFromScene(self._item.sceneBoundingRect()).boundingRect()
        painter.fillRect(area, QBrush(self._checker))
        painter.restore()

    def set_pixmap(self, pixmap: QPixmap) -> None:
        """Show a frame, keeping whatever zoom is already set.

        Scrubbing replaces the picture many times a second, and refitting each
        time would throw away the close look someone had just taken. A frame of
        a different shape is a different picture, so that one is fitted.
        """
        same_shape = (not self._item.pixmap().isNull()
                      and self._item.pixmap().size() == pixmap.size())
        self._item.setPixmap(pixmap)
        self._scene.setSceneRect(self._item.boundingRect())
        self._rescale_overlay()
        if not same_shape:
            self.fit()

    def set_overlay(self, pixmap: QPixmap) -> None:
        self._overlay.setPixmap(pixmap)
        self._rescale_overlay()
        if self._item.pixmap().isNull():
            self._scene.setSceneRect(self._overlay.boundingRect())
            self.fit_overlay()

    def _rescale_overlay(self) -> None:
        """Stretch the overlay onto the rendered frame, whatever its own size."""
        overlay = self._overlay.pixmap()
        base = self._item.pixmap()
        if overlay.isNull() or base.isNull():
            self._overlay.setScale(1.0)
            return
        self._overlay.setScale(base.width() / overlay.width())

    def fit_overlay(self) -> None:
        if not self._overlay.pixmap().isNull():
            self.fitInView(self._overlay, Qt.AspectRatioMode.KeepAspectRatio)

    def set_overlay_visible(self, visible: bool) -> None:
        self._overlay.setVisible(visible and not self._overlay.pixmap().isNull())

    def set_overlay_opacity(self, opacity: float) -> None:
        self._overlay.setOpacity(opacity)

    def fit(self) -> None:
        # Fitting the item rather than its rectangle fits the opaque part of
        # it, so the black around the screen does not eat the window.
        if not self._item.pixmap().isNull():
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    def actual_size(self) -> None:
        self.resetTransform()

    def wheelEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        if self._item.pixmap().isNull():
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    # -- drops -----------------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            self.setStyleSheet("border:2px solid #7fb2d9;")
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet(self._normal_frame)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet(self._normal_frame)
        for url in event.mimeData().urls():
            if url.isLocalFile():
                event.acceptProposedAction()
                self.fileDropped.emit(url.toLocalFile())
                return


class SceneView(QWidget):
    """The frame shown on the screen where it stands, turned with the mouse.

    Drawing costs about 2 ms, so every drag step is a fresh render rather than
    a transformed picture -- there is nothing to be gained by caching.
    """

    VIEWS = [
        ("View", None),          # the projection camera's own angle
        ("Front", (-135.0, 5.0)),
        ("Top", (-135.0, 65.0)),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(QSize(420, 260))
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.scene: preview3d.Scene3D | None = None
        self.orbit: preview3d.Orbit | None = None

        self.views = QComboBox(self)
        for label, _ in self.VIEWS:
            self.views.addItem(label)
        self.views.setToolTip("Jump back to a known angle")
        self.views.setFixedWidth(84)
        self.views.move(10, 10)
        self.views.currentIndexChanged.connect(self._pick_view)
        self._picture: QImage | None = None
        self._buffer = None
        self._frame = None
        self._last_pos = None
        self._panning = False
        self.error = ""

    # -- setting up --------------------------------------------------------

    def attach(self, geometry_path: Path) -> str:
        """Load the geometry and the renderer; returns an error, or empty."""
        try:
            geometry = preview3d.Geometry(geometry_path)
            self.scene = preview3d.Scene3D(geometry)
            self.orbit = geometry.default_orbit.copy()
            self.error = ""
        except Exception as problem:  # noqa: BLE001 -- shown in the window
            self.error = str(problem)
        return self.error

    def set_frame(self, image) -> None:
        self._frame = image
        if self.scene is not None:
            self.scene.set_frame(image)
            self.redraw()

    def _pick_view(self, index: int) -> None:
        """Back to a known angle, at the framing that angle was set up with."""
        if self.scene is None or not 0 <= index < len(self.VIEWS):
            return
        self.orbit = self.scene.geometry.default_orbit.copy()
        angles = self.VIEWS[index][1]
        if angles is not None:
            self.orbit.yaw = np.radians(angles[0])
            self.orbit.pitch = np.radians(angles[1])
        self.redraw()

    def reset_view(self) -> None:
        self.views.setCurrentIndex(0)
        self._pick_view(0)

    def set_overlay_image(self, path: Path | None) -> None:
        if self.scene is None:
            return
        try:
            self.scene.set_overlay(imagefile.read_rgba(path) if path is not None
                                   else np.zeros((1, 1, 4), dtype=np.uint8))
        except Exception:  # noqa: BLE001 -- an unreadable overlay just stays off
            self.scene.set_overlay(np.zeros((1, 1, 4), dtype=np.uint8))
        self.redraw()

    def set_overlay_opacity(self, opacity: float) -> None:
        if self.scene is not None:
            self.scene.set_overlay_opacity(opacity)
            self.redraw()

    # -- drawing -----------------------------------------------------------

    def redraw(self) -> None:
        if self.scene is None or self._frame is None:
            return
        ratio = self.devicePixelRatioF()
        width = max(64, int(self.width() * ratio))
        height = max(64, int(self.height() * ratio))
        try:
            image = self.scene.render(width, height, self.orbit)
        except Exception as problem:  # noqa: BLE001
            self.error = str(problem)
            return
        self._buffer = np.ascontiguousarray(image)
        picture = QImage(self._buffer.data, width, height, width * 4,
                         QImage.Format.Format_RGBA8888)
        picture.setDevicePixelRatio(ratio)
        self._picture = picture
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#141414"))
        if self._picture is not None:
            painter.drawImage(0, 0, self._picture)
            return
        painter.setPen(QColor("#8a8a8a"))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                         self.error or "no frame yet")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.redraw()

    # -- the mouse ---------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._last_pos = event.position()
        self._panning = event.button() == Qt.MouseButton.MiddleButton
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._last_pos = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._last_pos is None or self.orbit is None:
            return
        delta = event.position() - self._last_pos
        self._last_pos = event.position()

        if self._panning:
            # Slide the point being orbited, scaled so the drag tracks the view.
            reach = self.orbit.distance * np.tan(self.orbit.fov_y / 2.0) * 2.0
            step = reach / max(1, self.height())
            right = np.array([-np.sin(self.orbit.yaw), np.cos(self.orbit.yaw), 0.0],
                             dtype=np.float32)
            self.orbit.target = self.orbit.target - right * float(delta.x()) * step
            self.orbit.target[2] += float(delta.y()) * step
        else:
            self.orbit.yaw -= float(delta.x()) * 0.008
            self.orbit.pitch = float(np.clip(
                self.orbit.pitch + float(delta.y()) * 0.008,
                np.radians(-85.0), np.radians(85.0)))
        self.redraw()

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Zoom by narrowing the view, never by moving the camera.

        Walking the eye closer would change the perspective, and this view
        exists to show the projection as it will really look -- so the picture
        is cropped instead, exactly as a longer lens from the same spot would.
        """
        if self.orbit is None:
            return
        factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
        self.orbit.zoom = float(np.clip(self.orbit.zoom * factor, 0.35, 20.0))
        self.redraw()


class Timeline(QWidget):
    """Scrub bar for the source sequence.

    The full track is the whole sequence; the brighter band is the range that
    will actually be rendered; the handle is the frame the preview will use.
    """

    frameChanged = Signal(int)

    MARGIN = 16
    TRACK_HEIGHT = 12
    HANDLE_WIDTH = 11

    def __init__(self) -> None:
        super().__init__()
        self._first = 0
        self._last = 0
        self._value = 0
        self._range_start = 0
        self._range_end = 0
        self._dragging = False
        self.setMinimumHeight(58)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setEnabled(False)

    # -- state ---------------------------------------------------------

    def set_bounds(self, first: int, last: int) -> None:
        self._first, self._last = first, last
        self._range_start, self._range_end = first, last
        self.set_value(first)
        self.setEnabled(True)
        self.update()

    def clear(self) -> None:
        self._first = self._last = self._value = 0
        self._range_start = self._range_end = 0
        self.setEnabled(False)
        self.update()

    def set_render_range(self, start: int, end: int) -> None:
        self._range_start, self._range_end = start, end
        self.update()

    def value(self) -> int:
        return self._value

    def set_value(self, frame: int) -> None:
        frame = max(self._first, min(self._last, int(frame)))
        if frame != self._value:
            self._value = frame
            self.frameChanged.emit(frame)
        self.update()

    # -- geometry ------------------------------------------------------

    def _span(self) -> float:
        return float(max(1, self._last - self._first))

    def _track_width(self) -> float:
        return max(1.0, self.width() - 2 * self.MARGIN)

    def _x_of(self, frame: int) -> float:
        return self.MARGIN + (frame - self._first) / self._span() * self._track_width()

    def _frame_of(self, x: float) -> int:
        ratio = (x - self.MARGIN) / self._track_width()
        return round(self._first + ratio * self._span())

    # -- interaction ---------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        if not self.isEnabled():
            return
        self._dragging = True
        self.set_value(self._frame_of(event.position().x()))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self.set_value(self._frame_of(event.position().x()))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging = False

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self.isEnabled():
            return
        step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        self.set_value(self._value + (step if event.angleDelta().y() > 0 else -step))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        steps = {
            Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1,
            Qt.Key.Key_PageDown: -50, Qt.Key.Key_PageUp: 50,
        }
        if event.key() in steps:
            self.set_value(self._value + steps[event.key()])
        elif event.key() == Qt.Key.Key_Home:
            self.set_value(self._first)
        elif event.key() == Qt.Key.Key_End:
            self.set_value(self._last)
        else:
            super().keyPressEvent(event)

    # -- painting ------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        track_top = self.height() - self.TRACK_HEIGHT - 16
        track = QRectF(self.MARGIN, track_top, self._track_width(), self.TRACK_HEIGHT)

        painter.setBrush(QColor("#1b1b1b"))
        painter.drawRoundedRect(track, 3, 3)

        if not self.isEnabled():
            painter.setPen(QColor("#666"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "no sequence selected")
            return

        band_left = self._x_of(self._range_start)
        band_right = self._x_of(self._range_end)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#3d6d91"))
        painter.drawRoundedRect(QRectF(band_left, track_top, max(2.0, band_right - band_left),
                                       self.TRACK_HEIGHT), 3, 3)

        # end labels
        painter.setPen(QColor("#8a8a8a"))
        painter.setFont(QFont(UI_FONTS, 8))
        baseline = track_top + self.TRACK_HEIGHT + 13
        painter.drawText(QRectF(0, baseline - 12, self.width(), 14),
                         Qt.AlignmentFlag.AlignLeft, f"  {self._first}")
        painter.drawText(QRectF(0, baseline - 12, self.width() - 4, 14),
                         Qt.AlignmentFlag.AlignRight, str(self._last))

        # the handle
        x = self._x_of(self._value)
        handle = QRectF(x - self.HANDLE_WIDTH / 2, track_top - 6,
                        self.HANDLE_WIDTH, self.TRACK_HEIGHT + 12)
        painter.setPen(QPen(QColor("#cfe6f7"), 1))
        painter.setBrush(QColor("#7fb2d9"))
        painter.drawRoundedRect(handle, 3, 3)

        # frame number above the handle, kept inside the widget
        painter.setFont(QFont(UI_FONTS, 9, QFont.Weight.Bold))
        text = str(self._value)
        width = painter.fontMetrics().horizontalAdvance(text) + 14
        left = min(max(0.0, x - width / 2), self.width() - width)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2c2c2c"))
        painter.drawRoundedRect(QRectF(left, 2, width, 18), 3, 3)
        painter.setPen(QColor("#e8e8e8"))
        painter.drawText(QRectF(left, 2, width, 18), Qt.AlignmentFlag.AlignCenter, text)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{constants.APP_NAME}  -  {constants.APP_VERSION}")
        self.resize(1320, 880)

        self.sequences: list[scan.Sequence] = []
        self.current: scan.Sequence | None = None
        # What Auto made of this source's alpha. None until asked, because
        # asking costs a decode; cleared whenever the source changes.
        self._detected_alpha: str | None = None
        self.filename_edited = False
        self._named_for = ""            # the sequence that name was made from
        self.preview = app_jobs.PreviewRenderer()
        self.job: app_jobs.RenderJob | None = None
        self.live_preview = True
        self._job_started_at = 0.0
        self._job_output: Path | None = None
        self._job_target: Path | None = None
        # Corrected by check_dependencies as soon as the window is up.
        self._ffmpeg_ok = True
        self._pending_drop: Path | None = None
        # A dropped image outranks the timeline until the timeline is used
        # again: it is what the preview shows, so it is what Snapshot writes.
        self._dropped: Path | None = None
        self._preview_buffer = None
        self._flat_frame = None
        self._overlay_array = None

        # Scrubbing fires far faster than frames can be drawn; collapse a burst
        # of handle moves into one render instead of queueing every step. Kept
        # short because a frame now costs 17-25 ms, so the timer should not be
        # what limits how often the picture updates.
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(12)
        self._preview_timer.timeout.connect(self._do_preview)
        self.overlay_path: Path | None = None
        self.overlay_is_custom = False

        self.setAcceptDrops(True)
        self._build_ui()
        self.source_edit.setText(constants.SOURCE_DIR_REL)
        self.output_dir_edit.setText(constants.OUTPUT_DIR_REL)
        self._on_format_changed()      # nothing emits this on construction
        self._rescan()
        self._restore_overlay()
        self.scroller.verticalScrollBar().setValue(0)
        QTimer.singleShot(0, self._check_tables)
        self.seq_table.setFocus()
        self._say(IDLE_NOTE)

    def _restore_overlay(self) -> None:
        settings = constants.load_settings()
        self.overlay_slider.setValue(int(settings.get("overlay_alpha", 50)))
        remembered = settings.get("overlay")
        self._load_overlay(constants.find_overlay())
        self.overlay_is_custom = bool(remembered) and self.overlay_path is not None             and self.overlay_path == constants.resolve(remembered)
        if settings.get("overlay_on") and self.overlay_path is not None:
            self.overlay_check.setChecked(True)

    # -- construction ------------------------------------------------------

    # -- putting the window together -----------------------------------------

    def _heading(self, title: str, layout) -> None:
        """A heading and a hairline, where a framed box used to be.

        Five framed groups down one column is a lot of drawn furniture for what
        is really one form filled in from the top. A heading says the same and
        leaves the space to the parts that have something to show.
        """
        if layout.count():
            layout.addSpacing(6)
            rule = QFrame()
            rule.setFrameShape(QFrame.Shape.HLine)
            rule.setStyleSheet("color:#333;")
            rule.setFixedHeight(1)
            layout.addWidget(rule)
        label = QLabel(title)
        label.setStyleSheet("color:#7fa7cc; font-weight:600;")
        layout.addWidget(label)

    def _build_ui(self) -> None:
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 10, 12, 10)
        left_layout.setSpacing(5)
        self._build_source_section(left_layout)
        self._build_range_section(left_layout)
        self._build_resolution_section(left_layout)
        self._build_output_section(left_layout)
        left_layout.addStretch(1)

        scroller = self.scroller = QScrollArea()
        scroller.setWidget(left)
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setMinimumWidth(560)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(scroller)
        splitter.addWidget(self._build_preview_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([560, 760])

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(splitter, 1)
        root_layout.addWidget(self._build_render_bar())
        root_layout.addWidget(self._build_log())
        self.setCentralWidget(root)

    def _build_source_section(self, layout) -> None:
        self._heading("Source", layout)

        row = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Folder holding the sequence or movie")
        self.source_edit.editingFinished.connect(self._rescan)
        row.addWidget(self.source_edit, 1)
        for name, relative in constants.SOURCE_PRESETS:
            button = QPushButton(name)
            button.setToolTip("Jump to this folder")
            button.clicked.connect(lambda _=False, r=relative: self._set_source(r))
            row.addWidget(button)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_source)
        row.addWidget(browse)
        rescan = QPushButton("Rescan")
        rescan.setToolTip("Read the folder again")
        rescan.clicked.connect(self._rescan)
        row.addWidget(rescan)
        layout.addLayout(row)

        self.seq_table = QTableWidget(0, 4)
        self.seq_table.setHorizontalHeaderLabels(["Sequence", "Frames", "Range", "Size"])
        self.seq_table.verticalHeader().setVisible(False)
        self.seq_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.seq_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.seq_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.seq_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.seq_table.setMinimumHeight(120)
        self.seq_table.itemSelectionChanged.connect(self._on_sequence_selected)
        self.seq_table.setMaximumHeight(140)
        # The one part of this column with more to show than fits, so it is the
        # one given the slack. It used to be capped at four rows while half the
        # column stood empty underneath it.
        layout.addWidget(self.seq_table, 3)

        self.source_note = QLabel("")
        self.source_note.setWordWrap(True)
        layout.addWidget(self.source_note)

    def _build_range_section(self, layout) -> None:
        self._heading("Frame range", layout)

        row = QHBoxLayout()
        row.addWidget(QLabel("Start"))
        self.start_spin = QSpinBox()
        self.start_spin.setMaximum(9_999_999)
        self.start_spin.valueChanged.connect(self._on_range_changed)
        row.addWidget(self.start_spin)

        row.addWidget(QLabel("End"))
        self.end_spin = QSpinBox()
        self.end_spin.setMaximum(9_999_999)
        self.end_spin.valueChanged.connect(self._on_range_changed)
        row.addWidget(self.end_spin)

        full = QPushButton("Full range")
        full.clicked.connect(self._reset_range)
        row.addWidget(full)

        row.addWidget(QLabel("at"))
        self.in_fps_combo = QComboBox()
        self.in_fps_combo.setFixedWidth(112)
        self.in_fps_combo.addItem("Auto", 0)
        for rate in constants.INPUT_RATES:
            self.in_fps_combo.addItem(f"{rate} fps", rate)
        self.in_fps_combo.setToolTip(
            "How fast the material is meant to run. A movie usually says so "
            "itself and Auto believes it; an image sequence cannot say, so "
            "Auto takes it to be the written rate and every frame is kept.")
        self.in_fps_combo.currentIndexChanged.connect(self._on_range_changed)
        row.addWidget(self.in_fps_combo)

        row.addSpacing(12)
        row.addWidget(QLabel("Alpha"))
        self.src_alpha_combo = QComboBox()
        self.src_alpha_combo.setFixedWidth(126)
        for label, value in constants.SOURCE_ALPHA_CHOICES:
            self.src_alpha_combo.addItem(label, value)
        self.src_alpha_combo.setToolTip(
            "How the frame's own transparency is written, for footage that has "
            "any. Auto asks the file: colour that never exceeds its own alpha "
            "was premultiplied, which is what After Effects writes. Getting it "
            "wrong shows only along soft edges -- too dark one way, too bright "
            "the other. Ignore throws the frame's alpha away and keeps the "
            "screen's own.")
        self.src_alpha_combo.currentIndexChanged.connect(self._on_source_alpha)
        row.addWidget(self.src_alpha_combo)
        row.addStretch(1)
        layout.addLayout(row)

        self.range_note = QLabel("")
        layout.addWidget(self.range_note)

    def _build_resolution_section(self, layout) -> None:
        self._heading("Resolution", layout)
        row = QHBoxLayout()
        self.resolution_group = QButtonGroup(self)
        width, height = constants.FULL_RESOLUTION
        for index, (name, percent) in enumerate(constants.RESOLUTION_PRESETS):
            button = QRadioButton(
                f"{name}    {width * percent // 100} x {height * percent // 100}"
            )
            button.setChecked(index == 0)
            button.toggled.connect(self._on_resolution_changed)
            self.resolution_group.addButton(button, percent)
            row.addWidget(button)
        row.addStretch(1)

        self.supersample_check = CheckBox("Fine sampling")
        self.supersample_check.setToolTip(
            "Four samples per output pixel instead of one. One output pixel "
            "reaches across 1.0 to 2.0 source pixels here, and a single sample "
            "cannot see that far -- on detail near the source's own pixel grid "
            "that shows as crawl. "
            "Measured against the same mapping supersampled sixteen times, it "
            "cuts the error from 16.8 to 7.4 of 255 on the worst kind of "
            "content, and costs nothing worth measuring: this stage waits on "
            "moving frames, not on sampling.")
        self.supersample_check.toggled.connect(self._on_supersample)
        row.addWidget(self.supersample_check)
        layout.addLayout(row)

    def _on_supersample(self, _on: bool) -> None:
        self._present()

    def _on_checker(self, on: bool) -> None:
        self.view.set_checker(on)

    def _build_output_section(self, layout) -> None:
        self._heading("Output", layout)

        row = QHBoxLayout()
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.textChanged.connect(self._check_output)
        row.addWidget(self.output_dir_edit, 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_output)
        row.addWidget(browse)
        layout.addLayout(row)

        name_row = QHBoxLayout()
        self.filename_edit = QLineEdit()
        self.filename_edit.textEdited.connect(self._on_filename_edited)
        self.filename_edit.textChanged.connect(self._check_output)
        name_row.addWidget(self.filename_edit, 1)
        bump = QPushButton("+1 version")
        bump.clicked.connect(self._bump_version)
        name_row.addWidget(bump)
        layout.addLayout(name_row)

        format_row = QHBoxLayout()
        self.format_combo = QComboBox()
        for label, kind, _ in constants.OUTPUT_FORMATS:
            self.format_combo.addItem(label, kind)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        format_row.addWidget(self.format_combo)

        self.alpha_check = CheckBox("Alpha")
        self.alpha_check.setToolTip(
            "Keep transparency. H.264 cannot carry it and stays composited over black."
        )
        self.alpha_check.toggled.connect(self._check_output)
        format_row.addWidget(self.alpha_check)

        self.depth_combo = QComboBox()
        self.depth_combo.addItem("8 bit", 8)
        self.depth_combo.addItem("16 bit", 16)
        self.depth_combo.setToolTip("PNG bit depth")
        format_row.addWidget(self.depth_combo)

        self.encoder_combo = QComboBox()
        for label, value in constants.ENCODER_CHOICES:
            self.encoder_combo.addItem(label, value)
        self.encoder_combo.setToolTip(
            "Auto uses the card's encoder wherever it can take the job. NVENC "
            "cannot do H.264 above 4096 pixels wide, so at Full it is reachable "
            "only through HEVC -- and there it is worth 91 fps against 57."
        )
        format_row.addWidget(self.encoder_combo)

        format_row.addStretch(1)
        self.out_fps_combo = QComboBox()
        self.out_fps_combo.setFixedWidth(80)
        for rate in constants.OUTPUT_RATES:
            self.out_fps_combo.addItem(f"{rate} fps", rate)
        self.out_fps_combo.setCurrentIndex(
            constants.OUTPUT_RATES.index(constants.SCENE_FPS)
            if constants.SCENE_FPS in constants.OUTPUT_RATES else 0)
        self.out_fps_combo.setToolTip(
            "The rate of the finished file. Frames are chosen by time, not one "
            "for one, so sixty a second written at thirty stays the same length "
            "and loses every other frame.")
        self.out_fps_combo.currentIndexChanged.connect(self._on_range_changed)
        format_row.addWidget(self.out_fps_combo)
        layout.addLayout(format_row)

        self.output_note = QLabel("")
        self.output_note.setWordWrap(True)
        layout.addWidget(self.output_note)

    # -- the preview ----------------------------------------------------------

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)

        # One control for what is being looked at. There were three: a combo
        # floating over the picture, another floating over the 3D view, and a
        # 3D toggle in the bar -- so "what am I looking at" was answered in two
        # places at once and neither of them showed the whole answer.
        self.view_buttons = []
        for index, (label, tip) in enumerate((
                ("Flat", "The frame as it goes to the screen"),
                ("Viewer", "That frame seen from the projection camera, baked "
                           "the same way the reprojection is"),
                ("3D", "The frame on the screen where it stands. Drag to turn, "
                       "wheel to come closer, middle button to slide."))):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setFixedWidth(64)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, i=index: self._choose_view(i))
            bar.addWidget(button)
            self.view_buttons.append(button)

        bar.addSpacing(10)
        self.flat_buttons = []
        for label, tip, slot in (
                ("Fit", "Fit the whole frame", lambda: self.view.fit()),
                ("1:1", "One pixel of the frame to one of the screen",
                 lambda: self.view.actual_size())):
            button = QPushButton(label)
            button.setFixedWidth(44)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            bar.addWidget(button)
            self.flat_buttons.append(button)

        bar.addStretch(1)

        self.checker_check = CheckBox("Checker")
        self.checker_check.setChecked(True)
        self.checker_check.setToolTip(
            "Tile the frame's own footprint behind it, so black content and no "
            "content stop looking alike. What shows through is transparent: "
            "the gaps between the lamellas, and any hole the material brought "
            "with it.")
        self.checker_check.toggled.connect(self._on_checker)
        bar.addWidget(self.checker_check)

        self.overlay_check = CheckBox("Overlay")
        self.overlay_check.setToolTip("Draw the layout map over the preview")
        self.overlay_check.toggled.connect(self._on_overlay_toggled)
        bar.addWidget(self.overlay_check)

        # Only while it is on. Three controls for a switched-off option took a
        # third of the bar and answered a question nobody had asked.
        self.overlay_extras = []
        self.overlay_slider = QSlider(Qt.Orientation.Horizontal)
        self.overlay_slider.setRange(0, 100)
        self.overlay_slider.setValue(50)
        self.overlay_slider.setFixedWidth(110)
        self.overlay_slider.valueChanged.connect(self._on_overlay_alpha)
        bar.addWidget(self.overlay_slider)
        self.overlay_extras.append(self.overlay_slider)

        self.overlay_alpha_label = QLabel("50%")
        self.overlay_alpha_label.setFixedWidth(34)
        self.overlay_alpha_label.setStyleSheet("color:#8a8a8a;")
        bar.addWidget(self.overlay_alpha_label)
        self.overlay_extras.append(self.overlay_alpha_label)

        pick_overlay = QPushButton("...")
        pick_overlay.setFixedWidth(30)
        pick_overlay.setToolTip("Choose the overlay image")
        pick_overlay.clicked.connect(self._browse_overlay)
        bar.addWidget(pick_overlay)
        self.overlay_extras.append(pick_overlay)
        for widget in self.overlay_extras:
            widget.setVisible(False)

        bar.addSpacing(10)
        self.snapshot_button = QPushButton("Snapshot")
        self.snapshot_button.setToolTip(
            "Save the frame on screen at full resolution into the Snapshots "
            "folder, with transparency where the screen does not cover the frame."
        )
        self.snapshot_button.clicked.connect(self._save_snapshot)
        bar.addWidget(self.snapshot_button)
        layout.addLayout(bar)

        self.view = ImageView()
        self.view.modes.setVisible(False)      # the bar asks this now
        self.view.fileDropped.connect(lambda path: self._handle_drop(Path(path)))
        self.view.modeChanged.connect(self._on_view_mode_changed)
        self.scene_view = SceneView()

        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.view)
        self.view_stack.addWidget(self.scene_view)
        layout.addWidget(self.view_stack, 1)

        self.timeline = Timeline()
        self.timeline.frameChanged.connect(self._on_scrub)
        layout.addWidget(self.timeline)

        under = QHBoxLayout()
        self.frame_label = QLabel("")
        self.frame_label.setStyleSheet("color:#8a8a8a;")
        under.addWidget(self.frame_label)
        under.addSpacing(16)
        self.preview_note = QLabel("No preview yet.")
        self.preview_note.setWordWrap(True)
        self.preview_note.setAlignment(Qt.AlignmentFlag.AlignRight
                                       | Qt.AlignmentFlag.AlignVCenter)
        under.addWidget(self.preview_note, 1)
        layout.addLayout(under)

        # The 3D toggle the rest of the window still asks about. Kept as the
        # place that answer lives, driven by the buttons above.
        self.mode_button = QPushButton()
        self.mode_button.setCheckable(True)
        self.mode_button.setVisible(False)
        self.mode_button.toggled.connect(self._on_mode_changed)
        return panel

    def _choose_view(self, index: int) -> None:
        """Flat, Viewer, or the thing standing in the square."""
        self.view.modes.setCurrentIndex(min(index, 1))
        self.mode_button.setChecked(index == 2)
        self._sync_view_buttons()

    def _on_view_mode_changed(self, _index: int) -> None:
        self._sync_view_buttons()
        self._present()

    def _sync_view_buttons(self) -> None:
        """The buttons follow the state, whoever changed it."""
        if not hasattr(self, "view_buttons"):
            return
        live = (2 if self.mode_button.isChecked()
                else self.view.modes.currentIndex())
        for index, button in enumerate(self.view_buttons):
            button.blockSignals(True)
            button.setChecked(index == live)
            button.blockSignals(False)

    # -- the bottom of the window ---------------------------------------------

    def _build_render_bar(self) -> QWidget:
        bar = QFrame()
        bar.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        self.render_button = QPushButton("RENDER")
        self.render_button.setMinimumWidth(150)
        self.render_button.setMinimumHeight(32)
        self.render_button.setStyleSheet("font-weight:700;")
        self.render_button.clicked.connect(self._start_render)
        layout.addWidget(self.render_button)

        self.flipbook_button = QPushButton("Flipbook")
        self.flipbook_button.setToolTip(
            "Bake the frame range exactly as the preview is showing it -- flat "
            "or the viewer's view, with the layout map if it is on. A snapshot "
            "with a timeline; not the file the wall is fed."
        )
        self.flipbook_button.clicked.connect(lambda: self._start_render(flipbook=True))
        layout.addWidget(self.flipbook_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel_render)
        layout.addWidget(self.cancel_button)

        # Idle this strip says what the application is standing on; working, it
        # says how far along it is. Two things never true at once, so they take
        # turns in one place rather than each keeping a line of its own.
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress, 1)

        self.eta_label = QLabel("")
        self.eta_label.setMinimumWidth(260)
        self.eta_label.setVisible(False)
        layout.addWidget(self.eta_label)

        self.status_label = QLabel(IDLE_NOTE)
        self.status_label.setStyleSheet("color:#8fbf8f;")
        layout.addWidget(self.status_label, 1)

        self.tables_warning = QLabel("")
        layout.addWidget(self.tables_warning)

        # What the application is standing on: the tables it warps through and
        # the thing doing the warping. One label, not two -- as two they were
        # each asking the layout for room and the second one lost.
        self.facts_label = QLabel("checking ...")
        self.facts_label.setStyleSheet("color:#9ad;")
        # Minimum, not Preferred: the message beside it is free to take the
        # whole strip, and a Preferred neighbour is one Qt will happily shrink
        # to six pixels to let it.
        self.facts_label.setSizePolicy(QSizePolicy.Policy.Minimum,
                                       QSizePolicy.Policy.Preferred)
        layout.addWidget(self.facts_label)

        # Written to by the two checks; read back by `_show_facts`. Neither is
        # ever shown, so neither takes any width.
        self.tables_label = QLabel("checking ...")
        self.tables_label.setVisible(False)
        self.engine_label = QLabel("")
        self.engine_label.setVisible(False)

        # A message long enough to fill the strip loses its own tail rather
        # than pushing what is beside it off the end.
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Preferred)

        self.log_button = QPushButton("Log")
        self.log_button.setCheckable(True)
        self.log_button.setFixedWidth(52)
        self.log_button.setToolTip("Show what this session has been doing")
        self.log_button.toggled.connect(self._show_log)
        layout.addWidget(self.log_button)
        return bar

    def _build_log(self) -> QWidget:
        self.log_panel = QWidget()
        outer = QVBoxLayout(self.log_panel)
        outer.setContentsMargins(12, 0, 12, 8)
        outer.setSpacing(4)

        head = QHBoxLayout()
        head.addStretch(1)
        folder = QPushButton("Open folder")
        folder.setToolTip(
            "The folder this session is being written to. Send that file when "
            "something needs explaining.")
        folder.clicked.connect(self._open_log_folder)
        head.addWidget(folder)
        outer.addLayout(head)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(110)
        self.log.setMaximumHeight(160)   # opened, not taking over
        self.log.setFont(QFont(MONO_FONTS, 9))
        outer.addWidget(self.log)

        # Folded away until asked for: a hundred pixels of scrollback is not
        # what anybody is looking at while they set a render up.
        self.log_panel.setVisible(False)
        return self.log_panel

    def _show_log(self, on: bool) -> None:
        self.log_panel.setVisible(on)

    def _say(self, text: str, level: str = "") -> None:
        """The one line that speaks for the window as a whole."""
        self._note(self.status_label, text, level)

    def _show_facts(self) -> None:
        """The tables and the engine, on one line, in the order they matter."""
        parts = [p for p in (self.tables_label.text(), self.engine_label.text()) if p]
        self.facts_label.setText("   ·   ".join(parts))
        self.facts_label.setStyleSheet(
            "color:#e06c6c;" if self.tables_label.text() == "missing" else "color:#9ad;")
        self.facts_label.setToolTip(self.tables_label.toolTip())

    def _working(self, busy: bool) -> None:
        """Swap the strip between what it stands on and how far along it is."""
        self.progress.setVisible(busy)
        self.eta_label.setVisible(busy)
        self.cancel_button.setVisible(busy)
        self.status_label.setVisible(not busy)
        self.facts_label.setVisible(not busy)
        self.tables_warning.setVisible(not busy and bool(self.tables_warning.text()))


    # -- engine ------------------------------------------------------------

    def _check_tables(self) -> None:
        """Confirm the baked tables are there before anything asks for one."""
        problem = constants.project_dir_problem()
        if problem:
            self._say(problem, "error")
            self._note(self.preview_note, problem, "error")
            return

        try:
            names = [name for name, _ in constants.RESOLUTION_PRESETS]
            sizes = sum(constants.table_path(n).stat().st_size for n in names)
            self.tables_label.setText(f"tables {', '.join(names).lower()}")
            self.tables_label.setToolTip(
                f"The baked lookup tables this renders through"
                f"   ({sizes / 1024 / 1024:.1f} MB)")
            self._show_facts()
            self._note(self.tables_warning, "")
        except FileNotFoundError as error:
            self.tables_label.setText("missing")
            self._show_facts()
            self._note(self.tables_warning, str(error), "error")
            self._say("Lookup tables are missing -- nothing can render.", "error")
            self._update_render_enabled()
            return

        self._say(IDLE_NOTE)
        self._run_pending_drop()

    def _note_engine(self) -> None:
        """What is doing the warping, short enough to sit in the status strip."""
        description = self.preview.description
        if not description:
            return
        short = description.split()[0]
        backend = description.rpartition("(")[2].rstrip(")")
        self.engine_label.setText(f"{short} {backend}".strip())
        self._show_facts()

    # -- source ------------------------------------------------------------

    def _browse_source(self) -> None:
        start = str(constants.resolve(self.source_edit.text() or constants.SOURCE_DIR_REL))
        chosen = QFileDialog.getExistingDirectory(self, "Choose sequence folder", start)
        if chosen:
            self._set_source(chosen)

    def _set_source(self, path: str) -> None:
        self.source_edit.setText(constants.display(path))
        self._rescan()

    def _rescan(self) -> None:
        root = constants.resolve(self.source_edit.text())
        self.sequences = scan.scan_folder(root)

        self.seq_table.setRowCount(len(self.sequences))
        for row, sequence in enumerate(self.sequences):
            name_item = QTableWidgetItem(f"{sequence.name}{sequence.extension}")
            name_item.setToolTip(
                f"{constants.display(sequence.directory)}\n{sequence.pattern_label}"
            )
            self.seq_table.setItem(row, 0, name_item)
            self.seq_table.setItem(row, 1, QTableWidgetItem(sequence.count_label))
            self.seq_table.setItem(row, 2, QTableWidgetItem(sequence.range_label))
            self.seq_table.setItem(row, 3, QTableWidgetItem(sequence.resolution_label))

        self._fit_table()
        if not root.is_dir():
            self._note(self.source_note, f"Folder does not exist: {root}", "error")
        elif not self.sequences:
            self._note(
                self.source_note,
                "No image sequence here. Single files and videos are ignored.",
                "warn",
            )
        else:
            self._note(self.source_note, f"{len(self.sequences)} sequence(s) found.")

        self.current = None
        if len(self.sequences) == 1:
            self.seq_table.selectRow(0)
        else:
            self._on_sequence_selected()

    def _fit_table(self) -> None:
        """As tall as it has rows, up to a point.

        Given the whole column it would stand mostly empty on a folder holding
        three sequences; given four rows it hid the rest of a folder holding
        twenty. So it takes what it needs and leaves the remainder below.
        """
        height = (self.seq_table.horizontalHeader().height()
                  + 2 * self.seq_table.frameWidth() + 4)
        for row in range(self.seq_table.rowCount()):
            height += self.seq_table.rowHeight(row)
        self.seq_table.setMaximumHeight(max(120, min(height, 460)))

    def _on_sequence_selected(self) -> None:
        self._detected_alpha = None
        model = self.seq_table.selectionModel()
        rows = model.selectedRows() if model else []
        self.current = self.sequences[rows[0].row()] if rows else None

        if self.current is None:
            for spin in (self.start_spin, self.end_spin):
                spin.setEnabled(False)
            self.timeline.clear()
            self.frame_label.setText("")
            self.range_note.setText("")
            self._update_render_enabled()
            return

        sequence = self.current
        for spin in (self.start_spin, self.end_spin):
            spin.setEnabled(True)
            spin.blockSignals(True)
            spin.setRange(sequence.first, sequence.last)
            spin.blockSignals(False)
        self.start_spin.setValue(sequence.first)
        self.end_spin.setValue(sequence.last)
        self.timeline.set_bounds(sequence.first, sequence.last)
        self._on_scrub(sequence.first)

        if sequence.is_contiguous:
            self._note(
                self.source_note,
                f"{sequence.count} files, {sequence.range_label}, {sequence.resolution_label}"
                f"   ->   scene frames 1-{sequence.count}",
            )
        else:
            missing = sequence.missing
            listed = ", ".join(str(number) for number in missing[:6])
            more = f" (+{len(missing) - 6} more)" if len(missing) > 6 else ""
            self._note(
                self.source_note,
                f"Gaps in numbering: {len(missing)} missing  ->  {listed}{more}",
                "warn",
            )

        if self._name_is_ours():
            # A movie already called *_remap_v1 must not become *_remap_v1_remap_v1.
            stem = re.sub(r"_remap_v\d+$", "", sequence.name)
            self.filename_edit.setText(self._free_version(stem))
            self._named_for = stem
            self.filename_edited = False
        self._on_range_changed()

        if sequence.kind == "movie" and not sequence.is_probed:
            self._probe_movie(sequence)

    def _probe_movie(self, movie) -> None:
        """Read how long the movie is straight from its container.

        ffprobe answers from metadata rather than by decoding, so this is
        instant and can happen inline instead of as a background job.
        """
        try:
            frames, width, height, rate = app_jobs.probe_movie(movie.path)
        except Exception as error:  # noqa: BLE001 -- reported in the window
            self._note(self.source_note, f"could not read {movie.path.name}: {error}", "error")
            self._update_render_enabled()
            return

        movie.frames, movie.width, movie.height = frames, width, height
        movie.fps = rate
        self._show_detected_rate()

        row = self.sequences.index(movie)
        self.seq_table.item(row, 1).setText(movie.count_label)
        self.seq_table.item(row, 2).setText(movie.range_label)
        self.seq_table.item(row, 3).setText(movie.resolution_label)

        for spin in (self.start_spin, self.end_spin):
            spin.blockSignals(True)
            spin.setRange(movie.first, movie.last)
            spin.blockSignals(False)
        self.start_spin.setValue(movie.first)
        self.end_spin.setValue(movie.last)
        self.timeline.set_bounds(movie.first, movie.last)
        self._on_scrub(movie.first)
        self._on_range_changed()
        self._note(
            self.source_note,
            f"{movie.path.name}   |   {frames} frames, {width}x{height}"
            + (f", {rate:g} fps" if rate else "")
            + f"   ->   scene frames 1-{frames}",
        )

    # -- range and resolution ---------------------------------------------

    def _input_fps(self) -> float:
        """What the source runs at: chosen, or what the file said.

        A sequence has nothing to detect, so Auto falls back to the written
        rate, which keeps every frame -- that is what this did before there
        was a choice, and it is still the right answer for stills.
        """
        chosen = self.in_fps_combo.currentData() if hasattr(self, "in_fps_combo") else 0
        if chosen:
            return float(chosen)
        detected = getattr(self.current, "fps", 0.0) if self.current else 0.0
        return float(detected) or self._output_fps()

    def _output_fps(self) -> float:
        if not hasattr(self, "out_fps_combo"):
            return float(constants.SCENE_FPS)
        return float(self.out_fps_combo.currentData() or constants.SCENE_FPS)

    def _show_detected_rate(self) -> None:
        """Say on the Auto entry what the file turned out to be."""
        if not hasattr(self, "in_fps_combo"):
            return
        detected = getattr(self.current, "fps", 0.0) if self.current else 0.0
        self.in_fps_combo.setItemText(
            0, f"Auto ({detected:g} fps)" if detected else "Auto")

    def _on_range_changed(self) -> None:
        if self.current is None:
            return
        if self.end_spin.value() < self.start_spin.value():
            self.end_spin.setValue(self.start_spin.value())
        total = self.end_spin.value() - self.start_spin.value() + 1
        in_fps, out_fps = self._input_fps(), self._output_fps()
        written = remap_render.frames_out(total, in_fps, out_fps)
        length = total / in_fps if in_fps > 0 else 0.0
        self.range_note.setText(
            f"{total} at {in_fps:g} fps  =  {length:.1f} s  ->  "
            f"{written} at {out_fps:g} fps")
        self.range_note.setToolTip(
            "Frames are chosen by time, not one for one, so the clip comes out "
            "the length it went in whichever rate it is written at."
            if written != total else "")
        self.timeline.set_render_range(self.start_spin.value(), self.end_spin.value())
        self._update_render_enabled()

    def _reset_range(self) -> None:
        if self.current is None:
            return
        self.start_spin.setValue(self.current.first)
        self.end_spin.setValue(self.current.last)

    def _on_resolution_changed(self, checked: bool) -> None:
        if checked and self.current is not None:
            self._log(f"[mock] resolution set to {self.resolution_group.checkedId()}%")

    def _output_resolution(self) -> tuple[int, int]:
        percent = self.resolution_group.checkedId()
        width, height = constants.FULL_RESOLUTION
        return width * percent // 100, height * percent // 100

    # -- output ------------------------------------------------------------

    def _browse_output(self) -> None:
        start = str(constants.resolve(self.output_dir_edit.text() or constants.OUTPUT_DIR_REL))
        chosen = QFileDialog.getExistingDirectory(self, "Choose output folder", start)
        if chosen:
            self.output_dir_edit.setText(constants.display(chosen))

    def _on_filename_edited(self) -> None:
        self.filename_edited = True

    def _free_version(self, stem: str) -> str:
        """The first version of this name nothing has been written to yet.

        Landing on a name that is already taken only to be told so is a click
        wasted: the answer is known here. Nothing is overwritten and no rail is
        skipped -- this picks a free name rather than freeing a taken one.
        """
        folder = constants.resolve(self.output_dir_edit.text())
        extension = self._output_extension()
        for version in range(1, 1000):
            candidate = folder / f"{stem}_remap_v{version}{extension}"
            # A PNG sequence lands in a folder of its own; that counts as taken.
            if not candidate.exists() and not candidate.with_suffix("").exists():
                return candidate.name
        return f"{stem}_remap_v1{extension}"

    def _name_is_ours(self) -> bool:
        """Whether the output name is still one this window wrote.

        The name it generates belongs to the sequence it was made for, and so
        does that name with the version bumped -- picking another sequence
        should replace either, or the second render is named after the first.
        A name typed by hand belongs to whoever typed it and is left alone.
        """
        if not self.filename_edited:
            return True
        if not self._named_for:
            return False
        return re.fullmatch(rf"{re.escape(self._named_for)}_remap_v\d+\.\w+",
                            self.filename_edit.text().strip()) is not None

    def _bump_version(self) -> None:
        name = self.filename_edit.text()
        match = re.search(r"_v(\d+)(\.[A-Za-z0-9]+)$", name)
        if match:
            name = f"{name[:match.start()]}_v{int(match.group(1)) + 1}{match.group(2)}"
        else:
            stem, dot, extension = name.rpartition(".")
            name = f"{stem}_v2{dot}{extension}" if dot else f"{name}_v2"
        self.filename_edit.setText(name)
        self.filename_edited = True

    def output_path(self) -> Path:
        return constants.resolve(self.output_dir_edit.text()) / self.filename_edit.text().strip()

    def _check_output(self) -> None:
        directory = constants.resolve(self.output_dir_edit.text())
        name = self.filename_edit.text().strip()
        if not name:
            self._note(
                self.output_note,
                "" if self.current is None else "Enter a file name.",
                "error",
            )
        elif not directory.is_dir():
            self._note(self.output_note, f"Output folder does not exist: {directory}", "error")
        elif self.output_path().exists():
            megabytes = self.output_path().stat().st_size / (1024 * 1024)
            self._note(
                self.output_note,
                f"File already exists ({megabytes:.0f} MB). Rename it or press +1 version -- "
                f"nothing is overwritten.",
                "error",
            )
        else:
            self._note(self.output_note, "")
        self._update_render_enabled()

    def _output_blocked(self) -> bool:
        directory = constants.resolve(self.output_dir_edit.text())
        return (
            not self.filename_edit.text().strip()
            or not directory.is_dir()
            or self.output_path().exists()
        )

    # -- preview -----------------------------------------------------------

    def _on_scrub(self, frame: int) -> None:
        """Timeline handle moved: update the readout and repaint the preview.

        A frame costs about 12 ms on the GPU, so this can follow the handle
        while it is being dragged. The timer only collapses a burst of moves
        into a single render; it is not there to make it feel fast.
        """
        if self.current is None:
            self.frame_label.setText("")
            return
        scene_frame = frame - self.current.first + 1
        inside = self.start_spin.value() <= frame <= self.end_spin.value()
        outside = "" if inside else "   (outside the render range)"
        self.frame_label.setText(
            f"frame {frame:0{self.current.padding}d}   ->   scene frame {scene_frame}{outside}"
        )
        if self.live_preview and not self._render_running():
            self._preview_timer.start()

    def _load_overlay(self, path: Path | None) -> None:
        """Point the preview overlay at an image, or clear it."""
        self.overlay_path = path
        if path is None:
            self.overlay_check.setEnabled(False)
            self.overlay_check.setToolTip("No overlay image chosen -- press ... to pick one")
            return

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.overlay_check.setEnabled(False)
            self._log(f"cannot read the overlay image {path}")
            return

        self.view.set_overlay(pixmap)
        self.view.set_overlay_opacity(self.overlay_slider.value() / 100)
        try:
            self._overlay_array = imagefile.read_rgba(path)
        except Exception:  # noqa: BLE001 -- an unreadable overlay simply stays off
            self._overlay_array = None
        if self.scene_view.scene is not None:
            self.scene_view.set_overlay_image(path)
        self._present()
        self.overlay_check.setEnabled(True)
        self.overlay_check.setToolTip(
            f"{constants.display(path)}   ({pixmap.width()}x{pixmap.height()})"
        )

    def _browse_overlay(self) -> None:
        start = str(self.overlay_path.parent) if self.overlay_path else str(constants.PROJECT_DIR)
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Choose the overlay image", start, "Images (*.png *.jpg *.jpeg *.tif *.tga)"
        )
        if not chosen:
            return
        self._load_overlay(Path(chosen))
        if self.overlay_path is not None:
            self.overlay_is_custom = True
            self.overlay_check.setChecked(True)
            self._remember_overlay()

    def _on_overlay_toggled(self, checked: bool) -> None:
        for widget in self.overlay_extras:
            widget.setVisible(checked)
        self.scene_view.set_overlay_opacity(
            self.overlay_slider.value() / 100 if checked else 0.0)
        self._present()
        self._remember_overlay()

    def _on_overlay_alpha(self, value: int) -> None:
        self.overlay_alpha_label.setText(f"{value}%")
        self.view.set_overlay_opacity(value / 100)
        if self.overlay_check.isChecked():
            self.scene_view.set_overlay_opacity(value / 100)
        self._present()
        self._remember_overlay()

    def _remember_overlay(self) -> None:
        settings = constants.load_settings()
        # Only a hand-picked file is worth storing. The bundled Check.png lives
        # in PyInstaller's temp folder, which is gone by the next launch.
        if self.overlay_is_custom and self.overlay_path is not None:
            settings["overlay"] = constants.display(self.overlay_path)
        else:
            settings.pop("overlay", None)
        settings["overlay_on"] = self.overlay_check.isChecked()
        settings["overlay_alpha"] = self.overlay_slider.value()
        constants.save_settings(settings)

    def preview_single_frame(self, path: Path) -> None:
        """Render one dropped image through the table, ignoring the timeline."""
        if self._render_running():
            self._pending_drop = path
            self._note(self.preview_note, f"{path.name} queued -- a render is running", "warn")
            return
        self._pending_drop = None
        self._warn_if_cold()
        try:
            owner, planes = app_jobs.avio.read_single_frame(str(path), True, 0, 0)
            started = time.monotonic()
            engine = self.preview.engine(self._resolution_name(),
                                         (owner.width, owner.height))
            engine.set_premultiplied(True)
            if hasattr(engine, "set_output_yuv"):
                engine.set_output_yuv(False)
            image = engine.render(planes if engine.name == "GPU"
                                  else owner.to_ndarray(format="rgba"))
        except Exception as error:  # noqa: BLE001 -- shown in the window
            self._note(self.preview_note, str(error), "error")
            return

        self._dropped = path
        self._update_render_enabled()
        self._show_array(image)
        width, height = self._output_resolution()
        self._note(
            self.preview_note,
            f"dropped frame {path.name}   |   {width} x {height}   |   "
            f"{1000 * (time.monotonic() - started):.0f} ms",
        )
        self._note_engine()

    def _warn_if_cold(self) -> None:
        """The first frame at a given resolution has to build the renderer."""
        if self.current is None:
            return
        key = (self._resolution_name(), self.current.width, self.current.height)
        if key not in self.preview._engines:
            self._note(self.preview_note, "preparing the renderer ...", "warn")
            QApplication.processEvents()

    def _do_preview(self) -> None:
        """Render the frame the timeline handle is sitting on."""
        if self.current is None or self._render_running():
            return
        frame = self.timeline.value()
        self._dropped = None
        self._warn_if_cold()
        started = time.monotonic()
        try:
            image = self.preview.render(
                self.current, frame, self._resolution_name(),
                self.supersample_check.isChecked(),
                remap_render.alpha_setting(self._source_alpha()))
        except Exception as error:  # noqa: BLE001 -- shown in the window
            self._note(self.preview_note, str(error), "error")
            return

        self._show_array(image)
        width, height = self._output_resolution()
        # Which frame this is belongs to the label on the left; side by side
        # the two of them were saying it twice.
        self._note(
            self.preview_note,
            f"{width} x {height}   |   {1000 * (time.monotonic() - started):.0f} ms",
        )
        self._note_engine()

    def _viewing_from_camera(self) -> bool:
        """True when the flat viewport is showing the view from the camera."""
        return (self.view.modes.currentIndex() == 1
                and not self.mode_button.isChecked())

    def _save_snapshot(self) -> None:
        """Write the frame on screen to Snapshots, always at full resolution.

        Whatever the viewport is showing is what gets written: the flat frame,
        or the view from the projection camera under a viewer_ prefix. Straight
        alpha rather than composited over black, so the file is useful both for
        looking at and for dropping onto another background.
        """
        if self._render_running():
            return
        dropped = self._dropped
        if dropped is None and self.current is None:
            return
        viewer = self._viewing_from_camera()
        prefix = "viewer_" if viewer else ""
        folder = constants.resolve(constants.SNAPSHOT_DIR_REL)
        if dropped is None:
            frame = self.timeline.value()
            target = _unique_path(
                folder,
                f"{prefix}{self.current.name}_{frame:0{max(4, self.current.padding)}d}.png")
        else:
            target = _unique_path(folder, f"{prefix}{dropped.stem}.png")

        started = time.monotonic()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if dropped is None:
                alpha_mode = remap_render.alpha_setting(self._source_alpha())
                owner, planes = self.preview.decode(
                    self.current, frame, alpha_mode != remap_engine.ALPHA_IGNORE)
                source_size = (self.current.width, self.current.height)
            else:
                # A dropped still is read whole either way, so it costs nothing
                # to let a PNG with a hole in it stay one; and it is already in
                # memory, so its convention can be read off it directly.
                owner, planes = app_jobs.avio.read_single_frame(
                    str(dropped), True, 0, 0, True)
                source_size = (owner.width, owner.height)
                planes = None
                alpha_mode = remap_render.alpha_setting(
                    remap_render.alpha_convention_of(
                        owner.to_ndarray(format="rgba")))
            opacity = (self.overlay_slider.value() / 100
                       if self.overlay_check.isChecked()
                       and self._overlay_array is not None else 0.0)
            engine = self.preview.engine(constants.RESOLUTION_PRESETS[0][0], source_size)
            # Colour weighted by coverage is what a second lookup needs; only
            # the stage that writes the file wants straight alpha.
            engine.set_premultiplied(viewer)
            if hasattr(engine, "set_source_alpha"):
                engine.set_source_alpha(alpha_mode)
            if hasattr(engine, "set_output_yuv"):
                engine.set_output_yuv(False)
            if not viewer and opacity > 0.0:
                # The map is drawn on the flat frame, which here is the output.
                engine.set_overlay(self._overlay_array)
                engine.set_overlay_opacity(opacity)
                engine.set_overlay_space(True)
            image = engine.render(planes if planes is not None and engine.name == "GPU"
                                  else owner.to_ndarray(format="rgba"))
            engine.set_premultiplied(True)           # the window wants it back
            if hasattr(engine, "set_source_alpha"):
                engine.set_source_alpha(remap_engine.ALPHA_IGNORE)
            engine.set_overlay_opacity(0.0)
            engine.set_overlay_space(False)

            if viewer:
                # The same flat frame is what this stage samples, so the map
                # sits in its source and travels through the warp with it.
                through = self.preview.viewer_engine((image.shape[1], image.shape[0]))
                through.set_premultiplied(False)     # straight alpha for the file
                if opacity > 0.0:
                    through.set_overlay(self._overlay_array)
                through.set_overlay_opacity(opacity)
                image = through.render(image)
                through.set_premultiplied(True)

            height, width = image.shape[:2]
            picture = QImage(np.ascontiguousarray(image).data, width, height,
                             width * 4, QImage.Format.Format_RGBA8888)
            if not picture.copy().save(str(target), "PNG"):
                raise RuntimeError(f"could not write {target.name}")
        except Exception as error:  # noqa: BLE001 -- shown in the window
            self._note(self.preview_note, f"snapshot failed: {error}", "error")
            return

        self._note(
            self.preview_note,
            f"saved {constants.display(target)}   |   {width} x {height}   |   "
            f"{1000 * (time.monotonic() - started):.0f} ms",
        )
        self._log(f"snapshot: {constants.display(target)}")

    def _on_mode_changed(self, three_d: bool) -> None:
        """Swap the flat frame for the screen in space, and the tools with it."""
        if three_d and self.scene_view.scene is None:
            path = constants.geometry_path()
            if path is None:
                self._note(self.preview_note,
                           "no baked screen geometry beside the application", "error")
                self.mode_button.setChecked(False)
                return
            self._note(self.preview_note, "preparing the 3D view ...", "warn")
            QApplication.processEvents()
            problem = self.scene_view.attach(path)
            if problem:
                self._note(self.preview_note, f"3D view unavailable: {problem}", "error")
                self.mode_button.setChecked(False)
                return
            if self._flat_frame is not None:
                self.scene_view.set_frame(self._flat_frame)
            self.scene_view.set_overlay_image(self.overlay_path)
            self.scene_view.set_overlay_opacity(
                self.overlay_slider.value() / 100 if self.overlay_check.isChecked() else 0.0)

        self.view_stack.setCurrentIndex(1 if three_d else 0)
        for button in self.flat_buttons:
            button.setVisible(not three_d)
        self._sync_view_buttons()
        if three_d:
            self._note(self.preview_note,
                       "drag to turn, wheel to crop in, middle button to slide")
        else:
            # Or it stays up over the flat frame, telling you to drag something
            # that is no longer there.
            self._note(self.preview_note, "")

    def _show_array(self, image) -> None:
        """Hand the finished frame to whichever view is on."""
        self._flat_frame = image if image.flags["C_CONTIGUOUS"] else \
            np.ascontiguousarray(image)
        if self.scene_view.scene is not None:
            self.scene_view.set_frame(self._flat_frame)
        self._present()

    def _present(self) -> None:
        """Draw the flat frame, or the view from the camera, as chosen."""
        viewer = self.view.modes.currentIndex() == 1

        # The layout map is warped along with the frame in the viewer, so the
        # flat overlay item on top of it would only double it.
        self.view.set_overlay_visible(self.overlay_check.isChecked() and not viewer)

        if self._flat_frame is None:
            return
        shown = self._flat_frame

        if viewer:
            try:
                engine = self.preview.viewer_engine(
                    (shown.shape[1], shown.shape[0]))
                # The map is megabytes; upload it when it changes, not per frame.
                if getattr(engine, "overlay_id", None) != id(self._overlay_array):
                    engine.set_overlay(self._overlay_array
                                       if self._overlay_array is not None
                                       else np.zeros((1, 1, 4), dtype=np.uint8))
                    engine.overlay_id = id(self._overlay_array)
                engine.set_overlay_opacity(
                    self.overlay_slider.value() / 100
                    if self.overlay_check.isChecked() else 0.0)
                shown = engine.render(shown)
            except Exception as error:  # noqa: BLE001 -- shown in the window
                self._note(self.preview_note, f"viewer unavailable: {error}", "error")
                self.view.modes.setCurrentIndex(0)
                return

        height, width = shown.shape[:2]
        self._preview_buffer = shown if shown.flags["C_CONTIGUOUS"] else \
            np.ascontiguousarray(shown)
        # The engine weights colour by coverage, so say so: read as straight
        # alpha the silhouette would be composited by its own alpha twice, and
        # over a checkerboard that shows.
        picture = QImage(self._preview_buffer.data, width, height,
                         width * 4, QImage.Format.Format_RGBA8888_Premultiplied)
        self._preview_image = picture          # QPixmap does not take ownership
        self.view.set_pixmap(QPixmap.fromImage(picture))

    # -- render ------------------------------------------------------------

    def _source_alpha(self) -> str:
        """What the picker says, with Auto already asked of the file.

        Answered once per source and remembered, because it costs a decode and
        the answer is a property of the file rather than of the frame.
        """
        chosen = self.src_alpha_combo.currentData() or "auto"
        if chosen != "auto" or self.current is None:
            return chosen
        if self._detected_alpha is None:
            try:
                # Through the decoder already in this process rather than
                # through ffmpeg: the preview must work before ffmpeg is there.
                owner, _ = app_jobs.read_frame(
                    self.current, self.timeline.value(), True)
                self._detected_alpha = remap_render.alpha_convention_of(
                    owner.to_ndarray(format="rgba"))
            except Exception:  # noqa: BLE001 -- a guess, never a blocker
                self._detected_alpha = "opaque"
            self._show_source_alpha()
        return self._detected_alpha

    def _show_source_alpha(self) -> None:
        """Say what Auto decided, where the source's other facts are."""
        if self.src_alpha_combo.currentData() != "auto":
            return
        found = self._detected_alpha
        if found in (None, "opaque"):
            return
        self._note(self.source_note, f"alpha in this file reads as {found}")

    def _on_source_alpha(self) -> None:
        self._detected_alpha = None
        self._note(self.source_note, "")
        self._preview_timer.start()

    def _resolution_name(self) -> str:
        percent = self.resolution_group.checkedId()
        for name, value in constants.RESOLUTION_PRESETS:
            if value == percent:
                return name
        return constants.RESOLUTION_PRESETS[0][0]

    def _render_running(self) -> bool:
        return self.job is not None and self.job.isRunning()

    def _output_kind(self) -> str:
        return self.format_combo.currentData() or "h264"

    def _output_extension(self) -> str:
        kind = self._output_kind()
        for _, name, extension in constants.OUTPUT_FORMATS:
            if name == kind:
                return extension
        return ".mp4"

    def _on_format_changed(self) -> None:
        """Follow the format with the file extension and the enabled controls.

        Each control belongs to some formats and not others: bit depth is a PNG
        idea, alpha only exists where the container can carry it, and the
        encoder choice means nothing for a still image. Anything that does not
        apply is greyed out rather than silently ignored.
        """
        kind = self._output_kind()

        self.depth_combo.setEnabled(kind == "png")
        self.encoder_combo.setEnabled(kind in ("h264", "hevc"))
        self.alpha_check.setEnabled(kind in ("png", "prores"))
        if not self.alpha_check.isEnabled() and self.alpha_check.isChecked():
            self.alpha_check.setChecked(False)

        self.depth_combo.setToolTip(
            "PNG bit depth" if kind == "png" else "Only a PNG sequence has a bit depth")
        self.alpha_check.setToolTip(
            "Keep transparency" if self.alpha_check.isEnabled()
            else "This format cannot carry transparency; the frame stays over black")

        name = self.filename_edit.text().strip()
        if name:
            self.filename_edit.setText(str(Path(name).with_suffix(self._output_extension())))
        self._check_output()

    def _as_render_target(self, base: Path) -> Path:
        """What ffmpeg is actually told to write.

        A PNG sequence is thousands of files, so it goes into a folder of its
        own named after the output instead of next to everything else.
        """
        if self._output_kind() == "png":
            folder = base.with_suffix("")
            return folder / (folder.name + "_%08d.png")
        return base

    def _flipbook_path(self) -> Path:
        """Beside the render, named so the two can never be confused."""
        base = self.output_path()
        prefix = "viewer_" if self._viewing_from_camera() else ""
        return _unique_path(base.parent, f"{prefix}{base.stem}_flipbook{base.suffix}")

    def check_dependencies(self) -> None:
        """Show the list on the first run, and later only if something is gone."""
        found = depends.check()
        self._log(depends.summary())
        self._ffmpeg_ok = next(item.ok for item in found if item.name == "ffmpeg")

        settings = constants.load_settings()
        missing = any(item.required and not item.ok for item in found)
        if settings.get("checked_dependencies") and not missing:
            self._update_render_enabled()
            return

        DependencyDialog(found, self).exec()
        remap_render.refresh_ffmpeg()
        self._ffmpeg_ok = bool(depends.ffmpeg_version())
        settings = constants.load_settings()
        settings["checked_dependencies"] = True
        constants.save_settings(settings)
        self._update_render_enabled()

    def _update_render_enabled(self) -> None:
        idle = not self._render_running()
        self._working(not idle)
        known = self.current is not None and self.current.count > 0
        ready = known and idle and constants.tables_present() and self._ffmpeg_ok
        self.render_button.setEnabled(ready and not self._output_blocked())
        # A flipbook names its own file, so an existing render does not block it.
        self.flipbook_button.setEnabled(ready and bool(self.filename_edit.text().strip()))
        self.snapshot_button.setEnabled((known or self._dropped is not None) and idle)
        self.cancel_button.setEnabled(not idle)

    def _start_render(self, flipbook: bool = False) -> None:
        """Start a render, or a flipbook of what the preview is showing."""
        if self.current is None or self._render_running():
            return

        viewer_table = None
        if flipbook and self._viewing_from_camera():
            viewer_table = constants.viewer_table_path()
            if viewer_table is None:
                self._note(self.output_note,
                           "no baked viewer's view beside the application", "error")
                return

        first, last = self.start_spin.value(), self.end_spin.value()
        width, height = self._output_resolution()
        base = self._flipbook_path() if flipbook else self.output_path()
        target = self._as_render_target(base)
        target.parent.mkdir(parents=True, exist_ok=True)

        opacity = (self.overlay_slider.value() / 100
                   if flipbook and self.overlay_check.isChecked()
                   and self._overlay_array is not None else 0.0)
        output = remap_render.Output(
            kind=self._output_kind(),
            path=target,
            fps=int(self._output_fps()),
            alpha=self.alpha_check.isChecked(),
            png_depth=self.depth_combo.currentData() or 8,
            encoder=self.encoder_combo.currentData() or "auto",
            width=width,
            height=height,
            supersample=self.supersample_check.isChecked(),
        )

        self._log("-" * 78)
        self._log(
            f"{'flipbook' if flipbook else 'render'}: "
            f"{self.current.name}{self.current.extension}   frames {first}-{last}"
            f" at {self._input_fps():g} fps   ->   {output.fps} fps   "
            f"{width}x{height}"
            f"{'   fine sampling' if self.supersample_check.isChecked() else ''}"
            f"   {self.format_combo.currentText()} "
            f"[{output.chosen_encoder}]   ->   {constants.display(base)}"
        )
        # Only a single file can be half-written; a folder of PNGs is resumable.
        self._job_output = base if self._output_kind() != "png" else None
        self._job_target = base
        self._job_started_at = time.monotonic()
        self.progress.setRange(0, remap_render.frames_out(
            last - first + 1, self._input_fps(), self._output_fps()))
        self.progress.setValue(0)
        self.eta_label.setText("starting ...")

        self.job = app_jobs.RenderJob(
            self.current, first, last,
            constants.table_path(self._resolution_name()), output,
            viewer_table_path=viewer_table,
            overlay=self._overlay_array if opacity > 0.0 else None,
            overlay_opacity=opacity,
            source_fps=self._input_fps(),
            alpha_mode=self._source_alpha())
        self.job.progress.connect(self._on_job_progress)
        self.job.failed.connect(self._on_job_failed)
        self.job.finished_ok.connect(self._on_job_finished)
        self.job.start()
        self._update_render_enabled()

    def _open_log_folder(self) -> None:
        target = logfile.path()
        if target is None:
            self._note(self.output_note, "no log file this session", "warn")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))

    def _cancel_render(self) -> None:
        if self.job is not None:
            self.job.cancel()

    # -- job signals -------------------------------------------------------

    def _on_job_progress(self, done: int, total: int, fps: float, eta: float) -> None:
        self.progress.setValue(done)
        self.eta_label.setText(f"{done}/{total}   |   {fps:.1f} fps   |   ETA {eta:.0f} s")

    def _run_pending_drop(self) -> None:
        pending, self._pending_drop = self._pending_drop, None
        if pending is not None:
            QTimer.singleShot(0, lambda: self.preview_single_frame(pending))

    def _on_job_failed(self, message: str) -> None:
        # ffmpeg's own words can run to several lines; the label takes the
        # first, the log takes all of it, because that is what gets pasted
        # into a message when someone asks why it did not work.
        lines = message.strip().splitlines() or [message]
        self.eta_label.setText(lines[0])
        self._log("FAILED: " + message)
        self._discard_partial_output()
        if self._pending_drop is not None:
            self._note(self.preview_note,
                       f"dropped {self._pending_drop.name} was not rendered", "error")
            self._pending_drop = None
        self._update_render_enabled()

    def _discard_partial_output(self) -> None:
        """Throw away the unplayable fragment a cancelled render leaves behind.

        Render is only ever enabled when the target does not exist, so anything
        sitting there now was written by the run that just died.
        """
        target = self._job_output
        self._job_output = None
        if target is None or not target.exists():
            return
        try:
            target.unlink()
            self._log(f"removed the partial file {constants.display(target)}")
        except OSError as error:
            self._log(f"could not remove {constants.display(target)}: {error}")

    def _on_job_finished(self, result: dict) -> None:
        self._job_output = None
        self.progress.setValue(self.progress.maximum())
        frames = result["frames"]
        seconds = result["seconds"]
        rate = result["fps"]
        self.eta_label.setText(f"{frames} frames in {seconds:.1f} s ({rate:.1f} fps)")
        self._log(f"saved: {constants.display(self._job_target or self.output_path())}"
                  f"   ({result.get('encoder', '')})")
        self.engine_label.setText(
            "{} {} ({})".format(result["engine"], result["adapter"], result["backend"]).strip())
        if result.get("complaints"):
            self._log("ffmpeg said: " + result["complaints"])
        self._check_output()
        self._update_render_enabled()
        self._run_pending_drop()

    # -- drag and drop -----------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if not paths:
            return
        event.acceptProposedAction()
        self._handle_drop(paths[0])

    def _handle_drop(self, path: Path) -> None:
        """A folder switches the source, a movie gets picked, an image is previewed."""
        if path.is_dir():
            self._set_source(constants.display(path))
            return

        suffix = path.suffix.lower()
        if suffix in scan.VIDEO_EXTENSIONS:
            self._set_source(constants.display(path.parent))
            self._select_by_path(path)
            return

        if suffix in scan.IMAGE_EXTENSIONS:
            self._scrub_to_dropped(path)
            self.preview_single_frame(path)
            return

        self._log(f"dropped file is neither an image nor a movie: {path.name}")

    def _select_by_path(self, path: Path) -> None:
        for row, source in enumerate(self.sequences):
            if source.kind == "movie" and source.path == path:
                self.seq_table.selectRow(row)
                return

    def _scrub_to_dropped(self, path: Path) -> None:
        """If the dropped image belongs to the loaded sequence, follow it."""
        source = self.current
        if source is None or source.kind != "sequence" or path.parent != source.directory:
            return
        stem = path.stem
        if not stem.startswith(source.prefix) or path.suffix.lower() != source.extension:
            return
        digits = stem[len(source.prefix):]
        if digits.isdigit() and int(digits) in source.numbers:
            self.timeline.set_value(int(digits))

    # -- misc --------------------------------------------------------------

    @staticmethod
    def _note(label: QLabel, text: str, level: str = "") -> None:
        colors = {"error": "color:#e06c6c;", "warn": "color:#d9a441;"}
        label.setText(text)
        label.setStyleSheet(colors.get(level, "color:#8fbf8f;" if text else ""))

    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)
        logfile.write(message)


STYLESHEET = """
QWidget { background:#232323; color:#dcdcdc; font-size:12px; }
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTableWidget {
    background:#1b1b1b; border:1px solid #3a3a3a; border-radius:3px; padding:3px; }
QPushButton { background:#333; border:1px solid #454545; border-radius:3px; padding:5px 12px; }
QPushButton:hover { background:#3c3c3c; }
QPushButton:disabled { color:#666; background:#2a2a2a; }
QComboBox:disabled, QSpinBox:disabled, QLineEdit:disabled {
    color:#5a5a5a; background:#232323; border-color:#333; }
QCheckBox:disabled, QRadioButton:disabled { color:#5a5a5a; }
/* Checkboxes paint themselves -- see CheckBox -- so no ::indicator rules. */
QProgressBar { background:#1b1b1b; border:1px solid #3a3a3a; border-radius:3px;
               text-align:center; }
QProgressBar::chunk { background:#4a7ea8; }
QHeaderView::section { background:#2c2c2c; border:0; padding:4px; }
QTableWidget::item:selected { background:#3d6d91; }
QStatusBar { color:#d9a441; }
"""


def main() -> int:
    # Before anything else: a windowed build has no console, so until this is
    # open there is nowhere for a failure to be recorded.
    written = logfile.start()

    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    # Unpack whatever the folder is missing before the window reads any of it.
    prepared = constants.ensure_project_files()

    window = MainWindow()
    if written is not None:
        window._log(f"log: {constants.display(written)}")
    for line in prepared:
        window._log(line)
    window.show()
    window.check_dependencies()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
