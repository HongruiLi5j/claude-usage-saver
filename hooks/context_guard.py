#!/usr/bin/env python3
"""上下文守卫（UserPromptSubmit hook），同时是状态栏共用的「额度分档 + 软线/强线」计算模块。

statusline.sh 会 `import context_guard` 调 lines()，所以顶层只做定义，hook 入口在 main()（R4D/Codex 11：
两边用同一套阈值算法，含项目覆盖、档位倍数、下限、Pro 封顶）。

定位（R5I / 所有者定 CG13）：套件目录 = 本脚本所在目录的上一级（不写死 ~/Documents/Projects/…）；config.json 在
套件目录；state 目录 = $USAGE_SAVER_STATE，没有就用 套件/state。statusline.sh 用同一规则，所以它写的 rate.json
和这里读的是同一个文件。config.json 读不到或不是 JSON 对象时，往 stderr 打一行提示，然后全部用默认值。
阈值优先级：项目覆盖（项目根的 .usage-saver.json 里 thresholds.soft_tokens / strong_tokens，经
tools/project_profile.py 读取）> 套件 config（per_project_threshold_tokens 按目录名、再 handoff_threshold_tokens）
> 默认值；之后再乘额度档倍数、套下限和 Pro 封顶。
上下文窗口（R5I → R6M 改）：
- 窗口来源依次是：hook payload 自带的 context_window_size → 状态栏按 session_id 记在 state/ctx_window.json 的值
  → 模型 id 里显式写着 [1m] → 都没有时按保守的 20 万「假设窗口」。不按模型族名猜窗口。
- 知道窗口：软线 ≤ 窗口 60%、强线 ≤ 窗口 80%（R6M；原来是 85% / 95%）。理由：Claude Code 大约在窗口 83% 处自动
  压缩（20 万窗口约 16.7 万），强线放在 95% 永远等不到。比例可在 config 的 window_soft_ratio / window_strong_ratio 改。
- 假设窗口（只在 transcript 里确实有模型名、但窗口不知道时才用）：强线 ≤ 80%，软线只收到 75%（猜的窗口不该把
  真实是 100 万的会话提前打扰）；上下文已经超过假设窗口的 85% 还没被压缩，说明真实窗口更大，就不再按假设收紧。
- transcript 里连模型名都没有（旧格式、合成数据、别的工具）且窗口不知道：不改阈值，和状态栏 payload 没给窗口时
  调 lines(window=None) 的行为一致，两边判定不会对不上。
- 读不到 usage（transcript 不存在、字段改名、全是 0、不是 Claude 的格式）时，在 mode.json 写 usage_unreadable=true
  和原因 usage_why，不再静默；新会话还没有回复时不算读不到。

CG11（所有者定，保持现状）：「快用完」档不看读数是否过期——任一未重置窗口的用量 ≥ near_limit_pct 就判「快用完」，
即使读数已超过 rate_stale_seconds。理由：窗口内用量只增不减，旧读数是下限；只有窗口的 resets_at 已过才作废。
"""
import json, sys, os, re, time, datetime as dt
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.environ.get("USAGE_SAVER_STATE") or os.path.join(BASE, "state")
try:
    cfg = json.load(open(os.path.join(BASE, "config.json")))
    if not isinstance(cfg, dict):
        raise ValueError("顶层不是 JSON 对象")
except Exception as _e:
    cfg = {}
    try:
        sys.stderr.write(f"[usage-saver] 读不到 {os.path.join(BASE, 'config.json')}（{type(_e).__name__}），全部用默认值\n")
    except Exception:
        pass
WEEK = 7 * 24 * 3600
DEFAULTS = dict(handoff_threshold_tokens=160000, strong_threshold_tokens=300000, min_threshold_tokens=100000,
                max_soft_line_tokens=180000, max_strong_line_tokens=195000, rate_stale_seconds=6 * 3600,
                near_limit_pct=90, headroom_rich_pts=10, headroom_tight_pts=5,
                remind_every_tokens=50000, remind_every_prompts=10,
                # R6M：按窗口比例收紧（见文件头）
                window_soft_ratio=0.6, window_strong_ratio=0.8, assumed_window_tokens=200000,
                assumed_soft_ratio=0.75, assumed_lift_ratio=0.85)
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


def ratio(key):
    """读 0–1 之间的比例配置；缺失、写错或不在 (0, 1] 里时用默认值。"""
    v = _num(cfg.get(key))
    return v if v is not None and 0 < v <= 1 else float(DEFAULTS[key])


def window_ratios(assumed=False):
    """(软线比例, 强线比例)。配置写反了（软 ≥ 强）就整组回退默认，保证软线 < 强线。"""
    rs = ratio("assumed_soft_ratio" if assumed else "window_soft_ratio")
    rg = ratio("window_strong_ratio")
    if rs >= rg:
        rs = float(DEFAULTS["assumed_soft_ratio" if assumed else "window_soft_ratio"])
        rg = float(DEFAULTS["window_strong_ratio"])
    return rs, rg


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


