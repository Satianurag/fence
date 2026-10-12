import json

from fence import fix
from fence.fix import Verification, VerifyRun

ISSUE = {"number": 9, "title": "trailer breaks", "body": "details"}


def test_go_packages_most_frequent_first():
    assert fix.go_packages(["a/b/x.go", "a/c/y.go", "a/c/z_test.go", "README.md", "main.go"]) == ["./a/c/", "./a/b/", "./"]


def test_packages_for_spans_keeps_the_main_package_and_any_with_a_quarter_of_the_spans():
    spans = [["p/trailers/t.go", 1, 2, "func:A"]] * 6 + [["p/cli/a.go", 1, 2, "func:B"], ["p/strategy/m.go", 1, 2, "func:C"]]
    assert fix.packages_for_spans(spans) == ["./p/trailers/"]
    assert fix.packages_for_spans([["p/a/x.go", 1, 2, "f"], ["p/b/y.go", 1, 2, "f"]]) == ["./p/a/", "./p/b/"]


def test_test_command_is_verbose():
    assert fix.test_command(["./a/", "./b/"]) == "go test -v ./a/ ./b/"


def test_parse_verify_report():
    text = "NEWLY PASSING (2): TestA, TestB/sub_case\nNEWLY FAILING (1): TestC\nVERDICT: REGRESSION in 1 test: TestC"
    assert fix.parse_verify(text) == (["TestA", "TestB/sub_case"], ["TestC"], "REGRESSION in 1 test: TestC")
    assert fix.parse_verify("VERDICT: NO EFFECT — the target tests behave exactly as before your edit.")[:2] == ([], [])


def test_worker_prompt_carries_issue_spans_rationale_and_scope():
    p = fix.worker_prompt(ISSUE, ["./p/trailers/"], [["p/trailers/t.go", 3, 9, "func:A"]], "never considered")
    assert all(x in p for x in ("trailer breaks", "p/trailers/t.go:3-9 (func:A)", "never considered", "./p/trailers/"))
    bare = fix.worker_prompt(ISSUE, None)
    assert "trailer breaks" in bare and "Fence located" not in bare and "Keep the test" not in bare


def test_retry_prompt_carries_the_real_failure():
    p = fix.retry_prompt("FIRST", Verification(False, "still failing with the fix: TestA"), "--- FAIL: TestA")
    assert p.startswith("FIRST") and "still failing with the fix: TestA" in p and "--- FAIL: TestA" in p


class Harness:
    """Stands in for git and `entire graph verify`; verify_fix's decisions are what is under test."""

    def __init__(self, monkeypatch, tmp_path, files, base, red, green_passing, regress_failing=()):
        self.tmp = tmp_path
        self.git_calls = []
        (tmp_path / "base.json").write_text(json.dumps({"results": base}))
        monkeypatch.setattr(fix, "changed_files", lambda repo: files)
        monkeypatch.setattr(fix, "git", lambda repo, *a, **k: self.git_calls.append(a) or "")

        def run_verify(repo, cmd, purpose, record=None, baseline=None):
            if record:
                record.write_text(json.dumps({"results": red}))
                return VerifyRun(purpose, cmd, 0, "BASELINE RECORDED", 1)
            if baseline.name == "red.json":
                return VerifyRun(purpose, cmd, 0, "", 1, newly_passing=list(green_passing))
            return VerifyRun(purpose, cmd, 0, "", 1, newly_failing=list(regress_failing))
        monkeypatch.setattr(fix, "run_verify", run_verify)

    def run(self):
        return fix.verify_fix(self.tmp, ["./p/"], self.tmp / "base.json", self.tmp)


FILES = ["p/fix.go", "p/repro_test.go"]


def test_verified_when_test_fails_without_fix_and_passes_with_it(monkeypatch, tmp_path):
    h = Harness(monkeypatch, tmp_path, FILES, {"TestOld": "pass"}, {"TestOld": "pass", "TestRepro": "fail"}, ["TestRepro"])
    v = h.run()
    assert v.ok and v.repro_tests == ["TestRepro"] and v.newly_passing == ["TestRepro"]
    assert [c[0] for c in h.git_calls] == ["stash", "stash"] and h.git_calls[0][-1] == "p/fix.go"  # only the fix is set aside


def test_rejected_when_the_new_test_passes_without_the_fix(monkeypatch, tmp_path):
    v = Harness(monkeypatch, tmp_path, FILES, {"TestOld": "pass"}, {"TestOld": "pass", "TestRepro": "pass"}, []).run()
    assert not v.ok and "does not reproduce" in v.reason


def test_rejected_when_the_fix_does_not_make_it_pass(monkeypatch, tmp_path):
    v = Harness(monkeypatch, tmp_path, FILES, {}, {"TestRepro": "fail"}, []).run()
    assert not v.ok and v.reason == "still failing with the fix: TestRepro"


def test_rejected_on_regression(monkeypatch, tmp_path):
    v = Harness(monkeypatch, tmp_path, FILES, {"TestOld": "pass"}, {"TestOld": "pass", "TestRepro": "fail"}, ["TestRepro"], ["TestOld"]).run()
    assert not v.ok and v.reason == "regressions: TestOld"


def test_a_test_that_already_failed_is_not_a_reproduction(monkeypatch, tmp_path):
    v = Harness(monkeypatch, tmp_path, FILES, {"TestFlaky": "fail"}, {"TestFlaky": "fail"}, []).run()
    assert not v.ok and v.repro_tests == []


def test_rejected_without_test_or_without_code_or_outside_scope(monkeypatch, tmp_path):
    assert Harness(monkeypatch, tmp_path, ["p/fix.go"], {}, {}, []).run().reason == "the worker added no test"
    assert Harness(monkeypatch, tmp_path, ["p/repro_test.go"], {}, {}, []).run().reason == "the worker changed no non-test code"
    assert Harness(monkeypatch, tmp_path, [], {}, {}, []).run().reason == "the worker changed no files"
    assert "outside the baselined packages" in Harness(monkeypatch, tmp_path, FILES + ["q/other.go"], {}, {}, []).run().reason
