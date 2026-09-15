"""Named framings, in a file of their own beside the project.

Kept apart from `remap_tool.json` on purpose. That file is what this window
remembers about the work in front of it -- which clip was framed how, where the
overlay came from -- and it is nobody else's business. A preset is meant to
travel: handed to whoever is doing the next piece, or checked in beside the
tables it was measured against.

A preset holds the transform and nothing else. Not the alpha mode, not the
output format: those belong to a clip and to a delivery, and a framing that
quietly changed either would be a framing nobody trusted.
"""
from __future__ import annotations

import json
from pathlib import Path

import constants
import transform as xf

FILE = "remap_presets.json"


def path() -> Path:
    return constants.PROJECT_DIR / FILE


def load() -> dict[str, xf.Transform]:
    """Every preset by name. A missing or broken file is simply none of them."""
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    found: dict[str, xf.Transform] = {}
    for name, state in raw.items():
        if isinstance(state, dict):
            found[str(name)] = xf.Transform.from_dict(state)
    return found


def save(presets: dict[str, xf.Transform]) -> bool:
    """Write them all back, keeping one copy of what was there before."""
    live = path()
    try:
        if live.is_file():
            live.replace(live.with_suffix(live.suffix + ".bak"))
    except OSError:
        pass
    try:
        live.write_text(
            json.dumps({name: placement.to_dict()
                        for name, placement in presets.items()},
                       ensure_ascii=False, indent=1),
            encoding="utf-8")
        return True
    except OSError:
        return False


def remember(name: str, placement: xf.Transform) -> bool:
    name = name.strip()
    if not name:
        return False
    presets = load()
    presets[name] = placement.copy()
    return save(presets)


def forget(name: str) -> bool:
    presets = load()
    if name not in presets:
        return False
    del presets[name]
    return save(presets)
