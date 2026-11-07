#!/usr/bin/env python3
"""第 6 轮 P 的测试：价格表、节约表、状态栏（并发/限时/坏输入/额度历史）、weekly_compare、usaver-status。

用法：
  python3 tools/tests_r6P.py              # 全部用例（约 30 秒）
  python3 tools/tests_r6P.py --calib      # F2 校准：离线跑节约表，对照 F2 实测（只读 transcript，不写 state）
  R6P_STATUSLINE=路径 python3 tools/tests_r6P.py   # 测指定的状态栏副本（上线前在副本上测）
隔离：每个用例一个临时目录（建在 state/_test_tmp/ 下）：假 state、假 HOME、套件文件的副本；不碰真实 state 和 ~/.claude。
"""
import glob, json, math, os, re, shutil, subprocess, sys, tempfile, time, traceback

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMPROOT = os.path.join(KIT, "state", "_test_tmp")
STATUSLINE = os.environ.get("R6P_STATUSLINE") or os.path.join(KIT, "hooks", "statusline.sh")
sys.path.insert(0, os.path.join(KIT, "tools"))
sys.path.insert(0, os.path.join(KIT, "hooks"))
import pricing as PR            # noqa: E402
import savings_meter as SM      # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


class Box:
    """一个隔离环境：suite/（hooks、tools、config.json 的副本）、state/、home/、proj/。"""

    def __init__(self, meter=True):
        os.makedirs(TMPROOT, exist_ok=True)
        self.tmp = tempfile.mkdtemp(prefix="r6P_", dir=TMPROOT)
        self.suite = os.path.join(self.tmp, "suite")
        self.state = os.path.join(self.tmp, "state")
        self.home = os.path.join(self.tmp, "home")
        self.proj = os.path.join(self.home, ".claude", "projects", "-x-proj")
        for d in (os.path.join(self.suite, "hooks"), os.path.join(self.suite, "tools"), self.state, self.proj):
            os.makedirs(d, exist_ok=True)
        shutil.copy(STATUSLINE, os.path.join(self.suite, "hooks", "statusline.sh"))
        shutil.copy(os.path.join(KIT, "hooks", "context_guard.py"), os.path.join(self.suite, "hooks"))
        shutil.copy(os.path.join(KIT, "config.json"), self.suite)
        if meter:
            shutil.copy(os.path.join(KIT, "hooks", "savings_meter.py"), os.path.join(self.suite, "hooks"))
            shutil.copy(os.path.join(KIT, "tools", "pricing.py"), os.path.join(self.suite, "tools"))
        c = json.load(open(os.path.join(self.suite, "config.json")))
        c["cap_from_date"] = "2099-01-01"
        json.dump(c, open(os.path.join(self.suite, "config.json"), "w"))

    def env(self, **kw):
        e = {"HOME": self.home, "USAGE_SAVER_STATE": self.state, "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
             "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "TZ": "America/Los_Angeles"}
        e.update(kw)
        return e

    def sl(self, payload, raw=None):
        t0 = time.time()
        p = subprocess.run(["/bin/sh", os.path.join(self.suite, "hooks", "statusline.sh")],
                           input=raw if raw is not None else json.dumps(payload), capture_output=True, text=True,
                           env=self.env(), timeout=20)
        return p.returncode, p.stdout.strip(), p.stderr, time.time() - t0

    def sl_bg(self, payload):
        return subprocess.Popen(["/bin/sh", os.path.join(self.suite, "hooks", "statusline.sh")], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env())

    def jl(self, name, rows, sub=None):
        d = self.proj if not sub else os.path.join(self.proj, sub)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p, "w") as f:
            for r in rows:
                f.write((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n")
        return p

    def done(self):
        shutil.rmtree(self.tmp, ignore_errors=True)   # 只删测试自己建的临时目录


# ------------------------------------------------------------------ transcript 构造
_n = [0]


def asst(cr=0, cw1h=0, cw5m=0, inp=2, out=100, model="claude-sonnet-5-5", ts="2026-11-06T10:00:00Z", mid=None, side=False, agent=None):
    _n[0] += 1
    cc = {"ephemeral_1h_input_tokens": cw1h, "ephemeral_5m_input_tokens": cw5m}
    r = {"type": "assistant", "timestamp": ts, "isSidechain": side,
         "message": {"id": mid or "msg_%d" % _n[0], "model": model, "role": "assistant", "content": [],
                     "usage": {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
                               "cache_creation_input_tokens": cw1h + cw5m, "cache_creation": cc}}}
    if agent:
        r["agentId"] = agent
    return r


def tool_result(text, tuid="toolu_1", ts="2026-11-06T10:00:01Z"):
    return {"type": "user", "timestamp": ts, "isSidechain": False,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tuid, "content": text}]}}


def tf_text(log, n_lines, filtered=True):
    body = "\n".join("FAILED test_%d" % i for i in range(5))
    if filtered:
        return "[测试输出写入 %s]\n%s\n----- 结尾 -----\nend\n[测试输出已过滤：完整 %d 行在上面的日志里；需要原样输出就在命令前加 USAGE_SAVER_RAW=1]" % (log, body, n_lines)
    return "[测试输出写入 %s]\n%s" % (log, open(log).read())


