"""Step 4: coverage, truck factor, departure impact and risk ranking.

All "recent" windows are measured back from the HEAD commit's date, not from
today, so old repositories still produce meaningful successors and recency.
"""

import ast
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from handoff import doa as doa_mod
from handoff.miner import History, mine_repo

RECENT = timedelta(days=365)
TOP_MODULES = 5
W_LOC, W_FAN_IN, W_RECENT = 0.4, 0.4, 0.2
AT_RISK_WEIGHT = 0.5


def coverage(authors: dict[str, set[str]], excluded: set[str] = frozenset()) -> float:
    if not authors:
        return 0.0
    return sum(1 for a in authors.values() if a - excluded) / len(authors)


def truck_factor(authors: dict[str, set[str]], excluded: set[str] = frozenset()) -> tuple[int, list[str]]:
    """Greedily remove the developer who authors the most files until coverage drops below 50%."""
    counts: dict[str, int] = defaultdict(int)
    for a in authors.values():
        for d in a - excluded:
            counts[d] += 1
    ranked = sorted(counts, key=lambda d: (-counts[d], d))
    removed: list[str] = []
    gone = set(excluded)
    for d in ranked:
        if coverage(authors, gone) < 0.5:
            break
        removed.append(d)
        gone.add(d)
    return len(removed), removed


def find_dev(history: History, who: str) -> str:
    """Resolve a dev id, name or email (case-insensitive; substring as a fallback)."""
    if who in history.developers:
        return who
    w = who.lower().strip()
    devs = sorted(history.developers.values(), key=lambda d: -d.commits)
    for d in devs:
        if w in (n.lower() for n in d.names) or w in d.emails:
            return d.id
    for d in devs:
        if any(w in n.lower() for n in d.names) or any(w in e for e in d.emails):
            return d.id
    raise ValueError(f"No developer matches {who!r}")


def count_loc(repo: Path, path: str) -> int:
    try:
        with open(repo / path, encoding="utf-8", errors="replace") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def _module_names(path: str) -> list[str]:
    """Dotted module names a Python file can be imported as (from the root, or below src/ or lib/)."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    names = [".".join(parts)] if parts else []
    if len(parts) > 1 and parts[0] in ("src", "lib", "python"):
        names.append(".".join(parts[1:]))
    return names


def python_fan_in(repo: Path, files: list[str]) -> dict[str, int]:
    """Number of other repo files that import each Python file (static imports only)."""
    py = [f for f in files if f.endswith(".py")]
    index: dict[str, str] = {}
    for f in py:
        for name in _module_names(f):
            index.setdefault(name, f)

    importers: dict[str, set[str]] = defaultdict(set)
    for f in py:
        try:
            tree = ast.parse((repo / f).read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError, OSError):
            continue
        package = _module_names(f)[0].split(".") if _module_names(f) else []
        if not f.endswith("__init__.py"):
            package = package[:-1]
        targets: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    targets.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                    mod = ".".join(base + ([node.module] if node.module else []))
                else:
                    mod = node.module or ""
                targets.add(mod)
                for alias in node.names:  # `from pkg import submodule`
                    targets.add(f"{mod}.{alias.name}" if mod else alias.name)
        for t in targets:
            # Walk up `a.b.c` -> `a.b` -> `a` until something in the repo matches.
            parts = t.split(".")
            while parts:
                hit = index.get(".".join(parts))
                if hit:
                    if hit != f:
                        importers[hit].add(f)
                    break
                parts.pop()
    return {f: len(importers.get(f, ())) for f in files}


def _norm(values: dict[str, float]) -> dict[str, float]:
    top = max(values.values(), default=0)
    return {k: (v / top if top else 0.0) for k, v in values.items()}


def _module_of(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "./" if parent == "." else parent + "/"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def run_analysis(repo: Path, who: str | None = None, bulk_limit: int = 100) -> dict:
    """Returns the analysis.json dict (handoff.md §6). `departure` is filled only when `who` is given."""
    repo = Path(repo)
    history = mine_repo(repo, bulk_limit)
    auth = doa_mod.compute(history.commits, history.head_files)

    files_authored: dict[str, int] = defaultdict(int)
    for a in auth.authors.values():
        for d in a:
            files_authored[d] += 1

    tf, removed = truck_factor(auth.authors)
    result = {
        "repo": history.repo_name,
        "head": history.head,
        "head_date": history.head_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "developers": [
            {
                "id": d.id,
                "names": d.names,
                "emails": d.emails,
                "commits": d.commits,
                "files_authored": files_authored[d.id],
                "last_commit": _iso(d.last_commit),
            }
            for d in history.developers.values()
        ],
        "truck_factor": {"value": tf, "removed": removed, "coverage": round(coverage(auth.authors), 4)},
        "departure": None,
        "modules": [],
    }
    if who is None:
        return result

    x = find_dev(history, who)
    at_risk = [f for f, a in auth.authors.items() if x in a]
    orphaned = {f for f in at_risk if auth.authors[f] == {x}}
    tf_after, _ = truck_factor(auth.authors, excluded={x})
    result["departure"] = {
        "dev": x,
        "at_risk_files": len(at_risk),
        "orphaned_files": len(orphaned),
        "coverage_before": round(coverage(auth.authors), 4),
        "coverage_after": round(coverage(auth.authors, {x}), 4),
        "truck_factor_after": tf_after,
    }

    cutoff = history.head_date - RECENT
    recent: dict[str, int] = defaultdict(int)
    for c in history.commits:
        if c.dev_id == x and c.date >= cutoff:
            for f in c.files:
                recent[f] += 1

    loc = {f: count_loc(repo, f) for f in at_risk}
    fan_in_all = python_fan_in(repo, history.head_files)
    fan_in = {f: fan_in_all.get(f, 0) for f in at_risk}
    n_loc, n_fan, n_rec = _norm(loc), _norm(fan_in), _norm({f: recent[f] for f in at_risk})
    importance = {f: W_LOC * n_loc[f] + W_FAN_IN * n_fan[f] + W_RECENT * n_rec[f] for f in at_risk}

    active = {d.id for d in history.developers.values() if d.last_commit and d.last_commit >= cutoff}
    by_module: dict[str, list[str]] = defaultdict(list)
    for f in at_risk:
        by_module[_module_of(f)].append(f)

    modules = []
    for path, mfiles in by_module.items():
        risk = sum(importance[f] * (1.0 if f in orphaned else AT_RISK_WEIGHT) for f in mfiles)
        doa_sums: dict[str, float] = defaultdict(float)
        for f in mfiles:
            for d, v in auth.doa_n[f].items():
                if d != x and d in active:
                    doa_sums[d] += v
        best = max(doa_sums, key=lambda d: (doa_sums[d], d), default=None)
        successor = {"dev": best, "doa_sum": round(doa_sums[best], 3)} if best and doa_sums[best] > 0 else None
        modules.append({
            "path": path,
            "risk": round(risk, 3),
            "orphaned_files": sum(f in orphaned for f in mfiles),
            "at_risk_files": len(mfiles),
            "successor": successor,
            "files": [
                {
                    "path": f,
                    "loc": loc[f],
                    "fan_in": fan_in[f],
                    "orphaned": f in orphaned,
                    "importance": round(importance[f], 3),
                    "authors": sorted(auth.authors[f]),
                    "doa_n": {d: round(v, 3) for d, v in sorted(auth.doa_n[f].items(), key=lambda kv: -kv[1])},
                }
                for f in sorted(mfiles, key=lambda f: -importance[f])
            ],
        })
    modules.sort(key=lambda m: -m["risk"])
    result["modules"] = modules[:TOP_MODULES]
    return result
