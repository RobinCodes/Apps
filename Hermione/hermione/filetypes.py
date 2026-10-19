"""What kind of file this is, and what it should look like.

Two questions, deliberately answered separately.

*Which icon?* GIO already knows: `g_content_type_get_symbolic_icon` returns a
themed icon with a fallback chain — `text-x-python-symbolic`, then
`text-x-generic-symbolic`, then the non-symbolic pair — and the icon theme
picks the first one it actually has. Guessing icon names by extension here
would be a worse copy of a table that already ships with the system.

*Which colour?* Nobody knows that but us. A file manager that tints every
icon by what the file is becomes readable at a glance — the eye finds the one
video in a folder of stills without reading a single name — but only if the
colour means a *category* and the categories are few. Twenty hues is noise.
So: eleven categories, each with one colour that works on a light background
and a dark one, and everything else left uncoloured on purpose.

The extension tables are the categories, not the types: `.mkv` and `.webm`
are both "video" and that is all this module has to say about them.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio  # noqa: E402

# key -> (label, colour, fallback icon)
#
# The colours are picked to sit at roughly one lightness, so no category
# shouts louder than another, and to stay legible against both Adwaita
# grounds — which is why they are mid-tone rather than saturated.
CATEGORIES: dict[str, tuple[str, str, str]] = {
    "folder":      ("Folder",       "#8ab4f8", "folder-symbolic"),
    "image":       ("Image",        "#f0a3c8", "image-x-generic-symbolic"),
    "video":       ("Video",        "#c8a0f5", "video-x-generic-symbolic"),
    "audio":       ("Audio",        "#7ddcc0", "audio-x-generic-symbolic"),
    "document":    ("Document",     "#f5c177", "x-office-document-symbolic"),
    "sheet":       ("Spreadsheet",  "#8fd98f", "x-office-spreadsheet-symbolic"),
    "slides":      ("Presentation", "#f2a07b", "x-office-presentation-symbolic"),
    "code":        ("Code",         "#6fd0e8", "text-x-generic-symbolic"),
    "archive":     ("Archive",      "#d6b36e", "package-x-generic-symbolic"),
    "binary":      ("Program",      "#b0b6c0", "application-x-executable-symbolic"),
    "font":        ("Font",         "#d9a8e0", "font-x-generic-symbolic"),
    "text":        ("Text",         "#a8b2c0", "text-x-generic-symbolic"),
    "other":       ("File",         "",        "text-x-generic-symbolic"),
}

# The same eleven, darkened for a light theme. A tint chosen to glow against
# a dark background is a pale smudge against a white one — same hue, taken
# down in lightness and up in saturation until it reads as a colour rather
# than as a stain.
LIGHT_COLOURS = {
    "folder":   "#1a6fd4",
    "image":    "#c43f84",
    "video":    "#7b3fc4",
    "audio":    "#0e8f72",
    "document": "#b5720a",
    "sheet":    "#2d8a2d",
    "slides":   "#c1562a",
    "code":     "#0b7f9e",
    "archive":  "#9a7312",
    "binary":   "#5c6370",
    "font":     "#9a3fae",
    "text":     "#5a6676",
}


def accent(key: str, dark: bool = True) -> str:
    """The category's colour for the theme in use, or "" where it has none."""
    if not dark:
        return LIGHT_COLOURS.get(key, "")
    return CATEGORIES.get(key, CATEGORIES["other"])[1]


