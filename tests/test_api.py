import json

from fastapi.testclient import TestClient

from handoff.api import create_app


def test_serves_cached_files(tmp_path):
    (tmp_path / "analysis.json").write_text(json.dumps({"repo": "x/y"}), encoding="utf-8")
    (tmp_path / "kt.json").write_text(json.dumps({"dev": "d1", "markdown": "# KT"}), encoding="utf-8")
    client = TestClient(create_app(tmp_path))

    assert client.get("/api/analysis").json() == {"repo": "x/y"}
    assert client.get("/api/kt/d1").json()["markdown"] == "# KT"
    assert client.get("/api/kt/d2").status_code == 404
    assert client.get("/").status_code == 200


def test_missing_analysis_is_404(tmp_path):
    assert TestClient(create_app(tmp_path)).get("/api/analysis").status_code == 404
