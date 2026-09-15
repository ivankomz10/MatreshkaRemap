"""Take the icons this window needs out of Houdini's set, once.

Rendered to a single-colour mask rather than copied: Houdini's are drawn for a
dark interface and this window follows the system theme, so what is kept is the
shape, and the colour is decided when it is drawn.
"""
import sys, zipfile
from pathlib import Path

sys.argv = ["x"]
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import QByteArray, QRectF

HOUDINI = Path(r"C:\Program Files\Side Effects Software\Houdini 20.5.584"
               r"\houdini\config\Icons\icons.zip")
OUT = Path(__file__).resolve().parent / "icons"

WANTED = {
    # COP2 is Houdini's own two-dimensional compositor, so its icons are drawn
    # flat. The three-dimensional handles from BUTTONS say the same words but
    # turn to noise at sixteen pixels.
    "position":   "COP2/shift.svg",
    "scale":      "COP2/scale.svg",
    "rotation":   "COP2/rotoshape.svg",
    "pivot":      "COP2/pin.svg",
    "flip_h":     "BUTTONS/flip_horizontal.svg",
    "flip_v":     "BUTTONS/flip_vertical.svg",
    "crop":       "COP2/crop.svg",
    "link_on":    "BUTTONS/lock_closed.svg",
    "link_off":   "BUTTONS/lock_open.svg",
    "link":       "BUTTONS/link.svg",
    "reset":      "BUTTONS/undo.svg",
    "fit":        "IMAGE/fit_to_window.svg",
    "fill":       "COP2/expand.svg",
    "stretch":    "IMAGE/view_aspectratio.svg",
    "edge":       "COP2/border.svg",
    "snapshot":   "IMAGE/snapshots.svg",
    "flipbook":   "TOOLS/flipbook.svg",
    "memory":     "COMMON/memory.svg",
    "fullscreen": "VOLTMATERIAL/fullscreen_sharp.svg",
    "transform":  "BUTTONS/tools_outline.svg",
    "clear":      "BUTTONS/delete.svg",
    "browse":     "BUTTONS/folder.svg",
    "play":       "PLAYBAR/play_forward.svg",
    "pause":      "PLAYBAR/pause.svg",
    "first":      "PLAYBAR/frame_first.svg",
    "back":       "PLAYBAR/frame_prev.svg",
    "forward":    "PLAYBAR/frame_next.svg",
    "last":       "PLAYBAR/last_frame.svg",
    "loop":       "PLAYBAR/play_loop.svg",
    "once":       "PLAYBAR/play_once.svg",
}
SIZE = 40                       # generous, so it stays sharp on a hidpi screen

app = QApplication([])
OUT.mkdir(exist_ok=True)
archive = zipfile.ZipFile(HOUDINI)
inside = set(archive.namelist())

for name, entry in WANTED.items():
    if entry not in inside:
        print(f"  {name:11s} MISSING {entry}")
        continue
    renderer = QSvgRenderer(QByteArray(archive.read(entry)))
    drawn = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    drawn.fill(0)
    painter = QPainter(drawn)
    renderer.render(painter, QRectF(2, 2, SIZE - 4, SIZE - 4))
    painter.end()

    # Keep the shape, drop the colour. Houdini draws most of these light on
    # nothing, so brightness is part of the shading and belongs in the mask --
    # but a few are drawn dark, and weighting those by brightness erases them.
    # Which it is, is measured rather than assumed.
    lit = [max(drawn.pixelColor(x, y).getRgb()[:3])
           for y in range(SIZE) for x in range(SIZE)
           if drawn.pixelColor(x, y).alpha() > 8]
    shaded = bool(lit) and sum(lit) / len(lit) > 90

    mask = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    mask.fill(0)
    for y in range(SIZE):
        for x in range(SIZE):
            colour = drawn.pixelColor(x, y)
            if colour.alpha() == 0:
                continue
            weight = max(colour.getRgb()[:3]) / 255.0 if shaded else 1.0
            mask.setPixelColor(x, y, QColor(0, 0, 0, int(colour.alpha() * weight)))
    # Normalise: a set where one icon is drawn at half opacity and the next at
    # full reads as a mistake, not as emphasis.
    peak = max((mask.pixelColor(x, y).alpha()
                for y in range(SIZE) for x in range(SIZE)), default=0)
    if 0 < peak < 255:
        gain = 255.0 / peak
        for y in range(SIZE):
            for x in range(SIZE):
                alpha = mask.pixelColor(x, y).alpha()
                if alpha:
                    mask.setPixelColor(x, y, QColor(0, 0, 0, min(255, int(alpha * gain))))
    mask.save(str(OUT / f"{name}.png"))
    print(f"  {name:11s} <- {entry:34s} {'shaded' if shaded else 'solid'}")
print("into", OUT)
