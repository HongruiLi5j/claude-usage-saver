#!/usr/bin/env python3
"""第 5 轮：额度花在哪。只读 transcript，输出统计到 r5_out.json。不输出对话正文。"""
import json, glob, os, collections, re, datetime as dt, pickle, sys
ROOT = os.path.expanduser('~/.claude/projects')
OUT = os.path.dirname(os.path.abspath(__file__))
UTC = dt.timezone.utc
FROM = dt.datetime(2026, 11, 4, 8, 0, tzinfo=UTC)   # 11-04 00:00 PST
W = dict(cr=0.1, inp=1.0, out=5.0)

def bad(f):
    return f.split('/projects/')[1].startswith('-private-tmp-') or any(x in f for x in ('.secret', 'sealed', 'test2'))

def ts(j):
    try: return dt.datetime.fromisoformat(j['timestamp'].replace('Z', '+00:00'))
    except Exception: return None

def nchars(x):
    """返回 (ascii 字符数, 非 ascii 字符数, 图片数)"""
    a = n = img = 0
    if isinstance(x, str):
        na = sum(1 for ch in x if ord(ch) > 127); return len(x) - na, na, 0
    if isinstance(x, list):
        for b in x:
            if isinstance(b, dict):
                if b.get('type') == 'image': img += 1; continue
                t = b.get('text') if 'text' in b else b.get('content', '')
                r = nchars(t if t is not None else ''); a += r[0]; n += r[1]; img += r[2]
            elif isinstance(b, str):
                r = nchars(b); a += r[0]; n += r[1]
    elif isinstance(x, dict):
        return nchars([x])
    return a, n, img

def tok(a, n, img=0, ka=0.30, kn=0.75):   # 估算系数，下面会用实测增量标定后覆盖
    return a * ka + n * kn + img * 1500

def bash_kind(cmd):
    c = cmd.strip()
    c = re.sub(r'^(cd\s+\S+\s*(&&|;)\s*)+', '', c)
    c = re.sub(r'^(\w+=\S+\s+)+', '', c)
    if '<<' in c.split('\n')[0] and re.match(r'(python3?|\S*python)\s+-', c): return 'python3 - <<heredoc（内联脚本）'
    parts = c.split()
    if not parts: return 'bash:空'
    w0 = os.path.basename(parts[0])
    if w0 in ('python', 'python3') or w0.endswith('python'):
        if len(parts) > 1 and parts[1] == '-c': return 'python3 -c（内联）'
        if len(parts) > 1 and parts[1] == '-m': return 'python3 -m ' + (parts[2] if len(parts) > 2 else '')
        if len(parts) > 1: return 'python3 运行脚本(' + os.path.splitext(parts[1])[1] + ')'
    if w0 == 'git' and len(parts) > 1: return 'git ' + parts[1]
    if w0 in ('sed', 'awk') and '-n' in parts[:3]: return w0 + ' -n（按行段打印）'
    if w0 in ('cat', 'head', 'tail'):
        ext = {os.path.splitext(p)[1] for p in parts[1:] if not p.startswith('-')}
        return f'{w0} {"/".join(sorted(e for e in ext if e)) or "(无扩展名)"}'
    return w0

def mcp_name(n):
    if n.startswith('mcp__'):
        p = n.split('__'); return 'MCP:' + p[1] + ('/' + p[2] if len(p) > 2 else '')
    return n

files = [f for f in glob.glob(ROOT + '/**/*.jsonl', recursive=True) if not bad(f)]
files = [f for f in files if os.path.getmtime(f) >= FROM.timestamp()]

