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
| `hooks/statusline.sh` | statusLine | Shows context size (and window), quota tier, a ⚠ warning before the threshold, and the savings meter (`省≈X%累计 本会话Y%(估·上限)`, see below). Appends quota readings to `state/rate_history.jsonl` when they change. Shared state files are written under a lock with atomic rename. |
| `hooks/savings_meter.py` | used by statusline | Savings meter: per-event counterfactual log in `state/savings.jsonl`. See "Savings meter" below. |
| `tools/pricing.py` | shared | Price table by model family/version from Anthropic's pricing page (2026-11-06). Converts usage to "Sonnet 5.5 equivalent tokens" (= API USD ÷ $2/MTok, so Opus 5.5 counts 2×). Unknown models are priced as the family's latest model or as Sonnet 5.5, and always flagged. |
| `tools/weekly_compare.py` | report | Weekly comparison of cost per request, split by main session vs. subagent and by model family (built from the families actually present). Reports both "own-model units" and Sonnet 5.5 equivalents, using `tools/pricing.py` (cache reads are 0.05× input on the 5.5 models, 0.1× on older ones). Cost per day counts only complete local days; partial edge days are listed and excluded. |
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

### Savings meter (round 6)

The status line shows `省≈X%累计 本会话Y%(估·上限)`: estimated savings ÷ (actual cost + estimated savings), cumulative and for this session, in Sonnet 5.5 equivalent tokens. `usaver-status` shows the same number (it only reads `state/savings.jsonl`).

