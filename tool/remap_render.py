"""The render pipeline that replaced Blender: ffmpeg -> warp -> ffmpeg.

Two ffmpeg processes, one decoding and one encoding, with the warp between
them. Doing the same work in-process through PyAV was measured and came out
slower -- 38 fps against 59 -- because two processes genuinely run on separate
cores while everything inside one interpreter takes turns holding the GIL.
The pipes cost real copies, and they are still the cheaper option.

PyAV is used for previews and for reading a movie's length, where there is
nothing to run in parallel and starting a process would dominate.
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import constants
import depends
import remap_engine

# Whichever ffmpeg the machine has: one downloaded beside the application, or
# one already on PATH. Resolved once, and again if one is fetched at runtime.
FFMPEG = depends.ffmpeg_command()

# Without this every ffmpeg would flash a console window over the interface.
# The flag only exists on Windows; elsewhere there is nothing to hide.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_ENCODERS: set[str] | None = None
_PASSTHROUGH: list[str] | None = None
_ACCEPTED: dict[tuple, bool] = {}


def refresh_ffmpeg() -> str:
    """Look again -- a copy may have arrived since this module was imported."""
    global FFMPEG, _ENCODERS, _PASSTHROUGH
    FFMPEG = depends.ffmpeg_command()
    _ENCODERS = None
    _PASSTHROUGH = None
    _ACCEPTED.clear()
    return FFMPEG


def passthrough_arguments() -> list[str]:
    """Tell ffmpeg to leave the frame rate alone, in the dialect it speaks.

    `-vsync 0` was removed in ffmpeg 8 and `-fps_mode` did not exist before
    5.1, so which of the two is right depends on the build in front of us and
    not on anything decidable here. Asked once, by trying it.
    """
    global _PASSTHROUGH
    if _PASSTHROUGH is None:
        modern = ["-fps_mode", "passthrough"]
        try:
            attempt = subprocess.run(
                [FFMPEG, "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "nullsrc"] + modern
                + ["-frames:v", "1", "-f", "null", "-"],
                capture_output=True, timeout=20, creationflags=NO_WINDOW)
            _PASSTHROUGH = modern if attempt.returncode == 0 else ["-vsync", "0"]
        except Exception:  # noqa: BLE001 -- the old spelling is the safer guess
            _PASSTHROUGH = ["-vsync", "0"]
    return _PASSTHROUGH


def available_encoders() -> set[str]:
    """Which encoders this ffmpeg was built with, asked once."""
    global _ENCODERS
    if _ENCODERS is None:
        try:
            listing = subprocess.run(
                [FFMPEG, "-hide_banner", "-encoders"],
                capture_output=True, text=True, timeout=20,
                creationflags=NO_WINDOW).stdout
            _ENCODERS = {
                line.split()[1] for line in listing.splitlines()
                if line.startswith(" V") and len(line.split()) > 1
            }
        except Exception:  # noqa: BLE001 -- absence is the answer
            _ENCODERS = set()
    return _ENCODERS


def encoder_takes(arguments: list[str], width: int, height: int) -> bool:
    """Whether this encoder will really take a frame of this size.

    Asked rather than assumed. A hardware encoder refuses for reasons written
    down nowhere we can read: a width past a ceiling, a quality knob the
    platform never implemented, a session another application is holding. One
    black frame through a null output answers all of them at once, and the
    answer is about this machine rather than about a table someone wrote after
    trying two of them.
    """
    if width <= 0 or height <= 0:
        return False

    key = (tuple(arguments), width, height)
    known = _ACCEPTED.get(key)
    if known is None:
        try:
            attempt = subprocess.run(
                [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", f"color=black:s={width}x{height}:r=30", "-frames:v", "1"]
                + arguments + ["-f", "null", "-"],
                capture_output=True, timeout=60, creationflags=NO_WINDOW)
            known = attempt.returncode == 0
        except Exception:  # noqa: BLE001 -- an unanswered question is a no
            known = False
        _ACCEPTED[key] = known
    return known


_HW_DECODE: bool | None = None


def hardware_decode_ok() -> bool:
    """Whether this machine will decode on its own media engine.

    Only asked on macOS, where the answer is VideoToolbox. Without an output
    format the frames come back to ordinary memory by themselves, so the filter
    chain downstream neither knows nor cares -- what changes is that the
    decoding stops costing processor cores the encoder wants.
    """
    global _HW_DECODE
    if _HW_DECODE is None:
        if sys.platform != "darwin":
            _HW_DECODE = False
        else:
            try:
                attempt = subprocess.run(
                    [FFMPEG, "-hide_banner", "-loglevel", "error",
                     "-hwaccel", "videotoolbox", "-f", "lavfi",
                     "-i", "color=black:s=320x240:r=30", "-frames:v", "1",
                     "-f", "null", "-"],
                    capture_output=True, timeout=60, creationflags=NO_WINDOW)
                _HW_DECODE = attempt.returncode == 0
            except Exception:  # noqa: BLE001 -- an unanswered question is a no
                _HW_DECODE = False
    return _HW_DECODE


def hardware_encoder_for(kind: str, width: int, height: int = 0,
                         probe=None) -> str | None:
    """The card's own encoder for this job, if there is one that will take it."""
    encoders = available_encoders()
    candidates = {
        "hevc": ("hevc_nvenc", "hevc_videotoolbox"),
        "h264": ("h264_nvenc", "h264_videotoolbox"),
        # Apple Silicon has a ProRes engine from the M1 Pro onwards. Where it
        # is missing the probe below says so and prores_ks takes the job.
        "prores": ("prores_videotoolbox",),
    }.get(kind, ())

    for name in candidates:
        if name not in encoders:
            continue
        if probe is not None and not probe(name):
            continue
        return name
    return None