seen = set()
R = dict(req=[], items=[], files={}, reads=[], calib=[], subagents={}, agentcalls=[], firstreq=[], shapes=[])
for f in files:
    is_sub = '/subagents/' in f
    tu = {}            # tool_use_id -> (name, kind, detail)
    events = []        # 顺序事件
    lines = []
    for l in open(f, errors='ignore'):
        try: lines.append(json.loads(l))
        except Exception: pass
    # 先把每个 message.id 的最终 usage 取出（同一条消息多条记录时取 output 最大的）
    final = {}
    for j in lines:
        if j.get('type') == 'assistant':
            m = j.get('message') or {}
            u = m.get('usage'); mid = m.get('id')
            if u and mid:
                if mid not in final or u.get('output_tokens', 0) > final[mid].get('output_tokens', 0): final[mid] = u
    local_ids = set()
    for j in lines:
        t = j.get('type'); T = ts(j)
        if t == 'system' and j.get('subtype') == 'compact_boundary':
            events.append(('compact', T)); continue
        if t == 'api-request-shape':
            sh = j.get('shape') or {}
            sysc = sum(len(b.get('text', '')) for b in sh.get('system', []) if isinstance(b, dict))
            tl = sh.get('tools', [])
            toolc = sum(len(json.dumps(x, ensure_ascii=False)) for x in tl)
            R['shapes'].append(dict(f=f, sys=sysc, tools=toolc, ntools=len(tl), sub=is_sub,
                                    mcp=sum(len(json.dumps(x, ensure_ascii=False)) for x in tl if x.get('name', '').startswith('mcp__')),
                                    nmcp=sum(1 for x in tl if x.get('name', '').startswith('mcp__'))))
            continue
        if t == 'assistant':
            m = j.get('message') or {}
            mid = m.get('id'); u = final.get(mid)
            for b in m.get('content') or []:
                if isinstance(b, dict) and b.get('type') == 'tool_use':
                    nm = b.get('name', ''); inp = b.get('input') or {}
                    if nm == 'Bash': kind = 'Bash: ' + bash_kind(inp.get('command', ''))
                    elif nm == 'Read':
                        p = inp.get('file_path', ''); kind = 'Read ' + (os.path.splitext(p)[1] or '(无扩展名)') + (' 分段' if inp.get('offset') or inp.get('limit') else ' 整个文件')
                    elif nm in ('Agent', 'Task'): kind = 'Agent: ' + str(inp.get('subagent_type') or '默认')
                    elif nm == 'Grep': kind = 'Grep ' + str(inp.get('output_mode', 'files_with_matches')) + (' 带上下文' if any(k in inp for k in ('-C', '-A', '-B', 'context')) else '')
                    else: kind = mcp_name(nm)
                    tu[b.get('id')] = (nm, kind, inp.get('file_path') if nm in ('Read', 'Edit', 'Write') else None)
                    if nm in ('Agent', 'Task'):
                        R['agentcalls'].append(dict(f=f, id=b.get('id'), type=inp.get('subagent_type'), model=inp.get('model'),
                                                    bg=inp.get('run_in_background'), desc=inp.get('description', ''), plen=len(inp.get('prompt', '')),
                                                    T=T.isoformat() if T else None))
                    if nm in ('Edit', 'Write', 'NotebookEdit'):
                        events.append(('edit', T, inp.get('file_path')))
            if not u or mid in seen or mid in local_ids:
                continue
            seen.add(mid); local_ids.add(mid)
            if T is None: continue
            cc = u.get('cache_creation') or {}
            cw_raw = u.get('cache_creation_input_tokens', 0)
            cw5 = cc.get('ephemeral_5m_input_tokens', cw_raw if not cc else 0); cw1 = cc.get('ephemeral_1h_input_tokens', 0)
            cw = 1.25 * cw5 + 2.0 * cw1
            mdl = m.get('model') or ''
            mdl = 'opus' if 'opus' in mdl else 'sonnet' if 'sonnet' in mdl else 'haiku' if 'haiku' in mdl else 'other'
            cr = u.get('cache_read_input_tokens', 0); inp_ = u.get('input_tokens', 0); out = u.get('output_tokens', 0)
            ctx = cr + cw_raw + inp_
            side = 'sub' if (is_sub or j.get('isSidechain')) else 'main'
            req = dict(f=f, T=T, mdl=mdl, side=side, cr=cr, cw_raw=cw_raw, cw5=cw5, cw1=cw1, inp=inp_, out=out, ctx=ctx,
                       cost=cr * 0.1 + cw + inp_ + out * 5, cost_cr=cr * 0.1, cost_cw=cw, cost_inp=inp_, cost_out=out * 5,
                       inwin=T >= FROM)
            R['req'].append(req)
            events.append(('req', T, len(R['req']) - 1))
            continue
        if t == 'user':
            m = j.get('message') or {}
            c = m.get('content')
            if isinstance(c, list):
                for b in c:
                    if not isinstance(b, dict): continue
                    if b.get('type') == 'tool_result':
                        nm, kind, fp = tu.get(b.get('tool_use_id'), ('?', '?', None))
                        a, n, img = nchars(b.get('content', ''))
                        events.append(('item', T, dict(cat='tool:' + mcp_name(nm), kind=kind, a=a, n=n, img=img, fp=fp, tool=nm, err=bool(b.get('is_error')))))
                    elif b.get('type') == 'text':
                        a, n, img = nchars(b.get('text', ''))
                        events.append(('item', T, dict(cat='user_text' + (':meta' if j.get('isMeta') else ''), kind='user', a=a, n=n, img=img)))
                    elif b.get('type') == 'image':
                        events.append(('item', T, dict(cat='user_image', kind='image', a=0, n=0, img=1)))
            elif isinstance(c, str):
                a, n, img = nchars(c)
                kind = 'compact_summary' if j.get('isCompactSummary') else ('meta' if j.get('isMeta') else 'prompt')
                events.append(('item', T, dict(cat='user_text:' + kind, kind=kind, a=a, n=n, img=0)))
            continue
        if t == 'attachment':
            a_ = j.get('attachment') or {}
            at = a_.get('type')
            rend = ''.join(r.get('content', '') for r in (j.get('rendered') or []) if isinstance(r, dict) and isinstance(r.get('content'), str))
            a, n, img = nchars(rend)
            sub = ''
            if at == 'hook_additional_context':
                txt = rend
                sub = ':' + str(a_.get('hookName'))
                if 'AGENTS.md' in txt[:300]: sub += '(AGENTS.md)'
                elif '[上下文守卫]' in txt: sub += '(context_guard)'
                elif '[自动接续]' in txt: sub += '(session_resume)'
            if at == 'queued_command':
                p = a_.get('prompt') or ''
                p = p if isinstance(p, str) else json.dumps(p)
                sub = ':' + ('task-notification' if a_.get('commandMode') == 'task-notification' else 'henchmen-room' if p.startswith('Room message from') else 'agent-message' if p.startswith('<agent-message') else 'project_memory' if p.startswith('<project_claude_me') else 'user-queued')
            if at == 'nested_memory':
                sub = ':' + os.path.basename(str(a_.get('path')))
            events.append(('item', T, dict(cat='att:' + str(at) + sub, kind=at, a=a, n=n, img=img)))
    R['files'][f] = events
    # 子 agent 汇总
    if is_sub:
        meta = {}
        mp = f[:-6] + '.meta.json'
        if os.path.exists(mp):
            try: meta = json.load(open(mp))
            except Exception: pass
        R['subagents'][f] = meta

