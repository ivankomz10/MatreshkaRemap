"""Run the checks.

    python tests/run.py                 everything except the launch test
    python tests/run.py --launch        also start the built exe and close it
    python tests/run.py --show          draw the windows instead of hiding them
    python tests/run.py gizmo           only the checks whose name says gizmo

Windows are offscreen by default, which is what lets this run over a remote
session, in a build step, or while somebody else is at the machine.

`remap_tool.json` is put back the way it was found, whatever happens. It holds
work rather than preferences -- the framings aimed on every clip are in it --
and the suite drives the real window, which writes to it. Once was enough.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

WANTED = [word for word in sys.argv[1:] if not word.startswith("--")]
FLAGS = {word for word in sys.argv[1:] if word.startswith("--")}

if "--show" not in FLAGS:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["MATRESHKA_TESTS_LAUNCH"] = "1" if "--launch" in FLAGS else ""

import constants                                     # noqa: E402

SETTINGS = constants.PROJECT_DIR / constants.SETTINGS_FILE
KEPT = HERE / "_scratch" / "remap_tool.json.before-the-tests"


def hold_the_settings() -> None:
    if SETTINGS.is_file():
        KEPT.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SETTINGS, KEPT)


def give_them_back() -> None:
    if KEPT.is_file():
        shutil.copy2(KEPT, SETTINGS)


import harness                                       # noqa: E402

import check_tables                                  # noqa: E402,F401
import check_transform                               # noqa: E402,F401
import check_gizmo                                   # noqa: E402,F401
import check_panel                                   # noqa: E402,F401
import check_window                                  # noqa: E402,F401
import check_flow                                    # noqa: E402,F401
import check_render                                  # noqa: E402,F401
import check_build                                   # noqa: E402,F401

if __name__ == "__main__":
    print(f"Matreshka Remap {constants.APP_VERSION} -- "
          f"{len(harness.CHECKS)} checks")
    if "--launch" not in FLAGS:
        print("(the built exe is not started; pass --launch for that)")
    hold_the_settings()
    try:
        sad = harness.run(" ".join(WANTED))
    finally:
        give_them_back()
    raise SystemExit(1 if sad else 0)
