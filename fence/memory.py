"""Ask once, remember: human rulings stored as durable facts in Entire Brain.

Rulings live under the `constraints.` taxonomy in this repository's brain. Before a human is asked about an
issue, the brain is searched for rulings that may already cover it."""
import json
import subprocess
from pathlib import Path
from typing import Any

from .config import ROOT

RULING_PATH = "constraints.fence.rulings"
BRAIN_TIMEOUT_S = 180


class MemoryError_(Exception):
    pass


def _brain(args: list[str], cwd: Path) -> Any:
    try:
        p = subprocess.run(["entire", "brain", *args, "--json"], capture_output=True, text=True, errors="replace", cwd=cwd, timeout=BRAIN_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise MemoryError_(f"entire brain {args[0]} did not run: {type(e).__name__}") from None
    if p.returncode != 0:
        raise MemoryError_(f"entire brain {args[0]} exited with status {p.returncode}")
    try:
        return json.loads(p.stdout)
    except ValueError:
        raise MemoryError_(f"entire brain {args[0]} output was not JSON") from None


def constraint_facts(reply: dict[str, Any]) -> list[dict[str, str]]:
    """Active facts filed under constraints.*, reduced to id and text."""
    out = []
    for f in reply.get("facts") or []:
        if f.get("status", "active") == "active" and any(str(p).startswith("constraints.") for p in f.get("paths") or []) and f.get("text"):
            out.append(dict(id=f["id"], text=f["text"]))
    return out


def recall(query: str, cwd: Path = ROOT) -> list[dict[str, str]]:
    return constraint_facts(_brain(["recall", query], cwd))


def ruling_text(issue: dict[str, Any], ruling: str, source: str) -> str:
    return f"Ruling on issue #{issue['number']} ({issue.get('title') or ''}): {ruling.strip()} [source: {source.strip()}]"


def remember(issue: dict[str, Any], ruling: str, source: str, cwd: Path = ROOT) -> dict[str, str]:
    """Store a ruling; --path is given so no agent is asked to classify it."""
    text = ruling_text(issue, ruling, source)
    fact = _brain(["remember", text, "--path", RULING_PATH, "--kind", "decision"], cwd)
    if not isinstance(fact, dict) or not fact.get("id"):
        raise MemoryError_("entire brain remember returned no fact id")
    return dict(id=fact["id"], text=fact.get("text") or text)


def question_markdown(issue: dict[str, Any], run: dict[str, Any]) -> str:
    """The one question a human is asked for a decision or no_record verdict."""
    v = run["verdict"]
    n = issue["number"]
    lines = [f"# Question for a maintainer: issue #{n}", "", f"**{issue.get('title') or ''}**", ""]
    if v["verdict"] == "decision":
        lines += ["The recorded history says the behaviour this issue reports was chosen on purpose. "
                  "**Should it stay as it is, or has that decision changed?**"]
    else:
        lines += ["Fence found no record of why this code behaves the way the issue reports. "
                  "**Is the reported behaviour intended, or is it a bug to fix?**"]
    lines += ["", f"Fence's verdict: `{v['verdict']}` (confidence {v['confidence']:.2f}). {v['rationale']}", "", "## Evidence", ""]
    loc = run.get("located") or {}
    if v["quotes"]:
        for q in v["quotes"]:
            lines += [f"> {q['quote']}", "", f"- source: {q['source']}, commit `{q['commit']}`, checkpoint `{q['checkpoint']}`", ""]
    else:
        lines += ["No quote from the record supports either reading."]
        seen = list(dict.fromkeys((e["commit"], e["checkpoint"]) for e in run.get("evidence") or []))
        if seen:
            lines += ["", "Records Fence read without finding the behaviour discussed:", ""]
            lines += [f"- commit `{c}`, checkpoint `{k}`" for c, k in seen]
        else:
            lines += ["", f"No checkpoint is linked to the located code ({loc.get('lines_linked', 0)} of {loc.get('lines', 0)} lines traced)."]
        lines += [""]
    if loc.get("span_list"):
        lines += ["Code examined" + (" (found by search, not named in the issue)" if loc.get("located_by") == "search" else "") + ":", ""]
        lines += [f"- `{f}:{a}-{b}` ({how})" for f, a, b, how in loc["span_list"]] + [""]
    lines += ["## What Fence does with each answer", "",
              "| Your answer | What Fence does |", "|---|---|",
              "| It is intended, leave it | Stores the ruling, changes no code, and answers later issues of the same kind from it without asking again. |",
              "| It is a bug, fix it | Stores the ruling. Fence does not yet turn a \"fix it\" ruling into a fix by itself; later issues of the same kind are answered from the ruling. |",
              "| Something else | Stores your wording as the ruling; later issues are compared against it. |",
              "", "## How to answer", "", "```bash", f"fence answer --issue {n} \"<your ruling>\" --source \"<who said it and where>\"", "```", ""]
    return "\n".join(lines)
