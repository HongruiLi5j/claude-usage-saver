#!/usr/bin/env python3
"""第 5 轮 D：hooks/read_guard.py 的回归用例。

用法：
  python3 tools/round5/D_tests.py            # 全部用例
  python3 tools/round5/D_tests.py -k 重复     # 只跑描述里含「重复」的
隔离：每个用例一个临时目录（tempfile；不可用时用 scratch 环境变量 D_TEST_TMP）。hook 以子进程真跑
（和 Claude Code 调它的方式一样：JSON 进 stdin，看 stdout 和退出码），状态目录用 READ_GUARD_STATE 指到临时目录，
配置用 READ_GUARD_CONFIG 指到临时文件；不碰真实 state/、config.json、~/.claude。
"""
import argparse, contextlib, importlib.machinery, importlib.util, json, os, random, shutil, subprocess, sys, tempfile, time

sys.dont_write_bytecode = True
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HOOK = os.path.join(ROOT, "hooks", "read_guard.py")
TF_HOOK = os.path.join(ROOT, "hooks", "test_filter.py")


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


G = load(HOOK, "rg_t")
TF = load(TF_HOOK, "tf_t")


class Env:
    """一个假会话：工作目录、transcript、状态目录、配置。"""
    def __init__(self, tmp, cfg=None):
        self.tmp = tmp
        self.wd = os.path.join(tmp, "wd"); os.makedirs(self.wd)
        self.proj = os.path.join(tmp, "proj"); os.makedirs(self.proj)
        self.sid = "sess-1"
        self.tp = os.path.join(self.proj, self.sid + ".jsonl")
        open(self.tp, "w").close()
        self.state = os.path.join(tmp, "state")
        self.cfgp = os.path.join(tmp, "config.json")
        json.dump({"read_guard": cfg or {}}, open(self.cfgp, "w"))
        self.n = 0
        self.env = dict(os.environ, READ_GUARD_STATE=self.state, READ_GUARD_CONFIG=self.cfgp)
        self.env.pop("READ_GUARD", None)

    def write(self, name, text):
        p = os.path.join(self.wd, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def sub_tp(self, aid):
        p = os.path.join(self.proj, self.sid, "subagents", "agent-%s.jsonl" % aid)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if not os.path.exists(p):
            open(p, "w").close()
        return p

    def hook(self, tool, tin, agent=None, tuid=None, raw_stdin=None, env=None, timeout=10):
        """跑一次 hook；返回 (退出码, stdout 解析后的 dict 或 None, stdout 原文, tuid)。"""
        self.n += 1
        tuid = tuid or "toolu_%04d" % self.n
        d = {"session_id": self.sid, "transcript_path": self.tp, "cwd": self.wd, "hook_event_name": "PreToolUse",
             "tool_name": tool, "tool_input": tin, "tool_use_id": tuid}
        if agent:
            d["agent_id"] = agent; d["agent_type"] = "general-purpose"
        p = subprocess.run([sys.executable, HOOK], input=raw_stdin if raw_stdin is not None else json.dumps(d),
                           capture_output=True, text=True, env=env or self.env, timeout=timeout)
        out = p.stdout.strip()
        try:
            js = json.loads(out) if out else None
        except Exception:
            js = "BAD_JSON"
        return p.returncode, js, out, tuid

    def result(self, tuid, text, err=False, agent=None, tool="Bash", tin=None):
        """把 tool_use + tool_result 写进 transcript（模拟 Claude Code 执行完这条命令）。"""
        tp = self.sub_tp(agent) if agent else self.tp
        with open(tp, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tuid, "name": tool, "input": tin or {}}]}},
                               separators=(",", ":")) + "\n")
            b = {"type": "tool_result", "tool_use_id": tuid, "content": text}
            if err:
                b["is_error"] = True
            f.write(json.dumps({"type": "user", "message": {"content": [b]}}, separators=(",", ":")) + "\n")

    def compact(self, agent=None):
        tp = self.sub_tp(agent) if agent else self.tp
        with open(tp, "a") as f:
            f.write(json.dumps({"type": "system", "subtype": "compact_boundary"}) + "\n")

    def bash(self, cmd, agent=None, result=None, err=False):
        """跑 hook；没被拦就把结果写进 transcript。返回 hook 输出的 dict 或 None。"""
        rc, js, out, tuid = self.hook("Bash", {"command": cmd}, agent=agent)
        assert rc == 0, rc
        if not is_deny(js):
            self.result(tuid, result if result is not None else "content\n", err=err, agent=agent, tin={"command": cmd})
        return js


