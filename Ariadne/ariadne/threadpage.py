"""A thread's page: its title, the questions it answers, and the canvas.

The canvas draws and the page decides: every gesture on the canvas comes
here as a callback, and here the thread is changed, written, and — for
anything taken away — offered back with an Undo.

Thinking with Claude happens here too. What Claude proposes is laid over the
canvas as ghost thoughts and dashed arrows, and stays that way until it is
accepted, all of it, or discarded, all of it; nothing reaches the file before.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import claude, thinking, widgets  # noqa: E402
from .canvas import Canvas  # noqa: E402
from .dialogs import ThoughtEditor  # noqa: E402

SAVE_DELAY_MS = 600


class ThreadPage(Gtk.Box):
    def __init__(self, view):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.view = view
        self.thread = None
        self.command = None               # the suite's stand-in for `claude`
        self.proposal = None
        self._ask = None
        self._focus = None
        self._title_timer = 0
        self._filling = False

        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bar.add_css_class("ariadne-thread-bar")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.list_button = Gtk.Button(icon_name="sidebar-show-symbolic", css_classes=["flat"],
                                      tooltip_text="The list (F9)")
        self.list_button.connect("clicked", lambda _b: view.show_list(True))
        top.append(self.list_button)
        self.title = Gtk.Entry(hexpand=True, css_classes=["ariadne-big-title"],
                               placeholder_text="What the thread argues")
        self.title.connect("changed", lambda _e: self._title_edited())
        top.append(self.title)
        bar.append(top)
        self.answers = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.answers.add_css_class("ariadne-answers")
        bar.append(self.answers)

        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        for icon, tip, handler in (
                ("list-add-symbolic", "Add a thought (Ctrl+Enter)", lambda: self.add_thought()),
                ("view-grid-symbolic", "Arrange (Ctrl+L)", lambda: self.arrange()),
                ("zoom-out-symbolic", "Zoom out (Ctrl+−)", lambda: self.canvas.zoom(1 / 1.2)),
                ("zoom-in-symbolic", "Zoom in (Ctrl++)", lambda: self.canvas.zoom(1.2)),
                ("zoom-fit-best-symbolic", "Everything in view (Ctrl+0)",
                 lambda: self.canvas.fit())):
            button = Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=["flat"])
            button.connect("clicked", lambda _b, fn=handler: fn())
            tools.append(button)
        tools.append(Gtk.Box(hexpand=True))
        if claude.available():
            think = Gtk.Button(label="Think with Claude", css_classes=["flat"],
                               tooltip_text="About the selected thought, or the whole "
                                            "thread (Ctrl+Shift+A)")
            think.connect("clicked", lambda _b: self.think())
            tools.append(think)
        bar.append(tools)
        self.append(bar)

        self.canvas = Canvas(on_moved=self._moved, on_connect=self._connect,
                             on_edit=self.edit_thought, on_add=self._add_at,
                             on_edge=self._edge_clicked, on_link=view.follow_link,
                             on_delete=self._delete)
        self.append(self.canvas)

        self.ghost_bar = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        ghost = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ghost.add_css_class("ariadne-ghost-bar")
        self.ghost_label = Gtk.Label(xalign=0, hexpand=True, wrap=True,
                                     wrap_mode=Pango.WrapMode.WORD_CHAR)
        ghost.append(self.ghost_label)
        self.stop_button = Gtk.Button(label="Stop")
        self.stop_button.connect("clicked", lambda _b: self.stop())
        ghost.append(self.stop_button)
        self.discard_button = Gtk.Button(label="Discard")
        self.discard_button.connect("clicked", lambda _b: self.discard())
        ghost.append(self.discard_button)
        self.accept_button = Gtk.Button(label="Accept", css_classes=["suggested-action"])
        self.accept_button.connect("clicked", lambda _b: self.accept())
        ghost.append(self.accept_button)
        self.ghost_bar.set_child(ghost)
        self.append(self.ghost_bar)

    # -- showing -------------------------------------------------------------

    def show(self, thread, keep_view=False):
        self.flush()
        if thread is not self.thread:
            self.stop()
            self.discard()
        self.thread = thread
        self._filling = True
        try:
            self.title.set_text(thread.title)
        finally:
            self._filling = False
        self.fill_answers()
        self.canvas.set_thread(thread, keep_view=keep_view)
        self.title.select_region(0, 0)
        self.canvas.grab_focus()

    def fill_answers(self):
        widgets.clear(self.answers)
        if self.thread is None:
            return
        questions = self.view.library.questions_of(self.thread)
        if not self.thread.answers:
            self.answers.append(Gtk.Label(label="Answers no question — just thinking.",
                                          xalign=0))
            return
        self.answers.append(Gtk.Label(label="Answers:", xalign=0))
        for title in self.thread.answers:
            found = next((q for q in questions if q.title == title), None)
            if found is None:
                self.answers.append(Gtk.Label(label=f"{title} (not kept here)",
                                              ellipsize=Pango.EllipsizeMode.END))
                continue
            link = Gtk.LinkButton(label=title, uri="about:blank")
            link.get_child().set_ellipsize(Pango.EllipsizeMode.END)
            link.connect("activate-link", lambda _b, q=found: (self.view.open_item(q), True)[1])
            self.answers.append(link)

    # -- saving --------------------------------------------------------------

    def save(self):
        """The thread to its file, with every card where it is now."""
        if self.thread is None:
            return
        if not self.canvas.auto:
            self.thread.layout = self.canvas.places()
        self.view.save(self.thread)

    def _title_edited(self):
        if self._filling or self.thread is None:
            return
        if self._title_timer:
            GLib.source_remove(self._title_timer)
        self._title_timer = GLib.timeout_add(SAVE_DELAY_MS, self._title_tick)

    def _title_tick(self):
        self._title_timer = 0
        self.flush()
        return GLib.SOURCE_REMOVE

    def flush(self):
        if self._title_timer:
            GLib.source_remove(self._title_timer)
            self._title_timer = 0
        if self.thread is None:
            return
        title = " ".join(self.title.get_text().split())
        if title and title != self.thread.title:
            self.thread.title = title
            self.save()
            self.view.refresh_list()

    # -- the canvas's hands --------------------------------------------------

    def _moved(self):
        self.save()

    def _connect(self, source, target):
        if self.thread.connect(source, target) is not None:
            # Where everything is now is kept: arranged afresh with the new
            # arrow, the cards would all move under the hand that drew it.
            self.canvas.auto = False
            self.save()
            self.canvas.refresh()

    def _add_at(self, x, y):
        self.add_thought((x, y))

    def add_thought(self, where=None):
        if self.thread is None:
            return
        node = self.thread.add("")
        self.canvas.refresh()
        x, y = where or self.canvas.centre_world()
        self.canvas.place(node.id, x, y)
        self.canvas.auto = False
        self.edit_thought(node.id, fresh=True)

    def edit_thought(self, node_id, fresh=False):
        node = self.thread.node(node_id) if self.thread else None
        if node is None:
            return

        def done(key, title, text):
            target = self.thread.node(key)
            if target is None:
                return
            if fresh and not title.strip() and not text.strip():
                self.thread.remove(key)          # an empty new thought is no thought
                self.canvas.refresh()
                return
            target.title, target.text = title or target.title or "Untitled", text
            self.save()
            self.canvas.refresh()

        ThoughtEditor(node, done, on_delete=self._delete_node).present(self.view)

    def _edge_clicked(self, source, edge):
        target = self.thread.node(edge.target)
        heading = f"From “{self.thread.node(source).title}”"
        body = (f"to “{target.title}”" if target else f"to {edge.target}, which is not here")

        def save(label):
            edge.label = label
            self.save()
            self.canvas.refresh()

        widgets.ask_text(self.view, heading, body + ". A word on the arrow — therefore, "
                         "because, but, unless — or nothing.", edge.label, "Save", save,
                         placeholder="therefore",
                         extra=("remove", "Remove Arrow",
                                lambda _text: self._delete(("edge", (source, edge)))))

    def _delete(self, selection):
        kind, what = selection
        if kind == "node":
            self._delete_node(what)
            return
        source, edge = what
        node = self.thread.node(source)
        if node is None or edge not in node.edges:
            return
        index = node.edges.index(edge)
        node.edges.remove(edge)
        self.save()
        self.canvas.selection = None
        self.canvas.refresh()

        def undo():
            node.edges.insert(index, edge)
            self.save()
            self.canvas.refresh()

        self.view.toast("Arrow removed", undo=undo)

    def _delete_node(self, node_id):
        if self.thread.node(node_id) is None:
            return
        index = [n.id for n in self.thread.nodes].index(node_id)
        where = self.canvas.places().get(node_id)
        node, incoming = self.thread.remove(node_id)
        self.save()
        self.canvas.selection = None
        self.canvas.refresh()
        thread = self.thread

        def undo():
            thread.restore(node, incoming, index)
            if thread is self.thread:
                self.canvas.refresh()
                if where:
                    self.canvas.place(node.id, *where)
                self.save()
            else:
                self.view.save(thread)

        self.view.toast(f"“{node.title or node.id}” taken away", undo=undo)

    def arrange(self):
        self.canvas.arrange()
        self.save()

    # -- Claude --------------------------------------------------------------

    def think(self, question=None):
        """Ask Claude about the selected thought, or the question, or the thread."""
        if self.thread is None:
            return
        selection = self.canvas.selection
        node = self.thread.node(selection[1]) if selection and selection[0] == "node" else None
        if node is not None:
            prompt = thinking.prompt_for_node(self.thread, node)
        else:
            if question is None:
                found = self.view.library.questions_of(self.thread)
                question = found[0] if found else None
            prompt = (thinking.prompt_for_question(self.thread, question.title, question.text)
                      if question else thinking.prompt_for_thread(self.thread))
        self.stop()
        self.discard()
        self._focus = node.id if node else None
        files = [self.thread.path] if self.thread.path else []
        ask = claude.Ask(prompt, files=files, command=self.command)
        thread = self.thread
        ask.on_done = lambda ok, result, session: GLib.idle_add(
            self._answered, ask, thread, ok, result)
        self._ask = ask
        self._say("Claude is thinking…" + (f" about “{node.title}”" if node else ""),
                  running=True)
        ask.start()

    def _answered(self, ask, thread, ok, result):
        if ask is not self._ask:
            return GLib.SOURCE_REMOVE
        self._ask = None
        if thread is not self.thread:
            self.ghost_bar.set_reveal_child(False)
            return GLib.SOURCE_REMOVE
        if not ok:
            self._say("Stopped." if result == "cancelled" else f"Claude could not answer: {result}",
                      done=True)
            return GLib.SOURCE_REMOVE
        self.proposal = thinking.read(result, thread)
        if not self.proposal:
            self._say("Claude proposed nothing the thread does not have.", done=True)
            return GLib.SOURCE_REMOVE
        self.canvas.set_ghosts(self.proposal, near=self._focus)
        thoughts, arrows = len(self.proposal.nodes), len(self.proposal.edges)
        self._say(f"Claude proposes {thoughts} {'thought' if thoughts == 1 else 'thoughts'} "
                  f"and {arrows} {'arrow' if arrows == 1 else 'arrows'}. Drag them where "
                  f"you want them, then keep them or not.", proposal=True)
        return GLib.SOURCE_REMOVE

    def _say(self, text, running=False, proposal=False, done=False):
        self.ghost_label.set_text(text)
        self.stop_button.set_visible(running)
        self.accept_button.set_visible(proposal)
        self.discard_button.set_visible(proposal or done)
        self.discard_button.set_label("Discard" if proposal else "Close")
        self.ghost_bar.set_reveal_child(True)

    def stop(self):
        if self._ask is not None:
            self._ask.cancel()
            self._ask = None
        self.ghost_bar.set_reveal_child(False)

    def accept(self):
        if not self.proposal or self.thread is None:
            return
        # Every card where it stands now, the ghosts where they were dragged.
        self.thread.layout = self.canvas.places()
        thinking.accept(self.thread, self.proposal, self.canvas.ghost_places())
        self.proposal = None
        self.canvas.clear_ghosts()
        self.canvas.set_thread(self.thread, keep_view=True)
        self.view.save(self.thread)
        self.ghost_bar.set_reveal_child(False)
        self.view.refresh_list()

    def discard(self):
        self.proposal = None
        self.canvas.clear_ghosts()
        self.ghost_bar.set_reveal_child(False)
