#!/usr/bin/env python3
"""usaver-run：跨工具的「测试/长输出瘦身」包装命令（第 5 轮 H）。

hooks/test_filter.py 靠 Claude Code 的 PreToolUse updatedInput 偷偷改写命令；Codex / Gemini / Cursor / Aider
不一定支持改写命令，所以这里换个做法：一个普通命令，任何工具（或人）都能显式调用：

    usaver-run pytest -q
    usaver-run npm test
    usaver-run --sh 'pytest -q tests/a && npm test'     # 整条交给 bash -c

行为（和 test_filter 保持同一套规则，常量和摘要都从 hooks/test_filter.py 导入，只有一份实现）：
- 完整输出（stdout+stderr 合并）写进日志文件，运行前先打印日志路径（超时被杀也知道去哪看）；
- 日志 ≤ SHORT 行：原样打印；否则交给 test_filter.summarize 出摘要（R6H2：和 Claude Code 版同一份代码——
  每个失败的名字和至少一行 E 都在，PASSED/SKIPPED/XFAIL 进度行只计数，摘要 ≤ SUM_BYTES 字节），
  再加一行英文提示（完整行数和日志路径）；
- 保底：找不到 test_filter、它没有 summarize、抛异常、返回的不是非空文本，或日志超过 64MB，就退回旧规则
  （匹配 FAIL/ERROR/Traceback/assert… 的行，前 2 后 12 行上下文，最多 120 行，加最后 12 行）；
- 退出码与原命令相同（被信号杀死时返回 128+信号号）；
- 放行（直接 exec，不过滤）：USAGE_SAVER_RAW=1、--raw、带 --watch/--pdb 等交互参数、日志目录建不了；
- 不删旧日志：沿用 test_filter.prune_old_logs（超过 7 天的移到 _old/）。
"""
import os, re, signal, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "hooks"))
_tf_summarize = None                            # R6H2：test_filter 的摘要函数；导入不了就是 None（走旧规则）
try:                                            # 与 Claude Code 版共用一套常量，改一处两边一起变
    import test_filter as _tf
    GREP_PAT, SHORT, INTERACTIVE, prune_old_logs = _tf.GREP_PAT, _tf.SHORT, _tf.INTERACTIVE, _tf.prune_old_logs
    _tf_summarize = getattr(_tf, "summarize", None)
except Exception:                               # 单独拷走 adapters/ 也能用
    _tf = None
    GREP_PAT = "(FAIL|ERROR|Error|error:|Traceback|assert|not ok)"
    SHORT = 40
    INTERACTIVE = ("--watch", "--pdb", "--trace", "--looponfail")
    def prune_old_logs(logdir, **kw):
        pass

BEFORE, AFTER, MAX_MATCH_LINES, TAIL = 2, 12, 120, 12     # 旧规则（保底用）
# R6H3：原样输出的另外两条门槛，与 test_filter.wrap 的 T3 判断相同（常量取自 test_filter，取不到用同值）
SUM_BYTES = getattr(_tf, "SUM_BYTES", 16000)    # 原样输出最多这么多 UTF-8 字节
LINE_CHARS = getattr(_tf, "LINE_CHARS", 400)    # 有任何一行超过这么多字符就不原样输出
BIG_CHARS = 64 * 1024 * 1024                    # 与 test_filter.summarize_main 一样：超大日志不走新摘要
USAGE = ("usage: usaver-run [--raw] [--logdir DIR] [--sh] [--] COMMAND [ARGS...]\n"
         "  Runs COMMAND, saves full output to a log, prints only failures + tail. Exit code is preserved.\n"
         "  --sh: treat the remaining words as one shell command line (bash -c).\n"
         "  USAGE_SAVER_RAW=1 or --raw: run unfiltered.\n")


def default_logdir():
    return os.environ.get("USAVER_LOGDIR") or os.path.join(REPO, "state", "testlogs")


