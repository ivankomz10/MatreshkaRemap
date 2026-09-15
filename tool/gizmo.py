"""Aiming content while looking at the wall rather than at the clip.

The viewport shows a frame that has already been through the warp, so the
clip's rectangle is not a rectangle there -- the wall is lamellas, and a box in
the window arrives as a row of disjoint pieces. What can still be drawn is
where the corners went, and what can still be read is which point of the window
sits under the cursor. Both come from the baked table, which already says, for
every pixel of the finished frame, where in the window it read from.

The other direction -- window to frame -- the table does not give, so it is
scattered into a coarse grid once per table and looked up from there. Coarse is
enough: it positions handles, and the handle is a place to grab, not a
measurement. The measurement is in the panel.
"""
from __future__ import annotations

import math

import numpy as np

import transform as xf

CELLS = 384                  # across the window, for the scattered inverse
HANDLE = 8.0                 # the side of a grab square, in screen pixels
GRAB = 12.0                  # how close the cursor has to be to catch one
TURN_ARM = 38.0              # how far past the box the rotation handle sits

# Where the pivot likes to land. Fractions of the source frame, and the reason
# a pivot ends up exactly on a corner instead of near one.
SNAPS = [(x, y) for y in (0.0, 0.5, 1.0) for x in (0.0, 0.5, 1.0)]
SNAP_PULL = 12.0             # screen pixels