# Kept for older callers.
nvenc_for = hardware_encoder_for


@dataclass
class Output:
    """How the finished frames are written."""

    kind: str = "h264"          # h264 | hevc | png | prores
    supersample: bool = False   # four taps per output pixel instead of one
    path: Path = Path("out.mp4")
    fps: int = 30
    alpha: bool = False
    crf: int = 18
    preset: str = "medium"
    encoder: str = "auto"       # auto | medium | veryfast
    width: int = 0              # of the finished frame, for asking the encoder
    height: int = 0
    png_depth: int = 8
    prores_profile: str = "3"   # 3 = 422 HQ, 4 = 4444

    @property
    def wants_alpha(self) -> bool:
        return self.alpha and self.kind in ("png", "prores")

    @property
    def chosen_encoder(self) -> str:
        """What will actually encode this, once the machine has been asked."""
        if self.kind not in ("h264", "hevc", "prores"):
            return self.kind
        if self.encoder == "auto":
            hardware = hardware_encoder_for(
                self.kind, self.width, self.height,
                probe=lambda name: encoder_takes(
                    self.codec_arguments(name), self.width, self.height))
            if hardware:
                return hardware
        if self.kind == "prores":
            return "prores_ks"
        return "libx265" if self.kind == "hevc" else "libx264"

    def arguments(self) -> list[str]:
        """The codec flags and where the result goes."""
        return self.codec_arguments(self.chosen_encoder) + [str(self.path)]

    def codec_arguments(self, encoder: str) -> list[str]:
        """Everything about the encoding, and nothing about the destination."""
        if self.kind in ("h264", "hevc"):
            if encoder.endswith("_nvenc"):
                return ["-c:v", encoder, "-preset", constants.NVENC_PRESET,
                        "-rc", "vbr", "-cq", str(constants.NVENC_CQ),
                        "-pix_fmt", "yuv420p"]
            if encoder.endswith("_videotoolbox"):
                # VideoToolbox takes a 0-100 quality instead of a quantiser.
                # Deliberately not -allow_sw: its software encoder is slower
                # than libx264, so a hardware refusal should fail where it can
                # be seen rather than quietly become a render nobody can wait
                # out.
                return ["-c:v", encoder, "-q:v", str(constants.VIDEOTOOLBOX_QUALITY),
                        "-pix_fmt", "yuv420p"]
            preset = self.preset if self.encoder == "auto" else self.encoder
            return ["-c:v", encoder, "-crf", str(self.crf), "-preset", preset,
                    "-pix_fmt", "yuv420p"]
        if self.kind == "png":
            fmt = "rgba" if self.wants_alpha else "rgb24"
            if self.png_depth == 16:
                fmt = "rgba64be" if self.wants_alpha else "rgb48be"
            return ["-c:v", "png", "-pix_fmt", fmt]
        if self.kind == "prores":
            profile = "4" if self.wants_alpha else self.prores_profile
            fmt = "yuva444p10le" if self.wants_alpha else "yuv422p10le"
            if encoder.endswith("_videotoolbox"):
                # The engine names its profiles rather than numbering them, and
                # only 4444 carries alpha. If this machine's build disagrees the
                # probe fails and prores_ks takes the job instead.
                named = {"4": "4444", "5": "4444xq"}.get(profile, "hq")
                return ["-c:v", encoder, "-profile:v", named, "-pix_fmt", fmt]
            return ["-c:v", "prores_ks", "-profile:v", profile,
                    "-pix_fmt", fmt]
        raise ValueError(f"unknown output kind {self.kind!r}")


