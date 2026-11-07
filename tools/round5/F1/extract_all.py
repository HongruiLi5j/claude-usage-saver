# 所有 transcript（含 -private-tmp- 基准）的每次请求 usage：时间、模型、四类 token。只数字。
import json, glob, os, csv, sys, datetime as dt
ROOT = os.path.expanduser('~/.claude/projects'); seen = set()
w = csv.writer(open(sys.argv[1], 'w', newline='')); w.writerow(['ts', 'model', 'inp', 'cw5', 'cw1h', 'cr', 'out', 'tmp'])
for f in glob.glob(ROOT + '/**/*.jsonl', recursive=True):
    tmp = int('-private-tmp-' in f)
    for line in open(f, errors='ignore'):
        if '"usage"' not in line: continue
        try: j = json.loads(line)
        except Exception: continue
        m = j.get('message') or {}
        u = m.get('usage') if isinstance(m, dict) else None
        if not u or m.get('id') in seen or not m.get('model') or m.get('model') == '<synthetic>': continue
        seen.add(m.get('id'))
        try: t = dt.datetime.fromisoformat(j['timestamp'].replace('Z', '+00:00')).timestamp()
        except Exception: continue
        cc = u.get('cache_creation') or {}
        cwr = u.get('cache_creation_input_tokens', 0) or 0
        c5 = cc.get('ephemeral_5m_input_tokens', cwr if not cc else 0) or 0; c1 = cc.get('ephemeral_1h_input_tokens', 0) or 0
        w.writerow([t, m['model'], u.get('input_tokens', 0) or 0, c5, c1, u.get('cache_read_input_tokens', 0) or 0, u.get('output_tokens', 0) or 0, tmp])