_PP = []


def project_override(cwd):
    """项目根 .usage-saver.json 里的 (soft, strong)，没有就 (None, None)。用 tools/project_profile.py 的轻量入口
    （只找根、读覆盖文件，不遍历项目）；模块缺失或出任何错都返回 (None, None)。"""
    try:
        if not cwd:
            return None, None
        if not _PP:
            mod = None
            path = os.path.join(BASE, "tools", "project_profile.py")
            if os.path.isfile(path):
                import importlib.util
                spec = importlib.util.spec_from_file_location("usage_saver_project_profile", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            _PP.append(mod)
        if _PP[0] is None:
            return None, None
        t = _PP[0].project_thresholds(cwd)
        return _num(t.get("soft_tokens")), _num(t.get("strong_tokens"))
    except Exception:
        return None, None


def project_soft(cwd):
    """项目软线。优先级：项目 .usage-saver.json > config 的 per_project（cwd 的某一级目录名等于键才算命中，
    R4D：quant-backtest-old 不再命中 quant-backtest）> handoff_threshold_tokens > 默认。"""
    ov = project_override(cwd)[0]
    if ov:
        return ov
    parts = os.path.normpath(cwd).split(os.sep) if cwd else []
    pp = cfg.get("per_project_threshold_tokens")
    if isinstance(pp, dict):
        for k, v in pp.items():
            if k in parts and _num(v):
                return _num(v)
    return num("handoff_threshold_tokens")


def lines(cwd, mult, today=None, window=None, assumed=False):
    """返回 (软线, 强线, 说明)。守卫和状态栏都调这一个函数。
    window：本会话上下文窗口（不知道就 None，不改阈值）。assumed=True 表示 window 是猜的（假设窗口），
    软线只收到 assumed_soft_ratio；强线两种情况都收到 window_strong_ratio。"""
    mult = _num(mult) or 1.0
    soft = max(int(project_soft(cwd) * mult), int(num("min_threshold_tokens")))
    base_strong = project_override(cwd)[1] or num("strong_threshold_tokens")
    strong = max(int(base_strong * (mult if mult > 1 else 1)), soft + 50000)
    # D5：换回 Pro 后（cap_from_date 起）软线/强线封顶，Pro 在 20 万以上的行为未实测
    on, note = cap_active(today)
    if on:
        soft = min(soft, int(num("max_soft_line_tokens")))
        strong = min(strong, int(num("max_strong_line_tokens")))
        strong = max(strong, soft + 1)
    w = _num(window)
    if w and w >= 20000:
        # R5I/R6M：不超过上下文窗口的一定比例，保证在自动压缩（约 83%）之前能看到强提醒
        rs, rg = window_ratios(assumed)
        if soft > w * rs or strong > w * rg:
            what = f"窗口未知,按 {int(w) // 1000}k 假设收紧" if assumed else f"按上下文窗口 {int(w) // 1000}k 收紧"
            note = (note + "," if note else "") + what
        soft = min(soft, int(w * rs))
        strong = max(min(strong, int(w * rg)), soft + 1)
    return soft, strong, note


def _model_id(v):
    """payload / transcript 里的模型：字符串，或 {"id": ...}；拿不到返回 ""。"""
    if isinstance(v, dict):
        v = v.get("id")
    return v.strip() if isinstance(v, str) else ""


def resolve_window(d, sid, model, n):
    """本会话上下文窗口。返回 (窗口或 None, 来源, 是否假设)。来源：
    payload / statusline / model-id（id 里写着 [1m]）/ assumed（猜 20 万）/ evidence（上下文已超过假设窗口的
    assumed_lift_ratio 还没被压缩，真实窗口更大，不收紧）/ none（连模型名都没有，不改阈值）。"""
    cw = d.get("context_window") if isinstance(d.get("context_window"), dict) else {}
    for v in (cw.get("context_window_size"), d.get("context_window_size")):
        if _num(v):
            return _num(v), "payload", False
    w = session_window(sid)
    if w:
        return w, "statusline", False
    m = model.lower()
    if "[1m]" in m:
        return 1_000_000.0, "model-id", False
    if not m or m == "<synthetic>":
        return None, "none", False
    aw = num("assumed_window_tokens") or float(DEFAULTS["assumed_window_tokens"])
    if n > aw * ratio("assumed_lift_ratio"):
        return None, "evidence", False
    return aw, "assumed", True


def session_window(sid):
    """状态栏记下的本会话 context_window_size（state/ctx_window.json）；没有、过期（7 天）或出错返回 None。"""
    try:
        e = (json.load(open(os.path.join(STATE, "ctx_window.json"))) or {}).get(sid) if sid else None
        if isinstance(e, dict) and time.time() - (_num(e.get("ts")) or 0) < 7 * 86400:
            return _num(e.get("w"))
    except Exception:
        pass
    return None


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
USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
# 认识、但不算上下文的 usage 字段（R6M：其余带 token 的数字字段记进 mode.json 的 usage_unknown_fields，提示可能少算）
USAGE_KNOWN_OTHER = ("output_tokens",)


def _usage_total(u):
    """一条 usage 的上下文 token 数（input + cache_read + cache_creation）；字段坏了按 0。"""
    try:
        return sum(int(_num(u.get(k)) or 0) for k in USAGE_KEYS)
    except Exception:
        return 0


def scan_usage(path):
    """从 transcript 尾部找主会话最后一条有效 usage。返回 (usage 或 None, 信息)。
    信息：model（那条回复的模型名）、unreadable（读不到 usage 时 True）、why（原因）、unknown_fields。
    不抛异常：任何出错都记成 unreadable（R6M：读不到 usage 不能静默）。"""
    info = {"model": "", "unreadable": False, "why": "", "unknown_fields": []}
    if not isinstance(path, str) or not path or not os.path.isfile(path):
        info.update(unreadable=True, why="transcript 不存在或没给路径")
        return None, info
    last = None
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); size = f.tell()
            # 先读尾部 400KB；最后几行是很大的 tool_result 时找不到 usage，就往回多读（CG08）
            for win in (400_000, 4_000_000, 16_000_000):
                f.seek(max(0, size - win))
                asst = with_usage = zero = foreign = 0
                odd, unknown = set(), set()       # odd：usage 里完全没有认识的字段；unknown：多出来的 token 字段
                for line in f.read().decode("utf-8", "ignore").splitlines():
                    if '"usage"' not in line and '"assistant"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(o, dict) or o.get("isSidechain"):
                        continue          # 子 agent 的记录不算主会话上下文（R4D/Codex 14）
                    if o.get("type") != "assistant":
                        if '"usage"' in line:
                            foreign += 1  # 有 usage 但不是 Claude 的 assistant 记录（例如 Codex 的格式）
                        continue
                    asst += 1
                    msg = o.get("message") if isinstance(o.get("message"), dict) else {}
                    u = msg.get("usage")
                    if not isinstance(u, dict):
                        continue
                    with_usage += 1
                    if not any(_num(u.get(k)) is not None for k in USAGE_KEYS):
                        odd.update(str(k) for k in u)
                        continue
                    for k, v in u.items():
                        if "token" in str(k) and k not in USAGE_KEYS + USAGE_KNOWN_OTHER and _num(v) is not None:
                            unknown.add(str(k))
                    # R5I：API 报错时写进来的 usage 全是 0（本机 transcript 有 77 条）——跳过，用前面最近一条有效的；
                    # 否则 n=0 会漏发提醒，还会把本会话的去重记录清掉（回到软线以下的分支）
                    if _usage_total(u) > 0:
                        last = u
                        info["model"] = _model_id(msg.get("model"))
                    else:
                        zero += 1
                if last or win >= size:
                    break
    except Exception as e:
        info.update(unreadable=True, why=f"读 transcript 出错（{type(e).__name__}）")
        return None, info
    if last:
        if unknown:
            info["unknown_fields"] = sorted(unknown)
            info["why"] = "usage 里有不认识的 token 字段 %s,上下文可能少算" % ",".join(sorted(unknown))
        return last, info
    if odd:
        info.update(unreadable=True, why="usage 字段名不认识（%s）" % ",".join(sorted(odd)[:6]))
    elif zero:
        info.update(unreadable=True, why="usage 全为 0（API 报错记录）")
    elif asst and not with_usage:
        info.update(unreadable=True, why="assistant 记录里没有 usage 字段")
    elif foreign:
        info.update(unreadable=True, why="有 usage 但不是 Claude assistant 记录的格式")
    else:
        info["why"] = "还没有带 usage 的回复（新会话）"
    return None, info


