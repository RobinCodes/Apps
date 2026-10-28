"""Paper: a photograph of a notebook page, made into a page.

Notes are taken on paper and the loop that turns them into a second brain
starts with a photograph of each page (SYSTEM.md §6). A phone photograph of a
page is a poor page: it is taken at an angle, so the page is a trapezium; the
light falls off towards one corner; the paper is grey and the ink is not quite
black; and it is four megabytes of desk around a page that will be synced
through git to three machines for the rest of its life. Each step below undoes
one of those.

*Finding the page.* Paper on a desk is the brightest large thing in the
picture, so the page is looked for at a few hundred pixels across: a threshold
between the two halves of the histogram (Otsu's), the largest connected bright
region, and its four extreme points along the diagonals — which are a
rectangle's corners however it is turned, short of forty-five degrees. Every
step of that can be fooled by a white desk or an open notebook of two pages,
so the result carries a measure of how page-like it was, and below a floor the
corners are offered for dragging instead of being trusted.

*Straightening it.* A perspective warp, from the four corners to a rectangle
whose sides are the longer of each pair of opposite edges. Within seven per
cent of √2 it is drawn as √2, because a school notebook is A5 or A4 and the
measured shape is foreshortened, not the paper.

*Flattening the light.* The brightest pixel of each block of about a line of
handwriting is the paper there, whatever the ink did; spread a little, so a
drawn box does not count as a shadow, and smoothed, that is the light falling
on the page. Dividing by it leaves the paper evenly white and the ink as dark
as it was against the paper next to it.

*Contrast.* A tone curve from the darkest ink to just under the paper's own
white, so the paper becomes pure white — which is also what makes the file
small, because noise in the paper is most of what a JPEG of a page spends its
bytes on — and the ruled lines stay a light grey.

*Writing it small.* A JPEG at 1600 pixels on the long side, at the highest of
a few qualities that comes in under 250KB.

Two ways to do the arithmetic. With numpy everything is a few array
operations. Without it — the school laptop may not have it — GdkPixbuf does
the scaling and the desaturating in C, cairo does the warp as a mesh of small
affine pieces (it can draw an affine transform and nothing else, and a
projective map is affine to well under a pixel over a forty-pixel square), and
`bytes.translate` does the tone curve; only the division by the light is a
Python loop, and that is over a lookup table. Both are checked against each
other in the suite. When cairo is missing too, a plain Python warp remains,
which is slow but fine for the preview.

Everything here is blocking and belongs on a worker thread.
"""

from __future__ import annotations

import math
from collections import Counter

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, GLib  # noqa: E402

try:
    import numpy
except ImportError:                                     # pragma: no cover
    numpy = None
try:
    import cairo
except ImportError:                                     # pragma: no cover
    cairo = None

# The long side of a filed page. A5 at 1600 pixels is about 190 dots to the
# inch: enough for handwriting and for a formula's subscripts, and no more.
LONGEST = 1600

# The photograph is read no larger than this. A page fills most of a phone
# picture, so this still leaves more than LONGEST across the page itself.
WORKING = 2600

# The long side the page is looked for at.
DETECT = 360

# What a filed page should weigh, in bytes. They sync through git, forever.
TARGET = 250_000

# Tried in order until one comes in under TARGET.
QUALITIES = (84, 78, 72, 66, 60, 54)

# The four corners when none were found: just inside the photograph.
INSET = 0.03
FALLBACK = ((INSET, INSET), (1 - INSET, INSET),
            (1 - INSET, 1 - INSET), (INSET, 1 - INSET))

# How page-like a found region has to be before its corners are trusted.
CONFIDENT = 0.5

ROOT_TWO = math.sqrt(2.0)


def engine() -> str:
    """Which arithmetic this machine gets: "numpy", "cairo" or "python"."""
    if numpy is not None:
        return "numpy"
    return "cairo" if cairo is not None else "python"


