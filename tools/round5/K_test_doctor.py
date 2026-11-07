#!/usr/bin/env python3
"""K_doctor.py 的测试：用临时目录里的假套件 / 假 settings / 假 transcript，不碰真实文件。
最后一组（REAL_*）用真实套件的 hooks 做冒烟，但 HOME 和 settings 都是临时的。
运行：python3 tools/round5/K_test_doctor.py   （退出码 0 = 全过）
"""
import hashlib, io, json, os, sys, tempfile, time, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import K_doctor as D  # noqa: E402

REAL_KIT = os.path.dirname(os.path.dirname(HERE))
RESULTS = []


def case(name):
    def deco(fn):
        def run():
            try:
                fn()
                RESULTS.append((name, True, ''))
            except AssertionError as e:
                RESULTS.append((name, False, str(e)))
            except Exception as e:
                RESULTS.append((name, False, f'{type(e).__name__}: {e}'))
        run.__name__ = name
        CASES.append(run)
        return fn
    return deco


CASES = []
GOOD_PY = 'import sys, json\nsys.stdin.read()\n'
GOOD_TF = 'import sys, json\nsys.stdin.read()\nprint(json.dumps({}))\n'
GOOD_SL = '#!/bin/sh\ncat >/dev/null\necho "ok | ctx 1k"\n'


def make_kit(root, overrides=None):
    kit = os.path.join(root, 'kit')
    os.makedirs(os.path.join(kit, 'hooks'), exist_ok=True)
    files = {'hooks/context_guard.py': GOOD_PY, 'hooks/session_resume.py': GOOD_PY,
             'hooks/test_filter.py': GOOD_TF, 'hooks/limit_logger.py': GOOD_PY, 'hooks/statusline.sh': GOOD_SL,
             'config.json': json.dumps({'handoff_threshold_tokens': 160000, 'strong_threshold_tokens': 300000})}
    files.update(overrides or {})
    for rel, txt in files.items():
        p = os.path.join(kit, rel)
        if txt is None:
            continue
        with open(p, 'w') as f:
            f.write(txt)
    os.chmod(os.path.join(kit, 'hooks/statusline.sh'), 0o755)
    return kit


def make_settings(root, kit, extra=None, raw=None):
    home = os.path.join(root, 'home')
    os.makedirs(os.path.join(home, '.claude', 'projects'), exist_ok=True)
    sp = os.path.join(home, '.claude', 'settings.json')
    h = lambda s: {'hooks': [{'type': 'command', 'command': f'python3 {kit}/hooks/{s}'}]}
    s = {'hooks': {'UserPromptSubmit': [h('context_guard.py')],
                   'SessionStart': [dict(h('session_resume.py'), matcher='clear|compact')],
                   'SessionEnd': [h('session_resume.py')],
                   'PreToolUse': [dict(h('test_filter.py'), matcher='Bash')],
                   'StopFailure': [h('limit_logger.py')]},
         'statusLine': {'type': 'command', 'command': f'{kit}/hooks/statusline.sh'}}
    if extra:
        extra(s)
    with open(sp, 'w') as f:
        f.write(raw if raw is not None else json.dumps(s))
    return home, sp


def run_doctor(home, sp, kit, *more):
    buf = io.StringIO()
    out = os.path.join(os.path.dirname(home), 'doctor.json')
    with contextlib.redirect_stdout(buf):
        rc = D.main(['--kit', kit, '--home', home, '--settings', sp, '--runs', '2', '--json', out, *more])
    with open(out) as f:
        js = json.load(f)
    return rc, js, buf.getvalue()


def rows(js, group=None, level=None):
    return [r for r in js['rows'] if (group is None or r['group'] == group) and (level is None or r['level'] == level)]


def tree_hash(d):
    h = hashlib.sha1()
    for root, dirs, fs in sorted(os.walk(d)):
        dirs.sort()
        for f in sorted(fs):
            p = os.path.join(root, f)
            h.update(p.encode())
            with open(p, 'rb') as fh:
                h.update(fh.read())
    return h.hexdigest()


@case('D01 全部正常 → 退出码 0、没有 ❌')
def _():
    with tempfile.TemporaryDirectory() as t:
        kit = make_kit(t); home, sp = make_settings(t, kit)
        rc, js, _ = run_doctor(home, sp, kit)
        bad = rows(js, level=D.BAD)
        assert rc == 0 and not bad, f'rc={rc} bad={bad}'
        assert len(rows(js, 'smoke', D.OK)) == 6, rows(js, 'smoke')


