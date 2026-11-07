#!/usr/bin/env python3
"""PreToolUse(Bash|Read) hook：read_guard —— 同一个 agent 里重复读「没改过的文件」时，拦下这次读取。

做什么（只有一种动作会影响模型：deny；从不改写命令，从不输出 updatedInput）：
  1. 记录：每次 Bash 读文件（cat / nl / head / tail / sed -n 行段 / less，含 `| grep` 之类过滤）和
     Read 工具调用，记下「哪个文件、哪一段、文件指纹（大小+mtime+inode+内容 sha1）、tool_use_id」。
  2. 重复：之后同一个 agent（session_id + agent_id）再用 Bash 读「已经读过、被上次覆盖」的同一段，且
       - 文件指纹完全没变（内容 sha1 相同）；
       - 上次那条 tool_result 在 transcript 里找得到，不是错误，不是「输出太大落盘只给预览」；
       - 从上次读到现在，transcript 里没有 compact_boundary（没压缩过，上次输出还在上下文里）；
       - 这条命令是「纯读取」：只由 cd / echo / printf / 读文件 / 管道过滤组成，没有重定向写文件、
         没有 $ 变量、通配符、子 shell、后台 &，拒绝它不会少做任何有副作用的事；
     全部满足才 deny，并告诉模型「文件没变，用上次那份；真找不到就原样再发一次」。
  3. 放行阀：同一个 agent 对同一条命令「被拦后再发一次」一律放行（deny-once）；命令里带
     USAGE_SAVER_RAW=1 也放行。Read 工具永远不拦（Read→Read 重复 Claude Code 自己会给短桩；
     Bash 读过之后的 Read 可能是为了 Edit，拦了会坏事），只记录。
  4. 可选「大文件整读提醒」（config.json 里 read_guard.big，默认 off）：见 BIG_* 常量。

fail-open：任何异常、看不懂的命令、找不到 transcript、拿不到锁、状态文件坏了 —— 一律不输出、退出码 0（放行）。
状态：<套件>/state/read_guard/<session_id>.json（state/ 已 gitignore）。关掉：config.json 设
"read_guard": {"dup": "off"} 或环境变量 READ_GUARD=off。
"""
import hashlib, json, os, re, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.environ.get("READ_GUARD_STATE") or os.path.join(ROOT, "state", "read_guard")
CONFIG = os.environ.get("READ_GUARD_CONFIG") or os.path.join(ROOT, "config.json")

INLINE_LIMIT = 30000        # Claude Code 默认 Bash 输出超过 30000 字符就落盘、只给约 2k 预览
MAX_OUT_BYTES = 28000       # 本次要读的内容超过这么多字节，上次那份可能被截断，不算重复
MAX_HASH_BYTES = 2_000_000  # 大于 2MB 的文件不算内容哈希、不参与（行段读大文件也不拦）
MAX_SCAN_BYTES = 30_000_000 # 在 transcript 里往后找上次结果 / compact_boundary，最多扫这么多
MAX_PATHS = 300             # 每个 agent 最多记这么多个文件
BIG_LINES, BIG_BYTES = 600, 40000   # 「大文件整读」阈值（只在 big != off 时用）

# ---------------------------------------------------------------- 词法 / 语法（只做识别，不改命令）

