"""The numbers behind the handles.

The handles live over the picture, in `gizmo.py`; this is the other half, where
the same state is readable and typeable. A wall is built to drawings, and
"about there" is not a measurement.

Numbers are shown in pixels of the source frame and kept as fractions of it, so
swapping a clip of one size for another leaves the framing looking the same.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

import icons
import transform as xf

LABEL = 14                   # one letter: the section heading says the rest
ENTRY = 66
ART = 18                     # icons, big enough to read beside nine-point type

# What "on" and "changed" look like. The app's own heading colour, so a lit
# control belongs to the same window as everything else.
ACCENT = "#7fa7cc"
LIT = ("QPushButton:checked { background-color: rgba(127,167,204,55); "
       "border: 1px solid %s; }" % ACCENT)


class ScrubEdit(QLineEdit):
    """A number you can type, or roll the wheel over.

    The middle button did this job for a while and has been taken off again.
    Windows and most mouse drivers can bind it to autoscroll, and then the
    press never reaches the application at all -- the picture pans and the
    field sits there unchanged, which reads as a broken field rather than as a
    busy driver. A wheel notch cannot be taken away like that.
    """

    nudged = Signal(float)       # one wheel notch, in units of one step

    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setFixedWidth(ENTRY)
        self.setToolTip("Roll the wheel over it, or drag the letter beside it.\n"
                        "Shift for a tenth, Ctrl for ten times.")

    def wheelEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        """One notch, one step -- and no driver in the way."""
        notches = event.angleDelta().y() / 120.0
        if not notches:
            return
        keys = event.modifiers()
        weight = 1.0
        if keys & Qt.KeyboardModifier.ShiftModifier:
            weight = 0.1
        elif keys & Qt.KeyboardModifier.ControlModifier:
            weight = 10.0
        self.nudged.emit(notches * weight)
        event.accept()


class NumberField(QWidget):
    """An icon, and a number that can be typed or dragged."""

    edited = Signal(float)

    def __init__(self, mark: str = "", decimals: int = 1, step: float = 1.0,
                 tip: str = "") -> None:
        super().__init__()
        self.decimals = decimals
        self.step = step
        self._value = 0.0
        self._before = 0.0
        self._from: float | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        # One letter rather than an icon: the section above already carries
        # the picture, and four unlabelled boxes under "Crop" is a guess.
        self.mark = QLabel(mark)
        self.mark.setFixedWidth(LABEL)
        self.mark.setStyleSheet("color:#9a9a9a;")
        # The letter drags with the left button. It is a label, so there is no
        # text to select and nothing to be ambiguous about -- and it is the one
        # way in that neither the driver nor the caret can take.
        self.mark.setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
        row.addWidget(self.mark)

        self.edit = ScrubEdit()
        self.edit.editingFinished.connect(self._typed)
        self.edit.nudged.connect(self._nudged)
        if tip:
            self.setToolTip(tip)
        row.addWidget(self.edit)

    def show_value(self, value: float) -> None:
        """Put a number in without claiming anyone edited it."""
        self._value = float(value)
        if not self.edit.hasFocus():
            self.edit.setText(f"{self._value:.{self.decimals}f}")

    def value(self) -> float:
        return self._value

    def _typed(self) -> None:
        text = self.edit.text().replace(",", ".").strip()
        try:
            value = float(text)
        except ValueError:
            self.show_value(self._value)
            return
        self._value = value
        self.show_value(value)
        self.edited.emit(value)

    def _nudged(self, notches: float) -> None:
        self.show_value(self._value + notches * self.step)
        self.edited.emit(self._value)

    def mousePressEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        if (event.button() == Qt.MouseButton.LeftButton
                and self.mark.geometry().contains(event.position().toPoint())):
            self._from = event.position().x()
            self._before = self._value
            return
        self._from = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._from is None:
            super().mouseMoveEvent(event)
            return
        moved = event.position().x() - self._from
        fine = 0.1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        self.show_value(self._before + moved * self.step * fine)
        self.edited.emit(self._value)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._from = None
        super().mouseReleaseEvent(event)


class TransformPanel(QFrame):
    """The numbers, laid out the way an inspector lays them out."""

    changed = Signal()
    fitRequested = Signal(bool)      # True to cover the window, False to fit inside
    resetRequested = Signal()

    # Which fields are pixels of the source rather than plain numbers, and on
    # which axis they are measured.
    PIXEL_FIELDS = {"x": "w", "pivot_x": "w", "crop_left": "w", "crop_right": "w",
                    "y": "h", "pivot_y": "h", "crop_top": "h", "crop_bottom": "h"}

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        # Wide enough for two fields and a reset, and for the three buttons
        # along the bottom, whichever asks for more. A minimum rather than a
        # fixed size, so the same panel works floating and filling a column.
        self.setMinimumWidth(2 * (LABEL + ENTRY) + 108)
        self.setFixedWidth(2 * (LABEL + ENTRY) + 108)
        self.placement = xf.Transform()
        self.source_size = (0, 0)
        self._quiet = False
        self.fields: dict[str, NumberField] = {}
        self.resets: list[tuple[QPushButton, tuple]] = []
        self._rows: list = []
        self._wide: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        self._section("Position", "position", layout)
        self._pair(layout, ("x", "Across, in pixels of the source frame"),
                   ("y", "Down, in pixels of the source frame"))

        self._section("Scale", "scale", layout)
        self.link = QPushButton()
        self.link.setCheckable(True)
        self.link.setChecked(True)
        self.link.setFixedWidth(30)
        self.link.setStyleSheet(LIT)
        self.link.setToolTip("Scale both axes together. Off, they move apart.")
        self.link.toggled.connect(self._show_link)
        self._pair(layout, ("scale_x", "Across, as a multiplier"),
                   ("scale_y", "Down, as a multiplier"),
                   decimals=4, step=0.002, between=self.link)

        self._section("Rotation", "rotation", layout)
        self._one(layout, "angle", "Degrees, anticlockwise, about the pivot",
                  decimals=2, step=0.25)

        self._section("Pivot", "pivot", layout)
        self._pair(layout, ("pivot_x", "What scale and rotation turn about"),
                   ("pivot_y", "Drag it on the picture and it snaps to the "
                               "corners, the edges and the middle"))

        self._section("Flip", "flip_h", layout)
        flips = QHBoxLayout()
        flips.setSpacing(4)
        self.flip_h = QPushButton(" Across")
        self.flip_v = QPushButton(" Down")
        self._flip_art = {self.flip_h: "flip_h", self.flip_v: "flip_v"}
        for button, art in self._flip_art.items():
            button.setCheckable(True)
            button.setStyleSheet(LIT)
            icons.put(button, art, ART)
            button.toggled.connect(self._from_widgets)
            flips.addWidget(button)
        self._wide_row(layout, flips)

        self._section("Crop", "crop", layout)
        self._pair(layout, ("crop_left", "Cut off the left"),
                   ("crop_right", "Cut off the right"))
        self._pair(layout, ("crop_top", "Cut off the top"),
                   ("crop_bottom", "Cut off the bottom"))

        self._section("Beyond the clip", "edge", layout)
        self.edge = QComboBox()
        for label, value in xf.EDGE_MODES:
            self.edge.addItem(label, value)
        self.edge.setToolTip(
            "What fills the frame where the clip no longer reaches. Transparent "
            "goes into the alpha, so PNG and ProRes 4444 carry a hole there and "
            "H.264 shows black.")
        self.edge.currentIndexChanged.connect(self._from_widgets)
        edge_row = QHBoxLayout()
        edge_row.addWidget(self.edge)
        self._wide_row(layout, edge_row)

        layout.addSpacing(8)
        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        for art, label, tip, slot in (
                ("fit", "Fit", "The whole clip inside the window, undistorted",
                 lambda: self.fitRequested.emit(False)),
                ("fill", "Fill", "The clip over the whole window, undistorted",
                 lambda: self.fitRequested.emit(True)),
                ("stretch", "Stretch", "Back to the whole frame across the whole "
                                       "window -- what the renderer did before "
                                       "there was a transform",
                 lambda: self.resetRequested.emit())):
            button = QPushButton(f" {label}")
            icons.put(button, art, ART)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        self._wide_row(layout, buttons)
        # Somewhere for the window to hang things that belong to the framing
        # but not to the transform itself -- saved presets, and spreading one
        # across a selection.
        self._extras = QVBoxLayout()
        self._extras.setSpacing(4)
        layout.addSpacing(10)
        layout.addLayout(self._extras)
        layout.addStretch(1)
        self._show_link()
        self._show_resets()

    def extras(self):
        """The empty run under the buttons, for the window to fill."""
        return self._extras

    def fill_width(self) -> None:
        """Let the panel take whatever column it is put in.

        Floating over the picture it should be no wider than it needs; in a tab
        it should use the space, or the fields sit in a narrow strip with the
        rest of the column blank beside them.
        """
        self.setMaximumWidth(16777215)
        self.setMinimumWidth(2 * (LABEL + ENTRY) + 108)
        for field in self.fields.values():
            # Room to breathe, but a number does not want a hundred pixels of
            # empty box: the rows stay a block on the left rather than
            # stretching to whatever the column happens to be.
            field.edit.setMinimumWidth(ENTRY)
            field.edit.setMaximumWidth(ENTRY + 25)
        for row in self._rows:
            row.addStretch(1)
        # The buttons and the combo keep to the same block as the numbers, so
        # the panel reads as one column and not as two different widths. The
        # cap goes on the row, not on each button: capping the buttons lets the
        # stretch take the difference and they shrink to their own words.
        for holder in self._wide:
            # Fixed, not a maximum: a maximum caps a row that was never going
            # to reach it anyway, and the buttons stay the width of their own
            # words while the numbers above them run wider.
            holder.setFixedWidth(self._block())

    def _block(self) -> int:
        """How wide a row of two fields and a reset arrow comes out."""
        return 2 * (LABEL + 4 + ENTRY + 25) + 4 + 28 + 10

    def _wide_row(self, layout, inner) -> None:
        """A row that would otherwise stretch, held to the block width."""
        holder = QWidget()
        holder.setLayout(inner)
        inner.setContentsMargins(0, 0, 0, 0)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(holder)
        line.addStretch(1)
        layout.addLayout(line)
        self._wide.append(holder)

    # -- building ----------------------------------------------------------

    def _section(self, title: str, art: str, layout) -> None:
        if layout.count():
            layout.addSpacing(7)
        row = QHBoxLayout()
        row.setSpacing(5)
        mark = QLabel()
        mark.setFixedWidth(ART)
        icons.mark(mark, art, ART)
        row.addWidget(mark)
        label = QLabel(title)
        label.setStyleSheet("color:#7fa7cc; font-weight:600;")
        row.addWidget(label, 1)
        layout.addLayout(row)

    MARKS = {"x": "X", "y": "Y", "scale_x": "X", "scale_y": "Y",
             "pivot_x": "X", "pivot_y": "Y", "crop_left": "L", "crop_right": "R",
             "crop_top": "T", "crop_bottom": "B", "angle": ""}

    def _one(self, layout, key: str, tip: str, **kw) -> None:
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(self._field(key, tip, **kw))
        row.addSpacing(ENTRY + LABEL + 48)
        row.addWidget(self._reset(key))
        layout.addLayout(row)
        self._rows.append(row)

    def _pair(self, layout, first, second, between=None, **kw) -> None:
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(self._field(first[0], first[1], **kw))
        if between is not None:
            row.addWidget(between)
        row.addWidget(self._field(second[0], second[1], **kw))
        row.addWidget(self._reset(first[0], second[0]))
        layout.addLayout(row)
        self._rows.append(row)

    def _field(self, key: str, tip: str, **kw) -> NumberField:
        field = NumberField(mark=self.MARKS.get(key, ""), tip=tip, **kw)
        field.edited.connect(lambda _value, k=key: self._field_edited(k))
        self.fields[key] = field
        return field

    def _reset(self, *keys: str) -> QPushButton:
        button = QPushButton()
        button.setFixedWidth(28)
        button.setToolTip("Back to the default")
        button.clicked.connect(lambda _=False, k=keys: self._reset_keys(k))
        self.resets.append((button, keys))
        return button

    def _default_placement(self) -> xf.Transform:
        """The framing a clip of this shape opens at.

        Identity everywhere but the scale, which is set so the clip sits in the
        window undistorted -- the same number `main._placement` opens a fresh
        clip with. On a clip already shaped like the wall's picture this is the
        bare identity; on anything else the scale's default is the fit, not one.

        Resetting a field, and deciding whether its arrow has anything to undo,
        both answer to this rather than to a bare `Transform()`: otherwise the
        scale arrow lights on every clip that is not the wall's own shape and
        springs the picture back to stretched when pressed.
        """
        width, height = self.source_size
        return xf.Transform().fitted(width, height, False)

    def _show_resets(self) -> None:
        """Lit where there is something to undo, and dead where there is not.

        An arrow beside a field that is already at its default is a button that
        does nothing, and a row of them says nothing about which values have
        been touched.
        """
        fresh = self._default_placement()
        for button, keys in self.resets:
            changed = any(getattr(self.placement, key) != getattr(fresh, key)
                          for key in keys)
            button.setEnabled(changed)
            icons.put(button, "reset", 16,
                      QColor(ACCENT) if changed else icons.ink(button))

    def _show_link(self, on: bool | None = None) -> None:
        """A closed padlock or an open one -- an icon that changes shape says it
        better than one that only changes colour."""
        locked = self.link.isChecked() if on is None else bool(on)
        icons.put(self.link, "link_on" if locked else "link_off", ART,
                  QColor(ACCENT) if locked else icons.ink(self.link))

    def _reset_keys(self, keys) -> None:
        fresh = self._default_placement()
        for key in keys:
            setattr(self.placement, key, getattr(fresh, key))
        self.show_placement(self.placement, self.source_size)
        self.changed.emit()

    # -- pixels out, fractions in ------------------------------------------

    def _scale_for(self, key: str) -> float:
        """How many pixels one whole unit of this field is worth."""
        axis = self.PIXEL_FIELDS.get(key)
        if axis is None:
            return 1.0
        width, height = self.source_size
        return float((width if axis == "w" else height) or 1)

    def show_placement(self, placement: xf.Transform,
                       source_size: tuple[int, int]) -> None:
        """Put a transform on screen without treating it as an edit."""
        self.placement = placement
        self.source_size = source_size
        self._quiet = True
        for key, field in self.fields.items():
            field.show_value(getattr(placement, key) * self._scale_for(key))
        self.flip_h.setChecked(placement.flip_h)
        self.flip_v.setChecked(placement.flip_v)
        self.edge.setCurrentIndex(max(0, self.edge.findData(placement.edge)))
        self._show_resets()
        self._show_flips()
        self._quiet = False

    def _field_edited(self, key: str) -> None:
        if self._quiet:
            return
        value = self.fields[key].value() / self._scale_for(key)
        if key in ("scale_x", "scale_y") and self.link.isChecked():
            other = "scale_y" if key == "scale_x" else "scale_x"
            was = getattr(self.placement, key)
            if abs(was) > 1e-9:
                setattr(self.placement, other,
                        getattr(self.placement, other) * value / was)
        setattr(self.placement, key, value)
        self.show_placement(self.placement, self.source_size)
        self.changed.emit()

    def _show_flips(self) -> None:
        for button, art in self._flip_art.items():
            icons.put(button, art, ART,
                      QColor(ACCENT) if button.isChecked() else icons.ink(button))

    def _from_widgets(self) -> None:
        self._show_flips()
        if self._quiet:
            return
        self.placement.flip_h = self.flip_h.isChecked()
        self.placement.flip_v = self.flip_v.isChecked()
        self.placement.edge = self.edge.currentData() or 0
        self.changed.emit()
