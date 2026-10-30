"""Step 5: build the evidence ("context pack") the LLM sees for each at-risk file.

Everything keeps its `path:line` (or commit sha) so the validator can check
every claim against exactly what the model was shown. Each file's pack is
capped at ~6k tokens (estimated as chars / 4).
"""

import ast
import re
from pathlib import Path, PurePosixPath

from handoff.impact import _module_names
from handoff.miner import Commit, git

CHAR_BUDGET = 6000 * 4
MAX_COMMITS = 20
MAX_CALLERS = 3
MAX_TESTS = 5
HEADER_LINES = 30  # module header (docstring, imports) kept at the top of every outline
DOC_LINES = 6      # docstring lines kept per function / class
OTHER_HEAD = 40    # non-Python files: first N lines, then function-like headers

MARKER_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
RISKY_RE = re.compile(r"\b(fix|fixes|fixed|revert|workaround|hotfix|hack)\b", re.I)
HEADER_RE = re.compile(r"^\s*(export\s+)?(async\s+)?(def|class|func|fn|function|interface|struct|impl|module)\b")


def _numbered(lines: list[str], start: int, end: int) -> str:
    """Source lines start..end (1-based, inclusive) prefixed with their line number."""
    return "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, min(end, len(lines)) + 1))


def _python_spans(src: str, n_lines: int) -> list[tuple[int, int]]:
    """(start, end) line spans covering each def/class signature plus the head of its docstring."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    spans = []

    def visit(body):
        for node in body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if any(getattr(d, "id", getattr(d, "attr", None)) == "overload" for d in node.decorator_list):
                continue  # typing stubs repeat the real signature
            start = min([d.lineno for d in node.decorator_list] + [node.lineno])
            first = node.body[0]
            end = first.lineno - 1 if first.lineno > node.lineno else node.lineno
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
                    and isinstance(first.value.value, str):
                end = min(first.end_lineno, first.lineno + DOC_LINES - 1)
            spans.append((start, max(end, node.lineno)))
            if isinstance(node, ast.ClassDef):
                visit(node.body)

    visit(tree.body)
    first_def = min((s for s, _ in spans), default=n_lines + 1)
    header_end = min(HEADER_LINES, first_def - 1, n_lines)
    if header_end >= 1:
        spans.insert(0, (1, header_end))
    return spans


def _other_spans(lines: list[str]) -> list[tuple[int, int]]:
    spans = [(1, min(OTHER_HEAD, len(lines)))] if lines else []
    spans += [(i, i) for i, line in enumerate(lines, 1) if i > OTHER_HEAD and HEADER_RE.match(line)]
    return spans


def outline(repo: Path, path: str, budget: int = CHAR_BUDGET) -> list[dict]:
    """Code structure with line numbers: [{"start", "end", "text"}], within `budget` chars."""
    try:
        src = (repo / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = src.splitlines()
    spans = _python_spans(src, len(lines)) if path.endswith(".py") else _other_spans(lines)
    out, used = [], 0
    for start, end in spans:
        text = _numbered(lines, start, end)
        if used + len(text) > budget:
            break
        out.append({"start": start, "end": min(end, len(lines)), "text": text})
        used += len(text)
    return out


def _dev_matcher(analysis: dict, dev_id: str):
    dev = next(d for d in analysis["developers"] if d["id"] == dev_id)
    emails = {e.lower() for e in dev["emails"]}
    names = {" ".join(n.lower().split()) for n in dev["names"]}
    return lambda name, email: email.lower() in emails or " ".join(name.lower().split()) in names


def markers(repo: Path, path: str, is_dev) -> list[dict]:
    """TODO/FIXME/HACK/XXX lines that `git blame` attributes to the departing developer."""
    try:
        out = git(repo, "blame", "--line-porcelain", "-w", "HEAD", "--", path)
    except RuntimeError:
        return []
    found, name, email, line_no = [], "", "", 0
    for raw in out.splitlines():
        if raw.startswith("\t"):
            text = raw[1:]
            if MARKER_RE.search(text) and is_dev(name, email):
                found.append({"line": line_no, "text": text.strip()[:200]})
        elif raw.startswith("author "):
            name = raw[7:]
        elif raw.startswith("author-mail "):
            email = raw[12:].strip("<>")
        elif re.match(r"^[0-9a-f]{40} ", raw):
            line_no = int(raw.split()[2])
    return found


def _import_lines(repo: Path, caller: str, target: str) -> list[tuple[int, str]]:
    """Lines in `caller` that import `target` (absolute, `from pkg import mod`, or relative)."""
    names = _module_names(target)
    stem = PurePosixPath(target).stem
    if stem == "__init__":
        stem = PurePosixPath(target).parent.name
    same_dir = PurePosixPath(caller).parent == PurePosixPath(target).parent
    pats = []
    for n in names:
        pkg, _, mod = n.rpartition(".")
        pats.append(rf"^\s*(from\s+{re.escape(n)}\s+import|import\s+{re.escape(n)}\b)")
        if pkg:
            pats.append(rf"^\s*from\s+{re.escape(pkg)}\s+import\s+.*\b{re.escape(mod)}\b")
    if same_dir:
        pats.append(rf"^\s*from\s+\.{re.escape(stem)}\s+import")
        pats.append(rf"^\s*from\s+\.\s+import\s+.*\b{re.escape(stem)}\b")
    rx = re.compile("|".join(pats))
    try:
        lines = (repo / caller).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [(i, line.strip()) for i, line in enumerate(lines, 1) if rx.match(line)]


def _is_test(path: str) -> bool:
    p = PurePosixPath(path)
    return any(part in ("test", "tests", "testing") for part in p.parts[:-1]) \
        or p.name.startswith("test_") or p.stem.endswith("_test")


def file_pack(repo: Path, path: str, dev_id: str, commits: list[Commit], py_files: list[str], is_dev) -> dict:
    mine = [c for c in reversed(commits) if c.dev_id == dev_id and path in c.files]  # newest first
    seen, recent = set(), []
    for c in mine:
        key = c.subject.strip().lower()
        if key not in seen:
            seen.add(key)
            recent.append({"sha": c.sha, "date": c.date.date().isoformat(), "msg": c.subject})
        if len(recent) == MAX_COMMITS:
            break
    risky = [{"sha": c.sha, "msg": c.subject} for c in mine if RISKY_RE.search(c.subject)][:MAX_COMMITS]

    importers = {}
    if path.endswith(".py"):
        for f in py_files:
            if f != path:
                hits = _import_lines(repo, f, path)
                if hits:
                    importers[f] = hits[0]
    stem = PurePosixPath(path).stem
    tests = sorted({f for f in importers if _is_test(f)} | {f for f in py_files if _is_test(f) and stem in PurePosixPath(f).stem})
    callers = [
        {"path": f, "line": line, "text": text}
        for f, (line, text) in sorted(importers.items(), key=lambda kv: (_is_test(kv[0]), kv[0]))
        if not _is_test(f)
    ][:MAX_CALLERS]

    pack = {
        "path": path,
        "outline": [],
        "commits": recent,
        "markers": markers(repo, path, is_dev),
        "risky_commits": risky,
        "tests": tests[:MAX_TESTS],
        "callers": callers,
    }
    # The outline gets whatever budget the rest of the evidence leaves.
    pack["outline"] = outline(repo, path, max(CHAR_BUDGET - len(render_file(pack)), 2000))
    return pack


def build_pack(repo: Path, analysis: dict, dev_id: str, module: dict, commits: list[Commit]) -> dict:
    """The contextpack.json for one module (handoff.md §6)."""
    repo = Path(repo)
    py_files = [f for f in git(repo, "ls-files").splitlines() if f.endswith(".py")]
    is_dev = _dev_matcher(analysis, dev_id)
    return {
        "module": module["path"],
        "dev": dev_id,
        "files": [file_pack(repo, f["path"], dev_id, commits, py_files, is_dev) for f in module["files"]],
    }


def render_file(fp: dict) -> str:
    """The evidence for one file as plain text for the prompt. Every item carries its cite."""
    out = [f"=== FILE {fp['path']} ==="]
    if fp.get("outline"):
        out.append("--- code outline (line: source) ---")
        out += [chunk["text"] for chunk in fp["outline"]]
    if fp["markers"]:
        out.append("--- TODO/FIXME/HACK comments written by the departing maintainer ---")
        out += [f"{fp['path']}:{m['line']}: {m['text']}" for m in fp["markers"]]
    if fp["commits"]:
        out.append("--- the departing maintainer's commits to this file (newest first) ---")
        out += [f"commit:{c['sha'][:10]} ({c['date']}) {c['msg']}" for c in fp["commits"]]
    if fp["risky_commits"]:
        out.append("--- their fix / revert / workaround commits ---")
        out += [f"commit:{c['sha'][:10]} {c['msg']}" for c in fp["risky_commits"]]
    if fp["callers"]:
        out.append("--- files that import this one ---")
        out += [f"{c['path']}:{c['line']}: {c['text']}" for c in fp["callers"]]
    out.append("--- tests that reference it ---")
    out.append(", ".join(fp["tests"]) if fp["tests"] else "(none found)")
    return "\n".join(out)
