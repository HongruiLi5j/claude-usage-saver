#!/usr/bin/env python3
"""PreToolUse(Bash) hook：测试命令只把失败部分和结尾摘要给 Claude 看，完整输出存文件。

只在「命令位置」识别测试（每段的第一个词），只包装测试那一段，其余段的输出原样保留。
看不准就放行：含管道/重定向/heredoc/$(...)/反引号/括号子 shell/后台 &，任何一段以 bash 保留字开头
（if/for/while/{ 等多行结构），测试带 --watch/--pdb 等交互参数，或带 USAGE_SAVER_RAW=1。
包装保留原退出码；在 set -e 下也先打印失败摘要再退出。任何异常一律放行。
包装测试时顺手把超过 7 天的日志移到 testlogs/_old/（不删，最多每小时一次）。"""
import json, sys, re, os, shlex, time

LOGDIR = os.path.expanduser("~/Documents/Projects/claude-usage-saver/state/testlogs")
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
GREP_PAT = "(FAIL|ERROR|Error|error:|Traceback|assert|not ok)"
RESERVED = {"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac",
            "in", "{", "}", "function", "select", "!", "time", "coproc"}
INTERACTIVE = ("--watch", "--pdb", "--trace", "--looponfail")   # 交互/常驻：输出全进文件会什么都看不到，放行
SHORT = 40                                                       # 日志不超过这么多行就原样 cat，不过滤


def split_segments(cmd):
    """按未加引号的 ; && || 换行 拆段，去掉注释。返回 [(段文本, 后面的分隔符)]；看不懂返回 None。
    B4：# 只有在「词首」才是注释（前面是未转义的空格/制表符或段首）；`foo\\ #x` 里的 # 是字面字符。
    B4：段首是 [[ 时，在 ]] 之前遇到 ; && || 换行 就整条放行——[[ … ]] 里不能拆段。"""
    segs, buf, i, q = [], [], 0, None
    n = len(cmd)
    ws = True                                     # 当前位置是否在词首（决定 # 是不是注释）
    word, nwords, cond = [], 0, False             # 当前词、本段已结束的词数、是否在未闭合的 [[ 里

    def end_word():
        nonlocal nwords, cond
        w = "".join(word); word.clear()
        if w:
            if nwords == 0 and w == "[[":
                cond = True
            elif cond and w == "]]":
                cond = False
            nwords += 1

    while i < n:
        c = cmd[i]
        if q:
            buf.append(c); word.append(c)
            if c == "\\" and q == '"' and i + 1 < n:
                buf.append(cmd[i + 1]); word.append(cmd[i + 1]); i += 2; continue
            if c == q:
                q = None
            i += 1; continue
        if c in "'\"":
            q = c; buf.append(c); word.append(c); ws = False; i += 1; continue
        if c == "\\" and i + 1 < n:
            buf.append(c); buf.append(cmd[i + 1])
            if cmd[i + 1] != "\n":                # \⏎ 是续行（bash 直接删掉），不改变词首状态
                word.append(c); word.append(cmd[i + 1]); ws = False
            i += 2; continue
        if c == "#" and ws:
            while i < n and cmd[i] != "\n":       # 注释到行尾
                i += 1
            continue
        two = cmd[i:i + 2]
        if two in ("&&", "||") or c in ";\n":
            end_word()
            if cond:
                return None                       # [[ … ]] 内部的换行 / && / ||：不拆，整条放行
            segs.append(("".join(buf), two if two in ("&&", "||") else ";"))
            buf = []; nwords = 0; ws = True
            i += 2 if two in ("&&", "||") else 1; continue
        if c in "|<>&()`" or two == "$(":
            return None                           # 管道/重定向/后台/子 shell：不碰
        buf.append(c)
        if c in " \t":
            end_word(); ws = True
        else:
            word.append(c); ws = False
        i += 1
    end_word()
    if q or cond:
        return None
    segs.append(("".join(buf), ""))
    segs = [(s.strip(), sep) for s, sep in segs]
    # B4：去掉首尾空白后段尾还剩一个未转义的 \（如 `foo\ ;`、`foo \⏎# 注释`），拼回会改变含义，放行
    for s, _ in segs:
        if (len(s) - len(s.rstrip("\\"))) % 2 == 1:
            return None
    # 任何一段以 bash 保留字开头（if/for/while/{ …）就整条放行：拆段后再拼回会把 then⏎ 变成 then ;，是语法错误
    for s, _ in segs:
        w = s.split(None, 1)
        if w and w[0] in RESERVED:
            return None
    return segs


