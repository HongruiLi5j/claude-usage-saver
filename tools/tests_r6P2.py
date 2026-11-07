#!/usr/bin/env python3
"""第 6 轮 P2/P3 的测试：Codex 复核 P 打回的两个复现（原样）、补写、rev 单调、关 Artifact 计入总数、一直显示、耗时；
P3：Codex 复核 P2 打回的「设置停用后旧记录不撤销」（原样复现 + 其它会话 + test_filter 事件消失）。

用法：
  python3 tools/tests_r6P2.py
  R6P_STATUSLINE=路径 python3 tools/tests_r6P2.py     # 测指定的状态栏副本
隔离：沿用 tests_r6P.py 的 Box（state/_test_tmp/ 下的临时目录：假 state、假 HOME、套件副本），不碰真实 state 和 ~/.claude。
"""
import json, os, subprocess, sys, time, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tests_r6P as T          # noqa: E402
SM, PR, KIT = T.SM, T.PR, T.KIT

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def append(path, rows):
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def ledger(B, sid=None):
    return SM.totals_from_jsonl(os.path.join(B.state, "savings.jsonl"), sid)


def status_cli(B):
    p = subprocess.run([sys.executable, os.path.join(B.suite, "adapters", "common", "usaver_status.py"), "--tool", "claude", "--any"],
                       env=B.env(CLAUDE_CONFIG_DIR=os.path.join(B.home, ".claude")), capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout.strip(), p.stderr


def box():
    """Box + usaver_status 的副本（U 类用例要用套件副本里的 adapters，读的是副本的 hooks）。"""
    B = T.Box()
    import shutil
    d = os.path.join(B.suite, "adapters", "common")
    os.makedirs(d, exist_ok=True)
    shutil.copy(os.path.join(KIT, "adapters", "common", "usaver_status.py"), d)
    return B


def pct_of(out):
    import re
    m = re.search(r"省≈([\d.]+)%累计 本会话([\d.]+)%", out)
    return (float(m.group(1)), float(m.group(2))) if m else None


AFTER = "2026-11-07T09:00:00Z"            # enableArtifact=false 生效（07:42Z）之后
BEFORE = "2026-11-07T07:00:00Z"
MAIN_TOOLS = ["Agent", "AskUserQuestion", "Bash", "Edit", "Read", "Write", "Skill", "ToolSearch"]
SUB_TOOLS = ["Agent", "Bash", "Edit", "Read", "Write", "Skill", "SubagentHandback", "ToolSearch"]
EXPLORE_TOOLS = ["Bash", "Read", "Skill", "ToolSearch"]


def shape(names, ts=AFTER, agent=None):
    r = {"type": "api-request-shape", "sessionId": "x", "shapeHash": str(hash(tuple(names))), "timestamp": ts, "version": "2.1.319",
         "shape": {"system": [{"type": "text", "text": "s" * 9000}], "tools": [{"name": n, "description": "d", "input_schema": {}} for n in names]}}
    if agent:
        r["agentId"] = agent
    return r


def per(kind, model="claude-sonnet-5-5"):
    return PR.per_token(model, kind)


# ------------------------------------------------------------------ Codex 复核 P 的两个复现（原样）
@case("C1 Codex 复现 1：本次节省约 1.7k（<2000）时，状态栏 43.0%，账本（usaver-status 读的）也要是 43.0%，不能停在 0.0%")
def _():
    B = box()
    try:
        log = T.make_log(B, "incremental-log", 100)
        tp = B.jl("incremental.jsonl", [T.asst(cr=10000, cw5m=100), T.tool_result(T.tf_text(log, 100))])
        p = T.payload("incremental", tp)
        first = B.sl(p)[1]
        append(tp, [T.asst(cr=10000, cw5m=100)])
        after = B.sl(p)[1]
        for _ in range(5):
            B.sl(p)
        L = ledger(B, "incremental")
        rc, cli, err = status_cli(B)
        print("    状态栏：%s → %s；账本 cum=%.1f%% sess=%.1f%% saved=%.0f；usaver-status：%s" % (
            pct_of(first), pct_of(after), L["cum_pct"], L["sess_pct"], L["cum_saved"], pct_of(cli)))
        assert pct_of(first) == (0.0, 0.0) and abs(pct_of(after)[0] - 43.0) < 0.6, (first, after)
        assert 0 < L["cum_saved"] < 2000                   # 正是「变化不到 2000 当量 token」的情形
        assert round(L["cum_pct"], 1) == pct_of(after)[0] and round(L["sess_pct"], 1) == pct_of(after)[1], (L, after)
        assert rc == 0 and pct_of(cli) == pct_of(after), (cli, err)
        return True
    finally:
        B.done()


@case("C2 Codex 复现 2：进度文件丢了重建后，rev 仍比旧行大；账本跟着实时值（5.7%），不停在旧值 17.2%")
def _():
    B = box()
    try:
        log = T.make_log(B, "rebuild-log", 300)
        tp = B.jl("rebuild.jsonl", [T.asst(cr=10000, cw5m=100), T.tool_result(T.tf_text(log, 300))])
        p = T.payload("rebuild", tp)
        B.sl(p)
        for i in range(4):
            append(tp, [T.asst(cr=100, cw5m=20000)])
            B.sl(p)
        before = ledger(B, "rebuild")
        os.rename(os.path.join(B.state, "savings", "rebuild.json"), os.path.join(B.state, "rebuild.saved"))
        B.sl(p)
        append(tp, [{"type": "system", "subtype": "compact_boundary"}, T.asst(cr=100, cw5m=200000)])
        out = B.sl(p)[1]
        L = ledger(B, "rebuild")
        rc, cli, _ = status_cli(B)
        print("    重建前账本 %.1f%%；重建后实时 %s；账本 %.1f%%；usaver-status %s" % (before["cum_pct"], pct_of(out), L["cum_pct"], pct_of(cli)))
        assert abs(before["cum_pct"] - 17.2) < 0.3 and abs(pct_of(out)[0] - 5.7) < 0.3, (before, out)
        assert round(L["cum_pct"], 1) == pct_of(out)[0] and pct_of(cli) == pct_of(out), (L, out, cli)
        return True
    finally:
        B.done()


@case("C3 rev 单调：旧版本写的 rev=1,2,3 的行、同一毫秒内连写多次、进度重建，合并时都取到最新值；rev 相同取文件里靠后的行")
def _():
    B = T.Box()
    try:
        jp = os.path.join(B.state, "savings.jsonl")
        with open(jp, "w") as f:
            for rev, v in ((1, 10.0), (2, 20.0), (3, 30.0)):
                f.write(json.dumps({"event_id": "actual:s", "rev": rev, "session_id": "s", "counted": True,
                                    "mechanism": "actual", "actual_weighted_tokens": v, "saved_weighted_tokens": 0}) + "\n")
        r1 = SM._next_rev(0)
        assert r1 > 3 and SM._next_rev(r1) > r1 and SM._next_rev(0) > r1      # 重建（prev 未知 = 0）也不会倒退
        with open(jp, "a") as f:
            f.write(json.dumps({"event_id": "actual:s", "rev": r1, "session_id": "s", "counted": True, "mechanism": "actual",
                                "actual_weighted_tokens": 5.0, "saved_weighted_tokens": 0}) + "\n")
            f.write(json.dumps({"event_id": "actual:s", "rev": r1, "session_id": "s", "counted": True, "mechanism": "actual",
                                "actual_weighted_tokens": 6.0, "saved_weighted_tokens": 0}) + "\n")
        return SM.read_jsonl(jp)["actual:s"]["actual_weighted_tokens"] == 6.0
    finally:
        B.done()


@case("C4 补写：会话最后一次请求之后状态栏没再刷新；别的会话下一次刷新（隔 ≥30 秒）替它补进账本")
def _():
    B = T.Box()
    try:
        log = T.make_log(B, "cu-log", 200)
        tx = B.jl("X.jsonl", [T.asst(cr=10000, cw5m=100), T.tool_result(T.tf_text(log, 200), "tx")])
        ty = B.jl("Y.jsonl", [T.asst(cr=5000, cw5m=100)])
        now = time.time()
        SM.update({"session_id": "X", "transcript_path": tx, "cwd": "/x"}, B.state, now=now)
        append(tx, [T.asst(cr=12000, cw5m=200), T.asst(cr=13000, cw5m=200)])   # X 的最后两次请求：之后 X 的状态栏没再跑
        stale = ledger(B, "X")["sess_pct"]
        SM.update({"session_id": "Y", "transcript_path": ty, "cwd": "/x"}, B.state, now=now + 5)    # <30 秒：不补
        assert ledger(B, "X")["sess_pct"] == stale
        SM.update({"session_id": "Y", "transcript_path": ty, "cwd": "/x"}, B.state, now=now + 40)   # ≥30 秒：补
        ref = SM.offline([tx])[0]
        L = ledger(B, "X")
        print("    X 账本：补写前 %.1f%% → 补写后 %.1f%%（离线全量 %.1f%%）" % (stale, L["sess_pct"], SM.pct(ref["saved"], ref["actual"])))
        assert stale == 0.0 and abs(L["sess_pct"] - SM.pct(ref["saved"], ref["actual"])) < 0.05, (stale, L, ref)
        # 状态栏路径（子进程）也一样：把 sweep_ts 拨回去，跑一次 Y 的状态栏
        append(tx, [T.asst(cr=100, cw5m=50000)])
        tot = os.path.join(B.state, "savings_totals.json")
        Tt = json.load(open(tot)); Tt["sweep_ts"] = 0; json.dump(Tt, open(tot, "w"))
        B.sl(T.payload("Y", ty))
        ref = SM.offline([tx])[0]
        return abs(ledger(B, "X")["sess_pct"] - SM.pct(ref["saved"], ref["actual"])) < 0.05
    finally:
        B.done()


@case("C5 补写也接旧版本：v1 进度文件（没有设置类状态）+ 合计里有它 → 第一次补写建索引并重算；--session-end 不限时补算一次")
def _():
    B = T.Box()
    try:
        tz = B.jl("Z.jsonl", [shape(MAIN_TOOLS), T.asst(cr=30000, cw1h=500, ts=AFTER)])
        os.makedirs(os.path.join(B.state, "savings"), exist_ok=True)
        json.dump({"v": 1, "files": {}, "cwd": "/x", "tp": tz}, open(os.path.join(B.state, "savings", "Z.json"), "w"))
        json.dump({"sessions": {"Z": [100.0, 0.0, 0.0, int(time.time()), 0]}}, open(os.path.join(B.state, "savings_totals.json"), "w"))
        ty = B.jl("Y.jsonl", [T.asst(cr=5000, cw5m=100)])
        SM.update({"session_id": "Y", "transcript_path": ty, "cwd": "/x"}, B.state)
        L = ledger(B, "Z")
        assert L and L["sess_pct"] > 0 and (L["sess_by_mech"] or {}).get("setting:artifact_off", 0) > 0, L
        # --session-end：会话结束时补写（hook 由所有者决定挂不挂）
        tw = B.jl("W.jsonl", [shape(MAIN_TOOLS), T.asst(cr=30000, cw1h=500, ts=AFTER)])
        p = subprocess.run([sys.executable, os.path.join(B.suite, "hooks", "savings_meter.py"), "--session-end"],
                           input=json.dumps({"session_id": "W", "transcript_path": tw, "cwd": "/x", "hook_event_name": "SessionEnd"}),
                           env=B.env(), capture_output=True, text=True, timeout=30)
        bad = subprocess.run([sys.executable, os.path.join(B.suite, "hooks", "savings_meter.py"), "--session-end"],
                             input="{坏", env=B.env(), capture_output=True, text=True, timeout=30)
        return p.returncode == 0 and ledger(B, "W")["sess_pct"] > 0 and bad.returncode == 0 and "Traceback" not in bad.stderr
    finally:
        B.done()


# ------------------------------------------------------------------ 关掉 Artifact 计入总数
def _art_state(B, sid):
    st = T.sess_state(B, sid)
    return SM.summarize(st)


@case("A1 生效前：工具表里没有 Artifact，但请求在 2026-11-07T07:42Z（所有者改设置）之前 → 不计；工具表里有 Artifact → 不计")
def _():
    B = T.Box()
    try:
        t1 = B.jl("b1.jsonl", [shape(MAIN_TOOLS, ts=BEFORE), T.asst(cr=30000, cw1h=500, ts=BEFORE), T.asst(cr=31000, cw1h=500, ts=BEFORE)])
        t2 = B.jl("b2.jsonl", [shape(MAIN_TOOLS + ["Artifact"]), T.asst(cr=30000, cw1h=500, ts=AFTER)])
        r1 = SM.update({"session_id": "b1", "transcript_path": t1, "cwd": "/x"}, B.state)
        r2 = SM.update({"session_id": "b2", "transcript_path": t2, "cwd": "/x"}, B.state)
        s1, s2 = _art_state(B, "b1"), _art_state(B, "b2")
        assert r1["sess_pct"] == 0.0 and r2["sess_pct"] == 0.0, (r1, r2)
        return not s1["settings"] and not s2["settings"]
    finally:
        B.done()


@case("A2 生效后（主会话）：每次请求 Δ=13,421 × 单价——前缀写缓存（cache_read 很小）的按 1h/5m 写入价，其余按读价；账本有 setting:artifact_off 记录")
def _():
    B = T.Box()
    try:
        reqs = [T.asst(cr=0, cw1h=30000, ts=AFTER),            # 首请求：前缀写入（1h 档）
                T.asst(cr=30000, cw1h=800, ts=AFTER),          # 热：前缀读（本身有少量新消息写入，不算前缀写）
                T.asst(cr=31000, cw1h=200, ts=AFTER),          # 热
                T.asst(cr=0, cw5m=32000, ts=AFTER)]            # 冷了、5m 档：前缀重写
        tp = B.jl("a2.jsonl", [shape(MAIN_TOOLS)] + reqs)
        r = SM.update({"session_id": "a2", "transcript_path": tp, "cwd": "/x"}, B.state)
        exp = 13421 * (per("cw1h") + 2 * per("cr") + per("cw5m"))
        s = _art_state(B, "a2")
        got = s["settings"]["artifact_off"]
        actual = sum(PR.weighted("claude-sonnet-5-5", a["message"]["usage"])[0] for a in reqs)
        print("    节省 %.0f 当量 token（预期 %.0f），实际 %.0f，本会话 %.1f%%" % (got["saved"], exp, actual, r["sess_pct"]))
        assert abs(got["saved"] - exp) < 1e-6 * exp and got["n"] == 4 and got["n_write"] == 2, (got, exp)
        assert abs(r["sess_pct"] - 100 * exp / (actual + exp)) < 1e-6, r
        rec = [v for k, v in SM.read_jsonl(os.path.join(B.state, "savings.jsonl")).items() if k.startswith("set:artifact_off:")]
        assert rec and rec[0]["counted"] is True and rec[0]["mechanism"] == "setting:artifact_off" and "round5_A" in rec[0]["source"]
        L = ledger(B, "a2")
        return abs(L["sess_pct"] - r["sess_pct"]) < 0.01
    finally:
        B.done()


@case("A3 子 agent：通用子 agent（有 Write/Edit、没 Artifact）按 Δ=13,140 计；只读子 agent（Explore 类，本来就没有 Artifact）不计；带 Artifact 的不计")
def _():
    B = T.Box()
    try:
        tp = B.jl("a3.jsonl", [shape(MAIN_TOOLS + ["Artifact"], ts=AFTER), T.asst(cr=30000, cw1h=500, ts=AFTER)])   # 主会话开始于改设置前
        sub = os.path.join("a3", "subagents")
        B.jl("agent-g.jsonl", [shape(SUB_TOOLS), T.asst(cr=0, cw5m=16000, ts=AFTER), T.asst(cr=16000, cw5m=300, ts=AFTER)], sub=sub)
        B.jl("agent-e.jsonl", [shape(EXPLORE_TOOLS), T.asst(cr=0, cw5m=8000, ts=AFTER), T.asst(cr=8000, cw5m=300, ts=AFTER)], sub=sub)
        B.jl("agent-o.jsonl", [shape(SUB_TOOLS + ["Artifact"]), T.asst(cr=0, cw5m=30000, ts=AFTER)], sub=sub)
        SM.update({"session_id": "a3", "transcript_path": tp, "cwd": "/x"}, B.state)
        got = _art_state(B, "a3")["settings"]["artifact_off"]
        exp = 13140 * (per("cw5m") + per("cr"))
        print("    子 agent 节省 %.0f（预期 %.0f），主 %d 次 / 子 %d 次请求计入" % (got["saved"], exp, got["n"], got["n_sub"]))
        return abs(got["saved"] - exp) < 1e-6 * exp and got["n"] == 0 and got["n_sub"] == 2
    finally:
        B.done()


@case("A4 没有缓存写的会话：全是缓存读 → 每次都按读价；完全没用缓存（只有 input）→ 按输入价；Opus 的单价是 Sonnet 的 2 倍")
def _():
    B = T.Box()
    try:
        t1 = B.jl("a4.jsonl", [shape(MAIN_TOOLS)] + [T.asst(cr=40000 + i, cw5m=0, ts=AFTER) for i in range(5)])
        t2 = B.jl("a5.jsonl", [shape(MAIN_TOOLS), T.asst(inp=30000, cr=0, cw5m=0, ts=AFTER, model="claude-opus-5-5")])
        SM.update({"session_id": "a4", "transcript_path": t1, "cwd": "/x"}, B.state)
        SM.update({"session_id": "a5", "transcript_path": t2, "cwd": "/x"}, B.state)
        g1 = _art_state(B, "a4")["settings"]["artifact_off"]
        g2 = _art_state(B, "a5")["settings"]["artifact_off"]
        assert abs(g1["saved"] - 13421 * 5 * per("cr")) < 1e-6 and g1["n_write"] == 0, g1
        assert abs(g2["saved"] - 13421 * per("inp", "claude-opus-5-5")) < 1e-6 and g2["n_inp"] == 1, g2
        return abs(per("inp", "claude-opus-5-5") - 2 * per("inp")) < 1e-12
    finally:
        B.done()


@case("A5 工具表中途变化：先有 Artifact、后来没有 → 只算变化之后的请求；`claude -p` 形状（没有 AskUserQuestion）不计；没有 shape 记录的不计")
def _():
    B = T.Box()
    try:
        tp = B.jl("a6.jsonl", [shape(MAIN_TOOLS + ["Artifact"]), T.asst(cr=30000, cw1h=500, ts=AFTER),
                               shape(MAIN_TOOLS), T.asst(cr=30000, cw1h=500, ts=AFTER), T.asst(cr=31000, cw1h=500, ts=AFTER)])
        tq = B.jl("a7.jsonl", [shape([t for t in MAIN_TOOLS if t != "AskUserQuestion"]), T.asst(cr=30000, cw1h=500, ts=AFTER)])
        tn = B.jl("a8.jsonl", [T.asst(cr=30000, cw1h=500, ts=AFTER)])
        for sid, p in (("a6", tp), ("a7", tq), ("a8", tn)):
            SM.update({"session_id": sid, "transcript_path": p, "cwd": "/x"}, B.state)
        g = _art_state(B, "a6")["settings"]["artifact_off"]
        assert g["n"] == 2 and abs(g["saved"] - 13421 * 2 * per("cr")) < 1e-6, g
        return not _art_state(B, "a7")["settings"] and not _art_state(B, "a8")["settings"]
    finally:
        B.done()


@case("A6 Δ 可配置：config.json 的 savings_meter.settings.artifact_off.delta_main 改了立即生效（不用重算）；enabled=false 就不计")
def _():
    B = T.Box()
    try:
        tp = B.jl("a9.jsonl", [shape(MAIN_TOOLS), T.asst(cr=30000, cw1h=500, ts=AFTER)])
        SM.update({"session_id": "a9", "transcript_path": tp, "cwd": "/x"}, B.state)
        base = _art_state(B, "a9")["settings"]["artifact_off"]["saved"]
        cfgp = os.path.join(B.suite, "config.json")
        c = json.load(open(cfgp)); c["savings_meter"] = {"settings": {"artifact_off": {"delta_main": 26842}}}; json.dump(c, open(cfgp, "w"))
        code = ("import sys,json;sys.path.insert(0,%r);import savings_meter as SM;"
                "print(json.dumps(SM.summarize(json.load(open(%r)))['settings']))") % (
            os.path.join(B.suite, "hooks"), os.path.join(B.state, "savings", "a9.json"))
        out = json.loads(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30).stdout)
        assert abs(out["artifact_off"]["saved"] - 2 * base) < 1e-6, (out, base)
        c["savings_meter"] = {"settings": {"artifact_off": {"enabled": False}}}; json.dump(c, open(cfgp, "w"))
        out = json.loads(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30).stdout)
        return out == {}
    finally:
        B.done()


