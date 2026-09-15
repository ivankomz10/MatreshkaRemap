"""The renderer that replaced Blender: a static lookup applied per frame.

The scene never changes, so the mapping from output pixel to source pixel was
baked once (see bake_tables.py). All that is left per frame is a gather:

    out[x, y] = source(u[x, y], v[x, y]) * coverage[x, y]

Two implementations behind one interface. The GPU one runs through wgpu --
Vulkan on Windows and Linux, Metal on macOS -- and gets sRGB decoding and
mip-mapped filtering from the hardware, which is what makes it match Blender in
the areas where the image is squeezed. The CPU one uses OpenCV and exists so
the tool still works on a machine without a usable GPU.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

import transform

# How a frame's own transparency is to be read. Not a style: getting it wrong
# is visible along every soft edge, dark if premultiplied footage is called
# straight and bright the other way round.
ALPHA_IGNORE = 0
ALPHA_PREMULTIPLIED = 1
ALPHA_STRAIGHT = 2

VERTEX_STAGE = """
struct VOut {
    @builtin(position) pos: vec4<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) index: u32) -> VOut {
    // One oversized triangle covering the target; cheaper than a quad.
    var corners = array<vec2<f32>, 3>(
        vec2<f32>(-1.0, -1.0), vec2<f32>(3.0, -1.0), vec2<f32>(-1.0, 3.0));
    var out: VOut;
    out.pos = vec4<f32>(corners[index], 0.0, 1.0);
    return out;
}

"""

WARP_SHADER = VERTEX_STAGE + """
struct Settings {
    // x premul out, y overlay, z overlay space, w taps
    flags: vec4<f32>,
    // x source alpha: 0 ignore, 1 premultiplied, 2 straight
    // y window aspect, z transform in use, w edge: 0 clear, 1 black, 2 repeat
    more: vec4<f32>,
    xform0: vec4<f32>,   // cos, sin, 1/scale x, 1/scale y
    xform1: vec4<f32>,   // pivot x, pivot y, position x, position y
    xform2: vec4<f32>,   // what the crop keeps: left, top, right, bottom
    xform3: vec4<f32>,   // flip across, flip down, frame plane on, frame fit
    xform4: vec4<f32>,   // frame aspect, output aspect
};
@group(0) @binding(0) var table: texture_2d<f32>;
@group(0) @binding(1) var source: texture_2d<f32>;
@group(0) @binding(2) var source_sampler: sampler;
@group(0) @binding(3) var<uniform> settings: Settings;
@group(0) @binding(4) var overlay: texture_2d<f32>;
@group(0) @binding(5) var frame_plane: texture_2d<f32>;


// The source's own transparency is undone in the space it was applied in.
// After Effects and Fusion weight the eight-bit value, not the light it
// stands for, so dividing after the hardware has linearised gives an edge
// far darker than the one that was authored -- white at half alpha comes
// back as 0.42 rather than 1.0.
fn encoded(c: vec3<f32>) -> vec3<f32> {
    let safe = max(c, vec3<f32>(0.0));
    return select(1.055 * pow(safe, vec3<f32>(1.0 / 2.4)) - 0.055,
                  safe * 12.92, safe <= vec3<f32>(0.0031308));
}

fn linearised(c: vec3<f32>) -> vec3<f32> {
    let safe = max(c, vec3<f32>(0.0));
    return select(pow((safe + 0.055) / 1.055, vec3<f32>(2.4)),
                  safe / 12.92, safe <= vec3<f32>(0.04045));
}

// A point of the window as a point of the source frame -- the inverse of what
// the editor draws with, because a gather asks "where did this pixel come
// from" and never "where does this pixel go".
//
// Rotation has to happen where a circle is round, so both ways through it the
// horizontal is stretched by the window's aspect and put back afterwards.
fn to_source(m: vec2<f32>) -> vec2<f32> {
    if (settings.more.z < 0.5) {
        return m;               // identity, and the same instructions as before
    }
    let aspect = settings.more.y;
    let ax = (m.x - settings.xform1.x - settings.xform1.z) * aspect;
    let ay = m.y - settings.xform1.y - settings.xform1.w;
    let cos = settings.xform0.x;
    let sin = settings.xform0.y;
    let rx = (ax * cos + ay * sin) * settings.xform0.z;
    let ry = (ay * cos - ax * sin) * settings.xform0.w;
    var f = vec2<f32>(rx / aspect + settings.xform1.x, ry + settings.xform1.y);
    if (settings.xform3.x > 0.5) { f.x = 1.0 - f.x; }
    if (settings.xform3.y > 0.5) { f.y = 1.0 - f.y; }
    return f;
}

