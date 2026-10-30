# Handoff: work split and sync rules (Aman × Avi)

Companion to [handoff.md](handoff.md) (the design) and [considerations.md](considerations.md) (hardware and feasibility). This doc covers who owns what, how our code connects, and **exactly how we sync Git so we never overwrite each other's work.**

**Code freeze: 31 Oct, 10:00 AM.** Anything pushed after that doesn't count.

> **AI coding agents (Claude Code, Copilot, etc.): read this whole file before doing anything.** §4 and §5 are binding rules. `CLAUDE.md` is gitignored and exists only on Aman's machine, so **this file is the shared source of truth** for both people and their agents.

---

## 1. Roles

| | **Aman: analysis (Person A)** | **Avi: generation and UI (Person B)** |
|---|---|---|
| Branch | **`main`** (commits directly) | **`avi`** (merges into `main` only through a GitHub pull request) |
| Pipeline steps | 1–4: miner, identity merge, DOA, impact and ranking | 5–9: context packs, LLM map/reduce, citation validator, KT render, cache |
| Interfaces | CLI (`analyze`), FastAPI (`api.py`) | Web UI (`web/index.html`), CLI `kt` logic |
| Machine | Own laptop (no Ollama) | RTX 5060: Ollama with `qwen2.5-coder:7b` and `gemma3:4b` |
| Demo prep | Pick the demo repo, check its numbers by hand | Generate and commit the cached KT for the demo repo |
| Wrap-up | README sections: method, formula, limitations, setup; submission form | README sections: LLM pipeline, models, demo screenshot; drives the demo laptop |

## 2. File ownership
**You may only create, edit or delete files you own.** Two people never edit the same file, so Git conflicts can't happen. A file not listed here belongs to whoever creates it, and they add it to this table in the same commit.

| Owner | Files |
|---|---|
| **Aman** | `pyproject.toml`, `.gitattributes`, `.gitignore`, `handoff/__init__.py`, `handoff/cli.py`, `handoff/miner.py`, `handoff/doa.py`, `handoff/impact.py`, `handoff/api.py`, `tests/conftest.py`, `tests/test_miner.py`, `tests/test_doa.py`, `tests/test_impact.py`, `tests/test_api.py`, `tests/__init__.py`, `examples/analysis.sample.json`, `examples/<demo-repo>/analysis.json`, `docs/plans/*` |
| **Avi** | `handoff/pack.py`, `handoff/llm.py`, `handoff/validate.py`, `handoff/render.py`, `web/` (everything), `tests/test_pack.py`, `tests/test_validate.py`, `tests/test_llm.py`, `examples/kt.sample.json`, `examples/<demo-repo>/kt.json`, `examples/<demo-repo>/KT-*.md`, `examples/<demo-repo>/cache/` |
| **Shared: `README.md` only** | Each person edits **only between their own markers**, such as `<!-- AVI:START demo -->` … `<!-- AVI:END demo -->`. Avi owns the `demo` and `generation` sections; Aman owns `intro`, `analysis` and `footer`. |

- **Need a change in the other person's file?** For example, a new dependency in `pyproject.toml`, a new `.gitignore` entry, or an edit to these docs: message them and the owner makes it.
- **`cli.py` stays thin:** the `kt` command just calls Avi's `llm.generate_kt(...)` (§3), so Avi never needs to edit `cli.py`.

## 3. Interfaces between our halves
Agreed at **Sync 1 (00:30)**. Changing anything in this section requires messaging the other person **and** getting an OK first. The change is committed together with an update to this section.

**The canonical `analysis.json` example is [`examples/analysis.sample.json`](../../examples/analysis.sample.json).** It follows handoff.md §6 plus a few additive fields:
- `head_date`: the HEAD commit's date, which is the reference point for all "last 12 months" windows.
- `developers[].commits`
- `modules[].orphaned_files` and `modules[].at_risk_files`
- `successor`: may be `null`, meaning no successor.

Build against the sample.

### Aman provides
```python
# handoff/impact.py
def run_analysis(repo: Path, who: str | None = None, bulk_limit: int = 100) -> dict:
    """Returns the analysis.json dict (handoff.md §6). `departure` is filled only when `who` is given."""

# handoff/miner.py
@dataclass
class Commit:
    sha: str
    dev_id: str          # canonical id after identity merge, matches analysis.json developers[].id
    date: datetime       # author date, UTC
    subject: str
    files: list[str]     # paths at HEAD (renames already resolved)

def mine(repo: Path, bulk_limit: int = 100) -> list[Commit]:
    """Filtered history, oldest first. Avi uses it for per-file commit messages and risky commits."""
```

