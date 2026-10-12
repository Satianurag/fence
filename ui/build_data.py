#!/usr/bin/env python3
"""Build ui/data/fence.json and the terrain binaries from real git history and resolver output.

usage: build_data.py <cli clone> <trace_at_created.json> <cli_issues.json> <out dir> [featured issue]

Everything here is read from git, from data/ files produced by fence/resolve.py, or from the run records
under runs/ (fix runs, compare.json, eval.json, scan.json, decisions.md). Nothing is invented.
"""
import collections, datetime, json, os, re, struct, subprocess, sys

RUNS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runs")

repo, trace_path, issues_path, out = sys.argv[1:5]
FEATURED = int(sys.argv[5]) if len(sys.argv) > 5 else 2256
W, H = 200, 160
PREFIX = "cmd/entire/cli/"


def git(*a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, errors="replace").stdout


def month_index(d, first):
    return (d.year - first.year) * 12 + d.month - first.month


# ---- commits: date, checkpoint trailer, per-file added/deleted lines
commits, cur = [], None
log = git("log", "--reverse", "--format=@%H|%as|%(trailers:key=Entire-Checkpoint,valueonly,separator=%x2C)|%s", "--numstat", "--", "cmd/entire/cli")
for line in log.splitlines():
    if line.startswith("@"):
        h, date, ck, subj = line[1:].split("|", 3)
        cur = dict(hash=h, date=date, ckpt=[c for c in ck.split(",") if c], subject=subj, files=[])
        commits.append(cur)
    elif line.strip() and cur is not None:
        p = line.split("\t")
        if len(p) != 3 or p[0] == "-":
            continue
        path = re.sub(r"\{[^{}]* => ([^{}]*)\}", r"\1", p[2]).replace("//", "/")
        if " => " in path:
            path = path.split(" => ")[1]
        cur["files"].append((path, int(p[0]), int(p[1])))

