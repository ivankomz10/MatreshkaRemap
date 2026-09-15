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

## The transform editor

Nothing in the pipeline ever knew what shape the material was meant to be. The
tables read a picture 0..1 across, that picture was authored at 2000 x 2360,
and a clip of any other shape arrived stretched -- silently, because there was
nothing to compare it against. `transform.py` is now the one place that knows.

**Identity is the old behaviour**, the whole frame across the whole window, and
the shader skips the entire path when nothing has been touched. Measured: four
formats of an opaque clip render byte-for-byte what they rendered before the
editor existed, and a transformed frame has zero soft-edge pixels at identity.

Order: crop cuts the source frame, flips mirror it, scale and rotation turn
about the pivot, position moves last. Crop cuts without rescaling, the way an
inspector's crop does. Values are kept as fractions of the source frame and
shown as pixels, so swapping a 2000 x 2360 clip for a 3328 x 2496 one leaves
the framing looking the same.

**The window is drawn at 0.847, not at the clip's own shape.** This falls out
of keeping the old identity: if the window were the clip's frame there would be
nothing for `Fit` to fit -- the clip would already fill it exactly. Drawing the
window at the baked aspect makes the stretch visible, and Fit and Fill correct
it. Fit on a 3328 x 2496 clip gives scale Y 0.6356; the displayed proportion
comes back to 1.3333, its own.

| checked on frame 234, full table | |
|---|---|
| shader's map vs `transform.inverse()` | mean 0.30 of 255, max 1.4 |
| ...the same residual at identity | so it is the sRGB round trip, not the map |
| where the clip lands vs the maths | 99.4% to 100% of samples agree |
| the rest | the soft edge, always wider than a hard prediction |
| that edge, on a 20 degree rotation | 5903 px, mean alpha 127.7 -- half, as it should be |

**Two things the shader had to be told twice.** The four taps step by the
difference between neighbouring table entries, which is a distance in the
window; after a transform they have to go through the same map or they walk in
the wrong direction. And the clip's edge fades over the footprint already baked
into the table's fourth channel, divided by the scale, which is what makes the
clip's edge and the wall's edge look drawn by the same hand.

The CPU fallback carries the same maths, rebuilt into its gather maps whenever
the transform changes. Its edge fades over one source pixel rather than the
baked footprint, so the two renderers differ there by well under a pixel.

`transform_ui.py` holds the two halves of the editor: `SourceView`, where the
window stands still and the clip moves over it, and `TransformPanel`, the
numbers. Dragging a corner solves for the scale that puts that corner under the
pointer rather than nudging it, so a long drag does not drift. Dragging the
pivot compensates the position so the picture does not move.

Placements live in `remap_tool.json` under `transforms`, keyed by file name,
and an identity one is deleted rather than written.

---

## The editor, second pass

**The handles moved onto the picture.** There is no separate view to aim in any
more; the gizmo is drawn over the flat frame and over the view from the camera,
and the camera view is where aiming actually happens. What made that possible
is `gizmo.py`: the baked table already says, for every pixel of the finished
frame, where in the window it read from, so the cursor's window coordinate is a
table lookup. The other direction is scattered into a 384-square grid once per
table -- 19 ms to build, 0.023 ms a lookup, round trip a median 0.0013 of the
window. Coarse on purpose: it places a handle, and the measurement is in the
panel. For the camera view the two tables are composed, viewer into flat.

The clip's border is walked and dropped through the same map, so it arrives as
several strokes rather than one closed shape -- which is honest, because a
rectangle on this wall really is a row of disjoint pieces. Five runs flat, one
in the camera view.

Rotation is a screen-space handle a fixed distance above the pivot: an arm that
followed the bend would point somewhere useless, while the angle it produces is
still worked out in the window.

