import json

import pytest

from fence.evidence import Excerpt
from fence.verdict import LLMCall, VerdictError, classify, parse_cli_output, parse_verdict

EX = [
    Excerpt("commit_message", "c1", "k1", "Always add a blank line\nbefore the trailer, by design."),
    Excerpt("transcript", "c1", "k1", "[user] keep it simple"),
]
ISSUE = {"number": 1, "title": "t", "body": "b"}
RESOLVED = {"located_by": "named", "spans": 1, "span_list": [["a.go", 1, 2, "func:Thing"]], "lines": 2, "lines_linked": 2}


def reply(**kw):
    base = dict(verdict="decision", confidence=0.8, rationale="Chosen.", quotes=[dict(evidence_id=1, quote="before the trailer, by design.")])
    return json.dumps({**base, **kw})


def test_valid_verdict_gets_quote_provenance():
    v = parse_verdict(reply(), EX)
    assert v.verdict == "decision" and v.confidence == 0.8
    assert v.quotes == [dict(evidence_id=1, source="commit_message", commit="c1", checkpoint="k1", quote="before the trailer, by design.")]


def test_quote_may_differ_only_in_whitespace():
    assert parse_verdict(reply(quotes=[dict(evidence_id=1, quote="blank line before the trailer")]), EX).quotes


def test_code_fence_is_tolerated():
    assert parse_verdict("```json\n" + reply() + "\n```", EX).verdict == "decision"


@pytest.mark.parametrize("bad", [
    "not json",
    "[]",
    reply(verdict="bug"),
    reply(confidence=1.5),
    reply(confidence="high"),
    reply(confidence=True),
    reply(rationale=" "),
    reply(quotes="none"),
    reply(quotes=[dict(evidence_id=9, quote="x")]),
    reply(quotes=[dict(evidence_id=1, quote="words that are not in the evidence")]),
    reply(quotes=[dict(evidence_id=2, quote="before the trailer, by design.")]),
    reply(quotes=[dict(evidence_id=1, quote="")]),
    reply(quotes=[]),
])
def test_invalid_verdicts_are_rejected(bad):
    with pytest.raises(VerdictError):
        parse_verdict(bad, EX)


def test_mistake_needs_no_quote():
    assert parse_verdict(reply(verdict="mistake", quotes=[]), EX).quotes == []


def runner_from(outputs):
    seen = []

    def run(prompt, model):
        seen.append(prompt)
        return LLMCall(model=model, prompt=prompt, output=outputs[len(seen) - 1], cost_usd=0.01)
    return run, seen


def test_classify_retries_once_with_the_reason():
    run, seen = runner_from(["oops", reply()])
    v, calls = classify(ISSUE, RESOLVED, EX, "m", runner=run)
    assert v.verdict == "decision" and len(calls) == 2
    assert "rejected" in seen[1] and calls[0].error.startswith("invalid verdict")


def test_classify_gives_up_after_two_attempts():
    run, seen = runner_from(["oops", "still oops"])
    with pytest.raises(VerdictError) as e:
        classify(ISSUE, RESOLVED, EX, "m", runner=run)
    assert len(seen) == 2 and len(e.value.calls) == 2


def test_no_spans_or_no_evidence_is_no_record_without_an_llm_call():
    run, seen = runner_from([])
    v, calls = classify(ISSUE, {**RESOLVED, "spans": 0, "span_list": []}, [], "m", runner=run)
    assert (v.verdict, calls, seen) == ("no_record", [], [])
    v, calls = classify(ISSUE, RESOLVED, [], "m", runner=run)
    assert (v.verdict, calls, seen) == ("no_record", [], [])


def test_parse_cli_output_reads_usage_and_cost():
    out = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "{}", "total_cost_usd": 0.0123, "duration_ms": 900,
                      "usage": {"input_tokens": 5, "output_tokens": 7, "cache_read_input_tokens": 11, "cache_creation_input_tokens": 13},
                      "modelUsage": {"claude-sonnet-5-5": {}}})
    c = parse_cli_output(out, "sonnet", "p")
    assert (c.input_tokens, c.output_tokens, c.cache_read_tokens, c.cache_write_tokens) == (5, 7, 11, 13)
    assert (c.cost_usd, c.model, c.error, c.output) == (0.0123, "claude-sonnet-5-5", None, "{}")


def test_parse_cli_output_flags_errors():
    assert parse_cli_output("garbage", "m", "p").error
    assert parse_cli_output(json.dumps({"is_error": True, "subtype": "error_during_execution"}), "m", "p").error
