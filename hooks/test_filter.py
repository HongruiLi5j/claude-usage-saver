#!/usr/bin/env python3
"""PreToolUse(Bash) hook：测试命令只把失败部分和结尾摘要给 Claude 看，完整输出存文件。
绕过：命令里含 USAGE_SAVER_RAW=1，或命令本身已有管道/重定向。"""
import json, sys, re, os, shlex
PAT = re.compile(r'(^|[;&]\s*|\s)((\S*python\S*\s+(-\S+\s+)*-m\s+pytest)|pytest|npm\s+(run\s+)?test|node\s+(--\S+\s+)*--test)(\s|$)')
LOGDIR = os.path.expanduser("~/Documents/Projects/claude-usage-saver/state/testlogs")
try:
    d = json.load(sys.stdin)
    ti = d.get("tool_input") or {}
    cmd = ti.get("command") or ""
    if (not PAT.search(cmd)) or "USAGE_SAVER_RAW=1" in cmd or re.search(r'[|>]', cmd) or "<<" in cmd or cmd.rstrip().endswith("&"):
        sys.exit(0)
    os.makedirs(LOGDIR, exist_ok=True)
    wrapped = (f'L=$(mktemp {shlex.quote(LOGDIR)}/run-XXXXXX); {{\n{cmd}\n}} >"$L" 2>&1; rc=$?; '
               f'grep -nE -A6 "(FAIL|ERROR|Error|error:|Traceback|assert|not ok)" "$L" | head -80; '
               f'echo "----- 结尾 -----"; tail -n 12 "$L"; '
               f'echo "[测试输出已过滤：完整 $(wc -l <"$L") 行在 $L ；需要原样输出就在命令前加 USAGE_SAVER_RAW=1]"; exit $rc')
    out = dict(ti); out["command"] = wrapped
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": out}}, ensure_ascii=False))
except Exception:
    pass
sys.exit(0)
