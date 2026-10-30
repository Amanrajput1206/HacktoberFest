from handoff.miner import mine
from handoff.pack import build_pack, outline, render_file

CORE = '''"""Core module."""
import os


def connect(host):
    """Open a connection."""
    # HACK: retry once because the server drops the first handshake
    return host


class Pool:
    """A pool."""

    def get(self):
        return 1
'''


def _analysis(path):
    return {
        "developers": [
            {"id": "d1", "names": ["Alice"], "emails": ["alice@example.com"]},
            {"id": "d2", "names": ["Bob"], "emails": ["bob@example.com"]},
        ],
        "modules": [{"path": "pkg/", "files": [{"path": path}]}],
    }


def _setup(repo):
    repo.commit("Alice", {"pkg/__init__.py": "", "pkg/core.py": CORE}, msg="Add core")
    repo.commit("Alice", {"pkg/core.py": CORE + "# v2\n"}, msg="Fix handshake retry")
    repo.commit("Alice", {"pkg/core.py": CORE + "# v3\n"}, msg="Fix handshake retry")  # duplicate subject
    repo.commit("Bob", {"pkg/api.py": "from .core import connect\n", "tests/test_core.py": "from pkg.core import Pool\n"},
                msg="Add api")
    commits = mine(repo.path)
    analysis = _analysis("pkg/core.py")
    return build_pack(repo.path, analysis, "d1", analysis["modules"][0], commits)["files"][0]


def test_outline_has_signatures_with_line_numbers(repo):
    repo.commit("Alice", {"core.py": CORE})
    chunks = outline(repo.path, "core.py")
    text = "\n".join(c["text"] for c in chunks)
    assert "5: def connect(host):" in text
    assert "11: class Pool:" in text and "14:     def get(self):" in text
    assert "return host" not in text  # bodies are left out


def test_pack_collects_evidence(repo):
    fp = _setup(repo)
    assert [m["line"] for m in fp["markers"]] == [7]
    assert [c["msg"] for c in fp["commits"]] == ["Fix handshake retry", "Add core"]  # newest first, deduped
    assert [c["msg"] for c in fp["risky_commits"]] == ["Fix handshake retry"] * 2
    assert fp["callers"] == [{"path": "pkg/api.py", "line": 1, "text": "from .core import connect"}]
    assert fp["tests"] == ["tests/test_core.py"]


def test_markers_only_from_departing_dev(repo):
    repo.commit("Bob", {"pkg/__init__.py": "", "pkg/core.py": CORE}, msg="Add core")
    repo.commit("Bob", {"pkg/core.py": CORE + "# v2\n"})
    repo.commit("Alice", {"pkg/other.py": "x = 1\n"})
    commits = mine(repo.path)  # Bob has more commits, so Bob is d1 and Alice is d2
    analysis = _analysis("pkg/core.py")
    analysis["developers"].reverse()
    analysis["developers"][0]["id"], analysis["developers"][1]["id"] = "d1", "d2"
    fp = build_pack(repo.path, analysis, "d2", analysis["modules"][0], commits)["files"][0]
    assert fp["markers"] == [] and fp["commits"] == []


def test_render_carries_cites(repo):
    text = render_file(_setup(repo))
    assert "pkg/core.py:7: # HACK" in text
    assert "pkg/api.py:1: from .core import connect" in text
    assert "commit:" in text
