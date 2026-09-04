# Matreshka Remap — what this is and what has been learned

Working notes, kept so the next session can start from facts rather than from
re-derivation. Everything measured here was measured on this machine.

---

## The thing itself

A LED wall at Zaryadye. Content is authored at **2000 x 2360** and has to be
reprojected onto the wall's own layout, **4608 x 1584**. There is also a second
projection: the wall seen from a fixed camera, **1710 x 2024**, used to judge
how content will read from where people stand.

**The scene is static.** An orthographic camera that never moves, a mesh
deformed once by geometry nodes, a material that is a plain emission fed by one
UV map. So the mapping from output pixel to source pixel is the same for every
frame, and Blender was recomputing it 2665 times.

That mapping was baked once. A frame is now a gather:

    out[x, y] = source(u[x, y], v[x, y]) * coverage[x, y]

Blender ran at 3.3 fps. This runs at 39–116 depending on resolution and codec.

---

## Layout on disk

```
E:\05_Render\Matreshka_Remap\
    MatreshkaRemapRenderer.exe      the Windows build
    MatreshkaRemapRenderer_mac.zip  the source archive a Mac builds from
    pack_mac.py                     builds that archive (line endings, paths)
    tool\                           the application
        main.py                     the window
        remap_render.py             the render pipeline, ffmpeg, encoders
        remap_engine.py             the GPU warp (wgpu) and a CPU fallback
        avio.py                     decoding and encoding through PyAV
        app_jobs.py                 threads: preview and render
        scan.py                     finding sequences on disk
        constants.py, depends.py, logfile.py, imagefile.py, preview3d.py
        exrfile.py                  a hand-written EXR reader/writer
        export_tables_exr.py        npz tables -> ST map EXRs
        bake_tables.py              runs in Blender, makes the tables
        bake_geometry.py            runs in Blender, makes the viewer table
        tables\                     the baked tables
    fusion_plugin\                  the DaVinci Resolve plugin, ready to copy
    mac_build\                      what pack_mac.py zips
```

A second, separate project lives at `E:\05_Render\Matreshka_Viewer` — a HAP
player that shows content on the baked geometry. Different codebase, same wall.
Its `CharCity_kinetic.json` **must not be touched**; it is for something else.

---

## The tables

`bake_tables.py` renders, in Blender, a material that emits its own UV
coordinates: red carries u, green carries v, alpha carries the coverage EEVEE's
antialiasing produces at the silhouette. Two passes per resolution:

- `_sharp` — one unjittered sample, giving exact coordinates
- `_soft` — many samples, giving antialiased coverage

Both are un-premultiplied, coordinates taken from the sharp pass and coverage
from the soft one, and the result saved as `.npz`.

**The `.npz` is the real table.** The `table_*.exr` files sitting beside them in
`tool/tables` are one of the two intermediate passes — still premultiplied and
carrying that pass's hard-edged coverage. Wiring those into anything gives an
aliased silhouette. This cost an hour to notice.

`tool/tables/st/*.exr` are the real tables exported for hosts that read ST maps,
written by `export_tables_exr.py`.

| | size | covered |
|---|---|---|
| table_full | 4608 x 1584 | 55.8% |
| table_half | 2304 x 792 | |
| table_quarter | 1152 x 396 | |
| viewer_table | 1710 x 2024 | 90.2% |

---

## Measurements worth not repeating

**The footprint.** One output pixel of the full table reaches across **1.0 to
2.0 source pixels**, median 1.44, and nothing anywhere exceeds 2.0. So the
mapping is mildly minifying everywhere and there is no regime where a
rasteriser's filtering would win.

**The mip level in use** is 0 to 1.32, median 0.53 — the engine bakes the level
into the table's fourth channel and samples a real mip chain.

**The warp is not the bottleneck.** At Full it takes 7.4 ms a frame (135 a
second) while the whole pipeline runs at 39–48. On Windows at Full, NVENC
refuses H.264 above 4096 wide, so libx264 takes it and sets the pace.

| | warp alone | whole pipeline |
|---|---|---|
| Quarter 1152x396 | 336/s | 116/s |
| Half 2304x792 | 301/s | 107/s |
| Full 4608x1584 | 136/s | 48/s |

**Four taps instead of one** cut the resampling error on near-Nyquist content
from 16.8 to 7.4 of 255, measured against the same mapping supersampled sixteen
times. Cost: nothing measurable, because the stage waits on moving frames.
**On the real sources it changes almost nothing** — 0.2 to 0.6 of 255 on
average — so it earns its keep on thin type and line work, not on photography.

**Parameters were swept, not chosen:** tap spread 0.25 of the footprint, mip
level dropped by 0.8. Every other pair tried was worse on both mean and p99.

**Alpha output, tested through PNG and ProRes.** Flat-colour source and real
content, quarter and full tables, every combination the app can produce.

