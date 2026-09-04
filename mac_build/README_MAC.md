# Matreshka Remap Renderer — building on macOS

Renders the Zaryadye screen reprojection. The projection was baked into three
small lookup tables, so nothing here needs Blender — a frame is a lookup, and
the renderer is wgpu, which means Metal on this platform.

The app cannot be cross-compiled from Windows, so this folder holds everything
needed to build it on a Mac. Nothing here is installed system-wide — the whole
build stays inside this folder.

## Requirements

- **macOS** with Python **3.10 or newer** (`python3 --version`)
- Internet access for one `pip install` (PySide6 + PyInstaller, ~150 MB)
- **ffmpeg** on the PATH — it decodes the source and writes the output
- a GPU with Metal (any Mac from the last decade); without one the app falls
  back to the CPU renderer automatically

**Blender is not needed at all.** The reprojection was baked into the three
lookup tables in `tables/`, which ship inside the app.

Build on the machine architecture you intend to run on: an Apple Silicon build
and an Intel build have to be made separately.

## Build

```bash
chmod +x build_mac.sh
./build_mac.sh
```

Takes a few minutes, mostly the download. The result is:

```
dist/MatreshkaRemapRenderer.app
```

If `python3` is not the interpreter you want:

```bash
PYTHON=/opt/homebrew/bin/python3.12 ./build_mac.sh
```

## Running it

Move `MatreshkaRemapRenderer.app` into an **empty folder** and open it. On first
launch it makes that folder into a working project:

- creates `ToRemap/` (source footage) and `OUT/` (renders)
- writes `remap_tool.json` for its own settings

An existing file is never overwritten.

## Gatekeeper: warnings, and the read-only copy problem

Two separate mechanisms, worth telling apart.

**Quarantine and app translocation.** Anything that arrives through a browser,
AirDrop or a zip gets the `com.apple.quarantine` flag. macOS then launches the
app from a random **read-only copy** under
`/private/var/folders/…/AppTranslocation/`. This app finds its project folder
by looking at where it is, so translocated it would try to create `ToRemap/`
and `OUT/` inside that temporary copy. It detects this and says so in the
status bar instead of failing quietly — but it cannot work until the flag is
gone. Two ways out, both free:

```bash
xattr -dr com.apple.quarantine /path/to/MatreshkaRemapRenderer.app
```

or simply **move the .app in Finder** — a user-initiated move clears
translocation.

**The "unidentified developer" / "damaged" warning.** This is about signing,
and there are three levels:

| level | cost | what happens |
|---|---|---|
| ad-hoc (what `build_mac.sh` does by default) | free | runs on the Mac that built it; blocked elsewhere |
| Developer ID signature | $99/year | still warns — since macOS 10.15 a signature alone is not enough |
| Developer ID **+ notarization** | same $99/year | opens anywhere with no warning at all |

If the app never leaves the Mac that built it, the free level is enough: an app
you built locally has no quarantine flag, so Gatekeeper never inspects it.

To sign, and then to notarize, pass credentials to the same script:

```bash
SIGN_IDENTITY="Developer ID Application: Name (TEAMID)" ./build_mac.sh

SIGN_IDENTITY="Developer ID Application: Name (TEAMID)" \
APPLE_ID="you@example.com" TEAM_ID="TEAMID" APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" \
./build_mac.sh
```

`APP_PASSWORD` is an **app-specific password** from appleid.apple.com, not the
Apple ID password. Notarization uploads the app to Apple and takes a few
minutes; `stapler` then writes the ticket into the bundle so it also works
offline. The script prints the `spctl` verdict at the end, which is the honest
answer to "will this open on someone else's Mac".

Signing needs the hardened runtime, which normally blocks the way Python and
PyInstaller load code — `entitlements.plist` here carries exactly the
exceptions that are needed and nothing more. The app is deliberately **not**
sandboxed: it has to read footage anywhere on disk, write renders beside itself
and run ffmpeg as a child process.

## What it does

Point it at a folder of image sequences or an mp4/mov, scrub the timeline — the
preview follows the handle live — and render the range to H.264, a PNG sequence
or ProRes, with alpha where the format allows. Drag a single image onto the
preview to push just that frame through. `Check.png`, the screen layout map,
can be laid over the preview at any opacity to check composition.

ffmpeg decodes on one side and encodes on the other, both in parallel with the
GPU. Cancel stops the pipeline and removes the fragment it left behind.

## One caveat about this project on a Mac

The application is fully portable now — the tables carry the reprojection and
nothing refers to a Windows path any more. What still has to travel is the
footage itself: point the app at wherever the sequences live on that machine.

## Files here

| file | role |
|---|---|
| `main.py` | window, wiring, job state |
| `scan.py` | sequence and movie scanning |
| `app_jobs.py` | preview renderer, movie probing, the render thread |
| `remap_engine.py` | the wgpu renderer (Metal here) and the CPU fallback |
| `remap_render.py` | ffmpeg in, warp, ffmpeg out |
| `constants.py` | table lookup and the relative project layout |
| `Check.png` | layout overlay, 4608×1584 — the same frame as a Full render |
| `remap_tool.json` | default settings, unpacked next to the app |
| `tables/*.npz` | the baked reprojection, bundled into the app |
| `build_mac.sh` | the build |
