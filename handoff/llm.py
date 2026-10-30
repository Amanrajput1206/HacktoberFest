"""Steps 6 and 8: per-file notes (map) and per-module KT sections (reduce) with a local Ollama model.

The model only explains evidence we chose deterministically. Every claim must
cite that evidence; the validator (step 7) drops the ones that don't.
"""

import json

PROMPT_VERSION = "v1"
DEFAULT_MODEL = "qwen2.5-coder:7b"
OPTIONS = {"num_ctx": 8192, "temperature": 0.2}  # Ollama's default context silently truncates long packs

MAP_SYSTEM = """You help a departing open-source maintainer hand over their code.
You get EVIDENCE about one file: a code outline with line numbers, comments, commits and callers.
Return JSON only, exactly this shape:
{
  "purpose": "one paragraph: what this file is for and how it fits the project",
  "key_entry_points": [{"name": "function or class", "cite": "path:line"}],
  "invariants": [{"claim": "something a contributor must not break", "cite": "path:line"}],
  "gotchas": [{"claim": "a landmine, workaround or past breakage", "cite": "path:line or commit:sha"}],
  "open_questions": ["a question only the maintainer can answer"]
}
Rules:
- Use ONLY the evidence. Do not use outside knowledge about this project.
- Every cite must be copied from the evidence: a "path:line" whose line number is shown, or a "commit:sha" that is listed.
- One cite per claim. No claim without a cite.
- If the reason behind something is not in the evidence, ask about it in open_questions instead of guessing.
- 3-6 entry points, up to 5 invariants, up to 5 gotchas, 2-5 open questions. Short, specific sentences."""


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


def map_file(evidence: str, dev_name: str, model: str = DEFAULT_MODEL) -> dict:
    """Per-file notes in the map JSON shape. Missing keys come back empty, never absent."""
    user = f"The departing maintainer is {dev_name}.\n\nEVIDENCE\n{evidence}"
    notes = chat_json(MAP_SYSTEM, user, model)
    return {
        "purpose": str(notes.get("purpose") or ""),
        "key_entry_points": list(notes.get("key_entry_points") or []),
        "invariants": list(notes.get("invariants") or []),
        "gotchas": list(notes.get("gotchas") or []),
        "open_questions": [str(q) for q in notes.get("open_questions") or []],
    }
