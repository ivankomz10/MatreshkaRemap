"""A log on disk, because a windowed application has nowhere else to speak.

A frozen `--windowed` build has no console: every `print` goes into a void, and
an unhandled exception takes the window with it and leaves nothing behind. That
is fine until something only happens on someone else's machine, and then it is
the whole problem -- there is no way to ask the program what it saw.

So everything the window says goes to a file as well, along with what the
modules print, what ffmpeg complains about, and any traceback that would
otherwise have been the last thing nobody read.
"""
from __future__ import annotations

import datetime
import platform
import sys
import threading
import traceback
from pathlib import Path

import constants

DIR_NAME = "Logs"
KEEP = 10                      # sessions; older ones are removed on start


class _Tee:
    """A stream that writes to the log as well as wherever it went before.

    In a frozen windowed build the original is None, and then this is simply
    where print goes.
    """

    def __init__(self, original, tag: str) -> None:
        self.original = original
        self.tag = tag
        self._partial = ""

    def write(self, text: str) -> int:
        if self.original is not None:
            try:
                self.original.write(text)
            except Exception:  # noqa: BLE001 -- the log still gets it
                pass
        self._partial += text
        while "\n" in self._partial:
            line, self._partial = self._partial.split("\n", 1)
            if line.strip():
                write(line.rstrip(), self.tag)
        return len(text)

    def flush(self) -> None:
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:  # noqa: BLE001
                pass

    def isatty(self) -> bool:
        return False


_handle = None
_path: Path | None = None
_lock = threading.Lock()


def path() -> Path | None:
    """Where this session is being written, once it has started."""
    return _path


def folder() -> Path:
    return constants.app_dir() / DIR_NAME


def write(message: str, tag: str = "") -> None:
    """One timestamped line, on disk before the next one is composed.

    Flushed every time on purpose: the interesting line is usually the last
    one before something died, and a buffer would have eaten exactly that.
    """
    if _handle is None:
        return
    stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    prefix = f"{stamp}  {tag + '  ' if tag else ''}"
    with _lock:
        for line in str(message).splitlines() or [""]:
            _handle.write(f"{prefix}{line}\n")
        _handle.flush()


def _trim() -> None:
    """Keep the last few sessions and no more."""
    try:
        existing = sorted(folder().glob("session_*.log"))
        for stale in existing[:-KEEP]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def start() -> Path | None:
    """Open this session's file and start catching everything."""
    global _handle, _path

    if _handle is not None:
        return _path
    try:
        folder().mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        _path = folder() / f"session_{stamp}.log"
        _handle = _path.open("w", encoding="utf-8")
    except OSError:
        _handle, _path = None, None
        return None

    _trim()
    write(f"{constants.APP_NAME} {constants.APP_VERSION}")
    write(f"{platform.platform()}   python {sys.version.split()[0]}   "
          f"{'frozen' if getattr(sys, 'frozen', False) else 'source'}")
    write(f"executable   {sys.executable}")
    write(f"application  {constants.app_dir()}")
    write(f"project      {constants.PROJECT_DIR}")

    sys.stdout = _Tee(sys.stdout, "out")
    sys.stderr = _Tee(sys.stderr, "err")

    previous = sys.excepthook

    def catch(kind, value, tail) -> None:
        write("UNHANDLED\n" + "".join(traceback.format_exception(kind, value, tail)),
              "err")
        previous(kind, value, tail)

    sys.excepthook = catch

    # A crash inside a QThread does not reach sys.excepthook on its own.
    if hasattr(threading, "excepthook"):
        def catch_thread(args) -> None:
            write(f"UNHANDLED in {args.thread.name if args.thread else '?'}\n"
                  + "".join(traceback.format_exception(
                      args.exc_type, args.exc_value, args.exc_traceback)), "err")
        threading.excepthook = catch_thread

    return _path
