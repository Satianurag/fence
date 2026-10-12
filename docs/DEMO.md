# Fence in three minutes

A script to read aloud while screen-recording. Times are the budget per step; the whole thing is 180 seconds.
Everything shown comes from files in this repo or from the Neatlogs project "fence". Have three things open
before recording: a terminal in the repo root, the dashboard (`python3 -m http.server --directory ui 8010`),
and Neatlogs on the "fence" project.

Run times, so you know what can be live: an ask takes about 9 seconds, a fix about 45 seconds, a baseline on a
decision issue 10 to 25 minutes. Show the long ones from their run files.

## 1. The problem (0:00 to 0:10)

Say: "A coding agent given a GitHub issue will change the code. It never asks why the code was written that way.
Sometimes the behaviour in the issue was somebody's decision. Fence checks the record first."

Show: the title of the README.

## 2. A mistake: found, fixed, verified (0:10 to 0:50)

```bash
.venv/bin/fence fix --issue 2256
```

Live, this takes about 45 seconds and costs about 13 cents. If you would rather not wait:

```bash
python3 -m json.tool runs/fix/2256.json | less
```

Say, while it runs: "Issue 2256. Fence finds the functions the issue names, blames each line, and follows the commits
to their Entire checkpoints: the session transcripts and prompts from when the code was written. Nothing on record
shows anyone chose this behaviour, so the verdict is mistake."

Point at these lines of the output:

- `#2256 mistake (confidence ...)`
- `action: fixed | attempts: 1`
- `committed ... on fence/issue-2256 in work/2256 | newly passing: TestAppendCheckpointTrailer_...`

Say: "The worker is headless Claude Code. Fence does not trust its report. It reruns the tests itself with
entire graph verify: the new test fails without the fix, passes with it, and nothing else broke. The commit stays
in a private clone."

Recorded run: $0.1324, 46.4 s, trace `abe01d2622eba06e3ef75f409a37b8b7`.

## 3. A decision: Fence asks, the baseline does not (0:50 to 1:30)

```bash
.venv/bin/fence fix --issue 2033
cat runs/questions/2033.md
```

Live, about 9 seconds and 3 cents.

Say: "Issue 2033 says checkpoint-remote supports only the github provider. The record has the prompt where a person
asked for exactly that structured format, and a later commit that calls other providers deferred. Verdict: decision.
Fence changes nothing and writes one question for a maintainer, with the quote and the commit it came from."

Then show the same issue sent straight to the worker, from the run file:

```bash
python3 -c "import json; r=json.load(open('runs/fix/2033.baseline.json')); print(r['action'], len(r['attempts']), 'attempts', r['cost']['usd'], r['duration_s'], 's'); [print(' attempt', a['attempt'], '->', a['verification']['reason']) for a in r['attempts']]"
```

Say: "Without Fence the worker changed it. Its first attempt broke the existing test that rejects unknown providers.
Fence's verifier caught that and sent it back. The second attempt passed every test, so it was committed: 25 minutes,
24 cents, and a recorded decision overridden without anyone being asked. Passing tests did not protect the decision.
The verdict did."

Recorded runs: Fence $0.0280, 8.8 s, trace `96cb76a7115d1fc0c78e056d101acaaa`. Baseline $0.2412, 1472 s, trace
`3e5ddbd88914720c38549db486c3c449`.

## 4. Ask once, then precedent (1:30 to 1:55)

```bash
cat runs/questions/2160.md | head -12
python3 -c "import json; a=json.load(open('runs/answers/2160.json')); print(a['ruling']); print(a['source'])"
.venv/bin/fence investigate --issue 1554
```

The last command is live: about 10 seconds, about 1.5 cents.

Say: "Issue 2160 asks for two new agent integrations. Nothing on record, so Fence asked. The answer here is what a
maintainer actually wrote in that thread: new agents go through the external agent path. Fence stored it in Entire
Brain. Issue 1554 asks for another agent integration. Fence recalls the ruling and answers precedent. Nobody is asked
twice."

Point at: `outcome: precedent, nobody asked.`

Recorded traces: asked `6cfd5a1c9e25f045df08b48561660475`, precedent `b5629bbe9652f5f1cd3f2dc93795d2dc`.

## 5. The trace and the detection (1:55 to 2:20)

In Neatlogs, project "fence":

1. Open trace `3e5ddbd88914720c38549db486c3c449` (the #2033 baseline). Show the span tree: `fence.work_copy`,
   `fence.worker`, `fence.verify` in red, `fence.explain_failure`, `fence.worker`, `fence.verify`, `fence.commit`.
2. Click the red `fence.verify` span: its error is the list of regressed tests.
3. Open Detections, "Fence: fix failed verification" (`ed210539-07d4-44a7-9ee4-b35ef6894484`). It has three hits, all
   failed verify steps.

Say: "Every run is one trace: each step, each model call with its tokens and cost. When a worker's change fails
Fence's verification, this detection fires. It caught this run."

You can also show "Fence: asked a human" (`749b960e-fa61-402e-a1b5-cf78c067da84`): it fires on the ask step of trace
`21fe34a6767accb2cb6999b3137ac62d` (#2263) and on nothing else in that trace.

Say about cost, if asked: the dollar figures in the README are Claude Code's own; Neatlogs' computed cost differs.

## 6. The dashboard (2:20 to 2:50)

- `http://localhost:8010/?issue=2256` : the replay walks the evidence commits and ends on MISTAKE, FIXED.
- `http://localhost:8010/?issue=2033` : the same replay ends on DECISION, ASKED A HUMAN in the strip above the 3D view. The quote itself is in `runs/questions/2033.md`.
- Point at the FENCE VS BASELINE table and the EVAL panel.

Say: "Same picture for every issue: the commits behind the code, which of them have a checkpoint, and what Fence did.
And the honest numbers. On plain bugs the investigation costs a few cents more and changes nothing. The eval agrees
with maintainers 40 times out of 47, and a rule that always says mistake would score 45. The value is in the
decision cases."

Any window from 1280x720 to 1920x1080 works; the layout was checked at those sizes. Serve it with
`python3 -m http.server --directory ui 8010`.

## 7. The plan (2:50 to 3:00)

Open https://app.cfo.ai/s/xcdEIupLOCiVDXSI (the "Fence plan (v2)" page). Show the drivers table, then the monthly
table, then switch the scenario to "Fence plan: price 0.50 (v2)".

Say: "The plan is a live model in cfo.ai. The cost rows are measured: 2 cents to ask, 12 cents to fix, about 10 cents
blended per issue. Price, customers and fixed costs are assumptions and are labelled as such. At one dollar an issue
it breaks even at about 220 issues a month; at fifty cents, about 500."

## If something goes wrong on camera

- A live `fence fix` gives a different verdict or a failed fix: say so and switch to the run file. One run in nine
  on #2256 gave a different verdict.
- Neatlogs rejects a trace: the command prints the status; the run file is still written.
- Do not run a baseline on #2033 or #2263 live. They take 10 to 25 minutes.