@dataclass
class Source:
    """Where the frames come from: a numbered sequence or a single movie."""

    path: str                    # printf pattern for a sequence, a file for a movie
    is_sequence: bool
    first: int = 0               # first file number / movie frame
    width: int = 0
    height: int = 0
    fps: float = 0.0             # how fast the material is meant to run
    file_fps: float = 0.0        # what the container itself claimed, if anything
    alpha_mode: str = "auto"     # auto | premultiplied | straight | ignore

    def seconds(self, frames: int) -> float:
        """How long that many source frames last."""
        return frames / self.fps if self.fps > 0 else 0.0

    def decode_arguments(self, start: int, count: int, yuv: bool = True,
                         out_fps: float = 0.0) -> list[str]:
        """ffmpeg command that streams the wanted frames as raw planes.

        4:2:0 is a third of the bytes of RGBA, and moving those bytes was the
        pipeline's limit -- not the decoding and not the GPU. The range and
        matrix are forced so one conversion in the shader is right for both a
        JPEG sequence (full range) and an H.264 movie (limited).

        `count` is how many frames to *produce*. When the material runs at one
        rate and the file is written at another, that is not the same as how
        many are read: sixty a second going out at thirty is two read for each
        one written. The `fps` filter does the choosing, and it chooses by
        time, which is the only thing the two rates have in common.
        """
        # -nostats because the progress line goes to stderr and different
        # builds disagree about whether -loglevel silences it. Leaving it on
        # means a pipe filling up with something nobody wants to read.
        args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostats", "-nostdin"]
        # A numbered sequence of stills has no engine path worth taking; a movie
        # does, and on Apple Silicon it is the difference between the processor
        # decoding and the processor being free for everything else.
        if not self.is_sequence and hardware_decode_ok():
            args += ["-hwaccel", "videotoolbox"]
        filters = []
        if self.is_sequence:
            # An image sequence carries no rate of its own; ffmpeg assumes 25
            # and would resample against a number nobody chose.
            if self.fps > 0:
                args += ["-framerate", _rate(self.fps)]
            args += ["-start_number", str(start), "-i", self.path]
        else:
            # Only when the file has been contradicted. Forcing a rate on the
            # input makes it constant, which on a variable-rate file drops or
            # repeats frames on its own -- not something to do to a movie whose
            # own timestamps were believed.
            if self.fps > 0 and abs(self.fps - self.file_fps) > 1e-6:
                args += ["-r", _rate(self.fps)]
            # Movies are seekable by frame number rather than by file name.
            args += ["-i", self.path]
            filters.append(f"select=gte(n\\,{start - self.first})")

        if yuv:
            filters.append("scale=out_range=pc:out_color_matrix=bt601")
        # Only when the two disagree: at equal rates the filter has nothing to
        # do and every frame should reach the other end untouched.
        if out_fps > 0 and self.fps > 0 and abs(out_fps - self.fps) > 1e-6:
            filters.append("fps=" + _rate(out_fps))
        if filters:
            args += ["-vf", ",".join(filters)]
        if not self.is_sequence:
            args += passthrough_arguments()

        args += ["-frames:v", str(count), "-f", "rawvideo",
                 "-pix_fmt", "yuv420p" if yuv else "rgba", "-"]
        return args


