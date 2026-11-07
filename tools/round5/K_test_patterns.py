#!/usr/bin/env python3
"""K_patterns.py 与 K_testall.py 解析逻辑的测试（合成 transcript，不读真实对话）。
运行：python3 tools/round5/K_test_patterns.py   （退出码 0 = 全过）
"""
import datetime as dt, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import K_patterns as P  # noqa: E402
import K_testall as TA  # noqa: E402

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 6, 10, 0, tzinfo=UTC)
RES = []


def check(name, cond, info=''):
    RES.append((name, bool(cond), info))


def iso(t):
    return t.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def asst(mid, t, cr=0, cw5=0, cw1=0, out=10, tools=(), model='claude-opus-x', **kw):
    content = [{'type': 'tool_use', 'id': tid, 'name': nm, 'input': inp} for tid, nm, inp in tools] or [{'type': 'text', 'text': 'x'}]
    d = {'type': 'assistant', 'timestamp': iso(t), 'effort': 'medium',
         'message': {'id': mid, 'model': model, 'content': content,
                     'usage': {'input_tokens': 0, 'cache_read_input_tokens': cr, 'cache_creation_input_tokens': cw5 + cw1,
                               'cache_creation': {'ephemeral_5m_input_tokens': cw5, 'ephemeral_1h_input_tokens': cw1},
                               'output_tokens': out}}}
    d.update(kw)
    return d


def result(t, tid, text, err=False):
    return {'type': 'user', 'timestamp': iso(t),
            'message': {'content': [{'type': 'tool_result', 'tool_use_id': tid, 'content': text, 'is_error': err}]}}


def human(t, text):
    return {'type': 'user', 'timestamp': iso(t), 'message': {'content': text}}


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')


m = lambda k: T0 + dt.timedelta(minutes=k)

# ---- err_kind ----
check('E01 Edit 未匹配', P.err_kind('Edit', 'String to replace not found in file.') == 'Edit: old_string 未匹配')
check('E02 未先 Read', P.err_kind('Write', 'File has not been read yet. Read it first') == 'Write: 未先 Read')
check('E03 用户拒绝', P.err_kind('Bash', "The user doesn't want to proceed with this tool use.") == '用户拒绝')
check('E04 Bash 非 0', P.err_kind('Bash', 'Exit code 1\nboom') == 'Bash: 非 0 退出')
check('E05 路径不存在', P.err_kind('Read', 'File does not exist.') == 'Read: 路径不存在')

