# The issues Fence called `decision`

Thirteen issues: the six closed-completed issues from `runs/eval.json` and the seven open issues from
`runs/scan.json`. For each: the quote Fence relied on, what the maintainers did, and a reading of the
evidence. The readings are by the session that built Fence, from the quoted commit messages and
transcripts in `runs/eval/<N>.json` and `runs/scan/<N>.json` and from the issue threads and linked pull
requests on GitHub (read 2026-10-12). Pull request diffs were not read, only their descriptions.

Reading labels:

- **reversed**: the record shows a real choice about the reported behaviour, and maintainers later changed it.
- **stands**: a real choice about the reported behaviour, and maintainers kept it.
- **never a choice**: the record shows a choice about nearby design, not about the behaviour the issue reports.
- **misread**: the quote does not support `decision` for this issue at all.
- **open**: no maintainer outcome yet.

## Tally

| Reading | Closed (6) | Open (7) |
|---|---|---|
| Real choice about the reported behaviour | 3 (#275 stands, #601 stands, #2362 reversed) | 3 (#2033, #2263, #2393) |
| Never a choice about the reported behaviour | 2 (#1262, #1876) | 3 (#2091, #2401, #2612) |
| Misread | 1 (#1767) | 1 (#2718) |

Six of thirteen are a recorded choice about the thing the issue reports. Five quote a real design choice
whose unconsidered side effect is what the issue reports; in four of those five Fence's own rationale says
the case "was not considered" or "was not addressed", which by Fence's definition is `mistake`. Two are
feature requests where the quote is beside the point.

## Closed, completed

### #275 Checkpoint data not pushed when agent commits and pushes in the same turn

- **Quote** (commit message, commit `97ce79e96b`, checkpoint `db3f4ae57df0`): "Add TurnEndHandler optional
  interface and implement it on ManualCommitStrategy to dispatch deferred condensation at turn end."
  Second quote from commit `b5e924cd40`, checkpoint `1c5eb527b36d`, about storing `PendingCheckpointID`.
- **What maintainers did**: confirmed it as a real problem (khaong, 2026-02-12), a contributor PR (#303) was
  closed unmerged, and on 2026-04-28 ashtom labelled it `wontfix`: they "don't have a universal solution".
  The issue's state_reason is `completed`, but nothing was fixed.
- **Reading: stands.** Deferring condensation to turn end is a recorded design choice and the reported
  symptom follows directly from it. Maintainers kept the design. `decision` was the useful answer here; the
  `completed` label that counts this row as a disagreement in the eval is wrong about what happened.

### #601 Refactor hasTTY() to use optional agent interface

- **Quote** (commit message, commit `1df71eb2e3`, checkpoint `edbda7258ea5`): "Like Gemini CLI, the subprocess
  may inherit the user's TTY but can't respond to interactive prompts." Second quote from commit
  `8e9c62e8c6`, checkpoint `9b106c8acecb`, on respecting `GIT_TERMINAL_PROMPT=0`.
- **What maintainers did**: declined the proposed agent interface (PR #658 closed unmerged). Soph closed it
  2026-06-01 as "Resolved by centralization rather than the agent-interface approach proposed here": the
  same hardcoded environment checks were moved into an `interactive` package.
- **Reading: stands.** Each hardcoded check was added on purpose and they are still hardcoded. This is a
  refactor request, so mistake-or-decision is a poor fit, but Fence's reading that the current shape was
  chosen matches the maintainers keeping it.

### #1262 Cursor inadvertently invokes Claude Code hooks when `.cursor/hooks.json` is not installed

- **Quote** (commit message, commit `c62b2ff5bd`, checkpoint `e51b741d6a56`): "SessionStart hint: first agent
  to fire claims the session ID". Second quote from commit `22ddd7b9c7`, checkpoint `35ba11dcdbb3`.
- **What maintainers did**: fixed it. PR #1263, merged 2026-06-25, added a guard that skips a hook forwarded
  from another agent's session; the first-writer-wins model was left in place.
- **Reading: never a choice.** First-writer-wins was chosen to settle a race when both hook files exist.
  Nobody chose misattribution when Cursor's hook file is absent, and Fence's rationale says so itself ("the
  gap when Cursor hooks are absent was not addressed"). This should have been `mistake`.

### #1767 checkpoint list --json, so the deprecated rewind --list can be removed

- **Quote** (commit message, commit `1e9b4a70b1`, checkpoint `f32f963571e7`): "The command stays functional
  but prints a removal notice to stderr and is excluded from help and completion". Second quote from a
  transcript, commit `e8bf0c9547`, checkpoint `e3e035743cb8`.
- **What maintainers did**: built the feature. PR #1768 merged the day the issue was filed.
- **Reading: misread.** The quotes show the deprecation was deliberate, which the issue takes as given. The
  issue asks for the planned follow-up. Nothing here is a decision someone would be overriding. Confidence
  was 0.60, the lowest of the six.

### #1876 session attach cannot find transcripts for OpenCode sessions not spawned from a hooked terminal

- **Quote** (commit message, commit `4f50f1a04d`, checkpoint `c0dbb5d73ef9`): "Move PrepareTranscript call
  inside the file-exists check in resolveAndValidateTranscript — don't poll on non-existent files during agent
  auto-detection (fixes Cursor's 3s poll on miss)".
- **What maintainers did**: fixed it. PR #1877, merged 2026-08-24, added an optional `TranscriptFetcher`
  capability so OpenCode can produce a transcript on demand.
- **Reading: never a choice, but the most useful row here.** The exact line the issue blames was put there
  deliberately, for a stated reason (Cursor's 3 second poll). OpenCode's on-demand export was not
  considered, so the reported failure was not chosen. A worker that simply removed the file-exists check
  would have brought the Cursor delay back; the merged fix added a separate capability instead, per its
  description. Strictly `mistake`, but with a recorded reason the fix has to respect. Fence has no verdict
  for that, and it is the case the product is named for.

### #2362 entire status collapses multiple sessions per agent, and mutates session state while reporting it

- **Quote** (transcript, commit `439be1e3e9`, checkpoint `3ee2f4dbe270`): "**status finalizes too** — reverted
  to your original \"finalize on detection\" intent now that status isn't a hot path." Second quote from the
  same commit's message.
- **What maintainers did**: reversed it. PR #2363, merged 2026-09-16: status now "finalizes nothing" and reads
  read-only. The issue itself says each change is "a deliberate behaviour change worth recording".
- **Reading: reversed.** The clearest case of the thirteen. A human asked for finalize-on-read, the agent
  recorded doing it, and five days after the issue the maintainers undid it knowingly. `decision`, then
  asking a human, is exactly what happened in reality.

## Open

### #2033 `--checkpoint-remote` supports only the `github` provider

- **Quote** (prompt, commit `e8b5898358`, checkpoint `e72402ff9efa`): "let's do something like \"github:
  org/repo\" or more structured even, so we could support other backen[ds]". Also the commit message of
  `ea96dce85c`, checkpoint `01M2TP416HJZHCW58ETT47YMMT`: other providers "documented as deferred, not
  silently missing."
- **What maintainers did**: nothing decided yet. PR #2301 (generic git provider) is open.
- **Reading: real choice, open.** The structured provider format replaced a raw URL on purpose and
  self-hosted hosts were explicitly deferred. Asking before a worker reopens raw URLs is right.

### #2091 Hook latency: remaining work after #2002

- **Quote** (commit message, commit `7e9c63f9f9`, checkpoint `27a4b7b0bdeb`): "writeCompactTranscript now uses
  compact.FullWithBoundary to write the entire compacted session into transcript.jsonl on every checkpoint".
  Second quote from commit `afd94281e9`, checkpoint `63b41777384a`.
- **What maintainers did**: are working on it. PR #2107 merged, #2099 and #2131 open; Soph discussed the
  remaining items in the thread.
- **Reading: never a choice.** Writing the full compact transcript each time was chosen for self-contained
  checkpoints. Its latency cost is what the issue reports, and Fence's rationale says "the latency cost was
  not weighed in the record". A tracking issue for performance work, already being done. Confidence 0.62.

### #2263 Lefthook is classified as not overwriting Entire's hooks

- **Quote** (commit message, commit `bace45e26b`, checkpoint `2939ce7ed7da`): "Husky users get copy-paste
  instructions for durable integration; other managers get a note to re-run `entire enable` if hooks are
  overwritten."
- **What maintainers did**: nothing merged. PR #2331 (fix Lefthook overwrite detection) is open; PR #2365
  was closed unmerged.
- **Reading: real choice, open, possibly on a wrong premise.** Treating Lefthook as non-overwriting was
  explicit in the original commit. The issue argues the premise was factually wrong. That is a decision for
  a human to revisit, which is what `decision` asks for. See `runs/compare.json` for what the baseline
  worker did with this issue unasked.

### #2393 git-refs writes can return success without durable push bookkeeping

- **Quote** (commit message, commit `989c1c0872`, checkpoint `a4baf5553823`): "gitRefsStore.setRef enqueues
  best-effort." Second quote from commit `783582781e`, checkpoint `01KZE9Z15Z7HP0JS2HA7SP00RM`.
- **What maintainers did**: nothing merged. PR #2395 (surface push queue failures) is open.
- **Reading: real choice, open.** Best-effort enqueue is stated outright, and the issue author acknowledges
  "the documented intent". The second quote shows a maintainer already called the consequence "worse than
  noisy" in a later commit, so the choice is under pressure. `decision` is right.

### #2401 ULID checkpoint refs unreadable when their shard case-collides on a case-insensitive filesystem

- **Quote** (commit message, commit `dd6fdeb6f9`, checkpoint `d17074281105`): "Collapse it to a single
  positional rule: the last two characters, for both legacy 12-hex IDs and 26-char ULIDs." Two more quotes
  from commits `b01b59663f` and `a75a7a8710`.
- **What maintainers did**: PR #2406 (compare the shard case-insensitively) was closed unmerged. No
  maintainer comment on the issue.
- **Reading: never a choice.** The shard rule and the strict parse were chosen for determinism. Case-insensitive
  filesystems appear nowhere in the record, as Fence's rationale admits. The closed PR hints the maintainers
  want a different fix, but that is a guess; nothing recorded says the data loss was accepted.

### #2612 Commit linking attaches an unrelated Codex session from another worktree

- **Quote** (commit message, commit `f86f8a42c5`, checkpoint `01M08AY54K8908EW6MF62XGH0W`): "nearest ancestor
  wins, ties go to the most recently interacting session, start times guard against PID reuse".
- **What maintainers did**: agreed it is a bug. Soph, 2026-09-28: "The diagnosis checks out." PRs #2531 and
  #2621 are open; #2621 is titled "break shared-owner ties by environment and phase, not recency".
- **Reading: never a choice.** The tie-break rule was chosen before Codex moved every session into one
  daemon process, which makes every tie resolve wrongly. Fence's rationale: "the concurrent shared-daemon
  case wasn't considered". Should have been `mistake`; the maintainer treats it as one. Confidence 0.60.

### #2718 Feature request: --author and --branch filters for entire checkpoint list

- **Quote** (transcript, commit `0535c282d3`, checkpoint `8ccdd0fbae20`): "Keep `IsOnDefaultBranch` and
  helpers that take a repo parameter in `strategy/common.go`".
- **What maintainers did**: nothing yet; filed 2026-10-09, no comments.
- **Reading: misread.** A feature request. The quote is about a duplicated helper the issue mentions in
  passing, not about the missing filters. Confidence 0.60.

## What this says about the verdict

1. When Fence quotes a human instruction or an explicit statement of intent about the reported behaviour
   itself (#2362, #2033, #2393, #2263, #275), the `decision` verdict held up against what maintainers did or
   are doing.
2. The common failure is treating "this design was chosen" as "this consequence was chosen" (#1262, #2091,
   #2401, #2612, and #1876). The rationale usually contains the giveaway phrase. A check on the rationale,
   or a prompt that asks separately "was the design chosen" and "was this consequence considered", would
   catch most of them.
3. #1876 suggests a fourth outcome is missing: fix it, but here is the recorded reason the current code
   exists, which the fix must not undo. Today that information is dropped when the verdict is `mistake`.
4. Low confidence tracked the weak rows: the four at 0.60 to 0.62 are two misreads and two "never a choice".
5. Feature requests and refactors do not fit the question. Three of the thirteen are such (#601, #1767, #2718).