@fragment
fn fs_main(in: VOut) -> @location(0) vec4<f32> {
    let texel = vec2<i32>(i32(in.pos.x), i32(in.pos.y));
    let entry = textureLoad(table, texel, 0);

    let coverage = entry.z;
    if (coverage <= 0.0) {
        return vec4<f32>(0.0, 0.0, 0.0, 0.0);
    }

    // Blender's UV origin is bottom-left, a texture's is top-left.
    var uv = to_source(vec2<f32>(entry.x, 1.0 - entry.y));

    // The clip's own edge, once it can be moved off the window. Two numbers
    // because the three ways of ending a picture differ in what they touch:
    // clear takes the alpha out, black takes the colour out, repeat takes
    // neither and reads the last row of pixels instead.
    var edge_alpha = 1.0;
    var edge_dark = 1.0;
    if (settings.more.z > 0.5) {
        let lo = settings.xform2.xy;
        let hi = settings.xform2.zw;
        if (settings.more.w > 1.5) {
            uv = clamp(uv, lo, hi);
        } else {
            // How far this output pixel reaches is already baked into the
            // table; the scale is the only thing that changes it. Fading over
            // exactly that distance makes the clip's edge and the wall's edge
            // look like they were drawn by the same hand.
            let size = vec2<f32>(textureDimensions(source, 0));
            let reach = exp2(entry.w);
            let span = max(vec2<f32>(reach * settings.xform0.z / size.x,
                                     reach * settings.xform0.w / size.y),
                           vec2<f32>(1e-6));
            let inside = clamp((uv - lo) / span + 0.5, vec2<f32>(0.0), vec2<f32>(1.0))
                       * clamp((hi - uv) / span + 0.5, vec2<f32>(0.0), vec2<f32>(1.0));
            let cover = inside.x * inside.y;
            if (settings.more.w > 0.5) { edge_dark = cover; } else { edge_alpha = cover; }
        }
    }
    // The level of detail is baked in: the footprint of an output pixel in the
    // source is fixed, so there is nothing to derive at run time.
    var colour = textureSampleLevel(source, source_sampler, uv, entry.w);

    // Four taps instead of one, across the same footprint.
    //
    // The footprint of an output pixel here is 1.0 to 2.0 source pixels, so a
    // single trilinear tap is filtering across a span it only samples once.
    // On content near the source's own pixel grid that shows as crawl. Four
    // taps at the quarter points, each filtered a little tighter to make room
    // for them, is ordinary 2x2 supersampling -- and it is nearly free here,
    // because this shader waits on getting frames in and out, not on sampling.
    //
    // Where the taps go comes from the table itself: the entries either side
    // are where the neighbouring output pixels read from, so the difference is
    // exactly how far one output pixel reaches. Nothing has to be re-baked.
    //
    // 0.25 and 0.8 are measured, not chosen: against the same mapping
    // supersampled 16x, they beat every other pair tried on both the mean
    // error and the worst percentile.
    if (settings.flags.w > 0.0) {
        let across = textureLoad(table, texel + vec2<i32>(1, 0), 0);
        let down = textureLoad(table, texel + vec2<i32>(0, 1), 0);
        // At the last column and row there is no neighbour to difference
        // against; textureLoad answers zero there, which would fling the taps
        // across the whole picture. Those edges keep the single tap.
        if (across.z > 0.0 && down.z > 0.0) {
            // Through the same map as the centre, or the taps would step in
            // the window's directions while the sample sits in the source's.
            let du = (to_source(vec2<f32>(across.x, 1.0 - across.y)) - uv) * 0.25;
            let dv = (to_source(vec2<f32>(down.x, 1.0 - down.y)) - uv) * 0.25;
            let lod = max(entry.w - 0.8, 0.0);
            colour = 0.25 * (
                textureSampleLevel(source, source_sampler, uv - du - dv, lod)
              + textureSampleLevel(source, source_sampler, uv + du - dv, lod)
              + textureSampleLevel(source, source_sampler, uv - du + dv, lod)
              + textureSampleLevel(source, source_sampler, uv + du + dv, lod));
        }
    }

    // The frame may carry transparency of its own -- a matte rendered out of
    // After Effects, a plate with a hole in it. The table's coverage says
    // where the wall is; this says where the picture is. Both have to hold.
    var rgb = colour.rgb;
    var source_alpha = 1.0;
    if (settings.more.x > 0.5) {
        source_alpha = colour.a;
        if (settings.more.x < 1.5 && source_alpha > 0.0) {
            // Premultiplied: take the weighting back out before anything else
            // reads the colour, so what follows works on the authored value.
            rgb = linearised(encoded(rgb) / max(source_alpha, 1.0 / 255.0));
        }
    }
    // Before the layout map is drawn: the map is a ruler over the finished
    // frame and has no business being darkened by where the clip ends.
    rgb = rgb * edge_dark;

    // A border drawn on the wall: over the reprojection, under the layout map.
    // It sits in the finished frame rather than in the source, so it does not
    // travel with the clip -- the frame belongs to the screen, the picture
    // moves inside it.
    if (settings.xform3.z > 0.0) {
        let shown = vec2<f32>(textureDimensions(table));
        var deco_uv = in.pos.xy / shown;
        if (settings.xform3.w > 0.5) {
            // Fit: the border keeps its own shape and the rest is left alone.
            let ratio = settings.xform4.x / max(settings.xform4.y, 1e-6);
            if (ratio > 1.0) {
                deco_uv.y = (deco_uv.y - 0.5) * ratio + 0.5;
            } else {
                deco_uv.x = (deco_uv.x - 0.5) / max(ratio, 1e-6) + 0.5;
            }
        }
        if (deco_uv.x >= 0.0 && deco_uv.x <= 1.0
            && deco_uv.y >= 0.0 && deco_uv.y <= 1.0) {
            let deco = textureSampleLevel(frame_plane, source_sampler, deco_uv, 0.0);
            rgb = mix(rgb, deco.rgb, deco.a);
        }
    }

    // Where the layout map is drawn decides where it is read. A map drawn in
    // the space being sampled (settings.flags.z = 0) is read at the same coordinates
    // as the picture, so it follows the warp; one drawn on the finished frame
    // (settings.flags.z = 1) is read where the fragment sits, so it stays flat.
    if (settings.flags.y > 0.0) {
        var map_uv = uv;
        if (settings.flags.z > 0.0) {
            map_uv = in.pos.xy / vec2<f32>(textureDimensions(table));
        }
        let map = textureSampleLevel(overlay, source_sampler, map_uv, 0.0);
        rgb = mix(rgb, map.rgb, map.a * settings.flags.y);
    }

    // Premultiplied, so dropping alpha gives exactly a composite over black;
    // straight alpha is what PNG and ProRes 4444 want instead. Either way the
    // alpha written is both transparencies at once.
    let alpha = coverage * source_alpha * edge_alpha;
    let weight = mix(1.0, alpha, settings.flags.x);
    return vec4<f32>(rgb * weight, alpha);
}

"""

YUV_SHADER = VERTEX_STAGE + """
@group(0) @binding(0) var plane_y: texture_2d<f32>;
@group(0) @binding(1) var plane_u: texture_2d<f32>;
@group(0) @binding(2) var plane_v: texture_2d<f32>;
@group(0) @binding(3) var plane_sampler: sampler;

