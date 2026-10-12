import json

from fence import evaluate

ISSUES = [
    dict(number=1, title="a", state="closed", state_reason="completed", labels=[]),
    dict(number=2, title="b", state="closed", state_reason="not_planned", labels=[]),
    dict(number=3, title="c", state="open", state_reason=None, labels=[]),
    dict(number=4, title="d", state="closed", state_reason="completed", labels=[]),
    dict(number=5, title="e", state="closed", state_reason="duplicate", labels=[]),
    dict(number=6, title="f", state="closed", state_reason="completed", labels=[]),
]
STORED = [dict(number=1, spans=2, lines_linked=5), dict(number=2, spans=1, lines_linked=1), dict(number=3, spans=1, lines_linked=1),
          dict(number=4, spans=1, lines_linked=0), dict(number=5, spans=1, lines_linked=3), dict(number=6, spans=0, lines_linked=0)]


def run(verdict, cost=0.01):
    return dict(verdict=dict(verdict=verdict, confidence=0.8, quotes=[]), located=dict(spans=1, lines=2, lines_linked=1), evidence=[], cost=dict(usd=cost), trace_id="t", error=None)


def test_select_closed_named_and_traced():
    assert evaluate.select(ISSUES, STORED) == [1, 2, 5]


def test_agreement_rules():
    by = {i["number"]: i for i in ISSUES}
    assert evaluate.row(by[1], run("mistake"))["agrees"] is True
    assert evaluate.row(by[1], run("decision"))["agrees"] is False
    assert evaluate.row(by[2], run("decision"))["agrees"] is True
    assert evaluate.row(by[2], run("no_record"))["agrees"] is None  # abstention
    assert evaluate.row(by[5], run("mistake"))["agrees"] is None  # duplicate has no expected verdict


def test_run_eval_writes_table_limits_and_total_cost(tmp_path):
    verdicts = {1: "mistake", 2: "mistake", 5: "no_record"}
    calls = []

    def one(n):
        calls.append(n)
        r = run(verdicts[n])
        (tmp_path / "eval").mkdir(exist_ok=True)
        (tmp_path / "eval" / f"{n}.json").write_text(json.dumps(r))
        return r
    report = evaluate.run_eval(ISSUES, [1, 2, 5], one, tmp_path, workers=1)
    saved = json.loads((tmp_path / "eval.json").read_text())
    assert saved == report and sorted(calls) == [1, 2, 5]
    sm = report["summary"]
    assert sm["verdict_by_state_reason"] == {"completed": {"mistake": 1}, "duplicate": {"no_record": 1}, "not_planned": {"mistake": 1}}
    assert (sm["agreement"]["compared"], sm["agreement"]["agree"], sm["total_cost_usd"]) == (2, 1, 0.03)
    assert any("feature work" in l for l in report["limits"]) and any("very small" in l for l in report["limits"])
    assert report["limits"][0].startswith('A classifier that always answered "mistake" would agree on 1 of 2')
    evaluate.run_eval(ISSUES, [1, 2, 5], one, tmp_path, workers=1)  # resume: nothing is run again
    assert len(calls) == 3


def test_compare_rows_for_both_modes(tmp_path):
    (tmp_path / "fix").mkdir()
    fence_run = dict(verdict=dict(verdict="decision"), outcome="ask", action="not_fixed", reason="asked", attempts=[], files_changed=[],
                     tests_newly_passing=[], cost=dict(usd=0.03), duration_s=12.0, commit=None, trace_id="t1", error=None)
    base_run = dict(verdict=None, action="fixed", attempts=[dict(verification=dict(ok=False, reason="x")), dict(verification=dict(ok=True, reason="1 test(s) newly passing, no regressions"))],
                    files_changed=["a.go", "a_test.go"], tests_newly_passing=["TestA"], cost=dict(usd=0.4), duration_s=90.0, commit=dict(commit="abc"), trace_id="t2", error=None)
    (tmp_path / "fix" / "7.json").write_text(json.dumps(fence_run))
    (tmp_path / "fix" / "7.baseline.json").write_text(json.dumps(base_run))
    rows = evaluate.build_compare(tmp_path, [7, 8])["rows"]
    assert [(r["issue"], r["mode"], r["ran"]) for r in rows] == [(7, "fence", True), (7, "baseline", True), (8, "fence", False), (8, "baseline", False)]
    assert (rows[0]["verdict"], rows[0]["action"], rows[0]["attempts"], rows[0]["verified"]) == ("decision", "not_fixed", 0, False)
    assert (rows[1]["action"], rows[1]["attempts"], rows[1]["verified"], rows[1]["tests_newly_passing"], rows[1]["cost_usd"]) == ("fixed", 2, True, ["TestA"], 0.4)
    assert json.loads((tmp_path / "compare.json").read_text())["rows"] == rows
