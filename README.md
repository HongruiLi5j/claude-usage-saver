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

### Honest numbers so far

| Metric | Value | Confidence |
|---|---|---|
| Regression scenarios passing (initial → current) | 40% → 48% (same 40 applicable scenarios: 40% → 50%) | Measured. 26 failing scenarios form the round-3 fix list. |
| Test output shown to Claude | 313 → ~30 lines | Measured, n = 1 |
| Context guard savings | ~30–50% of the easily-saved context | **Simulated** on replayed transcripts. The threshold was tuned on the same data, so the estimate is optimistic. |
| Opus → Sonnet on small coding tasks | API list price ×0.50 (95% CI 0.487–0.510, 142 paired tasks) | Measured at API prices. The effect on **subscription quota is unknown**, and the **quality difference was not measurable** (ceiling effect). |

Two earlier claims, "−21% per request on day one" and "Sonnet saves 50% of quota", were withdrawn after review. The reasoning is in `reports/round2_review_BD.md`.

## Install

The hooks are registered in `~/.claude/settings.json`. Paths currently assume the repo lives at `~/Documents/Projects/claude-usage-saver`. Thresholds are in `config.json`. See `使用说明.md` for the user guide (in Chinese).

## Status

Personal project, work in progress (November 2026). Not affiliated with Anthropic.