class WindowMap:
    """Both directions between the finished frame and the window it read from."""

    def __init__(self, table, viewer=None) -> None:
        window_x = table.u.astype(np.float32)
        window_y = (1.0 - table.v).astype(np.float32)
        live = table.coverage > 0.0

        if viewer is not None:
            # The viewer's frame is a second gather, out of the flat frame this
            # table makes -- so where a viewer pixel read from the window is
            # wherever the flat pixel it sampled read from.
            height, width = window_x.shape
            column = np.clip((viewer.u * width - 0.5).round().astype(np.int32),
                             0, width - 1)
            row = np.clip(((1.0 - viewer.v) * height - 0.5).round().astype(np.int32),
                          0, height - 1)
            window_x = window_x[row, column]
            window_y = window_y[row, column]
            live = live[row, column] & (viewer.coverage > 0.0)

        self.window_x = window_x
        self.window_y = window_y
        self.live = live
        self.height, self.width = live.shape
        self._grid = None
        self._drift = None

    @property
    def drift(self) -> tuple[float, float, float, float]:
        """The window's slope across the whole frame, as a last resort.

        Least squares over every covered pixel: the one straight line that
        best explains where the window went. Right for the viewer, which is
        very nearly a plain picture of the content; only roughly right for the
        flat frame, where the wall turns back on itself and the true slope
        changes sign partway across. Used where a local reading cannot be had.
        """
        if self._drift is None:
            rows, columns = np.nonzero(self.live)
            if rows.size < 64:
                self._drift = (1.0 / self.width, 0.0, 0.0, 1.0 / self.height)
                return self._drift
            if rows.size > 200000:
                pick = np.linspace(0, rows.size - 1, 200000).astype(np.int64)
                rows, columns = rows[pick], columns[pick]
            basis = np.stack([columns.astype(np.float64),
                              rows.astype(np.float64),
                              np.ones(rows.size)], axis=1)
            across, _, _, _ = np.linalg.lstsq(
                basis, self.window_x[rows, columns].astype(np.float64), rcond=None)
            down, _, _, _ = np.linalg.lstsq(
                basis, self.window_y[rows, columns].astype(np.float64), rcond=None)
            self._drift = (float(across[0]), float(across[1]),
                           float(down[0]), float(down[1]))
        return self._drift

    def jacobian(self, x: float, y: float, reach: int = 24):
        """How the window moves for one pixel across and one pixel down, here.

        Four numbers rather than two, and signed: on this wall the answer is
        different in different places and in one stretch it is the other way
        round entirely. A single figure for the whole frame is what made a drag
        in the left third of the wall push the picture right when the hand went
        left.

        Read as the middle value of the differences taken across a patch over
        a baseline of several pixels rather than between one pixel and the
        next. Both halves of that matter. The baseline is because the table is
        a staircase in places -- two pixels of the viewer often read the same
        pixel of the flat frame, so half the neighbouring differences are
        exactly zero and the middle of them is a slope of nothing. The middle
        value is because of the seams: a fold in the wall puts a step of a
        third of the window into a handful of the differences, and a mean
        would carry it while the middle value never sees it.
        """
        column, row = int(x), int(y)
        for span, stride in ((reach * 2, 16), (reach * 6, 32), (reach * 18, 64)):
            lo_x, hi_x = max(0, column - span), min(self.width, column + span + 1)
            lo_y, hi_y = max(0, row - span), min(self.height, row + span + 1)
            if hi_x - lo_x <= stride or hi_y - lo_y <= stride:
                continue
            wx = self.window_x[lo_y:hi_y, lo_x:hi_x]
            wy = self.window_y[lo_y:hi_y, lo_x:hi_x]
            live = self.live[lo_y:hi_y, lo_x:hi_x]
            side = live[:, :-stride] & live[:, stride:]
            over = live[:-stride, :] & live[stride:, :]
            if side.sum() < 12 or over.sum() < 12:
                continue
            found = (
                float(np.median((wx[:, stride:] - wx[:, :-stride])[side])) / stride,
                float(np.median((wx[stride:, :] - wx[:-stride, :])[over])) / stride,
                float(np.median((wy[:, stride:] - wy[:, :-stride])[side])) / stride,
                float(np.median((wy[stride:, :] - wy[:-stride, :])[over])) / stride,
            )
            # A patch that lands square on a fold can read flat along one axis,
            # and a drag with a zero in it is a drag that does nothing.
            if abs(found[0]) < 1e-7 or abs(found[3]) < 1e-7:
                continue
            return found
        return self.drift

    # -- frame to window ---------------------------------------------------

    def window_at(self, x: float, y: float) -> tuple[float, float] | None:
        """Which point of the window this pixel of the frame came from."""
        column, row = int(x), int(y)
        if not (0 <= column < self.width and 0 <= row < self.height):
            return None
        if not self.live[row, column]:
            return None
        return float(self.window_x[row, column]), float(self.window_y[row, column])

    def window_near(self, x: float, y: float, reach: int = 12):
        """The same, but looking a little way around for a covered pixel.

        Grabbing exactly on the gap between two lamellas is easy to do and
        should not mean nothing happens.
        """
        found = self.window_at(x, y)
        if found is not None:
            return found
        column, row = int(x), int(y)
        for radius in range(1, reach + 1):
            for dy in (-radius, radius):
                for dx in range(-radius, radius + 1):
                    found = self.window_at(column + dx, row + dy)
                    if found is not None:
                        return found
            for dx in (-radius, radius):
                for dy in range(-radius + 1, radius):
                    found = self.window_at(column + dx, row + dy)
                    if found is not None:
                        return found
        return None

    # -- window to frame ---------------------------------------------------

    def _scatter(self) -> np.ndarray:
        """Every covered pixel dropped into the cell of the window it reads.

        Built on a quarter of the pixels in each direction: this places
        handles, and a handle a pixel out is a handle you still grab.
        """
        if self._grid is not None:
            return self._grid
        step = max(1, min(self.width, self.height) // 400)
        rows = np.arange(0, self.height, step)
        columns = np.arange(0, self.width, step)
        grid_y, grid_x = np.meshgrid(rows, columns, indexing="ij")
        wx = self.window_x[::step, ::step]
        wy = self.window_y[::step, ::step]
        keep = self.live[::step, ::step]

        cell_x = np.clip((wx * CELLS).astype(np.int32), 0, CELLS - 1)[keep]
        cell_y = np.clip((wy * CELLS).astype(np.int32), 0, CELLS - 1)[keep]
        grid = np.full((CELLS, CELLS, 2), -1.0, dtype=np.float32)
        grid[cell_y, cell_x, 0] = grid_x[keep]
        grid[cell_y, cell_x, 1] = grid_y[keep]
        self._grid = grid
        return grid

    def frame_at(self, wx: float, wy: float) -> tuple[float, float] | None:
        """Where in the frame a point of the window ended up.

        Rings outward when the exact cell is empty, because a point of the
        window can easily land between two lamellas and belong to neither.
        """
        grid = self._scatter()
        cell_x = int(np.clip(wx * CELLS, 0, CELLS - 1))
        cell_y = int(np.clip(wy * CELLS, 0, CELLS - 1))
        for radius in range(0, 24):
            lo_y, hi_y = max(0, cell_y - radius), min(CELLS, cell_y + radius + 1)
            lo_x, hi_x = max(0, cell_x - radius), min(CELLS, cell_x + radius + 1)
            patch = grid[lo_y:hi_y, lo_x:hi_x]
            found = patch[..., 0] >= 0.0
            if not found.any():
                continue
            # The nearest of whatever this ring turned up, not just the first.
            ys, xs = np.nonzero(found)
            best = np.argmin((ys + lo_y - cell_y) ** 2 + (xs + lo_x - cell_x) ** 2)
            spot = patch[ys[best], xs[best]]
            return float(spot[0]), float(spot[1])
        return None


class Gizmo:
    """The box, its handles, and what dragging one of them means.

    Kept apart from the widget that draws it: the same handles are wanted over
    the flat frame and over the view from the camera, and those are two
    different pictures of one transform.
    """

    def __init__(self) -> None:
        self.placement = xf.Transform()
        self.map: WindowMap | None = None
        self.visible = True
        self._grab: str | None = None
        self._start: xf.Transform | None = None
        self._from = (0.0, 0.0)          # the pointer, in frame pixels
        self._anchor: tuple[float, float] | None = None   # the handle, in window
        self._slope = (1.0, 0.0, 0.0, 1.0)                # window per frame pixel
        self._pivot_frame: tuple[float, float] | None = None
        self.snapped: tuple[float, float] | None = None

    # -- where the handles are, in frame pixels ----------------------------

    def _corners(self) -> list[tuple[float, float]]:
        left, top, right, bottom = self.placement.kept
        return [(left, top), (right, top), (right, bottom), (left, bottom)]

    def _spot(self, source_x: float, source_y: float):
        if self.map is None:
            return None
        return self.map.frame_at(*self.placement.forward(source_x, source_y))

    def handles(self) -> dict[str, tuple[float, float]]:
        """Every grabbable point, in frame pixels, skipping any that missed."""
        if self.map is None or not self.visible:
            return {}
        corners = self._corners()
        spots: dict[str, tuple[float, float]] = {}
        for index, corner in enumerate(corners):
            found = self._spot(*corner)
            if found:
                spots[f"corner{index}"] = found
        for index in range(4):
            first, second = corners[index], corners[(index + 1) % 4]
            found = self._spot((first[0] + second[0]) / 2.0,
                               (first[1] + second[1]) / 2.0)
            if found:
                spots[f"edge{index}"] = found
        found = self._spot(self.placement.pivot_x, self.placement.pivot_y)
        if found:
            spots["pivot"] = found
        return spots

    def outline(self, steps: int = 22) -> list[list[tuple[float, float]]]:
        """The clip's border, walked and dropped into the frame.

        A run breaks wherever the wall does, so this comes back as several
        strokes rather than one closed shape -- which is honest, because that
        is what the border of a picture on this screen actually looks like.
        """
        if self.map is None or not self.visible:
            return []
        corners = self._corners()
        runs: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []
        for index in range(4):
            first, second = corners[index], corners[(index + 1) % 4]
            for step in range(steps + 1):
                share = step / steps
                point = self._spot(first[0] + (second[0] - first[0]) * share,
                                   first[1] + (second[1] - first[1]) * share)
                if point is None:
                    if len(current) > 1:
                        runs.append(current)
                    current = []
                    continue
                if current and math.dist(current[-1], point) > 160.0:
                    runs.append(current)
                    current = [point]
                else:
                    current.append(point)
        if len(current) > 1:
            runs.append(current)
        return runs

    # -- dragging ----------------------------------------------------------

    def grab(self, frame_x: float, frame_y: float, near: float) -> str | None:
        """Take hold of whatever handle is within `near` pixels of this point.

        Everything the drag will need is settled here: which handle, where in
        the window that handle presently is, and how much of the window one
        pixel of the preview is worth just there. Nothing is read out of the
        map again until the button comes up, which is what stops a drag
        sticking, reversing, or throwing the picture across the wall.
        """
        self._start = self.placement.copy()
        self._from = (frame_x, frame_y)
        self._anchor = None
        self._pivot_frame = None
        self.snapped = None
        if self.map is None:
            self._grab = None
            return None

        spots = self.handles()
        self._pivot_frame = spots.get("pivot")
        best, distance = None, near
        for name, spot in spots.items():
            # The pivot answers to the ring that is drawn for it and not to the
            # wider reach the corners get. It sits in the middle of the picture
            # by default, and a wide reach there means every attempt to slide
            # the clip takes hold of the pivot instead -- and moving the pivot
            # leaves the picture exactly where it was, so the drag reads as
            # nothing happening at all.
            reach = math.dist(spot, (frame_x, frame_y))
            allowed = min(distance, near * HANDLE / GRAB) if name == "pivot" else distance
            if reach <= allowed:
                best, distance = name, reach
        if best is None:
            # A plain read under the cursor, which the map answers exactly --
            # it is differentiating the map that is treacherous, not sampling
            # it. Asked once, to decide whether the click landed on the clip.
            window = self.map.window_near(frame_x, frame_y)
            if window is not None:
                source = self.placement.inverse(*window)
                left, top, right, bottom = self.placement.kept
                if left <= source[0] <= right and top <= source[1] <= bottom:
                    best = "move"

        self._grab = best
        if best is None:
            return None
        # Read the slope where the thing being dragged is, not where the
        # pointer happens to be: on the flat frame those can be two sides of a
        # fold, and then the handle sets off in the opposite direction.
        at = spots.get(best, (frame_x, frame_y))
        self._slope = self.map.jacobian(*at)
        if best.startswith("corner"):
            self._anchor = self.placement.forward(*self._corners()[int(best[-1])])
        elif best.startswith("edge"):
            index = int(best[-1])
            first, second = self._corners()[index], self._corners()[(index + 1) % 4]
            self._anchor = self.placement.forward((first[0] + second[0]) / 2.0,
                                                  (first[1] + second[1]) / 2.0)
        elif best == "pivot":
            self._anchor = self.placement.forward(self.placement.pivot_x,
                                                  self.placement.pivot_y)
        return best

    def travelled(self, frame_x: float, frame_y: float) -> tuple[float, float]:
        """How far the pointer has come, in the window rather than in pixels."""
        across, side, rise, down = self._slope
        dx = frame_x - self._from[0]
        dy = frame_y - self._from[1]
        return (across * dx + side * dy, rise * dx + down * dy)

    def _reached(self, frame_x: float, frame_y: float):
        """Where the held handle should now be, in the window.

        Where it was, plus how far the hand has moved -- so the handle keeps
        whatever distance it had from the cursor when it was taken hold of,
        rather than jumping under it.
        """
        if self._anchor is None:
            return None
        moved = self.travelled(frame_x, frame_y)
        return (self._anchor[0] + moved[0], self._anchor[1] + moved[1])

    def holding(self) -> bool:
        return self._grab is not None

    def release(self) -> None:
        self._grab = None
        self._start = None
        self._anchor = None
        self._pivot_frame = None
        self.snapped = None

    def drag(self, frame_x: float, frame_y: float, shift: bool, ctrl: bool,
             scale_pull: float = 1.0) -> bool:
        """Move whatever is held. False when the point is off the wall."""
        if self._grab is None or self._start is None or self.map is None:
            return False
        start, placement = self._start, self.placement

        if self._grab == "move":
            moved = self.travelled(frame_x, frame_y)
            placement.x = start.x + moved[0]
            placement.y = start.y + moved[1]
            return True

        target = self._reached(frame_x, frame_y)
        if target is None:
            return False

        if self._grab == "pivot":
            source = start.inverse(*target)
            if not ctrl:
                source = self._snap(source, scale_pull)
            # The pivot moves; the picture does not. Which means working out
            # where the picture would go with the new pivot and then taking
            # that back out of the position -- and doing it against the pose
            # the drag started from.
            #
            # It used to be measured against the pose the last mouse move left
            # behind, which is the same thing only for the first move of a
            # drag. Every one after that took its correction from a position
            # that already carried the previous correction, so a real drag --
            # which is thirty moves, not one -- walked the picture across the
            # wall while the pivot was being placed.
            anchor = start.forward(0.5, 0.5)
            placement.pivot_x, placement.pivot_y = source
            placement.x, placement.y = start.x, start.y
            moved = placement.forward(0.5, 0.5)
            placement.x = start.x + (anchor[0] - moved[0])
            placement.y = start.y + (anchor[1] - moved[1])
            return True

        index = int(self._grab[-1])
        if self._grab.startswith("corner"):
            corner = self._corners()[index]
            free_x = free_y = True
        else:
            first, second = self._corners()[index], self._corners()[(index + 1) % 4]
            corner = ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)
            free_x = abs(first[0] - second[0]) < 1e-9
            free_y = not free_x

        scale_x, scale_y = self._reaching(start, corner, target)
        if free_x and scale_x is not None:
            placement.scale_x = scale_x
        if free_y and scale_y is not None:
            placement.scale_y = scale_y
        if shift and free_x and free_y and start.scale_x and start.scale_y:
            grown = math.sqrt(abs(placement.scale_x / start.scale_x
                                  * placement.scale_y / start.scale_y))
            placement.scale_x = start.scale_x * grown
            placement.scale_y = start.scale_y * grown
        return True

    def turn(self, frame_x: float, frame_y: float, ctrl: bool) -> bool:
        """Rotate about the pivot, from wherever the drag started.

        The handle is a screen-space affordance -- a fixed arm above the pivot,
        wherever the warp has put it -- so the angle is taken from where the
        hand is relative to the pivot in the preview, and carried into the
        window by the same slope everything else uses.
        """
        if self._start is None or self.map is None or self._pivot_frame is None:
            return False
        centre = self._start.forward(self._start.pivot_x, self._start.pivot_y)
        across, side, rise, down = self._slope
        pivot_x, pivot_y = self._pivot_frame

        def carried(x: float, y: float) -> tuple[float, float]:
            dx, dy = x - pivot_x, y - pivot_y
            return (centre[0] + across * dx + side * dy,
                    centre[1] + rise * dx + down * dy)

        was = carried(*self._from)
        now = carried(frame_x, frame_y)
        if math.dist(now, centre) < 1e-9 or math.dist(was, centre) < 1e-9:
            return False

        def bearing(point):
            return math.degrees(math.atan2(-(point[1] - centre[1]),
                                           (point[0] - centre[0]) / xf.SOURCE_ASPECT))

        self.placement.angle = self._start.angle - (bearing(now) - bearing(was))
        if ctrl:
            self.placement.angle = round(self.placement.angle / 15.0) * 15.0
        return True

    def _snap(self, source: tuple[float, float], pull: float):
        """Pull the pivot onto a corner, an edge or the middle.

        The reach is a distance in the preview, given in frame pixels, so it
        is the same span under the hand however far the view is zoomed in and
        whichever of the two pictures is being aimed in. Measuring it in
        pixels of the clip instead made the snap grab from half a screen away
        on a small clip and be unreachable on a large one.
        """
        start = self._start or self.placement
        across, _, _, down = self._slope
        here = start.forward(*source)
        best, distance = None, pull
        for spot in SNAPS:
            there = start.forward(*spot)
            reach = math.hypot((there[0] - here[0]) / max(abs(across), 1e-9),
                               (there[1] - here[1]) / max(abs(down), 1e-9))
            if reach <= distance:
                best, distance = spot, reach
        self.snapped = best
        return best if best is not None else source

    @staticmethod
    def _reaching(start: xf.Transform, corner: tuple[float, float],
                  target: tuple[float, float]):
        """The scale that puts `corner` of the clip on `target` of the window.

        Solved rather than nudged, so a long drag does not leave the handle
        trailing behind the pointer.
        """
        fx = 1.0 - corner[0] if start.flip_h else corner[0]
        fy = 1.0 - corner[1] if start.flip_v else corner[1]
        arm_x = (fx - start.pivot_x) * xf.SOURCE_ASPECT
        arm_y = fy - start.pivot_y
        radians = math.radians(start.angle)
        cos, sin = math.cos(radians), math.sin(radians)
        dx = (target[0] - start.pivot_x - start.x) * xf.SOURCE_ASPECT
        dy = target[1] - start.pivot_y - start.y
        return (((dx * cos + dy * sin) / arm_x) if abs(arm_x) > 1e-6 else None,
                ((dy * cos - dx * sin) / arm_y) if abs(arm_y) > 1e-6 else None)