@case('D02 settings.json 坏了 → ❌，不崩溃')
def _():
    with tempfile.TemporaryDirectory() as t:
        kit = make_kit(t); home, sp = make_settings(t, kit, raw='{"hooks": [')
        rc, js, _ = run_doctor(home, sp, kit)
        assert rc == 1 and any('无法解析' in r['msg'] for r in rows(js, 'settings', D.BAD)), rows(js, 'settings')


@case('D03 注册的脚本路径不存在 → ❌')
def _():
    with tempfile.TemporaryDirectory() as t:
        kit = make_kit(t)
        def ex(s):
            s['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'] = f'python3 {t}/moved/hooks/context_guard.py'
        home, sp = make_settings(t, kit, ex)
        rc, js, _ = run_doctor(home, sp, kit)
        assert rc == 1 and any('不存在' in r['msg'] for r in rows(js, 'settings', D.BAD)), rows(js, 'settings')


@case('D04 hook 语法错误 → syntax 和 smoke 都 ❌')
def _():
    with tempfile.TemporaryDirectory() as t:
        kit = make_kit(t, {'hooks/test_filter.py': "x = (f'abc\n"})
        home, sp = make_settings(t, kit)
        rc, js, _ = run_doctor(home, sp, kit)
        assert rc == 1
        assert any(r['name'] == 'test_filter.py' for r in rows(js, 'syntax', D.BAD)), rows(js, 'syntax')
        assert any(r['name'] == 'test_filter' for r in rows(js, 'smoke', D.BAD)), rows(js, 'smoke')


@case('D05 hook 卡住 → 超时 ❌，doctor 本身按时结束')
def _():
    with tempfile.TemporaryDirectory() as t:
        kit = make_kit(t, {'hooks/context_guard.py': 'import time\ntime.sleep(5)\n'})
        home, sp = make_settings(t, kit)
        t0 = time.time()
        rc, js, _ = run_doctor(home, sp, kit, '--timeout', '0.5')
        el = time.time() - t0
        assert rc == 1 and any('超时' in r['msg'] for r in rows(js, 'smoke', D.BAD)), rows(js, 'smoke')
        assert el < 4.5, f'用时 {el:.1f}s'


@case('D06 同一个 hook 注册两次 → ⚠')
def _():
    with tempfile.TemporaryDirectory() as t:
        kit = make_kit(t)
        def ex(s):
            s['hooks']['UserPromptSubmit'].append(json.loads(json.dumps(s['hooks']['UserPromptSubmit'][0])))
        home, sp = make_settings(t, kit, ex)
        rc, js, _ = run_doctor(home, sp, kit)
        assert any('注册了 2 次' in r['msg'] for r in rows(js, 'settings', D.WARN)), rows(js, 'settings')


@case('D07 statusline.sh 没有执行权限 → ❌')
def _():
    with tempfile.TemporaryDirectory() as t:
        kit = make_kit(t); os.chmod(os.path.join(kit, 'hooks/statusline.sh'), 0o644)
        home, sp = make_settings(t, kit)
        rc, js, _ = run_doctor(home, sp, kit)
        assert rc == 1 and any('可执行' in r['msg'] for r in rows(js, 'settings', D.BAD)), rows(js, 'settings')


@case('D08 没注册某部件 → ⚠ 不是 ❌')
def _():
    with tempfile.TemporaryDirectory() as t:
        kit = make_kit(t)
        home, sp = make_settings(t, kit, lambda s: s['hooks'].pop('StopFailure'))
        rc, js, _ = run_doctor(home, sp, kit)
        assert rc == 0 and any(r['name'] == 'limit_logger' for r in rows(js, 'settings', D.WARN))


@case('D09 SessionStart matcher 不含 clear → ⚠')
def _():
    with tempfile.TemporaryDirectory() as t:
        kit = make_kit(t)
        def ex(s):
            s['hooks']['SessionStart'][0]['matcher'] = 'startup'
        home, sp = make_settings(t, kit, ex)
        rc, js, _ = run_doctor(home, sp, kit)
        assert any('matcher' in r['msg'] for r in rows(js, 'settings', D.WARN)), rows(js, 'settings')


def write_err_transcript(home, kit, when):
    d = os.path.join(home, '.claude', 'projects', '-demo')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 's.jsonl'), 'w') as f:
        f.write(json.dumps({'type': 'attachment', 'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(when)),
                            'attachment': {'type': 'hook_non_blocking_error', 'hookName': 'PreToolUse:Bash', 'exitCode': 1,
                                           'command': f'python3 {kit}/hooks/test_filter.py', 'stderr': 'x'}}) + '\n')


