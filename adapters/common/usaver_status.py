#!/usr/bin/env python3
"""usaver-status：给没有可编程状态栏的 AI 工具用的一行用量显示（第 5 轮 H）。

输出格式与 hooks/statusline.sh 一致：
    gpt-6.1-sol | ctx 224k | 5h 39% | 7d 5% | ⚠ 到线：做完这步就 /new

  usaver-status                       # 自动识别：cwd 下最近活动的 Codex / Claude Code 会话
  usaver-status --tool codex          # 只看 Codex
  usaver-status --watch [秒]          # 在另一个终端窗格里实时刷新（默认 3 秒）
  usaver-status --cwd DIR             # 指定项目目录（默认当前目录；tmux 里传 #{pane_current_path}）
  usaver-status --json                # 机器可读

数据来源（只读，读文件尾部，几十毫秒）：
- Codex：$CODEX_HOME/sessions/**/rollout-*.jsonl（cwd 来自 session_meta）。上下文 = 最近一次请求的 input_tokens
  （token_usage_record.usage 或 token_count.info.last_token_usage，含缓存命中）；窗口 = info.model_context_window；
  额度 = token_count.rate_limits 的 primary(300 分钟)/secondary(10080 分钟) used_percent；模型 = turn_context.model。
- Claude Code：~/.claude/projects/**/<session>.jsonl 最后一条 assistant 的 usage（input+cache_read+cache_creation）；
  额度读套件 state/rate.json（statusline.sh 写的，跨会话合并过）。
到线阈值：调 hooks/context_guard.lines(cwd, 档位倍数)，和状态栏、守卫同一套（config.json + 项目覆盖 + Pro 封顶）。
档位倍数：Claude 用 state/mode.json（6 小时内有效）；Codex 用 Codex 自己的额度读数按 context_guard.get_mode() 算。
已知模型窗口时，软线/强线另外不超过窗口的 60% / 80%（新模型窗口更小时不会「线在窗口外、永远不提醒」）。
任何异常都输出兜底行并 exit 0（可以放心挂进 tmux / 提示符）。
"""
import glob, json, os, sys, time

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
STATE = os.environ.get("USAGE_SAVER_STATE") or os.path.join(REPO, "state")
FALLBACK = "? | ctx ? | 5h ? | 7d ? | usaver-status: 没找到会话"
TAIL = 512 * 1024
CLEAR_CMD = {"claude": "/clear", "codex": "/new"}
WIN_SOFT, WIN_STRONG = 0.6, 0.8
IDLE_NOTE = 30 * 60


def tail_lines(path, size=TAIL):
    with open(path, "rb") as f:
        f.seek(0, 2)
        end = f.tell()
        f.seek(max(0, end - size))
        data = f.read()
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[1:] if end > size else lines              # 第一行可能被截断


def head_json(path, n=5):
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for _ in range(n):
                l = f.readline()
                if not l:
                    break
                try:
                    out.append(json.loads(l))
                except Exception:
                    pass
    except Exception:
        pass
    return out


def _real(p):
    try:
        return os.path.realpath(p)
    except Exception:
        return p or ""


