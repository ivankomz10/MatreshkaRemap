"""Puts the plugin where Resolve looks for it, on either platform.

Resolve reads Fuses and Macros once, at startup, so it has to be restarted
after this. That is not an oversight in the installer; there is no way to ask
Fusion to look again.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

WHERE = {
    "win32": Path.home() / "AppData/Roaming/Blackmagic Design/DaVinci Resolve"
                           "/Support/Fusion",
    "darwin": Path.home() / "Library/Application Support/Blackmagic Design"
                            "/DaVinci Resolve/Fusion",
}


def main() -> int:
    support = WHERE.get(sys.platform)
    if support is None:
        print(f"no known Fusion folder for {sys.platform}")
        return 2
    if not support.parent.is_dir():
        print(f"Resolve does not seem to be installed: {support.parent}")
        return 2

    copied = 0
    for source in sorted(p for p in HERE.rglob("*") if p.is_file()):
        relative = source.relative_to(HERE)
        # Only the three folders Resolve reads; the readme and the generator
        # stay in the package.
        if relative.parts[0] not in ("Fuses", "Macros", "Templates"):
            continue
        target = support / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
        print(f"  {relative.as_posix()}")

    print(f"\n{copied} files into {support}")
    print("Restart Resolve -- Fuses and Macros are read only at startup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
