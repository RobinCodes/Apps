"""The grade as render nodes: where the arithmetic in grade.py becomes pixels.

Everything here runs on the GPU, because all of it is built out of the nodes
GSK already knows how to draw — a colour matrix, a blur, a blend, a mask, a
gradient, a repeated texture. Nothing is read back, nothing is computed per
pixel in Python, and the same tree draws a 60-megapixel photograph and a
video frame. That last point is the whole design: a grade means exactly one
thing in this app, so the preview, the exported still and the rendered video
cannot disagree.

The tree is built up a stage at a time. Each stage takes the picture so far
as a `Gsk.RenderNode`, wraps it in whatever it needs — sometimes drawing it
two or three times, as a mask, as a source, as a bright pass — and hands back
a new node. Nodes are immutable and reference counted, so drawing the same
one four times costs four quads and one offscreen, not four copies.

Three constructions here are worth reading before changing anything:

*The bright pass.* GSK clamps a colour matrix node's output to 0..1, so
`(value - threshold) * slope` deletes everything below the threshold. That is
how bloom and halation find the highlights without a shader.

*The unsharp mask.* `DIFFERENCE(0.5 + a/2, b/2)` is exactly `0.5 + (a-b)/2`
for every a and b in range: the sum never leaves 0..1, so nothing clamps, and
it can never go negative, so the absolute value in DIFFERENCE never folds.
That gives a signed high-pass layer centred on mid grey, and mid grey is the
fixed point of both SOFT_LIGHT and OVERLAY — so blending it back sharpens the
edges and leaves flat areas bit-for-bit alone. The test suite checks both
halves of that claim rather than taking it on trust.

*The masked lifts.* A mask node with `LUMINANCE` takes the picture's own
brightness as the alpha of whatever is drawn through it. Draw a brighter copy
of the picture through that mask, over the picture, and the result is the
picture lerped towards the brighter copy in proportion to how bright it
already was — which is a highlights control. `INVERTED_LUMINANCE` is the
shadows control, and the same trick with a flat colour instead of a copy is
split toning.

The one thing not done here is a tone curve with arbitrary control points.
GSK has no curve node, and a curve is the one piece of a grading pipeline
that cannot be assembled out of blends — so `midtones` and `filmic` offer the
two curve shapes that *can* be, a gamma and an S, and the editor does not
pretend to offer more.
"""

from __future__ import annotations

import atexit
import random

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")

from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from . import grade as grading  # noqa: E402

# Blur is the only thing in here that is not a textured quad, so it is the
# only thing worth capping. These are multipliers on every stage's radius,
# chosen per the quality setting; "draft" also drops the stages that are
# purely decorative.
QUALITY = {
    "high": 1.0,
    "balanced": 0.7,
    "draft": 0.45,
}

# Stages that cost a blur, and are therefore the ones draft quality thins out
# when a video is already struggling.
BLURRED = ("bloom", "glow", "unsharp")

# Stages that draw their input more than once — as a blend's backdrop and
# again as its source, or as a mask and again as what is masked. Every one of
# these doubles or triples the work of everything before it in the chain,
# because GSK re-renders a subtree each time it is referenced rather than
# caching it. Measured on a nine-stage grade: 4.7ms for one bloom, 14.3 for
# two, 30.7 for three, 62.9 for four. That is the whole reason `flatten()`
# exists.
DOUBLING = ("filmic", "highlights", "shadows", "highlight_tint", "shadow_tint",
            "unsharp", "bloom", "glow")

# Above this many pixels, flattening costs more than it saves: the texture
# itself becomes the expensive thing. A window on this kind of machine is
# about a megapixel, so the cap only bites when a picture is zoomed well past
# fitting — and at that zoom the picture is clipped to the viewport anyway.
FLATTEN_CAP = 4_000_000

# Flattening is not free — it is an extra render pass — so it only pays when
# there is enough left in the chain to pay for it. With two doubling stages
# the nested tree costs about four times one stage and flattening costs two
# passes plus two stages, which is a wash; measured, it is slightly worse.
# From three it wins, and by nine it wins by a factor of thirty. So the rule
# is: do nothing unless this many doubling stages are in the chain, and then
# flatten after each one but the last, because nothing follows the last one to
# benefit.
FLATTEN_FROM = 3

