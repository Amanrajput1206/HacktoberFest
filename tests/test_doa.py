from handoff.doa import compute, doa
from handoff.miner import mine_repo
from tests.conftest import body


def authors_of(repo, path):
    h = mine_repo(repo.path)
    auth = compute(h.commits, h.head_files)
    return {h.developers[d].names[0] for d in auth.authors[path]}, auth, h


def test_formula():
    assert doa(0, 0, 0) == 3.293
    assert round(doa(1, 5, 0), 3) == round(3.293 + 1.098 + 0.82, 3)


def test_sole_creator_is_only_author(repo):
    for i in range(5):
        repo.commit("Alice", {"x.py": body(i)})
    names, _, _ = authors_of(repo, "x.py")
    assert names == {"Alice"}


def test_overwritten_creator_loses_authorship(repo):
    repo.commit("Alice", {"x.py": body(0)})
    for i in range(1, 16):
        repo.commit("Bob", {"x.py": body(i)})
    names, _, _ = authors_of(repo, "x.py")
    assert names == {"Bob"}


def test_two_close_experts_both_author(repo):
    # Alice: FA=1, DL=4, AC=6 -> 4.42.  Bob: DL=6, AC=4 -> 3.76 (DOA_N 0.85).
    repo.commit("Alice", {"x.py": body(0)})
    for i in range(1, 4):
        repo.commit("Alice", {"x.py": body(i)})
    for i in range(6):
        repo.commit("Bob", {"x.py": body(i + 100)})
    names, _, _ = authors_of(repo, "x.py")
    assert names == {"Alice", "Bob"}


def test_negative_max_does_not_flip_signs():
    from handoff.miner import Commit
    from datetime import datetime, timezone

    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # 60k single-commit developers: 0.321*ln(60k) > 3.457, so everyone's DOA is negative.
    commits = [Commit(f"s{i}", f"d{i}", t, "m", ["x.py"]) for i in range(1, 60_000)]
    auth = compute(commits, ["x.py"])
    assert max(auth.doa["x.py"].values()) <= 0
    assert auth.authors["x.py"] == set()
    assert all(v == 0.0 for v in auth.doa_n["x.py"].values())
