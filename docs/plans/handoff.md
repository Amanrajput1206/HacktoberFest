# Handoff — Knowledge-Transfer Plans for Departing Maintainers

**Devf8rge × Hacktoberfest Hack Day 2026 · Theme: AI for Open Source**
**Hacking window:** 30 Oct 10:00 PM → 31 Oct 10:00 AM (code freeze)

---

## 1. Problem statement

> When a maintainer leaves an open-source project, the knowledge that only they held leaves with them. Contributor counts hide this. A repo can have 200 contributors and still have entire modules that exactly one person understands. Maintainers find out only after that person is gone, when a bug lands in code nobody can explain.
>
> **Handoff** answers one question: _"If this person leaves, what do we lose, and what should we do before they go?"_ It analyses Git history to find the code whose **last remaining expert** is the departing maintainer. It ranks that code by importance and generates a **cited knowledge-transfer (KT) plan**: what each module does, its invariants and landmines, who should take it over, and the questions to ask the maintainer before they leave.

### Who uses it

- **Project leads / core teams**: run it when a maintainer announces a step-back, or proactively for the top contributors.
- **Departing maintainers**: get a checklist of what to document instead of a vague "please write docs."
- **Successors**: get a starting map of unfamiliar code with links to exact lines.

### What it is not (scope guardrails)

- It does **not** measure what a person truly understands. It **estimates** knowledge concentration from authorship history, and the README says so.
- It does **not** post issues or comments to GitHub. Drafts are generated locally for a human to review, in line with the "no spam" rule.
- It does **not** use an LLM to compute risk. Risk is deterministic and reproducible; the LLM only explains and writes.

---

## 2. First principles: why this design works

**Q1. What does "knowing" a file mean, in terms we can observe?**
We can't see understanding. We _can_ see three behavioural signals in Git history:

1. **Creating the file (FA).** The creator made the original design decisions.
2. **Changing it repeatedly (DL).** Each change forces you to reload the file's logic into your head.
3. **Others changing it (AC).** Your mental model goes stale as other people rewrite the code.

These are exactly the inputs to the **Degree-of-Authorship (DOA)** model from software-engineering research (Fritz et al.; used by Avelino et al. for truck-factor estimation). We use a validated formula instead of inventing weights at 2 AM, so it holds up in Q&A.

**Q2. What does "losing" knowledge mean?**
A file is **at risk** when the departing person is one of its authors. It is **orphaned** when they are its _only_ author: nobody else has the history to understand it.

**Q3. What should the KT document actually contain?**
Not a code summary. The successor can read the code and so can an LLM. The KT must contain what the code **doesn't say**:

- **Why** decisions were made → the maintainer's commit messages.
- **Where the landmines are** → `TODO`/`FIXME`/`HACK` comments they left, and their "fix", "revert" and "workaround" commits.
- **What's untested or unexplained** → gaps the model can _point at_ but cannot _fill_. These become questions for the maintainer.

**Q4. Where does the LLM belong?**
LLMs are unreliable judges but good summarisers. So the pipeline uses:

- **deterministic code** to decide _what matters_;
- **the LLM** to explain _what it is_;
- **a validator** that rejects any claim whose `file:line` citation does not exist in the evidence we gave the model.

---

## 3. System overview

```
              ┌───────────────────────── ANALYSIS (deterministic) ─────────────────────────┐
 repo path ──►│ 1. Git miner ─► 2. Identity merge ─► 3. DOA scoring ─► 4. Departure impact │
 maintainer ─►│        + bot / bulk-commit filter         per (dev, file)    + risk ranking  │
              └───────────────────────────────────────────────┬────────────────────────────┘
                                                              │ analysis.json
              ┌──────────────────────── GENERATION (LLM, cached) ───────────────▼──────────┐
              │ 5. Context-pack builder ─► 6. Map: per-file notes ─► 7. Citation validator │
              │                            8. Reduce: module KT ─► 9. Final KT + issue drafts│
              └───────────────────────────────────────────────┬────────────────────────────┘
                                                              ▼
                               CLI (`handoff …`)  +  Web UI (FastAPI + single page)
```

Two halves joined by **one JSON contract** (§6), so two people can build in parallel from minute one.

---

## 4. Pipeline: step by step

### Step 1: Git miner

**What:** one pass over history, oldest → newest.

```bash
git log --reverse --no-merges -M --name-status \
  --format='%x1e%H%x1f%aN%x1f%aE%x1f%at%x1f%s'
```

