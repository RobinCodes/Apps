"""What can be checked without a screen, and what needs one.

    python3 tests/test_theeye.py

Most of this app is arithmetic about colour, and most of the suite is
therefore arithmetic: a known colour through a known grade, compared against
the number it should come out as. That part needs no media and no display, and
it is where the real bugs would be — a transposed matrix or a channel swap
looks plausible on screen and is obvious in a number.

The rest needs GTK, and some of it needs GStreamer. Those tests are skipped
rather than failed when the machine has not got what they need, because "not
installed here" and "broken" are different answers and only one is worth
acting on.

Nothing here puts a window on screen. Widgets are built and drawn into
offscreen render nodes but never presented, so running the suite while using
the machine does not interrupt anything — and every video is opened muted, for
the same reason.

Test media is built, not checked in: see tests/fixtures.py. It costs about a
fifth of a second and two hundred kilobytes.
"""

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

scratch = tempfile.mkdtemp(prefix="the-eye-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(scratch, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(scratch, "cache")
# An app built by a test must not go to the network for an update.
os.environ["APPS_NO_UPDATE"] = "1"
# The second brain pages are filed into: a scratch home, never the real one.
os.environ["ORDER_HOME"] = os.path.join(scratch, "home")
os.environ.pop("ANAM_HOME", None)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")

from gi.repository import Gdk, GLib, Gsk, Gtk  # noqa: E402

import fixtures  # noqa: E402

from theeye import (config as configuration, editor, effects, export,  # noqa: E402
                    grade, library, looks, media, metadata, photo, player,
                    shortcuts, theme, thumbs, widgets)

MEDIA = os.path.join(scratch, "media")
missing_media = fixtures.build(MEDIA)

failures = []
skipped = []


def check(name, fn):
    try:
        fn()
    except Exception as exc:                            # noqa: BLE001
        import traceback

        failures.append((name, exc))
        print(f"  FAIL  {name}")
        for line in traceback.format_exc().strip().splitlines()[-4:]:
            print(f"        {line}")
        return
    print(f"  ok  {name}")


def skip(name, why):
    skipped.append((name, why))
    print(f"  --  {name}  ({why})")


def needs_media(name, fn, *files):
    gone = [f for f in files if not os.path.exists(os.path.join(MEDIA, f))]
    if gone:
        skip(name, f"could not build {', '.join(gone)}")
    else:
        check(name, fn)


def needs_gtk(name, fn):
    if DISPLAY is None:
        skip(name, "no display")
    else:
        check(name, fn)


DISPLAY = None


def start_gtk():
    global DISPLAY
    if Gtk.init_check():
        DISPLAY = Gdk.Display.get_default()
    return DISPLAY is not None


def near(got, want, tolerance=2, what=""):
    if abs(got - want) > tolerance:
        raise AssertionError(f"{what or 'value'}: got {got}, wanted {want}"
                             f" (±{tolerance})")


# ====================================================== colour arithmetic ====
# No GTK, no media. If anything in here is wrong, every picture the app shows
# is wrong in a way nobody would notice by looking.

def test_matrix_identity():
    settings = grade.Grade()
    rows, offset = settings.matrix()
    assert rows == grade.IDENTITY, "a neutral grade is not the identity matrix"
    assert offset == grade.ZERO
    assert settings.is_neutral()
    assert settings.cost() == 1, "a neutral grade should cost one draw"


def test_greys_stay_grey():
    """The property that makes saturation and hue safe at any strength.

    Both matrices are built so that every row sums to one, which is exactly
    the statement "a grey is unchanged". A matrix that failed this would tint
    every neutral in every photograph.
    """
    for amount in (-1.0, -0.5, 0.25, 1.0):
        for row in grade.saturation_matrix(amount)[:3]:
            near(sum(row[:3]), 1.0, 1e-6, f"saturation {amount} row sum")
    for degrees in (-180, -60, 30, 90, 179):
        for row in grade.hue_matrix(degrees)[:3]:
            near(sum(row[:3]), 1.0, 1e-5, f"hue {degrees} row sum")


def test_exposure_is_in_stops():
    """One stop must double the light, not double the number.

    The values a colour matrix works on are sRGB-encoded, so a scale of two
    is nearly three stops. The scale for one stop is 2^(1/2.2).
    """
    for stops in (-2.0, -1.0, 1.0, 2.5):
        scale = grade.exposure_matrix(stops)[0][0]
        before, after = 0.5, 0.5 * scale
        ratio = (after ** grade.EXPOSURE_GAMMA) / (before ** grade.EXPOSURE_GAMMA)
        near(ratio, 2.0 ** stops, 0.01, f"{stops} stops of light")


def test_contrast_holds_middle_grey():
    for amount in (-0.8, -0.3, 0.4, 1.0):
        rows, offset = grade.contrast_matrix(amount)
        out = grade.apply(rows, offset, (grade.MIDDLE_GREY,) * 3 + (1.0,))
        near(out[0], grade.MIDDLE_GREY, 1e-6, f"middle grey at contrast {amount}")


def test_balance_holds_brightness():
    """Warming a picture must not also brighten it."""
    for temperature in (-1.0, -0.4, 0.4, 1.0):
        rows = grade.balance_matrix(temperature, 0.0)
        gains = (rows[0][0], rows[1][1], rows[2][2])
        luma = sum(w * g for w, g in zip(grade.LUMA, gains))
        near(luma, 1.0, 1e-6, f"luma of the gains at temperature {temperature}")


def test_bright_pass_is_a_threshold():
    rows, offset = grade.bright_pass(0.6, 2.5)
    for value, want in ((0.0, 0.0), (0.3, 0.0), (0.6, 0.0), (0.8, 0.5), (1.0, 1.0)):
        out = grade.apply(rows, offset, (value, value, value, 1.0))
        near(out[0], want, 1e-6, f"bright pass of {value}")


def test_monochrome_and_duotone():
    rows = grade.monochrome_matrix()
    out = grade.apply(rows, grade.ZERO, (1.0, 0.0, 0.0, 1.0))
    near(out[0], grade.LUMA[0], 1e-6, "red through monochrome")
    assert out[0] == out[1] == out[2], "monochrome did not produce a grey"
    rows = grade.monochrome_matrix((1.0, 0.5, 0.0))
    out = grade.apply(rows, grade.ZERO, (1.0, 1.0, 1.0, 1.0))
    near(out[0], 1.0, 1e-6, "duotone white, red channel")
    near(out[1], 0.5, 1e-6, "duotone white, green channel")
    near(out[2], 0.0, 1e-6, "duotone white, blue channel")


def test_composition_order():
    """Composing two matrices must mean "do the first, then the second"."""
    first, first_off = grade.contrast_matrix(0.5)
    second = grade.exposure_matrix(1.0)
    rows, offset = grade.compose(first, first_off, second, grade.ZERO)
    colour = (0.3, 0.4, 0.5, 1.0)
    step = grade.apply(first, first_off, colour)
    want = grade.apply(second, grade.ZERO, step)
    got = grade.apply(rows, offset, colour)
    for index in range(3):
        near(got[index], want[index], 1e-6, f"composed channel {index}")


def test_alpha_is_never_touched():
    """A grade changes how a picture looks, never how opaque it is."""
    for key, value in (("exposure", 2.0), ("contrast", 1.0), ("saturation", -1.0),
                       ("hue", 120.0), ("blacks", 1.0), ("whites", -1.0),
                       ("temperature", 1.0), ("monochrome", True)):
        rows, offset = grade.Grade({key: value}).matrix()
        assert rows[3] == (0.0, 0.0, 0.0, 1.0), f"{key} touched the alpha row"
        near(offset[3], 0.0, 1e-9, f"{key} alpha offset")


def test_grade_rejects_nonsense():
    settings = grade.Grade({"exposure": 99, "saturation": "lots",
                            "monochrome": 1, "wash_mode": "invented",
                            "nonexistent": 5, "rotation": 450})
    near(settings["exposure"], 3.0, 1e-9, "exposure clamped to its range")
    assert settings["saturation"] == 0.0, "a string got into a number"
    assert settings["monochrome"] is False, "an int got into a boolean"
    assert settings["wash_mode"] == "soft-light", "an invented blend mode stuck"
    assert "nonexistent" not in settings


def test_grade_round_trips():
    settings = grade.Grade(looks.by_key("cinema")["values"])
    again = grade.Grade.from_json(settings.to_json())
    assert dict(again) == dict(settings), "a grade did not survive JSON"
    assert "exposure" not in settings.to_json(), "the JSON holds neutral values"


def test_every_look_is_valid_and_does_something():
    for look in looks.LOOKS:
        settings = grade.Grade(look["values"])
        for key, value in look["values"].items():
            assert key in grade.DEFAULTS, f"{look['key']} sets unknown {key}"
            assert settings[key] == value, \
                f"{look['key']}'s {key} was rejected: {value!r} -> {settings[key]!r}"
        if look["key"] != "none":
            assert not settings.is_neutral(), f"{look['key']} does nothing"
        assert looks.matching(look["values"]) == look["key"], \
            f"{look['key']} does not identify itself"
        assert look["blurb"], f"{look['key']} has no description"
        assert look["category"] in looks.CATEGORIES


def test_every_control_has_a_slider():
    """The editor is built from a table; it must cover the grade."""
    keyed = {control.key for _n, _b, controls in editor.GROUPS
             for control in controls}
    for key in grade.RANGES:
        if key.endswith("_amount"):
            continue                    # driven by a tint row, not a slider
        assert key in keyed, f"{key} has no slider in the editor"
    for key in keyed:
        assert key in grade.DEFAULTS, f"the editor has a slider for unknown {key}"


# ============================================================ the themes ====

def test_text_is_readable_on_every_theme():
    for scheme in theme.ordered():
        ratio = theme.contrast(scheme.fg, scheme.bg)
        assert ratio >= 4.5, f"{scheme.key}: body text is {ratio:.2f}:1"
        ratio = theme.contrast(scheme.dim, scheme.bg)
        assert ratio >= 4.5, f"{scheme.key}: quiet text is {ratio:.2f}:1"
        ratio = theme.contrast(scheme.accent, scheme.bg)
        assert ratio >= 3.0, f"{scheme.key}: the accent is {ratio:.2f}:1"


def test_every_matte_is_neutral():
    """The surround a picture sits on must not have a hue of its own.

    The eye judges a colour against its surroundings, so a warm matte makes
    every photograph on it look cold. This is the check that keeps a theme
    honest about it.
    """
    for scheme in theme.ordered():
        spread = scheme.neutral_matte()
        assert spread <= 0.012, \
            f"{scheme.key}: the matte {scheme.matte} has a hue (spread {spread:.4f})"


def test_theme_lookup_survives_nonsense():
    assert theme.get("no such theme").key == theme.DEFAULT
    assert theme.get("").key == theme.DEFAULT


# =========================================================== the settings ====

def test_config_defaults_and_validation():
    config = configuration.Config()
    assert config.trimmed() == {}, "a fresh config is not all defaults"
    config.take({"theme": "ember", "translucency": 99.0, "volume": -3,
                 "fit": "invented", "motion": 3, "window_width": 1})
    assert config["theme"] == "ember"
    near(config["translucency"], 1.0, 1e-9, "translucency clamped")
    near(config["volume"], 0.0, 1e-9, "volume clamped")
    assert config["fit"] == "window", "an invented fit mode stuck"
    assert config["motion"] is True, "an int got into a boolean"
    near(config["window_width"], 360, 1e-9, "width clamped to the minimum")


def test_config_saves_only_what_changed():
    config = configuration.Config()
    config["theme"] = "obsidian"
    config.save()
    again = configuration.Config()
    assert again["theme"] == "obsidian"
    assert again.trimmed() == {"theme": "obsidian"}, again.trimmed()


def test_resume_positions():
    """Three positions are not worth remembering, and one is."""
    state = configuration.State()
    state.remember_position("/f/film.mkv", 1200, 7200, 30, 15)
    state.remember_position("/f/early.mkv", 2, 7200, 30, 15)
    state.remember_position("/f/credits.mkv", 7195, 7200, 30, 15)
    state.remember_position("/f/short.mkv", 5, 20, 30, 15)
    assert sorted(state["resume"]) == ["/f/film.mkv"], sorted(state["resume"])
    near(state.position_for("/f/film.mkv"), 1200, 0.01)
    near(state.progress_for("/f/film.mkv"), 1200 / 7200, 1e-6)
    state.forget_position("/f/film.mkv")
    assert state.position_for("/f/film.mkv") == 0.0


def test_state_lists_are_capped():
    state = configuration.State()
    for index in range(configuration.RESUME_LIMIT + 40):
        state.remember_position(f"/f/{index}.mkv", 100, 400, 30, 15)
    assert len(state["resume"]) <= configuration.RESUME_LIMIT
    for index in range(60):
        state.remember_folder(f"/f/{index}")
    assert len(state["recent_folders"]) <= configuration.RECENT_LIMIT


# ======================================================= what a file is ====

def test_format_recognition():
    for name, kind in (("a.JPG", "photo"), ("b.heic", "photo"),
                       ("c.mkv", "video"), ("d.CR3", "raw"),
                       ("e.flac", "audio"), ("f.gif", "photo"),
                       ("g.srt", None), ("h.m3u", None), ("i.txt", None)):
        assert media.kind(name) == kind, f"{name} came back as {media.kind(name)}"
    assert media.is_playlist("list.m3u")
    assert media.is_subtitle("film.srt")
    assert media.is_animated("x.gif") and media.is_animated("x.webp")


def test_every_claimed_photo_format_has_a_loader():
    """The claims must describe this machine, not a wish."""
    gone = [ext for ext in media.claimed() if not media.loader_for("x" + ext)]
    assert not gone, f"claimed but not loadable here: {gone}"


def test_claims_follow_the_loaders():
    """A format is claimed where its loader is, and recognised everywhere.

    Recognised, so that a QOI on a machine without the loader is listed and
    explained; not claimed, so that nothing offers to open one.
    """
    real = media._loaders
    try:
        media._loaders = {".jpeg", ".jpg", ".png", ".gif", ".tiff", ".tif", ".webp"}
        claimed = media.claimed()
        assert ".qoi" not in claimed and ".exr" not in claimed, sorted(claimed)
        assert ".webp" in claimed and ".jfif" in claimed, "an alias lost its loader"
        assert not claimed & media.RAW, "raw claimed with no raw loader"
        media._loaders = {".png"}
        assert media.BASE <= media.claimed(), "the base four stopped being claimed"
        media._loaders = {".png", ".qoi", ".raw"}
        assert ".qoi" in media.claimed() and ".nef" in media.claimed()
        assert media.kind("x.qoi") == "photo", "an unclaimed format stopped being a photo"
        types = media.claimed_types(playable=False)
        assert not types["video"] and not types["audio"], "video claimed with no player"
    finally:
        media._loaders = real


def test_the_installer_is_told_what_opens():
    """`the-eye.py --list-types`, which windows/install.ps1 registers from."""
    import io

    from theeye import app

    out = io.StringIO()
    app.list_types(out)
    lines = [line.split() for line in out.getvalue().splitlines()]
    assert lines and all(len(bits) == 2 for bits in lines), lines[:5]
    photos = {ext for kind, ext in lines if kind == "photo"}
    assert photos == media.claimed() - media.RAW, photos ^ (media.claimed() - media.RAW)
    assert {kind for kind, _ext in lines} <= {"photo", "raw", "video", "audio"}


def test_a_path_is_found_however_it_is_spelled():
    """MSYS2's Python joins with "/" and scandir with "\\"; Windows takes
    either, and any case. The file opened must be the one the reel lands on."""
    folder = _folder_of_files()
    items, _ = library.scan(folder)
    reel = library.Reel(items, order="name")
    target = os.path.join(folder, "IMG_10.jpg")
    assert reel.index_of(target) >= 0, "a scanned path is not the joined path"
    assert all(item.path == os.path.join(folder, item.name) for item in items)
    spelled = target.replace("/", "\\") if os.name == "nt" else target
    if os.name == "nt":
        spelled = spelled.upper()
    assert reel.index_of(spelled) == reel.index_of(target), spelled
    assert library.same_path(folder + os.sep + "x", os.path.join(folder, "x"))


def test_sidecar_subtitles():
    folder = os.path.join(scratch, "subs")
    os.makedirs(folder, exist_ok=True)
    for name in ("Film.mkv", "Film.srt", "Film.en.srt",
                 "Film.English.forced.ass", "Other.srt"):
        open(os.path.join(folder, name), "w").close()
    found = media.sidecar_subtitles(os.path.join(folder, "Film.mkv"))
    names = [os.path.basename(path) for path in found]
    assert names == ["Film.English.forced.ass", "Film.en.srt", "Film.srt"], names
    labels = [media.subtitle_label(os.path.join(folder, "Film.mkv"), p)
              for p in found]
    assert labels == ["English.forced (ASS)", "en (SRT)", "SRT"], labels


# ============================================================= the folder ====

def _folder_of_files():
    folder = os.path.join(scratch, "reel")
    if os.path.isdir(folder):
        return folder
    os.makedirs(folder)
    os.makedirs(os.path.join(folder, "sub"))
    names = ["IMG_9.jpg", "IMG_10.jpg", "IMG_2.jpg", "Ähre.png", "clip.mkv",
             "song.flac", ".hidden.jpg", "notes.txt", "a.CR2"]
    for index, name in enumerate(names):
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"x" * (100 - index * 5))
        os.utime(path, (0, 1000 + index))
    open(os.path.join(folder, "sub", "deep.png"), "w").close()
    return folder