def lex(cmd):
    """把命令拆成 token：('w', 文本, unsafe) / ('op', 文本) / ('redir', 文本)。
    unsafe=True 表示这个词里有未加单引号的 $、通配符 * ? [、或 ~ 以外的展开，结果可能随环境变。
    遇到反引号、heredoc/herestring、引号不闭合、结尾悬空的 \\ —— 返回 None（看不懂就不管）。"""
    toks, buf, i, n = [], [], 0, len(cmd)
    st = {"in": False, "unsafe": False}

    def flush():
        if st["in"]:
            toks.append(("w", "".join(buf), st["unsafe"]))
        buf.clear(); st["in"] = False; st["unsafe"] = False

    while i < n:
        c = cmd[i]
        if c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                return None
            buf.append(cmd[i + 1:j]); st["in"] = True; i = j + 1; continue
        if c == '"':
            i += 1; st["in"] = True
            while i < n and cmd[i] != '"':
                if cmd[i] == "\\" and i + 1 < n and cmd[i + 1] in '"\\$`\n':
                    if cmd[i + 1] != "\n":
                        buf.append(cmd[i + 1])
                    i += 2; continue
                if cmd[i] in "$`":
                    st["unsafe"] = True
                buf.append(cmd[i]); i += 1
            if i >= n:
                return None
            i += 1; continue
        if c == "\\":
            if i + 1 >= n:
                return None
            if cmd[i + 1] == "\n":
                i += 2; continue
            buf.append(cmd[i + 1]); st["in"] = True; i += 2; continue
        if c == "#" and not st["in"]:
            while i < n and cmd[i] != "\n":
                i += 1
            continue
        if c in " \t\r":
            flush(); i += 1; continue
        if c == "`":
            return None
        if c in "$*?[{":
            if c != "{" or (i + 1 < n and cmd[i + 1] != "}"):   # {} 单独出现（find -exec）不算展开
                st["unsafe"] = True
            buf.append(c); st["in"] = True; i += 1; continue
        if c in "<>":
            word = "".join(buf)
            if st["in"] and word.isdigit():
                fd = word; buf.clear(); st["in"] = False; st["unsafe"] = False
            else:
                flush(); fd = ""
            op = c; i += 1
            if c == "<" and i < n and cmd[i] in "<(":
                return None                       # heredoc / herestring / 进程替换：不管
            if i < n and cmd[i] in ">&|":
                op += cmd[i]; i += 1
            toks.append(("redir", fd + op)); continue
        if c in ";\n&|()":
            flush()
            two = cmd[i:i + 2]
            if two == "&>":
                toks.append(("redir", "&>")); i += 2
                if i < n and cmd[i] == ">":
                    i += 1
                continue
            if two in ("&&", "||", "|&", ";;"):
                toks.append(("op", two)); i += 2; continue
            toks.append(("op", c)); i += 1; continue
        buf.append(c); st["in"] = True; i += 1
    flush()
    return toks


def structure(toks):
    """token → [ {'pipe': [简单命令...], 'sep': 分隔符} ]；简单命令 = {'words': [(文本, unsafe)], 'redirs': [(op, 目标)], 'env': {}}。
    有括号/子 shell、;; 、重定向后面没目标 —— 返回 None。"""
    lists, pipe, cur = [], [], {"words": [], "redirs": [], "env": {}}
    k = 0
    while k < len(toks):
        t = toks[k]
        if t[0] == "w":
            if not cur["words"] and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t[1]):
                name, _, val = t[1].partition("=")
                cur["env"][name] = (val, t[2])
            else:
                cur["words"].append((t[1], t[2]))
        elif t[0] == "redir":
            if k + 1 >= len(toks) or toks[k + 1][0] != "w":
                return None
            cur["redirs"].append((t[1], toks[k + 1][1], toks[k + 1][2])); k += 2; continue
        else:
            op = t[1]
            if op in ("(", ")", ";;"):
                return None
            if op in ("|", "|&"):
                pipe.append(cur); cur = {"words": [], "redirs": [], "env": {}}
            else:
                pipe.append(cur)
                lists.append({"pipe": pipe, "sep": op})
                pipe, cur = [], {"words": [], "redirs": [], "env": {}}
        k += 1
    pipe.append(cur)
    lists.append({"pipe": pipe, "sep": ""})
    out = []
    for L in lists:
        L["pipe"] = [c for c in L["pipe"] if c["words"] or c["redirs"] or c["env"]]
        if L["pipe"]:
            out.append(L)
        elif L["sep"] == "&":
            return None
    return out

# ---------------------------------------------------------------- 识别「读文件」

RE_SED_RANGE = re.compile(r"^\s*(\d+)\s*(?:,\s*(\d+|\$)\s*)?p\s*$")
FILTERS = {"grep", "egrep", "fgrep", "rg", "head", "tail", "wc", "sort", "uniq", "cut", "tr", "nl", "cat",
           "column", "awk", "sed", "fold", "expand", "less", "more"}
