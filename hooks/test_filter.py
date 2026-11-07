#!/usr/bin/env python3
"""PreToolUse(Bash) hook：测试命令只把失败部分和结尾摘要给 Claude 看，完整输出存文件。

只在「命令位置」识别测试（每段的第一个词），只包装测试那一段，其余段的输出原样保留。
看不准就放行：含管道/重定向/heredoc/$(...)/反引号/括号子 shell/后台 &，任何一段以 bash 保留字开头
（if/for/while/{ 等多行结构），测试带 --watch/--pdb 等交互参数，或带 USAGE_SAVER_RAW=1。
包装保留原退出码；在 set -e 下也先打印失败摘要再退出。任何异常一律放行。
包装测试时顺手把超过 7 天的日志移到 testlogs/_old/（不删，最多每小时一次）。
R5I（项目画像）：内置规则（pytest / python -m pytest / npm test / node --test）没认出测试时，才按 hook 输入的 cwd
读 tools/project_profile.py 的画像，认这个项目自己的测试命令（lake build、cargo test、go test、make test、
python tests/test_x.py、覆盖文件 .usage-saver.json 的 test_commands …），并把画像的 grep 片段（如 Lean 的
warning:|sorry、中文「失败」）加进失败摘要。内置规则认出的命令改写结果与改前逐字相同；画像缺失/出错 = 改前行为。
R6T（第 6 轮）：
- 摘要改由本脚本的 --summarize 模式生成（失败多时 grep|head -120 会丢掉中段的失败）：先保证每个失败的名字
  （FAILED/ERROR 行、short test summary）都在，再给每个失败至少一行 E 报错，剩下的预算才放上下文。
  行数预算按失败数增长（120 + 4×失败数，上限 300 行），总字节上限 SUM_BYTES；放不下就写明「还有 N 个失败没显示」。
  原样打印日志行（不再带 grep -n 的行号前缀），省略处写「…（省略第 a–b 行）…」。python 跑不起来就退回旧的 grep/tail。
- 实测 Claude Code（2.1.319）：一次 Bash 输出超过 30000 字节（UTF-8 字节，不是字符）就整段落盘，模型只看到前 2KB 预览
  （看不到结尾的失败列表），所以摘要必须远低于 30000 字节，见 SUM_BYTES。
- 认 `timeout [选项] 时长 pytest …`（含 gtimeout）。
- 整条命令拆不了段时（前面有管道、重定向、单行 [[ a && b ]] 等），只要最后一段是单纯的测试命令、前面是单行的简单结构
  （没有 heredoc、换行、括号/子 shell、反引号/$(…)、保留字、后台 &），就只包装最后一段，前面原样保留（tail_split）。"""
import json, sys, re, os, shlex, time

LOGDIR = os.path.expanduser("~/Documents/Projects/claude-usage-saver/state/testlogs")
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
GREP_PAT = "(FAIL|ERROR|Error|error:|Traceback|assert|not ok)"
RESERVED = {"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac",
            "in", "{", "}", "function", "select", "!", "time", "coproc"}
INTERACTIVE = ("--watch", "--pdb", "--trace", "--looponfail")   # 交互/常驻：输出全进文件会什么都看不到，放行
SHORT = 40                                                       # 日志不超过这么多行就原样 cat，不过滤
# R6T：摘要预算
SUM_BYTES = 16000            # 摘要（不含首行日志路径）字节上限；Claude Code 超过 30000 字节就只给 2KB 预览，留足余量
BASE_LINES, PER_FAIL, MAX_LINES = 120, 4, 300    # 正文行数预算 = min(MAX_LINES, BASE_LINES + PER_FAIL × 失败数)
TAIL_N, CTX_B, CTX_A = 12, 2, 12                 # 结尾行数；匹配行的前/后上下文（与旧 grep -B2 -A12 相同）
LINE_CHARS = 400                                 # 单行超过这么多字符就截断（名字在行首，截断不丢名字）
TIMEOUT_HEADS = ("timeout", "gtimeout")
TIMEOUT_FLAGS = ("--foreground", "--preserve-status", "-v", "--verbose")
TIMEOUT_ARGS = ("-s", "--signal", "-k", "--kill-after")      # 后面跟一个参数的选项
DURATION = re.compile(r"[0-9]+(\.[0-9]+)?[smhd]?")
FALLBACK_RESERVED = RESERVED | {"]]", "repeat", "foreach", "end", "nocorrect"}   # tail_split 里命令位置出现就放行


