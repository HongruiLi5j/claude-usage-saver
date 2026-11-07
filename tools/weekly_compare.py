#!/usr/bin/env python3
"""每周对比：改动前（10-21..11-05）vs 最近 7 天的真实用量。排除基准/测试会话（-private-tmp- 目录）。
输出 reports/weekly_<日期>.md，并追加 reports/weekly_history.json。只读会话记录，不改任何设置。

R6P 改动：
- 价格用共享价格表 tools/pricing.py（按模型族/版本；官方页：5.5 代缓存读 = 0.05× 本模型输入价，旧模型 0.1×）。
  pricing.py 缺失时退回内置的通用倍数（input 1、写入 1.25/2、缓存读 0.1、output 5），报告里写明。
- 两种口径都报：「本模型单位」（每个模型按自己的输入价归一，跨模型不可比，兼容旧数字）和
  「Sonnet 5.5 当量」（= API 美元 ÷ $2/M；Opus 5.5 的同样 token 记 2 倍，F1 采用的额度折算）。
- 每天成本只算**完整的本地日**（窗口覆盖 ≥98% 的那天）；窗口两头的零头天（例如 11-05 23:58 之后的 17 次请求）
  剔除并列出。理由：每天成本要同样长度的天才可比；按活跃小时折算会把「用得多久」和「用得多狠」混在一起。
  history 里的旧字段（days / cost_per_day / calls_per_day，按「有记录的天」）保留不变，给旧读者用。
- 分组按实际出现的模型族动态生成（opus / sonnet / haiku / fable …；不认识的族原样列出，不并进 other）。"""
import json, glob, os, collections, datetime as dt
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # CG13（R5I）：按脚本位置定套件目录，不写死路径
ROOT = os.path.expanduser('~/.claude/projects')
try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location('usaver_pricing', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pricing.py'))
    PR = _ilu.module_from_spec(_spec); _spec.loader.exec_module(PR)
except Exception:
    PR = None                                         # 退回内置倍数（报告里会写明）
UTC = dt.timezone.utc
BASE_FROM = dt.datetime(2026, 10, 21, tzinfo=UTC)
CHANGE = dt.datetime(2026, 11, 6, 7, 58, tzinfo=UTC)   # 套件启用时刻 = 11-05 23:58 PST（WC04）
BASE_TO = CHANGE
now = dt.datetime.now(UTC); RECENT_FROM = max(now - dt.timedelta(days=7), CHANGE)
WIN = {'before': (BASE_FROM, BASE_TO), 'recent': (RECENT_FROM, now)}
FULL_DAY_COVER = 0.98


def costs(model, u):
    """→ (本模型单位, Sonnet 5.5 当量, 美元, 分项{cr,cw,out} 当量, flag, 族)。"""
    if PR:
        nat, flag = PR.native(model, u)
        seq, _ = PR.weighted(model, u)
        usd, _ = PR.usd(model, u)
        p = PR.parts(u)
        price, _ = PR.lookup(model, p['inp'] + p['cw5m'] + p['cw1h'] + p['cr'])
        r = PR.REF_INPUT_USD
        sub = dict(cr=p['cr'] * price['cr'] / r, cw=(p['cw5m'] * price['cw5m'] + p['cw1h'] * price['cw1h']) / r,
                   out=p['out'] * price['out'] / r)
        return nat, seq, usd, sub, flag, PR.family(model)
    cc = u.get('cache_creation') or {}
    cw_raw = u.get('cache_creation_input_tokens', 0) or 0
    cw = (1.25 * cc.get('ephemeral_5m_input_tokens', 0) + 2.0 * cc.get('ephemeral_1h_input_tokens', 0)) if cc else 1.25 * cw_raw
    cr, out = (u.get('cache_read_input_tokens', 0) or 0) * 0.1, (u.get('output_tokens', 0) or 0) * 5.0
    nat = cr + cw + (u.get('input_tokens', 0) or 0) + out
    m = (model or '').lower()
    fam = next((f for f in ('fable', 'mythos', 'opus', 'sonnet', 'haiku') if f in m), model or '<无模型名>')
    return nat, nat, None, dict(cr=cr, cw=cw, out=out), 'no_pricing_py', fam


P = {'before': collections.Counter(), 'recent': collections.Counter()}
ctx = {'before': [], 'recent': []}; days = {'before': set(), 'recent': set()}
DAYC = {'before': collections.defaultdict(collections.Counter), 'recent': collections.defaultdict(collections.Counter)}
HOURS = {'before': set(), 'recent': set()}
FLAGS = collections.Counter(); UNKNOWN = collections.Counter()
FAMS = collections.Counter()
seen = set()
AG = collections.defaultdict(lambda: collections.defaultdict(list))   # (窗口, 主/子, 族) → agent → [(时间, 上下文)]（R4C 子 agent 明细）
PARTS = collections.defaultdict(collections.Counter)                     # (窗口, 主/子, 族) → 成本分项（Sonnet 当量）
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
        model = m.get('model') or ''
        nat, seq, usd, sub, flag, fam = costs(model, u)
        if flag == 'synthetic':
            continue                                 # 客户端合成的报错消息，usage 全 0
        if flag:
            FLAGS[flag] += 1
            if flag in ('unknown', 'family_fallback'):
                UNKNOWN[model] += 1
        cw_raw = u.get('cache_creation_input_tokens', 0) or 0
        P[k]['cost'] += nat; P[k]['seq'] += seq; P[k]['usd'] += usd or 0; P[k]['calls'] += 1
        for kk, field in (('t_inp', 'input_tokens'), ('t_cw', 'cache_creation_input_tokens'),
                          ('t_cr', 'cache_read_input_tokens'), ('t_out', 'output_tokens')):
            P[k][kk] += u.get(field, 0) or 0
        P[k]['opus_seq'] += seq if fam == 'opus' else 0
        side = 'sub' if (j.get('isSidechain') or '/subagents/' in f) else 'main'
        FAMS[fam] += seq
        P[k][f'cost_{side}_{fam}'] += nat; P[k][f'seq_{side}_{fam}'] += seq; P[k][f'calls_{side}_{fam}'] += 1
        akey = (f + '#' + str(j.get('agentId') or j.get('sessionId'))) if side == 'sub' and '/subagents/' not in f else f
        for g in ((k, side, fam), (k, 'all', 'all')):
            AG[g][akey].append((t, (u.get('cache_read_input_tokens', 0) or 0) + cw_raw + (u.get('input_tokens', 0) or 0)))
            PARTS[g].update(cr=sub['cr'], cw=sub['cw'], out=sub['out'], cost=seq)
        lt = t.astimezone()
        ctx[k].append((u.get('cache_read_input_tokens', 0) or 0) + cw_raw + (u.get('input_tokens', 0) or 0))
        days[k].add(lt.date())   # 按本地日期分天（WC07）
        DAYC[k][lt.date()].update(cost=nat, seq=seq, calls=1)
        HOURS[k].add(lt.replace(minute=0, second=0, microsecond=0))


def full_days(k):
    """窗口内覆盖 ≥98% 的本地日（不论那天有没有请求）；返回 (完整日列表, 有数据但不完整的日列表)。"""
    lo, hi = WIN[k]
    if hi <= lo:
        return [], sorted(days[k])
    d0, d1 = lo.astimezone().date(), hi.astimezone().date()
    full = []
    d = d0
    while d <= d1:
        s = dt.datetime(d.year, d.month, d.day).astimezone()
        e = (dt.datetime(d.year, d.month, d.day) + dt.timedelta(days=1)).astimezone()
        cover = (min(e, hi) - max(s, lo)).total_seconds() / max((e - s).total_seconds(), 1)
        if cover >= FULL_DAY_COVER:
            full.append(d)
        d += dt.timedelta(days=1)
    partial = sorted(x for x in days[k] if x not in set(full))
    return full, partial


def stat(k):
    n = max(len(days[k]), 1); v = sorted(ctx[k])
    fd, part = full_days(k)
    fc = collections.Counter()
    for d in fd:
        fc.update(DAYC[k].get(d, {}))
    nf = len(fd)
    calls = max(P[k]['calls'], 1)
    return dict(days=len(days[k]), cost_per_day=P[k]['cost'] / n / 1e6, calls_per_day=P[k]['calls'] / n,
                cost_per_call=P[k]['cost'] / calls / 1e3,
                median_ctx=v[len(v) // 2] if v else 0, mean_ctx=sum(v) // len(v) if v else 0,
                opus_share=100 * P[k]['opus_seq'] / max(P[k]['seq'], 1),
                # R6P 新字段
                full_days=nf, partial_days=[str(x) for x in part], active_hours=len(HOURS[k]),
                seq_cost_per_call=P[k]['seq'] / calls / 1e3,
                seq_cost_per_full_day=(fc['seq'] / nf / 1e6) if nf else None,
                cost_per_full_day=(fc['cost'] / nf / 1e6) if nf else None,
                calls_per_full_day=(fc['calls'] / nf) if nf else None,
                seq_cost_per_active_hour=P[k]['seq'] / max(len(HOURS[k]), 1) / 1e6,
                usd_total=round(P[k]['usd'], 2), calls=P[k]['calls'],
                tokens=dict(inp=P[k]['t_inp'], cw=P[k]['t_cw'], cr=P[k]['t_cr'], out=P[k]['t_out']),
                pricing='tools/pricing.py' if PR else '内置通用倍数（pricing.py 缺失）')


b, r = stat('before'), stat('recent')
b_raw, r_raw = P['before'], P['recent']
FAM_ORDER = [f for f, _ in FAMS.most_common()]
def pcv(Pk, sd, md, unit='cost'): return Pk[f'{unit}_{sd}_{md}'] / Pk[f'calls_{sd}_{md}'] / 1e3 if Pk[f'calls_{sd}_{md}'] else 0
def pc(Pk, sd, md, unit='cost'): return f"{pcv(Pk, sd, md, unit):.1f} ({Pk[f'calls_{sd}_{md}']})" if Pk[f'calls_{sd}_{md}'] else '—'
def ch(x, y): return f"{100 * (y - x) / x:+.0f}%" if x and y is not None else 'n/a'
def f1(x, fmt='{:.1f}'): return 'n/a（没有完整天）' if x is None else fmt.format(x)
def row(g, k):
    A = AG[(k,) + g]; Q = PARTS[(k,) + g]; n = sum(len(v) for v in A.values())
    first = sum(min(v)[1] for v in A.values()) / len(A); mean = sum(x for v in A.values() for _, x in v) / n
    win = '改动前' if k == 'before' else '启用后'
    return (f"| {g[0]}·{g[1]} | {win} | {len(A)} | {n / len(A):.1f} | {first / 1e3:.1f}k | {mean / 1e3:.0f}k | "
            f"{Q['cr'] / n / 1e3:.1f} | {Q['cw'] / n / 1e3:.1f} | {Q['out'] / n / 1e3:.1f} | {Q['cost'] / n / 1e3:.1f} |")
def share(k, fam):
    tot = sum(P[k][f'seq_{sd}_{fam}'] for sd in ('main', 'sub'))
    return f"{100 * tot / max(P[k]['seq'], 1):.0f}%"
warn = []
if not PR:
    warn.append('⚠ 没找到 tools/pricing.py：按内置通用倍数（缓存读 0.1×）算，5.5 代模型的缓存读会被高估一倍；Sonnet 当量 = 本模型单位。')
if UNKNOWN:
    warn.append('⚠ 价格表不认识的模型（按同族最新价或 Sonnet 5.5 价计，请核对）：' +
                '、'.join(f'{m or "<空>"}（{n} 次）' for m, n in UNKNOWN.most_common()))
other = sum(v for f, v in FAMS.items() if f not in ('opus', 'sonnet', 'haiku', 'fable', 'mythos'))
if FAMS and other / max(sum(FAMS.values()), 1) > 0.05:
    warn.append(f'⚠ 不认识的模型族占成本 {100 * other / sum(FAMS.values()):.0f}%（>5%）：结论可能受价格假设影响。')
L = [f"# 每周用量对比 {now.astimezone().strftime('%Y-%m-%d')}", '', *warn, *([''] if warn else []),
     '| 指标 | 改动前（10-21 → 11-05 23:58） | 启用后（最多最近 7 天） | 变化 |', '|---|---|---|---|',
     f"| 完整天数（每天成本只用这些天） | {b['full_days']} | {r['full_days']} | |",
     f"| 有记录但不完整、已剔除的天 | {'、'.join(b['partial_days']) or '无'} | {'、'.join(r['partial_days']) or '无'} | |",
     f"| 每天成本（Sonnet 5.5 当量，M） | {f1(b['seq_cost_per_full_day'])} | {f1(r['seq_cost_per_full_day'])} | {ch(b['seq_cost_per_full_day'], r['seq_cost_per_full_day'])} |",
     f"| 每天成本（本模型单位，M） | {f1(b['cost_per_full_day'])} | {f1(r['cost_per_full_day'])} | {ch(b['cost_per_full_day'], r['cost_per_full_day'])} |",
     f"| 每次请求成本（Sonnet 5.5 当量，k） | {b['seq_cost_per_call']:.1f} | {r['seq_cost_per_call']:.1f} | {ch(b['seq_cost_per_call'], r['seq_cost_per_call'])} |",
     f"| 每次请求成本（本模型单位，k） | {b['cost_per_call']:.1f} | {r['cost_per_call']:.1f} | {ch(b['cost_per_call'], r['cost_per_call'])} |",
     f"| 每天请求数（完整天） | {f1(b['calls_per_full_day'], '{:.0f}')} | {f1(r['calls_per_full_day'], '{:.0f}')} | {ch(b['calls_per_full_day'], r['calls_per_full_day'])} |",
     f"| 活跃小时数 / 每活跃小时成本（Sonnet 当量 M） | {b['active_hours']} / {b['seq_cost_per_active_hour']:.2f} | {r['active_hours']} / {r['seq_cost_per_active_hour']:.2f} | {ch(b['seq_cost_per_active_hour'], r['seq_cost_per_active_hour'])} |",
     f"| 每次请求上下文中位数 | {b['median_ctx'] // 1000}k | {r['median_ctx'] // 1000}k | {ch(b['median_ctx'], r['median_ctx'])} |",
     f"| 每次请求上下文平均 | {b['mean_ctx'] // 1000}k | {r['mean_ctx'] // 1000}k | {ch(b['mean_ctx'], r['mean_ctx'])} |",
     f"| API 美元合计 | {b['usd_total']:.2f} | {r['usd_total']:.2f} | |",
     *[f"| {fam} 成本占比（Sonnet 当量） | {share('before', fam)} | {share('recent', fam)} | |" for fam in FAM_ORDER], '',
     '### 按主会话/子 agent × 模型族分开（每次请求成本，k；括号内为请求数）', '',
     '| 分组 | 改动前·本模型单位 | 最近·本模型单位 | 改动前·Sonnet 当量 | 最近·Sonnet 当量 | 变化（当量） |', '|---|---|---|---|---|---|',
     *[f"| {sd}·{md} | {pc(b_raw, sd, md)} | {pc(r_raw, sd, md)} | {pc(b_raw, sd, md, 'seq')} | {pc(r_raw, sd, md, 'seq')} | "
       f"{ch(pcv(b_raw, sd, md, 'seq'), pcv(r_raw, sd, md, 'seq'))} |"
       for sd in ('main', 'sub') for md in FAM_ORDER if b_raw[f'calls_{sd}_{md}'] or r_raw[f'calls_{sd}_{md}']], '',
     '### 子 agent 明细（任务结构；成本 Sonnet 5.5 当量 k/每次请求，平均上下文按请求平均）', '',
     '| 分组 | 窗口 | agent 数 | 每 agent 请求数 | 第一次请求上下文（均值） | 平均上下文 | cache read | cache write | 输出 | 每次合计 |',
     '|---|---|---|---|---|---|---|---|---|---|',
     *[row(g, k) for g in [(sd, md) for sd in ('main', 'sub') for md in FAM_ORDER] + [('all', 'all')]
       for k in ('before', 'recent') if AG[(k,) + g]], '',
     '读法：**第一次请求上下文**是套件要盯的数（系统提示 + 工具 + skill 列表 + 首条 prompt），它涨了才说明套件让 prompt 变长；每 agent 请求数、平均上下文、输出涨，多半是任务本身变重。'
     '"all·all" 是主会话 + 子 agent 合计（agent 数 = 主会话文件数 + 子 agent 数），子 agent 单独涨而合计降，往往只是重活从主会话搬到了子 agent。'
     '三项分项之外还有少量未缓存 input，所以分项之和略小于合计。', '',
     '读法：**每次请求成本**最能说明省额度措施本身的效果（不受你这周用得多还是少影响）；每天成本还取决于工作量。',
     '两种单位：**本模型单位** = 每个模型按自己的输入价归一（同一模型内可比，跨模型不能比，和旧报告数字连续）；'
     '**Sonnet 5.5 当量** = API 美元 ÷ $2/M，Opus 5.5 的同样 token 记 2 倍（F1 采用的额度折算，未被额度数据精确证实，R 的 95% 区间 1.43–3.56）。'
     '价格来自 tools/pricing.py（官方定价页 11-06）：5.5 代缓存读是 0.05× 输入价，旧模型 0.1×。不等于订阅额度百分比。已排除基准/测试会话。',
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
