"""Every key the app answers to, in one table.

One table, for three reasons. The accelerators are registered from it, so a
key cannot be bound to an action that does not exist. The help window is built
from it, so the list cannot fall behind the code. And it is the only place to
look when deciding what a new key should be, which is how a keyboard layout
stays coherent rather than becoming a pile of whatever was free at the time.

The layout follows the conventions of the things it is replacing, because
muscle memory is the whole point of a keyboard shortcut. Space, the arrow keys
and F for fullscreen are what every video player uses; the bracket keys for
speed are VLC's; `+`, `-`, `1` and `0` for zoom are what every image viewer
uses. Where the two traditions collide — the arrow keys, which mean "seek" in
a player and "next picture" in a viewer — the key does whichever the thing on
screen calls for, and the help window says so.
"""

from __future__ import annotations

# (action, keys, label, group, applies_to)
#
# `applies_to` is "all", "photo", "video" or "edit" and only affects what the
# help window says; the actions themselves decide what to do with a key that
# does not apply, which is usually nothing.
SHORTCUTS: list[tuple[str, tuple, str, str, str]] = [
    # Moving through the folder
    ("win.next", ("<Page_Down>", "n"), "Next", "Moving", "all"),
    ("win.previous", ("<Page_Up>", "p"), "Previous", "Moving", "all"),
    ("win.first", ("<Home>",), "First", "Moving", "all"),
    ("win.last", ("<End>",), "Last", "Moving", "all"),
    ("", ("Right", "Left"), "Next and previous picture, or seek in a video",
     "Moving", "all"),
    ("win.open", ("<Control>o",), "Open a file", "Moving", "all"),
    ("win.open-folder", ("<Control><Shift>o",), "Open a folder", "Moving", "all"),
    ("win.reload", ("<Control>r", "F5"), "Load it again from disk", "Moving", "all"),
    ("win.mark", ("<Control>m",), "Mark this one, to do something to several",
     "Moving", "all"),
    ("win.clear-marks", ("<Control><Shift>m",), "Clear the marks", "Moving", "all"),

    # Playing
    ("win.play-pause", ("space", "k"), "Play or pause", "Playing", "video"),
    ("win.seek-back", ("Left", "j"), "Back a few seconds", "Playing", "video"),
    ("win.seek-forward", ("Right", "l"), "On a few seconds", "Playing", "video"),
    ("win.seek-back-long", ("<Shift>Left",), "Back a minute", "Playing", "video"),
    ("win.seek-forward-long", ("<Shift>Right",), "On a minute", "Playing", "video"),
    ("win.frame-back", ("comma",), "Back one frame", "Playing", "video"),
    ("win.frame-forward", ("period",), "On one frame", "Playing", "video"),
    ("win.slower", ("bracketleft",), "Slower", "Playing", "video"),
    ("win.faster", ("bracketright",), "Faster", "Playing", "video"),
    ("win.normal-speed", ("<Control>bracketright",), "Normal speed",
     "Playing", "video"),
    ("win.volume-up", ("<Control>Up",), "Louder", "Playing", "video"),
    ("win.volume-down", ("<Control>Down",), "Quieter", "Playing", "video"),
    ("win.mute", ("m",), "Mute", "Playing", "video"),
    ("win.loop", ("r",), "Repeat", "Playing", "all"),
    ("win.loop-start", ("<Shift>bracketleft",), "Start a repeating stretch here",
     "Playing", "video"),
    ("win.loop-end", ("<Shift>bracketright",), "End it here", "Playing", "video"),
    ("win.loop-clear", ("<Shift>backslash",), "Forget the stretch",
     "Playing", "video"),
    ("win.subtitles", ("s",), "Subtitles on or off", "Playing", "video"),
    ("win.next-subtitle", ("<Shift>s",), "Next subtitle track", "Playing", "video"),
    ("win.next-audio", ("a",), "Next audio track", "Playing", "video"),
    ("win.subtitle-earlier", ("g",), "Subtitles earlier", "Playing", "video"),
    ("win.subtitle-later", ("<Shift>g",), "Subtitles later", "Playing", "video"),
    ("win.audio-earlier", ("<Control>g",), "Sound earlier", "Playing", "video"),
    ("win.audio-later", ("<Control><Shift>g",), "Sound later", "Playing", "video"),

    # Looking
    ("win.zoom-in", ("plus", "equal", "<Control>plus", "KP_Add"), "Zoom in",
     "Looking", "photo"),
    ("win.zoom-out", ("minus", "<Control>minus", "KP_Subtract"), "Zoom out",
     "Looking", "photo"),
    ("win.zoom-actual", ("1", "<Control>1"), "Actual size", "Looking", "photo"),
    ("win.zoom-fit", ("0", "<Control>0"), "Fit the window", "Looking", "photo"),
    ("win.zoom-fill", ("9",), "Fill the window", "Looking", "photo"),
    ("win.fullscreen", ("f", "F11"), "Fullscreen", "Looking", "all"),
    ("win.strip", ("t",), "The strip of thumbnails", "Looking", "all"),
    ("win.info", ("i", "<Control>i"), "Details about this file", "Looking", "all"),
    ("win.slideshow", ("<Control>s",), "Slideshow", "Looking", "all"),
    ("win.sort", ("<Control>t",), "How to sort", "Looking", "all"),

    # Editing
    ("win.editor", ("e",), "The editor", "Editing", "all"),
    ("win.compare", ("backslash",), "Before and after", "Editing", "edit"),
    # Shift+R and Shift+L rather than the bracket keys: the brackets are the
    # playback speed, which is VLC's layout and worth more than a second
    # binding for rotation.
    ("win.rotate-right", ("<Shift>r",), "Rotate right", "Editing", "all"),
    ("win.rotate-left", ("<Shift>l",), "Rotate left", "Editing", "all"),
    ("win.flip-h", ("h",), "Flip across", "Editing", "all"),
    ("win.flip-v", ("v",), "Flip over", "Editing", "all"),
    ("win.reset-grade", ("<Control><Shift>z",), "Undo every adjustment",
     "Editing", "edit"),
    ("win.copy-grade", ("<Control>c",), "Copy this grade", "Editing", "edit"),
    ("win.paste-grade", ("<Control>v",), "Paste a grade", "Editing", "edit"),
    ("win.export", ("<Control>e",), "Export a copy", "Editing", "all"),
    ("win.snapshot", ("<Control><Shift>s",), "Save this frame", "Editing", "video"),

    # The file
    ("win.copy-image", ("<Control><Shift>c",), "Copy the picture",
     "This file", "all"),
    ("win.copy-path", ("<Control><Shift>p",), "Copy the file's path",
     "This file", "all"),
    ("win.show-in-files", ("<Control>Return",), "Show it in the file manager",
     "This file", "all"),
    ("win.trash", ("Delete",), "Move it to the wastebasket", "This file", "all"),
    ("win.set-wallpaper", (), "Use it as the desktop background",
     "This file", "photo"),

    # Into the brain. Ctrl+Shift+I is Notedown's key for the inbox too
    # (SYSTEM.md §4.1), which is where a page goes unless it is told otherwise.
    ("win.import-pages", ("<Control><Shift>i",), "Import notebook pages",
     "Into the brain", "photo"),
    ("win.palace-locus", (), "Use as a palace locus", "Into the brain", "photo"),
    ("win.ask-claude", ("<Control><Shift>a",), "Ask Claude about this picture",
     "Into the brain", "all"),

    # The window
    ("win.palette", ("<Control>k",), "Every command, by name", "Window", "all"),
    ("win.theme-next", ("<Control>comma",), "The next theme", "Window", "all"),
    ("win.preferences", ("<Control>p",), "Preferences", "Window", "all"),
    ("win.shortcuts", ("<Control>question", "question", "F1"),
     "This list", "Window", "all"),
    ("app.new-window", ("<Control>n",), "Another window", "Window", "all"),
    ("win.close", ("<Control>w", "q", "Escape"), "Close", "Window", "all"),
    ("app.quit", ("<Control>q",), "Quit", "Window", "all"),
]

