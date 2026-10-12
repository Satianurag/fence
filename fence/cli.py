"""fence investigate --issue N: locate -> evidence -> verdict, traced. Never edits the target repo.
fence fix --issue N: investigate, and on a `mistake` verdict let a worker fix it in a private work copy."""
import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import config, evidence, fix, trace
from .resolve import Repo, resolve_issue
from .verdict import LLMCall, Verdict, VerdictError, classify

LOCATED_KEYS = ("rev", "located_by", "search_error", "files_named", "symbols_named", "spans", "span_list", "lines", "lines_linked", "checkpoints")


def _cost(calls: list[LLMCall]) -> dict[str, Any]:
    return dict(
        usd=round(sum(c.cost_usd for c in calls), 6),
        llm_calls=len(calls),
        input_tokens=sum(c.input_tokens for c in calls),
        output_tokens=sum(c.output_tokens for c in calls),
        cache_read_tokens=sum(c.cache_read_tokens for c in calls),
        cache_write_tokens=sum(c.cache_write_tokens for c in calls),
    )


def load_issue(issues_path: Path, number: int) -> dict[str, Any]:
    with open(issues_path) as fh:
        issue = next((i for i in json.load(fh) if i["number"] == number), None)
    if issue is None:
        raise SystemExit(f"issue #{number} is not in {issues_path}")
    return issue


def new_run(issue: dict[str, Any], cfg: config.Config) -> dict[str, Any]:
    return dict(issue=issue["number"], title=issue["title"], model=cfg.model, verdict=None, located=None, evidence=[], cost=_cost([]), trace_id=None, error=None)


def investigate_steps(issue: dict[str, Any], cfg: config.Config, tracer: trace.Tracer, run: dict[str, Any], calls: list[LLMCall],
                      at_created: bool, search: bool) -> tuple[dict[str, Any], Verdict]:
    """locate -> evidence -> verdict as spans on `tracer`; fills `run` and appends to `calls`. Raises on a failed step."""
    repo = Repo(cfg.target)
    with tracer.step("locate", input=dict(issue=issue["number"], at_created=at_created, search=search)) as s:
        resolved = resolve_issue(repo, issue, at_created, search)
        run["located"] = {k: resolved[k] for k in LOCATED_KEYS if k in resolved}
        s.output = run["located"]
    with tracer.step("evidence", input=[dict(commit=r["commit"], checkpoint=r["checkpoint"], lines=r["lines"]) for r in resolved["evidence"]]) as s:
        excerpts = evidence.collect(repo, resolved)
        run["evidence"] = [e.to_dict() for e in excerpts]
        s.output = dict(excerpts=len(excerpts), chars=sum(len(e.text) for e in excerpts), sources=[f"{e.commit}:{e.source}" for e in excerpts])
    with tracer.step("verdict", input=dict(model=cfg.model, excerpts=len(excerpts))) as s:
        made: list[LLMCall] = []
        try:
            verdict, made = classify(issue, resolved, excerpts, cfg.model)
        except VerdictError as e:
            made = getattr(e, "calls", [])
            raise
        finally:
            s.children = [trace.llm_span(c) for c in made]
            calls += made
            run["cost"] = _cost(calls)
        run["verdict"] = verdict.to_dict()
        s.output = run["verdict"]
    return resolved, verdict


def finish(run: dict[str, Any], tracer: trace.Tracer, cfg: config.Config, out_path: Path, send_trace: bool) -> None:
    """Post the trace once and write the run file."""
    tracer.root.extra["metadata"] = dict(cost_usd=run["cost"]["usd"], model=cfg.model)
    if send_trace:
        if not cfg.neatlogs_api_key:
            run["trace_error"] = "NEATLOGS_API_KEY is not set"
        else:
            try:
                run["trace_id"], run["trace_spans"] = trace.send(tracer, cfg.neatlogs_endpoint, cfg.neatlogs_api_key)
            except RuntimeError as e:
                run["trace_error"] = str(e)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(run, fh, indent=1, ensure_ascii=False)


def investigate(number: int, cfg: config.Config, issues_path: Path, out_dir: Path, at_created: bool = True, send_trace: bool = True, search: bool = True) -> dict[str, Any]:
    if not cfg.target:
        raise SystemExit("FENCE_TARGET is not set")
    issue = load_issue(issues_path, number)
    tracer = trace.Tracer("fence.investigate", cfg.neatlogs_project, input=dict(issue=number, title=issue["title"]))
    run = new_run(issue, cfg)
    try:
        investigate_steps(issue, cfg, tracer, run, [], at_created, search)
        tracer.root.output = run["verdict"]
    except Exception as e:
        run["error"] = f"{type(e).__name__}: {e}"
    finish(run, tracer, cfg, out_dir / f"{number}.json", send_trace)
    return run


