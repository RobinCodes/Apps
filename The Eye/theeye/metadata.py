"""What a file says about itself.

Two very different jobs behind one interface.

*Moving pictures* are asked through `GstPbutils.Discoverer`, which opens the
file, lets GStreamer identify everything in it and hands back the duration,
the container, every stream with its codec and shape, and the tags. It is the
right tool and it costs a few hundred milliseconds, so it runs on a worker
thread and the result is cached.

*Stills* have no such tool here. The full answer would be gexiv2, which is a
separate library and is not installed on every machine — and an app that
shows no camera settings unless an optional library is present is an app that
shows no camera settings. So the EXIF this app cares about is parsed here:
about a hundred lines for the maker, the model, the lens, the exposure, the
aperture, the ISO, the focal length, when the shutter opened and where the
camera was standing.

That parser is deliberately narrow. It reads the three IFDs that matter and
the dozen tags anybody looks at, it never follows a pointer it has not range
checked, and every field is independently optional — a malformed tag loses
that tag and nothing else. It is a reader for files that might be corrupt,
so the whole thing is wrapped in the assumption that it will be.

What it does not do is write. Nothing in The Eye modifies a file's metadata;
a grade goes in a sidecar beside the picture, never into the picture.
"""

from __future__ import annotations

import datetime
import os
import struct

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")

from gi.repository import GdkPixbuf, GLib, Gst, GstPbutils  # noqa: E402

from . import media, player

# How long to let the discoverer work before giving up on a file. A local
# video is identified in well under a second; this limit is for a file on a
# network share that has gone away, where the alternative is a thread that
# never finishes.
DISCOVER_TIMEOUT = 8

# Container names, which are not codec names: the caps for a Matroska file say
# `video/x-matroska`, and nobody calls it that.
CONTAINERS = {
    "video/x-matroska": "Matroska", "video/webm": "WebM",
    "video/quicktime": "QuickTime / MP4", "video/x-msvideo": "AVI",
    "video/mpegts": "MPEG transport stream", "video/mpeg": "MPEG program stream",
    "application/x-3gp": "3GP", "video/x-ms-asf": "ASF / WMV",
    "application/ogg": "Ogg", "video/x-flv": "Flash video",
    "audio/x-m4a": "MPEG-4 audio", "application/x-id3": "MP3",
    "audio/x-flac": "FLAC", "audio/x-wav": "WAV",
}


