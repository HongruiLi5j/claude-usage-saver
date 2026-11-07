# 5 小时额度读数 vs 同时段 transcript 用量：Δ% = a·ΔOpus + b·ΔSonnet (+ h·ΔHaiku)，单位 = 本模型 input 价为 1 的加权 token（M）
import json, pandas as pd, numpy as np, datetime as dt, sys
from scipy.optimize import nnls
P = {'opus': (4, 5, 8, .20, 20), 'sonnet': (2, 2.5, 4, .10, 10), 'haiku': (.10, .125, .20, .01, .5), 'fable': (10, 12.5, 20, .25, 50)}
LAG = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0     # transcript 时间戳相对读数的滞后（秒）
d = pd.read_csv('all.csv'); d['m'] = d.model.str.extract(r'(opus|sonnet|haiku|fable)')[0]
d['u'] = 0.0
for m, p in P.items():
    k = d.m == m; d.loc[k, 'u'] = (d.inp * p[0] + d.cw5 * p[1] + d.cw1h * p[2] + d.cr * p[3] + d.out * p[4])[k] / p[0] / 1e6
S = []
for l in open('rate_samples.jsonl'):
    r = json.loads(l)['rate']['rate_limits']['five_hour']; S.append((r['sampled_at'], r['used_percentage'], r['resets_at']))
S = pd.DataFrame(S, columns=['t', 'y', 'reset']).drop_duplicates(['t']).sort_values('t')
S = S[S.reset == S.reset.iloc[-1]]                      # 只用同一个 5h 窗口（剔除重置）
S = S.drop_duplicates('y', keep='first')                # 每个整数读数第一次出现的时刻 ≈ 刚越过该整数
t0 = S.reset.iloc[-1] - 5 * 3600
def cum(t, m): x = d[(d.ts >= t0) & (d.ts < t - LAG) & (d.m == m)]; return x.u.sum()
for m in ['opus', 'sonnet', 'haiku']: S[m] = [cum(t, m) for t in S.t]
S['opus_share'] = S.opus * 4 / (S.opus * 4 + S.sonnet * 2 + S.haiku * .1)
print(S.assign(t=lambda x: x.t.map(lambda v: dt.datetime.fromtimestamp(v).strftime('%H:%M:%S'))).round(3).to_string())
# 增量回归（相邻读数之差），并做块 bootstrap
D = S[['y', 'opus', 'sonnet', 'haiku']].diff().dropna()
X = D[['opus', 'sonnet', 'haiku']].values; y = D.y.values
coef, _ = nnls(X, y); print('NNLS 增量: a(opus)=%.3f b(sonnet)=%.3f h=%.3f  %%/M本模型单位' % tuple(coef))
# 累计回归（带截距 c = 窗口内 transcript 外的用量）
Xc = np.c_[S[['opus', 'sonnet', 'haiku']].values, np.ones(len(S))]; cc, _ = nnls(Xc, S.y.values)
print('NNLS 累计: a=%.3f b=%.3f h=%.3f c=%.2f' % tuple(cc))
rng = np.random.default_rng(0); B = []
n = len(D); blk = 3
for _ in range(4000):
    starts = rng.integers(0, max(n - blk, 1), int(np.ceil(n / blk))); idx = np.concatenate([np.arange(s, min(s + blk, n)) for s in starts])[:n]
    c, _ = nnls(X[idx], y[idx])
    if c[1] > 0: B.append((c[0], c[1], c[0] / c[1]))
B = np.array(B); print('块 bootstrap: a 95%% [%.3f, %.3f]; b [%.3f, %.3f]; a/b 中位 %.2f, 95%% [%.2f, %.2f]; 有效次数 %d' % (*np.percentile(B[:, 0], [2.5, 97.5]), *np.percentile(B[:, 1], [2.5, 97.5]), np.median(B[:, 2]), *np.percentile(B[:, 2], [2.5, 97.5]), len(B)))
# 以"美元"表达：a/4 与 b/2 是每美元的百分比；价格比先验下 a/b=2
print('每 API 美元的 5h%%：opus %.3f, sonnet %.3f' % (coef[0] / 4, coef[1] / 2))
S.to_csv('fit5h_series.csv', index=False)
# 敏感性：缓存读的额度权重不按价格（0 或 0.1×input）时，拟合是否更差
for crw in [0.0, 0.05, 0.1]:
    dd = d.copy(); dd['u2'] = 0.0
    for m, p in P.items():
        k = dd.m == m; dd.loc[k, 'u2'] = (dd.inp + 1.25 * dd.cw5 + 2 * dd.cw1h + crw * dd.cr + 5 * dd.out)[k] / 1e6
    Z = np.array([[dd[(dd.ts >= t0) & (dd.ts < t - LAG) & (dd.m == m)].u2.sum() for m in ['opus', 'sonnet', 'haiku']] for t in S.t])
    Zd = np.diff(Z, axis=0); c, res = nnls(Zd, np.diff(S.y.values)); print('cr 权重 %.2f: a=%.3f b=%.3f a/b=%s  残差平方和 %.2f' % (crw, c[0], c[1], ('%.2f' % (c[0] / c[1])) if c[1] > 0 else 'inf', res ** 2))
