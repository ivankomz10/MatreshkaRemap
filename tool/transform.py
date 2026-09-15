"""Where the content sits inside the picture the wall was mapped from.

The baked tables read a picture 0..1 across and 0..1 down, and that picture was
authored at 2000 x 2360. Hand the renderer anything else and it lands stretched,
because nothing in the pipeline ever knew what shape the material was meant to
be. This is the one place that knows.

Identity is exactly what the renderer did before there was a transform: the
whole source frame across the whole window. So anything rendered without
touching this reproduces bit for bit, and `Fit` is what corrects the stretch.

Order, decided once and written down rather than rediscovered from the maths:
crop cuts the source frame, flips mirror it, scale and rotation turn about the
pivot, position moves last. Crop does not rescale what is left -- it cuts, the
way an inspector's crop does, and the rest stays where it was.

Values are kept as fractions of the source frame, so swapping a 2000 x 2360
clip for a 3328 x 2496 one keeps the framing looking the same. The window shows
them as pixels, which is what anyone actually thinks in.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

# The shape of the picture the tables were baked against. Anything angular --
# rotation, or a scale meant to look uniform -- has to happen in a space where
# a circle is round, and this is the number that makes it one.
SOURCE_ASPECT = 2000.0 / 2360.0          # 0.847458

EDGE_MODES = [
    ("Transparent", 0),
    ("Black", 1),
    ("Repeat edge", 2),
]


class History:
    """What each source's framing has been, so a step can be taken back.

    Per source, because undoing on one clip should never disturb another --
    and in memory only. Reversing a week later something nobody remembers
    doing is not a thing anyone wants; reversing the drag just made is.

    A step is a settled state, not a movement. A drag that crosses two hundred
    pixels is one entry, pushed when the handle is let go, or the whole history
    would be a hundred indistinguishable nudges deep.
    """

    DEPTH = 100

    def __init__(self) -> None:
        self._past: dict[str, list[dict]] = {}
        self._future: dict[str, list[dict]] = {}

    def remember(self, key: str, placement: "Transform") -> None:
        """Keep where this source was, before it is moved somewhere else."""
        if not key:
            return
        past = self._past.setdefault(key, [])
        state = placement.to_dict()
        if past and past[-1] == state:
            return                      # nothing actually changed
        past.append(state)
        del past[:-self.DEPTH]
        self._future.pop(key, None)     # a new move ends the old future

    def undo(self, key: str, now: "Transform"):
        past = self._past.get(key) or []
        if not past:
            return None
        self._future.setdefault(key, []).append(now.to_dict())
        return Transform.from_dict(past.pop())

    def redo(self, key: str, now: "Transform"):
        future = self._future.get(key) or []
        if not future:
            return None
        self._past.setdefault(key, []).append(now.to_dict())
        return Transform.from_dict(future.pop())

    def depth(self, key: str) -> tuple[int, int]:
        return len(self._past.get(key) or []), len(self._future.get(key) or [])


@dataclass
class Transform:
    """Everything the editor can set, in fractions of the source frame."""

    x: float = 0.0                # position, fractions of the window
    y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    angle: float = 0.0            # degrees, anticlockwise
    pivot_x: float = 0.5          # fractions of the source frame
    pivot_y: float = 0.5
    flip_h: bool = False
    flip_v: bool = False
    crop_left: float = 0.0        # fractions cut off each side
    crop_right: float = 0.0
    crop_top: float = 0.0
    crop_bottom: float = 0.0
    edge: int = 0                 # index into EDGE_MODES

    @property
    def is_identity(self) -> bool:
        """Whether this leaves the picture exactly as the renderer always had it.

        Worth asking rather than inferring: at identity the whole transform
        path is skipped, so anything rendered without opening the editor is the
        same bytes it was before the editor existed.
        """
        return (self.x == 0.0 and self.y == 0.0
                and self.scale_x == 1.0 and self.scale_y == 1.0
                and self.angle == 0.0
                and not self.flip_h and not self.flip_v
                and self.crop_left == 0.0 and self.crop_right == 0.0
                and self.crop_top == 0.0 and self.crop_bottom == 0.0)

    @property
    def is_default(self) -> bool:
        """Whether there is anything here worth writing down.

        Not the same question as `is_identity`, and confusing the two is what
        made a dragged pivot spring back: moving the pivot on its own leaves
        the mapping exactly as it was, so the state looked like nothing and was
        thrown away between one frame and the next.
        """
        return asdict(self) == asdict(Transform())

    def copy(self) -> "Transform":
        return Transform(**asdict(self))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Transform":
        if not data:
            return cls()
        fields = cls().to_dict()
        return cls(**{name: data[name] for name in fields if name in data})

    @property
    def kept(self) -> tuple[float, float, float, float]:
        """What survives the crop: left, top, right, bottom in source 0..1."""
        left = min(self.crop_left, 0.999)
        top = min(self.crop_top, 0.999)
        right = max(1.0 - self.crop_right, left + 0.001)
        bottom = max(1.0 - self.crop_bottom, top + 0.001)
        return left, top, right, bottom

    @property
    def crop_centre(self) -> tuple[float, float]:
        left, top, right, bottom = self.kept
        return (left + right) / 2.0, (top + bottom) / 2.0

    def forward(self, x: float, y: float) -> tuple[float, float]:
        """A point of the source frame, as a point of the window.

        This is the direction a person thinks in -- where does this corner of
        my clip end up -- and the editor draws with it. The shader needs the
        other direction and derives it from the same numbers.
        """
        fx = 1.0 - x if self.flip_h else x
        fy = 1.0 - y if self.flip_v else y
        ax = (fx - self.pivot_x) * SOURCE_ASPECT * self.scale_x
        ay = (fy - self.pivot_y) * self.scale_y
        radians = math.radians(self.angle)
        cos, sin = math.cos(radians), math.sin(radians)
        return ((ax * cos - ay * sin) / SOURCE_ASPECT + self.pivot_x + self.x,
                (ax * sin + ay * cos) + self.pivot_y + self.y)

    def inverse(self, x: float, y: float) -> tuple[float, float]:
        """A point of the window, as a point of the source frame."""
        ax = (x - self.pivot_x - self.x) * SOURCE_ASPECT
        ay = y - self.pivot_y - self.y
        radians = math.radians(self.angle)
        cos, sin = math.cos(radians), math.sin(radians)
        rx = (ax * cos + ay * sin) / max(abs(self.scale_x), 1e-6)
        ry = (-ax * sin + ay * cos) / max(abs(self.scale_y), 1e-6)
        fx = rx / SOURCE_ASPECT + self.pivot_x
        fy = ry + self.pivot_y
        return (1.0 - fx if self.flip_h else fx,
                1.0 - fy if self.flip_v else fy)

    def uniforms(self) -> list[float]:
        """The sixteen numbers the shader reads, in the order it reads them."""
        radians = math.radians(self.angle)
        left, top, right, bottom = self.kept
        return [
            math.cos(radians), math.sin(radians),
            1.0 / max(abs(self.scale_x), 1e-6), 1.0 / max(abs(self.scale_y), 1e-6),
            self.pivot_x, self.pivot_y, self.x, self.y,
            left, top, right, bottom,
            1.0 if self.flip_h else 0.0, 1.0 if self.flip_v else 0.0, 0.0, 0.0,
        ]

    def fitted(self, width: int, height: int, cover: bool) -> "Transform":
        """Scale set so the clip is undistorted: inside the window, or over it.

        The stretch this corrects is the identity's doing and not a fault --
        the renderer has always laid the whole frame across the whole window.
        A clip of another shape needs a number in the scale field, and this
        works it out rather than making anyone measure.

        A clip scaled by (sx, sy) covers `SOURCE_ASPECT * sx` by `sy` of true
        proportion, so looking right means `sx / sy` is the clip's own aspect
        over the window's. Fit takes the largest such pair that stays inside
        the window, Fill the smallest that covers it.
        """
        made = self.copy()
        if width <= 0 or height <= 0:
            return made
        ratio = (width / height) / SOURCE_ASPECT
        wide = ratio > 1.0
        if cover:
            made.scale_x, made.scale_y = (ratio, 1.0) if wide else (1.0, 1.0 / ratio)
        else:
            made.scale_x, made.scale_y = (1.0, 1.0 / ratio) if wide else (ratio, 1.0)
        return made
