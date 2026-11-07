# claude-usage-saver

An automatic usage-saving kit for Claude Code subscription users: hooks, a status line, skills and measurement tools. The goal is to cut subscription quota consumption **without lowering answer quality** and **without asking the user to think about it**. The user's only manual step is typing `/clear` when the kit suggests it.

## Why

An audit of my own local Claude Code transcripts (2026-10-21 → 11-06, about 4.1B tokens) found:

- **98% of tokens were cache reads**: every request re-sends the whole conversation history.
- The median context per request was 113k tokens, and the most expensive sessions averaged 300k–470k.
- Output was only 0.4%, so "write shorter replies" is not where the savings are.

The biggest lever is therefore **context length**: hand off and `/clear` early, then resume automatically.

## Components

| File | Hook / role | What it does |
|---|---|---|
| `hooks/context_guard.py` | UserPromptSubmit | Reads the last request's real token usage from the transcript. Past a threshold, it asks Claude to write a handoff file and tell the user to `/clear`. The threshold adapts to remaining weekly quota (plentiful / normal / tight) and to the project. |
| `hooks/session_resume.py` | SessionStart (`clear`) | After `/clear`, injects the project's `HANDOFF.md` plus an automatic digest of the previous transcript: owner messages, Claude's last replies, files edited. Any short follow-up like "1" or "ok" then continues the work. |
| `hooks/test_filter.py` | PreToolUse (Bash) | Rewrites test commands so Claude only sees failures and errors. The full log is saved to a file. Exit codes are preserved. |
| `hooks/statusline.sh` | statusLine | Shows context size, quota tier and a ⚠ warning before the threshold. |
| `tools/weekly_compare.py` | report | Weekly comparison of cost per request, split by main session vs. subagent and by model. Weights 1h cache writes at 2× and 5m cache writes at 1.25×. |
| `tools/usage_report.py` | report | Usage breakdown by model, project, day and session. |
| `tools/scorecard.py` | regression | 50 fixed scenarios, run against the initial version and the current version (see below). |
| `tools/replay_guard.py`, `tools/bench/` | experiments | Replays real transcripts to simulate the guard; paired Opus-vs-Sonnet benchmark runs. |

## Optimization process

The kit is improved in rounds, each run by several agents with different models:

**research new information → cross-review → decide → fix + independent re-check → log.**

- `reports/round1_findings.md`: four parallel investigations (code, measurement, usability, missed techniques).
- `reports/round2_review_AC.md` and `reports/round2_review_BD.md`: cross-reviews. Each reviewer re-reproduced every claim, and several claims were overturned.
- `reports/improvements.md`: every change with before / after / gain / how it was measured. Simulated results are kept separate from measured ones.
- `reports/scorecard.md`: regression pass rate, initial version vs. current version.

### Honest numbers so far (updated 2026-11-06, after round 4)

| Metric | Value | Confidence |
|---|---|---|
| **Real usage per request** (cost-weighted tokens) | Raw before/after: 32.1k → 21.5k (−33%). **Not attributable to the kit.** | An independent statistical review (`reports/round5_F1_critique.md`) found: only ~6 hours of post-change data; excluding one unusually expensive overnight run the drop is −21% (95% CI −33% to −5%); within the same context-length bucket the per-request cost is essentially unchanged; and a placebo test shows −19% to −26% drops on 3 of 11 baseline days with no change at all. A controlled A/B benchmark is being built (`reports/round5_F2_bench.md`). |
| Self-written regression scorecard | 38% (16/42) → 100% (107/107) | Measured, but the scenarios were written against the fixed behaviour, so this is a regression gate, not evidence of quality. The initial version scores 34% (20/58) on the current applicable set. |
| Held-out blind test (scenarios written by Codex from the docs only) | Raw 80% (48/60); 93% (56/60) after reclassifying 10 failures as fixture errors | The reclassification was done after seeing the implementation, and 29 scenarios were not re-run, so read the raw number as the honest one. A new held-out round is in progress. |
| Replay of 19,951 real Bash commands through `test_filter` | 0 broken among the 82 commands it actually rewrites | Measured (syntax and exit code only). With 0/82 the 95% upper bound on the break rate is ~3.7%. `test_filter` touches only ~5% of test commands, so it is **not** a main source of savings. |
| Opus → Sonnet on small coding tasks | API list price ×0.50 (95% CI 0.487–0.510, 142 paired tasks) | Measured at API prices. The effect on **subscription quota is unknown**, and the **quality difference was not measurable** (ceiling effect). |

Every change goes through an independent reviewer (Codex) that reruns the evidence. As of round 4, only 1 of 7 items has passed review; the others are in fix loops. Status is tracked in `reports/round4_ledger.md`, and review conclusions are in `reports/round4_codex_reviews_summary.md`.

Earlier claims withdrawn after review: "−21% per request on day one" and "Sonnet saves 50% of quota" (see `reports/round2_review_BD.md`). The round-1 "313 → ~30 lines" test-output figure (n = 1) and the simulated context-guard estimate have been superseded by the measured numbers above.

## Install

The hooks are registered in `~/.claude/settings.json`. Paths currently assume the repo lives at `~/Documents/Projects/claude-usage-saver`. Thresholds are in `config.json`. See `使用说明.md` for the user guide (in Chinese).

## Status

Personal project, work in progress (November 2026). Not affiliated with Anthropic.
