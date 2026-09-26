"""Overleaf's own editor, in a LaRenderer tab.

Real-time collaboration — everyone's typing and cursors live — is Overleaf's
editor talking to Overleaf's servers over a private protocol. LaRenderer does
not reimplement that. A copy of the protocol would be unofficial, would have
to sign in with a browser cookie, and would break whenever Overleaf changed
it; and a mistake in it would not fail quietly, it would corrupt a document
other people are writing in. So when live editing is what is wanted, the tab
shows Overleaf itself, in WebKit, and Overleaf does what Overleaf does.

The git bridge (window.py, "Open an Overleaf project…") stays for working on
a copy here with LaRenderer's own editor and preview; closing a live tab
pulls what was done in it into that copy.

WebKit is only loaded when the first such tab is opened: it is a large
library and a web process of a few hundred megabytes, which nobody should pay
for just by starting the editor. Closing the tab ends its web process.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from . import widgets, winenv  # noqa: E402

OVERLEAF = "https://www.overleaf.com"
DASHBOARD = OVERLEAF + "/project"

# WebKitGTK 2.52 aborts the web process from inside its accessibility code
# when a selection is extended past the last rendered line of an editor that
# recycles its line elements — which, in Overleaf, is Ctrl+A in the LaTeX
# pane, every time. Pointing the web process at an accessibility bus that is
# not there stops it building the tree the crash walks. Found and verified in
# Lyndon Browser (src/main.c). The cost is that a screen reader sees nothing
# of the page; LARENDERER_ACCESSIBILITY=1 keeps the tree and takes the crash.
if not os.environ.get("LARENDERER_ACCESSIBILITY"):
    os.environ.setdefault("WEBKIT_A11Y_BUS_ADDRESS", "unix:path=/nonexistent")

_webkit = None
_session = None
_context = None
_open_views = 0


def available() -> bool:
    """Whether WebKitGTK for GTK 4 is installed — the tab needs it."""
    return _load() is not None


def _load():
    global _webkit
    if _webkit is None:
        try:
            gi.require_version("WebKit", "6.0")
            from gi.repository import WebKit  # noqa: PLC0415 - loaded on first use only
        except (ImportError, ValueError):
            _webkit = False
        else:
            _webkit = WebKit
    return _webkit or None


def _network_session():
    """One signed-in session for every Overleaf tab, kept between runs.

    Its own directory, not Lyndon's: two processes sharing one WebKit data
    directory corrupt each other's databases. So LaRenderer is signed in to
    Overleaf once, on its own.
    """
    global _session
    if _session is None:
        WebKit = _load()
        data = os.path.join(winenv.data_home("larenderer"), "overleaf-web")
        cache = os.path.join(winenv.cache_home("larenderer"), "overleaf-web")
        os.makedirs(data, exist_ok=True)
        _session = WebKit.NetworkSession.new(data, cache)
        _session.get_cookie_manager().set_persistent_storage(
            os.path.join(data, "cookies.sqlite"), WebKit.CookiePersistentStorage.SQLITE)
        _session.connect("download-started", _on_download)
    return _session


def _web_context():
    """A WebKit context of Overleaf's own, with WebKit's page caches off.

    WebKit keeps the web process of a page it navigated away from, in case
    Back is pressed — for Overleaf, the project list's, left behind by the
    sign-in redirect. That is 160 MB still resident after the tab has closed,
    on a machine with 3.4 GB. The document-viewer cache model keeps nothing
    of the kind; an editor that lives on one page loses nothing by it.
    """
    global _context
    if _context is None:
        WebKit = _load()
        _context = WebKit.WebContext.new()
        _context.set_cache_model(WebKit.CacheModel.DOCUMENT_VIEWER)
    return _context


def _let_go():
    """The last Overleaf tab closed: drop WebKit's context and session too.

    With nothing holding them, WebKit ends every process they started — the
    network process as well as any web process it was still keeping.
    """
    global _context, _session
    _context = None
    _session = None


def is_overleaf(uri: str | None, base: str = OVERLEAF) -> bool:
    """True for pages of this Overleaf, the one the tab was opened on."""
    if not uri:
        return False
    host = (urlparse(uri).hostname or "").lower()
    home = (urlparse(base).hostname or "").lower()
    if not host or not home:
        return False
    if home.endswith("overleaf.com"):
        return host == "overleaf.com" or host.endswith(".overleaf.com")
    return host == home


# ------------------------------------------------------------------ downloads --


def _downloads_dir() -> str:
    folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
    return folder or os.path.expanduser("~")


def _unique(folder: str, name: str) -> str:
    stem, ext = os.path.splitext(os.path.basename(name) or "download")
    path = os.path.join(folder, stem + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem} ({n}){ext}")
        n += 1
    return path


def _on_download(_session, download):
    """Overleaf's "Download PDF" and source zip land in Downloads, and say so."""
    view = download.get_web_view()

    def destination(dl, suggested):
        dl.set_destination(_unique(_downloads_dir(), suggested or "download"))
        return True

    def finished(dl):
        path = dl.get_destination()
        if view is not None and path:
            widgets.toast(view, f"Saved {os.path.basename(path)} in Downloads")

    def failed(_dl, error):
        if view is not None:
            widgets.toast(view, f"The download failed: {error.message}", timeout=6)

    download.connect("decide-destination", destination)
    download.connect("finished", finished)
    download.connect("failed", failed)


