"""What kind of file is this, and what can be done with it.

The question gets asked constantly — by the folder scan deciding what to put
in the strip, by the window deciding whether to show a transport bar, by the
export code deciding what it is allowed to write — so the answer lives in one
place.

It is answered by extension first and content type second, which is the
opposite of the usual advice, and deliberate. The usual advice is for
deciding whether a file is safe to open; this is for deciding what to *list*,
and listing a folder of ten thousand photographs cannot afford to read the
first bytes of each one. Where the extension is unknown or ambiguous the
content type is asked, and where a file is actually opened GdkPixbuf and
GStreamer both sniff for themselves anyway — so a JPEG called `.png` still
opens, it just sorts as a photograph either way.

The format lists are what is *claimed*; `available()` says what this machine
can really do, by asking GdkPixbuf which loaders it has and GStreamer which
decoders. A machine without the HEIF loader should say so when a HEIC file is
opened, not show a broken picture and leave the user guessing.
"""

from __future__ import annotations

import os

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, Gio  # noqa: E402

# Stills. The extensions are what the loaders on a well-equipped Linux box
# handle; `available()` filters this down to what is actually installed.
PHOTO = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp", ".avif",
    ".heic", ".heif", ".jxl", ".bmp", ".tif", ".tiff", ".tga", ".ico",
    ".icns", ".svg", ".svgz", ".qoi", ".pnm", ".pbm", ".pgm", ".ppm",
    ".xpm", ".xbm", ".dds", ".exr", ".ani",
}

# Camera raw. Separate from PHOTO because a raw file is not a picture until
# something has demosaiced it, the loader for them is often missing, and a
# folder of raws usually has JPEGs beside them that should be what is shown.
RAW = {
    ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng", ".orf",
    ".rw2", ".raf", ".pef", ".ptx", ".srw", ".kdc", ".dcr", ".mrw", ".3fr",
    ".fff", ".iiq", ".mos", ".x3f", ".erf", ".mef", ".rwl",
}

# Moving pictures.
VIDEO = {
    ".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".qt", ".wmv", ".asf",
    ".flv", ".f4v", ".mpg", ".mpeg", ".mpe", ".m1v", ".m2v", ".ts",
    ".mts", ".m2ts", ".tod", ".vob", ".ogv", ".ogm", ".3gp", ".3g2", ".rm",
    ".rmvb", ".divx", ".mxf", ".y4m", ".nut", ".dv", ".amv", ".mjpeg",
}

# Sound. The Eye plays it — a music file in a folder of videos should not be
# a dead end — but it has nothing to show, so it gets the album art if there
# is any and a waveform-less still if there is not.
AUDIO = {
    ".mp3", ".mp2", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac",
    ".wav", ".wv",
    ".ape", ".alac", ".wma", ".aiff", ".aif", ".aifc", ".mka", ".mid",
    ".midi", ".mod", ".spx", ".ac3", ".eac3", ".dts", ".amr", ".ra",
}

# Sidecar subtitles, in the order they are preferred when several sit beside
# the same video: the ones that can carry styling first.
SUBTITLE = (".ass", ".ssa", ".sub", ".srt", ".vtt", ".webvtt", ".sbv", ".smi",
            ".lrc", ".idx")

# Playlists we will read.
PLAYLIST = {".m3u", ".m3u8", ".pls", ".xspf"}

# Animated stills. These are photographs by file type and videos by
# behaviour, so they are decoded by GdkPixbuf and played by the still side of
# the app rather than by GStreamer — which keeps a 200-frame GIF off a
# pipeline that would take longer to start than the animation lasts.
ANIMATED = {".gif", ".webp", ".ani", ".png"}

PHOTO_ALL = PHOTO | RAW
EVERYTHING = PHOTO_ALL | VIDEO | AUDIO


