#!/usr/bin/env python3
"""第 5 轮分析：读 r5.pkl，按请求分摊成本到上下文里的各个组成部分。"""
import pickle, collections, os, json, re, datetime as dt, statistics as st
D = os.path.dirname(os.path.abspath(__file__))
R = pickle.load(open(D + '/r5.pkl', 'rb'))
KA, KN = R['calib']['ka'], R['calib']['kn']
CHANGE = dt.datetime(2026, 11, 6, 7, 58, tzinfo=dt.timezone.utc)
def tk(it): return it['a'] * KA + it['n'] * KN + it['img'] * 1500
def day(T): return T.astimezone().strftime('%m-%d')
reqs = R['req']
O = {}
POST = os.environ.get('WIN') == 'post'
if POST:
    for r in reqs: r['inwin'] = r['T'] >= CHANGE

# ---------- 1. 成本构成 ----------
def comp(sel):
    c = collections.Counter()
    for r in sel:
        for k in ('cost', 'cost_cr', 'cost_cw', 'cost_inp', 'cost_out', 'cr', 'cw_raw', 'cw5', 'cw1', 'inp', 'out', 'ctx'): c[k] += r[k]
        c['n'] += 1
    return c
win = [r for r in reqs if r['inwin']]
post = [r for r in win if r['T'] >= CHANGE]
O['comp_all'] = comp(win); O['comp_post'] = comp(post)
grp = collections.defaultdict(list)
for r in win:
    grp[('win', r['side'], r['mdl'])].append(r)
    grp[('day', day(r['T']))].append(r)
    grp[('proj', r['f'].split('/projects/')[1].split('/')[0])].append(r)
    if r['T'] >= CHANGE: grp[('post', r['side'], r['mdl'])].append(r)
O['groups'] = {str(k): comp(v) for k, v in grp.items()}

