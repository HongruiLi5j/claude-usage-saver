#!/bin/bash
# 把"复核 / 通读 / 调研"类子任务派给 Codex（不占 Claude 额度），主会话只读一个短结论文件。
#
# 用法：tools/codex_task.sh <review|read|research> <名字> <任务说明文件>
#   结论 → reports/codex_<名字>.md（只有 Codex 的最终回复 + 末尾一行元数据，通常 1–4k 字符）
#   完整过程日志 → <工作区>/codex.log（主会话不要读，排查时才看）
#   退出码：0 成功；124 超时；75 额度用完/429；1 没有最终回复；2 用法错；其它为 codex 自身退出码
#   脚本 stdout：一行状态（结论路径 + 字符数）+ 结论全文（CODEX_QUIET=1 时只有状态行）。进程退出即完成。
# 环境变量（可选）：
#   CODEX_EFFORT=low|medium|high   推理强度，默认 medium（第 5 轮实验用的就是 medium）
#   CODEX_SEARCH=0|1               开网页搜索；research 默认 1，其它默认 0
#   CODEX_WS=<目录>                工作区（可写）；默认 $CODEX_WS_ROOT/<名字>
#   CODEX_WS_ROOT=<目录>           默认 /private/tmp/claude-501/codexws
#   CODEX_OUT=<文件>               结论输出路径，覆盖默认
#   CODEX_FAST=1                   保留 fast 档（默认 --disable fast_mode，省约 60% Codex 额度，更慢）
#   CODEX_TIMEOUT=<秒>             超时，默认 900（15 分钟）；到点结束，结论第一行写明，退出码 124
#   CODEX_QUIET=1                  stdout 只打一行状态，不打结论
#   CODEX_PRINT_PROMPT=1           只打印拼好的完整 prompt，不运行（用于同一 brief 交给 claude -p 做对照）
# 主会话的典型用法（Bash 工具）：
#   预计 <9 分钟：前台跑，timeout 设 600000，结论直接出现在工具结果里
#   更长：run_in_background，完成通知后只 Read reports/codex_<名字>.md（不要读 codex.log）
set -u
KIND="${1:-}"; NAME="${2:-}"; TASK_FILE="${3:-}"
case "${KIND}" in review|read|research) ;; *)
  echo "用法：$0 <review|read|research> <名字> <任务说明文件>" >&2; exit 2;; esac
[ -n "${NAME}" ] && [ -f "${TASK_FILE}" ] || { echo "缺名字或任务文件不存在：${TASK_FILE}" >&2; exit 2; }

HERE="$(cd "$(dirname "$0")" && pwd)"          # 按脚本所在位置定位项目（C2），不写死 ~/Documents/...
PROJ="$(cd "$HERE/.." && pwd)"
. "$HERE/codex_lib.sh"
TIMEOUT="${CODEX_TIMEOUT:-900}"
WS="${CODEX_WS:-${CODEX_WS_ROOT:-/private/tmp/claude-501/codexws}/${NAME}}"
OUT="${CODEX_OUT:-$PROJ/reports/codex_${NAME}.md}"
EFFORT="${CODEX_EFFORT:-medium}"
if [ "${KIND}" = research ]; then SEARCH="${CODEX_SEARCH:-1}"; else SEARCH="${CODEX_SEARCH:-0}"; fi
mkdir -p "${WS}" "$(dirname "${OUT}")"

COMMON="你是主会话派出的独立执行者。你的工作目录 ${WS} 可写（TMPDIR 已指向它）。
规则：
- 只读任务说明里点名的路径；不读 .secret/ sealed/ test2/ tools/heldout/，不读 ~/.claude/projects 下的对话正文，不改 ~/.claude/ 和 ~/.codex/ 下任何文件。
- 不写工作目录以外的地方。要跑会写文件的代码，先复制到工作目录，并把 HOME 设成工作目录再跑。
- 沙箱可能禁止 ps、网络等；因环境失败的检查要写明是环境原因，不能算成被测对象的问题。
- 你的最终回复会原样交给主会话，主会话只读这一条、不看过程。所以要短：不贴大段源码或日志，用 文件:行号 指向证据。用中文。"