def _under(child, parent):
    c, p = _real(child), _real(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


# ------------------------------------------------------------------ Codex
def codex_candidates(cwd, max_files=40):
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    files = glob.glob(os.path.join(home, "sessions", "*", "*", "*", "rollout-*.jsonl"))
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for p in files[:max_files]:
        meta = next((j.get("payload") or {} for j in head_json(p) if j.get("type") == "session_meta"), {})
        if cwd is None or _under(meta.get("cwd") or "", cwd) or _under(cwd, meta.get("cwd") or "/nonexistent"):
            yield p, meta


def _ts(s):
    try:
        import datetime as dt
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def codex_state(path):
    model = window = ctx = None
    rate = None
    samples = []
    for l in tail_lines(path):
        try:
            j = json.loads(l)
        except Exception:
            continue
        t, p = j.get("type"), j.get("payload")
        if not isinstance(p, dict):
            continue
        if t == "turn_context":
            model = p.get("model") or model
        elif t == "token_usage_record":
            u = p.get("usage") or {}
            if (u.get("input_tokens") or 0) > 0:
                ctx = u.get("input_tokens")
        elif t == "event_msg" and p.get("type") in ("token_count", "task_started"):
            info = p.get("info") or {}
            window = info.get("model_context_window") or p.get("model_context_window") or window
            u = info.get("last_token_usage") or {}
            if (u.get("input_tokens") or 0) > 0:
                ctx = u.get("input_tokens")
            if isinstance(p.get("rate_limits"), dict):
                rate = p["rate_limits"]
                w = rate.get("primary") or {}
                ts = _ts(j.get("timestamp"))
                if ts and w.get("window_minutes") == 300 and w.get("used_percent") is not None:
                    samples.append((ts, float(w["used_percent"]), w.get("resets_at")))
    return dict(model=model, window=window, ctx=ctx, rate=rate, samples=samples)


def codex_rate_shape(rl, now):
    """Codex rate_limits → statusline 的 rate.json 形状；过了重置时间的窗口作废。"""
    out = {}
    for k in ("primary", "secondary"):
        w = (rl or {}).get(k)
        if not isinstance(w, dict) or w.get("used_percent") is None:
            continue
        name = {300: "five_hour", 10080: "seven_day"}.get(w.get("window_minutes"))
        if name and (w.get("resets_at") or 0) > now:
            out[name] = {"used_percentage": w["used_percent"], "resets_at": w["resets_at"], "sampled_at": int(now)}
    return out


# ------------------------------------------------------------------ Claude Code
def claude_candidates(cwd, max_files=40):
    root = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")
    files = [p for p in glob.glob(os.path.join(root, "*", "*.jsonl"))]      # 不含 subagents/ 子目录
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for p in files[:max_files]:
        c = None
        for j in head_json(p, 20):
            if j.get("cwd"):
                c = j["cwd"]; break
        if c is None:                                          # 头几行没有 cwd：看尾部
            for l in reversed(tail_lines(p, 64 * 1024)):
                try:
                    c = json.loads(l).get("cwd")
                except Exception:
                    continue
                if c:
                    break
        if cwd is None or (c and (_under(c, cwd) or _under(cwd, c))):
            yield p, {"cwd": c}


def claude_state(path):
    model = ctx = None
    for l in tail_lines(path):
        if '"usage"' not in l:
            continue
        try:
            o = json.loads(l)
        except Exception:
            continue
        if o.get("type") != "assistant" or o.get("isSidechain"):
            continue
        m = o.get("message") or {}
        u = m.get("usage") or {}
        if u:
            ctx = sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            model = m.get("model") or model
    rate = {}
    try:
        r = json.load(open(os.path.join(STATE, "rate.json")))
        now = time.time()
        rate = {k: v for k, v in (r.get("rate_limits") or {}).items()
                if isinstance(v, dict) and (v.get("resets_at") or 0) > now}
    except Exception:
        pass
    return dict(model=model, window=None, ctx=ctx, rate=rate, samples=[])


# ------------------------------------------------------------------ 5 小时窗口用完时间预测
ETA_LOOKBACK, ETA_MIN_SPAN, HIST_KEEP = 90 * 60, 5 * 60, 300


def update_history(tool, rate, samples, now):
    """把这次的 5 小时读数（和 Codex 日志里自带的历史读数）并进 state/status_hist_<tool>.json，返回合并后的列表。"""
    path = os.path.join(STATE, "status_hist_%s.json" % tool)
    try:
        hist = [tuple(x) for x in json.load(open(path))]
    except Exception:
        hist = []
    fh = rate.get("five_hour") or {}
    if fh.get("used_percentage") is not None:
        samples = list(samples) + [(now, float(fh["used_percentage"]), fh.get("resets_at"))]
    merged = {}
    for t, p, r in hist + [tuple(x) for x in samples]:
        if t and now - t <= 6 * 3600:
            merged[round(t)] = (round(t), p, r)
    out = sorted(merged.values())[-HIST_KEEP:]
    try:
        os.makedirs(STATE, exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w") as f:
            json.dump(out, f)
        os.replace(tmp, path)
    except Exception:
        pass
    return out


def eta_5h(hist, now):
    """按最近 90 分钟（同一个重置窗口内）的消耗速度，估 5 小时窗口何时到 100%。估不了或重置前用不完返回 None。"""
    if not hist:
        return None
    reset = hist[-1][2]
    pts = [(t, p) for t, p, r in hist if r == reset and now - t <= ETA_LOOKBACK]
    if len(pts) < 2 or pts[-1][0] - pts[0][0] < ETA_MIN_SPAN:
        return None
    slope = (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])      # 百分点 / 秒
    if slope <= 0:
        return None
    eta = pts[-1][0] + (100 - pts[-1][1]) / slope
    return eta if (not reset or eta < reset) else None


# ------------------------------------------------------------------ 阈值与渲染
def guard():
    sys.path.insert(0, os.path.join(REPO, "hooks"))
    import context_guard as G
    return G


def thresholds(tool, cwd, rate, now):
    """(软线, 强线, 档位名)。和 statusline.sh 同一个 lines()；Codex 的档位用 Codex 自己的额度读数算。"""
    G = guard()
    mode = None
    if tool == "codex":
        old = G.STATE
        d = os.path.join(STATE, "codex")
        try:
            os.makedirs(d, exist_ok=True)
            G._atomic_write(os.path.join(d, "rate.json"), {"ts": int(now), "source": "codex", "rate_limits": rate})
            G.STATE = d
            mode, mult, _ = G.get_mode(now)
        except Exception:
            mult = 1.0
        finally:
            G.STATE = old
    else:
        try:
            m = json.load(open(os.path.join(STATE, "mode.json")))
            mult = m.get("mult", 1.0) if now - m.get("ts", 0) < 6 * 3600 else 1.0
            mode = m.get("mode") if now - m.get("ts", 0) < 6 * 3600 else None
        except Exception:
            mult = 1.0
    soft, strong, _ = G.lines(cwd or "", mult)
    return soft, strong, mode


def status(tool="auto", cwd=None, now=None):
    now = now or time.time()
    cands = []
    if tool in ("auto", "codex"):
        cands += [("codex", p, m) for p, m in list(codex_candidates(cwd))[:1]]
    if tool in ("auto", "claude"):
        cands += [("claude", p, m) for p, m in list(claude_candidates(cwd))[:1]]
    if not cands:
        return None
    kind, path, meta = max(cands, key=lambda c: os.path.getmtime(c[1]))
    st = codex_state(path) if kind == "codex" else claude_state(path)
    rate = codex_rate_shape(st["rate"], now) if kind == "codex" else st["rate"]
    soft, strong, mode = thresholds(kind, meta.get("cwd") or cwd, rate, now)
    note = None
    if st["window"]:
        s2, t2 = int(st["window"] * WIN_SOFT), int(st["window"] * WIN_STRONG)
        if s2 < soft or t2 < strong:
            note = "按窗口 %dk 收紧" % (st["window"] // 1000)
        soft, strong = min(soft, s2), min(strong, t2)
        strong = max(strong, soft + 1)
    eta = eta_5h(update_history(kind, rate, st["samples"], now), now)
    return dict(tool=kind, path=path, cwd=meta.get("cwd"), model=st["model"], ctx=st["ctx"], window=st["window"],
                rate=rate, soft=soft, strong=strong, mode=mode, window_note=note, eta_5h=eta,
                idle_s=int(now - os.path.getmtime(path)), savings=savings(kind, path))


def savings(kind, path):
    """R6P：节约表的数（只读 state/savings.jsonl，和 Claude Code 状态栏同一个数）。没有就返回 None。"""
    try:
        sys.path.insert(0, os.path.join(REPO, "hooks"))
        import savings_meter as SM
    except Exception:                                      # 没装节约表（只拷了 adapters）：不显示这一段
        return None
    try:
        sid = os.path.splitext(os.path.basename(path))[0] if kind == "claude" else None
        jp = os.path.join(STATE, "savings.jsonl")
        if not os.path.exists(jp):                         # 这台机器上节约表从没跑过（例如只用 Codex）：不显示这一段
            return None
        r = SM.totals_from_jsonl(jp, sid)
        if r is None:                                      # P2：账本还是空的 → 显示 0%（所有者要求一直显示）
            r = {"cum_pct": 0.0, "sess_pct": None, "cum_actual": 0.0, "cum_saved": 0.0, "by_mech": {}}
        if r.get("cum_pct") is None:
            r["cum_pct"] = 0.0
        if sid and r.get("sess_pct") is None:
            r["sess_pct"] = 0.0
        r["detail"] = SM.breakdown(r.get("by_mech"), r.get("cum_actual"), r.get("cum_saved")) if hasattr(SM, "breakdown") else ""
        return r
    except Exception:
        return {"error": True}


def render(s):
    if not s:
        return FALLBACK
    pct = lambda k: "?" if (s["rate"].get(k) or {}).get("used_percentage") is None else "%d%%" % round(s["rate"][k]["used_percentage"])
    n = s["ctx"]
    ctx = "?" if n is None else "%dk" % (n // 1000)
    if n is not None and s["window"]:
        ctx += "/%dk" % (s["window"] // 1000)
    line = "%s | ctx %s | 5h %s | 7d %s" % (s["model"] or s["tool"], ctx, pct("five_hour"), pct("seven_day"))
    if s.get("eta_5h"):
        line = line.replace(" | 7d", " (约 %s 用完) | 7d" % time.strftime("%H:%M", time.localtime(s["eta_5h"])), 1)
    clear = CLEAR_CMD.get(s["tool"], "开新会话")
    if n is not None and n >= s["strong"]:
        line += " | ⚠⚠ 很长了：先写交接再 %s" % clear
    elif n is not None and n >= s["soft"]:
        line += " | ⚠ 到线：做完这步就 %s" % clear
    if s["idle_s"] >= IDLE_NOTE:
        line += " | 闲置 %d 分钟" % (s["idle_s"] // 60)
    sv = s.get("savings") or {}
    if sv.get("error"):                                    # P2：一直显示；读不了账本就显示 ?
        line += " | 省≈?"
    elif sv.get("cum_pct") is not None:                    # R6P：节约表（估算、各机制上限，不是 A/B 实测）
        line += " | 省≈%.1f%%累计" % sv["cum_pct"]
        if sv.get("sess_pct") is not None:
            line += " 本会话%.1f%%" % sv["sess_pct"]
        line += "(估·上限)" + (sv.get("detail") or "")    # P2：累计的分项，例如（关Artifact 9.8、测试过滤 0.0 个百分点）
    return line


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    tool, cwd, watch, as_json = "auto", os.getcwd(), None, False
    i = 0
    while i < len(a):
        x = a[i]
        if x == "--tool" and i + 1 < len(a):
            tool = a[i + 1]; i += 1
        elif x == "--cwd" and i + 1 < len(a):
            cwd = a[i + 1]; i += 1
        elif x == "--any":
            cwd = None                                   # 不按目录过滤：全机最近的会话
        elif x == "--watch":
            watch = 3.0
            if i + 1 < len(a) and a[i + 1].replace(".", "", 1).isdigit():
                watch = float(a[i + 1]); i += 1
        elif x == "--json":
            as_json = True
        elif x in ("-h", "--help"):
            print(__doc__); return 0
        i += 1
    if tool not in ("auto", "codex", "claude"):
        print("usaver-status: --tool 只能是 auto/codex/claude", file=sys.stderr); return 2

    def once():
        try:
            s = status(tool, cwd)
        except Exception as e:
            return FALLBACK if not as_json else json.dumps({"error": repr(e)})
        return json.dumps(s, ensure_ascii=False) if as_json else render(s)
    if not watch:
        print(once()); return 0
    try:
        while True:
            line = once()
            sys.stdout.write("\r\033[K" + line if sys.stdout.isatty() else line + "\n"); sys.stdout.flush()
            time.sleep(watch)
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
