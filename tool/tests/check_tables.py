"""The baked geometry, and the slope that every drag is measured against."""
from __future__ import annotations

import numpy as np

import constants
import world
from harness import Failure, check, inside, want

GROUP = "the baked tables"

# The wall, and the picture it was mapped from. Written down rather than read
# off the file, so a table baked at the wrong size is caught here and not in a
# render three hours later.
WALL = (4608, 1584)
CAMERA_VIEW = (1710, 2024)


@check(GROUP, "every resolution loads at the size it claims")
def tables_load():
    sizes = []
    for name, percent in constants.RESOLUTION_PRESETS:
        found = world.table(name)
        height, width = found.coverage.shape
        want(abs(width - WALL[0] * percent / 100) <= 1
             and abs(height - WALL[1] * percent / 100) <= 1,
             f"{name} is {width} x {height}, not {percent}% of {WALL[0]} x {WALL[1]}")
        inside(float(found.coverage.min()), 0.0, 1.0, f"{name} coverage low")
        inside(float(found.coverage.max()), 0.0, 1.0, f"{name} coverage high")
        live = found.coverage > 0
        inside(float(found.u[live].max()), 0.0, 1.0, f"{name} u")
        inside(float(found.v[live].max()), 0.0, 1.0, f"{name} v")
        sizes.append(f"{name} {width}x{height}")
    seen = world.viewer_table()
    height, width = seen.coverage.shape
    want((width, height) == CAMERA_VIEW,
         f"the camera view is {width} x {height}, not {CAMERA_VIEW}")
    return ", ".join(sizes) + f", camera {width}x{height}"


@check(GROUP, "the window map covers what the wall lights")
def maps_build():
    notes = []
    for label, viewer in world.BOTH_VIEWS:
        shape = world.window_map("Full", viewer)
        lit = float(shape.live.mean())
        inside(lit, 0.4, 1.0, f"{label}: share of the frame that shows content")
        notes.append(f"{label} {100 * lit:.0f}% lit")
    return ", ".join(notes)


@check(GROUP, "the flat wall really does fold, so the rest is a fair test")
def the_wall_folds():
    """The fold is the thing that broke aiming twice. Prove it is still there.

    If a rebake ever straightened the wall, the drag checks below would start
    passing for the wrong reason -- so this fails loudly rather than letting
    them go quiet.
    """
    shape = world.window_map("Full")
    across = np.diff(shape.window_x, axis=1)
    both = shape.live[:, :-1] & shape.live[:, 1:]
    forward = float((across[both] > 0).mean())
    inside(forward, 0.55, 0.9,
           "share of the wall where the content runs left to right")
    return f"{100 * forward:.1f}% of the wall runs one way, the rest the other"


@check(GROUP, "the local slope predicts where the window goes")
def slope_is_local_and_signed():
    """The slope a drag runs on, checked against the table it came from.

    Reading it as the middle of the differences between *neighbouring* pixels
    gave 1/2048 everywhere -- the size of a step in the table, not the slope of
    anything -- and no sign, so a third of the wall dragged backwards. Both
    faults show up here as a prediction that misses.
    """
    worst = 0.0
    notes = []
    for label, viewer in world.BOTH_VIEWS:
        shape = world.window_map("Full", viewer)
        rows, columns = np.nonzero(shape.live)
        pick = np.random.default_rng(4).choice(rows.size, 250, replace=False)
        errors = []
        for index in pick:
            x, y = float(columns[index]), float(rows[index])
            slope = shape.jacobian(x, y)
            want(all(np.isfinite(slope)), f"{label}: the slope came back {slope}")
            here = shape.window_at(x, y)
            for dx, dy in ((80, 0), (-80, 0), (0, 80), (0, -80)):
                there = shape.window_at(x + dx, y + dy)
                if here is None or there is None:
                    continue
                guess = (here[0] + slope[0] * dx + slope[1] * dy,
                         here[1] + slope[2] * dx + slope[3] * dy)
                errors.append(float(np.hypot(guess[0] - there[0],
                                             guess[1] - there[1])))
        median = float(np.median(errors))
        # Two per cent of the window over an eighty pixel step. The reading
        # that broke aiming was off by six times that on the flat frame.
        if median > 0.02:
            raise Failure(f"{label}: the slope misses by {median:.4f} of the "
                          f"window over eighty pixels")
        worst = max(worst, median)
        notes.append(f"{label} {median:.4f}")
    return "misses by " + ", ".join(notes) + " of the window over 80 px"


@check(GROUP, "a point of the frame and a point of the window agree")
def round_trip():
    notes = []
    for label, viewer in world.BOTH_VIEWS:
        shape = world.window_map("Full", viewer)
        rows, columns = np.nonzero(shape.live)
        pick = np.random.default_rng(9).choice(rows.size, 300, replace=False)
        gaps = []
        for index in pick:
            x, y = float(columns[index]), float(rows[index])
            spot = shape.window_at(x, y)
            back = shape.frame_at(*spot)
            if back is None:
                continue
            # The wall shows some points of the window twice, so what is
            # checked is that the answer reads back to the same window point,
            # not that it is the same pixel.
            again = shape.window_near(*back)
            gaps.append(float(np.hypot(again[0] - spot[0], again[1] - spot[1])))
        median = float(np.median(gaps))
        if median > 0.01:
            raise Failure(f"{label}: the round trip lands {median:.4f} of the "
                          f"window away")
        notes.append(f"{label} {median:.5f}")
    return "round trip within " + ", ".join(notes) + " of the window"
