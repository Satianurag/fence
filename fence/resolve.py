#!/usr/bin/env python3
"""Fence resolver v0: issue -> code it names -> blame -> Entire checkpoint -> intent evidence.
Reads only git data (no Entire login, no `entire why`).
usage: resolve.py <repo> <issues.json> <out.json> [--at-created] [--only N,N]"""
import subprocess, re, sys, json, collections
repo, issues_path, out_path = sys.argv[1:4]
AT_CREATED = "--at-created" in sys.argv
ONLY = None
for a in sys.argv:
    if a.startswith("--only="): ONLY = set(int(x) for x in a[7:].split(","))
def g(*a, inp=None):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, input=inp, errors="replace").stdout
# commit -> checkpoint ids (from the Entire-Checkpoint trailer)
c2t = {}
for l in g("log", "--format=%H %(trailers:key=Entire-Checkpoint,valueonly,separator=%x2C)").splitlines():
    p = l.split()
    c2t[p[0]] = p[1].split(",") if len(p) > 1 else []
ULID = {r.rsplit("/", 1)[1] for r in g("for-each-ref", "--format=%(refname)", "refs/entire/checkpoints/").split()}
HEX = set()
for n in g("ls-tree", "-r", "--name-only", "entire/checkpoints/v1").splitlines():
    m = re.match(r"^([0-9a-f]{2})/([0-9a-f]{10})/metadata.json$", n)
    if m: HEX.add(m.group(1) + m.group(2))
def ck_tree(i):
    if i in ULID: return f"refs/entire/checkpoints/{i[-2:]}/{i}:"
    if i in HEX: return f"entire/checkpoints/v1:{i[:2]}/{i[2:]}/"
    return None
STOP = set("""New Error String Close Open Read Write Run Get Set Load Save Config Client Context Status Start Stop
Init Parse Format Name Type Kind Event Path Hook Hooks Session Sessions Check Fix Apply Is Has Do Len Add Remove main""".split())
PATH_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.go)(?:(?::|#L)(\d+)(?:[-–](?:L)?(\d+))?)?")
SYM_RE = re.compile(r"`([A-Za-z_][\w.]*)(?:\(\))?`|\b([a-z]+[A-Z]\w{4,}|[A-Z][a-z]+[A-Z]\w{3,})\(")
def refs_from(text):
    files, syms = [], []
    for m in PATH_RE.finditer(text or ""):
        files.append((m.group(1), int(m.group(2)) if m.group(2) else None, int(m.group(3)) if m.group(3) else None))
    for m in SYM_RE.finditer(text or ""):
        s = (m.group(1) or m.group(2)).split(".")[-1]
        if len(s) >= 5 and s not in STOP and re.match(r"^[A-Za-z_]\w*$", s): syms.append(s)
    return list(dict.fromkeys(files))[:12], list(dict.fromkeys(syms))[:15]
def rev_at(ts):
    r = g("rev-list", "-1", f"--before={ts}", "HEAD").strip()
    return r or "HEAD"
_files = {}
def tree_files(rev):
    if rev not in _files: _files[rev] = [f for f in g("ls-tree", "-r", "--name-only", rev).splitlines() if f.endswith(".go")]
    return _files[rev]
def locate_file(rev, f):
    fs = tree_files(rev)
    if f in fs: return f
    c = [x for x in fs if x.endswith("/" + f)]
    return c[0] if len(c) == 1 else None
def func_spans(rev, sym):
    out = g("grep", "-n", "-E", r"^func (\([^)]*\) )?%s[\[(]" % re.escape(sym), rev, "--", "*.go")
    res = []
    for l in out.splitlines():
        p = l.split(":", 3)
        if len(p) < 4 or p[1].endswith("_test.go"): continue
        f, n = p[1], int(p[2]); src = g("show", f"{rev}:{f}").split("\n"); e = n
        while e < len(src) and src[e - 1] != "}": e += 1
        res.append((f, n, e))
    return res[:3]
def blame(rev, f, a, b):
    out = g("blame", "--line-porcelain", "-L", f"{a},{b}", rev, "--", f)
    return [m.group(1) for m in re.finditer(r"^([0-9a-f]{40}) \d+ \d+", out, re.M)]
_ev = {}
def evidence(commit, ck):
    k = (commit, ck)
    if k in _ev: return _ev[k]
    t = ck_tree(ck); names = g("ls-tree", "-r", "--name-only", t.rstrip("/") if not t.endswith(":") else t).splitlines() if t else []
    subj, date, body = (g("log", "-1", "--format=%s%x00%as%x00%b", commit).split("\x00") + ["", "", ""])[:3]
    body = "\n".join(l for l in body.splitlines() if not re.match(r"^(Co-Authored-By|Entire-|Signed-off-by)", l)).strip()
    prompt = g("cat-file", "-p", t + "0/prompt.txt") if "0/prompt.txt" in names else ""
    meta = {}
    try: meta = json.loads(g("cat-file", "-p", t + "0/metadata.json"))
    except Exception: pass
    _ev[k] = dict(commit=commit[:10], date=date, subject=subj, body_chars=len(body), body=body[:1500], checkpoint=ck, agent=meta.get("agent"),
                  sessions=sum(1 for n in names if n.endswith("/full.jsonl")), prompt_chars=len(prompt), prompt=prompt[:800])
    return _ev[k]
issues = json.load(open(issues_path)); results = []
for it in issues:
    if ONLY and it["number"] not in ONLY: continue
    rev = rev_at(it["created_at"]) if AT_CREATED else "HEAD"
    files, syms = refs_from((it.get("title") or "") + "\n" + (it.get("body") or ""))
    spans = []
    for f, a, b in files:
        lf = locate_file(rev, f)
        if lf and a: spans.append((lf, a, b or a, "file:line"))
    for s in syms:
        for f, a, b in func_spans(rev, s): spans.append((f, a, b, "func:" + s))
    lines = 0; linked = 0; pairs = collections.Counter()
    for f, a, b, _ in spans:
        for c in blame(rev, f, a, b):
            lines += 1; ids = [i for i in c2t.get(c, []) if ck_tree(i)]
            if ids:
                linked += 1
                for i in ids: pairs[(c, i)] += 1
    ev = [dict(evidence(c, i), lines=n) for (c, i), n in pairs.most_common(8)]
    results.append(dict(number=it["number"], title=it["title"], state=it["state"], state_reason=it.get("state_reason"), labels=it["labels"], rev=rev[:10],
                        files_named=len(files), symbols_named=len(syms), spans=len(spans), span_list=[list(x) for x in spans], lines=lines, lines_linked=linked, checkpoints=len({i for _, i in pairs}), evidence=ev))
json.dump(results, open(out_path, "w"), indent=1)
n = len(results); sp = [r for r in results if r["spans"]]; tr = [r for r in results if r["lines_linked"]]
print(f"issues={n} | code located={len(sp)} | traced to >=1 public checkpoint={len(tr)}")
by = collections.defaultdict(lambda: [0, 0, 0])
for r in results:
    k = r["state"] + "/" + str(r["state_reason"]); by[k][0] += 1; by[k][1] += bool(r["spans"]); by[k][2] += bool(r["lines_linked"])
for k, v in sorted(by.items()): print(f"  {k}: issues={v[0]} located={v[1]} traced={v[2]}")