@case("A7 通用接口：register_setting 接入另一个经过实测的设置（例：工具表里没有 Foo → Δ=1000），同一套计价，账本 mechanism=setting:foo_off")
def _():
    B = T.Box()
    saved = list(SM.SETTING_SAVINGS)
    try:
        SM.register_setting({"id": "foo_off", "delta_main": 1000, "delta_sub": 500, "since": None, "source": "测试用",
                             "detect": SM._tools_absent_detector("Foo")})
        tp = B.jl("f1.jsonl", [shape(MAIN_TOOLS), T.asst(cr=30000, cw1h=500, ts=AFTER)])
        SM.update({"session_id": "f1", "transcript_path": tp, "cwd": "/x"}, B.state)
        s = _art_state(B, "f1")
        assert abs(s["settings"]["foo_off"]["saved"] - 1000 * per("cr")) < 1e-9, s["settings"]
        assert "setting:foo_off" in ledger(B, "f1")["sess_by_mech"]
        try:
            SM.register_setting({"id": "bad"})
            return False
        except ValueError:
            return True
    finally:
        SM.SETTING_SAVINGS[:] = saved
        SM._settings_cache = None
        B.done()


@case("A8 状态栏端到端：关 Artifact 的会话显示的本会话% = 账本 = usaver-status；usaver-status 带分项（关Artifact x 个百分点）")
def _():
    B = box()
    try:
        tp = B.jl("e1.jsonl", [shape(MAIN_TOOLS), T.asst(cr=0, cw1h=30000, ts=AFTER)] + [T.asst(cr=30000 + 500 * i, cw1h=500, ts=AFTER) for i in range(20)])
        out = B.sl(T.payload("e1", tp))[1]
        rc, cli, err = status_cli(B)
        L = ledger(B, "e1")
        print("    状态栏：%s" % out.split(" | ")[-1])
        print("    usaver-status：%s" % cli.split(" | ")[-1])
        return pct_of(out) and pct_of(out) == pct_of(cli) and round(L["sess_pct"], 1) == pct_of(out)[1] and "关Artifact" in cli
    finally:
        B.done()


