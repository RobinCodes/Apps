"""Where thoughts go when nobody has put them anywhere: left to right, in layers.

A thread without `layout:` is still a thread (SYSTEM.md §10), so the canvas
has to be able to draw one from nothing. Thoughts are read as a flow: each is
put one column to the right of the furthest thought that leads to it, and
the thoughts in a column are ordered so that the arrows between columns cross
as little as a few sweeps can manage. A cycle — a thread that comes back to
where it started — is broken for the arrangement only, never in the file.

No GTK here; the suite checks it without a display.
"""

from __future__ import annotations

COLUMN = 340
ROW = 190
GAP = 48               # between two cards in a column, whatever their height
ORIGIN = (80.0, 60.0)


def arrange(ids, edges, origin=ORIGIN, column=COLUMN, row=ROW,
            heights=None) -> dict[str, tuple[float, float]]:
    """Positions for `ids`, given `edges` as (from, to) pairs between them.

    `heights`, when given, are the cards' own, so a long thought pushes the
    one under it down rather than lying across it; without them every card is
    taken to be `row` minus the gap.
    """
    ids = list(dict.fromkeys(ids))
    known = set(ids)
    successors = {i: [] for i in ids}
    for source, target in edges:
        if source in known and target in known and source != target \
                and target not in successors[source]:
            successors[source].append(target)

    # Break cycles: an arrow back to a thought still being walked is set aside.
    kept = {i: [] for i in ids}
    state: dict[str, int] = {}

    def visit(node):
        stack = [(node, iter(successors[node]))]
        state[node] = 1
        while stack:
            current, children = stack[-1]
            child = next(children, None)
            if child is None:
                state[current] = 2
                stack.pop()
                continue
            if state.get(child) == 1:
                continue                      # a way back: not for layering
            kept[current].append(child)
            if child not in state:
                state[child] = 1
                stack.append((child, iter(successors[child])))

    for node in ids:
        if node not in state:
            visit(node)

    # Longest path from a source: a thought sits right of all that lead to it.
    incoming = {i: 0 for i in ids}
    for source in ids:
        for target in kept[source]:
            incoming[target] += 1
    layer = {i: 0 for i in ids}
    ready = [i for i in ids if incoming[i] == 0]
    while ready:
        node = ready.pop(0)
        for target in kept[node]:
            layer[target] = max(layer[target], layer[node] + 1)
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)

    columns: dict[int, list[str]] = {}
    for node in ids:
        columns.setdefault(layer[node], []).append(node)

    # A few sweeps of the barycentre heuristic, each way.
    predecessors = {i: [] for i in ids}
    for source in ids:
        for target in kept[source]:
            predecessors[target].append(source)
    order = {node: index for column_nodes in columns.values()
             for index, node in enumerate(column_nodes)}
    depth = max(columns) if columns else 0
    for sweep in range(4):
        sequence = range(1, depth + 1) if sweep % 2 == 0 else range(depth - 1, -1, -1)
        neighbours = predecessors if sweep % 2 == 0 else kept
        for level in sequence:
            nodes = columns.get(level, [])

            def centre(node):
                linked = [order[n] for n in neighbours[node] if n in order]
                return sum(linked) / len(linked) if linked else order[node]

            nodes.sort(key=lambda n: (centre(n), order[n]))
            for index, node in enumerate(nodes):
                order[node] = index

    heights = heights or {}

    def height(node):
        return heights.get(node, row - GAP)

    def extent(nodes):
        return sum(height(n) for n in nodes) + GAP * max(0, len(nodes) - 1)

    tallest = max((extent(nodes) for nodes in columns.values()), default=0)
    places = {}
    for level, nodes in columns.items():
        y = origin[1] + (tallest - extent(nodes)) / 2
        for node in nodes:
            places[node] = (origin[0] + level * column, y)
            y += height(node) + GAP
    return places


def free_spot(places: dict, near: tuple[float, float], column=COLUMN, row=ROW):
    """A place beside `near` that no thought is standing on: right, then down."""
    x, y = near[0] + column, near[1]
    taken = list(places.values())
    for _ in range(200):
        if all(abs(x - px) > column * 0.6 or abs(y - py) > row * 0.6 for px, py in taken):
            return x, y
        y += row
    return x, y
