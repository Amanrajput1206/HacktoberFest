import json

from handoff import llm
from handoff.impact import run_analysis
from tests.test_pack import CORE


def test_generate_kt_validates_renders_and_caches(repo, tmp_path, monkeypatch):
    repo.commit("Alice", {"pkg/__init__.py": "", "pkg/core.py": CORE}, msg="Add core")
    repo.commit("Alice", {"pkg/core.py": CORE + "# v2\n"}, msg="Fix handshake retry")
    analysis = run_analysis(repo.path, who="Alice")
    analysis["repo"] = "owner/name"
    module = next(m for m in analysis["modules"] if m["path"] == "pkg/")
    module["files"] = [f for f in module["files"] if f["path"] == "pkg/core.py"]
    analysis["modules"] = [module]

    calls = []

    def fake_chat(system, user, model):
        calls.append(system)
        if system == llm.MAP_SYSTEM:
            return {
                "purpose": "Opens connections.",
                "key_entry_points": [{"name": "connect", "cite": "pkg/core.py:5"}],
                "invariants": [{"claim": "made up", "cite": "pkg/core.py:999"}],
                "gotchas": [{"claim": "retry once on handshake", "cite": "pkg/core.py:7"}],
                "open_questions": ["Why retry only once?"],
            }
        return {"summary": "Connection handling.", "questions": ["Why retry only once?"], "tasks": ["Document connect"]}

    monkeypatch.setattr(llm, "chat_json", fake_chat)
    out = tmp_path / "out"
    md_path = llm.generate_kt(repo.path, analysis, "d1", out, model="fake")

    kt = json.loads((out / "kt.json").read_text(encoding="utf-8"))
    assert kt["dev"] == "d1" and kt["prompt_version"] == llm.PROMPT_VERSION
    assert (kt["claims_verified"], kt["claims_total"]) == (2, 3)
    md = md_path.read_text(encoding="utf-8")
    assert "made up" not in md
    assert f"https://github.com/owner/name/blob/{analysis['head']}/pkg/core.py#L7" in md
    assert "- [ ] Why retry only once?" in md
    assert kt["markdown"] == md and [m["path"] for m in kt["modules"]] == [m["path"] for m in analysis["modules"]]

    n_calls = len(calls)
    seen = []
    llm.generate_kt(repo.path, analysis, "d1", out, model="fake", progress=lambda *a: seen.append(a))
    assert len(calls) == n_calls  # second run comes entirely from the cache
    assert seen == [(1, 1, "pkg/core.py")]
