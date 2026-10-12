"""Fence resolver: issue -> code it names -> blame -> Entire checkpoint -> intent evidence.
Reads only git data (no Entire login, no `entire why`).
When an issue names nothing the resolver can locate, `entire graph search` on the issue title is the fallback.
usage: python -m fence.resolve <repo> <issues.json> <out.json> [--at-created] [--search] [--only=N,N]"""
import collections
import json
import re
import subprocess
import sys
from functools import cached_property
from typing import Any

FileRef = tuple[str, int | None, int | None]  # path, first line, last line
Span = tuple[str, int, int, str]  # file, first line, last line, how it was found: file:line, func:<sym> or search:<sym>
SEARCH_TOP_K = 5
SEARCH_TIMEOUT_S = 300

STOP = set("""New Error String Close Open Read Write Run Get Set Load Save Config Client Context Status Start Stop
Init Parse Format Name Type Kind Event Path Hook Hooks Session Sessions Check Fix Apply Is Has Do Len Add Remove main""".split())
PATH_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.go)(?:(?::|#L)(\d+)(?:[-–](?:L)?(\d+))?)?")
SYM_RE = re.compile(r"`([A-Za-z_][\w.]*)(?:\(\))?`|\b([a-z]+[A-Z]\w{4,}|[A-Z][a-z]+[A-Z]\w{3,})\(")
TRAILER_RE = re.compile(r"^(Co-Authored-By|Entire-|Signed-off-by)")


def refs_from(text: str | None) -> tuple[list[FileRef], list[str]]:
    """Go files (with optional line range) and symbols named in free text."""
    files: list[FileRef] = []
    syms: list[str] = []
    for m in PATH_RE.finditer(text or ""):
        files.append((m.group(1), int(m.group(2)) if m.group(2) else None, int(m.group(3)) if m.group(3) else None))
    for m in SYM_RE.finditer(text or ""):
        s = (m.group(1) or m.group(2)).split(".")[-1]
        if len(s) >= 5 and s not in STOP and re.match(r"^[A-Za-z_]\w*$", s):
            syms.append(s)
    return list(dict.fromkeys(files))[:12], list(dict.fromkeys(syms))[:15]


def strip_trailers(body: str) -> str:
    return "\n".join(l for l in body.splitlines() if not TRAILER_RE.match(l)).strip()


class Repo:
    """Read-only view of a git clone that has the Entire checkpoint refs fetched."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._files: dict[str, list[str]] = {}
        self._ev: dict[tuple[str, str], dict[str, Any]] = {}

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", self.path, *args], capture_output=True, text=True, errors="replace").stdout

    @cached_property
    def commit_checkpoints(self) -> dict[str, list[str]]:
        """commit -> checkpoint ids (from the Entire-Checkpoint trailer)."""
        c2t: dict[str, list[str]] = {}
        for l in self.git("log", "--format=%H %(trailers:key=Entire-Checkpoint,valueonly,separator=%x2C)").splitlines():
            p = l.split()
            c2t[p[0]] = p[1].split(",") if len(p) > 1 else []
        return c2t

    @cached_property
    def ulid_ids(self) -> set[str]:
        return {r.rsplit("/", 1)[1] for r in self.git("for-each-ref", "--format=%(refname)", "refs/entire/checkpoints/").split()}

    @cached_property
    def hex_ids(self) -> set[str]:
        ids: set[str] = set()
        for n in self.git("ls-tree", "-r", "--name-only", "entire/checkpoints/v1").splitlines():
            m = re.match(r"^([0-9a-f]{2})/([0-9a-f]{10})/metadata.json$", n)
            if m:
                ids.add(m.group(1) + m.group(2))
        return ids

    def checkpoint_tree(self, ck: str) -> str | None:
        """Tree-ish prefix for a checkpoint; append a path inside it to address a blob."""
        if ck in self.ulid_ids:
            return f"refs/entire/checkpoints/{ck[-2:]}/{ck}:"
        if ck in self.hex_ids:
            return f"entire/checkpoints/v1:{ck[:2]}/{ck[2:]}/"
        return None

    def checkpoint_files(self, ck: str) -> list[str]:
        t = self.checkpoint_tree(ck)
        if not t:
            return []
        return self.git("ls-tree", "-r", "--name-only", t if t.endswith(":") else t.rstrip("/")).splitlines()

    def checkpoint_blob(self, ck: str, name: str) -> str:
        t = self.checkpoint_tree(ck)
        return self.git("cat-file", "-p", t + name) if t else ""

    def rev_at(self, ts: str) -> str:
        return self.git("rev-list", "-1", f"--before={ts}", "HEAD").strip() or "HEAD"

    def tree_files(self, rev: str) -> list[str]:
        if rev not in self._files:
            self._files[rev] = [f for f in self.git("ls-tree", "-r", "--name-only", rev).splitlines() if f.endswith(".go")]
        return self._files[rev]

    def locate_file(self, rev: str, f: str) -> str | None:
        fs = self.tree_files(rev)
        if f in fs:
            return f
        c = [x for x in fs if x.endswith("/" + f)]
        return c[0] if len(c) == 1 else None

    def func_spans(self, rev: str, sym: str) -> list[tuple[str, int, int]]:
        out = self.git("grep", "-n", "-E", r"^func (\([^)]*\) )?%s[\[(]" % re.escape(sym), rev, "--", "*.go")
        res: list[tuple[str, int, int]] = []
        for l in out.splitlines():
            p = l.split(":", 3)
            if len(p) < 4 or p[1].endswith("_test.go"):
                continue
            f, n = p[1], int(p[2])
            src = self.git("show", f"{rev}:{f}").split("\n")
            e = n
            while e < len(src) and src[e - 1] != "}":
                e += 1
            res.append((f, n, e))
        return res[:3]

    def blame(self, rev: str, f: str, a: int, b: int) -> list[str]:
        out = self.git("blame", "--line-porcelain", "-L", f"{a},{b}", rev, "--", f)
        return [m.group(1) for m in re.finditer(r"^([0-9a-f]{40}) \d+ \d+", out, re.M)]

    def commit_message(self, commit: str) -> tuple[str, str, str]:
        """subject, date, body without trailers."""
        subj, date, body = (self.git("log", "-1", "--format=%s%x00%as%x00%b", commit).split("\x00") + ["", "", ""])[:3]
        return subj, date, strip_trailers(body)

    def search(self, query: str, top_k: int = SEARCH_TOP_K) -> list[dict[str, Any]]:
        """Functions `entire graph search` ranks for a plain-language query, over the committed tree (--head),
        so uncommitted changes in the clone are ignored. Raises RuntimeError if the search cannot run."""
        cmd = ["entire", "graph", "search", "--query", query, "--repo", self.path, "--head", "--format", "json", "--top-k", str(top_k)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=SEARCH_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"entire graph search did not run: {type(e).__name__}") from None
        if p.returncode != 0:
            raise RuntimeError(f"entire graph search exited with status {p.returncode}")
        try:
            results = json.loads(p.stdout)["results"]
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("entire graph search output was not the expected JSON") from None
        return [h for h in results if isinstance(h, dict) and h.get("kind") == "function" and h.get("symbol_name")
                and not str(h.get("file_path", "")).endswith("_test.go") and not h.get("section")][:top_k]

    def evidence(self, commit: str, ck: str) -> dict[str, Any]:
        k = (commit, ck)
        if k in self._ev:
            return self._ev[k]
        names = self.checkpoint_files(ck)
        subj, date, body = self.commit_message(commit)
        prompt = self.checkpoint_blob(ck, "0/prompt.txt") if "0/prompt.txt" in names else ""
        meta: dict[str, Any] = {}
        try:
            meta = json.loads(self.checkpoint_blob(ck, "0/metadata.json"))
        except Exception:
            pass
        self._ev[k] = dict(commit=commit[:10], date=date, subject=subj, body_chars=len(body), body=body[:1500], checkpoint=ck, agent=meta.get("agent"),
                           sessions=sum(1 for n in names if n.endswith("/full.jsonl")), prompt_chars=len(prompt), prompt=prompt[:800])
        return self._ev[k]


def searched_spans(repo: Repo, rev: str, title: str) -> list[Span]:
    """Spans for the functions the search returns. The search reads HEAD, so each function is looked up
    again at `rev`; a function that does not exist there is dropped."""
    spans: list[Span] = []
    for h in repo.search(title):
        sym = h["symbol_name"]
        if rev == "HEAD":
            found = [(h["file_path"], int(h.get("symbol_start_line") or h["start_line"]), int(h.get("symbol_end_line") or h["end_line"]))]
        else:
            at_rev = repo.func_spans(rev, sym)
            found = [x for x in at_rev if x[0] == h["file_path"]] or at_rev[:1]
        spans += [(f, a, b, "search:" + sym) for f, a, b in found]
    return list(dict.fromkeys(spans))


def resolve_issue(repo: Repo, issue: dict[str, Any], at_created: bool = False, search: bool = False) -> dict[str, Any]:
    """One issue -> spans it names -> blamed commits -> checkpoint-linked evidence rows.
    With search=True, an issue that yields no named span falls back to a title search; named spans always come first."""
    rev = repo.rev_at(issue["created_at"]) if at_created else "HEAD"
    files, syms = refs_from((issue.get("title") or "") + "\n" + (issue.get("body") or ""))
    spans: list[Span] = []
    for f, a, b in files:
        lf = repo.locate_file(rev, f)
        if lf and a:
            spans.append((lf, a, b or a, "file:line"))
    for s in syms:
        for f, a, b in repo.func_spans(rev, s):
            spans.append((f, a, b, "func:" + s))
    located_by = "named" if spans else None
    search_error = None
    if search and not spans:
        try:
            spans += searched_spans(repo, rev, issue.get("title") or "")
        except RuntimeError as e:
            search_error = str(e)
        if spans:
            located_by = "search"
    lines = 0
    linked = 0
    pairs: collections.Counter[tuple[str, str]] = collections.Counter()
    for f, a, b, _ in spans:
        for c in repo.blame(rev, f, a, b):
            lines += 1
            ids = [i for i in repo.commit_checkpoints.get(c, []) if repo.checkpoint_tree(i)]
            if ids:
                linked += 1
                for i in ids:
                    pairs[(c, i)] += 1
    ev = [dict(repo.evidence(c, i), lines=n) for (c, i), n in pairs.most_common(8)]
    out = dict(number=issue["number"], title=issue["title"], state=issue["state"], state_reason=issue.get("state_reason"), labels=issue["labels"], rev=rev[:10],
               files_named=len(files), symbols_named=len(syms), spans=len(spans), span_list=[list(x) for x in spans], lines=lines, lines_linked=linked,
               checkpoints=len({i for _, i in pairs}), evidence=ev)
    if search:  # only present when the fallback is enabled, so plain runs keep the stored output shape
        out.update(located_by=located_by, search_error=search_error)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    pos = [a for a in argv if not a.startswith("--")]
    if len(pos) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    repo_path, issues_path, out_path = pos
    only: set[int] | None = None
    for a in argv:
        if a.startswith("--only="):
            only = {int(x) for x in a[7:].split(",")}
    repo = Repo(repo_path)
    with open(issues_path) as fh:
        issues = json.load(fh)
    results = [resolve_issue(repo, it, "--at-created" in argv, "--search" in argv) for it in issues if not only or it["number"] in only]
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=1)
    sp = [r for r in results if r["spans"]]
    tr = [r for r in results if r["lines_linked"]]
    print(f"issues={len(results)} | code located={len(sp)} | traced to >=1 public checkpoint={len(tr)}")
    by: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0])
    for r in results:
        k = r["state"] + "/" + str(r["state_reason"])
        by[k][0] += 1
        by[k][1] += bool(r["spans"])
        by[k][2] += bool(r["lines_linked"])
    for k, v in sorted(by.items()):
        print(f"  {k}: issues={v[0]} located={v[1]} traced={v[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