- `%aN`/`%aE` already apply the repo's `.mailmap`, which handles identity merging for free where it exists.
- `--name-status` gives `A` (added), `M` (modified), `D` (deleted) and `R<score> old new` (renamed) per file.
- `-M` enables rename detection. On `R old new`, **move** the accumulated stats from `old` to `new` so history survives renames. This is the main failure of naive `git blame` approaches.

**Filters (and why):**
| Filter | Rule | Why |
|---|---|---|
| Current files only | keep paths in `git ls-files` at HEAD | Deleted code needs no handoff |
| Non-source | drop lockfiles, `vendor/`, `node_modules/`, `dist/`, generated, binaries, `*.min.js` | Authorship of generated files is meaningless |
| Bots | name/email matches `[bot]`, `dependabot`, `renovate`, `github-actions`, `pre-commit-ci` | Bots don't hold knowledge |
| Bulk commits | skip commits touching > 100 files (configurable) | Mass reformat / licence-header commits would make one person look like they own everything |

### Step 2: Identity merge

`.mailmap` first. Then a union-find over developers, merging when:

- the lower-cased email is equal; or
- the lower-cased, whitespace-normalised name is equal; or
- the email is `NNN+username@users.noreply.github.com` and `username` matches another identity's email local-part or name.

Output: canonical `dev_id` → all names/emails. Shown in the UI so a judge can see the merge was done.

### Step 3: DOA scoring

For each developer _d_ and file _f_:

```
DOA(d, f) = 3.293 + 1.098·FA + 0.164·DL − 0.321·ln(1 + AC)

FA = 1 if d created f, else 0
DL = number of commits by d to f
AC = number of commits to f by everyone else
```

Normalise per file: `DOA_N(d, f) = DOA(d, f) / max_d' DOA(d', f)`.

**d is an author of f** iff `DOA_N > 0.75` **and** `DOA ≥ 3.293`.

_Why these thresholds:_ 3.293 is the formula's intercept, i.e. the baseline for "has touched it." Requiring at least that means a creator who has since been heavily overwritten drops out. The 0.75 relative cut keeps only people close to the top expert on that file. Both values come from the literature, not tuning.

### Step 4: Departure impact and risk ranking

Given departing developer **X**:

| Metric                          | Definition                                                                                                                                                                    |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **At-risk files**               | files where X is an author                                                                                                                                                    |
| **Orphaned files**              | files where X is the **only** author                                                                                                                                          |
| **Coverage before / after**     | % of files with ≥ 1 author, with and without X                                                                                                                                |
| **Truck factor before / after** | greedy: repeatedly remove the developer who authors the most files until coverage < 50%; count removals. Run with and without X.                                              |
| **Successor** (per module)      | the non-X developer with the highest summed `DOA_N` over the module's files who has committed in the last 12 months. If none qualifies, the module is flagged "no successor". |

**Importance of a file** (only for ranking orphaned / at-risk files; weights documented, not learned):

```
importance = 0.4·norm(LOC) + 0.4·norm(fan_in) + 0.2·norm(recent_commits_by_X_last_12mo)
```

- **LOC:** bigger files hold more logic.
- **fan_in:** number of other repo files that import this one. Python via `ast`; JS/TS via an `import … from` / `require()` regex. Central code hurts more when orphaned.
- **Recency:** code X is still actively changing is the freshest, least-documented knowledge.

**Module = directory.** Module risk = sum of the importance of its orphaned files (plus 0.5× for files that are at risk but not orphaned). Take the **top K = 5 modules** forward to generation.

### Step 5: Context-pack builder

Purpose: give the model the evidence for _why_ and _where it breaks_, not just the code, inside a hard token budget.

Per file in a top module (budget ≈ 6k tokens/file, estimated as chars ÷ 4):

| Evidence                         | Source                                                                                                   | Why it's included                           |
| -------------------------------- | -------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| Code outline                     | `ast` signatures + docstrings (Python); first N lines + function headers (others), **with line numbers** | Structure without blowing the budget        |
| X's commit messages to this file | step-1 data, newest 20, deduped                                                                          | The "why" behind decisions                  |
| Landmine markers                 | `git blame --line-porcelain` lines by X containing `TODO`/`FIXME`/`HACK`/`XXX`, **with line numbers**    | Known problems in X's own words             |
| Risky commits                    | X's commits whose subject matches `fix|revert|workaround|hotfix|hack`                                    | Where it has broken before                  |
| Tests                            | test files whose path/name references the module                                                         | What behaviour is protected, and what isn't |
| Top callers                      | up to 3 files from the fan-in set, with the import line                                                  | How the rest of the repo depends on it      |