| | result |
|---|---|
| PNG 8-bit alpha vs baked coverage | identical, max 0 |
| PNG 16-bit alpha vs coverage | max 0.5 of 65535 |
| Straight RGB on partial-coverage pixels | the source colour, not scaled by it |
| Straight x alpha over black vs the no-alpha render | mean 0.000, max 1.1 |
| ProRes 4444 alpha vs the PNG's | mean 0.001, max 1 |
| ProRes 4444 RGB vs the PNG | mean 0.05, max 3 |
| ProRes 422 HQ vs the no-alpha PNG, silhouette | mean 3.4, p99 28, max 43 |

The last row is 4:2:2 chroma across a hard edge and is the reason a matte goes
out as 4444 rather than HQ. `prores_ks` writes 4444 at 4608 wide without
complaint, and stores it 12-bit whatever `-pix_fmt` asks for. Outside coverage
every format is transparent black -- the shader returns early there, so nothing
is sampled and nothing leaks.

Two warts found, neither in the alpha itself. A PNG sequence is numbered from 1
rather than from the frame it started at, because nothing passes
`-start_number` on the encode side. And through the API, profile 4444 with
alpha unticked gives a 4444 file holding 4:2:2 -- unreachable from the window,
which has no profile picker and forces 4444 when Alpha is on.

**Against Blender**, the renderer differs by 0.5/255 in the interior; silhouette
edges by ~9/255 because the antialiasing is reconstructed from coverage rather
than sampled.

---

## Two bugs worth remembering

**Rates.** The pipeline had no notion of frame rate: it read N frames and wrote
N frames at a fixed 30. A 60 fps source therefore came out twice as long. Fixed
by `frames_out(frames_in, in_fps, out_fps)` and an `fps=` filter on the decode
side, so frames are chosen by **time**. At equal rates the filter is omitted
entirely and the old path is untouched. The source rate is probed from the
container and can be overridden; `-r` is only forced on the input when the user
has contradicted the file, because forcing constant rate on a variable-rate
source drops frames by itself.

**Colour space in measurements.** Comparing a GPU result against a numpy
reference means averaging in the same space. The GPU works in linear light; a
reference averaged in sRGB inflates the error several times over. Two of my
early numbers were wrong for exactly this reason.

---

## The source's own alpha

Reported as "black instead of transparency in the middle" on a ProRes 4444
matte out of After Effects. Three faults stacked, and only the last one was
where it looked.

**The alpha never reached the shader.** The GPU path asks for 4:2:0 planes
because moving RGBA was the pipeline's limit, and 4:2:0 has nowhere to put an
alpha channel. `wants_yuv` is now per instance and turns off exactly when a
frame's transparency counts; opaque footage keeps the fast pipe and renders
byte-for-byte what it did before.

**The warp threw it away.** The shader wrote `coverage` as the output alpha and
never read `colour.a`. It now writes `coverage * source_alpha`: the table says
where the wall is, the frame says where the picture is, and both have to hold.

**Premultiplied has to be undone where it was applied.** After Effects weights
the eight-bit value, not the light it stands for. Dividing after the hardware
has linearised gives white at half alpha back as 0.42 instead of 1.0 -- a
visibly dark fringe. The shader encodes back to sRGB, divides, and linearises
again.

Which convention a file uses is measured, not assumed: premultiplied colour
cannot exceed its own alpha. On the file in question not one pixel of 941306
partial-alpha ones did, and the largest ratio was 0.6. `Auto` in the window
runs that test on the frame under the playhead, through PyAV so it works
before ffmpeg is installed, and says what it found under the sequence table.

| measured on frame 234, full table | |
|---|---|
| output alpha vs coverage x warped source alpha | mean 0.097, 98.4% within 1 |
| ...away from steep matte edges | mean 0.034 |
| the residual, on edges steeper than 4/px | 13751 px, 0.19% of frame |
| opaque footage, before vs after | byte-identical, all four formats |

The residual is mip filtering against a flat bilinear reference, not the alpha.

**The viewer chain deliberately stops there.** It is looking at the wall, and a
hole in the content is a piece of wall that is not lit -- black, not missing.
Its alpha stays the wall's own silhouette.

**Straight alpha amplifies near-transparent colour**, as it does in every
compositor: 0.16% of fully clear pixels carry colour, contributing at most
0.76 of 255 once composited. Correct, and worth knowing before looking at the
RGB of a matte with alpha switched off.

The preview tiles a checkerboard under the frame, in viewport space so the
squares do not zoom, because black content and no content look identical over
a dark background. The preview image is handed to Qt as
`Format_RGBA8888_Premultiplied`, which is what the engine actually produces --
read as straight it was compositing the silhouette by its own alpha twice.

---

## macOS

`wgpu` reaches Metal. Apple Silicon and Intel builds must be made separately;
there is no cross-compilation. `build_mac.sh` makes its own venv.

