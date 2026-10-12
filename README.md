# Fence

Before an agent changes code for a GitHub issue, Fence finds out why the code is the way it is.

1. It locates the code the issue is about and traces each line, through `git blame`, to the Entire checkpoint of the session that wrote it.
2. It reads the commit message, session transcript and prompt on record and gives a verdict: **mistake** (nobody chose this behaviour), **decision** (someone did, here is the quote) or **no_record**.
3. On a mistake it lets a worker fix the issue in a private clone, then checks the result itself. On a decision it changes nothing and asks a maintainer once.
4. It remembers the answer, so the next issue of the same kind is settled without asking.

**What Fence is and is not.** The worker that edits code is headless Claude Code. Fence is the layer around it: the policy (when may code be changed), the verification (did the change really work) and the measurement (what did it cost, what happened). The name is Chesterton's fence: don't remove one until you know why it was put up.

Everything below was measured on [entireio/cli](https://github.com/entireio/cli), a public repository whose commits carry Entire checkpoints. Numbers are from single runs; the limits are printed next to them.

## The result in one table

Five open issues, each run two ways: through Fence, and straight to the same worker with no investigation ("baseline"). Source: [runs/compare.json](runs/compare.json).

| Issue | Fence verdict | Fence | Cost | Time | Baseline | Cost | Time |
|---|---|---|---|---|---|---|---|
| #2256 | mistake | fixed, 1 attempt | $0.1381 | 48.5 s | fixed, 1 attempt | $0.0934 | 38.6 s |
| #1870 | mistake | fixed, 1 attempt | $0.1402 | 48.2 s | fixed, 1 attempt | $0.0650 | 34.1 s |
| #2274 | mistake | fixed, 1 attempt | $0.1215 | 56.6 s | fixed on attempt 2 | $0.2069 | 1659 s |
| #2686 | mistake | fixed, 1 attempt | $0.0763 | 47.0 s | fixed, 1 attempt | $0.0558 | 24.4 s |
| #2263 | decision | **changed nothing, asked** | $0.0048 | 8.6 s | changed the code | $0.1174 | 456 s |

What this does and does not show:

- **On three of the four mistake issues the investigation added cost and time and changed nothing.** The baseline fixed them on the first attempt for 27 to 54% less.
- **#2263 is the behavioural difference.** The record shows a maintainer chose the behaviour the issue complains about. Fence stopped and wrote [a question](runs/questions/2263.md). The baseline worker changed it, and to get there flipped an assertion in an existing test from `OverwritesHooks=false` to `=true`. A maintainer PR making the same change is still open, so the baseline may be right on substance. The point is that it overrode a recorded decision, and rewrote the test guarding it, without anyone being asked.
- **The verifier was then changed, and the same baseline run repeated once.** Fence now rejects any change to a test that existed before the worker ran. In the repeat ([runs/fix/2263.baseline.json](runs/fix/2263.baseline.json), trace `b957c492ec5097bd8603265736271951`) the worker flipped the same assertion again and was rejected as `existing_test_modified`; told to restore it, it kept its fix, and the restored test then failed. Outcome: `fix_failed`, nothing committed, $0.1352. The first run is kept as [runs/fix/2263.baseline.before-test-guard.json](runs/fix/2263.baseline.before-test-guard.json). The existing test was guarding the recorded decision.
- **One run per cell.** Worker output and cost vary between runs. The three extra mistake issues were picked because their test packages are small, out of 34 open issues Fence called mistake, which favours easy fixes.
- **"Fixed" means verified, not reviewed.** The worker's test fails without its fix and passes with it, and nothing in the tested packages regressed. No human has judged whether these are the fixes a maintainer would want.

## A run where an action failed, and what the agent did next

Baseline run on #2274, recorded in [runs/fix/2274.baseline.json](runs/fix/2274.baseline.json), Neatlogs trace `ac2f762e4d41cecc9f027c1a65707513`.

1. **Attempt 1.** The worker added a test and changed `settings.go` and `setup.go`. It reported success.
2. **Fence did not take its word.** It reran the tests with `entire graph verify`: the new test passed, but 10 existing tests were NEWLY FAILING (`TestRunDisable`, `TestRunEnable` and others). Attempt rejected: `regressions: TestRunDisable, ...`.
3. **What happened next.** Fence piped the failing `go test` output through `entire graph explain` and gave the worker that, plus the rejection reason, as its next prompt.
4. **Attempt 2.** The worker corrected its change. Verification: 1 test newly passing, no regressions against the pre-edit baseline. Fence committed it on `fence/issue-2274` in the work copy.

This failure was not staged. It is also the only retry in the data, and it happened in baseline mode. In Fence mode the worker was told which package the issue is about and got #2274 right first time in two files; with one run each, that is an observation, not a finding.

## Does the verdict agree with what maintainers did?

`fence eval` ran the verdict (no fixing) on all 52 closed issues that name their code and trace to a checkpoint. Source: [runs/eval.json](runs/eval.json), total cost $1.8171.

| Issue was closed as | mistake | decision | no_record |
|---|---|---|---|
| completed (47) | 39 | 6 | 2 |
| not planned (4) | 1 | 1 | 2 |
| duplicate (1) | 1 | 0 | 0 |

Counting completed↔mistake and not planned↔decision as agreement, and no_record as an abstention: **40 of 47 agree (85%)**. Read that number with these limits, which the command prints with it:

- **A classifier that always answered "mistake" would score 45 of 47.** This comparison does not show that the verdict adds information; it mostly shows the set is dominated by completed issues.
- "Completed" also covers feature work and refactors, where mistake-or-decision is not really the question.
- The not-planned side is four issues, two of them abstentions. That is not a rate.
- Not planned is not the same as a deliberate decision; issues are also closed that way for process reasons.
- One run per issue, and the verdict can flip between runs: across nine runs #2256 came out mistake eight times and no_record once (the no_record was before the prompt's definition of no_record was tightened).

The more informative check is [runs/decisions.md](runs/decisions.md), which reads all 13 issues Fence called `decision` (6 closed, 7 open) against the quoted evidence and what maintainers did:

| Reading | Count | Issues |
|---|---|---|
| A real recorded choice about the reported behaviour | 6 | #275, #601, #2362 (later reversed on purpose), #2033, #2263, #2393 |
| A real design choice, but the reported consequence was never chosen | 5 | #1262, #1876, #2091, #2401, #2612 |
| Misread: a feature request, the quote is beside the point | 2 | #1767, #2718 |

So about half of Fence's `decision` verdicts hold up. The usual error is reading "this design was chosen" as "this consequence was chosen". These readings are by the same session that built Fence, from pull request descriptions, not diffs.

## Ask once, remember

Source: [runs/2160.asked.json](runs/2160.asked.json), [runs/answers/2160.json](runs/answers/2160.json), [runs/1554.json](runs/1554.json).

1. #2160 asks for Goose and Qwen Code agent integrations. Nothing on record: Fence wrote [runs/questions/2160.md](runs/questions/2160.md).
2. The answer stored is what a maintainer wrote in that issue's thread ([comment](https://github.com/entireio/cli/issues/2160#issuecomment-5476033646)): new agents go through the external agent path first, not into the CLI. `fence answer` saved it in Entire Brain under `constraints.fence.rulings`.
3. #1554 asks for a Kilo Code integration. Fence recalled the ruling and returned `precedent`; nobody was asked. A maintainer had told that issue's author the same thing, so the precedent matches reality.

## The three tools, inside the running product

### Entire

- **Checkpoints as evidence.** [fence/resolve.py](fence/resolve.py) blames the located lines and follows each commit's `Entire-Checkpoint` trailer to the checkpoint ref; [fence/evidence.py](fence/evidence.py) reads the commit message, then `full.jsonl`, then the prompt. Artefact: the `evidence` list in [runs/2256.json](runs/2256.json), 22 excerpts with commit and checkpoint ids.
- **`entire graph search`** locates code when an issue names none. Artefact: `located_by: "search"` in [runs/1924.json](runs/1924.json).
- **`entire graph verify`** is the independent check after the worker. Artefact: the three verify reports per attempt in [runs/fix/2256.json](runs/fix/2256.json).
- **`entire graph explain`** turns a real test failure into the worker's retry prompt. Artefact: `explained_failure` in [runs/fix/2274.baseline.json](runs/fix/2274.baseline.json).
- **Entire Brain** stores rulings and recalls them. Artefact: [runs/answers/2160.json](runs/answers/2160.json) with the fact id.

### Neatlogs

Every run posts one span tree to Neatlogs ([fence/trace.py](fence/trace.py)): a TOOL span per step, an LLM span per model call with model, tokens, cache read/write and the cost reported by Claude Code, and status ERROR on a failed step. Every run file under `runs/` has a `trace_id` field. For example:

| Run | Trace id |
|---|---|
| #2256 fix, Fence mode (12 spans: investigate, worker, three verifies, commit) | `dc90932fbe6e2d57187313df187b5a36` |
| #2274 baseline, the failed attempt and the retry | `ac2f762e4d41cecc9f027c1a65707513` |
| #2263 Fence mode, stopped and asked | `4480f8efcc171eed4fae66fedc77ad66` |
| #2160 asked, then #1554 precedent | `f24eb51f5ec614f8322b2299205f707f`, `b19f55e57702ac722e99980863bf5220` |

Limit: Neatlogs' ingest endpoint rejects request bodies over about 8 KB, so traces are gzipped and long span text is truncated. The full prompt and evidence are in the run files.

### cfo.ai

Placeholder. The plan (what Fence costs per issue at volume, and when the investigation pays for itself) will be built from the measured costs in `runs/` and added here. Nothing in this section exists yet.

## Run it

Needs Python 3.12, git, Go, the `entire` CLI with the graph and brain plugins, and the `claude` CLI signed in.

```bash
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -e '.[dev]'
```

`.env` (git-ignored):

| Variable | What it is |
|---|---|
| `FENCE_TARGET` | path to a local clone of the target repo with its checkpoint refs fetched; Fence only reads it |
| `FENCE_MODEL` | model for the verdict and the worker, for example `sonnet` |
| `NEATLOGS_API_KEY` | Neatlogs write key |
| `NEATLOGS_PROJECT` | Neatlogs project name (optional, has a default) |

```bash
.venv/bin/fence investigate --issue 2256     # locate, evidence, verdict; writes runs/2256.json
.venv/bin/fence fix --issue 2256             # investigate, and on a mistake fix it in work/2256
.venv/bin/fence fix --issue 2256 --baseline  # no investigation: straight to the worker
.venv/bin/fence answer --issue 2160 "<ruling>" --source "<who, where>"
.venv/bin/fence eval                         # all closed, named, traced issues; reuses runs/eval/
.venv/bin/python -m pytest                   # 83 tests, no network
```

Dashboard: `python3 -m http.server --directory ui 8000`, then open `http://localhost:8000/?issue=2256` (fixed) or `?issue=2263` (stopped and asked).

**What it costs** (Claude Code's own reported cost, model `claude-sonnet-5-5`):

| Action | Measured |
|---|---|
| Verdict for one issue | $0.005 to $0.05 (the low end is a warm prompt cache) |
| Fix, Fence mode | $0.08 to $0.14, about 50 s |
| Fix, baseline | $0.06 to $0.21, 24 s to 28 min |
| `fence eval`, 52 issues | $1.82 |
| Open scan, 45 issues | $1.59 |

## What is not real

- **Nobody answered a question live.** The one ruling in memory was copied from a maintainer's public comment. No maintainer of entireio/cli has seen Fence or its questions.
- **The demo issues were chosen by hand.** #2256 came with the project. #1870, #2274 and #2686 were picked for small test packages. #2160 and #1554 were picked by reading threads for a real ruling and a matching second issue.
- **No fix has been reviewed or submitted.** Commits exist only on `fence/issue-<N>` branches in local work copies. Nothing was pushed to entireio/cli and no pull request was opened.
- **Verification is narrow.** Tests run only in the packages the issue's code is in. Regressions elsewhere in the repository are not checked.
- **A "fix it" ruling does not trigger a fix.** After any ruling the outcome is `precedent`; turning that into a fix is manual.
- **The retry path ran once**, in baseline mode. Fence mode has never needed it.
- **The verdict is one model call and is not stable.** See the eval limits above and [runs/decisions.md](runs/decisions.md).
- **The readings in decisions.md are the builder's own**, not an independent review.
- **Recall is not selective.** With one ruling stored, Brain returns it for every query; whether it applies is left to the verdict model.
- **The dashboard layout has not been checked in a browser** by the session that wrote it.