**The pivot sprang back** because `_store_placement` threw away anything whose
mapping was the identity, and moving the pivot alone leaves the mapping exactly
as it was. `is_identity` (what the shader skips) and `is_default` (what the
file bothers to keep) are now two different questions. Dragging it snaps to the
corners, the edges and the middle, within twelve screen pixels.

**Everything opens fitted.** A clip already shaped like the wall's picture is
unaffected -- fitted is the identity there, and the four-format regression is
still byte-for-byte. Anything else now arrives undistorted instead of squeezed.

**Icons are Houdini's**, rendered to flat alpha masks by `_make_icons.py` and
tinted at draw time from the palette, so they read on a light theme and a dark
one. Resolve was the first place looked: its interface icons are compiled into
the binary, and the only images on disk are labels for the hardware panel.

**Number fields scrub with the middle button**, Houdini's way. The left button
was tried first and was wrong: it belongs to the text, so a drag selected
instead of counting, and telling the two apart by how far the pointer had moved
was a guess made on every click. The middle button needs no guess. While
dragging, the pointer is hidden and wraps round the screen rather than stopping
at it.

**A control that is doing something says so.** Reset arrows are lit and clickable
only beside a value that differs from its default; the scale link is a padlock
that opens and closes rather than a button that only changes shade; the flip
buttons take the same accent. All three read on a light theme and a dark one,
because icons are tinted from the widget's own palette at the moment they are
applied and repainted whenever the theme moves -- once when the window is first
shown, and again on any palette change. An icon tinted once at startup is drawn
in the previous theme's ink, which after a switch is the background colour.

**The panel is a tab, not a drawer.** It used to float over the picture, which
meant it covered the thing it was describing and had to be placed by hand on
every resize. The left column is now two tabs -- Source, holding everything a
render needs, and Transform, holding the whole editor -- and the handles appear
on the picture exactly while the Transform tab is on top. Aiming and setting up
a render are two jobs done at different times, and each is better with the whole
column than both are with half of it.

**A drop belongs to whatever it landed on.** The viewport takes its own and
makes it the source; the Frame row takes its own and makes it the border; the
rest of the window catches what missed both and treats it as a source. The Frame
row's own line edit had to be told to stop accepting drops -- a QLineEdit takes
them by default and was swallowing the file before the row saw it.

**Two builds, two lists of data files.** The Windows build reads
`build.spec`; `build_mac.sh` spells the same list out again as `--add-data`
flags, because the spec's EXE section is Windows-shaped. The icon folder went
into one and not the other, so a Mac build came out with no icons at all.
Anything new that has to ship goes in both places.

**The left column measures itself.** It was clipped by thirty-six pixels because
its minimum width was a number typed in once, and icons had since made every
button wider. It now asks the column what it needs and adds the width of the
scrollbar, since the bar appears exactly when the content does not fit and would
otherwise take away the space that made it necessary. Asked again once the
window is shown, when the style has settled.

**The Frame row** puts a border on the wall, over the reprojection and under the
layout map, as a still, a numbered sequence or a movie; the last two loop on
their own length. It is drawn on
the flat frame, so with a viewer chain it travels through the second warp with
the wall. Alpha is untouched: measured, a border changed 21% of the wall's
colour and not one pixel of its transparency.

**Two things that were making it feel slow.** A file dropped on the viewport was
thrown away by the next scrub, which read as the drop failing a moment late; it
now stays until a source is picked. And a handle being dragged warps at quarter
size, with one full-size pass the moment it is let go.

Fullscreen is F11, Esc back, and it remembers which bars were showing rather
than working it out again.

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

---

---

## The gizmo, the keyboard, and one fewer mouse button

**The handles were reading the wrong thing.** Every mouse move asked the map
where the pointer was. Across the flat frame only **70.6%** of neighbouring
pixels move the window coordinate the same way -- the lamellas are angled and
some face the other way -- so on about a third of the wall a drag ran backwards.
And the coordinate jumps: between adjacent pixels it can move **0.0553** of the
window against a median step of **0.000488**, and in the camera view **0.875**,
nearly the whole window in one pixel. Sticking and inversion were the same
fault wearing two faces.

