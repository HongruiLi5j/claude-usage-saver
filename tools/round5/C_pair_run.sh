#!/bin/bash
# 第 5 轮 C：配对实验。同一个 brief，(a) claude -p --model opus 无头跑；(b) tools/codex_task.sh 派给 Codex。
# 用法：tools/round5/C_pair_run.sh <opus|codex> <任务名> <重复号>
#   任务名：review_tf | review_cg | read_impl | research_cli
# 工作区：$R5C_ROOT/runs/<任务>_<方式>_<重复号>/（每次全新复制，不碰真实项目）
set -u
METHOD="$1"; T="$2"; REP="$3"
PROJ="$HOME/Documents/Projects/claude-usage-saver"
ROOT="${R5C_ROOT:?需要设 R5C_ROOT}"
WS="$ROOT/runs/${T}_${METHOD}_${REP}"
[ -e "$WS" ] && mv "$WS" "$WS.old.$(date +%s)"
mkdir -p "$WS"
case "$T" in
  review_tf) KIND=review; cp -p "$PROJ/backups/test_filter.py.pre-r4B4" "$WS/test_filter.py";;
  review_cg) KIND=review; cp -p "$PROJ/backups/context_guard.py.pre-r4D2" "$WS/context_guard.py"; cp -p "$PROJ/config.json" "$WS/config.json";;
  read_impl) KIND=read; mkdir -p "$WS/docs"; cp -p "$PROJ"/reports/round4_impl_{B,C,D,E,F,G}.md "$WS/docs/";;
  research_cli) KIND=research;;
  *) echo "未知任务 $T" >&2; exit 2;;
esac
TASK="$PROJ/tools/round5/C_tasks/$T.md"
T0=$(date +%s)
if [ "$METHOD" = codex ]; then
  CODEX_WS="$WS" CODEX_OUT="$WS/concl.md" CODEX_QUIET=1 "$PROJ/tools/codex_task.sh" "$KIND" "r5C_${T}_${REP}" "$TASK" > "$WS/runner.out" 2>&1
else
  PROMPT="$(CODEX_WS="$WS" CODEX_PRINT_PROMPT=1 "$PROJ/tools/codex_task.sh" "$KIND" "r5C_${T}_${REP}" "$TASK")"
  printf '%s\n' "$PROMPT" > "$WS/prompt.txt"
  cd "$WS" && claude -p --model opus --effort medium --setting-sources project,local --no-session-persistence \
    --output-format stream-json --verbose --permission-mode acceptEdits \
    --allowedTools "Bash Read Grep Glob Write Edit WebFetch WebSearch" \
    < "$WS/prompt.txt" > "$WS/stream.jsonl" 2> "$WS/claude.err"
  python3 -c '
import json,sys
r=[json.loads(l) for l in open(sys.argv[1]) if l.strip().startswith("{")]
res=[x for x in r if x.get("type")=="result"]
open(sys.argv[2],"w").write((res[-1].get("result") or "") if res else "（没有 result）")
json.dump(res[-1] if res else {}, open(sys.argv[3],"w"), ensure_ascii=False, indent=1)
' "$WS/stream.jsonl" "$WS/concl.md" "$WS/result.json"
fi
echo "$METHOD $T $REP secs=$(( $(date +%s) - T0 ))" | tee -a "$ROOT/timing.txt"