def extension(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _content_kind(path: str) -> str | None:
    """What Gio makes of the name, when the extension was no help."""
    guess, _uncertain = Gio.content_type_guess(path, None)
    if not guess:
        return None
    if guess.startswith("image/"):
        return "photo"
    if guess.startswith("video/"):
        return "video"
    if guess.startswith("audio/"):
        return "audio"
    return None


def kind(path: str) -> str | None:
    """"photo", "raw", "video", "audio" — or None for anything else."""
    ext = extension(path)
    if ext in PHOTO:
        return "photo"
    if ext in RAW:
        return "raw"
    if ext in VIDEO:
        return "video"
    if ext in AUDIO:
        return "audio"
    if ext in PLAYLIST:
        # Before Gio is asked, which calls an .m3u `audio/x-mpegurl` and would
        # have the app try to decode a list of filenames as a song.
        return None
    return _content_kind(path)


def is_photo(path: str) -> bool:
    return kind(path) in ("photo", "raw")


def is_video(path: str) -> bool:
    return kind(path) == "video"


def is_audio(path: str) -> bool:
    return kind(path) == "audio"


def is_playable(path: str) -> bool:
    """Anything GStreamer should be handed rather than GdkPixbuf."""
    return kind(path) in ("video", "audio")


def is_animated(path: str) -> bool:
    """Might this still have more than one frame in it?

    A guess from the extension, because the only certain answer costs a
    decode. The caller loads it as an animation and finds out: a single-frame
    GIF or APNG comes back as an animation of one frame, which draws exactly
    as a still would.
    """
    return extension(path) in ANIMATED


def is_viewable(path: str) -> bool:
    return kind(path) is not None


def is_subtitle(path: str) -> bool:
    return extension(path) in SUBTITLE


def is_playlist(path: str) -> bool:
    return extension(path) in PLAYLIST


# --------------------------------------------------- what this box can do ----

# Extensions that are some other format wearing a different name, and so are
# handled by a loader that has never heard of them. There is no API that
# reports these — the JPEG loader registers .jpg, .jpeg and .jpe and simply
# does not mention that .jfif is the same file.
ALIASES = {
    ".jfif": ".jpeg",
    ".jpe": ".jpeg",
    ".svgz": ".svg",
    ".pbm": ".pnm",
    ".pgm": ".pnm",
    ".ppm": ".pnm",
    ".heif": ".heic",
    ".tif": ".tiff",
}

_loaders: set[str] | None = None


def pixbuf_extensions() -> set[str]:
    """Every extension GdkPixbuf has a loader for on this machine."""
    global _loaders
    if _loaders is None:
        found = set()
        for fmt in GdkPixbuf.Pixbuf.get_formats():
            for ext in fmt.get_extensions():
                found.add("." + ext.lower())
        _loaders = found
    return _loaders


def loader_for(path: str) -> bool:
    """Can this machine decode this still at all?

    Asked before a picture is opened, so that a HEIC on a box without the
    HEIF loader produces "no loader for .heic on this machine" rather than a
    grey rectangle. Raw is its own question — the pixbuf raw loader handles
    some cameras and not others, and there is no way to know which without
    trying.
    """
    ext = extension(path)
    loaders = pixbuf_extensions()
    if ext in RAW:
        return ".raw" in loaders or ext in loaders
    return ext in loaders or ALIASES.get(ext, ext) in loaders


def describe(path: str) -> str:
    """A short human name for the format — for the title bar and the toasts."""
    ext = extension(path).lstrip(".")
    if not ext:
        return kind(path) or "file"
    return ext.upper()


def sidecar_subtitles(path: str) -> list[str]:
    """Subtitle files sitting next to `path` that look like they belong to it.

    A video called `Film.mkv` is matched by `Film.srt`, `Film.en.srt` and
    `Film.English.forced.ass` — anything in the same folder whose name starts
    with the video's own, which is the convention every subtitle site follows
    and the only one worth implementing.
    """
    folder = os.path.dirname(path) or "."
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    if not stem:
        return []
    found = []
    try:
        entries = os.listdir(folder)
    except OSError:
        return []
    for name in entries:
        lower = name.lower()
        if not lower.startswith(stem) or not is_subtitle(name):
            continue
        found.append(os.path.join(folder, name))
    # Preferred format first, then alphabetically, so the order is stable and
    # an .ass with styling beats a plain .srt of the same film.
    return sorted(found, key=lambda p: (SUBTITLE.index(extension(p))
                                        if extension(p) in SUBTITLE else 99,
                                        p.lower()))


def subtitle_label(video: str, subtitle: str) -> str:
    """What to call a sidecar in the menu: the part of the name that differs.

    `Film.en.forced.srt` beside `Film.mkv` is "en.forced (SRT)", because the
    only part of a sidecar's name that carries information is the part the
    video's name does not already have.
    """
    stem = os.path.splitext(os.path.basename(video))[0]
    name = os.path.basename(subtitle)
    tail = os.path.splitext(name)[0]
    if tail.lower().startswith(stem.lower()):
        tail = tail[len(stem):].strip(" .-_")
    fmt = extension(subtitle).lstrip(".").upper()
    return f"{tail} ({fmt})" if tail else fmt