def human_size(size: float) -> str:
    """Bytes as something readable, in powers of 1024."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}" if size < 100 else f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.0f} TB"


def human_time(seconds: float, long_form: bool = False) -> str:
    """Seconds as h:mm:ss, or m:ss when it is under an hour.

    Negative and unknown both come back as a dash of the right width, so that
    a seek bar's labels do not change size as a file loads — a duration that
    appears from nowhere and pushes the clock sideways is a small thing that
    looks careless.
    """
    if seconds is None or seconds < 0:
        return "-:--:--" if long_form else "-:--"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours or long_form:
        return f"{hours}:{minutes:02}:{secs:02}"
    return f"{minutes}:{secs:02}"


# ----------------------------------------------------------------- stills ----

def read_still(path: str) -> list[tuple[str, str]]:
    """A still's details, as label-and-value pairs in the order to show them.

    A list rather than a dict because the order is part of the answer, and
    pairs rather than a class because every consumer just puts them in rows.
    """
    rows: list[tuple[str, str]] = []
    try:
        stat = os.stat(path)
    except OSError:
        return rows

    fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(path)
    name = fmt.get_name().upper() if fmt else media.describe(path)
    rows.append(("File", os.path.basename(path)))
    rows.append(("Folder", os.path.dirname(path) or "."))
    if width > 0 and height > 0:
        pixels = width * height
        detail = f"{width} x {height}"
        if pixels >= 1_000_000:
            detail += f"   ({pixels / 1e6:.1f} megapixels)"
        rows.append(("Size", detail))
        rows.append(("Shape", aspect_name(width, height)))
    rows.append(("Format", name))
    rows.append(("On disk", human_size(stat.st_size)))
    rows.append(("Modified", _stamp(stat.st_mtime)))

    exif = read_exif(path)
    for label, key in (("Taken", "taken"), ("Camera", "camera"),
                       ("Lens", "lens"), ("Exposure", "exposure"),
                       ("Aperture", "aperture"), ("ISO", "iso"),
                       ("Focal length", "focal"), ("Flash", "flash"),
                       ("Where", "where")):
        value = exif.get(key)
        if value:
            rows.append((label, value))
    return rows


def aspect_name(width: int, height: int) -> str:
    """"3:2 (landscape)" — the ratio reduced, and which way up it is.

    Reduced by the greatest common divisor, then given a name only when it
    comes out as one of the ratios that has one. A 4000x2999 photograph is
    not 4:3 and saying so would be a lie; it gets its decimal instead.
    """
    import math

    if width <= 0 or height <= 0:
        return ""
    orientation = ("square" if width == height else
                   "landscape" if width > height else "portrait")
    divisor = math.gcd(width, height)
    w, h = width // divisor, height // divisor
    if max(w, h) <= 40:
        return f"{w}:{h} ({orientation})"
    ratio = width / height
    for (rw, rh) in ((16, 9), (4, 3), (3, 2), (5, 4), (21, 9), (1, 1),
                     (9, 16), (3, 4), (2, 3)):
        if abs(ratio - rw / rh) < 0.004:
            return f"about {rw}:{rh} ({orientation})"
    return f"{ratio:.2f} to 1 ({orientation})"


def _stamp(epoch: float) -> str:
    try:
        return datetime.datetime.fromtimestamp(epoch).strftime("%-d %B %Y, %H:%M")
    except (ValueError, OSError):
        try:
            return datetime.datetime.fromtimestamp(epoch).strftime("%d %B %Y, %H:%M")
        except (ValueError, OSError):
            return ""


# ------------------------------------------------------------------- EXIF ----
# Only the tags anybody reads, in the three directories they live in.

IFD0 = {0x010F: "make", 0x0110: "model", 0x0112: "orientation",
        0x0132: "datetime", 0x013B: "artist", 0x8298: "copyright",
        0x010E: "description"}

EXIF_IFD = {0x829A: "exposure_time", 0x829D: "fnumber", 0x8822: "program",
            0x8827: "iso", 0x9003: "datetime_original", 0x9004: "datetime_digitised",
            0x920A: "focal_length", 0xA405: "focal_35", 0x9207: "metering",
            0x9209: "flash", 0xA402: "exposure_mode", 0xA403: "white_balance",
            0xA434: "lens_model", 0x9286: "comment", 0x9201: "shutter_speed",
            0x9202: "aperture_value"}

GPS_IFD = {1: "lat_ref", 2: "lat", 3: "lon_ref", 4: "lon",
           5: "alt_ref", 6: "alt"}

# Pointers from IFD0 to the other two.
EXIF_POINTER = 0x8769
GPS_POINTER = 0x8825

# bytes per component, by TIFF type
TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
              11: 4, 12: 8}

FLASH_FIRED = {0x00: "No", 0x01: "Yes", 0x05: "Yes, no return detected",
               0x07: "Yes", 0x09: "Yes, compulsory", 0x0D: "Yes, compulsory",
               0x0F: "Yes, compulsory", 0x10: "No, compulsory",
               0x18: "No, auto", 0x19: "Yes, auto", 0x1D: "Yes, auto",
               0x1F: "Yes, auto", 0x20: "No flash fitted"}

# A JPEG's EXIF lives in an APP1 segment; a TIFF is one all the way down.
# Nothing else is attempted — PNG, WebP and HEIF can all carry EXIF, in three
# different containers, and guessing wrong is worse than saying nothing.
EXIF_FORMATS = {".jpg", ".jpeg", ".jpe", ".jfif", ".tif", ".tiff"}

MAX_EXIF = 256 * 1024


def read_exif(path: str) -> dict:
    """The camera's own account of a photograph, as far as it is readable.

    Every value is optional and every failure is local: a corrupt aperture
    costs the aperture. The whole function returning an empty dict is the
    normal case for a picture that was not taken by a camera.
    """
    fields = _fields(path)
    return _present(fields) if fields else {}


def taken(path: str) -> datetime.datetime | None:
    """When the camera says the picture was taken, as a time — or None.

    What `read_exif` puts in words for the panel, kept as a value for the
    things that file a photograph by its day.
    """
    fields = _fields(path)
    for key in ("datetime_original", "datetime_digitised", "datetime"):
        text = fields.get(key)
        if isinstance(text, str):
            try:
                return datetime.datetime.strptime(text.strip()[:19],
                                                  "%Y:%m:%d %H:%M:%S")
            except ValueError:
                continue
    return None


def _fields(path: str) -> dict:
    """The raw tags, or {} for anything without readable ones."""
    if media.extension(path) not in EXIF_FORMATS:
        return {}
    try:
        with open(path, "rb") as handle:
            head = handle.read(4)
            if head[:2] == b"\xFF\xD8":
                raw = _jpeg_exif(handle)
            elif head[:2] in (b"II", b"MM"):
                handle.seek(0)
                raw = handle.read(MAX_EXIF)
            else:
                return {}
    except OSError:
        return {}
    if not raw:
        return {}
    try:
        return _parse_tiff(raw)
    except Exception:                                   # noqa: BLE001
        return {}


def _jpeg_exif(handle) -> bytes:
    """Walk a JPEG's segments to the APP1 that starts with "Exif\\0\\0"."""
    handle.seek(2)
    while True:
        marker = handle.read(2)
        if len(marker) < 2 or marker[0] != 0xFF:
            return b""
        kind = marker[1]
        if kind in (0xD8, 0xD9) or 0xD0 <= kind <= 0xD7:
            continue
        if kind == 0xDA:        # start of scan: the pixels, and no more tags
            return b""
        length = handle.read(2)
        if len(length) < 2:
            return b""
        size = struct.unpack(">H", length)[0] - 2
        if size <= 0 or size > MAX_EXIF:
            if size > 0:
                handle.seek(size, os.SEEK_CUR)
            continue
        body = handle.read(size)
        if kind == 0xE1 and body[:6] == b"Exif\x00\x00":
            return body[6:]
    return b""