- **Only mechanisms with a direct counterfactual are counted.** `test_filter`: the tokens it cut, compared with what Claude Code itself would have shown (the full log up to ~30k characters; above that Claude Code saves the output to a file and shows a ~2k-character preview). Each cut token is charged at the cache-write price on the next request, then at the cache-read price on each later request (write price again when the cache was cold), until compaction or `/clear`. This can be negative, and it is recorded as negative.
- **Settings with a measured fixed-prefix saving (round 6 P2).** Today: `enableArtifact: false` (set by the owner on 2026-11-06 23:42 PST). Round 5 A measured the first request of a fresh interactive session at −13,421 tokens (main) and −13,140 (subagent). For every request made after 2026-11-07T07:42Z whose tool list — taken from the `api-request-shape` records Claude Code writes into the transcript — has no `Artifact` but would otherwise have had it (main thread: interactive tool set, i.e. `AskUserQuestion` present; subagent: has `Write`/`Edit`, so read-only agents such as Explore are excluded), the meter adds Δ × that request's price for the prefix: cache-write price (1.25× / 2×) when the prefix was written (cache read < 4,096 tokens), cache-read price otherwise, input price when no cache was used. Requests without a shape record are not counted. Δ, the start time, and on/off can be overridden in `config.json` under `savings_meter.settings.artifact_off` (changing Δ takes effect at once; changing the start time or turning it off recomputes every session and appends revoke rows — `counted: false`, saved 0 — for events that are no longer produced, so the ledger and `usaver-status` follow the status line); other measured settings plug into the same registry (`SETTING_SAVINGS` / `register_setting` in `hooks/savings_meter.py`).
- **Ledger consistency (P2).** A ledger row is appended whenever a value changes (at the ledger's 0.1 precision), so `usaver-status` and the status line agree. `rev` is a millisecond timestamp, so rebuilding a lost progress file cannot leave the ledger on an old value. Each refresh also catches up, at most every 30 s and for ≤12 ms, other recently seen sessions whose transcript grew after their last refresh. `python3 hooks/savings_meter.py --session-end` does the same for one session from a SessionEnd hook payload (not installed; it would need a change to `~/.claude/settings.json`).
- **Always shown.** The segment is shown even at 0.0%. `usaver-status` shows it whenever `state/savings.jsonl` exists, and adds a per-mechanism breakdown (percentage points of the cumulative total).
- **Not counted:** the context guard's `/clear` is shown only as an estimate range [0, upper bound] in `savings.jsonl` (`counted: false`). In the F2 benchmark the upper bound was 12.2% on the long-context task, while the measured A/B difference was 2.7%, so the upper bound overstates by about 4×. Early compaction, model routing and session resume are not estimated.
- **This is an upper-bound estimate per mechanism, not an A/B measurement** (the Artifact part is a measured Δ times the price of each request, which is closer to a point estimate). Calibration against the F2 benchmark (`reports/round6_P.md`): the meter reports 0.00% for arm B (test_filter never fired), and F2 measured no significant difference (R = 1.008, 95% CI 0.970–1.048). On the owner's real transcripts up to 2026-11-06, `test_filter` fired twice, both on short logs, for a net of −338 tokens (≈ 0%).
- Set `USAGE_SAVER_METER=0` to turn the meter off.

## Install

The hooks are registered in `~/.claude/settings.json`. Paths currently assume the repo lives at `~/Documents/Projects/claude-usage-saver`. Thresholds are in `config.json`. See `使用说明.md` for the user guide (in Chinese).

## Status

Personal project, work in progress (November 2026). Not affiliated with Anthropic.

## Using with other AI coding tools

The hooks above are Claude Code specific. `adapters/` holds the parts that work with Codex CLI, Gemini CLI, Cursor, Aider and similar tools. Details and measurements are in `reports/round5_H.md` (Chinese), and the machine-readable compatibility matrix is `adapters/matrix.json`.

| Piece | What it does | Works with |
|---|---|---|
| `adapters/bin/usaver-run` | `usaver-run pytest -q`: runs any command, saves the full log, and prints only the failures and the tail. Exit code is preserved, and `USAGE_SAVER_RAW=1` gives raw output. Same rules as `hooks/test_filter.py`. | Anything that can run a shell command. For Aider: `--test-cmd "usaver-run pytest -q"`. |
| `adapters/agents/AGENTS.usage-saver{,.lite}.md` | Token-saving rules for the agent: use `usaver-run`, read narrowly, hand off through `HANDOFF.md`. The handoff format is the one `session_resume` parses, so a handoff written by Codex is picked up by Claude Code. | Codex (`AGENTS.md`), Cursor (`AGENTS.md`), Gemini (`GEMINI.md`, or set `context.fileName`), Aider (`--read CONVENTIONS.md`) |
| `adapters/codex/codex_usage.py` | Per-request token usage from `~/.codex/sessions` rollout logs (`.jsonl` and `.jsonl.zst`), in the same cost units as `tools/weekly_compare.py`. Also reports the 5-hour and 7-day quota readings. It excludes `/tmp` benchmark sessions and Claude sessions that Codex Desktop imported. | Codex CLI / Codex Desktop |
| `adapters/bin/usaver-status` | A one-line live status in the same format as `hooks/statusline.sh`, for example `gpt-6.1-sol \| ctx 22k/475k \| 5h 95% \| 7d 27%`. It auto-detects the most recent Codex or Claude Code session in the current directory and uses the kit's thresholds. When the model's context window is known, it also caps the warning lines at 60% / 80% of that window, and it predicts when the 5-hour window will run out. Use `--watch` in a spare pane, or put it in tmux `status-right` or a zsh `RPROMPT`. | Codex, Claude Code |
| `adapters/apply.py` | Applies and reverts the above with one command each. Every change is backed up, and nothing is deleted. | — |
| `adapters/skills_sync.py` | Generates per-tool copies of the kit's skills (`model-router`, `handoff`) from the single source in `~/.claude/skills`, for example into `~/.agents/skills` for Codex. Kit paths are resolved at run time, and `check` detects copies that are stale or were overwritten. Codex Desktop's own importer had replaced every "Claude" with "Codex", which broke paths. | Codex (`~/.agents/skills`) |

```sh
python3 adapters/apply.py bin link                                   # usaver-run + usaver-status into ~/.local/bin
python3 adapters/apply.py agents apply --variant lite                # rules block into ~/.codex/AGENTS.md
python3 adapters/apply.py agents apply --target ~/.gemini/GEMINI.md  # same block for Gemini CLI
python3 adapters/apply.py agents revert; python3 adapters/apply.py bin unlink   # undo
python3 adapters/codex/codex_usage.py --days 7                       # Codex usage report
python3 adapters/skills_sync.py plan                                 # read-only: are ~/.agents/skills copies current?
python3 -m pytest -q adapters/tests/test_adapters.py                 # offline tests (36)
```

Honest numbers (measured on one machine, small n):

- Codex already truncates each tool output on its own (a model-chosen `max_output_tokens`, median 5000). In a live paired run, `usaver-run` cut the test output from 7158 to 771 tokens, but total input was almost unchanged.
- The full rules block costs about 677 tokens per request.
- On this owner's Codex history, the estimated net effect is about −0.4% for the full block and about +0.1% for the lite block. Use the lite block, and keep the full one for test-heavy projects.
- For other tools, the main value is visibility (usage and status), not savings.
- Gemini CLI, Cursor and Aider were not installed here. Their entries are based on documentation only.