SAFE_STDERR = ("2>", "2>>")


def _num(s):
    return int(s) if s.isdigit() else None


def _sed_ranges(scripts):
    """sed -n 脚本 → [(a, b|None)]；只认纯行号打印（'10,50p' '5p' '1,$p' 用 ; 或多个 -e 连起来），其余 None。"""
    rs = []
    for s in scripts:
        for part in s.split(";"):
            if not part.strip():
                continue
            m = RE_SED_RANGE.match(part)
            if not m:
                return None
            a = int(m.group(1)); b = m.group(2)
            rs.append((a, None if b == "$" else int(b) if b else a))
    return rs or None


def parse_read_cmd(words):
    """一个简单命令（不含 env 前缀）→ ('read', verb, [(文件, spec)], pure) / ('other', verb)。
    spec：('full',) | ('lines', a, b|None) | ('tail', n) | ('bytes', 'head'|'tail', n) | ('cmd', 原文)。
    pure=False：能认出在读，但选项里有不确定的东西（只统计，不拦）。"""
    if not words:
        return ("other", "")
    w = [x[0] for x in words]
    v = os.path.basename(w[0])
    args = w[1:]
    unsafe = any(x[1] for x in words)
    if v in ("cat", "nl", "less", "more"):
        files, pure = [], not unsafe
        for a in args:
            if a == "-":
                return ("other", v)
            if a.startswith("-") and len(a) > 1:
                if v == "cat" and not re.fullmatch(r"-[nbs]+", a):
                    pure = False
                continue
            files.append(a)
        if not files:
            return ("other", v)
        return ("read", v, [(f, ("full",)) for f in files], pure)
    if v in ("head", "tail"):
        n, mode, files, pure, k = 10, "lines", [], not unsafe, 0
        frm = False
        while k < len(args):
            a = args[k]
            if a.startswith("--follow") or re.fullmatch(r"-[A-Za-z]*[fF][A-Za-z]*", a):
                return ("other", v)              # tail -f：常驻，不管
            if a in ("-n", "-c") and k + 1 < len(args):
                val = args[k + 1]; mode = "bytes" if a == "-c" else "lines"
                frm = val.startswith("+"); val = val.lstrip("+-")
                if not val.isdigit():
                    return ("other", v)
                n = int(val); k += 2; continue
            m = re.fullmatch(r"-([nc])(\+?)(\d+)", a) or re.fullmatch(r"--(lines|bytes)=(\+?)(\d+)", a)
            if m:
                mode = "bytes" if m.group(1) in ("c", "bytes") else "lines"; frm = m.group(2) == "+"; n = int(m.group(3)); k += 1; continue
            m = re.fullmatch(r"([-+])(\d+)", a)
            if m:
                frm = m.group(1) == "+" and v == "tail"; n = int(m.group(2)); k += 1; continue
            if a in ("-q", "-v", "--quiet", "--silent", "--verbose"):
                k += 1; continue
            if a.startswith("-") and len(a) > 1:
                pure = False; k += 1; continue
            files.append(a); k += 1
        if not files:
            return ("other", v)
        if mode == "bytes":
            spec = ("bytes", v, n) if not frm else ("cmd", json.dumps(w, ensure_ascii=False))
        elif v == "head":
            spec = ("lines", 1, n)
        elif frm:
            spec = ("lines", n, None)
        else:
            spec = ("tail", n)
        return ("read", v, [(f, spec) for f in files], pure)
    if v in ("sed", "gsed"):
        quiet, scripts, files, k, pure = False, [], [], 0, not unsafe
        while k < len(args):
            a = args[k]
            if a in ("-n", "--quiet", "--silent"):
                quiet = True
            elif a in ("-e", "--expression") and k + 1 < len(args):
                scripts.append(args[k + 1]); k += 1
            elif a.startswith("-i") or a.startswith("--in-place"):
                return ("other", v)              # 改文件
            elif a in ("-E", "-r", "-s", "-u", "--posix"):
                pass
            elif re.fullmatch(r"-[nEsru]+", a):
                quiet = quiet or "n" in a
            elif a.startswith("-") and len(a) > 1:
                pure = False
            elif not scripts and not files and not any(x in ("-e", "--expression") for x in args):
                scripts.append(a)
            else:
                files.append(a)
            k += 1
        if not files or not scripts:
            return ("other", v)
        rs = _sed_ranges(scripts) if quiet else None
        if rs is None:
            if any(re.search(r"(^|[;}\s])[wWeR]\b|\b[wW]\s", s) for s in scripts):
                return ("other", v)              # sed 的 w/e 命令会写文件/执行
            return ("read", v, [(f, ("cmd", json.dumps(w, ensure_ascii=False))) for f in files], False)
        if len(files) > 1:                        # 多文件时行号是拼起来算的，只做精确匹配
            return ("read", v, [(f, ("cmd", json.dumps(w, ensure_ascii=False))) for f in files], pure)
        if len(rs) == 1:
            return ("read", v, [(files[0], ("lines", rs[0][0], rs[0][1]))], pure)
        return ("read", v, [(files[0], ("cmd", json.dumps(w, ensure_ascii=False)))], pure)
    if re.fullmatch(r"python[0-9.]*", v) and len(args) >= 2 and args[0] == "-c":
        prog = args[1]
        fs = re.findall(r"open\(\s*r?['\"]([^'\"]+)['\"]", prog)
        if fs and ("read" in prog or "load" in prog) and not re.search(r"open\([^)]*['\"][wax]\+?b?['\"]", prog):
            return ("read", "python", [(f, ("cmd", prog)) for f in fs], False)
        return ("other", "python")
    return ("other", v)


