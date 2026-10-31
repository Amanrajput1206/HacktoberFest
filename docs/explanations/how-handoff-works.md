# How Handoff works (in plain language)

This page explains what the project does, how each piece works, and what was built on Avi's side tonight. It assumes no background beyond basic Git.

---

## 1. The idea in one paragraph

When someone who maintains an open-source project leaves, the things only they understood leave with them. **Handoff** looks at a repository's Git history and answers: *"If this person leaves, which code loses its last expert, and what should we ask them before they go?"* It then writes a **knowledge-transfer (KT) plan**: a document that explains that code, warns about its traps, suggests who should take it over, and lists questions for the departing person. Every statement in that plan points to an exact line of code or commit, and anything the AI says without proof is thrown away.

---

## 2. The big picture: two halves

```
  Git history
      │
      ▼
┌─────────────────────────────┐        ┌──────────────────────────────────┐
│ ANALYSIS (Aman)             │        │ GENERATION (Avi)                 │
│ no AI, pure math            │  JSON  │ local AI, checked by code        │
│                             │ ─────► │                                  │
│ who knows which file?       │        │ explain the risky code,          │
│ what is lost if X leaves?   │        │ with proof for every claim       │
└─────────────────────────────┘        └──────────────────────────────────┘
                                                      │
                                                      ▼
                                        KT-<person>.md  +  kt.json  +  web page
```

- The **analysis half** decides *what matters*. It never uses AI, so its numbers are repeatable and explainable.
- The **generation half** explains *what the code is*. It uses an AI model, but the AI only gets to describe evidence we picked, and a checker removes anything it can't back up.
- The two halves only talk through one file, `analysis.json`. That is why two people could build them at the same time.

---

## 3. The analysis half (Aman's part), briefly

1. **Read the history.** Every commit: who, when, which files. Renamed files keep their history.
2. **Ignore noise.** Bots (like dependabot) and huge reformatting commits don't count as knowledge.
3. **Merge identities.** "Jane Doe" at two email addresses is one person.
4. **Score who "knows" each file** with a published formula (Degree of Authorship). Creating a file, changing it often, and few other people changing it all raise your score.
5. **Simulate a departure.** For the chosen person, count:
   - **orphaned files**: files where they are the *only* expert,
   - **coverage**: the share of files that still have at least one expert,
   - **truck factor**: how many people would have to leave before most of the code has no expert.
6. **Rank** the risky code by size, how many other files depend on it, and how recently the person worked on it. Folders are "modules"; the top 5 go on to generation.

On the demo repo, **psf/requests**, if **Kenneth Reitz** leaves: 13 files are orphaned, coverage drops from 100% to 65%, and the truck factor drops from 3 to 2.

---

## 4. The generation half (Avi's part), step by step

Think of it as a careful research assistant: we hand it a folder of evidence, it writes notes, and a fact-checker crosses out anything not in the folder.

### Step 5: Build an evidence pack (`handoff/pack.py`)

For each risky file we collect, **with line numbers**:

| Evidence | Where it comes from | Why |
|---|---|---|
| Code outline | Function and class headers plus the start of their docstrings | Shows the structure without the whole file |
| The person's commit messages to this file | Git history | The *why* behind changes |
| Their `TODO` / `FIXME` / `HACK` comments | `git blame` | Known problems in their own words |
| Their "fix", "revert", "workaround" commits | Git history | Where it broke before |
| Tests that use the file | File names and imports | What is protected |
| Up to 3 files that import it | Import lines | How the rest of the code depends on it |

Each pack is capped at about 6,000 tokens (roughly 24,000 characters) so it fits the model comfortably.

### Step 6: The AI writes notes per file (`handoff/llm.py`, "map")

We send one pack at a time to a **local** model running on Avi's GPU through **Ollama**. Nothing leaves the laptop. The model must reply in a fixed JSON shape:

- `purpose`: what the file is for
- `key_entry_points`: the main functions/classes, each with a line cite
- `invariants`: rules you must not break, each with a cite
- `gotchas`: traps and past breakages, each with a cite
- `open_questions`: things only the departing person can answer

The rules we give it: use only the evidence, cite every claim, and **if the reason isn't in the evidence, ask a question instead of guessing**.

### Step 7: The fact-checker (`handoff/validate.py`)

This is plain code, no AI. For every claim it checks:

- a `file:line` cite must point to a line that was **actually in the pack**;
- a `commit:sha` cite must be a commit **actually in the pack**;
- an entry point must **name something on the line it cites** (so "Session at line 2" fails if line 2 is a docstring).

Claims that fail are dropped, and we count them. That gives the headline number on the demo: **"81 / 94 claims verified."** It turns "trust the AI" into something you can measure.

### Step 8: The AI summarises each module (`llm.py`, "reduce")

For each folder, the model reads the checked notes and writes a short summary, the most important questions, and concrete handoff tasks. It returns only these three things, so it **cannot add new uncited claims** to the plan.

### Step 9: Assemble the plan (`handoff/render.py`)

Code (not AI) builds `KT-d1.md` from the verified claims:

```
## src/requests/
Risk: HIGH · 10 orphaned / 13 at-risk files · successor: @Nate Prewitt
### What it does
### How to work in it            ← entry points, grouped by file, linked
### Invariants you must not break ← each linked to a GitHub line
### Known landmines               ← each linked to a line or commit
### Questions to ask Kenneth Reitz before they leave
### Suggested handoff tasks
```

