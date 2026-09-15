"""The window as a whole: what can be read, what takes a drop, what keys do.

These are the checks for the faults that were never about arithmetic -- a tab
bar drawing dark on dark, icons the same colour as the panel behind them, a
file landing on the wrong target, a column of controls too wide for its column.
None of them would trip a unit test and all of them made the program unusable.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QPushButton, QWidget

import icons
import world
from harness import Failure, check, want

GROUP = "the window"

# Small text on a coloured ground. The web's threshold for body text, and the
# one the unreadable tab bar failed by a mile.
READABLE = 4.5


def _icon_names() -> list[str]:
    folder = Path(icons.__file__).resolve().parent / icons.FOLDER
    return sorted(path.stem for path in folder.glob("*.png"))


def _dominant(pixels: np.ndarray):
    """The colour most of these pixels are, which is the background."""
    flat = pixels[..., :3].reshape(-1, 3)
    keys, counts = np.unique(flat, axis=0, return_counts=True)
    return keys[int(np.argmax(counts))]


@check(GROUP, "the window builds and finds its geometry")
def window_builds():
    made = world.window()
    want(made.view.gizmo.map is not None,
         "the window came up without the baked geometry")
    want(made.tabs.count() == 2, f"{made.tabs.count()} tabs, not two")
    names = [made.tabs.tabText(index) for index in range(made.tabs.count())]
    want(names == ["Source", "Transform"], f"the tabs read {names}")
    return f"tabs {', '.join(names)}; preview map {made.view.gizmo.map.width} wide"


@check(GROUP, "the tab bar can be read, chosen and not")
def tabs_are_readable():
    """A dark stylesheet with a light palette drew dark text on dark tabs.

    Measured off the pixels rather than off the rules, because it was the two
    disagreeing that caused it -- reading either one alone would have said the
    window was fine.
    """
    made = world.window()
    bar = made.tabs.tabBar()
    notes = []
    for index in range(made.tabs.count()):
        made.tabs.setCurrentIndex(index)
        world.pump()
        pixels = world.painted(bar)
        ground = _dominant(pixels)
        from PySide6.QtGui import QColor
        back = QColor(int(ground[0]), int(ground[1]), int(ground[2]))
        best = 1.0
        for row in pixels.reshape(-1, 4):
            ratio = world.contrast(
                QColor(int(row[0]), int(row[1]), int(row[2])), back)
            best = max(best, ratio)
        if best < READABLE:
            raise Failure(f"tab {made.tabs.tabText(index)!r} has nothing on it "
                          f"with more than {best:.1f}:1 against its own ground")
        notes.append(f"{made.tabs.tabText(index)} {best:.1f}:1")
    made.tabs.setCurrentIndex(0)
    return "contrast " + ", ".join(notes)


@check(GROUP, "every icon draws, and draws in a colour that shows")
def icons_are_visible():
    """They were cached at build time in the palette of the moment, and the
    app set a dark stylesheet without a dark palette -- so the whole set came
    out in ink the colour of the panel it sat on."""
    made = world.window()
    button = QPushButton()
    button.setPalette(made.palette())
    ink = icons.ink(button)
    ground = made.palette().color(QPalette.ColorRole.Window)
    ratio = world.contrast(ink, ground)
    if ratio < READABLE:
        raise Failure(f"icons are drawn at {ratio:.1f}:1 against the window")

    names = _icon_names()
    want(names, "no icons were found beside the application at all")
    empty = []
    for name in names:
        icon = icons.get(name, 18, ink)
        if icon.isNull():
            empty.append(name)
            continue
        picture = icon.pixmap(18, 18).toImage()
        raw = np.frombuffer(picture.constBits().tobytes(), dtype=np.uint8)
        if raw.reshape(-1, 4)[:, 3].max() == 0:
            empty.append(name)
    want(not empty, f"these icons came out blank: {empty}")
    return f"{len(names)} icons, drawn at {ratio:.1f}:1 against the window"


@check(GROUP, "the icons the window asks for are the icons that exist")
def icons_are_all_there():
    """A name with no file behind it is a button with no picture on it, and
    the only sign is a line on the console nobody reads."""
    source = (Path(icons.__file__).resolve().parent / "main.py").read_text("utf-8")
    panel = (Path(icons.__file__).resolve().parent
             / "transform_ui.py").read_text("utf-8")
    asked = set()
    for text in (source, panel):
        for call in ("icons.put(", "icons.get(", "icons.mark("):
            start = 0
            while True:
                start = text.find(call, start)
                if start < 0:
                    break
                piece = text[start + len(call): start + len(call) + 200]
                for quote in ('"', "'"):
                    first = piece.find(quote)
                    if first < 0:
                        continue
                    second = piece.find(quote, first + 1)
                    if second > first and "\n" not in piece[:first]:
                        asked.add(piece[first + 1: second])
                    break
                start += len(call)
    have = set(_icon_names())
    missing = sorted(name for name in asked if name and name not in have
                     and " " not in name and name.isidentifier())
    want(not missing, f"asked for icons that are not there: {missing}")
    return f"{len(asked & have)} names asked for, all present"


@check(GROUP, "a drop on the preview is content, a drop on Frame is the border")
def drops_go_where_they_are_aimed():
    """They used to be the same target, so a border landed on the wall."""
    made = world.window()
    border = world.flat_png("border.png", (255, 255, 255, 90))
    content = world.holed_png("content.png")

    rows = [child for child in made.findChildren(QWidget)
            if type(child).__name__ == "_DropRow"]
    want(rows, "the Frame row is not a drop target at all")
    row = rows[0]

    before = made._frame_path
    world.drop_file(row, border)
    world.pump()
    want(made._frame_path is not None and made._frame_path.name == border.name,
         f"the border did not reach the Frame row (it holds {made._frame_path})")

    heard = []
    made.view.fileDropped.connect(lambda path: heard.append(path))
    world.drop_file(made.view, content)
    world.pump()
    want(heard and Path(heard[-1]).name == content.name,
         "a drop on the preview did not reach the content")
    want(made._frame_path.name == border.name,
         "a drop on the preview changed the border as well")

    made._load_frame(before)
    world.pump()
    return "border to the Frame row, content to the preview, neither to both"


@check(GROUP, "a line edit inside the Frame row does not eat the drop")
def frame_row_children_keep_out():
    """A QLineEdit accepts drops of its own and would take the file first."""
    made = world.window()
    rows = [child for child in made.findChildren(QWidget)
            if type(child).__name__ == "_DropRow"]
    want(rows, "no Frame row")
    greedy = [type(child).__name__ for child in rows[0].findChildren(QWidget)
              if child.acceptDrops()]
    want(not greedy, f"these children still take drops: {greedy}")
    return f"{len(rows[0].findChildren(QWidget))} children, none of them greedy"


@check(GROUP, "the preview answers the keys it advertises")
def hotkeys_work():
    """The legend in the corner is drawn from the same table the key handler
    reads, so a key that works and a key that is advertised cannot drift."""
    made = world.window()
    view = made.view
    view.setFocus()
    nudges, steps = [], []
    view.nudged.connect(lambda across, down: nudges.append((across, down)))
    view.stepped.connect(lambda by: steps.append(by))

    for code, wanted in ((Qt.Key.Key_Left, (-1, 0)), (Qt.Key.Key_Right, (1, 0)),
                         (Qt.Key.Key_Up, (0, -1)), (Qt.Key.Key_Down, (0, 1))):
        nudges.clear()
        world.key(view, code)
        want(nudges == [wanted], f"{code} gave {nudges}, wanted {[wanted]}")
    nudges.clear()
    world.key(view, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    want(nudges == [(10, 0)], f"shift and right gave {nudges}")

    for code, wanted in ((Qt.Key.Key_Comma, -1), (Qt.Key.Key_Period, 1)):
        steps.clear()
        world.key(view, code)
        want(steps == [wanted], f"{code} stepped {steps}, wanted {wanted}")

    advertised = {name for name, _ in view.KEYS}
    for name in ("arrows", "shift + arrows", ", ."):
        want(name in advertised, f"the legend does not mention {name!r}")
    return f"{len(view.KEYS)} lines in the legend, arrows and steps all answer"


@check(GROUP, "the left column fits in the column it is given")
def the_interface_fits():
    """Buttons that do not fit are buttons whose words are cut in half, which
    is what the row of icons did before the tabs were sized to their contents.
    """
    made = world.window()
    made.resize(1320, 880)
    world.pump()
    room = made.tabs.width()
    want(room > 100, f"the left column came out {room} px wide")
    too_wide = []
    for index in range(made.tabs.count()):
        page = made.tabs.widget(index)
        for child in page.findChildren(QWidget):
            if not child.isVisible():
                continue
            needs = child.sizeHint().width()
            if needs > room + 40 and child.parent() is not None:
                too_wide.append(f"{type(child).__name__} wants {needs}")
    want(not too_wide, f"in a {room} px column: {too_wide[:4]}")
    return f"{room} px of column, nothing in it asks for more"


@check(GROUP, "the two preview modes are the two that exist")
def modes_are_flat_and_camera():
    """Source mode was taken out; the camera view is what aiming is done in."""
    made = world.window()
    names = [made.view.modes.itemText(index)
             for index in range(made.view.modes.count())]
    want(names == ["Flat", "Viewer"], f"the modes read {names}")
    return ", ".join(names)


@check(GROUP, "the resolution buttons say the size the table really is")
def resolutions_match_the_tables():
    """The buttons carry the pixel size beside the name, and it is typed in
    rather than read off the file -- so a rebake at another size leaves the
    window telling everyone the old one."""
    import constants
    made = world.window()
    labels = [button.text() for button in made.resolution_group.buttons()]
    for name, _ in constants.RESOLUTION_PRESETS:
        said = [text for text in labels if text.startswith(name)]
        want(said, f"no button for {name}; the buttons read {labels}")
        height, width = world.table(name).coverage.shape
        want(str(width) in said[0] and str(height) in said[0],
             f"{said[0]!r} but the {name} table is {width} x {height}")
    return "; ".join(labels)


@check(GROUP, "the cache button sits at the left end of the transport")
def cache_button_is_left_of_the_transport():
    """Filling memory is its own act, so it has its own button, and it is
    where the eye starts rather than buried among the playback keys."""
    made = world.window()
    want(getattr(made, "cache_button", None) is not None,
         "there is no cache button")
    row = made.cache_button.parentWidget()
    mine = made.cache_button.mapTo(row, made.cache_button.rect().topLeft()).x()
    others = []
    for name in ("play_button", "loop_button"):
        other = getattr(made, name, None)
        if other is not None and other.isVisible():
            others.append((name, other.mapTo(row, other.rect().topLeft()).x()))
    want(others, "none of the transport buttons could be found")
    behind = [name for name, x in others if x <= mine]
    want(not behind, f"the cache button is to the right of {behind}")
    want(made.cache_button.icon().isNull() is False,
         "the cache button has no icon on it")
    return (f"at x={mine}, ahead of " +
            ", ".join(f"{name} at {x}" for name, x in others))
