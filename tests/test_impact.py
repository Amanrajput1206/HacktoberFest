import pytest

from handoff.impact import python_fan_in, run_analysis, truck_factor
from tests.conftest import body


def test_truck_factor_greedy():
    authors = {"a": {"d1"}, "b": {"d1"}, "c": {"d1"}, "d": {"d2"}, "e": {"d2", "d3"}}
    # Remove d1 -> 2/5 covered (< 50%), so TF = 1.
    assert truck_factor(authors) == (1, ["d1"])
    # Without d1, remove d2 -> only "e" via d3 covered = 20%.
    assert truck_factor(authors, excluded={"d1"}) == (0, [])


def test_departure_impact(repo):
    repo.commit("Alice", {"core/engine.py": body(0), "core/util.py": body(0)})
    repo.commit("Bob", {"api/routes.py": "from core.engine import run\n"})
    repo.commit("Bob", {"core/util.py": body(1)})
    repo.commit("Bob", {"core/util.py": body(2)})
    repo.commit("Bob", {"core/util.py": body(3)})
    result = run_analysis(repo.path, who="alice")

    devs = {d["names"][0]: d["id"] for d in result["developers"]}
    dep = result["departure"]
    assert dep["dev"] == devs["Alice"]
    assert dep["orphaned_files"] == 1          # core/engine.py
    assert dep["coverage_after"] < dep["coverage_before"]

    core = next(m for m in result["modules"] if m["path"] == "core/")
    engine = next(f for f in core["files"] if f["path"] == "core/engine.py")
    assert engine["orphaned"] and engine["fan_in"] == 1
    assert core["successor"]["dev"] == devs["Bob"]


def test_who_must_match(repo):
    repo.commit("Alice", {"a.py": body(0)})
    with pytest.raises(ValueError):
        run_analysis(repo.path, who="nobody")


def test_fan_in_relative_imports(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text("from .core import x\n")
    (tmp_path / "pkg/core.py").write_text("x = 1\n")
    (tmp_path / "pkg/api.py").write_text("from . import core\nimport pkg.core\n")
    files = ["pkg/__init__.py", "pkg/core.py", "pkg/api.py"]
    assert python_fan_in(tmp_path, files)["pkg/core.py"] == 2


def test_successor_window_uses_head_date(repo):
    # All commits are from 2023, years before "today". Relative to HEAD, Bob is still active.
    for i in range(3):
        repo.commit("Alice", {"m/x.py": body(i)})
    repo.commit("Bob", {"m/x.py": body(10)})
    result = run_analysis(repo.path, who="Alice")
    names = {d["id"]: d["names"][0] for d in result["developers"]}
    assert names[result["modules"][0]["successor"]["dev"]] == "Bob"


def test_stale_developer_is_not_a_successor(repo):
    for i in range(3):
        repo.commit("Alice", {"m/x.py": body(i)})
    repo.commit("Bob", {"m/x.py": body(10)})
    repo.commit("Alice", {"other.py": body(0)}, days=800)  # moves HEAD 800 days past Bob's last commit
    result = run_analysis(repo.path, who="Alice")
    module = next(m for m in result["modules"] if m["path"] == "m/")
    assert module["successor"] is None