def parse(argv):
    opts = {"raw": False, "logdir": None, "sh": False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            i += 1; break
        if a == "--raw":
            opts["raw"] = True
        elif a == "--sh":
            opts["sh"] = True
        elif a == "--logdir" and i + 1 < len(argv):
            opts["logdir"] = argv[i + 1]; i += 1
        elif a.startswith("--logdir="):
            opts["logdir"] = a.split("=", 1)[1]
        elif a in ("-h", "--help"):
            opts["help"] = True
        else:
            break
        i += 1
    return opts, argv[i:]


def select_lines(lines, pat=GREP_PAT):
    """等价于 grep -nE -B2 -A12 PAT | head -120：返回带行号的片段（不同片段之间用 -- 隔开）。"""
    rx = re.compile(pat)
    keep = []
    last_end = -1
    for idx, line in enumerate(lines):
        if rx.search(line):
            lo, hi = max(0, idx - BEFORE), min(len(lines) - 1, idx + AFTER)
            if keep and lo > last_end + 1:
                keep.append(None)               # 片段分隔
            for k in range(max(lo, last_end + 1), hi + 1):
                keep.append(k)
            last_end = max(last_end, hi)
    out = []
    for k in keep:
        if len(out) >= MAX_MATCH_LINES:
            break
        out.append("--" if k is None else "%d%s%s" % (k + 1, ":" if rx.search(lines[k]) else "-", lines[k]))
    return out


def summarize(log_text, logpath, short=SHORT):
    # R6H2：按 \n 切行、去掉末尾空行，和 test_filter.summarize_main / wc -l 的数法一致
    lines = log_text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    n = len(lines)
    # R6H3：与 test_filter.wrap 的 T3 一致，三条都满足才原样输出：≤ SHORT 行、≤ SUM_BYTES 字节（UTF-8）、
    # 没有超过 LINE_CHARS 字符的行；否则走摘要（只看行数时，一行 '中'*12000 就让 21 行的日志有 36KB）
    if (n <= short and len(log_text.encode("utf-8", "replace")) <= SUM_BYTES
            and all(len(t) <= LINE_CHARS for t in lines)):
        return log_text if log_text.endswith("\n") or not log_text else log_text + "\n"
    note = ("[usaver-run: output filtered; all %d lines are in %s; rerun with USAGE_SAVER_RAW=1 for raw output]"
            % (n, logpath))
    if _tf_summarize is not None and len(log_text) <= BIG_CHARS:
        try:
            body = _tf_summarize(lines, GREP_PAT, logpath)
            if isinstance(body, str) and body.strip():
                return body + ("" if body.endswith("\n") else "\n") + note + "\n"
        except Exception:
            pass                                # 出任何错都退回旧规则
    parts = select_lines(lines)
    parts.append("----- tail -----")
    parts.extend(lines[-TAIL:])
    parts.append(note)
    # R6H3：保底路径也截断超长行（旧规则只限行数不限字节）
    parts = [t if len(t) <= LINE_CHARS else t[:LINE_CHARS] + " ...[line truncated, %d chars]" % len(t) for t in parts]
    return "\n".join(parts) + "\n"


def passthrough(cmd, sh):
    """不过滤，直接换成目标进程（保留 tty、信号、退出码）。"""
    if sh:
        os.execvp("bash", ["bash", "-c", " ".join(cmd)])
    os.execvp(cmd[0], cmd)


def run(cmd, sh=False, logdir=None, raw=False, out=sys.stdout):
    """返回退出码。供测试直接调用（out 可换成 StringIO）。"""
    if raw or os.environ.get("USAGE_SAVER_RAW") == "1" or any(a.startswith(INTERACTIVE) for a in cmd[1:]):
        out.flush()
        passthrough(cmd, sh)
    # 日志目录依次尝试：指定的 → 套件 state/testlogs → 系统临时目录。Codex 等工具的沙箱常常只允许写工作区和
    # $TMPDIR，套件目录写不了时退到临时目录，而不是放弃过滤（第 5 轮 H 实测）。
    fd = None
    for d in ([logdir] if logdir else [default_logdir()]) + [os.path.join(tempfile.gettempdir(), "usaver-testlogs")]:
        try:
            os.makedirs(d, exist_ok=True)
            fd, logpath = tempfile.mkstemp(prefix="run-", dir=d)
            logdir = d
            break
        except Exception:
            continue
    if fd is None:
        out.flush()
        passthrough(cmd, sh)                    # 哪里都建不了日志就原样跑
    try:
        prune_old_logs(logdir)
    except Exception:
        pass
    out.write("[usaver-run: full output -> %s]\n" % logpath); out.flush()
    argv = ["bash", "-c", " ".join(cmd)] if sh else cmd
    with os.fdopen(fd, "wb") as logf:
        try:
            p = subprocess.Popen(argv, stdout=logf, stderr=subprocess.STDOUT)
        except FileNotFoundError:
            logf.write(("usaver-run: command not found: %s\n" % argv[0]).encode())
            rc = 127
        else:
            try:
                rc = p.wait()
            except KeyboardInterrupt:
                p.send_signal(signal.SIGINT)
                rc = p.wait()
    rc = 128 - rc if rc < 0 else rc             # Popen 用负数表示被信号杀死
    try:
        with open(logpath, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        text = ""
    try:
        s = summarize(text, logpath)
    except Exception:                           # R6H2：摘要本身出错也不能丢退出码
        s = "[usaver-run: could not summarize the output; full output is in %s]\n" % logpath
    out.write(s); out.flush()
    return rc


def main(argv=None):
    opts, cmd = parse(sys.argv[1:] if argv is None else argv)
    if opts.get("help") or not cmd:
        sys.stderr.write(USAGE)
        return 0 if opts.get("help") else 2
    return run(cmd, sh=opts["sh"], logdir=opts["logdir"], raw=opts["raw"])


if __name__ == "__main__":
    sys.exit(main())