def _filter_ok(words):
    """管道后半段的过滤命令是否只往 stdout 输出（不写文件、不执行东西）。"""
    if not words or any(x[1] for x in words):
        return False
    w = [x[0] for x in words]
    v = os.path.basename(w[0])
    if v not in FILTERS:
        return False
    if v == "sed":
        if any(a.startswith("-i") or a.startswith("--in-place") for a in w[1:]):
            return False
        # 脚本里出现 w/W/e/r/R 命令或 s///w、s///e 标志就当成可能写文件/执行（宁可漏拦）
        return not any(re.search(r"/[A-Za-z0-9]*[weWR]|(^|[;{}\s\d$,!])[wWeRr]\b", a) for a in w[1:] if not a.startswith("-"))
    if v == "awk":
        return not any(re.search(r"[>|]|system|getline", a) for a in w[1:])
    if v == "sort":
        return not any(a.startswith("-o") or a.startswith("--output") for a in w[1:])
    if v == "tail":
        return not any(a in ("-f", "-F") or a.startswith("--follow") for a in w[1:])
    return True


def analyze(cmd, cwd):
    """整条 Bash 命令 → dict(units=[{path, spec, verb, filtered}], pure=bool, raw=bool, reasons=[])；看不懂返回 None。
    pure：拒绝这条命令不会少做任何有副作用的事（只有 cd / echo / printf / 读文件 / 只输出到 stdout 的过滤）。"""
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    toks = lex(cmd)
    if toks is None:
        return None
    lists = structure(toks)
    if lists is None:
        return None
    cwd = cwd or os.getcwd()
    units, pure, reasons = [], True, []
    raw = "USAGE_SAVER_RAW=1" in cmd
    for L in lists:
        if L["sep"] == "&":
            pure = False; reasons.append("后台&")
        pipe = L["pipe"]
        for c in pipe:
            for r in c["redirs"]:
                ok = (r[0] in SAFE_STDERR and r[1] == "/dev/null") or (r[0] == "2>&" and r[1] == "1")
                if not ok:
                    pure = False; reasons.append("重定向")
            if c["env"]:
                if any(u for _, u in c["env"].values()):
                    pure = False
        head = pipe[0]
        if not head["words"]:
            pure = False; continue
        w0 = os.path.basename(head["words"][0][0])
        if w0 == "cd":
            if len(pipe) > 1 or len(head["words"]) > 2 or any(x[1] for x in head["words"]):
                pure = False; reasons.append("cd 带展开"); continue
            tgt = head["words"][1][0] if len(head["words"]) == 2 else "~"
            cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(tgt)))
            continue
        if w0 in ("echo", "printf", "true", ":"):
            if len(pipe) > 1 or any(x[1] for x in head["words"]):
                pure = False; reasons.append("echo 带展开或管道")
            continue
        r = parse_read_cmd(head["words"])
        if r[0] != "read":
            pure = False; reasons.append("非读取:" + r[1])
            continue
        _, verb, fl, p = r
        if not p:
            pure = False; reasons.append("读取选项不确定")
        filt = len(pipe) > 1
        if filt and not all(_filter_ok(c["words"]) for c in pipe[1:]):
            pure = False; reasons.append("管道到非过滤命令")
        seg_text = json.dumps([[x[0] for x in c["words"]] for c in pipe], ensure_ascii=False)
        for f, spec in fl:
            path = os.path.normpath(os.path.join(cwd, os.path.expanduser(f)))
            if filt:
                spec = ("cmd", seg_text)
            num = (not filt) and (verb == "nl" or (verb == "cat" and any(re.fullmatch(r"-[nbs]*[nb][nbs]*", x[0]) for x in head["words"][1:])))
            units.append({"path": path, "spec": list(spec), "verb": verb, "filtered": filt, "num": num,
                          "unsafe": any(x[1] for x in head["words"])})
    return {"units": units, "pure": pure and bool(units), "raw": raw, "reasons": reasons, "cwd_end": cwd}


