# Matreshka Remap Renderer

Renders the Zaryadye screen reprojection. **Blender is no longer involved at
run time** — it baked the lookup tables once and is not needed again.

## Why there is no renderer here

The scene turned out to be completely static: an orthographic camera that never
moves, a mesh deformed once by geometry nodes, and a material that is nothing
but an emission fed by one UV map. Every one of the 2665 frames used the same
mapping from output pixel to source pixel — Blender was recomputing an
unchanging projection 2665 times.

So that mapping was baked once (`bake_tables.py`, the only script that still
needs Blender) into three small tables. A frame is now a gather:

    out[x, y] = source(u[x, y], v[x, y]) * coverage[x, y]

| | speed | whole DenGoroda sequence |
|---|---|---|
| Blender | 3.3 fps | ~13 min |
| H.264 at Full (libx264) | 58 fps | ~46 s |
| HEVC at Full (NVENC) | 91 fps | ~29 s |
| H.264 at Half (NVENC) | 121 fps | ~22 s |

Measured against Blender on nine frames spread across the sequence: interior
mean difference 0.5/255, about 1% of pixels off by more than 8, and those sit
in fine texture where the image is squeezed. Silhouette edges differ by ~9/255
because the antialiasing is reconstructed from coverage rather than sampled.

## What it does

Pick a source — an image sequence or an mp4/mov — scrub, and the preview
follows the handle live (17–25 ms a frame). Drag any image onto the preview to
push it through the camera on its own. **Snapshot** writes whatever is on
screen into the `Snapshots` folder at full resolution with straight alpha, no
matter which resolution the preview happens to be showing -- including the
viewer's view, which lands there under a `viewer_` prefix, and the layout map
when it is switched on. The map goes into the colour only: alpha stays the
screen's coverage, so the file still drops onto any background.

**Flipbook** bakes the range the same way, but as what the preview is showing:
flat or the viewer's view, with the layout map painted in if it is switched on.
It is a snapshot with a timeline -- something to send round for review -- and it
names its own file (`..._flipbook`, with a `viewer_` prefix from that mode) so
it can never be mistaken for the file the wall is fed. **RENDER** is untouched
by any of it: no map, no second lookup, whatever the preview happens to show.

Then render the range to H.264, HEVC, a PNG sequence, or ProRes. Controls that
belong to one format are greyed out for the others: bit depth is a PNG idea,
alpha only exists where the container can carry it, and the encoder choice
means nothing for a still image.

## The viewer's view

The dropdown in the corner of the preview switches between **Flat** — the frame
as it goes to the LED wall — and **Viewer**, the same frame seen from
`Zaryadye_ProjectionCamera_9x16_v1`, which is where a person actually stands.

That is not a 3D pass. The camera does not move and neither does the screen, so
which pixel of the frame shows up at each pixel of that view is as fixed as the
reprojection itself, and `bake_geometry.py` bakes it into `viewer_table.npz` the
same way `bake_tables.py` bakes the warp: frame coordinates written into a UV
layer, rendered as colour, coverage from a second pass. The framing is the
camera's own, exactly as set up in the file -- no lens is invented and nothing
is re-fitted, so what you see is what that camera renders.

It is baked at the scene's own 2160x3840 and then trimmed to the screen: the
camera sees mostly black around it, and those pixels would cost 16 bytes of
table and a gather each for a result that is always transparent. What is left
is 1710x2024, 90% of it covered, 1.1 MB in the file and about 11 ms a frame.

The layout map, when it is on, goes through the same lookup, so it warps with
the picture instead of sitting flat on top of it.

## The 3D view

The **3D** switch shows the same frame wrapped back onto the screen where it
actually stands — ten metres across, ten tall, curved — so a warp that looks
right flat can be checked against the surface it was made for. Drag to turn,
wheel to come closer, middle button to slide; **View** returns to the
projection camera's own angle, **Front** and **Top** are fixed looks.

Nothing is re-derived for it. The scene holds the same mesh twice: the screen
in space, and that mesh flattened by geometry nodes for the orthographic camera
to photograph — same vertices, same order. So for vertex *i* the position comes
from the standing copy and its place in the frame is the projection of vertex
*i* of the flat one. No UV layer is consulted and nothing is fitted;
`bake_geometry.py` works it out once into a 16 KB file. Drawing a view costs
about 2 ms.

The renderer is wgpu: **Vulkan on Windows and Linux, Metal on macOS**. Source
frames are uploaded as sRGB textures with a mip chain, so colour decoding and
filtering happen in hardware — that is what closes the gap with Blender in the
minified areas. A machine without a usable GPU falls back to OpenCV on the CPU,
slower and slightly more aliased, but working.

Frames come from and go to ffmpeg, one process each way, running in parallel
with the GPU. Both directions carry 4:2:0 planes rather than RGBA -- a third of
the bytes -- with the conversion done in the shader at one end and the packing
done on the card at the other. H.264 stores 4:2:0 regardless, so the packing
costs nothing that the encoder was not going to do anyway.

Doing the decoding and encoding in-process through PyAV was tried and measured
at 38 fps against 59: two ffmpeg processes genuinely run on separate cores,
while everything inside one interpreter takes turns holding the GIL. PyAV is
used for previews and for reading a movie length, where there is nothing to
parallelise and starting a process would dominate.

## Encoders

**Auto** uses the graphics card's own encoder wherever it can take the job, and
falls back to libx264 otherwise. Two limits shape that choice:

A hardware encoder refuses jobs for reasons written down nowhere we can read:
a width past some ceiling, a quality knob the platform never implemented, a
session another application is holding. Two of those were found the hard way on
two different machines, so the application no longer keeps a table of them --
it asks. Before choosing, it pushes one black frame of the exact output size
through the candidate with the exact flags, into a null output, and reads the
exit code. About 250 ms, once, then remembered.

