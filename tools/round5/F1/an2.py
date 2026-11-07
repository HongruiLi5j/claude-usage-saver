import pandas as pd, numpy as np
rng = np.random.default_rng(0)
d = pd.read_csv('req.csv'); d['ts'] = pd.to_datetime(d.ts, utc=True, format='ISO8601'); d = d[d.model != '<synthetic>']
CHANGE = pd.Timestamp('2026-11-06 07:58', tz='UTC'); d['win'] = np.where(d.ts < CHANGE, 'before', 'after')
d['mdl'] = d.model.str.extract(r'(opus|sonnet|haiku|fable)')[0]
d['cell'] = d.side + '·' + d.mdl
d['aq'] = d.proj.str.contains('AQ-')
d['bin'] = pd.cut(d.ctx, [0, 50e3, 100e3, 150e3, 200e3, 300e3, 2e6], labels=['<50k','50-100k','100-150k','150-200k','200-300k','>=300k'])
A, B = d[d.win == 'after'], d[d.win == 'before']; Bx = B[~B.aq]
print('== cells: share of requests and cpc ==')
t = d.groupby(['cell', 'win']).agg(n=('cost', 'size'), cpc=('cost', lambda x: x.mean() / 1e3)).unstack()
t['shareB'] = t[('n', 'before')] / len(B); t['shareA'] = t[('n', 'after')] / len(A); print(t.round(3).to_string())
def std(src, wts_from, key):
    m = src.groupby(key, observed=True).cost.mean(); w = wts_from[key].value_counts(normalize=True)
    common = m.index.intersection(w.index); return (m[common] * w[common] / w[common].sum()).sum() / 1e3
print('\n== direct standardization ==')
for key in ['cell', 'bin']:
    print(key, ': after cpc reweighted to BEFORE mix = %.1f ; before cpc reweighted to AFTER mix = %.1f ; before-exAQ reweighted to AFTER mix = %.1f'
          % (std(A, B, key), std(B, A, key), std(Bx, A, key)))
print('\n== cpc by context bin (mechanical link) ==')
print(d.groupby(['bin', 'win'], observed=True).agg(n=('cost', 'size'), cpc=('cost', lambda x: x.mean() / 1e3)).unstack().round(1).to_string())
# 回归：cpc ~ ctx 的斜率
for nm, s in [('before', B), ('after', A)]:
    X = np.c_[np.ones(len(s)), s.ctx / 1e3]; beta = np.linalg.lstsq(X, s.cost / 1e3, rcond=None)[0]; print(nm, 'cost_k = %.2f + %.4f * ctx_k' % tuple(beta))
print('\n== session (cluster) level ==')
S = d.groupby(['win', 'sess']).agg(n=('cost', 'size'), cost=('cost', 'sum'), side=('side', 'first'), aq=('aq', 'first'), maxctx=('ctx', 'max'), first=('ctx', 'first'), edits=('n_edit', 'sum'), tools=('n_tool', 'sum'), human=('human_prompts_before', 'max')).reset_index()
for w in ['before', 'after']:
    s = S[S.win == w]; print(w, 'sessions', len(s), 'main', (s.side == 'main').sum(), 'sub', (s.side == 'sub').sum(), 'calls/sess median', s.n.median(), 'top-5 sessions share of cost %.2f' % (s.cost.nlargest(5).sum() / s.cost.sum()), 'top-5 share of calls %.2f' % (s.n.nlargest(5).sum()/s.n.sum()))
def boot_ratio(s, B_=4000):
    c, n = s.cost.values, s.n.values; idx = rng.integers(0, len(s), (B_, len(s)))
    r = c[idx].sum(1) / n[idx].sum(1) / 1e3; return c.sum() / n.sum() / 1e3, np.percentile(r, [2.5, 97.5])
for nm, s in [('before', S[S.win == 'before']), ('before exAQ', S[(S.win == 'before') & ~S.aq]), ('after', S[S.win == 'after'])]:
    est, ci = boot_ratio(s); print(f'{nm:12s} cpc {est:.1f}k  cluster-bootstrap 95% CI [{ci[0]:.1f}, {ci[1]:.1f}]  (n_sessions={len(s)})')
# 差值的 CI（两样本独立 cluster bootstrap）
def diff_ci(s1, s2, B_=4000):
    out = []
    for _ in range(B_):
        a = s1.sample(len(s1), replace=True, random_state=rng.integers(1e9)); b = s2.sample(len(s2), replace=True, random_state=rng.integers(1e9))
        out.append(np.log(b.cost.sum() / b.n.sum()) - np.log(a.cost.sum() / a.n.sum()))
    return np.exp(np.percentile(out, [2.5, 50, 97.5])) - 1
print('ratio change after/before (all), 95% CI:', np.round(diff_ci(S[S.win == 'before'], S[S.win == 'after'], 1500), 3))
print('ratio change after/before-exAQ, 95% CI:', np.round(diff_ci(S[(S.win == 'before') & ~S.aq], S[S.win == 'after'], 1500), 3))
print('\n== unit-of-work proxies (main sessions only for human prompts) ==')
for w, s in [('before', B), ('before exAQ', Bx), ('after', A)]:
    mainS = S[(S.win == ('after' if w == 'after' else 'before')) & (S.side == 'main')]
    if w == 'before exAQ': mainS = mainS[~mainS.aq]
    hp = mainS.human.sum()
    print(f"{w:12s} total cost {s.cost.sum()/1e6:7.1f}M | calls {len(s):6d} | human prompts(main) {hp:5d} -> cost/prompt {s.cost.sum()/max(hp,1)/1e3:7.0f}k, calls/prompt {len(s)/max(hp,1):5.1f} | edits {s.n_edit.sum():5d} -> cost/edit {s.cost.sum()/max(s.n_edit.sum(),1)/1e3:6.0f}k | tool calls {s.n_tool.sum():6d} -> cost/toolcall {s.cost.sum()/max(s.n_tool.sum(),1)/1e3:5.1f}k | output tok {s.out.sum()/1e6:5.2f}M -> cost/out_tok {s.cost.sum()/s.out.sum():5.1f}")
print('\n== first-request ctx (startup tax) ==')
print(S.groupby(['win', 'side']).first_ctx.median() if 'first_ctx' in S else S.groupby(['win', 'side'])['first'].median())