def split_segments(cmd):
    """按未加引号的 ; && || 换行 拆段，去掉注释。返回 [(段文本, 后面的分隔符)]；看不懂返回 None。
    B4：# 只有在「词首」才是注释（前面是未转义的空格/制表符或段首）；`foo\\ #x` 里的 # 是字面字符。
    B4：段首是 [[ 时，在 ]] 之前遇到 ; && || 换行 就整条放行——[[ … ]] 里不能拆段。
    B5：认不出来的引号 / 嵌套形式一律整条放行：$'…'（ANSI-C，里面 \\' 不结束引号）、$"…"、$[…]；
        双引号里的 $( 和反引号（里面还能再套引号）；${…} 里含引号、$、\\、括号、; & | < > 换行，或没有闭合的 }。"""
    if "$'" in cmd or '$"' in cmd:                # B5：出现就放行，不管在不在引号里（宁可少包装）
        return None
    segs, buf, i, q = [], [], 0, None
    n = len(cmd)

    def param_end(j):
        """cmd[j:j+2] == '${'：返回 } 之后的位置；里面有认不出来的内容（嵌套引号/替换/分隔符）返回 None。"""
        k = cmd.find("}", j + 2)
        if k < 0:
            return None
        if any(ch in "\"'`$\\{();&|<>\n" for ch in cmd[j + 2:k]):
            return None
        return k + 1
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
        two = cmd[i:i + 2]
        if q == '"':                              # B5：双引号里的嵌套结构
            if c == "`" or two in ("$(", "$["):
                return None
            if two == "${":
                e = param_end(i)
                if e is None:
                    return None
                buf.extend(cmd[i:e]); word.extend(cmd[i:e]); i = e; continue
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
        if two == "$[":                           # B5：旧式算术 $[…]，不认识，放行
            return None
        if two == "${":                           # B5：${…} 整块照抄（里面的 ; # 不是分隔符/注释）；认不出来就放行
            e = param_end(i)
            if e is None:
                return None
            buf.extend(cmd[i:e]); word.extend(cmd[i:e]); ws = False; i = e; continue
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


def _param_end(cmd, j):
    """cmd[j:j+2] == '${'：返回 } 之后的位置；里面有认不出来的内容返回 None（与 split_segments 里的规则相同）。"""
    k = cmd.find("}", j + 2)
    if k < 0 or any(ch in "\"'`$\\{();&|<>\n" for ch in cmd[j + 2:k]):
        return None
    return k + 1