# ---------- 2. 按组成部分分摊 ----------
cw_w = O['comp_all']['cost_cw'] / max(O['comp_all']['cw_raw'], 1)
items_all = []                 # 每个上下文条目：dict(tok, cat, kind, cost, nreq, f, T, fp, side)
cat_cost = collections.defaultdict(lambda: collections.Counter())   # (scope, cat) -> cost_cr, cost_cw
fileinfo = {}
for f, ev in R['files'].items():
    live = []      # 当前上下文里的条目
    pend = []      # 上次请求以来的新条目
    first = True; nreq = 0; side = None
    finfo = dict(first_ctx=None, base=None, maxctx=0, nreq=0, cost=0, guard=0, compact=0, big_reqs=0, big_cost=0, guard_first_ctx=None,
                 after_guard_reqs=0, req_ctx=[], side=None, mdl=None, T0=None, T1=None)
    guard_seen = False
    for e in ev:
        if e[0] == 'compact':
            live = []; pend = []; first = True; finfo['compact'] += 1; continue
        if e[0] == 'edit':
            pend.append(dict(tok=0, cat='_edit', kind='edit', fp=e[2], cost=0, nreq=0, f=f, T=e[1], marker=True)); continue
        if e[0] == 'item':
            it = dict(e[2]); it['tok'] = tk(it); it['cost'] = 0.0; it['nreq'] = 0; it['f'] = f; it['T'] = e[1]
            if 'context_guard' in it['cat']:
                finfo['guard'] += 1; guard_seen = True
            pend.append(it); continue
        r = reqs[e[2]]
        finfo['nreq'] += 1; finfo['side'] = r['side']; finfo['mdl'] = r['mdl']
        finfo['T0'] = finfo['T0'] or r['T']; finfo['T1'] = r['T']
        finfo['maxctx'] = max(finfo['maxctx'], r['ctx']); finfo['req_ctx'].append((r['T'], r['ctx'], r['cost'], guard_seen))
        if r['inwin']: finfo['cost'] += r['cost']
        if guard_seen and finfo['guard_first_ctx'] is None: finfo['guard_first_ctx'] = r['ctx']
        if guard_seen: finfo['after_guard_reqs'] += 1
        real_pend = [p for p in pend if not p.get('marker')]
        live_marks = [p for p in pend if p.get('marker')]
        if first:
            pre = sum(p['tok'] for p in real_pend)
            base = max(r['ctx'] - pre, 0)
            if finfo['first_ctx'] is None: finfo['first_ctx'] = r['ctx']; finfo['base'] = base; finfo['first_items'] = collections.Counter({p['cat']: p['tok'] for p in real_pend})
            bcat = 'fixed:系统提示+工具定义' if finfo['compact'] == 0 else 'fixed:压缩后重建'
            real_pend.insert(0, dict(tok=base, cat=bcat, kind='base', cost=0.0, nreq=0, f=f, T=r['T'], a=0, n=0, img=0))
            first = False
        old_tok = sum(x['tok'] for x in live); new_tok = sum(x['tok'] for x in real_pend)
        unexpl = r['ctx'] - old_tok - new_tok
        if unexpl > 0:
            real_pend.append(dict(tok=unexpl, cat='其他(系统提醒/估算误差)', kind='resid', cost=0.0, nreq=0, f=f, T=r['T'], a=0, n=0, img=0))
            new_tok += unexpl
            scale = 1.0
        else:
            scale = r['ctx'] / max(old_tok + new_tok, 1)
        if r['inwin']:
            # 缓存写入 + 未缓存输入：先给新条目，超出部分（缓存过期重写）按比例摊到旧条目
            wcost = r['cost_cw'] + r['cost_inp']; wtok = r['cw_raw'] + r['inp']
            new_s = new_tok * scale
            if new_s > 0:
                to_new = wcost * min(1.0, new_s / max(wtok, 1)) if wtok else 0
            else:
                to_new = 0
            for x in real_pend: x['cost'] += to_new * x['tok'] / max(new_tok, 1e-9)
            rest = wcost - to_new
            # 缓存读取：按 token 摊到旧条目（加上重写部分）
            rc = r['cost_cr'] + rest
            if old_tok > 0:
                for x in live: x['cost'] += rc * x['tok'] / old_tok; x['nreq'] += 1
            else:
                for x in real_pend: x['cost'] += rc * x['tok'] / max(new_tok, 1e-9)
            for x in real_pend: x['nreq'] += 1
        out_it = dict(tok=r['out'], cat='assistant输出(思考/文字/工具调用)', kind='out', cost=r['cost_out'] if r['inwin'] else 0, nreq=0, f=f, T=r['T'], a=0, n=0, img=0, side=r['side'])
        for x in real_pend: x['side'] = r['side']
        live.extend(real_pend); live.append(out_it)
        items_all.extend(real_pend); items_all.append(out_it)
        for x in live_marks: items_all.append(x)
        pend = []
    fileinfo[f] = finfo
O['cw_weight'] = cw_w
# 分类汇总
cc = collections.defaultdict(collections.Counter)
for x in items_all:
    if x.get('marker'): continue
    if POST and x['cost'] == 0: continue
    sd = x.get('side', 'main')
    for sc in ('all', sd):
        cc[(sc, x['cat'])]['cost'] += x['cost']; cc[(sc, x['cat'])]['tok'] += x['tok']; cc[(sc, x['cat'])]['n'] += 1
        cc[(sc, x['cat'])]['reqs'] += x['nreq']
O['cat'] = {f'{k[0]}|{k[1]}': v for k, v in cc.items()}
tot_alloc = sum(v['cost'] for k, v in cc.items() if k[0] == 'all')
O['tot_alloc'] = tot_alloc
# 按工具细类 kind
kc = collections.defaultdict(collections.Counter)
for x in items_all:
    if x.get('marker') or not x['cat'].startswith('tool:'): continue
    if POST and x['cost'] == 0: continue
    k = x['kind']
    kc[k]['cost'] += x['cost']; kc[k]['tok'] += x['tok']; kc[k]['n'] += 1; kc[k]['reqs'] += x['nreq']
O['kind'] = kc
# 前 20 个最贵单条
tops = sorted((x for x in items_all if not x.get('marker') and x['kind'] not in ('base', 'resid', 'out')), key=lambda x: -x['cost'])[:30]
O['top'] = [dict(cat=x['cat'], kind=x['kind'], tok=round(x['tok']), cost=round(x['cost']), nreq=x['nreq'], side=x.get('side'),
                 proj=x['f'].split('/projects/')[1].split('/')[0][-18:], fpext=os.path.splitext(x.get('fp') or '')[1],
                 lines=None, err=x.get('err')) for x in tops]
