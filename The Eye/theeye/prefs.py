"""The preferences, as an Adwaita dialog.

Everything here writes straight into the live `Config` and calls back into the
window, so a change is visible while the dialog is still open. That matters
more than usual in this app: half of these settings are about how the glass
looks, and choosing a translucency from a slider you cannot see the effect of
is guesswork.

The rows are built from small helpers rather than from a table, because unlike
the editor's sliders — which are all the same shape — a settings page is a
dozen different kinds of control and a table would only be a worse way of
writing the same code.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import housestyle, jobs, metadata, theme as theming, thumbs, widgets  # noqa: E402

QUALITIES = (("high", "Everything, at full strength"),
             ("balanced", "Softer blurs, for a slower machine"),
             ("draft", "Blurs halved, grain and glow dropped"))

FITS = (("window", "Fit the window"), ("width", "Fit the width"),
        ("height", "Fit the height"), ("fill", "Fill the window"),
        ("actual", "Actual size"))

STRIP_SIDES = (("bottom", "Along the bottom"), ("top", "Along the top"),
               ("start", "Down the left"), ("end", "Down the right"))

LOOPS = (("off", "Play once"), ("one", "Repeat this one"),
         ("all", "Repeat the folder"))


class Preferences(Adw.PreferencesDialog):
    __gtype_name__ = "TheEyePreferences"

    def __init__(self, window):
        super().__init__(title="Preferences")
        self.window = window
        self.config = window.config
        self.add(self._look_page())
        self.add(self._viewing_page())
        self.add(self._playing_page())
        self.add(self._editing_page())

    # -- plumbing ---------------------------------------------------------

    def _retheme(self):
        """A theme row changed: work out which theme that means and apply it.

        Through the window rather than straight into `install_css`, because a
        theme here is not only a stylesheet — it is the ambient layer, the
        matte behind the picture and the tint of every painted panel.
        """
        self.window.scheme = theming.get(self.window._theme_key())
        self.window._apply_theme()

    def _changed(self, key, value, theme=False):
        self.config[key] = value
        self.config.save()
        if theme:
            self.window._apply_theme()
        self.window._apply_config()
        self.window._refresh_chrome()
        self.window.queue_draw()

    def _switch(self, group, key, title, subtitle="", theme=False):
        row = Adw.SwitchRow(title=title, subtitle=subtitle,
                            active=bool(self.config[key]))
        row.connect("notify::active",
                    lambda r, _p: self._changed(key, r.get_active(), theme))
        group.add(row)
        return row

    def _choice(self, group, key, title, options, subtitle="", theme=False):
        keys = [option[0] for option in options]
        row = Adw.ComboRow(title=title, subtitle=subtitle,
                           model=Gtk.StringList.new(
                               [option[1] for option in options]))
        if self.config[key] in keys:
            row.set_selected(keys.index(self.config[key]))
        row.connect("notify::selected",
                    lambda r, _p: self._changed(key, keys[r.get_selected()], theme))
        group.add(row)
        return row

    def _slider(self, group, key, title, low, high, step, subtitle="",
                theme=False, digits=2):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                         low, high, step)
        scale.set_size_request(190, -1)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.LEFT)
        scale.set_digits(digits)
        scale.set_value(float(self.config[key]))
        scale.set_valign(Gtk.Align.CENTER)
        scale.connect("value-changed",
                      lambda s: self._changed(key, s.get_value(), theme))
        row.add_suffix(scale)
        group.add(row)
        return row

    def _spin(self, group, key, title, low, high, step, subtitle=""):
        row = Adw.SpinRow(title=title, subtitle=subtitle,
                          adjustment=Gtk.Adjustment.new(
                              float(self.config[key]), low, high, step, step, 0))
        row.connect("notify::value",
                    lambda r, _p: self._changed(key, r.get_value()))
        group.add(row)
        return row

    # -- the pages --------------------------------------------------------

    def _look_page(self):
        page = Adw.PreferencesPage(title="Look", icon_name="applications-graphics-symbolic")

        # The rows themselves come from the house, so the list and its order
        # are the same here as in every other app. What this app adds is the
        # one sentence about the matte, which is the only part of a theme
        # that means something different in a viewer.
        page.add(housestyle.theme_group(
            self.config, self._retheme,
            description=housestyle.THEME_BLURB + " The matte — the surround "
            "the picture sits on — is a neutral grey in every one of them, "
            "because a tinted surround changes the colours you see in the "
            "picture; Studio makes it the 18% grey a print is judged on."))

        glass_group = Adw.PreferencesGroup(
            title="Glass",
            description="The panels are painted rather than borrowed from the "
                        "desktop: there is no backdrop blur to ask a Wayland "
                        "compositor for. What they do blur is the picture "
                        "behind them, which is the part worth blurring.")
        self._switch(glass_group, "glass", "Painted glass",
                     "Off, the panels are flat. Cheapest of all the settings "
                     "here.", theme=True)
        self._switch(glass_group, "backdrop_blur", "Blur the picture behind",
                     "Lets the panels be much thinner, which is what makes "
                     "them look like glass.", theme=True)
        self._slider(glass_group, "translucency", "Translucency", 0.45, 1.0, 0.01,
                     "Never taken literally: a transport bar you cannot read "
                     "is worse than one that is not quite as pretty.",
                     theme=True)
        self._switch(glass_group, "motion", "Drifting light",
                     "Slow colour behind the glass. It stops while a video "
                     "plays, and settles twelve seconds after you stop "
                     "touching anything.", theme=True)
        page.add(glass_group)

        chrome = Adw.PreferencesGroup(title="The controls")
        self._switch(chrome, "chrome_autohide", "Hide the bars when idle")
        self._slider(chrome, "autohide_seconds", "Hide after", 0.5, 10.0, 0.5,
                     "Seconds of no pointer movement.", digits=1)
        self._slider(chrome, "corner_radius", "Rounded corners on the picture",
                     0.0, 32.0, 1.0, digits=0)
        page.add(chrome)

        effects_group = Adw.PreferencesGroup(
            title="Effects",
            description="How much work the editor's effects are allowed to "
                        "cost per frame. It only matters while a video is "
                        "playing with a heavy grade on it.")
        self._choice(effects_group, "effect_quality", "Effect quality", QUALITIES)
        page.add(effects_group)
        return page

    def _viewing_page(self):
        page = Adw.PreferencesPage(title="Viewing", icon_name="view-paged-symbolic")

        fit = Adw.PreferencesGroup(title="Size")
        self._choice(fit, "fit", "Open pictures at", FITS)
        self._slider(fit, "zoom_steps", "Zoom step", 1.05, 3.0, 0.05,
                     "How far one notch of the wheel goes.")
        self._switch(fit, "smooth_scaling", "Smooth scaling",
                     "Off for pixel art, which wants hard edges.")
        self._switch(fit, "background_matte", "The theme's matte behind pictures")
        self._switch(fit, "checkerboard", "Chequers behind transparency",
                     "Rather than the matte, for pictures with holes in them.")
        page.add(fit)

        strip = Adw.PreferencesGroup(title="The strip of thumbnails")
        self._switch(strip, "show_strip", "Show the strip")
        self._choice(strip, "strip_at", "Where", STRIP_SIDES)
        self._spin(strip, "strip_size", "Thumbnail size", 56, 260, 4)
        page.add(strip)

        folder = Adw.PreferencesGroup(
            title="The folder",
            description="What goes into the reel when a folder is opened.")
        self._switch(folder, "recurse", "Include subfolders")
        self._switch(folder, "show_hidden", "Include hidden files")
        self._switch(folder, "wrap_around", "The end goes back to the start")
        page.add(folder)

        slideshow = Adw.PreferencesGroup(title="Slideshow")
        self._slider(slideshow, "slideshow_seconds", "Seconds each", 0.5, 60.0,
                     0.5, "A video plays to its end whatever this says.",
                     digits=1)
        self._switch(slideshow, "slideshow_shuffle", "Shuffled")
        self._switch(slideshow, "slideshow_loop", "Start again at the end")
        page.add(slideshow)

        cache = Adw.PreferencesGroup(
            title="Thumbnails",
            description="Kept under the cache directory, named after the file "
                        "and the time it was last changed — so an edited "
                        "picture gets a new thumbnail and cannot be served a "
                        "stale one.")
        self.cache_row = Adw.ActionRow(title="On disk", subtitle="Counting…")
        clear = Gtk.Button(label="Empty", valign=Gtk.Align.CENTER)
        clear.add_css_class("pill")
        clear.connect("clicked", lambda _b: self._empty_cache())
        self.cache_row.add_suffix(clear)
        cache.add(self.cache_row)
        page.add(cache)
        self._measure_cache()
        return page

    def _playing_page(self):
        page = Adw.PreferencesPage(title="Playing",
                                   icon_name="media-playback-start-symbolic")

        start = Adw.PreferencesGroup(title="Starting")
        self._switch(start, "autoplay", "Play as soon as a video opens")
        self._choice(start, "loop", "At the end", LOOPS)
        page.add(start)

        resume = Adw.PreferencesGroup(
            title="Carrying on",
            description="Where you stopped watching is remembered for up to "
                        "six hundred files. A position in the last few "
                        "seconds is not remembered at all, because stopping "
                        "in the credits means you finished it.")
        self._switch(resume, "resume_playback", "Carry on where I stopped")
        self._slider(resume, "resume_threshold", "Only for files longer than",
                     0.0, 600.0, 10.0, "Seconds.", digits=0)
        self._slider(resume, "resume_tail", "Treat the last", 0.0, 120.0, 5.0,
                     "Seconds as finished.", digits=0)
        page.add(resume)

        seeking = Adw.PreferencesGroup(title="Seeking")
        self._slider(seeking, "seek_short", "The arrow keys", 1.0, 60.0, 1.0,
                     "Seconds.", digits=0)
        self._slider(seeking, "seek_long", "Shift and the arrow keys", 5.0,
                     600.0, 5.0, "Seconds.", digits=0)
        page.add(seeking)

        sound = Adw.PreferencesGroup(title="Sound and subtitles")
        self._slider(sound, "volume", "Volume", 0.0, 1.0, 0.01)
        self._switch(sound, "subtitles", "Subtitles on",
                     "A subtitle file beside the video is loaded "
                     "automatically.")
        self._slider(sound, "subtitle_size", "Subtitle size", 0.4, 3.0, 0.1,
                     digits=1)
        page.add(sound)

        decoding = Adw.PreferencesGroup(
            title="Decoding",
            description="Takes effect on the next file opened: the pipeline "
                        "is built when playback starts and rebuilding it "
                        "mid-film would be a visible glitch.")
        self._switch(decoding, "hardware_decoding", "Use the graphics card",
                     "Turn this off if a video plays with corrupt colours or "
                     "blocks in it.")
        page.add(decoding)
        return page

    def _editing_page(self):
        page = Adw.PreferencesPage(title="Editing", icon_name="document-edit-symbolic")

        grades = Adw.PreferencesGroup(
            title="Grades",
            description="A grade is never written into the picture. It goes "
                        "in a file called .the-eye.grades.json beside it, so "
                        "it travels with the folder; when the folder cannot "
                        "be written to, it goes in The Eye's own state "
                        "instead.")
        self._switch(grades, "remember_grades", "Remember what I adjusted")
        self._switch(grades, "editor_live", "Grade video while it plays",
                     "Off, the grade is only drawn while paused — which on a "
                     "slow machine is the difference between smooth playback "
                     "and not.")
        page.add(grades)

        output = Adw.PreferencesGroup(title="Exporting")
        self._choice(output, "export_format", "Format for stills",
                     (("same", "The same as the original"), ("png", "PNG"),
                      ("jpeg", "JPEG"), ("webp", "WebP")))
        self._spin(output, "export_quality", "Quality", 40, 100, 1,
                   "For JPEG and WebP. PNG and TIFF are lossless whatever "
                   "this says.")
        page.add(output)
        return page

    # -- the cache --------------------------------------------------------

    def _measure_cache(self):
        def work():
            return thumbs.cache_size()

        def done(size):
            self.cache_row.set_subtitle(
                metadata.human_size(size) if size else "Nothing cached yet")

        jobs.run(work, done, lambda _exc: self.cache_row.set_subtitle("Unknown"))

    def _empty_cache(self):
        def work():
            return thumbs.empty_cache()

        def done(count):
            self.window.thumbs.clear()
            self.cache_row.set_subtitle("Nothing cached yet")
            widgets.toast(self.window,
                          f"{count} thumbnail{'s' if count != 1 else ''} removed")

        jobs.run(work, done, lambda exc: widgets.error_toast(self.window, exc))