class Gray:
    """A greyscale picture: one byte a pixel, the rows packed with no padding."""

    __slots__ = ("width", "height", "data")

    def __init__(self, width: int, height: int, data):
        self.width, self.height = int(width), int(height)
        self.data = bytes(data)
        if len(self.data) != self.width * self.height:
            raise ValueError(f"{len(self.data)} bytes is not {self.width}x{self.height}")

    def at(self, x: int, y: int) -> int:
        return self.data[y * self.width + x]

    def mean(self) -> float:
        sample = self.data[::7] or self.data
        return sum(sample) / max(1, len(sample))


# ------------------------------------------------------------- pixbufs ----

def load(path: str, longest: int = WORKING) -> GdkPixbuf.Pixbuf:
    """The photograph, the right way up and no larger than `longest`."""
    _fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(path)
    if width and height and width > 0 and max(width, height) > longest:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, longest, longest, True)
    else:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    return pixbuf.apply_embedded_orientation() or pixbuf


def _rows(pixbuf: GdkPixbuf.Pixbuf) -> bytes:
    """A pixbuf's bytes, padded out to whole rows.

    GdkPixbuf does not pad the last row to the rowstride, so a buffer read
    straight off one is a few bytes short of `rowstride * height`.
    """
    raw = pixbuf.read_pixel_bytes().get_data()
    whole = pixbuf.get_rowstride() * pixbuf.get_height()
    return raw + bytes(whole - len(raw)) if len(raw) < whole else raw


def _channel(pixbuf: GdkPixbuf.Pixbuf, channel: int = 0) -> Gray:
    width, height = pixbuf.get_width(), pixbuf.get_height()
    step, stride = pixbuf.get_n_channels(), pixbuf.get_rowstride()
    raw = _rows(pixbuf)
    return Gray(width, height, b"".join(
        raw[y * stride + channel:y * stride + width * step:step]
        for y in range(height)))