def read_units(tool_input):
    """Read 工具 → [unit]。offset 按 1 起算；没 offset/limit 记为 full。"""
    p = tool_input.get("file_path")
    if not isinstance(p, str) or not p:
        return []
    off, lim = tool_input.get("offset"), tool_input.get("limit")
    if off is None and lim is None:
        spec = ["full"]
    else:
        try:
            a = max(int(off or 1), 1); b = a + int(lim or 2000) - 1
        except (TypeError, ValueError):
            return []
        spec = ["lines", a, b]
    if tool_input.get("pages"):
        spec = ["cmd", "pages=" + str(tool_input.get("pages"))]
    return [{"path": os.path.normpath(os.path.expanduser(p)), "spec": spec, "verb": "Read", "filtered": False, "num": True, "unsafe": False}]

# ---------------------------------------------------------------- 覆盖关系

def covers(old, new, old_num=False, new_num=False):
    """上次读到的 old 是否已经包含这次要读的 new（同一文件、内容没变的前提下）。
    old_num/new_num：输出带不带行号（cat -n / nl / Read 带；cat / sed -n / head / tail 不带）。
    - 完全相同的读法：算覆盖（这次要行号、上次没有就不算）。
    - 「上次整读、这次只要其中一段」：只有上次带行号才算——否则模型没法按行号在旧输出里找到那一段
      （典型：先 cat 整个文件，后来照着报错行号 sed -n '120,160p'），拦了就是误伤。"""
    o, nw = tuple(old), tuple(new)
    if new_num and not old_num:
        return False
    if o == nw:
        return True
    if not old_num:
        return False
    if o[0] == "full":
        return nw[0] in ("full", "lines", "tail")
    if o[0] == "lines" and nw[0] == "lines":
        a, b = o[1], o[2]; c, d = nw[1], nw[2]
        return a <= c and (b is None or (d is not None and d <= b))
    return False

# ---------------------------------------------------------------- 文件指纹

def fingerprint(path):
    """(size, mtime_ns, ino, sha1) ；文件不存在/不是普通文件/太大 → None。同时返回内容（bytes）供算输出大小。"""
    try:
        st = os.stat(path)
        if not os.path.isfile(path) or st.st_size > MAX_HASH_BYTES:
            return None, None
        with open(path, "rb") as f:
            data = f.read(MAX_HASH_BYTES + 1)
        return [st.st_size, st.st_mtime_ns, st.st_ino, hashlib.sha1(data).hexdigest()], data
    except Exception:
        return None, None