WASH_BLENDS = {
    "soft-light": Gsk.BlendMode.SOFT_LIGHT,
    "multiply": Gsk.BlendMode.MULTIPLY,
    "screen": Gsk.BlendMode.SCREEN,
    "overlay": Gsk.BlendMode.OVERLAY,
    "colour": Gsk.BlendMode.COLOR,
    "hue": Gsk.BlendMode.HUE,
    "luminosity": Gsk.BlendMode.LUMINOSITY,
}


# ------------------------------------------------------------- primitives ----

def rect(x: float, y: float, w: float, h: float) -> Graphene.Rect:
    return Graphene.Rect().init(x, y, w, h)


def point(x: float, y: float) -> Graphene.Point:
    return Graphene.Point().init(x, y)


def rgba(r: float, g: float, b: float, a: float = 1.0) -> Gdk.RGBA:
    return Gdk.RGBA(red=r, green=g, blue=b, alpha=max(0.0, min(1.0, a)))


def grey(value: float, alpha: float = 1.0) -> Gdk.RGBA:
    return rgba(value, value, value, alpha)


def stops(*pairs) -> list[Gsk.ColorStop]:
    """[(offset, Gdk.RGBA), ...] as the list the gradient calls want.

    Gsk.ColorStop takes no keyword arguments from Python, so each one has to
    be built and then filled in.
    """
    out = []
    for offset, colour in pairs:
        stop = Gsk.ColorStop()
        stop.offset = offset
        stop.color = colour
        out.append(stop)
    return out


def matrix(rows, offset=grading.ZERO):
    """A grade.py matrix and offset as the pair Graphene wants.

    grade.py uses the ordinary convention, out[j] = sum M[j][i] * in[i].
    Graphene's sixteen floats are read by GSK the other way round — the i-th
    group of four is how much of input channel i reaches each output — so
    this transposes. Getting it backwards is a silent bug that looks like a
    channel swap, which is why the test suite checks a known colour through a
    known matrix rather than checking the matrix.
    """
    floats = []
    for i in range(4):
        floats.extend(rows[j][i] for j in range(4))
    out = Graphene.Matrix()
    out.init_from_float(floats)
    vector = Graphene.Vec4()
    vector.init(*offset)
    return out, vector


def _node(snapshot) -> Gsk.RenderNode | None:
    """`snapshot.to_node()`, which is None when nothing was drawn."""
    return snapshot.to_node()


def through_matrix(node, rows, offset=grading.ZERO):
    """`node` with a colour matrix over it."""
    snapshot = Gtk.Snapshot()
    m, v = matrix(rows, offset)
    snapshot.push_color_matrix(m, v)
    snapshot.append_node(node)
    snapshot.pop()
    return _node(snapshot) or node


def blurred(node, radius: float, bounds: Graphene.Rect):
    """`node` blurred, and clipped back to `bounds`.

    The clip is not cosmetic. A blur grows a node's bounds by its radius, and
    without the clip a bloom would spill out past the edge of the picture and
    over the chrome — which looks like a bug even though it is what light
    actually does.
    """
    snapshot = Gtk.Snapshot()
    snapshot.push_clip(bounds)
    snapshot.push_blur(max(0.5, radius))
    snapshot.append_node(node)
    snapshot.pop()
    snapshot.pop()
    return _node(snapshot) or node


def blended(bottom, top, mode: Gsk.BlendMode):
    """The two nodes blended. First child is the backdrop, second the source."""
    snapshot = Gtk.Snapshot()
    snapshot.push_blend(mode)
    snapshot.append_node(bottom)
    snapshot.pop()
    snapshot.append_node(top)
    snapshot.pop()
    return _node(snapshot) or bottom


def faded(node, alpha: float):
    if alpha >= 0.999:
        return node
    snapshot = Gtk.Snapshot()
    snapshot.push_opacity(max(0.0, alpha))
    snapshot.append_node(node)
    snapshot.pop()
    return _node(snapshot) or node


def over(bottom, top):
    """`top` drawn over `bottom`, ordinary alpha compositing."""
    snapshot = Gtk.Snapshot()
    snapshot.append_node(bottom)
    snapshot.append_node(top)
    return _node(snapshot) or bottom


def masked_by(node, mask, mode: Gsk.MaskMode):
    """`node` with its alpha multiplied by `mask`, read the way `mode` says.

    The mask is the first child and the source the second, which is the
    opposite of what the argument order here suggests and the reason this is
    a function rather than four lines at each call site.
    """
    snapshot = Gtk.Snapshot()
    snapshot.push_mask(mode)
    snapshot.append_node(mask)
    snapshot.pop()
    snapshot.append_node(node)
    snapshot.pop()
    return _node(snapshot) or node