# ----------------------------------------------------------------------- tab --


class OverleafView(Gtk.Box):
    """Overleaf in a tab: a small bar of text buttons above the page.

    `on_title(text)` names the tab after the project. `on_new_tab(uri)` is
    asked to open an Overleaf page that wants a window of its own; anything
    that is not Overleaf goes to `open_external(uri)`, the desktop's browser.
    """

    def __init__(self, uri: str, on_title=None, on_new_tab=None, open_external=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        WebKit = _load()
        if WebKit is None:
            raise RuntimeError("WebKitGTK 6.0 is not installed.")
        self.on_title = on_title
        self.on_new_tab = on_new_tab
        self.open_external = open_external
        self.base = f"{urlparse(uri).scheme}://{urlparse(uri).netloc}" if "://" in uri else OVERLEAF

        global _open_views
        self.web = WebKit.WebView(web_context=_web_context(), network_session=_network_session(),
                                  vexpand=True, hexpand=True)
        _open_views += 1
        self._closed = False
        settings = self.web.get_settings()
        settings.set_enable_developer_extras(False)
        settings.set_javascript_can_open_windows_automatically(False)
        self.web.connect("notify::title", lambda *_: self._title_changed())
        self.web.connect("notify::uri", lambda *_: self._uri_changed())
        self.web.connect("notify::estimated-load-progress", lambda *_: self._progress())
        self.web.connect("create", self._on_create)
        self.web.connect("decide-policy", self._on_policy)
        self.web.connect("permission-request", lambda _v, request: (request.deny(), True)[1])
        self.web.connect("web-process-terminated", self._on_crash)

        self.append(self._build_bar())
        self.progress = Gtk.ProgressBar(css_classes=["osd"], visible=False)
        self.append(self.progress)

        self.crash = Adw.StatusPage(
            title="The Overleaf page stopped",
            description="Its web process ended. Your work is on Overleaf's servers — "
                        "reloading brings the editor back where it was.",
            vexpand=True, visible=False,
        )
        again = Gtk.Button(label="Reload", halign=Gtk.Align.CENTER,
                           css_classes=["suggested-action", "pill"])
        again.connect("clicked", lambda _b: self.reload())
        self.crash.set_child(again)
        self.append(self.crash)
        self.append(self.web)

        self.web.load_uri(uri)

    # -- the bar ---------------------------------------------------------------

    def _build_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.add_css_class("overleaf-bar")

        self.back = widgets.small_button("Back", tooltip="The previous page")
        self.back.connect("clicked", lambda _b: self.web.go_back())
        self.back.set_sensitive(False)
        bar.append(self.back)

        projects = widgets.small_button("Projects", tooltip="Your Overleaf projects")
        projects.connect("clicked", lambda _b: self.web.load_uri(self.base + "/project"))
        bar.append(projects)

        reload = widgets.small_button("Reload", tooltip="Load the page again")
        reload.connect("clicked", lambda _b: self.reload())
        bar.append(reload)

        self.where = Gtk.Label(xalign=0.5, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE,
                               css_classes=["dim-label", "caption"])
        bar.append(self.where)

        browser = widgets.small_button("Open in browser",
                                       tooltip="This page in your usual web browser")
        browser.connect("clicked", lambda _b: self._external(self.web.get_uri()))
        bar.append(browser)
        return bar

    # -- state ------------------------------------------------------------------

    @property
    def uri(self) -> str:
        return self.web.get_uri() or ""

    def project_id(self) -> str | None:
        """The project open in the tab, when it is a project page."""
        return project_id_of(self.uri)

    def title(self) -> str:
        """The project's name, without Overleaf's " - Overleaf, Online LaTeX Editor"."""
        raw = (self.web.get_title() or "").strip()
        for tail in (" - Overleaf, Online LaTeX Editor", " - Overleaf"):
            if raw.endswith(tail):
                raw = raw[: -len(tail)]
        return raw or "Overleaf"

    def reload(self):
        self.crash.set_visible(False)
        self.web.set_visible(True)
        self.web.reload()

    def close(self):
        """End the web process now, rather than whenever the widget is freed."""
        global _open_views
        if self._closed:
            return
        self._closed = True
        try:
            self.web.terminate_web_process()
        except Exception:  # noqa: BLE001 - already gone is fine
            pass
        _open_views -= 1
        if _open_views <= 0:
            _open_views = 0
            _let_go()

    def _title_changed(self):
        if self.on_title:
            self.on_title(self.title())

    def _uri_changed(self):
        uri = self.uri
        parsed = urlparse(uri)
        self.where.set_label(parsed.netloc + parsed.path if uri else "")
        self.back.set_sensitive(self.web.can_go_back())

    def _progress(self):
        fraction = self.web.get_estimated_load_progress()
        self.progress.set_fraction(fraction)
        self.progress.set_visible(0 < fraction < 1)

    def _on_crash(self, _view, _reason):
        self.web.set_visible(False)
        self.crash.set_visible(True)

    # -- where links go -------------------------------------------------------------

    def _external(self, uri):
        if uri and self.open_external:
            self.open_external(uri)

    def _on_create(self, _view, action):
        """A page that asks for a new window: Overleaf's in a tab, the rest outside."""
        uri = action.get_request().get_uri()
        if is_overleaf(uri, self.base):
            if self.on_new_tab:
                self.on_new_tab(uri)
        else:
            self._external(uri)
        return None

    def _on_policy(self, _view, decision, kind):
        WebKit = _load()
        if kind != WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        # Top-level navigation stays in the tab whatever the host: signing in
        # goes through Google, ORCID or a university's own login page and has
        # to come back here with the session. Only a link the user opened in
        # a new window (handled by _on_create) leaves.
        action = decision.get_navigation_action()
        uri = action.get_request().get_uri()
        if uri and uri.startswith(("mailto:", "tel:")):
            decision.ignore()
            self._external(uri)
            return True
        return False


def project_id_of(uri: str | None) -> str | None:
    """The project in an editor address (…/project/<24 hex>), if there is one."""
    if not uri:
        return None
    parts = urlparse(uri).path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "project" and len(parts[1]) == 24:
        return parts[1]
    return None


def web_url_for_remote(remote: str | None) -> str | None:
    """The editor's address for a project cloned from Overleaf's git bridge."""
    if not remote:
        return None
    parsed = urlparse(remote)
    parts = parsed.path.strip("/").split("/")
    project_id = parts[-1] if parts and len(parts[-1]) == 24 else None
    if not project_id:
        return None
    host = parsed.hostname or ""
    if host.endswith("overleaf.com"):
        return f"{OVERLEAF}/project/{project_id}"
    # A self-hosted Overleaf: …/git/<id> for git, …/project/<id> for the editor.
    return f"{parsed.scheme}://{parsed.netloc}/project/{project_id}"


def launch_external(parent, uri):
    """Open `uri` in the desktop's browser."""
    launcher = Gtk.UriLauncher.new(uri)

    def done(source, result, *_):
        try:
            source.launch_finish(result)
        except GLib.Error as exc:
            widgets.error_toast(parent, exc)

    launcher.launch(parent.get_root() if parent else None, None, done)

