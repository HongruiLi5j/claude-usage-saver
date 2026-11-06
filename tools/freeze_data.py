import json,glob,os,datetime as dt
root=os.path.expanduser('~/.claude/projects')
since=dt.datetime(2026,10,21,tzinfo=dt.timezone.utc); until=dt.datetime(2026,11,5,23,59,59,tzinfo=dt.timezone.utc)
out=[]; seen=set()
for f in sorted(glob.glob(root+'/**/*.jsonl',recursive=True)):
    if '/subagents/' in f: continue
    rows=[]; pend=False
    for line in open(f,errors='ignore'):
        try:j=json.loads(line)
        except:continue
        m=j.get('message') or {}
        if j.get('type')=='user' and isinstance(m,dict):
            c=m.get('content')
            if isinstance(c,str) or (isinstance(c,list) and not any(isinstance(x,dict) and x.get('type')=='tool_result' for x in c)): pend=True
            continue
        u=m.get('usage') if isinstance(m,dict) else None
        if not u or m.get('id') in seen: continue
        seen.add(m.get('id'))
        try:t=dt.datetime.fromisoformat(j['timestamp'].replace('Z','+00:00'))
        except:continue
        if t<since or t>until: continue
        rows.append([t.timestamp(),u.get('input_tokens',0),u.get('cache_creation_input_tokens',0),u.get('cache_read_input_tokens',0),u.get('output_tokens',0),1 if pend else 0]); pend=False
    rows.sort()
    if rows: out.append(rows)
os.makedirs('state',exist_ok=True)
json.dump(dict(window='2026-10-21..2026-11-05',sessions=out),open('state/frozen.json','w'))
print(len(out),'sessions',sum(len(r) for r in out),'calls frozen')
