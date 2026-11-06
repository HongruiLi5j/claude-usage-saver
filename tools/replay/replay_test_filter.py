#!/usr/bin/env python3
"""真实命令回放：把历史会话里所有 Bash 命令喂给 test_filter 的 rewrite()，检查改写前后的语法。

安全约定（红线）：
  - 绝不执行任何历史命令。只用 `bash -n -c` / `zsh -n -c`（只解析不执行）和字符串分析。
  - 被测 hook 以只读方式加载（SourceFileLoader，关闭 .pyc 写入），不改它。
  - 只写 state/replay/（含私人命令原文，state/ 已被 .gitignore 排除）。

用法：
  python3 tools/replay/replay_test_filter.py                     # 测 hooks/test_filter.py
  python3 tools/replay/replay_test_filter.py --hook backups/test_filter.py.pre-r4B
  python3 tools/replay/replay_test_filter.py --refresh           # 重新扫 transcripts（默认复用已提取的命令）
输出：state/replay/commands.jsonl（去重命令）、state/replay/result_<label>.json、终端打印汇总。
"""
import argparse, hashlib, importlib.machinery, importlib.util, json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

sys.dont_write_bytecode = True
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "state", "replay")
PROJECTS = os.path.expanduser("~/.claude/projects")
EXCLUDE_PARTS = ("relay-room", ".secret", "sealed", "test2")      # 不读这些路径下的 transcript
SHELLS = [s for s in ("/bin/bash", "/bin/zsh") if os.path.exists(s)]

# ---------- 1. 提取 ----------

