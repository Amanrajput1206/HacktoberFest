"""Handoff Studio: run the analysis and KT generation on any repo from the browser.

A local tool for the machine that has Ollama. It is separate from `handoff serve`
(api.py), which only serves cached results and never runs anything.

    python -m handoff.studio [--runs-dir PATH] [--port 8001]

Each run lives in `<runs-dir>/<name>/`: run.json, repo/ (when cloned), analysis.json,
kt.json, KT-<dev>.md and cache/. Jobs run one at a time in a background thread,
since there is one GPU; the page polls /api/jobs/{id} for progress.
"""

import argparse
import json
import re
import subprocess
import threading
import uuid
import webbrowser
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from handoff import llm
from handoff.impact import run_analysis

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
DEMO_DIRS = {"requests-demo": ROOT / "examples" / "requests"}
DEFAULT_RUNS_DIR = Path.home() / "handoff-runs"

GITHUB_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
TOP_DEVS = 10


@dataclass
class Job:
    id: str
    kind: str
    run: str
    state: str = "running"  # running | done | error
    step: str = ""
    done: int = 0
    total: int = 0
    log: list[str] = field(default_factory=list)
    error: str | None = None
    result: dict | None = None

    def say(self, msg: str):
        self.step = msg
        self.log.append(msg)


class Source(BaseModel):
    source: str


class Who(BaseModel):
    who: str


class Model(BaseModel):
    model: str = llm.DEFAULT_MODEL


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-.")[:80] or "repo"


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def top_developers(analysis: dict) -> list[dict]:
    devs = sorted(analysis["developers"], key=lambda d: (-d["files_authored"], -d.get("commits", 0)))
    return [
        {"id": d["id"], "name": d["names"][0], "commits": d.get("commits"),
         "files_authored": d["files_authored"], "last_commit": d.get("last_commit")}
        for d in devs[:TOP_DEVS]
    ]