# Extensions, one line per category. Written as plain words so adding one is
# a one-word edit; the lookup table below is built from these once.
_TABLE = {
    "image": """
        png jpg jpeg jpe jfif gif bmp webp avif heic heif tiff tif svg svgz ico
        xpm xcf psd ai eps kra ora raw cr2 cr3 nef arw dng orf rw2 raf pef srw
        tga pbm pgm ppm pnm jp2 j2k jxl qoi
    """,
    "video": """
        mp4 m4v mkv webm avi mov wmv flv f4v mpg mpeg mpe m2v mts vob
        ogv 3gp 3g2 asf rm rmvb divx mxf y4m m2ts
    """,
    "audio": """
        mp3 flac wav wave aac m4a m4b ogg oga opus wma aiff aif aifc alac ape
        wv mka mid midi amr ac3 dts caf au ra xm s3m it sid spx
    """,
    "document": """
        pdf doc docx odt rtf tex latex ltx djvu epub mobi azw azw3 fb2 pages
        wpd abw dvi xps oxps chm cbz cbr
    """,
    "sheet": """
        xls xlsx xlsm xlsb ods csv tsv numbers gnumeric dif sylk
    """,
    "slides": """
        ppt pptx pps ppsx odp key otp sxi
    """,
    "code": """
        c h cc cpp cxx hpp hh hxx m mm cs java kt kts scala swift go rs rb py
        pyw pyi pl pm php phtml lua tcl r jl hs lhs ml mli fs fsx fsi erl hrl
        ex exs elm clj cljs cljc edn scm ss rkt lisp cl el vim nim zig d dart
        groovy gradle js mjs cjs jsx ts tsx vue svelte astro coffee sql psql
        sh bash zsh fish ksh csh ps1 psm1 bat cmd awk sed asm s nasm f f90 f95
        for cob cbl pas pp dpr bas vb vbs cu glsl vert frag hlsl wgsl v sv
        vhd vhdl ada adb ads rsx pro ipynb
    """,
    "archive": """
        zip tar gz tgz bz2 tbz tbz2 xz txz lz lzma lz4 zst tzst 7z rar br
        cab arj lha lzh ace cpio rpm deb pkg apk xpi crx jar war ear iso img
        dmg squashfs appimage snap flatpak flatpakref whl gem
    """,
    "binary": """
        exe dll so dylib a o obj lib bin elf com msi msix appx wasm pyc pyd
        class ko sys efi rom
    """,
    "font": """
        ttf otf ttc woff woff2 eot pfb pfm afm bdf pcf psf fon fnt
    """,
    "text": """
        txt text log md markdown mdown mkd rst adoc asciidoc org nfo me 1st
        json json5 jsonc yaml yml toml ini cfg conf config properties env
        xml xsd xsl xslt dtd html htm xhtml css scss sass less styl
        csv-schema diff patch po pot mo srt vtt sub ass ssa bib cls sty
        gitignore gitattributes editorconfig desktop service socket timer
        plist reg lock sum makefile cmake nix dockerfile gemfile rakefile
    """,
}

# extension (no dot, lower case) -> category. Built once from the tables
# above; an extension claimed twice is a bug the suite checks for rather than
# something to settle silently by table order.
EXTENSIONS: dict[str, str] = {}
for _key, _words in _TABLE.items():
    for _word in _words.split():
        EXTENSIONS.setdefault(_word, _key)

# Suffixes that are really a second extension on an archive: the type of
# `linux-6.1.tar.xz` is the tar, not the xz, and `.tar.gz` must not read as
# "a gz file called linux-6.1.tar".
DOUBLE = ("pkg.tar.zst", "pkg.tar.xz", "pkg.tar.gz",
          "tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tar.lz4", "tar.lzma",
          "tar.br", "tar.z")

# A compressed tar is an archive under its whole suffix as well, or
# `linux-6.1.tar.xz` would be a file of no known category.
for _double in DOUBLE:
    EXTENSIONS.setdefault(_double, "archive")

# Files with no extension that are still obviously something. Matched on the
# whole lower-cased name, because `Makefile` is not `.makefile`.
NAMED = {
    "makefile": "code", "gnumakefile": "code", "cmakelists.txt": "code",
    "dockerfile": "code", "containerfile": "code", "vagrantfile": "code",
    "rakefile": "code", "gemfile": "code", "procfile": "code", "justfile": "code",
    "readme": "text", "license": "text", "licence": "text", "copying": "text",
    "changelog": "text", "authors": "text", "notice": "text", "todo": "text",
    "install": "text", "news": "text", "manifest": "text", "pkgbuild": "code",
    "go.mod": "code", "go.sum": "text", "cargo.lock": "text", "flake.lock": "text",
    "kconfig": "code", "meson.build": "code", "build.zig": "code",
}



def extension(name: str) -> str:
    """The part of the name that says what it is, lower case and without dots.

    A trailing number is a version, not a type — `libfoo.so.6` is a shared
    object and `backup.2024` is whatever it was before — so one is skipped
    over rather than reported as the extension.
    """
    lowered = name.lower()
    for double in DOUBLE:
        if lowered.endswith("." + double):
            return double
    stem, dot, ext = lowered.rpartition(".")
    if not dot or not stem or not ext:
        return ""
    if ext.isdigit():
        inner = extension(stem)
        return inner if inner else ""
    # Nobody's type is twelve characters long; that is part of the name.
    return ext if len(ext) <= 12 and ext.isalnum() else ""


