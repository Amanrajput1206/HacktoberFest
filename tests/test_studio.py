import os
import stat
import threading
import time

import pytest
from fastapi.testclient import TestClient

from handoff import llm, studio
from tests.test_pack import CORE


def _wait(client, job):
    for _ in range(300):
        state = client.get(f"/api/jobs/{job['id']}").json()
        if state["state"] != "running":
            return state
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.fixture
def client(tmp_path):
    return TestClient(studio.create_studio(tmp_path / "runs", demo_dirs={}))


def _fake_chat(system, user, model):
    if system == llm.MAP_SYSTEM:
        return {"purpose": "Core.", "key_entry_points": [{"name": "connect", "cite": "pkg/core.py:5"}],
                "invariants": [], "gotchas": [], "open_questions": ["Why?"]}
    return {"summary": "S.", "questions": ["Why?"], "tasks": ["T"]}


def test_local_repo_full_flow(client, repo, monkeypatch):
    repo.commit("Alice", {"pkg/__init__.py": "", "pkg/core.py": CORE}, msg="Add core")
    repo.commit("Alice", {"pkg/core.py": CORE + "# v2\n"}, msg="Fix retry")
    repo.commit("Bob", {"other/b.py": "x = 1\n"})

    created = _wait(client, client.post("/api/runs", json={"source": str(repo.path)}).json())
    assert created["state"] == "done", created["error"]
    assert [d["name"] for d in created["result"]["developers"]] == ["Alice", "Bob"]
    name = created["run"]

    analyzed = _wait(client, client.post(f"/api/runs/{name}/analyze", json={"who": "Alice"}).json())
    assert analyzed["state"] == "done" and analyzed["result"] == {"dev": "d1"}
    assert client.get(f"/api/runs/{name}/kt").status_code == 404

    monkeypatch.setattr(llm, "chat_json", _fake_chat)
    made = _wait(client, client.post(f"/api/runs/{name}/kt", json={"model": "fake"}).json())
    assert made["state"] == "done", made["error"]
    assert made["done"] == made["total"] > 0
    kt = client.get(f"/api/runs/{name}/kt").json()
    assert kt["dev"] == "d1" and kt["claims_verified"] >= 1

    run = next(r for r in client.get("/api/runs").json() if r["name"] == name)
    assert run["dev_name"] == "Alice" and run["has_kt"] and not run["readonly"]


def _run_with_three_files(client, repo):
    repo.commit("Alice", {"pkg/__init__.py": "", "pkg/core.py": CORE, "pkg/a.py": "x = 1\n", "pkg/b.py": "y = 2\n"})
    name = _wait(client, client.post("/api/runs", json={"source": str(repo.path)}).json())["run"]
    _wait(client, client.post(f"/api/runs/{name}/analyze", json={"who": "Alice"}).json())
    return name


def test_max_files_keeps_the_most_important_and_skips_empty(client, repo, monkeypatch):
    name = _run_with_three_files(client, repo)
    monkeypatch.setattr(llm, "chat_json", _fake_chat)
    one = _wait(client, client.post(f"/api/runs/{name}/kt", json={"model": "fake", "max_files": 1}).json())
    assert one["state"] == "done" and one["total"] == 1
    every = _wait(client, client.post(f"/api/runs/{name}/kt", json={"model": "fake", "max_files": None}).json())
    assert every["total"] == 3  # the empty __init__.py is never sent to the model


def test_kt_job_can_be_stopped_and_resumed(client, repo, monkeypatch):
    name = _run_with_three_files(client, repo)

    def slow_chat(system, user, model):
        time.sleep(0.3)
        return _fake_chat(system, user, model)

    monkeypatch.setattr(llm, "chat_json", slow_chat)
    job = client.post(f"/api/runs/{name}/kt", json={"model": "fake", "max_files": None}).json()
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
    stopped = _wait(client, job)
    assert stopped["state"] == "cancelled" and stopped["done"] == 1
    assert client.get(f"/api/runs/{name}/kt").status_code == 404  # no half-written plan
    assert client.get("/api/jobs/current").json() is None  # the lock is free again

    resumed = _wait(client, client.post(f"/api/runs/{name}/kt", json={"model": "fake", "max_files": None}).json())
    assert resumed["state"] == "done" and client.get(f"/api/runs/{name}/kt").status_code == 200


def test_delete_run_keeps_the_local_repo(client, repo, tmp_path):
    repo.commit("Alice", {"a.py": "x = 1\n"})
    name = _wait(client, client.post("/api/runs", json={"source": str(repo.path)}).json())["run"]
    run_dir = tmp_path / "runs" / name
    locked = run_dir / "repo-like.pack"
    locked.write_text("x")
    os.chmod(locked, stat.S_IREAD)  # like Git's read-only pack files on Windows

    assert client.delete(f"/api/runs/{name}").json() == {"deleted": name}
    assert not run_dir.exists()
    assert (repo.path / ".git").exists() and (repo.path / "a.py").exists()
    assert name not in [r["name"] for r in client.get("/api/runs").json()]
    assert client.delete(f"/api/runs/{name}").status_code == 404


def test_delete_is_refused_while_its_job_runs(client, repo, monkeypatch):
    repo.commit("Alice", {"a.py": "x = 1\n"})
    gate = threading.Event()
    monkeypatch.setattr(studio, "run_analysis", lambda *a, **k: gate.wait(5) and {"developers": []})
    job = client.post("/api/runs", json={"source": str(repo.path)}).json()
    assert client.delete(f"/api/runs/{job['run']}").status_code == 409
    gate.set()
    _wait(client, job)


def test_bad_sources_and_names_are_rejected(client, tmp_path):
    assert client.post("/api/runs", json={"source": "https://gitlab.com/a/b"}).status_code == 400
    assert client.post("/api/runs", json={"source": str(tmp_path)}).status_code == 400  # not a git repo
    assert client.get("/api/runs/..%2F..%2Fetc/analysis").status_code in (400, 404)
    assert client.get("/api/runs/Bad Name/analysis").status_code == 400
    assert client.post("/api/runs/nope/kt", json={}).status_code == 404


def test_one_job_at_a_time(client, repo, monkeypatch):
    repo.commit("Alice", {"a.py": "x = 1\n"})
    gate = threading.Event()
    monkeypatch.setattr(studio, "run_analysis", lambda *a, **k: gate.wait(5) and {"developers": []})
    first = client.post("/api/runs", json={"source": str(repo.path)})
    assert first.status_code == 200
    assert client.post("/api/runs", json={"source": str(repo.path)}).status_code == 409
    assert client.get("/api/jobs/current").json()["id"] == first.json()["id"]  # a reloaded page can reattach
    gate.set()
    _wait(client, first.json())
    assert client.get("/api/jobs/current").json() is None


def test_demo_run_is_read_only(tmp_path):
    demo = tmp_path / "demo"
    demo.mkdir()
    (demo / "analysis.json").write_text('{"repo": "o/r", "head": "abc", "developers": [], "departure": null}')
    c = TestClient(studio.create_studio(tmp_path / "runs", demo_dirs={"demo": demo}))
    assert [r["name"] for r in c.get("/api/runs").json()] == ["demo"]
    assert c.get("/api/runs/demo/analysis").status_code == 200
    assert c.post("/api/runs/demo/analyze", json={"who": "x"}).status_code == 403
    assert c.delete("/api/runs/demo").status_code == 403 and demo.exists()


def test_ollama_missing_is_reported(client, monkeypatch):
    import ollama

    def boom():
        raise ConnectionError("not running")

    monkeypatch.setattr(ollama, "list", boom)
    assert client.get("/api/ollama").json()["available"] is False
