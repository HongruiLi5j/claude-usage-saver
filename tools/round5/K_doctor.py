#!/usr/bin/env python3
"""claude-usage-saver doctor：一条命令检查套件"装了没有、在不在工作、拖不拖慢"。

只读：不改 ~/.claude/settings.json、不写套件的 state/。需要跑 hook 时，先把套件复制到临时目录，
用临时 HOME + USAGE_SAVER_STATE 在沙箱里跑（hook 写的文件都落在沙箱里，结束后整个沙箱移走/留在 TMPDIR）。

检查项（每项 ✅ 正常 / ⚠ 提醒 / ❌ 故障，并给修复建议）：
  env       python3 版本、python3 是否在 PATH
  settings  settings.json 能否解析；每个部件是否注册、路径是否存在、是否指向本套件、是否重复注册、matcher 对不对
  syntax    hooks/*.py 能否编译（用 compile()，不写 __pycache__）
  smoke     在沙箱里用合成输入跑每个 hook：退出码、有无 traceback、输出格式、延迟 p50/max
  evidence  最近 N 小时的 transcript 里 hook 真正注入/报错的次数（只数次数，不读正文）
  state     rate.json / mode.json 新不新鲜（状态栏和守卫是否真的在跑）、config.json 是否合法、state 占用多大
用法：
  python3 K_doctor.py                # 全部检查
  python3 K_doctor.py --quick        # 不跑延迟循环（每个 hook 只跑 1 次）
  python3 K_doctor.py --json out.json
  测试用参数：--kit DIR --settings FILE --projects DIR --home DIR
退出码：有 ❌ 返回 1，否则 0。
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, time, glob

OK, WARN, BAD = '✅', '⚠', '❌'
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KIT = os.path.dirname(os.path.dirname(HERE))

# 部件：(名字, 事件, 脚本相对路径, 期望 matcher 里包含的词, 是否在关键路径上)
COMPONENTS = [
    ('context_guard', 'UserPromptSubmit', 'hooks/context_guard.py', None, True),
    ('session_resume(start)', 'SessionStart', 'hooks/session_resume.py', 'clear', False),
    ('session_resume(end)', 'SessionEnd', 'hooks/session_resume.py', None, False),
    ('test_filter', 'PreToolUse', 'hooks/test_filter.py', 'Bash', True),
    ('limit_logger', 'StopFailure', 'hooks/limit_logger.py', None, False),
    ('statusline', 'statusLine', 'hooks/statusline.sh', None, True),
]
LAT_WARN_MS = {True: 300, False: 1500}     # 关键路径 hook 超过 300ms 提醒；其它 1.5s
TIMEOUT_S = 10


class Report:
    def __init__(self):
        self.rows = []

    def add(self, group, name, level, msg, fix=''):
        self.rows.append(dict(group=group, name=name, level=level, msg=msg, fix=fix))

    def worst(self):
        lv = [r['level'] for r in self.rows]
        return BAD if BAD in lv else WARN if WARN in lv else OK

    def render(self):
        out = []
        cur = None
        for r in self.rows:
            if r['group'] != cur:
                cur = r['group']; out.append(f'\n[{cur}]')
            line = f"  {r['level']} {r['name']}: {r['msg']}"
            if r['fix'] and r['level'] != OK:
                line += f"\n       → {r['fix']}"
            out.append(line)
        n = {k: sum(1 for r in self.rows if r['level'] == k) for k in (OK, WARN, BAD)}
        out.append(f"\n总计：{OK} {n[OK]}  {WARN} {n[WARN]}  {BAD} {n[BAD]}")
        return '\n'.join(out)


# ---------------- settings ----------------
def load_json(path):
    try:
        with open(path) as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, '文件不存在'
    except Exception as e:
        return None, f'无法解析（{type(e).__name__}）'


def registered(settings):
    """返回 [(事件, matcher, 命令)]；statusLine 也算一个"事件"。"""
    out = []
    hooks = (settings or {}).get('hooks') or {}
    if isinstance(hooks, dict):
        for ev, groups in hooks.items():
            for g in groups if isinstance(groups, list) else []:
                if not isinstance(g, dict):
                    continue
                for h in g.get('hooks') or []:
                    if isinstance(h, dict) and h.get('type', 'command') == 'command':
                        out.append((ev, g.get('matcher'), str(h.get('command') or '')))
    sl = (settings or {}).get('statusLine')
    if isinstance(sl, dict) and sl.get('command'):
        out.append(('statusLine', None, str(sl['command'])))
    return out


def script_in(cmd, rel):
    """命令里引用的脚本路径（以 rel 结尾的那个词），没有返回 None。"""
    base = os.path.basename(rel)
    for w in re.findall(r'''"([^"]+)"|'([^']+)'|(\S+)''', cmd):
        p = next(x for x in w if x)
        if os.path.basename(p) == base and p.replace('\\', '/').endswith(rel.split('/')[-2] + '/' + base):
            return os.path.expanduser(p)
    return None


def check_settings(rep, settings_paths, kit):
    regs = []
    for sp in settings_paths:
        s, err = load_json(sp)
        if err and err != '文件不存在':
            rep.add('settings', os.path.basename(sp), BAD, err, f'用 python3 -m json.tool {sp} 找出语法错误；撤回用 backups/ 里最近的 settings 备份')
            continue
        if s is None:
            continue
        rep.add('settings', os.path.basename(sp), OK, '可以解析')
        regs += [(ev, m, c, sp) for ev, m, c in registered(s)]
    found = {}
    for name, ev, rel, want_m, critical in COMPONENTS:
        hits = [(e, m, c, sp) for e, m, c, sp in regs if e == ev and script_in(c, rel)]
        if not hits:
            lvl = WARN
            rep.add('settings', name, lvl, f'没有在 {ev} 注册（这个部件不会运行）',
                    '如果是有意不装可以忽略；要装见 README「Install」或对应 tools/install_*.py')
            continue
        paths = {os.path.realpath(script_in(c, rel)) for _, _, c, _ in hits}
        if len(hits) > 1:
            rep.add('settings', name, WARN, f'在 {ev} 注册了 {len(hits)} 次（每次事件会跑多遍，注入可能重复）', '删掉重复的那条注册（先备份 settings）')
        for p in paths:
            if not os.path.exists(p):
                rep.add('settings', name, BAD, f'注册的脚本不存在：{p}', '套件目录被移动过？把 settings 里的路径改回实际位置')
            elif not p.startswith(os.path.realpath(kit) + os.sep):
                rep.add('settings', name, WARN, f'注册的是另一份副本：{p}（不是 {kit}）', '确认要用哪一份；改了这里的代码不会生效')
            elif rel.endswith('.sh') and not os.access(p, os.X_OK):
                rep.add('settings', name, BAD, '脚本没有可执行权限', f'chmod +x {p}')
            else:
                ms = [m for _, m, _, _ in hits]
                if want_m and not any(m and want_m in str(m) for m in ms):
                    rep.add('settings', name, WARN, f'matcher={ms} 不含 {want_m}', f'{ev} 的 matcher 应包含 {want_m}')
                else:
                    rep.add('settings', name, OK, f'已注册（{ev}{" matcher=" + str(ms[0]) if ms[0] else ""}）')
        found[name] = hits[0][2]
    return found


# ---------------- syntax ----------------
def check_syntax(rep, kit):
    for p in sorted(glob.glob(os.path.join(kit, 'hooks', '*.py'))):
        try:
            with open(p, encoding='utf-8') as f:
                compile(f.read(), p, 'exec')
            rep.add('syntax', os.path.basename(p), OK, '可编译')
        except SyntaxError as e:
            rep.add('syntax', os.path.basename(p), BAD, f'语法错误 第 {e.lineno} 行', f'python3 -m py_compile {p}；或从 backups/ 撤回')
        except Exception as e:
            rep.add('syntax', os.path.basename(p), BAD, f'读不了（{type(e).__name__}）')


# ---------------- smoke + latency ----------------
def make_sandbox(kit):
    sb = tempfile.mkdtemp(prefix='usage_saver_doctor_')
    skit = os.path.join(sb, 'kit')
    os.makedirs(skit)
    for d in ('hooks', 'profiles'):
        if os.path.isdir(os.path.join(kit, d)):
            shutil.copytree(os.path.join(kit, d), os.path.join(skit, d), ignore=shutil.ignore_patterns('__pycache__'))
    os.makedirs(os.path.join(skit, 'tools'))
    for f in ('tools/project_profile.py', 'config.json'):
        if os.path.isfile(os.path.join(kit, f)):
            shutil.copy2(os.path.join(kit, f), os.path.join(skit, f))
    os.makedirs(os.path.join(skit, 'state'))
    home = os.path.join(sb, 'home')
    proj = os.path.join(home, 'Documents', 'Projects', 'demo')
    os.makedirs(proj)
    tdir = os.path.join(home, '.claude', 'projects', '-demo')
    os.makedirs(tdir)
    # 合成 transcript：两次请求，第二次上下文 17 万（让守卫走"超线"分支）
    tp = os.path.join(tdir, 'sess-a.jsonl')
    now = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
    with open(tp, 'w') as f:
        f.write(json.dumps({'type': 'user', 'sessionId': 'sess-a', 'cwd': proj, 'timestamp': now,
                            'message': {'role': 'user', 'content': 'doctor 合成消息'}}) + '\n')
        for i, ctx in enumerate((60000, 170000)):
            f.write(json.dumps({'type': 'assistant', 'sessionId': 'sess-a', 'cwd': proj, 'timestamp': now,
                                'message': {'id': f'msg_{i}', 'model': 'claude-opus-x', 'role': 'assistant',
                                            'content': [{'type': 'text', 'text': 'ok'}],
                                            'usage': {'input_tokens': 10, 'cache_read_input_tokens': ctx - 10,
                                                      'cache_creation_input_tokens': 0, 'output_tokens': 50}}}) + '\n')
    return sb, skit, home, proj, tp


def payloads(proj, tp):
    base = dict(session_id='sess-a', transcript_path=tp, cwd=proj)
    return {
        'context_guard': [dict(base, hook_event_name='UserPromptSubmit', prompt='继续')],
        'test_filter': [dict(base, hook_event_name='PreToolUse', tool_name='Bash', tool_input={'command': 'pytest -q'}),
                        dict(base, hook_event_name='PreToolUse', tool_name='Bash', tool_input={'command': 'ls -la'})],
        'session_resume(end)': [dict(base, hook_event_name='SessionEnd', reason='clear')],
        'session_resume(start)': [dict(base, session_id='sess-b', transcript_path=tp.replace('sess-a', 'sess-b'),
                                       hook_event_name='SessionStart', source='clear')],
        'limit_logger': [dict(base, hook_event_name='StopFailure', error='rate_limit', error_details='doctor synthetic')],
        'statusline': [dict(session_id='sess-a', transcript_path=tp, cwd=proj, model={'display_name': 'Opus'},
                            workspace={'current_dir': proj}, context_window={'used_percentage': 17, 'context_window_size': 1000000},
                            rate_limits={'five_hour': {'used_percentage': 12, 'resets_at': int(time.time()) + 3600},
                                         'seven_day': {'used_percentage': 40, 'resets_at': int(time.time()) + 86400 * 3}})],
    }


def run_once(cmd, stdin_obj, env, cwd, timeout):
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, shell=True, input=json.dumps(stdin_obj), capture_output=True, text=True,
                           env=env, cwd=cwd, timeout=timeout)
        return (time.perf_counter() - t0) * 1000, p.returncode, p.stdout, p.stderr, False
    except subprocess.TimeoutExpired:
        return timeout * 1000, None, '', '', True


def check_smoke(rep, kit, commands, runs, timeout=TIMEOUT_S):
    sb, skit, home, proj, tp = make_sandbox(kit)
    env = dict(os.environ, HOME=home, USAGE_SAVER_STATE=os.path.join(skit, 'state'), TMPDIR=sb, READ_GUARD_STATE=os.path.join(skit, 'state', 'rg'))
    env.pop('CLAUDE_PROJECT_DIR', None)
    pl = payloads(proj, tp)
    lat = {}
    for name, ev, rel, _, critical in COMPONENTS:
        src = os.path.join(skit, rel)
        if not os.path.exists(src):
            rep.add('smoke', name, WARN, f'套件里没有 {rel}，跳过')
            continue
        reg = commands.get(name)
        # 用注册的命令（解释器、参数照旧），只把脚本路径换成沙箱副本；没注册就用默认写法
        if reg and script_in(reg, rel):
            cmd = reg.replace(script_in(reg, rel), src)
            for orig in re.findall(r'\S*' + re.escape(rel), reg):
                cmd = cmd.replace(orig, src)
        else:
            cmd = (f'python3 "{src}"' if rel.endswith('.py') else f'sh "{src}"')
        times, problems = [], []
        for i in range(max(1, runs)):
            for obj in pl.get(name, [{}]):
                ms, rc, out, err, to = run_once(cmd, obj, env, proj, timeout)
                times.append(ms)
                if to:
                    problems.append(f'超时（>{timeout}s）'); break
                if rc != 0:
                    problems.append(f'退出码 {rc}')
                if 'Traceback' in err:
                    problems.append('stderr 有 traceback')
                if name == 'test_filter' and out.strip():
                    try:
                        json.loads(out)
                    except Exception:
                        problems.append('stdout 不是 JSON')
                if name == 'statusline' and not out.strip():
                    problems.append('没有输出')
            if problems:
                break
        times.sort()
        p50 = times[len(times) // 2]
        lat[name] = dict(p50_ms=round(p50, 1), max_ms=round(times[-1], 1), n=len(times))
        if problems:
            rep.add('smoke', name, BAD, '；'.join(sorted(set(problems))), f'在沙箱里重现：HOME={home} 运行 {cmd}')
        elif p50 > LAT_WARN_MS[critical]:
            rep.add('smoke', name, WARN, f'能跑，但偏慢：p50 {p50:.0f}ms、max {times[-1]:.0f}ms（{len(times)} 次）',
                    '它在每条消息/每次工具调用的关键路径上，慢会拖长每一步' if critical else '')
        else:
            rep.add('smoke', name, OK, f'正常，p50 {p50:.0f}ms、max {times[-1]:.0f}ms（{len(times)} 次）')
    return sb, lat


# ---------------- evidence from transcripts ----------------
def check_evidence(rep, projects, hours):
    since = time.time() - hours * 3600
    files = [f for f in glob.glob(os.path.join(projects, '**', '*.jsonl'), recursive=True)
             if os.path.getmtime(f) >= since and '-private-tmp-' not in f and not any(x in f for x in ('.secret', 'sealed', 'test2'))]
    cnt = dict(guard=0, resume=0, hook_err=0, stop_err=0, sessions=0, bash=0, test_wrapped=0)
    errs = []
    for f in files:
        if '/subagents/' not in f:
            cnt['sessions'] += 1
        try:
            fh = open(f, errors='ignore')
        except OSError:
            continue
        with fh:
            for l in fh:
                if '"hook_' not in l and 'stop_hook_summary' not in l and '"Bash"' not in l:
                    continue
                try:
                    j = json.loads(l)
                except Exception:
                    continue
                t = j.get('type')
                if t == 'attachment':
                    a = j.get('attachment') or {}
                    at = a.get('type')
                    if at == 'hook_additional_context':
                        hn = str(a.get('hookName') or '')
                        if hn.startswith('UserPromptSubmit'):
                            cnt['guard'] += 1
                        elif hn.startswith('SessionStart'):
                            cnt['resume'] += 1
                    elif at in ('hook_non_blocking_error', 'hook_blocking_error', 'hook_error'):
                        cnt['hook_err'] += 1
                        errs.append((str(j.get('timestamp') or ''), str(a.get('hookName') or '?'), str(a.get('command') or '')))
                elif t == 'system' and j.get('subtype') == 'stop_hook_summary' and j.get('hookErrors'):
                    cnt['stop_err'] += 1
                elif t == 'assistant':
                    for b in (j.get('message') or {}).get('content') or []:
                        if isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') == 'Bash':
                            cnt['bash'] += 1
    rep.add('evidence', '范围', OK, f'最近 {hours} 小时：{cnt["sessions"]} 个主会话文件、{len(files)} 个 transcript')
    if errs or cnt['stop_err']:
        # 报错之后脚本又改过 → 多半已修（⚠）；脚本在报错后没动过 → 故障还在（❌）
        still = []
        for tstamp, hn, cmd in errs:
            scr = next((os.path.expanduser(w) for w in cmd.split() if w.endswith(('.py', '.sh'))), None)
            try:
                t_err = time.mktime(time.strptime(tstamp[:19], '%Y-%m-%dT%H:%M:%S')) - time.timezone
            except Exception:
                t_err = 0
            if not scr or not os.path.exists(scr) or os.path.getmtime(scr) <= t_err:
                still.append(hn)
        last = max((e[0] for e in errs), default='?')[:16]
        names = sorted({e[1] for e in errs})
        msg = f'{len(errs)} 次 hook 报错（{"、".join(names)}，最近一次 {last}Z）、{cnt["stop_err"]} 次 Stop hook 报错'
        if still or cnt['stop_err']:
            rep.add('evidence', 'hook 报错', BAD, msg + '；报错之后脚本没有改过，故障可能还在',
                    '先看上面 smoke 的结果；在 Claude Code 里按 ctrl+o 看 hook 输出，或用 claude --debug 启动')
        else:
            rep.add('evidence', 'hook 报错', WARN, msg + '；报错之后脚本改过，多半已修好',
                    '如果是边改边被调用导致的（改 hook 时文件写到一半），以后先改副本、测过再整体替换')
    else:
        rep.add('evidence', 'hook 报错', OK, '没有 hook 报错记录')
    rep.add('evidence', '守卫/接续注入', OK if (cnt['guard'] or cnt['resume'] or not cnt['sessions']) else WARN,
            f'UserPromptSubmit 注入 {cnt["guard"]} 次、SessionStart 注入 {cnt["resume"]} 次'
            + ('' if cnt['guard'] or cnt['resume'] or not cnt['sessions'] else '（有会话但一次都没注入：上下文都没超线，或者 hook 没生效）'))
    return cnt


# ---------------- state ----------------
def human(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024:
            return f'{n:.0f}{u}'
        n /= 1024
    return f'{n:.1f}TB'


def dir_size(p):
    tot = 0
    for root, _, fs in os.walk(p):
        for f in fs:
            try:
                tot += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return tot


def check_state(rep, kit, recent_sessions):
    st = os.environ.get('USAGE_SAVER_STATE') or os.path.join(kit, 'state')
    cfg, err = load_json(os.path.join(kit, 'config.json'))
    if err:
        rep.add('state', 'config.json', BAD if err != '文件不存在' else WARN, err, '从 backups/ 撤回最近的 config 备份')
    else:
        soft, strong = cfg.get('handoff_threshold_tokens'), cfg.get('strong_threshold_tokens')
        if isinstance(soft, (int, float)) and isinstance(strong, (int, float)) and soft < strong:
            rep.add('state', 'config.json', OK, f'软线 {soft:,}、强线 {strong:,}')
        else:
            rep.add('state', 'config.json', WARN, f'阈值不合理：软线={soft} 强线={strong}', '软线应小于强线')
    now = time.time()
    for fn, who, max_age in (('rate.json', '状态栏', 6 * 3600), ('mode.json', 'context_guard', 24 * 3600)):
        p = os.path.join(st, fn)
        if not os.path.exists(p):
            rep.add('state', fn, WARN, f'不存在（{who}还没写过）')
            continue
        age = now - os.path.getmtime(p)
        stale = age > max_age and recent_sessions
        rep.add('state', fn, WARN if stale else OK, f'{age/3600:.1f} 小时前更新' + (f'（最近有会话，{who}可能没在跑）' if stale else ''))
    for d, lim in (('testlogs', 200 << 20), ('_to_delete', 200 << 20)):
        p = os.path.join(st, d)
        if os.path.isdir(p):
            s = dir_size(p)
            rep.add('state', d, WARN if s > lim else OK, human(s), f'{p} 偏大，可以 mv 到 _to_delete 后由所有者清理' if s > lim else '')


def check_env(rep):
    py = shutil.which('python3')
    v = sys.version_info
    rep.add('env', 'python3', OK if py and v >= (3, 8) else BAD, f'{py or "不在 PATH"}，{v.major}.{v.minor}.{v.micro}',
            'hook 命令用的是 PATH 里的 python3，需要 ≥3.8')
    rep.add('env', 'codex（可选）', OK if shutil.which('codex') else WARN,
            '已安装' if shutil.which('codex') else '没装（只影响 tools/codex_*.sh 复核）')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--kit', default=DEFAULT_KIT)
    ap.add_argument('--home', default=os.path.expanduser('~'))
    ap.add_argument('--settings', action='append', help='默认 ~/.claude/settings.json 和 settings.local.json')
    ap.add_argument('--projects')
    ap.add_argument('--hours', type=float, default=72)
    ap.add_argument('--runs', type=int, default=5)
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--timeout', type=float, default=TIMEOUT_S, help='单个 hook 的超时秒数')
    ap.add_argument('--json')
    a = ap.parse_args(argv)
    kit = os.path.realpath(a.kit)
    sps = a.settings or [os.path.join(a.home, '.claude', 'settings.json'), os.path.join(a.home, '.claude', 'settings.local.json')]
    projects = a.projects or os.path.join(a.home, '.claude', 'projects')
    rep = Report()
    t0 = time.time()
    check_env(rep)
    cmds = check_settings(rep, sps, kit)
    check_syntax(rep, kit)
    sb, lat = check_smoke(rep, kit, cmds, 1 if a.quick else a.runs, a.timeout)
    cnt = check_evidence(rep, projects, a.hours)
    check_state(rep, kit, cnt['sessions'] > 0)
    # 不删沙箱（所有者红线：不 rm）。它在系统 TMPDIR 里、只有几百 KB 的复制品，系统会定期清理。
    print(f'claude-usage-saver doctor  套件={kit}  用时 {time.time()-t0:.1f}s  沙箱={sb}')
    print(rep.render())
    if a.json:
        with open(a.json, 'w') as f:
            json.dump(dict(kit=kit, sandbox=sb, worst=rep.worst(), rows=rep.rows, latency=lat, evidence=cnt), f, ensure_ascii=False, indent=1)
    return 1 if rep.worst() == BAD else 0


if __name__ == '__main__':
    sys.exit(main())