// 4:2:0 planes come in at a third of the bytes of RGBA, which is the whole
// point: the pipe between ffmpeg and here was the bottleneck, not the maths.
// ffmpeg is asked for full-range BT.601 regardless of what the source was, so
// one fixed conversion is correct for both JPEG sequences and H.264 movies.
@fragment
fn fs_yuv(in: VOut) -> @location(0) vec4<f32> {
    let size = vec2<f32>(textureDimensions(plane_y, 0));
    let uv = in.pos.xy / size;

    let y = textureSampleLevel(plane_y, plane_sampler, uv, 0.0).r;
    let u = textureSampleLevel(plane_u, plane_sampler, uv, 0.0).r - 0.5;
    let v = textureSampleLevel(plane_v, plane_sampler, uv, 0.0).r - 0.5;

    let r = y + 1.402 * v;
    let g = y - 0.344136 * u - 0.714136 * v;
    let b = y + 1.772 * u;

    // YUV gives sRGB-encoded values. The target is an sRGB texture and will
    // encode whatever it is handed, so decode here and let it put the same
    // bytes back -- the alternative, a second view of the texture in a raw
    // format, is not supported by this wgpu build.
    let encoded = clamp(vec3<f32>(r, g, b), vec3<f32>(0.0), vec3<f32>(1.0));
    let low = encoded / 12.92;
    let high = pow((encoded + vec3<f32>(0.055)) / 1.055, vec3<f32>(2.4));
    let linear = select(high, low, encoded <= vec3<f32>(0.04045));
    return vec4<f32>(linear, 1.0);
}
"""

PACK_SHADER = VERTEX_STAGE + """
@group(0) @binding(0) var warped: texture_2d<f32>;
@group(0) @binding(1) var warped_sampler: sampler;

fn encode(linear: vec3<f32>) -> vec3<f32> {
    let low = linear * 12.92;
    let high = 1.055 * pow(max(linear, vec3<f32>(0.0)), vec3<f32>(1.0 / 2.4)) - vec3<f32>(0.055);
    return select(high, low, linear <= vec3<f32>(0.0031308));
}

// Limited-range BT.709: what H.264 carries at this size, so the encoder can
// take these planes as they are instead of converting them again.
fn luma(rgb: vec3<f32>) -> f32 {
    return dot(rgb, vec3<f32>(0.2126, 0.7152, 0.0722));
}

fn to_tv_luma(y: f32) -> f32 {
    return (16.0 + y * 219.0) / 255.0;
}

fn to_tv_chroma(c: f32) -> f32 {
    return (128.0 + c * 224.0) / 255.0;
}

@fragment
fn fs_pack_y(in: VOut) -> @location(0) f32 {
    let size = vec2<f32>(textureDimensions(warped, 0));
    let rgb = encode(textureSampleLevel(warped, warped_sampler, in.pos.xy / size, 0.0).rgb);
    return to_tv_luma(luma(rgb));
}

struct Chroma {
    @location(0) u: f32,
    @location(1) v: f32,
};

// Chroma at half resolution: one bilinear tap already averages the 2x2 block,
// which is what 4:2:0 subsampling is.
@fragment
fn fs_pack_uv(in: VOut) -> Chroma {
    let size = vec2<f32>(textureDimensions(warped, 0)) * 0.5;
    let rgb = encode(textureSampleLevel(warped, warped_sampler, in.pos.xy / size, 0.0).rgb);
    let y = luma(rgb);
    var out: Chroma;
    out.u = to_tv_chroma((rgb.b - y) / 1.8556);
    out.v = to_tv_chroma((rgb.r - y) / 1.5748);
    return out;
}
"""

DOWNSAMPLE_SHADER = VERTEX_STAGE + """
@group(0) @binding(0) var mip_source: texture_2d<f32>;
@group(0) @binding(1) var mip_sampler: sampler;

