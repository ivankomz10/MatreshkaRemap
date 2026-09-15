"""Decoded source frames, kept in memory so a range can be played smoothly.

What is kept is the **source**, before the warp, and that is the whole point.
The decode is the slow half -- the full pipeline manages 48 frames a second at
Full while the warp alone manages 135 -- so holding the source means a change
of framing costs one warp per frame and nothing else. Move a handle in the
middle of playback and it keeps playing.

The cost is memory: a 3328 x 2496 source frame is 33 MB as RGBA, more than the
29 MB of a finished Full frame. Where the engine wants planes rather than
pixels, the planes are kept instead and that is a third of the bytes.

Nothing here decides what to warm or when. It holds frames, says how many it
can hold, and forgets them when the thing they came from changes.
"""
from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass

import numpy as np

# How much of what is free the cache may take. Half leaves the machine room to
# keep working -- the renderer itself wants memory, and so does everything
# else the user has open.
SHARE = 0.5
FLOOR = 512 * 1024 * 1024          # never bother with less than this
CEILING = 32 * 1024 * 1024 * 1024  # nor promise more than anyone needs


def free_memory() -> int:
    """Bytes of physical memory going spare, as well as can be told.

    Windows answers exactly. Elsewhere there is no portable answer, so a
    quarter of the total stands in for it -- wrong, but wrong in the safe
    direction, and the ceiling is a comfort rather than a contract.
    """
    if sys.platform == "win32":
        class Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullAvailPhys)
        return FLOOR
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return int(total * 0.25)
    except (ValueError, OSError, AttributeError):
        return FLOOR


def budget() -> int:
    """What the cache may spend, asked afresh every time warming starts."""
    return int(min(max(free_memory() * SHARE, FLOOR), CEILING))


@dataclass
class Held:
    """One frame, in whichever shape the engine asked for it.

    `pixels` for RGBA, `planes` for the three 4:2:0 planes with their strides.
    Exactly one of the two is set.
    """

    pixels: np.ndarray | None = None
    planes: list | None = None

    @property
    def bytes(self) -> int:
        if self.pixels is not None:
            return int(self.pixels.nbytes)
        return sum(int(plane[0].nbytes) for plane in (self.planes or []))


class FrameCache:
    """Frames of one source, by frame number, up to a budget.

    Keyed by an identity string rather than by path alone: what was decoded
    depends on how the alpha is being read, and a frame decoded one way is not
    the frame decoded the other.
    """

    def __init__(self) -> None:
        self.identity = ""
        self._held: dict[int, Held] = {}
        self._spent = 0
        self._budget = 0

    # -- what is in it -----------------------------------------------------

    def matches(self, identity: str) -> bool:
        return bool(identity) and identity == self.identity

    def begin(self, identity: str) -> None:
        """Start again for a new source, and take a fresh look at memory."""
        if identity != self.identity:
            self.clear()
            self.identity = identity
        self._budget = budget()

    def clear(self) -> None:
        self._held.clear()
        self._spent = 0

    def __contains__(self, frame: int) -> bool:
        return frame in self._held

    def get(self, frame: int) -> Held | None:
        return self._held.get(frame)

    def put(self, frame: int, held: Held) -> bool:
        """Keep a frame. False when there is no room, which ends the warming."""
        if frame in self._held:
            return True
        cost = held.bytes
        if self._spent + cost > self._budget:
            return False
        self._held[frame] = held
        self._spent += cost
        return True

    # -- what to say about it ----------------------------------------------

    @property
    def spent(self) -> int:
        return self._spent

    @property
    def allowed(self) -> int:
        return self._budget

    @property
    def count(self) -> int:
        return len(self._held)

    def span(self) -> tuple[int, int] | None:
        """The run of frames held from the lowest one, with no gaps.

        A run rather than the whole set, because that is what can be played and
        what the timeline draws. Warming fills forward from the first frame of
        the range, so in practice the two are the same thing.
        """
        if not self._held:
            return None
        first = min(self._held)
        last = first
        while last + 1 in self._held:
            last += 1
        return first, last

    def holds(self, first: int, last: int) -> bool:
        return all(frame in self._held for frame in range(first, last + 1))
