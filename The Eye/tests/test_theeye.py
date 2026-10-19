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
    """The lists must describe this machine, not a wish."""
    gone = [ext for ext in media.PHOTO if not media.loader_for("x" + ext)]
    assert not gone, f"claimed but not loadable here: {gone}"


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
            win.open_path(os.path.join(MEDIA, "test.png"))
            if not _pump_until(lambda: win.stage.content() is not None, 8):
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
        ("corrupt EXIF loses tags, not the app",
         test_exif_survives_corruption, "exif.jpg"),
        ("a still's details", test_still_details, "test.png"),
    ):
        needs_media(name, fn, *needed)

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
