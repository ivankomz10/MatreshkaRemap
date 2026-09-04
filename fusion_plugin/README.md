# Matreshka Screen — the reprojection as a DaVinci Resolve node

The same warp the standalone renderer does, inside Resolve. It is not a port:
the mapping was already baked into lookup tables, so both roads lead to the
same gather. Measured on one frame against the renderer's own output: mean
difference **0.186 of 255**, p99 = 2, and the alpha matches bit for bit.

## Installing

Copy the three folders into Resolve's Fusion support folder, keeping the
structure. Nothing is compiled and nothing is installed system-wide.

- **Windows** — `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\`
- **macOS** — `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/`

```
Fuses/MatreshkaRemap.fuse
Macros/MatreshkaScreen.setting
Macros/Matreshka/*.exr
Templates/Edit/Effects/MatreshkaScreen.setting
```

Restart Resolve. Both folders are read once, at startup.

The GPU kernel is written once and compiled by Resolve into OpenCL or Metal
depending on the machine, so Windows and Apple Silicon run the same source with
no build step.

## Using it

On the **Edit** page the node appears in the Effects library as a Fusion
Effect; drag it onto a clip. On the **Fusion** page it is under
`Add Tool → Macros`.

Set the timeline to **4608 x 1584**. The node always answers at the size of the
map it is warping through, because that is the screen's own layout; on a
timeline of another shape Resolve will fit it in.

| Control | What it does |
|---|---|
| Mode | `Flat` the wall's layout · `Viewer` that layout seen from the projector's camera |
| Resolution | Which baked table to warp through: Full, Half or Quarter |
| Source area centre, height, aspect | Which part of the incoming frame the map's 0..1 coordinates refer to |
| Position, Size, Angle, Flip | An ordinary transform applied to the source **before** the warp — this is what you aim the content with |

### The source area

The map runs 0..1 across the picture it was baked against, which was 2000 x
2360. Hand the node that picture and 0..1 lands on it exactly — which is what
happens on the Fusion page when a Loader feeds it directly.

On the Edit page the input is the whole timeline frame with a clip sitting
somewhere inside it, so 0..1 would land on the frame instead. The node
therefore reads from a box in the middle of the frame, described by an aspect
rather than by pixels: at 4608 x 1584 the default 0.847 gives 1342 x 1584
centred, and on a frame that already has the source's shape the same number
gives the whole frame. One setting, right on both pages.

Move the clip into that box with the transform. The box stays where it is; the
content moves through it.

The view from the camera is warped out of the finished layout rather than out
of the source, so it follows the transform on its own.

## What is in `Macros/Matreshka`

Lookup tables as ST maps: `u` in red, `v` in green, coverage in alpha, 32-bit
float. Exported from the renderer's own `.npz` tables, **not** from the
`table_*.exr` files that sit beside them in `tool/tables` — those are one of
the two passes the bake renders, still premultiplied and carrying that pass's
hard-edged coverage rather than the antialiased one.

Written by `tool/export_tables_exr.py`, which needs `tool/exrfile.py`.

## Two things worth knowing

**No mip levels.** A Fusion kernel has none, so the node takes four samples per
output pixel across the footprint the map itself describes. That is the only
filtering available here, and measured against the same mapping supersampled
sixteen times it beats the single mip-filtered sample the standalone renderer
takes by default.

**The transform is a resample.** Anything moved before the warp is resampled
twice. Unavoidable when you are aiming content; worth knowing when you are not.
