#!/usr/bin/env python3
"""The house style's own suite: the palettes, the sheet, and the vendoring.

Three things are checked here that no single app's suite can check, because
each of them is a fact about the *set* of apps.

*The palettes are legible.* Every theme's text, every meaningful colour in it
and every matte is measured against its own background. A palette here is
arithmetic that happens to look nice; this is the arithmetic.

*The sheet parses.* The house stylesheet is generated, and a CSS parse error in
GTK is a warning on stderr and a rule that silently does nothing — so every
theme's sheet is loaded into a real `GtkCssProvider` and the errors it reports
are collected rather than ignored. Skipped when the bindings are not installed,
like the rest of the suites here.

*The copies are in step.* `theme.py` and `housestyle.py` are vendored into each
app rather than imported from one place, for the same reason `updater.py` is:
each app folder is a thing you can copy to another machine and run. The cost is
that nine copies can drift, so the last test runs `house/sync.py --check` and
fails if any of them has.
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# As a package, not as loose modules: `housestyle` imports `theme` as a
# sibling of its own package, because that is what it has to do once it has
# been copied into an app. Importing it any other way here would test
# something that is not the file the apps run.
sys.path.insert(0, ROOT)

from house import theme as theming  # noqa: E402


def have_gtk() -> bool:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401
    except Exception:
        return False
    return True


# ---------------------------------------------------------------- palettes --

def test_every_theme_keeps_its_text_readable():
    for scheme in theming.ordered():
        ratio = theming.contrast(scheme.fg, scheme.bg)
        assert ratio >= 7.0, f"{scheme.key}: body text is {ratio:.2f}:1"
        ratio = theming.contrast(scheme.dim, scheme.bg)
        assert ratio >= 4.5, f"{scheme.key}: quiet text is {ratio:.2f}:1"
        ratio = theming.contrast(scheme.accent, scheme.bg)
        assert ratio >= 3.0, f"{scheme.key}: the accent is {ratio:.2f}:1"


def test_a_label_on_the_accent_is_readable():
    """Whatever `on()` picks has to actually be readable on it.

    The function measures rather than thresholds, so this is the check that
    the better of the two answers is also a good enough one — an accent so
    mid-toned that neither black nor white clears 4.5:1 would pass `on()`
    and fail here, which is the right way round.
    """
    for scheme in theming.ordered():
        ink = theming.on(scheme.accent)
        ratio = theming.contrast(ink, scheme.accent)
        assert ratio >= 4.5, f"{scheme.key}: {ink} on the accent is {ratio:.2f}:1"


def test_every_colour_that_carries_meaning_is_legible():
    for scheme in theming.ordered():
        for index in theming.CHROMATIC:
            ratio = theming.contrast(scheme.ansi[index], scheme.bg)
            assert ratio >= 4.5, f"{scheme.key}: ANSI {index} is {ratio:.2f}:1"


def test_the_grey_ramp_only_disappears_at_one_end():
    # One grey is meant to be the background — ANSI 0 on a dark theme, ANSI 15
    # on a light one. The other three have to stay visible.
    for scheme in theming.ordered():
        for index, ratio in theming.grey_ramp(scheme)[1:]:
            assert ratio >= 3.0, f"{scheme.key}: ANSI {index} is {ratio:.2f}:1"


def test_a_light_theme_puts_its_greys_the_other_way_up():
    assert theming.grey_ramp(theming.get(theming.DARK_DEFAULT))[0][0] == 0
    assert theming.grey_ramp(theming.get(theming.LIGHT_DEFAULT))[0][0] == 15


def test_every_matte_is_neutral():
    """The surround a picture sits on must not have a hue of its own.

    The eye judges a colour against its surroundings, so a warm matte makes
    every photograph on it look cold. Only The Eye draws one, and every theme
    carries one anyway — a theme that is only half there in one of the apps
    is not a house style.
    """
    for scheme in theming.ordered():
        spread = scheme.neutral_matte()
        assert spread <= 0.012, \
            f"{scheme.key}: the matte {scheme.matte} has a hue (spread {spread:.4f})"


def test_every_theme_is_complete():
    for scheme in theming.ordered():
        assert len(scheme.ansi) == 16, scheme.key
        assert scheme.aurora, scheme.key
        assert scheme.blurb, scheme.key
        for colour in (list(scheme.ansi) + list(scheme.aurora)
                       + [scheme.fg, scheme.bg, scheme.dim, scheme.accent,
                          scheme.cursor, scheme.matte, scheme.glass]):
            assert len(colour) == 7 and colour[0] == "#", f"{scheme.key}: {colour}"
        assert 0.0 <= scheme.glass_alpha <= 1.0, scheme.key
        assert 0.0 <= scheme.rim <= 1.5, scheme.key


def test_the_house_pair_exists_and_is_the_right_way_round():
    assert theming.get(theming.DARK_DEFAULT).dark
    assert not theming.get(theming.LIGHT_DEFAULT).dark
    # And they lead their halves of the list, because they are the two
    # everything else is tuned against.
    listed = theming.ordered()
    assert listed[0].key == theming.DARK_DEFAULT
    assert next(t for t in listed if not t.dark).key == theming.LIGHT_DEFAULT


def test_the_themes_are_listed_dark_first():
    listed = theming.ordered()
    assert [t.dark for t in listed] == sorted((t.dark for t in listed), reverse=True)


def test_a_name_nobody_knows_falls_back_rather_than_raising():
    assert theming.get("no such theme").key == theming.DEFAULT
    assert theming.get("").key == theming.DEFAULT
    assert theming.get(None).key == theming.DEFAULT     # type: ignore[arg-type]
    # And a name from before the palettes were shared still resolves.
    assert theming.get("vitreous").key == "aurora"


# ---------------------------------------------------------------- settings --

def test_the_settings_reader_takes_what_it_should_and_no_more():
    config = dict(theming.SETTINGS)
    theming.take(config, {
        "translucency": 1,          # a float written as a whole number
        "rounding": 999,            # out of range
        "glass": "yes",             # wrong type
        "glass_blur": True,         # a bool is not a number
        "theme": "renamed-later",   # not checked here, resolved at lookup
        "nonsense": 1,
    })
    assert config["translucency"] == 1.0
    assert config["rounding"] == theming.RANGES["rounding"][1]
    assert config["glass"] is True                      # the default stands
    assert config["glass_blur"] == theming.SETTINGS["glass_blur"]
    assert config["theme"] == "renamed-later"
    assert "nonsense" not in config


def test_a_settings_mapping_saves_through_its_owner():
    saved = []
    settings = theming.Settings(lambda: saved.append(True))
    settings["theme"] = "ember"
    settings.save()
    assert saved == [True]
    assert settings.payload()["theme"] == "ember"
    assert set(settings.payload()) == set(theming.SETTINGS)


# ------------------------------------------------------------------- sheet --

def test_the_sheet_parses_for_every_theme():
    if not have_gtk():
        print("  skipped (no GTK bindings)")
        return
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    from house import housestyle                        # noqa: PLC0415

    problems: list[str] = []
    for scheme in theming.ordered():
        for translucency, blur, glass in ((1.0, 0, False), (0.86, 26, True),
                                          (0.45, 80, True)):
            provider = Gtk.CssProvider()
            provider.connect(
                "parsing-error",
                lambda _p, section, error, key=scheme.key:
                    problems.append(f"{key}: {section.to_string()}: {error.message}"))
            provider.load_from_string(
                housestyle.sheet(scheme, translucency, 16, blur, glass))
    assert not problems, "\n".join(problems)


def test_the_sheet_names_every_colour_it_uses():
    """Every `@house_*` the sheet reaches for is one the palette defines.

    A `@define-color` that is never defined is not a parse error — GTK
    resolves it to transparent and carries on — so this is the check that a
    renamed colour does not silently turn a surface into a hole.
    """
    if not have_gtk():
        print("  skipped (no GTK bindings)")
        return
    import re

    from house import housestyle                        # noqa: PLC0415

    for scheme in theming.ordered():
        text = housestyle.sheet(scheme, 0.86, 16, 26)
        defined = set(re.findall(r"@define-color\s+(\w+)", text))
        used = set(re.findall(r"@(\w+)", text)) - {"define-color", "media"}
        missing = {name for name in used if name.startswith("house_")} - defined
        assert not missing, f"{scheme.key}: {sorted(missing)}"


# --------------------------------------------------------------- vendoring --

def test_every_app_has_the_same_house_style():
    done = subprocess.run([sys.executable, os.path.join(HERE, "sync.py"), "--check"],
                          capture_output=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, (done.stdout or "") + (done.stderr or "")


# ------------------------------------------------------------------- main --

TESTS = (
    ("every theme keeps its text readable", test_every_theme_keeps_its_text_readable),
    ("a label on the accent is readable", test_a_label_on_the_accent_is_readable),
    ("every colour that carries meaning is legible",
     test_every_colour_that_carries_meaning_is_legible),
    ("the grey ramp only disappears at one end",
     test_the_grey_ramp_only_disappears_at_one_end),
    ("a light theme puts its greys the other way up",
     test_a_light_theme_puts_its_greys_the_other_way_up),
    ("every matte is neutral", test_every_matte_is_neutral),
    ("every theme is complete", test_every_theme_is_complete),
    ("the house pair exists and is the right way round",
     test_the_house_pair_exists_and_is_the_right_way_round),
    ("the themes are listed dark first", test_the_themes_are_listed_dark_first),
    ("a name nobody knows falls back rather than raising",
     test_a_name_nobody_knows_falls_back_rather_than_raising),
    ("the settings reader takes what it should and no more",
     test_the_settings_reader_takes_what_it_should_and_no_more),
    ("a settings mapping saves through its owner",
     test_a_settings_mapping_saves_through_its_owner),
    ("the sheet parses for every theme", test_the_sheet_parses_for_every_theme),
    ("the sheet names every colour it uses", test_the_sheet_names_every_colour_it_uses),
    ("every app has the same house style", test_every_app_has_the_same_house_style),
)


def main() -> int:
    failures = []
    for name, test in TESTS:
        try:
            test()
        except AssertionError as exc:
            failures.append((name, exc))
            print(f"  FAIL  {name}: {exc}")
        else:
            print(f"  ok  {name}")
    if failures:
        print(f"\n{len(failures)} failed")
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
