# Handoff: feasibility considerations

Companion to [handoff.md](handoff.md). It updates the plan for the hardware we actually have: Avi's **RTX 5060 (8 GB)** runs the LLM, and Aman's GTX 1650 (4 GB) does not.

## Verdict
**Feasible as originally written**, with the model swaps and guardrails below. Inference speed is no longer the main risk. What's left is ordinary build risk on the analysis half, which the 05:00 checkpoint already covers.

## 1. Model choice on 8 GB
These are rough estimates of memory and speed. Measure them in the smoke test (§6).

| Model | Weights (Q4) | Context memory at 8k tokens | Total | Fits 8 GB? |
|---|---|---|---|---|
| `qwen2.5-coder:7b` | ~4.7 GB | ~0.5 GB | ~5.2 GB | **Yes, with room to spare. Primary.** |
| `gemma3:4b` | ~3.3 GB | small | ~4 GB | **Yes. Fallback, and the Gemma option.** |
| `gemma2:9b` (old fallback) | ~5.4 GB | ~2.7 GB | ~8+ GB | **No.** It spills to the CPU and its context tops out at 8k. Dropped. |

- **Change from handoff.md:** the fallback is now `gemma3:4b`, not `gemma2:9b`.
- **The MLH Best Open-Source AI prize doesn't require Gemma.** It accepts open-source or open-weight AI, so Qwen qualifies. If both models produce valid JSON with good citations, Gemma is the safer pick, since MLH promoted it at this event. Run the same pack through both and keep whichever has the better verified-claim rate.

## 2. Speed and scope
- **Estimated speed with the model fully on the GPU:**
  - Reading the prompt: thousands of tokens per second.
  - Writing the answer: about 40–60 tokens per second.
- **That works out to roughly 10–20 s per map call** for a 6k-token pack and a ~500-token JSON reply.
- **Original scope stays:** K = 5 modules, about 6k tokens per file.
  - About 25 map calls plus 5 reduce calls is roughly **5–10 minutes** for a full KT run.
  - That's fast enough to rerun when prompts change, not just once for the cache.
- **Cut early:** JS/TS fan-in, since the demo repo is Python.
- **The rest of the cut list stays as written:** issue drafts, then the successor recency check, then the web UI.

## 3. Pitfalls
- **The RTX 5060 is a new (Blackwell) card.** It needs the latest Ollama release and a current NVIDIA driver. Older builds quietly fall back to the CPU. Check that `ollama ps` shows **100% GPU**.
- **Always pass `options={"num_ctx": 8192}`.** Ollama's default context is smaller and cuts long prompts off without any error, even on a big GPU.
- **Base date windows on the repo's HEAD commit date, not today.** That covers successor activity in the last 12 months and the recency part of importance. On an older demo repo, using today's date gives "no successor" everywhere and zero recency.
- **Guard DOA normalisation when the per-file maximum is ≤ 0.** With a large AC, the raw score can go negative, and dividing by a negative maximum flips the sign.
- **Move a renamed file's stats to its new path before filtering to files at HEAD.**
- **Read git output as utf-8 with `errors="replace"`.** On Windows, names with non-ASCII characters otherwise crash the miner.

## 4. Splitting the work between two laptops
- **Generation runs only on Avi's laptop.** The cache bridges the two machines: commit the cached `kt.json` for the demo repo to `examples/`. That way the UI and demo work on Aman's laptop too, with no Ollama there.
- **Present from Avi's laptop.** If a judge asks, it can regenerate live.
- **Don't count on pointing `OLLAMA_HOST` at Avi's machine over venue Wi-Fi.** Venue networks often block devices from talking to each other. Treat it as a bonus if it works.
- **Branches:** Aman works on `main` or a feature branch, and Avi on `avi`. Merge into `main` at every checkpoint, since `main` is the default branch the judges see. Agree on the JSON contract (handoff.md §6) first, so Avi can build against a hand-written `analysis.json`.

## 5. Revised timeline
| Until | Aman (analysis) | Avi (LLM and UI) |
|---|---|---|
| 01:00 | Skeleton, JSON contract and sample `analysis.json` committed | Latest Ollama, pull both models, GPU smoke test |
| 02:30 | Miner, renames, filters, identity merge, tests | Context-pack builder, map prompt on one file |
| 04:30 | DOA, impact, ranking produce a real `analysis.json`; CLI `analyze` | Citation validator; UI showing the impact numbers |
| **05:00** | **Checkpoint: the deterministic half works end to end in the UI** | |
| 07:00 | Successor logic, edge cases, FastAPI | Reduce step, KT render, cache committed to `examples/` |
| 09:00 | README covering method, limitations and AI tools | UI polish, clickable citations |
| 09:30–09:55 | Rehearse twice, final push to `main`, submission form | |

## 6. Avi's GPU smoke test (before 01:00)
1. `ollama --version` reports the latest release. `ollama pull qwen2.5-coder:7b` and `ollama pull gemma3:4b`.
2. Run one call in JSON mode with `num_ctx: 8192` on a hand-made ~6k-token pack. Time it, and check that `ollama ps` shows 100% GPU.
3. Confirm the reply parses as the map JSON schema and that its `path:line` citations fall inside the pack, by checking the validator logic by hand.
4. Run the same pack on both models and pick the one with more verified claims.