# ------------------------------------------------------------------ 一直显示
@case("D1 一直显示（所有者已定）：还没有任何数据 → 状态栏「省≈0.0%累计 本会话0.0%(估·上限)」；usaver-status 有账本就显示（0% 也显示），从没跑过节约表才不显示")
def _():
    B = box()
    try:
        rc, out, err, _ = B.sl(T.payload("n1", "/nonexistent/n1.jsonl"))
        assert rc == 0 and out.endswith("| 省≈0.0%累计 本会话0.0%(估·上限)"), out
        tp = B.jl("n2.jsonl", [{"type": "user", "message": {"role": "user", "content": "hi"}}])
        rc, out, err, _ = B.sl(T.payload("n2", tp))
        assert out.endswith("| 省≈0.0%累计 本会话0.0%(估·上限)"), out
        rc, cli, err = status_cli(B)                      # 账本存在、节省为 0 → usaver-status 也显示 0.0%
        assert rc == 0 and "省≈0.0%累计 本会话0.0%(估·上限)" in cli, (cli, err)
        os.replace(os.path.join(B.state, "savings.jsonl"), os.path.join(B.state, "moved.jsonl"))
        rc, cli, err = status_cli(B)                      # 这台机器从没跑过节约表（没有账本）→ 不显示这一段（adapters 测试的约定）
        return rc == 0 and "省≈" not in cli and "ctx" in cli
    finally:
        B.done()