def make_log(box, name, n_lines, width=50):
    p = os.path.join(box.tmp, name)
    with open(p, "w") as f:
        for i in range(n_lines):
            f.write(("line %d " % i).ljust(width, "x") + "\n")
    return p


def payload(sid, tp, cwd="/x", **kw):
    d = {"session_id": sid, "transcript_path": tp, "cwd": cwd, "model": {"id": "claude-sonnet-5-5", "display_name": "Sonnet 5.5"},
         "context_window": {"current_usage": {"input_tokens": 2, "cache_read_input_tokens": 50000, "cache_creation_input_tokens": 100},
                            "context_window_size": 1000000}}
    d.update(kw)
    return d


def sess_state(box, sid):
    return json.load(open(os.path.join(box.state, "savings", SM._safe_sid(sid) + ".json")))


# ------------------------------------------------------------------ 价格表
@case("P01 价格表与官方页一致：Opus5.5=2×Sonnet5.5（五类都是）；5.5 代缓存读 0.05×、旧模型 0.1×、Fable5.1 0.025×；写入 1.25×/2×")
def _():
    o, s = PR.lookup("claude-opus-5-5")[0], PR.lookup("claude-sonnet-5-5")[0]
    assert all(abs(o[k] - 2 * s[k]) < 1e-12 for k in PR.KINDS), (o, s)
    eq = lambda a, b: all(abs(x - y) < 1e-12 for x, y in zip(a, b))
    assert eq((o["inp"], o["cr"], o["cw5m"], o["cw1h"], o["out"]), (4.0, 0.2, 5.0, 8.0, 20.0))
    assert eq((s["inp"], s["cr"], s["cw5m"], s["cw1h"], s["out"]), (2.0, 0.1, 2.5, 4.0, 10.0))
    assert abs(PR.lookup("claude-opus-5")[0]["cr"] - 0.5) < 1e-12 and abs(PR.lookup("claude-sonnet-4-6")[0]["cr"] - 0.3) < 1e-12
    assert abs(PR.lookup("claude-fable-5-1")[0]["cr"] - 0.25) < 1e-12 and abs(PR.lookup("claude-fable-5")[0]["cr"] - 1.0) < 1e-12
    h = PR.lookup("claude-haiku-5-5")[0]
    assert eq((h["inp"], h["cr"], h["out"]), (0.10, 0.01, 0.50))
    assert PR.lookup("claude-haiku-5-5", 150_000)[0]["inp"] == 0.50     # >100k 提示的高一档
    return True


@case("P02 未知模型带标记、不悄悄算错：新版本→同族最新价+family_fallback；新族→Sonnet5.5价+unknown；<synthetic>→synthetic")
def _():
    assert PR.lookup("claude-opus-5-5-20261201")[1] is None and PR.lookup("claude-opus-5-5[1m]")[1] is None
    assert PR.lookup("anthropic.claude-sonnet-5-5")[1] is None
    p, f = PR.lookup("claude-opus-7")
    assert f == "family_fallback" and p is PR.TABLE[("opus", "5.5")]
    p, f = PR.lookup("gpt-6.1-sol")
    assert f == "unknown" and p is PR.TABLE[("sonnet", "5.5")]
    assert PR.lookup("")[1] == "unknown" and PR.lookup(None)[1] == "unknown"
    assert PR.lookup("<synthetic>")[1] == "synthetic"
    assert PR.family("claude-haiku-5-5") == "haiku" and PR.family("claude-fable-5-1") == "fable" and PR.family("weird-x") == "weird-x"
    return True


@case("P03 usage 分项：没有 5m/1h 拆分的写入按 5 分钟档；Sonnet 当量 = 美元 ÷ $2/M；本模型单位兼容旧口径（Opus 5 的 3.75k）")
def _():
    u = {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 10000, "cache_creation_input_tokens": 1000}
    assert PR.parts(u)["cw5m"] == 1000 and PR.parts(u)["cw1h"] == 0
    assert abs(PR.native("claude-opus-5", u)[0] - 3750) < 1e-6
    d, _ = PR.usd("claude-sonnet-5-5", u)
    assert abs(PR.weighted("claude-sonnet-5-5", u)[0] - d * 1e6 / 2) < 1e-6
    assert abs(PR.weighted("claude-opus-5-5", u)[0] - 2 * PR.weighted("claude-sonnet-5-5", u)[0]) < 1e-6
    assert PR.parts({"cache_creation_input_tokens": "x", "input_tokens": -5}) == {"inp": 0, "cw5m": 0, "cw1h": 0, "cr": 0, "out": 0}
    return True