def flat(colour, bounds: Graphene.Rect):
    snapshot = Gtk.Snapshot()
    snapshot.append_color(colour, bounds)
    return _node(snapshot)


# ------------------------------------------------------------------ grain ----

_grain_tiles: dict[int, Gdk.Texture] = {}
_GRAIN_TILE = 160


def grain_texture(variant: int = 0) -> Gdk.Texture:
    """A tile of grey noise centred on mid grey, built once per variant.

    Mid grey is the fixed point of SOFT_LIGHT, so a tile that averages 0.5
    changes the picture's exposure not at all — it only adds the texture. The
    distribution is the average of three uniform draws, which is close enough
    to Gaussian for grain and much cheaper than the real thing; film grain is
    not uniform, and uniform noise reads as digital sensor noise instead.

    A handful of variants are kept so that video can cycle between them. Real
    grain is a different crystal on every frame, and grain that holds still
    over moving pictures looks like dirt on the lens.
    """
    if variant not in _grain_tiles:
        rng = random.Random(0x5EED + variant * 7919)
        size = _GRAIN_TILE
        data = bytearray(size * size * 4)
        for i in range(0, len(data), 4):
            value = int((rng.random() + rng.random() + rng.random()) / 3.0 * 255)
            data[i] = data[i + 1] = data[i + 2] = value
            data[i + 3] = 255
        _grain_tiles[variant] = Gdk.MemoryTexture.new(
            size, size, Gdk.MemoryFormat.R8G8B8A8,
            GLib.Bytes.new(bytes(data)), size * 4)
    return _grain_tiles[variant]


GRAIN_VARIANTS = 6


def grain_node(bounds: Graphene.Rect, size: float, variant: int):
    """The noise tile repeated over `bounds`, scaled by `size`."""
    tile = rect(bounds.origin.x, bounds.origin.y,
                _GRAIN_TILE * size, _GRAIN_TILE * size)
    node = Gsk.TextureNode.new(grain_texture(variant % GRAIN_VARIANTS), tile)
    return Gsk.RepeatNode.new(bounds, node, tile)


# ------------------------------------------------------------- the stages ----

def _midtones(node, amount, bounds):
    """A gamma curve, as a soft-light blend against a flat grey.

    Soft light against a constant is a smooth curve pinned at both 0 and 1 —
    exactly what a gamma control is, and unlike a gamma it cannot be made to
    clip.
    """
    return blended(node, flat(grey(0.5 + amount * 0.30), bounds),
                   Gsk.BlendMode.SOFT_LIGHT)


def _filmic(node, amount, bounds):
    """An S-curve, pinned at black and white, built from the picture itself.

    Overlaying a picture on itself is the oldest contrast trick there is, and
    what it computes is 2x^2 below mid grey and its mirror above — a true S,
    with both ends nailed down so nothing clips however far it is pushed.
    Backwards, soft light against the picture's own inverse gives the
    reverse S, which takes contrast *out* without flattening the ends.
    """
    if amount > 0:
        curved = blended(node, node, Gsk.BlendMode.OVERLAY)
    else:
        inverse = through_matrix(node, grading.diagonal(-1.0, -1.0, -1.0),
                                 (1.0, 1.0, 1.0, 0.0))
        curved = blended(node, inverse, Gsk.BlendMode.SOFT_LIGHT)
    return over(node, faded(curved, min(1.0, abs(amount))))


def _masked_exposure(node, amount, mode, bounds):
    """One stop of exposure, weighted by how bright the picture already is."""
    stops_ = 1.0 if amount > 0 else -1.0
    lifted = through_matrix(node, grading.exposure_matrix(stops_))
    return over(node, faded(masked_by(lifted, node, mode), min(1.0, abs(amount))))


def _tint(node, amount, colour, mode, bounds):
    """A colour into one end of the tonal range — split toning.

    Soft light rather than a plain overlay, because the point of split toning
    is to colour the shadows without lifting them: soft light at this
    strength moves the hue and leaves the luminance nearly where it was.
    """
    tinted = masked_by(flat(rgba(*grading.rgb(colour)), bounds), node, mode)
    return blended(node, faded(tinted, min(1.0, amount)),
                   Gsk.BlendMode.SOFT_LIGHT)


def _wash(node, amount, colour, mode, bounds):
    """A colour laid over the whole picture, in whichever mode was asked for."""
    blend = WASH_BLENDS.get(mode or "soft-light", Gsk.BlendMode.SOFT_LIGHT)
    layer = faded(flat(rgba(*grading.rgb(colour)), bounds), min(1.0, amount))
    return blended(node, layer, blend)


