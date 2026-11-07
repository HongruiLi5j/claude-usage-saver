#!/usr/bin/env python3
"""B_compact_audit.py — 只读：统计 transcript 里的压缩和冷缓存重写（第 5 轮 B）。

用途：
  1. 实验后确认 compact 有没有发生、发生在多少 token（以 transcript 为准）。
  2. 下次通宵/henchmen 运行后，确认 autoCompactWindow / 无人值守模式是否生效。

只读 usage / 时间戳 / compact_boundary 元数据 / 是否 API 报错，不输出对话正文。

用法：
  python3 B_compact_audit.py <transcript.jsonl 或 项目目录 或 cwd 路径> [--since 2026-11-04] [--min-req 5] [--json]
  python3 B_compact_audit.py --session <session_id>           # 在 ~/.claude/projects 下找
目录会连带统计 <session>/subagents/*.jsonl（标 [sub]）。
"""
import argparse, datetime as dt, glob, json, os, re, sys

PROJ = os.path.expanduser('~/.claude/projects')


def P(s):
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00'))


def proj_dir_for(path):
    """cwd 路径 -> ~/.claude/projects/<编码后>"""
    return os.path.join(PROJ, re.sub(r'[^a-zA-Z0-9]', '-', os.path.realpath(path)))


def audit_file(f):
    seen = set(); reqs = []; comps = []; errs = 0; models = {}
    for line in open(f, errors='replace'):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get('subtype') == 'compact_boundary':
            m = r.get('compactMetadata') or {}
            comps.append({'t': r.get('timestamp'), 'trigger': m.get('trigger'),
                          'pre': m.get('preTokens'), 'post': m.get('postTokens')})
            continue
        if r.get('type') != 'assistant':
            continue
        msg = r.get('message') or {}
        if r.get('isApiErrorMessage'):
            errs += 1
        u = msg.get('usage'); mid = msg.get('id')
        if not u or mid in seen or msg.get('model') == '<synthetic>':
            continue
        seen.add(mid)
        models[msg.get('model')] = models.get(msg.get('model'), 0) + 1
        cw = u.get('cache_creation_input_tokens', 0) or 0
        ctx = (u.get('input_tokens', 0) or 0) + (u.get('cache_read_input_tokens', 0) or 0) + cw
        cc = u.get('cache_creation') or {}
        reqs.append({'t': r.get('timestamp'), 'ctx': ctx, 'cw': cw,
                     'cw1h': cc.get('ephemeral_1h_input_tokens', 0) or 0,
                     'out': u.get('output_tokens', 0) or 0})
    # 冷缓存重写：距上次请求 >55 分钟且写入 > 上下文一半
    cold = []
    for a, b in zip(reqs, reqs[1:]):
        gap = (P(b['t']) - P(a['t'])).total_seconds() / 60
        if gap > 55 and b['cw'] > 0.5 * b['ctx']:
            cold.append({'t': b['t'], 'gap_min': round(gap), 'prev_ctx': a['ctx'], 'cw': b['cw']})
    return {'file': f, 'n': len(reqs), 'models': models, 'max_ctx': max((x['ctx'] for x in reqs), default=0),
            'compacts': comps, 'cold_rewrites': cold, 'api_errors': errs,
            'first': reqs[0]['t'] if reqs else None, 'last': reqs[-1]['t'] if reqs else None,
            'ctx_series': [x['ctx'] for x in reqs]}


def files_for(target):
    if target.endswith('.jsonl') and os.path.isfile(target):
        return [target]
    d = target if os.path.isdir(target) and target.startswith(PROJ) else proj_dir_for(target)
    if not os.path.isdir(d):
        sys.exit(f'找不到 transcript 目录：{d}')
    fs = sorted(glob.glob(os.path.join(d, '*.jsonl')))
    fs += sorted(glob.glob(os.path.join(d, '*', 'subagents', '*.jsonl')))
    return fs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('target', nargs='?')
    ap.add_argument('--session')
    ap.add_argument('--since')
    ap.add_argument('--min-req', type=int, default=1)
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--series', action='store_true', help='打印每次请求的上下文序列')
    a = ap.parse_args()
    if a.session:
        fs = glob.glob(os.path.join(PROJ, '*', a.session + '.jsonl')) + glob.glob(os.path.join(PROJ, '*', a.session, 'subagents', '*.jsonl'))
    elif a.target:
        fs = files_for(a.target)
    else:
        ap.error('要给 target 或 --session')
    out = []
    for f in fs:
        r = audit_file(f)
        if r['n'] < a.min_req:
            continue
        if a.since and (r['last'] or '') < a.since:
            continue
        out.append(r)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1)); return
    tot_cold = 0
    for r in out:
        tag = '[sub] ' if '/subagents/' in r['file'] else ''
        sid = os.path.basename(r['file'])[:-6]
        print(f"{tag}{sid[:12]}  请求 {r['n']}  最大上下文 {r['max_ctx']/1000:.0f}k  模型 {r['models']}  API报错 {r['api_errors']}  {(r['first'] or '')[:16]} → {(r['last'] or '')[:16]}")
        for c in r['compacts']:
            print(f"    compact {c['t'][:19]}  trigger={c['trigger']}  pre={c['pre']}  post={c['post']}")
        for c in r['cold_rewrites']:
            tot_cold += c['cw']
            print(f"    冷重写 {c['t'][:16]}  空闲 {c['gap_min']} 分钟  之前上下文 {c['prev_ctx']/1000:.0f}k  写入 {c['cw']/1000:.0f}k")
        if a.series:
            print('    ctx:', ' '.join(f"{x//1000}" for x in r['ctx_series']))
    print(f"合计 {len(out)} 个文件；冷重写写入 {tot_cold/1e6:.2f}M token（1h 缓存按 2× 计约 {2*tot_cold/1e6:.2f}M 单位）")


if __name__ == '__main__':
    main()
