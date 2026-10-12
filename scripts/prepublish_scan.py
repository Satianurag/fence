#!/usr/bin/env python3
"""Pre-publication scan: every blob reachable from every ref (branches, remotes, refs/entire/checkpoints/*, tags)
is searched for the values in .env, for email addresses and for absolute home-directory paths.
Values from .env are read at runtime and never printed; only variable names and counts are.
usage: python scripts/prepublish_scan.py [--show] [--json out.json]"""
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
HOME = re.compile(rb"(?:/Users|/home)/[A-Za-z0-9._-]+")
NOREPLY = (b"noreply", b"no-reply", b"example.com", b"example.org")
IGNORED_HOMES = (b"/Users/runner", b"/home/runner", b"/home/user", b"/Users/me", b"/Users/you", b"/home/me")


def git(*args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args], input=data, capture_output=True, check=True).stdout


def ref_type(ref: str) -> str:
    for prefix, name in (("refs/entire/checkpoints/", "entire checkpoint refs"), ("refs/heads/entire/", "entire branches"), ("refs/remotes/origin/entire/", "entire branches (remote)"),
                         ("refs/heads/", "branches"), ("refs/remotes/", "remote branches"), ("refs/tags/", "tags")):
        if ref.startswith(prefix):
            return name
    return "other refs"


def env_values() -> dict[str, bytes]:
    out = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            v = v.strip().strip("\"'")
            if len(v) >= 6:
                out[k.strip()] = v.encode()
    return out


def main() -> int:
    show = "--show" in sys.argv
    refs = [r for r in git("for-each-ref", "--format=%(refname)").decode().split() if r]
    blob_types: dict[str, set[str]] = collections.defaultdict(set)
    blob_path: dict[str, str] = {}
    commits: dict[str, set[str]] = collections.defaultdict(set)
    for ref in refs:
        t = ref_type(ref)
        for line in git("rev-list", "--objects", ref).decode(errors="replace").splitlines():
            sha, _, path = line.partition(" ")
            blob_types[sha].add(t)
            blob_path.setdefault(sha, path or "")
    kinds = dict(l.split()[:2] for l in git("cat-file", "--batch-check=%(objectname) %(objecttype)", data="\n".join(blob_types).encode() + b"\n").decode().splitlines())
    blobs = [s for s in blob_types if kinds.get(s) == "blob"]
    commit_ids = [s for s in blob_types if kinds.get(s) == "commit"]
    secrets = env_values()
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)  # category -> ref type -> hits
    distinct: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    where: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    def scan(sha: str, data: bytes, label: str) -> None:
        found: list[tuple[str, bytes]] = []
        for name, value in secrets.items():
            n = data.count(value)
            found += [(f"env:{name}", b"<value>")] * n
        for m in EMAIL.findall(data):
            found.append(("email (noreply/example)" if any(x in m.lower() for x in NOREPLY) else "email", m))
        for m in HOME.findall(data):
            if m not in IGNORED_HOMES:
                found.append(("home path", m))
        for cat, val in found:
            for t in blob_types[sha]:
                counts[cat][t] += 1
            distinct[cat][val] += 1
            where[cat][label] += 1

    out = git("cat-file", "--batch", data="\n".join(blobs + commit_ids).encode() + b"\n")
    pos = 0
    while pos < len(out):
        nl = out.index(b"\n", pos)
        sha, kind, size = out[pos:nl].decode().split()
        body = out[nl + 1:nl + 1 + int(size)]
        pos = nl + 1 + int(size) + 1
        scan(sha, body, blob_path.get(sha) or "(root)" if kind == "blob" else "(commit message or author)")

    by_type = collections.Counter(t for r in refs for t in [ref_type(r)])
    report = dict(refs=dict(by_type), blobs=len(blobs), commits=len(commit_ids), env_vars_checked=sorted(secrets),
                  hits={c: dict(v) for c, v in sorted(counts.items())},
                  distinct={c: len(v) for c, v in sorted(distinct.items())},
                  top_locations={c: where[c].most_common(8) for c in sorted(where)})
    print(json.dumps(report, indent=1))
    if show:  # local inspection only; never paste this output anywhere public
        for c in sorted(distinct):
            if not c.startswith("env:"):
                print(c, [(k.decode(errors="replace"), n) for k, n in distinct[c].most_common(40)])
    if "--json" in sys.argv:
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(report, indent=1))
    secret = lambda c: c.startswith("env:") and any(w in c for w in ("KEY", "TOKEN", "SECRET", "PASSWORD", "_ID"))
    return 1 if any(secret(c) for c in counts) else 0


if __name__ == "__main__":
    sys.exit(main())