# ------------------------------------------------------------------ 节约表：逐次事件的反事实
@case("M01 test_filter 事件：截掉的 token × (首个请求写入价 + 热请求读价 + 冷请求写入价)，compact 之后不再计；之前的请求不计")
def _():
    B = Box()
    try:
        log = make_log(B, "run-A", 100)                  # 100 行 × 51 字符 ≈ 5100 字符 < 30000：反事实 = 全文
        shown = tf_text(log, 100)
        tp = B.jl("s1.jsonl", [asst(cr=9000, cw1h=500), tool_result(shown),
                               asst(cr=10000, cw1h=500),          # 截掉的内容第一次进上下文：写入价（1h）
                               asst(cr=11000, cw1h=200),          # 热：读价
                               asst(cr=100, cw1h=12000),          # 冷（写入 > 读）：写入价
                               {"type": "system", "subtype": "compact_boundary", "isSidechain": False},
                               asst(cr=5000, cw1h=100)])
        r = SM.update({"session_id": "s1", "transcript_path": tp, "cwd": "/x"}, B.state)
        tok = SM.est_tokens(open(log).read()) - SM.est_tokens(shown)
        exp = tok * (PR.per_token("claude-sonnet-5-5", "cw1h") * 2 + PR.per_token("claude-sonnet-5-5", "cr"))
        it = list(sess_state(B, "s1")["files"][tp]["items"].values())[0]
        assert abs(it["saved"] - exp) < 1e-6 * max(1, exp) and it["n"] == 3 and it["closed"], (it, exp)
        actual = sum(PR.weighted("claude-sonnet-5-5", a["message"]["usage"])[0]
                     for a in [asst(cr=9000, cw1h=500), asst(cr=10000, cw1h=500), asst(cr=11000, cw1h=200), asst(cr=100, cw1h=12000), asst(cr=5000, cw1h=100)])
        assert abs(r["sess_pct"] - 100 * exp / (actual + exp)) < 1e-6, r
        return True
    finally:
        B.done()


@case("M02 日志超过 CC 的 3 万字符：反事实是 CC 的约 2.1k 字符预览，过滤结果更长时节省记为负数（照实记）；≤40 行的短日志多一行路径提示也是负数")
def _():
    B = Box()
    try:
        big = make_log(B, "run-B", 3000)                 # 约 15 万字符
        long_shown = "[测试输出写入 %s]\n" % big + ("E   assert x == y  " * 40 + "\n") * 30 + "[测试输出已过滤：完整 3000 行]"
        short = make_log(B, "run-C", 10)
        tp = B.jl("s2.jsonl", [tool_result(long_shown, "t1"), tool_result(tf_text(short, 10, filtered=False), "t2"),
                               asst(cr=1000, cw5m=100)])
        SM.update({"session_id": "s2", "transcript_path": tp, "cwd": "/x"}, B.state)
        items = sess_state(B, "s2")["files"][tp]["items"]
        toks = sorted(it["tok"] for it in items.values())
        assert len(items) == 2 and toks[0] < 0 and toks[1] < 0, toks
        assert any("CC 预览" in it["method"] for it in items.values())
        return True
    finally:
        B.done()


@case("M03 去重：同一 message.id 多行只算一次；同一 tool_use_id 的重复结果只算一个事件；重复调用 update 不重复累计")
def _():
    B = Box()
    try:
        log = make_log(B, "run-D", 200)
        a = asst(cr=1000, cw5m=10, mid="dup1")
        tp = B.jl("s3.jsonl", [a, a, tool_result(tf_text(log, 200), "tX"), tool_result(tf_text(log, 200), "tX"), asst(cr=2000, cw5m=100)])
        p = {"session_id": "s3", "transcript_path": tp, "cwd": "/x"}
        r1 = SM.update(p, B.state)
        r2 = SM.update(p, B.state)
        fs = sess_state(B, "s3")["files"][tp]
        assert fs["n_req"] == 2 and len(fs["items"]) == 1, fs
        assert abs(r1["sess_pct"] - r2["sess_pct"]) < 1e-12
        best = SM.read_jsonl(os.path.join(B.state, "savings.jsonl"))
        assert sum(1 for k in best if k.startswith("tf:")) == 1
        # 进度文件丢了从头重算：savings.jsonl 里同一 event_id 取最大 rev，不会算两次
        os.rename(os.path.join(B.state, "savings", "s3.json"), os.path.join(B.state, "s3.moved"))
        SM.update(p, B.state)
        t = SM.totals_from_jsonl(os.path.join(B.state, "savings.jsonl"), "s3")
        assert abs(t["sess_pct"] - r1["sess_pct"]) < 0.01, (t, r1)       # jsonl 里的值保留 1 位小数
        return True
    finally:
        B.done()


@case("M04 增量读取：最后一行没写完不读、写完后接着读；文件被截短就从头重算；坏 JSON 行、二进制垃圾跳过")
def _():
    B = Box()
    try:
        tp = os.path.join(B.proj, "s4.jsonl")
        full = json.dumps(asst(cr=1000, cw5m=100, mid="m1"))
        with open(tp, "w") as f:
            f.write("{坏行\n" + json.dumps(asst(cr=500, cw5m=50, mid="m0")) + "\n")
            f.write(full[:40])
        p = {"session_id": "s4", "transcript_path": tp, "cwd": "/x"}
        SM.update(p, B.state)
        assert sess_state(B, "s4")["files"][tp]["n_req"] == 1
        with open(tp, "a") as f:
            f.write(full[40:] + "\n")
        with open(tp, "ab") as f:
            f.write(b"\xff\xfe\x00garbage \"usage\"\n")
        SM.update(p, B.state)
        assert sess_state(B, "s4")["files"][tp]["n_req"] == 2
        with open(tp, "w") as f:
            f.write(json.dumps(asst(cr=10, cw5m=1, mid="z")) + "\n")
        SM.update(p, B.state)
        assert sess_state(B, "s4")["files"][tp]["n_req"] == 1
        return True
    finally:
        B.done()


