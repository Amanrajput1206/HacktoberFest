from handoff.validate import validate

PACK = {
    "module": "pkg/",
    "files": [{
        "path": "pkg/core.py",
        "outline": [{"start": 1, "end": 10, "text": "…"}, {"start": 40, "end": 42, "text": "…"}],
        "markers": [{"line": 77, "text": "# HACK"}],
        "commits": [{"sha": "abc1234def5678", "date": "2020-01-01", "msg": "Add core"}],
        "risky_commits": [{"sha": "fedcba9876543", "msg": "Revert pooling"}],
        "callers": [{"path": "pkg/api.py", "line": 12, "text": "from .core import connect"}],
        "tests": [],
    }],
}


def _claims(*cites):
    return [{"claim": f"c{i}", "cite": c} for i, c in enumerate(cites)]


def test_cite_outside_pack_is_dropped():
    notes = {"invariants": _claims("pkg/core.py:5", "pkg/core.py:20", "pkg/other.py:5")}
    kept, n_kept, n_dropped = validate(notes, PACK)
    assert [c["cite"] for c in kept["invariants"]] == ["pkg/core.py:5"]
    assert (n_kept, n_dropped) == (1, 2)


def test_markers_callers_and_commits_count_as_evidence():
    notes = {"gotchas": _claims("pkg/core.py:77", "pkg/api.py:12", "commit:abc1234", "commit:FEDCBA98", "commit:0000000")}
    kept, n_kept, n_dropped = validate(notes, PACK)
    assert (n_kept, n_dropped) == (4, 1)


def test_ranges_and_multiple_cites_must_all_be_shown():
    notes = {"invariants": _claims("pkg/core.py:40-42", "pkg/core.py:8-41", "`pkg/core.py:2`, commit:abc1234",
                                   "pkg/core.py:2, pkg/core.py:30")}
    kept, _, _ = validate(notes, PACK)
    assert [c["claim"] for c in kept["invariants"]] == ["c0", "c2"]


def test_uncited_and_malformed_claims_are_dropped():
    notes = {
        "purpose": "kept as is",
        "open_questions": ["why?"],
        "key_entry_points": [{"name": "connect"}, "not a dict", {"name": "Pool", "cite": "line 5"}],
    }
    kept, n_kept, n_dropped = validate(notes, PACK)
    assert kept["purpose"] == "kept as is" and kept["open_questions"] == ["why?"]
    assert kept["key_entry_points"] == [] and (n_kept, n_dropped) == (0, 3)