def _rate(fps: float) -> str:
    """A rate ffmpeg will read back exactly, whole or not."""
    return str(int(round(fps))) if abs(fps - round(fps)) < 1e-6 else f"{fps:.6f}"


def frames_out(frames_in: int, in_fps: float, out_fps: float) -> int:
    """How many frames that much material becomes at the written rate.

    By time, not by count: the length of the clip is the thing the two rates
    have to agree about, and it is the thing that was going wrong.
    """
    if in_fps <= 0 or out_fps <= 0 or abs(in_fps - out_fps) < 1e-6:
        return max(1, frames_in)
    return max(1, int(round(frames_in / in_fps * out_fps)))


# How much of a frame has to disagree before the guess flips. One pixel is
# codec noise; a soft edge is thousands.
_STRAIGHT_SHARE = 0.001
_STRAIGHT_MARGIN = 3            # of 255, to sit above ProRes' rounding


def read_alpha_convention(source: Source, frame: int) -> str:
    """Decode one frame and ask how its transparency was written.

    Premultiplied colour cannot exceed its own alpha -- that is the whole of
    the test, and it is the one every compositor uses. It answers "opaque"
    when there is nothing transparent to judge, which is most footage and
    means the question does not arise.
    """
    try:
        raw = subprocess.run(source.decode_arguments(frame, 1, yuv=False),
                             capture_output=True, creationflags=NO_WINDOW).stdout
        pixels = np.frombuffer(raw, dtype=np.uint8)[
            :source.width * source.height * 4].reshape(-1, 4)
    except Exception:                       # noqa: BLE001 -- a guess, not a step
        return "opaque"
    if pixels.size == 0:
        return "opaque"
    return alpha_convention_of(pixels)


def alpha_convention_of(pixels) -> str:
    """The same judgement on RGBA already in memory."""
    pixels = np.asarray(pixels).reshape(-1, 4)
    alpha = pixels[:, 3]
    if alpha.min() >= 250:
        return "opaque"
    partial = (alpha > 4) & (alpha < 251)
    if not partial.any():
        # A hard matte: nothing is half transparent, so the two conventions
        # agree everywhere and either answer renders the same picture.
        return "premultiplied"
    over = (pixels[partial, :3].astype(np.int16)
            > alpha[partial, None].astype(np.int16) + _STRAIGHT_MARGIN)
    return "straight" if over.any(axis=1).mean() > _STRAIGHT_SHARE else "premultiplied"


def alpha_setting(mode: str) -> int:
    """The window's word for it, as the shader's number."""
    return {"premultiplied": remap_engine.ALPHA_PREMULTIPLIED,
            "straight": remap_engine.ALPHA_STRAIGHT}.get(mode,
                                                         remap_engine.ALPHA_IGNORE)


@dataclass
class Progress:
    frames_done: int = 0
    total: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def fps(self) -> float:
        elapsed = time.monotonic() - self.started_at
        return self.frames_done / elapsed if elapsed > 0 else 0.0

    @property
    def eta(self) -> float:
        rate = self.fps
        return (self.total - self.frames_done) / rate if rate > 0 else 0.0