@case("M05 子 agent（<会话>/subagents/*.jsonl）的成本和 test_filter 事件计入本会话；未知模型 → 显示带 ?")
def _():
    B = Box()
    try:
        log = make_log(B, "run-E", 300)
        tp = B.jl("s5.jsonl", [asst(cr=1000, cw5m=100)])
        B.jl("agent-1.jsonl", [tool_result(tf_text(log, 300), "ta"), asst(cr=2000, cw5m=50, side=True, agent="a1", model="gpt-9")],
             sub=os.path.join("s5", "subagents"))
        r = SM.update({"session_id": "s5", "transcript_path": tp, "cwd": "/x"}, B.state)
        st = sess_state(B, "s5")
        assert len(st["files"]) == 2 and r["sess_pct"] > 0 and r["flags"].get("unknown") == 1, (r, st["files"].keys())
        assert SM.render(r).endswith("?")
        return True
    finally:
        B.done()


@case("M06 守卫后 /clear：只进「估计区间」(counted=false)，不进总数；上限 = 上下文差 × 之后每次读价")
def _():
    B = Box()
    try:
        g = {"type": "attachment", "timestamp": "2026-11-06T10:05:00Z",
             "attachment": {"type": "hook_additional_context", "content": ["[上下文守卫] 当前上下文约 200k token"]}}
        p1 = B.jl("old.jsonl", [asst(cr=150000, cw1h=1000, ts="2026-11-06T10:00:00Z"), g, asst(cr=199000, cw1h=1000, ts="2026-11-06T10:06:00Z")])
        os.utime(p1, (time.time() - 100, time.time() - 100))
        clear = {"type": "attachment", "timestamp": "2026-11-06T10:07:00Z", "attachment": {"type": "hook_success", "hookName": "SessionStart:clear"}}
        p2 = B.jl("new.jsonl", [clear, asst(cr=10000, cw1h=20000, ts="2026-11-06T10:08:00Z"),
                                asst(cr=31000, cw1h=1000, ts="2026-11-06T10:09:00Z")])
        os.utime(p2, None)
        # 离线（校准用）：old.jsonl 的修改时间要早于 new 的开始时间
        import datetime as _dt
        e = _dt.datetime(2026, 11, 6, 10, 6, 30, tzinfo=_dt.timezone.utc).timestamp()
        os.utime(p1, (e, e))                             # 修改时间 = clear 之前（10:06:30Z）
        r = SM.update({"session_id": "new", "transcript_path": p2, "cwd": "/x"}, B.state)
        fs = sess_state(B, "new")["files"][p2]
        D = 200000 + 2 + 0 - 30002                       # clear 前上下文 − clear 后首个请求上下文
        exp = D * PR.per_token("claude-sonnet-5-5", "cr") * 2
        assert fs["glink"] and abs(fs["guard_hi"] - exp) < 1e-6, (fs.get("glink"), fs["guard_hi"], exp)
        assert r["sess_pct"] == 0.0                     # 不计入总数
        best = SM.read_jsonl(os.path.join(B.state, "savings.jsonl"))
        gr = [v for k, v in best.items() if k.startswith("guard:")]
        assert gr and gr[0]["counted"] is False and gr[0]["estimate_range"][0] == 0.0
        return True
    finally:
        B.done()


@case("M07 Lock 互斥：16 个进程各自在锁内读-改-写计数器，结果正好 16（没有丢更新）")
def _():
    B = Box()
    try:
        code = ("import sys,os,json,time;sys.path.insert(0,%r);import savings_meter as SM\n"
                "p=%r\n"
                "with SM.Lock(p+'.lock',wait=10) as l:\n"
                "  assert l.ok\n"
                "  v=json.load(open(p)) if os.path.exists(p) else 0\n"
                "  time.sleep(0.005)\n"
                "  SM._atomic(p,v+1)\n") % (os.path.join(KIT, "hooks"), os.path.join(B.state, "ctr.json"))
        ps = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(16)]
        assert all(p.wait(timeout=30) == 0 for p in ps)
        return json.load(open(os.path.join(B.state, "ctr.json"))) == 16
    finally:
        B.done()