What that finds in practice:

- **NVENC** takes H.264 up to 4096, so at Full it can only help through HEVC --
  worth 91 fps against 57 at the same file size (9.45 MB against 9.75 for 200
  frames). At Half and Quarter its H.264 works and runs at 121 fps.
- **VideoToolbox** on the Mac this was tested against opens no session at 4608,
  and its H.264 has no constant-quality mode at all. So there both go to the
  CPU encoder at Full, and the hardware takes over at smaller sizes. Note that
  libx265 is far slower than libx264: at Full, H.264 is the practical choice on
  a Mac.

Deliberately not `-allow_sw`: asked to allow software, VideoToolbox answers a
refused session with an encoder slower than libx264, and a render nobody can
wait out is worse than one that says no. The quality settings are chosen so a
hardware-encoded file lands on the same size as libx264 at crf 18, rather than
quietly trading size for speed.

## What the machine needs

Everything Python is inside the executable. ffmpeg is not -- it is large, and
its licence makes shipping a copy inside someone else's binary a question
better left alone. So on the first run the application looks for what it needs
and says what it found: ffmpeg, a GPU wgpu can use, the lookup tables.

If ffmpeg is missing it offers to fetch it. Nothing is installed and nothing
goes on PATH: the binary is lifted out of the archive into an `ffmpeg` folder
beside the application, which is looked in before PATH, and deleting that
folder undoes it. The list only appears again if something required has gone.

| platform | where it comes from |
|---|---|
| Windows | BtbN's FFmpeg-Builds, `latest` release, ~163 MB |
| macOS | evermeet.cx, ~30 MB |
| Linux | not offered -- use the package manager |

## The log

A windowed build has no console: every `print` goes into a void, and an
unhandled exception takes the window with it and leaves nothing behind. That is
fine until something only happens on someone else's machine, and then it is the
whole problem.

So each run writes `Logs\session_<date>_<time>.log` beside the application, and
the **Log** button opens that folder. It holds the machine and the paths, what
the dependency check found, everything the window's own log said, the full
ffmpeg command lines for both processes, whatever ffmpeg complained about, and
any traceback. Every line is flushed as it is written -- the interesting one is
usually the last before something died. The ten most recent sessions are kept.

## Talking to whichever ffmpeg is there

Builds disagree, and the disagreements are not visible until a render dies on
someone else's machine. Two are handled by asking rather than assuming:

- `-vsync 0` was removed in ffmpeg 8 and `-fps_mode passthrough` did not exist
  before 5.1. Which one to use is decided once, by trying the modern spelling
  against a null input and watching the exit code.
- `-nostats`, because the progress line goes to stderr and builds disagree
  about whether `-loglevel error` silences it. Both processes' stderr is read
  by a thread of its own regardless -- an unread pipe is not only evidence
  discarded, it is a process that stops once the pipe fills.

And nothing in the render loop waits without a way out. An encoder can stall as
easily as it can die -- a hardware session that never opens, a disk that stops
-- and a plain `put()` on a full queue would wait for it in the one place the
cancel check cannot reach. Every wait is taken in short steps that re-ask, and
on cancel the processes are killed before anything is joined, because the
writing thread may be stuck on a pipe nobody is reading.

## Safety rails

- Render is blocked while the target file exists — rename or press **+1 version**
- Picking a sequence names the output after it, at the first free version, so a
  name is never handed over already taken. A name typed by hand is left alone.
- Cancel stops the pipeline and deletes the unplayable fragment it left
- Missing lookup tables and a missing ffmpeg are reported at start-up,
  not on the first render, and RENDER stays disabled until they are there
- A PNG sequence goes into its own folder rather than into `OUT` directly

## Layout

Nothing is hard-coded to a drive letter; the app finds the project from its own
location on disk.

```
Matreshka_Remap\
  MatreshkaRemapRenderer.exe     <- built here by tool\build.bat
  ToRemap\                       <- default source folder
  OUT\                           <- default output folder
  Snapshots\                     <- single frames, made on demand
  Logs\                          <- one file per run, last ten kept
  ffmpeg\                        <- only if it was fetched on the first run
  tool\                          <- sources (this folder)
    tables\                      <- the baked lookup tables
```

## Files

| file | role |
|---|---|
| `main.py` | window and wiring |
| `app_jobs.py` | preview renderer, movie probing, the render thread |
| `remap_render.py` | ffmpeg in, warp, ffmpeg out |
| `depends.py` | what the machine needs, and fetching ffmpeg |
| `remap_engine.py` | the wgpu renderer and the OpenCV fallback |
| `scan.py` | sequence and movie scanning |
| `constants.py` | scene names, table lookup, relative project layout |
| `logfile.py` | the session log, and catching what has nowhere else to go |
| `preview3d.py` | the screen in space, drawn with the frame on it |
| `bake_tables.py` | Blender: bakes the lookup tables, once per scene change |
| `bake_geometry.py` | Blender: bakes the screen for the 3D view and the viewer's view |

## Running

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
run.bat
```

## Re-baking, if the scene ever changes

```
blender -b zaryadye_remap_v1.blend --factory-startup -P tool\bake_tables.py -- tool\tables
```

Two passes per resolution: coordinates from a single unjittered sample, so a UV
seam never averages two distant coordinates into a third that means nothing, and
coverage from 256 samples, because a scalar averages fine. Takes seconds.

## Building

`build.bat` produces `..\MatreshkaRemapRenderer.exe` with `Check.png` and the
three tables inside it.
