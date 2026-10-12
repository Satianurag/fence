"""fence investigate --issue N: locate -> evidence -> verdict, traced. Never edits the target repo."""
import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import config, evidence, trace
from .resolve import Repo, resolve_issue
from .verdict import LLMCall, VerdictError, classify


def _cost(calls: list[LLMCall]) -> dict[str, Any]:
    return dict(
        usd=round(sum(c.cost_usd for c in calls), 6),
        llm_calls=len(calls),
        input_tokens=sum(c.input_tokens for c in calls),
        output_tokens=sum(c.output_tokens for c in calls),
        cache_read_tokens=sum(c.cache_read_tokens for c in calls),
        cache_write_tokens=sum(c.cache_write_tokens for c in calls),
    )


def investigate(number: int, cfg: config.Config, issues_path: Path, out_dir: Path, at_created: bool = True, send_trace: bool = True, search: bool = True) -> dict[str, Any]:
    if not cfg.target:
        raise SystemExit("FENCE_TARGET is not set")
    with open(issues_path) as fh:
        issue = next((i for i in json.load(fh) if i["number"] == number), None)
    if issue is None:
        raise SystemExit(f"issue #{number} is not in {issues_path}")

    repo = Repo(cfg.target)
    tracer = trace.Tracer("fence.investigate", cfg.neatlogs_project, input=dict(issue=number, title=issue["title"]))
    run: dict[str, Any] = dict(issue=number, title=issue["title"], model=cfg.model, verdict=None, located=None, evidence=[], cost=_cost([]), trace_id=None, error=None)
    calls: list[LLMCall] = []
    try:
        with tracer.step("locate", input=dict(issue=number, at_created=at_created, search=search)) as s:
            resolved = resolve_issue(repo, issue, at_created, search)
            run["located"] = {k: resolved[k] for k in ("rev", "located_by", "search_error", "files_named", "symbols_named", "spans", "span_list", "lines", "lines_linked", "checkpoints") if k in resolved}
            s.output = run["located"]
        with tracer.step("evidence", input=[dict(commit=r["commit"], checkpoint=r["checkpoint"], lines=r["lines"]) for r in resolved["evidence"]]) as s:
            excerpts = evidence.collect(repo, resolved)
            run["evidence"] = [e.to_dict() for e in excerpts]
            s.output = dict(excerpts=len(excerpts), chars=sum(len(e.text) for e in excerpts), sources=[f"{e.commit}:{e.source}" for e in excerpts])
        with tracer.step("verdict", input=dict(model=cfg.model, excerpts=len(excerpts))) as s:
            try:
                verdict, calls = classify(issue, resolved, excerpts, cfg.model)
            except VerdictError as e:
                calls = getattr(e, "calls", [])
                raise
            finally:
                s.children = [trace.llm_span(c) for c in calls]
                run["cost"] = _cost(calls)
            run["verdict"] = verdict.to_dict()
            s.output = run["verdict"]
        tracer.root.output = run["verdict"]
    except Exception as e:
        run["error"] = f"{type(e).__name__}: {e}"
    tracer.root.extra["metadata"] = dict(cost_usd=run["cost"]["usd"], model=cfg.model)

    if send_trace:
        if not cfg.neatlogs_api_key:
            run["trace_error"] = "NEATLOGS_API_KEY is not set"
        else:
            try:
                run["trace_id"], run["trace_spans"] = trace.send(tracer, cfg.neatlogs_endpoint, cfg.neatlogs_api_key)
            except RuntimeError as e:
                run["trace_error"] = str(e)

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / f"{number}.json", "w") as fh:
        json.dump(run, fh, indent=1, ensure_ascii=False)
    return run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fence")
    sub = ap.add_subparsers(dest="cmd", required=True)
    inv = sub.add_parser("investigate", help="locate the code an issue names, gather intent evidence and give a verdict")
    inv.add_argument("--issue", type=int, required=True)
    inv.add_argument("--issues", type=Path, default=config.ROOT / "data" / "cli_issues.json", help="issues JSON file")
    inv.add_argument("--out", type=Path, default=config.ROOT / "runs", help="directory for <issue>.json")
    inv.add_argument("--head", action="store_true", help="resolve against HEAD instead of the commit current when the issue was filed")
    inv.add_argument("--no-search", action="store_true", help="do not fall back to `entire graph search` when the issue names no code")
    inv.add_argument("--no-trace", action="store_true", help="do not post the trace to Neatlogs")
    a = ap.parse_args(argv)

    run = investigate(a.issue, config.load(), a.issues, a.out, at_created=not a.head, send_trace=not a.no_trace, search=not a.no_search)
    v = run["verdict"]
    if v:
        print(f"#{run['issue']} {v['verdict']} (confidence {v['confidence']:.2f}): {v['rationale']}")
    loc = run["located"] or {}
    print(f"located by: {loc.get('located_by') or 'nothing'} | spans: {loc.get('spans', 0)} | lines traced: {loc.get('lines_linked', 0)}/{loc.get('lines', 0)}")
    print(f"evidence excerpts: {len(run['evidence'])} | llm calls: {run['cost']['llm_calls']} | cost: ${run['cost']['usd']:.4f} | trace: {run['trace_id'] or run.get('trace_error') or 'not sent'}")
    print(f"wrote {a.out / (str(a.issue) + '.json')}")
    if run["error"]:
        print(f"error: {run['error']}", file=sys.stderr)
        return 1
    return 1 if run.get("trace_error") else 0


if __name__ == "__main__":
    sys.exit(main())