# ------------------------------------------------------------------ 耗时
@case("T1 状态栏稳态耗时 ≤50ms（中位数）：2MB transcript + 关 Artifact + 索引里 60 个其它会话（补写检查每 30 秒一次，含在内）")
def _():
    B = T.Box()
    try:
        rows = [shape(MAIN_TOOLS)]
        for i in range(1500):
            rows.append({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "f%d" % i, "content": "y" * 900}]}})
            rows.append(T.asst(cr=30000 + i, cw1h=300, ts=AFTER))
        tp = B.jl("big.jsonl", rows)
        for k in range(60):                                # 60 个其它会话（都已追上）
            t = B.jl("o%d.jsonl" % k, [shape(MAIN_TOOLS), T.asst(cr=30000, cw1h=300, ts=AFTER)])
            SM.update({"session_id": "o%d" % k, "transcript_path": t, "cwd": "/x"}, B.state, sweep=False)
        p = T.payload("big", tp)
        B.sl(p); B.sl(p)                                   # 首次解析
        times = []
        for i in range(12):
            if i % 4 == 0:                                 # 每 4 次把 sweep_ts 拨回去，让补写检查也算进稳态
                tot = os.path.join(B.state, "savings_totals.json")
                Tt = json.load(open(tot)); Tt["sweep_ts"] = 0; json.dump(Tt, open(tot, "w"))
            times.append(B.sl(p)[3])
        ms = sorted(round(t * 1000) for t in times)
        print("    transcript %.1fMB；稳态 12 次（ms，排序后）：%s；中位数 %d ms" % (os.path.getsize(tp) / 1e6, ms, ms[6]))
        return ms[6] <= 50
    finally:
        B.done()


