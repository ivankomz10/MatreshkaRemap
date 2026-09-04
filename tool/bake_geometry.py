"""Runs inside Blender, once: bakes the screen as a 3D object for the preview.

    blender -b <blend> --factory-startup -P bake_geometry.py -- <output_dir>

The scene holds the same mesh twice: SCREEN_BOTTOM_Main is the screen where it
really stands, and SCREEN_BOTTOM_Remaped is that mesh flattened by geometry
nodes so an orthographic camera can photograph it. Same vertex count, same
order, vertex for vertex.

That is what makes the 3D preview exact and free of any unwrapping: for vertex
i the position comes from the standing mesh, and the place it occupies in the
rendered frame is the orthographic projection of vertex i of the flat one. No
UV layer is consulted and nothing is approximated.
"""
import os
import sys

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import constants as C  # noqa: E402

SCREEN_IN_SPACE = "SCREEN_BOTTOM_Main"
PREVIEW_CAMERA = "Zaryadye_ProjectionCamera_9x16_v1"

# The viewer's view baked as a table: for every pixel of it, which pixel of the
# rendered frame is showing there. The framing is the camera's own, exactly as
# set up in the file -- nothing is cropped or re-fitted here, so what the table
# gives back is what that camera renders.
VIEWER_HEIGHT = 3840          # the scene aspect is kept; this is preview detail
VIEWER_SAMPLES = 256
VIEWER_MARGIN = 8             # pixels of black kept around the screen


def say(tag: str, message: str = "") -> None:
    print(f">>>{tag} {message}".rstrip(), flush=True)


def fail(message: str) -> None:
    say("ERROR", message)
    sys.exit(2)


def evaluated_mesh(obj, depsgraph):
    return obj.evaluated_get(depsgraph).to_mesh()