def tail_split(cmd):
    """R6T：split_segments 认不出来的整条命令，找最后一个顶层分隔符（; && ||），返回 (前缀原文, 分隔符, 最后一段)。
    只接受单行、结构简单的命令，否则返回 None（放行）：
    - 不许换行、反引号、$(…)、$'…'、$"…"、$[…]、heredoc、括号（子 shell / 进程替换 / zsh 通配限定符）、后台 &、;;；
    - 命令位置不许出现保留字（if/for/{/!/time …），[[ … ]] 必须在同一行闭合，里面的 && || < > 不算分隔符；
    - 最后一个顶层分隔符必须是 ; && ||（是管道 | 就放行：测试的输出被管道接走了）；
    - # 注释只在词首算，注释一直到行尾（单行），只可能落在最后一段里，交给 split_segments 去掉。
    前缀一个字符都不改，拼回去是 `前缀 分隔符 { 包装 }`；bash/zsh 里 && || 同级左结合、; 最低，所以语义不变。"""
    if not cmd or any(ch in cmd for ch in "\n\r`") or any(t in cmd for t in ("$(", "$'", '$"', "$[", "<<")):
        return None
    n, i, q = len(cmd), 0, None
    last = None                                   # (分隔符开始, 结束, 分隔符)
    word, ws = [], True
    st = {"cmdpos": True, "cond": False, "nw": 0}  # 命令位置？在 [[ 里？本条简单命令已有几个词

    def end_word():
        w = "".join(word); word.clear()
        if not w:
            return True
        st["nw"] += 1
        if st["cond"]:
            if w == "]]":
                st["cond"] = False
            return True
        if st["cmdpos"]:
            if ENV_ASSIGN.match(w):
                return True                       # FOO=1 之后仍是命令位置
            if w == "[[":
                st["cond"] = True; st["cmdpos"] = False; return True
            if w in FALLBACK_RESERVED:
                return False
            st["cmdpos"] = False
        return True

    def new_cmd(a, b, sep):
        nonlocal last
        if not end_word() or st["nw"] == 0 or st["cond"]:
            return False                          # 分隔符前面没有命令（`&& x`、`a ;; b`）或 [[ 没闭合
        last = (a, b, sep); st["cmdpos"] = True; st["nw"] = 0
        return True

    while i < n:
        c, two = cmd[i], cmd[i:i + 2]
        if q == "'":
            word.append(c); q = None if c == "'" else q; i += 1; continue
        if q == '"':
            if c == "\\" and i + 1 < n:
                word.append(two); i += 2; continue
            if two == "${":
                e = _param_end(cmd, i)
                if e is None:
                    return None
                word.append(cmd[i:e]); i = e; continue
            word.append(c); q = None if c == '"' else q; i += 1; continue
        if c in "'\"":
            q = c; word.append(c); ws = False; i += 1; continue
        if c == "\\":
            if i + 1 >= n:
                return None
            word.append(two); ws = False; i += 2; continue
        if c == "#" and ws:
            break                                 # 注释到行尾（命令是单行）
        if two == "${":
            e = _param_end(cmd, i)
            if e is None:
                return None
            word.append(cmd[i:e]); ws = False; i = e; continue
        if c in " \t":
            if not end_word():
                return None
            ws = True; i += 1; continue
        if c in "()":
            return None
        if st["cond"]:                            # [[ … ]] 里：&& || 是条件运算符，; | & 单独出现看不懂
            if two in ("&&", "||"):
                if not end_word():
                    return None
                ws = True; i += 2; continue
            if c in ";|&":
                return None
        elif two in ("&&", "||"):
            if not new_cmd(i, i + 2, two):
                return None
            ws = True; i += 2; continue
        elif c == ";":
            if cmd[i + 1:i + 2] in (";", "&") or not new_cmd(i, i + 1, ";"):
                return None
            ws = True; i += 1; continue
        elif c == "|":
            if i > 0 and cmd[i - 1] == ">":
                return None                       # >| 强制覆盖重定向：不认
            e = i + 2 if two == "|&" else i + 1
            if not new_cmd(i, e, "|"):
                return None
            ws = True; i = e; continue
        elif c == "&":
            if not ((i > 0 and cmd[i - 1] in "<>") or cmd[i + 1:i + 2] == ">"):
                return None                       # 单独的 & 是后台：放行（2>&1、>&2、&> 是重定向，照常）
        word.append(c); ws = False; i += 1
    if q or not end_word() or st["cond"] or not last or last[2] not in (";", "&&", "||"):
        return None
    prefix, tail = cmd[:last[0]], cmd[last[1]:]
    if not prefix.strip() or not tail.strip():
        return None
    return prefix, last[2], tail