@fragment
fn fs_downsample(in: VOut) -> @location(0) vec4<f32> {
    let size = vec2<f32>(textureDimensions(mip_source, 0));
    let uv = in.pos.xy / (size * 0.5);
    return textureSampleLevel(mip_source, mip_sampler, uv, 0.0);
}
"""


class Table:
    """A baked lookup table for one output resolution."""

    def __init__(self, path: Path) -> None:
        data = np.load(path)
        self.u = data["u"].astype(np.float32)
        self.v = data["v"].astype(np.float32)
        self.coverage = data["coverage"].astype(np.float32)
        self.height, self.width = self.coverage.shape
        self.path = path
        self._packed: dict[tuple[int, int], np.ndarray] = {}

    def packed_for(self, source_width: int, source_height: int) -> np.ndarray:
        """RGBA32F table: u, v, coverage, mip level -- ready to upload.

        The mip level is worked out here rather than in the shader: how far an
        output pixel reaches into the source is a property of the frozen
        geometry, not of the frame, so it is the same for every frame and only
        depends on how big the source happens to be.
        """
        key = (source_width, source_height)
        cached = self._packed.get(key)
        if cached is not None:
            return cached

        x = self.u * source_width
        y = self.v * source_height
        dx_col, dx_row = np.gradient(x)
        dy_col, dy_row = np.gradient(y)
        footprint = np.maximum(np.hypot(dx_row, dy_row), np.hypot(dx_col, dy_col))
        lod = np.log2(np.maximum(footprint, 1.0)).astype(np.float32)

        packed = np.empty((self.height, self.width, 4), dtype=np.float32)
        packed[..., 0] = self.u
        packed[..., 1] = self.v
        packed[..., 2] = self.coverage
        packed[..., 3] = lod
        self._packed[key] = packed
        return packed

    @property
    def covered_fraction(self) -> float:
        return float((self.coverage > 0).mean())


def mip_count(width: int, height: int) -> int:
    return max(1, int(np.floor(np.log2(max(width, height)))) + 1)


class GpuRemapper:
    """wgpu implementation. Raises RuntimeError when no adapter is available."""

    name = "GPU"

    def __init__(self, table: Table, source_size: tuple[int, int]) -> None:
        import wgpu

        self.wgpu = wgpu
        adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
        if adapter is None:
            raise RuntimeError("no GPU adapter available")
        self.adapter_name = adapter.info.get("device", "unknown")
        self.backend = adapter.info.get("backend_type", "unknown")
        self.device = adapter.request_device_sync()

        self.table = table
        self.source_width, self.source_height = source_size
        self.width, self.height = table.width, table.height
        self.levels = mip_count(self.source_width, self.source_height)
        self._premultiplied = True
        self._source_alpha = ALPHA_IGNORE
        self._transform = None
        # Planes cost a third of RGBA over the pipe, which is the pipeline's
        # limit -- but 4:2:0 has nowhere to put an alpha channel. Frames whose
        # own transparency counts have to come across whole.
        self.wants_yuv = True

        self._build()

    # -- setup -------------------------------------------------------------

    def _build(self) -> None:
        wgpu, device = self.wgpu, self.device

        self.source_texture = device.create_texture(
            size=(self.source_width, self.source_height, 1),
            format="rgba8unorm-srgb",
            usage=(wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST
                   | wgpu.TextureUsage.RENDER_ATTACHMENT),
            mip_level_count=self.levels,
        )

        # The three 4:2:0 planes ffmpeg hands over.
        chroma = ((self.source_width + 1) // 2, (self.source_height + 1) // 2)
        plane_usage = wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST
        self.plane_sizes = [
            (self.source_width, self.source_height), chroma, chroma,
        ]
        self.planes = [
            device.create_texture(size=(w, h, 1), format="r8unorm", usage=plane_usage)
            for w, h in self.plane_sizes
        ]
        self.plane_bytes = [w * h for w, h in self.plane_sizes]
        self.frame_bytes_yuv = sum(self.plane_bytes)
        self.table_texture = device.create_texture(
            size=(self.width, self.height, 1),
            format="rgba32float",
            usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST,
        )
        device.queue.write_texture(
            {"texture": self.table_texture},
            self.table.packed_for(self.source_width, self.source_height).tobytes(),
            {"bytes_per_row": self.width * 16, "rows_per_image": self.height},
            (self.width, self.height, 1),
        )

        # Two of everything, used alternately: while the GPU works on one
        # frame the CPU reads the previous one back. Waiting on a mapped buffer
        # blocks the interpreter, so if that wait lands on work the GPU has not
        # even started, decoding and encoding stall behind it too.
        # A texture copies into a buffer only on 256-byte rows, which a width
        # like 1080 does not give. Pad the rows and strip the padding on the
        # way out; at the render resolutions nothing needs padding at all.
        self.frame_row = self.width * 4
        self.row_bytes = -(-self.frame_row // 256) * 256
        self.rgba_padded = self.row_bytes != self.frame_row
        self.targets = [
            device.create_texture(
                size=(self.width, self.height, 1),
                format="rgba8unorm-srgb",
                usage=(wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC
                       | wgpu.TextureUsage.TEXTURE_BINDING),
            )
            for _ in range(2)
        ]
        self.target_views = [texture.create_view() for texture in self.targets]
        self.readbacks = [
            device.create_buffer(
                size=self.row_bytes * self.height,
                usage=wgpu.BufferUsage.COPY_DST | wgpu.BufferUsage.MAP_READ,
            )
            for _ in range(2)
        ]
        self.slot = 0
        self.pending = None          # slot holding a frame not yet read back

        # The output side has the same problem the input side had: RGBA is
        # three times the bytes of 4:2:0, and moving them was the cost. When
        # the destination is H.264 the planes are what ffmpeg wants anyway, so
        # pack them on the GPU and read back a third as much.
        chroma_out = ((self.width + 1) // 2, (self.height + 1) // 2)
        plane_usage = (wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC
                       | wgpu.TextureUsage.TEXTURE_BINDING)
        self.out_plane_sizes = [(self.width, self.height), chroma_out, chroma_out]
        self.out_planes = [
            device.create_texture(size=(w, h, 1), format="r8unorm", usage=plane_usage)
            for w, h in self.out_plane_sizes
        ]
        self.out_plane_views = [texture.create_view() for texture in self.out_planes]
        self.out_plane_bytes = [w * h for w, h in self.out_plane_sizes]
        self.out_bytes_yuv = sum(self.out_plane_bytes)

        # Copying a texture into a buffer needs rows on a 256-byte boundary,
        # which a 1152-wide chroma plane is not. Pad the rows in the buffer and
        # strip the padding on the way out; at Full nothing needs padding at
        # all and the readback is handed over untouched.
        self.out_strides = [-(-width // 256) * 256 for width, _ in self.out_plane_sizes]
        self.out_padded_bytes = [
            stride * height
            for stride, (_, height) in zip(self.out_strides, self.out_plane_sizes)
        ]
        self.yuv_padded = any(
            stride != width
            for stride, (width, _) in zip(self.out_strides, self.out_plane_sizes)
        )

        self.yuv_readbacks = [
            device.create_buffer(
                size=sum(self.out_padded_bytes),
                usage=wgpu.BufferUsage.COPY_DST | wgpu.BufferUsage.MAP_READ,
            )
            for _ in range(2)
        ]
        self.output_yuv = False

        warp_shader = device.create_shader_module(code=WARP_SHADER)
        mip_shader = device.create_shader_module(code=DOWNSAMPLE_SHADER)
        yuv_shader = device.create_shader_module(code=YUV_SHADER)
        self.sampler = device.create_sampler(
            mag_filter="linear",
            min_filter="linear",
            mipmap_filter="linear",
            address_mode_u="clamp-to-edge",
            address_mode_v="clamp-to-edge",
        )

        self.pipeline = device.create_render_pipeline(
            layout="auto",
            vertex={"module": warp_shader, "entry_point": "vs_main"},
            fragment={
                "module": warp_shader,
                "entry_point": "fs_main",
                "targets": [{"format": "rgba8unorm-srgb"}],
            },
            primitive={"topology": "triangle-list"},
        )
        self.downsample_pipeline = device.create_render_pipeline(
            layout="auto",
            vertex={"module": mip_shader, "entry_point": "vs_main"},
            fragment={
                "module": mip_shader,
                "entry_point": "fs_downsample",
                "targets": [{"format": "rgba8unorm-srgb"}],
            },
            primitive={"topology": "triangle-list"},
        )

        self.flags = device.create_buffer(
            size=112,
            usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST,
        )
        self.set_premultiplied(self._premultiplied)

        self.yuv_pipeline = device.create_render_pipeline(
            layout="auto",
            vertex={"module": yuv_shader, "entry_point": "vs_main"},
            fragment={
                "module": yuv_shader,
                "entry_point": "fs_yuv",
                "targets": [{"format": "rgba8unorm-srgb"}],
            },
            primitive={"topology": "triangle-list"},
        )
        self.yuv_bind_group = device.create_bind_group(
            layout=self.yuv_pipeline.get_bind_group_layout(0),
            entries=[
                {"binding": 0, "resource": self.planes[0].create_view()},
                {"binding": 1, "resource": self.planes[1].create_view()},
                {"binding": 2, "resource": self.planes[2].create_view()},
                {"binding": 3, "resource": self.sampler},
            ],
        )

        pack_shader = device.create_shader_module(code=PACK_SHADER)
        self.pack_y_pipeline = device.create_render_pipeline(
            layout="auto",
            vertex={"module": pack_shader, "entry_point": "vs_main"},
            fragment={"module": pack_shader, "entry_point": "fs_pack_y",
                      "targets": [{"format": "r8unorm"}]},
            primitive={"topology": "triangle-list"},
        )
        self.pack_uv_pipeline = device.create_render_pipeline(
            layout="auto",
            vertex={"module": pack_shader, "entry_point": "vs_main"},
            fragment={"module": pack_shader, "entry_point": "fs_pack_uv",
                      "targets": [{"format": "r8unorm"}, {"format": "r8unorm"}]},
            primitive={"topology": "triangle-list"},
        )
        self.pack_bind_groups = [
            device.create_bind_group(
                layout=pipeline.get_bind_group_layout(0),
                entries=[
                    {"binding": 0, "resource": self.target_views[slot]},
                    {"binding": 1, "resource": self.sampler},
                ],
            )
            for pipeline in (self.pack_y_pipeline, self.pack_uv_pipeline)
            for slot in range(2)
        ]

        # The bind group is built by set_overlay, because the overlay texture
        # is part of it and starts as a transparent 1x1 placeholder.
        self.table_view = self.table_texture.create_view()
        self.source_view = self.source_texture.create_view(
            base_mip_level=0, mip_level_count=self.levels)
        self._overlay_opacity = 0.0
        self._overlay_size = (0, 0)
        self._overlay_texture = None
        self._frame_size = (0, 0)
        self._frame_texture = None
        self._frame_on = False
        self._frame_fit = False
        self._frame_aspect = 1.0
        blank = np.zeros((1, 1, 4), dtype=np.uint8)
        # The frame plane first: the overlay's upload builds the bind group,
        # and a bind group cannot name a texture that does not exist yet.
        self._upload_plane("frame", blank)
        self.set_overlay(blank)

        self.mip_views = [
            self.source_texture.create_view(base_mip_level=level, mip_level_count=1)
            for level in range(self.levels)
        ]
        self.mip_bind_groups = [
            device.create_bind_group(
                layout=self.downsample_pipeline.get_bind_group_layout(0),
                entries=[
                    {"binding": 0, "resource": self.mip_views[level - 1]},
                    {"binding": 1, "resource": self.sampler},
                ],
            )
            for level in range(1, self.levels)
        ]

    def set_output_yuv(self, enabled: bool) -> None:
        """Read frames back as 4:2:0 planes instead of RGBA.

        Only for destinations that cannot carry alpha anyway -- H.264 encodes
        4:2:0 regardless, so packing here costs nothing and saves two thirds of
        the traffic back from the card.
        """
        self.output_yuv = enabled

    @property
    def output_frame_bytes(self) -> int:
        return self.out_bytes_yuv if self.output_yuv else self.row_bytes * self.height

    def set_premultiplied(self, premultiplied: bool) -> None:
        self._premultiplied = premultiplied
        self._write_flags()

    def set_supersample(self, on: bool) -> None:
        """Four taps per output pixel instead of one. See the warp shader."""
        self._supersample = bool(on)
        self._write_flags()

    def _rebuild_bind_group(self) -> None:
        """Both drawn-on planes are part of it, so either one resizing rebuilds."""
        self.bind_group = self.device.create_bind_group(
            layout=self.pipeline.get_bind_group_layout(0),
            entries=[
                {"binding": 0, "resource": self.table_view},
                {"binding": 1, "resource": self.source_view},
                {"binding": 2, "resource": self.sampler},
                {"binding": 3, "resource": {"buffer": self.flags,
                                            "offset": 0, "size": 112}},
                {"binding": 4, "resource": self._overlay_texture.create_view()},
                {"binding": 5, "resource": self._frame_texture.create_view()},
            ])

    def _upload_plane(self, which: str, image: np.ndarray):
        """Put a picture on one of the drawn-on planes, resizing if it has to."""
        wgpu, device = self.wgpu, self.device
        height, width = image.shape[:2]
        texture = getattr(self, f"_{which}_texture", None)
        if getattr(self, f"_{which}_size", None) != (width, height):
            setattr(self, f"_{which}_size", (width, height))
            texture = device.create_texture(
                size=(width, height, 1), format="rgba8unorm-srgb",
                usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST)
            setattr(self, f"_{which}_texture", texture)
            # Only once both planes exist: a bind group has to name every
            # binding, and the first upload of a fresh engine has only one.
            if self._overlay_texture is not None and self._frame_texture is not None:
                self._rebuild_bind_group()
        device.queue.write_texture(
            {"texture": texture}, np.ascontiguousarray(image),
            {"bytes_per_row": width * 4, "rows_per_image": height},
            (width, height, 1))

    def set_overlay(self, image: np.ndarray) -> None:
        """A picture in the same space as the source, drawn on at sampling time."""
        self._upload_plane("overlay", image)

    def set_frame_plane(self, image: np.ndarray | None) -> None:
        """A border drawn on the finished frame, over the picture.

        None switches it off rather than uploading a blank, so a frame nobody
        asked for costs nothing at all.
        """
        self._frame_on = image is not None
        if image is not None:
            height, width = image.shape[:2]
            self._frame_aspect = width / max(height, 1)
            self._upload_plane("frame", image)
        self._write_flags()

    def set_frame_fit(self, fit: bool) -> None:
        """Fit keeps the border's own shape; otherwise it is stretched to fill."""
        self._frame_fit = bool(fit)
        self._write_flags()

    def set_overlay_opacity(self, opacity: float) -> None:
        self._overlay_opacity = float(max(0.0, min(1.0, opacity)))
        self._write_flags()

    def set_overlay_space(self, on_output: bool) -> None:
        """Whether the map is drawn on the finished frame or on the source."""
        self._overlay_on_output = bool(on_output)
        self._write_flags()

    def set_source_alpha(self, mode: int) -> None:
        """Whether the frame's own transparency counts, and how it is stored.

        ALPHA_IGNORE for footage that has none or whose alpha means nothing;
        the other two say which convention the colour was written in, because
        undoing the wrong one shows up along every soft edge.
        """
        self._source_alpha = int(mode)
        self.wants_yuv = self._source_alpha == ALPHA_IGNORE
        self._write_flags()

    def set_transform(self, placement) -> None:
        """Where the clip sits inside the window the tables read from.

        `None`, or anything that says it is the identity, switches the whole
        path off in the shader rather than running a transform that happens to
        change nothing -- so a render made without opening the editor comes out
        as the same bytes it did before there was one.
        """
        self._transform = None if placement is None or placement.is_identity             else placement
        self._write_flags()

    def _write_flags(self) -> None:
        placement = getattr(self, "_transform", None)
        value = np.zeros(28, dtype=np.float32)
        value[0] = 1.0 if self._premultiplied else 0.0
        value[1] = getattr(self, "_overlay_opacity", 0.0)
        value[2] = 1.0 if getattr(self, "_overlay_on_output", False) else 0.0
        value[3] = 1.0 if getattr(self, "_supersample", False) else 0.0
        value[4] = float(getattr(self, "_source_alpha", ALPHA_IGNORE))
        value[5] = transform.SOURCE_ASPECT
        value[6] = 0.0 if placement is None else 1.0
        value[7] = 0.0 if placement is None else float(placement.edge)
        if placement is not None:
            value[8:24] = placement.uniforms()
        value[22] = 1.0 if getattr(self, "_frame_on", False) else 0.0
        value[23] = 1.0 if getattr(self, "_frame_fit", False) else 0.0
        value[24] = getattr(self, "_frame_aspect", 1.0)
        value[25] = self.width / max(self.height, 1)
        self.device.queue.write_buffer(self.flags, 0, value.tobytes())

    # -- per frame ---------------------------------------------------------

    def render_bytes(self, frame) -> bytes:
        """Render one frame, blocking until it is back. Simple, one at a time."""
        self.submit(frame)
        return self.flush()

    def submit(self, frame) -> bytes | None:
        """Queue a frame and return the previous one, or None on the first call.

        This is the pipelined entry point: the caller gets frame N-1 while the
        GPU is still busy with frame N, so the readback wait lands on work that
        has already finished.
        """
        ready = self._drain() if self.pending is not None else None
        self._encode_frame(frame, self.slot)
        self.pending = self.slot
        self.slot ^= 1
        return ready

    def flush(self) -> bytes | None:
        """The frame still on the GPU, if any."""
        return self._drain() if self.pending is not None else None

    def _drain(self) -> bytes:
        readback = (self.yuv_readbacks if self.output_yuv else self.readbacks)[self.pending]
        readback.map_sync("read")
        try:
            if self.output_yuv and self.yuv_padded:
                data = self._strip_padding(readback.read_mapped())
            elif not self.output_yuv and self.rgba_padded:
                rows = np.frombuffer(readback.read_mapped(), dtype=np.uint8)
                data = rows[: self.row_bytes * self.height].reshape(
                    self.height, self.row_bytes)[:, : self.frame_row].tobytes()
            else:
                data = bytes(readback.read_mapped())
        finally:
            readback.unmap()
        self.pending = None
        return data

    def _strip_padding(self, mapped) -> bytes:
        """Drop the alignment padding the copy needed, plane by plane."""
        source = np.frombuffer(mapped, dtype=np.uint8)
        pieces = []
        offset = 0
        for (width, height), stride, padded in zip(
                self.out_plane_sizes, self.out_strides, self.out_padded_bytes):
            rows = source[offset:offset + padded].reshape(height, stride)
            pieces.append(rows[:, :width].tobytes())
            offset += padded
        return b"".join(pieces)

    def _upload_yuv(self, frame) -> None:
        """Three planes, a third of the bytes of RGBA.

        Accepts either one contiguous block or the decoder's own plane views,
        which carry their own row stride -- uploading those directly is what
        avoids repacking a frame that libav already laid out correctly.
        """
        if isinstance(frame, (list, tuple)):
            for texture, plane in zip(self.planes, frame):
                self.device.queue.write_texture(
                    {"texture": texture},
                    plane.data,
                    {"bytes_per_row": plane.stride, "rows_per_image": plane.height},
                    (plane.width, plane.height, 1),
                )
            return

        view = memoryview(frame)
        offset = 0
        for plane, (width, height), size in zip(self.planes, self.plane_sizes,
                                                self.plane_bytes):
            self.device.queue.write_texture(
                {"texture": plane},
                view[offset:offset + size],
                {"bytes_per_row": width, "rows_per_image": height},
                (width, height, 1),
            )
            offset += size

    def _upload_rgba(self, frame) -> None:
        self.device.queue.write_texture(
            {"texture": self.source_texture, "mip_level": 0},
            frame if isinstance(frame, (bytes, bytearray, memoryview)) else frame.tobytes(),
            {"bytes_per_row": self.source_width * 4, "rows_per_image": self.source_height},
            (self.source_width, self.source_height, 1),
        )

    def _encode_frame(self, frame, slot: int) -> None:
        wgpu, device = self.wgpu, self.device

        yuv = (isinstance(frame, (list, tuple))
               or (hasattr(frame, "__len__") and len(frame) == self.frame_bytes_yuv))
        if yuv:
            self._upload_yuv(frame)
        else:
            self._upload_rgba(frame)

        encoder = device.create_command_encoder()

        if yuv:
            pass_ = encoder.begin_render_pass(
                color_attachments=[{
                    "view": self.mip_views[0],
                    "load_op": "clear",
                    "store_op": "store",
                    "clear_value": (0, 0, 0, 0),
                }])
            pass_.set_pipeline(self.yuv_pipeline)
            pass_.set_bind_group(0, self.yuv_bind_group)
            pass_.draw(3)
            pass_.end()

        for level in range(1, self.levels):
            pass_ = encoder.begin_render_pass(
                color_attachments=[{
                    "view": self.mip_views[level],
                    "load_op": "clear",
                    "store_op": "store",
                    "clear_value": (0, 0, 0, 0),
                }])
            pass_.set_pipeline(self.downsample_pipeline)
            pass_.set_bind_group(0, self.mip_bind_groups[level - 1])
            pass_.draw(3)
            pass_.end()

        pass_ = encoder.begin_render_pass(
            color_attachments=[{
                "view": self.target_views[slot],
                "load_op": "clear",
                "store_op": "store",
                "clear_value": (0, 0, 0, 0),
            }])
        pass_.set_pipeline(self.pipeline)
        pass_.set_bind_group(0, self.bind_group)
        pass_.draw(3)
        pass_.end()

        if self.output_yuv:
            luma_pass = encoder.begin_render_pass(
                color_attachments=[{
                    "view": self.out_plane_views[0],
                    "load_op": "clear", "store_op": "store",
                    "clear_value": (0, 0, 0, 0),
                }])
            luma_pass.set_pipeline(self.pack_y_pipeline)
            luma_pass.set_bind_group(0, self.pack_bind_groups[slot])
            luma_pass.draw(3)
            luma_pass.end()

            chroma_pass = encoder.begin_render_pass(
                color_attachments=[
                    {"view": self.out_plane_views[1], "load_op": "clear",
                     "store_op": "store", "clear_value": (0, 0, 0, 0)},
                    {"view": self.out_plane_views[2], "load_op": "clear",
                     "store_op": "store", "clear_value": (0, 0, 0, 0)},
                ])
            chroma_pass.set_pipeline(self.pack_uv_pipeline)
            chroma_pass.set_bind_group(0, self.pack_bind_groups[2 + slot])
            chroma_pass.draw(3)
            chroma_pass.end()

            offset = 0
            for plane, (width, height), stride, padded in zip(
                    self.out_planes, self.out_plane_sizes, self.out_strides,
                    self.out_padded_bytes):
                encoder.copy_texture_to_buffer(
                    {"texture": plane},
                    {"buffer": self.yuv_readbacks[slot], "offset": offset,
                     "bytes_per_row": stride, "rows_per_image": height},
                    (width, height, 1),
                )
                offset += padded
        else:
            encoder.copy_texture_to_buffer(
                {"texture": self.targets[slot]},
                {"buffer": self.readbacks[slot], "bytes_per_row": self.row_bytes,
                 "rows_per_image": self.height},
                (self.width, self.height, 1),
            )
        device.queue.submit([encoder.finish()])

    def render(self, frame) -> np.ndarray:
        """The same frame as an array, for callers that want to look at it."""
        raw = self.render_bytes(frame)
        return np.frombuffer(raw, dtype=np.uint8).reshape(self.height, self.width, 4)