def bake_viewer_table(scene, standing, camera, frame_uvs, out_dir: str) -> None:
    """Where each pixel of the viewer's view lands in the rendered frame.

    The camera does not move and neither does the screen, so this mapping is as
    fixed as the reprojection itself -- which means the viewer's view can be a
    lookup exactly like the render is, instead of a 3D pass.

    The frame coordinates are written into a temporary UV layer and rendered as
    colour, the same trick bake_tables.py uses, so the answer comes out of the
    same rasteriser that will be judged against it.
    """
    import bake_tables

    mesh = standing.data
    layer = mesh.uv_layers.get("__frame_uv__") or mesh.uv_layers.new(name="__frame_uv__")
    for loop in mesh.loops:
        layer.data[loop.index].uv = frame_uvs[loop.vertex_index]

    original_materials = [slot for slot in mesh.materials]
    uv_material = bake_tables.build_uv_material("__frame_uv__")
    for index in range(len(mesh.materials)):
        mesh.materials[index] = uv_material

    render = scene.render
    previous = (scene.camera, render.resolution_x, render.resolution_y,
                render.resolution_percentage, render.filter_size,
                scene.eevee.taa_render_samples)

    # Keep the shape of the frame the file is set up for, just smaller: this is
    # a preview, and the table costs memory in proportion to its pixels.
    aspect = render.resolution_x / render.resolution_y
    height = VIEWER_HEIGHT
    width = max(2, int(round(height * aspect)) // 2 * 2)

    scene.camera = camera
    render.resolution_x, render.resolution_y = width, height
    render.resolution_percentage = 100
    render.film_transparent = True
    render.use_file_extension = False
    if hasattr(render.image_settings, "media_type"):
        render.image_settings.media_type = "IMAGE"
    render.image_settings.file_format = "OPEN_EXR"
    render.image_settings.color_mode = "RGBA"
    render.image_settings.color_depth = "32"
    render.image_settings.exr_codec = "ZIP"

    say("INFO", f"viewer table: {width}x{height} from {camera.name}, "
                f"lens {camera.data.lens:.1f} mm as set up in the file")

    sharp = os.path.join(out_dir, "_viewer_sharp.exr")
    soft = os.path.join(out_dir, "_viewer_soft.exr")

    scene.eevee.taa_render_samples = 1
    render.filter_size = 0.01
    render.filepath = sharp
    bpy.ops.render.render(write_still=True, scene=scene.name)

    scene.eevee.taa_render_samples = VIEWER_SAMPLES
    render.filter_size = previous[4]
    render.filepath = soft
    bpy.ops.render.render(write_still=True, scene=scene.name)

    table = os.path.join(out_dir, "viewer_table.npz")
    bake_tables.combine(sharp, soft, table)
    crop_to_screen(table)
    for temp in (sharp, soft):
        try:
            os.remove(temp)
        except OSError:
            pass

    for index, material in enumerate(original_materials):
        mesh.materials[index] = material
    (scene.camera, render.resolution_x, render.resolution_y,
     render.resolution_percentage, render.filter_size,
     scene.eevee.taa_render_samples) = previous


def crop_to_screen(path: str, margin: int = VIEWER_MARGIN) -> None:
    """Throw away the black the camera sees around the screen.

    Most of this frame is empty -- the screen fills about two fifths of it --
    and every one of those pixels would otherwise cost 16 bytes of table and a
    gather per frame for a result that is always transparent. Cropping is safe
    because nothing outside the covered area can ever become visible: the
    mapping is fixed.
    """
    data = np.load(path)
    u, v, coverage = data["u"], data["v"], data["coverage"]
    height, width = coverage.shape

    rows = np.flatnonzero(coverage.any(axis=1))
    cols = np.flatnonzero(coverage.any(axis=0))
    if not len(rows) or not len(cols):
        say("INFO", "    nothing covered -- left uncropped")
        return

    top = max(0, int(rows[0]) - margin)
    bottom = min(height, int(rows[-1]) + 1 + margin)
    left = max(0, int(cols[0]) - margin)
    right = min(width, int(cols[-1]) + 1 + margin)
    bottom -= (bottom - top) % 2
    right -= (right - left) % 2

    box = (slice(top, bottom), slice(left, right))
    np.savez_compressed(path, u=u[box], v=v[box], coverage=coverage[box])
    say("INFO", f"    cropped {width}x{height} -> {right - left}x{bottom - top} "
                f"({100.0 * (right - left) * (bottom - top) / (width * height):.0f}% "
                f"of the frame, {os.path.getsize(path) / 1e6:.1f} MB)")


def main() -> None:
    out_dir = sys.argv[sys.argv.index("--") + 1:][0]
    os.makedirs(out_dir, exist_ok=True)

    scene = bpy.data.scenes[C.SCENE_NAME]
    bpy.context.window.scene = scene

    standing = bpy.data.objects.get(SCREEN_IN_SPACE)
    flat = bpy.data.objects.get(C.OBJECT_NAME)
    render_camera = bpy.data.objects.get(C.CAMERA_NAME)
    view_camera = bpy.data.objects.get(PREVIEW_CAMERA)
    for obj, label in ((standing, SCREEN_IN_SPACE), (flat, C.OBJECT_NAME),
                       (render_camera, C.CAMERA_NAME), (view_camera, PREVIEW_CAMERA)):
        if obj is None:
            fail(f"{label} not found in this file")

    if standing.data.name != flat.data.name:
        say("INFO", f"note: different mesh datablocks "
                    f"({standing.data.name} / {flat.data.name})")

    depsgraph = bpy.context.evaluated_depsgraph_get()
    standing_mesh = evaluated_mesh(standing, depsgraph)
    flat_mesh = evaluated_mesh(flat, depsgraph)

    if len(standing_mesh.vertices) != len(flat_mesh.vertices):
        fail(f"the two meshes disagree: {len(standing_mesh.vertices)} vertices "
             f"against {len(flat_mesh.vertices)} -- the vertex-for-vertex "
             f"correspondence this depends on does not hold")

    count = len(standing_mesh.vertices)

    positions = np.empty((count, 3), dtype=np.float32)
    for index, vertex in enumerate(standing_mesh.vertices):
        positions[index] = standing.matrix_world @ vertex.co

    # Where each vertex lands in the rendered frame: the same orthographic
    # projection the render itself uses, applied to the flattened twin.
    #
    # world_to_camera_view normalises against the scene's own resolution, so
    # that has to be the resolution the reprojection is rendered at -- not
    # whatever the file happens to be set to for some other camera. Getting
    # this wrong silently squashes every coordinate.
    uvs = np.empty((count, 2), dtype=np.float32)
    saved = (scene.camera, scene.render.resolution_x, scene.render.resolution_y,
             scene.render.resolution_percentage)
    scene.camera = render_camera
    scene.render.resolution_x, scene.render.resolution_y = C.FULL_RESOLUTION
    scene.render.resolution_percentage = 100
    for index, vertex in enumerate(flat_mesh.vertices):
        projected = world_to_camera_view(
            scene, render_camera, flat.matrix_world @ vertex.co)
        uvs[index] = (projected.x, projected.y)
    (scene.camera, scene.render.resolution_x, scene.render.resolution_y,
     scene.render.resolution_percentage) = saved

    standing_mesh.calc_loop_triangles()
    triangles = np.empty((len(standing_mesh.loop_triangles), 3), dtype=np.uint32)
    for index, triangle in enumerate(standing_mesh.loop_triangles):
        triangles[index] = triangle.vertices

    inside = ((uvs >= -0.001) & (uvs <= 1.001)).all(axis=1)
    say("INFO", f"{count} vertices, {len(triangles)} triangles")
    say("INFO", f"    u {uvs[:, 0].min():.3f}..{uvs[:, 0].max():.3f}  "
                f"v {uvs[:, 1].min():.3f}..{uvs[:, 1].max():.3f}  "
                f"inside the frame: {100.0 * inside.mean():.1f}%")
    say("INFO", f"    world x {positions[:, 0].min():.2f}..{positions[:, 0].max():.2f}  "
                f"y {positions[:, 1].min():.2f}..{positions[:, 1].max():.2f}  "
                f"z {positions[:, 2].min():.2f}..{positions[:, 2].max():.2f}")

    # The viewpoint to open on. Stored as the camera's own transform plus its
    # field of view; the application frames the screen from there itself.
    matrix = np.array(view_camera.matrix_world, dtype=np.float32)
    lens = view_camera.data
    sensor = lens.sensor_height if lens.sensor_fit == "VERTICAL" else lens.sensor_width
    fov_y = 2.0 * np.arctan(0.5 * lens.sensor_height / lens.lens)
    say("INFO", f"    camera {PREVIEW_CAMERA}: lens {lens.lens:.1f}mm  "
                f"sensor {lens.sensor_width:.1f}x{lens.sensor_height:.1f} ({lens.sensor_fit})  "
                f"fov_y {np.degrees(fov_y):.1f} deg")

    bake_viewer_table(scene, standing, view_camera, uvs, out_dir)

    path = os.path.join(out_dir, "screen_geometry.npz")
    np.savez_compressed(
        path,
        positions=positions,
        uvs=uvs,
        triangles=triangles,
        camera_matrix=matrix,
        fov_y=np.float32(fov_y),
        sensor=np.float32(sensor),
    )
    say("DONE", path)


if __name__ == "__main__":
    main()