def _unsharp(node, amount, radius, detail, bounds):
    """Local contrast at one scale: an unsharp mask, or its reverse.

    See the module docstring for why the high-pass layer is a DIFFERENCE of
    two offset copies. The wide version uses soft light and the narrow one
    overlay: clarity is meant to be unnoticeable at a glance, and soft light
    rolls off instead of biting, while sharpening is meant to bite.
    """
    if amount < 0:
        # Backwards: towards the blur instead of away from it. A diffusion
        # filter, which is a sheet of glass with grease on it.
        return over(node, faded(blurred(node, radius, bounds),
                                min(1.0, -amount)))

    # One honest limitation, visible at the frame edge: the blur's input is
    # the picture and nothing else, so within a radius of the border it is
    # averaging the picture against transparency and comes out darker than it
    # should. The high pass is then not quite mid grey there, and clarity
    # leaves a faint halo a few pixels wide along the edge. Every unsharp mask
    # that does not deliberately clamp its edges does this; fixing it properly
    # means extending the picture outwards before blurring, which is another
    # pass over every frame for something only visible if you go looking.
    soft = blurred(node, radius, bounds)
    high = blended(
        through_matrix(node, grading.diagonal(0.5, 0.5, 0.5), (0.5, 0.5, 0.5, 0.0)),
        through_matrix(soft, grading.diagonal(0.5, 0.5, 0.5)),
        Gsk.BlendMode.DIFFERENCE)
    mode = (Gsk.BlendMode.SOFT_LIGHT if detail == "clarity"
            else Gsk.BlendMode.OVERLAY)
    return over(node, faded(blended(node, high, mode), min(1.0, amount)))


def _bloom(node, amount, radius, threshold, colour, bounds):
    """Light spilling out of the highlights, and film's red halation.

    The bright pass is `(value - threshold) * slope` with the slope chosen so
    that pure white comes out of it as pure white — otherwise raising the
    threshold would also dim the bloom, and the two controls would fight.

    With a colour, the bright pass is run through a duotone matrix instead of
    kept neutral, so the spill is driven by the highlights' luminance and
    carries the colour. That is what halation is: light scattering back off
    the film base through the layer that is sensitive to red.
    """
    slope = 1.0 / max(0.05, 1.0 - threshold)
    rows, offset = grading.bright_pass(threshold, slope)
    bright = through_matrix(node, rows, offset)
    if colour:
        bright = through_matrix(bright, grading.monochrome_matrix(grading.rgb(colour)))
    spill = blurred(bright, radius, bounds)
    return blended(node, faded(spill, min(1.0, amount * 0.9)),
                   Gsk.BlendMode.SCREEN)


def _glow(node, amount, radius, bounds):
    """Diffusion over the whole frame, highlights and all.

    Unlike bloom this has no threshold, so the shadows lift too and the
    picture loses its bite everywhere at once. That is the soft-focus filter,
    and the reason it is a separate control is that nobody has ever wanted
    exactly as much of it as they wanted bloom.
    """
    return blended(node, faded(blurred(node, radius, bounds),
                               min(1.0, amount * 0.75)),
                   Gsk.BlendMode.SCREEN)


def _grain(node, amount, size, variant, bounds):
    return blended(node, faded(grain_node(bounds, max(0.4, size), variant),
                               min(1.0, amount * 0.55)),
                   Gsk.BlendMode.SOFT_LIGHT)


def _vignette(node, amount, bounds):
    """The corners burned down, or dodged up.

    A radial gradient over the picture rather than a multiply, because the
    gradient's own alpha is what makes the falloff smooth, and because
    inverting the colour is then the whole of the negative case.
    """
    dark = amount > 0
    strength = min(1.0, abs(amount)) * (0.62 if dark else 0.45)
    colour = (0.0, 0.0, 0.0) if dark else (1.0, 1.0, 1.0)
    width, height = bounds.size.width, bounds.size.height
    snapshot = Gtk.Snapshot()
    snapshot.append_node(node)
    snapshot.push_clip(bounds)
    snapshot.append_radial_gradient(
        bounds,
        point(bounds.origin.x + width / 2, bounds.origin.y + height / 2),
        width * 0.78, height * 0.78, 0.30, 1.0,
        stops((0.0, rgba(*colour, 0.0)),
              (0.55, rgba(*colour, strength * 0.22)),
              (1.0, rgba(*colour, strength))))
    snapshot.pop()
    return _node(snapshot) or node