# ------------------------------------------------------------------ P3：Codex 复核 P2 打回的一项（设置停用后旧记录不撤销）
def _view(B, tp, sid):
    """Codex independent_checks.py 的 view() 原样：状态栏子进程 + usaver-status 子进程 + 账本。"""
    rc, out, err, _ = B.sl(T.payload(sid, tp))
    assert rc == 0, (out, err)
    rc, cli, err = status_cli(B)
    assert rc == 0, (cli, err)
    L = ledger(B, sid)
    return {"statusline": pct_of(out), "ledger_saved": L["cum_saved"], "ledger_pct": round(L["cum_pct"], 1), "cli": pct_of(cli)}


def _cfg(B, over):
    cfgp = os.path.join(B.suite, "config.json")
    c = json.load(open(cfgp))
    if over is None:
        c.pop("savings_meter", None)
    else:
        c["savings_meter"] = {"settings": {"artifact_off": over}}
    with open(cfgp, "w") as f:
        json.dump(c, f)


def _agree(v, pct):
    return v["statusline"] == (pct, pct) and v["ledger_pct"] == pct and v["cli"] == (pct, pct)


@case("P1 Codex 复现（原样）：关 Artifact 已省 26,842 → config 设 artifact_off.enabled=false → 状态栏、账本、usaver-status 都 0.0%，连刷 5 次不变；再打开 → 都回到 30.7%")
def _():
    B = box()
    try:
        tp = B.jl("disabled.jsonl", [shape(MAIN_TOOLS), T.asst(cr=0, cw1h=30000, ts=AFTER)])
        before = _view(B, tp, "disabled")
        _cfg(B, {"enabled": False})
        after = _view(B, tp, "disabled")
        for _ in range(5):
            after = _view(B, tp, "disabled")
        jp = os.path.join(B.state, "savings.jsonl")
        n1 = sum(1 for _ in open(jp))
        _view(B, tp, "disabled")
        n2 = sum(1 for _ in open(jp))                        # 撤销只写一次，不会每次刷新都追加
        rec = SM.read_jsonl(jp)["set:artifact_off:disabled"]
        assert rec["revoked"] is True and rec["counted"] is False and rec["saved_weighted_tokens"] == 0.0, rec
        _cfg(B, None)
        back = _view(B, tp, "disabled")
        print("    打开 %s → 停用后 %s → 再打开 %s；账本行数 %d→%d" % (before, after, back, n1, n2))
        assert abs(before["ledger_saved"] - 26842) < 1 and _agree(before, 30.7), before
        assert _agree(after, 0.0) and after["ledger_saved"] == 0.0, after
        assert n1 == n2, (n1, n2)
        return _agree(back, 30.7) and abs(back["ledger_saved"] - 26842) < 1
    finally:
        B.done()


