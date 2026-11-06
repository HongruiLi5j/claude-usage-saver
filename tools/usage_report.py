import json,glob,os,collections,datetime as dt
root=os.path.expanduser('~/.claude/projects')
by=collections.defaultdict(lambda:collections.Counter())
sess=collections.defaultdict(lambda:collections.Counter())
since=dt.datetime(2026,10,21,tzinfo=dt.timezone.utc)
seen=set()
for f in glob.glob(root+'/**/*.jsonl',recursive=True):
    proj=f.split('/projects/')[1].split('/')[0]
    side='subagent' if '/subagents/' in f else 'main'
    for line in open(f,errors='ignore'):
        try:j=json.loads(line)
        except:continue
        m=j.get('message') or {}
        u=m.get('usage') if isinstance(m,dict) else None
        if not u: continue
        mid=m.get('id'); 
        if mid in seen: continue
        seen.add(mid)
        ts=j.get('timestamp')
        try:t=dt.datetime.fromisoformat(ts.replace('Z','+00:00'))
        except:continue
        if t<since: continue
        model=(m.get('model') or '?').replace('claude-','')
        c=collections.Counter(inp=u.get('input_tokens',0),cw=u.get('cache_creation_input_tokens',0),cr=u.get('cache_read_input_tokens',0),out=u.get('output_tokens',0),calls=1)
        for k in [('model',model),('proj',proj[:45]),('kind',side),('day',t.strftime('%m-%d'))]: by[k]+=c
        sess[f]+=c
def show(kind,n=12):
    rows=[(k[1],v) for k,v in by.items() if k[0]==kind]
    rows.sort(key=lambda r:-(r[1]['cr']+r[1]['cw']+r[1]['inp']+r[1]['out']))
    print(f'== {kind}')
    for name,v in rows[:n]:
        tot=v['cr']+v['cw']+v['inp']+v['out']
        print(f"{name:46s} calls={v['calls']:6d} total={tot/1e6:8.1f}M cacheRead={v['cr']/1e6:7.1f}M cacheWrite={v['cw']/1e6:6.1f}M out={v['out']/1e6:5.2f}M")
for k in ['model','kind','proj','day']: show(k, 20 if k=='day' else 12)
tot=sum(v['cr']+v['cw']+v['inp']+v['out'] for v in sess.values())
print('sessions',len(sess),'total',round(tot/1e6,1),'M')
avg=[ (v['cr']+v['cw'])/max(v['calls'],1) for v in sess.values() if v['calls']>20]
avg.sort(); print('median context per call (tokens) over sessions>20 calls:', int(avg[len(avg)//2]) if avg else None)
print('== top sessions')
rows=sorted(sess.items(),key=lambda kv:-(kv[1]['cr']+kv[1]['cw']))[:10]
for f,v in rows:
    print(f"{f.split('/projects/')[1][:70]:72s} calls={v['calls']:5d} total={(v['cr']+v['cw'])/1e6:7.1f}M avgctx={int((v['cr']+v['cw']+v['inp'])/v['calls'])}")
import bisect
b=collections.Counter()
for v in sess.values(): pass