def extract(refresh=False):
    path = os.path.join(OUT, "commands.jsonl")
    meta_p = os.path.join(OUT, "extract_meta.json")
    if not refresh and os.path.exists(path) and os.path.exists(meta_p):
        cmds = [json.loads(l) for l in open(path, encoding="utf-8")]
        return cmds, json.load(open(meta_p))
    seen, files, total, skipped = {}, 0, 0, 0
    for dp, dns, fns in os.walk(PROJECTS):
        if any(x in dp for x in EXCLUDE_PARTS):
            dns[:] = []; continue
        for fn in fns:
            if not fn.endswith(".jsonl"):
                continue
            fp = os.path.join(dp, fn)
            if any(x in fp for x in EXCLUDE_PARTS):
                skipped += 1; continue
            files += 1
            sub = "/subagents/" in fp
            try:
                fh = open(fp, encoding="utf-8", errors="replace")
            except OSError:
                continue
            with fh:
                for line in fh:
                    if '"Bash"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    content = (d.get("message") or {}).get("content")
                    if not isinstance(content, list):
                        continue
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash":
                            c = (b.get("input") or {}).get("command")
                            if not isinstance(c, str) or not c:
                                continue
                            total += 1
                            e = seen.get(c)
                            if e is None:
                                seen[c] = e = {"cmd": c, "n": 0, "sub": 0}
                            e["n"] += 1
                            e["sub"] += sub
    os.makedirs(OUT, exist_ok=True)
    cmds = list(seen.values())
    with open(path, "w", encoding="utf-8") as f:
        for e in cmds:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    meta = {"files": files, "skipped_files": skipped, "total_calls": total, "unique": len(cmds),
            "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    json.dump(meta, open(meta_p, "w"), ensure_ascii=False, indent=1)
    return cmds, meta

# ---------- 2. 只读加载被测 hook ----------

def load_hook(path):
    loader = importlib.machinery.SourceFileLoader("tf_under_test_%d" % abs(hash(path)), path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)          # 模块顶层只有定义；__main__ 分支不会跑
    return mod

# ---------- 3. 语法检查（只解析，不执行） ----------

_cache = {}
def _key(sh, cmd):
    return sh + ":" + hashlib.sha1(cmd.encode("utf-8", "surrogatepass")).hexdigest()

def syntax_ok(sh, cmd):
    k = _key(sh, cmd)
    if k in _cache:
        return _cache[k]
    if "\x00" in cmd:
        r = [False, "NUL byte"]
    else:
        try:
            p = subprocess.run([sh, "-n", "-c", cmd], capture_output=True, text=True, timeout=15,
                               stdin=subprocess.DEVNULL, cwd="/tmp" if os.path.isdir("/tmp") else None)
            r = [p.returncode == 0, (p.stderr or "").strip()[:300]]
        except subprocess.TimeoutExpired:
            r = [False, "timeout"]
        except Exception as ex:
            r = [False, "err:%s" % ex]
    _cache[k] = r
    return r

def load_cache():
    p = os.path.join(OUT, "syntax_cache.json")
    if os.path.exists(p):
        try: _cache.update(json.load(open(p)))
        except Exception: pass

def save_cache():
    json.dump(_cache, open(os.path.join(OUT, "syntax_cache.json"), "w"))

# ---------- 4. 字符串分析 ----------

# 范围内（hook 声称要管的）：pytest / py.test / python -m pytest / npm test / npm run test / node --test
INSCOPE = re.compile(r"(?:^|[\s;&|(`{])(?:\S*/)?(?:py\.?test\b|python[0-9.]*\s+(?:-\S+\s+)*-m\s+pytest\b|npm\s+(?:run\s+)?test\b|node\s+(?:\S+\s+)*?--test\b)")
# 范围外的其他测试跑法（只统计，供参考）
OUTSCOPE = re.compile(r"(?:^|[\s;&|(`{])(?:(?:\S*/)?python[0-9.]*\s+(?:-\S+\s+)*-m\s+unittest\b|(?:yarn|pnpm|bun)\s+(?:run\s+)?test\b|npx\s+(?:jest|vitest|mocha)\b|(?:\S*/)?(?:jest|vitest|mocha)\b|cargo\s+test\b|go\s+test\b|make\s+(?:\S+\s+)*test\b|tox\b|(?:uv|poetry|pipenv|hatch)\s+run\s+(?:\S+\s+)*pytest\b|lake\s+test\b)")
NOT_REALLY_TEST_ARGS = ("--version", "--help", "-h", "--collect-only", "--co", "--fixtures", "--markers",
                        "--setup-plan", "--setup-only", "-V")

HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

def strip_heredocs(cmd):
    """去掉 heredoc 正文（保留 << 行本身）。"""
    out, lines, i = [], cmd.split("\n"), 0
    while i < len(lines):
        ln = lines[i]; out.append(ln); i += 1
        for m in HEREDOC.finditer(ln):
            tag = m.group(2)
            while i < len(lines) and lines[i].strip() != tag:
                i += 1
            i += 1
    return "\n".join(out)

def strip_quotes(s):
    return re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", "''", s)

CMD_SPLIT = re.compile(r"\|\||&&|[;\n|&(){}`]|\$\(|\bthen\b|\bdo\b|\belse\b")
SKIP_WORDS = {"timeout", "gtimeout", "env", "sudo", "nice", "nohup", "command", "exec", "time",
              "caffeinate", "!", "if", "while", "until", "elif"}
TEST_AT_START = re.compile(r"^(?:\S*/)?(?:py\.?test\b|python[0-9.]*\s+(?:-\S+\s+)*-m\s+pytest\b|npm\s+(?:run\s+)?test\b|node\s+(?:\S+\s+)*?--test\b|(?:uv|poetry|pipenv|hatch)\s+run\s+(?:\S+\s+)*?(?:py\.?test|python[0-9.]*\s+-m\s+pytest)\b)")

def test_at_cmd_pos(cmd):
    """去掉 heredoc 正文和引号内容后，看是否有某个「命令位置」在跑 pytest/npm test/node --test。
    返回 (命中的段, 前面挂的包装器或 None)。"""
    s = strip_quotes(strip_heredocs(cmd))
    for seg in CMD_SPLIT.split(s):
        w = seg.split()
        wrapper = None
        while w and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0]) or w[0] in SKIP_WORDS
                     or (wrapper in ("timeout", "gtimeout") and re.fullmatch(r"[0-9.]+[smh]?|-\S+", w[0]))):
            if w[0] in SKIP_WORDS:
                wrapper = w[0]
            w = w[1:]
        t = " ".join(w)
        if t and TEST_AT_START.match(t):
            if re.match(r"(?:uv|poetry|pipenv|hatch)\b", t):
                wrapper = t.split()[0] + " run"
            return t, wrapper
    return None, None

