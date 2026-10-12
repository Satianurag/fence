"""fence eval: run investigate over closed issues and compare each verdict with what happened to the issue."""
import collections
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

LIMITS = [
    "state_reason is a weak label. \"completed\" also covers feature work and refactors that were done as asked, where "
    "\"mistake\" (nobody chose the old behaviour) is not really the question being answered.",
    "The not_planned set that locates by name and traces to a checkpoint is very small, so the decision side of this "
    "comparison is a handful of issues and its rate means little.",
    "not_planned is not the same as a deliberate decision: issues are also closed that way as duplicates, as folded into "
    "another issue, or for process reasons.",
    "Only issues that name a Go file or symbol and trace to at least one public checkpoint are included; that is a "
    "minority of closed issues, and probably the better-specified ones.",
    "Each issue was run once. The verdict varies between runs of the same issue, so single cells can flip.",
    "no_record is an abstention (Fence would ask a human) and is counted separately, not as agreement or disagreement.",
]
EXPECTED = {"completed": "mistake", "not_planned": "decision"}


def select(issues: list[dict[str, Any]], stored: list[dict[str, Any]], state: str = "closed") -> list[int]:
    """Issues in `state` whose stored resolver output has named spans and at least one checkpoint-linked line."""
    closed = {i["number"] for i in issues if i["state"] == state}
    return sorted(t["number"] for t in stored if t["number"] in closed and t["spans"] and t["lines_linked"])


def row(issue: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    v = run.get("verdict") or {}
    expected = EXPECTED.get(issue.get("state_reason"))
    verdict = v.get("verdict")
    if expected is None or verdict is None:
        agrees = None
    elif verdict == "no_record":
        agrees = None
    else:
        agrees = verdict == expected
    loc = run.get("located") or {}
    return dict(number=issue["number"], title=issue["title"], labels=issue.get("labels") or [], state_reason=issue.get("state_reason"),
                expected=expected, verdict=verdict, confidence=v.get("confidence"), agrees=agrees, quotes=len(v.get("quotes") or []),
                spans=loc.get("spans"), lines=loc.get("lines"), lines_linked=loc.get("lines_linked"), excerpts=len(run.get("evidence") or []),
                cost_usd=(run.get("cost") or {}).get("usd", 0.0), trace_id=run.get("trace_id"), error=run.get("error"))


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    table: dict[str, dict[str, int]] = collections.defaultdict(lambda: collections.defaultdict(int))
    for r in rows:
        table[str(r["state_reason"])][str(r["verdict"] or "error")] += 1
    judged = [r for r in rows if r["agrees"] is not None]
    per = {}
    for reason, expected in EXPECTED.items():
        rs = [r for r in rows if r["state_reason"] == reason and r["verdict"]]
        per[reason] = dict(issues=len(rs), expected=expected, agree=sum(r["agrees"] is True for r in rs),
                           disagree=sum(r["agrees"] is False for r in rs), abstained_no_record=sum(r["verdict"] == "no_record" for r in rs))
    return dict(
        issues=len(rows),
        errors=sum(1 for r in rows if r["error"]),
        verdict_by_state_reason={k: dict(v) for k, v in sorted(table.items())},
        agreement=dict(compared=len(judged), agree=sum(r["agrees"] for r in judged),
                       rate=round(sum(r["agrees"] for r in judged) / len(judged), 3) if judged else None, by_state_reason=per),
        total_cost_usd=round(sum(r["cost_usd"] for r in rows), 4),
    )


def run_eval(issues: list[dict[str, Any]], numbers: list[int], investigate_one: Callable[[int], dict[str, Any]], out_dir: Path,
             workers: int = 4, resume: bool = True, name: str = "eval", what: str | None = None, limits: list[str] | None = None) -> dict[str, Any]:
    """Investigate each issue (skipping ones already in out_dir/<name> when resuming) and write out_dir/<name>.json."""
    by_number = {i["number"]: i for i in issues}
    runs_dir = out_dir / name
    runs_dir.mkdir(parents=True, exist_ok=True)

    def one(n: int) -> dict[str, Any]:
        path = runs_dir / f"{n}.json"
        if resume and path.exists():
            run = json.loads(path.read_text())
            if run.get("verdict"):
                return run
        return investigate_one(n)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        runs = list(pool.map(one, numbers))
    rows = [row(by_number[n], r) for n, r in zip(numbers, runs)]
    report = dict(
        what=what or "Fence verdict (investigate only, at the issue-creation commit, no search fallback, no memory) for every closed issue that "
                     "locates by name and traces to at least one checkpoint, compared with the issue's state_reason.",
        limits=LIMITS if limits is None else limits,
        summary=summarize(rows),
        issues=rows,
    )
    with open(out_dir / f"{name}.json", "w") as fh:
        json.dump(report, fh, indent=1, ensure_ascii=False)
    return report


def compare_row(issue: int, mode: str, run: dict[str, Any] | None) -> dict[str, Any]:
    """One line of the before/after table, from a runs/fix file."""
    if run is None:
        return dict(issue=issue, mode=mode, ran=False)
    last = (run.get("attempts") or [{}])[-1].get("verification") or {}
    return dict(issue=issue, mode=mode, ran=True, verdict=(run.get("verdict") or {}).get("verdict"), outcome=run.get("outcome"), action=run.get("action"),
                reason=run.get("reason"), files_changed=run.get("files_changed") or [], tests_newly_passing=run.get("tests_newly_passing") or [],
                verified=bool(last.get("ok")), verification=last.get("reason"), attempts=len(run.get("attempts") or []),
                cost_usd=(run.get("cost") or {}).get("usd", 0.0), duration_s=run.get("duration_s"), commit=(run.get("commit") or {}).get("commit"),
                trace_id=run.get("trace_id"), error=run.get("error"))


def build_compare(out_dir: Path, numbers: list[int]) -> dict[str, Any]:
    """runs/compare.json: Fence mode next to baseline mode for each issue, from the fix runs already on disk."""
    rows = []
    for n in numbers:
        for mode, suffix in (("fence", ""), ("baseline", ".baseline")):
            path = out_dir / "fix" / f"{n}{suffix}.json"
            rows.append(compare_row(n, mode, json.loads(path.read_text()) if path.exists() else None))
    report = dict(
        what="Per issue and mode: fence = investigate, then fix only on a mistake verdict; baseline = the issue goes straight to the same worker. "
             "Both are verified the same way (the worker's test must fail without its fix, pass with it, nothing regresses).",
        limits=["Each cell is one run; worker output and cost vary between runs.",
                "Baseline cost has no investigation in it; fence cost includes the verdict call.",
                "\"verified\" says the worker's own test passes and nothing in the tested packages regressed. It does not say the change is the fix a maintainer would want."],
        rows=rows,
    )
    with open(out_dir / "compare.json", "w") as fh:
        json.dump(report, fh, indent=1, ensure_ascii=False)
    return report
