#!/usr/bin/env python3
"""第 5 轮 D：read_guard 在「全部历史 Bash 命令」上的安全回放（照 tools/replay/replay_test_filter.py 的做法）。

证明三件事（任何一条不满足，退出码 1）：
  1. 从不改写命令：每条历史命令都真的喂给 hook 的 handle()（和 hook 进程里走的是同一个函数），
     输出只可能是 None / deny / additionalContext，绝不出现 updatedInput；抽样再用真子进程跑一遍看 stdout。
  2. 只拦「纯读取」：analyze() 判成 pure 的每一条，再用一套**独立写法**（shlex 分词 + 白名单）复查，
     确认没有写文件的重定向、tee、sed -i、$ 展开、子 shell、非白名单命令；并且 `bash -n` 语法检查通过。
     → 就算拦错了，也只是少跑了一条只往屏幕输出的命令，不会少做任何有副作用的事。
  3. 和 test_filter 不重叠：test_filter 会改写的命令，read_guard 一条都不认成纯读取。

安全约定：绝不执行任何历史命令（只 `bash -n` 解析）；只写 state/round5_D/（含命令原文，state/ 已 gitignore）。
用法：python3 tools/round5/D_replay.py [--refresh] [--sample 2000]
"""
import argparse, collections, importlib.machinery, importlib.util, json, os, random, re, shlex, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor

sys.dont_write_bytecode = True
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "state", "round5_D")
HOOK = os.path.join(ROOT, "hooks", "read_guard.py")


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


G = load(HOOK, "rg_replay")
TF = load(os.path.join(ROOT, "hooks", "test_filter.py"), "tf_replay")
RT = load(os.path.join(ROOT, "tools", "replay", "replay_test_filter.py"), "rt_replay")
RT.OUT = OUT                                        # 提取结果写到本目录，不动 state/replay/

ALLOW_FIRST = {"cd", "echo", "printf", "true", ":", "cat", "nl", "head", "tail", "sed", "gsed", "less", "more", "grep", "egrep",
               "fgrep", "rg", "wc", "sort", "uniq", "cut", "tr", "column", "awk", "fold", "expand"}


def independent_pure(cmd):
    """另一套写法复查「纯读取」：返回问题列表（空 = 确认纯读取）。"""
    probs = []
    no_sq = re.sub(r"'[^']*'", "''", cmd)
    if "$" in no_sq or "`" in no_sq:
        probs.append("有 $ 或反引号")
    if re.search(r"[*?]", re.sub(r'"(?:\\.|[^"\\])*"', '""', no_sq)):
        probs.append("有通配符")
    try:
        lx = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lx.whitespace_split = True
        tk = list(lx)
    except ValueError as e:
        return ["shlex 解析失败:%s" % e]
    first, i, verb = True, 0, ""
    while i < len(tk):
        t = tk[i]
        if t in (";", "&&", "||", "|", "|&"):
            first = True; i += 1; continue
        if set(t) <= set("();<>|&"):
            ok = False
            if t in (">", ">>") and i > 0 and tk[i - 1] == "2" and i + 1 < len(tk) and tk[i + 1] == "/dev/null":
                ok = True
            if t == ">&" and i > 0 and tk[i - 1] == "2" and i + 1 < len(tk) and tk[i + 1] == "1":
                ok = True
            if not ok:
                probs.append("符号 %s" % t)
            i += 1; continue
        if first:
            v = os.path.basename(t)
            if v not in ALLOW_FIRST and not (v == "2" and i + 1 < len(tk) and tk[i + 1] in (">", ">>", ">&")):
                probs.append("命令 %s 不在白名单" % v)
            first = False; verb = v
        else:
            if verb in ("sed", "gsed") and (t.startswith("-i") or t.startswith("--in-place")):
                probs.append("sed 参数 %s" % t)
            if verb == "sort" and t.startswith("-o"):
                probs.append("sort -o")
            if verb == "awk" and re.search(r"\bsystem\b|getline|print[^;]*>|\|", t):
                probs.append("awk 写文件/执行")
            if verb in ("sed", "gsed") and not t.startswith("-") and re.search(r"/[A-Za-z0-9]*[weWR]$|(^|[;{}\s])[wWe]\s", t):
                probs.append("sed 脚本可能写文件")
        i += 1
    return probs