def is_deny(js):
    return isinstance(js, dict) and (js.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


def has_updated(js):
    return isinstance(js, dict) and "updatedInput" in (js.get("hookSpecificOutput") or {})


CASES = []


def case(desc, cfg=None):
    def deco(fn):
        CASES.append((desc, cfg, fn)); return fn
    return deco

# ------------------------------------------------------------------ 识别

@case("识别：cat / sed -n / head / tail / nl 的读法和行段")
def _(E):
    a = lambda c: [(os.path.basename(u["path"]), tuple(u["spec"]), u["num"]) for u in G.analyze(c, "/w")["units"]]
    return (a("cat x.py") == [("x.py", ("full",), False)] and a("cat -n x.py") == [("x.py", ("full",), True)]
            and a("nl x.py") == [("x.py", ("full",), True)] and a("sed -n '10,50p' x") == [("x", ("lines", 10, 50), False)]
            and a("sed -n 7p x") == [("x", ("lines", 7, 7), False)] and a("sed -n '3,$p' x") == [("x", ("lines", 3, None), False)]
            and a("head -n 40 x") == [("x", ("lines", 1, 40), False)] and a("head -20 x") == [("x", ("lines", 1, 20), False)]
            and a("head x") == [("x", ("lines", 1, 10), False)] and a("tail -n +30 x") == [("x", ("lines", 30, None), False)]
            and a("tail -5 x") == [("x", ("tail", 5), False)] and a("cat a b") == [("a", ("full",), False), ("b", ("full",), False)])


@case("识别：cd 之后的相对路径、~ 展开")
def _(E):
    u = G.analyze("cd /p/q && cat r/s.md", "/w")["units"]
    u2 = G.analyze("cd ~/x; sed -n 1,5p y", "/w")["units"]
    return u[0]["path"] == "/p/q/r/s.md" and u2[0]["path"] == os.path.expanduser("~/x/y")


@case("识别：有副作用或结果会变的命令一律不算「纯读取」")
def _(E):
    impure = ["cat a > b", "cat a >> b", "cat a | tee b", "cat $F", "cat \"$F\"", "cat *.py", "cat a; ls", "cat a & ",
              "sed -n 1,5p a | sort -o out", "cat a | awk '{print > \"o\"}'", "cat a | sh", "cat a | python3",
              "cat a && rm b", "cat a; python3 x.py", "cat a | xargs rm", "sed -n '1w out' a", "cat a 1>/dev/null",
              "cat -A a", "FOO=$BAR cat a", "cat a | sed -i s/x/y/ b", "cat a | sed 'w out'", "cat {a,b}"]
    none = ["cat <<EOF\nx\nEOF", "(cat a)", "cat `ls`", "cat $(ls)", "cat 'unclosed", "cat <(ls)", "cat a <<< x"]
    bad = [c for c in impure if (G.analyze(c, "/w") or {}).get("pure")]
    bad += [c for c in none if G.analyze(c, "/w") is not None and G.analyze(c, "/w")["pure"]]
    if bad:
        print("    误判为纯读取：", bad)
    return not bad


@case("识别：纯读取的常见写法")
def _(E):
    ok = ["cat a", "cat a 2>/dev/null", "cat a 2>&1 | head -50", "echo ---; cat a; echo ---; cat b", "cd /x && sed -n 1,80p a",
          "head -n 30 a | grep -n foo", "cat -n a | sed -n 10,20p", "tail -n 40 log.txt", "nl -ba a | head"]
    bad = [c for c in ok if not (G.analyze(c, "/w") or {}).get("pure")]
    if bad:
        print("    没认出：", bad)
    return not bad


@case("覆盖关系：带行号的整读覆盖段读；不带行号的只覆盖完全相同的读法")
def _(E):
    c = G.covers
    return (c(["full"], ["full"]) and c(["full"], ["lines", 5, 9], True, False) and not c(["full"], ["lines", 5, 9], False, False)
            and not c(["full"], ["full"], False, True) and c(["lines", 1, 100], ["lines", 10, 20], True, False)
            and not c(["lines", 1, 100], ["lines", 90, 120], True, False) and c(["lines", 10, 50], ["lines", 10, 50])
            and not c(["lines", 10, 50], ["lines", 11, 50]) and not c(["cmd", "x"], ["cmd", "y"]) and c(["cmd", "x"], ["cmd", "x"]))

# ------------------------------------------------------------------ 端到端：重复读

@case("重复：第一次放行；内容没变的第二次被拦（deny，不改命令）")
def _(E):
    E.write("a.py", "print(1)\n")
    j1 = E.bash("cat a.py")
    j2 = E.bash("cat a.py")
    return j1 is None and is_deny(j2) and not has_updated(j2) and "read_guard" in j2["hookSpecificOutput"]["permissionDecisionReason"]


@case("重复：被拦后原样再发一次 → 放行（deny-once）")
def _(E):
    E.write("a.py", "x\n")
    E.bash("cat a.py"); j2 = E.bash("cat a.py"); j3 = E.bash("cat a.py")
    return is_deny(j2) and j3 is None


@case("重复：文件改过（内容变了）→ 放行")
def _(E):
    p = E.write("a.py", "x\n")
    E.bash("cat a.py")
    with open(p, "a") as f:
        f.write("y\n")
    return E.bash("cat a.py") is None


@case("重复：只改 mtime（内容不变）→ 指纹含 mtime，保守放行；不出错、不改命令")
def _(E):
    p = E.write("a.py", "x\n")
    E.bash("cat a.py")
    os.utime(p, (time.time() + 100, time.time() + 100))
    # mtime 变了 → 指纹不同（保守：放行）；这里只要求不出错且不改命令
    j = E.bash("cat a.py")
    return j is None or (is_deny(j) and not has_updated(j))


@case("重复：中间压缩过（compact_boundary）→ 放行")
def _(E):
    E.write("a.py", "x\n")
    E.bash("cat a.py"); E.compact()
    return E.bash("cat a.py") is None


@case("重复：正文里出现 compact_boundary 字样不算压缩")
def _(E):
    E.write("a.py", "x\n")
    E.bash("cat a.py")
    E.result("toolu_other", "这段文字里提到 compact_boundary 但不是压缩记录")
    return is_deny(E.bash("cat a.py"))


@case("重复：上次结果是错误 → 放行")
def _(E):
    E.write("a.py", "x\n")
    E.bash("cat a.py", err=True)
    return E.bash("cat a.py") is None


@case("重复：上次输出太大被落盘（persisted-output）→ 放行")
def _(E):
    E.write("a.py", "x\n")
    E.bash("cat a.py", result="<persisted-output>\nOutput too large (40KB). Full output saved to: /x\n</persisted-output>")
    return E.bash("cat a.py") is None


@case("重复：上次的 tool_result 还没写进 transcript（并行调用）→ 放行")
def _(E):
    E.write("a.py", "x\n")
    rc1, j1, _, t1 = E.hook("Bash", {"command": "cat a.py"})
    rc2, j2, _, t2 = E.hook("Bash", {"command": "cat a.py"})
    return rc1 == rc2 == 0 and j1 is None and j2 is None


@case("重复：transcript 不存在 → 放行")
def _(E):
    E.write("a.py", "x\n")
    E.bash("cat a.py")
    os.rename(E.tp, E.tp + ".gone")
    return E.bash("cat a.py") is None


@case("重复：文件超过 28000 字节（上次可能被截断）→ 放行")
def _(E):
    E.write("big.txt", "y" * 29000 + "\n")
    E.bash("cat big.txt")
    return E.bash("cat big.txt") is None


@case("重复：行段在大文件里也可以拦（只看这段的字节数）")
def _(E):
    E.write("big.txt", ("line\n" * 20000))
    E.bash("sed -n '10,20p' big.txt")
    return is_deny(E.bash("sed -n '10,20p' big.txt"))


@case("重复：不带行号整读之后读其中一段 → 放行（模型没法按行号在旧输出里找）")
def _(E):
    E.write("a.py", "".join("l%d\n" % i for i in range(300)))
    E.bash("cat a.py")
    return E.bash("sed -n '100,120p' a.py") is None


@case("重复：Read（带行号）之后 Bash 读其中一段 → 拦")
def _(E):
    E.write("a.py", "".join("l%d\n" % i for i in range(300)))
    rc, js, _, t = E.hook("Read", {"file_path": os.path.join(E.wd, "a.py")})
    E.result(t, "     1\tl0\n...", tool="Read")
    return js is None and is_deny(E.bash("sed -n '100,120p' a.py"))


@case("重复：Read 超过 2000 行只记前 2000 行；读 2500 行之后的段 → 放行")
def _(E):
    E.write("a.py", "".join("l%d\n" % i for i in range(3000)))
    rc, js, _, t = E.hook("Read", {"file_path": os.path.join(E.wd, "a.py")})
    E.result(t, "     1\tl0\n...", tool="Read")
    return E.bash("sed -n '2500,2510p' a.py") is None and is_deny(E.bash("sed -n '10,20p' a.py"))


@case("重复：cat 之后再 cat -n（要行号）→ 放行")
def _(E):
    E.write("a.py", "x\n")
    E.bash("cat a.py")
    return E.bash("cat -n a.py") is None


@case("Read 工具永远不拦（Read→Read 交给 Claude Code 自己；Bash→Read 可能是为了 Edit）")
def _(E):
    p = E.write("a.py", "x\n")
    E.bash("cat a.py")
    outs = []
    for _ in range(3):
        rc, js, _, t = E.hook("Read", {"file_path": p}); outs.append(js); E.result(t, "     1\tx", tool="Read")
    return outs == [None, None, None]


@case("重复：不同 agent 之间不共享（子 agent 看不到父会话的输出）")
def _(E):
    E.write("a.py", "x\n")
    E.bash("cat a.py")
    j_sub = E.bash("cat a.py", agent="a111")
    j_sub2 = E.bash("cat a.py", agent="a111")
    j_other = E.bash("cat a.py", agent="a222")
    return j_sub is None and is_deny(j_sub2) and j_other is None


@case("重复：子 agent 自己的 transcript 里压缩过 → 放行")
def _(E):
    E.write("a.py", "x\n")
    E.bash("cat a.py", agent="a333"); E.compact(agent="a333")
    return E.bash("cat a.py", agent="a333") is None


@case("重复：USAGE_SAVER_RAW=1 → 放行")
def _(E):
    E.write("a.py", "x\n")
    E.bash("cat a.py")
    return E.bash("USAGE_SAVER_RAW=1 cat a.py") is None


@case("重复：命令里还有别的（cat a; ls / cat a && python3 x.py）→ 放行")
def _(E):
    E.write("a.py", "x\n")
    E.bash("cat a.py")
    return E.bash("cat a.py; ls") is None and E.bash("cat a.py && python3 -c 1") is None


@case("重复：多文件命令，有一个没读过 → 放行；都读过 → 拦")
def _(E):
    E.write("a.py", "x\n"); E.write("b.py", "y\n")
    E.bash("cat a.py")
    j1 = E.bash("cat a.py b.py")
    j2 = E.bash("echo ---; cat a.py; echo ---; cat b.py")
    return j1 is None and is_deny(j2)


@case("重复：管道过滤只在命令完全相同时拦")
def _(E):
    E.write("a.py", "foo\nbar\n")
    E.bash("cat a.py | grep -n foo")
    return E.bash("cat a.py | grep -n bar") is None and is_deny(E.bash("cat a.py | grep -n foo"))


@case("开关：config dup=off → 什么都不做", cfg={"dup": "off"})
def _(E):
    E.write("a.py", "x\n")
    E.bash("cat a.py")
    return E.bash("cat a.py") is None


@case("开关：环境变量 READ_GUARD=off → 什么都不做")
def _(E):
    E.write("a.py", "x\n")
    E.env["READ_GUARD"] = "off"
    E.bash("cat a.py")
    return E.bash("cat a.py") is None


@case("开关：dup=advise → 只注入 additionalContext，不拦", cfg={"dup": "advise"})
def _(E):
    E.write("a.py", "x\n")
    E.bash("cat a.py")
    js = E.bash("cat a.py")
    h = (js or {}).get("hookSpecificOutput") or {}
    return h.get("additionalContext") and "permissionDecision" not in h and "updatedInput" not in h


@case("大文件规则：默认关闭（整读 50KB 文件放行）")
def _(E):
    E.write("big.md", "z" * 50000)
    return E.bash("cat big.md") is None


@case("大文件规则：big=deny 时拦第一次、原样再发放行；小文件不拦", cfg={"big": "deny", "big_bytes": 40000})
def _(E):
    E.write("big.md", "z" * 50000); E.write("s.md", "small")
    j1 = E.bash("cat big.md"); j2 = E.bash("cat big.md"); j3 = E.bash("cat s.md")
    return is_deny(j1) and "grep -n" in j1["hookSpecificOutput"]["permissionDecisionReason"] and j2 is None and j3 is None

# ------------------------------------------------------------------ fail-open

@case("fail-open：坏输入（空、非 JSON、数组、缺字段、tool_input 不是对象）→ 退出码 0、无输出")
def _(E):
    bad = ["", "not json", "[]", "null", "{}", json.dumps({"tool_name": "Bash"}), json.dumps({"session_id": "s", "tool_name": "Bash", "tool_input": "cat a"}),
           json.dumps({"session_id": "s", "tool_name": "Bash", "tool_input": {"command": 123}}), json.dumps({"session_id": "s", "tool_name": "Read", "tool_input": {"file_path": None}})]
    for b in bad:
        rc, js, out, _ = E.hook("Bash", {}, raw_stdin=b)
        if rc != 0 or out:
            print("    坏输入", repr(b)[:40], rc, out[:80]); return False
    return True


@case("fail-open：状态文件损坏 → 当成新会话，不出错")
def _(E):
    E.write("a.py", "x\n")
    os.makedirs(E.state, exist_ok=True)
    with open(os.path.join(E.state, E.sid + ".json"), "w") as f:
        f.write("{broken")
    return E.bash("cat a.py") is None and is_deny(E.bash("cat a.py"))


@case("fail-open：状态目录不可写 → 放行")
def _(E):
    E.write("a.py", "x\n")
    ro = os.path.join(E.tmp, "ro"); os.makedirs(ro); os.chmod(ro, 0o500)
    env = dict(E.env, READ_GUARD_STATE=os.path.join(ro, "st"))
    try:
        r = [E.hook("Bash", {"command": "cat a.py"}, env=env) for _ in range(2)]
    finally:
        os.chmod(ro, 0o700)
    return all(x[0] == 0 and x[2] == "" for x in r)


@case("fail-open：锁被别的进程占着 → 0.3 秒左右放弃、放行")
def _(E):
    import fcntl
    E.write("a.py", "x\n")
    E.bash("cat a.py")
    os.makedirs(E.state, exist_ok=True)
    fh = open(os.path.join(E.state, E.sid + ".json.lock"), "a")
    fcntl.flock(fh, fcntl.LOCK_EX)
    try:
        t = time.time(); rc, js, out, _ = E.hook("Bash", {"command": "cat a.py"}); dt = time.time() - t
    finally:
        fh.close()
    return rc == 0 and out == "" and dt < 2.0


@case("fail-open：文件不存在 / 是目录 / 没权限读 → 放行")
def _(E):
    os.makedirs(os.path.join(E.wd, "d"))
    p = E.write("noperm.txt", "x"); os.chmod(p, 0)
    try:
        r = [E.bash(c) for c in ("cat nope.txt", "cat nope.txt", "cat d", "cat d", "cat noperm.txt", "cat noperm.txt")]
    finally:
        os.chmod(p, 0o600)
    return r == [None] * 6

# ------------------------------------------------------------------ 不改坏命令 / 和 test_filter 共存

@case("从不输出 updatedInput：随机拼 3000 条命令（读/写/测试/管道混合），输出只有 deny 或空")
def _(E):
    rnd = random.Random(5)
    for n in ("a.py", "b.md", "c.txt"):
        E.write(n, "".join("l%d\n" % i for i in range(rnd.randint(1, 400))))
    parts = ["cat a.py", "cat b.md", "sed -n '1,20p' a.py", "head -n 5 c.txt", "tail -3 b.md", "ls", "echo hi", "pytest -q",
             "python3 -m pytest tests", "cat a.py | grep -n l1", "cat a.py > /dev/null", "cd .", "npm test", "cat -n c.txt", "wc -l a.py"]
    seps = ["; ", " && ", " || ", "\n", " | "]
    for i in range(3000):
        k = rnd.randint(1, 3)
        cmd = parts[rnd.randrange(len(parts))]
        for _ in range(k - 1):
            cmd += seps[rnd.randrange(len(seps))] + parts[rnd.randrange(len(parts))]
        a = G.analyze(cmd, E.wd)
        st = G.load_state(os.path.join(E.state, "x.json"))
        agent = st["agents"].setdefault("main", {})
        fps = {u["path"]: G.fingerprint(u["path"])[0] and (G.fingerprint(u["path"])[0], 10) or (None, None) for u in (a or {}).get("units", [])}
        act, msg, info = G.decide(agent, "Bash", cmd, E.wd, (a or {}).get("units", []), (a or {}).get("pure", False), False,
                                  fps, lambda rec: True, {"dup": "deny", "big": "deny", "big_bytes": 100}, i, "t%d" % i, {})
        if act not in ("allow", "deny"):
            return False
        if act == "deny" and not a["pure"]:
            print("    拦了非纯读取命令：", cmd); return False
    # 再用真进程跑 200 条，检查 stdout
    for i in range(200):
        cmd = parts[rnd.randrange(len(parts))] + seps[rnd.randrange(len(seps))] + parts[rnd.randrange(len(parts))]
        js = E.bash(cmd)
        if has_updated(js) or (js is not None and not is_deny(js)):
            print("    异常输出：", js); return False
    return True


@case("和 test_filter 共存：测试命令 read_guard 不出声；读文件命令 test_filter 不改写；没有命令两边都动")
def _(E):
    E.write("a.py", "x\n")
    cmds = ["pytest -q", "python3 -m pytest -x tests", "npm test", "cat a.py", "cat a.py; pytest -q", "sed -n 1,5p a.py && npm test",
            "cat a.py", "cat a.py && pytest"]
    both = 0
    for c in cmds:
        tf = TF.rewrite(c, "/tmp/x")
        js = E.bash(c)
        if tf and js:
            both += 1; print("    两边都动：", c)
        if TF.rewrite(c, "/tmp/x") and G.analyze(c, E.wd) and G.analyze(c, E.wd)["pure"]:
            both += 1; print("    test_filter 会改写的命令被 read_guard 认成纯读取：", c)
    return both == 0


@case("性能：单次调用 < 300ms（中位数）")
def _(E):
    E.write("a.py", "x\n")
    ts = []
    for _ in range(7):
        t = time.time(); E.hook("Bash", {"command": "ls -la"}); ts.append(time.time() - t)
    ts.sort()
    return ts[3] < 0.3


# ------------------------------------------------------------------ 安装脚本（只在临时副本上跑）

INSTALL = os.path.join(ROOT, "tools", "round5", "D_install.py")
BASE_SETTINGS = {"model": "opus", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command",
                 "command": "python3 " + TF_HOOK}]}], "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 x.py"}]}]},
                 "permissions": {"allow": ["Bash(ls:*)"]}}