# -------------------------------------------------------------- the walk ----

_flattener: "Painter | None" = None
_flatten_broken = False


def shutdown():
    """Put the shared renderer away.

    GSK asserts in `gsk_renderer_dispose` if a renderer is finalized while
    still realized, and a module-level one is finalized at interpreter exit —
    which is how an otherwise clean run ends in `Bail out!`. Registered with
    `atexit` so that it happens even when nobody thought to call it, and safe
    to call twice.
    """
    global _flattener
    if _flattener is not None:
        _flattener.close()
        _flattener = None


atexit.register(shutdown)


def flatten(node, bounds: Graphene.Rect):
    """Collapse a node tree into a single texture node of the same picture.

    This is the one optimisation in the module and it is worth a paragraph.
    Nearly every stage draws its input more than once — a blend needs it as
    the backdrop and again as the source, a masked lift needs it as the mask
    and again as the thing being lifted — and GSK renders a referenced subtree
    again each time rather than keeping the first result. So a chain of nine
    stages is not nine times the work of one, it is closer to two to the
    ninth, and a grade with bloom, halation and glow in it took three quarters
    of a second a frame before this existed.

    Rendering the chain so far into a texture and carrying on from that
    texture makes the cost linear. It stays on the GPU — `render_texture`
    hands back a texture that was never read back to the processor — so the
    only cost is the render pass that was going to happen anyway.

    Returns the node unchanged if there is no renderer to be had, if the
    picture is too large to be worth it, or if a previous attempt failed; the
    grade then still draws correctly, only slower.
    """
    global _flattener, _flatten_broken
    width = int(round(bounds.size.width))
    height = int(round(bounds.size.height))
    if (_flatten_broken or node is None or width <= 0 or height <= 0
            or width * height > FLATTEN_CAP):
        return node
    if _flattener is None:
        _flattener = Painter()
        if not _flattener.ok():
            _flatten_broken = True
            return node
    texture = _flattener.render(node, width, height)
    if texture is None:
        # One failure is enough to stop trying: whatever stopped it — no GL
        # context, a driver that will not render offscreen — will not fix
        # itself, and trying again every frame would be slower than not
        # flattening at all.
        _flatten_broken = True
        return node
    flat = Gsk.TextureNode.new(texture, rect(0, 0, width, height))
    if bounds.origin.x or bounds.origin.y:
        snapshot = Gtk.Snapshot()
        snapshot.save()
        snapshot.translate(point(bounds.origin.x, bounds.origin.y))
        snapshot.append_node(flat)
        snapshot.restore()
        return _node(snapshot) or flat
    return flat


def apply_grade(node, bounds: Graphene.Rect, settings,
                quality: str = "high", variant: int = 0,
                collapse: bool = True):
    """`node` — a picture drawn at `bounds` — through the whole grade.

    `settings` is a grade.Grade. `variant` cycles the grain so that it moves
    on video and holds still on a photograph. Returns a new node, or the one
    it was given when the grade is neutral, which is the case worth being
    fast: an ungraded picture must cost exactly one textured quad.

    `collapse` flattens the chain into a texture between stages — see
    `flatten`. It is on by default and off in the test suite, which checks
    that both paths produce the same picture.
    """
    if node is None:
        return None
    if settings is None or settings.is_neutral():
        return node

    scale = QUALITY.get(quality, 1.0)
    draft = quality == "draft"

    stages = settings.stages()
    doubling = [index for index, stage in enumerate(stages)
                if stage.kind in DOUBLING]
    # The last one is excluded: flattening after it would be a render pass
    # whose result nothing draws twice.
    flatten_after = (set(doubling[:-1]) if collapse and len(doubling) >= FLATTEN_FROM
                     else set())

    rows, offset = settings.matrix()
    if rows != grading.IDENTITY or offset != grading.ZERO:
        node = through_matrix(node, rows, offset)

    for index, stage in enumerate(stages):
        kind = stage.kind
        if draft and kind in ("grain", "glow"):
            # Decoration, and the two that pay least for what they cost when
            # the machine is already behind.
            continue
        radius = stage.radius * scale if kind in BLURRED else stage.radius

        if kind == "midtones":
            node = _midtones(node, stage.amount, bounds)
        elif kind == "filmic":
            node = _filmic(node, stage.amount, bounds)
        elif kind == "highlights":
            node = _masked_exposure(node, stage.amount,
                                    Gsk.MaskMode.LUMINANCE, bounds)
        elif kind == "shadows":
            node = _masked_exposure(node, stage.amount,
                                    Gsk.MaskMode.INVERTED_LUMINANCE, bounds)
        elif kind == "highlight_tint":
            node = _tint(node, stage.amount, stage.colour,
                         Gsk.MaskMode.LUMINANCE, bounds)
        elif kind == "shadow_tint":
            node = _tint(node, stage.amount, stage.colour,
                         Gsk.MaskMode.INVERTED_LUMINANCE, bounds)
        elif kind == "wash":
            node = _wash(node, stage.amount, stage.colour, stage.detail, bounds)
        elif kind == "unsharp":
            node = _unsharp(node, stage.amount, radius, stage.detail, bounds)
        elif kind == "bloom":
            node = _bloom(node, stage.amount, radius, stage.threshold,
                          stage.colour, bounds)
        elif kind == "glow":
            node = _glow(node, stage.amount, radius, bounds)
        elif kind == "grain":
            node = _grain(node, stage.amount, stage.radius, variant, bounds)
        elif kind == "vignette":
            node = _vignette(node, stage.amount, bounds)

        if index in flatten_after:
            node = flatten(node, bounds)
    return node