Every snippet keeps its `path:line` so citations can be validated.

### Step 6: Map (per-file notes, LLM)

**Model:** `qwen2.5-coder:7b` (fallback `gemma2:9b`) via **Ollama**, local, open-weight, JSON mode, `temperature=0.2`.

Prompt contract: return JSON only.

```json
{
  "purpose": "one paragraph",
  "key_entry_points": [{ "name": "...", "cite": "path:line" }],
  "invariants": [{ "claim": "...", "cite": "path:line" }],
  "gotchas": [{ "claim": "...", "cite": "path:line | commit:sha" }],
  "open_questions": ["question only the maintainer can answer"]
}
```

System-prompt rules: _only use the evidence provided; every claim needs a cite that appears in the evidence; if intent is unclear, write an open question instead of guessing._

### Step 7: Citation validator (the anti-hallucination step)

Deterministic. For each claim:

- `path:line` must refer to a line range actually included in that file's context pack;
- `commit:sha` must be one of the SHAs in the pack.

Invalid cite → the claim is **dropped**. The count of kept vs. dropped claims is logged and shown in the UI ("41 claims, 37 verified"). This turns "trust the LLM" into a measurable number, which is good evidence for Technical execution.

### Step 8: Reduce (module KT, LLM)

Input: validated per-file notes for one module plus the module stats. Output: a markdown section per module.

```
## <module path>   risk: HIGH · 7 orphaned files · successor: @alice (or "none")
### What it does
### How to work in it (entry points, cited)
### Invariants you must not break (cited)
### Known landmines (cited)
### Questions to ask <X> before they leave
### Suggested handoff tasks (doc to write, test to add, pairing session)
```

### Step 9: Final outputs

- `KT-<maintainer>.md`: summary header (coverage drop, truck-factor drop, orphaned count) + the top-K module sections.
- `issues/*.md`: one issue draft per module. **Never posted automatically.**
- `analysis.json` + `kt.json`: machine-readable, for reuse and CI.
- **Caching:** results are keyed by `(repo HEAD sha, dev_id, model, prompt version)`, so the demo is instant and immune to Wi-Fi or GPU failures.

---

## 5. Key design decisions (summary)

| Decision              | Chosen                                         | Rejected                 | Why                                                                 |
| --------------------- | ---------------------------------------------- | ------------------------ | ------------------------------------------------------------------- |
| Expertise signal      | DOA over full history                          | `git blame` snapshot     | Blame loses renames, rewards reformatters, ignores the creator      |
| Risk computation      | Deterministic formula                          | LLM scoring each commit  | Reproducible, testable, fast, defensible to judges                  |
| LLM role              | Explain + write, cited                         | Decide what matters      | Plays to LLM strengths; risk can't be hallucinated                  |
| Hallucination control | Validator drops uncited claims                 | "Trust the prompt"       | Produces a measurable verification rate                             |
| Model hosting         | Ollama, local 7B                               | vLLM on rented GPU       | Minutes to set up instead of hours; open-weight; works offline      |
| Scale control         | Top-K = 5 modules, ~6k tokens/file, map-reduce | Whole repo in one prompt | Fits a 7B model's context; keeps latency to minutes                 |
| Interface             | CLI first, thin web UI on top                  | Web-only                 | CLI is reusable in CI (open-source value) and keeps the demo honest |
| Unit of output        | Per-person KT plan                             | Repo-wide heatmap        | Actionable, with a concrete user and moment of use                  |

---

## 6. JSON contract (agree on this first, before splitting work)

**`analysis.json`** (produced by Person A, consumed by Person B)

```json
{
  "repo": "owner/name",
  "head": "sha",
  "generated_at": "ISO-8601",
  "developers": [
    {
      "id": "d1",
      "names": ["…"],
      "emails": ["…"],
      "files_authored": 42,
      "last_commit": "ISO-8601"
    }
  ],
  "truck_factor": {
    "value": 3,
    "removed": ["d1", "d4", "d7"],
    "coverage": 0.83
  },
  "departure": {
    "dev": "d1",
    "at_risk_files": 61,
    "orphaned_files": 34,
    "coverage_before": 0.83,
    "coverage_after": 0.58,
    "truck_factor_after": 2
  },
  "modules": [
    {
      "path": "src/core/",
      "risk": 8.7,
      "successor": { "dev": "d4", "doa_sum": 3.1 },
      "files": [
        {
          "path": "src/core/session.py",
          "loc": 812,
          "fan_in": 14,
          "orphaned": true,
          "importance": 0.91,
          "authors": ["d1"],
          "doa_n": { "d1": 1.0, "d4": 0.42 }
        }
      ]
    }
  ]
}
```