head_files = [f for f in git("ls-files", "cmd/entire/cli").splitlines() if f.endswith(".go") and not f.endswith("_test.go")]
head_files.sort()
xof = {f: min(W - 1, i * W // len(head_files)) for i, f in enumerate(head_files)}
first = datetime.date.fromisoformat(commits[0]["date"])
last = datetime.date.fromisoformat(commits[-1]["date"])
tspan = max(1, (last - first).days)
zof = lambda d: min(H - 1, (datetime.date.fromisoformat(d) - first).days * H // tspan)

# ---- packages: contiguous x ranges, real file and line counts
pk = collections.OrderedDict()
for f in head_files:
    name = f[len(PREFIX):].split("/")[0]
    name = name if "/" in f[len(PREFIX):] else "(root)"
    pk.setdefault(name, []).append(f)
loc = {}
numstat = git("ls-tree", "-r", "-l", "HEAD", "cmd/entire/cli")
for line in numstat.splitlines():
    meta, path = line.split("\t", 1)
    size = meta.split()[3]
    if path in xof and size.isdigit():
        loc[path] = int(size)

# ---- heightmap: cumulative net lines per (x,z) cell, snapshot per time row
height = [[0.0] * W for _ in range(H)]
cover_add = [[0.0] * W for _ in range(H)]
cover_ck = [[0.0] * W for _ in range(H)]
month_stats = collections.OrderedDict()
for c in commits:
    z = zof(c["date"])
    m = c["date"][:7]
    ms = month_stats.setdefault(m, dict(m=m, commits=0, checkpoints=0, added=0, deleted=0, agents=collections.Counter()))
    ms["commits"] += 1
    ms["checkpoints"] += 1 if c["ckpt"] else 0
    for path, a, d in c["files"]:
        ms["added"] += a
        ms["deleted"] += d
        if path in xof:
            x = xof[path]
            for zz in range(z, H):
                height[zz][x] += a - d
            cover_add[z][x] += a
            if c["ckpt"]:
                cover_ck[z][x] += a

flat = [max(0.0, v) for row in height for v in row]
mx = max(flat) or 1.0
# log scale so one giant file does not flatten the rest
norm = [int(3000 * (pow(v, 0.5) / pow(mx, 0.5))) for v in flat]
struct_h = struct.pack("<%dh" % len(norm), *norm)
open(f"{out}/fence-terrain.bin", "wb").write(struct_h)

# coverage at HEAD time (final): 0..255
cov = []
for z in range(H):
    for x in range(W):
        a = cover_add[z][x]
        cov.append(0 if a <= 0 else int(255 * cover_ck[z][x] / a))
open(f"{out}/fence-cover.bin", "wb").write(bytes(cov))

packages = []
for name, files in pk.items():
    xs = [xof[f] for f in files]
    packages.append(dict(name=name.upper() if name != "(root)" else "CLI", x0=min(xs), x1=max(xs), files=len(files),
                         lines=sum(loc.get(f, 0) for f in files), linked=0))

# ---- issues + resolver evidence
issues = {i["number"]: i for i in json.load(open(issues_path))}
trace = {t["number"]: t for t in json.load(open(trace_path))}
commit_by_hash = {c["hash"][:10]: c for c in commits}


def clean(s, n):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def issue_row(n):
    i, t = issues[n], trace[n]
    return dict(number=n, title=clean(i["title"], 90), state=i["state"], reason=i.get("state_reason"),
                lines=t["lines"], linked=t["lines_linked"], checkpoints=t["checkpoints"], created=(i.get("created_at") or "")[:10])


ranked = sorted([t for t in trace.values() if t["checkpoints"]], key=lambda t: -t["lines_linked"])
top = [issue_row(t["number"]) for t in ranked[:14]]


def load(name):
    with open(os.path.join(RUNS, name)) as fh:
        return json.load(fh)


def evidence_path(t):
    """Evidence commits of one resolver result, oldest first, placed on the terrain grid."""
    path = []
    for e in t["evidence"]:
        c = commit_by_hash.get(e["commit"][:10])
        if not c:
            continue
        files = sorted(c["files"], key=lambda f: -(f[1] + f[2]))
        f0 = next((f for f in files if f[0] in xof), None)
        if not f0:
            continue
        path.append(dict(commit=e["commit"][:10], date=e["date"], subject=clean(e["subject"], 70), body=clean(e.get("body"), 220),
                         file=f0[0][len(PREFIX):], x=xof[f0[0]], z=zof(e["date"]), lines=e["lines"], checkpoint=e["checkpoint"], linked=e["lines"] if e["checkpoint"] else 0,
                         agent=e.get("agent") or "", prompt=clean(e.get("prompt"), 160), promptChars=int(e.get("prompt_chars") or 0),
                         added=sum(f[1] for f in c["files"]), deleted=sum(f[2] for f in c["files"])))
    path.sort(key=lambda p: p["date"])
    return path


def run_summary(run):
    """What a fix run did, reduced to what the dashboard shows."""
    if not run:
        return None
    v = run.get("verdict") or {}
    last = (run.get("attempts") or [{}])[-1].get("verification") or {}
    return dict(verdict=v.get("verdict"), confidence=v.get("confidence"), rationale=clean(v.get("rationale"), 420),
                quotes=[dict(quote=clean(q["quote"], 240), source=q["source"], commit=q["commit"], checkpoint=q["checkpoint"]) for q in v.get("quotes") or []],
                outcome=run.get("outcome"), action=run.get("action"), reason=clean(run.get("reason"), 200), question=run.get("question"),
                attempts=len(run.get("attempts") or []), files=run.get("files_changed") or [], tests=run.get("tests_newly_passing") or [],
                verification=clean(last.get("reason"), 160), commit=((run.get("commit") or {}).get("commit") or "")[:10], branch=(run.get("commit") or {}).get("branch"),
                cost=(run.get("cost") or {}).get("usd", 0.0), duration=run.get("duration_s"), trace=run.get("trace_id"))


def replay(n):
    t, i = trace[n], issues[n]
    path = evidence_path(t)
    agents = collections.Counter(p["agent"] or "UNKNOWN" for p in path)
    fix = lambda suffix: load(f"fix/{n}{suffix}.json") if os.path.exists(os.path.join(RUNS, f"fix/{n}{suffix}.json")) else None
    return dict(issue=dict(number=n, title=clean(i["title"], 110), state=i["state"], created=(i.get("created_at") or "")[:10], rev=t["rev"],
                           checkpoints=sum(1 for p in path if p["checkpoint"])),
                path=path, spans=[dict(file=s[0][len(PREFIX):], a=s[1], b=s[2], sym=s[3].replace("func:", "")) for s in t["span_list"]],
                agents=[dict(name=k, count=v) for k, v in agents.most_common()], fence=run_summary(fix("")), baseline=run_summary(fix(".baseline")))


def parse_decisions(text):
    """Entries of runs/decisions.md: one per '### #N title' section."""
    out, group = [], ""
    for block in re.split(r"\n(?=##+ )", text):
        head = block.split("\n", 1)[0]
        if head.startswith("## "):
            group = head[3:].strip()
            continue
        m = re.match(r"### #(\d+) (.*)", head)
        if not m:
            continue
        flat = re.sub(r"\s+", " ", block).replace('\\"', "'")
        quote = re.search(r"\*\*Quote\*\* \(([^,]+), commit `([0-9a-f]+)`, checkpoint `([^`]+)`\): \"(.*?)\"\.?(?= (?:Second|Also|Two) | - \*\*What)", flat)
        did = re.search(r"\*\*What maintainers did\*\*: (.*?) - \*\*Reading", flat)
        reading = re.search(r"\*\*Reading: (.*?)\.\*\* (.*)$", flat)
        out.append(dict(number=int(m.group(1)), title=clean(m.group(2), 90), group=group.split(",")[0].lower(),
                        source=quote.group(1) if quote else "", commit=quote.group(2) if quote else "", checkpoint=quote.group(3) if quote else "",
                        quote=clean(quote.group(4), 260) if quote else "", did=clean(did.group(1), 300) if did else "",
                        reading=reading.group(1) if reading else "", note=clean(reading.group(2), 320) if reading else ""))
    return out


compare = load("compare.json")
evalr = load("eval.json")
scan = load("scan.json")
with open(os.path.join(RUNS, "decisions.md")) as fh:
    decisions = parse_decisions(fh.read())
replays = {str(n): replay(n) for n in dict.fromkeys([FEATURED] + [r["issue"] for r in compare["rows"]])}
replays = {k: r for k, r in replays.items() if r["path"]}  # an issue whose evidence lies outside cmd/entire/cli has nothing to draw
feat = replays[str(FEATURED)]
path, spans = feat["path"], feat["spans"]

months = [dict(m=m, **{k: v for k, v in s.items() if k != "agents" and k != "m"}) for m, s in month_stats.items()]
agents = collections.Counter(p["agent"] or "UNKNOWN" for p in path)
states = collections.Counter()
for i in issues.values():
    states["open" if i["state"] == "open" else (i.get("state_reason") or "closed")] += 1
cum, ci, cc = [], 0, 0
for s in months:
    ci += s["commits"]; cc += s["checkpoints"]
    cum.append(dict(m=s["m"], commits=ci, checkpoints=cc))

head = git("log", "-1", "--format=%h|%as").strip().split("|")
fence = dict(
    meta=dict(repo="entireio/cli", head=head[0], first=str(first), last=str(last), commits=len(commits), files=len(head_files),
              generated=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
              sources=["git log --numstat (entireio/cli)", "git ls-tree HEAD", "entireio/cli-checkpoints refs", "data/trace_at_created.json", "data/cli_issues.json",
                       "runs/fix/*.json", "runs/compare.json", "runs/eval.json", "runs/scan.json", "runs/decisions.md"]),
    grid=dict(w=W, h=H, xLabels=[dict(name=p["name"], x0=p["x0"], x1=p["x1"]) for p in packages], t0=str(first), t1=str(last)),
    packages=packages,
    issue=feat["issue"], path=path, spans=spans, replays=replays,
    compare=dict(what=compare["what"], limits=compare["limits"], rows=[
        dict(issue=r["issue"], mode=r["mode"], verdict=r.get("verdict"), action=r.get("action"), verified=r.get("verified"), attempts=r.get("attempts"),
             files=len(r.get("files_changed") or []), tests=len(r.get("tests_newly_passing") or []), cost=r.get("cost_usd"), duration=r.get("duration_s"), trace=r.get("trace_id"))
        for r in compare["rows"]]),
    eval=dict(what=evalr["what"], limits=evalr["limits"], table=evalr["summary"]["verdict_by_state_reason"], agreement=evalr["summary"]["agreement"],
              baseline=evalr["summary"]["always_mistake_baseline"], issues=evalr["summary"]["issues"], cost=evalr["summary"]["total_cost_usd"]),
    scan=dict(issues=scan["summary"]["issues"], cost=scan["summary"]["total_cost_usd"],
              verdicts=dict(collections.Counter(r["verdict"] for r in scan["issues"]))),
    decisions=decisions, issues=top, months=months, agents=[dict(name=k, count=v) for k, v in agents.most_common()], cumulative=cum, states=dict(states),
    totals=dict(issues=len(issues), traced=sum(1 for t in trace.values() if t["checkpoints"]), located=sum(1 for t in trace.values() if t["spans"]),
                commits=len(commits), checkpointCommits=sum(1 for c in commits if c["ckpt"]), coveragePct=round(100 * sum(1 for v in cov if v > 0) / len(cov), 1)),
)
json.dump(fence, open(f"{out}/fence.json", "w"), separators=(",", ":"))
print("wrote", {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in fence.items()}, "path", [(p["commit"], p["x"], p["z"], p["agent"]) for p in path])
print(fence["totals"], "sizes", len(struct_h), len(cov))
