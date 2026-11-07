import json, glob, os, collections
ROOT = os.path.expanduser('~/.claude/projects')
recs = collections.defaultdict(list); files = sorted(glob.glob(ROOT + '/**/*.jsonl', recursive=True), key=os.path.getmtime)[-120:]
for f in files:
    if '-private-tmp-' in f: continue
    for line in open(f, errors='ignore'):
        try: j = json.loads(line)
        except Exception: continue
        m = j.get('message') or {}
        if isinstance(m, dict) and m.get('usage') and m.get('id'):
            u = m['usage']; blocks = [x.get('type') for x in (m.get('content') or []) if isinstance(x, dict)]
            recs[m['id']].append((f, u.get('output_tokens'), u.get('cache_read_input_tokens'), u.get('cache_creation_input_tokens'), tuple(blocks)))
multi = {k: v for k, v in recs.items() if len(v) > 1}
print('ids', len(recs), 'with >1 record', len(multi))
diff_out = sum(1 for v in multi.values() if len({x[1] for x in v}) > 1); diff_cr = sum(1 for v in multi.values() if len({x[2] for x in v}) > 1)
cross_file = sum(1 for v in multi.values() if len({x[0] for x in v}) > 1)
print('multi with differing output_tokens', diff_out, 'differing cache_read', diff_cr, 'spanning files', cross_file)
first_vs_max = [(v[0][1], max(x[1] for x in v)) for v in multi.values() if len({x[1] for x in v}) > 1]
if first_vs_max:
    import statistics; r = [a / b for a, b in first_vs_max if b]; print('first/max output ratio median', statistics.median(r), 'n', len(r), 'sum first', sum(a for a,_ in first_vs_max), 'sum max', sum(b for _,b in first_vs_max))
print('block patterns of first record', collections.Counter(v[0][4] for v in multi.values()).most_common(5))
