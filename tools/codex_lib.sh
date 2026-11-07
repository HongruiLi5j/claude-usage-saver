# codex_lib.sh：被 tools/codex_task.sh 和 tools/codex_review.sh 用 source 引入，不单独运行。（第 5 轮 C2）
# 提供三样东西：
#   codex_run <日志> <超时秒> <命令...>   stdin 接 /dev/null，到点先 TERM 整个进程组，5 秒后再 KILL
#                                          执行后 CODEX_TIMED_OUT=1 表示是超时杀掉的；返回值是命令的退出码
#   codex_quota_line <日志>                日志里有"额度用完 / 限流 429"的报错行就打印第一行（最多 200 字符）
#   退出码约定：超时 124（和 coreutils timeout 一样），额度用完或 429 返回 75（EX_TEMPFAIL），用法错 2
# macOS 自带的工具里没有 timeout/gtimeout，所以这里自己实现。

codex_run() {
  local log="$1" t="$2"; shift 2
  local mark="$log.timeout"
  : > "$mark"
  set -m                                   # 后台任务单独成一个进程组，超时可以连 node 和原生 codex 子进程一起杀
  "$@" < /dev/null > "$log" 2>&1 &
  local pid=$!
  ( sleep "$t"; echo 1 > "$mark"; kill -TERM -- "-$pid" 2>/dev/null; sleep 5; kill -KILL -- "-$pid" 2>/dev/null ) \
    < /dev/null > /dev/null 2>&1 &
  local wd=$!
  set +m
  wait "$pid" 2>/dev/null; local rc=$?
  kill -TERM -- "-$wd" 2>/dev/null; wait "$wd" 2>/dev/null
  CODEX_TIMED_OUT=0; [ -s "$mark" ] && CODEX_TIMED_OUT=1
  return "$rc"
}

codex_quota_line() {
  # 只看 Codex 自己的 ERROR 行，避免把被复核代码或命令输出里出现的 "429"、"usage limit" 当成报错
  grep -E -i -m1 '^(ERROR|error)[: ].*(usage limit|rate limit|429|too many requests|quota)' "$1" 2>/dev/null | cut -c1-200
}