# ---- 标定 token/字符 ----
pts = []
for f, ev in R['files'].items():
    last = None; between = []; comp = False
    for e in ev:
        if e[0] == 'compact': comp = True
        elif e[0] == 'item': between.append(e[2])
        elif e[0] == 'req':
            r = R['req'][e[2]]
            if last is not None and not comp and len(between) == 1 and between[0]['cat'].startswith('tool:') and between[0]['img'] == 0:
                it = between[0]; tot = it['a'] + it['n']
                if tot > 3000:
                    d = r['ctx'] - last['ctx'] - last['out']
                    pts.append((it['a'], it['n'], d))
            last = r; between = []; comp = False
# 两参数最小二乘（过原点）
import math
Saa = sum(a * a for a, n, d in pts); Snn = sum(n * n for a, n, d in pts); San = sum(a * n for a, n, d in pts)
Sad = sum(a * d for a, n, d in pts); Snd = sum(n * d for a, n, d in pts)
det = Saa * Snn - San * San
ka, kn = ((Sad * Snn - Snd * San) / det, (Snd * Saa - Sad * San) / det) if det else (0.3, 0.75)
ratios = sorted(d / (a + n) for a, n, d in pts if a + n)
R['calib'] = dict(n=len(pts), ka=ka, kn=kn, median_ratio=ratios[len(ratios) // 2] if ratios else None,
                  p25=ratios[len(ratios) // 4] if ratios else None, p75=ratios[3 * len(ratios) // 4] if ratios else None)
print('calib', R['calib'])
pickle.dump(R, open(OUT + '/r5.pkl', 'wb'))