# 结果大小分布
sizes = collections.Counter()
for x in items_all:
    if x['cat'].startswith('tool:') and not x.get('marker') and not (POST and x['cost'] == 0):
        b = '<1k' if x['tok'] < 1000 else '1-5k' if x['tok'] < 5000 else '5-10k' if x['tok'] < 10000 else '>=10k'
        sizes[(b, 'n')] += 1; sizes[(b, 'cost')] += x['cost']; sizes[(b, 'tok')] += x['tok']
O['sizes'] = sizes

# ---------- 3. 重复读取 ----------
rr = collections.Counter(); rr_ex = collections.Counter()
for f, ev in R['files'].items():
    pass
byfile = collections.defaultdict(list)
for x in items_all:
    if x.get('marker') and x['kind'] == 'edit': byfile[x['f']].append(('edit', x['fp']))
    elif x.get('tool') == 'Read' and x.get('fp'): byfile[x['f']].append(('read', x))
for f, seq in byfile.items():
    seenr = {}; edited = set()
    for k, v in seq:
        if k == 'edit': edited.add(v); continue
        fp = v['fp']
        rr['reads'] += 1; rr['read_cost'] += v['cost']; rr['read_tok'] += v['tok']
        if fp in seenr:
            if fp in edited:
                rr['reread_after_edit'] += 1; rr['reread_after_edit_cost'] += v['cost']; rr['reread_after_edit_tok'] += v['tok']
            else:
                rr['reread_noedit'] += 1; rr['reread_noedit_cost'] += v['cost']; rr['reread_noedit_tok'] += v['tok']
                if v['tok'] < 300: rr['reread_noedit_small'] += 1
            edited.discard(fp)
        seenr[fp] = 1
O['reread'] = rr

# ---------- 4. 会话长度 ----------
big = collections.Counter()
for r in win:
    b = '<50k' if r['ctx'] < 50e3 else '50-100k' if r['ctx'] < 100e3 else '100-150k' if r['ctx'] < 150e3 else '150-200k' if r['ctx'] < 200e3 else '>=200k'
    big[(b, 'cost')] += r['cost']; big[(b, 'n')] += 1
    big[(b + '|' + r['side'], 'cost')] += r['cost']
    if r['T'] >= CHANGE: big[('post' + b, 'cost')] += r['cost']; big[('post' + b, 'n')] += 1
O['ctxbuckets'] = big
sess = []
for f, fi in fileinfo.items():
    if fi['nreq'] == 0: continue
    over = [(T, c, k, g) for T, c, k, g in fi['req_ctx'] if c >= 150e3 and T >= dt.datetime(2026, 11, 4, 8, tzinfo=dt.timezone.utc)]
    if not over: continue
    # 超过 150k 之后的成本，以及若在 150k 时重开、保持 ~base+30k 的假想成本
    oc = sum(k for _, _, k, _ in over)
    sess.append(dict(f=f.split('/projects/')[1][:60], side=fi['side'], mdl=fi['mdl'], nreq=fi['nreq'], maxctx=fi['maxctx'], n_over=len(over),
                     cost_over=oc, cost=fi['cost'], guard=fi['guard'], compact=fi['compact'], guard_first_ctx=fi['guard_first_ctx'],
                     first_over=over[0][0].astimezone().strftime('%m-%d %H:%M'), post=over[-1][0] >= CHANGE,
                     guard_before_over=any(g for _, _, _, g in over),
                     mean_cr_over=sum(c for _, c, _, _ in over) / len(over)))
O['bigsess'] = sorted(sess, key=lambda s: -s['cost_over'])

# ---------- 5. 固定开销 ----------
fx = collections.defaultdict(list)
for f, fi in fileinfo.items():
    if fi['first_ctx'] is None or fi['T0'] is None or fi['T0'] < dt.datetime(2026, 11, 4, 8, tzinfo=dt.timezone.utc): continue
    fx[fi['side']].append((fi['first_ctx'], fi['base'], fi.get('first_items', {})))
