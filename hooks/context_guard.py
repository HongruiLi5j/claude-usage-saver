#!/usr/bin/env python3
"""上下文守卫（UserPromptSubmit hook），同时是状态栏共用的「额度分档 + 软线/强线」计算模块。

statusline.sh 会 `import context_guard` 调 lines()，所以顶层只做定义，hook 入口在 main()（R4D/Codex 11：
两边用同一套阈值算法，含项目覆盖、档位倍数、下限、Pro 封顶）。
"""
import json, sys, os, re, time, datetime as dt
BASE = os.path.expanduser("~/Documents/Projects/claude-usage-saver")
STATE = os.environ.get("USAGE_SAVER_STATE", BASE + "/state")
try:
    cfg = json.load(open(BASE + "/config.json"))
except Exception:
    cfg = {}
if not isinstance(cfg, dict):
    cfg = {}
WEEK = 7 * 24 * 3600
DEFAULTS = dict(handoff_threshold_tokens=160000, strong_threshold_tokens=300000, min_threshold_tokens=100000,
                max_soft_line_tokens=180000, max_strong_line_tokens=195000, rate_stale_seconds=6 * 3600,
                near_limit_pct=90, headroom_rich_pts=10, headroom_tight_pts=5,
                remind_every_tokens=50000, remind_every_prompts=10)
TIERS = {"宽裕": 1.5, "正常": 1.0, "紧张": 0.75, "快用完": 0.6}


def _num(v):
    """能当非负有限数用就返回 float，否则 None（"18万"、None、布尔、负数都算无效）。"""
    if isinstance(v, bool):
        return None
    try:
        v = float(v)
    except Exception:
        return None
    return v if 0 <= v < float("inf") else None


def num(key):
    """读数值配置；缺失或写错时回退默认值（R4D：配置写错不能让守卫整体静默失效）。"""
    v = _num(cfg.get(key))
    return float(DEFAULTS[key]) if v is None else v


def tiers():
    t = dict(TIERS)
    raw = cfg.get("tier_multipliers")
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in t and _num(v):
                t[k] = _num(v)
    return t


