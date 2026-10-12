#!/usr/bin/env python3
"""Can entire-graph find the code from the issue TITLE alone? Scored against issues that name their code."""
import json, subprocess, sys, os
repo, trace, out, shard, nsh = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
R = [r for r in json.load(open(trace)) if r.get("span_list")][shard::nsh]; res = []
for r in R:
    truth = sorted({s[0] for s in r["span_list"]})
    p = subprocess.run(["entire", "graph", "search", "--repo", repo, "--profile", "fast", "--head", "--top-k", "10", "--format", "json", "--max-context-bytes", "0", "--exclude-tests", "--query", r["title"]], capture_output=True, text=True)
    if p.returncode != 0:
        p = subprocess.run(["entire", "graph", "search", "--repo", repo, "--profile", "fast", "--head", "--top-k", "10", "--format", "json", "--max-context-bytes", "0", "--query", r["title"]], capture_output=True, text=True)
    try: hits = [(h["file_path"], h.get("symbol_name"), h["start_line"], h["end_line"]) for h in json.loads(p.stdout)["results"]]
    except Exception as e: hits = []; print("ERR", r["number"], p.stderr[:200], file=sys.stderr)
    files = [h[0] for h in hits if not h[0].endswith("_test.go")]
    rank = next((i + 1 for i, f in enumerate(files) if f in truth), None)
    res.append(dict(number=r["number"], title=r["title"], truth=truth, hits=hits, rank=rank))
json.dump(res, open(out, "w"))
