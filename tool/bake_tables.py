"""Runs inside Blender, once: bakes the reprojection into lookup tables.

    blender -b <blend> --factory-startup -P bake_tables.py -- <output_dir>

The scene is a static orthographic view of a static deformed mesh whose
material is a plain emission fed by one UV map. Nothing in it depends on the
frame. So instead of rendering 2665 frames, we render the *UV coordinates
themselves* once per output resolution: the red and green channels carry u and
v, and alpha carries the coverage that EEVEE's antialiasing produces at the
silhouette.

The result is a float EXR per resolution, premultiplied -- exactly the form the
runtime needs: source position per output pixel, plus how much of that pixel is
covered by the screen at all.
"""
import os
import sys

import bpy
import numpy as np

# Blender does not put the script's folder on the path reliably; be explicit.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import constants as C  # noqa: E402


def say(tag: str, message: str = "") -> None:
    print(f">>>{tag} {message}".rstrip(), flush=True)


def build_uv_material(uv_layer: str) -> bpy.types.Material:
    """A material that emits its own UV coordinates as colour."""
    material = bpy.data.materials.new("__uv_bake__")
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()

    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = uv_layer

    emission = tree.nodes.new("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = 1.0

    output = tree.nodes.new("ShaderNodeOutputMaterial")

    tree.links.new(uv.outputs["UV"], emission.inputs["Color"])
    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def read_exr(exr_path: str):
    """Premultiplied RGBA from a rendered EXR, flipped to top-down."""
    image = bpy.data.images.load(exr_path)
    width, height = image.size
    flat = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    buffer = flat.reshape(height, width, 4)[::-1].copy()  # EXR is bottom-up
    bpy.data.images.remove(image)
    return buffer


def unpremultiply(buffer):
    alpha = buffer[..., 3]
    safe = np.maximum(alpha, 1e-8)
    return (buffer[..., 0] / safe).astype(np.float32),            (buffer[..., 1] / safe).astype(np.float32),            np.clip(alpha, 0.0, 1.0).astype(np.float32)


def combine(sharp_path: str, soft_path: str, npz_path: str) -> None:
    """Exact coordinates from the unfiltered pass, soft coverage from the other.

    Averaging coordinates is wrong wherever the UV jumps -- across a seam the
    mean of two distant coordinates points at neither. So the coordinates come
    from a single unjittered sample per pixel, and only the coverage, which is
    a scalar and averages fine, comes from the heavily sampled pass. Silhouette
    pixels that the sharp pass missed entirely fall back to the soft one.
    """
    u_sharp, v_sharp, cov_sharp = unpremultiply(read_exr(sharp_path))
    u_soft, v_soft, coverage = unpremultiply(read_exr(soft_path))

    fallback = (cov_sharp <= 0.0) & (coverage > 0.0)
    u = np.where(fallback, u_soft, u_sharp)
    v = np.where(fallback, v_soft, v_sharp)

    covered = coverage > 0.0
    if covered.any():
        say("INFO", f"    u {u[covered].min():.4f}..{u[covered].max():.4f}  "
                    f"v {v[covered].min():.4f}..{v[covered].max():.4f}  "
                    f"covered {100.0 * covered.mean():.1f}%  "
                    f"edge px {int(((coverage > 0.001) & (coverage < 0.999)).sum()):,}  "
                    f"borrowed {int(fallback.sum()):,}")

    np.savez_compressed(npz_path, u=u, v=v, coverage=coverage)


def main() -> None:
    out_dir = sys.argv[sys.argv.index("--") + 1:][0]
    os.makedirs(out_dir, exist_ok=True)

    scene = bpy.data.scenes[C.SCENE_NAME]
    camera = bpy.data.objects[C.CAMERA_NAME]
    obj = bpy.data.objects[C.OBJECT_NAME]

    node = bpy.data.materials[C.MATERIAL_NAME].node_tree.nodes[C.TEXTURE_NODE_NAME]
    uv_layer = None
    for link in node.inputs["Vector"].links:
        uv_layer = getattr(link.from_node, "uv_map", None)
    if not uv_layer:
        say("ERROR", "the texture node is not driven by a UV Map node")
        sys.exit(2)
    say("INFO", f"uv layer: {uv_layer}")

    bpy.context.window.scene = scene
    scene.camera = camera

    # Replace every material slot the mesh actually uses with the UV material,
    # so whatever is on screen reports coordinates instead of pixels.
    uv_material = build_uv_material(uv_layer)
    for index in range(len(obj.data.materials)):
        obj.data.materials[index] = uv_material

    render = scene.render
    render.resolution_x, render.resolution_y = C.FULL_RESOLUTION
    render.film_transparent = True          # alpha becomes the coverage
    render.use_file_extension = False
    if hasattr(render.image_settings, "media_type"):
        render.image_settings.media_type = "IMAGE"
    render.image_settings.file_format = "OPEN_EXR"
    render.image_settings.color_mode = "RGBA"
    render.image_settings.color_depth = "32"  # half-float cannot hold these
    render.image_settings.exr_codec = "ZIP"

    # Antialiasing is the whole point of baking: the coverage in alpha has to be
    # smooth, so give EEVEE far more samples than the 2 the scene renders with.
    scene.eevee.taa_render_samples = C.BAKE_SAMPLES

    # No view transform -- the EXR must carry the coordinates unchanged.
    scene.display_settings.display_device = "sRGB"
    scene.view_settings.view_transform = "Raw" if "Raw" in [
        v.identifier for v in
        scene.view_settings.bl_rna.properties["view_transform"].enum_items
    ] else "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0

    scene.frame_start = scene.frame_end = 1
    scene.frame_set(1)

    base_filter = render.filter_size

    for name, percentage in C.RESOLUTION_PRESETS:
        render.resolution_percentage = percentage
        width = C.FULL_RESOLUTION[0] * percentage // 100
        height = C.FULL_RESOLUTION[1] * percentage // 100
        say("INFO", f"baking {name}: {width}x{height}")

        sharp = os.path.join(out_dir, f"_{name.lower()}_sharp.exr")
        soft = os.path.join(out_dir, f"_{name.lower()}_soft.exr")

        # Pass 1: one sample, no reconstruction filter -> untouched coordinates.
        scene.eevee.taa_render_samples = 1
        render.filter_size = 0.01
        render.filepath = sharp
        bpy.ops.render.render(write_still=True, scene=scene.name)

        # Pass 2: many samples, normal filter -> the coverage used for edges.
        scene.eevee.taa_render_samples = C.BAKE_SAMPLES
        render.filter_size = base_filter
        render.filepath = soft
        bpy.ops.render.render(write_still=True, scene=scene.name)

        combine(sharp, soft, os.path.join(out_dir, f"table_{name.lower()}.npz"))
        for temp in (sharp, soft):
            try:
                os.remove(temp)
            except OSError:
                pass

    say("DONE", out_dir)


if __name__ == "__main__":
    main()