The first repair read the window position **once**, at the grab, and added the
pointer's travel times a fixed rate. That stopped the sticking. It did not make
the handle follow the hand, because the rate was one positive number per axis
for the whole frame -- and see the section below, which replaces it.

A grab that lands in the gap between two lamellas looks a few pixels around for
a covered one, so it still takes hold.

**The middle button is off the number fields.** Windows and most mouse drivers
bind it to autoscroll, and then the press never arrives -- the picture pans and
the field sits unchanged, which reads as a broken field rather than a busy
driver. The wheel does the job and cannot be taken away; dragging the letter
beside a field still works.

**The preview answers the keyboard** once it has been clicked: arrows move the
clip a source pixel, Shift ten, `F` fits, `1` is one to one, `,` and `.` step a
frame. The list is drawn faintly down the bottom left of the viewport from the
same table the key handler reads, so a key that works and a key that is
advertised cannot drift apart.

## Dragging in the preview's own space

**The rate was measured wrong, and it was one number where four were needed.**
The fixed rate above came from the median of the differences between
*neighbouring* pixels of the table. Two things are wrong with that.

It has no sign. On the flat frame the wall folds: reading the median window
coordinate across the 4608 columns gives 0.500 at the left edge, falling to
**0.034** a third of the way in, rising to **0.965** at two thirds, and falling
again -- so on two stretches of the wall the content runs the other way, and a
single positive rate pushed the picture left when the hand went right.

And it measures the wrong thing entirely. Neighbouring differences came out at
exactly **0.000488** -- 1/2048 -- for both axes in both views, which is not the
slope of anything. The camera view is a nearest-neighbour gather out of the
flat frame, so two of its pixels often read the same flat pixel: half the
neighbouring differences are exactly zero and the other half are one whole
step, and the middle of a staircase's treads is a tread, not a gradient. The
true slope across the camera view is **0.000587**, across the flat frame's
middle **0.000732**. The number in use was 17% slow in one place and 50% slow
in another, which is what turned a corner drag into a lunge and a zoom.

**What it does now.** At the moment a handle is taken hold of, three things are
settled and never asked again: which handle, where in the window that handle
is -- computed, not looked up -- and the local slope, four signed numbers read
over a baseline of sixteen pixels rather than between neighbours. The baseline
steps over the staircase; the median still steps over the seams, because a fold
spoils at most one difference in six and the middle value never sees it. Every
mouse move is then the pointer's travel through that slope, and nothing else.

The anchor matters as much as the rate. It used to be the window point under
the cursor at the moment of the grab, which on the flat frame can be on the far
side of a fold from the handle itself -- so the first mouse move threw the
handle to wherever that was. It is now the handle's own position, so the handle
keeps whatever distance it had from the cursor.

Measured over 1560 corner drags and the moves, on a clip framed inside the
window, error being how far the handle ended up from the cursor:

| | before | after |
|---|---|---|
| corner, flat | 23.3 px (p90 29.6) | **6.3 px** (p90 13.4) |
| slide, flat | 29.1 px (p90 38.2) | **3.6 px** (p90 8.7) |
| corner, camera view | 15.7 px (p90 22.0) | **2.2 px** (p90 3.0) |
| slide, camera view | 15.1 px (p90 20.0) | **2.2 px** (p90 4.5) |

Reversals: none, either way round, in any of the four. On a clip fitted to the
whole window the flat corners land on the folds, where the map is stationary
and no scheme can make a handle track a cursor; there the error is 50 px
against the old 159, and reversals while sliding fall from 57 to 5.

Through the widget itself, with real mouse events and the view fitted: a drag
of 140 screen pixels right moves the picture **140.0** pixels right, and 90
down moves it **90.0** down, in both views and all four directions.

