"""One terminal: the VTE widget, the glass it sits on, and the shell in it.

VTE does the emulation. That is not a small thing to hand off — it is the
part of a terminal that has to be exactly right, from how a wide character
occupies two cells to what happens when a program resizes the window from
inside — and there is no version of this app worth writing that reimplements
it. What is left here is everything around it: the colours, the font, the
pseudo terminal, the clicking on links, and the glass underneath.

The one unusual thing is `set_clear_background(False)`. Normally VTE fills
its allocation with the background colour before drawing any text; told not
to, it draws only the glyphs, and whatever is behind it shows through. That
is what lets the slab from glass.py be the terminal's background rather than
a frame around it. It also means the background colour handed to `set_colors`
is never painted — but it is still *used*, for reverse video and for the
selection, so it has to be the colour the glass actually looks like rather
than something transparent.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Vte", "3.91")

from gi.repository import Gdk, GLib, GObject, Gtk, Vte  # noqa: E402

from . import glass, theme as theming  # noqa: E402
from .shell import child_environment, font_for, shell_argv  # noqa: E402

# PCRE2's flags, which VTE takes as a plain integer. Named here because the
# numbers are meaningless at the call site and PCRE2 is not a dependency we
# can import to ask.
PCRE2_CASELESS = 0x00000008
PCRE2_MULTILINE = 0x00000400
PCRE2_UTF = 0x00080000
PCRE2_NO_UTF_CHECK = 0x40000000

# What counts as a link in output that never said it was one. Deliberately
# conservative: a false positive turns an ordinary word into something that
# changes colour under the pointer and opens a browser when clicked, which is
# worse than missing a link.
URL_PATTERN = (
    r"(?:https?://|ftp://|file://|www\.)"
    r"[-A-Za-z0-9@:%._+~#=/?&,;!$'()*\[\]]{2,}"
    r"[-A-Za-z0-9@:%_+~#=/?&$'*\[\]]"
)

# A path with a line number after it, which is how every compiler and every
# grep in the world reports where something is.
FILE_LINE_PATTERN = r"(?:[~.]?/|\./)?[-\w.+/]+\.\w+:\d+(?::\d+)?"


class TerminalPane(glass.Slab):
    """A slab of glass with a shell running on it.

    Emits `title-changed` when the program in it renames itself, `activity`
    when output arrives — the window uses that both to wake the light and to
    mark an unfocused tab — and `exited` when the shell is gone, which is the
    pane asking to be closed rather than the pane closing itself. Who owns the
    layout decides that.
    """

    __gtype_name__ = "PrismTerminalPane"

    __gsignals__ = {
        "title-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "activity": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "exited": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "open-uri": (GObject.SignalFlags.RUN_LAST, bool, (str,)),
    }

    def __init__(self, config, scheme: theming.Theme, light: glass.Light,
                 cwd: str | None = None, command: list[str] | None = None):
        super().__init__(glass.Material.pane(scheme, config["opacity"],
                                             radius=config["corner_radius"],
                                             grain=config["grain"]),
                         radius=config["corner_radius"],
                         specular=config["specular"], light=light)
        self.add_css_class("pane")
        self._config = config
        self._scheme = scheme
        self._title = ""
        self._exited = False
        self._pid = -1
        self._url_tag = -1
        self._file_tag = -1
        self._command = list(command) if command else None

        self.terminal = Vte.Terminal()
        self.terminal.set_hexpand(True)
        self.terminal.set_vexpand(True)

        # The terminal goes in a scroller for two reasons: it is where the
        # scrollbar comes from, and it is what lets the window be narrower
        # than the grid. A bare VTE asks for the whole of its current grid as
        # its minimum size, so a window containing one could be widened but
        # never narrowed again.
        self.scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            child=self.terminal)
        self.scroller.add_css_class("pane-scroller")
        self.append(self.scroller)

        self._wire()
        self.apply_config(config, scheme)
        self.spawn(cwd, command)

    # ------------------------------------------------------------ setup --

    def _wire(self):
        term = self.terminal
        term.connect("window-title-changed", self._on_title)
        term.connect("contents-changed", self._on_contents)
        term.connect("child-exited", self._on_child_exited)
        term.connect("bell", self._on_bell)
        term.connect("selection-changed", self._on_selection)
        # The current directory arrives as a property notification rather
        # than a signal of its own, and only when the shell cooperates by
        # emitting OSC 7 — which the vte-common package wires into bash and
        # zsh for everyone on this distribution.
        try:
            term.connect("notify::current-directory-uri", self._on_cwd)
        except TypeError:
            pass

        click = Gtk.GestureClick()
        click.set_button(0)
        click.connect("pressed", self._on_click)
        term.add_controller(click)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        term.add_controller(keys)

    def apply_config(self, config, scheme: theming.Theme):
        """Everything in the settings that the terminal itself carries."""
        self._config = config
        self._scheme = scheme
        term = self.terminal

        self.set_material(glass.Material.pane(scheme, config["opacity"],
                                              radius=config["corner_radius"],
                                              grain=config["grain"]))
        self._radius = config["corner_radius"]
        self.set_specular(config["specular"])

        # The same on every side. The scrollbar is an overlay one and sits
        # on top of the last column rather than taking a column of its own,
        # so there is nothing to leave room for, and text that is 12px from
        # the left edge and 6px from the right looks like a mistake.
        pad = config["pane_padding"]
        for setter in (self.scroller.set_margin_start, self.scroller.set_margin_end,
                       self.scroller.set_margin_top, self.scroller.set_margin_bottom):
            setter(pad)

        term.set_font(font_for(config["font_family"], config["font_size"]))
        term.set_cell_height_scale(config["line_height"])
        term.set_cell_width_scale(config["letter_spacing"])
        term.set_bold_is_bright(config["bold_is_bright"])
        term.set_clear_background(False)
        term.set_colors(glass.rgba(scheme.fg), glass.rgba(scheme.bg),
                        [glass.rgba(c) for c in scheme.ansi])
        term.set_color_cursor(glass.rgba(scheme.cursor))
        term.set_color_cursor_foreground(glass.rgba(scheme.bg))
        # A tinted wash rather than a block, so selected text keeps its own
        # colours and stays readable.
        term.set_color_highlight(glass.rgba(scheme.selection(), 0.30))

        term.set_cursor_shape({"block": Vte.CursorShape.BLOCK,
                               "underline": Vte.CursorShape.UNDERLINE}.get(
                                   config["cursor_shape"], Vte.CursorShape.IBEAM))
        term.set_cursor_blink_mode(Vte.CursorBlinkMode.ON if config["cursor_blink"]
                                   else Vte.CursorBlinkMode.OFF)
        term.set_scrollback_lines(config["scrollback_lines"])
        term.set_scroll_on_output(config["scroll_on_output"])
        term.set_scroll_on_keystroke(config["scroll_on_keystroke"])
        term.set_mouse_autohide(config["mouse_autohide"])
        term.set_audible_bell(config["audible_bell"])
        term.set_word_char_exceptions(config["word_chars"])
        term.set_allow_hyperlink(config["hyperlinks"])
        try:
            term.set_enable_sixel(config["sixel"])
        except (AttributeError, TypeError):
            pass

        self._install_matches(config["hyperlinks"])

    def _install_matches(self, enabled: bool):
        """Make bare URLs and `file.c:12` clickable, not just OSC 8 links."""
        term = self.terminal
        for tag in (self._url_tag, self._file_tag):
            if tag >= 0:
                term.match_remove(tag)
        self._url_tag = self._file_tag = -1
        if not enabled:
            return
        flags = PCRE2_UTF | PCRE2_NO_UTF_CHECK | PCRE2_MULTILINE | PCRE2_CASELESS
        for pattern, store in ((URL_PATTERN, "_url_tag"),
                               (FILE_LINE_PATTERN, "_file_tag")):
            try:
                regex = Vte.Regex.new_for_match(pattern, -1, flags)
            except (GLib.Error, TypeError):
                continue
            tag = term.match_add_regex(regex, 0)
            term.match_set_cursor_name(tag, "pointer")
            setattr(self, store, tag)

    # ------------------------------------------------------------ spawn --

    def spawn(self, cwd: str | None = None, command: list[str] | None = None):
        """Start the shell on a pseudo terminal of its own.

        Done through Vte.Pty rather than through `Vte.Terminal.spawn_async`,
        which cannot be called from Python at all: its C signature carries a
        GDestroyNotify for the child-setup data, PyGObject has no way to pass
        a null function pointer for it, and every form of the call fails with
        "Argument 9 does not allow None as a value". Vte.Pty.spawn_async is
        an ordinary Gio-style async method and works. The keyword form is not
        a style choice either — the positional one runs into the same null
        callback a slot earlier.
        """
        # `prism -e htop` runs htop as the child, rather than starting a
        # shell and typing "htop" into it. That is what every program that
        # launches a terminal expects: the window belongs to the command, it
        # closes when the command is done, and there is no shell underneath
        # to be left sitting at a prompt or to mangle the arguments.
        argv = list(command) if command else shell_argv(self._config)
        directory = cwd if cwd and os.path.isdir(cwd) else os.path.expanduser("~")
        try:
            pty = Vte.Pty.new_sync(Vte.PtyFlags.DEFAULT, None)
        except GLib.Error as error:
            self.feed_notice(f"Could not open a terminal: {error.message}")
            return
        self.terminal.set_pty(pty)
        try:
            pty.spawn_async(
                working_directory=directory, argv=argv,
                envv=child_environment(), spawn_flags=GLib.SpawnFlags.DEFAULT,
                child_setup=None, timeout=-1, cancellable=None,
                callback=self._spawned)
        except (GLib.Error, TypeError) as error:
            self.feed_notice(f"Could not start the shell: {error}")

    def _spawned(self, pty, result, *_user):
        try:
            ok, pid = pty.spawn_finish(result)
        except GLib.Error as error:
            self._pid = -1
            # Nowhere else to say it: the pane is the only thing on screen
            # that belongs to this shell, so the failure is written into it.
            self.feed_notice(f"Could not start the shell: {error.message}")
            return
        if not ok:
            self._pid = -1
            self.feed_notice("Could not start the shell.")
            return
        self._pid = pid
        # Without this the child is never reaped and `child-exited` never
        # fires, so a pane whose shell has exited just sits there.
        self.terminal.watch_child(pid)
        if self._command is not None:
            return          # a command of its own runs nothing else
        if self._config["startup_command"].strip():
            self.send(self._config["startup_command"].rstrip("\n") + "\n")

    def feed_notice(self, text: str):
        """Write a line of our own into the terminal, in the theme's red."""
        self.terminal.feed(("\r\n\033[1;31m" + text + "\033[0m\r\n").encode())

    def send(self, text: str):
        self.terminal.feed_child(text.encode())

    # ----------------------------------------------------------- events --

    def _on_title(self, _term):
        title = self.terminal.get_window_title() or ""
        if title != self._title:
            self._title = title
            self.emit("title-changed")

    def _on_cwd(self, *_args):
        self.emit("title-changed")

    def _on_contents(self, _term):
        self.emit("activity")

    def _on_selection(self, _term):
        if self._config["copy_on_select"] and self.terminal.get_has_selection():
            self.terminal.copy_clipboard_format(Vte.Format.TEXT)

    def _on_bell(self, _term):
        if self._config["visual_bell"]:
            self.flash()

    def _on_child_exited(self, _term, status):
        self._exited = True
        self.emit("exited", status)

    def _on_click(self, gesture, n_press, x, y):
        button = gesture.get_current_button()
        state = gesture.get_current_event_state()
        if button == Gdk.BUTTON_PRIMARY and (state & Gdk.ModifierType.CONTROL_MASK):
            if self._activate_link(x, y):
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_key(self, _controller, keyval, _keycode, state):
        # Ctrl+Shift+C copies only when there is a selection; otherwise it has
        # to reach the shell as the interrupt it has always been. Everything
        # else is bound as an action on the window.
        ctrl_shift = (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
        if (state & ctrl_shift) == ctrl_shift and keyval in (Gdk.KEY_C, Gdk.KEY_c):
            if self.terminal.get_has_selection():
                self.terminal.copy_clipboard_format(Vte.Format.TEXT)
                return True
        return False

    def _activate_link(self, x, y) -> bool:
        uri = self.terminal.check_hyperlink_at(x, y)
        if not uri:
            match = self.terminal.check_match_at(x, y)
            uri = match[0] if match else None
        if not uri:
            return False
        if uri.startswith("www."):
            uri = "https://" + uri
        return bool(self.emit("open-uri", uri))

    # ------------------------------------------------------------ state --

    @property
    def title(self) -> str:
        return self._title

    @property
    def exited(self) -> bool:
        return self._exited

    def cwd(self) -> str | None:
        """Where the shell in this pane currently is.

        Asked two ways, because the usual way does not work here. A terminal
        normally learns this from OSC 7, an escape sequence the shell emits
        on every prompt — and on this distribution the hook that emits it
        lives in /etc/profile.d/vte.sh, which is sourced by *login* shells.
        A terminal emulator starts an ordinary interactive shell, nothing
        sources it, and the terminal is never told anything. Relying on it
        alone would mean "new tab in the same directory" quietly opening in
        $HOME forever.

        So OSC 7 is used when it is there — it is the only one of the two
        that can be right about a shell that has sshed somewhere, though
        then the path will not exist here and is dropped — and /proc answers
        otherwise, which needs nothing from the shell at all.
        """
        uri = self.terminal.get_current_directory_uri()
        if uri:
            try:
                path = GLib.filename_from_uri(uri)[0]
            except GLib.Error:
                path = None
            if path and os.path.isdir(path):
                return path
        if self._pid > 0:
            try:
                path = os.readlink(f"/proc/{self._pid}/cwd")
            except OSError:
                return None
            # A deleted directory comes back as "/some/path (deleted)".
            if path and not path.endswith(" (deleted)") and os.path.isdir(path):
                return path
        return None

    def foreground_command(self) -> str | None:
        """What is running in the terminal that is not the shell itself.

        Read from /proc rather than from VTE, which does not track it. Used
        for the "something is still running" question before a tab closes, so
        a wrong answer costs a needless prompt and nothing worse.

        A pane started with `prism -e something` is a special case: the
        child *is* the command, so there is no second process on the pty to
        find and the search below would say nothing is running right up to
        the moment the window closed and killed it.
        """
        if self._pid <= 0 or self._exited:
            return None
        if self._command:
            return os.path.basename(self._command[0])
        try:
            pty = os.readlink(f"/proc/{self._pid}/fd/0")
        except OSError:
            return None
        best = None
        try:
            for entry in os.listdir("/proc"):
                if not entry.isdigit() or entry == str(self._pid):
                    continue
                try:
                    if os.readlink(f"/proc/{entry}/fd/0") != pty:
                        continue
                    with open(f"/proc/{entry}/comm", encoding="utf-8") as fh:
                        best = fh.read().strip()
                except OSError:
                    continue
        except OSError:
            return None
        return best

    def zoom(self, step: float):
        scale = self.terminal.get_font_scale()
        self.terminal.set_font_scale(max(0.4, min(4.0, scale + step)))

    def zoom_reset(self):
        self.terminal.set_font_scale(1.0)

    def grab_terminal_focus(self):
        self.terminal.grab_focus()
