# Fence

Before an agent changes code for a GitHub issue, Fence finds out why the code is the way it is.

1. It locates the code the issue is about and traces each line, through `git blame`, to the Entire checkpoint of the session that wrote it.
2. It reads the commit message, session transcript and prompt on record and gives a verdict: **mistake** (nobody chose this behaviour), **decision** (someone did, here is the quote) or **no_record**.
3. On a mistake it lets a worker fix the issue in a private clone, then checks the result itself. On a decision it changes nothing and asks a maintainer once.
4. It remembers the answer, so the next issue of the same kind is settled without asking.

**What Fence is and is not.** The worker that edits code is headless Claude Code. Fence is the layer around it: the policy (when may code be changed), the verification (did the change really work) and the measurement (what did it cost, what happened). The name is Chesterton's fence: don't remove one until you know why it was put up.

Everything below was measured on [entireio/cli](https://github.com/entireio/cli), a public repository whose commits carry Entire checkpoints. Numbers are from single runs; the limits are printed next to them.

## The result in one table
Six open issues, each run two ways: through Fence, and straight to the same worker with no investigation ("baseline"). Source: [runs/compare.json](runs/compare.json); full trace ids are in each `runs/fix/<issue>[.baseline].json`. Traces are in the Neatlogs project "fence" except ¹.

| Issue | Verdict | Fence | Cost | Time | Trace | Baseline | Cost | Time | Trace |
|---|---|---|---|---|---|---|---|---|---|
| #2256 | mistake | fixed, 1 attempt | $0.1324 | 46.4 s | `abe01d26` | fixed, 1 attempt | $0.0876 | 40.2 s | `ca1b8412` |
| #1870 | mistake | fixed, 1 attempt | $0.1435 | 45.3 s | `dca41c92` | fixed, 1 attempt | $0.0903 | 40.3 s | `e07a79ec` |
| #2274 | mistake | fixed, 1 attempt | $0.1268 | 56.0 s | `3f2861ad` | fixed on attempt 2 | $0.2069 | 1659 s | `ac2f762e` ¹ |
| #2686 | mistake | fixed, 1 attempt | $0.0729 | 32.5 s | `63be8460` | fixed, 1 attempt | $0.0558 | 21.6 s | `de23e0ba` |
| #2263 | decision | **changed nothing, asked** | $0.0205 | 8.9 s | `6766b7ba` | stopped: needs a human | $0.1653 | 592 s | `7c82f50f` |
| #2033 | decision | **changed nothing, asked** | $0.0280 | 8.8 s | `96cb76a7` | fixed on attempt 2 | $0.2412 | 1472 s | `3e5ddbd8` |

¹ #2274 baseline is the run from the first Neatlogs project ("Anurag's project"), kept because it is the recorded failure and recovery described below. It was not repeated.

**The verifier does not stop a worker from overriding a recorded decision when existing tests still pass; only the verdict did.** (#2033 below.)

What this does and does not show:

- **On the four mistake issues the investigation added cost and, on three of them, changed nothing.** Both modes fixed #2256, #1870 and #2686 on the first attempt; the baseline cost 23 to 37% less. On #2274 the baseline needed two attempts (see below); with one run each, that is an observation, not a finding.
- **The two decision issues are the behavioural difference.** For #2263 and #2033 the record shows a maintainer chose the behaviour the issue complains about. Fence changed nothing and wrote a question ([#2263](runs/questions/2263.md), [#2033](runs/questions/2033.md)) for about 2 cents in 9 seconds.
  - **#2263 baseline** tried to change it anyway. Attempt 1 broke an existing test that asserts the old behaviour. Attempt 2 edited that test, which Fence's verifier refuses (`existing_test_modified`), so the run ended as `needs_human` with nothing committed, after 10 minutes and $0.17.
  - **#2033 baseline** also broke existing tests on attempt 1 (`TestParseCheckpointRemoteFlag/unsupported_provider`), then found a change that passes on attempt 2 and was committed in the work copy. It overrode a recorded decision without anyone being asked, in 25 minutes for $0.24. A maintainer PR for the same feature is open, so it may be right on substance.
- **The verifier rule that stopped #2263 was added after seeing it.** In the first baseline run on #2263 the worker flipped the assertion and verification passed ([runs/fix/2263.baseline.before-test-guard.json](runs/fix/2263.baseline.before-test-guard.json)). Fence now rejects any changed or deleted line in a test that existed before the worker ran.

**What the numbers do not show.** Each cell is one run, and worker output, cost and even the verdict vary between runs. The mistake issues other than #2256 were picked because their test packages are small, out of 34 open issues Fence called mistake, which favours easy fixes; #2033 was picked from its title ("supports only the `github` provider" reads as a deliberate limit) and #2263 because it had the most confident decision verdict in the scan. "Fixed" means verified, not reviewed: the worker's test fails without its fix and passes with it, and nothing in the tested packages regressed. Nobody has reviewed the fixes or judged whether a maintainer would want them.

## A run where an action failed, and what the agent did next
Baseline run on #2274, recorded in [runs/fix/2274.baseline.json](runs/fix/2274.baseline.json), Neatlogs trace `ac2f762e4d41cecc9f027c1a65707513` (in the first project, "Anurag's project").

1. **Attempt 1.** The worker added a test and changed `settings.go` and `setup.go`. It reported success.
2. **Fence did not take its word.** It reran the tests with `entire graph verify`: the new test passed, but 10 existing tests were NEWLY FAILING (`TestRunDisable`, `TestRunEnable` and others). Attempt rejected: `regressions: TestRunDisable, ...`.
3. **What happened next.** Fence piped the failing `go test` output through `entire graph explain` and gave the worker that, plus the rejection reason, as its next prompt.
4. **Attempt 2.** The worker corrected its change. Verification: 1 test newly passing, no regressions against the pre-edit baseline. Fence committed it on `fence/issue-2274` in the work copy.

**A second recovery: #2033 baseline**, [runs/fix/2033.baseline.json](runs/fix/2033.baseline.json), trace `3e5ddbd88914720c38549db486c3c449` in the "fence" project.

1. **Attempt 1.** The worker added generic git provider support to `setup.go` with a new test, ran only that test, and reported success.
2. **Fence's verification** found two existing tests NEWLY FAILING: `TestParseCheckpointRemoteFlag` and its `unsupported_provider` case, which asserts that unknown providers are rejected. Attempt rejected; the "Fence: fix failed verification" detection fired on this span.
3. **Attempt 2.** Given the failure, the worker reworked the change so the existing tests pass again alongside the new one. Verification: 1 test newly passing, no regressions. Committed in the work copy.

This is a recovery in the mechanical sense only: the result overrides a behaviour the record says was chosen (see the compare table).

Neither failure was staged or reproduced on purpose. A third, #2263 baseline (trace `7c82f50f2a3cc1c68ff0399ea87eb90d`), was rejected twice and ended `needs_human`. All three were in baseline mode; Fence mode has never needed the retry. The `explain` input is the tail of the test output, which for a large package is mostly unrelated log lines.

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
Source: [runs/2160.asked.json](runs/2160.asked.json), [runs/answers/2160.json](runs/answers/2160.json), [runs/1554.json](runs/1554.json), [runs/1612.json](runs/1612.json). All four traces are in the "fence" project.

1. #2160 asks for Goose and Qwen Code agent integrations. Nothing on record: Fence wrote [runs/questions/2160.md](runs/questions/2160.md). Trace `6cfd5a1c9e25f045df08b48561660475`.
2. The answer stored is what a maintainer wrote in that issue's thread ([comment](https://github.com/entireio/cli/issues/2160#issuecomment-5476033646)): new agents go through the external agent path first, not into the CLI. `fence answer` saved it in Entire Brain under `constraints.fence.rulings` and ran the issue again: `precedent`. Trace `6269339a10453e92349cb1b323927c78`.
3. #1554 asks for a Kilo Code integration. Fence recalled the ruling and returned `precedent`; nobody was asked. A maintainer had told that issue's author the same thing, so the precedent matches reality. Trace `b5629bbe9652f5f1cd3f2dc93795d2dc`.
4. #1612 asks for Antigravity CLI support: `precedent` again, $0.0065. Trace `cd1ee49fa76311506b7ebd4c0fb4038f`.

For this recording the ruling was retracted from the brain and stored again, so step 1 really ran with an empty memory.

## The three tools, inside the running product

### Entire

- **Checkpoints as evidence.** [fence/resolve.py](fence/resolve.py) blames the located lines and follows each commit's `Entire-Checkpoint` trailer to the checkpoint ref; [fence/evidence.py](fence/evidence.py) reads the commit message, then `full.jsonl`, then the prompt. Artefact: the `evidence` list in [runs/2256.json](runs/2256.json), 22 excerpts with commit and checkpoint ids.
- **`entire graph search`** locates code when an issue names none. Artefact: `located_by: "search"` in [runs/1924.json](runs/1924.json).
- **`entire graph verify`** is the independent check after the worker. Artefact: the three verify reports per attempt in [runs/fix/2256.json](runs/fix/2256.json).
- **`entire graph explain`** turns a real test failure into the worker's retry prompt. Artefact: `explained_failure` in [runs/fix/2274.baseline.json](runs/fix/2274.baseline.json).
- **Entire Brain** stores rulings and recalls them. Artefact: [runs/answers/2160.json](runs/answers/2160.json) with the fact id.

### Neatlogs

Every run posts one span tree to Neatlogs ([fence/trace.py](fence/trace.py)): a TOOL span per step (`fence.locate`, `fence.verdict`, `fence.ask`, `fence.worker`, `fence.verify`, ...), an LLM span per model call with model, provider, tokens and cache read/write counts, and status ERROR on a failed step. Every run file under `runs/` has `trace_id` and `trace_project`.

| Run | Trace id (project "fence") |
|---|---|
| #2256 fix, Fence mode (investigate, worker, three verifies, commit) | `abe01d2622eba06e3ef75f409a37b8b7` |
| #2263 Fence mode, stopped and asked | `6766b7ba6a740fd776174b2f9bab80a6` |
| #2263 baseline, rejected twice, `needs_human` | `7c82f50f2a3cc1c68ff0399ea87eb90d` |
| #2160 asked, then #1554 precedent | `6cfd5a1c9e25f045df08b48561660475`, `b5629bbe9652f5f1cd3f2dc93795d2dc` |

**Detections.** Two conditional detections exist in the "fence" project, created by the project owner's session (Fence only prints the configs: `fence detections`, [docs/neatlogs-detections.json](docs/neatlogs-detections.json)):

| Detection | Id | Seen firing? |
|---|---|---|
| Fence: fix failed verification | `ed210539-07d4-44a7-9ee4-b35ef6894484` | Yes, correctly: 3 hits, all `fence.verify` spans with status ERROR (#2033 baseline once, #2263 baseline twice). |
| Fence: asked a human | `749b960e-fa61-402e-a1b5-cf78c067da84` | Yes, after one correction. As first created (TOOL spans named `fence.ask`) it hit every successful TOOL span, because Neatlogs does not apply the `span_names` filter. `fence.ask` is now the only CHAIN span and the rule matches CHAIN spans. Checked on a fresh ask (#2263, trace `21fe34a6767accb2cb6999b3137ac62d`): one hit, on the `fence.ask` span, none on the trace's other spans, and the verification detection stayed silent on it. |

Limits: the first detection is only correct because `fence.verify` is the only step that ends in ERROR in these runs; the same missing name filter applies to it. Neatlogs computes its own cost for each LLM span and it does not match Claude Code's: on three calls it was 35% under, 4% under and 34% over, apparently because cache writes and reads are not priced as such. All costs in this README are Claude Code's own `total_cost_usd`. The ingest endpoint rejects request bodies over about 8 KB, so traces are gzipped and long span text is truncated; the full prompt and evidence are in the run files.

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
| `NEATLOGS_RUNS_API_KEY`, `NEATLOGS_RUNS_PROJECT` | optional pair: when both are set, investigate, fix and eval post to this project instead |

```bash
.venv/bin/fence investigate --issue 2256     # locate, evidence, verdict; writes runs/2256.json
.venv/bin/fence fix --issue 2256             # investigate, and on a mistake fix it in work/2256
.venv/bin/fence fix --issue 2256 --baseline  # no investigation: straight to the worker
.venv/bin/fence answer --issue 2160 "<ruling>" --source "<who, where>"
.venv/bin/fence eval                         # all closed, named, traced issues; reuses runs/eval/
.venv/bin/python -m pytest                   # 84 tests, no network
.venv/bin/fence detections                    # the two Neatlogs detection configs
```

Dashboard: `python3 -m http.server --directory ui 8000`, then open `http://localhost:8000/?issue=2256` (fixed) or `?issue=2263` (stopped and asked).

**What it costs** (Claude Code's own reported cost, model `claude-sonnet-5-5`):

| Action | Measured |
|---|---|
| Verdict for one issue | $0.005 to $0.05 (the low end is a warm prompt cache) |
| Fix, Fence mode, mistake issue | $0.07 to $0.14, 33 to 56 s |
| Fence mode, decision issue (stops and asks) | $0.02 to $0.03, 9 s |
| Fix, baseline | $0.06 to $0.24, 22 s to 28 min |
| `fence eval`, 52 issues | $1.82 |
| Open scan, 45 issues | $1.59 |

## What is not real

- **Nobody answered a question live.** The one ruling in memory was copied from a maintainer's public comment. No maintainer of entireio/cli has seen Fence or its questions.
- **The demo issues were chosen by hand.** #2256 came with the project. #1870, #2274 and #2686 were picked for small test packages. #2160 and #1554 were picked by reading threads for a real ruling and a matching second issue.
- **No fix has been reviewed or submitted.** Commits exist only on `fence/issue-<N>` branches in local work copies. Nothing was pushed to entireio/cli and no pull request was opened.
- **Verification is narrow.** Tests run only in the packages the issue's code is in. Regressions elsewhere in the repository are not checked.
- **A "fix it" ruling does not trigger a fix.** After any ruling the outcome is `precedent`; turning that into a fix is manual.
- **The retry path has run three times, always in baseline mode.** Fence mode has never needed it.
- **The verdict is one model call and is not stable.** See the eval limits above and [runs/decisions.md](runs/decisions.md).
- **The readings in decisions.md are the builder's own**, not an independent review.
- **Recall is not selective.** With one ruling stored, Brain returns it for every query; whether it applies is left to the verdict model.
- **The dashboard layout has not been checked in a browser** by the session that wrote it.
- **Traces live in two Neatlogs projects.** The fix, ask and precedent runs linked above are in the project "fence". The 52-issue eval, the 45-issue open scan and the #2274 baseline retry are in the first project, "Anurag's project", next to smoke tests and about a dozen probe traces from finding the ingest size limit.
- **One of the two detections is not usable yet** (see Neatlogs above), and Neatlogs' cost figures do not match Claude Code's.
- **There is no CLAUDE.md.** Working notes for agents in this repo have not been written.
