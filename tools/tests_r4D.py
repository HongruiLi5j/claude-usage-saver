#!/usr/bin/env python3
"""第 4 轮 D 组修复的独立回归用例（不并入 scorecard.py，由别人合并）。

用法：
  python3 tools/tests_r4D.py                    # 跑当前版（hooks/ 与 tools/ 里的文件）
  python3 tools/tests_r4D.py --version pre      # 跑改前版（backups/*.pre-r4D），用来确认用例能复现 bug
  python3 tools/tests_r4D.py --version pre2     # 跑 D2 改前版（backups/*.pre-r4D2），确认 D2 新用例能复现
  python3 tools/tests_r4D.py --keep DIR         # 临时目录放到 DIR 下且不清理，调试用

覆盖三个问题（见 reports/round3_review.md 🟡10、💭11、🟡18）：
  1. statusline / context_guard：已过期的 5 小时窗口被当成新数据 → 误判「快用完」
  2. 项目软线按子串匹配：quant-backtest-old 也命中 quant-backtest 的 250k
  3. weekly_compare：history 同一天去重用 UTC 日期，而报告文件名和分天用本地日期

隔离：每个用例一个临时目录：假 HOME（statusline 的 state 和两边的 config.json 都落在假 HOME 下）、
USAGE_SAVER_STATE 指向假 HOME 下的 state、TZ=America/Los_Angeles。被测脚本只以副本运行，不碰真实 state/ 与 reports/。

临时目录（D2）：优先 $R4D_TMP；否则 tempfile；tempfile 不可用（沙箱里 "No usable temporary directory"）时退回到
项目内 state/_test_tmp/，每个用例用完 mv 到 state/_to_delete/（不 rm，由所有者清理）。
R4D_FORCE_FALLBACK=1 可强制走退回路径（用来自测退回逻辑）。只有 tempfile 建的系统临时目录才用 rmtree 清理。
"""
import argparse, contextlib, json, os, shutil, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = {
    "current": {"statusline": "hooks/statusline.sh", "context_guard": "hooks/context_guard.py",
                "weekly_compare": "tools/weekly_compare.py"},
    "pre": {"statusline": "backups/statusline.sh.pre-r4D", "context_guard": "backups/context_guard.py.pre-r4D",
            "weekly_compare": "backups/weekly_compare.py.pre-r4D"},
    "pre2": {"statusline": "backups/statusline.sh.pre-r4D2", "context_guard": "backups/context_guard.py.pre-r4D2",
             "weekly_compare": "backups/weekly_compare.py.pre-r4D2"},
}
REAL_CFG = os.path.join(ROOT, "config.json")
TZ = "America/Los_Angeles"


