"""Starting a terminal, minus the terminal.

What to run, the environment to run it in, and the font to draw it in: three
decisions that belong to a terminal but need nothing from VTE to make. They
live apart from terminal.py for one reason, which is that it means they can
be tested on a machine where VTE is not installed — and the answers here are
exactly the kind that are wrong in a way nobody notices for a week. A shell
started without the dash in front of argv[0] is not a login shell and does
not read the profile; a child that inherits this process's COLUMNS lays its
prompt out for the wrong width.
"""

from __future__ import annotations

import os
import re
import shlex

import gi

gi.require_version("Pango", "1.0")

from gi.repository import Pango  # noqa: E402


def monospace_family(wanted: str) -> str:
    """The font family to ask for, given what the settings say.

    An empty setting means the desktop's own monospace font, which is what
    "monospace" resolves to — a family alias rather than a font of that name.
    A family that is not installed is left alone rather than second-guessed:
    Pango falls back on its own, and silently substituting here would make
    the preferences lie about what is being used.
    """
    return wanted.strip() or "monospace"


def font_for(family: str, size: float) -> Pango.FontDescription:
    desc = Pango.FontDescription()
    desc.set_family(monospace_family(family))
    desc.set_size(int(round(max(1.0, size) * Pango.SCALE)))
    return desc


def shell_argv(config) -> list[str]:
    """The command a new terminal runs.

    In order: whatever the settings name, then $SHELL, then the shell in
    /etc/passwd, then /bin/sh — which cannot be absent on a machine running
    this at all. A login shell is spelt by putting a dash in front of argv[0],
    which is a convention from 1979 and still the only way to say it; VTE
    does that itself when the `-l` is there.
    """
    configured = (config["shell"] or "").strip()
    if configured:
        argv = shlex.split(configured)
        if argv:
            return argv
    shell = os.environ.get("SHELL")
    if not shell:
        try:
            import pwd

            shell = pwd.getpwuid(os.getuid()).pw_shell
        except (ImportError, KeyError, OSError):
            shell = None
    shell = shell or "/bin/sh"
    if config["login_shell"]:
        return [shell, "-l"]
    return [shell]


def child_environment() -> list[str]:
    """The environment the shell starts in, as VTE wants it: KEY=VALUE.

    TERM is set to what this terminal can actually do, which is what VTE
    implements. COLORTERM is the twenty-four-bit flag every modern program
    looks for. COLUMNS and LINES are removed rather than passed on: they are
    this process's idea of a terminal size, they are wrong for the child, and
    a shell that trusts them lays out its prompt for the wrong width.
    """
    env = dict(os.environ)
    env.pop("COLUMNS", None)
    env.pop("LINES", None)
    env.pop("WINDOWID", None)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["TERM_PROGRAM"] = "Prism"
    from . import __version__

    env["TERM_PROGRAM_VERSION"] = __version__
    return [f"{key}={value}" for key, value in env.items()]


def paste_runs_itself(text: str) -> bool:
    """True when pasting `text` would set something going on its own.

    A newline in pasted text is not punctuation, it is the Enter key, and
    text that arrives with one in the middle runs before it has been read.
    This is an old trick: a copy button on a web page that quietly puts a
    second line on the clipboard. Control characters count too — an escape
    sequence can move the cursor around and hide what is really there.

    A single trailing newline is allowed through. Copying `make install`
    with the line ending is how people copy a command, bracketed paste
    (which VTE does, and which bash has had on by default since 5.1) stops
    even that from running on its own in a modern shell, and a terminal that
    asked twice a day about a one-line paste would have its guard turned off
    within a week.
    """
    body = text.rstrip("\n")
    if "\n" in body or "\r" in body:
        return True
    return any(char < " " and char != "\t" for char in body)


# What bash and zsh put in the window title when nobody has asked for
# anything else: the user, the host, and the whole path.
_DEFAULT_TITLE = re.compile(r"^[^@\s]+@[^:\s]+\s*:")


def short_place(path: str, home: str | None = None) -> str:
    """A directory as a tab would say it: the last part, or `~`."""
    home = home if home is not None else os.path.expanduser("~")
    if not path:
        return ""
    path = path.rstrip("/") or "/"
    if path == home:
        return "~"
    if path == "/":
        return "/"
    return os.path.basename(path)


def tab_name(window_title: str, cwd: str | None, home: str | None = None) -> str:
    """What to call a tab.

    A terminal gets a title from the program running in it, and for a shell
    sitting at a prompt that title is `robin@Arch:~/Projects/Programming/Apps`
    — the same forty characters in every tab, with the part that differs at
    the end where a tab strip cuts it off. So the shell's own title is
    recognised and thrown away, and the tab says the directory's last
    component instead.

    A title set by anything else is kept as it is: `vim README.md` and `htop`
    are exactly what the tab should say, and they are the reason a terminal
    reads the title at all.
    """
    title = (window_title or "").strip()
    if title and not _DEFAULT_TITLE.match(title):
        return title
    place = short_place(cwd or "", home)
    return place or "Terminal"
