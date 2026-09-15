"""A very small test harness, so the suite needs nothing the app does not.

The application's virtual environment has numpy, PySide6, wgpu and av in it and
nothing else. Asking whoever runs these to install pytest first is asking them
not to run them, so the whole apparatus is one decorator, three assertions and
a runner.

Checks report numbers whether they pass or fail. Half of what is worth knowing
about this program is a measurement -- how far a handle finished from the
cursor, how long a cached frame took, how much contrast a tab has -- and a
suite that prints only "ok" throws that away every time it runs.
"""
from __future__ import annotations

import time
import traceback

CHECKS: list = []


class Failure(Exception):
    """A check that ran and did not like what it saw."""


def check(group: str, about: str):
    """Register a function as a check. It may return a note to print."""
    def keep(function):
        CHECKS.append((group, about, function))
        return function
    return keep


def want(condition, message: str) -> None:
    if not condition:
        raise Failure(message)


def close(got: float, expected: float, within: float, what: str) -> None:
    if abs(got - expected) > within:
        raise Failure(f"{what}: {got:.4g}, wanted {expected:.4g} "
                      f"give or take {within:.4g}")


def inside(got: float, low: float, high: float, what: str) -> None:
    if not low <= got <= high:
        raise Failure(f"{what}: {got:.4g}, wanted between {low:.4g} and {high:.4g}")


def run(only: str = "") -> int:
    """Run every registered check. Returns the number that failed."""
    groups: dict[str, list] = {}
    for group, about, function in CHECKS:
        if only and only.lower() not in (group + " " + about).lower():
            continue
        groups.setdefault(group, []).append((about, function))
    if not groups:
        print("nothing matched")
        return 1

    passed = failed = 0
    started = time.monotonic()
    for group, items in groups.items():
        print(f"\n{group}")
        print("-" * max(len(group), 60))
        for about, function in items:
            began = time.monotonic()
            try:
                note = function()
                took = 1000 * (time.monotonic() - began)
                passed += 1
                tail = f"   {note}" if note else ""
                print(f"  ok    {about:<46s} {took:6.0f} ms{tail}")
            except Failure as sad:
                failed += 1
                took = 1000 * (time.monotonic() - began)
                print(f"  FAIL  {about:<46s} {took:6.0f} ms")
                print(f"        {sad}")
            except Exception:  # noqa: BLE001 -- a broken check is a failed check
                failed += 1
                print(f"  ERROR {about}")
                for line in traceback.format_exc().strip().splitlines():
                    print(f"        {line}")

    print(f"\n{passed} passed, {failed} failed, "
          f"{time.monotonic() - started:.1f} s")
    return failed