def out_bytes(spec, data):
    """这一段读出来大概多少字节（不算 cat -n 的行号前缀）。"""
    if data is None:
        return None
    s = tuple(spec)
    if s[0] == "full":
        return len(data)
    lines = data.split(b"\n")
    if s[0] == "lines":
        a, b = s[1], s[2]
        return sum(len(x) + 1 for x in lines[a - 1:(b if b is not None else len(lines))])
    if s[0] == "tail":
        return sum(len(x) + 1 for x in lines[-(s[1] + 1):])
    if s[0] == "bytes":
        return min(s[2], len(data))
    return len(data)                              # cmd：不知道过滤后多大，按整文件算（偏保守）

# ---------------------------------------------------------------- transcript 校验

def _scan(tpath, off):
    """从 off 往后读 transcript 的字节；读不到返回 None。"""
    try:
        sz = os.path.getsize(tpath)
        if sz < off or sz - off > MAX_SCAN_BYTES:
            return None
        with open(tpath, "rb") as f:
            f.seek(off)
            return f.read()
    except Exception:
        return None


def _compacted_after(blob, start):
    """start 之后有没有 compact_boundary 记录（逐行解析确认，正文里提到这个词不算）。"""
    pos = blob.find(b"compact_boundary", start)
    while pos >= 0:
        ls = blob.rfind(b"\n", 0, pos) + 1
        le = blob.find(b"\n", pos)
        le = len(blob) if le < 0 else le
        try:
            j = json.loads(blob[ls:le])
            if isinstance(j, dict) and j.get("type") == "system" and j.get("subtype") == "compact_boundary":
                return True
        except Exception:
            return True                           # 解析不了：当作压缩过（偏向放行）
        pos = blob.find(b"compact_boundary", le)
    return False


def prior_result_ok(rec, tpaths):
    """rec 记录的那次读取：在 transcript 里找到它的 tool_result，且不是错误、不是落盘预览、之后没压缩过。"""
    tuid = rec.get("tuid")
    if not tuid:
        return False
    for tp in tpaths:
        off = (rec.get("toff") or {}).get(tp)
        if off is None:
            continue
        blob = _scan(tp, off)
        if blob is None:
            continue
        key = ('"%s"' % tuid).encode()          # id 形如 toolu_01…，全局唯一；找到后再解析整行确认
        pos = blob.find(key)
        while pos >= 0:
            ls = blob.rfind(b"\n", 0, pos) + 1
            le = blob.find(b"\n", pos)
            le = len(blob) if le < 0 else le
            try:
                j = json.loads(blob[ls:le])
            except Exception:
                j = None
            if j and j.get("type") == "user":
                for b in ((j.get("message") or {}).get("content") or []):
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == tuid:
                        if b.get("is_error"):
                            return False
                        c = b.get("content")
                        txt = c if isinstance(c, str) else "".join(x.get("text", "") for x in c if isinstance(x, dict)) if isinstance(c, list) else ""
                        if "<persisted-output>" in txt or "Output too large" in txt[:400] or len(txt) >= INLINE_LIMIT \
                                or "PARTIAL view" in txt[-600:] or "exceeds maximum allowed" in txt[:400]:
                            return False
                        if txt.startswith("File unchanged since last read") or txt.startswith("Wasted call"):
                            return False         # 上次本身就是桩，不是真内容
                        if _compacted_after(blob, le):
                            return False         # 之后压缩过
                        return True
            pos = blob.find(key, pos + 1)
    return False

# ---------------------------------------------------------------- 状态

def load_config():
    cfg = {"dup": "deny", "big": "off", "big_lines": BIG_LINES, "big_bytes": BIG_BYTES}
    try:
        with open(CONFIG, encoding="utf-8") as f:
            c = json.load(f).get("read_guard") or {}
        if isinstance(c, dict):
            for k in cfg:
                if k in c:
                    cfg[k] = c[k]
    except Exception:
        pass
    if os.environ.get("READ_GUARD") == "off":
        cfg["dup"] = cfg["big"] = "off"
    return cfg


def _state_path(sid):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", sid)[:120] or "nosession"
    return os.path.join(STATE_DIR, safe + ".json")