**`contextpack.json`** (Person B, internal)

```json
{
  "module": "src/core/",
  "files": [
    {
      "path": "src/core/session.py",
      "outline": [{ "start": 1, "end": 40, "text": "…" }],
      "commits": [{ "sha": "abc123", "date": "…", "msg": "…" }],
      "markers": [{ "line": 311, "text": "# HACK: retry because …" }],
      "risky_commits": [{ "sha": "def456", "msg": "revert pooling change" }],
      "tests": ["tests/test_session.py"],
      "callers": [
        {
          "path": "src/api.py",
          "line": 12,
          "text": "from .core.session import Session"
        }
      ]
    }
  ]
}
```

---

## 7. Tech stack and repo layout

- **Python 3.11**, `typer` (CLI), `fastapi` + `uvicorn` (API), `ollama` (Python client), `pytest`
- **Frontend:** one `index.html` with vanilla JS + `marked` (CDN) for rendering the KT markdown. No build step.
- **Licence:** MIT, committed in the **first** commit.

```
handoff/
├── LICENSE
├── README.md
├── handoff/
│   ├── miner.py        # step 1–2
│   ├── doa.py          # step 3
│   ├── impact.py       # step 4
│   ├── pack.py         # step 5
│   ├── llm.py          # step 6, 8 (+ cache)
│   ├── validate.py     # step 7
│   ├── render.py       # step 9
│   ├── cli.py          # `handoff analyze`, `handoff kt`
│   └── api.py          # FastAPI wrapper
├── web/index.html
├── tests/              # tiny synthetic repos built in fixtures
└── examples/           # cached outputs for the demo repo
```

**CLI:**

```bash
handoff analyze ./repo                 # → analysis.json, prints truck factor + top devs
handoff kt ./repo --who "Jane Doe"     # → KT-jane-doe.md + issues/
handoff serve                          # web UI on :8000
```

---

## 8. Tests that make the core trustworthy

Build tiny repos in `pytest` fixtures with `git init` + scripted commits, so every expected answer is known:

| Test                | Setup                                          | Expected                                          |
| ------------------- | ---------------------------------------------- | ------------------------------------------------- |
| Sole creator        | A creates `x.py`, commits 5×                   | A is the only author; `x.py` orphaned if A leaves |
| Overwritten creator | A creates, B commits 15×                       | B is an author, A is not                          |
| Rename survives     | A creates `old.py`, renames to `new.py`, edits | A's history attaches to `new.py`                  |
| Bot ignored         | dependabot touches every file                  | dependabot never appears as an author             |
| Bulk commit ignored | C reformats 150 files                          | C is not an author of them                        |
| Identity merge      | A commits as two emails, same name             | one developer                                     |
| Citation validator  | claim cites a line outside the pack            | claim dropped                                     |

These double as evidence for "Technical execution" during Q&A.

---

## 9. Timeline (two people; ~10h left at 23:55)

**Person A = analysis (steps 1–4). Person B = generation + UI (steps 5–9).**
B uses a hand-written `analysis.json` that follows §6 until A's real one lands.

| Until     | Person A                                                                               | Person B                                                                    | Gate                                                                              |
| --------- | -------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| 00:15     | Repo public, MIT licence, skeleton, JSON contract committed                            | Pull Ollama model, test JSON-mode call                                      | Both: contract agreed                                                             |
| 01:45     | Miner + renames + filters + identity merge, with tests                                 | Context-pack builder on a hand-picked file                                  | —                                                                                 |
| 03:15     | DOA, authors, coverage, truck factor, departure impact, ranking → real `analysis.json` | Map prompt + citation validator working on 1 file                           | —                                                                                 |
| **05:00** | CLI `analyze` end-to-end on the demo repo; FastAPI endpoints                           | Web UI: pick maintainer → impact numbers → ranked modules                   | **Hard gate: deterministic half works in the UI. No new features until it does.** |
| 07:30     | Fan-in for JS/TS, successor logic, edge cases                                          | Reduce step, final KT render, issue drafts, cache                           | KT for the demo maintainer generated and cached                                   |
| 09:00     | README: formula, limits, setup, AI tools used; clean up commits                        | UI polish: verified-claims counter, clickable citations → GitHub line links | —                                                                                 |
| 09:30     | Rehearse demo ×2                                                                       | Rehearse demo ×2                                                            | —                                                                                 |
| **09:55** | Final push + submission form                                                           |                                                                             | **Freeze at 10:00**                                                               |

