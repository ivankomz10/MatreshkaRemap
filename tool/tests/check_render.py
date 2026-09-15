"""The warp itself: transparency, the identity, and what fills the edges.

These go through the real renderer -- the card if there is one, the CPU path
if not -- because the alpha fault that started all this lived in the shader
and the CPU path had it too.
"""
from __future__ import annotations

import numpy as np

import remap_engine
import remap_render
import transform as xf
import world
from harness import Failure, check, inside, want

GROUP = "the warp"

SOURCE = (400, 472)          # the authored shape, small enough to be quick
_engines: dict = {}


def engine(name: str = "Quarter"):
    """One renderer per table, kept: building it uploads the table."""
    if name not in _engines:
        _engines[name] = remap_engine.make_remapper(world.table(name), SOURCE)
    made = _engines[name]
    made.set_premultiplied(False)
    made.set_source_alpha(remap_engine.ALPHA_IGNORE)
    made.set_transform(None)
    if hasattr(made, "set_output_yuv"):
        made.set_output_yuv(False)
    return made


def holed(straight: bool = True) -> np.ndarray:
    """Opaque red with a transparent square through the middle of it."""
    width, height = SOURCE
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[..., 0], pixels[..., 1], pixels[..., 2] = 220, 40, 40
    pixels[..., 3] = 255
    hole = (slice(height // 4, 3 * height // 4), slice(width // 4, 3 * width // 4))
    pixels[hole + (3,)] = 0
    if not straight:
        pixels[hole + (slice(0, 3),)] = 0        # premultiplied: colour goes too
    return pixels


def middle_of(image: np.ndarray, shape) -> np.ndarray:
    """The pixels of the finished frame that read from the middle of the clip.

    Kept well inside the hole. The warp samples a mip level and takes four
    taps, so a band right up against the rim carries some of the rim with it,
    and what that would measure is the filter rather than the matte.
    """
    inside_hole = ((shape.window_x > 0.35) & (shape.window_x < 0.65)
                   & (shape.window_y > 0.35) & (shape.window_y < 0.65) & shape.live)
    return image[inside_hole]


def opacity(image: np.ndarray, shape) -> float:
    """How opaque the middle came out, against how opaque the wall can be
    there at all.

    Absolute alpha is not the measure: the wall is lamellas and a pixel that
    only half lands on one is half covered whatever the clip says. So the
    matte is read as a share of the coverage the same spot has with a solid
    clip -- which is what the eye compares it against too.
    """
    return float(np.mean(middle_of(image, shape)[:, 3]))


@check(GROUP, "a renderer comes up at all")
def renderer_exists():
    made = engine()
    want(made.name in ("GPU", "CPU"), f"the renderer calls itself {made.name}")
    frame = np.full((SOURCE[1], SOURCE[0], 4), 200, dtype=np.uint8)
    out = made.render(frame)
    height, width = world.table("Quarter").coverage.shape
    want(out.shape == (height, width, 4),
         f"the frame came back {out.shape}, wanted {(height, width, 4)}")
    return f"{made.name}, {width} x {height} out of {SOURCE[0]} x {SOURCE[1]} in"


@check(GROUP, "a hole in the clip is a hole on the wall")
def alpha_survives():
    """The fault that started this: a ProRes 4444 clip with a clear middle
    came back with a black square in it. Both conventions are checked, because
    the file said one thing and the pipeline assumed the other."""
    shape = world.window_map("Quarter")
    made = engine()
    made.set_source_alpha(remap_engine.ALPHA_IGNORE)
    filled = opacity(made.render(holed(straight=True)), shape)
    want(filled > 200, f"the wall only reaches {filled:.0f}/255 there even solid")

    notes = []
    for label, straight in (("straight", True), ("premultiplied", False)):
        made = engine()
        made.set_source_alpha(remap_engine.ALPHA_STRAIGHT if straight
                              else remap_engine.ALPHA_PREMULTIPLIED)
        out = made.render(holed(straight))
        left = opacity(out, shape)
        share = left / filled
        if share > 0.02:
            raise Failure(f"{label}: the hole came back {100 * share:.1f}% as "
                          f"opaque as solid content in the same place")
        want(out[..., 3].max() > 240,
             f"{label}: nothing on the wall is opaque at all")
        notes.append(f"{label} {100 * share:.2f}%")
    return "the hole keeps under " + ", ".join(notes) + " of solid"


@check(GROUP, "alpha is ignored when the picker says to ignore it")
def alpha_can_be_ignored():
    shape = world.window_map("Quarter")
    made = engine()
    made.set_source_alpha(remap_engine.ALPHA_IGNORE)
    holed_out = made.render(holed(straight=True)).copy()
    solid = np.full((SOURCE[1], SOURCE[0], 4), 255, dtype=np.uint8)
    solid_out = made.render(solid)
    # Ignoring alpha means the matte makes no difference at all, so the two
    # come back with the same shape on the wall.
    apart = int(np.abs(holed_out[..., 3].astype(int)
                       - solid_out[..., 3].astype(int)).max())
    if apart:
        raise Failure(f"with alpha off, a matte still changed the wall by "
                      f"{apart} levels")
    return f"a clip with a hole and one without render the same shape"


@check(GROUP, "the convention in a file is read off the file")
def convention_is_detected():
    want(remap_render.alpha_convention_of(holed(straight=True)) in
         ("straight", "premultiplied"), "a holed picture was called opaque")
    solid = np.full((8, 8, 4), 255, dtype=np.uint8)
    want(remap_render.alpha_convention_of(solid) == "opaque",
         "a solid picture was not called opaque")
    # Straight alpha is colour brighter than its own matte.
    bright = np.zeros((8, 8, 4), dtype=np.uint8)
    bright[..., :3], bright[..., 3] = 230, 90
    want(remap_render.alpha_convention_of(bright) == "straight",
         "colour well over its matte was not called straight")
    dim = np.zeros((8, 8, 4), dtype=np.uint8)
    dim[..., :3], dim[..., 3] = 60, 90
    want(remap_render.alpha_convention_of(dim) == "premultiplied",
         "colour under its matte was not called premultiplied")
    want(remap_render.alpha_setting("straight") == remap_engine.ALPHA_STRAIGHT,
         "the window's word for straight does not reach the shader")
    return "opaque, straight and premultiplied all told apart"


@check(GROUP, "an untouched framing renders exactly what it always did")
def identity_is_untouched():
    """The transform path is skipped entirely at the identity, so anything
    rendered before the editor existed is the same bytes now."""
    frame = (np.random.default_rng(2)
             .integers(0, 255, (SOURCE[1], SOURCE[0], 4), dtype=np.uint8))
    frame[..., 3] = 255
    made = engine()
    made.set_transform(None)
    without = made.render(frame).copy()
    made.set_transform(xf.Transform())
    with_identity = made.render(frame).copy()
    same = int(np.abs(without.astype(int) - with_identity.astype(int)).max())
    if same:
        raise Failure(f"the identity transform changed {same} levels of a pixel")
    return "bit for bit the same with the transform switched off and at identity"


@check(GROUP, "a framing moves the picture and a big scale fills more of it")
def transform_reaches_the_card():
    frame = np.full((SOURCE[1], SOURCE[0], 4), 255, dtype=np.uint8)
    frame[..., :3] = (30, 200, 90)
    made = engine()
    made.set_source_alpha(remap_engine.ALPHA_IGNORE)

    small = xf.Transform(scale_x=0.4, scale_y=0.4)
    made.set_transform(small)
    tight = made.render(frame)
    covered_small = float((tight[..., 3] > 200).mean())

    made.set_transform(xf.Transform(scale_x=0.9, scale_y=0.9))
    wide = made.render(frame)
    covered_big = float((wide[..., 3] > 200).mean())

    want(covered_big > covered_small * 1.5,
         f"a scale of 0.9 covers {covered_big:.3f} of the wall and 0.4 covers "
         f"{covered_small:.3f}")
    inside(covered_small, 0.001, 0.9, "a small clip covers")
    return (f"0.4 covers {100 * covered_small:.0f}% of the wall, "
            f"0.9 covers {100 * covered_big:.0f}%")


@check(GROUP, "what lies beyond the clip is what the menu says")
def edge_modes_do_what_they_say():
    frame = np.full((SOURCE[1], SOURCE[0], 4), 255, dtype=np.uint8)
    frame[..., :3] = (240, 240, 240)
    made = engine()
    made.set_source_alpha(remap_engine.ALPHA_IGNORE)
    seen = {}
    for label, value in xf.EDGE_MODES:
        made.set_transform(xf.Transform(scale_x=0.35, scale_y=0.35, edge=value))
        out = made.render(frame)
        seen[label] = (float((out[..., 3] < 16).mean()),
                       float((out[..., :3].max(axis=2) > 200).mean()))
    clear, _ = seen["Transparent"]
    want(clear > 0.05, f"Transparent left only {100 * clear:.1f}% of the wall clear")
    black_clear, black_light = seen["Black"]
    want(black_clear < clear * 0.5,
         "Black left as much of the wall transparent as Transparent did")
    repeat_clear, repeat_light = seen["Repeat edge"]
    want(repeat_light > black_light * 1.5,
         f"Repeat edge lit {100 * repeat_light:.0f}% and Black {100 * black_light:.0f}%")
    return (f"transparent leaves {100 * clear:.0f}% clear, black {100 * black_clear:.0f}%, "
            f"repeat lights {100 * repeat_light:.0f}%")


@check(GROUP, "a flip is a mirror and two flips are none")
def flips_mirror():
    frame = np.zeros((SOURCE[1], SOURCE[0], 4), dtype=np.uint8)
    frame[..., 3] = 255
    frame[:, : SOURCE[0] // 2, 0] = 255            # red down the left
    frame[:, SOURCE[0] // 2:, 2] = 255             # blue down the right
    made = engine()
    made.set_source_alpha(remap_engine.ALPHA_IGNORE)

    made.set_transform(xf.Transform())
    plain = made.render(frame).copy()
    made.set_transform(xf.Transform(flip_h=True))
    mirrored = made.render(frame).copy()
    want(int(np.abs(plain.astype(int) - mirrored.astype(int)).max()) > 100,
         "flipping across changed nothing")
    # Red and blue have swapped over: where it was red it is now blue.
    was_red = plain[..., 0] > 200
    want(float((mirrored[was_red][:, 2] > 200).mean()) > 0.8,
         "the halves did not change places")

    made.set_transform(xf.Transform(flip_h=True, flip_v=True))
    both = made.render(frame).copy()
    made.set_transform(xf.Transform(angle=180.0))
    turned = made.render(frame).copy()
    apart = float(np.abs(both.astype(int) - turned.astype(int)).mean())
    want(apart < 6.0,
         f"both flips and a half turn differ by {apart:.1f} levels a pixel")
    return f"mirrored, and both flips match a half turn to {apart:.1f} levels"


@check(GROUP, "the wall is warped the same at every resolution")
def resolutions_agree():
    """Half and Quarter are the same picture with fewer pixels in it. If a
    table is ever rebaked out of step with the others this is where it shows.
    """
    frame = (np.random.default_rng(7)
             .integers(0, 255, (SOURCE[1], SOURCE[0], 4), dtype=np.uint8))
    frame[..., 3] = 255
    seen = {}
    for name in ("Half", "Quarter"):
        made = engine(name)
        made.set_source_alpha(remap_engine.ALPHA_IGNORE)
        made.set_transform(xf.Transform(scale_x=0.5, scale_y=0.5))
        out = made.render(frame)
        seen[name] = float((out[..., 3] > 200).mean())
    apart = abs(seen["Half"] - seen["Quarter"])
    want(apart < 0.02,
         f"the clip covers {seen['Half']:.3f} of the wall at Half and "
         f"{seen['Quarter']:.3f} at Quarter")
    return (f"the same clip covers {100 * seen['Half']:.1f}% at Half and "
            f"{100 * seen['Quarter']:.1f}% at Quarter")