def gray(pixbuf: GdkPixbuf.Pixbuf) -> Gray:
    """Luminance, weighted the way GdkPixbuf desaturates: .30, .59, .11."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    step, stride = pixbuf.get_n_channels(), pixbuf.get_rowstride()
    if numpy is not None:
        pixels = numpy.frombuffer(_rows(pixbuf), numpy.uint8).reshape(height, stride)
        pixels = pixels[:, :width * step].reshape(height, width, step).astype(numpy.uint32)
        luma = (pixels[..., 0] * 77 + pixels[..., 1] * 151 + pixels[..., 2] * 28 + 128) >> 8
        return Gray(width, height, luma.astype(numpy.uint8).tobytes())
    # Without numpy, GdkPixbuf desaturates in C and any one channel of what
    # it leaves is the luminance.
    flat = pixbuf.copy()
    pixbuf.saturate_and_pixelate(flat, 0.0, False)
    return _channel(flat, 0)


def to_pixbuf(page: Gray) -> GdkPixbuf.Pixbuf:
    """A grey picture as an RGB pixbuf, which is what GdkPixbuf can scale and save."""
    rgb = bytearray(len(page.data) * 3)
    rgb[0::3] = rgb[1::3] = rgb[2::3] = page.data
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(rgb)), GdkPixbuf.Colorspace.RGB, False, 8,
        page.width, page.height, page.width * 3)


def scaled(page: Gray, width: int, height: int) -> Gray:
    """Resized by GdkPixbuf: integrating when it shrinks, bilinear when it grows."""
    width, height = max(1, int(width)), max(1, int(height))
    if (width, height) == (page.width, page.height):
        return page
    return _channel(to_pixbuf(page).scale_simple(
        width, height, GdkPixbuf.InterpType.BILINEAR), 0)


# -------------------------------------------------------- finding it ----

def find_corners(pixbuf: GdkPixbuf.Pixbuf):
    """The page's corners in a photograph, as fractions of its width and height.

    Returns `(corners, confidence)`: the corners clockwise from the top left
    and a number from 0 to 1, or `(FALLBACK, 0.0)` when nothing page-shaped
    was found. Below CONFIDENT the caller should ask rather than trust.
    """
    width, height = pixbuf.get_width(), pixbuf.get_height()
    scale = min(1.0, DETECT / max(width, height))
    small = pixbuf if scale >= 1.0 else pixbuf.scale_simple(
        max(1, round(width * scale)), max(1, round(height * scale)),
        GdkPixbuf.InterpType.BILINEAR)
    return corners_of(gray(small))


def otsu(counts) -> tuple[int, float]:
    """The threshold that best splits a histogram in two, and how well it does.

    The second number is the share of the variance the split explains — near
    one for a white page on a dark desk, near nothing for a picture with no
    two populations in it.
    """
    total = sum(counts)
    if not total:
        return 127, 0.0
    mean = sum(value * count for value, count in enumerate(counts)) / total
    spread = sum(count * (value - mean) ** 2 for value, count in enumerate(counts)) / total
    best, best_between = 127, -1.0
    below = weight = 0.0
    for value in range(255):
        weight += counts[value]
        below += value * counts[value]
        if weight == 0 or weight == total:
            continue
        low = below / weight
        high = (mean * total - below) / (total - weight)
        share = weight / total
        between = share * (1 - share) * (low - high) ** 2
        if between > best_between:
            best, best_between = value, between
    return best, (best_between / spread) if spread else 0.0


def _runs(row: bytes, y: int):
    """(y, first, last) for each run of 1s in a row of a 0/1 mask."""
    found = []
    x = row.find(1)
    while x >= 0:
        end = row.find(0, x)
        if end < 0:
            end = len(row)
        found.append((y, x, end - 1))
        x = row.find(1, end)
    return found


def corners_of(page: Gray):
    """`find_corners`, on a picture already small and grey."""
    width, height = page.width, page.height
    counts = [0] * 256
    for value, count in Counter(page.data).items():
        counts[value] = count
    threshold, separation = otsu(counts)
    mask = page.data.translate(bytes(1 if v > threshold else 0 for v in range(256)))

    # Connected regions, a row of runs at a time: two runs on neighbouring
    # rows that overlap are one region. Far fewer runs than pixels, so this
    # is quick in plain Python.
    parent: list[int] = []

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    runs: list[tuple[int, int, int]] = []
    previous: list[int] = []
    for y in range(height):
        current = []
        for run in _runs(mask[y * width:(y + 1) * width], y):
            index = len(runs)
            runs.append(run)
            parent.append(index)
            current.append(index)
        a = b = 0
        while a < len(previous) and b < len(current):
            _, p0, p1 = runs[previous[a]]
            _, c0, c1 = runs[current[b]]
            if p1 >= c0 and c1 >= p0:
                ra, rb = root(previous[a]), root(current[b])
                if ra != rb:
                    parent[rb] = ra
            if p1 < c1:
                a += 1
            else:
                b += 1
        previous = current
    if not runs:
        return FALLBACK, 0.0

    areas: dict[int, int] = {}
    for index, (_y, x0, x1) in enumerate(runs):
        r = root(index)
        areas[r] = areas.get(r, 0) + x1 - x0 + 1
    biggest = max(areas, key=areas.get)
    area = areas[biggest]

    # The four extreme points along the diagonals, from the ends of the runs.
    tl = tr = br = bl = None
    for index, (y, x0, x1) in enumerate(runs):
        if root(index) != biggest:
            continue
        left, right = (x0, y), (x1 + 1, y)
        if tl is None or x0 + y < tl[0] + tl[1]:
            tl = left
        if bl is None or x0 - (y + 1) < bl[0] - bl[1]:
            bl = (x0, y + 1)
        if tr is None or (x1 + 1) - y > tr[0] - tr[1]:
            tr = right
        if br is None or (x1 + 1) + (y + 1) > br[0] + br[1]:
            br = (x1 + 1, y + 1)
    quad = (tl, tr, br, bl)
    corners = tuple((x / width, y / height) for x, y in quad)

    hull = _area(quad)
    if hull <= 0 or not _convex(quad):
        return FALLBACK, 0.0
    sides = [math.dist(quad[i], quad[(i + 1) % 4]) for i in range(4)]
    if min(sides) < 0.12 * min(width, height):
        return FALLBACK, 0.0
    share = area / (width * height)       # how much of the picture it is
    fill = min(1.0, area / hull)          # how much of its outline it fills
    confidence = (min(1.0, separation / 0.6) * min(1.0, share / 0.25)
                  * max(0.0, (fill - 0.6) / 0.35))
    confidence = max(0.0, min(1.0, confidence))
    if confidence < 0.05:
        return FALLBACK, 0.0
    return corners, confidence


def _area(quad) -> float:
    return abs(sum(quad[i][0] * quad[(i + 1) % 4][1] - quad[(i + 1) % 4][0] * quad[i][1]
                   for i in range(4))) / 2


def _convex(quad) -> bool:
    signs = set()
    for i in range(4):
        (ax, ay), (bx, by), (cx, cy) = quad[i], quad[(i + 1) % 4], quad[(i + 2) % 4]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) > 1e-9:
            signs.add(cross > 0)
    return len(signs) == 1


# ------------------------------------------------------ straightening ----

def aspect(quad, centre) -> float | None:
    """The width over the height of the real page, from its picture.

    A trapezium does not say on its own how tall the rectangle it came from
    was: a page photographed from low down has a short picture and a long
    reality. But a picture of a rectangle taken by a pinhole camera does
    fix the camera's focal length, and with it the shape — Zhang and He's
    construction, from "Whiteboard scanning and image enhancement" (2007),
    with the centre of the photograph as the principal point. None when the
    corners admit no real camera, which is when the caller falls back on
    the lengths of the sides.
    """
    (x1, y1), (x2, y2), (x4, y4), (x3, y3) = quad   # m1 tl, m2 tr, m3 bl, m4 br
    u0, v0 = centre
    m1, m2, m3, m4 = ((x1, y1, 1.0), (x2, y2, 1.0), (x3, y3, 1.0), (x4, y4, 1.0))

    def cross(a, b):
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0])

    def dot(a, b):
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    try:
        k2 = dot(cross(m1, m4), m3) / dot(cross(m2, m4), m3)
        k3 = dot(cross(m1, m4), m2) / dot(cross(m3, m4), m2)
    except ZeroDivisionError:
        return None
    n2 = tuple(k2 * a - b for a, b in zip(m2, m1))
    n3 = tuple(k3 * a - b for a, b in zip(m3, m1))
    if abs(n2[2] * n3[2]) < 1e-12:
        # Opposite sides parallel: no perspective to undo.
        return math.sqrt((n2[0] ** 2 + n2[1] ** 2) / max(1e-12, n3[0] ** 2 + n3[1] ** 2))
    f2 = -((n2[0] * n3[0] - (n2[0] * n3[2] + n2[2] * n3[0]) * u0 + n2[2] * n3[2] * u0 * u0)
           + (n2[1] * n3[1] - (n2[1] * n3[2] + n2[2] * n3[1]) * v0
              + n2[2] * n3[2] * v0 * v0)) / (n2[2] * n3[2])
    if f2 <= 0:
        return None

    def length2(n):
        # nᵀ A⁻ᵀ A⁻¹ n, with A the camera: f on the diagonal, the centre beside it.
        x = (n[0] - u0 * n[2]) / math.sqrt(f2)
        y = (n[1] - v0 * n[2]) / math.sqrt(f2)
        return x * x + y * y + n[2] * n[2]

    ratio = math.sqrt(length2(n2) / length2(n3))
    return ratio if 0.1 < ratio < 10 else None


def page_size(quad, longest: int = LONGEST, centre=None) -> tuple[int, int]:
    """How big the straightened page is, from its corners in pixels.

    The shape comes from `aspect` when the photograph's centre is known and
    the corners make sense as a camera's view, from the sides otherwise.
    """
    tl, tr, br, bl = quad
    width = max(math.dist(tl, tr), math.dist(bl, br))
    height = max(math.dist(tl, bl), math.dist(tr, br))
    if width < 1 or height < 1:
        return 1, 1
    shape = aspect(quad, centre) if centre is not None else None
    if shape is not None:
        # Keep the larger measured side and let the shape set the other.
        if width >= height * shape:
            height = width / shape
        else:
            width = height * shape
    ratio = max(width, height) / min(width, height)
    if abs(ratio - ROOT_TWO) / ROOT_TWO < 0.07:
        if width > height:
            height = width / ROOT_TWO
        else:
            width = height / ROOT_TWO
    scale = min(1.0, longest / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def homography(source, target):
    """The 3x3 projective map taking four points to four others."""
    rows, values = [], []
    for (x, y), (u, v) in zip(source, target):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        values.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values.append(v)
    h = _solve(rows, values)
    return ((h[0], h[1], h[2]), (h[3], h[4], h[5]), (h[6], h[7], 1.0))


def _solve(rows, values):
    """Gaussian elimination with partial pivoting; eight unknowns."""
    n = len(values)
    m = [list(row) + [value] for row, value in zip(rows, values)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("those four corners do not make a page")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col and m[r][col]:
                factor = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= factor * m[col][c]
    return [m[r][n] / m[r][r] for r in range(n)]


def project(h, x: float, y: float) -> tuple[float, float]:
    d = h[2][0] * x + h[2][1] * y + h[2][2]
    return ((h[0][0] * x + h[0][1] * y + h[0][2]) / d,
            (h[1][0] * x + h[1][1] * y + h[1][2]) / d)


def warp(source: Gray, quad, width: int, height: int, how: str = "") -> Gray:
    """The quadrilateral `quad` of `source` (pixels, clockwise from the top
    left), drawn as a `width` x `height` rectangle."""
    rect = ((0, 0), (width, 0), (width, height), (0, height))
    h = homography(rect, quad)
    how = how or engine()
    if how == "numpy" and numpy is not None:
        return _warp_numpy(source, h, width, height)
    if how in ("numpy", "cairo") and cairo is not None:
        return _warp_cairo(source, h, width, height)
    return _warp_python(source, h, width, height)


def _warp_numpy(source, h, width, height):
    image = numpy.frombuffer(source.data, numpy.uint8).reshape(source.height, source.width)
    image = image.astype(numpy.float32)
    out = numpy.empty((height, width), numpy.uint8)
    xs = numpy.arange(width, dtype=numpy.float64) + 0.5
    # A band of rows at a time, so a page's worth of coordinates is never in
    # memory at once on a machine with little of it.
    for top in range(0, height, 128):
        ys = numpy.arange(top, min(height, top + 128), dtype=numpy.float64) + 0.5
        gx, gy = numpy.meshgrid(xs, ys)
        d = h[2][0] * gx + h[2][1] * gy + h[2][2]
        sx = (h[0][0] * gx + h[0][1] * gy + h[0][2]) / d - 0.5
        sy = (h[1][0] * gx + h[1][1] * gy + h[1][2]) / d - 0.5
        sx = numpy.clip(sx, 0, source.width - 1)
        sy = numpy.clip(sy, 0, source.height - 1)
        x0 = numpy.minimum(sx.astype(numpy.int32), source.width - 2 if source.width > 1 else 0)
        y0 = numpy.minimum(sy.astype(numpy.int32), source.height - 2 if source.height > 1 else 0)
        fx, fy = (sx - x0).astype(numpy.float32), (sy - y0).astype(numpy.float32)
        x1 = numpy.minimum(x0 + 1, source.width - 1)
        y1 = numpy.minimum(y0 + 1, source.height - 1)
        top_row = image[y0, x0] * (1 - fx) + image[y0, x1] * fx
        bottom_row = image[y1, x0] * (1 - fx) + image[y1, x1] * fx
        band = top_row * (1 - fy) + bottom_row * fy
        out[top:top + band.shape[0]] = numpy.clip(band + 0.5, 0, 255).astype(numpy.uint8)
    return Gray(width, height, out.tobytes())


def _surface(page: Gray):
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_A8, page.width)
    buffer = bytearray(stride * page.height)
    for y in range(page.height):
        buffer[y * stride:y * stride + page.width] = \
            page.data[y * page.width:(y + 1) * page.width]
    return cairo.ImageSurface.create_for_data(buffer, cairo.FORMAT_A8, page.width,
                                              page.height, stride), buffer


# The mesh the cairo warp is drawn in, in output pixels.
CELL = 40


def _warp_cairo(source, h, width, height):
    """The warp as cairo can draw it: triangles, each an affine map.

    An A8 surface is a grey picture as far as this is concerned — painting
    one onto another with SOURCE copies the value. Every triangle is pushed
    out by most of a pixel so the mesh can have no gaps; with SOURCE an
    overlap costs nothing.
    """
    image, _keep = _surface(source)
    target = cairo.ImageSurface(cairo.FORMAT_A8, width, height)
    context = cairo.Context(target)
    context.set_operator(cairo.OPERATOR_SOURCE)
    context.set_antialias(cairo.ANTIALIAS_NONE)
    pattern = cairo.SurfacePattern(image)
    pattern.set_filter(cairo.FILTER_BILINEAR)
    pattern.set_extend(cairo.EXTEND_PAD)
    across = max(1, math.ceil(width / CELL))
    down = max(1, math.ceil(height / CELL))
    xs = [width * i / across for i in range(across + 1)]
    ys = [height * j / down for j in range(down + 1)]
    grid = [[project(h, x, y) for x in xs] for y in ys]
    for j in range(down):
        for i in range(across):
            corners = ((xs[i], ys[j]), (xs[i + 1], ys[j]),
                       (xs[i + 1], ys[j + 1]), (xs[i], ys[j + 1]))
            mapped = (grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i])
            for a, b, c in ((0, 1, 2), (0, 2, 3)):
                _paint_triangle(context, pattern, (corners[a], corners[b], corners[c]),
                                (mapped[a], mapped[b], mapped[c]))
    target.flush()
    data, stride = target.get_data(), target.get_stride()
    return Gray(width, height, b"".join(bytes(data[y * stride:y * stride + width])
                                        for y in range(height)))


def _paint_triangle(context, pattern, dest, src):
    (x0, y0), (x1, y1), (x2, y2) = dest
    (u0, v0), (u1, v1), (u2, v2) = src
    det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(det) < 1e-9:
        return
    # The affine map from this triangle of the page to its triangle in the
    # photograph — which is what a cairo pattern matrix is: user space to
    # pattern space.
    a = ((u1 - u0) * (y2 - y0) - (u2 - u0) * (y1 - y0)) / det
    b = ((u2 - u0) * (x1 - x0) - (u1 - u0) * (x2 - x0)) / det
    c = ((v1 - v0) * (y2 - y0) - (v2 - v0) * (y1 - y0)) / det
    d = ((v2 - v0) * (x1 - x0) - (v1 - v0) * (x2 - x0)) / det
    pattern.set_matrix(cairo.Matrix(a, c, b, d, u0 - a * x0 - b * y0,
                                    v0 - c * x0 - d * y0))
    cx, cy = (x0 + x1 + x2) / 3, (y0 + y1 + y2) / 3
    context.save()
    context.new_path()
    for index, (x, y) in enumerate(dest):
        length = math.hypot(x - cx, y - cy) or 1.0
        px, py = x + (x - cx) / length * 0.75, y + (y - cy) / length * 0.75
        if index == 0:
            context.move_to(px, py)
        else:
            context.line_to(px, py)
    context.close_path()
    context.clip()
    context.set_source(pattern)
    context.paint()
    context.restore()


def _warp_python(source, h, width, height):
    """The same warp, a pixel at a time. Slow; there for completeness."""
    data, sw, sh = source.data, source.width, source.height
    out = bytearray(width * height)
    for y in range(height):
        fy = y + 0.5
        for x in range(width):
            sx, sy = project(h, x + 0.5, fy)
            sx = min(max(sx - 0.5, 0.0), sw - 1.0)
            sy = min(max(sy - 0.5, 0.0), sh - 1.0)
            x0, y0 = min(int(sx), max(0, sw - 2)), min(int(sy), max(0, sh - 2))
            x1, y1 = min(x0 + 1, sw - 1), min(y0 + 1, sh - 1)
            ax, ay = sx - x0, sy - y0
            top = data[y0 * sw + x0] * (1 - ax) + data[y0 * sw + x1] * ax
            bottom = data[y1 * sw + x0] * (1 - ax) + data[y1 * sw + x1] * ax
            out[y * width + x] = int(top * (1 - ay) + bottom * ay + 0.5)
    return Gray(width, height, out)


# ------------------------------------------------------------- the light ----

# A block is about a line of handwriting at LONGEST, and the light is taken
# from the brightest block this many blocks either way.
BLOCK = 24
SPREAD = 3


def background(page: Gray) -> Gray:
    """The light falling on the page, at the page's size."""
    width, height = page.width, page.height
    across, down = max(1, math.ceil(width / BLOCK)), max(1, math.ceil(height / BLOCK))
    grid = bytearray(across * down)
    data = page.data
    # The brightest pixel of each block, from the brightest of each row's
    # slice of it — max() over a slice of bytes runs in C.
    for j in range(down):
        top, bottom = j * BLOCK, min(height, (j + 1) * BLOCK)
        for i in range(across):
            left, right = i * BLOCK, min(width, (i + 1) * BLOCK)
            grid[j * across + i] = max(max(data[y * width + left:y * width + right])
                                       for y in range(top, bottom))
    # Spread three blocks each way, so a filled box or a drawing as wide as a
    # thumb is not read as a shadow; then smoothed, so the blocks do not
    # show. The light on a page changes over its width, not over a word, so
    # nothing real is lost by looking this far.
    grid = _neighbourhood(grid, across, down, max, SPREAD)
    grid = _neighbourhood(grid, across, down, lambda vs: sum(vs) // len(vs))
    return scaled(Gray(across, down, grid), width, height)


def _neighbourhood(grid, across, down, combine, reach=1):
    out = bytearray(len(grid))
    for j in range(down):
        for i in range(across):
            values = [grid[y * across + x]
                      for y in range(max(0, j - reach), min(down, j + reach + 1))
                      for x in range(max(0, i - reach), min(across, i + reach + 1))]
            out[j * across + i] = combine(values)
    return out


_DIVIDE: bytes | None = None


def _divide_table() -> bytes:
    """min(255, 255·value/light) for every pair, as one 64KB table."""
    global _DIVIDE
    if _DIVIDE is None:
        table = bytearray(65536)
        for light in range(256):
            for value in range(256):
                table[(value << 8) | light] = min(255, (value * 255 + light // 2)
                                                  // max(1, light))
        _DIVIDE = bytes(table)
    return _DIVIDE


def flatten(page: Gray) -> Gray:
    """The page divided by its own light, so the paper is evenly bright."""
    light = background(page)
    if numpy is not None:
        value = numpy.frombuffer(page.data, numpy.uint8).astype(numpy.uint32)
        under = numpy.maximum(numpy.frombuffer(light.data, numpy.uint8).astype(numpy.uint32), 1)
        out = numpy.minimum(255, (value * 255 + under // 2) // under)
        return Gray(page.width, page.height, out.astype(numpy.uint8).tobytes())
    table = _divide_table()
    return Gray(page.width, page.height,
                bytes(table[(v << 8) | b] for v, b in zip(page.data, light.data)))


def levels(page: Gray) -> Gray:
    """From the darkest ink to just under the paper's white, a little gamma.

    Measured from a sample of the pixels rather than all of them: the
    percentiles of a page do not need a million votes.
    """
    sample = page.data[::5] or page.data
    counts = [0] * 256
    for value, count in Counter(sample).items():
        counts[value] = count
    total = len(sample)

    def percentile(share):
        running = 0
        for value, count in enumerate(counts):
            running += count
            if running >= share * total:
                return value
        return 255

    black = percentile(0.004)
    paper = percentile(0.5)                # most of a page is paper
    white = max(black + 40, min(255, round(paper * 0.94)))
    if white > 255:
        black, white = 215, 255
    table = bytearray(256)
    for value in range(256):
        t = (value - black) / (white - black)
        t = 0.0 if t <= 0 else 1.0 if t >= 1 else t ** 1.35
        table[value] = round(t * 255)
    return Gray(page.width, page.height, page.data.translate(bytes(table)))


def enhance(page: Gray) -> Gray:
    """Paper made white and even, ink made dark: what a photocopier would do."""
    return levels(flatten(page))


# ----------------------------------------------------------- the whole way ----

def straighten(photo, corners, longest: int = LONGEST, enhanced: bool = True,
               how: str = "") -> Gray:
    """A page out of a photograph: warped from its corners and enhanced.

    `photo` is a path or a pixbuf that is already upright; `corners` are
    fractions of its size, clockwise from the top left.
    """
    pixbuf = load(photo, max(WORKING, longest)) if isinstance(photo, str) else photo
    # No need for more of the photograph than the page will be made of.
    width, height = pixbuf.get_width(), pixbuf.get_height()
    quad = [(x * width, y * height) for x, y in corners]
    out_w, out_h = page_size(quad, longest, (width / 2, height / 2))
    reach = max(math.dist(quad[0], quad[2]), math.dist(quad[1], quad[3]))
    wanted = math.hypot(out_w, out_h) * 1.25
    if reach > wanted * 1.1:
        factor = wanted / reach
        pixbuf = pixbuf.scale_simple(max(1, round(width * factor)),
                                     max(1, round(height * factor)),
                                     GdkPixbuf.InterpType.BILINEAR)
        width, height = pixbuf.get_width(), pixbuf.get_height()
        quad = [(x * width, y * height) for x, y in corners]
    page = warp(gray(pixbuf), quad, out_w, out_h, how)
    return enhance(page) if enhanced else page


def jpeg(page: Gray, target: int = TARGET) -> bytes:
    """The page as a JPEG, at the best of QUALITIES that fits in `target`."""
    pixbuf = to_pixbuf(page)
    data = b""
    for _attempt in range(3):
        for quality in QUALITIES:
            data = _encode(pixbuf, quality)
            if len(data) <= target:
                return data
        # Still too heavy at the lowest quality: a busy page. Smaller, then.
        pixbuf = pixbuf.scale_simple(max(1, round(pixbuf.get_width() * 0.85)),
                                     max(1, round(pixbuf.get_height() * 0.85)),
                                     GdkPixbuf.InterpType.BILINEAR)
    return data


def _encode(pixbuf, quality: int) -> bytes:
    result = pixbuf.save_to_bufferv("jpeg", ["quality"], [str(quality)])
    return bytes(result[1] if isinstance(result, tuple) else result)


def texture_of(page: Gray):
    """A page as a GdkTexture, for showing it."""
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk

    return Gdk.Texture.new_for_pixbuf(to_pixbuf(page))