def first_blocker(cmd):
    """仿照 split_segments：第一个让它放弃的未加引号字符。"""
    i, q, n = 0, None, len(cmd)
    while i < n:
        c = cmd[i]
        if q:
            if c == "\\" and q == '"': i += 2; continue
            if c == q: q = None
            i += 1; continue
        if c in "'\"": q = c; i += 1; continue
        if c == "\\": i += 2; continue
        if c == "#" and (i == 0 or cmd[i-1].isspace()):
            while i < n and cmd[i] != "\n": i += 1
            continue
        two = cmd[i:i+2]
        if two in ("&&", "||"): i += 2; continue
        if c == "|": return "pipe", cmd[i+1:]
        if c in "<>": return "redirect", None
        if c == "&": return "background", None
        if c == "`" or two == "$(": return "subst", None
        if c in "()": return "subshell", None
        i += 1
    return ("unclosed_quote" if q else None), None

def miss_reason(mod, cmd, wrapper):
    if "USAGE_SAVER_RAW=1" in cmd:
        return "RAW=1 显式放行"
    SELF = r"\|\s*(tail|head|grep|rg|sed|awk|cut|wc)\b"
    if "<<" in cmd:
        body = strip_heredocs(cmd)
        lines = [l for l in body.split("\n") if TEST_AT_START.search(strip_quotes(l)) or INSCOPE.search(strip_quotes(l))]
        if lines and all(re.search(SELF, l) for l in lines):
            return "heredoc 放行；测试行本身也有 | tail 等（已自带过滤）"
        if lines and all(re.search(r"[|<>&]", strip_quotes(l).replace("&&", "")) for l in lines):
            return "heredoc 放行；测试行本身也有管道/重定向"
        return "heredoc 放行；测试行是裸跑（若无 heredoc 就会被包装 = 真漏网）"
    segs = mod.split_segments(cmd)
    if segs is None:
        b, rest = first_blocker(cmd)
        if b == "pipe":
            if rest is not None and re.match(r"\s*(tail|head|grep|rg|sed|awk|cut|wc)\b", rest):
                return "| tail/head/grep（已自带过滤）放行"
            return "管道到其他命令 放行"
        if b == "redirect":
            if re.search(r"2>&1\s*" + SELF, cmd):
                return "2>&1 | tail/head/grep（已自带过滤）放行"
            return "重定向(>file 等) 放行"
        if b == "background": return "后台 & 放行"
        if b == "subst": return "含 $(...)/反引号 放行"
        if b == "subshell": return "含括号/子shell/函数定义 放行"
        if b == "unclosed_quote": return "引号未闭合 放行"
        return "含 bash 保留字结构(if/for/{...) 放行"
    if wrapper:
        return "测试前有包装器(%s) 未识别" % wrapper
    for s, _ in segs:
        w = s.split()
        while w and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0]): w = w[1:]
        if hasattr(mod, "INTERACTIVE") and any(a.startswith(mod.INTERACTIVE) for a in w[1:]) and TEST_AT_START.match(" ".join(w)):
            return "交互参数(--watch/--pdb) 放行"
    return "其他未识别"

def misfire_reason(mod, cmd, new):
    """改写了，但看着不像在跑测试。"""
    segs = mod.split_segments(cmd) or []
    reasons = []
    for s, _ in segs:
        if s and mod.is_test(s):
            import shlex
            try: w = shlex.split(s)
            except ValueError: w = []
            bad = [a for a in w[1:] if a in NOT_REALLY_TEST_ARGS]
            if bad:
                reasons.append("测试段只是 %s（不跑测试）" % "/".join(bad))
            if not INSCOPE.search(s):
                reasons.append("段不匹配常规测试写法")
    return "; ".join(reasons)