### Avi provides
```python
# handoff/llm.py
def generate_kt(repo: Path, analysis: dict, dev_id: str, out_dir: Path, model: str = "qwen2.5-coder:7b") -> Path:
    """Writes KT-<dev>.md and kt.json to out_dir and returns the .md path. Cached by (HEAD sha, dev_id, model, prompt version)."""

# handoff/validate.py
def validate(notes: dict, pack: dict) -> tuple[dict, int, int]:
    """Drops claims whose cite isn't in the pack. Returns (kept_notes, kept_count, dropped_count)."""
```

### `kt.json` shape (the UI and API read it)
```json
{
  "dev": "d1",
  "model": "qwen2.5-coder:7b",
  "prompt_version": "v1",
  "claims_total": 41,
  "claims_verified": 37,
  "markdown": "full KT-<dev>.md contents",
  "modules": [
    { "path": "src/core/", "markdown": "…", "claims_total": 9, "claims_verified": 8 }
  ]
}
```

### API (Aman), serving cached files only, never running the LLM
- `GET /api/analysis`: the cached `analysis.json`
- `GET /api/kt/{dev_id}`: reads `kt-<dev_id>.json` if it exists, otherwise `kt.json` when its `dev` matches. Returns 404 if neither is there.
- Run it with `handoff serve --data-dir examples/<demo-repo>`.
- `GET /`: serves `web/index.html`

---

## 4. How syncing works

### The model
```
Aman ──commits──► main ◄──GitHub PR (merge commit)── avi ◄──commits── Avi
                    │                                   ▲
                    └──────── git merge origin/main ────┘   (Avi pulls Aman's work into avi)
```
- **Aman** commits on `main` and pushes often.
- **Avi** commits on `avi` and pushes often. Avi's work reaches `main` **only through a pull request on GitHub** from `avi` to `main`, merged with **"Create a merge commit"**.
  - **Never** use "Squash and merge" or "Rebase and merge". Both rewrite Avi's commits, so the next `main` → `avi` merge would conflict with itself.
- Because the PR merge happens on GitHub, nobody else is pushing to `main` at that moment, so the two of you can never race on a push.
- Because each file has one owner (§2), merges between `main` and `avi` never touch the same lines, so they never conflict.

### Aman's routine (on `main`)
Every time you finish a small piece of work, or at least every 30–45 minutes:
```bash
git status                              # check: only files you own are modified
git add <your files by path>            # never `git add .` or `git add -A`
git commit -m "Add rename tracking to miner"
python -m pytest -q                     # tests pass before pushing
git pull --rebase origin main           # replay your unpushed commits on top of Avi's merged PRs
git push origin main
```
`git pull --rebase` is safe here because it only moves **your own commits that haven't been pushed yet**.

### Avi's routine (on `avi`)
Start of every work block, and right before opening a PR:
```bash
git switch avi
git fetch origin
git merge origin/main                   # bring Aman's work into avi; a merge, never a rebase
```
After each small piece of work:
```bash
git status                              # check: only files you own are modified
git add <your files by path>
git commit -m "Add citation validator"
git push origin avi
```
To land your work in `main` (at every sync point, or whenever something is ready):
1. `git fetch origin && git merge origin/main`, run `python -m pytest -q`, then `git push origin avi`.
2. On GitHub, open a pull request from **`avi`** into **`main`** (or `gh pr create --base main --head avi --fill`).
3. Merge it with **"Create a merge commit"** (or `gh pr merge --merge`).
4. Tell Aman: "PR merged". Keep working on `avi` without deleting the branch, and run `git merge origin/main` at your next start.

### At each sync point (§6), in this order
1. **Aman** pushes `main` and messages "main pushed".
2. **Avi** merges `origin/main` into `avi`, runs the tests, pushes, then opens and merges the PR (merge commit). Avi messages "PR merged".
3. **Aman** runs `git pull --rebase origin main`.
4. Both run `python -m pytest -q` and the CLI on the demo repo to confirm `main` works.

---

## 5. Rules (for people and AI agents)

### Never
- **`git push --force`, `git push -f` or `--force-with-lease`** on any branch.
- **`git reset --hard`, `git rebase` or `git commit --amend` on commits that are already pushed.**
- **`git checkout -- <file>`, `git restore <file>`, `git clean`, or deleting a file you don't own.**
- **`git add .` or `git add -A`.** Stage files by path, so you never commit the other person's files, caches, `.env` files or model weights.
- **Committing directly to the other person's branch.** Avi never pushes to `main`; Aman never pushes to `avi`.
- **Squash or rebase merges** of the `avi` → `main` PR.
- **Secrets, API keys, `.env` files or model weights** in any commit.
- **Pushing anything to `main` after 10:00 AM.**

