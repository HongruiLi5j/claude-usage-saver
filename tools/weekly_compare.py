#!/usr/bin/env python3
"""每周对比：改动前（10-21..11-05）vs 最近 7 天的真实用量。排除基准/测试会话（-private-tmp- 目录）。
输出 reports/weekly_<日期>.md，并追加 reports/weekly_history.json。只读会话记录，不改任何设置。"""
import json, glob, os, collections, datetime as dt
BASE = os.path.expanduser('~/Documents/Projects/claude-usage-saver')
ROOT = os.path.expanduser('~/.claude/projects')
W = dict(cr=0.1, cw=1.0, inp=1.0, out=5.0)           # API 价格比；cw 已在下面按 5 分钟 1.25×、1 小时 2× 折好
UTC = dt.timezone.utc
BASE_FROM = dt.datetime(2026, 10, 21, tzinfo=UTC)
CHANGE = dt.datetime(2026, 11, 6, 7, 58, tzinfo=UTC)   # 套件启用时刻 = 11-05 23:58 PST（WC04）
BASE_TO = CHANGE
now = dt.datetime.now(UTC); RECENT_FROM = max(now - dt.timedelta(days=7), CHANGE)
P = {'before': collections.Counter(), 'recent': collections.Counter()}
ctx = {'before': [], 'recent': []}; days = {'before': set(), 'recent': set()}
seen = set()
AG = collections.defaultdict(lambda: collections.defaultdict(list))   # (窗口, 主/子, 模型) → agent → [(时间, 上下文)]（R4C 子 agent 明细）
PARTS = collections.defaultdict(collections.Counter)                     # (窗口, 主/子, 模型) → 成本分项
for f in glob.glob(ROOT + '/**/*.jsonl', recursive=True):
    proj = f.split('/projects/')[1].split('/')[0]
    if proj.startswith('-private-tmp-'):
        continue                                     # 基准、测试
    for line in open(f, errors='ignore'):
        try: j = json.loads(line)
        except Exception: continue
        m = j.get('message') or {}
        u = m.get('usage') if isinstance(m, dict) else None
        if not u or m.get('id') in seen: continue
        seen.add(m.get('id'))
        try: t = dt.datetime.fromisoformat(j['timestamp'].replace('Z', '+00:00'))
        except Exception: continue
        k = 'before' if BASE_FROM <= t < BASE_TO else 'recent' if t >= RECENT_FROM else None
        if not k: continue
        cc = u.get('cache_creation') or {}
        cw_raw = u.get('cache_creation_input_tokens', 0)
        if cc:
            cw = 1.25 * cc.get('ephemeral_5m_input_tokens', 0) + 2.0 * cc.get('ephemeral_1h_input_tokens', 0)
        else:
            cw = 1.25 * cw_raw
        c = dict(cr=u.get('cache_read_input_tokens', 0), cw=cw,
                 inp=u.get('input_tokens', 0), out=u.get('output_tokens', 0))
        cost = sum(c[x] * W[x] for x in W)
        P[k]['cost'] += cost; P[k]['calls'] += 1
        P[k]['opus_cost'] += cost if 'opus' in (m.get('model') or '') else 0
        side = 'sub' if (j.get('isSidechain') or '/subagents/' in f) else 'main'
        mdl = 'opus' if 'opus' in (m.get('model') or '') else 'sonnet' if 'sonnet' in (m.get('model') or '') else 'other'
        P[k][f'cost_{side}_{mdl}'] += cost; P[k][f'calls_{side}_{mdl}'] += 1
        akey = (f + '#' + str(j.get('agentId') or j.get('sessionId'))) if side == 'sub' and '/subagents/' not in f else f
        for g in ((k, side, mdl), (k, 'all', 'all')):
            AG[g][akey].append((t, c['cr'] + cw_raw + c['inp']))
            PARTS[g].update(cr=c['cr'] * W['cr'], cw=cw, out=c['out'] * W['out'], cost=cost)
        ctx[k].append(c['cr'] + cw_raw + c['inp']); days[k].add(t.astimezone().date())   # 按本地日期分天（WC07）
