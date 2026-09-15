"""Aiming with the mouse, driven through the real widget.

Every check here presses, moves and releases the actual preview with actual
mouse events, and then measures in screen pixels -- because screen pixels are
the only unit the person dragging can see, and because both times this went
wrong the arithmetic underneath was perfectly self-consistent and still put
the handle somewhere the hand was not.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPixmap

import gizmo
import world
from harness import Failure, check, close, inside, want

GROUP = "aiming with the mouse"

# Short enough that a handle stays inside the window. Past its edge there is
# no frame position to read back, and the measurement rather than the drag is
# what would have stopped.
STEP = 60
WAYS = [(STEP, 0), (-STEP, 0), (0, STEP), (0, -STEP),
        (STEP * 3 // 4, STEP * 3 // 4), (-STEP * 3 // 4, -STEP * 3 // 4)]

# A drag of sixty pixels that finishes more than twenty from the cursor is
# wrong enough to feel. The faults this suite exists to catch missed by 150 to
# 450 -- the handle left the pointer behind entirely -- so there is a wide gap
# between passing and the failures that have actually happened.
SLACK = 20.0
WORST = STEP


def _corner_spots(view):
    return {name: spot for name, spot in view.gizmo.handles().items()
            if name.startswith("corner")}


def _inside_the_clip(view, shape, placement):
    """A screen point on the clip but away from the pivot, for a slide."""
    found = shape.frame_at(*placement.forward(0.32, 0.63))
    return view._screen_point(found) if found else None


def _pull(view, shape, seed, dx, dy, quarter=False):
    """One press-move-release. Returns how far the held handle finished from
    where the cursor did, in screen pixels."""
    placement = world.framing()
    view.set_pixmap(QPixmap(shape.width, shape.height))
    view.show_gizmo(placement, shape, True)
    view.fit()
    world.pump()

    if seed == "move":
        start = _inside_the_clip(view, shape, placement)
        if start is None:
            return None
    else:
        spots = view.gizmo.handles()
        if seed not in spots:
            return None
        start = view._screen_point(spots[seed])

    world.press(view, start)
    held = view.gizmo._grab
    if held != seed:
        world.release(view, start)
        return None
    if seed == "move":
        # the point of the content that was taken hold of
        anchor = (0.32, 0.63)
        was = view._screen_point(shape.frame_at(*placement.forward(*anchor)))
    if quarter:
        # what the flat preview really does the instant a handle is grabbed
        view.set_pixmap(QPixmap(shape.width // 4, shape.height // 4))
    for part in (0.3, 0.6, 1.0):
        world.move_to(view, QPointF(start.x() + dx * part, start.y() + dy * part))
    world.release(view, QPointF(start.x() + dx, start.y() + dy))
    if quarter:
        view.set_pixmap(QPixmap(shape.width, shape.height))

    if seed == "move":
        landed = shape.frame_at(*placement.forward(*anchor))
        if landed is None:
            return None
        now = view._screen_point(landed)
        went = (now.x() - was.x(), now.y() - was.y())
    else:
        spots = view.gizmo.handles()
        if seed not in spots:
            return None
        now = view._screen_point(spots[seed])
        went = (now.x() - start.x(), now.y() - start.y())
    return went


def _sweep(quarter: bool):
    """Every handle, every direction, both views. The core measurement."""
    report = []
    for label, viewer in world.BOTH_VIEWS:
        shape = world.window_map("Full", viewer)
        view = world.preview(shape)
        errors, reversals, tried = [], 0, 0
        for seed in ("corner0", "corner1", "corner2", "corner3", "move"):
            for dx, dy in WAYS:
                went = _pull(view, shape, seed, dx, dy, quarter)
                if went is None:
                    continue
                tried += 1
                errors.append(float(np.hypot(went[0] - dx, went[1] - dy)))
                if went[0] * dx + went[1] * dy <= 0:
                    reversals += 1
        want(tried >= 20, f"{label}: only {tried} drags could be made at all")
        if reversals:
            raise Failure(f"{label}: {reversals} of {tried} drags went the "
                          f"wrong way")
        median, worst = float(np.median(errors)), float(np.max(errors))
        if median > SLACK or worst > WORST:
            raise Failure(f"{label}: the handle finished a median {median:.1f} px "
                          f"from the cursor, worst {worst:.1f}, over {tried} "
                          f"drags of {STEP} px")
        report.append(f"{label} {median:.1f} px (worst {worst:.1f}, {tried} drags)")
    return "; ".join(report)


@check(GROUP, "the handle goes where the hand goes")
def handle_follows_cursor():
    return _sweep(quarter=False)


@check(GROUP, "and still does when the warp drops to a quarter mid-drag")
def survives_the_quarter_warp():
    """The flat preview warps at quarter size while a handle is held.

    Four times less work, so the picture keeps up with the hand -- but the
    table the handles are computed against is the full one, so for a while the
    cursor was measured in quarter-frame pixels against a full-frame map. The
    box was drawn four times too far out and the drag ran at a quarter rate:
    a sixty pixel pull finished 150 to 450 pixels from the pointer.

    The camera view never showed it, because its gather always writes the
    viewer table's own size whatever it is fed.
    """
    return _sweep(quarter=True)


@check(GROUP, "the handles are drawn on the picture, whatever size it is")
def drawn_where_the_picture_is():
    """The same check from the painter's side rather than the mouse's."""
    notes = []
    for label, viewer in world.BOTH_VIEWS:
        shape = world.window_map("Full", viewer)
        view = world.preview(shape)
        full = {name: view._screen_point(spot)
                for name, spot in view.gizmo.handles().items()}
        view.set_pixmap(QPixmap(shape.width // 4, shape.height // 4))
        world.pump()
        quarter = {name: view._screen_point(spot)
                   for name, spot in view.gizmo.handles().items()}
        gaps = [float(np.hypot(quarter[name].x() - point.x(),
                               quarter[name].y() - point.y()))
                for name, point in full.items() if name in quarter]
        want(gaps, f"{label}: no handles were drawn at all")
        worst = max(gaps)
        if worst > 3.0:
            raise Failure(f"{label}: a handle moved {worst:.1f} px on screen "
                          f"when only the warp size changed")
        notes.append(f"{label} {worst:.1f} px")
    return "handles hold their place within " + ", ".join(notes)


@check(GROUP, "changing the warp size does not throw the view about")
def zoom_survives_a_resolution_change():
    """Grabbing a handle used to refit the view, and letting go refit it back.

    A frame of another size is not another picture, and someone who has just
    zoomed in on a corner should not lose it by touching that corner.
    """
    shape = world.window_map("Full")
    view = world.preview(shape)
    view.scale(3.0, 3.0)
    world.pump()

    def state():
        wide = view._item.pixmap().width()
        tall = view._item.pixmap().height()
        middle = view.mapToScene(view.viewport().rect().center())
        return (abs(view.transform().m11()) * wide,
                middle.x() / wide, middle.y() / tall)

    was = state()
    view.set_pixmap(QPixmap(shape.width // 4, shape.height // 4))
    held = state()
    view.set_pixmap(QPixmap(shape.width, shape.height))
    back = state()
    for label, got in (("while held", held), ("after", back)):
        close(got[0], was[0], 2.0, f"the picture changed size on screen {label}")
        close(got[1], was[1], 0.01, f"the view slid across {label}")
        close(got[2], was[2], 0.01, f"the view slid down {label}")
    return (f"{was[0]:.0f} px wide before, {held[0]:.0f} held, {back[0]:.0f} after; "
            f"the middle moves {abs(back[1] - was[1]):.4f} of the frame")


@check(GROUP, "the pivot does not swallow the middle of the picture")
def pivot_keeps_to_its_ring():
    """It sits in the middle by default, and moving it leaves the picture put.

    With the corners' reach it caught every attempt to slide the clip, and the
    drag read as nothing happening at all. It answers to the ring that is
    drawn for it and nothing wider.
    """
    notes = []
    for label, viewer in world.BOTH_VIEWS:
        shape = world.window_map("Full", viewer)
        view = world.preview(shape)
        spots = view.gizmo.handles()
        want("pivot" in spots, f"{label}: no pivot was drawn")
        middle = view._screen_point(spots["pivot"])
        caught = {}
        for out in (0, 5, 7, 11, 20, 40):
            world.press(view, QPointF(middle.x() + out, middle.y()))
            caught[out] = view.gizmo._grab
            world.release(view, QPointF(middle.x() + out, middle.y()))
        want(caught[0] == "pivot", f"{label}: the pivot itself was not grabbable")
        want(caught[5] == "pivot", f"{label}: the pivot ring is too small at 5 px")
        for out in (11, 20, 40):
            want(caught[out] != "pivot",
                 f"{label}: the pivot still caught the click {out} px away")
        notes.append(f"{label} holds to {gizmo.HANDLE:.0f} px")
    return "; ".join(notes)


@check(GROUP, "moving the pivot moves the pivot and nothing else")
def pivot_moves_alone():
    notes = []
    for label, viewer in world.BOTH_VIEWS:
        shape = world.window_map("Full", viewer)
        placement = world.framing()
        view = world.preview(shape, placement)
        start = view._screen_point(view.gizmo.handles()["pivot"])
        corners_was = [placement.forward(*spot)
                       for spot in ((0.0, 0.0), (1.0, 1.0))]
        held = world.drag(view, start, 90, 60)
        want(held == "pivot", f"{label}: the drag took hold of {held}")
        corners_now = [placement.forward(*spot)
                       for spot in ((0.0, 0.0), (1.0, 1.0))]
        slid = max(float(np.hypot(now[0] - was[0], now[1] - was[1]))
                   for was, now in zip(corners_was, corners_now))
        if slid > 1e-6:
            raise Failure(f"{label}: the picture slid {slid:.5f} of the window "
                          f"while only the pivot was dragged")
        went = view._screen_point(view.gizmo.handles()["pivot"])
        close(went.x() - start.x(), 90, 25, f"{label}: the pivot went across")
        close(went.y() - start.y(), 60, 25, f"{label}: the pivot went down")
        notes.append(f"{label} picture held to {slid:.1e}")
    return "; ".join(notes)


@check(GROUP, "the pivot snaps at the distance it advertises")
def snap_catches_and_lets_go():
    """The reach is twelve screen pixels, and used to be measured in pixels of
    the clip -- so it grabbed from half a screen away on a small clip and was
    unreachable on a large one."""
    notes = []
    for label, viewer in world.BOTH_VIEWS:
        shape = world.window_map("Full", viewer)
        view = world.preview(shape)
        middle = view._screen_point(view.gizmo.handles()["pivot"])
        caught = {}
        for out in (2, 8, 11, 16, 30):
            world.press(view, middle)
            for part in (0.5, 1.0):
                world.move_to(view, QPointF(middle.x() + out * part, middle.y()))
            caught[out] = view.gizmo.snapped
            world.release(view, QPointF(middle.x() + out, middle.y()))
        for out in (2, 8, 11):
            want(caught[out] == (0.5, 0.5),
                 f"{label}: the snap let go {out} px out, before its {gizmo.SNAP_PULL:.0f}")
        for out in (16, 30):
            want(caught[out] is None,
                 f"{label}: the snap still held {out} px out, past its "
                 f"{gizmo.SNAP_PULL:.0f}")
        notes.append(f"{label} holds to 11 px, free by 16")
    return "; ".join(notes)


@check(GROUP, "rotation turns the way the hand turns, evenly both ways")
def rotation_is_even():
    notes = []
    for label, viewer in world.BOTH_VIEWS:
        shape = world.window_map("Full", viewer)
        turns = []
        for side in (-1, 1):
            placement = world.framing()
            view = world.preview(shape, placement)
            arm = view._turn_spot()
            want(arm is not None, f"{label}: no rotation handle was drawn")
            world.press(view, arm)
            want(getattr(view, "_turning", False),
                 f"{label}: pressing the arm did not start a rotation")
            for part in (0.5, 1.0):
                world.move_to(view, QPointF(arm.x() + side * 150 * part, arm.y()))
            world.release(view, QPointF(arm.x() + side * 150, arm.y()))
            turns.append(placement.angle)
        want(turns[0] < -5.0 < 5.0 < turns[1],
             f"{label}: turning left gave {turns[0]:.1f} and right {turns[1]:.1f}")
        # Not exactly equal: the centre of the turn is the pivot's drawn
        # position, which comes off a coarse grid, so the two arms are not
        # quite mirror images. A degree and a half of difference over eighty
        # is the grid, not the maths.
        close(abs(turns[0]), abs(turns[1]), 2.5,
              f"{label}: the two directions turn by different amounts")
        notes.append(f"{label} {turns[0]:+.1f} / {turns[1]:+.1f} degrees")
    return "; ".join(notes)


@check(GROUP, "an edge handle is free along one axis only")
def edges_are_single_axis():
    shape = world.window_map("Full", viewer=True)
    view = world.preview(shape)
    moved = []
    for index in range(4):
        name = f"edge{index}"
        spots = view.gizmo.handles()
        if name not in spots:
            continue
        for dx, dy in ((STEP, 0), (0, STEP)):
            placement = world.framing()
            view.show_gizmo(placement, shape, True)
            world.pump()
            start = view._screen_point(view.gizmo.handles()[name])
            was = (placement.scale_x, placement.scale_y)
            if world.drag(view, start, dx, dy) != name:
                continue
            across = abs(placement.scale_x - was[0]) > 1e-6
            down = abs(placement.scale_y - was[1]) > 1e-6
            want(not (across and down),
                 f"{name}: a drag of {dx},{dy} changed both axes at once")
            moved.append(f"{name}{'x' if across else 'y' if down else '-'}")
    want(len(moved) >= 4, f"only {len(moved)} edge drags could be made")
    return f"{len(moved)} edge drags, none touched both axes"


@check(GROUP, "letting go leaves the framing exactly where the drag left it")
def release_does_not_nudge():
    """The release renders once more at full size, and that pass must not be
    allowed to recompute the framing from anything."""
    for label, viewer in world.BOTH_VIEWS:
        shape = world.window_map("Full", viewer)
        placement = world.framing()
        view = world.preview(shape, placement)
        start = view._screen_point(view.gizmo.handles()["corner2"])
        world.press(view, start)
        for part in (0.5, 1.0):
            world.move_to(view, QPointF(start.x() + 70 * part, start.y() + 40 * part))
        before = placement.to_dict()
        world.release(view, QPointF(start.x() + 70, start.y() + 40))
        want(placement.to_dict() == before,
             f"{label}: the framing changed on release")
    return "the framing is settled before the button comes up"