@case("M08 多会话并发写：16 个状态栏同时跑（各带一个 test_filter 事件）→ savings.jsonl 每行都是合法 JSON、每个会话都在、合计不重复")
def _():
    B = Box()
    try:
        log = make_log(B, "run-F", 400)
        pays = []
        for i in range(16):
            tp = B.jl("c%d.jsonl" % i, [asst(cr=1000, cw5m=100), tool_result(tf_text(log, 400), "t%d" % i), asst(cr=3000, cw5m=200)])
            pays.append(payload("c%d" % i, tp))
        for rnd in range(2):
            ps = [B.sl_bg(p) for p in pays]
            for p, pl in zip(ps, pays):
                out, _ = p.communicate(json.dumps(pl), timeout=30)
                assert p.returncode == 0 and "ctx" in out
        lines = open(os.path.join(B.state, "savings.jsonl")).read().splitlines()
        rows = [json.loads(l) for l in lines]
        best = SM.read_jsonl(os.path.join(B.state, "savings.jsonl"))
        assert sum(1 for k in best if k.startswith("tf:")) == 16 and sum(1 for k in best if k.startswith("actual:")) == 16
        T = json.load(open(os.path.join(B.state, "savings_totals.json")))
        assert len(T["sessions"]) == 16, len(T["sessions"])
        t = SM.totals_from_jsonl(os.path.join(B.state, "savings.jsonl"))
        one = SM.totals_from_jsonl(os.path.join(B.state, "savings.jsonl"), "c0")
        assert abs(t["cum_pct"] - one["sess_pct"]) < 1e-6      # 16 个相同会话：总% = 单个会话%
        return len(rows) >= 32
    finally:
        B.done()


@case("M09 savings.jsonl 超过上限时在锁内压缩成每个 event 一行（不丢 event、取最大 rev）")
def _():
    B = Box()
    try:
        p = os.path.join(B.state, "savings.jsonl")
        with open(p, "w") as f:
            for rev in range(1, 50):
                for e in range(20):
                    f.write(json.dumps({"event_id": "e%d" % e, "rev": rev, "counted": True, "saved_weighted_tokens": rev,
                                        "actual_weighted_tokens": 0, "session_id": "s"}) + "\n")
        SM.compact_jsonl(p)
        rows = [json.loads(l) for l in open(p)]
        return len(rows) == 20 and all(r["rev"] == 49 for r in rows)
    finally:
        B.done()


# ------------------------------------------------------------------ 状态栏
@case("S01 坏输入安全降级：坏 JSON / 非对象 / 空输入 → 兜底行、退出码 0、无 traceback；transcript 不存在 → 正常行")
def _():
    B = Box()
    try:
        for raw in ("{坏", "[1,2]", "", "null", json.dumps({"model": "x", "context_window": 5})):
            rc, out, err, _ = B.sl(None, raw=raw)
            assert rc == 0 and "状态栏输入无法解析" in out and "Traceback" not in err, (raw, out, err)
        rc, out, err, _ = B.sl(payload("sX", "/nonexistent/x.jsonl"))
        assert rc == 0 and out.startswith("Sonnet 5.5 | ctx 50k/1M") and "Traceback" not in err, out
        rc, out, err, _ = B.sl(payload("sY", 12345))
        assert rc == 0 and "ctx 50k" in out
        return True
    finally:
        B.done()


@case("S02 显示：有事件时出现「省≈X%累计 本会话Y%(估·上限)」；没装节约表（缺 savings_meter.py / pricing.py）时这一段不出现、其余照常")
def _():
    B = Box()
    try:
        log = make_log(B, "run-G", 500)
        tp = B.jl("d1.jsonl", [asst(cr=1000, cw5m=100), tool_result(tf_text(log, 500)), asst(cr=3000, cw5m=200)])
        rc, out, _, _ = B.sl(payload("d1", tp))
        assert rc == 0 and re.search(r"省≈\d+\.\d%累计 本会话\d+\.\d%\(估·上限\)", out), out
    finally:
        B.done()
    B = Box(meter=False)
    try:
        tp = B.jl("d2.jsonl", [asst(cr=1000, cw5m=100)])
        rc, out, _, _ = B.sl(payload("d2", tp))
        assert rc == 0 and "省≈" not in out and "ctx 50k" in out, out
        os.makedirs(os.path.join(B.suite, "tools"), exist_ok=True)
        shutil.copy(os.path.join(KIT, "hooks", "savings_meter.py"), os.path.join(B.suite, "hooks"))   # 有表、没价格表
        rc, out, err, _ = B.sl(payload("d2", tp))
        assert rc == 0 and "省≈" not in out and "Traceback" not in err, (out, err)
        return True
    finally:
        B.done()