def _atomic_write(path, obj):
    """同目录临时文件写完再 os.replace，读方不会读到半个 JSON（R4D/Codex 15）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 额度分档
def get_mode(now=None):
    """按剩余额度分档。返回 (档名, 软线倍数, 说明)。数据缺失或过期一律按「正常」。

    顺序（R4D/Codex 8–10）：先丢掉已重置的窗口 → 任一未重置窗口 ≥ near_limit_pct 就判「快用完」
    （窗口内用量只增不减，旧读数是下限，所以这一步不看陈旧）→ 再用新鲜的周读数算时间进度分档。
    「新鲜」看每个窗口自己的 sampled_at（= 读数最后一次变化的时间，由 statusline 写；旧格式回退到顶层 ts）。
    """
    now = now or time.time()
    T = tiers()
    try:
        r = json.load(open(STATE + "/rate.json"))
        rl = r.get("rate_limits") or {}
        near = num("near_limit_pct")
        live = {}
        for k in ("five_hour", "seven_day"):
            w = rl.get(k) or {}
            if isinstance(w, dict) and _num(w.get("used_percentage")) is not None and (_num(w.get("resets_at")) or 0) > now:
                live[k] = dict(w, used_percentage=_num(w["used_percentage"]), resets_at=_num(w["resets_at"]))
        def pct(k):
            return "%.0f%%" % live[k]["used_percentage"] if k in live else "无"
        if any(w["used_percentage"] >= near for w in live.values()):
            return "快用完", T["快用完"], f"周用量 {pct('seven_day')},5小时窗口 {pct('five_hour')}"
        wk = live.get("seven_day")
        if not wk:
            raw = rl.get("seven_day") or {}
            if not raw or raw.get("used_percentage") is None:
                return "正常", T["正常"], "无周用量数据,按正常档"
            if not raw.get("resets_at"):
                return "正常", T["正常"], f"周用量 {raw['used_percentage']:.0f}%,无重置时间,按正常档"
            # 重置时间已过：这个用量是上一周的，不能拿来判「宽裕」
            return "正常", T["正常"], "周额度已重置但用量数据未更新,按正常档"
        if now - (wk.get("sampled_at") or r.get("ts", 0)) > num("rate_stale_seconds"):
            return "正常", T["正常"], "额度数据已过期,按正常档"
        used, resets = wk["used_percentage"], wk["resets_at"]
        elapsed = min(max(1 - (resets - now) / WEEK, 0), 1) * 100
        head = elapsed - used     # 正数=用量落后于时间进度=有富余
        if head > num("headroom_rich_pts"):
            return "宽裕", T["宽裕"], f"周用量 {used:.0f}% < 时间进度 {elapsed:.0f}%"
        if head < -num("headroom_tight_pts"):
            return "紧张", T["紧张"], f"周用量 {used:.0f}% > 时间进度 {elapsed:.0f}%"
        return "正常", T["正常"], f"周用量 {used:.0f}%,时间进度 {elapsed:.0f}%"
    except Exception:
        return "正常", T["正常"], "读取额度失败,按正常档"


# ---------------------------------------------------------------- 软线 / 强线
def parse_day(s):
    """cap_from_date → date。接受 2026-11-21、2026-11-1、2026/11/21、2026.11.21；其他返回 None。"""
    m = re.fullmatch(r"\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*", str(s))
    if not m:
        return None
    try:
        return dt.date(*map(int, m.groups()))
    except ValueError:
        return None


def cap_active(today=None):
    """(是否封顶, 说明)。未配置 → 不封顶；配置了但解析不了 → 按已生效封顶（保守：Pro 额度更紧），并在说明里写明。"""
    raw = cfg.get("cap_from_date")
    if not raw:
        return False, ""
    d = parse_day(raw)
    if d is None:
        return True, f"cap_from_date={raw!r} 无法解析,按已生效封顶"
    return (today or dt.date.today()) >= d, ""


def project_soft(cwd):
    """项目软线：cwd 的某一级目录名等于键才算命中（R4D：quant-backtest-old 不再命中 quant-backtest）。"""
    parts = os.path.normpath(cwd).split(os.sep) if cwd else []
    pp = cfg.get("per_project_threshold_tokens")
    if isinstance(pp, dict):
        for k, v in pp.items():
            if k in parts and _num(v):
                return _num(v)
    return num("handoff_threshold_tokens")


def lines(cwd, mult, today=None):
    """返回 (软线, 强线, 说明)。守卫和状态栏都调这一个函数。"""
    mult = _num(mult) or 1.0
    soft = max(int(project_soft(cwd) * mult), int(num("min_threshold_tokens")))
    strong = max(int(num("strong_threshold_tokens") * (mult if mult > 1 else 1)), soft + 50000)
    # D5：换回 Pro 后（cap_from_date 起）软线/强线封顶，Pro 在 20 万以上的行为未实测
    on, note = cap_active(today)
    if on:
        soft = min(soft, int(num("max_soft_line_tokens")))
        strong = min(strong, int(num("max_strong_line_tokens")))
        strong = max(strong, soft + 1)
    return soft, strong, note


# ---------------------------------------------------------------- 会话级提醒去重
REMIND_TTL = 7 * 86400


def raw_kind(n, soft, strong):
    """不考虑去重时，本次「应有的」提醒：'strong' / 'soft' / None。"""
    return None if n < soft else "strong" if n >= strong else "soft"


def _clean_entry(v, now):
    """校验一条 remind.json 记录（D2/Codex 复核 D）：字段齐全、类型对、未超过 7 天 → 返回规整后的记录；
    否则返回 None，调用方按「没有记录」处理（坏记录、过期记录都不能参与去重）。"""
    if not isinstance(v, dict) or v.get("level") not in ("soft", "strong"):
        return None
    ts, at_n, since = _num(v.get("ts")), _num(v.get("at_n", 0)), _num(v.get("since", 0))
    if ts is None or at_n is None or since is None or now - ts >= REMIND_TTL:
        return None
    return {"level": v["level"], "at_n": int(at_n), "since": int(since), "ts": int(ts)}


def remind_kind(sid, n, soft, strong, now=None):
    """返回 'strong' / 'soft' / None。同一会话：首次越软线、首次越强线各提醒一次；
    之后每涨 remind_every_tokens 或每 remind_every_prompts 条消息再提醒一次（Codex「最值得做」第 3 条）。
    状态在 state/remind.json，按 session_id。没有 session_id、状态文件写不了、或去重过程出任何异常
    → 返回本次应有的提醒（宁多勿漏，D2）。读进来的记录先逐条校验：坏的、超过 7 天的先清掉，再参与去重。"""
    now = now or time.time()
    kind = raw_kind(n, soft, strong)
    if not sid:
        return kind
    path = STATE + "/remind.json"
    try:
        try:
            R = json.load(open(path))
        except Exception:
            R = {}
        if not isinstance(R, dict):
            R = {}
        R = {k: c for k, v in R.items() if (c := _clean_entry(v, now))}   # 先清理，再去重
        e = R.get(sid) or {}
        out = None
        if kind is None:
            R.pop(sid, None)              # 回到软线以下（例如 compact 后）：下次越线重新算「首次」
        else:
            since = e.get("since", 0) + 1
            if not e:
                out = kind                # 首次越线（可能直接越过强线），或旧记录坏了/过期了
            elif kind == "strong" and e["level"] != "strong":
                out = "strong"            # 首次越强线
            elif n - e["at_n"] >= num("remind_every_tokens") or since >= num("remind_every_prompts"):
                out = kind                # 间隔提醒
            if out:
                level = "strong" if "strong" in (out, e.get("level")) else "soft"
                R[sid] = {"level": level, "at_n": n, "since": 0, "ts": int(now)}
            else:
                R[sid] = dict(e, since=since, ts=int(now))
        _atomic_write(path, R)
    except Exception:
        return kind
    return out


# ---------------------------------------------------------------- hook 入口
def last_usage(path):
    last = None
    with open(path, "rb") as f:
        f.seek(0, 2); size = f.tell()
        # 先读尾部 400KB；最后几行是很大的 tool_result 时找不到 usage，就往回多读（CG08）
        for win in (400_000, 4_000_000, 16_000_000):
            f.seek(max(0, size - win))
            for line in f.read().decode("utf-8", "ignore").splitlines():
                if '"usage"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("isSidechain"):
                    continue          # 子 agent 的记录不算主会话上下文（R4D/Codex 14）
                u = (o.get("message") or {}).get("usage") if o.get("type") == "assistant" else None
                if u:
                    last = u
            if last or win >= size:
                break
    return last


def main():
    try:
        d = json.load(sys.stdin)
        cwd = d.get("cwd") or ""
        mode, mult, why = get_mode()
        limit, strong, note = lines(cwd, mult)
        if note:
            why += "," + note
        try:
            # 调试用：属于最后一次提交消息的会话（statusline 只读其中的 mult）
            _atomic_write(STATE + "/mode.json", {"ts": int(time.time()), "mode": mode, "mult": mult, "why": why,
                                                 "soft_line": limit, "strong_line": strong})
        except Exception:
            pass
        last = last_usage(d.get("transcript_path"))
        if not last:
            return
        n = last.get("input_tokens", 0) + last.get("cache_read_input_tokens", 0) + last.get("cache_creation_input_tokens", 0)
        try:
            kind = remind_kind(d.get("session_id"), n, limit, strong)
        except Exception:
            kind = raw_kind(n, limit, strong)     # 去重本身出错：照发本次应有的提醒（D2）
        if not kind:
            return
        if kind == "strong":
            tail = "上下文已很长。不要中断正在做的工作;做到下一个自然停顿点时,用 handoff skill 写好交接(高消耗项目最多 120 行,更早的细节放 HANDOFF.history.md),在那次回复末尾用一句话请所有者决定是否输入 /clear。"
        else:
            tail = "这只是软提醒:仅当当前任务已完成或处于自然停顿点时,才用 handoff skill 写交接并请所有者输入 /clear 后说「接着做」;若任务正做到一半,先完成,不要为省额度中断或降低质量。"
        msg = f"[上下文守卫] 当前上下文约 {n//1000}k token(本项目软线 {limit//1000}k,额度档:{mode},{why})。" + tail
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}, ensure_ascii=False))
    except Exception:
        pass


if __name__ == "__main__":
    if "--mode" in sys.argv:
        m = get_mode(); print(m[0], m[1], m[2])
    else:
        main()
    sys.exit(0)
