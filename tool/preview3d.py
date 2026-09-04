"""The 3D half of the preview: the rendered frame shown on the real screen.

The flat render is what goes to the LEDs; this shows the same pixels wrapped
back onto the screen where it actually stands, so a warp that looks right in
the flat frame can be checked against the curved surface it was made for.

Nothing here re-derives the mapping: bake_geometry.py already worked out, for
every vertex, both where it is in the room and where it sits in the frame.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SHADER = """
struct Camera {
    view_projection: mat4x4<f32>,
    overlay: vec4<f32>,          // x = opacity
};

@group(0) @binding(0) var<uniform> camera: Camera;
@group(0) @binding(1) var frame: texture_2d<f32>;
@group(0) @binding(2) var frame_sampler: sampler;
@group(0) @binding(3) var overlay: texture_2d<f32>;

struct VOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_main(@location(0) position: vec3<f32>,
           @location(1) uv: vec2<f32>) -> VOut {
    var out: VOut;
    out.clip = camera.view_projection * vec4<f32>(position, 1.0);
    // The frame is stored top-down, the baked coordinates are bottom-up.
    out.uv = vec2<f32>(uv.x, 1.0 - uv.y);
    return out;
}

@fragment
fn fs_main(in: VOut) -> @location(0) vec4<f32> {
    var colour = textureSampleLevel(frame, frame_sampler, in.uv, 0.0).rgb;

    // The layout map goes on through the same coordinates the frame uses, so
    // it lands on the surface exactly where it lands on the flat view.
    if (camera.overlay.x > 0.0) {
        let map = textureSampleLevel(overlay, frame_sampler, in.uv, 0.0);
        colour = mix(colour, map.rgb, map.a * camera.overlay.x);
    }
    return vec4<f32>(colour, 1.0);
}
"""


def perspective(fov_y: float, aspect: float, near: float, far: float) -> np.ndarray:
    """Projection for WebGPU's clip space: y up, depth in 0..1."""
    focal = 1.0 / math.tan(fov_y / 2.0)
    matrix = np.zeros((4, 4), dtype=np.float32)
    matrix[0, 0] = focal / aspect
    matrix[1, 1] = focal
    matrix[2, 2] = far / (near - far)
    matrix[2, 3] = far * near / (near - far)
    matrix[3, 2] = -1.0
    return matrix


def look_at(eye: np.ndarray, target: np.ndarray, up: np.ndarray) -> np.ndarray:
    forward = target - eye
    forward = forward / np.linalg.norm(forward)
    right = np.cross(forward, up)
    norm = np.linalg.norm(right)
    if norm < 1e-6:                      # looking straight up or down
        right = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    else:
        right = right / norm
    true_up = np.cross(right, forward)

    matrix = np.eye(4, dtype=np.float32)
    matrix[0, :3], matrix[1, :3], matrix[2, :3] = right, true_up, -forward
    matrix[:3, 3] = -matrix[:3, :3] @ eye
    return matrix


@dataclass
class Orbit:
    """Where the viewer is, in terms a mouse can change."""

    target: np.ndarray
    distance: float
    yaw: float                  # radians, around the vertical axis
    pitch: float                # radians, positive looks down
    fov_y: float
    zoom: float = 1.0           # narrows the view without moving the eye

    def eye(self) -> np.ndarray:
        horizontal = self.distance * math.cos(self.pitch)
        return self.target + np.array([
            horizontal * math.cos(self.yaw),
            horizontal * math.sin(self.yaw),
            self.distance * math.sin(self.pitch),
        ], dtype=np.float32)

    def view_projection(self, aspect: float, radius: float) -> np.ndarray:
        near = max(0.05, self.distance - radius * 2.0)
        far = self.distance + radius * 4.0
        view = look_at(self.eye(), self.target,
                       np.array([0.0, 0.0, 1.0], dtype=np.float32))
        # Zooming narrows the field of view from where the eye already is.
        # Walking the camera closer would change the perspective, which is the
        # one thing this view must not do: it is here to show the projection as
        # it will really look.
        narrowed = 2.0 * math.atan(math.tan(self.fov_y / 2.0) / self.zoom)
        return perspective(narrowed, aspect, near, far) @ view

    def copy(self) -> "Orbit":
        return Orbit(self.target.copy(), self.distance, self.yaw, self.pitch,
                     self.fov_y, self.zoom)