@case("S03 超大 transcript（约 60MB）：每次刷新 <150ms（解析每次最多 45ms，几次追上），追上后稳态 <100ms，结果与一次性离线解析一致")
def _():
    B = Box()
    try:
        log = make_log(B, "run-H", 800)
        tp = os.path.join(B.proj, "big.jsonl")
        filler = "y" * 20000
        with open(tp, "w") as f:
            for i in range(3000):
                f.write(json.dumps({"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "f%d" % i, "content": filler}]}}) + "\n")
                f.write(json.dumps(asst(cr=100000 + i, cw5m=500, mid="b%d" % i)) + "\n")
                if i == 1500:
                    f.write(json.dumps(tool_result(tf_text(log, 800), "big1")) + "\n")
        size = os.path.getsize(tp)
        times, outs = [], []
        for _ in range(12):
            rc, out, _, dtm = B.sl(payload("big", tp))
            times.append(dtm); outs.append(out)
            if not out.endswith("…"):
                break
        steady = []
        for _ in range(3):
            rc, out, _, dtm = B.sl(payload("big", tp))
            steady.append(dtm)
        ref = SM.offline([tp])[0]
        st = SM.summarize(sess_state(B, "big"))
        print("    transcript %.0fMB；追上用了 %d 次刷新，耗时 %s ms；稳态 %s ms" % (
            size / 1e6, len(times), [round(t * 1000) for t in times], [round(t * 1000) for t in steady]))
        assert size > 50e6
        assert max(times) < 0.15 and max(steady) < 0.10, (times, steady)
        assert abs(st["actual"] - ref["actual"]) < 1e-3 and abs(st["saved"] - ref["saved"]) < 1e-3
        return not outs[-1].endswith("…")
    finally:
        B.done()


@case("S04 Codex 复核 I 阻塞项 1：32 个状态栏并发写 ctx_window.json × 3 轮，不丢任何会话的窗口记录；丢了会漏报的守卫场景照常提醒")
def _():
    B = Box()
    try:
        counts = []
        for rnd in range(3):
            pays = []
            for i in range(32):
                p = payload("w%d_%d" % (rnd, i), "/nonexistent")
                p["context_window"] = {"current_usage": {"input_tokens": 180000}, "context_window_size": 200000}
                pays.append(p)
            ps = [B.sl_bg(p) for p in pays]
            for p, pl in zip(ps, pays):
                p.communicate(json.dumps(pl), timeout=30)
                assert p.returncode == 0
            cw = json.load(open(os.path.join(B.state, "ctx_window.json")))
            counts.append(sum(1 for k in cw if k.startswith("w%d_" % rnd)))
        print("    每轮保留的记录数：%s（应为 32）" % counts)
        assert counts == [32, 32, 32], counts
        # 守卫读这个记录：200k 窗口、180k 上下文 → 要提醒
        t = os.path.join(B.tmp, "t.jsonl")
        open(t, "w").write(json.dumps(asst(cr=179998, cw5m=0)) + "\n")
        g = subprocess.run([sys.executable, os.path.join(B.suite, "hooks", "context_guard.py")], capture_output=True, text=True,
                           input=json.dumps({"session_id": "w2_7", "transcript_path": t, "cwd": B.tmp}), env=B.env(), timeout=20)
        return "[上下文守卫]" in g.stdout
    finally:
        B.done()


@case("S05 rate_history.jsonl：读数变化才追加（同样的读数不重复写）；带时间、5h/周、模型、会话；超过 2MB 轮转成 .1")
def _():
    B = Box()
    try:
        res = int(time.time()) + 3600
        def pl(p5, p7):
            return payload("r1", "/none", rate_limits={"five_hour": {"used_percentage": p5, "resets_at": res},
                                                       "seven_day": {"used_percentage": p7, "resets_at": res + 86400}})
        for x in ((10, 20), (10, 20), (10, 20), (11, 20), (11, 21), (11, 21)):
            B.sl(pl(*x))
        p = os.path.join(B.state, "rate_history.jsonl")
        rows = [json.loads(l) for l in open(p)]
        assert [(r["five_hour"], r["seven_day"]) for r in rows] == [(10, 20), (11, 20), (11, 21)], rows
        assert rows[0]["model"] == "claude-sonnet-5-5" and rows[0]["session_id"] == "r1" and rows[0]["ts"] > 0
        B.sl(pl(5, 21))                                  # 同窗口变小（旧会话的旧读数）：合并后仍是 11%，不写
        assert len(open(p).read().splitlines()) == 3
        with open(p, "a") as f:
            f.write(("#" * 1000 + "\n") * 2200)
        B.sl(pl(12, 21))
        assert os.path.exists(p + ".1") and len(open(p).read().splitlines()) == 1
        return True
    finally:
        B.done()


@case("S06 rate.json 并发：16 个会话同时带不同读数，rate.json 始终是合法 JSON，保留同窗口最大的读数")
def _():
    B = Box()
    try:
        res = int(time.time()) + 3600
        pays = [payload("q%d" % i, "/none", rate_limits={"five_hour": {"used_percentage": 10 + i, "resets_at": res}}) for i in range(16)]
        ps = [B.sl_bg(p) for p in pays]
        for p, pl in zip(ps, pays):
            p.communicate(json.dumps(pl), timeout=30)
        r = json.load(open(os.path.join(B.state, "rate.json")))
        return r["rate_limits"]["five_hour"]["used_percentage"] == 25
    finally:
        B.done()


@case("S07 H §4：没有 current_usage 时用 payload 的 used_percentage × context_window_size；窗口显示在 ctx 后面")
def _():
    B = Box()
    try:
        p = payload("u1", "/none")
        p["context_window"] = {"used_percentage": 40, "context_window_size": 200000}
        rc, out, _, _ = B.sl(p)
        assert "ctx 80k/200k" in out, out
        p["context_window"] = {"current_usage": {"input_tokens": 1000}, "used_percentage": 40, "context_window_size": 200000}
        rc, out, _, _ = B.sl(p)
        return "ctx 1k/200k" in out
    finally:
        B.done()


# ------------------------------------------------------------------ weekly_compare
def wc_run(B, rows_by_proj, now_expr):
    for pn, rows in rows_by_proj.items():
        d = os.path.join(B.home, ".claude", "projects", pn)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "s.jsonl"), "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    src = open(os.path.join(KIT, "tools", "weekly_compare.py")).read().replace("dt.datetime.now(UTC)", now_expr)
    dst = os.path.join(B.suite, "tools", "weekly_compare.py")
    open(dst, "w").write(src)
    p = subprocess.run([sys.executable, dst], capture_output=True, text=True, env=B.env(), timeout=60)
    assert p.returncode == 0, p.stderr[-800:]
    h = json.load(open(os.path.join(B.suite, "reports", "weekly_history.json")))
    md = open(glob.glob(os.path.join(B.suite, "reports", "weekly_*.md"))[0]).read()
    return h[-1], md


@case("W01 weekly_compare：同样 token 的 Opus5.5 每次成本（Sonnet 当量）= 2× Sonnet5.5；两种口径都报；haiku/fable 单独成组；未知模型在报告里点名")
def _():
    B = Box()
    try:
        u = {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 10000, "cache_creation_input_tokens": 1000}
        def m(mid, ts, model):
            return {"type": "assistant", "timestamp": ts, "message": {"id": mid, "model": model, "usage": u}}
        rows = [m("a", "2026-10-25T20:00:00Z", "claude-opus-5-5"), m("b", "2026-10-25T20:01:00Z", "claude-sonnet-5-5"),
                m("c", "2026-10-25T20:02:00Z", "claude-haiku-5-5"), m("d", "2026-10-25T20:03:00Z", "claude-fable-5-1"),
                m("e", "2026-10-25T20:04:00Z", "mystery-model-1")]
        last, md = wc_run(B, {"-x": rows}, "dt.datetime(2026, 11, 8, 20, 0, tzinfo=UTC)")
        po = re.search(r"\| main·opus \| [\d.]+ \(1\) \| — \| ([\d.]+) \(1\)", md)
        ps = re.search(r"\| main·sonnet \| [\d.]+ \(1\) \| — \| ([\d.]+) \(1\)", md)
        assert po and ps and abs(float(po.group(1)) - 2 * float(ps.group(1))) < 0.11, md[:3000]
        assert "main·haiku" in md and "main·fable" in md and "mystery-model-1" in md and "Sonnet 5.5 当量" in md and "本模型单位" in md
        assert last["before"]["pricing"] == "tools/pricing.py" and "seq_cost_per_call" in last["before"]
        return True
    finally:
        B.done()


@case("W02 weekly_compare 日数口径：11-05 23:58 之后的零头天剔除（列出来），每天成本只按完整天；旧字段 days 仍按「有记录的天」")
def _():
    B = Box()
    try:
        u = {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 10000, "cache_creation_input_tokens": 1000}
        def m(mid, ts):
            return {"type": "assistant", "timestamp": ts, "message": {"id": mid, "model": "claude-sonnet-5-5", "usage": u}}
        rows = [m("z%d" % i, "2026-11-06T07:59:%02dZ" % i) for i in range(17)]        # 11-05 23:59 PST，17 次
        rows += [m("y%d" % i, "2026-11-07T18:00:%02dZ" % i) for i in range(10)]       # 11-07 完整天
        last, md = wc_run(B, {"-x": rows}, "dt.datetime(2026, 11, 8, 20, 0, tzinfo=UTC)")
        r = last["recent"]
        assert "2026-11-05" in r["partial_days"] and "2026-11-08" not in r["partial_days"] and r["full_days"] == 2, r
        assert abs(r["calls_per_full_day"] - 5.0) < 1e-9 and r["days"] == 2, r       # 11-06、11-07 两个完整天共 10 次
        return "已剔除" in md
    finally:
        B.done()


@case("W03 weekly_compare 没有 pricing.py 时退回内置倍数并在报告里写明（scorecard 沙箱就是这种情况）")
def _():
    B = Box(meter=False)
    try:
        u = {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 10000, "cache_creation_input_tokens": 1000}
        rows = [{"type": "assistant", "timestamp": "2026-10-25T20:00:00Z", "message": {"id": "a", "model": "claude-opus-5", "usage": u}}]
        last, md = wc_run(B, {"-x": rows}, "dt.datetime(2026, 11, 8, 20, 0, tzinfo=UTC)")
        return abs(last["before"]["cost_per_call"] - 3.75) < 1e-9 and "没找到 tools/pricing.py" in md
    finally:
        B.done()


# ------------------------------------------------------------------ usaver-status
@case("U01 usaver-status 显示同一个节约数（只读 savings.jsonl）")
def _():
    B = Box()
    try:
        log = make_log(B, "run-I", 500)
        tp = B.jl("v1.jsonl", [dict(asst(cr=1000, cw5m=100), cwd=B.tmp), tool_result(tf_text(log, 500)), asst(cr=3000, cw5m=200)])
        rc, out, _, _ = B.sl(payload("v1", tp))
        m = re.search(r"省≈([\d.]+)%累计", out)
        p = subprocess.run([sys.executable, os.path.join(KIT, "adapters", "common", "usaver_status.py"), "--tool", "claude", "--any"],
                           capture_output=True, text=True, timeout=30,
                           env=B.env(CLAUDE_CONFIG_DIR=os.path.join(B.home, ".claude")))
        m2 = re.search(r"省≈([\d.]+)%累计 本会话([\d.]+)%", p.stdout)
        assert m and m2 and m.group(1) == m2.group(1), (out, p.stdout, p.stderr[-500:])
        return True
    finally:
        B.done()


# ------------------------------------------------------------------ F2 校准（--calib）
def calib():
    run = "20261106-185012"
    recs = [json.loads(l) for l in open(os.path.join(KIT, "tools", "bench", "results", "token_bench_%s_claude_A+B.jsonl" % run))]
    recs = [r for r in recs if not r.get("warmup") and not r.get("infra_error") and r.get("task")]
    root = os.path.expanduser("~/.claude/projects")
    rows = {}
    for r in recs:
        key = (r["task"], r["arm"], r["rep"])
        d = glob.glob(os.path.join(root, "*%s-claude-A-B-%s--%s--r%d-proj" % (run, r["task"].replace("_", "-"), r["arm"], r["rep"])))
        if not d:
            continue
        paths = glob.glob(os.path.join(d[0], "*.jsonl"))
        res = SM.offline(paths)
        rows[key] = dict(cost=r["cost_usd"], actual=sum(x["actual"] for x in res), saved=sum(x["saved"] for x in res),
                         ghi=sum(x["guard_hi"] for x in res), grw=sum(x["guard_rewrite"] for x in res),
                         events=sum(x["events"] for x in res), tf=r.get("test_filtered"), clears=r.get("clears"))
    tasks = sorted({k[0] for k in rows})
    out = ["| 任务 | A 实测 $/次 | B 实测 $/次 | F2 实测 B 相对 A | 节约表·计入总数（B） | 守卫区间上限（B，不计入） | 上限−重写开销 | 节约表算的成本 / F2 cost_usd |",
           "|---|---|---|---|---|---|---|---|"]
    tot = {"A": 0.0, "B": 0.0, "saved": 0.0, "actB": 0.0, "ghi": 0.0, "grw": 0.0}
    chk = []
    for t in tasks:
        A = [v for k, v in rows.items() if k[0] == t and k[1] == "A"]
        Bv = [v for k, v in rows.items() if k[0] == t and k[1] == "B"]
        if not A or not Bv:
            continue
        ca, cb = sum(x["cost"] for x in A) / len(A), sum(x["cost"] for x in Bv) / len(Bv)
        actB = sum(x["actual"] for x in Bv); sav = sum(x["saved"] for x in Bv); ghi = sum(x["ghi"] for x in Bv); grw = sum(x["grw"] for x in Bv)
        ratio = [x["actual"] * 2e-6 / x["cost"] for x in A + Bv if x["cost"]]
        chk += ratio
        out.append("| %s | %.3f | %.3f | %+.1f%% | %.2f%%（%d 个事件） | %.1f%% | %.1f%% | %.4f–%.4f |" % (
            t, ca, cb, 100 * (cb / ca - 1), SM.pct(sav, actB) or 0, sum(x["events"] for x in Bv),
            SM.pct(ghi, actB) or 0, SM.pct(max(ghi - grw, 0), actB) or 0, min(ratio), max(ratio)))
        tot["A"] += ca; tot["B"] += cb; tot["saved"] += sav; tot["actB"] += actB; tot["ghi"] += ghi; tot["grw"] += grw
    out.append("| **合计（各任务均值相加）** | %.3f | %.3f | %+.1f%% | %.2f%% | %.1f%% | %.1f%% | %.4f–%.4f |" % (
        tot["A"], tot["B"], 100 * (tot["B"] / tot["A"] - 1), SM.pct(tot["saved"], tot["actB"]) or 0,
        SM.pct(tot["ghi"], tot["actB"]) or 0, SM.pct(max(tot["ghi"] - tot["grw"], 0), tot["actB"]) or 0, min(chk), max(chk)))
    print("\n".join(out))
    print("\n（节约表的 %%  = 节省 / (实际 + 节省)；F2 实测列 = B 臂均值 / A 臂均值 − 1，负数表示 B 更省。运行 %s，%d 次运行。）" % (run, len(rows)))


def main():
    if "--calib" in sys.argv:
        calib()
        return 0
    ok = 0
    for name, fn in CASES:
        t0 = time.time()
        try:
            r = fn()
            good = r is True
            msg = "" if good else " → 返回 %r" % (r,)
        except Exception as e:
            good, msg = False, " → %s: %s\n%s" % (type(e).__name__, str(e)[:600], traceback.format_exc()[-1200:])
        ok += good
        print("%s %s（%.1fs）%s" % ("PASS" if good else "FAIL", name, time.time() - t0, msg))
    print("\n合计 %d：通过 %d" % (len(CASES), ok))
    print("%d/%d 通过" % (ok, len(CASES)))
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
