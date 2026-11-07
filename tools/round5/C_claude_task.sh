#!/bin/bash
# 反方向：Codex（或任何别的工具/脚本）把"复核 / 通读 / 调研"子任务派给 Claude（claude -p 无头），只读回短结论。
# 和 tools/codex_task.sh 对称：同样的三种模板（直接复用它拼 prompt），同样的输出约定。
#
# 用法：tools/round5/C_claude_task.sh <review|read|research> <名字> <任务说明文件>
#   结论 → ${CLAUDE_OUT:-<工作区>/concl.md}；完整事件流 → <工作区>/stream.jsonl（调用方不要读）
#   stdout：一行状态（含 Claude 用量折算的成本单位）+ 结论全文（CLAUDE_QUIET=1 只有状态行）
# 环境变量：CLAUDE_MODEL（默认 opus）、CLAUDE_EFFORT（默认 medium）、CLAUDE_WS（工作区，默认 /private/tmp/claude-501/claudews/<名字>）
#
# 从 Codex 里调用时（第 5 轮 C 实测）：
#   - workspace-write 沙箱默认断网 → claude -p 返回 "Not logged in · Please run /login"（rc=1）。
#   - 加 -c sandbox_workspace_write.network_access=true 后，订阅登录（OAuth）可用，rc=0。
#   - 不要用 --bare：它只认 ANTHROPIC_API_KEY / apiKeyHelper，订阅登录会失败。
#   - 会花 Claude 额度（不是 Codex 额度）。
set -u
KIND="${1:-}"; NAME="${2:-}"; TASK_FILE="${3:-}"
case "$KIND" in review|read|research) ;; *) echo "用法：$0 <review|read|research> <名字> <任务说明文件>" >&2; exit 2;; esac
[ -n "$NAME" ] && [ -f "$TASK_FILE" ] || { echo "缺名字或任务文件不存在：$TASK_FILE" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
WS="${CLAUDE_WS:-/private/tmp/claude-501/claudews/$NAME}"
OUT="${CLAUDE_OUT:-$WS/concl.md}"
mkdir -p "$WS" "$(dirname "$OUT")"
CODEX_WS="$WS" CODEX_PRINT_PROMPT=1 "$HERE/tools/codex_task.sh" "$KIND" "$NAME" "$TASK_FILE" > "$WS/prompt.txt" || exit 2
T0=$(date +%s)
cd "$WS" && claude -p --model "${CLAUDE_MODEL:-opus}" --effort "${CLAUDE_EFFORT:-medium}" \
  --setting-sources project,local --no-session-persistence \
  --output-format stream-json --verbose --permission-mode acceptEdits \
  --allowedTools "Bash Read Grep Glob Write Edit WebFetch WebSearch" \
  < "$WS/prompt.txt" > "$WS/stream.jsonl" 2> "$WS/claude.err"
RC=$?
SECS=$(( $(date +%s) - T0 ))
META=$(python3 - "$WS/stream.jsonl" "$OUT" <<'EOF'
import json, sys
res = None
for l in open(sys.argv[1], errors="replace"):
    try:
        x = json.loads(l)
    except Exception:
        continue
    if x.get("type") == "result":
        res = x
if not res:
    open(sys.argv[2], "w").write("结论：claude -p 没有给出结果（看 claude.err）\n"); print("units=? turns=?"); sys.exit()
open(sys.argv[2], "w").write((res.get("result") or "") + "\n")
u = 0.0
for m in (res.get("modelUsage") or {}).values():
    c5 = m.get("cacheCreationEphemeral5mInputTokens", 0); c1 = m.get("cacheCreationEphemeral1hInputTokens", 0)
    c5 += max(m.get("cacheCreationInputTokens", 0) - c5 - c1, 0)
    u += 0.1 * m.get("cacheReadInputTokens", 0) + 1.25 * c5 + 2 * c1 + m.get("inputTokens", 0) + 5 * m.get("outputTokens", 0)
print(f"units={u/1000:.0f}k turns={res.get('num_turns')} err={res.get('is_error')}")
EOF
)
printf '\n<!-- claude_task kind=%s rc=%s secs=%s %s -->\n' "$KIND" "$RC" "$SECS" "$META" >> "$OUT"
echo "claude_task $KIND $NAME rc=$RC ${SECS}s $META → $OUT"
[ "${CLAUDE_QUIET:-0}" = 1 ] || cat "$OUT"