# ---------- 5. 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hook", default=os.path.join(ROOT, "hooks", "test_filter.py"))
    ap.add_argument("--label", default=None)
    ap.add_argument("--refresh", action="store_true", help="重新扫描 transcripts")
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args()
    hook = os.path.abspath(a.hook)
    label = a.label or os.path.basename(hook).replace("test_filter.py", "").strip(".") or "current"
    hook_sha = hashlib.sha1(open(hook, "rb").read()).hexdigest()[:12]

    cmds, meta = extract(a.refresh)
    mod = load_hook(hook)
    load_cache()
    logdir = "/Users/x/Documents/Projects/claude-usage-saver/state/testlogs"   # 只影响字符串，不建目录

    rows = []
    for e in cmds:
        c = e["cmd"]
        try:
            new = mod.rewrite(c, logdir) if "logdir" in mod.rewrite.__code__.co_varnames else mod.rewrite(c)
            exc = None
        except Exception as ex:
            new, exc = None, repr(ex)[:200]
        rows.append({"cmd": c, "n": e["n"], "sub": e["sub"], "new": new, "exc": exc,
                     "inscope": bool(INSCOPE.search(c)), "outscope": bool(OUTSCOPE.search(c))})

    jobs = []
    for r in rows:
        for sh in SHELLS:
            jobs.append((sh, r["cmd"]))
            if r["new"]:
                jobs.append((sh, r["new"]))
    jobs = [j for j in jobs if _key(*j) not in _cache]
    with ThreadPoolExecutor(a.jobs) as ex:
        list(ex.map(lambda j: syntax_ok(*j), jobs))
    save_cache()

    cats = {k: [] for k in ("orig_ok_new_ok", "orig_ok_new_bad", "orig_bad_new_ok", "orig_bad_new_bad",
                             "rewrite_exception", "misfire", "miss_inscope", "mention_only", "outscope_untouched")}
    per_shell = {}
    for r in rows:
        r["orig_syntax"] = {os.path.basename(sh): syntax_ok(sh, r["cmd"]) for sh in SHELLS}
        if r["exc"]:
            cats["rewrite_exception"].append(r)
        if r["new"]:
            r["new_syntax"] = {os.path.basename(sh): syntax_ok(sh, r["new"]) for sh in SHELLS}
            for sh in SHELLS:
                b = os.path.basename(sh)
                o, n = r["orig_syntax"][b][0], r["new_syntax"][b][0]
                k = ("orig_ok" if o else "orig_bad") + "_" + ("new_ok" if n else "new_bad")
                per_shell.setdefault(b, {}).setdefault(k, 0)
                per_shell[b][k] += 1
            ob = r["orig_syntax"]["bash"][0]; nb = r["new_syntax"]["bash"][0]
            # 以 bash 为主分类；zsh 结果附在条目里
            cats[("orig_ok" if ob else "orig_bad") + "_" + ("new_ok" if nb else "new_bad")].append(r)
            mr = misfire_reason(mod, r["cmd"], r["new"])
            if mr:
                r["misfire"] = mr; cats["misfire"].append(r)
        else:
            hit, wrapper = test_at_cmd_pos(r["cmd"])
            if hit:
                r["miss"] = miss_reason(mod, r["cmd"], wrapper); cats["miss_inscope"].append(r)
            elif r["inscope"]:
                cats["mention_only"].append(r)
            elif r["outscope"]:
                cats["outscope_untouched"].append(r)
    # zsh 专属：bash 认为 OK 但 zsh 认为坏
    zsh_only = [r for r in rows if r.get("new") and "zsh" in r.get("new_syntax", {})
                and r["orig_syntax"]["zsh"][0] and not r["new_syntax"]["zsh"][0]]

    miss_hist = {}
    for r in cats["miss_inscope"]:
        miss_hist[r["miss"]] = miss_hist.get(r["miss"], 0) + 1
    orig_bad_all = sum(1 for r in rows if not r["orig_syntax"]["bash"][0])
    summary = {
        "hook": hook, "hook_sha1": hook_sha, "label": label, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "extract": meta, "shells": SHELLS,
        "unique_cmds": len(rows), "total_calls": meta["total_calls"],
        "orig_bash_syntax_bad_all": orig_bad_all,
        "rewritten_unique": sum(1 for r in rows if r["new"]),
        "rewritten_calls": sum(r["n"] for r in rows if r["new"]),
        "inscope_unique": sum(1 for r in rows if r["inscope"]),
        "counts": {k: len(v) for k, v in cats.items()},
        "zsh_new_bad_while_orig_ok": len(zsh_only),
        "per_shell": per_shell,
        "miss_reasons": dict(sorted(miss_hist.items(), key=lambda x: -x[1])),
    }
    out = {"summary": summary,
           "examples": {k: [{kk: r.get(kk) for kk in ("cmd", "n", "new", "orig_syntax", "new_syntax",
                                                       "misfire", "miss", "exc")} for r in v]
                        for k, v in cats.items()},
           "zsh_only_bad": [{"cmd": r["cmd"], "new": r["new"], "new_syntax": r["new_syntax"]} for r in zsh_only]}
    rp = os.path.join(OUT, "result_%s.json" % label)
    json.dump(out, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("结果已写入", rp)
    bad = summary["counts"]["orig_ok_new_bad"] + summary["counts"]["rewrite_exception"]
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
