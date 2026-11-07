import pandas as pd, numpy as np
d = pd.read_csv('req.csv'); d['ts'] = pd.to_datetime(d.ts, utc=True, format='ISO8601')
d = d[d.model != '<synthetic>']
CHANGE = pd.Timestamp('2026-11-06 07:58', tz='UTC')
d['win'] = np.where(d.ts < CHANGE, 'before', 'after')
d['local'] = d.ts.dt.tz_convert('America/Los_Angeles'); d['day'] = d.local.dt.date; d['hour'] = d.local.dt.hour
d['mdl'] = d.model.str.extract(r'(opus|sonnet|haiku|fable)')[0]
d['night'] = d.hour.between(0, 6)
d['overnight_proj'] = d.proj.str.contains('AQ-')
print('now max ts', d.ts.max())
g = d.groupby('win')
print(g.agg(n=('cost','size'), cost_M=('cost', lambda x: x.sum()/1e6), cpc_k=('cost', lambda x: x.mean()/1e3), ctx_mean_k=('ctx', lambda x: x.mean()/1e3), ctx_med_k=('ctx', lambda x: x.median()/1e3), days=('day','nunique')))
print('\n== daily series (before + after) ==')
dd = d.groupby(['day']).agg(n=('cost','size'), cost_M=('cost', lambda x: x.sum()/1e6), cpc_k=('cost', lambda x: x.mean()/1e3), ctx_k=('ctx', lambda x: x.mean()/1e3),
    sub_share=('side', lambda x: (x=='sub').mean()), opus_share=('mdl', lambda x: (x=='opus').mean()), aq_share=('overnight_proj','mean'), night_share=('night','mean'), nproj=('proj','nunique'))
print(dd.round(2).to_string())
b = dd.loc[[x for x in dd.index if pd.Timestamp(x) < pd.Timestamp('2026-11-06')]]
print('\nbefore daily cpc: mean %.1f sd %.1f median %.1f min %.1f max %.1f; n days %d' % (b.cpc_k.mean(), b.cpc_k.std(), b.cpc_k.median(), b.cpc_k.min(), b.cpc_k.max(), len(b)))
print('before daily ctx: mean %.0f sd %.0f min %.0f max %.0f' % (b.ctx_k.mean(), b.ctx_k.std(), b.ctx_k.min(), b.ctx_k.max()))
print('days with cpc <= 22k before:', list(b.index[b.cpc_k <= 22.5]))
print('\n== by project, before vs after ==')
p = d.groupby(['win','proj']).agg(n=('cost','size'), cost_M=('cost', lambda x: x.sum()/1e6), cpc_k=('cost', lambda x: x.mean()/1e3), ctx_k=('ctx', lambda x: x.mean()/1e3))
print(p.round(1).to_string())
print('\n== before excluding AQ-* overnight projects ==')
for nm, sub in [('before all', d[d.win=='before']), ('before excl AQ-*', d[(d.win=='before') & ~d.overnight_proj]), ('before excl AQ & 11-04', d[(d.win=='before') & ~d.overnight_proj & (d.day != pd.Timestamp('2026-11-04').date())]),
                ('before Projects-* only', d[(d.win=='before') & d.proj.str.contains('Documents-Projects')]), ('before last 7 days excl AQ', d[(d.win=='before') & ~d.overnight_proj & (d.ts >= CHANGE - pd.Timedelta(days=7))]),
                ('before claude-usage-saver-like (Documents-Projects root)', d[(d.win=='before') & (d.proj=='-Users-lihongrui-Documents-Projects')]), ('after', d[d.win=='after'])]:
    print(f"{nm:55s} n={len(sub):6d} cpc={sub.cost.mean()/1e3:5.1f}k ctx={sub.ctx.mean()/1e3:5.0f}k sub_share={(sub.side=='sub').mean():.2f} opus={(sub.mdl=='opus').mean():.2f}")
print('\n== by version x win ==')
print(d.groupby(['version','win']).agg(n=('cost','size'), cpc=('cost', lambda x: x.mean()/1e3)).round(1).to_string())
print('\n== by effort x win ==')
print(d.groupby(['effort','win']).agg(n=('cost','size'), cpc=('cost', lambda x: x.mean()/1e3)).round(1).to_string())
print('\n== night (0-6 local) x win ==')
print(d.groupby(['night','win']).agg(n=('cost','size'), cpc=('cost', lambda x: x.mean()/1e3), ctx=('ctx', lambda x: x.mean()/1e3)).round(1).to_string())
