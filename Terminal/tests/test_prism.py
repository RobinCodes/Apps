"""What can be checked without a screen, and what needs one.

    python3 tests/test_prism.py

The first half needs nothing installed beyond Python: the palettes and their
contrast, the settings file, the command matcher, the shell command and the
environment it is given. The second half opens a real window on a real
display and drives it — splitting panes, opening the palette, changing theme
— and the last of those actually starts a shell on a pseudo terminal and
reads back what it printed, because every other test in this file would pass
on a terminal that could not run anything.

Two things here are regression tests for bugs that were real:

* `the grain is faded in the tile rather than by an opacity node` — GSK's GL
  renderer ignores an opacity node wrapped around a repeat node and drew the
  frosting at full strength, which turned the whole window to static. The
  Cairo renderer composed it correctly, so this is checked on the tile
  itself rather than on anything rendered.
* `the window fits in a tiling column` — the screen this is written on is
  1366 wide and niri's narrowest preset column is about 455, so a window
  that cannot get down to it cannot be tiled.

Nothing here follows a link or launches anything: a test that activated a URL
would hand it to the desktop portal, which opens a browser on whatever
session the suite is running in.
"""

import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

scratch = tempfile.mkdtemp(prefix="prism-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(scratch, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(scratch, "cache")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from prism import command, config as config_module, glass, paths, shell  # noqa: E402
from prism import theme as theming  # noqa: E402
from prism import widgets  # noqa: E402

HAVE_VTE = True
try:
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte  # noqa: F401,E402
except (ValueError, ImportError):
    HAVE_VTE = False

# The narrowest column niri offers on a 1366-wide screen.
TILING_COLUMN = 455

failures = []
skipped = []


def check(name, fn):
    try:
        fn()
    except BaseException as exc:  # noqa: BLE001 - reported, then re-raised
        failures.append((name, exc))
        print(f"  FAIL  {name}: {exc}")
        return
    print(f"  ok  {name}")


# ------------------------------------------------------------- palettes -----

def test_every_theme_keeps_its_text_readable():
    for scheme in theming.THEMES.values():
        ratio = theming.contrast(scheme.fg, scheme.bg)
        assert ratio >= 7.0, f"{scheme.name}: foreground is {ratio:.1f}:1"


def test_every_colour_that_carries_meaning_is_legible():
    for scheme in theming.THEMES.values():
        for index in theming.CHROMATIC:
            ratio = theming.contrast(scheme.ansi[index], scheme.bg)
            assert ratio >= 4.5, \
                f"{scheme.name}: ANSI {index} is {ratio:.1f}:1"


def test_the_grey_ramp_only_disappears_at_one_end():
    # One grey is meant to be the background — ANSI 0 on a dark theme, ANSI
    # 15 on a light one. The other three have to stay visible.
    for scheme in theming.THEMES.values():
        ramp = theming.grey_ramp(scheme)
        for index, ratio in ramp[1:]:
            assert ratio >= 3.0, \
                f"{scheme.name}: ANSI {index} is {ratio:.1f}:1"


def test_a_light_theme_puts_its_greys_the_other_way_up():
    dark = theming.get("aurora")
    light = theming.get("daylight")
    assert theming.grey_ramp(dark)[0][0] == 0
    assert theming.grey_ramp(light)[0][0] == 15


def test_an_unknown_theme_falls_back_rather_than_raising():
    assert theming.get("no-such-theme").key == theming.DEFAULT
    assert theming.get("").key == theming.DEFAULT


def test_the_themes_are_listed_dark_first():
    listed = theming.ordered()
    assert [t.dark for t in listed] == sorted((t.dark for t in listed), reverse=True)


def test_every_theme_has_sixteen_colours_and_some_light_behind_it():
    for scheme in theming.THEMES.values():
        assert len(scheme.ansi) == 16, scheme.name
        assert scheme.aurora, scheme.name
        for colour in list(scheme.ansi) + list(scheme.aurora) + [
                scheme.fg, scheme.bg, scheme.cursor, scheme.glass, scheme.accent]:
            assert len(colour) == 7 and colour[0] == "#", f"{scheme.name}: {colour}"


# ------------------------------------------------------------- settings -----

def test_the_settings_start_at_their_defaults():
    conf = config_module.Config()
    for key, value in config_module.DEFAULTS.items():
        assert conf[key] == value, key


def test_a_setting_is_clamped_to_its_range():
    conf = config_module.Config()
    conf.set("opacity", 4.0)
    assert conf["opacity"] == 1.0
    conf.set("opacity", -3)
    assert conf["opacity"] == 0.55
    conf.set("cursor_shape", "spiral")
    assert conf["cursor_shape"] == config_module.DEFAULTS["cursor_shape"]


def test_a_setting_survives_being_written_and_read_back():
    conf = config_module.Config()
    conf.set("font_size", 15.5)
    conf.set("theme", "ember")
    again = config_module.Config()
    assert again["font_size"] == 15.5
    assert again["theme"] == "ember"


def test_a_settings_file_of_the_wrong_shape_is_ignored():
    os.makedirs(config_module.DIR, exist_ok=True)
    with open(config_module.PATH, "w", encoding="utf-8") as fh:
        fh.write('{"opacity": "very", "font_size": true, "theme": 7, '
                 '"motion": "yes", "nonsense": 1}')
    conf = config_module.Config()
    assert conf["opacity"] == config_module.DEFAULTS["opacity"]
    assert conf["font_size"] == config_module.DEFAULTS["font_size"]
    assert conf["theme"] == config_module.DEFAULTS["theme"]
    assert conf["motion"] == config_module.DEFAULTS["motion"]
    assert "nonsense" not in conf
    os.remove(config_module.PATH)


def test_an_integer_is_accepted_where_a_float_belongs():
    os.makedirs(config_module.DIR, exist_ok=True)
    with open(config_module.PATH, "w", encoding="utf-8") as fh:
        fh.write('{"opacity": 1, "font_size": 12}')
    conf = config_module.Config()
    assert conf["opacity"] == 1.0
    assert conf["font_size"] == 12.0
    os.remove(config_module.PATH)


def test_following_the_desktop_picks_the_matching_theme():
    conf = config_module.Config()
    conf["follow_system"] = True
    conf["dark_theme"] = "obsidian"
    conf["light_theme"] = "porcelain"
    assert conf.theme_key(True) == "obsidian"
    assert conf.theme_key(False) == "porcelain"
    conf["follow_system"] = False
    conf["theme"] = "ember"
    assert conf.theme_key(True) == "ember"


def test_the_scratch_directories_are_the_ones_in_use():
    assert paths.config_home().startswith(scratch)
    assert paths.data_home().startswith(scratch)


# -------------------------------------------------------- command palette ---

def _commands():
    names = [("New tab", "Session"), ("New window", "Session"),
             ("Close tab", "Session"), ("Split to the right", "Panes"),
             ("Split downwards", "Panes"), ("Close this pane", "Panes"),
             ("Find in output", "Edit"), ("Copy selection", "Edit"),
             ("Paste", "Edit"), ("Reset zoom", "View"),
             ("Full screen", "View"), ("Preferences", "Appearance")]
    return [command.Command(label, label, group, lambda: None)
            for label, group in names]


def test_an_abbreviation_finds_the_command_it_abbreviates():
    wanted = {"nt": "New tab", "nw": "New window", "spr": "Split to the right",
              "spd": "Split downwards", "cp": "Close this pane",
              "fio": "Find in output", "rz": "Reset zoom",
              "fs": "Full screen", "pref": "Preferences"}
    for query, label in wanted.items():
        best = command.rank(query, _commands())
        assert best, f"{query!r} matched nothing"
        assert best[0].label == label, \
            f"{query!r} found {best[0].label!r}, wanted {label!r}"


def test_a_query_that_matches_nothing_returns_nothing():
    assert command.rank("zzzq", _commands()) == []


def test_matching_is_a_subsequence_not_a_substring():
    assert command.score("ntb", "New tab") is not None
    assert command.score("tabnew", "New tab") is None


def test_a_name_match_beats_a_group_match():
    items = [command.Command("a", "Paste", "Edit", None),
             command.Command("b", "Something else", "Paste", None)]
    assert command.rank("paste", items)[0].label == "Paste"


# ----------------------------------------------------------------- glass ----

def test_the_grain_is_faded_in_the_tile_rather_than_by_an_opacity_node():
    # GSK's GL renderer draws a repeat node at full strength whatever
    # opacity it is wrapped in, so the fade has to be in the pixels.
    faint = glass.grain(0.02)
    strong = glass.grain(1.0)
    assert faint is not strong
    faint_bytes = faint.save_to_tiff_bytes().get_data()
    assert faint_bytes, "the tile has no pixels"
    # Reading the texture back is renderer business; what can be asserted
    # here is that two different alphas really are two different tiles and
    # that the cache hands the same one back twice.
    assert glass.grain(0.02) is faint


def test_shading_a_colour_moves_it_towards_white_or_black():
    assert glass.shade("#808080", 1.0) == "#FFFFFF"
    assert glass.shade("#808080", -1.0) == "#000000"
    assert glass.shade("#808080", 0.0) == "#808080"
    lighter = glass.shade("#102030", 0.2)
    assert theming.luminance(lighter) > theming.luminance("#102030")


def test_text_glass_is_more_opaque_than_the_window_is_asked_to_be():
    scheme = theming.get("aurora")
    for opacity in (0.55, 0.7, 0.94, 1.0):
        pane = glass.Material.pane(scheme, opacity)
        assert pane.alpha >= scheme.glass_alpha * opacity
        assert pane.alpha <= 1.0


def test_a_card_passes_less_light_than_a_pane_and_a_pane_less_than_the_chrome():
    scheme = theming.get("aurora")
    card = glass.Material.card(scheme, 0.94)
    pane = glass.Material.pane(scheme, 0.94)
    chrome = glass.Material.chrome(scheme, 0.94)
    assert card.refraction < pane.refraction < chrome.refraction


def test_the_light_is_sampled_in_window_coordinates_not_widget_ones():
    # Two widgets at different offsets must see the same blob in the same
    # place on screen, or the glow breaks at every pane edge.
    light = glass.Light(theming.get("aurora"), motion=False)
    light.clock = 3.0
    whole = light.sample(1000, 800)
    offset = light.sample(1000, 800, 200, 100)
    for (x1, y1, *_), (x2, y2, *_) in zip(whole, offset):
        assert abs((x1 - 200) - x2) < 0.001
        assert abs((y1 - 100) - y2) < 0.001


def test_the_light_keeps_moving_and_does_not_jump():
    light = glass.Light(theming.get("aurora"), motion=False)
    seen = []
    for tick in range(0, 240, 10):
        light.clock = tick
        seen.append(light.sample(100, 100)[0][:2])
    moved = [abs(a[0] - b[0]) + abs(a[1] - b[1])
             for a, b in zip(seen, seen[1:])]
    assert max(moved) > 0.5, "the light never moves"
    assert max(moved) < 30, "the light jumps"


# ------------------------------------------------------------- the shell ----

def test_the_shell_command_comes_from_the_settings_then_the_environment():
    conf = config_module.Config()
    conf["shell"] = "/bin/dash -i"
    assert shell.shell_argv(conf) == ["/bin/dash", "-i"]
    conf["shell"] = ""
    os.environ["SHELL"] = "/bin/zsh"
    assert shell.shell_argv(conf) == ["/bin/zsh"]
    conf["login_shell"] = True
    assert shell.shell_argv(conf) == ["/bin/zsh", "-l"]


def test_the_child_is_told_what_terminal_it_is_in():
    os.environ["COLUMNS"] = "9999"
    env = dict(item.split("=", 1) for item in shell.child_environment())
    assert env["TERM"] == "xterm-256color"
    assert env["COLORTERM"] == "truecolor"
    assert env["TERM_PROGRAM"] == "Prism"
    # This process's idea of a terminal size is not the child's.
    assert "COLUMNS" not in env
    del os.environ["COLUMNS"]


def test_a_paste_that_would_run_itself_is_held_up():
    assert not shell.paste_runs_itself("ls -la")
    assert not shell.paste_runs_itself("make install\n")   # how a command is copied
    assert not shell.paste_runs_itself("a\tb")
    assert shell.paste_runs_itself("echo hi\nrm -rf /\n")
    assert shell.paste_runs_itself("innocent\rsneaky")
    assert shell.paste_runs_itself("ls \x1b[200~ hidden")


def test_a_tab_is_named_after_the_directory_not_the_shells_title():
    # What bash puts there, which is the same in every tab.
    assert shell.tab_name("robin@Arch:~/Projects/Apps", "/home/robin/Projects/Apps",
                          "/home/robin") == "Apps"
    assert shell.tab_name("robin@Arch:~", "/home/robin", "/home/robin") == "~"
    assert shell.tab_name("", "/", "/home/robin") == "/"
    # What a program puts there, which is worth saying.
    assert shell.tab_name("vim README.md", "/home/robin") == "vim README.md"
    assert shell.tab_name("htop", "/home/robin") == "htop"
    # Nothing to go on at all.
    assert shell.tab_name("", None) == "Terminal"


def test_an_empty_font_family_means_the_desktops_own():
    assert shell.monospace_family("") == "monospace"
    assert shell.monospace_family("  ") == "monospace"
    assert shell.monospace_family("Adwaita Mono") == "Adwaita Mono"


def test_the_font_is_built_at_the_size_asked_for():
    from gi.repository import Pango

    desc = shell.font_for("Adwaita Mono", 13.5)
    assert desc.get_family() == "Adwaita Mono"
    assert abs(desc.get_size() / Pango.SCALE - 13.5) < 0.01


def test_a_half_typed_regular_expression_is_not_an_error():
    if not HAVE_VTE:
        skipped.append("search regexes need VTE")
        return
    from prism import find

    assert find.build_regex("", False, False) is None
    assert find.build_regex("(", True, False) is None       # not finished yet
    assert find.build_regex("(ok)", True, False) is not None
    # Outside regex mode the same text is matched literally.
    assert find.build_regex("a(b", False, False) is not None


# ------------------------------------------------------- on a real display --

def test_the_stylesheet_parses_for_every_theme(window):
    for scheme in theming.ordered():
        broken = []
        provider = Gtk.CssProvider()
        provider.connect("parsing-error",
                         lambda _p, _s, error: broken.append(error.message))
        provider.load_from_string(widgets.stylesheet(scheme))
        assert not broken, f"{scheme.name}: {broken}"


def test_the_window_fits_in_a_tiling_column(window):
    minimum = window.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    assert minimum <= TILING_COLUMN, \
        f"the window will not go below {minimum}px"


def test_the_window_opens_with_one_tab_and_one_pane(window):
    assert window.tabs.get_n_pages() == 1
    assert len(window.group.panes) == 1
    assert window.active_pane is not None


def test_splitting_makes_a_pane_and_closing_takes_it_away(window):
    group = window.group
    before = len(group.panes)
    group.split(True)
    assert len(group.panes) == before + 1
    group.split(False)
    assert len(group.panes) == before + 2
    group.close_active()
    assert len(group.panes) == before + 1
    group.close_active()
    assert len(group.panes) == before


def test_only_the_focused_pane_is_lit(window):
    group = window.group
    group.split(True)
    lit = [pane for pane in group.panes if pane._focused]
    assert len(lit) == 1, f"{len(lit)} panes are lit"
    assert lit[0] is group.active
    group.close_active()
    # Back to one pane, which is always lit whether or not it has the focus.
    assert group.panes[0]._focused


def test_the_tab_bar_appears_when_a_second_tab_does(window):
    window.config["tab_bar"] = "auto"
    window._sync_tab_bar()
    assert not window.tab_bar.get_visible()
    window.new_tab()
    assert window.tab_bar.get_visible()
    window._close_tab()


def test_the_palette_opens_filters_and_closes(window):
    window._open_palette()
    assert window.palette.get_visible()
    assert window.scrim.get_visible()
    assert window.palette._shown, "the palette is empty"
    window.palette.entry.set_text("split")
    labels = [c.label for c in window.palette._shown]
    assert len(labels) >= 2, labels
    assert all("Split" in label for label in labels[:2]), labels
    # A subsequence match will always find some nonsense; the cutoff is
    # there to keep it to a little rather than all of it.
    assert len(labels) <= 4, labels
    window.palette.close()
    window._run_command(None)
    assert not window.palette.get_visible()
    assert not window.scrim.get_visible()


def test_the_palette_never_grows_past_the_window(window):
    window.set_default_size(900, 420)
    pump(200)
    window._open_palette()
    pump(200)
    assert window.palette.get_height() <= window.get_height(), (
        f"palette is {window.palette.get_height()}px in a "
        f"{window.get_height()}px window")
    window.palette.close()
    window._run_command(None)
    window.set_default_size(900, 560)
    pump(150)


def test_every_theme_can_be_switched_to_while_running(window):
    for scheme in theming.ordered():
        window._use_theme(scheme.key)
        assert window.scheme.key == scheme.key
        assert window.light.scheme.key == scheme.key
        assert window.chrome.material.scheme.key == scheme.key
        for index in range(window.tabs.get_n_pages()):
            for pane in window.tabs.get_nth_page(index).get_child().panes:
                assert pane.material.scheme.key == scheme.key
    window._use_theme(theming.DEFAULT)


def test_the_preferences_open_and_change_something(window):
    from prism import prefs

    dialog = prefs.PrefsDialog(window.config, window.apply_settings)
    dialog.present(window)
    pump(150)
    before = window.config["opacity"]
    window.config.set("opacity", 0.8)
    window.apply_settings()
    assert window.config["opacity"] == 0.8
    window.config.set("opacity", before)
    window.apply_settings()
    dialog.force_close()
    pump(100)


def test_the_dialogs_in_the_menu_all_build(window):
    """Everything reachable from the menu, opened once.

    These are the parts with no other test: a wrong argument to one of them
    is a traceback the first time somebody opens the menu, and nothing else
    here would catch it.
    """
    window._open_shortcuts()
    pump(120)

    # The About dialog is the application's, built the same way app.py does.
    from prism import __version__

    about = Adw.AboutDialog(
        application_name="Prism", application_icon="utilities-terminal",
        version=__version__, developer_name="Robin",
        comments="A terminal made of glass.",
        license_type=Gtk.License.MIT_X11)
    about.present(window)
    pump(120)
    about.force_close()

    # And the menu model itself: every action it names has to exist.
    menu = window._menu_button().get_menu_model()
    missing = [name for name in _menu_actions(menu)
               if not _action_exists(window, name)]
    assert not missing, missing
    pump(80)


def _menu_actions(model):
    for index in range(model.get_n_items()):
        value = model.get_item_attribute_value(index, "action", None)
        if value is not None:
            yield value.get_string()
        for link in ("section", "submenu"):
            child = model.get_item_link(index, link)
            if child is not None:
                yield from _menu_actions(child)


def _action_exists(window, name):
    scope, _, action = name.partition(".")
    if scope == "win":
        return window.has_action(action)
    if scope == "app":
        # Not asked of the application object: the suite runs on a plain
        # Adw.Application so that it cannot take com.robin.Prism's bus name
        # from a copy Robin is actually using. The real one's list is what
        # the menu has to agree with.
        from prism.app import APP_ACTIONS

        return action in APP_ACTIONS
    return False


def test_the_find_bar_opens_and_closes(window):
    window._open_find()
    assert window.find_bar.get_visible()
    window.find_bar.entry.set_text("nothing-is-here-at-all")
    window.find_bar.close()
    assert not window.find_bar.get_visible()


def test_turning_everything_off_still_draws(window):
    for key in ("motion", "specular", "grain"):
        window.config[key] = False
    window.apply_settings()
    window.config["opacity"] = 0.55
    window.apply_settings()
    pump(120)
    assert window.active_pane is not None
    for key in ("motion", "specular", "grain"):
        window.config[key] = config_module.DEFAULTS[key]
    window.config["opacity"] = config_module.DEFAULTS["opacity"]
    window.apply_settings()


def test_a_saved_session_comes_back(window):
    """Write a session out, open a window on it, and count what returns."""
    import json

    from prism import window as window_module

    app = window.get_application()
    os.makedirs(os.path.dirname(window_module.SESSION_PATH), exist_ok=True)
    with open(window_module.SESSION_PATH, "w", encoding="utf-8") as fh:
        json.dump({"tabs": [{"panes": ["/tmp"]}, {"panes": ["/usr"]}]}, fh)

    app.config["restore_session"] = True
    try:
        restored = window_module.MainWindow(app, restore=True)
        restored.set_default_size(700, 420)
        restored.present()
        pump(500)
        assert restored.tabs.get_n_pages() == 2, restored.tabs.get_n_pages()
        places = [page_cwd(restored, i) for i in range(2)]
        assert places == ["/tmp", "/usr"], places
        restored._closing = True
        restored.close()
        pump(200)
    finally:
        app.config["restore_session"] = False
        try:
            os.remove(window_module.SESSION_PATH)
        except OSError:
            pass


def page_cwd(window, index):
    group = window.tabs.get_nth_page(index).get_child()
    wait_for(lambda: bool(group.panes[0].cwd()))
    return group.panes[0].cwd()


def test_a_session_file_of_the_wrong_shape_is_ignored(window):
    import json

    from prism import window as window_module

    app = window.get_application()
    os.makedirs(os.path.dirname(window_module.SESSION_PATH), exist_ok=True)
    for rubbish in ("[]", '{"tabs": "lots"}', '{"tabs": [1, 2, {"panes": [3]}]}',
                    "not json at all"):
        with open(window_module.SESSION_PATH, "w", encoding="utf-8") as fh:
            fh.write(rubbish)
        app.config["restore_session"] = True
        try:
            opened = window_module.MainWindow(app, restore=True)
            opened.set_default_size(700, 420)
            opened.present()
            pump(250)
            # Whatever the file said, there is a usable window.
            assert opened.tabs.get_n_pages() >= 1, rubbish
            opened._closing = True
            opened.close()
            pump(150)
        finally:
            app.config["restore_session"] = False
    os.remove(window_module.SESSION_PATH)


def test_a_closed_window_stops_listening_to_the_desktop(window):
    """Adw.StyleManager outlives every window, so handlers have to be let go.

    A window that stayed connected would be kept alive by the closure and
    called again, after it was destroyed, the next time the desktop switched
    between light and dark.
    """
    from gi.repository import GObject

    from prism import window as window_module

    extra = window_module.MainWindow(window.get_application(), restore=False)
    extra.present()
    pump(200)
    manager, handler = extra._style_manager, extra._style_handler
    assert handler, "the window never connected"
    assert GObject.signal_handler_is_connected(manager, handler)

    extra._closing = True
    extra.close()
    pump(300)
    assert not extra._style_handler, "the window never let go"
    assert not GObject.signal_handler_is_connected(manager, handler)


def test_the_session_records_a_tab_for_each_tab(window):
    window.new_tab()
    state = window.session_state()
    assert len(state["tabs"]) == window.tabs.get_n_pages()
    assert all("panes" in tab for tab in state["tabs"])
    window._close_tab()


def test_a_shell_runs_and_its_output_comes_back(window):
    """The one test that proves this is a terminal and not a picture of one."""
    pane = window.active_pane
    pane.send("echo prism-is-alive\n")

    def answered():
        text = pane.terminal.get_text_range_format(Vte.Format.TEXT, 0, 0, 200, 200)[0]
        return bool(text and "prism-is-alive" in text.replace("\n", " "))

    assert wait_for(answered), "the shell never answered"


def test_a_command_given_with_dash_e_is_the_child(window):
    """`prism -e something` runs it, rather than typing it at a shell."""
    group = window.new_tab(command=["/bin/sh", "-c", "echo i-am-the-child; sleep 20"])
    pane = group.panes[0]

    def printed():
        text = pane.terminal.get_text_range_format(Vte.Format.TEXT, 0, 0, 80, 200)[0]
        return bool(text and "i-am-the-child" in text)

    assert wait_for(printed), "the command never ran"
    text = pane.terminal.get_text_range_format(Vte.Format.TEXT, 0, 0, 80, 200)[0]
    # No shell prompt before it, because there is no shell.
    assert "$" not in (text or "").split("i-am-the-child")[0]
    # And the tab knows what is running, for the close prompt.
    assert pane.foreground_command() == "sh", pane.foreground_command()
    window._close_tab()
    pump(200)


def test_a_pane_knows_what_directory_it_is_in(window):
    """OSC 7, which vte-common wires into bash and zsh on this distribution."""
    pane = window.active_pane
    pane.send("cd /tmp\n")
    wait_for(lambda: pane.cwd() == "/tmp")
    where = pane.cwd()
    if where is None:
        skipped.append("the shell does not report its directory (no OSC 7)")
        return
    assert where == "/tmp", where
    pane.send("cd ~\n")


# ------------------------------------------------------------------ run -----

def wait_for(ready, seconds=10.0, step=40):
    """Pump the main loop until `ready()` is true, or give up.

    Counting iterations instead would make every one of these tests a bet on
    how fast the machine is: under a freshly started desktop portal the same
    work takes several times as long, and a fixed sixty-times-fifty-
    milliseconds loop starts failing for no reason anyone can act on.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if ready():
            return True
        pump(step)
    return ready()


def pump(ms):
    """Let GTK get on with it for a while.

    The deadline is checked on every iteration, not only between batches of
    them. With the light animating there is always another frame pending, so
    an inner `while context.pending()` loop never ends and the deadline is
    never looked at again — which is a hang, and was one.
    """
    deadline = GLib.get_monotonic_time() + ms * 1000
    context = GLib.MainContext.default()
    while GLib.get_monotonic_time() < deadline:
        if not context.iteration(False):
            time.sleep(0.002)


def _wants(fn, argument):
    code = getattr(fn, "__code__", None)
    return bool(code and code.co_argcount == 1 and code.co_varnames[0] == argument)


def main():
    print("Prism")
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and not _wants(fn, "window"):
            check(name[5:].replace("_", " "), fn)

    if not HAVE_VTE:
        print("\n  skipped the window tests: VTE's GTK 4 build is not installed")
        skipped.append("the window tests need VTE")
        return verdict()

    Adw.init()
    app = Adw.Application(application_id="com.robin.PrismTests",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

    from prism.config import Config
    from prism.window import MainWindow

    app.config = Config()
    # A shell that cannot be missing and starts in no time.
    app.config["shell"] = "/bin/sh"
    app.config["confirm_close"] = False
    app.config["restore_session"] = False

    def run_gtk_tests():
        window = MainWindow(app, restore=False)
        window.set_default_size(900, 560)
        window.present()
        pump(400)

        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and _wants(fn, "window"):
                check(name[5:].replace("_", " "),
                      lambda fn=fn, w=window: fn(w))

        window._closing = True
        window.close()
        app.release()
        app.quit()
        return GLib.SOURCE_REMOVE

    def activate(instance):
        instance.hold()
        GLib.timeout_add(200, run_gtk_tests)

    app.connect("activate", activate)
    app.run([])
    return verdict()


def verdict():
    for note in skipped:
        print(f"  skipped  {note}")
    if failures:
        print(f"\n{len(failures)} failed:")
        for name, exc in failures:
            print(f"  {name}: {exc}")
        raise SystemExit(1)
    print("\nall good")
    return 0


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