@case("P2 Codex 复现（原样）：config 把 since 改到 2027 并删掉进度文件重建 → 三处都 0.0%；只改 since、不动进度文件 → 也自动重算并撤销")
def _():
    B = box()
    try:
        tp = B.jl("recomputed.jsonl", [shape(MAIN_TOOLS), T.asst(cr=0, cw1h=30000, ts=AFTER)])
        before = _view(B, tp, "recomputed")
        _cfg(B, {"since": "2027-01-01T00:00:00Z"})
        os.rename(os.path.join(B.state, "savings", "recomputed.json"), os.path.join(B.state, "recomputed.saved"))
        after = _view(B, tp, "recomputed")
        tq = B.jl("q.jsonl", [shape(MAIN_TOOLS), T.asst(cr=0, cw1h=30000, ts=AFTER)])
        _cfg(B, None)
        b2 = _view(B, tq, "q")
        _cfg(B, {"since": "2027-01-01T00:00:00Z"})
        a2 = _view(B, tq, "q")
        print("    删进度：%s → %s；不删进度：%s → %s" % (before, after, b2, a2))
        assert _agree(before, 30.7) and _agree(after, 0.0) and after["ledger_saved"] == 0.0, (before, after)
        return b2["statusline"][1] == 30.7 and a2["statusline"][1] == 0.0 and a2["ledger_saved"] == 0.0 and a2["cli"][1] == 0.0
    finally:
        B.done()