**The pivot no longer swallows the middle of the picture.** It sits at the
centre by default and was catching anything within the same 12 screen pixels
the corners get -- so an attempt to slide the clip took hold of the pivot
instead, and moving the pivot deliberately leaves the picture exactly where it
was. The drag looked like nothing happening. The pivot now answers only to the
ring that is drawn for it, 8 screen pixels; the corners keep their 12.

**The pivot's snaps are measured in the preview too.** They were measured in
pixels of the clip, so the same twelve-pixel pull reached halfway across a
small clip and was unreachable on a large one. Now the reach is a distance
under the hand, and lets go at exactly the 12 pixels it advertises.

Rotation takes its angle the same way: the handle is a screen-space affordance,
a fixed arm above the pivot wherever the warp put it, so the angle comes from
where the hand is relative to the pivot in the preview, carried into the window
by the same slope. Left and right turn by the same amount in opposite
directions -- 56.14 degrees either way on the flat frame, 55.86 in the camera
view.

The arrow keys are deliberately **not** in preview space: they move the clip a
pixel of the source frame, which is the unit the panel shows, so four presses
and typing a four are the same act.

### And the flat view was still broken, for a different reason entirely

All of the above was arithmetic on the table. None of it noticed that **the
picture on screen is not the size of the table it is computed from.**

Two things resize the flat preview. The resolution radio buttons, which is
obvious. And the drag itself: `_preview_resolution` drops the warp to a quarter
the moment a handle is taken hold of -- deliberately, since four times less
work lands inside a frame of the mouse and the full one arrives on release.

So on the flat view, the instant a corner was grabbed:

* the pixmap went from 4608 x 1584 to 1152 x 396, so `mapToScene` began
  answering in quarter-frame pixels while every handle was still placed and
  every drag still measured against the full table -- the box was drawn four
  times too far out and the drag ran at a quarter rate;
* `set_pixmap` saw a frame of a different shape, called it a different picture
  and refitted the view, so the zoom jumped at the first touch of a handle and
  jumped back on release.

That is "перепрыгивает и зумится", both words, and it is why the earlier repair
did not help: it was correcting the slope of a map that the cursor was no
longer being measured against.

The camera view never showed either fault. Its gather always writes the viewer
table's own 1710 x 2024 whatever it is fed, so its picture and its map have
always been the same size -- which is exactly why one view looked merely wrong
and the other looked broken.

**Now** `_map_ratio` is the one place that knows the two differ, and every
coordinate crossing between the mouse, the map and the painter goes through it;
the grab radius and the snap pull are given on screen and spent in map pixels.
And a frame whose proportions have not changed keeps its place: the zoom is
multiplied by however much the frame shrank and the scene point that was in the
middle is put back there.

Measured on the real widget with the quarter-size pixmap arriving mid-drag, six
drags of 60 screen pixels per handle, error being how far the handle finished
from the cursor:

| | with the faults | fixed |
|---|---|---|
| flat, top-left corner | 153.3 px (worst 201.4) | **7.5 px** (worst 32.6) |
| flat, bottom-right corner | 374.2 px (worst 448.2) | **5.1 px** (worst 33.1) |
| flat, sliding the clip | | **1.8 px** (worst 6.3) |
| camera view, any corner | 1.2-1.6 px | 1.0-1.6 px |

The camera view is unchanged either way, which is the check that the diagnosis
was right. And with the view zoomed in three times, the picture measures 2982
screen pixels wide before a grab, 2982 while held at quarter size, and 2982
after; the scene point in the middle of the viewport moves by 0.002 of the
frame across both changes.

## The cache has its own button

Filling memory used to be something Play did on the way past, forwards from
wherever the playhead happened to be. Two things were wrong with that.

**Play pushed the warm run further every time.** `_start_warm` began at the
end of what was already held, and `_play_tick` began at the *start of the warm
run* whatever the playhead said. So warming from frame 300 and pressing Play a
second time threw the picture back to 300 instead of carrying on from where
the hand was. That is the jump.