class Geometry:
    """The baked screen: positions, frame coordinates, triangles, a viewpoint."""

    def __init__(self, path: Path) -> None:
        data = np.load(path)
        self.positions = data["positions"].astype(np.float32)
        self.uvs = data["uvs"].astype(np.float32)
        self.triangles = data["triangles"].astype(np.uint32)
        self.fov_y = float(data["fov_y"])
        camera = data["camera_matrix"].astype(np.float32)

        lower, upper = self.positions.min(axis=0), self.positions.max(axis=0)
        self.centre = ((lower + upper) / 2.0).astype(np.float32)
        self.radius = float(np.linalg.norm(upper - lower) / 2.0)

        # The vertex buffer wants position and coordinate interleaved.
        self.vertices = np.hstack([self.positions, self.uvs]).astype(np.float32)
        self.default_orbit = self._orbit_from(camera)

    def _orbit_from(self, camera: np.ndarray) -> Orbit:
        """Turn the baked camera into an orbit around the screen.

        The camera in the scene frames the whole hall, and only the screen is
        wanted here -- but it stays exactly where it is and the view is cropped
        instead. Walking it closer would change the perspective, and this view
        exists precisely to show the perspective someone standing there gets.
        """
        eye = camera[:3, 3].astype(np.float32)
        offset = eye - self.centre
        distance = float(np.linalg.norm(offset))
        yaw = math.atan2(offset[1], offset[0])
        pitch = math.asin(np.clip(offset[2] / max(distance, 1e-6), -1.0, 1.0))

        # No crop and no re-fitting: the camera in the file is framed the way
        # it is on purpose, and the flat Viewer table is baked from exactly
        # this. The wheel is there for anyone who wants a closer look.
        return Orbit(self.centre.copy(), distance, yaw, pitch, self.fov_y)


