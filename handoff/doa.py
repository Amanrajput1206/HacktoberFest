"""Step 3: Degree-of-Authorship (Fritz et al., as used by Avelino et al. for truck factor).

DOA(d, f) = 3.293 + 1.098*FA + 0.164*DL - 0.321*ln(1 + AC)
  FA = 1 if d created f, DL = d's commits to f, AC = everyone else's commits to f.
d is an author of f iff DOA_N(d, f) > 0.75 and DOA(d, f) >= 3.293.
"""

import math
from collections import defaultdict
from dataclasses import dataclass

from handoff.miner import Commit

INTERCEPT = 3.293
NORM_THRESHOLD = 0.75


@dataclass
class Authorship:
    doa: dict[str, dict[str, float]]    # file -> dev -> DOA
    doa_n: dict[str, dict[str, float]]  # file -> dev -> DOA normalised by the file's max
    authors: dict[str, set[str]]        # file -> devs that count as authors


def doa(fa: int, dl: int, ac: int) -> float:
    return INTERCEPT + 1.098 * fa + 0.164 * dl - 0.321 * math.log(1 + ac)


def compute(commits: list[Commit], files: list[str]) -> Authorship:
    deliveries: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    creator: dict[str, str] = {}
    for c in commits:  # oldest first, so the first `added` wins if a path was re-created
        for f in c.files:
            deliveries[f][c.dev_id] += 1
        for f in c.added:
            creator.setdefault(f, c.dev_id)

    scores, normalised, authors = {}, {}, {}
    for f in files:
        per_dev = deliveries.get(f, {})
        total = sum(per_dev.values())
        s = {d: doa(int(creator.get(f) == d), dl, total - dl) for d, dl in per_dev.items()}
        top = max(s.values(), default=0.0)
        # A non-positive max would flip signs when normalising; nobody can be an author then anyway.
        n = {d: v / top for d, v in s.items()} if top > 0 else {d: 0.0 for d in s}
        scores[f], normalised[f] = s, n
        authors[f] = {d for d in s if n[d] > NORM_THRESHOLD and s[d] >= INTERCEPT}
    return Authorship(scores, normalised, authors)
