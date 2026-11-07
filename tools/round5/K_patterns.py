#!/usr/bin/env python3
"""第 5 轮 K：对话模式层面的浪费统计（只读 transcript，只出数字，不输出对话正文）。

统计的是 round5_where_tokens_go.md 没覆盖的维度：
  1. 工具报错后的"纠错请求"成本，按错误类别（Edit 匹配失败、文件未读、用户拒绝、退出码非 0 …）
  2. 测试失败 → 改 → 再跑 的循环
  3. 所有者的"纠正/重来"类消息之后那一轮的成本（关键词分类，只计数）
  4. 用户中断（Request interrupted）
  5. Write 覆盖写同一文件（应改用 Edit）的 output 浪费
  6. 多个子 agent 重复读同一个文件（共享上下文税）
  7. 模型 × effort 的单次成本、output、思考时长
  8. 一天中各时段的成本、429 撞限时刻
  9. 空闲间隔 → 缓存重写（按主会话/子 agent 和 TTL 分）
用法：python3 K_patterns.py [--since 2026-10-21] [--post] [--json out.json]
"""
import json, glob, os, re, sys, collections, datetime as dt, argparse

ROOT = os.path.expanduser('~/.claude/projects')
UTC = dt.timezone.utc
PST = dt.timezone(dt.timedelta(hours=-8))
KIT_ON = dt.datetime(2026, 11, 6, 7, 58, tzinfo=UTC)   # 11-05 23:58 PST，套件启用

CORRECT_RE = re.compile(r'(不对|错了|不是这|重来|重新做|撤回|你没|没有按|别这样|不要这样|我说的是|理解错|搞错|wrong|redo|not what i|that\'s not|undo)', re.I)
INTERRUPT = '[Request interrupted by user'
TEST_RE = re.compile(r'(^|[\s;&|/])(pytest|py\.test|npm (run )?test|node --test|lake (build|test)|cargo test|go test|unittest|tests?_\w*\.py|scorecard\.py)\b')


def bad(f):
    rel = f.split('/projects/')[1]
    return rel.startswith('-private-tmp-') or any(x in f for x in ('.secret', 'sealed', 'test2'))


def ts(j):
    try:
        return dt.datetime.fromisoformat(j['timestamp'].replace('Z', '+00:00'))
    except Exception:
        return None


