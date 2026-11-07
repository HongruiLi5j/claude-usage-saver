#!/usr/bin/env python3
"""第 6 轮 M（适应新模型）的测试：context_guard 按窗口比例收紧、未知模型、usage 读不到、Pro 封顶；model-router 静态检查。

每个用例在临时套件目录里跑（复制被测的 context_guard.py + tools/project_profile.py，config.json 由用例写），
state 用 USAGE_SAVER_STATE 指到临时目录，HOME 也指到临时目录，不碰真实 state 和 ~/.claude。

用法：python3 tools/tests_r6M.py [--guard 被测 context_guard.py] [--skill 被测 SKILL.md]
默认被测：本套件 hooks/context_guard.py、~/.claude/skills/model-router/SKILL.md（只读）。
退出码：全过 0，否则 1。最后一行「R6M 测试：x/y 通过」。
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, time

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(KIT, "hooks", "context_guard.py")
SKILL = os.path.expanduser("~/.claude/skills/model-router/SKILL.md")
BASE_CFG = {"handoff_threshold_tokens": 160000, "strong_threshold_tokens": 300000,
            "per_project_threshold_tokens": {"quant-backtest": 250000},
            "max_soft_line_tokens": 180000, "max_strong_line_tokens": 195000}
CASES = []


def case(cid, desc):
    def deco(fn):
        CASES.append((cid, desc, fn))
        return fn
    return deco


class Env:
    """一个临时套件：kit/hooks/context_guard.py、kit/tools/project_profile.py、kit/config.json、state/、home/、projects/。"""

    def __init__(self, cfg=None):
        self.root = tempfile.mkdtemp(prefix="r6M_")
        self.kit = os.path.join(self.root, "kit")
        os.makedirs(os.path.join(self.kit, "hooks")); os.makedirs(os.path.join(self.kit, "tools"))
        shutil.copy(GUARD, os.path.join(self.kit, "hooks", "context_guard.py"))
        pp = os.path.join(KIT, "tools", "project_profile.py")
        if os.path.isfile(pp):
            shutil.copy(pp, os.path.join(self.kit, "tools", "project_profile.py"))
        self.state = os.path.join(self.root, "state"); os.makedirs(self.state)
        self.home = os.path.join(self.root, "home"); os.makedirs(self.home)
        self.set_cfg(**(cfg or {}))

    def set_cfg(self, **kw):
        c = dict(BASE_CFG); c.update(kw)
        c = {k: v for k, v in c.items() if v is not None}
        json.dump(c, open(os.path.join(self.kit, "config.json"), "w"))

    def proj(self, name):
        p = os.path.join(self.root, "projects", name); os.makedirs(p, exist_ok=True)
        return p

    def env(self):
        e = dict(os.environ, USAGE_SAVER_STATE=self.state, HOME=self.home)
        e.pop("PYTHONPATH", None)
        return e

    def run(self, args, stdin=""):
        p = subprocess.run(args, input=stdin, capture_output=True, text=True, env=self.env(), timeout=30)
        return p.returncode, p.stdout, p.stderr

    def tp(self, rows, name="t.jsonl"):
        p = os.path.join(self.root, name)
        with open(p, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        return p

    def guard(self, payload):
        """跑守卫，返回 (提醒等级 0/1/2, 文本, mode.json)。等级：0 不提醒、1 软、2 强。"""
        rc, out, err = self.run([sys.executable, os.path.join(self.kit, "hooks", "context_guard.py")], json.dumps(payload))
        if rc != 0 or "Traceback" in err:
            raise AssertionError(f"守卫退出码 {rc}：{err.strip()[-300:]}")
        msg = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""
        lv = 2 if "上下文已很长" in msg else 1 if "[上下文守卫]" in msg else 0
        try:
            m = json.load(open(os.path.join(self.state, "mode.json")))
        except Exception:
            m = {}
        return lv, msg, m

    def lines(self, cwd, mult=1.0, window=None, assumed=None, today=None):
        """在子进程里 import 被测 context_guard 调 lines()；today 用 'YYYY-MM-DD'。"""
        kw = []
        if window is not None:
            kw.append(f"window={window!r}")
        if assumed is not None:
            kw.append(f"assumed={assumed!r}")
        if today:
            kw.append("today=dt.date(%s)" % ",".join(str(int(x)) for x in today.split("-")))
        code = ("import sys,json,datetime as dt; sys.path.insert(0,%r); import context_guard as G; "
                "print(json.dumps(G.lines(%r,%r%s)))" % (os.path.join(self.kit, "hooks"), cwd, mult,
                                                         "".join("," + k for k in kw)))
        rc, out, err = self.run([sys.executable, "-c", code])
        if rc != 0:
            raise AssertionError(err.strip()[-300:])
        return json.loads(out)

    def put_window(self, sid, w):
        p = os.path.join(self.state, "ctx_window.json")
        try:
            cw = json.load(open(p))
        except Exception:
            cw = {}
        cw[sid] = {"w": w, "ts": int(time.time())}
        json.dump(cw, open(p, "w"))

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)   # 只删本测试自己建的临时目录


def asst(n, model="claude-opus-5-5", usage=None):
    msg = {"role": "assistant", "content": [{"type": "text", "text": "ok"}],
           "usage": usage if usage is not None else {"input_tokens": 10, "cache_read_input_tokens": n - 10,
                                                      "cache_creation_input_tokens": 0, "output_tokens": 5}}
    if model is not None:
        msg["model"] = model
    return {"type": "assistant", "message": msg}


USER = {"type": "user", "message": {"role": "user", "content": "hi"}}


def pl(E, rows, sid="S", cwd=None, **extra):
    d = {"hook_event_name": "UserPromptSubmit", "session_id": sid, "cwd": cwd or E.proj("a"),
         "transcript_path": E.tp([USER] + rows), "prompt": "hi"}
    d.update(extra)
    return d


# ---------------------------------------------------------------- 窗口 200k / 300k / 1M / 缺失
@case("W01", "窗口 200k（状态栏记在 ctx_window.json）：软线 120k、强线 160k；165k 发强提醒（自动压缩约在 167k）")
def _(E):
    E.put_window("S", 200000); E.put_window("S2", 200000)
    a = E.lines(E.proj("a"), window=200000)
    lv, _, m = E.guard(pl(E, [asst(165_000)]))
    lv2, _, _ = E.guard(pl(E, [asst(130_000)], sid="S2"))
    return (a[:2] == [120000, 160000] and lv == 2 and m["window"] == 200000 and m["window_source"] == "statusline"
            and lv2 == 1), (a, lv, m.get("window_source"), lv2)


@case("W02", "窗口 300k：软线 160k 不变（< 180k）、强线 300k → 240k；200k 软、250k 强")
def _(E):
    E.put_window("S", 300000)
    a = E.lines(E.proj("a"), window=300000)
    lv1 = E.guard(pl(E, [asst(200_000)], sid="S"))[0]
    lv2 = E.guard(pl(E, [asst(250_000)], sid="S"))[0]
    return a[:2] == [160000, 240000] and lv1 == 1 and lv2 == 2, (a, lv1, lv2)


@case("W03", "窗口 1M：阈值不变（160k / 300k），250k 软提醒，说明里不写「收紧」")
def _(E):
    E.put_window("S", 1_000_000)
    a = E.lines(E.proj("a"), window=1_000_000)
    lv, msg, m = E.guard(pl(E, [asst(250_000)]))
    return a[:2] == [160000, 300000] and "收紧" not in a[2] and lv == 1 and m["window"] == 1_000_000, (a, lv, m)


@case("W04", "窗口从 hook payload 的 context_window.context_window_size 拿（没有 ctx_window.json）：200k → 165k 强提醒，来源 payload")
def _(E):
    lv, _, m = E.guard(pl(E, [asst(165_000)], context_window={"context_window_size": 200000}))
    return lv == 2 and m["window_source"] == "payload", (lv, m)


@case("W05", "窗口缺失、模型已知：按 200k 假设——软线 150k、强线 160k；155k 软、165k 强；180k（已超假设窗口 85%）不再收紧")
def _(E):
    r = [E.guard(pl(E, [asst(n)], sid="X%d" % n)) for n in (140_000, 155_000, 165_000, 180_000)]
    lv = [x[0] for x in r]
    m155, m180 = r[1][2], r[3][2]
    return (lv == [0, 1, 2, 1] and m155["window_source"] == "assumed" and m155["soft_line"] == 150000
            and m155["strong_line"] == 160000 and "假设" in m155["why"]
            and m180["window_source"] == "evidence" and m180["soft_line"] == 160000), (lv, m155, m180)


@case("W06", "窗口缺失、transcript 里没有模型名：不改阈值（160k / 300k），与状态栏 lines(window=None) 一致")
def _(E):
    lv, _, m = E.guard(pl(E, [asst(150_000, model=None)]))
    a, b = E.lines(E.proj("a")), E.lines(E.proj("a"), window=None)
    return (lv == 0 and m["window_source"] == "none" and m["soft_line"] == 160000 and m["strong_line"] == 300000
            and a == b), (lv, m, a, b)


@case("W07", "模型 id 显式写着 [1m]、没有其他窗口来源：按 1M，不按 200k 假设")
def _(E):
    lv, _, m = E.guard(pl(E, [asst(165_000, model="claude-opus-5-5[1m]")]))
    return lv == 1 and m["window"] == 1_000_000 and m["window_source"] == "model-id", (lv, m)


@case("W08", "ctx_window.json 记录超过 7 天：当作没有（按假设 200k），不拿旧窗口")
def _(E):
    json.dump({"S": {"w": 1_000_000, "ts": int(time.time()) - 8 * 86400}}, open(os.path.join(E.state, "ctx_window.json"), "w"))
    lv, _, m = E.guard(pl(E, [asst(165_000)]))
    return lv == 2 and m["window_source"] == "assumed", (lv, m)


# ---------------------------------------------------------------- 未知模型名
@case("U01", "未知模型名 claude-zeta-9、没有窗口：按 200k 假设，165k 强提醒；mode.json 记下模型名")
def _(E):
    lv, _, m = E.guard(pl(E, [asst(165_000, model="claude-zeta-9")]))
    return lv == 2 and m["model"] == "claude-zeta-9" and m["window_source"] == "assumed", (lv, m)


@case("U02", "未知模型、状态栏给了 128k 小窗口：软线 76.8k（低于 100k 下限也以窗口为准）、强线 102.4k；90k 软、105k 强")
def _(E):
    E.put_window("S", 128000)
    a = E.lines(E.proj("a"), window=128000)
    lv1 = E.guard(pl(E, [asst(90_000, model="model-future-1")]))[0]
    lv2 = E.guard(pl(E, [asst(105_000, model="model-future-1")]))[0]
    return a[:2] == [76800, 102400] and lv1 == 1 and lv2 == 2, (a, lv1, lv2)


@case("U03", "payload 里的 model 是 {'id': ...} 字典、transcript 没有 model：也算知道模型 → 按假设窗口")
def _(E):
    lv, _, m = E.guard(pl(E, [asst(165_000, model=None)], model={"id": "claude-new-1", "display_name": "New"}))
    return lv == 2 and m["model"] == "claude-new-1" and m["window_source"] == "assumed", (lv, m)


@case("U04", "只有 <synthetic> 模型（API 报错的占位）：不当作已知模型")
def _(E):
    rows = [asst(150_000, model=None), asst(0, model="<synthetic>", usage={"input_tokens": 0, "output_tokens": 0})]
    lv, _, m = E.guard(pl(E, rows))
    return lv == 0 and m["window_source"] == "none", (lv, m)


# ---------------------------------------------------------------- usage 字段缺失或改名
@case("F01", "usage 字段全改名（prompt_tokens / cached_tokens）：不提醒、mode.json usage_unreadable=true，原因写「不认识」")
def _(E):
    lv, _, m = E.guard(pl(E, [asst(0, usage={"prompt_tokens": 170000, "cached_tokens": 5})]))
    return lv == 0 and m["usage_unreadable"] is True and "不认识" in m["usage_why"] and m["ctx_tokens"] is None, (lv, m)


@case("F02", "assistant 记录没有 usage 字段：usage_unreadable=true")
def _(E):
    row = {"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-5-5", "content": []}}
    lv, _, m = E.guard(pl(E, [row]))
    return lv == 0 and m["usage_unreadable"] is True and "没有 usage" in m["usage_why"], (lv, m)


@case("F03", "transcript 不存在：usage_unreadable=true，退出码 0、不打 traceback")
def _(E):
    d = pl(E, [asst(170_000)]); d["transcript_path"] = os.path.join(E.root, "nope.jsonl")
    lv, _, m = E.guard(d)
    return lv == 0 and m["usage_unreadable"] is True and "不存在" in m["usage_why"], (lv, m)


@case("F04", "别的工具的格式（Codex 式 event_msg / token_count）：usage_unreadable=true，原因写「不是 Claude」")
def _(E):
    row = {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 170000}},
                                            "usage": {"input_tokens": 170000}}}
    lv, _, m = E.guard(pl(E, [row]))
    return lv == 0 and m["usage_unreadable"] is True and "不是 Claude" in m["usage_why"], (lv, m)


@case("F05", "新会话（只有 user 记录）：usage_unreadable=false（这不是读不到），不提醒")
def _(E):
    lv, _, m = E.guard(pl(E, []))
    return lv == 0 and m["usage_unreadable"] is False and "新会话" in m["usage_why"], (lv, m)


@case("F06", "usage 多了不认识的 token 字段：按认识的字段算（170k 软提醒），mode.json 列出 usage_unknown_fields")
def _(E):
    u = {"input_tokens": 10, "cache_read_input_tokens": 169_990, "cache_creation_input_tokens": 0,
         "output_tokens": 3, "cache_read_tokens_v2": 5000}
    lv, _, m = E.guard(pl(E, [asst(0, model=None, usage=u)]))
    return (lv == 1 and m["usage_unreadable"] is False and m["usage_unknown_fields"] == ["cache_read_tokens_v2"]
            and m["ctx_tokens"] == 170000), (lv, m)


@case("F07", "usage 只有 input_tokens（缓存字段缺失）：照常相加，170k 软提醒")
def _(E):
    lv, _, m = E.guard(pl(E, [asst(0, model=None, usage={"input_tokens": 170000})]))
    return lv == 1 and m["usage_unreadable"] is False, (lv, m)


@case("F08", "usage 只有全 0 的 API 报错记录：usage_unreadable=true，原因写「全为 0」")
def _(E):
    lv, _, m = E.guard(pl(E, [asst(0, usage={"input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0})]))
    return lv == 0 and m["usage_unreadable"] is True and "全为 0" in m["usage_why"], (lv, m)


@case("F09", "usage 数值是字符串 / null：不崩溃，按能读的算（cache_read 为 null 时只算 input）")
def _(E):
    u = {"input_tokens": "170000", "cache_read_input_tokens": None, "cache_creation_input_tokens": "x"}
    lv, _, m = E.guard(pl(E, [asst(0, model=None, usage=u)]))
    return lv == 1 and m["ctx_tokens"] == 170000, (lv, m)


# ---------------------------------------------------------------- Pro 封顶（cap_from_date）日期前后
@case("P01", "封顶日 2026-11-21：11-20 不封顶（quant-backtest 250k / 300k），11-21、11-22 封顶（180k / 195k）")
def _(E):
    E.set_cfg(cap_from_date="2026-11-21")
    qb = E.proj("quant-backtest")
    r = [E.lines(qb, window=1_000_000, today=t)[:2] for t in ("2026-11-20", "2026-11-21", "2026-11-22")]
    return r == [[250000, 300000], [180000, 195000], [180000, 195000]], r


@case("P02", "封顶 + 已知 200k 窗口：取更紧的（120k / 160k）；封顶 + 宽裕档 ×1.5：仍封在 180k / 195k")
def _(E):
    E.set_cfg(cap_from_date="2026-11-21")
    qb = E.proj("quant-backtest")
    a = E.lines(qb, window=200000, today="2026-11-22")[:2]
    b = E.lines(qb, mult=1.5, window=1_000_000, today="2026-11-22")[:2]
    return a == [120000, 160000] and b == [180000, 195000], (a, b)


@case("P03", "封顶日之前、已知 200k 窗口：只按窗口收紧（120k / 160k），说明里不提封顶")
def _(E):
    E.set_cfg(cap_from_date="2026-11-21")
    a = E.lines(E.proj("quant-backtest"), window=200000, today="2026-11-20")
    return a[:2] == [120000, 160000] and "200k" in a[2] and "cap" not in a[2], a


@case("P04", "封顶已生效（cap_from_date=2000-01-01）、没有窗口、没有模型名：守卫 190k 软提醒，mode.json 180000 / 195000（原意不变）")
def _(E):
    E.set_cfg(cap_from_date="2000-01-01")
    lv, _, m = E.guard(pl(E, [asst(190_000, model=None)], cwd=E.proj("quant-backtest")))
    return lv == 1 and m["soft_line"] == 180000 and m["strong_line"] == 195000, (lv, m)


@case("P05", "封顶已生效 + 模型已知、窗口缺失、160k–170k：按假设窗口强线 160k 发强提醒（封顶 195k 来不及）")
def _(E):
    E.set_cfg(cap_from_date="2000-01-01")
    lv, _, m = E.guard(pl(E, [asst(165_000)], cwd=E.proj("quant-backtest")))
    return lv == 2 and m["strong_line"] == 160000, (lv, m)


# ---------------------------------------------------------------- 比例配置、接口兼容
@case("R01", "比例配置写错（「abc」、1.5、软 ≥ 强）：回退默认 0.6 / 0.8")
def _(E):
    out = []
    for kw in ({"window_soft_ratio": "abc"}, {"window_strong_ratio": 1.5}, {"window_soft_ratio": 0.9, "window_strong_ratio": 0.8}):
        E.set_cfg(**kw)
        out.append(E.lines(E.proj("a"), window=200000)[:2])
    return out == [[120000, 160000]] * 3, out


@case("R02", "比例可配置：window_soft_ratio=0.7、window_strong_ratio=0.9 → 200k 窗口 140k / 180k")
def _(E):
    E.set_cfg(window_soft_ratio=0.7, window_strong_ratio=0.9)
    a = E.lines(E.proj("a"), window=200000)[:2]
    return a == [140000, 180000], a


@case("R03", "lines() 旧调用方式不变：lines(cwd, mult) == lines(cwd, mult, window=None)；assumed=True 只放松软线")
def _(E):
    c = E.proj("a")
    a, b = E.lines(c, 1.0), E.lines(c, 1.0, window=None)
    k, g = E.lines(c, 1.0, window=200000), E.lines(c, 1.0, window=200000, assumed=True)
    return a == b and k[:2] == [120000, 160000] and g[:2] == [150000, 160000] and "假设" in g[2], (a, b, k, g)


@case("R04", "stdin 不是 JSON 对象（[1,2]、坏 JSON）：退出码 0、无输出、不打 traceback")
def _(E):
    bad = []
    for raw in ("[1, 2]", "{not json", ""):
        rc, out, err = E.run([sys.executable, os.path.join(E.kit, "hooks", "context_guard.py")], raw)
        if rc != 0 or out.strip() or "Traceback" in err:
            bad.append((raw, rc, out[:40], err[-80:]))
    return not bad, bad


# ---------------------------------------------------------------- model-router SKILL.md（静态检查）
def skill_text():
    return open(SKILL, encoding="utf-8").read()


def section(t, title_kw):
    """取标题含 title_kw 的那一节（到下一个 ## 为止）。"""
    m = re.search(r"^## [^\n]*%s[^\n]*\n(.*?)(?=^## |\Z)" % re.escape(title_kw), t, re.S | re.M)
    return m.group(1) if m else ""