def create_studio(runs_dir: Path = DEFAULT_RUNS_DIR, demo_dirs: dict[str, Path] | None = None) -> FastAPI:
    runs_dir = Path(runs_dir).resolve()
    runs_dir.mkdir(parents=True, exist_ok=True)
    demos = DEMO_DIRS if demo_dirs is None else demo_dirs
    jobs: dict[str, Job] = {}
    busy = threading.Lock()
    app = FastAPI(title="Handoff Studio")

    def run_dir(name: str) -> Path:
        if name in demos:
            return demos[name]
        if not NAME_RE.match(name):
            raise HTTPException(400, "Bad run name")
        path = (runs_dir / name).resolve()
        if path.parent != runs_dir or not path.is_dir():
            raise HTTPException(404, f"No run named {name}")
        return path

    def writable(name: str) -> Path:
        if name in demos:
            raise HTTPException(403, "The demo run is read-only; start a new run to regenerate")
        return run_dir(name)

    def repo_of(path: Path) -> Path:
        meta = _read(path / "run.json") or {}
        return Path(meta.get("repo_path") or path / "repo")

    def start(kind: str, run: str, work) -> Job:
        if not busy.acquire(blocking=False):
            raise HTTPException(409, "Another job is running; wait for it to finish")
        job = Job(uuid.uuid4().hex[:12], kind, run)
        jobs[job.id] = job

        def target():
            try:
                job.result = work(job)
                job.state = "done"
            except Exception as e:  # shown in the UI instead of a stack trace
                job.error = f"{type(e).__name__}: {e}"
                job.state = "error"
                job.log.append(job.error)
            finally:
                busy.release()

        threading.Thread(target=target, daemon=True).start()
        return job

    def summary(name: str, path: Path) -> dict:
        a = _read(path / "analysis.json") or {}
        kt = _read(path / "kt.json") or {}
        meta = _read(path / "run.json") or {}
        dep = a.get("departure") or {}
        names = {d["id"]: d["names"][0] for d in a.get("developers", [])}
        has_kt = bool(kt) and kt.get("dev") == dep.get("dev")
        return {
            "name": name,
            "source": meta.get("source", a.get("repo")),
            "repo": a.get("repo"),
            "head": a.get("head"),
            "dev": dep.get("dev"),
            "dev_name": names.get(dep.get("dev")),
            "has_kt": has_kt,
            "claims_verified": kt.get("claims_verified") if has_kt else None,
            "claims_total": kt.get("claims_total") if has_kt else None,
            "readonly": name in demos,
        }

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "studio.html")

    @app.get("/api/runs")
    def list_runs():
        out = [summary(n, p) for n, p in demos.items() if (p / "analysis.json").exists()]
        mine = sorted((p for p in runs_dir.iterdir() if p.is_dir() and (p / "run.json").exists()),
                      key=lambda p: -(p / "run.json").stat().st_mtime)
        return out + [summary(p.name, p) for p in mine]

    @app.post("/api/runs")
    def new_run(body: Source):
        source = body.source.strip()
        if m := GITHUB_RE.match(source):
            name, url, local = _slug(f"{m[1]}-{m[2]}"), f"https://github.com/{m[1]}/{m[2]}.git", None
        else:
            local = Path(source).expanduser()
            if not (local.is_dir() and (local / ".git").exists()):
                raise HTTPException(400, "Use an https://github.com/<owner>/<repo> URL or a local Git repository folder")
            name, url, local = _slug(local.resolve().name), None, local.resolve()
        path = runs_dir / name
        path.mkdir(exist_ok=True)

        def work(job: Job):
            repo = local or path / "repo"
            if url and not (repo / ".git").exists():
                job.say(f"Cloning {url} (full history, so this can take a minute)…")
                out = subprocess.run(["git", "clone", "-q", url, str(repo)], capture_output=True, timeout=1800)
                if out.returncode:
                    raise RuntimeError(out.stderr.decode("utf-8", errors="replace").strip())
            (path / "run.json").write_text(json.dumps(
                {"name": name, "source": source, "repo_path": str(repo)}, indent=2), encoding="utf-8")
            job.say("Mining history and scoring authorship…")
            analysis = run_analysis(repo)
            (path / "analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
            job.say(f"Found {len(analysis['developers'])} developers")
            return {"developers": top_developers(analysis)}

        return asdict(start("create", name, work))

    @app.post("/api/runs/{name}/analyze")
    def analyze(name: str, body: Who):
        path = writable(name)

        def work(job: Job):
            job.say(f"Simulating {body.who} leaving…")
            analysis = run_analysis(repo_of(path), who=body.who)
            (path / "analysis.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
            dep = analysis["departure"]
            job.say(f"{dep['orphaned_files']} orphaned, {dep['at_risk_files']} at-risk files")
            return {"dev": dep["dev"]}

        return asdict(start("analyze", name, work))

    @app.post("/api/runs/{name}/kt")
    def kt(name: str, body: Model):
        path = writable(name)
        analysis = _read(path / "analysis.json")
        if not analysis or not analysis.get("departure"):
            raise HTTPException(400, "Pick who is leaving first")

        def work(job: Job):
            def progress(done: int, total: int, file: str):
                job.done, job.total = done, total
                job.say(f"{done} / {total} files · {file}")

            job.total = sum(len(m["files"]) for m in analysis["modules"])
            job.say(f"Writing notes for {job.total} files with {body.model}…")
            llm.generate_kt(repo_of(path), analysis, analysis["departure"]["dev"], path, body.model, progress=progress)
            done = _read(path / "kt.json")
            job.say(f"Done: {done['claims_verified']} / {done['claims_total']} claims verified")
            return {"claims_verified": done["claims_verified"], "claims_total": done["claims_total"]}

        return asdict(start("kt", name, work))

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        if job_id not in jobs:
            raise HTTPException(404, "No such job")
        return asdict(jobs[job_id])

    @app.get("/api/runs/{name}/analysis")
    def get_analysis(name: str):
        data = _read(run_dir(name) / "analysis.json")
        if data is None:
            raise HTTPException(404, "No analysis yet")
        return {**data, "top_developers": top_developers(data)}

    @app.get("/api/runs/{name}/kt")
    def get_kt(name: str):
        path = run_dir(name)
        data, analysis = _read(path / "kt.json"), _read(path / "analysis.json") or {}
        if not data or data.get("dev") != (analysis.get("departure") or {}).get("dev"):
            raise HTTPException(404, "No KT generated for this departure yet")
        return data

    @app.get("/api/ollama")
    def ollama_status():
        try:
            import ollama

            resp = ollama.list()
            models = resp.get("models", []) if isinstance(resp, dict) else getattr(resp, "models", [])
            names = [m.get("model") or m.get("name") if isinstance(m, dict) else m.model for m in models]
            return {"available": True, "models": sorted(n for n in names if n), "default": llm.DEFAULT_MODEL}
        except Exception as e:
            return {"available": False, "models": [], "default": llm.DEFAULT_MODEL, "error": str(e)}

    return app


def main():
    p = argparse.ArgumentParser(description="Run Handoff on any repo from the browser.")
    p.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR, help="Where clones and results are kept.")
    p.add_argument("--port", type=int, default=8001)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()

    import uvicorn

    url = f"http://127.0.0.1:{args.port}"
    print(f"Handoff Studio on {url} (runs in {args.runs_dir})")
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, [url]).start()
    uvicorn.run(create_studio(args.runs_dir), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