**Cut list if behind (in this order):** JS/TS fan-in → issue drafts → successor recency check → web UI (fall back to CLI + rendered markdown).

---

## 10. Demo script (3:00)

| Time      | Show                                 | Say                                                                                                                                 |
| --------- | ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| 0:00–0:30 | Problem                              | "This repo has N contributors. Here's what happens if one specific person leaves."                                                  |
| 0:30–1:15 | Pick maintainer → numbers            | "34 files lose their last expert. Coverage drops 83% → 58%. Truck factor goes 3 → 2."                                               |
| 1:15–2:15 | Open the top module's KT             | Invariants with citations → **click one, it jumps to the exact line on GitHub**. Show the landmines from their own `HACK` comments. |
| 2:15–2:45 | "Questions to ask before they leave" | "The model doesn't pretend to know intent. It finds the gaps and turns them into questions."                                        |
| 2:45–3:00 | Verified counter + open-source       | "Every claim is cited; 37/41 verified. Runs locally on an open model, MIT, CLI usable in CI."                                       |

**Demo repo choice:** a mid-sized Python repo (1–5k commits, so analysis runs in seconds) with one clearly dominant historical maintainer. Pre-run it, check the numbers by hand, and cache the KT.

---

## 11. Mapping to the judging criteria

| Criterion (pts)          | How Handoff earns it                                                                                                                                         |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Technical execution (30) | Published authorship model, rename-aware mining, identity merge, truck-factor algorithm, map-reduce with a citation validator, unit tests on synthetic repos |
| Working demo (25)        | End-to-end by 05:00; cached LLM output; CLI fallback                                                                                                         |
| Impact & usefulness (20) | Concrete user and moment ("maintainer is stepping back"), actionable output, works on any Git repo                                                           |
| Open-source quality (15) | MIT from the first commit, README with method + limitations + AI tools, clean commits, open-weight local model, reusable JSON                                |
| Presentation (10)        | One-sentence problem, numbers in the first minute, clickable proof                                                                                           |

---

## 12. Risks and mitigations

| Risk                       | Mitigation                                                                        |
| -------------------------- | --------------------------------------------------------------------------------- |
| LLM too slow locally       | K = 5, token budget, cache by HEAD sha; run generation for the demo repo by 07:30 |
| LLM invents things         | Citation validator; verified-claim counter; "open questions" instead of guesses   |
| Identity merge mistakes    | `.mailmap` first; show merged identities in the UI                                |
| Authorship ≠ understanding | Stated plainly in the README and pitch: "estimate of knowledge concentration"     |
| Big repo too slow to mine  | Demo on a mid-sized repo; `--since` flag to limit history                         |
| Accidental GitHub spam     | No write access to GitHub at all; drafts only                                     |

---

## 13. Known limitations (put these in the README)

- Pair programming, code review knowledge, and design discussions outside Git are invisible.
- Squash-merged PRs credit a single author; `Co-authored-by` trailers are not parsed in v1.
- Fan-in is approximate (static imports only; no dynamic imports or dependency injection).
- The DOA thresholds come from prior research on other projects and may need tuning per repo.

---

## 14. Q&A prep

- **"Why not just use git blame?"** Blame is a snapshot. It loses renames, credits whoever reformatted last, and forgets the creator. DOA uses the whole history.
- **"Where do the formula's numbers come from?"** The Degree-of-Authorship model (Fritz et al.), as used by Avelino et al. for truck-factor estimation. We didn't tune them.
- **"How do you know the KT is correct?"** Every claim must cite evidence we supplied. Uncited claims are dropped, and we report the verification rate. Where intent isn't in the evidence, it becomes a question, not an answer.
- **"Why a local 7B model?"** Open-weight, private (a repo's code never leaves the machine), and free to run in CI.
- **"What's next?"** GitHub Action that comments a bus-factor report on PRs, `Co-authored-by` support, CODEOWNERS agreement as a validation metric.