def strip_timeout(w):
    """R6T：`timeout [选项] 时长 命令…` → 命令…；不是 timeout 原样返回；timeout 的写法认不出来返回 None。"""
    if not w or os.path.basename(w[0]) not in TIMEOUT_HEADS:
        return w
    i = 1
    while i < len(w):
        a = w[i]
        if a in TIMEOUT_FLAGS:
            i += 1
        elif a in TIMEOUT_ARGS:
            i += 2
        elif re.fullmatch(r"--(signal|kill-after)=\S+|-[sk]\S+", a):
            i += 1
        else:
            break
    if i < len(w) and DURATION.fullmatch(w[i]) and len(w) > i + 1:
        return w[i + 1:]
    return None


def is_test(seg, prof=None, pp=None):
    try:
        w = shlex.split(seg)
    except ValueError:
        return False
    while w and ENV_ASSIGN.match(w[0]):          # 跳过 FOO=1 这类前缀
        w = w[1:]
    if not w:
        return False
    w = strip_timeout(w)                         # R6T：timeout 60 pytest …
    if not w:
        return False
    if any(a.startswith(INTERACTIVE) for a in w[1:]):   # --watch/--watchAll/--pdb 等：放行
        return False
    head = os.path.basename(w[0])
    if head == "pytest" or head == "py.test":
        return True
    if re.fullmatch(r"python[0-9.]*", head):     # python -m pytest / .venv/bin/python3 -m pytest
        args = w[1:]
        if "-m" in args and args[args.index("-m") + 1:args.index("-m") + 2] == ["pytest"]:
            return True
    elif head == "npm":
        if w[1:2] == ["test"] or w[1:3] == ["run", "test"]:
            return True
    elif head == "node":
        if "--test" in w[1:]:
            return True
    if prof and pp:                              # R5I：这个项目自己的测试命令
        try:
            return bool(pp.match_test(w, prof))
        except Exception:
            return False
    return False


NAME_GENERIC = re.compile(r"^(?:(?:FAILED|ERROR)\s+\S|(?:FAIL|ERROR):\s+\S|\s*not ok\s+\d|--- FAIL:\s|test\s+\S.*\s\.\.\.\s+FAILED\s*$)")
PY_BANNER = re.compile(r"^=+ .* =+$")                  # pytest 的 ==== xxx ==== 分节线
PY_FAILSEC = re.compile(r"^=+ (FAILURES|ERRORS) =+$")
PY_HDR = re.compile(r"^_{3,} (.+?) _{3,}$")            # ____ test_x ____ 每个失败的小节标题
PY_NAME = re.compile(r"^(FAILED|ERROR)\s")             # short test summary 里的一行
E_LINE = re.compile(r"^E\s")
LOC_LINE = re.compile(r"^\S[^:\s]*:\d+: \S")         # test_x.py:30: AssertionError
# R6T2：pytest 逐行进度（-v、-rA、xdist 等插件）。PASSED/SKIPPED/XFAIL 一律丢掉只计数；
# FAILED/ERROR 进度行和 short test summary 里的同名 FAILED 行去重（summary 里有就丢进度行）。
PROG_STAT = r"(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)"
PROG_V = re.compile(r"^(\S+::\S+)(?:\s+<-\s+\S+)?\s+" + PROG_STAT + r"(?:\s+\(.*\))?(?:\s+\[\s*\d+%\])?\s*$")
PROG_X = re.compile(r"^(?:\[gw\d+\]\s+)?(?:\[\s*\d+%\]\s+)?" + PROG_STAT + r"\s+(\S+::\S+)\s*$")   # xdist、-rA
PROG_DROP = ("PASSED", "SKIPPED", "XFAIL")


def progress(t):
    """是 pytest 进度行就返回 (状态, 测试名)，否则 None。"""
    m = PROG_V.match(t)
    if m:
        return m.group(2), m.group(1)
    m = PROG_X.match(t)
    if m:
        return m.group(1), m.group(2)
    return None