def last_usage(path):
    """旧接口：只返回 usage（找不到返回 None）。"""
    return scan_usage(path)[0]


def main():
    try:
        d = json.load(sys.stdin)
        if not isinstance(d, dict):
            return
        cwd = d.get("cwd") or ""
        sid = d.get("session_id")
        mode, mult, why = get_mode()
        last, info = scan_usage(d.get("transcript_path"))
        n = _usage_total(last) if last else 0
        model = info.get("model") or _model_id(d.get("model"))
        win, src, assumed = resolve_window(d, sid, model, n)
        limit, strong, note = lines(cwd, mult, window=win, assumed=assumed)
        if note:
            why += "," + note
        try:
            # 调试用：属于最后一次提交消息的会话（statusline 只读其中的 mult）
            _atomic_write(STATE + "/mode.json", {"ts": int(time.time()), "mode": mode, "mult": mult, "why": why,
                                                 "soft_line": limit, "strong_line": strong,
                                                 "window": int(win) if win else None, "window_source": src,
                                                 "model": model or None, "ctx_tokens": n if last else None,
                                                 "usage_unreadable": bool(info.get("unreadable")),
                                                 "usage_why": info.get("why") or "",
                                                 "usage_unknown_fields": info.get("unknown_fields") or []})
        except Exception:
            pass
        if not last:
            return
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