**And there was no way to fill memory without playing.**

So there is now a **cache button at the left end of the transport**, ahead of
everything else in the row, carrying Houdini's `COMMON/memory`. It holds the
range from its first frame on, and pressing it again stops. Play does not warm
at all unless nothing whatever is held -- in which case it starts the same job
rather than being a dead button -- and **Play begins at the playhead**, every
time. On a loop it goes round from the start of what is warm, not from its own
late start, or each pass would play a shorter piece than the last.

### Reading outwards, and why it was taken out again

For a day the warming ran outwards from the playhead instead, in blocks of 48
alternating either side, so the stretch around whatever was being looked at
arrived first. It filled the right frames in the right order. It was also
**2.8 times slower**, and that is what it feels like:

| filling 240 frames of `Remap_30fps.mov` | opens | time | rate |
|---|---|---|---|
| one pass from the first frame | 1 | **1.39 s** | 172.5 frames a second |
| outwards from the middle, in blocks | 6 | 3.95 s | 60.7 frames a second |

Every change of direction is another open and another seek, and a decoder
already running at the right place is the cheapest frame there is. Warming is
something you set going and leave; how soon it is all there beats what order
it arrives in.

The part worth keeping from that day was the diagnosis, not the cure. What
made the playhead matter was never the warming -- it was Play starting at the
wrong end. That is fixed in the player, where it belongs, and the warming is
one reader reading straight through. A check counts the opens: 240 frames,
one open, and a range already held is not read a second time.

## The checks

`tool/tests/`, sixty-six of them, nine seconds, run with `run_tests.bat` or
`python tests/run.py`. Offscreen by default. `remap_tool.json` is copied before
the run and put back after it whatever happens, because the suite drives the
real window and that file holds work rather than preferences.

**Why they are shaped the way they are.** Nothing that has gone seriously wrong
in this program would have tripped a unit test. Every time, the arithmetic was
self-consistent and the fault was between two parts that were each correct on
their own: a table measured in one size and a picture drawn in another, a dark
stylesheet with a light palette, a cache that filled and was never read, a
compensation computed against the pose the last mouse move left behind. So the
checks press, drag, type and drop through the real widgets, and then measure in
the units the person using it can see -- screen pixels, contrast ratios,
milliseconds.

Every check prints its measurement whether it passes or fails. Half of what is
worth knowing about this program is a number, and a suite that prints only "ok"
throws that away on every run.

Each of the faults in this document has a check standing over it now: the alpha
hole, the identity render, the drag in preview space, the quarter-size warp
arriving mid-drag, the refit on grab, the pivot swallowing the middle, the snap
measured in the wrong units, the unreadable tabs, the invisible icons, the
drops going to the wrong target, the cache the scrub ignored, and the Mac
archive that shipped without its icons.

**It found one while it was being written.** The pivot's compensation -- the
arithmetic that keeps the picture still while the pivot moves under it -- was
measured against the pose the *previous* mouse move had left behind rather than
against the pose the drag started from. Which is the same thing for the first
move of a drag and nothing like it for the thirtieth: every move took its
correction from a position that already carried the previous correction, so a
real drag walked the picture 0.147 of the way across the wall while the pivot
was being placed. Every check written before it had used a single synthetic
step and passed. The one that pressed, moved three times and released did not.

**And a second one the day after.** Drop a still on the viewport of a program
with no sequence open -- which is what someone does when they start it and drag
a picture in -- and the transform editor was dead. Not visibly broken: the
numbers moved, the handles moved, nothing on screen changed. `_do_preview` gave
up on `self.current is None` before it ever looked at what had been dropped, so
the drop drew once through `preview_single_frame` and every edit after it hit
that guard and returned. The branch that redraws a dropped still was sitting
right underneath the line that made it unreachable.