class Locked:
    """对状态文件加非阻塞锁；0.3 秒拿不到就放弃（ok=False，调用方放行）。"""
    def __init__(self, path):
        self.path, self.fh, self.ok = path + ".lock", None, False

    def __enter__(self):
        try:
            import fcntl
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self.fh = open(self.path, "a")
            t0 = time.time()
            while True:
                try:
                    fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB); self.ok = True; break
                except OSError:
                    if time.time() - t0 > 0.3:
                        break
                    time.sleep(0.02)
        except Exception:
            self.ok = False
        return self

    def __exit__(self, *a):
        try:
            if self.fh:
                self.fh.close()
        except Exception:
            pass


def load_state(p):
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and d.get("v") == 1 and isinstance(d.get("agents"), dict):
            return d
    except Exception:
        pass
    return {"v": 1, "agents": {}}


def save_state(p, d):
    tmp = p + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, p)

# ---------------------------------------------------------------- 决策（纯函数，回放/测试直接调）

def cmd_key(cmd, cwd):
    return hashlib.sha1((cwd + "\0" + cmd).encode("utf-8", "surrogatepass")).hexdigest()[:16]


def decide(agent, tool, cmd, cwd, units, pure, raw, fps, result_ok, cfg, now_seq, tuid, toff):
    """返回 (action, message, dup_info)；action ∈ allow / deny / advise。会就地更新 agent（记录本次读取）。
    fps: {path: (fingerprint, 本段输出字节)}；result_ok(rec) → 上次那份是否完整在上下文里。"""
    recs = agent.setdefault("recs", {})
    denied = agent.setdefault("denied", {})
    action, msg, info = "allow", None, None
    key = cmd_key(cmd, cwd) if tool == "Bash" else None
    if tool == "Bash" and units and pure and not raw and cfg.get("dup") in ("deny", "advise"):
        hits, total = [], 0
        for u in units:
            fp, nbytes = fps.get(u["path"], (None, None))
            if fp is None or nbytes is None:
                hits = None; break
            total += nbytes
            hit = None
            for rec in reversed(recs.get(u["path"], [])):
                if rec["fp"] == fp and covers(rec["spec"], u["spec"], rec.get("num", False), u.get("num", False)) and result_ok(rec):
                    hit = rec; break
            if hit is None:
                hits = None; break
            hits.append((u, hit))
        if hits and total <= MAX_OUT_BYTES:
            if key in denied:
                del denied[key]                   # 被拦后又发了同一条：放行
                info = {"retry_after_deny": True}
            else:
                ago = max(now_seq - min(h["seq"] for _, h in hits), 0)
                names = "、".join(sorted({os.path.basename(u["path"]) for u, _ in hits}))
                msg = ("[read_guard] 没有执行这条命令：%s 自你 %d 次工具调用前读过之后内容没变（sha1 相同），"
                       "上次的输出还在你的上下文里，请直接用那份，不必再读。"
                       "如果确实找不到那份输出，原样再发一次同一条命令就会放行。" % (names, ago))
                action = "deny" if cfg.get("dup") == "deny" else "advise"
                if action == "deny":
                    denied[key] = now_seq
                info = {"ago": ago, "bytes": total, "n_units": len(hits)}
    if action == "allow" and tool == "Bash" and units and pure and not raw and cfg.get("big") in ("deny", "advise"):
        big = []
        for u in units:
            if tuple(u["spec"]) == ("full",):
                fp, nbytes = fps.get(u["path"], (None, None))
                if fp and fp[0] >= int(cfg.get("big_bytes", BIG_BYTES)):
                    big.append(u)
        if big and key not in denied:
            msg = ("[read_guard] %s 很大（≥%d 字节）。先用 grep -n 定位，再用 sed -n 'a,bp' 只读需要的几十行；"
                   "确实要整个文件，原样再发一次同一条命令就会放行。" % (os.path.basename(big[0]["path"]), int(cfg.get("big_bytes", BIG_BYTES))))
            action = "deny" if cfg.get("big") == "deny" else "advise"
            if action == "deny":
                denied[key] = now_seq
            info = {"big": True}
        elif big and key in denied:
            del denied[key]
    # 记录本次读取（被拦的不记：没读）
    if action != "deny":
        for u in units:
            fp, nbytes = fps.get(u["path"], (None, None))
            if fp is None:
                continue
            lst = recs.setdefault(u["path"], [])
            lst.append({"spec": u["spec"], "num": u.get("num", False), "fp": fp, "tuid": tuid, "toff": toff, "seq": now_seq})
            del lst[:-6]
        if len(recs) > MAX_PATHS:
            for p in sorted(recs, key=lambda p: recs[p][-1]["seq"])[:len(recs) - MAX_PATHS]:
                del recs[p]
    for k in [k for k, s in denied.items() if now_seq - s > 50]:
        del denied[k]
    return action, msg, info