def summarize(lines, pat, log):
    """R6T：把长日志压成摘要文本（不含首行日志路径）。优先级：
    0 失败名字（short test summary 的 FAILED/ERROR 行及其标题；没有 summary 时用通用规则认 FAIL:/not ok/--- FAIL 等）
    1 每个 pytest 失败小节：标题 + 第一行 E 和紧挨着的第二行 E（没有 E 就取小节里第一条匹配行）；再给出错位置行（可选）
    2 每个小节再多给：> 那一行、后面最多 4 行 E
    3 旧规则：匹配 pat 的行，前 2 后 12 行上下文（按出现顺序，直到预算用完）
    结尾 TAIL_N 行总是给。行数预算 min(MAX_LINES, BASE_LINES + PER_FAIL×失败数)，字节预算 SUM_BYTES。
    R6T2：pytest 逐行进度里的 PASSED/SKIPPED/XFAIL 一律丢掉只计数；FAILED/ERROR 进度行与 summary 同名的丢掉（去重）；
    0、1 级之外的内容另受「可见行数 ≤ 原来一半」约束。"""
    rx = re.compile(pat)
    n = len(lines)
    tail0 = max(0, n - TAIL_N)
    prog = {}                                     # 行号 → (状态, 测试名)
    for k, t in enumerate(lines):
        if "::" in t:
            pr = progress(t)
            if pr:
                prog[k] = pr
    cut = lambda t: t if len(t) <= LINE_CHARS else t[:LINE_CHARS] + " …[本行共 %d 字符，已截断]" % len(t)
    blen = lambda t: len(cut(t).encode("utf-8", "replace")) + 1
    # ---- pytest 结构：失败小节、short test summary 区间
    sections, cur, in_fail, summ = [], None, False, None
    for k, t in enumerate(lines):
        if PY_BANNER.match(t):
            if cur is not None:
                sections.append((cur, k)); cur = None
            in_fail = bool(PY_FAILSEC.match(t))
            if summ and summ[1] is None:
                summ[1] = k
            if "short test summary info" in t:
                summ = [k, None]
            continue
        if in_fail and PY_HDR.match(t):
            if cur is not None:
                sections.append((cur, k))
            cur = k
    if cur is not None:
        sections.append((cur, n))
    if summ and summ[1] is None:
        summ[1] = n
    if summ:
        names = [k for k in range(summ[0] + 1, summ[1]) if PY_NAME.match(lines[k])]
        tier0 = ([summ[0]] if names else []) + names     # 没有失败就不必给 summary 标题
    else:
        names = [k for k, t in enumerate(lines) if NAME_GENERIC.match(t)]
        tier0 = list(names)
    # R6T2：要丢的进度行。PASSED/SKIPPED/XFAIL 全丢；FAILED/ERROR 进度行若 summary 里已有同名就丢（去重）
    summ_ids = set()
    if summ:
        for k in names:
            w = lines[k].split()
            if len(w) > 1:
                summ_ids.add(w[1])
    drop_pass = {k for k, (st, _) in prog.items() if st in PROG_DROP}
    name_set = set(names)
    drop_dup = {k for k, (st, nid) in prog.items()
                if st in ("FAILED", "ERROR") and k not in name_set           # summary 自己的行不丢
                and (nid in summ_ids or nid.lstrip(".FEsxX") in summ_ids)}   # -q -s 时行首会粘上 . F 等进度字符
    drop = drop_pass | drop_dup
    if not summ:                                  # 没有 summary：FAILED/ERROR 进度行本身就是失败名
        names = [k for k in names if k not in drop] + \
                [k for k, (st, _) in sorted(prog.items()) if st in ("FAILED", "ERROR") and not NAME_GENERIC.match(lines[k])]
        names.sort()
        tier0 = list(names)
    tail_keep = [k for k in range(tail0, n) if k not in drop]
    nfail = max(len(names), len(sections))
    max_lines = min(MAX_LINES, BASE_LINES + PER_FAIL * nfail)
    # R6T2：可选内容（出错位置、第 2 级、第 3 级）另受「可见行数不超过原来一半」约束（日志很短时至少 SHORT 行）；
    # 必保内容（失败名、小节标题、每个失败第一行 E）只受 max_lines 约束，宁可超过一半也不丢
    # 只对 pytest 形状的日志生效；jest、cargo、lake、go 等保持 R6T 的行为不变
    py = bool(summ or sections or prog)
    soft_lines = max(0, min(max_lines, max(SHORT, n // 2) - len(tail_keep) - 7)) if py else max_lines
    footer_cost = 600 + (100 if drop else 0)      # 结尾标记、提示行的估计字节数（含中文）
    used = {"lines": 0, "bytes": footer_cost + sum(blen(lines[k]) for k in tail_keep)}
    chosen = set()

    def add(k, must=False):
        if k >= tail0 or k in chosen or k in drop:
            return True                           # 结尾里本来就有、已选、或是要丢的进度行
        mark = 0 if (k - 1 in chosen or k == 0) else 1   # 前面可能多出一个省略标记
        cost = blen(lines[k]) + 40 * mark
        nl = 1 + (mark if py else 0)              # pytest 形状才把省略标记也算进行数
        if used["lines"] + nl > (max_lines if must else soft_lines) or used["bytes"] + cost > SUM_BYTES:
            return False
        chosen.add(k); used["lines"] += nl; used["bytes"] += cost
        return True

    shown_names = sum(1 for k in names if add(k, True))
    for k in tier0:
        add(k, True)
    sec_ok = 0
    for a, b in sections:                         # 1：每个失败至少一行 E
        es = [k for k in range(a + 1, b) if E_LINE.match(lines[k])]
        first = es[:1] or [k for k in range(a + 1, b) if rx.search(lines[k])][:1]
        if len(es) > 1 and es[1] == es[0] + 1:
            first = es[:2]                        # 紧挨着的第二行 E（通常是 assert 的具体值），只多 1 行、不多省略标记
        ok = add(a, True) and all(add(k, True) for k in first)
        sec_ok += ok
    for a, b in sections:                         # 1b：出错位置行（可选）
        for k in [k for k in range(a + 1, b) if LOC_LINE.match(lines[k])][-1:]:
            add(k)
    for a, b in sections:                         # 2
        for k in [k for k in range(a + 1, b) if lines[k].startswith(">")][:1] + \
                 [k for k in range(a + 1, b) if E_LINE.match(lines[k])][1:5]:
            add(k)
    full = False                                  # 3：旧的 grep 上下文
    for k, t in enumerate(lines[:tail0]):
        if full:
            break
        if rx.search(t):
            for m in range(max(0, k - CTX_B), min(n, k + CTX_A + 1)):
                if not add(m):
                    full = True; break
    miss_name = (len(names) - shown_names) if names else (len(sections) - sec_ok)   # 名字没显示的失败
    miss_e = (len(sections) - sec_ok) if names else 0                               # 名字在、报错行没给的失败
    out, prev = [], -1

    def gap(a, b):                                # 省略 lines[a:b]（0 起）；只省 1 行时直接给那一行，标记反而更长
        keep = [k for k in range(a, b) if k not in drop]
        if not keep:
            return                                # 全是丢掉的进度行，最后统一计数
        if b - a == 1:
            out.append(cut(lines[a]))
        else:
            out.append("…（省略第 %d–%d 行）…" % (a + 1, b))
    for k in sorted(chosen):
        gap(prev + 1, k)
        out.append(cut(lines[k])); prev = k
    if chosen:
        gap(prev + 1, tail0)
    if drop:
        cnt = {}
        for k in drop_pass:
            cnt[prog[k][0]] = cnt.get(prog[k][0], 0) + 1
        parts = ["%d 行 %s 进度" % (cnt[s_], s_) for s_ in PROG_DROP if s_ in cnt]
        if drop_dup:
            parts.append("%d 行 FAILED/ERROR 进度（名字已在汇总里）" % len(drop_dup))
        out.append("…（省略 %s）…" % "、".join(parts))
    out.append("----- 结尾 -----")
    out.extend(cut(lines[k]) for k in tail_keep)
    if miss_name > 0:
        out.append("[还有 %d 个失败没显示，完整日志在 %s]" % (miss_name, log))
    if miss_e > 0:
        out.append("[另有 %d 个失败只显示了名字，报错行没显示，完整日志在 %s]" % (miss_e, log))
    out.append("[测试输出已过滤：完整 %d 行在上面的日志里；需要原样输出就在命令前加 USAGE_SAVER_RAW=1]" % n)
    return "\n".join(out) + "\n"


def summarize_main(argv):
    """--summarize 日志 模式：打印摘要，退出码 0；出任何错退出码 3 且什么都不打印（外面的 shell 退回旧 grep/tail）。"""
    try:
        log, pat = argv[0], argv[1]
        if os.path.getsize(log) > 64 * 1024 * 1024:
            return 3                              # 超大日志交给更快的 grep/tail
        with open(log, encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\n")
        if lines and lines[-1] == "":
            lines.pop()                           # 文件末尾的换行不算一行（与 wc -l 一致）
        text = summarize(lines, pat, log)
        sys.stdout.write(text); sys.stdout.flush()
        return 0
    except Exception:
        return 3


def wrap(seg, logdir, grep=GREP_PAT):
    """包装一段测试命令。要点：
    - mktemp 失败就原样跑这一段（不过滤）；
    - 运行前先打印日志路径（超时被杀时也知道去哪看）；
    - 日志 ≤ SHORT 行、≤ SUM_BYTES 字节（UTF-8）、且没有超过 LINE_CHARS 的行，才直接 cat（避免 grep 片段和结尾重复打印）；
      R6T3：三条有一条不满足（或 awk 出错）就走摘要，否则短日志里的超长行会让输出超过 30000 字节被落盘；
    - R6T：长日志交给本脚本的 --summarize 出摘要；python 不在/出错（退出码非 0、没有输出）才退回旧的 grep|head + tail；
    - grep/tail/wc 都加 || true：在 set -eo pipefail 下 grep 无匹配(1)或 head 提前关管道(141)不会让 shell 提前退出；
      退出码只由最后的 (exit $__usrc) 决定。"""
    ld = shlex.quote(logdir)
    summ = shlex.quote(sys.executable or "python3") + " " + shlex.quote(os.path.abspath(__file__))
    return ("{ __usL=$(mktemp " + ld + "/run-XXXXXX 2>/dev/null) || __usL=; "
            "if [ -z \"$__usL\" ]; then { " + seg + "\n}; else "
            "echo \"[测试输出写入 $__usL]\"; "
            "{ " + seg + "\n} >\"$__usL\" 2>&1 && __usrc=0 || __usrc=$?; "
            "__usn=$(wc -l <\"$__usL\" 2>/dev/null | tr -d ' ') || __usn=0; "
            "__usb=$(wc -c <\"$__usL\" 2>/dev/null | tr -d ' ') || __usb=0; "
            "if [ \"$__usn\" -le " + str(SHORT) + " ] 2>/dev/null && [ \"$__usb\" -le " + str(SUM_BYTES) + " ] 2>/dev/null"
            " && awk 'length($0)>" + str(LINE_CHARS) + "{f=1;exit} END{exit f}' \"$__usL\" 2>/dev/null; "
            "then cat \"$__usL\" || true; else "
            "{ " + summ + " --summarize \"$__usL\" '" + grep + "' 2>/dev/null || { "
            "{ grep -nE -B2 -A12 '" + grep + "' \"$__usL\" | head -120; } || true; "
            "echo '----- 结尾 -----'; { tail -n 12 \"$__usL\"; } || true; "
            "echo \"[测试输出已过滤：完整 $__usn 行在上面的日志里；需要原样输出就在命令前加 USAGE_SAVER_RAW=1]\" || true; "
            "}; } || true; "
            "fi; (exit $__usrc); fi; }")


def grep_for(prof, pp):
    """R5I：画像的 grep 片段并进默认的失败模式；画像没有或不合法就用默认。"""
    extra = ((prof or {}).get("tests") or {}).get("grep_extra") if prof else None
    if extra and pp and pp.valid_grep(extra) and pp.valid_grep(GREP_PAT + "|" + extra):
        return "(" + GREP_PAT.strip("()") + "|" + extra + ")"
    return GREP_PAT


def rewrite(cmd, logdir=LOGDIR, prof=None, pp=None):
    """prof/pp：项目画像和画像模块（R5I）；不给就是改前的行为。"""
    if "USAGE_SAVER_RAW=1" in cmd or "<<" in cmd:
        return None
    segs = split_segments(cmd)
    if not segs:
        return rewrite_last(cmd, logdir, prof, pp)   # R6T：拆不了段时，只看最后一段
    if not any(s and is_test(s, prof, pp) for s, _ in segs):
        return None
    grep = grep_for(prof, pp)
    out = []
    for s, sep in segs:
        if s:
            out.append(wrap(s, logdir, grep) if is_test(s, prof, pp) else s)
        if sep and s:
            out.append(sep)
    # 去掉末尾悬空的分隔符
    while out and out[-1] in (";", "&&", "||"):
        out.pop()
    return " ".join(out)


def rewrite_last(cmd, logdir=LOGDIR, prof=None, pp=None):
    """R6T：`简单前缀 ; / && / || 测试命令`：只包装最后一段，前缀原文保留。认不出来返回 None（放行）。"""
    r = tail_split(cmd)
    if not r:
        return None
    prefix, sep, tail = r
    ts = split_segments(tail)
    if not ts or len(ts) != 1 or not ts[0][0] or not is_test(ts[0][0], prof, pp):
        return None
    return prefix + " " + sep + " " + wrap(ts[0][0], logdir, grep_for(prof, pp))


def load_profile(cwd):
    """(画像, 画像模块)：按本脚本位置找 tools/project_profile.py；没有 cwd、模块缺失或出错返回 (None, None)。"""
    try:
        if not cwd:
            return None, None
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "project_profile.py")
        if not os.path.isfile(path):
            return None, None
        import importlib.util
        spec = importlib.util.spec_from_file_location("usage_saver_project_profile", path)
        pp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pp)
        prof = pp.get_profile(cwd, budget_s=0.2)
        return (prof, pp) if isinstance(prof, dict) and prof.get("tests") else (None, None)
    except Exception:
        return None, None


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
    if sys.argv[1:2] == ["--summarize"]:         # R6T：包装命令跑完后调用本脚本出摘要
        sys.exit(summarize_main(sys.argv[2:]))
    try:
        d = json.load(sys.stdin)
        ti = d.get("tool_input") or {}
        cmd = ti.get("command") or ""
        new = rewrite(cmd)
        if not new and cmd.strip() and "USAGE_SAVER_RAW=1" not in cmd and "<<" not in cmd and (split_segments(cmd) or tail_split(cmd)):
            prof, pp = load_profile(d.get("cwd"))      # R5I：内置规则没认出时才读画像
            if prof:
                new = rewrite(cmd, LOGDIR, prof, pp)
        if new:
            os.makedirs(LOGDIR, exist_ok=True)
            prune_old_logs()                      # D7：顺手整理旧日志，出错不影响包装
            o = dict(ti); o["command"] = new
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": o}},
                             ensure_ascii=False))
    except Exception:
        pass
    sys.exit(0)
