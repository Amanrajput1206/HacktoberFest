"""Steps 6 and 8: per-file notes (map) and per-module KT sections (reduce) with a local Ollama model.

The model only explains evidence we chose deterministically. Every claim must
cite that evidence; the validator (step 7) drops the ones that don't.
"""

import hashlib
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from handoff import render
from handoff.miner import git, mine
from handoff.pack import build_pack, render_file
from handoff.validate import validate

PROMPT_VERSION = "v1"
DEFAULT_MODEL = "qwen2.5-coder:7b"
OPTIONS = {"num_ctx": 8192, "temperature": 0.2}  # Ollama's default context silently truncates long packs

MAP_SYSTEM = """You help a departing open-source maintainer hand over their code.
You get EVIDENCE about one file: a code outline with line numbers, comments, commits and callers.
Return JSON only, exactly this shape:
{
  "purpose": "one paragraph: what this file is for and how it fits the project",
  "key_entry_points": [{"name": "function or class", "cite": "<file path>:<line>"}],
  "invariants": [{"claim": "something a contributor must not break", "cite": "<file path>:<line>"}],
  "gotchas": [{"claim": "a landmine, workaround or past breakage", "cite": "commit:<sha> or <file path>:<line>"}],
  "open_questions": ["a question only the maintainer can answer"]
}
Rules:
- Use ONLY the evidence. Do not use outside knowledge about this project.
- A cite is the full file path, a colon and a line number shown in the evidence (for example "src/app/core.py:42"),
  or "commit:" followed by a sha listed in the evidence (for example "commit:1a2b3c4d5e").
- Exactly one cite per claim. No claim without a cite.
- Gotchas come from the TODO/FIXME/HACK comments and the fix / revert / workaround commits: say what broke or what to watch for.
- If the reason behind something is not in the evidence, ask about it in open_questions instead of guessing.
- 3-6 entry points, up to 5 invariants, up to 5 gotchas, 2-5 open questions. Short, specific sentences."""

REDUCE_SYSTEM = """You write one module's section of a knowledge-transfer plan for a departing open-source maintainer.
You get verified notes about each file in the module. Return JSON only, exactly this shape:
{
  "summary": "one paragraph: what the module does and how its files fit together",
  "questions": ["the most important questions to ask the maintainer before they leave"],
  "tasks": ["concrete handoff tasks: a doc to write, a test to add, a pairing session on a named file"]
}
Rules:
- Use ONLY the notes. Do not invent features, history or file names.
- 3-6 questions: merge duplicates, keep the ones about intent, history and risk.
- 3-5 tasks, each naming a specific file or function from the notes."""


def _client():
    import ollama

    return ollama


def chat_json(system: str, user: str, model: str = DEFAULT_MODEL) -> dict:
    resp = _client().chat(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        format="json",
        options=OPTIONS,
    )
    content = resp["message"]["content"]
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def map_file(evidence: str, dev_name: str, model: str = DEFAULT_MODEL, path: str = "") -> dict:
    """Per-file notes in the map JSON shape. Missing keys come back empty, never absent."""
    how = f'Cite lines of this file as "{path}:<line>", for example "{path}:12".\n' if path else ""
    user = f"The departing maintainer is {dev_name}.\n{how}\nEVIDENCE\n{evidence}"
    notes = chat_json(MAP_SYSTEM, user, model) or chat_json(MAP_SYSTEM, user, model)  # one retry on bad JSON
    return {
        "purpose": str(notes.get("purpose") or ""),
        "key_entry_points": list(notes.get("key_entry_points") or []),
        "invariants": list(notes.get("invariants") or []),
        "gotchas": list(notes.get("gotchas") or []),
        "open_questions": [str(q) for q in notes.get("open_questions") or []],
    }