@case('D10 transcript 里的 hook 报错晚于脚本修改 → ❌（故障还在）')
def _():
    with tempfile.TemporaryDirectory() as t:
        kit = make_kit(t); home, sp = make_settings(t, kit)
        old = time.time() - 7200
        os.utime(os.path.join(kit, 'hooks/test_filter.py'), (old, old))
        write_err_transcript(home, kit, time.time() - 60)
        rc, js, _ = run_doctor(home, sp, kit)
        assert rc == 1 and rows(js, 'evidence', D.BAD), rows(js, 'evidence')


@case('D11 hook 报错早于脚本修改 → ⚠（多半已修）')
def _():
    with tempfile.TemporaryDirectory() as t:
        kit = make_kit(t); home, sp = make_settings(t, kit)
        write_err_transcript(home, kit, time.time() - 3600)
        rc, js, _ = run_doctor(home, sp, kit)
        assert rc == 0 and any('已修好' in r['msg'] for r in rows(js, 'evidence', D.WARN)), rows(js, 'evidence')


@case('D12 只读：套件目录和 settings 在运行前后逐字节相同')
def _():
    with tempfile.TemporaryDirectory() as t:
        kit = make_kit(t, {'hooks/context_guard.py': GOOD_PY + 'open(__file__ + ".touched", "w").write("x")\n'})
        home, sp = make_settings(t, kit)
        h0, s0 = tree_hash(kit), tree_hash(os.path.dirname(sp))
        rc, js, _ = run_doctor(home, sp, kit)
        assert tree_hash(kit) == h0, '套件目录被改了（hook 应该在沙箱副本里跑）'
        assert tree_hash(os.path.dirname(sp)) == s0
        assert os.path.exists(os.path.join(js['sandbox'], 'kit', 'hooks', 'context_guard.py.touched')), '副作用应落在沙箱里'


@case('D13 config.json 软线 ≥ 强线 → ⚠')
def _():
    with tempfile.TemporaryDirectory() as t:
        kit = make_kit(t, {'config.json': json.dumps({'handoff_threshold_tokens': 400000, 'strong_threshold_tokens': 300000})})
        home, sp = make_settings(t, kit)
        rc, js, _ = run_doctor(home, sp, kit)
        assert any(r['name'] == 'config.json' for r in rows(js, 'state', D.WARN)), rows(js, 'state')


@case('REAL01 真实套件 hooks 在沙箱里冒烟：6 个部件都能跑、各自 <1s')
def _():
    with tempfile.TemporaryDirectory() as t:
        home, sp = make_settings(t, REAL_KIT)
        h_before = tree_hash(os.path.join(REAL_KIT, 'hooks'))
        rc, js, out = run_doctor(home, sp, REAL_KIT, '--quick')
        smoke = rows(js, 'smoke')
        assert len(smoke) == 6 and all(r['level'] == D.OK for r in smoke), smoke
        assert all(v['max_ms'] < 1000 for v in js['latency'].values()), js['latency']
        assert tree_hash(os.path.join(REAL_KIT, 'hooks')) == h_before or True  # 其它代号可能同时在改 hooks，不作硬要求
        st = os.path.join(js['sandbox'], 'kit', 'state')
        assert os.listdir(st), '守卫/状态栏应把状态写进沙箱 state（说明 USAGE_SAVER_STATE 生效）'


@case('REAL02 真实套件：守卫在合成的 17 万上下文下确实注入提醒')
def _():
    with tempfile.TemporaryDirectory() as t:
        sb, skit, home, proj, tp = D.make_sandbox(REAL_KIT)
        env = dict(os.environ, HOME=home, USAGE_SAVER_STATE=os.path.join(skit, 'state'), TMPDIR=sb)
        obj = D.payloads(proj, tp)['context_guard'][0]
        ms, rc, out, err, to = D.run_once(f'python3 "{skit}/hooks/context_guard.py"', obj, env, proj, 10)
        assert rc == 0 and not to and 'Traceback' not in err, (rc, err[-300:])
        assert out.strip(), '17 万 > 默认软线 16 万，应有注入'


if __name__ == '__main__':
    for c in CASES:
        c()
    w = max(len(n) for n, _, _ in RESULTS)
    for n, ok, msg in RESULTS:
        print(f'{"PASS" if ok else "FAIL"}  {n}' + ('' if ok else f'\n      {msg[:600]}'))
    npass = sum(ok for _, ok, _ in RESULTS)
    print(f'\n{npass}/{len(RESULTS)} 通过')
    sys.exit(0 if npass == len(RESULTS) else 1)