Nine checks in the suite drove the editor, and all nine reached it by pointing
the window at a folder, because that is how the suite was written rather than
how the program is always used. There are four more now for the other way in:
a picture dropped on an empty window draws, an edit to its framing reaches the
picture, the handles drag it, and the framing is filed under its name.

Eight more went in with the cache button: that it warms without Play being
touched, that it fills the whole range whatever frame the playhead is on, that
it opens the file exactly once, that asking again does not read what is
already held, that Play begins where the playhead is across three presses at
three different frames, that Play neither pushes the warm run about nor sits
dead on an empty cache, and that the button is to the left of everything else
in the transport.

There are two checks that will fail on a working program, and should: the exe
and the Mac archive are compared against the newest source file, so forgetting
to rebuild is a red line rather than a silent difference between what was
tested and what was handed over.

What they cannot cover: encoding through ffmpeg, since it is not always there;
the 3D scene view, which needs a screen; and anything about how the wall
actually looks, which is a person standing in Zaryadye.

## Undo, and a lesson about the settings file

`Ctrl+Z` and `Ctrl+Shift+Z` over the framing, a separate history per source,
a hundred steps, in memory until the program closes. A step is a settled state,
not a movement: a drag is remembered when the handle is taken hold of, so
crossing two hundred pixels leaves one entry rather than two hundred. Checked
by walking a clip through Fit, Fill, a rotation and Stretch and stepping back
through all four.

The panel edits its transform in place, so by the time it says something
changed the previous numbers are already gone. The window therefore keeps a
copy of the framing as it stood -- `_before_edit`, refreshed whenever the
editor is shown -- and that is what goes onto the stack.

**`remap_tool.json` holds work, not preferences.** Every framing anyone has
aimed lives in it, keyed by file name. It was cleared once during this work by
an assistant tidying away what it took to be its own test entries; six of the
nine were the user's, and they were gone -- no shadow copy on the drive, no
stray duplicate. `save_settings` now keeps two rolling copies beside it,
`.bak` and `.bak2`, which cost a few kilobytes. Do not hand-edit this file.

---

---

## Playback

`Space` warms and plays, `Space` again stops, and the transport row does the
same for the hand already on the mouse: to the beginning, back a frame, play,
on a frame, to the end, and a loop toggle. Beside it, how many frames are warm
and what they cost; on the right, where playback is in seconds.

**The cache holds decoded source frames, before the warp** -- `framecache.py`.
The decode is the slow half: the whole pipeline manages 48 frames a second at
Full while the warp alone manages 135. Keeping the source means a change of
framing costs one warp per frame and nothing else, so a handle can be moved
while it is playing. Where the engine wants 4:2:0 planes the planes are kept
instead, copied out of libav's buffers, and that is a third of the bytes.

Warming runs on its own thread with **one reader for the whole run** -- opening
a container costs more than decoding a frame out of one already open, and the
preview's per-frame reader would have spent its time in `av.open`. Measured:
141 frames of 2000 x 2360 warmed in 0.8 s and cost 1.0 GB; playback then ran at
**36 frames a second at Full**, which carries thirty and not sixty.

The budget is half of free memory, asked afresh each time warming starts, with
a floor of 512 MB and a ceiling of 32 GB. On Windows the free figure is exact;
elsewhere a quarter of the total stands in for it.

The cache is emptied by a change of source, of range or of alpha mode -- the
last because it decides what is decoded. Not by the transform, and not by the
preview resolution.

**Scrubbing reads the cache too, and for a while it did not.** `_do_preview`
went back to the file for every frame, opening a container and seeking, so a
frame sitting in memory still cost **673 ms** to show -- worse the deeper into a
movie it was. Reading the cache instead: **21 ms**.

What that 21 ms is made of, measured at each resolution:

| | warp and readback | to the screen | total |
|---|---|---|---|
| Full 4608 x 1584 | 12.7 ms | 7.6 ms | 20.3 ms |
| Half 2304 x 792 | 4.0 ms | 2.7 ms | 6.7 ms |
| Quarter 1152 x 396 | 2.3 ms | 1.5 ms | 3.8 ms |

Sixteen milliseconds is out of reach at Full and comfortable below it: 29 MB has
to come back off the card and go to the screen for every frame, and that is the
price of the resolution rather than anything left to tune. On a viewport nine
hundred pixels wide, Half looks the same and costs a third.

The timeline draws the warm run as a green line under its track. Playback moves
the handle with the timeline's signals blocked, or every played frame would ask
for a render of the frame just rendered; `_say_frame` therefore exists apart
from the scrub handler so the readout still follows.

One to know: `WarmJob` cannot have a field called `start`. `QThread` already
has one, and shadowing it turns `job.start()` into an integer nobody can call.

---

## Reusing a framing, and one number that was wrong

**The source table takes more than one row.** What the preview shows is the row
the cursor is on rather than the first of the selection -- dragging a selection
upwards would otherwise show its far end. **Apply to the selected sources** puts
the framing on screen onto every other clip highlighted, each stored against its
own shape rather than against the one it was aimed on.

**Named presets** live in `remap_presets.json` beside the project, deliberately
not in `remap_tool.json`: that file is what this window remembers about the work
in front of it, and a preset is meant to travel. A preset holds the transform
and nothing else -- not the alpha mode, not the output format, because a framing
that quietly changed either is a framing nobody would trust. Both the preset row
at the bottom of the Transform tab and a right-click on the table reach the same
actions.

**A clip with no framing of its own takes the last one aimed at a source of the
same aspect**, within half a percent, and falls back to Fit. By aspect and not
by size: 1920 x 1080 and 3840 x 2160 are the same picture, and the numbers are
fractions of the frame, so one lands on the other exactly. The aspect is written
into each saved framing for this, and a framing that is updated moves to the end
of the file so that "the last one" means what it says.

**PNG output is numbered from the source frame.** Rendering 100 to 102 wrote
files 1 to 3, because nothing passed `-start_number` on the encode side; putting
that back together with the source was arithmetic done in someone's head.
Checked: frames 100 to 103 now write `f_00000100` to `f_00000103`.

---

## One dark theme, in two forms

The window was painted by a stylesheet and nothing else, which meant the
**palette stayed whatever the machine was set to**. Two things went wrong on the
back of that. Icons tint themselves to the palette's text colour, so on a light
system they came out dark on a dark window. And a native tab bar paints its own
furniture from the system theme whatever the stylesheet says, so "Source" and
"Transform" were dark text on a dark background.

Now the application sets **Fusion**, an explicit dark palette, and the
stylesheet, all from the same handful of colours near the bottom of `main.py`.
Fusion draws every widget itself, so the palette is obeyed the same way on every
machine. Anything added later that reads `palette()` -- and the icons do -- gets
the right answer without being told.

Rules were missing for the tab bar, menus, tooltips, scrollbars, sliders and the
splitter handle; those are in the stylesheet now.

---

## Decided against

Settled in a grilling session. Everything else agreed there is built and written
up above; what stayed out stayed out on purpose.



**A render queue.** Every clip is looked at before it is rendered, so a queue
would save nothing. Effort goes to what happens before the button.

**Audio.** There is none in the pipeline and none wanted; sound on the wall is
somebody else's job.

**A border in the render.** The Frame row is a ruler, like the layout map. What
goes to the wall is the reprojection and nothing else.

## How the user works

Questions go through the interactive picker, never as a numbered list in prose —
this is written into `~/.claude/CLAUDE.md`. Recommended option first, marked
"(рекомендую)", with the consequence in each option's description.

He renders verification frames himself and reads numbers, not adjectives. Twice
I diagnosed from a screenshot and was wrong both times; measure first.
