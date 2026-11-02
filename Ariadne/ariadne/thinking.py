"""Thinking with Claude: what is asked, and how the answer becomes ghost thoughts.

Claude is asked to answer in the thread's own syntax (SYSTEM.md §10) — new
thoughts as `## n1 · title` with their text, arrows as `-> id : label` — so
the answer is read by the same parser as the file, and a proposal is a
small thread laid over the real one. Its thoughts are ghosts until accepted:
drawn on the canvas, never written to the file. Accepting gives each ghost a
real id and appends it; discarding leaves the file as it was.

No GTK here; the suite checks it without a display.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .threads import Edge, Node, Thread

_FENCED = re.compile(r"^\s*(`{3,}|~{3,})[^\n]*\n(.*?)^\s*\1\s*$", re.M | re.S)

RULES = (
    "Answer only in the thread file's own syntax (SYSTEM.md §10): each new "
    "thought as a heading `## n1 · Its title` (ids n1, n2, n3 …) with its text "
    "on the lines under it, and each arrow as a line `-> <id> : <word>` inside "
    "the thought it leaves (the word — therefore, because, but, unless — is "
    "optional). An arrow from a thought that is already there goes under a "
    "bare heading with that thought's id and title, and no text. Refer to the "
    "thoughts that are already there by their ids. Write in the language the "
    "thread is written in. No commentary, no code fences, and do not edit any file."
)


def describe(thread: Thread) -> str:
    """The thread as Claude should see it: every thought and arrow, by id."""
    lines = []
    for node in thread.nodes:
        lines.append(f"## {node.id} · {node.title}")
        if node.text:
            lines.append(node.text)
        lines += [edge.line() for edge in node.edges]
        lines.append("")
    return "\n".join(lines).strip() or "(no thoughts yet)"


def prompt_for_node(thread: Thread, node: Node) -> str:
    return (f"This is a logical thread, \"{thread.title}\" — a directed graph of "
            f"thoughts:\n\n{describe(thread)}\n\n"
            f"Think further from the thought `{node.id} · {node.title}`: propose up "
            f"to five new thoughts that follow from it, object to it, or are "
            f"needed for it, and the arrows that join them to it and to the "
            f"other thoughts. {RULES}")


def prompt_for_question(thread: Thread, question_title: str, question_text: str = "") -> str:
    asked = f"\"{question_title}\"" + (f"\n\n{question_text}" if question_text else "")
    return (f"This question is kept in Robin's second brain: {asked}\n\n"
            f"The logical thread \"{thread.title}\" is where it is being worked "
            f"out. Its thoughts so far:\n\n{describe(thread)}\n\n"
            f"Propose up to six thoughts that would carry it towards an answer — "
            f"premises, objections, distinctions, a conclusion — and the arrows "
            f"between them and the thoughts already there. {RULES}")


def prompt_for_thread(thread: Thread) -> str:
    return (f"This is a logical thread, \"{thread.title}\" — a directed graph of "
            f"thoughts, answering no particular question:\n\n{describe(thread)}\n\n"
            f"Propose up to five thoughts that would carry it further — what it "
            f"assumes without saying, what speaks against it, where it leads — "
            f"and the arrows between them and the thoughts already there. {RULES}")


@dataclass
class Proposal:
    """Ghost thoughts and ghost arrows, not yet part of the thread."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[tuple[str, Edge]] = field(default_factory=list)   # (from id, arrow)

    def __bool__(self):
        return bool(self.nodes or self.edges)

    def ghost_ids(self) -> set[str]:
        return {node.id for node in self.nodes}


def read(reply: str, thread: Thread) -> Proposal:
    """What in Claude's reply is a thought or an arrow the thread does not have yet."""
    fenced = _FENCED.findall(reply or "")
    body = "\n\n".join(inner for _, inner in fenced) if fenced else (reply or "")
    parsed = Thread.parse(body)
    existing = {node.id for node in thread.nodes}
    proposal = Proposal()
    for node in parsed.nodes:
        if node.id in existing:
            continue
        proposal.nodes.append(Node(node.id, node.title, node.text))
    known = existing | proposal.ghost_ids()
    for node in parsed.nodes:
        for edge in node.edges:
            if edge.target not in known or edge.target == node.id:
                continue
            if node.id in existing and any(e.target == edge.target
                                           for e in thread.node(node.id).edges):
                continue                       # the thread already has this arrow
            proposal.edges.append((node.id, Edge(edge.target, edge.label)))
    return proposal


def accept(thread: Thread, proposal: Proposal, places: dict) -> dict[str, str]:
    """Make the ghosts real: real ids, appended, with their arrows. Returns the id map.

    `places` holds the ghosts' positions by their proposal ids; they are
    written into the thread's layout under the real ones.
    """
    mapping: dict[str, str] = {}
    for ghost in proposal.nodes:
        node = thread.add(ghost.title, ghost.text)
        mapping[ghost.id] = node.id
    for source, edge in proposal.edges:
        real_source = mapping.get(source, source)
        real_target = mapping.get(edge.target, edge.target)
        thread.connect(real_source, real_target, edge.label)
    layout = thread.layout
    for ghost_id, real in mapping.items():
        if ghost_id in places:
            layout[real] = places[ghost_id]
    if mapping:
        thread.layout = layout
    return mapping
