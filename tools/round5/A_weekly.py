#!/usr/bin/env python3
"""第 5 轮 A：把"首请求少了 Δ token"换算成实际成本节省（按真实 transcript 回放）。

口径和 round5_where_tokens_go / weekly_compare 一致：cache_read×0.1，缓存写入 5 分钟 1.25×、1 小时 2×，
未缓存 input×1，output×5；排除 -private-tmp-（实验）和 .secret/sealed/test2；按 message.id 去重。

回放规则（估算）：被去掉的内容在前缀最前面（工具定义、系统提示、首条 user 消息里的 skill 列表）。
  - 某次请求 cache_read ≥ 该会话首请求的上下文大小 → 这段前缀是读缓存的，省 0.1×Δ
  - 否则（首请求、缓存过期后整段重写、模型切换等）→ 这段是写缓存的，按该次请求 1h/5m 写入比例省 2×Δ 或 1.25×Δ
只读统计，不输出正文。
用法：python3 A_weekly.py --dmain 11000 --dsub 11000 [--from 2026-11-06T07:58:00Z] [--to ...]
"""
import json, glob, os, sys, datetime as dt, collections

ROOT = os.path.expanduser('~/.claude/projects')


def bad(f):
    return f.split('/projects/')[1].startswith('-private-tmp-') or any(x in f for x in ('.secret', 'sealed', 'test2'))


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00'))


def requests(path):
    seen = {}
    with open(path) as f:
        for line in f:
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get('type') != 'assistant':
                continue
            m = d.get('message') or {}
            u = m.get('usage')
            if not u or m.get('model') == '<synthetic>':
                continue
            mid = m.get('id') or d.get('uuid')
            seen[mid] = (d.get('timestamp'), u)
    return list(seen.values())


def main():
    a = sys.argv[1:]
    opt = {'--dmain': '0', '--dsub': '0', '--from': '2026-11-06T07:58:00Z', '--to': None, '--thr-main': '0', '--thr-sub': '0'}
    while a:
        k = a.pop(0); opt[k] = a.pop(0)
    dmain, dsub = int(opt['--dmain']), int(opt['--dsub'])
    # --thr-*：被去掉的内容在前缀里的结束位置（token）。cache_read 超过它就算读缓存。
    # 0 = 用保守规则（cache_read ≥ 90% 首请求上下文才算读，偏向高估节省）
    thr = {'main': int(opt['--thr-main']), 'sub': int(opt['--thr-sub'])}
    t_from = parse_ts(opt['--from'])
    t_to = parse_ts(opt['--to']) if opt['--to'] else dt.datetime.now(dt.timezone.utc)
    files = [f for f in glob.glob(ROOT + '/*/*.jsonl') + glob.glob(ROOT + '/*/*/subagents/*.jsonl') if not bad(f)]
    st = collections.defaultdict(float)
    for f in files:
        if os.path.getmtime(f) < t_from.timestamp():
            continue
        is_sub = '/subagents/' in f
        reqs = requests(f)
        if not reqs:
            continue
        first_ctx = None
        counted_session = False
        for ts, u in reqs:
            if not ts:
                continue
            t = parse_ts(ts)
            cr = u.get('cache_read_input_tokens', 0) or 0
            cw = u.get('cache_creation_input_tokens', 0) or 0
            inp = u.get('input_tokens', 0) or 0
            out = u.get('output_tokens', 0) or 0
            ctx = cr + cw + inp
            if first_ctx is None:
                first_ctx = ctx
            if not (t_from <= t <= t_to):
                continue
            cc = u.get('cache_creation') or {}
            w1h = cc.get('ephemeral_1h_input_tokens', 0) or 0
            w5m = cc.get('ephemeral_5m_input_tokens', 0) or 0
            wmul = (2.0 * w1h + 1.25 * w5m) / (w1h + w5m) if (w1h + w5m) else 2.0
            cost = cr * 0.1 + w1h * 2.0 + w5m * 1.25 + max(cw - w1h - w5m, 0) * 1.25 + inp + out * 5
            g = 'sub' if is_sub else 'main'
            d = dsub if is_sub else dmain
            st[g + '_cost'] += cost
            st[g + '_req'] += 1
            if not counted_session:
                st[g + '_n'] += 1; counted_session = True
            th = thr[g] if thr[g] else 0.9 * first_ctx
            if cr >= th:
                st[g + '_save'] += 0.1 * d; st[g + '_reads'] += 1
            else:
                st[g + '_save'] += wmul * d; st[g + '_writes'] += 1
    days = (t_to - t_from).total_seconds() / 86400
    tot = st['main_cost'] + st['sub_cost']
    sav = st['main_save'] + st['sub_save']
    res = {
        'window_days': round(days, 2),
        'main_sessions': int(st['main_n']), 'subagents': int(st['sub_n']),
        'main_requests': int(st['main_req']), 'sub_requests': int(st['sub_req']),
        'main_prefix_writes': int(st['main_writes']), 'sub_prefix_writes': int(st['sub_writes']),
        'total_cost_M': round(tot / 1e6, 2),
        'save_main_M': round(st['main_save'] / 1e6, 3), 'save_sub_M': round(st['sub_save'] / 1e6, 3),
        'save_pct': round(100 * sav / tot, 2) if tot else None,
        'save_per_week_M': round(sav / 1e6 / days * 7, 1) if days else None,
        'save_per_main_session_k': round(st['main_save'] / st['main_n'] / 1e3, 1) if st['main_n'] else None,
        'save_per_subagent_k': round(st['sub_save'] / st['sub_n'] / 1e3, 1) if st['sub_n'] else None,
    }
    print(json.dumps(res, ensure_ascii=False))


if __name__ == '__main__':
    main()
