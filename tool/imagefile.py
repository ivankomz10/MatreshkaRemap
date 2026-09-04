"""Reading image files without pulling OpenCV into the build.

Qt is already here for the window and its image plugins cover everything the
sources use, so this saves about 40 MB in the packaged application -- a poor
trade for one function call.

Named imagefile rather than imageio to stay clear of the package of that name.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtGui import QImage, QImageReader


def read_rgba(path: Path) -> np.ndarray:
    """An image file as a contiguous HxWx4 uint8 array."""
    image = QImage(str(path))
    if image.isNull():
        raise RuntimeError(f"could not read {Path(path).name}")
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)

    height, width, stride = image.height(), image.width(), image.bytesPerLine()
    # constBits() is a window into Qt's own buffer and does not keep the QImage
    # alive, so anything still viewing it when the image goes is a crash
    # waiting for the interpreter to shut down. Take one honest copy into
    # Python-owned memory and work from that.
    raw = bytes(image.constBits())
    rows = np.frombuffer(raw, dtype=np.uint8)[: stride * height].reshape(height, stride)
    # Qt pads rows to a 4-byte boundary; step over the padding.
    return np.ascontiguousarray(rows[:, : width * 4].reshape(height, width, 4))


def read_size(path: Path) -> tuple[int, int]:
    """Dimensions from the header, without decoding the pixels."""
    size = QImageReader(str(path)).size()
    return (size.width(), size.height()) if size.isValid() else (0, 0)