def inst(E, *args, settings=None):
    sp = settings or os.path.join(E.tmp, "settings.json")
    p = subprocess.run([sys.executable, INSTALL, "--settings", sp, "--backup-dir", os.path.join(E.tmp, "bk")] + list(args),
                       capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout + p.stderr


def sha(p):
    import hashlib
    return hashlib.sha1(open(p, "rb").read()).hexdigest()


@case("安装：--dry-run 和不带参数都不写文件、不备份")
def _(E):
    sp = os.path.join(E.tmp, "settings.json"); json.dump(BASE_SETTINGS, open(sp, "w"))
    h0 = sha(sp)
    r1 = inst(E, "--dry-run"); r2 = inst(E)
    return r1[0] == 0 and r2[0] == 0 and "dry-run" in r1[1] and sha(sp) == h0 and not os.path.exists(os.path.join(E.tmp, "bk"))


@case("安装：--apply 追加独立组、test_filter 组和其它键不变；再 --apply 幂等")
def _(E):
    sp = os.path.join(E.tmp, "settings.json"); json.dump(BASE_SETTINGS, open(sp, "w"))
    r = inst(E, "--apply")
    d = json.load(open(sp))
    pre = d["hooks"]["PreToolUse"]
    ok = (r[0] == 0 and len(pre) == 2 and pre[0] == BASE_SETTINGS["hooks"]["PreToolUse"][0] and pre[1]["matcher"] == "Bash|Read"
          and "read_guard.py" in pre[1]["hooks"][0]["command"] and d["permissions"] == BASE_SETTINGS["permissions"]
          and d["hooks"]["UserPromptSubmit"] == BASE_SETTINGS["hooks"]["UserPromptSubmit"] and d["model"] == "opus"
          and "撤回" in r[1] and len(os.listdir(os.path.join(E.tmp, "bk"))) == 1)
    h1 = sha(sp); r2 = inst(E, "--apply")
    return ok and r2[0] == 0 and "已经装过" in r2[1] and sha(sp) == h1


@case("安装：--undo 回到原样（JSON 相等）；再 --undo 无操作；备份可直接 cp 回去")
def _(E):
    sp = os.path.join(E.tmp, "settings.json"); json.dump(BASE_SETTINGS, open(sp, "w"))
    inst(E, "--apply")
    bk = [os.path.join(E.tmp, "bk", x) for x in os.listdir(os.path.join(E.tmp, "bk"))][0]
    r = inst(E, "--undo")
    back = json.load(open(sp)) == BASE_SETTINGS
    r2 = inst(E, "--undo")
    return r[0] == 0 and back and r2[0] == 0 and "没装过" in r2[1] and json.load(open(bk)) == BASE_SETTINGS


@case("安装：--undo 只删我们的条目，同组别人的 hook 保留；别处同名 read_guard.py 不算我们的")
def _(E):
    sp = os.path.join(E.tmp, "settings.json")
    s = json.loads(json.dumps(BASE_SETTINGS))
    s["hooks"]["PreToolUse"].append({"matcher": "Bash|Read", "hooks": [{"type": "command", "command": "python3 " + HOOK},
                                                                        {"type": "command", "command": "python3 /other/read_guard.py"}]})
    json.dump(s, open(sp, "w"))
    r = inst(E, "--undo")
    d = json.load(open(sp))
    g = d["hooks"]["PreToolUse"]
    return r[0] == 0 and len(g) == 2 and g[1]["hooks"] == [{"type": "command", "command": "python3 /other/read_guard.py"}]


@case("安装：settings 不是合法 JSON / hooks 结构不对 → 退出码 1、不写")
def _(E):
    sp = os.path.join(E.tmp, "settings.json")
    open(sp, "w").write("{bad"); h0 = sha(sp)
    r1 = inst(E, "--apply")
    json.dump({"hooks": []}, open(sp, "w")); h1 = sha(sp)
    r2 = inst(E, "--apply")
    json.dump({"hooks": {"PreToolUse": {}}}, open(sp, "w")); h2 = sha(sp)
    r3 = inst(E, "--apply")
    return r1[0] == r2[0] == r3[0] == 1 and sha(sp) == h2 and not os.path.exists(os.path.join(E.tmp, "bk"))


@case("安装：在真实 settings 的副本上 apply → undo 往返，结果与原文件 JSON 相等；真实文件 sha1 不变")
def _(E):
    real = os.path.expanduser("~/.claude/settings.json")
    if not os.path.isfile(real):
        return True
    h_real = sha(real)
    sp = os.path.join(E.tmp, "settings.json"); shutil.copy2(real, sp)
    orig = json.load(open(sp))
    r1 = inst(E, "--apply", settings=sp); r2 = inst(E, "--undo", settings=sp)
    return r1[0] == 0 and r2[0] == 0 and json.load(open(sp)) == orig and sha(real) == h_real


@contextlib.contextmanager
def tmpdir():
    base = os.environ.get("D_TEST_TMP")
    if base:
        os.makedirs(base, exist_ok=True)
        d = tempfile.mkdtemp(dir=base)
    else:
        d = tempfile.mkdtemp(prefix="rg_test_")
    try:
        yield d
    finally:
        for dp, dns, fns in os.walk(d):
            for x in dns:
                try: os.chmod(os.path.join(dp, x), 0o700)
                except Exception: pass
        shutil.rmtree(d, ignore_errors=True)   # 只清理本用例自己建的临时目录


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    a = ap.parse_args()
    sel = [c for c in CASES if a.k in c[0]]
    npass = 0
    for desc, cfg, fn in sel:
        with tmpdir() as t:
            E = Env(t, cfg)
            try:
                ok, note = bool(fn(E)), ""
            except Exception as e:
                ok, note = False, "  出错：%s: %s" % (type(e).__name__, str(e)[:200])
        npass += ok
        print("%s  %s%s" % ("PASS" if ok else "FAIL", desc, note))
    print("\n%d/%d 通过" % (npass, len(sel)))
    return 0 if npass == len(sel) else 1


if __name__ == "__main__":
    sys.exit(main())
