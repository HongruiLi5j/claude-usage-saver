#!/usr/bin/env python3
"""第 5 轮 C：配对实验的用量汇总。用法：python3 tools/round5/C_measure.py <R5C_ROOT>
- opus 方式：读 result.json 的 modelUsage（含子调用），按 weekly_compare 口径折成"成本单位"：
  cache_read×0.1 + 5m 写入×1.25 + 1h 写入×2 + input×1 + output×5。这是**实测**。
- codex 方式：Claude 侧只花"派发 + 读结论"。字数→token 用第 5 轮标定（ASCII 0.43、非 ASCII 1.39 token/字，±20%），
  这部分是**估算**。主会话侧两种方式都要付的部分（写 brief、读结果）单独算，方便对比。
- 泄题审计：检查执行者有没有读工作区外的项目文件（reports/ backups/ 等），有就标出来。
"""
import json, os, re, sys, glob

ROOT = sys.argv[1]
PROJ = os.path.expanduser("~/Documents/Projects/claude-usage-saver")
N_AFTER = int(os.environ.get("N_AFTER", 25))   # 结果留在主会话里之后被读的次数（第 5 轮实测中位数约 25）
CTX = int(os.environ.get("CTX", 103000))       # 启用后主会话平均上下文（第 5 轮实测）


def tok(s):
    a = sum(1 for c in s if ord(c) < 128)
    return round(0.43 * a + 1.39 * (len(s) - a))


def units_from_model_usage(mu):
    u = 0.0; d = dict(inp=0, out=0, cr=0, cw5=0, cw1=0)
    for m in mu.values():
        d['inp'] += m.get('inputTokens', 0); d['out'] += m.get('outputTokens', 0)
        d['cr'] += m.get('cacheReadInputTokens', 0)
        d['cw5'] += m.get('cacheCreationEphemeral5mInputTokens', 0)
        d['cw1'] += m.get('cacheCreationEphemeral1hInputTokens', 0)
        rest = m.get('cacheCreationInputTokens', 0) - m.get('cacheCreationEphemeral5mInputTokens', 0) - m.get('cacheCreationEphemeral1hInputTokens', 0)
        d['cw5'] += max(rest, 0)
    u = 0.1 * d['cr'] + 1.25 * d['cw5'] + 2 * d['cw1'] + d['inp'] + 5 * d['out']
    return u, d


def main_side(dispatch_tok, result_tok):
    # 主会话：brief 是 assistant 输出（×5），下一次请求写缓存（1h ×2），之后每次请求读 ×0.1
    return dispatch_tok * (5 + 2 + 0.1 * N_AFTER) + result_tok * (2 + 0.1 * N_AFTER)


timing = {}
for line in open(os.path.join(ROOT, "timing.txt")):
    m = re.match(r"(\w+) (\w+) (\d+) secs=(\d+)", line.strip())
    if m and int(m.group(4)) > 5:
        timing[(m.group(1), m.group(2), m.group(3))] = int(m.group(4))

rows = []
for d in sorted(glob.glob(os.path.join(ROOT, "runs", "*_*_[0-9]"))):
    base = os.path.basename(d)
    task, method, rep = base.rsplit("_", 2)
    concl_p = os.path.join(d, "concl.md")
    if not os.path.exists(concl_p):
        continue
    concl = open(concl_p).read()
    if not concl.strip() or concl.startswith("（没有 result）") or concl.startswith("结论：Codex 额度已用完"):
        print(f"跳过（没完成）：{base}"); continue
    concl = re.sub(r"\n<!-- codex_task .*?-->\n?", "", concl)
    task_txt = open(os.path.join(PROJ, "tools/round5/C_tasks", task + ".md")).read()
    r = dict(task=task, method=method, rep=rep, secs=timing.get((method, task, rep)),
             concl_chars=len(concl), concl_tok=tok(concl), task_tok=tok(task_txt))
    leak = []
    if method == "opus":
        res = json.load(open(os.path.join(d, "result.json")))
        u, det = units_from_model_usage(res.get("modelUsage", {}))
        r.update(sub_units=round(u), turns=res.get("num_turns"), usd=res.get("total_cost_usd"), **det)
        for l in open(os.path.join(d, "stream.jsonl")):
            if '"tool_use"' not in l:
                continue
            try:
                ev = json.loads(l)
            except Exception:
                continue
            for c in ev.get("message", {}).get("content", []) or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    s = json.dumps(c.get("input"), ensure_ascii=False)
                    if PROJ in s:   # 只算读真实项目的绝对路径；工作区里的假 HOME 路径不算
                        leak.append(s[:160])
    else:
        raw = open(concl_p).read()
        meta = re.search(r"codex_tokens=(\S+)", raw); ms = re.search(r"secs=(\d+)", raw)
        r.update(sub_units=0, codex_tokens=meta.group(1) if meta else "?")
        if ms: r["secs"] = int(ms.group(1))
        logp = os.path.join(d, "codex.log")
        if os.path.exists(logp):
            for l in open(logp, errors="replace"):
                if l.startswith("/bin/") and PROJ in l:
                    leak.append(l.strip()[:160])
    r["leak"] = leak
    # Claude 侧总成本：主会话（两种方式都付） + 子 agent（只有 opus 方式）
    r["main_units"] = round(main_side(r["task_tok"], r["concl_tok"]))
    r["claude_units"] = r["main_units"] + r["sub_units"]
    rows.append(r)

json.dump(rows, open(os.path.join(ROOT, "measure.json"), "w"), ensure_ascii=False, indent=1)
print(f"N_AFTER={N_AFTER} CTX={CTX}（codex 后台跑再 Read 一次 ≈ +{int(CTX*0.1)} 单位/次，未计入下表）")
print("task          method rep  secs turns  sub_units  main_units  claude_units  concl_chars codex_tok leak")
for r in rows:
    print(f"{r['task']:13} {r['method']:6} {r['rep']:>3} {r['secs'] or '-':>5} {r.get('turns') or '-':>5} {r['sub_units']:>10} {r['main_units']:>11} {r['claude_units']:>13} {r['concl_chars']:>12} {r.get('codex_tokens','-'):>9} {len(r['leak'])}")
    for x in r['leak'][:3]:
        print("    LEAK?", x)