def reduce_module(module: dict, notes: list[dict], dev_name: str, successor: str, model: str = DEFAULT_MODEL) -> dict:
    lines = [
        f"Module: {module['path']}",
        f"Departing maintainer: {dev_name}. Suggested successor: {successor}.",
        f"{module.get('orphaned_files', 0)} files would have no remaining expert.",
    ]
    for n in notes:
        lines.append(f"\nFILE {n['path']}\nPurpose: {n['purpose']}")
        lines += [f"Entry point: {i.get('name')}" for i in n["key_entry_points"]]
        lines += [f"Invariant: {i.get('claim')}" for i in n["invariants"]]
        lines += [f"Gotcha: {i.get('claim')}" for i in n["gotchas"]]
        lines += [f"Open question: {q}" for q in n["open_questions"]]
    out = chat_json(REDUCE_SYSTEM, "\n".join(lines), model)
    return {
        "summary": _text(out.get("summary")),
        "questions": [_text(q) for q in out.get("questions") or []],
        "tasks": [_text(t) for t in out.get("tasks") or []],
    }


def _text(value) -> str:
    """Small models sometimes wrap a string as {"task": "..."}; unwrap it."""
    if isinstance(value, dict):
        value = next((v for v in value.values() if isinstance(v, str)), "")
    return str(value or "").strip()


def _cached(path: Path, compute):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    value = compute()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    return value


def _slug(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def generate_kt(repo: Path, analysis: dict, dev_id: str, out_dir: Path, model: str = DEFAULT_MODEL,
                progress: Callable[[int, int, str], None] | None = None) -> Path:
    """Writes KT-<dev>.md and kt.json to out_dir and returns the .md path. Cached by (HEAD sha, dev_id, model, prompt version).

    `progress(done, total, path)` is called after each file, for UIs that show a progress bar.
    """
    repo, out_dir = Path(repo), Path(out_dir)
    head = git(repo, "rev-parse", "HEAD").strip()
    if head != analysis["head"]:
        _log(f"warning: repo HEAD {head[:8]} differs from analysis.json head {analysis['head'][:8]}")
    cache = out_dir / "cache" / _slug(f"{head}|{dev_id}|{model}|{PROMPT_VERSION}")
    dev_name = render._name(analysis, dev_id)
    modules = analysis["modules"]
    top_risk = max((m["risk"] for m in modules), default=0)

    # The cache holds raw model output; validation always reruns, so validator fixes need no new LLM calls.
    commits = mine(repo)
    sections, kt_modules = [], []
    total = verified = 0
    n_files, done = sum(len(m["files"]) for m in modules), 0
    for module in modules:
        file_notes, m_total, m_verified = [], 0, 0
        pack = build_pack(repo, analysis, dev_id, module, commits)
        for fp in pack["files"]:
            def compute_map():
                t = time.time()
                notes = map_file(render_file(fp), dev_name, model, fp["path"])
                _log(f"  map {fp['path']} ({time.time() - t:.0f}s)")
                return {"path": fp["path"], "raw": notes}

            raw = _cached(cache / f"map-{_slug(fp['path'])}.json", compute_map)["raw"]
            kept, n_kept, n_dropped = validate(raw, fp)
            file_notes.append({"path": fp["path"], **kept})
            m_total += n_kept + n_dropped
            m_verified += n_kept
            done += 1
            if progress:
                progress(done, n_files, fp["path"])

        succ = module.get("successor")
        succ_name = render._name(analysis, succ["dev"]) if succ else "none"
        reduced = _cached(cache / f"reduce-{_slug(module['path'])}.json",
                          lambda: reduce_module(module, file_notes, dev_name, succ_name, model))
        section = render.module_section(analysis, module, file_notes, reduced, dev_name, top_risk)
        sections.append(section)
        kt_modules.append({"path": module["path"], "markdown": section,
                           "claims_total": m_total, "claims_verified": m_verified})
        total += m_total
        verified += m_verified
        _log(f"{module['path']}: {m_verified}/{m_total} claims verified")

    markdown = render.kt_markdown(analysis, dev_id, sections, total, verified, model)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"KT-{dev_id}.md"
    md_path.write_text(markdown, encoding="utf-8")
    kt = {
        "dev": dev_id,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "claims_total": total,
        "claims_verified": verified,
        "markdown": markdown,
        "modules": kt_modules,
    }
    (out_dir / "kt.json").write_text(json.dumps(kt, indent=2, ensure_ascii=False), encoding="utf-8")
    return md_path