def transcript_candidates(d):
    """这个 agent 的 tool_result 会写到哪个 transcript。
    主会话：transcript_path；子 agent：<项目>/<session_id>/subagents/agent-<agent_id>.jsonl
    （如果 transcript_path 本身就是这个文件也认）。文件暂时不存在也返回（偏移记 0，之后找不到结果就放行）。"""
    tp = d.get("transcript_path") or ""
    aid, sid = d.get("agent_id"), d.get("session_id")
    if not tp or not isinstance(tp, str):
        return []
    if aid and sid:
        suffix = os.path.join(sid, "subagents", "agent-%s.jsonl" % aid)
        if tp.endswith(suffix):
            return [tp]
        return [os.path.join(os.path.dirname(tp), suffix)]
    return [tp]


def handle(d, cfg=None):
    """hook 主逻辑。返回要打印的 dict 或 None。"""
    cfg = cfg or load_config()
    if cfg.get("dup") == "off" and cfg.get("big") == "off":
        return None
    tool = d.get("tool_name")
    ti = d.get("tool_input") or {}
    sid = d.get("session_id") or ""
    if tool not in ("Bash", "Read") or not sid:
        return None
    cwd = d.get("cwd") or os.getcwd()
    if tool == "Bash":
        cmd = ti.get("command") or ""
        a = analyze(cmd, cwd)
        if a is None or not a["units"]:
            return None
        units, pure, raw = a["units"], a["pure"], a["raw"]
        units = [u for u in units if not u["unsafe"]]
        if len(units) != len(a["units"]):
            pure = False
    else:
        cmd = ""
        units, pure, raw = read_units(ti), False, False
        if not units:
            return None
    fps = {}                                      # 同一文件出现多次（cat a; head a）时输出字节累加
    for u in units:
        fp, data = fingerprint(u["path"])
        ob = out_bytes(u["spec"], data) if fp else None
        prev = fps.get(u["path"], (None, 0))[1]
        fps[u["path"]] = (fp, None if ob is None or prev is None else prev + ob)
    if tool == "Read":                            # Read 默认只给前 2000 行、单行超 2000 字符会截断：按它实际给的范围记
        fp, data = fingerprint(units[0]["path"])
        if fp is None:
            return None
        lines = data.split(b"\n")
        if any(len(x) > 2000 for x in lines):
            return None
        if units[0]["spec"] == ["full"] and len(lines) > 2000:
            units[0]["spec"] = ["lines", 1, 2000]
    tps = transcript_candidates(d)
    toff = {}
    for tp in tps:
        try:
            toff[tp] = os.path.getsize(tp)
        except Exception:
            toff[tp] = 0
    sp = _state_path(sid)
    with Locked(sp) as lk:
        if not lk.ok:
            return None
        st = load_state(sp)
        agent = st["agents"].setdefault(d.get("agent_id") or "main", {})
        seq = agent.get("seq", 0) + 1
        agent["seq"] = seq
        action, msg, info = decide(agent, tool, cmd, cwd, units, pure, raw, fps,
                                   lambda rec: prior_result_ok(rec, tps), cfg, seq, d.get("tool_use_id"), toff)
        st["t"] = time.time()
        save_state(sp, st)
    if action == "deny":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": msg}}
    if action == "advise":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}
    return None


if __name__ == "__main__":
    try:
        out = handle(json.load(sys.stdin))
        if out:
            print(json.dumps(out, ensure_ascii=False))
    except Exception:
        pass
    sys.exit(0)
