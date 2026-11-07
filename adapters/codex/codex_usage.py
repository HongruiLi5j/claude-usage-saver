#!/usr/bin/env python3
"""Codex CLI 用量统计（第 5 轮 H）：读 ~/.codex/sessions 的 rollout 日志，得出每次请求的 token，
按天 / 模型 / 主会话·子 agent / 交互·exec 汇总，口径与 tools/weekly_compare.py 对齐。只读，不改任何设置。

日志格式（本机 codex-cli 0.163.0 / Desktop 0.164.0-alpha 实测，2026-11-06）：
- 文件：~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl，较旧的被压成 rollout-*.jsonl.zst。
- 每次模型请求一条 {"type":"token_usage_record","payload":{"response_id","thread_id","turn_id","usage":{...}}}；
  usage = input_tokens（含缓存命中）、cached_input_tokens、cache_write_input_tokens、output_tokens（含推理）、
  reasoning_output_tokens、total_tokens（= input + output，3001/3001 条成立）。
- 没有 token_usage_record 的文件（Desktop 的较早格式）退回用 {"type":"event_msg","payload":{"type":"token_count",
  "info":{"last_token_usage":{...},"total_token_usage":{...}},"rate_limits":{...}}}：累计值变化一次算一次请求。
- rate_limits：primary（window_minutes=300，5 小时）/ secondary（10080，7 天）的 used_percent、resets_at、plan_type。
- 模型名来自最近一条 turn_context.payload.model；cwd / originator / source 来自 session_meta
  （source 是 {"subagent":…} 或 thread_source 是 subagent/guardian_review 的算子 agent）。

成本单位（默认与 weekly_compare 一致，便于和 Claude Code 并排看）：缓存命中 ×0.1、未缓存输入 ×1、
缓存写入 ×1、输出 ×5。--weights openai 改用 OpenAI 常见比例（输出 ×8）。两种都**不是订阅额度百分比**。
"""
import argparse, collections, datetime as dt, glob, json, os, subprocess, sys

UTC = dt.timezone.utc
WEIGHTS = {"kit": dict(cr=0.1, inp=1.0, cw=1.0, out=5.0),
           "openai": dict(cr=0.1, inp=1.0, cw=1.0, out=8.0)}
SUB_SOURCES = {"subagent", "guardian_review"}


def read_lines(path):
    """jsonl 直接读；.zst 先试 zstandard 模块，再试 zstd 命令；都不行返回 None（计入 skipped）。"""
    if not path.endswith(".zst"):
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    try:
        import zstandard
        with open(path, "rb") as f:
            data = zstandard.ZstdDecompressor().stream_reader(f).read()
        return data.decode("utf-8", "replace").splitlines()
    except Exception:
        pass
    try:
        r = subprocess.run(["zstd", "-dcq", path], capture_output=True, timeout=60)
        if r.returncode == 0:
            return r.stdout.decode("utf-8", "replace").splitlines()
    except Exception:
        pass
    return None