def _try_opencv():
    """OpenCV if it happens to be installed, else None.

    It is not bundled -- it costs 40 MB for one function. But the hand-written
    gather below is several times slower, so if someone has installed OpenCV on
    a machine that has to run on the CPU, use it.
    """
    try:
        import cv2

        return cv2
    except ImportError:
        return None


def bilinear_gather(source: np.ndarray, map_x: np.ndarray,
                    map_y: np.ndarray) -> np.ndarray:
    """Sample `source` at float coordinates, clamped at the border."""
    height, width = source.shape[:2]
    x0 = np.floor(map_x).astype(np.int32)
    y0 = np.floor(map_y).astype(np.int32)
    fx = (map_x - x0)[..., None]
    fy = (map_y - y0)[..., None]

    x0c = np.clip(x0, 0, width - 1)
    x1c = np.clip(x0 + 1, 0, width - 1)
    y0c = np.clip(y0, 0, height - 1)
    y1c = np.clip(y0 + 1, 0, height - 1)

    top = source[y0c, x0c]
    top = top + (source[y0c, x1c] - top) * fx
    bottom = source[y1c, x0c]
    bottom = bottom + (source[y1c, x1c] - bottom) * fx
    return top + (bottom - top) * fy


class CpuRemapper:
    """Fallback for machines without a usable GPU.

    Bilinear only -- no mip chain, so a little more aliasing where the source
    is squeezed, and several times slower than the GPU path."""

    name = "CPU"
    backend = "cpu"
    wants_yuv = False    # the numpy path works on RGBA

    def __init__(self, table: Table, source_size: tuple[int, int]) -> None:
        self.cv2 = _try_opencv()
        self.adapter_name = "OpenCV" if self.cv2 else "numpy"
        self.table = table
        self.source_width, self.source_height = source_size
        self.width, self.height = table.width, table.height

        self.premultiplied = True
        self.source_alpha = ALPHA_IGNORE
        self._transform = None
        self._edge_alpha = None
        self._edge_dark = None
        self.set_transform(None)

        ramp = np.arange(256, dtype=np.float32) / 255.0
        self.to_linear = np.where(
            ramp <= 0.04045, ramp / 12.92, ((ramp + 0.055) / 1.055) ** 2.4
        ).astype(np.float32)
        steps = 4096
        linear = np.arange(steps, dtype=np.float32) / (steps - 1)
        self.to_srgb = np.clip(
            np.where(linear <= 0.0031308, linear * 12.92,
                     1.055 * np.power(linear, 1 / 2.4) - 0.055) * 255.0 + 0.5,
            0, 255).astype(np.uint8)
        self.steps = steps

    def set_output_yuv(self, enabled: bool) -> None:
        """Read frames back as 4:2:0 planes instead of RGBA.

        Only for destinations that cannot carry alpha anyway -- H.264 encodes
        4:2:0 regardless, so packing here costs nothing and saves two thirds of
        the traffic back from the card.
        """
        self.output_yuv = enabled

    @property
    def output_frame_bytes(self) -> int:
        return self.out_bytes_yuv if self.output_yuv else self.row_bytes * self.height

    def set_premultiplied(self, premultiplied: bool) -> None:
        self.premultiplied = premultiplied

    def set_source_alpha(self, mode: int) -> None:
        self.source_alpha = int(mode)

    def set_transform(self, placement=None) -> None:
        """Rebuild the gather maps for where the clip now sits.

        The maps are the whole of this renderer's speed, so they are built once
        here rather than per frame -- the transform is static, the same as the
        table is.
        """
        self._transform = None if placement is None or placement.is_identity             else placement
        window_x = self.table.u
        window_y = 1.0 - self.table.v
        self._edge_alpha = None
        self._edge_dark = None

        if self._transform is None:
            source_x, source_y = window_x, window_y
        else:
            placement = self._transform
            aspect = transform.SOURCE_ASPECT
            radians = np.radians(placement.angle)
            cos, sin = np.cos(radians), np.sin(radians)
            ax = (window_x - placement.pivot_x - placement.x) * aspect
            ay = window_y - placement.pivot_y - placement.y
            rx = (ax * cos + ay * sin) / max(abs(placement.scale_x), 1e-6)
            ry = (ay * cos - ax * sin) / max(abs(placement.scale_y), 1e-6)
            source_x = rx / aspect + placement.pivot_x
            source_y = ry + placement.pivot_y
            if placement.flip_h:
                source_x = 1.0 - source_x
            if placement.flip_v:
                source_y = 1.0 - source_y

            left, top, right, bottom = placement.kept
            if placement.edge == 2:
                source_x = np.clip(source_x, left, right)
                source_y = np.clip(source_y, top, bottom)
            else:
                # One source pixel wide, which is all this renderer's bilinear
                # gather can resolve -- the GPU fades over the baked footprint
                # instead, so their edges differ by well under a pixel.
                span_x = 1.0 / max(self.source_width * abs(placement.scale_x), 1.0)
                span_y = 1.0 / max(self.source_height * abs(placement.scale_y), 1.0)
                cover = (np.clip((source_x - left) / span_x + 0.5, 0.0, 1.0)
                         * np.clip((right - source_x) / span_x + 0.5, 0.0, 1.0)
                         * np.clip((source_y - top) / span_y + 0.5, 0.0, 1.0)
                         * np.clip((bottom - source_y) / span_y + 0.5, 0.0, 1.0))
                cover = cover.astype(np.float32)
                if placement.edge == 1:
                    self._edge_dark = cover[..., None]
                else:
                    self._edge_alpha = cover

        self.map_x = (source_x * self.source_width - 0.5).astype(np.float32)
        self.map_y = (source_y * self.source_height - 0.5).astype(np.float32)
        self.coverage = self.table.coverage[..., None]

    def set_supersample(self, on: bool) -> None:
        pass                      # the CPU path has no room to spare for it

    def set_overlay(self, image) -> None:
        pass                      # the fallback draws the picture only

    def set_frame_plane(self, image) -> None:
        pass

    def set_frame_fit(self, fit: bool) -> None:
        pass

    def set_overlay_opacity(self, opacity: float) -> None:
        pass

    def set_overlay_space(self, on_output: bool) -> None:
        pass

    def render_bytes(self, frame) -> bytes:
        return self.render(frame).tobytes()

    def submit(self, frame) -> bytes:
        return self.render_bytes(frame)

    def flush(self):
        return None

    def render(self, frame) -> np.ndarray:
        if isinstance(frame, (bytes, bytearray, memoryview)):
            frame = np.frombuffer(frame, dtype=np.uint8).reshape(
                self.source_height, self.source_width, 4)
        colour = frame[..., :3]
        if self.source_alpha == ALPHA_PREMULTIPLIED:
            # Undone on the stored values, the way it was applied. Doing it
            # after to_linear is a different sum and a much darker edge.
            weight = np.maximum(frame[..., 3:], 1).astype(np.float32)
            colour = np.clip(colour.astype(np.float32) * (255.0 / weight),
                             0, 255).astype(np.uint8)
        linear = self.to_linear[colour]
        if self.cv2 is not None:
            warped = self.cv2.remap(linear, self.map_x, self.map_y,
                                    self.cv2.INTER_LINEAR,
                                    borderMode=self.cv2.BORDER_REPLICATE)
        else:
            warped = bilinear_gather(linear, self.map_x, self.map_y)

        alpha = self.table.coverage
        if self._edge_alpha is not None:
            alpha = alpha * self._edge_alpha
        if self.source_alpha != ALPHA_IGNORE:
            plane = frame[..., 3].astype(np.float32) / 255.0
            if self.cv2 is not None:
                carried = self.cv2.remap(plane, self.map_x, self.map_y,
                                         self.cv2.INTER_LINEAR,
                                         borderMode=self.cv2.BORDER_REPLICATE)
            else:
                carried = bilinear_gather(plane[..., None], self.map_x,
                                          self.map_y)[..., 0]
            alpha = alpha * carried

        if self._edge_dark is not None:
            warped *= self._edge_dark
        if self.premultiplied:
            warped *= alpha[..., None]
        index = np.clip(warped * (self.steps - 1), 0, self.steps - 1).astype(np.int32)
        out = np.empty((self.height, self.width, 4), dtype=np.uint8)
        out[..., :3] = self.to_srgb[index]
        out[..., 3] = np.clip(alpha * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return out


def make_remapper(table: Table, source_size: tuple[int, int], prefer_gpu: bool = True):
    """The GPU renderer when one can be had, the CPU one otherwise."""
    if prefer_gpu:
        try:
            return GpuRemapper(table, source_size)
        except Exception as error:  # noqa: BLE001 -- any GPU failure is a fallback
            print(f"GPU unavailable ({error}); falling back to CPU")
    return CpuRemapper(table, source_size)