class Env:
    def __init__(self, version, tmp, cfg_patch=None):
        self.version, self.tmp = version, tmp
        self.home = os.path.join(tmp, "home")
        self.suite = os.path.join(self.home, "Documents", "Projects", "claude-usage-saver")
        self.state = os.path.join(self.suite, "state")
        for d in (self.state, os.path.join(self.suite, "hooks"), os.path.join(self.suite, "tools"),
                  os.path.join(self.suite, "reports")):
            os.makedirs(d, exist_ok=True)
        cfg = json.load(open(REAL_CFG))
        cfg.pop("cap_from_date", None)        # 默认不封顶，让结果不随运行日期变化；需要封顶的用例自己传
        cfg.update(cfg_patch or {})
        json.dump(cfg, open(os.path.join(self.suite, "config.json"), "w"))

    def script(self, comp, patches=()):
        txt = open(os.path.join(ROOT, SRC[self.version][comp]), encoding="utf-8").read()
        for old, new in patches:
            if old not in txt:
                raise RuntimeError(f"{comp}: 替换目标不存在：{old!r}")
            txt = txt.replace(old, new)
        name = {"statusline": "hooks/statusline.sh", "context_guard": "hooks/context_guard.py",
                "weekly_compare": "tools/weekly_compare.py"}[comp]
        dst = os.path.join(self.suite, name)
        open(dst, "w", encoding="utf-8").write(txt)
        os.chmod(dst, 0o755)
        return dst

    def run(self, argv, stdin=""):
        env = {"HOME": self.home, "USAGE_SAVER_STATE": self.state, "TZ": TZ,
               "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, cwd=self.tmp, env=env, timeout=15)
        return p.returncode, p.stdout, p.stderr

    def proj(self, *names):
        p = os.path.join(self.home, "Documents", "Projects", *names)
        os.makedirs(p, exist_ok=True)
        return p

    def rate_json(self):
        try:
            return json.load(open(os.path.join(self.state, "rate.json")))
        except Exception:
            return {}


def win(used, resets_in_s):
    return {"used_percentage": used, "resets_at": int(time.time() + resets_in_s)}


# ---------------------------------------------------------------- 工具函数
def sl(E, n, cwd=None, rl=None, pre_rate=None):
    E.script("context_guard")     # 当前版 statusline 会 import 它算软线/强线（pre 版不用，复制了也无妨）
    s = E.script("statusline")
    if pre_rate is not None:
        json.dump(pre_rate, open(os.path.join(E.state, "rate.json"), "w"))
    cwd = cwd or E.proj("a")
    payload = {"session_id": "s1", "cwd": cwd, "workspace": {"current_dir": cwd, "project_dir": cwd},
               "model": {"id": "claude-opus", "display_name": "Opus"},
               "context_window": {"current_usage": {"input_tokens": 1000, "cache_read_input_tokens": n - 1000,
                                                    "cache_creation_input_tokens": 0}}}
    if rl is not None:
        payload["rate_limits"] = rl
    rc, out, err = E.run(["/bin/sh", s], json.dumps(payload))
    return out, E.rate_json()


def sl_raw(E, stdin):
    """把任意（可能损坏的）stdin 原样喂给状态栏。返回 (退出码, stdout, stderr)。"""
    E.script("context_guard")
    return E.run(["/bin/sh", E.script("statusline")], stdin)


def cg_mode(E):
    s = E.script("context_guard")
    return E.run([sys.executable, s, "--mode"])[1].strip()


def asst(tokens, side=False):
    return {"type": "assistant", "isSidechain": side, "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1000, "cache_read_input_tokens": tokens - 1000, "cache_creation_input_tokens": 0}}}


def cg(E, ctx_tokens, cwd=None, rate=None, sid=None, extra_rows=()):
    s = E.script("context_guard")
    if rate is not None:
        json.dump(rate, open(os.path.join(E.state, "rate.json"), "w"))
    rows = [{"type": "user", "message": {"role": "user", "content": "hi"}}, asst(ctx_tokens), *extra_rows]
    tp = os.path.join(E.tmp, "t.jsonl")
    open(tp, "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
    payload = {"hook_event_name": "UserPromptSubmit", "transcript_path": tp, "cwd": cwd or E.proj("a"), "prompt": "hi"}
    if sid:
        payload["session_id"] = sid
    rc, out, _ = E.run([sys.executable, s], json.dumps(payload))
    if rc != 0:
        return None
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""


def wc(E, now_expr, history):
    """固定「现在」跑一次 weekly_compare；history 为预置的 weekly_history.json。返回运行后的 history。"""
    pdir = os.path.join(E.home, ".claude", "projects", "-Users-x-proj")
    os.makedirs(pdir, exist_ok=True)
    row = {"type": "assistant", "timestamp": "2026-10-25T20:00:00Z", "isSidechain": False,
           "message": {"id": "m1", "model": "claude-opus-5", "role": "assistant", "content": [],
                       "usage": {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 10000,
                                 "cache_creation_input_tokens": 1000}}}
    open(os.path.join(pdir, "s.jsonl"), "w").write(json.dumps(row) + "\n")
    H = os.path.join(E.suite, "reports", "weekly_history.json")
    json.dump(history, open(H, "w"))
    s = E.script("weekly_compare", [("dt.datetime.now(UTC)", now_expr)])
    rc, out, err = E.run([sys.executable, s])
    if rc != 0:
        raise RuntimeError("weekly_compare 出错：" + err[-300:])
    return json.load(open(H))


# ---------------------------------------------------------------- 用例
CASES = []


def case(cid, desc, cfg_patch=None):
    def deco(fn):
        CASES.append((cid, desc, cfg_patch, fn))
        return fn
    return deco


# ---- 问题 1：过期的 5 小时窗口
@case("SL07", "payload 的 five_hour 已过期（resets_at 1 小时前、95%），rate.json 为空：rate.json 不写入 95%，状态栏不显示 5h 95%")
def _(E):
    out, r = sl(E, 50_000, rl={"five_hour": win(95, -3600), "seven_day": win(50, 86400)})
    fh = (r.get("rate_limits") or {}).get("five_hour") or {}
    sd = (r.get("rate_limits") or {}).get("seven_day") or {}
    return fh.get("used_percentage") != 95 and "5h 95%" not in out and sd.get("used_percentage") == 50


@case("SL07E", "端到端：SL07 之后跑 context_guard --mode，周用量 50%、5h 已过期：不判「快用完」")
def _(E):
    sl(E, 50_000, rl={"five_hour": win(95, -3600), "seven_day": win(50, 86400)})
    m = cg_mode(E)
    return m != "" and not m.startswith("快用完")


@case("SL10", "payload 的 five_hour 已过期，但 rate.json 里有别的会话写的有效 5h 30%：保留 30%")
def _(E):
    pre = {"ts": int(time.time()), "rate_limits": {"five_hour": win(30, 3600), "seven_day": win(50, 86400)}}
    out, r = sl(E, 50_000, rl={"five_hour": win(95, -3600), "seven_day": win(50, 86400)}, pre_rate=pre)
    return ((r.get("rate_limits") or {}).get("five_hour") or {}).get("used_percentage") == 30 and "5h 30%" in out


@case("SL11", "不回归：payload 的 five_hour 没有 resets_at（字段缺失）：照常显示和写入（与 SL09 一致）")
def _(E):
    out, r = sl(E, 50_000, rl={"five_hour": {"used_percentage": 40}, "seven_day": win(50, 86400)})
    return "5h 40%" in out and ((r.get("rate_limits") or {}).get("five_hour") or {}).get("used_percentage") == 40


@case("CG10", "rate.json 新鲜，但 five_hour resets_at 已过、95%；seven_day 50%：不判「快用完」")
def _(E):
    rate = {"ts": int(time.time()), "rate_limits": {"five_hour": win(95, -3600), "seven_day": win(50, 86400)}}
    json.dump(rate, open(os.path.join(E.state, "rate.json"), "w"))
    m = cg_mode(E)
    return m != "" and not m.startswith("快用完")


@case("CG11", "不回归：five_hour 95% 且未过期：仍判「快用完」")
def _(E):
    rate = {"ts": int(time.time()), "rate_limits": {"five_hour": win(95, 3600), "seven_day": win(50, 86400)}}
    json.dump(rate, open(os.path.join(E.state, "rate.json"), "w"))
    return cg_mode(E).startswith("快用完")


# ---- 问题 2：项目软线按路径组件匹配
@case("CG12", "cwd=Projects/quant-backtest-old、200k：不该用 quant-backtest 的 250k，按默认 160k 应提醒")
def _(E):
    return "[上下文守卫]" in (cg(E, 200_000, cwd=E.proj("quant-backtest-old")) or "")


@case("CG13", "不回归：cwd=Projects/quant-backtest/src（项目子目录）、200k：仍按 250k，不提醒")
def _(E):
    return cg(E, 200_000, cwd=E.proj("quant-backtest", "src")) == ""


@case("CG14", "封顶不被破坏：cap_from_date 已到、quant-backtest 190k：软线 250k 被封到 180k，应软提醒（非强提醒）",
      cfg_patch={"cap_from_date": "2000-01-01"})
def _(E):
    msg = cg(E, 190_000, cwd=E.proj("quant-backtest")) or ""
    return "[上下文守卫]" in msg and "软线 180k" in msg and "上下文已很长" not in msg


@case("CG15", "封顶不被破坏：cap_from_date 已到、quant-backtest 196k：强线封到 195k，应强提醒",
      cfg_patch={"cap_from_date": "2000-01-01"})
def _(E):
    return "上下文已很长" in (cg(E, 196_000, cwd=E.proj("quant-backtest")) or "")


@case("SL12", "状态栏：cwd=Projects/quant-backtest-old、200k：按默认软线 160k 显示 ⚠")
def _(E):
    out, _ = sl(E, 200_000, cwd=E.proj("quant-backtest-old"))
    return "⚠" in out


@case("SL13", "不回归：状态栏 cwd=Projects/quant-backtest、200k：按 250k 不显示 ⚠")
def _(E):
    out, _ = sl(E, 200_000, cwd=E.proj("quant-backtest"))
    return "⚠" not in out


# ---- 问题 3：weekly_compare history 去重按本地日期
@case("WC08", "现在 11-08 12:00 PST（UTC 11-08 20:00）；已有 11-07 17:00 PST（UTC 11-08 01:00）一条：本地是不同天，两条都留")
def _(E):
    h = wc(E, "dt.datetime(2026, 11, 8, 20, 0, tzinfo=UTC)",
           [{"ts": "2026-11-08T01:00:00+00:00", "before": {}, "recent": {}}])
    return len(h) == 2


@case("WC09", "现在 11-08 18:00 PST（UTC 11-09 02:00）；已有同一本地日 11-08 14:00 PST（UTC 11-08 22:00）一条：只留最新 1 条")
def _(E):
    h = wc(E, "dt.datetime(2026, 11, 9, 2, 0, tzinfo=UTC)",
           [{"ts": "2026-11-08T22:00:00+00:00", "before": {}, "recent": {}}])
    return len(h) == 1 and h[0]["ts"].startswith("2026-11-09T02:00")


@case("WC10", "不回归：history 里有坏 ts / 无 ts 的旧条目：不报错、保留它们")
def _(E):
    h = wc(E, "dt.datetime(2026, 11, 8, 20, 0, tzinfo=UTC)",
           [{"ts": "garbage", "before": {}, "recent": {}}, {"before": {}, "recent": {}}])
    return len(h) == 3


# ================================================================ Codex 条目（round4_codex_review.md 第 8–11、14、15 条 + 「最值得做」第 3 条）
def put_rate(E, rl, ts_ago_s=0):
    json.dump({"ts": int(time.time() - ts_ago_s), "rate_limits": rl}, open(os.path.join(E.state, "rate.json"), "w"))


@case("CX08", "Codex 8：rate.json 新鲜、周用量 95% 但周重置时间已过 2 天、5h 10%：不判「快用完」")
def _(E):
    put_rate(E, {"five_hour": win(10, 3600), "seven_day": win(95, -2 * 86400)})
    m = cg_mode(E)
    return m != "" and not m.startswith("快用完")


@case("CX09", "Codex 9：只有未过期的 5h 95%、没有 seven_day：判「快用完」")
def _(E):
    put_rate(E, {"five_hour": win(95, 3600)})
    return cg_mode(E).startswith("快用完")


@case("CX10A", "Codex 10：rate.json 里周用量 20%（7 小时前的读数、明天重置，本可判宽裕），新 payload 只带 5h 10%：周读数仍算过期，不判「宽裕」")
def _(E):
    put_rate(E, {"five_hour": win(5, 3600), "seven_day": win(20, 86400)}, ts_ago_s=7 * 3600)
    sl(E, 50_000, rl={"five_hour": win(10, 3600)})
    m = cg_mode(E)
    return m.startswith("正常")


@case("CX10B", "Codex 10：同一窗口、同样数值的重复旧 payload（周 20%）不刷新采样时间：7 小时后仍算过期，不判「宽裕」")
def _(E):
    w7, w5 = win(20, 86400), win(5, 3600)
    put_rate(E, {"five_hour": w5, "seven_day": w7}, ts_ago_s=7 * 3600)
    sl(E, 50_000, rl={"five_hour": dict(w5), "seven_day": dict(w7)})
    return cg_mode(E).startswith("正常")


@case("CX10C", "不回归：周读数从 20% 变成 21%（真有新数据）：采样时间刷新，按时间进度判「宽裕」")
def _(E):
    w7 = win(20, 86400)
    put_rate(E, {"five_hour": win(5, 3600), "seven_day": w7}, ts_ago_s=7 * 3600)
    sl(E, 50_000, rl={"five_hour": win(5, 3600), "seven_day": dict(w7, used_percentage=21)})
    return cg_mode(E).startswith("宽裕")


@case("CX11", "Codex 11：封顶已生效、quant-backtest：状态栏 185k 显示 ⚠、196k 显示 ⚠⚠，与守卫的软提醒/强提醒一致",
      cfg_patch={"cap_from_date": "2000-01-01"})
def _(E):
    p = E.proj("quant-backtest")
    o1, _ = sl(E, 185_000, cwd=p); o2, _ = sl(E, 196_000, cwd=p)
    g1 = cg(E, 185_000, cwd=p) or ""; g2 = cg(E, 196_000, cwd=p) or ""
    return ("⚠" in o1 and "⚠⚠" not in o1 and "⚠⚠" in o2
            and "[上下文守卫]" in g1 and "上下文已很长" not in g1 and "上下文已很长" in g2)


@case("CX11G", "Codex 11：状态栏与守卫在 3 个 cwd × 6 个上下文长度 × 封顶开/关 上判定完全一致（无 ⚠ ⇔ 不提醒，⚠ ⇔ 软，⚠⚠ ⇔ 强）")
def _(E):
    bad = []
    for cap in ("2000-01-01", "2099-01-01"):
        c = json.load(open(os.path.join(E.suite, "config.json"))); c["cap_from_date"] = cap
        json.dump(c, open(os.path.join(E.suite, "config.json"), "w"))
        for cwd in (E.proj("a"), E.proj("quant-backtest"), E.proj("quant-backtest-old")):
            for n in (150_000, 170_000, 185_000, 196_000, 260_000, 320_000):
                o, _ = sl(E, n, cwd=cwd); g = cg(E, n, cwd=cwd) or ""
                s_lv = 2 if "⚠⚠" in o else 1 if "⚠" in o else 0
                g_lv = 2 if "上下文已很长" in g else 1 if "[上下文守卫]" in g else 0
                if s_lv != g_lv:
                    bad.append((cap, os.path.basename(cwd), n, s_lv, g_lv))
    if bad:
        raise AssertionError("不一致：" + str(bad[:4]))
    return True


@case("CX14", "Codex 14：transcript 里主会话 200k 之后跟一条 isSidechain:true 的 1k usage：仍按 200k 提醒")
def _(E):
    return "[上下文守卫]" in (cg(E, 200_000, extra_rows=[asst(1_000, side=True)]) or "")


@case("CX15", "Codex 15：mode.json 原子写（临时文件 + os.replace）：写后 inode 变了、内容是完整 JSON（代理断言，竞态本身不好稳定复现）")
def _(E):
    p = os.path.join(E.state, "mode.json")
    open(p, "w").write("{}")
    ino = os.stat(p).st_ino
    cg(E, 50_000)
    return os.stat(p).st_ino != ino and "mult" in json.load(open(p))


@case("CXR1", "会话去重：同一 session 170k 连发 3 条只提醒 1 次；涨到 225k（+55k）再提醒；到 310k 首次越强线发强提醒")
def _(E):
    r = [cg(E, n, sid="S1") or "" for n in (170_000, 172_000, 175_000, 225_000, 310_000)]
    return ("[上下文守卫]" in r[0] and r[1] == "" and r[2] == "" and "[上下文守卫]" in r[3]
            and "上下文已很长" not in r[3] and "上下文已很长" in r[4])


@case("CXR2", "会话去重：上下文不涨时每 10 条消息再提醒一次（11 条 170k → 提醒 2 次）；另一个 session 不受影响")
def _(E):
    got = sum(bool(cg(E, 170_000, sid="S1")) for _ in range(11))
    other = bool(cg(E, 170_000, sid="S2"))
    return got == 2 and other


@case("CXR3", "会话去重：回到软线以下（如 compact 后）再越线，重新算首次，立即提醒")
def _(E):
    a = cg(E, 170_000, sid="S1"); b = cg(E, 60_000, sid="S1"); c = cg(E, 170_000, sid="S1")
    return bool(a) and b == "" and bool(c)


@case("CXR4", "不回归：payload 没有 session_id 时每次都提醒（旧行为，宁多勿漏）")
def _(E):
    return all(cg(E, 170_000) for _ in range(3))


# ================================================================ 复核（round4_review_12_13.md【低】）
@case("LA1", "cap_from_date 写成「今年/1/1」（斜杠、不补零，已过去）：应封顶 → quant-backtest 190k 软提醒（软线 180k）",
      cfg_patch={"cap_from_date": "%d/1/1" % time.localtime().tm_year})
def _(E):
    return "软线 180k" in (cg(E, 190_000, cwd=E.proj("quant-backtest")) or "")


@case("LA2", "cap_from_date 写成「明年/1/1」（未来）：不封顶 → quant-backtest 190k 不提醒（软线 250k）",
      cfg_patch={"cap_from_date": "%d/1/1" % (time.localtime().tm_year + 1)})
def _(E):
    return cg(E, 190_000, cwd=E.proj("quant-backtest")) == ""


@case("LA3", "cap_from_date 无法解析（「下周」）：按已生效封顶，并在说明里写明",
      cfg_patch={"cap_from_date": "下周"})
def _(E):
    m = cg(E, 190_000, cwd=E.proj("quant-backtest")) or ""
    return "软线 180k" in m and "无法解析" in m


@case("LA4", "max_soft_line_tokens 写成「18万」：回退默认 180k，守卫照常工作（不整体静默失效）",
      cfg_patch={"cap_from_date": "2000-01-01", "max_soft_line_tokens": "18万", "max_strong_line_tokens": None})
def _(E):
    m = cg(E, 190_000, cwd=E.proj("quant-backtest")) or ""
    return "软线 180k" in m and "上下文已很长" not in m


@case("LA5", "handoff_threshold_tokens 写成「abc」、项目阈值写成 null：都回退默认，170k 仍提醒",
      cfg_patch={"handoff_threshold_tokens": "abc", "per_project_threshold_tokens": {"a": None}})
def _(E):
    return "软线 160k" in (cg(E, 170_000) or "")


@case("LB1", "强提醒文案与 handoff skill 一致：写「最多 120 行」，不再写「不限行数」")
def _(E):
    m = cg(E, 320_000) or ""
    return "最多 120 行" in m and "不限行数" not in m


# ================================================================ D2（Codex 复核打回 round4_codex_review_D.md + 盲测分诊 SL03）
def put_remind(E, obj):
    p = os.path.join(E.state, "remind.json")
    open(p, "w").write(obj if isinstance(obj, str) else json.dumps(obj))
    return p


def read_remind(E):
    try:
        return json.load(open(os.path.join(E.state, "remind.json")))
    except Exception:
        return None


@case("CXR5", "Codex D-1：会话 A 的记录已超过 7 天（ts=now-604801, soft, at_n=170k）；A 再以 170k 提交：先清掉过期记录，按首次越线发软提醒")
def _(E):
    put_remind(E, {"A": {"level": "soft", "at_n": 170_000, "since": 0, "ts": int(time.time()) - 604_801}})
    m = cg(E, 170_000, sid="A") or ""
    a = (read_remind(E) or {}).get("A") or {}
    return "[上下文守卫]" in m and "上下文已很长" not in m and a.get("since") == 0 and a.get("level") == "soft"


@case("CXR6", "Codex D-2a：remind.json={\"A\":\"broken\"}，会话 A 主会话 320k：坏记录按缺失处理，照发强提醒（不静默）")
def _(E):
    put_remind(E, {"A": "broken"})
    m = cg(E, 320_000, sid="A") or ""
    return "上下文已很长" in m and ((read_remind(E) or {}).get("A") or {}).get("level") == "strong"


@case("CXR7", "Codex D-2b：会话 B 的记录 ts=\"broken\"，会话 A 320k：A 照发强提醒；B 的坏记录被清掉")
def _(E):
    put_remind(E, {"B": {"level": "soft", "at_n": 170_000, "since": 0, "ts": "broken"}})
    m = cg(E, 320_000, sid="A") or ""
    R = read_remind(E) or {}
    return "上下文已很长" in m and "B" not in R and "A" in R


@case("CXR8", "D2：会话 A 的记录字段类型全错（at_n/since 是字符串、level 是数字）、170k：按缺失处理，发软提醒")
def _(E):
    put_remind(E, {"A": {"level": 3, "at_n": "x", "since": "y", "ts": int(time.time())}})
    m = cg(E, 170_000, sid="A") or ""
    return "[上下文守卫]" in m and "上下文已很长" not in m


@case("CXR9", "D2 不回归：有效记录不被误清——同会话 170k 提醒后再发 172k 仍去重（不提醒），另一会话的有效记录保留")
def _(E):
    put_remind(E, {"B": {"level": "soft", "at_n": 165_000, "since": 2, "ts": int(time.time()) - 3600}})
    a = cg(E, 170_000, sid="A") or ""; b = cg(E, 172_000, sid="A")
    R = read_remind(E) or {}
    return "[上下文守卫]" in a and b == "" and (R.get("B") or {}).get("at_n") == 165_000


@case("CXR10", "D2：remind.json 是坏 JSON / 是列表：按无记录处理，320k 照发强提醒（原本就该通过，防回归）")
def _(E):
    ok = True
    for raw in ("{not json", "[1, 2]"):
        put_remind(E, raw)
        ok = ok and "上下文已很长" in (cg(E, 320_000, sid="A") or "")
        put_remind(E, "{}")
    return ok


@case("SL03", "盲测 SL03：状态栏 stdin 不是合法 JSON（坏 JSON / 空 / 非对象 / 字段类型错）：退出码 0、stderr 无 traceback、stdout 最多一行")
def _(E):
    bad = []
    for raw in ("{not json", "", "[1, 2]", '"x"', json.dumps({"context_window": "oops", "model": 5})):
        rc, out, err = sl_raw(E, raw)
        if rc != 0 or "Traceback" in err or len(out.strip().splitlines()) > 1:
            bad.append((raw[:20], rc, err.strip().splitlines()[-1:] if err.strip() else ""))
    if bad:
        raise AssertionError(str(bad[:3]))
    return True


@case("SL03B", "SL03 不回归：坏 stdin 不改写 rate.json（另一会话写的 5h 30% / 7d 50% 原样保留）；之后正常 payload 照常显示")
def _(E):
    pre = {"ts": int(time.time()), "rate_limits": {"five_hour": win(30, 3600), "seven_day": win(50, 86400)}}
    json.dump(pre, open(os.path.join(E.state, "rate.json"), "w"))
    rc, _, _ = sl_raw(E, "{not json")
    same = E.rate_json() == pre
    out, _ = sl(E, 50_000)
    return rc == 0 and same and "5h 30%" in out and "7d 50%" in out and "ctx 50k" in out


FALLBACK_USED = []


@contextlib.contextmanager
def case_dir(cid, keep=None):
    """临时目录：--keep → $R4D_TMP → tempfile → 项目 state/_test_tmp（后两者之外用完 mv 到 _to_delete）。"""
    if keep:
        yield os.path.realpath(tempfile.mkdtemp(prefix=f"r4D-{cid}-", dir=keep))
        return
    base = os.environ.get("R4D_TMP")
    if not base and os.environ.get("R4D_FORCE_FALLBACK") != "1":
        try:
            t = tempfile.mkdtemp(prefix=f"r4D-{cid}-")
        except Exception:
            t = None
        if t:
            try:
                yield os.path.realpath(t)
            finally:
                shutil.rmtree(t, ignore_errors=True)
            return
    if not base:
        base = os.path.join(ROOT, "state", "_test_tmp")
        FALLBACK_USED.append(cid)
    name = f"r4D-{cid}-{os.getpid()}-{time.strftime('%Y%m%d-%H%M%S')}"
    t = os.path.join(base, name)
    os.makedirs(t)
    try:
        yield os.path.realpath(t)
    finally:
        trash = (os.path.join(ROOT, "state", "_to_delete") if os.path.realpath(base).startswith(os.path.join(ROOT, "state"))
                 else os.path.join(base, "_to_delete"))
        os.makedirs(trash, exist_ok=True)
        os.rename(t, os.path.join(trash, name))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", choices=["current", "pre", "pre2"], default="current")
    ap.add_argument("--keep", help="临时目录放到这里且不清理")
    a = ap.parse_args()
    if a.keep:
        os.makedirs(a.keep, exist_ok=True)
    npass = 0
    for cid, desc, cfg_patch, fn in CASES:
        with case_dir(cid, a.keep) as tmp:
            E = Env(a.version, tmp, cfg_patch)
            try:
                ok, note = bool(fn(E)), ""
            except Exception as e:
                ok, note = False, f"  出错：{type(e).__name__}: {e}"[:200]
        npass += ok
        print(f"{'PASS' if ok else 'FAIL'}  {cid}  {desc}{note}")
    if FALLBACK_USED:
        print(f"（tempfile 不可用或被强制退回：{len(FALLBACK_USED)} 个用例用了 state/_test_tmp，已 mv 到 state/_to_delete/）")
    print(f"\n[{a.version}] {npass}/{len(CASES)} 通过")
    return 0 if npass == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