def draw_graded(snapshot, node, bounds: Graphene.Rect, settings,
                quality: str = "high", variant: int = 0, radius: float = 0.0):
    """The common case: put a graded picture on a snapshot, corners rounded.

    The rounded clip goes outside the grade rather than inside it, so that a
    bloom cannot bleed past the corner it was supposed to be cut off by.
    """
    graded = apply_grade(node, bounds, settings, quality, variant)
    if graded is None:
        return
    if radius > 0.5:
        box = Gsk.RoundedRect()
        box.init_from_rect(bounds, radius)
        snapshot.push_rounded_clip(box)
        snapshot.append_node(graded)
        snapshot.pop()
    else:
        snapshot.append_node(graded)


class Painter:
    """A realized GSK renderer, kept for as long as there is work for it.

    `render_to_texture` below realizes one per call, which is right for a
    one-off still and hopeless for a video: realizing a GL renderer is tens of
    milliseconds and an export does it thirty times a second. So an export
    holds one of these instead, and `close()` puts it away — which must
    happen, because a renderer that is finalized without being unrealized
    asserts inside GSK.

    The GPU's renderer is asked for first and Cairo is the fallback. They
    agree to within rounding, and the test suite checks that they do; the
    difference is a factor of twenty in speed.
    """

    def __init__(self, prefer_gpu: bool = True):
        self.renderer = None
        self.kind = "none"
        if prefer_gpu and Gdk.Display.get_default() is not None:
            for name in ("GLRenderer", "CairoRenderer"):
                builder = getattr(Gsk, name, None)
                if builder is None:
                    continue
                try:
                    renderer = builder.new()
                    renderer.realize(None)
                except Exception:                       # noqa: BLE001
                    continue
                self.renderer = renderer
                self.kind = "gpu" if name == "GLRenderer" else "software"
                break
        if self.renderer is None:
            try:
                renderer = Gsk.CairoRenderer.new()
                renderer.realize(None)
                self.renderer = renderer
                self.kind = "software"
            except Exception:                           # noqa: BLE001
                self.renderer = None

    def ok(self) -> bool:
        return self.renderer is not None

    def render(self, node, width: int, height: int) -> Gdk.Texture | None:
        if self.renderer is None or node is None:
            return None
        try:
            return self.renderer.render_texture(node, rect(0, 0, width, height))
        except Exception:                               # noqa: BLE001
            return None

    def close(self):
        if self.renderer is not None:
            try:
                self.renderer.unrealize()
            except Exception:                           # noqa: BLE001
                pass
            self.renderer = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def render_to_texture(node, width: int, height: int) -> Gdk.Texture | None:
    """A node drawn into a texture — for a single still, and for the tests.

    Realizes a renderer, uses it once and puts it away. Anything that needs
    more than one frame should hold a `Painter`.
    """
    if node is None:
        return None
    with Painter() as painter:
        return painter.render(node, width, height)


def texture_bytes(texture: Gdk.Texture) -> tuple[bytes, int]:
    """A texture's pixels as straight (unpremultiplied) RGBA, and its stride.

    This is the one place in the app where the GPU is read back, and it is
    where an export spends much of its time — a readback has to wait for the
    GPU to finish, so it cannot be overlapped with anything.
    """
    downloader = Gdk.TextureDownloader.new(texture)
    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = downloader.download_bytes()
    return data.get_data(), stride
