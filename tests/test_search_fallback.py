import json
import subprocess

import pytest

from fence import resolve
from fence.resolve import Repo, resolve_issue

SEARCH_OUT = json.dumps({"results": [
    {"rank": 1, "file_path": "cmd/a/hooks.go", "start_line": 505, "end_line": 532, "symbol_start_line": 510, "symbol_end_line": 532, "kind": "function", "symbol_name": "loadSettings"},
    {"rank": 2, "file_path": "cmd/a/types.go", "start_line": 4, "end_line": 8, "kind": "type", "symbol_name": "Settings"},
    {"rank": 3, "file_path": "cmd/a/hooks_test.go", "start_line": 1, "end_line": 9, "kind": "function", "symbol_name": "TestLoad"},
    {"rank": 4, "file_path": "cmd/a/hooks_test.go", "start_line": 1, "end_line": 9, "kind": "function", "qualified_name": "readSettings", "section": "covering-test"},
]})


class FakeRepo(Repo):
    """Git answers are canned; only search() reaches subprocess."""
    commit_checkpoints = {"c" * 40: ["ck1"]}

    def rev_at(self, ts):
        return "r" * 40

    def locate_file(self, rev, f):
        return f

    def func_spans(self, rev, sym):
        return {"loadSettings": [("cmd/a/hooks.go", 400, 420)], "NamedThing": [("cmd/a/named.go", 1, 3)]}.get(sym, [])

    def blame(self, rev, f, a, b):
        return ["c" * 40] * (b - a + 1)

    def checkpoint_tree(self, ck):
        return "tree:"

    def evidence(self, commit, ck):
        return dict(commit=commit[:10], checkpoint=ck)


def issue(body=""):
    return dict(number=7, title="remove deletes unrelated settings keys", body=body, state="open", labels=[], created_at="2026-01-01T00:00:00Z")


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=SEARCH_OUT, stderr="")
    monkeypatch.setattr(resolve.subprocess, "run", fake_run)
    return seen


def test_search_command_reads_the_committed_tree(calls):
    r = resolve_issue(FakeRepo("/target"), issue(), at_created=True, search=True)
    assert calls == [["entire", "graph", "search", "--query", "remove deletes unrelated settings keys", "--repo", "/target", "--head", "--format", "json", "--top-k", "5"]]
    assert r["located_by"] == "search" and r["search_error"] is None


def test_only_functions_outside_tests_become_spans_relocated_at_rev(calls):
    r = resolve_issue(FakeRepo("/target"), issue(), at_created=True, search=True)
    assert r["span_list"] == [["cmd/a/hooks.go", 400, 420, "search:loadSettings"]]
    assert (r["lines"], r["lines_linked"], r["checkpoints"]) == (21, 21, 1)


def test_at_head_uses_the_lines_the_search_returned(calls):
    r = resolve_issue(FakeRepo("/target"), issue(), at_created=False, search=True)
    assert r["span_list"] == [["cmd/a/hooks.go", 510, 532, "search:loadSettings"]]


def test_named_refs_win_and_search_is_not_run(calls):
    r = resolve_issue(FakeRepo("/target"), issue("`NamedThing` is wrong"), at_created=True, search=True)
    assert calls == []
    assert r["located_by"] == "named" and r["span_list"] == [["cmd/a/named.go", 1, 3, "func:NamedThing"]]


def test_search_off_keeps_the_stored_output_shape(calls):
    r = resolve_issue(FakeRepo("/target"), issue(), at_created=True)
    assert calls == [] and r["spans"] == 0 and "located_by" not in r


def test_search_failure_is_recorded_not_raised(monkeypatch):
    monkeypatch.setattr(resolve.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2, stdout="", stderr="boom"))
    r = resolve_issue(FakeRepo("/target"), issue(), at_created=True, search=True)
    assert r["spans"] == 0 and r["located_by"] is None and r["search_error"] == "entire graph search exited with status 2"