def is_test(seg):
    try:
        w = shlex.split(seg)
    except ValueError:
        return False
    while w and ENV_ASSIGN.match(w[0]):          # 跳过 FOO=1 这类前缀
        w = w[1:]
    if not w:
        return False
    if any(a.startswith(INTERACTIVE) for a in w[1:]):   # --watch/--watchAll/--pdb 等：放行
        return False
    head = os.path.basename(w[0])
    if head == "pytest" or head == "py.test":
        return True
    if re.fullmatch(r"python[0-9.]*", head):     # python -m pytest / .venv/bin/python3 -m pytest
        args = w[1:]
        return "-m" in args and args[args.index("-m") + 1:args.index("-m") + 2] == ["pytest"]
    if head == "npm":
        return w[1:2] == ["test"] or w[1:3] == ["run", "test"]
    if head == "node":
        return "--test" in w[1:]
    return False


def wrap(seg, logdir):
    """包装一段测试命令。要点：
    - mktemp 失败就原样跑这一段（不过滤）；
    - 运行前先打印日志路径（超时被杀时也知道去哪看）；
    - 日志 ≤ SHORT 行直接 cat，避免 grep 片段和结尾重复打印；
    - grep/tail/wc 都加 || true：在 set -eo pipefail 下 grep 无匹配(1)或 head 提前关管道(141)不会让 shell 提前退出；
      退出码只由最后的 (exit $__usrc) 决定。"""
    ld = shlex.quote(logdir)
    return ("{ __usL=$(mktemp " + ld + "/run-XXXXXX 2>/dev/null) || __usL=; "
            "if [ -z \"$__usL\" ]; then { " + seg + "\n}; else "
            "echo \"[测试输出写入 $__usL]\"; "
            "{ " + seg + "\n} >\"$__usL\" 2>&1 && __usrc=0 || __usrc=$?; "
            "__usn=$(wc -l <\"$__usL\" 2>/dev/null | tr -d ' ') || __usn=0; "
            "if [ \"$__usn\" -le " + str(SHORT) + " ] 2>/dev/null; then cat \"$__usL\" || true; else "
            "{ grep -nE -B2 -A12 '" + GREP_PAT + "' \"$__usL\" | head -120; } || true; "
            "echo '----- 结尾 -----'; { tail -n 12 \"$__usL\"; } || true; "
            "echo \"[测试输出已过滤：完整 $__usn 行在上面的日志里；需要原样输出就在命令前加 USAGE_SAVER_RAW=1]\" || true; "
            "fi; (exit $__usrc); fi; }")


def rewrite(cmd, logdir=LOGDIR):
    if "USAGE_SAVER_RAW=1" in cmd or "<<" in cmd:
        return None
    segs = split_segments(cmd)
    if not segs:
        return None
    if not any(s and is_test(s) for s, _ in segs):
        return None
    out = []
    for s, sep in segs:
        if s:
            out.append(wrap(s, logdir) if is_test(s) else s)
        if sep and s:
            out.append(sep)
    # 去掉末尾悬空的分隔符
    while out and out[-1] in (";", "&&", "||"):
        out.pop()
    return " ".join(out)


def prune_old_logs(logdir=LOGDIR, days=7, every_s=3600):
    """把 logdir 里超过 days 天的 run-* 日志移到 logdir/_old/（不删）。
    最多每 every_s 秒做一次（看上一级目录里 .testlogs_last_prune 的修改时间；放在 logdir 外，
    免得 logdir 里混进非日志文件）。任何异常都吞掉，不输出任何东西。"""
    try:
        stamp = os.path.join(os.path.dirname(logdir), ".testlogs_last_prune")
        now = time.time()
        if os.path.exists(stamp) and now - os.path.getmtime(stamp) < every_s:
            return
        with open(stamp, "w"):
            pass                                  # 先更新时间戳：即使后面出错，一小时内也不再重试
        old = os.path.join(logdir, "_old")
        cutoff = now - days * 86400
        for name in os.listdir(logdir):
            p = os.path.join(logdir, name)
            try:
                if name.startswith("run-") and os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    os.makedirs(old, exist_ok=True)
                    dst = os.path.join(old, name)
                    if not os.path.exists(dst):   # 同名已存在就跳过，不覆盖
                        os.rename(p, dst)
            except Exception:
                pass
    except Exception:
        pass


if __name__ == "__main__":
    try:
        d = json.load(sys.stdin)
        ti = d.get("tool_input") or {}
        new = rewrite(ti.get("command") or "")
        if new:
            os.makedirs(LOGDIR, exist_ok=True)
            prune_old_logs()                      # D7：顺手整理旧日志，出错不影响包装
            o = dict(ti); o["command"] = new
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": o}},
                             ensure_ascii=False))
    except Exception:
        pass
    sys.exit(0)