case "${KIND}" in
review) SPEC="任务类型：复核。
- 能跑就亲自跑证据（复制到工作目录、虚拟 HOME）；跑不了的写\"模拟\"。
- 只把会造成真实影响的问题（漏报、崩溃、阻塞、数据丢失、写坏共享状态、改坏用户命令）列为问题；理论边角放到\"建议\"。
- 每个问题都要有最小复现（命令或输入）和你看到的实际结果；没有复现的不要列为问题。
最终回复格式（总共不超过 40 行）：
第一行：结论：通过 / 打回 / 需所有者决定
## 问题
- [高/中/低] 文件:行 — 一句话说明 — 最小复现 — 实际结果 — 实跑/模拟
## 建议（不阻塞，最多 5 条）
## 没做到的检查（最多 3 条）";;
read) SPEC="任务类型：通读并回答。
- 先用 rg/grep -n 定位，再只读需要的段落；不要整文件打印大文件。
- 每个答案都要给出处（文件:行号）。找不到就写\"未找到\"，不要猜。
最终回复格式：每题一行：Qn: 答案 — 出处 文件:行号。最后一行写没读到的文件（没有就写\"无\"）。";;
research) SPEC="任务类型：调研。
- 优先一手来源（官方文档、--help 输出、源码）；本地能验证的，亲自跑命令验证。
- 每条答案标注依据：实测 / 文档 / 推测。
最终回复格式（总共不超过 30 行）：每题：Qn: 答案 — 来源（URL 或命令）— 实测/文档/推测。最后一行列出互相矛盾或查不到的点（没有就写\"无\"）。";;
esac

PROMPT="$COMMON

$SPEC

==== 任务说明 ====
$(cat "${TASK_FILE}")"

if [ "${CODEX_PRINT_PROMPT:-0}" = 1 ]; then printf '%s\n' "$PROMPT"; exit 0; fi

EXTRA=()
# 默认关 fast 档：本机 ~/.codex/config.toml 是 service_tier="priority"（fast），官方说按 2.5 倍扣 Codex 额度。CODEX_FAST=1 保留 fast
[ "${CODEX_FAST:-0}" = 1 ] || EXTRA+=(--disable fast_mode)
[ "$SEARCH" = 1 ] && EXTRA+=(-c 'web_search="live"')
: > "${WS}/last.md"
T0=$(date +%s)
cd "${WS}" && TMPDIR="${WS}" codex_run "${WS}/codex.log" "${TIMEOUT}" codex exec --skip-git-repo-check --ephemeral -s workspace-write -C "${WS}" \
  -c model_reasoning_effort="\"${EFFORT}\"" ${EXTRA[@]+"${EXTRA[@]}"} -o "${WS}/last.md" "$PROMPT"
RC=$?
SECS=$(( $(date +%s) - T0 ))
TOK=$(grep -A1 '^tokens used' "${WS}/codex.log" | tail -1 | tr -d ', ')
QUOTA=$(codex_quota_line "${WS}/codex.log")
if [ "${CODEX_TIMED_OUT}" = 1 ]; then
  { echo "结论：Codex 超时（${TIMEOUT} 秒）被结束，任务没做完。请缩小任务后重派，或改派 Claude 子 agent。"
    [ -s "${WS}/last.md" ] && { echo "（超时前已有的最后回复如下，可能不完整）"; cat "${WS}/last.md"; }; } > "${OUT}"
  RC=124
elif [ -n "${QUOTA}" ] && { [ "${RC}" != 0 ] || [ ! -s "${WS}/last.md" ]; }; then
  { echo "结论：Codex 额度已用完或被限流（429），任务没做完（rc=${RC}）。请改派 Claude 子 agent，或等 Codex 额度恢复。"
    echo "${QUOTA}"; } > "${OUT}"
  RC=75
elif [ -s "${WS}/last.md" ]; then cp "${WS}/last.md" "${OUT}"
else
  { echo "结论：Codex 没有给出最终回复（rc=${RC}）。日志末尾："; tail -5 "${WS}/codex.log" | cut -c1-300; } > "${OUT}"
  [ "${RC}" = 0 ] && RC=1
fi
printf '\n<!-- codex_task kind=%s rc=%s secs=%s codex_tokens=%s log=%s -->\n' "${KIND}" "${RC}" "${SECS}" "${TOK:-?}" "${WS}/codex.log" >> "${OUT}"
echo "codex_task ${KIND} ${NAME} rc=${RC} ${SECS}s → ${OUT} ($(wc -c < "${OUT}" | tr -d ' ') 字符)"
# 默认把结论也打到 stdout：前台跑时主会话直接拿到结论，不必再 Read 一次（省一次请求）；CODEX_QUIET=1 只打一行状态
[ "${CODEX_QUIET:-0}" = 1 ] || cat "${OUT}"
exit "${RC}"
