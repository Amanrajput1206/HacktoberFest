"""Steps 1-2: mine Git history and merge developer identities.

One `git log` pass gives every commit with its per-file status. Paths are
resolved to their name at HEAD by walking history newest -> oldest, so a
file's history survives renames. Bot and bulk commits still take part in
rename tracking but are never credited to anyone.
"""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

BOT_RE = re.compile(r"\[bot\]|dependabot|renovate|github-actions|pre-commit-ci|greenkeeper|snyk-bot|allcontributors", re.I)
NOREPLY_RE = re.compile(r"^(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$", re.I)

SOURCE_EXTS = {
    ".py", ".pyx", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte",
    ".go", ".rs", ".java", ".kt", ".scala", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cs", ".rb", ".php", ".swift", ".m", ".sh", ".sql", ".lua", ".r", ".jl", ".ex", ".exs",
}
SKIP_DIRS = {"vendor", "vendored", "node_modules", "dist", "build", "third_party", "_vendor", "site-packages", "__pycache__"}
SKIP_SUFFIXES = (".min.js", ".min.css", "_pb2.py", "_pb2_grpc.py", ".pb.go", ".generated.ts", ".d.ts")
# Names too generic to merge identities on.
GENERIC_NAMES = {"root", "admin", "user", "unknown", "github", "ubuntu", "your name"}

FIELD = "\x1f"
RECORD = "\x1e"


@dataclass
class Commit:
    sha: str
    dev_id: str          # canonical id after identity merge, matches analysis.json developers[].id
    date: datetime       # author date, UTC
    subject: str
    files: list[str]     # paths at HEAD (renames already resolved)
    added: list[str] = field(default_factory=list)  # subset of `files` this commit created


@dataclass
class Developer:
    id: str
    names: list[str]
    emails: list[str]
    commits: int = 0
    last_commit: datetime | None = None


@dataclass
class History:
    repo_name: str
    head: str
    head_date: datetime
    head_files: list[str]           # tracked source files at HEAD
    commits: list[Commit]           # credited commits only, oldest first
    developers: dict[str, Developer]


@dataclass
class _RawCommit:
    sha: str
    name: str
    email: str
    date: datetime
    subject: str
    changes: list[tuple[str, ...]]  # (status, path) or (status, old, new)


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=repo, capture_output=True)
    if out.returncode:
        err = out.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args[:2])} failed in {repo}: {err}")
    return out.stdout.decode("utf-8", errors="replace")


def is_source(path: str) -> bool:
    p = PurePosixPath(path)
    if any(part in SKIP_DIRS for part in p.parts[:-1]):
        return False
    if path.endswith(SKIP_SUFFIXES):
        return False
    return p.suffix.lower() in SOURCE_EXTS


def is_bot(name: str, email: str) -> bool:
    return bool(BOT_RE.search(name) or BOT_RE.search(email))


def _repo_name(repo: Path) -> str:
    try:
        url = git(repo, "remote", "get-url", "origin").strip()
    except RuntimeError:
        return repo.resolve().name
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else repo.resolve().name


def _read_log(repo: Path) -> list[_RawCommit]:
    """All non-merge commits, newest first."""
    out = git(
        repo, "-c", "diff.renameLimit=10000", "log", "--no-merges", "-M", "--name-status",
        f"--format={RECORD}%H{FIELD}%aN{FIELD}%aE{FIELD}%at{FIELD}%s",
    )
    commits = []
    for chunk in out.split(RECORD)[1:]:
        header, _, body = chunk.partition("\n")
        sha, name, email, ts, subject = header.split(FIELD, 4)
        changes = []
        for line in body.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            changes.append((parts[0][0], *parts[1:]))
        commits.append(_RawCommit(sha, name, email, datetime.fromtimestamp(int(ts), timezone.utc), subject, changes))
    return commits


def _resolve_paths(raw: list[_RawCommit], head_files: set[str]) -> list[tuple[list[str], list[str]]]:
    """For each commit (newest first), the HEAD paths it touched and the HEAD paths it created.

    Walking backwards, `cur[p]` is the HEAD path that path `p` will become,
    or None if `p` at this point in history is not an ancestor of a HEAD file.
    """
    cur: dict[str, str | None] = {}

    def resolve(p: str) -> str | None:
        return cur[p] if p in cur else (p if p in head_files else None)

    result = []
    for c in raw:
        touched, added = [], []
        for change in c.changes:
            status = change[0]
            if status in ("R", "C"):
                old, new = change[1], change[2]
                h = resolve(new)
                cur[new] = None  # before this commit, `new` did not exist under this name
                if status == "R":
                    cur[old] = h
                    if h:
                        touched.append(h)
                elif h:  # a copy creates the new file
                    touched.append(h)
                    added.append(h)
            else:
                p = change[1]
                h = resolve(p)
                if status == "D":
                    cur[p] = None
                    continue
                if h:
                    touched.append(h)
                    if status == "A":
                        added.append(h)
                if status == "A":
                    cur[p] = None
        result.append((touched, added))
    return result


