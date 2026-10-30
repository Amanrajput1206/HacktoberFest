import json
import sys
from pathlib import Path

import typer

# Windows consoles default to a legacy code page; contributor names are often non-ASCII.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(
    help="Find the code a departing maintainer is the last expert on, and plan the handoff.",
    no_args_is_help=True,
)


@app.command()
def analyze(
    repo: Path = typer.Argument(..., exists=True, file_okay=False, help="Path to a local Git repository."),
    who: str = typer.Option(None, help="Departing developer (name or email) to compute impact for."),
    out: Path = typer.Option(Path("analysis.json"), help="Where to write analysis.json."),
    bulk_limit: int = typer.Option(100, help="Skip commits touching more files than this."),
):
    """Mine history, score authorship, and write analysis.json (steps 1-4)."""
    from handoff.impact import run_analysis

    result = run_analysis(repo, who=who, bulk_limit=bulk_limit)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    devs = {d["id"]: d for d in result["developers"]}
    tf = result["truck_factor"]
    typer.echo(f"{result['repo']} @ {result['head'][:8]}: {len(devs)} developers")
    typer.echo(f"Truck factor: {tf['value']} ({', '.join(devs[d]['names'][0] for d in tf['removed'])})")
    typer.echo("Top authors by files authored:")
    for d in sorted(devs.values(), key=lambda d: -d["files_authored"])[:5]:
        typer.echo(f"  {d['id']:>4}  {d['files_authored']:>4} files  {d['names'][0]}")

    dep = result.get("departure")
    if dep:
        typer.echo(
            f"\nIf {devs[dep['dev']]['names'][0]} leaves: {dep['orphaned_files']} orphaned, "
            f"{dep['at_risk_files']} at risk, coverage {dep['coverage_before']:.0%} -> {dep['coverage_after']:.0%}, "
            f"truck factor {tf['value']} -> {dep['truck_factor_after']}"
        )
        for m in result["modules"]:
            succ = m["successor"]
            succ_name = devs[succ["dev"]]["names"][0] if succ else "none"
            typer.echo(f"  {m['risk']:>6.2f}  {m['path']}  (successor: {succ_name})")
    typer.echo(f"\nWrote {out}")


@app.command()
def kt(
    repo: Path = typer.Argument(..., exists=True, file_okay=False),
    who: str = typer.Option(..., help="Departing developer id from analysis.json (e.g. d1)."),
    analysis: Path = typer.Option(Path("analysis.json"), help="Output of `handoff analyze --who ...`."),
    out_dir: Path = typer.Option(Path("."), help="Where to write KT-<dev>.md and kt.json."),
    model: str = typer.Option("qwen2.5-coder:7b", help="Ollama model."),
):
    """Generate the cited KT plan for one maintainer (steps 5-9)."""
    from handoff.llm import generate_kt

    data = json.loads(analysis.read_text(encoding="utf-8"))
    path = generate_kt(repo, data, who, out_dir, model=model)
    typer.echo(f"Wrote {path}")


@app.command()
def serve(
    data_dir: Path = typer.Option(Path("."), help="Folder holding analysis.json and kt.json."),
    port: int = typer.Option(8000),
):
    """Run the web UI on cached results."""
    import uvicorn

    from handoff.api import create_app

    uvicorn.run(create_app(data_dir), host="127.0.0.1", port=port)


if __name__ == "__main__":
    app()