def stat(k):
    n = max(len(days[k]), 1); v = sorted(ctx[k])
    return dict(days=len(days[k]), cost_per_day=P[k]['cost'] / n / 1e6, calls_per_day=P[k]['calls'] / n,
                cost_per_call=P[k]['cost'] / max(P[k]['calls'], 1) / 1e3,
                median_ctx=v[len(v) // 2] if v else 0, mean_ctx=sum(v) // len(v) if v else 0, opus_share=100 * P[k]['opus_cost'] / max(P[k]['cost'], 1))
b, r = stat('before'), stat('recent')
b_raw, r_raw = P['before'], P['recent']
def pcv(Pk, sd, md): return Pk[f'cost_{sd}_{md}'] / Pk[f'calls_{sd}_{md}'] / 1e3 if Pk[f'calls_{sd}_{md}'] else 0
def pc(Pk, sd, md): return f"{pcv(Pk, sd, md):.1f} ({Pk[f'calls_{sd}_{md}']})" if Pk[f'calls_{sd}_{md}'] else '—'
def ch(x, y): return f"{100 * (y - x) / x:+.0f}%" if x else 'n/a'
def row(g, k):
    A = AG[(k,) + g]; Q = PARTS[(k,) + g]; n = sum(len(v) for v in A.values())
    first = sum(min(v)[1] for v in A.values()) / len(A); mean = sum(x for v in A.values() for _, x in v) / n
    win = '改动前' if k == 'before' else '启用后'
    return (f"| {g[0]}·{g[1]} | {win} | {len(A)} | {n / len(A):.1f} | {first / 1e3:.1f}k | {mean / 1e3:.0f}k | "
            f"{Q['cr'] / n / 1e3:.1f} | {Q['cw'] / n / 1e3:.1f} | {Q['out'] / n / 1e3:.1f} | {Q['cost'] / n / 1e3:.1f} |")
L = [f"# 每周用量对比 {now.astimezone().strftime('%Y-%m-%d')}", '',
     '| 指标 | 改动前（10-21 → 11-05 23:58） | 启用后（最多最近 7 天） | 变化 |', '|---|---|---|---|',
     f"| 有记录的天数 | {b['days']} | {r['days']} | |",
     f"| 每天成本（M 单位） | {b['cost_per_day']:.1f} | {r['cost_per_day']:.1f} | {ch(b['cost_per_day'], r['cost_per_day'])} |",
     f"| 每次请求成本（k 单位） | {b['cost_per_call']:.1f} | {r['cost_per_call']:.1f} | {ch(b['cost_per_call'], r['cost_per_call'])} |",
     f"| 每天请求数 | {b['calls_per_day']:.0f} | {r['calls_per_day']:.0f} | {ch(b['calls_per_day'], r['calls_per_day'])} |",
     f"| 每次请求上下文中位数 | {b['median_ctx'] // 1000}k | {r['median_ctx'] // 1000}k | {ch(b['median_ctx'], r['median_ctx'])} |",
     f"| 每次请求上下文平均 | {b['mean_ctx'] // 1000}k | {r['mean_ctx'] // 1000}k | {ch(b['mean_ctx'], r['mean_ctx'])} |",
     f"| Opus 成本占比 | {b['opus_share']:.0f}% | {r['opus_share']:.0f}% | |", '',
     '### 按主会话/子 agent × 模型分开（每次请求成本，k 单位；括号内为请求数）', '',
     '| 分组 | 改动前 | 最近 7 天 | 变化 |', '|---|---|---|---|',
     *[f"| {sd}·{md} | {pc(b_raw, sd, md)} | {pc(r_raw, sd, md)} | {ch(pcv(b_raw, sd, md), pcv(r_raw, sd, md))} |"
       for sd in ('main', 'sub') for md in ('opus', 'sonnet')], '',
     '### 子 agent 明细（任务结构；成本 k 单位/每次请求，平均上下文按请求平均）', '',
     '| 分组 | 窗口 | agent 数 | 每 agent 请求数 | 第一次请求上下文（均值） | 平均上下文 | cache read | cache write | 输出 | 每次合计 |',
     '|---|---|---|---|---|---|---|---|---|---|',
     *[row(g, k) for g in [(sd, md) for sd in ('main', 'sub') for md in ('opus', 'sonnet', 'other')] + [('all', 'all')]
       for k in ('before', 'recent') if AG[(k,) + g]], '',
     '读法：**第一次请求上下文**是套件要盯的数（系统提示 + 工具 + skill 列表 + 首条 prompt），它涨了才说明套件让 prompt 变长；每 agent 请求数、平均上下文、输出涨，多半是任务本身变重。'
     '"all·all" 是主会话 + 子 agent 合计（agent 数 = 主会话文件数 + 子 agent 数），子 agent 单独涨而合计降，往往只是重活从主会话搬到了子 agent。'
     '三项分项之外还有少量未缓存 input，所以分项之和略小于合计。', '',
     '读法：**每次请求成本**最能说明省额度措施本身的效果（不受你这周用得多还是少影响）；每天成本还取决于工作量。',
     '单位按 API 价格比折算（同一模型内可比；Opus 与 Sonnet 单价不同，跨模型不能直接比），不等于订阅额度百分比。已排除基准/测试会话。',
     '**样本提醒**：最近窗口不足 3 个完整天时，变化百分比没有意义，只看趋势。']
os.makedirs(BASE + '/reports', exist_ok=True)
out = BASE + f"/reports/weekly_{now.astimezone().strftime('%Y%m%d')}.md"
open(out, 'w').write('\n'.join(L))
H = BASE + '/reports/weekly_history.json'
h = json.load(open(H)) if os.path.exists(H) else []
def local_day(ts):
    """history 里的 ts（UTC isoformat）→ 本地日期；和报告文件名、分天用同一口径（R4D）。解析不了返回 None。"""
    try:
        t = dt.datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        return (t if t.tzinfo else t.replace(tzinfo=UTC)).astimezone().date()
    except Exception:
        return None
today = now.astimezone().date()
h = [x for x in h if local_day(x.get('ts')) != today]   # 同一本地日只留最新一条（WC05；R4D 改为本地日期）
h.append(dict(ts=now.isoformat(), before=b, recent=r)); json.dump(h, open(H, 'w'), indent=1)
print('\n'.join(L)); print('\n已写入', out)
