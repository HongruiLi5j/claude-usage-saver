#!/usr/bin/env python3
import json, sys, os, time
BASE = os.path.expanduser("~/Documents/Projects/claude-usage-saver")
STATE = os.environ.get("USAGE_SAVER_STATE", BASE + "/state")
try:
    cfg = json.load(open(BASE + "/config.json"))
except Exception:
    cfg = {}
WEEK = 7 * 24 * 3600

def get_mode(now=None):
    """按剩余额度分档。返回 (档名, 软线倍数, 说明)。数据缺失或过期一律按「正常」。"""
    now = now or time.time()
    tiers = cfg.get("tier_multipliers", {"宽裕": 1.5, "正常": 1.0, "紧张": 0.75, "快用完": 0.6})
    try:
        r = json.load(open(STATE + "/rate.json"))
        if now - r.get("ts", 0) > cfg.get("rate_stale_seconds", 6 * 3600):
            return "正常", tiers["正常"], "额度数据已过期,按正常档"
        rl = r.get("rate_limits") or {}
        wk = rl.get("seven_day") or {}
        fh = rl.get("five_hour") or {}
        used = wk.get("used_percentage")
        if used is None:
            return "正常", tiers["正常"], "无周用量数据,按正常档"
        if used >= cfg.get("near_limit_pct", 90) or (fh.get("used_percentage") or 0) >= cfg.get("near_limit_pct", 90):
            return "快用完", tiers["快用完"], f"周用量 {used:.0f}%,5小时窗口 {(fh.get('used_percentage') or 0):.0f}%"
        resets = wk.get("resets_at")
        if not resets:
            return "正常", tiers["正常"], f"周用量 {used:.0f}%,无重置时间,按正常档"
        elapsed = min(max(1 - (resets - now) / WEEK, 0), 1) * 100
        head = elapsed - used     # 正数=用量落后于时间进度=有富余
        if head > cfg.get("headroom_rich_pts", 10):
            return "宽裕", tiers["宽裕"], f"周用量 {used:.0f}% < 时间进度 {elapsed:.0f}%"
        if head < -cfg.get("headroom_tight_pts", 5):
            return "紧张", tiers["紧张"], f"周用量 {used:.0f}% > 时间进度 {elapsed:.0f}%"
        return "正常", tiers["正常"], f"周用量 {used:.0f}%,时间进度 {elapsed:.0f}%"
    except Exception:
        return "正常", tiers["正常"], "读取额度失败,按正常档"

if __name__ == "__main__" and "--mode" in sys.argv:
    m = get_mode(); print(m[0], m[1], m[2]); sys.exit(0)

limit = int(cfg.get("handoff_threshold_tokens", 160000))
strong = int(cfg.get("strong_threshold_tokens", 300000))
floor = int(cfg.get("min_threshold_tokens", 100000))
try:
    d = json.load(sys.stdin)
    path = d.get("transcript_path")
    cwd = d.get("cwd") or ""
    for k, v in (cfg.get("per_project_threshold_tokens") or {}).items():
        if k in cwd:
            limit = int(v); break
    mode, mult, why = get_mode()
    limit = max(int(limit * mult), floor)
    strong = max(int(strong * (mult if mult > 1 else 1)), limit + 50000)
    try:
        os.makedirs(STATE, exist_ok=True)
        json.dump({"ts": int(time.time()), "mode": mode, "mult": mult, "why": why, "soft_line": limit, "strong_line": strong}, open(STATE + "/mode.json", "w"), ensure_ascii=False)
    except Exception:
        pass
    last = None
    with open(path, "rb") as f:
        f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 400000))
        for line in f.read().decode("utf-8", "ignore").splitlines():
            try:
                o = json.loads(line)
            except Exception:
                continue
            u = (o.get("message") or {}).get("usage") if o.get("type") == "assistant" else None
            if u:
                last = u
    if not last:
        sys.exit(0)
    n = last.get("input_tokens", 0) + last.get("cache_read_input_tokens", 0) + last.get("cache_creation_input_tokens", 0)
    if n >= limit:
        if n >= strong:
            tail = "上下文已很长。请在本次回答中向所有者说明情况,并在合适的停顿点用 handoff skill 写好交接(高消耗项目的交接要详细,不限行数),再请所有者决定是否输入 /clear。"
        else:
            tail = "这只是软提醒:仅当当前任务已完成或处于自然停顿点时,才用 handoff skill 写交接并请所有者输入 /clear 后说「接着做」;若任务正做到一半,先完成,不要为省额度中断或降低质量。"
        msg = f"[上下文守卫] 当前上下文约 {n//1000}k token(本项目软线 {limit//1000}k,额度档:{mode},{why})。" + tail
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}, ensure_ascii=False))
except Exception:
    pass
sys.exit(0)