def render(source: Source, table_path: Path, output: Output,
           start: int, end: int, prefer_gpu: bool = True,
           on_progress=None, should_stop=None,
           viewer_table_path: Path | None = None,
           overlay=None, overlay_opacity: float = 0.0) -> dict:
    """Render [start, end] of the source through the baked table.

    With a viewer table the frames go through a second lookup on the way out --
    the flat frame is exactly what the viewer's view samples from, so the two
    chain the same way they do in the window.
    """
    table = remap_engine.Table(table_path)
    engine = remap_engine.make_remapper(table, (source.width, source.height), prefer_gpu)

    stage2 = None
    if viewer_table_path is not None:
        stage2 = remap_engine.make_remapper(
            remap_engine.Table(viewer_table_path), (table.width, table.height), prefer_gpu)
        if hasattr(engine, "set_output_yuv"):
            engine.set_output_yuv(False)         # the next stage wants pixels

    last = stage2 if stage2 is not None else engine
    output.width, output.height = last.width, last.height

    # Colour weighted by coverage is what a second resampling needs; only the
    # stage that writes the file cares about straight alpha.
    engine.set_premultiplied(True if stage2 is not None else not output.wants_alpha)
    last.set_premultiplied(not output.wants_alpha)

    # The frame's own transparency, if it has any. Asked of the file rather
    # than assumed, because premultiplied and straight look alike until an
    # edge is soft, and then they look wrong in opposite directions.
    alpha_mode = source.alpha_mode
    if alpha_mode == "auto":
        alpha_mode = read_alpha_convention(source, start)
    if hasattr(engine, "set_source_alpha"):
        engine.set_source_alpha(alpha_setting(alpha_mode))
    # The second stage deliberately does not carry it any further. It is
    # looking at the wall, and a hole in the content is a piece of wall that
    # is not lit -- black, not missing. Its alpha stays the wall's own
    # silhouette, which leaves that chain exactly as it was measured.
    # Only the stage that reads the source has a footprint to sample across;
    # the second one is looking at a frame it made itself.
    if hasattr(engine, "set_supersample"):
        engine.set_supersample(output.supersample)

    if overlay is not None and overlay_opacity > 0.0:
        # The map is drawn on the flat frame: that is this engine's output when
        # there is one stage, and the second stage's source when there are two.
        last.set_overlay(overlay)
        last.set_overlay_opacity(overlay_opacity)
        last.set_overlay_space(stage2 is None)

    # H.264 stores 4:2:0 whatever we feed it, so let the GPU pack the planes
    # and send a third of the bytes back. Formats that keep alpha stay RGBA.
    pack_yuv = (output.kind in ("h264", "hevc") and not output.wants_alpha
                and hasattr(last, "set_output_yuv"))
    if pack_yuv:
        last.set_output_yuv(True)

    # Two counts, and keeping them apart is the whole of the fix: how many
    # frames the source holds for this range, and how many go in the file.
    read_count = end - start + 1
    count = frames_out(read_count, source.fps, float(output.fps))
    yuv = getattr(engine, "wants_yuv", False)
    frame_bytes = (engine.frame_bytes_yuv if yuv
                   else source.width * source.height * 4)
    progress = Progress(total=count)

    output.path.parent.mkdir(parents=True, exist_ok=True)

    decode_command = source.decode_arguments(start, count, yuv, float(output.fps))
    decoder = subprocess.Popen(decode_command,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=NO_WINDOW)
    if pack_yuv:
        input_args = ["-f", "rawvideo", "-pix_fmt", "yuv420p",
                      "-color_range", "tv", "-colorspace", "bt709",
                      "-color_primaries", "bt709", "-color_trc", "bt709"]
    else:
        input_args = ["-f", "rawvideo", "-pix_fmt", "rgba"]
    encoder_args = ([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostats", "-y"]
                    + input_args
                    + ["-s", f"{last.width}x{last.height}",
                       "-framerate", str(output.fps), "-i", "-"] + output.arguments())
    encoder = subprocess.Popen(encoder_args, stdin=subprocess.PIPE,
                               stderr=subprocess.PIPE, creationflags=NO_WINDOW)

    # Into the log file rather than the window: too long to read at a glance,
    # and exactly what is wanted when a machine somewhere else misbehaves.
    print("decode:", " ".join(decode_command))
    print("encode:", " ".join(encoder_args))

    # Whatever the two of them have to say. A pipe nobody reads is not just
    # evidence thrown away -- it is a process that stops once the pipe fills.
    complaints: dict[str, list[bytes]] = {"decoder": [], "encoder": []}

    def listen(process, who: str) -> None:
        for line in iter(process.stderr.readline, b""):
            complaints[who].append(line)

    ears = [threading.Thread(target=listen, args=(decoder, "decoder"), daemon=True),
            threading.Thread(target=listen, args=(encoder, "encoder"), daemon=True)]
    for ear in ears:
        ear.start()

    def said(who: str) -> str:
        return b"".join(complaints[who]).decode("utf-8", "replace").strip()

    def code(process) -> int:
        """Windows reports failures as huge unsigned numbers; show the real one."""
        value = process.returncode
        return value - (1 << 32) if value is not None and value > (1 << 31) else value

    # Decoding, warping and encoding are three independent machines. Run them
    # at the same time: ffmpeg keeps working while the GPU does, instead of
    # each waiting its turn. The queues are short on purpose -- a few frames of
    # slack is enough to hide the jitter.
    incoming: queue.Queue = queue.Queue(maxsize=3)
    outgoing: queue.Queue = queue.Queue(maxsize=3)
    stop = threading.Event()

    # Set when the encoder stops taking frames. Without it the queue fills and
    # the loop waits on a reader that is never coming back.
    encoder_gone = threading.Event()

    def offer(queued: queue.Queue, item, until) -> bool:
        """Put an item, in short steps, so waiting stays interruptible."""
        while not until():
            try:
                queued.put(item, timeout=0.2)
                return True
            except queue.Full:
                continue
        return False

    def read_frames() -> None:
        try:
            for _ in range(count):
                if stop.is_set():
                    break
                raw = decoder.stdout.read(frame_bytes)
                if len(raw) < frame_bytes:
                    break
                if not offer(incoming, raw, stop.is_set):
                    break
        finally:
            offer(incoming, None, lambda: False)

    def write_frames() -> None:
        while True:
            item = outgoing.get()
            if item is None:
                break
            try:
                encoder.stdin.write(item)
            except (BrokenPipeError, OSError):
                encoder_gone.set()
                break

    reader = threading.Thread(target=read_frames, daemon=True)
    writer = threading.Thread(target=write_frames, daemon=True)
    reader.start()
    writer.start()

    cancelled = False
    ENOUGH = object()

    def stopping() -> bool:
        return should_stop is not None and should_stop()

    def done_waiting() -> bool:
        return stopping() or encoder_gone.is_set()

    def take():
        """The next decoded frame, None at the end, ENOUGH if we should stop.

        Waiting in short steps rather than one long block is what keeps the
        cancel button answerable while ffmpeg is thinking.
        """
        while not done_waiting():
            try:
                return incoming.get(timeout=0.2)
            except queue.Empty:
                continue
        return ENOUGH

    def hand_on(item) -> bool:
        """Pass a finished frame on; False if we gave up waiting to.

        An encoder can stall as easily as it can die -- a hardware session that
        never opens, a disk that stops -- and a plain put() would wait for it
        in the one place the cancel check cannot reach.
        """
        return offer(outgoing, item, done_waiting)

    try:
        while progress.frames_done < count:
            raw = take()
            if raw is ENOUGH:
                cancelled = stopping()
                break
            if raw is None:
                break
            if stage2 is None:
                ready = engine.submit(raw)
            else:
                flat = engine.submit(raw)
                ready = stage2.submit(flat) if flat is not None else None
            if ready is not None and not hand_on(ready):
                cancelled = stopping()
                break
            progress.frames_done += 1
            if on_progress is not None and progress.frames_done % 8 == 0:
                on_progress(progress)
    finally:
        stop.set()
        if cancelled:
            # Killed before anything is joined: the writer may be stuck on a
            # pipe nobody is reading, and it has to come back first.
            decoder.kill()
            encoder.kill()
        elif not encoder_gone.is_set():
            # One frame is still on the card when the loop ends -- one per stage.
            tail = engine.flush()
            if tail is not None and stage2 is not None:
                tail = stage2.submit(tail)
            if tail is not None:
                hand_on(tail)
            if stage2 is not None:
                tail = stage2.flush()
                if tail is not None:
                    hand_on(tail)

        # Room for the sentinel even when the writer has already gone and left
        # the queue full behind it.
        while True:
            try:
                outgoing.put(None, timeout=0.2)
                break
            except queue.Full:
                try:
                    outgoing.get_nowait()
                except queue.Empty:
                    pass

        writer.join(timeout=30)
        reader.join(timeout=5)
        for process, stream in ((encoder, encoder.stdin), (decoder, decoder.stdout)):
            try:
                stream.close()
            except OSError:
                pass
        decoder.wait()
        encoder.wait()
        for ear in ears:
            ear.join(timeout=5)

    # A render that produced nothing is a failure however calmly it ended, and
    # the reason is in what the processes said on their way out.
    if not cancelled and progress.frames_done == 0:
        raise RuntimeError(
            "ffmpeg produced no frames.\n"
            f"decoder exited {decoder.returncode}: {said('decoder') or '(silent)'}\n"
            f"encoder exited {encoder.returncode}: {said('encoder') or '(silent)'}\n"
            f"command: {' '.join(decode_command)}")

    trouble = []
    if decoder.returncode not in (0, None) and not cancelled:
        trouble.append(f"decoder exited {code(decoder)}: {said('decoder')}")
    if encoder.returncode not in (0, None) and not cancelled:
        trouble.append(f"encoder exited {code(encoder)}: {said('encoder')}")
    if trouble and progress.frames_done < count:
        raise RuntimeError("   ".join(trouble))

    return {
        "frames": progress.frames_done,
        "read": read_count,
        "in_fps": source.fps,
        "out_fps": float(output.fps),
        "seconds": time.monotonic() - progress.started_at,
        "fps": progress.fps,
        "engine": engine.name,
        "adapter": getattr(engine, "adapter_name", ""),
        "backend": getattr(engine, "backend", ""),
        "cancelled": cancelled,
        "output": output.path,
        "encoder": output.chosen_encoder,
        "source_alpha": alpha_mode,
        "command": " ".join(decode_command),
        "complaints": "   ".join(x for x in (said("decoder"), said("encoder")) if x),
    }


def main() -> int:
    """Small CLI so the pipeline can be exercised without the window."""
    import argparse

    parser = argparse.ArgumentParser(description="Render a remap without Blender")
    parser.add_argument("--pattern", required=True, help="printf pattern or movie file")
    parser.add_argument("--movie", action="store_true")
    parser.add_argument("--size", required=True, help="WxH of the source")
    parser.add_argument("--table", required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=0)
    parser.add_argument("--out", required=True)
    parser.add_argument("--kind", default="h264", choices=["h264", "png", "prores"])
    parser.add_argument("--alpha", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()

    width, height = (int(v) for v in args.size.lower().split("x"))
    source = Source(args.pattern, not args.movie, args.start, width, height)
    output = Output(kind=args.kind, path=Path(args.out), alpha=args.alpha)

    def report(progress: Progress) -> None:
        print(f"\r{progress.frames_done}/{progress.total}  "
              f"{progress.fps:.1f} fps  ETA {progress.eta:.0f}s", end="", flush=True)

    result = render(source, Path(args.table), output, args.start, args.end,
                    prefer_gpu=not args.cpu, on_progress=report)
    print(f"\n{result['frames']} frames in {result['seconds']:.1f}s "
          f"-> {result['fps']:.1f} fps on {result['engine']} "
          f"{result['adapter']} ({result['backend']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
