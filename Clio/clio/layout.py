"""Where everything on a board goes, for this zoom and this size.

The geometry is kept apart from the painting, so that what can be asserted is
asserted without a screen: that no two labels lie across each other, that a
milestone's name is the last to be dropped, that a span inside a span has its
brace further in.

Events hang above the axis. Each is a mark on the line — a dot for a day, and
for a month or a year a band across the whole of it, because an event is drawn
as precisely as its date says — with a stem up to its name, and the names are
stacked in rows so they do not collide. Spans lie below the axis as `}` braces,
turned on their side so that the point faces down at the name. A span inside
another sits nearer the axis, brace beside brace outwards.

No GTK here: `measure(text, bold)` is how wide a label would be, and the widget
passes in Pango's answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .axis import Axis
from .timelines import Board, Item

TOP = 8                 # above the highest row of names
ROW = 22                # one row of names
STEM = 18               # from the axis up to the first row
TICKS = 20              # the tick labels under the axis
AGES = 15               # the row of ages under those
LANE = 40               # one span's brace and its name
BRACE = 11              # how deep a brace is
BOTTOM = 8
LABEL_MAX = 230         # the widest a name is drawn
NEAR = 12               # how far past the edge an event's mark can still be seen
GAP = 8                 # between two names on a row
MIN_NAME = 36           # narrower than this a name is not worth drawing


@dataclass
class Mark:
    """An event as drawn."""
    item: Item
    x1: float
    x2: float
    centre: float
    band: bool            # across its month or year, rather than a dot
    radius: float
    row: int = -1         # which row its name is in; -1 when there was no room
    label_x: float = 0.0
    label_y: float = 0.0
    label_w: float = 0.0
    ghost: bool = False


@dataclass
class Brace:
    """A span as drawn: a `}` from x1 to x2, its point at `point`, its name under."""
    item: Item
    x1: float
    x2: float
    point: float          # the middle of what is in view, so the name stays on screen
    y: float
    lane: int
    label_x: float = 0.0
    label_y: float = 0.0
    label_w: float = 0.0
    ghost: bool = False


@dataclass
class Scene:
    axis_y: float = 0.0
    marks: list[Mark] = field(default_factory=list)
    braces: list[Brace] = field(default_factory=list)
    rows: int = 0
    overflow: float = 0.0   # how much of the braces hangs below the bottom edge

    def mark_at(self, x, y, reach=7.0, row_height=ROW):
        """The event under a point: its mark on the axis, or its name above."""
        hit = None
        for mark in self.marks:
            on_mark = (mark.x1 - reach <= x <= mark.x2 + reach
                       and abs(y - self.axis_y) <= reach + mark.radius)
            on_name = (mark.row >= 0 and mark.label_x <= x <= mark.label_x + mark.label_w
                       and mark.label_y <= y <= mark.label_y + row_height)
            if on_mark or on_name:
                hit = mark
        return hit

    def brace_at(self, x, y):
        """The span under a point: its brace, or its name."""
        for brace in reversed(self.braces):
            on_brace = (brace.x1 - 4 <= x <= brace.x2 + 4
                        and brace.y - 4 <= y <= brace.y + BRACE + 4)
            on_name = (brace.label_x <= x <= brace.label_x + brace.label_w
                       and brace.label_y <= y <= brace.label_y + ROW)
            if on_brace or on_name:
                return brace
        return None


def shown(item: Item, hidden) -> bool:
    """A filtered-out category hides an item only if all of its categories are hidden."""
    names = item.categories
    return not names or not hidden or any(name not in hidden for name in names)


def arrange(board: Board, axis: Axis, height: float, measure, hidden=frozenset(),
            ghosts=(), today=None) -> Scene:
    """The whole board, for an axis of this width and a widget of this height."""
    items = [item for item in board.items if shown(item, hidden)]
    ghost_ids = {id(item) for item in ghosts}
    items += list(ghosts)
    width = axis.width

    # Lanes for every span there is, not only those in view, so that a brace
    # does not change lane under your eyes as you move along the axis.
    every_span = [item for item in items if item.span]
    lanes = board.lanes(every_span, today)
    count = max(lanes.values(), default=-1) + 1
    extra = AGES if board.born else 0
    below = TICKS + extra + count * LANE + BOTTOM
    # As low as what hangs below it allows, for the names' sake, but no lower than
    # all of them could need, with what is left over shared out above and below.
    # By what the board has rather than what is in view, so it holds still as you zoom.
    lowest = max(height * 0.34, height - below - 4)
    wanted = TOP + STEM + ROW * (sum(1 for item in items if not item.span) + 1)
    axis_y = lowest if wanted >= lowest else (wanted + lowest) / 2
    brace_top = axis_y + TICKS + extra
    scene = Scene(axis_y=axis_y, overflow=max(0.0, brace_top + count * LANE + BOTTOM - height))

    # Events: the milestones choose their rows first, so they are the last to
    # lose their names when it gets crowded.
    first, last = axis.day(-NEAR), axis.day(width + NEAR)
    events = [item for item in items if not item.span
              and item.start.first_day() < last and item.start.end_day() > first]
    rows = max(1, int((axis_y - STEM - TOP) / ROW))
    scene.rows = rows
    occupied: list[list[tuple[float, float]]] = [[] for _ in range(rows)]
    for item in sorted(events, key=lambda i: (not i.milestone, i.start.first_day())):
        x1, x2 = axis.x(item.start.first_day()), axis.x(item.start.end_day())
        radius = 7.0 if item.milestone else 4.5
        centre = (max(x1, 0.0) + min(x2, width)) / 2     # the part of it in view
        mark = Mark(item, x1, x2, centre, x2 - x1 >= 2 * radius, radius,
                    ghost=id(item) in ghost_ids)
        wanted = min(measure(item.title or "Untitled", item.milestone) + 4, LABEL_MAX)
        left = max(2.0, min(centre - 3, width - wanted - 2))
        for row in range(rows):
            if all(left + wanted + GAP <= a or b + GAP <= left for a, b in occupied[row]):
                occupied[row].append((left, left + wanted))
                mark.row = row
                mark.label_x, mark.label_w = left, wanted
                mark.label_y = axis_y - STEM - ROW * (row + 1)
                break
        scene.marks.append(mark)
    scene.marks.sort(key=lambda m: m.centre)

    # Spans: each brace's point stays over what is in view, and the names of a
    # lane are kept off each other, left to right, the later one giving way.
    spans = [item for item in every_span
             if axis.x(item.first_day()) < width and axis.x(item.end_day(today)) > 0]
    ends: dict[int, float] = {}
    for item in sorted(spans, key=lambda s: (lanes[id(s)], s.first_day())):
        x1, x2 = axis.x(item.first_day()), axis.x(item.end_day(today))
        lane = lanes[id(item)]
        point = (max(x1, 0.0) + min(x2, width)) / 2
        y = brace_top + lane * LANE
        wanted = min(measure(item.title or "Untitled", False) + 4, LABEL_MAX)
        label_x = max(2.0, min(point - wanted / 2, width - wanted - 2))
        label_x = max(label_x, ends.get(lane, -GAP) + GAP)
        room = width - 2 - label_x
        wanted = min(wanted, room)
        if wanted < MIN_NAME:
            wanted = 0.0                 # no room for a name: the tooltip still has it
        else:
            ends[lane] = label_x + wanted
        scene.braces.append(Brace(item, x1, x2, point, y, lane, label_x, y + BRACE + 1, wanted,
                                  ghost=id(item) in ghost_ids))
    return scene