def parse_ts(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _usage(u):
    """统一成整数五元组；缺字段按 0。"""
    g = lambda k: int(u.get(k) or 0) if isinstance(u, dict) else 0
    return dict(inp=g("input_tokens"), cached=g("cached_input_tokens"), cw=g("cache_write_input_tokens"),
                out=g("output_tokens"), reasoning=g("reasoning_output_tokens"))


def parse_file(path):
    """返回 (records, rate_samples, ok, session_id)。record: ts, model, cwd, side, origin, session, usage 字段。"""
    lines = read_lines(path)
    if lines is None:
        return [], [], False, None
    meta, model = {}, None
    tur, tc, rates = [], [], []
    prev_total = None
    for line in lines:
        try:
            j = json.loads(line)
        except Exception:
            continue
        t, p = j.get("type"), j.get("payload")
        if not isinstance(p, dict):
            continue
        if t == "session_meta" and not meta:
            src = p.get("source")
            sub = (isinstance(src, dict) and "subagent" in src) or p.get("thread_source") in SUB_SOURCES
            meta = dict(cwd=p.get("cwd") or "", session=p.get("session_id") or p.get("id") or path,
                        side="sub" if sub else "main",
                        origin="exec" if (src == "exec" or p.get("originator") == "codex_exec") else "interactive")
        elif t == "turn_context":
            model = p.get("model") or model
        elif t == "token_usage_record":
            u = _usage(p.get("usage"))
            if u["inp"] + u["out"] > 0:
                tur.append(dict(ts=parse_ts(j.get("timestamp")), model=model, key=p.get("response_id"), **u))
        elif t == "event_msg" and p.get("type") == "token_count":
            info = p.get("info") or {}
            total = json.dumps(info.get("total_token_usage"), sort_keys=True)
            u = _usage(info.get("last_token_usage"))
            if info and total != prev_total and u["inp"] + u["out"] > 0:
                tc.append(dict(ts=parse_ts(j.get("timestamp")), model=model, key="tc:" + total, **u))
            if info:
                prev_total = total
            rl = p.get("rate_limits")
            if isinstance(rl, dict):
                rates.append((parse_ts(j.get("timestamp")), rl))
    recs = tur if tur else tc                   # 有逐请求记录就只用它，避免和 token_count 重复计
    m = meta or dict(cwd="", session=path, side="main", origin="interactive")
    for r in recs:
        r.update(m)
        if not r["key"] or r["key"].startswith("tc:"):   # token_count 没有 response_id：用 会话 + 累计值 去重
            r["key"] = "%s#%s" % (m["session"], r["key"] or "ts:%s" % r["ts"])
    return recs, rates, True, m["session"]


def imported_ids(codex_home):
    """Codex Desktop 会把 Claude Code 等外部会话导入成 Codex 线程（external_agent_session_imports.json）。
    这些文件里也有 token_count，但那是外部工具的用量，算进来会和 weekly_compare 重复，默认排除。"""
    try:
        with open(os.path.join(codex_home, "external_agent_session_imports.json")) as f:
            d = json.load(f)
        return {r.get("imported_thread_id") for r in d.get("records") or [] if isinstance(r, dict)} - {None}
    except Exception:
        return set()


def collect(root, since=None, until=None, include_tmp=False, exclude_sessions=frozenset(), cwd_prefix=None):
    files = sorted(glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True) +
                   glob.glob(os.path.join(root, "**", "rollout-*.jsonl.zst"), recursive=True))
    seen, out, rates = set(), [], []
    skipped = imported = 0
    for f in files:
        recs, rs, ok, sid = parse_file(f)
        skipped += not ok
        if sid in exclude_sessions:
            imported += 1
            continue
        rates.extend(rs)
        for r in recs:
            if r["key"] in seen or r["ts"] is None:
                continue
            if since and r["ts"] < since or until and r["ts"] >= until:
                continue
            if cwd_prefix and not os.path.realpath(r["cwd"] or "/").startswith(cwd_prefix):
                continue
            if not cwd_prefix and not include_tmp and (r["cwd"].startswith("/private/tmp/") or r["cwd"].startswith("/tmp/")):
                continue                         # 基准 / 测试（和 weekly_compare 排除 -private-tmp- 同理）
            seen.add(r["key"])
            out.append(r)
    return out, rates, len(files), skipped, imported


def cost(r, w):
    unc = max(r["inp"] - r["cached"] - r["cw"], 0)
    return r["cached"] * w["cr"] + unc * w["inp"] + r["cw"] * w["cw"] + r["out"] * w["out"]


