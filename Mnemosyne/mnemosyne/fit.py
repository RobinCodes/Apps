"""FSRS, fitted to Robin's own memory once there is enough of it to fit.

FSRS-5's default parameters were fitted on hundreds of millions of other
people's reviews, and they are a good start for anyone. They are not anyone in
particular: how fast a first "Good" fades, how much a lapse costs, how much a
review of a nearly forgotten card helps — those differ from person to person
and from Hungarian grammar to Italian vocabulary. HANDBOOK.md §5.2 asks for
the schedule to be calibrated on the log once the log can say something, and
this module is that calibration.

**What it fits.** The 19 FSRS weights, scored by how well they predict the
log: every review that came at least a day after the one before is a
prediction — FSRS said the card had a retrievability R at that moment, and
the card was either recalled (any rating but Again) or not. The score is the
log loss of those predictions, the same objective the FSRS optimiser uses.

**How.** Not gradient descent and not a dependency: a bounded coordinate
search. Each weight in turn is nudged up and down by a step; a nudge that
lowers the loss is kept, the step shrinks when nothing helps, and the whole
pass is repeated a few times. Every weight is held within a factor of four of
its default, and a small penalty pulls towards the defaults — felt with a
few hundred reviews, fading as the log grows — so that a short or lopsided
history bends the schedule rather than breaking it. That is cruder than the
real optimiser and it does not need to be better: the defaults are already
close, and what is wanted is the last stretch towards one person.

**When.** At 400 spaced reviews (`MIN_REVIEWS`), and again whenever the log has
grown by a quarter since the last fit. The result is written to
`config/learning.yaml` as `weights`, so every machine schedules with the same
numbers; `weights_reviews` says how many reviews they came from.

Pure Python, standard library; a few seconds on the school laptop for a few
thousand reviews, run on a worker thread.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import fsrs

MIN_REVIEWS = 400
REFIT_GROWTH = 1.25
BOUND = 4.0                    # each weight stays within ×/÷ this of its default
ROUNDS = 4


@dataclass
class Fit:
    weights: tuple[float, ...]
    reviews: int               # spaced reviews it was scored on
    loss_before: float
    loss_after: float


def spaced_reviews(histories) -> int:
    """How many reviews came a day or more after the card's previous one."""
    count = 0
    for history in histories:
        last = None
        for when, _rating in history:
            if last is not None and fsrs.elapsed_days(last, when) >= 1:
                count += 1
            last = when
    return count


def loss(histories, w) -> tuple[float, int]:
    """Mean log loss of FSRS's predicted recall over the spaced reviews."""
    total = 0.0
    n = 0
    for history in histories:
        s = d = None
        last = None
        for when, rating in history:
            if s is None:
                s = fsrs.initial_stability(rating, w)
                d = fsrs.initial_difficulty(rating, w)
                last = when
                continue
            days = fsrs.elapsed_days(last, when)
            if days >= 1:
                r = min(max(fsrs.retrievability(days, s), 1e-4), 1 - 1e-4)
                recalled = rating != fsrs.AGAIN
                total -= math.log(r) if recalled else math.log(1 - r)
                n += 1
                if rating == fsrs.AGAIN:
                    s_new = fsrs.stability_after_lapse(d, s, r, w)
                else:
                    s_new = fsrs.stability_after_recall(d, s, r, rating, w)
            else:
                s_new = fsrs.stability_same_day(s, rating, w)
            d = fsrs.next_difficulty(d, rating, w)
            s = s_new
            last = when
    return (total / n if n else 0.0), n


def fit(histories, start=fsrs.DEFAULT_W, rounds: int = ROUNDS) -> Fit:
    """Fit the weights to `histories`, each a time-ordered list of `(when, rating)`."""
    histories = [sorted(h, key=lambda item: item[0]) for h in histories if len(h) > 1]
    defaults = list(fsrs.DEFAULT_W)
    weights = list(start)
    base, n = loss(histories, weights)
    if not n:
        return Fit(tuple(weights), 0, base, base)
    # The pull towards the defaults: worth about ten reviews' disagreement,
    # so it decides only where the log has little to say.
    strength = 10.0 / max(n, 1)

    def score(candidate):
        value, _ = loss(histories, candidate)
        drift = sum(math.log(c / d) ** 2 for c, d in zip(candidate, defaults))
        return value + strength * drift

    current = score(weights)
    steps = [0.25] * len(weights)
    for _round in range(rounds):
        improved = False
        for i in range(len(weights)):
            for direction in (1, -1):
                trial = list(weights)
                trial[i] = weights[i] * math.exp(direction * steps[i])
                low, high = defaults[i] / BOUND, defaults[i] * BOUND
                trial[i] = min(high, max(low, trial[i]))
                if trial[i] == weights[i]:
                    continue
                value = score(trial)
                if value < current - 1e-6:
                    weights, current, improved = trial, value, True
                    break
            else:
                steps[i] *= 0.5
        if not improved:
            break
    after, _ = loss(histories, weights)
    return Fit(tuple(round(w, 5) for w in weights), n, base, after)


def due_for_a_fit(spaced: int, fitted_on: int) -> bool:
    """Whether the log has grown enough since the last fit to be worth another."""
    if spaced < MIN_REVIEWS:
        return False
    return not fitted_on or spaced >= fitted_on * REFIT_GROWTH
