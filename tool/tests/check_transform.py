"""The numbers behind the framing: identity, fit, the pivot, and undo."""
from __future__ import annotations

import math

import transform as xf
from harness import check, close, want

GROUP = "the transform"

CORNERS = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.37, 0.61)]


@check(GROUP, "an untouched framing is the renderer's old behaviour")
def identity_is_identity():
    """Anything rendered without opening the editor must be the same bytes.

    `is_identity` is what turns the whole transform path off in the shader, so
    if a fresh transform ever stops answering yes to it, every render made
    before the editor existed stops matching.
    """
    fresh = xf.Transform()
    want(fresh.is_identity, "a fresh transform is not the identity")
    want(fresh.is_default, "a fresh transform is not the default")
    for x, y in CORNERS:
        got = fresh.forward(x, y)
        close(got[0], x, 1e-12, "forward x")
        close(got[1], y, 1e-12, "forward y")
    return "identity maps every corner to itself"


@check(GROUP, "forward and inverse undo each other")
def round_trip():
    worst = 0.0
    for angle in (0.0, 17.0, -90.0, 143.5):
        for flip_h, flip_v in ((False, False), (True, False), (True, True)):
            placement = xf.Transform(x=0.11, y=-0.07, scale_x=0.83, scale_y=1.31,
                                     angle=angle, pivot_x=0.3, pivot_y=0.75,
                                     flip_h=flip_h, flip_v=flip_v)
            for x, y in CORNERS:
                back = placement.inverse(*placement.forward(x, y))
                worst = max(worst, abs(back[0] - x), abs(back[1] - y))
    want(worst < 1e-9, f"the round trip drifts by {worst:.3g}")
    return f"worst drift {worst:.2g} over rotations, flips and an off-centre pivot"


@check(GROUP, "moving the pivot alone leaves the picture where it was")
def pivot_is_not_a_move():
    """The bug this replaces: a moved pivot sprang back.

    Moving the pivot changes nothing about where the picture lands, so the
    state looked like the identity and was thrown away between one frame and
    the next. `is_identity` and `is_default` are different questions and this
    is the difference.
    """
    placement = xf.Transform()
    placement.pivot_x, placement.pivot_y = 0.0, 1.0
    want(placement.is_identity, "a moved pivot changed where the picture lands")
    want(not placement.is_default, "a moved pivot is nothing worth keeping")
    for x, y in CORNERS:
        got = placement.forward(x, y)
        close(got[0], x, 1e-12, "the picture moved across")
        close(got[1], y, 1e-12, "the picture moved down")
    return "identity to the shader, worth writing down to the file"


@check(GROUP, "Fit stays inside the window and Fill covers it")
def fit_and_fill():
    """And both come out undistorted, which is the whole point of them.

    A clip scaled by (sx, sy) covers SOURCE_ASPECT * sx by sy of true
    proportion, so looking right means sx / sy is the clip's own aspect over
    the window's.
    """
    notes = []
    for width, height in ((1920, 1080), (2000, 2360), (1080, 1920), (4096, 1716)):
        wanted = (width / height) / xf.SOURCE_ASPECT
        for cover in (False, True):
            made = xf.Transform().fitted(width, height, cover)
            close(made.scale_x / made.scale_y, wanted, 1e-9,
                  f"{width}x{height} {'fill' if cover else 'fit'} is distorted")
            if cover:
                want(made.scale_x >= 1.0 - 1e-9 and made.scale_y >= 1.0 - 1e-9,
                     f"{width}x{height} fill leaves a gap")
            else:
                want(made.scale_x <= 1.0 + 1e-9 and made.scale_y <= 1.0 + 1e-9,
                     f"{width}x{height} fit hangs over the edge")
        notes.append(f"{width}x{height}")
    return "undistorted for " + ", ".join(notes)


@check(GROUP, "the crop cuts and does not rescale")
def crop_cuts():
    placement = xf.Transform(crop_left=0.25, crop_bottom=0.5)
    left, top, right, bottom = placement.kept
    close(left, 0.25, 1e-12, "left")
    close(bottom, 0.5, 1e-12, "bottom")
    # What is left stays where it was: the corner that survived has not moved.
    got = placement.forward(0.25, 0.0)
    close(got[0], 0.25, 1e-12, "the surviving corner moved across")
    close(got[1], 0.0, 1e-12, "the surviving corner moved down")
    return "cropped edges move, the rest stays put"


@check(GROUP, "the shader gets sixteen finite numbers in a fixed order")
def uniforms():
    placement = xf.Transform(x=0.2, scale_x=0.0, angle=30.0)
    values = placement.uniforms()
    want(len(values) == 16, f"{len(values)} numbers, not sixteen")
    want(all(math.isfinite(value) for value in values),
         f"a uniform came out {values}")
    close(values[0], math.cos(math.radians(30.0)), 1e-12, "cos")
    # A scale of zero must not divide by zero on the way to the card.
    want(values[2] < 1e7, "a zero scale became an infinity")
    return "sixteen finite numbers, a zero scale survives"


@check(GROUP, "undo is per source and remembers a hundred steps")
def history():
    past = xf.History()
    first, second = "a.mov", "b.mov"
    here = xf.Transform()
    for step in range(150):
        moved = xf.Transform(x=step / 1000)
        past.remember(first, here)
        here = moved
    past.remember(second, xf.Transform(y=0.5))

    deep, ahead = past.depth(first)
    want(deep == xf.History.DEPTH,
         f"{first} kept {deep} steps, not {xf.History.DEPTH}")
    want(past.depth(second)[0] == 1, "the second source picked up the first's past")

    went_back = past.undo(first, here)
    want(went_back is not None, "undo gave nothing back")
    came_forward = past.redo(first, went_back)
    want(came_forward is not None, "redo gave nothing back")
    want(came_forward.to_dict() == here.to_dict(),
         "redo did not land back where undo started")

    # A new move ends the old future, or redo walks into a branch nobody took.
    past.remember(first, xf.Transform(x=9.0))
    want(past.depth(first)[1] == 0, "a new move left a future behind it")
    return f"{xf.History.DEPTH} steps deep, kept apart per source"


@check(GROUP, "a framing survives being written down and read back")
def to_and_from_dict():
    placement = xf.Transform(x=0.1, y=-0.2, scale_x=1.5, scale_y=0.5, angle=12.0,
                             pivot_x=0.25, pivot_y=0.75, flip_h=True,
                             crop_top=0.1, edge=2)
    again = xf.Transform.from_dict(placement.to_dict())
    want(again.to_dict() == placement.to_dict(), "the framing changed on the way")
    # And an old file missing a field still loads.
    thin = {"x": 0.3, "scale_y": 2.0}
    loaded = xf.Transform.from_dict(thin)
    close(loaded.x, 0.3, 1e-12, "x")
    close(loaded.scale_x, 1.0, 1e-12, "a missing field did not default")
    return "round trips, and an older file still loads"
