import pandas as pd, numpy as np, datetime as dt
d = pd.read_csv('all.csv'); d['m'] = d.model.str.extract(r'(opus|sonnet|haiku|fable)')[0]
# API 价格（美元/MTok，官方定价页 2026-11-06 读取）：in, cw5, cw1h, cr, out
P = {'opus': (4, 5, 8, .20, 20), 'sonnet': (2, 2.5, 4, .10, 10), 'haiku': (.10, .125, .20, .01, .5), 'fable': (10, 12.5, 20, .25, 50)}
# 每个模型"以本模型 input 价为 1"的类别权重（各模型相同：1, 1.25, 2, 0.05, 5；haiku cr 0.1、fable 0.025）
def tok_units(x, m):  # 本模型内的加权 token（相对本模型 input 单价）
    p = P[m]; return (x.inp * p[0] + x.cw5 * p[1] + x.cw1h * p[2] + x.cr * p[3] + x.out * p[4]) / p[0]
def usd(x): return sum(((x[x.m == m].inp * P[m][0] + x[x.m == m].cw5 * P[m][1] + x[x.m == m].cw1h * P[m][2] + x[x.m == m].cr * P[m][3] + x[x.m == m].out * P[m][4]) / 1e6).sum() for m in P)
for nm, t0, t1, pct in [('W1 上一周窗口', 1794013200 - 7 * 86400, 1793952180, 79), ('W2 本周窗口', 1794013200, 1794019219, 4)]:
    x = d[(d.ts >= t0) & (d.ts < t1)]
    u = {m: tok_units(x[x.m == m], m).sum() / 1e6 for m in P}
    print(nm, dt.datetime.fromtimestamp(t0).strftime('%m-%d %H:%M'), '→', dt.datetime.fromtimestamp(t1).strftime('%m-%d %H:%M'), f'{pct}%', 'model-units(M, 本模型 input 价为1):', {k: round(v, 1) for k, v in u.items()}, 'API$ %.0f' % usd(x), '-> $ per 1%% = %.1f' % (usd(x) / pct))