O['firstctx'] = {k: dict(n=len(v), mean_first=st.mean(a for a, _, _ in v), med_first=st.median(a for a, _, _ in v),
                         mean_base=st.mean(b for _, b, _ in v)) for k, v in fx.items()}
fi_items = collections.defaultdict(collections.Counter)
for sd, v in fx.items():
    for _, _, its in v:
        for c, t in its.items(): fi_items[sd][c] += t / len(v)
O['first_items'] = fi_items
sh = R['shapes']
O['shapes'] = dict(n=len(sh), main=[(s['sys'], s['tools'], s['ntools'], s['mcp'], s['nmcp']) for s in sh if not s['sub']][:200],
                   sub=[(s['sys'], s['tools'], s['ntools'], s['mcp'], s['nmcp']) for s in sh if s['sub']][:200])

# ---------- 6. 子 agent ----------
subs = []
fi_by = {}
for f, meta in R['subagents'].items():
    fi = fileinfo.get(f)
    if not fi or fi['nreq'] == 0: continue
    ev = R['files'][f]
    ntool = sum(1 for e in ev if e[0] == 'item' and e[2]['cat'].startswith('tool:'))
    tools = collections.Counter(e[2]['tool'] for e in ev if e[0] == 'item' and e[2]['cat'].startswith('tool:'))
    toktool = sum(tk(e[2]) for e in ev if e[0] == 'item' and e[2]['cat'].startswith('tool:'))
    T0 = fi['T0']
    subs.append(dict(f=f, type=meta.get('agentType'), model=meta.get('model'), desc=meta.get('description', ''), bg=meta.get('requestShape'),
                     nreq=fi['nreq'], cost=fi['cost'], first=fi['first_ctx'], maxctx=fi['maxctx'], ntool=ntool, tools=dict(tools),
                     toktool=toktool, inwin=T0 >= dt.datetime(2026, 11, 4, 8, tzinfo=dt.timezone.utc), mdl=fi['mdl']))
# 报告长度：父会话里对应 Agent 的 tool_result / task-notification
O['subs'] = subs
ag_ret = [x for x in items_all if x.get('tool') in ('Agent', 'Task')]
tn = [x for x in items_all if 'task-notification' in x['cat']]
O['agent_returns'] = dict(n=len(ag_ret), tok=sum(x['tok'] for x in ag_ret), cost=sum(x['cost'] for x in ag_ret),
                          med=st.median([x['tok'] for x in ag_ret]) if ag_ret else 0,
                          tn_n=len(tn), tn_tok=sum(x['tok'] for x in tn), tn_cost=sum(x['cost'] for x in tn),
                          tn_med=st.median([x['tok'] for x in tn]) if tn else 0)
O['fileinfo_summary'] = dict(nfiles=len(fileinfo))
pickle.dump(O, open(D + ('/r5b_post.pkl' if POST else '/r5b.pkl'), 'wb'))
print('done', tot_alloc, O['comp_all']['cost'])

# 追加：守卫时序
G = []
for f, fi in fileinfo.items():
    if fi['guard'] == 0: continue
    rc = fi['req_ctx']
    aft = [(T, c, k) for T, c, k, g in rc if g]
    G.append(dict(f=f.split('/projects/')[1][:40], side=fi['side'], nreq=fi['nreq'], guard=fi['guard'], first_guard_ctx=fi['guard_first_ctx'], maxctx=fi['maxctx'],
                  n_after=len(aft), cost_after=sum(k for TT, cx, k in aft if TT >= CHANGE), mean_ctx_after=sum(c for _, c, _ in aft) / max(len(aft), 1)))
O['guard'] = G
pickle.dump(O, open(D + ('/r5b_post.pkl' if POST else '/r5b.pkl'), 'wb'))
O['titems'] = [(x.get('tool'), x['kind'], x['tok'], x['cost'], x['nreq'], x.get('side'), x['a'], x['n']) for x in items_all if x['cat'].startswith('tool:') and not x.get('marker') and x['cost'] > 0]
pickle.dump(O, open(D + ('/r5b_post.pkl' if POST else '/r5b.pkl'), 'wb'))