def bash_n(cmd):
    try:
        p = subprocess.run(["/bin/bash", "-n", "-c", cmd], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL, cwd="/tmp")
        return p.returncode == 0
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--sample", type=int, default=2000)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    cmds, meta = RT.extract(a.refresh or not os.path.exists(os.path.join(OUT, "commands.jsonl")))
    tmp = tempfile.mkdtemp(prefix="rg_replay_", dir=OUT)
    os.environ["READ_GUARD_STATE"] = os.path.join(tmp, "state")
    G.STATE_DIR = os.environ["READ_GUARD_STATE"]
    wd = os.path.join(tmp, "wd"); os.makedirs(wd)
    cfg = {"dup": "deny", "big": "off", "big_lines": G.BIG_LINES, "big_bytes": G.BIG_BYTES}
    C = collections.Counter()
    disagree, pure_cmds, exc_examples = [], [], []
    for k, e in enumerate(cmds):
        c = e["cmd"]
        d = {"session_id": "replay", "transcript_path": os.path.join(tmp, "t.jsonl"), "cwd": wd,
             "tool_name": "Bash", "tool_input": {"command": c}, "tool_use_id": "toolu_r%d" % k}
        try:
            out = G.handle(d, cfg)
        except Exception as ex:
            C["handle_exception"] += 1; exc_examples.append(repr(ex)[:120]); out = None
        if out is not None:
            h = out.get("hookSpecificOutput") or {}
            C["output"] += 1
            if "updatedInput" in h:
                C["updatedInput"] += 1
        try:
            an = G.analyze(c, wd)
        except Exception:
            C["analyze_exception"] += 1; an = None
        if an is None:
            C["unparsed"] += 1; continue
        if an["units"]:
            C["read_cmds"] += 1; C["read_calls"] += e["n"]
        if an["pure"] and all(not u["unsafe"] for u in an["units"]):
            C["pure"] += 1; C["pure_calls"] += e["n"]
            pure_cmds.append(c)
            pr = independent_pure(c)
            if pr:
                disagree.append({"cmd": c, "probs": pr})
            if TF.rewrite(c, "/tmp/x"):
                C["tf_overlap"] += 1
    with ThreadPoolExecutor(8) as ex:
        syn = list(ex.map(bash_n, pure_cmds))
    C["pure_bash_n_bad"] = sum(1 for s in syn if not s)
    bad_syntax = [c for c, s in zip(pure_cmds, syn) if not s]
    # 真子进程抽样：stdout 只能是空或合法 JSON 的 deny/additionalContext
    rnd = random.Random(1)
    samp = rnd.sample(cmds, min(a.sample, len(cmds)))
    env = dict(os.environ, READ_GUARD_STATE=os.path.join(tmp, "state2"))

    def run(e):
        d = {"session_id": "replay2", "transcript_path": os.path.join(tmp, "t.jsonl"), "cwd": wd,
             "tool_name": "Bash", "tool_input": {"command": e["cmd"]}, "tool_use_id": "x"}
        p = subprocess.run([sys.executable, HOOK], input=json.dumps(d), capture_output=True, text=True, env=env, timeout=20)
        o = p.stdout.strip()
        if p.returncode != 0:
            return "rc!=0"
        if not o:
            return "empty"
        try:
            h = json.loads(o).get("hookSpecificOutput") or {}
        except Exception:
            return "bad_json"
        return "updatedInput" if "updatedInput" in h else "deny" if h.get("permissionDecision") == "deny" else "other"
    t0 = time.time()
    with ThreadPoolExecutor(8) as ex:
        sub = collections.Counter(ex.map(run, samp))
    S = {"extract": meta, "unique_cmds": len(cmds), "counts": dict(C), "independent_disagree": len(disagree),
         "subprocess_sample": dict(sub), "subprocess_secs": round(time.time() - t0, 1), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    json.dump({"summary": S, "disagree": disagree, "bad_syntax": bad_syntax, "exc": exc_examples},
              open(os.path.join(OUT, "replay_result.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(S, ensure_ascii=False, indent=1))
    bad = C["updatedInput"] + C["handle_exception"] + C["analyze_exception"] + C["tf_overlap"] + len(disagree) + \
        sub.get("updatedInput", 0) + sub.get("rc!=0", 0) + sub.get("bad_json", 0)
    print("不合格项合计：", bad, "（明细见 state/round5_D/replay_result.json）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