@case("P3 其它会话（含 3 天前的）：停用后由补写重算并撤销；补写后状态栏累计 = 账本 = usaver-status；补写之前两边也一致（都是旧值）")
def _():
    B = box()
    try:
        tx = B.jl("X.jsonl", [shape(MAIN_TOOLS), T.asst(cr=0, cw1h=30000, ts=AFTER)])
        ty = B.jl("Y.jsonl", [shape(MAIN_TOOLS), T.asst(cr=0, cw5m=20000, ts=AFTER), T.asst(cr=20000, cw5m=300, ts=AFTER)])
        now = time.time()
        SM.update({"session_id": "X", "transcript_path": tx, "cwd": "/x"}, B.state, now=now - 5 * 86400, sweep=False)
        _view(B, ty, "Y")
        _cfg(B, {"enabled": False})
        tot = os.path.join(B.state, "savings_totals.json")
        Tt = json.load(open(tot)); Tt["sweep_ts"] = int(time.time()); json.dump(Tt, open(tot, "w"))   # 先不让补写跑
        mid = _view(B, ty, "Y")
        Lx = ledger(B, "X")
        Tt = json.load(open(tot)); Tt["sweep_ts"] = 0; json.dump(Tt, open(tot, "w"))
        after = _view(B, ty, "Y")
        Lx2 = ledger(B, "X")
        print("    补写前：Y %s，X 账本 %.1f%%；补写后：%s，X 账本 %.1f%%" % (mid, Lx["sess_pct"], after, Lx2["sess_pct"]))
        assert mid["statusline"][1] == 0.0 and mid["statusline"][0] == mid["ledger_pct"] == mid["cli"][0] > 0, mid   # 未补写：X 的旧值两边都还在
        return _agree(after, 0.0) and Lx2["sess_pct"] == 0.0
    finally:
        B.done()