@case("S01", "SKILL：分派表按能力档写（最便宜 / 中档 / 最强），表里不出现 haiku / sonnet / opus 别名")
def _(E):
    t = skill_text()
    tbl = section(t, "分派表")
    return (all(k in tbl for k in ("最便宜", "中档", "最强"))
            and not re.search(r"haiku|sonnet|opus|fable", tbl, re.I)), tbl[:200]


@case("S02", "SKILL：别名单独一节，写 haiku / sonnet / opus / fable，并写清未知新模型怎么办")
def _(E):
    t = skill_text()
    al = section(t, "档位与别名")
    return (all(k in al.lower() for k in ("haiku", "sonnet", "opus", "fable")) and "未知" in al), al[:200]


@case("S03", "SKILL：实测数字带模型版本和日期；不再用「<8 万 / ≥8 万」这类绝对上下文值当门槛（举例换算可以）")
def _(E):
    t = skill_text()
    return (not re.search(r"[<≥>]\s*8\s*万", t) and re.search(r"2026-11-0\d", t) is not None
            and re.search(r"(Sonnet|Opus|Haiku)\s*\d", t) is not None), ""


@case("S04", "SKILL：K3 规则——打回/续用看缓存冷热（5 分钟）和老 agent 上下文大小，写了估算公式")
def _(E):
    t = skill_text()
    return ("5 分钟" in t and "SendMessage" in t and "1.25" in t and re.search(r"C\s*[×x*]\s*1\.25", t) is not None
            and "打回清单" in t), ""


def main():
    global GUARD, SKILL
    ap = argparse.ArgumentParser()
    ap.add_argument("--guard", default=GUARD)
    ap.add_argument("--skill", default=SKILL)
    ap.add_argument("--only")
    a = ap.parse_args()
    GUARD, SKILL = os.path.abspath(a.guard), os.path.abspath(a.skill)
    ok = 0
    run = [c for c in CASES if not a.only or a.only in c[0]]
    for cid, desc, fn in run:
        E = Env()
        try:
            r = fn(E)
            passed, detail = (r if isinstance(r, tuple) else (r, ""))
        except Exception as e:
            passed, detail = False, f"{type(e).__name__}: {e}"
        finally:
            E.close()
        ok += bool(passed)
        print(f"{cid}  {'PASS' if passed else 'FAIL'}  {desc}" + ("" if passed else f"\n      → {detail}"))
    print(f"\nR6M 测试：{ok}/{len(run)} 通过")
    sys.exit(0 if ok == len(run) else 1)


if __name__ == "__main__":
    main()