def fix_issue(number: int, cfg: config.Config, issues_path: Path, out_dir: Path, work_root: Path, baseline_mode: bool = False,
              send_trace: bool = True, search: bool = True) -> dict[str, Any]:
    """Investigate at HEAD, then on `mistake` run worker -> verify (-> one retry) -> commit in work/<issue>.
    With baseline_mode the investigation is skipped and the issue goes straight to the worker."""
    if not cfg.target:
        raise SystemExit("FENCE_TARGET is not set")
    issue = load_issue(issues_path, number)
    mode = "baseline" if baseline_mode else "fence"
    tracer = trace.Tracer("fence.fix" + (".baseline" if baseline_mode else ""), cfg.neatlogs_project, input=dict(issue=number, title=issue["title"], mode=mode))
    run = new_run(issue, cfg)
    run.update(mode=mode, action=None, attempts=[], work_copy=None, commit=None, files_changed=[], tests_newly_passing=[])
    calls: list[LLMCall] = []
    t0 = time.monotonic()
    try:
        span_list: list[list[Any]] | None = None
        rationale: str | None = None
        if not baseline_mode:
            # HEAD, not the issue-creation commit: the worker edits today's code, so the spans must match it
            resolved, verdict = investigate_steps(issue, cfg, tracer, run, calls, at_created=False, search=search)
            if verdict.verdict != "mistake":
                run["action"] = "not_fixed"
                run["reason"] = f"verdict is {verdict.verdict}; Fence only changes code on a mistake verdict"
                tracer.root.output = dict(verdict=verdict.verdict, action=run["action"])
                return run
            span_list, rationale = resolved["span_list"], verdict.rationale

        name = f"{number}-baseline" if baseline_mode else str(number)
        scratch = work_root / f"{name}.verify"
        scratch.mkdir(parents=True, exist_ok=True)
        base = scratch / "base.json"
        packages: list[str] | None = None
        with tracer.step("work_copy", input=dict(dest=f"work/{name}")) as s:
            work = fix.make_work_copy(cfg.target, work_root / name)
            run["work_copy"] = f"work/{name}"
            s.output = dict(head=fix.git(work, "rev-parse", "HEAD").strip()[:10])
        if span_list:
            packages = fix.packages_for_spans(span_list)
            with tracer.step("test_baseline", input=dict(test=fix.test_command(packages))) as s:
                rec = fix.run_verify(work, fix.test_command(packages), "pre-edit baseline", record=base)
                run["baseline"] = rec.to_dict()
                s.output = rec.text
                if rec.exit_code != 0 or not base.exists():
                    raise fix.FixError(f"could not record the test baseline: {rec.text[:200]}")

        first = fix.worker_prompt(issue, packages, span_list, rationale)
        prompt = first
        verification = None
        for attempt in (1, 2):
            rec: dict[str, Any] = dict(attempt=attempt)
            run["attempts"].append(rec)
            with tracer.step(f"worker_{attempt}", input=dict(model=cfg.model, cwd=run["work_copy"])) as s:
                call = fix.run_worker(prompt, cfg.model, work)
                calls.append(call)
                run["cost"] = _cost(calls)
                s.children = [trace.llm_span(call, name="claude -p (worker)")]
                rec["worker"] = dict(report=call.output, error=call.error, cost_usd=call.cost_usd, duration_ms=call.duration_ms)
                s.output = call.output
                if call.error and not fix.changed_files(work):
                    raise fix.FixError(f"worker failed: {call.error}")
            with tracer.step(f"verify_{attempt}") as s:
                if packages is None:  # baseline mode: the packages are only known from what the worker touched
                    packages = fix.go_packages(fix.changed_files(work))
                    if packages:
                        run["baseline"] = fix.record_pristine_baseline(work, packages, base).to_dict()
                if packages:
                    verification = fix.verify_fix(work, packages, base, scratch)
                else:
                    verification = fix.Verification(False, "the worker changed no Go files", files_changed=fix.changed_files(work))
                rec["verification"] = verification.to_dict()
                s.input = dict(test=fix.test_command(packages)) if packages else None
                s.output = dict(ok=verification.ok, reason=verification.reason, repro_tests=verification.repro_tests,
                                newly_passing=verification.newly_passing, regressions=verification.regressions,
                                reports=[r.text for r in verification.runs])
                if not verification.ok:
                    s.fail(verification.reason)
            run["files_changed"] = verification.files_changed
            if verification.ok:
                break
            if attempt == 1:
                with tracer.step("explain_failure") as s:
                    explained = fix.explain_failure(work, packages) if packages else "no Go files were changed"
                    rec["explained_failure"] = explained
                    s.output = explained
                prompt = fix.retry_prompt(first, verification, explained)

        if verification and verification.ok:
            with tracer.step("commit") as s:
                run["commit"] = fix.commit_fix(work, issue, verification)
                s.output = run["commit"]
            run["action"] = "fixed"
            run["tests_newly_passing"] = verification.newly_passing
        else:
            run["action"] = "fix_failed"
            run["reason"] = verification.reason if verification else "no verification ran"
        tracer.root.output = dict(action=run["action"], commit=run["commit"], attempts=len(run["attempts"]))
    except Exception as e:
        run["error"] = f"{type(e).__name__}: {e}"
        run["action"] = run["action"] or "error"
    finally:
        run["duration_s"] = round(time.monotonic() - t0, 1)
        finish(run, tracer, cfg, out_dir / "fix" / f"{number}{'.baseline' if baseline_mode else ''}.json", send_trace)
    return run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fence")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--issue", type=int, required=True)
    common.add_argument("--issues", type=Path, default=config.ROOT / "data" / "cli_issues.json", help="issues JSON file")
    common.add_argument("--out", type=Path, default=config.ROOT / "runs", help="directory for run files")
    common.add_argument("--no-search", action="store_true", help="do not fall back to `entire graph search` when the issue names no code")
    common.add_argument("--no-trace", action="store_true", help="do not post the trace to Neatlogs")
    inv = sub.add_parser("investigate", parents=[common], help="locate the code an issue names, gather intent evidence and give a verdict")
    inv.add_argument("--head", action="store_true", help="resolve against HEAD instead of the commit current when the issue was filed")
    fx = sub.add_parser("fix", parents=[common], help="investigate, and on a mistake verdict fix the issue in a private work copy")
    fx.add_argument("--baseline", action="store_true", help="skip investigation and send the issue straight to the worker")
    fx.add_argument("--work", type=Path, default=config.ROOT / "work", help="directory for work copies")
    a = ap.parse_args(argv)
    cfg = config.load()

    if a.cmd == "investigate":
        run = investigate(a.issue, cfg, a.issues, a.out, at_created=not a.head, send_trace=not a.no_trace, search=not a.no_search)
        out_path = a.out / f"{a.issue}.json"
    else:
        run = fix_issue(a.issue, cfg, a.issues, a.out, a.work, baseline_mode=a.baseline, send_trace=not a.no_trace, search=not a.no_search)
        out_path = a.out / "fix" / f"{a.issue}{'.baseline' if a.baseline else ''}.json"

    v = run["verdict"]
    if v:
        print(f"#{run['issue']} {v['verdict']} (confidence {v['confidence']:.2f}): {v['rationale']}")
    loc = run["located"] or {}
    if loc:
        print(f"located by: {loc.get('located_by') or 'nothing'} | spans: {loc.get('spans', 0)} | lines traced: {loc.get('lines_linked', 0)}/{loc.get('lines', 0)}")
    if a.cmd == "fix":
        print(f"action: {run['action']}" + (f" ({run['reason']})" if run.get("reason") else "") + f" | attempts: {len(run['attempts'])} | duration: {run.get('duration_s')}s")
        if run["commit"]:
            print(f"committed {run['commit']['commit'][:10]} on {run['commit']['branch']} in {run['work_copy']} | newly passing: {', '.join(run['tests_newly_passing'])}")
    print(f"evidence excerpts: {len(run['evidence'])} | llm calls: {run['cost']['llm_calls']} | cost: ${run['cost']['usd']:.4f} | trace: {run['trace_id'] or run.get('trace_error') or 'not sent'}")
    print(f"wrote {out_path}")
    if run["error"]:
        print(f"error: {run['error']}", file=sys.stderr)
        return 1
    return 1 if run.get("trace_error") or run.get("action") == "fix_failed" else 0


if __name__ == "__main__":
    sys.exit(main())