def test_scan_takes_what_it_should():
    folder = _folder_of_files()
    items, trouble = library.scan(folder)
    assert not trouble, trouble
    names = sorted(item.name for item in items)
    assert "notes.txt" not in names, "a text file got into the reel"
    assert ".hidden.jpg" not in names, "a hidden file got in by default"
    assert "deep.png" not in names, "a subfolder got in by default"
    assert len(names) == 7, names
    deep, _ = library.scan(folder, recurse=True, hidden=True)
    assert len(deep) == 9, sorted(item.name for item in deep)


def test_numbers_sort_as_numbers():
    folder = _folder_of_files()
    items, _ = library.scan(folder)
    reel = library.Reel(items, order="name")
    order = [item.name for item in reel.items if item.name.startswith("IMG")]
    assert order == ["IMG_2.jpg", "IMG_9.jpg", "IMG_10.jpg"], order


def test_accents_sort_next_to_their_letter():
    folder = _folder_of_files()
    items, _ = library.scan(folder)
    reel = library.Reel(items, order="name")
    names = [item.name for item in reel.items]
    assert names.index("Ähre.png") < names.index("clip.mkv"), names


def test_resorting_keeps_your_place():
    folder = _folder_of_files()
    items, _ = library.scan(folder)
    reel = library.Reel(items, order="name")
    reel.go_to_path(os.path.join(folder, "IMG_2.jpg"))
    here = reel.current().path
    for order in ("date", "size", "kind", "name"):
        reel.resort(order)
        assert reel.current().path == here, f"lost the place sorting by {order}"


def test_random_visits_everything_once():
    folder = _folder_of_files()
    items, _ = library.scan(folder)
    reel = library.Reel(items, order="random")
    seen = set()
    for _ in range(len(reel)):
        seen.add(reel.current().path)
        reel.step(1)
    assert len(seen) == len(reel), f"{len(seen)} of {len(reel)} in one pass"


def test_wrapping_and_not():
    folder = _folder_of_files()
    items, _ = library.scan(folder)
    reel = library.Reel(items, order="name", wrap=True)
    reel.last()
    assert reel.step(1) is not None, "wrapping reel stopped at the end"
    reel.wrap = False
    reel.last()
    assert reel.step(1) is None, "non-wrapping reel kept going"


def test_removing_leaves_you_on_the_next_one():
    folder = _folder_of_files()
    items, _ = library.scan(folder)
    reel = library.Reel(items, order="name")
    reel.go_to(2)
    following = reel.items[3].path
    reel.remove(reel.current().path)
    assert reel.current().path == following, "deleting moved the cursor wrongly"


# =============================================================== metadata ====