Hardware paths, all **probed rather than assumed** by encoding one black frame
to a null output — an encoder refuses for reasons written down nowhere:

- `h264_videotoolbox`, `hevc_videotoolbox` for movies
- `prores_videotoolbox` where there is a ProRes engine (M1 Pro and up; plain M1
  and M2 have none and fall back to `prores_ks`)
- `-hwaccel videotoolbox` on the decode side, for movies only

None of the above has been run on real Apple Silicon. The Windows paths were
regression-tested and are unchanged.

`pack_mac.py` exists because two things break a Mac build silently:
**CRLF line endings** (`set -o pipefail\r` reads as "invalid option name") and
**backslashes in zip entry names** (PowerShell's Compress-Archive writes them;
a Mac unpacks `tables\table_full.npz` into a file, not a folder).

---

## The DaVinci Resolve plugin

Lives in `fusion_plugin/`, installs by copying three folders into Resolve's
Fusion support folder. Nothing is compiled: the GPU kernel is written once and
Resolve compiles it to OpenCL or Metal per platform.

```
Fuses/MatreshkaRemap.fuse                    the node
Macros/MatreshkaScreen.setting               the wrapper, one input, controls
Macros/Matreshka/*.exr                       the ST maps
Templates/Edit/Effects/MatreshkaScreen.setting   the same file, for the Edit page
```

The macro is **generated**, not hand-written — see the generator in the
scratchpad (`make_macro.py`); five Loaders of boilerplate by hand is how a wrong
filename gets in and stays.

### Verified

One frame through the Fuse against the standalone renderer's own output:
**mean 0.186 of 255, p99 = 2, max 21, alpha bit-identical.** Closer to the
four-tap reference than the one-tap one, which also proves the taps branch runs.

### Everything that cost time

- A macro is `MacroOperator`, **not** `GroupOperator`. A GroupOperator macro is
  silently ignored.
- A Fuse's registry id is `Fuse.MatreshkaRemap`, not `MatreshkaRemap`.
- **`comp:AddTool()` does not add macros.** It works on registered *tools*.
  Macros are pasted: `comp:Execute('comp:Paste(bmd.readfile("path"))')`. I spent
  several restarts concluding a perfectly good macro was broken.
- `comp.Execute()` returns nothing across the Python bridge. Have the Lua write
  its answer to a file.
- A Fuse **cannot read an image file from disk**; there is no such Lua API in
  the shipped examples. Hence the Loaders inside a macro.
- **Every tool inside a macro needs `GlobalIn`/`GlobalOut`, not just the
  Loaders.** On the Edit page the comp is asked for frames at the timeline's own
  numbering — 01:00:00:00 is frame 86400 — and a tool whose range ends at 0
  answers once and then answers nothing, which arrives as a black frame after
  the first. Setting it on the map Loaders alone did not help, because the
  Transform, the two switches and the two warps each carry a range as well. Set
  `GlobalIn = 0` and `GlobalOut = 1000000` on all of them. Do not set a negative
  `GlobalIn`, and `HoldFirstFrame`/`ExtendLast` are not the lever here.
- `Fuses` and `Macros` are scanned **at startup**. Every change needs a restart.
- Fusion writes EXR with ZIP (16 scanlines a block); `exrfile.py` only reads
  what it writes (ZIPS, one scanline). To compare, have Fusion write PNG.
- Reading a `QImage`'s bits without copying gives a use-after-free segfault.

### The source area

The map's coordinates run 0..1 across the picture it was baked against. Hand
the node a 2000 x 2360 image and they land on it exactly — which is what a
Loader on the Fusion page does. On the Edit page the input is the whole
timeline frame with a clip fitted inside it, so they would land on the frame.

So the node reads from a box, described by an **aspect** rather than by pixels:
at 4608 x 1584 the default 0.847458 gives 1342 x 1584 centred, and on a frame
that already has the source's shape the same number gives the whole frame. One
setting, correct on both pages.

The transform inside the macro runs **before** the warp; that is what content is
aimed with. The box stays put and the content moves through it.

### Not verified

- Whether Fusion passes a Transform through untouched when it is at identity.
  If not, sharpness is lost even when nothing is moved.
- The vertical direction of the area offset. At a full-frame box it reduces to
  the identity by construction, so it is right there; moving the centre up or
  down may go the wrong way.

### Dropped deliberately

A `Both` mode that composited the camera view as an inset into the flat frame's
empty right corner (1244 x 1295 with no coverage at all). Built, then removed at
the user's request. The half-size viewer table it used is gone with it.

---

## How the user works

Questions go through the interactive picker, never as a numbered list in prose —
this is written into `~/.claude/CLAUDE.md`. Recommended option first, marked
"(рекомендую)", with the consequence in each option's description.

He renders verification frames himself and reads numbers, not adjectives. Twice
I diagnosed from a screenshot and was wrong both times; measure first.