def summarize(recs, w):
    """总体 + 分组统计。上下文 = input_tokens（含缓存命中，即这次请求送进去的全部输入）。"""
    def agg(rs):
        if not rs:
            return dict(calls=0)
        ctx = sorted(r["inp"] for r in rs)
        c = sum(cost(r, w) for r in rs)
        parts = dict(cached=sum(r["cached"] * w["cr"] for r in rs),
                     uncached=sum(max(r["inp"] - r["cached"] - r["cw"], 0) * w["inp"] for r in rs),
                     out=sum(r["out"] * w["out"] for r in rs))
        days = {r["ts"].astimezone().date() for r in rs}
        return dict(calls=len(rs), days=len(days), cost=c, cost_per_call=c / len(rs),
                    cost_per_day=c / max(len(days), 1), median_ctx=ctx[len(ctx) // 2], mean_ctx=sum(ctx) / len(ctx),
                    cache_hit=sum(r["cached"] for r in rs) / max(sum(r["inp"] for r in rs), 1),
                    out_tokens=sum(r["out"] for r in rs), reasoning_tokens=sum(r["reasoning"] for r in rs),
                    share={k: v / c if c else 0 for k, v in parts.items()})
    groups = collections.defaultdict(list)
    for r in recs:
        groups[("model", r["model"] or "?")].append(r)
        groups[("side", r["side"])].append(r)
        groups[("origin", r["origin"])].append(r)
        groups[("day", r["ts"].astimezone().date().isoformat())].append(r)
    return dict(total=agg(recs), groups={"%s=%s" % k: agg(v) for k, v in sorted(groups.items())})


def latest_rate(rates):
    """最近一次 rate_limits，转成和 state/rate.json 同形状（five_hour / seven_day 的 used_percentage、resets_at）。"""
    rates = [x for x in rates if x[0]]
    if not rates:
        return None
    ts, rl = max(rates, key=lambda x: x[0])
    out = {"ts": ts.timestamp(), "source": "codex", "plan_type": rl.get("plan_type"), "rate_limits": {}}
    for k in ("primary", "secondary"):
        win = rl.get(k)
        if not isinstance(win, dict):
            continue
        name = {300: "five_hour", 10080: "seven_day"}.get(win.get("window_minutes"), "%s_%sm" % (k, win.get("window_minutes")))
        out["rate_limits"][name] = {"used_percentage": win.get("used_percent"), "resets_at": win.get("resets_at"),
                                    "window_minutes": win.get("window_minutes"), "sampled_at": ts.timestamp()}
    return out


def fmt_k(x):
    return "%.1fk" % (x / 1e3)


def render(s, rate, meta):
    t = s["total"]
    L = ["# Codex 用量 %s" % dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"), "",
         "数据：%s（%d 个文件，%d 个读不了，%d 个是从外部工具导入的会话已排除）；窗口 %s → %s；%s。成本单位权重 %s（不是订阅额度百分比）。" % (
             meta["root"], meta["files"], meta["skipped"], meta.get("imported", 0), meta["since"], meta["until"],
             "含 /tmp 会话" if meta["include_tmp"] else "排除 /tmp、/private/tmp 下的会话（基准/测试）", meta["weights"]), ""]
    if not t.get("calls"):
        L.append("窗口内没有请求记录。")
    else:
        L += ["| 指标 | 值 |", "|---|---|",
              "| 请求数 | %d（%d 天） |" % (t["calls"], t["days"]),
              "| 总成本（M 单位） | %.2f |" % (t["cost"] / 1e6),
              "| 每天成本（M 单位） | %.2f |" % (t["cost_per_day"] / 1e6),
              "| 每次请求成本（k 单位） | %.1f |" % (t["cost_per_call"] / 1e3),
              "| 每次请求上下文 中位数 / 平均 | %s / %s |" % (fmt_k(t["median_ctx"]), fmt_k(t["mean_ctx"])),
              "| 缓存命中率（cached / input） | %.0f%% |" % (100 * t["cache_hit"]),
              "| 成本构成 缓存命中 / 未缓存输入 / 输出 | %.0f%% / %.0f%% / %.0f%% |" % tuple(100 * t["share"][k] for k in ("cached", "uncached", "out")),
              "| 输出 token（其中推理） | %d（%d） |" % (t["out_tokens"], t["reasoning_tokens"]), "",
              "| 分组 | 请求数 | 每次成本 | 上下文中位数 | 成本占比 |", "|---|---|---|---|---|"]
        for k, g in s["groups"].items():
            L.append("| %s | %d | %s | %s | %.0f%% |" % (k, g["calls"], fmt_k(g["cost_per_call"]), fmt_k(g["median_ctx"]),
                                                     100 * g["cost"] / t["cost"]))
    L.append("")
    if rate and rate.get("rate_limits"):
        L.append("最近一次 Codex 额度读数（%s，plan=%s）：" % (
            dt.datetime.fromtimestamp(rate["ts"]).strftime("%m-%d %H:%M"), rate.get("plan_type")))
        for k, v in rate["rate_limits"].items():
            rs = dt.datetime.fromtimestamp(v["resets_at"]).strftime("%m-%d %H:%M") if v.get("resets_at") else "?"
            L.append("- %s：已用 %s%%，%s 重置" % (k, v.get("used_percentage"), rs))
    else:
        L.append("日志里没有额度读数（rate_limits 为空）。")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Codex CLI usage from ~/.codex/sessions rollout logs (read-only).")
    ap.add_argument("--sessions", default=os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "sessions"))
    ap.add_argument("--days", type=float, default=7, help="最近 N 天（默认 7）；--since 优先")
    ap.add_argument("--since", help="ISO 时间/日期，如 2026-11-01")
    ap.add_argument("--until", help="ISO 时间/日期（不含）")
    ap.add_argument("--include-tmp", action="store_true", help="包括 /tmp 下的会话（默认排除基准/测试）")
    ap.add_argument("--cwd", help="只算 cwd 在这个目录下的会话（给了它就不再排除 /tmp）")
    ap.add_argument("--include-imported", action="store_true",
                    help="包括 Codex Desktop 从 Claude Code 等导入的会话（默认排除，避免重复计）")
    ap.add_argument("--weights", default="kit", help="kit（同 weekly_compare，输出×5）或 openai（输出×8）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是 markdown")
    ap.add_argument("--out", help="把 markdown 写到这个文件（例如 reports/codex_usage_<日期>.md）")
    ap.add_argument("--emit-rate", metavar="PATH", help="把最近额度读数按 state/rate.json 的形状写到 PATH（原子写）")
    a = ap.parse_args(argv)
    now = dt.datetime.now(UTC)

    def when(s):
        d = parse_ts(s)
        return d.replace(tzinfo=d.tzinfo or dt.datetime.now().astimezone().tzinfo) if d else None
    since = when(a.since) if a.since else now - dt.timedelta(days=a.days)
    until = when(a.until) if a.until else None
    w = WEIGHTS.get(a.weights)
    if w is None:
        ap.error("--weights 只能是 %s" % "/".join(WEIGHTS))
    home = os.path.dirname(os.path.abspath(a.sessions))
    excl = frozenset() if a.include_imported else frozenset(imported_ids(home))
    recs, rates, nfiles, skipped, nimp = collect(a.sessions, since, until, a.include_tmp, excl,
                                                os.path.realpath(a.cwd) if a.cwd else None)
    s = summarize(recs, w)
    rate = latest_rate(rates)
    meta = dict(root=a.sessions, files=nfiles, skipped=skipped, imported=nimp, since=since.astimezone().strftime("%Y-%m-%d %H:%M"),
                until=until.astimezone().strftime("%Y-%m-%d %H:%M") if until else "现在", include_tmp=a.include_tmp,
                weights=a.weights)
    if a.emit_rate and rate:
        os.makedirs(os.path.dirname(os.path.abspath(a.emit_rate)), exist_ok=True)
        tmp = "%s.%d.tmp" % (a.emit_rate, os.getpid())
        with open(tmp, "w") as f:
            json.dump(rate, f)
        os.replace(tmp, a.emit_rate)
    if a.json:
        print(json.dumps(dict(meta=meta, summary=s, rate=rate), default=str, ensure_ascii=False, indent=1))
        return 0
    md = render(s, rate, meta)
    if a.out:
        with open(a.out, "w") as f:
            f.write(md)
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