Every cite becomes a link like `github.com/psf/requests/blob/<commit>/src/requests/models.py#L284`, so a reader (or a judge) can click and see the exact line.

### Caching

Each AI reply is saved under `examples/requests/cache/`, keyed by (repo commit, person, model, prompt version). Running again is instant and needs no GPU, so the demo works even if Wi-Fi or Ollama fails. The cache stores the **raw** AI output and the checker re-runs every time, so improving the checker never needs new AI calls.

### The web page (`web/index.html`)

One HTML file, no build step. It shows:

1. the departing person and their merged identities,
2. the impact numbers (orphaned files, coverage, truck factor),
3. the ranked modules; click one to see its files,
4. the KT plan with the verified-claims meter and clickable citations.

It reads from Aman's API (`/api/analysis`, `/api/kt/d1`), which only serves saved files and never runs the AI.

---

## 5. What was done tonight on Avi's side, in order

1. **Read the plan** (`docs/plans/`) and wrote a personal checklist.
2. **Pulled Aman's half** into the `avi` branch and checked it: 21 tests pass and the CLI works.
3. **Installed Ollama**, downloaded `qwen2.5-coder:7b` and `gemma3:4b`, and cloned psf/requests next to this repo.
4. **Built the evidence packs.** All 13 files of `src/requests/` pack in about 2 seconds.
5. **GPU smoke test.** Both models run 100% on the GPU with an 8,192-token context.
   - The first try verified **0 of 7** claims. The model copied the placeholder `path:323` from our instructions instead of the real file path. The checker caught it, the prompt was fixed, and the next run verified **11 of 11**.
6. **Wrote the checker, the summariser, the renderer and the cache**, with tests that use a fake AI, so they run without a GPU.
7. **Picked the model by measurement.** On the full demo:
   - `gemma3:4b`: faster, but it often cited the wrong line. For example, it put class `Request` at line 289, and it is really at 284. Only **63%** of its claims survived.
   - `qwen2.5-coder:7b`: about 150 seconds for the whole plan, **86%** verified (81 of 94). **This is the one we use.**
8. **Built the web page** and checked that the API serves it.
9. **Committed in small steps, opened PR #1, and merged it** into `main` with a merge commit. 31 tests pass on `main`.

---

## 6. How to run it

### The easy way: Handoff Studio (needs Ollama)

```powershell
.\.venv\Scripts\activate
python -m handoff.studio            # opens http://127.0.0.1:8001
```

1. Click **New run** and paste a GitHub URL (like `https://github.com/pallets/click`) or a local repo folder. It clones the repo and analyzes it.
2. In **Who is leaving?**, click **Simulate leaving** next to a developer. The impact numbers and ranked modules appear.
3. Click **Generate KT** and watch the progress bar. The plan appears when it's done.
4. Use the **Run** dropdown to switch between past runs, including the read-only requests demo.

Runs are saved in `C:\Users\<you>\handoff-runs\` (outside this repo), so nothing gets committed by accident. The studio is only for the machine with Ollama. `handoff serve` stays the read-only demo page.

### The terminal way

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"

# See the demo from the saved results (no GPU needed)
handoff serve --data-dir examples/requests        # open http://127.0.0.1:8000

# Regenerate the KT plan (needs Ollama and a clone of psf/requests)
handoff kt ..\requests --who d1 --analysis examples/requests/analysis.json --out-dir examples/requests

# Run the tests
python -m pytest -q
```

---

## 7. Where things live

| File | What it does |
|---|---|
| `handoff/miner.py`, `doa.py`, `impact.py` | Analysis half (Aman) |
| `handoff/cli.py`, `api.py` | Commands and the read-only web API (Aman) |
| `handoff/pack.py` | Evidence packs (step 5) |
| `handoff/llm.py` | AI notes per file, module summaries, `generate_kt`, cache (steps 6, 8) |
| `handoff/validate.py` | The fact-checker (step 7) |
| `handoff/render.py` | Builds the final markdown and JSON (step 9) |
| `web/index.html` | The read-only demo page (served by `handoff serve`) |
| `handoff/studio.py`, `web/studio.html` | Handoff Studio: run everything from the browser |
| `examples/requests/` | Saved analysis, KT plan and AI cache for the demo |
| `tests/` | Synthetic mini-repos with known answers, plus a fake AI |

---

## 8. Honest limitations

- **"Verified" means "cited correctly", not "true".** The checker proves a claim points at real evidence. It can't prove the sentence about that line is right. A human should still read the plan.
- **Some notes are generic.** A 7B model sometimes writes "this function should handle X correctly". Better prompts would help.
- **Git history is not understanding.** Pair programming, code review and chats are invisible to it, so the scores are an *estimate* of who knows what.
- **The code outline is only signatures and docstrings**, so the model doesn't see function bodies. That keeps packs small, but it can miss details.

---

## 9. Questions a judge might ask about this half

- **"How do you stop the AI making things up?"** It only sees evidence we chose, every claim needs a cite, and plain code drops claims whose cite isn't in that evidence. We show the count: 81 of 94 kept.
- **"Why a small local model?"** It's open-weight, free, and private (the code never leaves the machine), and it runs in seconds per file on a laptop GPU.
- **"Why Qwen and not Gemma?"** We measured both on the same evidence. Gemma was faster, but its line numbers were wrong more often, so fewer of its claims survived the checker.
- **"What if the GPU fails during the demo?"** Everything is cached in `examples/requests/`, so the demo runs from saved results.
