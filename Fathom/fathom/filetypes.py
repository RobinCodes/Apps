"""What kind of thing a file is, and what colour to draw it.

The treemap's whole value is that a glance at it tells you something. A map
where every rectangle is the same blue says where the big things are; one
coloured by kind also says *what* they are, which is usually the actual
question — 40 GB of video is a decision to make, 40 GB of build output is an
`rm` to run.

Thirteen categories is about as many as a legend can be read at a glance, so
anything not listed lands in "Other" rather than growing the list. Hue alone
does not carry the difference — the lightnesses vary as well — so the map is
still readable to someone who cannot separate red from green.
"""

from __future__ import annotations

# (key, label, colour as r,g,b floats). This order is the legend's order, and
# the index into it is what gets stored per cell.
CATEGORIES: tuple[tuple[str, str, tuple[float, float, float]], ...] = (
    ("video",     "Video",        (0.545, 0.361, 0.965)),   # violet
    ("audio",     "Audio",        (0.925, 0.282, 0.600)),   # pink
    ("image",     "Images",       (0.024, 0.714, 0.831)),   # cyan
    ("document",  "Documents",    (0.231, 0.510, 0.965)),   # blue
    ("archive",   "Archives",     (0.961, 0.620, 0.043)),   # amber
    ("diskimage", "Disk images",  (0.937, 0.267, 0.267)),   # red
    ("code",      "Code",         (0.063, 0.725, 0.506)),   # emerald
    ("binary",    "Binaries",     (0.976, 0.451, 0.086)),   # orange
    ("font",      "Fonts",        (0.639, 0.902, 0.208)),   # lime
    ("database",  "Databases",    (0.851, 0.275, 0.937)),   # fuchsia
    ("gamedata",  "Game data",    (0.078, 0.722, 0.651)),   # teal
    ("model",     "Models",       (0.855, 0.706, 0.290)),   # sand
    ("other",     "Other",        (0.580, 0.639, 0.722)),   # slate
)

OTHER = len(CATEGORIES) - 1

LABELS = tuple(label for _key, label, _rgb in CATEGORIES)
COLOURS = tuple(rgb for _key, _label, rgb in CATEGORIES)

# The colour a directory is drawn in when it is too small to show its contents,
# and the ground the map is drawn on. Both neutral, so they never compete with
# a category for the eye.
DIR_COLOUR = (0.325, 0.376, 0.435)

_EXTENSIONS: dict[str, str] = {}


def _register(key: str, extensions: str):
    """Claim a list of extensions for a category.

    An extension claimed twice used to mean the later call quietly won, which
    is how `.apk` spent a while being both an archive and a binary. Now it is
    an error at import, so the tables can only disagree once.
    """
    for ext in extensions.split():
        if ext in _EXTENSIONS:
            raise ValueError(
                f"extension .{ext} is claimed by both "
                f"{_EXTENSIONS[ext]!r} and {key!r}")
        _EXTENSIONS[ext] = key


_register("video", """
    mkv mp4 avi mov wmv flv webm m4v mpg mpeg mpe m2v m2ts mts vob ogv
    3gp 3g2 rm rmvb asf divx f4v mxf y4m
""")
_register("audio", """
    mp3 flac wav aac ogg oga opus m4a m4b wma aiff aif ape alac mid midi
    mod xm s3m it au ra amr dsf dff bank fsb wem
""")
_register("image", """
    jpg jpeg jpe png gif bmp webp tiff tif svg svgz heic heif avif ico icns
    raw cr2 cr3 nef arw dng orf rw2 raf psd psb xcf kra ai eps tga dds ktx
    exr hdr pbm pgm ppm jfif
""")
_register("document", """
    pdf doc docx odt rtf txt md markdown rst tex bib epub mobi azw azw3 djvu
    chm xls xlsx xlsm ods csv tsv ppt pptx pps odp pages numbers key
    ps xps log nfo
""")
_register("archive", """
    zip rar 7z tar gz bz2 xz zst lz lz4 lzma tgz txz tbz tbz2 cab arj ace
    zipx sit sitx deb rpm xpi crx pkg zpaq pack
""")
_register("diskimage", """
    iso img vhd vhdx vmdk qcow qcow2 vdi hdd dmg sparseimage wim esd swm
    nrg cue toast cdr squashfs sfs
""")
_register("code", """
    c h cc cpp cxx hpp hxx m mm py pyx pyi rs go java scala kt kts js mjs cjs
    ts tsx jsx vue svelte rb php cs fs vb swift lua pl pm r jl hs ml clj ex exs
    erl zig nim dart sh bash zsh fish ps1 bat cmd sql html htm css scss sass
    less json json5 yaml yml toml ini cfg conf xml proto gradle cmake make
    mk am ac patch diff ipynb
""")
_register("binary", """
    exe dll so dylib a lib o obj bin elf com msi msix appx appimage snap
    flatpak apk jar war ear wasm pyc pyo pyd class rlib rmeta gch pch node
    ko sys drv efi
""")
_register("font", "ttf ttc otf woff woff2 eot pfb pfa fon bdf pcf fnt")
_register("database", """
    db db3 sqlite sqlite3 mdb accdb ldf mdf ndf frm ibd myd myi dbf pst ost
    edb realm leveldb sst wal shm
""")
_register("gamedata", """
    pak vpk bsa ba2 esm esp asset assets bundle resource resources ress
    wad gcf ncf uasset umap upk sav rpf big vpp cpk arc nsp xci
    unity3d bnk pck
""")
_register("model", """
    gguf ggml safetensors onnx pt pth ckpt pb tflite h5 hdf5 npz npy
    caffemodel pkl joblib mlmodel
""")


def extension_of(name: str) -> str:
    """The extension of a filename, lowercased, or "" if it hasn't got one.

    Three rules, each of which a real filename made necessary:

    * A leading dot is a hidden file, not an extension — `.bashrc` is not a
      bashrc file.
    * An "extension" of more than a dozen characters is a full stop in a
      sentence somebody saved as a filename.
    * A run of digits is a version, not a type. Unix is full of
      `libcef.so.0` and `libz.so.1.2.11`, which are shared objects, and of
      files called `2.1.234`, which are not `.234` files. So a numeric tail
      is stepped over until something that is not a number turns up, or the
      name runs out.
    """
    end = len(name)
    while True:
        dot = name.rfind(".", 0, end)
        if dot <= 0 or dot == end - 1 or end - dot > 13:
            return ""
        tail = name[dot + 1:end]
        if not tail.isdigit():
            return tail.lower()
        end = dot


def category_of(name: str) -> int:
    """The index of the category a filename belongs to.

    Written on the extension alone: reading the first bytes of every file
    would turn a two second scan into a twenty minute one, and would be wrong
    about roughly as much.
    """
    key = _EXTENSIONS.get(extension_of(name))
    if key is None:
        return OTHER
    return _INDEX[key]


_INDEX = {key: index for index, (key, _label, _rgb) in enumerate(CATEGORIES)}


def label_of(index: int) -> str:
    return LABELS[index] if 0 <= index < len(LABELS) else LABELS[OTHER]


def colour_of(index: int) -> tuple[float, float, float]:
    return COLOURS[index] if 0 <= index < len(COLOURS) else COLOURS[OTHER]