GROUPS = ("Moving", "Playing", "Looking", "Editing", "This file",
          "Into the brain", "Window")


def accelerators() -> list[tuple[str, tuple]]:
    """(action, keys) for everything that has both. For the application."""
    return [(action, keys) for action, keys, _label, _group, _to in SHORTCUTS
            if action and keys]


def actions() -> list[str]:
    """Every action named here, so the window can be checked against it."""
    return sorted({action for action, _k, _l, _g, _t in SHORTCUTS if action})


def in_group(group: str) -> list[tuple]:
    return [row for row in SHORTCUTS if row[3] == group]


def pretty(key: str) -> str:
    """A GTK accelerator as something to print.

    Done by hand rather than through `Gtk.accelerator_get_label`, because that
    returns the platform's own names — "Mod1" and the like — and because the
    help window wants `Ctrl` rather than `Control` and a real arrow rather than
    the word.
    """
    names = {
        "space": "Space", "Left": "←", "Right": "→", "Up": "↑", "Down": "↓",
        "Page_Up": "Page Up", "Page_Down": "Page Down", "Home": "Home",
        "End": "End", "Delete": "Delete", "Return": "Enter",
        "Escape": "Esc", "plus": "+", "minus": "−", "equal": "=",
        "comma": ",", "period": ".", "bracketleft": "[", "bracketright": "]",
        "backslash": "\\", "question": "?", "KP_Add": "+", "KP_Subtract": "−",
    }
    out = []
    rest = key
    for token, label in (("<Control>", "Ctrl"), ("<Shift>", "Shift"),
                         ("<Alt>", "Alt"), ("<Super>", "Super")):
        if token in rest:
            out.append(label)
            rest = rest.replace(token, "")
    rest = rest.strip("<>")
    out.append(names.get(rest, rest.upper() if len(rest) == 1 else rest))
    return "+".join(out)


def describe(keys) -> str:
    """Several accelerators for one action, as "Space, or K"."""
    shown = [pretty(key) for key in keys]
    if len(shown) <= 1:
        return shown[0] if shown else ""
    return shown[0] + "   or " + "   or ".join(shown[1:])