class Scene3D:
    """Draws the geometry with a frame on it, off screen, through wgpu."""

    def __init__(self, geometry: Geometry) -> None:
        import wgpu

        self.wgpu = wgpu
        self.geometry = geometry
        adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
        if adapter is None:
            raise RuntimeError("no GPU adapter available")
        self.device = adapter.request_device_sync()
        self.adapter_name = adapter.info.get("device", "unknown")

        self.size = (0, 0)
        self.frame_size = (0, 0)
        self._frame_texture = None
        self._targets = None
        self._build_static()
        self._make_placeholder_overlay()

    # -- one-time resources ------------------------------------------------

    def _build_static(self) -> None:
        wgpu, device = self.wgpu, self.device

        self.vertex_buffer = device.create_buffer_with_data(
            data=self.geometry.vertices.tobytes(),
            usage=wgpu.BufferUsage.VERTEX)
        self.index_buffer = device.create_buffer_with_data(
            data=self.geometry.triangles.tobytes(),
            usage=wgpu.BufferUsage.INDEX)
        self.index_count = self.geometry.triangles.size

        # A 4x4 matrix and one vec4 of settings.
        self.uniform = device.create_buffer(
            size=80, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST)
        self.overlay_opacity = 0.0
        self._overlay_texture = None
        self._overlay_size = (0, 0)

        shader = device.create_shader_module(code=SHADER)
        self.sampler = device.create_sampler(
            mag_filter="linear", min_filter="linear",
            address_mode_u="clamp-to-edge", address_mode_v="clamp-to-edge")

        self.pipeline = device.create_render_pipeline(
            layout="auto",
            vertex={
                "module": shader, "entry_point": "vs_main",
                "buffers": [{
                    "array_stride": 5 * 4,
                    "step_mode": "vertex",
                    "attributes": [
                        {"format": "float32x3", "offset": 0, "shader_location": 0},
                        {"format": "float32x2", "offset": 12, "shader_location": 1},
                    ],
                }],
            },
            fragment={"module": shader, "entry_point": "fs_main",
                      "targets": [{"format": "rgba8unorm-srgb"}]},
            primitive={"topology": "triangle-list", "cull_mode": "none"},
            depth_stencil={"format": "depth24plus", "depth_write_enabled": True,
                           "depth_compare": "less"},
        )

    def _make_placeholder_overlay(self) -> None:
        """A 1x1 transparent texture, so the binding always has something."""
        self.set_overlay(np.zeros((1, 1, 4), dtype=np.uint8))

    def set_overlay(self, image: np.ndarray) -> None:
        wgpu, device = self.wgpu, self.device
        height, width = image.shape[:2]
        if self._overlay_size != (width, height):
            self._overlay_size = (width, height)
            self._overlay_texture = device.create_texture(
                size=(width, height, 1), format="rgba8unorm-srgb",
                usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST)
            self._overlay_view = self._overlay_texture.create_view()
            self._rebuild_bind_group()
        device.queue.write_texture(
            {"texture": self._overlay_texture},
            np.ascontiguousarray(image),
            {"bytes_per_row": width * 4, "rows_per_image": height},
            (width, height, 1))

    def set_overlay_opacity(self, opacity: float) -> None:
        self.overlay_opacity = float(max(0.0, min(1.0, opacity)))

    def _rebuild_bind_group(self) -> None:
        if self._frame_texture is None or self._overlay_texture is None:
            return
        self.bind_group = self.device.create_bind_group(
            layout=self.pipeline.get_bind_group_layout(0),
            entries=[
                {"binding": 0, "resource": {"buffer": self.uniform,
                                            "offset": 0, "size": 80}},
                {"binding": 1, "resource": self._frame_view},
                {"binding": 2, "resource": self.sampler},
                {"binding": 3, "resource": self._overlay_view},
            ])

    def _ensure_targets(self, width: int, height: int) -> None:
        if self.size == (width, height):
            return
        wgpu, device = self.wgpu, self.device
        self.size = (width, height)

        self.colour = device.create_texture(
            size=(width, height, 1), format="rgba8unorm-srgb",
            usage=wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC)
        self.colour_view = self.colour.create_view()
        self.depth = device.create_texture(
            size=(width, height, 1), format="depth24plus",
            usage=wgpu.TextureUsage.RENDER_ATTACHMENT)
        self.depth_view = self.depth.create_view()

        self.row_bytes = -(-width * 4 // 256) * 256
        self.readback = device.create_buffer(
            size=self.row_bytes * height,
            usage=wgpu.BufferUsage.COPY_DST | wgpu.BufferUsage.MAP_READ)

    def set_frame(self, image: np.ndarray) -> None:
        """The flat render that gets shown on the screen surface."""
        wgpu, device = self.wgpu, self.device
        height, width = image.shape[:2]

        if self.frame_size != (width, height):
            self.frame_size = (width, height)
            self._frame_texture = device.create_texture(
                size=(width, height, 1), format="rgba8unorm-srgb",
                usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST)
            self._frame_view = self._frame_texture.create_view()
            self._rebuild_bind_group()

        device.queue.write_texture(
            {"texture": self._frame_texture},
            np.ascontiguousarray(image),
            {"bytes_per_row": width * 4, "rows_per_image": height},
            (width, height, 1))

    # -- per draw ----------------------------------------------------------

    def render(self, width: int, height: int, orbit: Orbit) -> np.ndarray:
        if self._frame_texture is None:
            raise RuntimeError("no frame has been given to show")
        self._ensure_targets(width, height)

        matrix = orbit.view_projection(width / height, self.geometry.radius)
        # WGSL reads matrices column by column; the vec4 follows the matrix.
        settings = np.array([self.overlay_opacity, 0, 0, 0], dtype=np.float32)
        self.device.queue.write_buffer(
            self.uniform, 0,
            np.ascontiguousarray(matrix.T, dtype=np.float32).tobytes()
            + settings.tobytes())

        encoder = self.device.create_command_encoder()
        pass_ = encoder.begin_render_pass(
            color_attachments=[{
                "view": self.colour_view,
                "load_op": "clear", "store_op": "store",
                "clear_value": (0.078, 0.078, 0.078, 1.0),
            }],
            depth_stencil_attachment={
                "view": self.depth_view,
                "depth_load_op": "clear", "depth_store_op": "store",
                "depth_clear_value": 1.0,
            })
        pass_.set_pipeline(self.pipeline)
        pass_.set_bind_group(0, self.bind_group)
        pass_.set_vertex_buffer(0, self.vertex_buffer)
        pass_.set_index_buffer(self.index_buffer, "uint32")
        pass_.draw_indexed(self.index_count)
        pass_.end()

        encoder.copy_texture_to_buffer(
            {"texture": self.colour},
            {"buffer": self.readback, "bytes_per_row": self.row_bytes,
             "rows_per_image": height},
            (width, height, 1))
        self.device.queue.submit([encoder.finish()])

        self.readback.map_sync("read")
        try:
            raw = np.frombuffer(bytes(self.readback.read_mapped()), dtype=np.uint8)
        finally:
            self.readback.unmap()
        return raw.reshape(height, self.row_bytes // 4, 4)[:, :width].copy()
