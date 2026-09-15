# The checks

Sixty-six of them, run in about nine seconds:

```bash
tool\tests\run_tests.bat
```

or, from `tool`:

```bash
.venv\Scripts\python.exe tests\run.py
```

Everything runs offscreen, so it works over a remote session, in a build step,
or while somebody else is at the machine. `--show` draws the windows instead.
A word on the command line runs only the checks whose name contains it:
`run_tests.bat gizmo`, `run_tests.bat warp`.

**`remap_tool.json` is copied before the run and put back after it, whatever
happens.** That file holds work rather than preferences -- the framing aimed on
every clip is in it -- and the suite drives the real window, which writes to it.

## What it does, and why it is shaped this way

Most of what has gone wrong in this program would not have tripped a unit test.
The arithmetic was self-consistent every time; the fault was between two parts
that were each correct on their own -- a table measured in one size and a
picture drawn in another, a stylesheet without the palette to match it, a cache
that filled and was never read. So the checks press, drag, type and drop
through the real widgets and then measure what came out **in the units the
person using it can see**: screen pixels, contrast ratios, milliseconds.

| group | what it covers |
|---|---|
| the baked tables | every resolution loads at its stated size; the map covers what the wall lights; the wall still folds; the local slope predicts the window to 0.001 |
| the transform | identity is the renderer's old behaviour; forward and inverse undo each other; Fit and Fill stay undistorted; a hundred steps of undo, per source |
| aiming with the mouse | a handle finishes where the cursor does, in both views, ten ways, including with the warp dropping to a quarter mid-drag; the pivot keeps to its ring and moves without moving the picture; rotation is even both ways |
| the transform panel | the wheel and the letter-drag move numbers; typing is read in source pixels; the resets, the link and the flips all change how they look |
| the window | the tab bar and the icons can be read against their own background; a drop lands on the target it was aimed at; the preview answers the keys it advertises; the left column fits |
| a sitting at the program | point it at a folder, pick the clip, aim it, scrub, warm the cache, undo, snapshot -- all through the widgets |
| the warp | a hole in the clip is a hole on the wall, both alpha conventions; the identity renders bit for bit what it always did; the edge modes; the resolutions agree |
| what gets handed over | the exe and the Mac archive are not older than the source in them; the archive carries every module, icon and table, written the way a Mac reads it |

## The launch check

`run_tests.bat --launch` also starts `MatreshkaRemapRenderer.exe`, watches it
for fifteen seconds and closes it. It is off by default because it opens a real
window on whoever's screen is there.

## Adding one

A check is a function with a decorator. It may return a line to print, and it
should: half of what is worth knowing here is a measurement, and a suite that
prints only "ok" throws that away every time it runs.

```python
@check(GROUP, "what this is supposed to be true of")
def something():
    want(condition, "what went wrong, with the number in it")
    return "the measurement, so the next person can see it drift"
```

`want`, `close` and `inside` are the whole vocabulary. A check that raises
anything else is reported as an error, which is also a failure -- a broken
check tells you nothing and should not be allowed to look green.

## Tolerances

Where a check has a number in it, the number is chosen with a wide gap between
passing and the failures that have actually happened. The drag checks allow a
handle to finish twenty screen pixels from the cursor on a sixty pixel pull;
the two faults they exist to catch missed by 150 to 450. A tolerance tight
enough to trip on the geometry of the wall itself -- which really does have
places where a handle cannot track a cursor, because the map is stationary
there -- would be a check that measures the screen rather than the program.