class _UnionFind:
    def __init__(self):
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def _norm_name(name: str) -> str:
    return " ".join(name.lower().split())


def merge_identities(identities: set[tuple[str, str]]) -> dict[tuple[str, str], tuple[str, str]]:
    """Map each (name, email) to a canonical representative identity.

    Merges on equal lower-cased email, equal normalised name, or a GitHub
    noreply username matching another identity's email local-part or name.
    """
    uf = _UnionFind()
    by_email: dict[str, tuple] = {}
    by_name: dict[str, tuple] = {}
    by_key: dict[str, tuple] = {}  # email local-parts and space-less names, for noreply lookups
    noreply: list[tuple[tuple, str]] = []
    for ident in sorted(identities):
        name, email = ident
        uf.find(ident)
        email_l = email.lower()
        n = _norm_name(name)
        if n and n not in GENERIC_NAMES:
            if n in by_name:
                uf.union(ident, by_name[n])
            by_name.setdefault(n, ident)
            by_key.setdefault(n.replace(" ", ""), ident)
        m = NOREPLY_RE.match(email_l)
        if m:
            noreply.append((ident, m.group(1)))
            continue
        if email_l in by_email:
            uf.union(ident, by_email[email_l])
        by_email.setdefault(email_l, ident)
        by_key.setdefault(email_l.split("@")[0], ident)
    # A noreply handle only links to an identity whose email local-part or name matches it.
    by_handle: dict[str, tuple] = {}
    for ident, handle in noreply:
        if handle in GENERIC_NAMES:
            continue
        if handle in by_key and by_key[handle] != ident:
            uf.union(ident, by_key[handle])
        if handle in by_handle:
            uf.union(ident, by_handle[handle])
        by_handle.setdefault(handle, ident)
    return {ident: uf.find(ident) for ident in identities}


def mine_repo(repo: Path, bulk_limit: int = 100) -> History:
    repo = Path(repo)
    head, head_ts = git(repo, "log", "-1", f"--format=%H{FIELD}%at").strip().split(FIELD)
    all_head = git(repo, "ls-tree", "-r", "--name-only", "-z", "HEAD").split("\0")
    head_files = sorted(p for p in all_head if p and is_source(p))
    head_set = set(head_files)

    raw = _read_log(repo)
    resolved = _resolve_paths(raw, head_set)

    credited = [
        (c, touched, added)
        for c, (touched, added) in zip(raw, resolved)
        if touched and not is_bot(c.name, c.email) and len(c.changes) <= bulk_limit
    ]
    canon = merge_identities({(c.name, c.email) for c, _, _ in credited})

    # Group identities, then number developers by commit count (d1 = most commits).
    groups: dict[tuple, list] = {}
    for c, touched, added in credited:
        groups.setdefault(canon[(c.name, c.email)], []).append((c, touched, added))
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    developers: dict[str, Developer] = {}
    commits: list[Commit] = []
    for i, (_, items) in enumerate(ordered, start=1):
        dev_id = f"d{i}"
        names = sorted({c.name for c, _, _ in items}, key=lambda n: -sum(c.name == n for c, _, _ in items))
        emails = sorted({c.email.lower() for c, _, _ in items})
        developers[dev_id] = Developer(dev_id, names, emails, len(items), max(c.date for c, _, _ in items))
        for c, touched, added in items:
            commits.append(Commit(c.sha, dev_id, c.date, c.subject, sorted(set(touched)), sorted(set(added))))
    commits.sort(key=lambda c: c.date)

    return History(
        repo_name=_repo_name(repo),
        head=head,
        head_date=datetime.fromtimestamp(int(head_ts), timezone.utc),
        head_files=head_files,
        commits=commits,
        developers=developers,
    )


def mine(repo: Path, bulk_limit: int = 100) -> list[Commit]:
    """Filtered history, oldest first. Avi uses it for per-file commit messages and risky commits."""
    return mine_repo(repo, bulk_limit).commits
