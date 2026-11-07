# F1：从 transcript 抽每次请求的 usage 元数据（只统计，不存正文）。口径与 tools/weekly_compare.py 一致。
import json, glob, os, datetime as dt, csv, sys
ROOT = os.path.expanduser('~/.claude/projects')
UTC = dt.timezone.utc
BASE_FROM = dt.datetime(2026, 10, 21, tzinfo=UTC)
out = csv.writer(open(sys.argv[1], 'w', newline=''))
out.writerow(['ts','proj','file','side','sess','model','version','effort','cr','cw','cw_raw','inp','out','cost','ctx','n_tool','n_edit','human_prompts_before'])
seen = {}; rows = []
for f in glob.glob(ROOT + '/**/*.jsonl', recursive=True):
    proj = f.split('/projects/')[1].split('/')[0]
    if proj.startswith('-private-tmp-'): continue
    human = 0
    for line in open(f, errors='ignore'):
        try: j = json.loads(line)
        except Exception: continue
        m = j.get('message') or {}
        if j.get('type') == 'user' and isinstance(m, dict) and not j.get('isMeta') and not j.get('isSidechain'):
            c = m.get('content')
            if isinstance(c, str) or (isinstance(c, list) and c and all(isinstance(x, dict) and x.get('type') == 'text' for x in c)):
                human += 1
        u = m.get('usage') if isinstance(m, dict) else None
        if not u: continue
        content = m.get('content') or []
        tools = [x.get('name') for x in content if isinstance(x, dict) and x.get('type') == 'tool_use']
        if m.get('id') in seen:
            r = rows[seen[m.get('id')]]; r[15] += len(tools); r[16] += sum(1 for x in tools if x in ('Edit', 'Write', 'NotebookEdit', 'MultiEdit')); continue
        try: t = dt.datetime.fromisoformat(j['timestamp'].replace('Z', '+00:00'))
        except Exception: continue
        if t < BASE_FROM: continue
        cc = u.get('cache_creation') or {}
        cw_raw = u.get('cache_creation_input_tokens', 0) or 0
        cw = 1.25 * cc.get('ephemeral_5m_input_tokens', 0) + 2.0 * cc.get('ephemeral_1h_input_tokens', 0) if cc else 1.25 * cw_raw
        cr = u.get('cache_read_input_tokens', 0) or 0; inp = u.get('input_tokens', 0) or 0; o = u.get('output_tokens', 0) or 0
        cost = 0.1 * cr + cw + inp + 5 * o
        side = 'sub' if (j.get('isSidechain') or '/subagents/' in f) else 'main'
        sess = f if '/subagents/' in f or side == 'main' else f + '#' + str(j.get('agentId'))
        seen[m.get('id')] = len(rows)
        rows.append([t.isoformat(), proj, os.path.basename(f), side, sess, m.get('model') or '', j.get('version') or '', j.get('effort') or '',
                      cr, cw, cw_raw, inp, o, cost, cr + cw_raw + inp, len(tools), sum(1 for x in tools if x in ('Edit', 'Write', 'NotebookEdit', 'MultiEdit')), human])
out.writerows(rows)
