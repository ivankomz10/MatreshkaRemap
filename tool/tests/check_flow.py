"""A whole sitting at the program, done the way a person does it.

Point it at a folder, pick the clip, aim it, scrub, warm the cache, scrub
again, take a snapshot. Every step through the widgets rather than the methods
behind them, because most of what has gone wrong here has gone wrong in the
wiring between two parts that were each correct.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, Qt

import constants
import framecache
import world
from harness import Failure, check, inside, want

GROUP = "a sitting at the program"

FRAMES = 6
SHOT = "checkshot"


def _sequence() -> Path:
    """A short numbered sequence, made once and left in the scratch folder."""
    from PySide6.QtGui import QImage
    folder = world.scratch() / "sequence"
    folder.mkdir(parents=True, exist_ok=True)
    if len(list(folder.glob(f"{SHOT}_*.png"))) == FRAMES:
        return folder
    width, height = 480, 270
    for number in range(1, FRAMES + 1):
        pixels = np.zeros((height, width, 4), dtype=np.uint8)
        pixels[..., 3] = 255
        # A different band lit per frame, so one frame can be told from another.
        band = (number - 1) * height // FRAMES
        pixels[band: band + height // FRAMES, :, :3] = (240, 200, 60)
        pixels[..., 0] = np.maximum(pixels[..., 0], 20 * number)
        picture = QImage(bytes(pixels.tobytes()), width, height, width * 4,
                         QImage.Format.Format_RGBA8888)
        picture.save(str(folder / f"{SHOT}_{number:04d}.png"))
    return folder


def _open_the_sequence():
    made = world.window()
    folder = _sequence()
    made.source_edit.setText(constants.display(folder))
    made._rescan()
    world.pump()
    names = [made.seq_table.item(row, 0).text()
             for row in range(made.seq_table.rowCount())]
    want(names, f"nothing was found in {folder}")
    row = next((index for index, name in enumerate(names)
                if name.startswith(SHOT)), None)
    want(row is not None, f"the test sequence is not in {names}")
    # Cleared first: selecting the row that is already selected raises no
    # signal, and then the window never picks the clip up at all.
    made.seq_table.clearSelection()
    made.seq_table.selectRow(row)
    world.pump()
    want(made.current is not None, "picking the row did not load the clip")
    return made


@check(GROUP, "pointing it at a folder finds the sequence in it")
def open_a_folder():
    made = _open_the_sequence()
    want(made.current is not None, "nothing was selected after picking a row")
    want(made.current.name.startswith(SHOT),
         f"it picked {made.current.name} instead")
    want(made.current.count == FRAMES,
         f"it counted {made.current.count} frames, not {FRAMES}")
    want((made.current.width, made.current.height) == (480, 270),
         f"it read the size as {made.current.width} x {made.current.height}")
    return (f"{made.current.name}{made.current.extension}, {made.current.count} "
            f"frames, {made.current.width} x {made.current.height}")


@check(GROUP, "a clip nobody has aimed arrives fitted rather than stretched")
def arrives_fitted():
    """The renderer has always laid the whole frame across the whole window,
    which stretches anything that is not the wall's own shape. Fit is the
    correction and it should not have to be asked for.

    Nothing else may be in the book while this is asked. A clip of the same
    proportions that somebody has already aimed is deliberately picked up
    instead of Fit -- that is the point of keeping the shape beside the
    framing -- so with the operator's own framings in there this measures
    that feature rather than this one.
    """
    made = _open_the_sequence()
    import transform as xf
    kept = dict(made._placements)
    try:
        made._placements.clear()
        placement = made._placement()
    finally:
        made._placements.update(kept)
    wanted = (made.current.width / made.current.height) / xf.SOURCE_ASPECT
    got = placement.scale_x / placement.scale_y
    if abs(got - wanted) > 1e-6:
        raise Failure(f"the clip came up at {got:.4f} to one and its own shape "
                      f"is {wanted:.4f}")
    return f"undistorted at {placement.scale_x:.3f} by {placement.scale_y:.3f}"


@check(GROUP, "and picks up a framing already aimed on a clip of its shape")
def inherits_from_the_same_shape():
    """The other half of the same rule, and the reason the check above has to
    empty the book first."""
    made = _open_the_sequence()
    import transform as xf
    kept = dict(made._placements)
    try:
        made._placements.clear()
        aimed = xf.Transform(x=0.13, scale_x=0.8, scale_y=0.45)
        state = aimed.to_dict()
        state["aspect"] = made.current.width / made.current.height
        made._placements["some other clip.mov"] = state
        got = made._placement()
        want(abs(got.x - aimed.x) < 1e-9 and abs(got.scale_x - aimed.scale_x) < 1e-9,
             f"it came up at x={got.x:.4f} scale={got.scale_x:.4f} instead of "
             f"the framing already aimed on a clip of the same shape")
    finally:
        made._placements.clear()
        made._placements.update(kept)
    return "a clip of the same proportions starts where the last one ended"


@check(GROUP, "the preview draws a frame, and a different one after a scrub")
def scrubbing_draws():
    made = _open_the_sequence()
    seen = {}
    for number in (1, FRAMES):
        made.timeline.set_value(number)
        made._do_preview()
        world.pump()
        want(made._flat_frame is not None, f"frame {number} did not draw")
        seen[number] = made._flat_frame.copy()
    apart = float(np.abs(seen[1].astype(int) - seen[FRAMES].astype(int)).mean())
    want(apart > 1.0,
         f"the first and last frames differ by {apart:.2f} levels a pixel")
    return f"first and last differ by {apart:.1f} levels a pixel"


@check(GROUP, "a warmed frame is served out of memory, not off the disk")
def scrubbing_reads_the_cache():
    """The fault this replaces: the cache filled and scrubbing never looked at
    it, so a frame already in memory cost a container open and a seek -- 673 ms
    at one point, and worse the further into the movie it was.
    """
    made = _open_the_sequence()
    made._cache.begin(made._cache_identity())
    for number in range(1, FRAMES + 1):
        owner, planes = made.preview.decode(made.current, number, False)
        made._cache.put(number, framecache.Held(
            pixels=owner.to_ndarray(format="rgba"), planes=None))
    want(made._cache.count == FRAMES,
         f"only {made._cache.count} of {FRAMES} frames went into the cache")

    # The cache is asked, and it answers.
    want(made._show_cached(FRAMES // 2) is True,
         "a frame sitting in the cache was not served from it")

    # And the preview path goes through it rather than round it.
    asked = []
    real = made._show_cached
    made._show_cached = lambda number: (asked.append(number), real(number))[1]
    try:
        made.timeline.set_value(FRAMES)
        made._do_preview()
    finally:
        made._show_cached = real
    want(asked, "the preview went to the file without asking the cache")

    warm = []
    for number in (2, FRAMES, 3, 1):
        began = time.monotonic()
        made._show_cached(number)
        warm.append(1000 * (time.monotonic() - began))
    made._cache.clear()
    return (f"{FRAMES} frames held, served in a median "
            f"{float(np.median(warm)):.0f} ms")


@check(GROUP, "the cache knows what it holds and lets go when it is full")
def the_cache_keeps_its_word():
    held = framecache.FrameCache()
    held.begin("one")
    want(held.matches("one"), "the cache forgot what it was filled for")
    want(not held.matches("two"), "the cache answered to the wrong source")

    frame = np.zeros((64, 64, 4), dtype=np.uint8)
    for number in range(1, 21):
        held.put(number, framecache.Held(pixels=frame.copy(), planes=None))
    want(held.count == 20, f"it kept {held.count} of twenty")
    want(held.span() == (1, 20), f"it thinks it holds {held.span()}")
    want(held.holds(5, 9), "it does not know it holds a run it holds")
    want(not held.holds(5, 25), "it claims a run that runs past the end")

    # Filling it past its budget throws the old ones out rather than the machine.
    room = held.allowed
    inside(room / (1024 ** 3), 0.4, 33.0, "the cache budget, in gigabytes")

    held.begin("two")
    want(held.count == 0, "starting on another source kept the old frames")
    return f"budget {room / 1024 ** 3:.1f} GB, span and eviction both honest"


@check(GROUP, "aiming with the mouse reaches the numbers in the panel")
def the_gizmo_and_the_panel_agree():
    """Two halves of one state. Dragging a handle has to move the fields, and
    typing in a field has to move the handles."""
    made = _open_the_sequence()
    made.tabs.setCurrentIndex(1)
    world.pump()
    was = made.panel.fields["x"].value()

    view = made.view
    spots = view.gizmo.handles()
    want(spots, "no handles were drawn over a loaded clip")
    inside_clip = view._screen_point(
        view.gizmo.map.frame_at(*made._placement().forward(0.32, 0.63)))
    held = world.drag(view, inside_clip, 80, 0)
    world.pump()
    want(held == "move", f"the drag took hold of {held}")
    now = made.panel.fields["x"].value()
    want(abs(now - was) > 1.0,
         f"the field still reads {now:.1f} after the clip was dragged")

    # And the other way about.
    made.panel.fields["y"].edit.setText("120")
    made.panel.fields["y"]._typed()
    world.pump()
    import transform as xf
    close_enough = abs(made._placement().y - 120 / made.current.height) < 1e-6
    want(close_enough,
         f"typing 120 down gave {made._placement().y:.5f} of the frame")
    made._reset_transform()
    world.pump()
    return f"a drag of 80 px moved the field by {now - was:+.0f} source pixels"


@check(GROUP, "undo takes back the drag and redo puts it on again")
def undo_walks_back():
    made = _open_the_sequence()
    made._reset_transform()
    world.pump()
    before = made._placement().to_dict()

    view = made.view
    inside_clip = view._screen_point(
        view.gizmo.map.frame_at(*made._placement().forward(0.4, 0.5)))
    world.drag(view, inside_clip, 60, 40)
    world.pump()
    moved = made._placement().to_dict()
    want(moved != before, "the drag did not change the framing at all")

    made._undo()
    world.pump()
    want(made._placement().to_dict() == before,
         "undo did not put the framing back")
    made._undo(forward=True)
    world.pump()
    want(made._placement().to_dict() == moved,
         "redo did not put the drag back on")
    made._undo()
    world.pump()
    return "one drag is one step, both ways"


@check(GROUP, "a snapshot is written, and carries its own transparency")
def snapshot_is_written():
    made = _open_the_sequence()
    made.timeline.set_value(2)
    made._do_preview()
    world.pump()
    folder = constants.PROJECT_DIR / "Snapshots"
    before = set(folder.glob("*.png")) if folder.is_dir() else set()
    made._save_snapshot()
    world.pump()
    after = set(folder.glob("*.png")) if folder.is_dir() else set()
    fresh = sorted(after - before)
    want(fresh, "no snapshot appeared in the Snapshots folder")
    written = fresh[-1]
    from PySide6.QtGui import QImage
    picture = QImage(str(written))
    want(not picture.isNull(), f"{written.name} is not a readable picture")
    want(picture.hasAlphaChannel(), f"{written.name} carries no alpha at all")
    # The number in the name is the frame it came from, not a counter.
    want("0002" in written.name or "_2" in written.stem,
         f"{written.name} is not named for frame 2")
    size = written.stat().st_size
    for path in fresh:
        path.unlink(missing_ok=True)
    return f"{written.name}, {size / 1024:.0f} KB, with alpha"


@check(GROUP, "a framing is remembered per clip and comes back with it")
def framings_are_per_clip():
    made = _open_the_sequence()
    made._reset_transform()
    world.pump()
    import transform as xf
    made._store_placement(xf.Transform(x=0.25, scale_x=0.5, scale_y=0.5))
    made._sync_transform_ui()
    world.pump()
    mine = made._placement().to_dict()

    made.current = None
    made._rescan()
    made.seq_table.clearSelection()
    made.seq_table.selectRow(0)
    world.pump()
    want(made.current is not None, "the clip did not come back after a rescan")
    back = made._placement().to_dict()
    want(back == mine,
         "the framing did not come back with the clip it was aimed on")
    made._reset_transform()
    world.pump()
    return "the framing follows the clip"


# -- the other way in: a picture dropped on a program with nothing open ----
#
# Everything above starts by pointing the window at a folder, which is how the
# suite was written and not how the program is always used. Dropping one still
# on a window that has never had a sequence in it is a different path through
# almost every method, and it is the path that was broken.

def _drop_a_still(name: str = "dropped.png"):
    """A fresh picture on the viewport, with no sequence loaded."""
    made = world.window()
    made.current = None
    made._dropped = None
    made.seq_table.clearSelection()
    world.pump()
    picture = world.holed_png(name, (640, 360))
    made._placements.pop(picture.name, None)
    made._handle_drop(picture)
    world.pump()
    want(made._dropped is not None, "the dropped picture was not taken up")
    return made, picture


def _forget(made, *names: str) -> None:
    """Take the test's own framings back out of the book.

    They are filed by file name and kept beside the shape they were aimed on,
    so one left behind is picked up by the next clip of the same proportions
    -- which is the feature working, and a check after it measuring the wrong
    thing.
    """
    for name in names:
        made._placements.pop(name, None)


@check(GROUP, "a picture dropped on an empty window draws")
def a_dropped_still_draws():
    made, picture = _drop_a_still()
    want(made._flat_frame is not None, "nothing was drawn after the drop")
    want(made._dropped.name == picture.name,
         f"it took up {made._dropped} instead")
    want(made._source_shape() == (640, 360),
         f"it read the size as {made._source_shape()}")
    return f"{picture.name}, {made._source_shape()[0]} x {made._source_shape()[1]}"


@check(GROUP, "and the transform works on it, with no sequence loaded")
def transform_works_on_a_dropped_still():
    """The fault: `_do_preview` gave up on `self.current is None` before it
    ever looked at what had been dropped, so every edit after the drop moved
    the numbers and the handles and left the picture exactly as it was.

    The whole editor looked dead, and it was only dead on the path that has no
    sequence behind it -- which is the path someone takes when they open the
    program and drag one still onto it.
    """
    made, _ = _drop_a_still("dropped_edit.png")
    made.tabs.setCurrentIndex(1)
    world.pump()
    made._reset_transform()
    world.pump()
    before = made._flat_frame.copy()

    import transform as xf
    made._store_placement(xf.Transform(x=0.2, scale_x=0.45, scale_y=0.45))
    made._sync_transform_ui()
    made._do_preview()
    world.pump()
    after = made._flat_frame
    apart = float(np.abs(before.astype(int) - after.astype(int)).mean())
    if apart < 1.0:
        raise Failure(f"the picture changed by {apart:.3f} levels a pixel when "
                      f"the framing was moved a fifth of the window and scaled "
                      f"to 0.45 -- the edit never reached the preview")
    _forget(made, "dropped_edit.png")
    return f"an edit moves the picture by {apart:.1f} levels a pixel"


@check(GROUP, "the handles are on the dropped still and drag it")
def gizmo_works_on_a_dropped_still():
    made, _ = _drop_a_still("dropped_drag.png")
    made.tabs.setCurrentIndex(1)
    world.pump()
    made._reset_transform()
    world.pump()

    view = made.view
    want(view.gizmo.visible, "no handles were drawn over the dropped picture")
    spots = view.gizmo.handles()
    want(spots, "the handles came back empty")

    was = made._placement().x
    at = view._screen_point(
        view.gizmo.map.frame_at(*made._placement().forward(0.35, 0.6)))
    held = world.drag(view, at, 90, 0)
    world.pump()
    want(held == "move", f"the drag took hold of {held}")
    now = made._placement().x
    want(abs(now - was) > 1e-4,
         f"the framing still reads {now:.5f} after a 90 px drag")
    want(made.panel.fields["x"].value() != 0.0,
         "the panel did not follow the drag")
    made._reset_transform()
    world.pump()
    _forget(made, "dropped_drag.png")
    return f"a 90 px drag moves it {now - was:+.4f} of the window"


@check(GROUP, "a framing aimed on a dropped still is remembered by its name")
def a_dropped_still_keeps_its_framing():
    made, picture = _drop_a_still("dropped_kept.png")
    import transform as xf
    made._store_placement(xf.Transform(y=-0.15, scale_x=0.7, scale_y=0.7))
    world.pump()
    mine = made._placement().to_dict()
    want(picture.name in made._placements,
         f"the framing was filed under {list(made._placements)[-3:]}")

    made._dropped = None
    made._handle_drop(picture)
    world.pump()
    want(made._placement().to_dict() == mine,
         "the framing did not come back with the picture")
    _forget(made, picture.name)
    return f"filed under {picture.name}"



# -- holding frames in memory ---------------------------------------------

def _warm(made, patience: float = 20.0) -> float:
    """Press the cache button and wait. Returns how long it took, in seconds."""
    began = time.monotonic()
    made.cache_button.click()
    while made._warming() and time.monotonic() - began < patience:
        world.pump()
        time.sleep(0.005)
    if made._warm_job is not None:
        made._warm_job.wait(3000)
    world.pump()
    return time.monotonic() - began


def _count_opens(made) -> int:
    """How many times the warming opens the file, and only the warming.

    Counted on the warming thread alone: the preview goes on redrawing on the
    main thread while this runs, and its decodes are not what is being
    measured.
    """
    import threading

    import app_jobs
    here = threading.current_thread()
    opens = []
    real = app_jobs.avio.Reader

    def counted(*args, **kw):
        if threading.current_thread() is not here:
            opens.append(args[:1])
        return real(*args, **kw)

    app_jobs.avio.Reader = counted
    try:
        _warm(made)
    finally:
        app_jobs.avio.Reader = real
    return len(opens)


@check(GROUP, "the cache is its own button, and it reaches memory")
def the_cache_button_warms():
    made = _open_the_sequence()
    made._forget_cache()
    world.pump()
    want(made._cache.span() is None, "the cache was not empty to begin with")
    made.timeline.set_value(FRAMES // 2)
    world.pump()
    took = _warm(made)
    span = made._cache.span()
    want(span == (1, FRAMES), f"it holds {span}, not the whole {FRAMES} frames")
    want(not made._playing, "pressing the cache button started playback")
    want(not made.cache_button.isChecked(),
         "the button stayed lit after the warming finished")
    return f"holds {span[0]} to {span[1]} in {1000 * took:.0f} ms, no Play touched"


@check(GROUP, "warming runs forward from the first frame, wherever the hand is")
def warming_starts_at_the_first_frame():
    """It was briefly done outwards from the playhead, so the stretch being
    looked at arrived first. That filled the right frames in the right order
    and took longer to finish, because every change of direction is another
    open and another seek. Warming is set going and left; how soon it is all
    there beats what order it arrives in.
    """
    made = _open_the_sequence()
    for standing_on in (1, FRAMES // 2 + 1, FRAMES):
        made._forget_cache()
        world.pump()
        made.timeline.set_value(standing_on)
        world.pump()
        _warm(made)
        span = made._cache.span()
        want(span == (1, FRAMES),
             f"from frame {standing_on} it holds {span}, not 1 to {FRAMES}")
        want(made._cache.holds(*span), f"the run {span} has a hole in it")
    return f"1 to {FRAMES} whatever frame the playhead is on"


@check(GROUP, "warming opens the file once and reads straight through")
def warming_opens_once():
    """This is the whole of why it is fast. Reading in blocks so the middle
    arrives first meant an open and a seek per block, and on a long clip that
    is most of the work.
    """
    import app_jobs
    made = _open_the_sequence()
    made._forget_cache()
    world.pump()

    opens = _count_opens(made)
    want(made._cache.span() == (1, FRAMES),
         f"it only got as far as {made._cache.span()}")
    if opens != 1:
        raise Failure(f"{FRAMES} frames cost {opens} opens of the file")
    return f"{FRAMES} frames, one open"


@check(GROUP, "asking again picks up where it left off")
def warming_resumes():
    made = _open_the_sequence()
    made._forget_cache()
    world.pump()
    _warm(made)
    want(made._cache.span() == (1, FRAMES), "the first run did not finish")

    opens = _count_opens(made)
    want(not opens,
         f"a full cache was read again from disk ({opens} opens)")
    want(made._cache.span() == (1, FRAMES),
         f"the second press left {made._cache.span()}")
    return "a range already held is not read a second time"


@check(GROUP, "Play begins where the playhead is, however it was warmed")
def play_starts_at_the_playhead():
    """The complaint this answers: warm the cache, scrub somewhere, press
    Play, and it jumped back to the start of the warm run instead of going
    on from where the hand was.

    This is where the playhead belongs -- in the player. Making the *warming*
    follow it as well was the wrong place for it, and cost time.
    """
    made = _open_the_sequence()
    made._forget_cache()
    world.pump()
    _warm(made)
    want(made._cache.span() is not None, "nothing was held to play")

    seen = []
    for asked in (FRAMES - 1, 2, FRAMES // 2):
        made.timeline.set_value(asked)
        world.pump()
        made._toggle_play()
        want(made._playing, f"Play did not start at frame {asked}")
        # The real timer is stopped before the pump, or it gets a tick of its
        # own in and the check measures the wall clock rather than the rule.
        made._play_timer.stop()
        want(made._play_from == asked,
             f"Play was set to begin at {made._play_from}, not {asked}")
        made._play_tick()
        world.pump()
        seen.append((asked, made.timeline.value()))
        made._toggle_play()
        world.pump()
        want(not made._playing, "Play did not stop again")
    wrong = [(asked, got) for asked, got in seen if got != asked]
    want(not wrong, f"Play began somewhere else: asked/got {wrong}")
    return "three presses at three frames, each began where it was asked to"


@check(GROUP, "Play does not fill memory behind the operator's back")
def play_leaves_the_warming_alone():
    """Pressing Play used to push the warm run further out every time, which
    is the other half of why the second press never began where the first
    one had."""
    made = _open_the_sequence()
    made._forget_cache()
    world.pump()
    _warm(made)
    before = made._cache.span()
    made.timeline.set_value(FRAMES - 1)
    world.pump()
    made._toggle_play()
    world.pump()
    want(not made._warming(), "Play started a warming job of its own")
    made._toggle_play()
    world.pump()
    want(made._cache.span() == before,
         f"the warm run went from {before} to {made._cache.span()} on Play")
    return f"the run stays {before[0]} to {before[1]} across a play and a stop"


@check(GROUP, "Play with nothing held starts the warming rather than nothing")
def play_is_never_a_dead_button():
    made = _open_the_sequence()
    made._forget_cache()
    world.pump()
    made._toggle_play()
    world.pump()
    warming = made._warming() or made._cache.span() is not None
    if made._warm_job is not None:
        made._warm_job.wait(5000)
    made._stop_play()
    world.pump()
    want(warming, "Play on an empty cache did nothing at all")
    return "an empty cache and Play starts the same job the button does"


# -- single images and the format filter ----------------------------------

def _write_png(path: Path, colour=(120, 180, 240, 255), size=(320, 240)) -> None:
    from PySide6.QtGui import QImage
    width, height = size
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[...] = colour
    picture = QImage(bytes(pixels.tobytes()), width, height, width * 4,
                     QImage.Format.Format_RGBA8888)
    picture.save(str(path))


def _mixed_folder() -> Path:
    """A run, two lone stills of two formats, and two movies of two containers,
    sitting in one folder -- the thing a real render output folder is full of.

    The movies are empty files: scanning lists them by extension without
    opening them, which is all the format filter needs to sort them.
    """
    folder = world.scratch() / "mixed"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*"):
        old.unlink()
    _write_png(folder / "run_0001.png", (200, 60, 60, 255))
    _write_png(folder / "run_0002.png", (60, 200, 60, 255))
    _write_png(folder / "poster.png", (60, 60, 200, 255))
    _write_png(folder / "logo.bmp", (200, 200, 60, 255))
    (folder / "clip.mp4").write_bytes(b"")
    (folder / "movie.mov").write_bytes(b"")
    return folder


def _open_the_mixed_folder():
    made = world.window()
    made.source_edit.setText(constants.display(_mixed_folder()))
    made.format_filter.setCurrentIndex(0)      # start from All formats
    made._rescan()
    world.pump()
    return made


@check(GROUP, "single images show up in the list, not only runs")
def stills_are_listed():
    """A folder of one-off frames used to read as empty: a lone file was not a
    sequence, so the table ignored it and the still could only be dragged on.
    """
    made = _open_the_mixed_folder()
    names = [made.seq_table.item(row, 0).text()
             for row in range(made.seq_table.rowCount())]
    want("poster.png" in names, f"the still is not in the list: {names}")
    want("logo.bmp" in names, f"the .bmp still is not in the list: {names}")
    want(any(name.startswith("run") for name in names),
         f"the run went missing when the stills came in: {names}")

    row = names.index("poster.png")
    made.seq_table.clearSelection()
    made.seq_table.selectRow(row)
    world.pump()
    want(made.current is not None and made.current.kind == "still",
         "picking a lone image did not load it as a still")
    want(made.current.count == 1,
         f"the still says it has {made.current.count} frames")
    return f"{len(names)} sources listed, stills and a run among them"


@check(GROUP, "the format filter narrows the list to one format")
def the_format_filter_narrows():
    made = _open_the_mixed_folder()
    everything = made.seq_table.rowCount()
    want(everything >= 3, f"only {everything} sources before filtering")

    index = made.format_filter.findData(".bmp")
    want(index > 0, "the .bmp format never reached the filter menu")
    made.format_filter.setCurrentIndex(index)
    world.pump()
    shown = [made.seq_table.item(row, 0).text()
             for row in range(made.seq_table.rowCount())]
    want(shown == ["logo.bmp"],
         f"the .bmp filter left {shown}, not just the one .bmp still")

    made.format_filter.setCurrentIndex(0)      # back to All formats
    world.pump()
    want(made.seq_table.rowCount() == everything,
         "clearing the filter did not bring the other sources back")
    return f"{everything} sources, one .bmp, filtered down to it and back"


@check(GROUP, "the Video umbrella filters to the movies whatever the container")
def the_video_filter_gathers_movies():
    """mp4, mov, mkv and the rest are one family; picking Video should leave
    every movie and no stills, without naming each container."""
    made = _open_the_mixed_folder()
    items = [made.format_filter.itemText(i)
             for i in range(made.format_filter.count())]
    want("Video" in items, f"there is no Video umbrella in the filter: {items}")
    want("Images" in items, f"there is no Images umbrella in the filter: {items}")
    want("MP4" in items and "MOV" in items,
         f"the specific movie formats are missing: {items}")

    made.format_filter.setCurrentIndex(made.format_filter.findData("video"))
    world.pump()
    shown = sorted(made.seq_table.item(row, 0).text()
                   for row in range(made.seq_table.rowCount()))
    want(shown == ["clip.mp4", "movie.mov"],
         f"the Video filter left {shown}, not just the two movies")

    made.format_filter.setCurrentIndex(made.format_filter.findData("image"))
    world.pump()
    kinds = {made._shown[row].kind for row in range(len(made._shown))}
    want(kinds and "movie" not in kinds,
         f"the Images filter still shows non-image sources: {kinds}")

    made.format_filter.setCurrentIndex(0)      # back to All formats
    world.pump()
    return f"Video → {shown}, Images → {sorted(kinds)}"


# -- where the working folder lands ---------------------------------------

PATHS = "the working folder"


@check(PATHS, "a translocated or read-only app writes to a real folder, not a temp copy")
def translocation_is_stepped_around():
    """The macOS fault this fixes: a quarantined app is launched from a random
    read-only copy under /private/var/folders (Gatekeeper's translocation), and
    anchoring ToRemap/OUT/Logs beside the executable then lands the whole
    project in that temporary folder. It must fall back to a real, writable,
    per-user place instead."""
    from pathlib import Path
    real = constants.app_dir
    translocated = Path("/private/var/folders/rz/abcd1234/T/AppTranslocation/"
                        "0FEEDFACE/d/MatreshkaRemapRenderer.app").parent
    try:
        constants.app_dir = lambda: translocated
        chosen = constants._find_project_dir()
    finally:
        constants.app_dir = real
    want(not constants._looks_translocated(chosen),
         f"the working folder is still the read-only temp copy: {chosen}")
    want(constants._is_writable_dir(chosen),
         f"the fallback folder {chosen} cannot be written to")
    return f"translocated launch → {chosen}"


@check(PATHS, "the folder the app actually resolved is writable and permanent")
def the_resolved_folder_is_writable():
    want(not constants._looks_translocated(constants.PROJECT_DIR),
         f"the project folder resolved to a temp copy: {constants.PROJECT_DIR}")
    want(constants._is_writable_dir(constants.PROJECT_DIR),
         f"the project folder {constants.PROJECT_DIR} is not writable")
    # The two writes that used to sit beside the executable now share it.
    import depends
    import logfile
    want(str(constants.PROJECT_DIR) in str(depends.tools_dir()),
         f"ffmpeg would still download beside the app: {depends.tools_dir()}")
    want(str(constants.PROJECT_DIR) in str(logfile.folder()),
         f"the log would still be written beside the app: {logfile.folder()}")
    return str(constants.PROJECT_DIR)
