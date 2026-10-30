"""Step 7: drop every LLM claim whose citation is not in the evidence we gave the model.

A cite is `path:line`, `path:start-end` or `commit:sha` (a prefix of 7+ hex chars).
A claim with several cites (`a.py:3, commit:abc1234`) is kept only if every one
checks out. Uncited claims are dropped too. An entry point must also name
something that appears on (or just below) the line it cites.
"""

import re

CITED = ("key_entry_points", "invariants", "gotchas")
LINE_RE = re.compile(r"^(?P<path>[^\s:]+):L?(?P<start>\d+)(?:\s*-\s*L?(?P<end>\d+))?$")
COMMIT_RE = re.compile(r"^commit:(?P<sha>[0-9a-f]{7,40})$", re.I)


def evidence(pack: dict) -> tuple[dict[str, dict[int, str]], set[str]]:
    """Source text shown per path and line, and the commit shas shown, in a contextpack (or one file of it)."""
    lines: dict[str, dict[int, str]] = {}
    shas: set[str] = set()
    for fp in pack.get("files", [pack]):
        shown = lines.setdefault(fp["path"], {})
        for chunk in fp.get("outline", []):
            for row in chunk["text"].splitlines():
                num, _, text = row.partition(": ")
                if num.isdigit():
                    shown[int(num)] = text
            for n in range(chunk["start"], chunk["end"] + 1):
                shown.setdefault(n, "")
        shown.update({m["line"]: m["text"] for m in fp.get("markers", [])})
        for c in fp.get("callers", []):
            lines.setdefault(c["path"], {})[c["line"]] = c["text"]
        shas.update(c["sha"].lower() for c in fp.get("commits", []) + fp.get("risky_commits", []))
    return lines, shas


def names_line(name: str, cite: str, lines: dict[str, dict[int, str]]) -> bool:
    """True if the entry point's identifier appears on the cited line or the 3 lines after it (decorators)."""
    ident = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", name or "")
    m = LINE_RE.match(cite.strip().strip("`'\"").removeprefix("./"))
    if not ident or not m:
        return False
    shown = lines.get(m["path"], {})
    start = int(m["start"])
    return any(re.search(rf"\b{re.escape(ident[-1])}\b", shown.get(n, "")) for n in range(start, start + 4))


def check_cite(cite: str, lines: dict[str, set[int]], shas: set[str]) -> bool:
    parts = [p.strip().strip("`'\"").removeprefix("./") for p in re.split(r"[,;|]|\s+and\s+", cite or "")]
    parts = [p for p in parts if p]
    if not parts:
        return False
    for p in parts:
        if m := COMMIT_RE.match(p):
            sha = m["sha"].lower()
            if not any(s.startswith(sha) for s in shas):
                return False
        elif m := LINE_RE.match(p):
            start, end = int(m["start"]), int(m["end"] or m["start"])
            shown = lines.get(m["path"], set())
            if end < start or not all(n in shown for n in range(start, end + 1)):
                return False
        else:
            return False
    return True


def validate(notes: dict, pack: dict) -> tuple[dict, int, int]:
    """Drops claims whose cite isn't in the pack. Returns (kept_notes, kept_count, dropped_count)."""
    lines, shas = evidence(pack)
    kept_notes = dict(notes)
    kept = dropped = 0
    for key in CITED:
        good = []
        for item in notes.get(key) or []:
            ok = isinstance(item, dict) and check_cite(str(item.get("cite", "")), lines, shas)
            if ok and key == "key_entry_points":
                ok = names_line(str(item.get("name", "")), str(item["cite"]), lines)
            if ok:
                good.append(item)
            else:
                dropped += 1
        kept += len(good)
        kept_notes[key] = good
    return kept_notes, kept, dropped