@case("P4 test_filter 事件重算后不再产生（transcript 被改写）→ 撤销；没变化的事件、Δ 改动不会被误撤销")
def _():
    B = box()
    try:
        log = T.make_log(B, "tf-log", 200)
        tp = B.jl("tf.jsonl", [T.asst(cr=10000, cw5m=100), T.tool_result(T.tf_text(log, 200), "t1"), T.asst(cr=10000, cw5m=100)])
        before = _view(B, tp, "tf")
        _cfg(B, {"delta_main": 26842})
        same = _view(B, tp, "tf")
        rows_tf = [r for r in SM.read_jsonl(os.path.join(B.state, "savings.jsonl")).values() if r["mechanism"] == "test_filter"]
        assert rows_tf and not any(r.get("revoked") for r in rows_tf) and same == before, (same, before)
        with open(tp, "w") as f:                               # transcript 被改写（比原来短）：进度按截断处理会重算
            f.write(json.dumps(T.asst(cr=10000, cw5m=100)) + "\n")
        os.rename(os.path.join(B.state, "savings", "tf.json"), os.path.join(B.state, "tf.saved"))
        after = _view(B, tp, "tf")
        print("    %s → %s" % (before, after))
        return before["statusline"][1] > 0 and _agree(after, 0.0)
    finally:
        B.done()


def main():
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