def _parse_tiff(raw: bytes) -> dict:
    if len(raw) < 8:
        return {}
    order = "<" if raw[:2] == b"II" else ">" if raw[:2] == b"MM" else None
    if order is None:
        return {}
    magic, first = struct.unpack(order + "HI", raw[2:8])
    if magic != 42 or not 8 <= first < len(raw):
        return {}

    fields: dict = {}
    pointers: dict = {}
    _read_ifd(raw, order, first, IFD0, fields, pointers)
    for pointer, table, prefix in ((EXIF_POINTER, EXIF_IFD, ""),
                                   (GPS_POINTER, GPS_IFD, "gps_")):
        offset = pointers.get(pointer)
        if isinstance(offset, int) and 8 <= offset < len(raw):
            _read_ifd(raw, order, offset, table, fields, {}, prefix)
    return fields


def _read_ifd(raw, order, offset, table, fields, pointers, prefix=""):
    """One image file directory: a count, then that many twelve-byte entries."""
    if offset + 2 > len(raw):
        return
    count = struct.unpack(order + "H", raw[offset:offset + 2])[0]
    # A corrupt count can claim sixty thousand entries in a two-kilobyte
    # block, so it is bounded by what is actually there.
    count = min(count, max(0, (len(raw) - offset - 2) // 12))
    for index in range(count):
        at = offset + 2 + index * 12
        tag, kind, components = struct.unpack(order + "HHI", raw[at:at + 8])
        if tag in (EXIF_POINTER, GPS_POINTER):
            value = _value(raw, order, kind, components, at + 8)
            if isinstance(value, (int, float)):
                pointers[tag] = int(value)
            continue
        name = table.get(tag)
        if name is None:
            continue
        value = _value(raw, order, kind, components, at + 8)
        if value is not None:
            fields[prefix + name] = value


def _value(raw, order, kind, components, at):
    """One entry's value, from the entry itself or from where it points."""
    size = TYPE_SIZES.get(kind)
    if size is None or components <= 0 or components > 1 << 16:
        return None
    total = size * components
    if total > 4:
        offset = struct.unpack(order + "I", raw[at:at + 4])[0]
        if offset + total > len(raw):
            return None
        data = raw[offset:offset + total]
    else:
        data = raw[at:at + total]

    if kind == 2:               # ASCII, NUL terminated
        text = data.split(b"\x00")[0]
        try:
            return text.decode("utf-8", "replace").strip() or None
        except Exception:                               # noqa: BLE001
            return None
    if kind == 7:               # UNDEFINED; only used here for comments
        return None
    codes = {1: "B", 3: "H", 4: "I", 6: "b", 8: "h", 9: "i"}
    if kind in codes:
        values = struct.unpack(order + codes[kind] * components, data)
        return values[0] if components == 1 else list(values)
    if kind in (5, 10):         # RATIONAL and its signed twin
        code = "II" if kind == 5 else "ii"
        out = []
        for i in range(components):
            num, den = struct.unpack(order + code, data[i * 8:i * 8 + 8])
            out.append((num / den) if den else 0.0)
        return out[0] if components == 1 else out
    return None


def _present(fields: dict) -> dict:
    """The raw tags as the strings a panel can show."""
    out: dict = {}
    maker = str(fields.get("make", "")).strip()
    model = str(fields.get("model", "")).strip()
    if model:
        # Most makers put their own name at the front of the model as well,
        # and "NIKON CORPORATION NIKON D750" is not what anybody wants to read.
        first = maker.split()[0] if maker else ""
        if first and model.upper().startswith(first.upper()):
            out["camera"] = model
        else:
            out["camera"] = f"{maker} {model}".strip()
    elif maker:
        out["camera"] = maker

    lens = fields.get("lens_model")
    if lens:
        out["lens"] = str(lens).strip()

    exposure = fields.get("exposure_time")
    if isinstance(exposure, (int, float)) and exposure > 0:
        out["exposure"] = (f"1/{round(1 / exposure)} s" if exposure < 1
                           else f"{exposure:g} s")
    fnumber = fields.get("fnumber")
    if isinstance(fnumber, (int, float)) and fnumber > 0:
        out["aperture"] = f"f/{fnumber:g}"
    iso = fields.get("iso")
    if isinstance(iso, list):
        iso = iso[0] if iso else None
    if isinstance(iso, (int, float)) and iso > 0:
        out["iso"] = f"ISO {int(iso)}"

    focal = fields.get("focal_length")
    if isinstance(focal, (int, float)) and focal > 0:
        text = f"{focal:g} mm"
        equivalent = fields.get("focal_35")
        if (isinstance(equivalent, (int, float)) and equivalent > 0
                and abs(equivalent - focal) > 1):
            text += f"   ({int(equivalent)} mm equivalent)"
        out["focal"] = text

    flash = fields.get("flash")
    if isinstance(flash, int):
        out["flash"] = FLASH_FIRED.get(flash, "Yes" if flash & 1 else "No")

    taken = (fields.get("datetime_original") or fields.get("datetime_digitised")
             or fields.get("datetime"))
    if isinstance(taken, str) and taken:
        out["taken"] = _exif_date(taken)

    where = _coordinates(fields)
    if where:
        out["where"] = where
    if fields.get("artist"):
        out["artist"] = str(fields["artist"])
    if fields.get("description"):
        out["description"] = str(fields["description"])
    if isinstance(fields.get("orientation"), int):
        out["orientation"] = fields["orientation"]
    return out


def _exif_date(text: str) -> str:
    """EXIF's "2024:06:01 18:22:04" as something written in words."""
    try:
        when = datetime.datetime.strptime(text.strip()[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return text.strip()
    try:
        return when.strftime("%-d %B %Y, %H:%M")
    except ValueError:
        return when.strftime("%d %B %Y, %H:%M")


def _coordinates(fields: dict) -> str:
    """GPS degrees, minutes and seconds as a decimal pair.

    The reference letters decide the sign, and a missing one means the whole
    coordinate is untrustworthy — a latitude without a hemisphere is two
    places on Earth.
    """
    lat, lon = fields.get("gps_lat"), fields.get("gps_lon")
    lat_ref = str(fields.get("gps_lat_ref", "")).upper()[:1]
    lon_ref = str(fields.get("gps_lon_ref", "")).upper()[:1]
    if not (isinstance(lat, list) and isinstance(lon, list)):
        return ""
    if len(lat) < 3 or len(lon) < 3 or lat_ref not in "NS" or lon_ref not in "EW":
        return ""
    def decimal(parts):
        return parts[0] + parts[1] / 60 + parts[2] / 3600
    north = decimal(lat) * (-1 if lat_ref == "S" else 1)
    east = decimal(lon) * (-1 if lon_ref == "W" else 1)
    if not (-90 <= north <= 90 and -180 <= east <= 180):
        return ""
    text = f"{abs(north):.5f}° {'S' if north < 0 else 'N'}, " \
           f"{abs(east):.5f}° {'W' if east < 0 else 'E'}"
    altitude = fields.get("gps_alt")
    if isinstance(altitude, (int, float)) and altitude:
        below = fields.get("gps_alt_ref") == 1
        text += f"   ({'-' if below else ''}{altitude:.0f} m)"
    return text


# ------------------------------------------------------- moving pictures ----

_discoverer = None


def read_media(path: str, timeout: int = DISCOVER_TIMEOUT) -> list[tuple[str, str]]:
    """A video or sound file's details. Blocking — use `jobs.run`."""
    rows: list[tuple[str, str]] = []
    try:
        stat = os.stat(path)
    except OSError:
        return rows
    rows.append(("File", os.path.basename(path)))
    rows.append(("Folder", os.path.dirname(path) or "."))
    rows.append(("On disk", human_size(stat.st_size)))
    rows.append(("Modified", _stamp(stat.st_mtime)))

    info = discover(path, timeout)
    if info is None:
        rows.append(("Format", media.describe(path)))
        return rows

    duration = info.get_duration() / Gst.SECOND if info.get_duration() else 0
    if duration > 0:
        rows.append(("Length", human_time(duration, long_form=True)))
        if stat.st_size and duration:
            rows.append(("Bit rate",
                         f"{stat.st_size * 8 / duration / 1e6:.2f} Mbit/s overall"))
    container = info.get_stream_info()
    if container is not None:
        caps = container.get_caps()
        if caps is not None and caps.get_size():
            name = caps.get_structure(0).get_name()
            rows.append(("Container", CONTAINERS.get(name)
                         or player.CODEC_NAMES.get(name, name)))

    for stream in info.get_video_streams():
        rows.append(("Video", _video_line(stream)))
    for stream in info.get_audio_streams():
        rows.append(("Audio", _audio_line(stream)))
    for stream in info.get_subtitle_streams():
        language = stream.get_language() or ""
        name = player.LANGUAGES.get(language.lower()[:3], language) or "Subtitles"
        rows.append(("Subtitles", name))

    tags = info.get_tags()
    if tags is not None:
        for label, tag in (("Title", Gst.TAG_TITLE), ("Artist", Gst.TAG_ARTIST),
                           ("Album", Gst.TAG_ALBUM), ("Date", Gst.TAG_DATE_TIME),
                           ("Genre", Gst.TAG_GENRE),
                           ("Comment", Gst.TAG_COMMENT),
                           ("Encoder", Gst.TAG_ENCODER)):
            ok, value = tags.get_string(tag)
            if ok and value:
                rows.append((label, value))
            elif tag == Gst.TAG_DATE_TIME:
                ok, when = tags.get_date_time(tag)
                if ok and when is not None:
                    rows.append((label, when.to_iso8601_string() or ""))
    return [(label, value) for label, value in rows if value]


def _video_line(stream) -> str:
    caps = stream.get_caps()
    codec = player.CODEC_NAMES.get(
        caps.get_structure(0).get_name() if caps and caps.get_size() else "",
        "") or (caps.get_structure(0).get_name() if caps and caps.get_size() else "")
    bits = [codec, f"{stream.get_width()}x{stream.get_height()}"]
    denominator = stream.get_framerate_denom()
    if denominator:
        rate = stream.get_framerate_num() / denominator
        if rate > 0:
            bits.append(f"{rate:g} fps")
    if stream.get_bitrate():
        bits.append(f"{stream.get_bitrate() / 1e6:.2f} Mbit/s")
    if stream.is_interlaced():
        bits.append("interlaced")
    par_n, par_d = stream.get_par_num(), stream.get_par_denom()
    if par_d and par_n != par_d:
        bits.append(f"pixel aspect {par_n}:{par_d}")
    return "   ".join(b for b in bits if b)


def _audio_line(stream) -> str:
    caps = stream.get_caps()
    codec = player.CODEC_NAMES.get(
        caps.get_structure(0).get_name() if caps and caps.get_size() else "",
        "") or (caps.get_structure(0).get_name() if caps and caps.get_size() else "")
    bits = [codec]
    channels = stream.get_channels()
    if channels:
        bits.append(player.CHANNELS.get(channels, f"{channels} channels"))
    if stream.get_sample_rate():
        bits.append(f"{stream.get_sample_rate() / 1000:g} kHz")
    if stream.get_bitrate():
        bits.append(f"{stream.get_bitrate() / 1000:.0f} kbit/s")
    language = stream.get_language()
    if language:
        bits.append(player.LANGUAGES.get(language.lower()[:3], language))
    return "   ".join(b for b in bits if b)


def discover(path: str, timeout: int = DISCOVER_TIMEOUT):
    """GStreamer's own description of a file, or None. Blocking."""
    global _discoverer
    if not player.init():
        return None
    try:
        if _discoverer is None:
            _discoverer = GstPbutils.Discoverer.new(timeout * Gst.SECOND)
        return _discoverer.discover_uri(Gst.filename_to_uri(os.path.abspath(path)))
    except GLib.Error:
        return None
    except Exception:                                   # noqa: BLE001
        return None


def duration_of(path: str) -> float:
    """How long a file is, in seconds — 0 when it cannot be told.

    Used by the strip to label a video before it has been played, and by the
    resume logic to know what a remembered position is a fraction of.
    """
    info = discover(path, 4)
    if info is None or not info.get_duration():
        return 0.0
    return info.get_duration() / Gst.SECOND


def rows_for(path: str) -> list[tuple[str, str]]:
    """The right reader for whatever this is."""
    if media.is_playable(path):
        return read_media(path)
    return read_still(path)
