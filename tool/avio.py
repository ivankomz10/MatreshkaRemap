"""Decoding and encoding in this process, through PyAV.

Frames used to travel to and from two ffmpeg processes as raw bytes over pipes.
At 4608x1584 that was 18 MB a frame through the kernel in each direction, and
it was the pipeline's limit -- not the decoding, not the GPU. PyAV is the same
libav code, called directly, so the frames never leave the process and the
planes can go straight to the graphics card.

It also means ffmpeg no longer has to be installed: the wheels carry it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np

# Everything is forced through this so one conversion in the shader is correct
# for a full-range JPEG sequence and a limited-range H.264 movie alike. It is
# byte-for-byte what the old ffmpeg command line produced.
SCALE_OPTIONS = "out_range=pc:out_color_matrix=bt601"


def probe(path: Path) -> tuple[int, int, int, float]:
    """Frames, width, height and rate, from the container rather than by decoding.

    The rate matters as much as the count: a sixty a second movie holds twice
    the frames of a thirty a second one for the same length of time, and
    writing them out one for one is how a clip ends up twice as long as it was.
    """
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        width, height = stream.codec_context.width, stream.codec_context.height
        rate = float(stream.average_rate or stream.guessed_rate or 0)
        frames = stream.frames
        if not frames and stream.duration and stream.average_rate:
            frames = int(round(float(stream.duration * stream.time_base
                                     * stream.average_rate)))
        if not frames and container.duration and stream.average_rate:
            frames = int(round(container.duration / av.time_base
                               * float(stream.average_rate)))
    if not frames:
        raise RuntimeError(f"could not work out how long {Path(path).name} is")
    return int(frames), int(width), int(height), rate


@dataclass
class Plane:
    """One image plane as the graphics card wants it: bytes plus a row stride."""

    data: memoryview
    stride: int
    width: int
    height: int


class Reader:
    """Frames out of an image sequence or a movie, as YUV planes.

    The planes point straight into libav's buffers, so the frame that owns them
    is handed over too and must be kept until the upload is done.
    """

    def __init__(self, path: str, is_sequence: bool, start: int, count: int,
                 first: int = 0, keep_alpha: bool = False) -> None:
        self.count = count
        # 4:2:0 has nowhere to put an alpha channel, so a frame whose own
        # transparency matters has to come out whole. Three times the bytes,
        # which is why it is asked for rather than assumed.
        self.keep_alpha = keep_alpha
        options = {}
        if is_sequence:
            options["start_number"] = str(start)
            self.container = av.open(path, format="image2", options=options)
            self.skip = 0
        else:
            self.container = av.open(path)
            self.skip = max(0, start - first)

        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"      # libav decodes on several cores

        self.graph = av.filter.Graph()
        source = self.graph.add_buffer(template=self.stream)
        scale = self.graph.add("scale", SCALE_OPTIONS)
        fmt = self.graph.add("format", "rgba" if keep_alpha else "yuv420p")
        sink = self.graph.add("buffersink")
        source.link_to(scale)
        scale.link_to(fmt)
        fmt.link_to(sink)
        self.graph.configure()

    def frames(self):
        """Yield (owner, planes) until `count` frames have come out."""
        produced = 0
        skipped = 0
        for decoded in self.container.decode(video=0):
            if skipped < self.skip:
                skipped += 1
                continue

            self.graph.push(decoded)
            frame = self.graph.pull()

            if self.keep_alpha:
                # One interleaved plane; the caller reads it as an array.
                yield frame, None
                produced += 1
                if produced >= self.count:
                    break
                continue

            half_w = (frame.width + 1) // 2
            half_h = (frame.height + 1) // 2
            sizes = ((frame.width, frame.height), (half_w, half_h), (half_w, half_h))
            planes = [
                Plane(memoryview(plane), plane.line_size, width, height)
                for plane, (width, height) in zip(frame.planes, sizes)
            ]
            yield frame, planes

            produced += 1
            if produced >= self.count:
                break

    def close(self) -> None:
        try:
            self.container.close()
        except Exception:  # noqa: BLE001 -- closing must not raise
            pass


class Writer:
    """Encodes finished frames straight from memory."""

    def __init__(self, path: Path, width: int, height: int, fps: int,
                 kind: str = "h264", crf: int = 18, preset: str = "medium",
                 alpha: bool = False, png_depth: int = 8,
                 prores_profile: str = "3") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.kind = kind
        self.width, self.height = width, height
        self.takes_yuv = kind == "h264" and not alpha

        if kind == "png":
            self.container = av.open(str(path), "w", format="image2")
            codec, pix_fmt = "png", "rgba" if alpha else "rgb24"
            if png_depth == 16:
                pix_fmt = "rgba64be" if alpha else "rgb48be"
        elif kind == "prores":
            self.container = av.open(str(path), "w")
            codec = "prores_ks"
            pix_fmt = "yuva444p10le" if alpha else "yuv422p10le"
        else:
            self.container = av.open(str(path), "w")
            codec, pix_fmt = "libx264", "yuv420p"

        self.stream = self.container.add_stream(codec, rate=fps)
        self.stream.width, self.stream.height = width, height
        self.stream.pix_fmt = pix_fmt

        if kind == "h264":
            self.stream.options = {"crf": str(crf), "preset": preset}
            # The GPU already packed limited-range BT.709, so nothing converts.
            self.stream.codec_context.color_range = 1        # tv
            self.stream.codec_context.colorspace = 1         # bt709
        elif kind == "prores":
            self.stream.options = {"profile": "4" if alpha else prores_profile}

    def write(self, data: bytes) -> None:
        """One finished frame: packed YUV planes, or RGBA when alpha is kept."""
        if self.takes_yuv:
            array = np.frombuffer(data, dtype=np.uint8).reshape(
                self.height * 3 // 2, self.width)
            frame = av.VideoFrame.from_ndarray(array, format="yuv420p")
            frame.color_range = 1
            frame.colorspace = 1
        else:
            array = np.frombuffer(data, dtype=np.uint8).reshape(
                self.height, self.width, 4)
            frame = av.VideoFrame.from_ndarray(array, format="rgba")

        for packet in self.stream.encode(frame):
            self.container.mux(packet)

    def close(self) -> None:
        try:
            for packet in self.stream.encode():
                self.container.mux(packet)
        except Exception:  # noqa: BLE001 -- a cancelled render has no tail
            pass
        try:
            self.container.close()
        except Exception:  # noqa: BLE001
            pass


def read_single_frame(path: str, is_sequence: bool, number: int, first: int = 0,
                      keep_alpha: bool = False):
    """One frame, for the preview. `planes` is None when alpha is kept."""
    reader = Reader(path, is_sequence, number, 1, first, keep_alpha)
    try:
        for owner, planes in reader.frames():
            return owner, planes
    finally:
        pass
    raise RuntimeError(f"could not read frame {number}")