def category(name: str, is_dir: bool = False) -> str:
    """Which of the eleven this file belongs to."""
    if is_dir:
        return "folder"
    lowered = os.path.basename(name).lower()
    if lowered in NAMED:
        return NAMED[lowered]
    ext = extension(lowered)
    if ext in EXTENSIONS:
        return EXTENSIONS[ext]
    if lowered.startswith(".") and "." not in lowered[1:]:
        return "text"           # .bashrc, .gitignore, .profile
    return "other"


def colour(key: str) -> str:
    """The category's accent, or "" for the ones left uncoloured."""
    return CATEGORIES.get(key, CATEGORIES["other"])[1]


def label(key: str) -> str:
    return CATEGORIES.get(key, CATEGORIES["other"])[0]


def mime_type(path: str, is_dir: bool = False) -> str:
    """The content type, guessed from the name alone.

    From the name and not the contents: a listing of ten thousand files cannot
    afford to open any of them, and GIO's sniffing is for when the name has
    already failed. `describe` is where the honest-but-slow answer lives.
    """
    if is_dir:
        return "inode/directory"
    guess, _certain = Gio.content_type_guess(path, None)
    return guess or "application/octet-stream"


def type_label(path: str, is_dir: bool = False, is_link: bool = False) -> str:
    """A human description of the type, for the properties dialog and column."""
    if is_dir:
        return "Folder" if not is_link else "Folder (link)"
    mime = mime_type(path)
    described = Gio.content_type_get_description(mime) if mime else ""
    ext = extension(os.path.basename(path))
    if described and described.lower() not in ("unknown", "unknown file type"):
        text = described
    elif ext:
        text = f"{ext.upper()} file"
    else:
        text = "File"
    return text + " (link)" if is_link else text


def icon_for(path: str, is_dir: bool = False) -> Gio.Icon:
    """A themed icon with GIO's own fallback chain behind it."""
    if is_dir:
        return Gio.ThemedIcon.new_from_names(["folder-symbolic", "folder"])
    # Our own table beats GIO's on the handful of names it knows by heart:
    # GIO reads `go.mod` as an Amiga sound module, which is true of the
    # extension and wrong about the file.
    if os.path.basename(path).lower() in NAMED:
        return Gio.ThemedIcon.new_from_names(
            [CATEGORIES[NAMED[os.path.basename(path).lower()]][2],
             CATEGORIES["other"][2]])
    mime = mime_type(path)
    # GIO's answer for "no idea" is application/octet-stream, whose icon says
    # "binary blob". Our own categories know more than that about a file
    # called .bashrc or Makefile, so they get the last word when GIO shrugs.
    if mime and mime != "application/octet-stream":
        icon = Gio.content_type_get_symbolic_icon(mime)
        if icon is not None:
            return icon
    key = category(path)
    names = [CATEGORIES[key][2]]
    if key != "other":
        names.append(CATEGORIES["other"][2])
    return Gio.ThemedIcon.new_from_names(names)


def icon_name_for(path: str, is_dir: bool = False) -> str:
    """The first name of the icon above, for the places that want a string."""
    icon = icon_for(path, is_dir)
    names = icon.get_names() if isinstance(icon, Gio.ThemedIcon) else []
    return names[0] if names else "text-x-generic-symbolic"


# Which files are worth trying to make a picture of, and which are worth
# showing in the preview pane as themselves.
THUMBNAILABLE = {"image", "video"}
PREVIEWABLE_TEXT = {"code", "text"}

# Images GdkPixbuf can read without help. Anything else wants a thumbnailer.
PIXBUF_READABLE = {
    "png", "jpg", "jpeg", "jpe", "jfif", "gif", "bmp", "ico", "xpm", "svg",
    "svgz", "tiff", "tif", "pnm", "pbm", "pgm", "ppm", "tga", "qoi", "webp",
    "avif", "jxl", "heic", "heif",
}


def is_image(name: str) -> bool:
    return category(name) == "image"


def is_video(name: str) -> bool:
    return category(name) == "video"


def is_text_like(name: str) -> bool:
    """Would opening this in a text view make sense?"""
    return category(name) in PREVIEWABLE_TEXT


def is_archive(name: str) -> bool:
    return category(name) == "archive"
