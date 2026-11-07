# 5 小时窗口"撞到 100%"事件：窗口 [resetsAt-5h, 首次被拒] 内各模型加权 token
import json, glob, os, collections, pandas as pd, numpy as np, datetime as dt
ROOT = os.path.expanduser('~/.claude/projects'); W = collections.defaultdict(list)
for f in glob.glob(ROOT + '/**/*.jsonl', recursive=True):
    for line in open(f, errors='ignore'):
        if 'quotaLimits' not in line and 'rate_limit_info' not in line: continue
        try: j = json.loads(line)
        except Exception: continue
        q = j.get('quotaLimits') or (j.get('apiErrorParams') or {}).get('rate_limit_info')
        if q and q.get('status') == 'rejected':
            W[(q['rateLimitType'], q['resetsAt'])].append(dt.datetime.fromisoformat(j['timestamp'].replace('Z', '+00:00')).timestamp())
d = pd.read_csv('all.csv'); d['m'] = d.model.str.extract(r'(opus|sonnet|haiku|fable)')[0]
d['wt'] = d.inp + 1.25 * d.cw5 + 2 * d.cw1h + 0.1 * d.cr + 5 * d.out
rows = []
for (typ, r), ts in sorted(W.items(), key=lambda x: x[0][1]):
    t0, t1 = r - 5 * 3600, min(ts)
    x = d[(d.ts >= t0) & (d.ts < t1)]
    g = x.groupby('m').wt.sum() / 1e6; raw = x.groupby('m')[['inp', 'cw5', 'cw1h', 'cr', 'out']].sum() / 1e6
    print(typ, 'window', dt.datetime.fromtimestamp(t0).strftime('%m-%d %H:%M'), '→ hit', dt.datetime.fromtimestamp(t1).strftime('%H:%M'), 'n_req', len(x), 'weighted M:', g.round(2).to_dict())
    print(raw.round(3).to_string())
    # 窗口之前那一段（看是否有前一个窗口的活动延续）
    rows.append(dict(win=dt.datetime.fromtimestamp(t0).strftime('%m-%d %H:%M'), O=g.get('opus', 0), S=g.get('sonnet', 0), H=g.get('haiku', 0), F=g.get('fable', 0), n=len(x), first_req=dt.datetime.fromtimestamp(x.ts.min()).strftime('%H:%M') if len(x) else None))
pd.DataFrame(rows).to_csv('hits.csv', index=False); print(pd.DataFrame(rows))