def test_exif_is_parsed():
    rows = dict(metadata.read_exif(os.path.join(MEDIA, "exif.jpg")))
    for key, want in fixtures.EXIF_EXPECTED.items():
        assert rows.get(key) == want, f"{key}: {rows.get(key)!r} not {want!r}"
    assert "35 mm" in rows.get("focal", ""), rows.get("focal")
    assert "52 mm" in rows.get("focal", ""), "the 35mm equivalent is missing"
    where = rows.get("where", "")
    assert "51.507" in where and "0.127" in where, where
    assert "N" in where and "W" in where, where


def test_exif_absent_is_not_an_error():
    assert metadata.read_exif(os.path.join(MEDIA, "test.png")) == {}
    assert metadata.read_exif("/no/such/file.jpg") == {}


def test_the_day_a_photograph_was_taken():
    import datetime

    when = metadata.taken(os.path.join(MEDIA, "exif.jpg"))
    assert when == datetime.datetime(2024, 6, 1, 18, 22, 4), when
    assert metadata.taken(os.path.join(MEDIA, "test.png")) is None


def test_exif_survives_corruption():
    """A truncated or scrambled EXIF block must lose tags, not raise."""
    source = os.path.join(MEDIA, "exif.jpg")
    raw = open(source, "rb").read()
    for cut in (40, 120, 300, len(raw) // 2):
        path = os.path.join(scratch, f"broken-{cut}.jpg")
        with open(path, "wb") as handle:
            handle.write(raw[:cut])
        metadata.read_exif(path)        # must not raise
    path = os.path.join(scratch, "scrambled.jpg")
    scrambled = bytearray(raw)
    for index in range(30, min(len(scrambled), 400), 7):
        scrambled[index] = (scrambled[index] + 97) % 256
    with open(path, "wb") as handle:
        handle.write(bytes(scrambled))
    metadata.read_exif(path)


def test_still_details():
    rows = dict(metadata.read_still(os.path.join(MEDIA, "test.png")))
    assert rows["Format"] == "PNG", rows.get("Format")
    assert "320 x 200" in rows["Size"], rows.get("Size")
    assert "landscape" in rows["Shape"], rows.get("Shape")


def test_helpers():
    assert metadata.human_time(-1) == "-:--"
    assert metadata.human_time(61) == "1:01"
    assert metadata.human_time(3661) == "1:01:01"
    assert metadata.human_time(90, long_form=True) == "0:01:30"
    assert metadata.human_size(1536).startswith("1.5")
    assert "16:9" in metadata.aspect_name(1920, 1080)
    assert "portrait" in metadata.aspect_name(1080, 1920)
    assert "square" in metadata.aspect_name(600, 600)


def test_shortcut_table_is_coherent():
    bound = {}
    for action, pressed, label, group, scope in shortcuts.SHORTCUTS:
        assert label, f"{action} has no label"
        assert group in shortcuts.GROUPS, f"{action} is in group {group!r}"
        assert scope in ("all", "photo", "video", "edit"), scope
        for key in pressed:
            if not action:
                continue
            assert key not in bound, \
                f"{key} is bound to both {bound[key]} and {action}"
            bound[key] = action
    assert shortcuts.pretty("<Control>plus") == "Ctrl++"
    assert shortcuts.pretty("space") == "Space"
    assert shortcuts.pretty("Left") == "←"


def test_window_installs_every_shortcut_action():
    """A key bound to an action that does not exist does nothing, silently."""
    source = open(os.path.join(ROOT, "theeye", "window.py"),
                  encoding="utf-8").read()
    for action in shortcuts.actions():
        if not action.startswith("win."):
            continue
        name = action.split(".", 1)[1]
        assert f'"{name}"' in source, f"{action} is bound but never installed"


# ======================================================= drawing the grade ====
# These need a display for a renderer. They are the tests that would catch a
# transposed matrix, which is the one mistake in this app that would be
# invisible on screen and obvious here.

def _patch(red, green, blue, size=8):
    snapshot = Gtk.Snapshot()
    snapshot.append_color(Gdk.RGBA(red=red, green=green, blue=blue, alpha=1.0),
                          effects.rect(0, 0, size, size))
    return snapshot.to_node()


def _pixel(node, size=8):
    """The middle pixel of a rendered node.

    The middle rather than the corner, deliberately. Anything involving a
    blur — clarity, bloom, glow — is averaging against transparency within a
    blur radius of the edge, so the corner of a small test patch is the one
    place every one of those stages is legitimately wrong. The middle is what
    the stage is actually being asked about.
    """
    texture = effects.render_to_texture(node, size, size)
    assert texture is not None, "no renderer produced a texture"
    data, stride = effects.texture_bytes(texture)
    raw = bytes(data)
    offset = (size // 2) * stride + (size // 2) * 4
    return tuple(raw[offset:offset + 3])


def test_colour_matrix_orientation():
    """The one test that catches a transposed matrix.

    grade.py uses out[j] = sum M[j][i] * in[i]; GSK reads its sixteen floats
    the other way round. A matrix passed through untransposed is a channel
    swap — which looks like a deliberate effect and is a bug.
    """
    rows = grade.from_rows(((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)))
    out = _pixel(effects.through_matrix(_patch(1.0, 0.5, 0.0), rows))
    near(out[0], 128, 2, "red should have taken green's value")
    near(out[1], 0, 2, "green should have taken blue's value")
    near(out[2], 255, 2, "blue should have taken red's value")


def test_offsets_reach_the_right_channel():
    out = _pixel(effects.through_matrix(_patch(0.0, 0.0, 0.0), grade.IDENTITY,
                                        (0.25, 0.5, 0.75, 0.0)))
    near(out[0], 64, 2, "red offset")
    near(out[1], 128, 2, "green offset")
    near(out[2], 191, 2, "blue offset")


def test_gsk_clamps_which_is_what_bright_pass_relies_on():
    out = _pixel(effects.through_matrix(_patch(0.1, 0.1, 0.1), grade.IDENTITY,
                                        (-0.25, -0.25, -0.25, 0.0)))
    near(out[0], 0, 1, "a negative result should clamp to black")
    out = _pixel(effects.through_matrix(_patch(0.9, 0.9, 0.9), grade.IDENTITY,
                                        (0.5, 0.5, 0.5, 0.0)))
    near(out[0], 255, 1, "a result over one should clamp to white")


def test_unsharp_leaves_flat_areas_alone():
    """The high-pass construction, which is exact or it is a brightness shift.

    DIFFERENCE(0.5 + a/2, b/2) is 0.5 + (a-b)/2 for every a and b in range,
    and mid grey is the fixed point of both soft light and overlay — so where
    the picture is flat, sharpening it must change nothing at all.
    """
    # Wide enough that the middle is further from the edge than the widest
    # blur here, so what is measured is the construction and not the border.
    size = 96
    for value in (0.0, 0.25, 0.5, 0.75, 1.0):
        node = _patch(value, value, value, size=size)
        for amount in (0.4, 1.0):
            for detail in ("clarity", "sharpen"):
                radius = 18.0 if detail == "clarity" else 2.0
                out = _pixel(effects._unsharp(node, amount, radius, detail,
                                              effects.rect(0, 0, size, size)),
                             size)
                near(out[0], round(value * 255), 2,
                     f"flat {value} after {detail} at {amount}")


def test_neutral_grade_is_a_no_op():
    node = _patch(0.5, 0.35, 0.22)
    bounds = effects.rect(0, 0, 8, 8)
    assert effects.apply_grade(node, bounds, grade.Grade()) is node, \
        "a neutral grade wrapped the node anyway"


def test_greys_stay_grey_through_the_renderer():
    bounds = effects.rect(0, 0, 8, 8)
    for key, value in (("saturation", 1.0), ("saturation", -1.0),
                       ("hue", 120.0), ("hue", -60.0), ("contrast", 0.8)):
        out = _pixel(effects.apply_grade(_patch(0.5, 0.5, 0.5), bounds,
                                         grade.Grade({key: value})))
        assert max(out) - min(out) <= 2, f"{key}={value} tinted a grey: {out}"


def test_flattening_does_not_change_the_picture():
    """The optimisation has to be invisible.

    Flattening renders the chain into a texture between stages to stop the
    cost doubling per stage. It is only legitimate if the result is the same
    picture, so every look is drawn both ways and compared.
    """
    bounds = effects.rect(0, 0, 24, 24)
    for look in looks.LOOKS:
        settings = grade.Grade(look["values"])
        if settings.is_neutral():
            continue
        node = _patch(0.46, 0.33, 0.21, size=24)
        flat = _pixel(effects.apply_grade(node, bounds, settings,
                                          collapse=True), 24)
        nested = _pixel(effects.apply_grade(node, bounds, settings,
                                            collapse=False), 24)
        for index in range(3):
            near(flat[index], nested[index], 4,
                 f"{look['key']} channel {index} differs when flattened")


def test_every_look_draws():
    bounds = effects.rect(0, 0, 8, 8)
    for look in looks.LOOKS:
        settings = grade.Grade(look["values"])
        out = _pixel(effects.apply_grade(_patch(0.5, 0.35, 0.22), bounds,
                                         settings))
        assert all(0 <= channel <= 255 for channel in out), f"{look['key']}: {out}"
        if look["key"] != "none":
            assert out != (128, 89, 56), f"{look['key']} changed nothing"


def test_monochrome_looks_are_monochrome():
    bounds = effects.rect(0, 0, 8, 8)
    for key in ("mono", "noir"):
        settings = grade.Grade(looks.by_key(key)["values"])
        out = _pixel(effects.apply_grade(_patch(0.8, 0.2, 0.1), bounds, settings))
        assert max(out) - min(out) <= 3, f"{key} left colour in: {out}"


# ============================================================== the widgets ====

def test_every_theme_makes_valid_css():
    for scheme in theme.ordered():
        provider = Gtk.CssProvider()
        provider.load_from_string(widgets.stylesheet(scheme, 0.86))


def test_glass_panels_thin_out_when_blurred():
    """The blurred material has to be transparent enough to show the blur."""
    from theeye import glass

    for scheme in theme.ordered():
        plain = glass.Material.bar(scheme, 0.86)
        blurred = glass.Material.bar(scheme, 0.86, blurred=True)
        assert blurred.alpha < plain.alpha - 0.2, \
            f"{scheme.key}: blurred glass is {blurred.alpha:.2f} against " \
            f"{plain.alpha:.2f} — the blur would not be visible"
        assert blurred.alpha > 0.3, \
            f"{scheme.key}: blurred glass at {blurred.alpha:.2f} is too thin " \
            "to carry text"


def test_grain_bakes_its_own_alpha():
    """No opacity node around the repeat node: the renderers disagree on it."""
    from theeye import glass

    first = glass.grain(0.02)
    again = glass.grain(0.02)
    assert first is again, "the grain tile was rebuilt for the same strength"
    assert glass.grain(0.5) is not first, "one tile served two strengths"


def test_radius_classes_match_the_slabs():
    assert widgets._radius_class(None, "bar") == "r18"
    assert widgets._radius_class((18, 18, 0, 0), "bar") == "r-top"
    assert widgets._radius_class(0, "strip") == "r0"


# =============================================================== the stills ====

def test_loading_a_photograph():
    still = photo.load(os.path.join(MEDIA, "test.png"))
    assert still.ok(), still.error
    assert (still.width, still.height) == (320, 200)
    assert "320 x 200" in still.describe()


def test_loading_something_that_is_not_a_picture():
    still = photo.load(os.path.join(MEDIA, "clip.mp4"))
    assert not still.ok()
    assert still.error, "a failure with no explanation"
    still = photo.load(os.path.join(MEDIA, "nothing-here.png"))
    assert not still.ok() and "no longer there" in still.error


def test_loading_an_animation():
    still = photo.load(os.path.join(MEDIA, "anim.gif"))
    assert still.ok(), still.error
    assert still.animation is not None, "a six-frame GIF loaded as a still"
    assert still.animation.texture() is not None
    assert "animated" in still.describe()


def test_a_large_picture_is_loaded_smaller():
    still = photo.load(os.path.join(MEDIA, "test.png"), longest=64)
    assert still.ok(), still.error
    assert max(still.width, still.height) <= 64, (still.width, still.height)
    assert still.is_scaled() and still.full_size() == (320, 200)


def test_thumbnails():
    cache = thumbs.Cache(48)
    texture = photo.thumbnail(os.path.join(MEDIA, "test.png"), 48)
    assert texture is not None
    assert max(texture.get_width(), texture.get_height()) == 48


# =============================================================== exporting ====

def test_exporting_a_still():
    settings = grade.Grade(looks.by_key("cinema")["values"])
    for name in ("out.png", "out.jpg", "out.webp"):
        destination = os.path.join(scratch, name)
        export.export_still(os.path.join(MEDIA, "test.png"), destination,
                            settings)
        fmt, width, height = photo.probe(destination)
        assert (width, height) == (320, 200), f"{name}: {width}x{height}"
        assert os.path.getsize(destination) > 200, f"{name} is suspiciously small"


def test_exporting_applies_the_grade():
    """The exported file must differ from one exported without the grade."""
    plain = os.path.join(scratch, "plain.png")
    graded = os.path.join(scratch, "graded.png")
    export.export_still(os.path.join(MEDIA, "test.png"), plain, grade.Grade())
    export.export_still(os.path.join(MEDIA, "test.png"), graded,
                        grade.Grade(looks.by_key("noir")["values"]))
    assert open(plain, "rb").read() != open(graded, "rb").read(), \
        "the grade did not reach the exported file"


def test_rotation_changes_the_shape():
    destination = os.path.join(scratch, "rotated.png")
    export.export_still(os.path.join(MEDIA, "test.png"), destination,
                        grade.Grade({"rotation": 90}))
    _fmt, width, height = photo.probe(destination)
    assert (width, height) == (200, 320), f"{width}x{height}"


def test_export_refuses_to_overwrite_the_original():
    source = os.path.join(MEDIA, "test.png")
    before = open(source, "rb").read()
    try:
        export.export_still(source, source, grade.Grade())
    except ValueError:
        pass
    else:
        raise AssertionError("it overwrote the original")
    assert open(source, "rb").read() == before, "the original was changed"


def test_suggested_names_are_new():
    folder = os.path.join(scratch, "names")
    os.makedirs(folder, exist_ok=True)
    source = os.path.join(folder, "photo.jpg")
    open(source, "w").close()
    first = export.suggested_name(source)
    assert first != source
    open(first, "w").close()
    second = export.suggested_name(source)
    assert second != first, "the same name was suggested twice"


def test_export_formats_are_honest():
    for extension in (".gif", ".bmp", ".xyz"):
        try:
            export.save_texture(_blank_texture(),
                                os.path.join(scratch, "x" + extension))
        except ValueError:
            continue
        raise AssertionError(f"claimed to write {extension}")


def _blank_texture():
    node = _patch(0.5, 0.5, 0.5)
    return effects.render_to_texture(node, 8, 8)


# ============================================================== the player ====

def test_player_is_available():
    assert player.init(), "GStreamer would not start"
    assert player.available(), "the elements the player needs are missing"


def test_playing_and_seeking():
    """Open muted, play, seek, and check where it got to.

    Muted and paused as soon as it has a frame: a test suite that makes a
    noise or runs a pipeline for longer than it needs to is a test suite
    nobody runs.
    """
    engine = player.Engine(max_size=(640, 480))
    engine.set_muted(True)
    engine.set_volume(0.0)
    path = os.path.join(MEDIA, "clip.mp4")
    try:
        assert engine.open(path, play=True), engine.error
        _pump_until(lambda: engine.paintable.has_frame()
                    and engine.duration > 0, 10)
        assert engine.paintable.has_frame(), "no frame arrived in ten seconds"
        assert engine.duration > 1.0, f"duration {engine.duration}"
        assert engine.video_size == (320, 240), engine.video_size
        engine.pause()
        _pump(0.3)
        assert engine.seek(1.0, accurate=True), "the seek was refused"
        _pump_until(lambda: abs(engine.position - 1.0) < 0.3, 5)
        near(engine.position, 1.0, 0.3, "position after seeking")
        assert engine.tracks_of("audio"), "no audio track was found"
    finally:
        engine.shutdown()


def test_track_selection():
    engine = player.Engine(max_size=(640, 480))
    engine.set_muted(True)
    try:
        assert engine.open(os.path.join(MEDIA, "multi.mkv"), play=True)
        _pump_until(lambda: len(engine.tracks_of("audio")) >= 2, 10)
        audio = engine.tracks_of("audio")
        assert len(audio) >= 2, f"{len(audio)} audio tracks in a two-track file"
        assert all(track.label() for track in engine.tracks), \
            "a track with no label would be an empty row in the menu"
        engine.pause()
        _pump(0.2)
        assert engine.select_track("audio", audio[1].ident), "selection refused"
        _pump(0.5)
    finally:
        engine.shutdown()


def test_playing_something_that_is_not_media():
    engine = player.Engine()
    engine.set_muted(True)
    trouble = []
    engine.connect("failed", lambda _e, message, _d: trouble.append(message))
    try:
        engine.open(os.path.join(MEDIA, "clip.srt"), play=True)
        _pump_until(lambda: bool(trouble), 6)
        assert trouble, "a subtitle file was played without complaint"
    finally:
        engine.shutdown()


def test_album_art_out_of_the_tags():
    """A sound file's cover, which is what the strip shows for music."""
    texture = thumbs.album_art(os.path.join(MEDIA, "withart.mp3"), 96)
    assert texture is not None, "no cover was found in the tags"
    assert max(texture.get_width(), texture.get_height()) <= 96


def test_sound_with_no_art_is_not_an_error():
    assert thumbs.album_art(os.path.join(MEDIA, "tone.ogg"), 96) is None


def test_video_poster():
    texture = thumbs.poster(os.path.join(MEDIA, "clip.mp4"), 64)
    assert texture is not None, "no poster frame could be extracted"
    assert texture.get_height() <= 64, texture.get_height()


def test_video_export():
    settings = grade.Grade(looks.by_key("cinema")["values"])
    destination = os.path.join(scratch, "exported.mp4")
    job = export.VideoExport(os.path.join(MEDIA, "clip.mp4"), destination,
                             settings)
    done = []
    job.connect("finished", lambda _j, path: done.append(path))
    job.connect("failed", lambda _j, message: done.append(None))
    assert job.start()
    _pump_until(lambda: bool(done), 90)
    assert done, "the export never finished"
    assert done[0] == destination, "the export failed"
    assert os.path.getsize(destination) > 1000, "the exported file is empty"
    assert job.frames > 20, f"only {job.frames} frames were written"
    info = metadata.discover(destination, 6)
    assert info is not None, "the exported file cannot be read back"
    assert info.get_video_streams(), "no video in the exported file"


def test_audio_shows_something():
    """A sound file has no frames; the stage must not be left empty.

    With a cover in the tags it shows the cover; without one it shows a
    drawn record. An empty stage reads as a broken app rather than as music,
    which matters more now that The Eye is the default for audio.
    """
    from theeye import window as windowing

    config = configuration.Config()
    config["chrome_autohide"] = False
    config["muted"] = True
    config["volume"] = 0.0
    # Paused: these fixtures are two seconds long and would otherwise reach
    # the end and advance to the next file mid-test.
    config["autoplay"] = False
    state = configuration.State()
    app = Gtk.Application(application_id="com.robin.TheEyeTest3")
    trouble = []

    def ready(_app):
        win = windowing.MainWindow(app, config, state)
        try:
            for name, want in (("withart.mp3", "artwork"), ("tone.ogg", "record")):
                path = os.path.join(MEDIA, name)
                if not os.path.exists(path):
                    continue
                win.open_path(path)
                _pump_until(lambda: (win.reel.current() is not None
                                     and win.reel.current().name == name
                                     and win.engine.duration > 0), 10)
                content = win.stage.content()
                if want == "artwork":
                    _pump_until(lambda: content is not win.engine.artwork
                                and win.engine.artwork is not None, 4)
                    content = win.stage.content()
                    got = ("artwork" if content is win.engine.artwork
                           else type(content).__name__)
                else:
                    got = ("record" if isinstance(content, widgets.SoundFace)
                           else type(content).__name__)
                if got != want:
                    trouble.append(f"{name}: stage shows {got}, wanted {want}")
                if not win.transport_reveal.get_reveal_child():
                    trouble.append(f"{name}: no transport bar for a sound file")
        finally:
            win.engine.shutdown()
            win.destroy()
            app.quit()

    app.connect("activate", ready)
    app.run([])
    assert not trouble, "; ".join(trouble)


def _pump(seconds):
    context = GLib.main_context_default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def _pump_until(ready, seconds):
    context = GLib.main_context_default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        if ready():
            return True
        time.sleep(0.01)
    return False


# ================================================================ the window ====

def test_the_window_builds_and_fits_a_tiling_column():
    """The window must be narrower than the narrowest column niri offers.

    455px is the narrowest preset on the machine this was written for, and a
    window that cannot be made that narrow cannot be tiled. The transport bar
    is the thing that pushes the floor up, which is what the breakpoint in
    window.py is for.
    """
    from theeye import window as windowing

    config = configuration.Config()
    state = configuration.State()
    app = Gtk.Application(application_id="com.robin.TheEyeTest")
    built = []

    def ready(_app):
        win = windowing.MainWindow(app, config, state)
        built.append(win)
        floor = win.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        assert floor <= 455, f"the window will not fit a tiling column: {floor}px"
        win.engine.shutdown()
        # Never presented, so nothing appears on anybody's screen.
        win.destroy()
        app.quit()

    app.connect("activate", ready)
    app.run([])
    assert built, "the window was never built"


def test_opening_a_folder_in_the_window():
    from theeye import window as windowing

    config = configuration.Config()
    config["chrome_autohide"] = False
    state = configuration.State()
    app = Gtk.Application(application_id="com.robin.TheEyeTest2")
    trouble = []

    def ready(_app):
        win = windowing.MainWindow(app, config, state)
        try:
            win.open_path(MEDIA)
            if not _pump_until(lambda: len(win.reel) > 0, 8):
                trouble.append("the folder never loaded")
                return
            if len(win.reel) < 6:
                trouble.append(f"only {len(win.reel)} items in the reel")
                return
            # Waited for by name: the stage already shows the folder's first
            # picture, so "something is on the stage" is true before the
            # second scan has even finished.
            win.open_path(os.path.join(MEDIA, "test.png"))
            if not _pump_until(lambda: win.reel.current() is not None
                               and win.reel.current().name == "test.png", 8):
                landed = win.reel.current().name if win.reel.current() else None
                trouble.append(f"the reel landed on {landed!r}, not on test.png")
                return
            if not _pump_until(lambda: win._still is not None
                               and win._still.path == win.reel.current().path
                               and win.stage.content() is not None, 8):
                trouble.append("the picture never reached the stage")
                return
            if "test.png" not in win.title.get_text():
                trouble.append(f"the title says {win.title.get_text()!r}")
        finally:
            win.engine.shutdown()
            win.destroy()
            app.quit()

    app.connect("activate", ready)
    app.run([])
    assert not trouble, "; ".join(trouble)


# ===================================================== paper, into the brain ====
# A photographed page, made by fixtures.py through a pinhole camera, so that
# where its corners are and what shape it really is are known exactly.

import datetime  # noqa: E402

DAY = datetime.date(2026, 10, 27)


def _page_photo():
    return os.path.join(MEDIA, "page.jpg")


def _synthetic(width=160, height=120):
    """A grey picture with structure in it: a ramp, and a checker over it."""
    from theeye import paper

    data = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            ramp = int(255 * x / max(1, width - 1))
            data[y * width + x] = ramp if (x // 16 + y // 16) % 2 else 255 - ramp
    return paper.Gray(width, height, data)


def test_the_page_is_found():
    from theeye import paper

    corners, confidence = paper.find_corners(paper.load(_page_photo()))
    assert confidence >= paper.CONFIDENT, f"confidence {confidence:.2f}"
    width, height = fixtures.PAGE_PHOTO
    for (x, y), (want_x, want_y) in zip(corners, fixtures.PAGE_CORNERS):
        near(x * width, want_x * width, 25, "a corner, across")
        near(y * height, want_y * height, 25, "a corner, down")


def test_nothing_page_shaped_is_not_trusted():
    import random

    from theeye import paper

    noise = random.Random(7)
    grey = paper.Gray(240, 180, bytes(noise.randint(90, 160) for _ in range(240 * 180)))
    corners, confidence = paper.find_corners(paper.to_pixbuf(grey))
    assert confidence < paper.CONFIDENT, f"noise looked like a page ({confidence:.2f})"
    if confidence == 0:
        assert corners == paper.FALLBACK


def test_the_page_comes_out_its_own_shape():
    """Its real shape, not the trapezium's: the camera is undone, not guessed."""
    from theeye import paper

    page = paper.straighten(_page_photo(), fixtures.PAGE_CORNERS, enhanced=False)
    near(page.width / page.height, fixtures.PAGE_ASPECT, 0.02, "the page's shape")
    assert max(page.width, page.height) == paper.LONGEST, (page.width, page.height)


def test_the_homography_lands_on_the_corners():
    from theeye import paper

    rect = ((0, 0), (100, 0), (100, 150), (0, 150))
    quad = ((10, 20), (300, 5), (320, 400), (-5, 390))
    h = paper.homography(rect, quad)
    for corner, want in zip(rect, quad):
        got = paper.project(h, *corner)
        near(got[0], want[0], 1e-6, "x")
        near(got[1], want[1], 1e-6, "y")


def test_the_warps_agree():
    """numpy, cairo's mesh of affine pieces and plain Python: one picture."""
    from theeye import paper

    source = _synthetic()
    quad = ((12, 9), (150, 14), (140, 112), (6, 104))
    reference = paper.warp(source, quad, 90, 70, how="python")
    others = [how for how, there in (("numpy", paper.numpy), ("cairo", paper.cairo))
              if there is not None]
    assert others, "neither numpy nor cairo is here to compare against"
    for how in others:
        got = paper.warp(source, quad, 90, 70, how=how)
        diffs = [abs(a - b) for a, b in zip(got.data, reference.data)]
        mean = sum(diffs) / len(diffs)
        assert mean < 2.5, f"{how} differs from the reference by {mean:.2f} on average"


def test_the_light_is_flattened_and_the_ink_kept():
    from theeye import paper

    raw = paper.straighten(_page_photo(), fixtures.PAGE_CORNERS, enhanced=False)
    page = paper.enhance(raw)
    sx, sy = raw.width / fixtures.PAGE_SIZE[0], raw.height / fixtures.PAGE_SIZE[1]

    def paper_at(image, fx, fy):
        # Between two ruled lines, away from the writing: paper.
        x, y = int(fx * fixtures.PAGE_SIZE[0] * sx), int(fy * fixtures.PAGE_SIZE[1] * sy)
        return image.at(x, y)

    corners = [(0.1, 0.075), (0.9, 0.075), (0.1, 0.93), (0.9, 0.93)]
    before = [paper_at(raw, *c) for c in corners]
    after = [paper_at(page, *c) for c in corners]
    assert max(before) - min(before) > 25, f"the fixture is not lit unevenly: {before}"
    assert max(after) - min(after) <= 12, f"the light was not flattened: {after}"
    assert min(after) >= 235, f"the paper is not white: {after}"
    ink = page.at(int(800 * sx), int(1600 * sy))     # the middle of the drawn box
    assert ink < 90, f"the filled box came out {ink}, not dark"


def test_without_numpy_the_page_is_the_same():
    from theeye import paper

    real = paper.numpy
    try:
        paper.numpy = None
        plain = paper.straighten(_page_photo(), fixtures.PAGE_CORNERS, longest=600)
    finally:
        paper.numpy = real
    fast = paper.straighten(_page_photo(), fixtures.PAGE_CORNERS, longest=600)
    assert (plain.width, plain.height) == (fast.width, fast.height)
    diffs = [abs(a - b) for a, b in zip(plain.data[::3], fast.data[::3])]
    assert sum(diffs) / len(diffs) < 3, "the two ways made different pages"


def test_a_page_is_small_enough_for_git():
    from theeye import paper

    page = paper.straighten(_page_photo(), fixtures.PAGE_CORNERS)
    data = paper.jpeg(page)
    assert data[:2] == b"\xff\xd8", "not a JPEG"
    assert len(data) <= paper.TARGET, f"{len(data)} bytes"


def test_subjects_and_the_day_note():
    from theeye import brain, filing

    p = brain.places()
    for name in ("Biology", "Matematika", ".git"):
        (p.studies / name).mkdir(parents=True, exist_ok=True)
    assert filing.subjects() == ["Inbox", "Biology", "Matematika"], filing.subjects()
    assert filing.day_note("Biology", DAY) == p.studies / "Biology" / "2026-10-27.md"
    old = p.studies / "Matematika" / "2026.10.27..md"
    old.write_text("# Régi\n", encoding="utf-8")
    assert filing.day_note("Matematika", DAY) == old, "the Hungarian name was not kept"
    folder = p.studies / "Biology" / "numbers"
    folder.mkdir()
    assert filing.next_image(folder, DAY).name == "2026-10-27-1.jpg"
    for n in (1, 3):
        (folder / f"2026-10-27-{n}.jpg").write_bytes(b"")
    assert filing.next_image(folder, DAY).name == "2026-10-27-4.jpg"
    assert filing.destination("pages/2026-10-27-1.jpg") == "pages/2026-10-27-1.jpg"
    assert filing.destination("Lakás — Mohs/01-a.jpg") == "<Lakás — Mohs/01-a.jpg>"


def _tiny_jpeg():
    from theeye import paper

    return paper.jpeg(_synthetic(64, 48))


def test_a_page_is_filed_into_its_day():
    from theeye import brain, filing

    p = brain.places()
    (p.studies / "Fizika").mkdir(parents=True, exist_ok=True)
    photo = _page_photo()
    before = open(photo, "rb").read()
    taken = datetime.datetime(2026, 10, 27, 19, 5)
    first = filing.file_page(_tiny_jpeg(), "Fizika", DAY, source=photo, taken=taken,
                             reader="tesseract")
    assert first.image == p.studies / "Fizika" / "pages" / "2026-10-27-1.jpg", first.image
    assert first.note == p.studies / "Fizika" / "2026-10-27.md"
    text = first.note.read_text(encoding="utf-8")
    assert text.startswith("# Fizika · 2026-10-27\n"), text[:40]
    assert "## Pages" in text and "![](pages/2026-10-27-1.jpg)" in text, text
    assert "read by Tesseract" in text and "taken 2026-10-27 19:05" in text, text

    # Rewritten below since; the next page still goes in among the pages.
    first.note.write_text(text + "\n## Rewrite\n\nIn my own words.\n", encoding="utf-8")
    second = filing.file_page(_tiny_jpeg(), "Fizika", DAY, source=photo, reader="claude")
    text = first.note.read_text(encoding="utf-8")
    assert text.index("-1.jpg") < text.index("-2.jpg") < text.index("## Rewrite"), text

    # The words arrive later, each under its own image.
    body = filing.format_text("## Erők\n\nA $F = ma$ :: Newton II.", "claude")
    assert filing.add_text(second.note, second.link, body)
    rough = filing.format_text("Erok :: Newton", "tesseract")
    assert rough.startswith("```text\n"), "Tesseract's text was not fenced"
    assert filing.add_text(first.note, first.link, rough)
    text = first.note.read_text(encoding="utf-8")
    assert "### Erők" in text and "\n## Erők" not in text, "a heading escaped its page"
    assert text.index("-2.jpg") < text.index("### Erők") < text.index("## Rewrite"), text
    assert text.index("-1.jpg") < text.index("```text") < text.index("-2.jpg"), text
    assert not filing.add_text(first.note, "pages/gone.jpg", "x"), \
        "text was written for a page that is not there"
    assert open(photo, "rb").read() == before, "the original photograph changed"


def test_a_page_to_the_inbox_keeps_its_provenance():
    from theeye import brain, filing

    p = brain.places()
    taken = datetime.datetime(2026, 10, 27, 19, 5)
    filed = filing.file_page(_tiny_jpeg(), filing.INBOX, DAY, source=_page_photo(),
                             taken=taken, reader="claude")
    assert filed.image.parent == p.inbox and filed.image.name == "2026-10-27-1.jpg"
    meta, body, _ = brain.split_front_matter(filed.note.read_text(encoding="utf-8"))
    assert meta["origin"] == "the-eye" and meta["app"] == "The Eye", meta
    assert meta["kind"] == "notebook-page" and meta["lesson"] == "2026-10-27", meta
    assert meta["reader"] == "claude" and meta["source"].endswith("page.jpg"), meta
    assert meta["taken"] == "2026-10-27T19:05:00", meta
    assert "![](2026-10-27-1.jpg)" in body, body
    assert filing.update_meta(filed.note, {"lesson": "2026-10-21", "topic": "Dinamika",
                                           "page": "14", "empty": ""})
    meta, again, _ = brain.split_front_matter(filed.note.read_text(encoding="utf-8"))
    assert (meta["lesson"], meta["topic"], meta["page"]) == ("2026-10-21", "Dinamika", "14")
    assert "empty" not in meta and again == body, "updating the front matter touched the body"


def test_the_header_line_is_read():
    from theeye import reading

    for text, want in (
        ("2026-10-27 · Dinamika · 14\n\nNewton", (DAY, "Dinamika", "14")),
        ("### 2026.10.21. | Mozgás | p. 7", (datetime.date(2026, 10, 21), "Mozgás", "7")),
        ("\n\n2026-10-27  Dinamika  14\nmore", (DAY, "Dinamika", "14")),
        (fixtures.PAGE_HEADER, (datetime.date(2026, 10, 21), "Dinamika", "14")),
        ("Kémia\n2026. 10. 27.\n", (DAY, "", "")),
    ):
        header = reading.header(text)
        assert (header.day, header.topic, header.page) == want, (text, header)
    assert reading.header("no date here").day is None
    assert reading.header("2026-13-45 nonsense").day is None
    assert reading.parse_day("2026. 10. 27.") == DAY
    assert reading.parse_day("27 October") is None


def test_claude_s_transcription_is_tidied():
    from theeye import reading

    assert reading.tidy_markdown("```markdown\n# Cím\n\nszöveg\n```") == "### Cím\n\nszöveg"
    kept = reading.tidy_markdown("Kód:\n\n```python\n# not a heading\n```\n## Fej")
    assert "# not a heading" in kept and "### Fej" in kept, kept


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def test_tesseract_is_asked_properly():
    """The call, checked against a stand-in, so it is tested where there is
    no Tesseract: the languages, stdout, and the brain's own language data."""
    import json

    from theeye import brain, reading

    data = brain.places().brain / "config" / "tessdata"
    data.mkdir(parents=True, exist_ok=True)
    (data / "hun.traineddata").write_bytes(b"")
    record = os.path.join(scratch, "tesseract-called.json")
    fake = _write(os.path.join(scratch, "fake_tesseract.py"), (
        "import json, os, sys\n"
        "if '--list-langs' in sys.argv:\n"
        "    print('List of available languages (3):'); print('eng'); print('hun'); print('osd')\n"
        "    sys.exit(0)\n"
        f"json.dump({{'argv': sys.argv[1:], 'data': os.environ.get('TESSDATA_PREFIX')}},"
        f" open({record!r}, 'w'))\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "print('Szöveg a lapon\\n\\n\\n\\f')\n"))
    image = os.path.join(MEDIA, "test.png")
    try:
        _ask_the_stand_in(reading, image, fake, record, data)
    finally:
        # A language file that is not one must not be found by the real
        # Tesseract later in the run.
        shutil.rmtree(data, ignore_errors=True)


def _ask_the_stand_in(reading, image, fake, record, data):
    import json

    text = reading.ocr(image, command=[sys.executable, fake])
    assert text == "Szöveg a lapon", repr(text)
    called = json.load(open(record))
    assert called["argv"] == [image, "stdout", "-l", "hun+eng"], called["argv"]
    assert called["data"] == str(data), called["data"]

    failing = _write(os.path.join(scratch, "failing_tesseract.py"),
                     "import sys\nsys.stderr.write('Error opening data file\\n'); sys.exit(1)\n")
    try:
        reading.ocr(image, command=[sys.executable, failing])
    except reading.Unreadable as exc:
        assert "Error opening data file" in str(exc), exc
    else:
        raise AssertionError("a failing Tesseract was taken for an empty page")

    real = reading.tesseract
    reading.tesseract = lambda: None
    try:
        reading.ocr(image)
    except reading.Unreadable as exc:
        assert "not installed" in str(exc), exc
    else:
        raise AssertionError("no Tesseract, and no complaint")
    finally:
        reading.tesseract = real
    assert reading.available("none")


def test_claude_is_asked_to_transcribe():
    import threading

    from theeye import reading

    folder = os.path.join(scratch, "reading")
    image = _write(os.path.join(folder, "page.jpg"), "jpeg")
    fake = _write(os.path.join(scratch, "fake_claude.py"), (
        "import json, os, sys\n"
        "prompt = sys.argv[sys.argv.index('-p') + 1]\n"
        "ok = 'Transcribe' in prompt and 'page.jpg' in prompt and "
        "os.path.basename(os.getcwd()) == 'reading'\n"
        "text = '```markdown\\n# 2026-10-21 · Dinamika · 14\\n\\n$F = ma$\\n```'\n"
        "print(json.dumps({'type': 'result', 'result': text if ok else 'wrong call', "
        "'is_error': not ok, 'session_id': 's'}))\n"))
    done, outcome = threading.Event(), {}

    def landed(ok, text):
        outcome.update(ok=ok, text=text)
        done.set()

    reading.transcribe(image, landed, command=[sys.executable, fake])
    assert done.wait(30), "Claude's stand-in never answered"
    assert outcome["ok"], outcome
    assert outcome["text"] == "### 2026-10-21 · Dinamika · 14\n\n$F = ma$", outcome["text"]


def test_tesseract_reads_a_printed_header():
    """The real thing, where it is installed with a language to read."""
    from theeye import paper, reading

    page = paper.straighten(_page_photo(), fixtures.PAGE_CORNERS)
    image = os.path.join(scratch, "for-tesseract.png")
    paper.to_pixbuf(page).savev(image, "png", [], [])
    header = reading.header(reading.ocr(image))
    assert header.day == datetime.date(2026, 10, 21), header


def test_a_photograph_becomes_a_palace_locus():
    from theeye import brain, filing

    palace = filing.create_palace("Lakás — Mohs-skála")
    assert palace.name == "Lakás — Mohs-skála.md"
    meta, _body, _ = brain.split_front_matter(palace.read_text(encoding="utf-8"))
    assert meta == {"kind": "palace", "title": "Lakás — Mohs-skála"}, meta
    with open(palace, "a", encoding="utf-8") as handle:
        handle.write("1. Bejárati ajtó :: talk (1)\n2. Cipőtartó :: gipsz (2)\n")
    _write(os.path.join(str(filing.palace_folder()), "Not a palace.md"), "# Lista\n")
    assert [p.title for p in filing.palaces()] == ["Lakás — Mohs-skála"]
    assert [(l.number, l.name, l.item) for l in filing.loci(palace)] == \
        [(1, "Bejárati ajtó", "talk (1)"), (2, "Cipőtartó", "gipsz (2)")]

    photo = _page_photo()
    copy = filing.link_locus(palace, "bejárati AJTO", photo)
    assert copy == palace.parent / "Lakás — Mohs-skála" / "01-bejarati-ajto.jpg", copy
    assert copy.stat().st_size < os.path.getsize(photo) and copy.read_bytes()[:2] == b"\xff\xd8"
    lines = palace.read_text(encoding="utf-8").splitlines()
    at = lines.index("1. Bejárati ajtó :: talk (1)")
    assert lines[at + 1] == "   ![](<Lakás — Mohs-skála/01-bejarati-ajto.jpg>)", lines
    assert lines[at + 2].startswith("2. Cipőtartó"), "the photograph left its locus"

    again = filing.link_locus(palace, "Mosdó", photo, item="fluorit (4)")
    assert again.name == "03-mosdo.jpg", again
    lines = palace.read_text(encoding="utf-8").splitlines()
    assert "3. Mosdó :: fluorit (4)" in lines, lines
    assert lines[lines.index("3. Mosdó :: fluorit (4)") + 1].endswith("03-mosdo.jpg>)")
    filing.link_locus(palace, "Konyhapult", photo)
    assert "4. Konyhapult" in palace.read_text(encoding="utf-8").splitlines()
    assert filing.slug("Előszobatükör ") == "eloszobatukor"


def test_the_filer_files_and_reads_in_order():
    """The worker, with the reading stood in for: two pages into one day."""
    from theeye import brain, filing, pages, reading

    p = brain.places()
    (p.studies / "Kémia").mkdir(parents=True, exist_ok=True)
    events = []
    filer = pages.Filer(lambda event, task, detail: events.append((event, detail)),
                        straighten=lambda path, corners: _synthetic(64, 48),
                        ocr=lambda image: "2026-10-21 · Savak · 3\n\nsósav :: HCl")
    for _ in range(2):
        filer.add(pages.Task(_page_photo(), fixtures.PAGE_CORNERS, "Kémia", DAY,
                             "tesseract", None))
    assert _pump_until(lambda: not filer.busy(), 20), "the pages were never done"
    _pump(0.3)
    assert [e for e, _d in events if e == "filed"] == ["filed", "filed"], events
    reads = [d for e, d in events if e == "read"]
    assert len(reads) == 2 and reads[0].day == datetime.date(2026, 10, 21), events
    text = (p.studies / "Kémia" / "2026-10-27.md").read_text(encoding="utf-8")
    assert text.count("```text") == 2 and text.index("-1.jpg") < text.index("-2.jpg"), text


# ------------------------------------------------------------ on screen ----

def test_the_strip_marks_several():
    from theeye import filmstrip

    folder = _folder_of_files()
    items, _ = library.scan(folder)
    reel = library.Reel(items, order="name")
    strip = filmstrip.Filmstrip()
    strip.set_items(reel.items, 0)
    changes = []
    strip.connect("marks-changed", lambda _s: changes.append(1))
    strip.toggle_mark(1)
    strip.mark_through(3)
    assert strip.marked() == [item.path for item in reel.items[1:4]], strip.marked()
    strip.toggle_mark(2)
    assert reel.items[2].path not in strip.marked() and len(changes) == 3
    reel.resort("size")
    strip.set_items(reel.items, 0)
    assert len(strip.marked()) == 2, "a re-sort lost the marks"
    keep = reel.items[0].path
    strip.set_items(reel.items[:1], 0)
    assert all(path == keep for path in strip.marked())
    strip.clear_marks()
    assert strip.marked() == []


def test_the_palette_finds_by_name():
    from theeye import palette

    everything = palette.commands()
    assert len(everything) == len({action for _l, action, _k in everything})
    assert palette.rank(everything, "imp")[0][1] == "win.import-pages"
    assert "win.palace-locus" in [a for _l, a, _k in palette.rank(everything, "palace")[:2]]
    assert palette.rank(everything, "zzzq") == []
    assert palette.score("dnl", "Downloads") > 0 and palette.score("x", "abc") == 0


def test_the_corner_editor_draws_the_page():
    from theeye import pages

    editor = pages.CornerEditor()
    still = photo.load(_page_photo(), animate=False, longest=640)
    editor.set_texture(still.texture)
    editor.set_corners(fixtures.PAGE_CORNERS)
    # Never allocated, so it is told its size: nothing here is on screen.
    editor.get_width = lambda: 480
    editor.get_height = lambda: 360
    for corner in fixtures.PAGE_CORNERS:
        back = editor.from_widget(*editor.to_widget(corner))
        near(back[0], corner[0], 1e-6, "x there and back")
        near(back[1], corner[1], 1e-6, "y there and back")
    grip = editor.to_widget(fixtures.PAGE_CORNERS[2])
    assert editor.grip_at(grip[0] + 5, grip[1] - 5) == 2
    snapshot = Gtk.Snapshot()
    editor.do_snapshot(snapshot)
    node = snapshot.to_node()
    assert node is not None, "the editor drew nothing"
    texture = effects.render_to_texture(node, 480, 360)
    data, stride = effects.texture_bytes(texture)
    raw = bytes(data)

    def luma(x, y):
        at = int(y) * stride + int(x) * 4
        return sum(raw[at:at + 3]) / 3

    x, y, _w, _h = editor.frame()
    middle = editor.to_widget((sum(c[0] for c in fixtures.PAGE_CORNERS) / 4,
                               sum(c[1] for c in fixtures.PAGE_CORNERS) / 4))
    outside = luma(x + 4, y + 4)
    inside = luma(*middle)
    assert outside < inside, f"outside the page ({outside}) was not dimmed ({inside})"


def test_the_importer_goes_page_by_page():
    """In a real window: two marked photographs, one filed and one skipped."""
    from theeye import brain, window as windowing

    config = configuration.Config()
    config["chrome_autohide"] = False
    config["page_reader"] = "none"         # never the real Claude from a test
    config["page_subject"] = "Inbox"
    state = configuration.State()
    app = Gtk.Application(application_id="com.robin.TheEyeTest4")
    trouble = []

    def ready(_app):
        win = windowing.MainWindow(app, config, state)
        try:
            win.open_path(_page_photo())
            if not _pump_until(lambda: win.reel.current() is not None
                               and win.reel.current().name == "page.jpg", 8):
                trouble.append("page.jpg never opened")
                return
            win.strip.toggle_mark(win.reel.index)
            win.strip.toggle_mark(win.reel.index_of(os.path.join(MEDIA, "test.png")))
            if "2 marked" not in win.subtitle.get_text():
                trouble.append(f"the title says {win.subtitle.get_text()!r}")
            win.activate_action("win.import-pages", None)
            _pump(0.1)
            if not win._importing():
                trouble.append("the importer did not open")
                return
            importer = win.importer
            if not _pump_until(lambda: importer.file_button.get_sensitive(), 15):
                trouble.append(f"the page was never ready: {importer.found.get_text()}")
                return
            if os.path.basename(importer._pages[0]) == "page.jpg":
                for got, want in zip(importer.editor.corners(), fixtures.PAGE_CORNERS):
                    if abs(got[0] - want[0]) > 0.02 or abs(got[1] - want[1]) > 0.02:
                        trouble.append(f"a corner landed at {got}, not {want}")
                        break
                if importer.day.get_text() != "2026-10-27":
                    trouble.append(f"the day offered was {importer.day.get_text()}")
            here = win.reel.current().path
            win.step(1)
            if win.reel.current().path != here:
                trouble.append("the reel moved under the importer")
            importer.file_page()
            if not _pump_until(lambda: importer.file_button.get_sensitive(), 15):
                trouble.append("the second page was never ready")
                return
            importer.next_page()
            if win._importing():
                trouble.append("the importer stayed open after the last page")
            if not _pump_until(lambda: not importer.filer.busy(), 20):
                trouble.append("the page was never filed")
                return
            if not list(brain.places().inbox.glob("*.jpg")):
                trouble.append("nothing reached the inbox")

            # The palette, and Escape closing it before the window.
            win.activate_action("win.palette", None)
            if not win.palette_glass.get_visible():
                trouble.append("the palette did not open")
            win.activate_action("win.close", None)
            if win.palette_glass.get_visible():
                trouble.append("Escape did not close the palette")
            if win._app_keys is not None:
                win.set_focus(win.palette.entry)
                _pump(0.05)
                if win._app_keys.get_propagation_phase() != Gtk.PropagationPhase.BUBBLE:
                    trouble.append("the shortcuts still come before a text field")
                win.set_focus(None)
                if win._app_keys.get_propagation_phase() != Gtk.PropagationPhase.CAPTURE:
                    trouble.append("the shortcuts did not get the keys back")
        except Exception as exc:                        # noqa: BLE001
            trouble.append(f"{type(exc).__name__}: {exc}")
        finally:
            win.engine.shutdown()
            win.destroy()
            app.quit()

    app.connect("activate", ready)
    app.run([])
    assert not trouble, "; ".join(trouble)


def test_the_palace_dialog_puts_it_there():
    from theeye import filing, loci

    palace = filing.create_palace("Az iskolába")
    with open(palace, "a", encoding="utf-8") as handle:
        handle.write("1. Kapu :: Wilson-tétel\n")
    said = []
    dialog = loci.LocusDialog(_page_photo(), lambda text, *_: said.append(text))
    titles = [p.title for p in dialog.palaces]
    dialog.palace.set_selected(titles.index("Az iskolába"))
    assert [l.name for l in dialog.loci] == ["Kapu"], dialog.loci
    assert not dialog.locus_name.get_visible(), "an existing locus asked for a name"
    dialog.put()
    assert _pump_until(lambda: said, 10), "nothing was said"
    assert "Kapu" in said[0], said
    assert "![](<Az iskolába/01-kapu.jpg>)" in palace.read_text(encoding="utf-8")


def test_asking_claude_needs_claude():
    from theeye import ask, claude

    said = []
    panel = ask.AskPanel(lambda text, *_: said.append(text), lambda: None)
    opened = panel.about(_page_photo())
    assert opened == claude.available(), (opened, claude.available())
    if opened:
        assert "page.jpg" in panel.subject.get_text()
        assert panel.folder == os.path.dirname(_page_photo())
    else:
        assert said and "not installed" in said[0], said


# ==================================================================== main ====

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    print("The Eye")
    if missing_media:
        print(f"  (could not build {', '.join(missing_media)} — some tests "
              "will be skipped)")

    print("\n-- colour arithmetic " + "-" * 38)
    for name, fn in (
        ("a neutral grade is the identity", test_matrix_identity),
        ("greys stay grey through saturation and hue", test_greys_stay_grey),
        ("exposure is measured in stops", test_exposure_is_in_stops),
        ("contrast pivots on middle grey", test_contrast_holds_middle_grey),
        ("white balance keeps the brightness", test_balance_holds_brightness),
        ("the bright pass is a real threshold", test_bright_pass_is_a_threshold),
        ("monochrome and duotone", test_monochrome_and_duotone),
        ("composing two matrices keeps the order", test_composition_order),
        ("a grade never touches the alpha", test_alpha_is_never_touched),
        ("a grade rejects nonsense", test_grade_rejects_nonsense),
        ("a grade survives being saved", test_grade_round_trips),
        ("every look is valid and does something",
         test_every_look_is_valid_and_does_something),
        ("every control has a slider", test_every_control_has_a_slider),
    ):
        check(name, fn)

    print("\n-- themes and settings " + "-" * 36)
    for name, fn in (
        ("text is readable on every theme",
         test_text_is_readable_on_every_theme),
        ("every matte is neutral", test_every_matte_is_neutral),
        ("an unknown theme falls back", test_theme_lookup_survives_nonsense),
        ("settings are validated", test_config_defaults_and_validation),
        ("settings save only what changed",
         test_config_saves_only_what_changed),
        ("resume positions worth keeping", test_resume_positions),
        ("state lists are capped", test_state_lists_are_capped),
    ):
        check(name, fn)

    print("\n-- files and folders " + "-" * 38)
    for name, fn in (
        ("formats are recognised", test_format_recognition),
        ("every claimed format has a loader",
         test_every_claimed_photo_format_has_a_loader),
        ("the claims follow the loaders", test_claims_follow_the_loaders),
        ("the installer is told what opens here",
         test_the_installer_is_told_what_opens),
        ("a path is found however it is spelled",
         test_a_path_is_found_however_it_is_spelled),
        ("subtitle files beside a video", test_sidecar_subtitles),
        ("the scan takes what it should", test_scan_takes_what_it_should),
        ("numbers sort as numbers", test_numbers_sort_as_numbers),
        ("accents sort next to their letter",
         test_accents_sort_next_to_their_letter),
        ("re-sorting keeps your place", test_resorting_keeps_your_place),
        ("random visits everything once", test_random_visits_everything_once),
        ("wrapping, and not", test_wrapping_and_not),
        ("deleting leaves you on the next one",
         test_removing_leaves_you_on_the_next_one),
        ("the keyboard table is coherent", test_shortcut_table_is_coherent),
        ("every bound action exists",
         test_window_installs_every_shortcut_action),
        ("the helpers", test_helpers),
    ):
        check(name, fn)

    print("\n-- metadata " + "-" * 47)
    for name, fn, *needed in (
        ("EXIF is parsed", test_exif_is_parsed, "exif.jpg"),
        ("no EXIF is not an error", test_exif_absent_is_not_an_error, "test.png"),
        ("the day a photograph was taken", test_the_day_a_photograph_was_taken,
         "exif.jpg", "test.png"),
        ("corrupt EXIF loses tags, not the app",
         test_exif_survives_corruption, "exif.jpg"),
        ("a still's details", test_still_details, "test.png"),
    ):
        needs_media(name, fn, *needed)

    from theeye import paper, reading

    print("\n-- paper, into the brain " + "-" * 34)
    print(f"   (the arithmetic is {paper.engine()}'s on this machine)")
    for name, fn, *needed in (
        ("the page is found in the photograph", test_the_page_is_found, "page.jpg"),
        ("noise is not taken for a page", test_nothing_page_shaped_is_not_trusted),
        ("the page comes out its own shape",
         test_the_page_comes_out_its_own_shape, "page.jpg"),
        ("the homography lands on the corners",
         test_the_homography_lands_on_the_corners),
        ("the three warps agree", test_the_warps_agree),
        ("the light is flattened and the ink kept",
         test_the_light_is_flattened_and_the_ink_kept, "page.jpg"),
        ("a page is small enough for git", test_a_page_is_small_enough_for_git,
         "page.jpg"),
        ("subjects, and the day's note", test_subjects_and_the_day_note),
        ("a page is filed into its day", test_a_page_is_filed_into_its_day, "page.jpg"),
        ("a page in the inbox keeps its provenance",
         test_a_page_to_the_inbox_keeps_its_provenance, "page.jpg"),
        ("the header line is read", test_the_header_line_is_read),
        ("Claude's transcription is tidied", test_claude_s_transcription_is_tidied),
        ("Tesseract is asked properly", test_tesseract_is_asked_properly, "test.png"),
        ("Claude is asked to transcribe", test_claude_is_asked_to_transcribe),
        ("a photograph becomes a palace locus",
         test_a_photograph_becomes_a_palace_locus, "page.jpg"),
        ("the worker files and reads in order",
         test_the_filer_files_and_reads_in_order, "page.jpg"),
    ):
        needs_media(name, fn, *needed)
    if paper.numpy is None:
        skip("without numpy the page is the same", "numpy is not installed, so "
             "everything above already ran without it")
    else:
        needs_media("without numpy the page is the same",
                    test_without_numpy_the_page_is_the_same, "page.jpg")
    # The real Tesseract, with the language data the real brain keeps for it
    # when the system has none — read, never written.
    own = os.path.expanduser(os.path.join("~", "SecondBrain", "config", "tessdata"))
    before = os.environ.get("TESSDATA_PREFIX")
    if reading.tesseract() is not None and not reading.languages() and os.path.isdir(own):
        os.environ["TESSDATA_PREFIX"] = own
    try:
        if reading.tesseract() is None:
            skip("Tesseract reads a printed header", "Tesseract is not installed")
        elif not reading.languages():
            skip("Tesseract reads a printed header",
                 "Tesseract has neither Hungarian nor English here")
        else:
            needs_media("Tesseract reads a printed header",
                        test_tesseract_reads_a_printed_header, "page.jpg")
    finally:
        if before is None:
            os.environ.pop("TESSDATA_PREFIX", None)
        else:
            os.environ["TESSDATA_PREFIX"] = before

    if not start_gtk():
        print("\n  -- everything below needs a display, and there is none")
    else:
        print("\n-- drawing the grade " + "-" * 38)
        for name, fn in (
            ("the colour matrix is the right way round",
             test_colour_matrix_orientation),
            ("offsets reach the right channel",
             test_offsets_reach_the_right_channel),
            ("GSK clamps, which the bright pass relies on",
             test_gsk_clamps_which_is_what_bright_pass_relies_on),
            ("sharpening leaves flat areas alone",
             test_unsharp_leaves_flat_areas_alone),
            ("a neutral grade costs nothing", test_neutral_grade_is_a_no_op),
            ("greys stay grey once drawn",
             test_greys_stay_grey_through_the_renderer),
            ("flattening does not change the picture",
             test_flattening_does_not_change_the_picture),
            ("every look draws", test_every_look_draws),
            ("the monochrome looks are monochrome",
             test_monochrome_looks_are_monochrome),
        ):
            check(name, fn)

        print("\n-- the glass " + "-" * 46)
        for name, fn in (
            ("every theme makes valid CSS", test_every_theme_makes_valid_css),
            ("blurred glass is thin enough to see through",
             test_glass_panels_thin_out_when_blurred),
            ("the grain bakes its own alpha", test_grain_bakes_its_own_alpha),
            ("the blur classes match the slabs",
             test_radius_classes_match_the_slabs),
        ):
            check(name, fn)

        print("\n-- stills " + "-" * 49)
        needs_media("loading a photograph", test_loading_a_photograph, "test.png")
        needs_media("loading something that is not a picture",
                    test_loading_something_that_is_not_a_picture, "clip.mp4")
        needs_media("loading an animation", test_loading_an_animation, "anim.gif")
        needs_media("a large picture is loaded smaller",
                    test_a_large_picture_is_loaded_smaller, "test.png")
        needs_media("thumbnails", test_thumbnails, "test.png")

        print("\n-- exporting " + "-" * 46)
        needs_media("exporting a still", test_exporting_a_still, "test.png")
        needs_media("exporting applies the grade",
                    test_exporting_applies_the_grade, "test.png")
        needs_media("rotation changes the shape",
                    test_rotation_changes_the_shape, "test.png")
        needs_media("export refuses to overwrite the original",
                    test_export_refuses_to_overwrite_the_original, "test.png")
        check("suggested names are new", test_suggested_names_are_new)
        check("export formats are honest", test_export_formats_are_honest)

        print("\n-- the player " + "-" * 45)
        check("GStreamer is here", test_player_is_available)
        needs_media("playing and seeking", test_playing_and_seeking, "clip.mp4")
        needs_media("choosing an audio track", test_track_selection, "multi.mkv")
        needs_media("playing something that is not media",
                    test_playing_something_that_is_not_media, "clip.srt")
        needs_media("a poster frame from a video", test_video_poster, "clip.mp4")
        needs_media("album art out of the tags",
                    test_album_art_out_of_the_tags, "withart.mp3")
        needs_media("sound with no art is not an error",
                    test_sound_with_no_art_is_not_an_error, "tone.ogg")
        needs_media("exporting a video", test_video_export, "clip.mp4")

        print("\n-- the window " + "-" * 45)
        check("the window builds and fits a tiling column",
              test_the_window_builds_and_fits_a_tiling_column)
        needs_media("opening a folder in the window",
                    test_opening_a_folder_in_the_window, "test.png")
        needs_media("a sound file shows something",
                    test_audio_shows_something, "tone.ogg")

        print("\n-- pages, palaces and Claude, on screen " + "-" * 19)
        check("the strip marks several", test_the_strip_marks_several)
        check("the palette finds a command by name", test_the_palette_finds_by_name)
        needs_media("the corner editor draws the page",
                    test_the_corner_editor_draws_the_page, "page.jpg")
        needs_media("the importer goes page by page",
                    test_the_importer_goes_page_by_page, "page.jpg", "test.png")
        needs_media("the palace dialog puts it there",
                    test_the_palace_dialog_puts_it_there, "page.jpg")
        needs_media("asking Claude needs Claude", test_asking_claude_needs_claude,
                    "page.jpg")

    print("\n" + "=" * 60)
    if skipped:
        for name, why in skipped:
            print(f"skipped  {name}  ({why})")
    if failures:
        print(f"FAILED   {len(failures)}")
        for name, exc in failures:
            print(f"   {name}: {exc}")
        return 1
    print("every test passed")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        effects.shutdown()
        shutil.rmtree(scratch, ignore_errors=True)
    sys.exit(code)
