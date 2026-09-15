"""The numbers beside the picture: typing, scrubbing, and what lights up."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent

import transform as xf
import transform_ui as ui
import world
from harness import check, close, want

GROUP = "the transform panel"

SOURCE = (2000, 2360)


def _panel():
    world.app()
    panel = ui.TransformPanel()
    panel.show_placement(xf.Transform(), SOURCE)
    world.pump()
    return panel


def _heard(panel):
    """Collect the panel's changed signal."""
    said = []
    panel.changed.connect(lambda: said.append(1))
    return said


@check(GROUP, "the wheel over a number field moves that number")
def wheel_scrubs():
    """And over the field, not over the name beside it.

    The wheel first landed on the label, where nothing happened, which reads
    as a dead control rather than a misplaced one.
    """
    panel = _panel()
    said = _heard(panel)
    field = panel.fields["x"]
    was = field.value()
    world.wheel(field.edit, field.edit.rect().center(), 3)
    world.pump()
    moved = field.value() - was
    want(abs(moved) > 0, "the wheel over the number changed nothing")
    want(said, "the wheel moved the number without telling the window")
    back = field.value()
    world.wheel(field.edit, field.edit.rect().center(), -3)
    want(abs(field.value() - was) < abs(back - was),
         "the wheel does not go back the other way")
    return f"three notches move it {moved:+.1f} px of the source"


@check(GROUP, "dragging the letter beside a field moves the number")
def letter_drag_scrubs():
    panel = _panel()
    field = panel.fields["angle"]
    was = field.value()
    spot = QPointF(field.mark.geometry().center())
    for kind, at, buttons in (
            (QEvent.Type.MouseButtonPress, spot, Qt.MouseButton.NoButton),
            (QEvent.Type.MouseMove, QPointF(spot.x() + 40, spot.y()),
             Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseButtonRelease, QPointF(spot.x() + 40, spot.y()),
             Qt.MouseButton.NoButton)):
        event = QMouseEvent(kind, at, field.mapToGlobal(at),
                            Qt.MouseButton.LeftButton, buttons,
                            Qt.KeyboardModifier.NoModifier)
        {QEvent.Type.MouseButtonPress: field.mousePressEvent,
         QEvent.Type.MouseMove: field.mouseMoveEvent,
         QEvent.Type.MouseButtonRelease: field.mouseReleaseEvent}[kind](event)
    moved = field.value() - was
    want(abs(moved) > 0, "dragging the letter changed nothing")
    return f"forty pixels of drag turn it {moved:+.2f} degrees"


@check(GROUP, "a number typed in is read in pixels of the source")
def typing_reads_pixels():
    """The fields show pixels because that is what anyone thinks in; the
    framing is kept as fractions so a clip of another size looks the same."""
    panel = _panel()
    panel.fields["x"].edit.setText("200")
    panel.fields["x"]._typed()
    world.pump()
    close(panel.placement.x, 200 / SOURCE[0], 1e-9, "200 px across")
    panel.fields["pivot_y"].edit.setText("590")
    panel.fields["pivot_y"]._typed()
    close(panel.placement.pivot_y, 590 / SOURCE[1], 1e-9, "590 px down")
    # And a scale is a plain multiplier, not pixels.
    panel.fields["scale_x"].edit.setText("1.5")
    panel.fields["scale_x"]._typed()
    close(panel.placement.scale_x, 1.5, 1e-9, "a scale of one and a half")
    return "200 px across is 0.1 of a 2000 px frame; a scale stays a multiplier"


@check(GROUP, "the reset arrows light up only where something changed")
def resets_light_up():
    """A row of arrows that all look alike says nothing about what was touched."""
    panel = _panel()
    lit = [button.isEnabled() for button, _ in panel.resets]
    want(not any(lit), f"{sum(lit)} reset arrows were lit on a fresh framing")
    changed = xf.Transform(angle=12.0)
    panel.show_placement(changed, SOURCE)
    world.pump()
    lit = {tuple(keys): button.isEnabled() for button, keys in panel.resets}
    want(lit.get(("angle",)), "the rotation reset did not light up")
    others = [keys for keys, on in lit.items() if on and keys != ("angle",)]
    want(not others, f"these lit up as well: {others}")
    return f"{len(panel.resets)} arrows, one lights for one changed value"


@check(GROUP, "the scale link says which way it is set")
def link_is_legible():
    """Checked and unchecked have to differ by more than a shade -- the icon
    is a closed padlock or an open one."""
    panel = _panel()
    panel.link.setChecked(True)
    world.pump()
    closed = world.painted(panel.link)
    panel.link.setChecked(False)
    world.pump()
    opened = world.painted(panel.link)
    want(closed.shape == opened.shape, "the button changed size instead")
    apart = float(np.abs(closed.astype(int) - opened.astype(int)).mean())
    want(apart > 1.0,
         f"the two states of the link differ by {apart:.2f} of a level per pixel")
    return f"the two states differ by {apart:.1f} levels a pixel"


@check(GROUP, "a flip lights up when it is applied")
def flips_light_up():
    panel = _panel()
    for button, key in ((panel.flip_h, "flip_h"), (panel.flip_v, "flip_v")):
        button.setChecked(False)
        world.pump()
        off = world.painted(button)
        button.setChecked(True)
        world.pump()
        on = world.painted(button)
        apart = float(np.abs(on.astype(int) - off.astype(int)).mean())
        want(apart > 1.0, f"{key} looks the same on and off ({apart:.2f})")
        want(getattr(panel.placement, key), f"{key} did not reach the framing")
        button.setChecked(False)
    return "both flips change how they look and reach the framing"


@check(GROUP, "Fit, Fill and Stretch ask the window for the right thing")
def fit_buttons_speak():
    panel = _panel()
    heard = []
    panel.fitRequested.connect(lambda cover: heard.append(("fit", cover)))
    panel.resetRequested.connect(lambda: heard.append(("reset", None)))
    buttons = [child for child in panel.findChildren(type(panel.link))
               if child.text().strip() in ("Fit", "Fill", "Stretch")]
    want(len(buttons) == 3, f"found {len(buttons)} of the three buttons")
    for button in buttons:
        button.click()
    world.pump()
    want(("fit", False) in heard, "Fit did not ask to fit inside")
    want(("fit", True) in heard, "Fill did not ask to cover")
    want(("reset", None) in heard, "Stretch did not ask for the old behaviour")
    return "Fit inside, Fill over, Stretch back to what the renderer always did"


@check(GROUP, "the edge menu offers what the shader knows")
def edge_modes_match():
    panel = _panel()
    want(panel.edge.count() == len(xf.EDGE_MODES),
         f"the menu has {panel.edge.count()} entries and the shader "
         f"{len(xf.EDGE_MODES)}")
    for index, (label, value) in enumerate(xf.EDGE_MODES):
        want(panel.edge.itemText(index) == label,
             f"entry {index} reads {panel.edge.itemText(index)!r}, not {label!r}")
        want(panel.edge.itemData(index) == value,
             f"{label} carries {panel.edge.itemData(index)}, not {value}")
    return ", ".join(label for label, _ in xf.EDGE_MODES)
