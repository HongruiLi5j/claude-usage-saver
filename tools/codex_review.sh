#!/bin/bash
# 用法：tools/codex_review.sh <名字> <任务说明文件>
# 让 Codex 在可写的临时工作区里独立复核（项目目录对它只读），输出到 reports/codex_<名字>.md
# 第 5 轮 C：默认关 fast 档（--disable fast_mode，本机配置是 priority=fast，按 2.5 倍扣 Codex 额度）。
# 新任务建议改用 tools/codex_task.sh review（结论单独成文件、过程日志不进 reports/）。
# C2：stdin 接 /dev/null；超时 CODEX_TIMEOUT 秒（默认 900）→ 退出码 124；额度用完/429 → 75，输出文件第一行写明。
# 完成判据：进程退出（不要用 "tokens used" 判断）。结论在输出文件末尾。
set -u
NAME="${1:-}"; TASK_FILE="${2:-}"
[ -n "$NAME" ] && [ -f "$TASK_FILE" ] || { echo "用法：$0 <名字> <任务说明文件>" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"          # 按脚本所在位置定位项目（C2），不写死 ~/Documents/...
PROJ="$(cd "$HERE/.." && pwd)"
. "$HERE/codex_lib.sh"
TIMEOUT="${CODEX_TIMEOUT:-900}"
OUT="$PROJ/reports/codex_$NAME.md"
WS="${CODEX_WS_ROOT:-/private/tmp/claude-501/codexws}/$NAME"
mkdir -p "$WS"
COMMON='你是独立复核者（配对制，要亲自重跑证据，不能只读报告）。项目在 '"$PROJ"'（对你只读；你的工作目录可写，TMPDIR 已指向它，tempfile 可用）。需要跑测试时，先 cp -R 项目到工作目录（排除 state/_to_delete、.secret、sealed、test2、tools/heldout），设虚拟 HOME 指向工作目录再跑，绝不写真实 ~/。不读 .secret/ sealed/ test2/ tools/heldout/ ~/.claude/projects 下的对话正文；不碰 ~/.claude/settings.json。注意：沙箱禁止 ps，依赖 ps 的场景失败要单独说明是否为环境原因。只把会造成真实影响的问题（漏报、崩溃、阻塞、数据丢失、写坏共享状态、改坏用户命令）作为打回理由；纯理论边角列为"建议，不阻塞"。结论只能是：通过 / 打回（具体问题+复现+行号）/ 需所有者决定。最后用中文给结论段和检查项表格，注明实跑还是模拟。'
cd "$WS" && TMPDIR="$WS" codex_run "$WS/codex.log" "$TIMEOUT" codex exec --disable fast_mode --skip-git-repo-check -s workspace-write -C "$WS" "$COMMON

$(cat "$TASK_FILE")"
RC=$?
QUOTA=$(codex_quota_line "$WS/codex.log")
# 输出约定不变（完整日志 + 末尾 exited 行）；失败时在第一行加一行状态，退出码非 0（C2）
HEAD=""
if [ "$CODEX_TIMED_OUT" = 1 ]; then HEAD="结论：Codex 超时（${TIMEOUT} 秒）被结束，复核没做完。"; RC=124
elif [ -n "$QUOTA" ] && { [ "$RC" != 0 ] || tail -5 "$WS/codex.log" | grep -q -F "$QUOTA"; }; then
  # 退出码非 0，或报错行就在日志最后几行（真实撞额度时是这样）才算；避免把中途已恢复的限流当成失败
  HEAD="结论：Codex 额度已用完或被限流（429），复核没做完（rc=${RC}）。${QUOTA}"; RC=75
fi
{ [ -n "$HEAD" ] && printf '%s\n\n' "$HEAD"; cat "$WS/codex.log"; echo "codex review $NAME exited $RC"; } > "$OUT"
[ -n "$HEAD" ] && echo "$HEAD" >&2
exit "$RC"