with tempfile.TemporaryDirectory() as tmp:
    proj = os.path.join(tmp, 'projects', '-demo')
    main_f = os.path.join(proj, 'sess.jsonl')
    sub_f = os.path.join(proj, 'sess', 'subagents', 'agent-x.jsonl')
    rows = [
        human(m(0), '开始'),
        asst('a1', m(0), cr=100000, out=100, tools=[('t1', 'Bash', {'command': 'pytest -q'})]),
        asst('a1', m(0), cr=100000, out=100, tools=[('t1', 'Bash', {'command': 'pytest -q'})]),   # 同一 message.id 重复一行
        result(m(1), 't1', 'E   AssertionError\n1 failed, 3 passed', err=True),
        asst('a2', m(1), cr=100000, cw1=2000, out=50, tools=[('t2', 'Bash', {'command': 'pytest -q'})]),
        result(m(2), 't2', '1 failed', err=True),
        asst('a3', m(2), cr=102000, out=50, tools=[('t3', 'Write', {'file_path': '/x/r.md', 'content': 'a' * 1000}),
                                                    ('t4', 'Write', {'file_path': '/x/r.md', 'content': 'b' * 1000})]),
        human(m(3), '不对，重来'),
        asst('a4', m(3), cr=102000, out=50),
        # 2 小时后回来：缓存全冷，整段 1h 写入
        human(m(125), '继续'),
        asst('a5', m(125), cr=0, cw1=104000, out=50),
        # 撞限额，之后恢复
        {'type': 'assistant', 'timestamp': iso(m(126)), 'isApiErrorMessage': True, 'apiError': 'usage_limit_reached',
         'message': {'id': 'err1', 'model': '<synthetic>', 'content': [{'type': 'text', 'text': 'limit'}]}},
        asst('a6', m(300), cr=0, cw1=104000, out=50),
    ]
    write(main_f, rows)
    sub_rows = [
        asst('s1', m(0), cr=0, cw5=80000, out=10, tools=[('u1', 'Bash', {'command': 'python3 - <<EOF\nlong\nEOF'})]),
        result(m(10), 'u1', 'done'),
        asst('s2', m(10), cr=0, cw5=81000, out=10, tools=[('u2', 'Read', {'file_path': '/k/hooks/session_resume.py'})]),
        result(m(10), 'u2', 'x' * 2000),
        asst('s3', m(11), cr=81000, cw5=1000, out=10),
    ]
    write(sub_f, sub_rows)
    files = [main_f, sub_f]
    since = T0 - dt.timedelta(hours=1)
    S, T, dup, _ = P.analyze(files, since)
    # 主会话 6 个去重后的请求 + 子 agent 3 个；synthetic 不计
    check('A01 message.id 去重、synthetic 不算', S['n'] == 9, S['n'])
    exp_a1 = 100000 * 0.1 + 100 * 5
    check('A02 成本公式（cache_read×0.1 + output×5）', abs(T['model_effort']['main·opus·medium|cost'] -
          (exp_a1 + (100000 * .1 + 2000 * 2 + 250) + (102000 * .1 + 250) * 2 + (104000 * 2 + 250) * 2)) < 1e-6,
          T['model_effort']['main·opus·medium|cost'])
    check('A03 测试运行 2 次、失败 2 次、连续失败再跑 1 次', (S['test_runs'], S['test_fail'], S['fail_fix_cycles']) == (2, 2, 1),
          (S['test_runs'], S['test_fail'], S['fail_fix_cycles']))
    check('A04 Write 覆盖写同一文件计 1 次', S['rewrite_n'] == 1, S['rewrite_n'])
    check('A05 纠正关键词计 1 次', S['correct_msgs'] == 1, S['correct_msgs'])
    check('A06 主会话 1–4h 间隔的整段重写被识别', T['rewrite_n'].get('main|1–4h') == 2, dict(T['rewrite_n']))
    check('A07 子 agent 5–60m 间隔、5m TTL 的重写被识别', T['rewrite_ttl'].get('sub|5m|5–60m', 0) > 0, dict(T['rewrite_ttl']))

    rbf = P.requests_by_file(files, since)
    ev = P.cold_rewrites(rbf)
    whys = sorted(e['why'] for e in ev)
    check('B01 冷重写事件 3 个（主会话闲置、撞限额恢复、子 agent 等长任务）', len(ev) == 3, whys)
    check('B02 撞限额后恢复被单独标出', '撞限额后恢复' in whys, whys)
    check('B03 子 agent 等待的是内联 python 长任务', any(w.startswith('等待：Bash:内联python') for w in whys), whys)
    lim = [e for e in ev if e['why'] == '撞限额后恢复'][0]
    check('B04 多付 = 重写量 ×（2.0−0.1）', abs(lim['extra'] - 104000 * 1.9) < 1e-6, lim['extra'])
    be = P.clear_breakeven(rbf, T=100000, restart=160000, new_ctx=50000)
    # 第一次 ≥10 万是 a1（10 万），之后还有 5 次请求：5×(100000−50000)×0.1 − 160000 = −135000
    check('B05 回本估算算术', be == [(5, 5 * 50000 * 0.1 - 160000)], be)

# ---- K_testall.parse ----
cases = [('15/15 通过', (15, 15)), ('合计 33：通过 33，失败 0，跳过 0', (33, 33)), ('Ran 5 tests in 0.1s\n\nOK', (5, 5)),
         ('Ran 5 tests in 0.1s\n\nFAILED (failures=2)', (3, 5)), ('=== 1 failed, 4 passed in 0.2s ===', (4, 5)),
         ('总计：✅ 27  ⚠ 0', None), ("PASS  T05 parse '=== 1 failed, 4 passed i'\nPASS  T06\n\n26/26 通过", (26, 26)),('前面 3/4 通过\n最后 4/4 通过', (4, 4)), ('格子通过率：21/21（100%）', (21, 21))]
for i, (txt, exp) in enumerate(cases):
    got = TA.parse(txt)
    check(f'T{i+1:02d} parse {txt[:24]!r}', got == exp, got)
disc = [d[0] for d in TA.discover()]
check('T09 自动发现包含 scorecard 与本测试、不含 heldout/live_', 'tools/scorecard.py' in disc and 'tools/round5/K_test_doctor.py' in disc
      and not any('heldout' in d or 'live_' in d for d in disc), disc)

if __name__ == '__main__':
    for n, ok, info in RES:
        print(f'{"PASS" if ok else "FAIL"}  {n}' + ('' if ok else f'   → {info}'))
    k = sum(ok for _, ok, _ in RES)
    print(f'\n{k}/{len(RES)} 通过')
    sys.exit(0 if k == len(RES) else 1)
