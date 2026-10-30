<!-- AMAN:START intro -->
# Handoff

> When a maintainer leaves, what do we lose, and what should we do before they go?

**Handoff** reads a repository's Git history, finds the code whose **last remaining expert** is a departing maintainer, ranks it by importance, and generates a **cited knowledge-transfer (KT) plan** with a local open-weight model.

Built at **DevF8rge Finals: Hacktoberfest 2026 Hack Day, Bengaluru** (30–31 Oct 2026), hosted by DevForge (Newton School of Technology) and powered by MLH.

**Theme:** AI for Open Source
<!-- AMAN:END intro -->

<!-- AVI:START demo -->
## Demo

TBD (screenshot, GIF or video link)
<!-- AVI:END demo -->

<!-- AMAN:START analysis -->
## How the analysis works

TBD: Degree-of-Authorship model, truck factor, departure impact, ranking.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
handoff analyze path/to/repo                       # analysis.json, truck factor, top authors
handoff analyze path/to/repo --who "Jane Doe"      # plus departure impact and ranked modules
handoff kt path/to/repo --who d1                   # KT-d1.md + kt.json (needs Ollama)
handoff serve                                      # web UI on http://127.0.0.1:8000
```

## Limitations

- Authorship history is an **estimate** of knowledge concentration, not a measure of understanding.
- Pair programming, code review and design discussions outside Git are invisible.
- Squash-merged PRs credit a single author; `Co-authored-by` trailers are not parsed yet.
- The DOA thresholds come from prior research and may need tuning per repo.
<!-- AMAN:END analysis -->

<!-- AVI:START generation -->
## How the KT plan is generated

TBD: context packs, local model, map/reduce, citation validator, caching.
<!-- AVI:END generation -->

<!-- AMAN:START footer -->
## AI tools used

As the event rules require, we disclose every AI tool we used:

- **Claude Code (Claude Opus 5.5)**: coding assistant used for scaffolding, implementation and docs. Every team member has reviewed and can explain the code.
- TBD: open-weight models the project runs and any other assistants used.

## Credits

- Degree-of-Authorship model: Fritz et al.; truck-factor algorithm: Avelino et al.
- TBD: libraries and demo repository.

## Contributing

Contributions are welcome.

1. Fork the repo and create a branch: `git checkout -b feature/your-feature`
2. Make your changes and commit with a clear message.
3. Open a pull request that describes what changed and why.

Please open an issue first for large changes. Never commit API keys or secrets: use a local `.env` file, which git ignores.

## Team

- Aman ([@Amanrajput1206](https://github.com/Amanrajput1206))
- Avi

## License

[MIT](LICENSE)
<!-- AMAN:END footer -->