def text_of(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out = []
        for b in c:
            if isinstance(b, dict):
                if b.get('type') == 'text':
                    out.append(b.get('text') or '')
                elif 'content' in b:
                    out.append(text_of(b.get('content')))
            elif isinstance(b, str):
                out.append(b)
        return '\n'.join(out)
    return ''


def tok_est(s):
    """字符 → token 估算（round5_where_tokens_go 的标定：ASCII 0.43、非 ASCII 1.39）"""
    na = sum(1 for ch in s if ord(ch) > 127)
    return (len(s) - na) * 0.43 + na * 1.39


def err_kind(tool, txt):
    t = txt[:400].lower()
    if 'string to replace not found' in t or 'old_string' in t and 'not found' in t:
        return 'Edit: old_string 未匹配'
    if 'found' in t and 'matches' in t and 'replace_all' in t:
        return 'Edit: 多处匹配'
    if 'has not been read' in t or 'read it first' in t or 'must read' in t:
        return f'{tool}: 未先 Read'
    if 'modified since read' in t or 'file has been modified' in t:
        return f'{tool}: 读后被改'
    if "doesn't want to proceed" in t or 'user rejected' in t or 'was rejected' in t or 'the user denied' in t:
        return '用户拒绝'
    if 'interrupted' in t:
        return '中断'
    if 'inputvalidationerror' in t or 'invalid input' in t or 'validation' in t:
        return f'{tool}: 参数错误'
    if 'exceeds maximum' in t or 'too large' in t or 'token limit' in t or 'exceeds' in t and 'tokens' in t:
        return f'{tool}: 结果过大'
    if 'no such file' in t or 'does not exist' in t or 'file not found' in t or 'enoent' in t:
        return f'{tool}: 路径不存在'
    if 'timed out' in t or 'timeout' in t:
        return f'{tool}: 超时'
    if 'permission' in t or 'not allowed' in t or 'denied' in t or 'blocked' in t or 'sandbox' in t:
        return f'{tool}: 权限/沙箱拦截'
    if tool == 'Bash' and ('exit code' in t or t.startswith('error')):
        return 'Bash: 非 0 退出'
    return f'{tool}: 其它'


def load(since, until=None):
    files = [f for f in glob.glob(ROOT + '/**/*.jsonl', recursive=True) if not bad(f)]
    files = [f for f in files if os.path.getmtime(f) >= since.timestamp()]
    return sorted(files)


def analyze(files, since, until=None):
    seen = set()
    S = collections.defaultdict(float)          # 标量
    T = collections.defaultdict(lambda: collections.defaultdict(float))  # 表
    reads_by_file = collections.defaultdict(lambda: dict(agents=set(), tok=0.0, n=0))
    sess_rows = []
    for f in files:
        is_sub = '/subagents/' in f
        side = 'sub' if is_sub else 'main'
        parent = os.path.dirname(os.path.dirname(f)) if is_sub else f[:-6]
        lines = []
        for l in open(f, errors='ignore'):
            try:
                lines.append(json.loads(l))
            except Exception:
                pass
        final = {}
        for j in lines:
            if j.get('type') == 'assistant':
                m = j.get('message') or {}
                u = m.get('usage'); mid = m.get('id')
                if u and mid and (mid not in final or u.get('output_tokens', 0) >= final[mid].get('output_tokens', 0)):
                    final[mid] = u
        tu = {}              # tool_use_id -> (name, input)
        pending_flag = None  # 下一次请求要记到哪个类别
        last_req_T = None
        last_ctx = 0
        written = set()
        test_state = None    # 'fail' 表示上次测试失败
        fail_fix_cycles = 0
        sess_cost = 0.0
        flags_for_next = []
        turn_after_correct = False
        for j in lines:
            t = j.get('type'); TT = ts(j)
            if TT is None or TT < since or (until and TT >= until):
                continue
            if t == 'assistant':
                m = j.get('message') or {}
                mid = m.get('id'); u = final.get(mid)
                for b in m.get('content') or []:
                    if isinstance(b, dict) and b.get('type') == 'tool_use':
                        nm = b.get('name', ''); inp = b.get('input') or {}
                        tu[b.get('id')] = (nm, inp)
                        if nm == 'Write':
                            p = inp.get('file_path', ''); c = inp.get('content', '') or ''
                            k = tok_est(c)
                            S['write_out_tok'] += k
                            if p in written:
                                S['rewrite_out_tok'] += k; S['rewrite_n'] += 1
                            written.add(p)
                        if nm in ('Edit', 'NotebookEdit'):
                            S['edit_out_tok'] += tok_est((inp.get('new_string') or '') + (inp.get('old_string') or ''))
                            written.add(inp.get('file_path', ''))
                if not u or mid in seen:
                    continue
                seen.add(mid)
                cc = u.get('cache_creation') or {}
                cw_raw = u.get('cache_creation_input_tokens', 0) or 0
                cw5 = cc.get('ephemeral_5m_input_tokens', cw_raw if not cc else 0) or 0
                cw1 = cc.get('ephemeral_1h_input_tokens', 0) or 0
                cr = u.get('cache_read_input_tokens', 0) or 0
                inp_ = u.get('input_tokens', 0) or 0
                out = u.get('output_tokens', 0) or 0
                cost = cr * 0.1 + cw5 * 1.25 + cw1 * 2.0 + inp_ + out * 5
                ctx = cr + cw_raw + inp_
                mdl = m.get('model') or ''
                mdl = 'opus' if 'opus' in mdl else 'sonnet' if 'sonnet' in mdl else 'haiku' if 'haiku' in mdl else ('fable' if 'fable' in mdl else 'other')
                if mdl == 'other' and m.get('model') == '<synthetic>':
                    continue
                eff = j.get('effort') or j.get('perTurnEffort') or '?'
                S['cost'] += cost; S['n'] += 1; S['cost_' + side] += cost
                sess_cost += cost
                key = f'{side}·{mdl}·{eff}'
                T['model_effort'][key + '|n'] += 1
                T['model_effort'][key + '|cost'] += cost
                T['model_effort'][key + '|out'] += out
                T['model_effort'][key + '|ctx'] += ctx
                T['model_effort'][key + '|think_ms'] += j.get('thinkingDurationMs') or 0
                T['model_effort'][key + '|think_n'] += 1 if j.get('thinkingDurationMs') else 0
                hr = TT.astimezone(PST).hour
                T['hour'][hr] += cost
                T['hour_n'][hr] += 1
                T['dow'][TT.astimezone(PST).strftime('%a')] += cost
                # 空闲间隔与缓存重写
                if last_req_T is not None:
                    gap = (TT - last_req_T).total_seconds()
                    b = '<5m' if gap < 300 else '5–60m' if gap < 3600 else '1–4h' if gap < 14400 else '>4h'
                    T['gap_n'][f'{side}|{b}'] += 1
                    T['gap_cost'][f'{side}|{b}'] += cost
                    # "重写"：缓存写入超过上次上下文的 50%（缓存失效后整段重写）
                    if cw_raw > 0.5 * max(last_ctx, 1) and last_ctx > 20000:
                        excess = min(cw_raw, last_ctx)
                        extra = excess * ((1.25 if cw5 >= cw1 else 2.0) - 0.1)
                        T['rewrite_n'][f'{side}|{b}'] += 1
                        T['rewrite_cost'][f'{side}|{b}'] += extra
                        T['rewrite_ttl'][f'{side}|{"5m" if cw5 >= cw1 else "1h"}|{b}'] += extra
                last_req_T = TT; last_ctx = ctx
                for fl in flags_for_next:
                    T['after_flag_cost'][fl] += cost
                    T['after_flag_n'][fl] += 1
                flags_for_next = []
                if turn_after_correct:
                    T['after_flag_cost']['所有者纠正后整轮'] += cost
                    T['after_flag_n']['所有者纠正后整轮'] += 1
                if j.get('isApiErrorMessage'):
                    T['apierr'][str(j.get('apiError'))] += 1
                continue
            if t == 'assistant' and j.get('isApiErrorMessage'):
                pass
            if t == 'user':
                m = j.get('message') or {}
                c = m.get('content')
                if j.get('isMeta') or j.get('isCompactSummary'):
                    continue
                tool_results = [b for b in c if isinstance(b, dict) and b.get('type') == 'tool_result'] if isinstance(c, list) else []
                if tool_results:
                    for b in tool_results:
                        nm, inp = tu.get(b.get('tool_use_id'), ('?', {}))
                        txt = text_of(b.get('content'))
                        ktok = tok_est(txt) if len(txt) < 400000 else len(txt) * 0.5
                        if nm == 'Read' or (nm == 'Bash' and re.match(r'\s*(cat|sed -n|head|tail)\s', inp.get('command', '') or '')):
                            p = inp.get('file_path') if nm == 'Read' else None
                            if nm == 'Bash':
                                mm = re.findall(r'(/[\w./~@+-]+\.\w+)', inp.get('command', ''))
                                p = mm[0] if len(mm) == 1 else None
                            if p and is_sub:
                                d = reads_by_file[os.path.basename(p) + '@' + os.path.basename(os.path.dirname(p))]
                                d['agents'].add(f); d['tok'] += ktok; d['n'] += 1
                        if nm == 'Bash':
                            cmd = inp.get('command', '') or ''
                            if TEST_RE.search(cmd):
                                failed = bool(b.get('is_error')) or bool(re.search(r'\b(\d+ failed|FAILED|Error|Traceback|AssertionError)\b', txt[-3000:]))
                                S['test_runs'] += 1
                                if failed:
                                    S['test_fail'] += 1
                                    if test_state == 'fail':
                                        fail_fix_cycles += 1
                                    test_state = 'fail'
                                    flags_for_next.append('测试失败后')
                                else:
                                    test_state = 'pass'
                        if b.get('is_error'):
                            k = err_kind(nm, txt)
                            T['err_n'][k] += 1
                            flags_for_next.append('报错后:' + k)
                            S['err_n'] += 1
                        S['tool_results'] += 1
                        T['tool_n'][nm] += 1
                    continue
                # 真正的所有者消息
                txt = text_of(c)
                if not txt.strip():
                    continue
                if txt.startswith('<task-notification') or txt.startswith('<command-') or txt.startswith('<local-command') or txt.startswith('Caveat:'):
                    continue
                if INTERRUPT in txt:
                    S['interrupts'] += 1
                    continue
                S['human_msgs_' + side] += 1
                turn_after_correct = bool(CORRECT_RE.search(txt[:300])) and side == 'main'
                if turn_after_correct:
                    S['correct_msgs'] += 1
                if len(txt.strip()) <= 4:
                    S['tiny_msgs'] += 1
        if test_state is not None or fail_fix_cycles:
            S['fail_fix_cycles'] += fail_fix_cycles
            if fail_fix_cycles >= 3:
                S['sessions_with_3plus_cycles'] += 1
        sess_rows.append((sess_cost, side, f))
    dup = []
    for k, d in reads_by_file.items():
        na = len(d['agents'])
        if na >= 3:
            # 一个文件被 na 个子 agent 读：至少 (na-1) 份是重复的
            dup.append((d['tok'] * (na - 1) / na, k, na, d['n'], d['tok']))
    dup.sort(reverse=True)
    return S, T, dup, sess_rows


def pct(x, tot):
    return f'{100 * x / tot:.1f}%' if tot else '-'


def report(S, T, dup, label):
    tot = S['cost']
    print(f'\n######## {label}：{S["n"]:.0f} 次请求，总成本 {tot/1e6:.1f}M 单位 ########')
    print('\n## 1. 工具报错（次数）与"报错后下一次请求"成本')
    rows = sorted(T['err_n'].items(), key=lambda x: -x[1])
    for k, n in rows[:16]:
        c = T['after_flag_cost'].get('报错后:' + k, 0)
        print(f'  {k:28s} {n:6.0f} 次   下一请求成本 {c/1e6:6.2f}M ({pct(c, tot)})')
    print(f'  报错率：{S["err_n"]:.0f}/{S["tool_results"]:.0f} = {pct(S["err_n"], S["tool_results"])}')
    print('\n## 2. 测试循环')
    print(f'  测试运行 {S["test_runs"]:.0f} 次，失败 {S["test_fail"]:.0f}（{pct(S["test_fail"], S["test_runs"])}）；连续失败再跑 {S["fail_fix_cycles"]:.0f} 次；≥3 次连续失败的会话 {S["sessions_with_3plus_cycles"]:.0f} 个')
    c = T['after_flag_cost'].get('测试失败后', 0)
    print(f'  测试失败后的下一请求成本 {c/1e6:.2f}M ({pct(c, tot)})')
    print('\n## 3. 所有者纠正 / 中断 / 极短消息')
    c = T['after_flag_cost'].get('所有者纠正后整轮', 0)
    print(f'  主会话所有者消息 {S["human_msgs_main"]:.0f} 条；含"纠正/重来"关键词 {S["correct_msgs"]:.0f} 条（{pct(S["correct_msgs"], S["human_msgs_main"])}）；其后整轮成本 {c/1e6:.2f}M（{pct(c, tot)}）')
    print(f'  用户中断 {S["interrupts"]:.0f} 次；≤4 字的消息 {S["tiny_msgs"]:.0f} 条')
    print('\n## 4. Write / Edit 的 output')
    print(f'  Write 正文约 {S["write_out_tok"]/1e3:.0f}k token（×5 = {S["write_out_tok"]*5/1e6:.2f}M，{pct(S["write_out_tok"]*5, tot)}）；其中覆盖写已写过的文件 {S["rewrite_n"]:.0f} 次、{S["rewrite_out_tok"]/1e3:.0f}k token（{pct(S["rewrite_out_tok"]*5, tot)}）')
    print(f'  Edit 参数约 {S["edit_out_tok"]/1e3:.0f}k token（{pct(S["edit_out_tok"]*5, tot)}）')
    print('\n## 5. 子 agent 重复读同一文件（≥3 个 agent 读过）前 12')
    tdup = sum(d[0] for d in dup)
    print(f'  重复部分合计约 {tdup/1e3:.0f}k token 首次读入（每份之后还会被读 ~20 次）')
    for d in dup[:12]:
        print(f'  {d[1][:55]:55s} agent {d[2]:3d}  读 {d[3]:3d} 次  合计 {d[4]/1e3:6.1f}k  重复 {d[0]/1e3:6.1f}k')
    print('\n## 6. 模型 × effort')
    me = T['model_effort']
    keys = sorted({k.split('|')[0] for k in me}, key=lambda k: -me[k + '|cost'])
    for k in keys:
        n = me[k + '|n']
        if n < 20:
            continue
        tn = me[k + '|think_n']
        print(f'  {k:24s} n={n:6.0f}  成本 {pct(me[k+"|cost"], tot):>6s}  每次 {me[k+"|cost"]/n/1e3:5.1f}k  output/次 {me[k+"|out"]/n:6.0f}  上下文 {me[k+"|ctx"]/n/1e3:5.0f}k  思考 {me[k+"|think_ms"]/max(tn,1)/1000:5.1f}s（{pct(tn, n)} 有思考）')
    print('\n## 7. 时段（PST）成本')
    for h in range(24):
        c = T['hour'].get(h, 0)
        print(f'  {h:02d}时 {pct(c, tot):>6s} {"█" * int(60 * c / max(tot, 1))}  n={T["hour_n"].get(h, 0):.0f}')
    print('  ', {k: pct(v, tot) for k, v in T['dow'].items()})
    print('  API 错误：', dict(T['apierr']))
    print('\n## 8. 空闲间隔与缓存重写')
    for side in ('main', 'sub'):
        for b in ('<5m', '5–60m', '1–4h', '>4h'):
            k = f'{side}|{b}'
            print(f'  {side:4s} {b:6s} 请求 {T["gap_n"].get(k, 0):6.0f}  重写 {T["rewrite_n"].get(k, 0):4.0f} 次  重写多付 {T["rewrite_cost"].get(k, 0)/1e6:6.2f}M ({pct(T["rewrite_cost"].get(k, 0), tot)})')
    for k, v in sorted(T['rewrite_ttl'].items(), key=lambda x: -x[1])[:8]:
        print(f'    TTL {k:22s} {v/1e6:6.2f}M ({pct(v, tot)})')


# ---------------- 第二遍：按文件取请求序列（时间、上下文、缓存写入、之前最后一个工具） ----------------
def requests_by_file(files, since, until=None):
    seen = set()
    out = {}
    for f in files:
        reqs = []
        last_tool = None
        hit_limit = False
        for l in open(f, errors='ignore'):
            if '"assistant"' not in l:
                continue
            try:
                j = json.loads(l)
            except Exception:
                continue
            if j.get('type') != 'assistant':
                continue
            m = j.get('message') or {}
            if j.get('isApiErrorMessage') and j.get('apiError') == 'usage_limit_reached':
                hit_limit = True
            u = m.get('usage'); mid = m.get('id'); TT = ts(j)
            if u and mid and mid not in seen and m.get('model') != '<synthetic>' and TT and TT >= since and not (until and TT >= until):
                seen.add(mid)
                cc = u.get('cache_creation') or {}
                cw = u.get('cache_creation_input_tokens', 0) or 0
                ctx = cw + (u.get('cache_read_input_tokens', 0) or 0) + (u.get('input_tokens', 0) or 0)
                ttl = '1h' if (cc.get('ephemeral_1h_input_tokens', 0) or 0) > (cc.get('ephemeral_5m_input_tokens', 0) or 0) else '5m'
                reqs.append(dict(T=TT, ctx=ctx, cw=cw, ttl=ttl, prev_tool=last_tool, after_limit=hit_limit))
                hit_limit = False
            for b in m.get('content') or []:
                if isinstance(b, dict) and b.get('type') == 'tool_use':
                    nm = b.get('name', '')
                    if nm == 'Bash':
                        inp = b.get('input') or {}
                        c = re.sub(r'^(cd\s+\S+\s*(&&|;)\s*)+', '', inp.get('command', '') or '')
                        w0 = os.path.basename((c.split() or ['?'])[0])
                        nm = 'Bash:' + ('长任务(codex/claude)' if re.search(r'\b(codex exec|claude -p)\b', c) else
                                        '内联python' if re.match(r'python3?\s+-', c) else w0) + (' 后台' if inp.get('run_in_background') else '')
                    last_tool = nm
        out[f] = reqs
    return out


def cold_rewrites(rbf, min_prev_ctx=20000):
    """缓存过期后整段重写的事件：本次写入 > 上次上下文一半，且距上次 ≥ 阈值（子 agent 5 分钟、主会话 1 小时）。
    多付的钱 = 重写量 ×（写入单价 − 0.1）。"""
    ev = []
    for f, reqs in rbf.items():
        side = 'sub' if '/subagents/' in f else 'main'
        for a, b in zip(reqs, reqs[1:]):
            gap = (b['T'] - a['T']).total_seconds()
            if a['ctx'] < min_prev_ctx or b['cw'] <= 0.5 * a['ctx']:
                continue
            ttl_s = 300 if b['ttl'] == '5m' else 3600
            if gap < ttl_s * 0.95:
                continue
            w = min(b['cw'], a['ctx'])
            extra = w * ((1.25 if b['ttl'] == '5m' else 2.0) - 0.1)
            why = '撞限额后恢复' if b['after_limit'] else ('交回后又被 SendMessage 续用' if a['prev_tool'] in ('SubagentHandback',) or b['prev_tool'] == 'SubagentHandback'
                                                          else '等待：' + str(b['prev_tool']))
            ev.append(dict(side=side, gap_min=gap / 60, prev_ctx=a['ctx'], write=w, extra=extra, why=why, ttl=b['ttl']))
    return ev


def clear_breakeven(rbf, T=160000, restart=160000, new_ctx=50000, post_from=None):
    """主会话第一次越过 T 时如果清理：之后每次请求省 (ctx−new_ctx)×0.1，一次性成本 restart
    （交接 output + 新会话前置写入 + 接续注入 + 重新读文件，约 16 万单位，估算）。返回每个越线会话 (剩余请求数, 净收益)。"""
    rows = []
    for f, reqs in rbf.items():
        if '/subagents/' in f:
            continue
        for i, r in enumerate(reqs):
            if post_from and r['T'] < post_from:
                continue
            if r['ctx'] >= T:
                n = len(reqs) - i - 1
                rows.append((n, n * (r['ctx'] - new_ctx) * 0.1 - restart))
                break
    return rows


def report2(rbf, tot, label):
    ev = cold_rewrites(rbf)
    print(f'\n## 9. 缓存过期后整段重写（{label}）')
    g = collections.defaultdict(lambda: [0, 0.0, 0.0])
    for e in ev:
        k = (e['side'], e['why'])
        g[k][0] += 1; g[k][1] += e['extra']; g[k][2] += e['gap_min']
    for (side, why), (n, x, gm) in sorted(g.items(), key=lambda kv: -kv[1][1])[:12]:
        print(f'  {side:4s} {why[:34]:34s} {n:4d} 次  平均间隔 {gm/n:6.1f} 分  多付 {x/1e6:6.2f}M ({pct(x, tot)})')
    rows = clear_breakeven(rbf)
    if rows:
        neg = [r for r in rows if r[1] < 0]
        import statistics
        print(f'\n## 10. 守卫 16 万线"清理回本"估算：越线会话 {len(rows)}，剩余请求中位数 {statistics.median(r[0] for r in rows):.0f}；'
              f'清理会亏的 {len(neg)} 个（亏 {-sum(r[1] for r in neg)/1e6:.2f}M），会赚的合计 {sum(r[1] for r in rows if r[1] > 0)/1e6:.1f}M')
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default='2026-10-21')
    ap.add_argument('--post', action='store_true', help='只看套件启用后')
    ap.add_argument('--json')
    a = ap.parse_args()
    since = KIT_ON if a.post else dt.datetime.fromisoformat(a.since).replace(tzinfo=PST)
    files = load(since)
    S, T, dup, sess = analyze(files, since)
    label = ('套件启用后' if a.post else f'{a.since} 起') + f'，{len(files)} 个文件'
    report(S, T, dup, label)
    report2(requests_by_file(files, since), S['cost'], label)
    if a.json:
        json.dump(dict(S=S, T={k: dict(v) for k, v in T.items()}, dup=[(d[0], d[1], d[2], d[3], d[4]) for d in dup[:50]]),
                  open(a.json, 'w'), ensure_ascii=False, indent=1, default=str)


if __name__ == '__main__':
    main()
