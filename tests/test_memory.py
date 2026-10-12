import json

import pytest

from fence import memory
from fence.evidence import Excerpt
from fence.verdict import LLMCall, VerdictError, build_prompt, classify, parse_verdict

RULINGS = [{"id": "fact:1", "text": "Ruling on issue #5 (x): not supported, leave it"}]
EX = [Excerpt("commit_message", "c1", "k1", "by design")]
ISSUE = {"number": 6, "title": "same kind of request", "body": "b"}
RESOLVED = {"located_by": "named", "spans": 1, "span_list": [["a.go", 1, 2, "func:Thing"]], "lines": 2, "lines_linked": 2}


def reply(**kw):
    return json.dumps({**dict(verdict="precedent", confidence=0.9, rationale="Covered.", quotes=[], ruling_id="fact:1"), **kw})


def test_only_active_constraint_facts_are_rulings():
    facts = {"facts": [
        {"id": "a", "paths": ["constraints.fence.rulings"], "text": "keep", "status": "active"},
        {"id": "b", "paths": ["preferences.coding.style"], "text": "other category", "status": "active"},
        {"id": "c", "paths": ["constraints.fence.rulings"], "text": "old", "status": "retracted"},
    ]}
    assert memory.constraint_facts(facts) == [{"id": "a", "text": "keep"}]
    assert memory.constraint_facts({"facts": None}) == []


def test_remember_files_the_ruling_under_constraints_without_an_agent(monkeypatch):
    seen = []
    monkeypatch.setattr(memory, "_brain", lambda args, cwd: seen.append(args) or {"id": "fact:9", "text": args[1]})
    fact = memory.remember({"number": 5, "title": "x"}, "not supported", "maintainer comment")
    assert seen[0][0] == "remember" and seen[0][2:] == ["--path", "constraints.fence.rulings", "--kind", "decision"]
    assert fact == {"id": "fact:9", "text": "Ruling on issue #5 (x): not supported [source: maintainer comment]"}


def test_prompt_lists_rulings_only_when_given():
    assert '<ruling id="fact:1">' in build_prompt(ISSUE, RESOLVED, EX, RULINGS)
    assert "Prior rulings" not in build_prompt(ISSUE, RESOLVED, EX)


def test_precedent_needs_a_supplied_ruling():
    assert parse_verdict(reply(), EX, RULINGS).ruling_id == "fact:1"
    with pytest.raises(VerdictError):
        parse_verdict(reply(ruling_id="fact:404"), EX, RULINGS)
    with pytest.raises(VerdictError):
        parse_verdict(reply(), EX)  # no rulings were recalled, so precedent is not an allowed verdict


def test_a_ruling_can_settle_an_issue_that_has_no_record():
    calls = []

    def run(prompt, model):
        calls.append(prompt)
        return LLMCall(model=model, prompt=prompt, output=reply())
    v, _ = classify(ISSUE, {**RESOLVED, "spans": 0, "span_list": []}, [], "m", runner=run, rulings=RULINGS)
    assert v.verdict == "precedent" and len(calls) == 1


RUN = {"verdict": {"verdict": "decision", "confidence": 0.8, "rationale": "Chosen.",
                   "quotes": [{"evidence_id": 1, "source": "transcript", "commit": "abc1234567", "checkpoint": "ck99", "quote": "we want it this way"}]},
       "located": {"span_list": [["a.go", 1, 2, "func:Thing"]], "located_by": "named", "lines": 2, "lines_linked": 2}, "evidence": []}


def test_question_has_the_quote_with_ids_and_each_answer():
    md = memory.question_markdown(ISSUE, RUN)
    assert "> we want it this way" in md and "commit `abc1234567`, checkpoint `ck99`" in md
    assert "It is intended, leave it" in md and "It is a bug, fix it" in md and "fence answer --issue 6" in md


def test_no_record_question_says_nothing_was_found():
    run = {**RUN, "verdict": {**RUN["verdict"], "verdict": "no_record", "quotes": []}}
    md = memory.question_markdown(ISSUE, run)
    assert "no record of why" in md and "No quote from the record" in md
