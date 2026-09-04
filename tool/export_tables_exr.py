"""Writes the lookup tables as EXR, for hosts that read an ST map.

The EXRs already in `tables/` are not these. Those are one of the two passes
`bake_tables.py` renders -- the sharp one, kept for reference -- and they are
premultiplied and carry that pass's own hard-edged coverage. The table the
renderer actually uses is the npz: exact coordinates from the sharp pass,
antialiased coverage from the soft one, both already divided through.

So these are written from the npz, un-premultiplied, which is also the form a
plugin wants: no division to undo, and nothing to amplify where coverage is
almost nothing.
"""
from pathlib import Path

import numpy as np

import exrfile

HERE = Path(__file__).resolve().parent / "tables"
OUT = HERE / "st"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for source in ("table_full", "table_half", "table_quarter", "viewer_table"):
        npz = HERE / f"{source}.npz"
        if not npz.is_file():
            print(f"{source}: no npz, skipped")
            continue
        d = np.load(npz)
        u, v, cov = d["u"], d["v"], d["coverage"]
        target = OUT / f"{source}_st.exr"
        exrfile.write(target, {"R": u, "G": v,
                               "B": np.zeros_like(u), "A": cov})

        back = exrfile.read(target)
        worst = max(float(np.abs(back[a] - b).max())
                    for a, b in (("R", u), ("G", v), ("A", cov)))
        h, w = u.shape
        print(f"{source:14s} {w:5d}x{h:<5d}  {target.stat().st_size/1e6:6.2f} MB "
              f"(raw {h*w*16/1e6:6.2f})   read back exact: {worst == 0.0}")


if __name__ == "__main__":
    main()