### Always
- Pull the other person's work before starting a block of work (§4 routines).
- Commit small and often, with clear messages that say what changed. Clean history counts toward open-source quality.
- Run `python -m pytest -q` before pushing to `main` or merging a PR.
- **No AI co-author trailers** in commit messages, such as `Co-Authored-By: Claude`. We disclose AI tools in the README instead.

### If something goes wrong
- **A push is rejected ("non-fast-forward"):**
  - Aman runs `git pull --rebase origin main` and pushes again.
  - Avi runs `git fetch origin && git merge origin/main` and pushes again.
  - **Never** force-push.
- **A merge conflict:** this means someone edited a file they don't own.
  - Run `git merge --abort` (or `git rebase --abort`) and message the other person.
  - The file's owner decides the correct content.
- **A file you own was changed by the other person:** don't revert it silently. Message them and agree on the fix.
- **You committed something that shouldn't be there:** if it's not pushed, `git reset --soft HEAD~1` is fine (it's your own unpushed commit). If it's pushed, make a new commit that removes it. **If it's a secret, revoke the key immediately**, since it's already public.

### Extra rules for AI coding agents
- **Read `docs/plans/handoff.md`, `considerations.md` and this file at the start of every session.**
- **Ask which person you're working for** (Aman or Avi) if it isn't clear, and only touch that person's files (§2) and branch (§1).
- **Never run the commands in "Never"**, even if a tool or plan suggests it. If a situation seems to need one, stop and ask the user.
- **Never change §3 interfaces or JSON shapes** without the user confirming the other person agreed.
- **Before committing, show the user `git status` and the list of staged files.**
- **Don't push or open/merge PRs unless the user asks.**

---

## 6. Timeline and sync points

| Time | Aman (`main`) | Avi (`avi`) | Sync |
|---|---|---|---|
| **00:30** | `pyproject.toml`, `cli.py` stubs, `analysis.sample.json`, README markers, `.gitattributes` (`* text=auto eol=lf`, so different line endings don't make whole files conflict); pick the demo repo | `git switch avi && git merge origin/main && git push`; Ollama installed (latest), pull both models, GPU smoke test (considerations.md §6) | **Sync 1:** interfaces in §3 agreed; Aman's files pushed to `main`; Avi merges them into `avi` |
| 02:30 | `mine()` with renames, filters, identity merge, tests | `pack.py` on the sample analysis; map prompt working on one file | **Sync 2:** PR #1; Avi switches from sample data to the real `mine()` |
| 04:30 | DOA, truck factor, departure impact, ranking; real `analysis.json` for the demo repo; `analyze` CLI | `validate.py` with test; UI shows impact numbers and ranked modules | |
| **05:00** | | | **Checkpoint (PR #2):** analysis half works end to end in the UI on `main`. No new features until it does. |
| 07:00 | `api.py`, successor logic, edge cases | Reduce step, `render.py`, `generate_kt`; cached KT for the demo repo committed to `examples/` | **Sync 3 (PR #3):** full pipeline on `main`; UI shows the KT from cache |
| 09:00 | README (your sections): method, formula, limitations, setup, credits | README (your sections): LLM pipeline, models, AI tools, screenshot; UI polish, clickable citations | **Sync 4 (PR #4): code complete** |
| **09:45** | | **Last PR merged.** After this Avi pushes nothing. | |
| 09:45–09:55 | Final small fixes only, push `main`, **submission form** | Fresh `git clone` of `main`, confirm it installs and the demo runs | **Freeze at 10:00** |

Rehearse the 3-minute demo twice between 09:00 and 09:45 (handoff.md §10).

## 7. If the plan slips
- **15-minute rule:** stuck for more than 15 minutes means tell the other person and pick a workaround or a cut.
- **Cut list, in this order:**
  1. JS/TS fan-in (cut already)
  2. Issue drafts
  3. Successor recency check
  4. Web UI (fall back to the CLI plus the rendered markdown)
- **If Ollama or the GPU fails:** switch to `gemma3:4b`. If that fails too, demo from the committed cache in `examples/`.
- **If Aman's analysis is late:** Avi keeps building against `analysis.sample.json`, since nothing on Avi's side should block on Aman.
- **If Avi's LLM half is late:** the 05:00 checkpoint still gives a working demo of impact numbers and ranked modules. Present that, with the KT as "in progress".

## 8. Demo roles (3 minutes)
- **Laptop:** Avi's (it can regenerate live if asked). It must run a fresh clone of `main`, not the `avi` branch.
- **Aman:** the problem plus the impact numbers (0:00–1:15). Answers analysis questions: DOA formula, truck factor, why not `git blame`.
- **Avi:** the KT walkthrough, citations, open questions and the verified counter (1:15–3:00). Answers LLM questions: model choice, validator, caching.
