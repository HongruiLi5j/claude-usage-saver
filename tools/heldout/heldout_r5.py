#!/usr/bin/env python3
"""R5 document-only black-box tests. Never import implementation modules."""
from __future__ import annotations
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

SUITE = Path(os.environ.get("HELDOUT_SUITE") or Path(__file__).resolve().parents[2])
CASES = []
MARK = '<!-- usage-saver-handoff -->'


def case(code, scene, expected, source, inferred=False):
    def deco(fn):
        CASES.append((code, scene, expected, source, inferred, fn))
        return fn
    return deco


def check(ok, message, actual=None):
    if not ok:
        raise AssertionError(message + (f'；实际 {str(actual)[:300]}' if actual is not None else ''))


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def tree(path):
    if not path.exists():
        return {}
    return {str(p.relative_to(path)): ('dir' if p.is_dir() else
            hashlib.sha256(p.read_bytes()).hexdigest()) for p in path.rglob('*')}


class RunnerFailure(Exception):
    pass


class Box:
    def __init__(self, root):
        self.root = Path(os.path.realpath(root))
        self.home = self.root / 'home'
        self.suite = self.home / 'Documents/Projects/claude-usage-saver'
        required = [SUITE / 'hooks' / n for n in (
            'limit_logger.py', 'context_guard.py', 'session_resume.py', 'test_filter.py', 'statusline.sh')]
        required += [SUITE / 'tools' / n for n in (
            'install_limit_logger.py', 'pro_switch_1121.py', 'install_sessionend.py')]
        required += [SUITE / 'config.json']
        for src in required:
            if not src.is_file():
                raise RunnerFailure(f'源文件不存在：{src}')
        sources = list((SUITE / 'hooks').glob('*.py')) + required
        for src in set(sources):
            target = self.suite / src.relative_to(SUITE)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        self.state = self.suite / 'state'
        self.state.mkdir()
        self.project = self.home / 'Documents/Projects/r5-project'
        self.project.mkdir(parents=True)
        (self.project / '.git').mkdir()
        self.transcripts = self.root / 'transcripts'
        self.transcripts.mkdir()
        self.settings = self.home / '.claude/settings.json'
        dump(self.settings, {'env': {'KEEP_R5': 'yes'}, 'other_r5': [1, 2]})
        self.backups = self.root / 'backups'
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(('USAGE_SAVER_', 'CLAUDE_'))}
        self.env.update(HOME=str(self.home), PYTHONDONTWRITEBYTECODE='1',
                        BASH_ENV='/dev/null', ENV='/dev/null',
                        XDG_CACHE_HOME=str(self.home / '.cache'))
        self.deadline = time.monotonic() + 29

    def timeout(self):
        remaining = self.deadline - time.monotonic()
        check(remaining > 0, '场景总超时（29 秒）')
        return min(8, remaining)

    def run(self, argv, raw='', good=True):
        p = subprocess.run(argv, input=raw, text=True, capture_output=True,
                           env=self.env, cwd=self.project, timeout=self.timeout())
        if good:
            check(p.returncode == 0, f'子进程退出 {p.returncode}', p.stderr)
        return p

    def payload(self, sid='old-r5', path=None, **kw):
        return dict(session_id=sid, cwd=str(self.project),
                    transcript_path=str(path or self.transcripts / (sid + '.jsonl')), **kw)

    def argv(self, hook):
        return ['/bin/sh' if hook.endswith('.sh') else sys.executable,
                str(self.suite / 'hooks' / hook)]

    def hook(self, name, payload=None, raw=None):
        p = self.run(self.argv(name), raw if raw is not None else json.dumps(payload))
        check(not p.stderr, 'hook 不应输出 stderr', p.stderr)
        return p.stdout

    def tool(self, name, *args, settings=None, good=True):
        return self.run([sys.executable, str(self.suite / 'tools' / name),
                         '--settings', str(settings or self.settings),
                         '--backup-dir', str(self.backups), *map(str, args)], good=good)

    def parallel(self, hook, payloads):
        processes = []
        try:
            for payload in payloads:
                p = subprocess.Popen(self.argv(hook), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    cwd=self.project, env=self.env)
                processes.append(p)
                p.stdin.write(json.dumps(payload))
                p.stdin.close()
                p.stdin = None
            results = []
            for p in processes:
                out, err = p.communicate(timeout=self.timeout())
                check(p.returncode == 0 and not err, '并发 hook 退出/错误输出异常', (p.returncode, err))
                results.append(out)
            return results
        finally:
            for p in processes:
                if p.poll() is None:
                    p.kill()
                    p.communicate()

    def transcript(self, sid='old-r5', tokens=170000, marker='OWNER_R5', edited=None):
        path = self.transcripts / (sid + '.jsonl')
        content = [{'type': 'text', 'text': 'NEXT_R5'}]
        if edited is not None:
            content.append({'type': 'tool_use', 'id': 'r5', 'name': 'Edit',
                            'input': {'file_path': str(edited), 'old_string': 'x', 'new_string': 'y'}})
        rows = [{'type': 'user', 'sessionId': sid, 'cwd': str(self.project),
                 'timestamp': dt.datetime.now(dt.timezone.utc).isoformat(),
                 'message': {'role': 'user', 'content': marker}},
                {'type': 'assistant', 'sessionId': sid, 'cwd': str(self.project),
                 'timestamp': dt.datetime.now(dt.timezone.utc).isoformat(),
                 'message': {'role': 'assistant', 'content': content, 'usage': {
                     'input_tokens': tokens, 'cache_read_input_tokens': 0,
                     'cache_creation_input_tokens': 0, 'output_tokens': 7}}}]
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        return path

    def end(self, sid='old-r5', foreign=False):
        payload = self.payload(sid, hook_event_name='SessionEnd', reason='clear')
        if foreign:
            # A distinct non-shell parent provides a distinct documented owner.
            code = ('import subprocess,sys; p=subprocess.run(sys.argv[1:],input=sys.stdin.read(),'
                    'text=True,capture_output=True);sys.stdout.write(p.stdout);'
                    'sys.stderr.write(p.stderr);sys.exit(p.returncode)')
            p = self.run([sys.executable, '-c', code, *self.argv('session_resume.py')], json.dumps(payload))
            check(not p.stdout and not p.stderr, 'SessionEnd 应安静', (p.stdout, p.stderr))
        else:
            check(not self.hook('session_resume.py', payload), 'SessionEnd 不应注入')

    def start(self, sid='new-r5', source='clear'):
        return context(self.hook('session_resume.py', self.payload(
            sid, hook_event_name='SessionStart', source=source)))

    def guard(self, tokens=170000, sid='guard-r5'):
        self.transcript(sid, tokens)
        return context(self.hook('context_guard.py', self.payload(
            sid, hook_event_name='UserPromptSubmit', prompt='继续')))

    def status(self, rates=None):
        value = self.payload(model={'display_name': 'R5'}, workspace={'current_dir': str(self.project)},
            context_window={'current_usage': {'input_tokens': 170000,
                'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0, 'output_tokens': 0}})
        if rates is not None:
            value['rate_limits'] = rates
        return value

    def rate(self, five=93, week=40):
        now = int(time.time())
        return {'five_hour': {'used_percentage': five, 'resets_at': now + 14400},
                'seven_day': {'used_percentage': week, 'resets_at': now + 302400}}


def context(out):
    if not out.strip():
        return ''
    if out.lstrip().startswith('{'):
        value = json.loads(out)
        text = value['hookSpecificOutput']['additionalContext']
        check(isinstance(text, str), 'additionalContext 应为字符串', text)
        return text
    return out


def event(s, text='EVENT_R5', kind='StopFailure'):
    return s.payload(hook_event_name=kind, error='rate_limit', error_details={'r5': text},
                     notification_type='quota_auto_resume_fired', title=text, message=text)


def events(s):
    p = s.state / 'limit_events.jsonl'
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def numeric_leaves(value):
    if isinstance(value, dict):
        return [n for child in value.values() for n in numeric_leaves(child)]
    if isinstance(value, list):
        return [n for child in value for n in numeric_leaves(child)]
    return [value] if type(value) in (int, float) else []


@case('LL01', '对象 error_details 与 Unicode', '追加有效 JSON 且保留对象中的唯一文本', 'headers.md：“字符串或对象都接受”')
def ll_object(s):
    check(not s.hook('limit_logger.py', event(s, '额度🧪R5')), 'logger stdout 非空')
    check('额度🧪R5' in json.dumps(events(s), ensure_ascii=False), '对象详情丢失', events(s))


@case('LL02', 'statusline→额度锚点快照', '日志同时包含两个窗口读数', 'headers.md：“附上当时 state/rate.json…百分比快照”')
def ll_snapshot(s):
    s.hook('statusline.sh', s.status(s.rate(97, 83)))
    s.hook('limit_logger.py', event(s))
    rows = events(s)
    numbers = numeric_leaves(rows)
    check(97 in numbers and 83 in numbers, '快照读数丢失', rows)


@case('LL03', '三种自动恢复通知', '每种通知各追加一条且从不输出', 'headers.md：“Notification quota_auto_resume_*”')
def ll_notifications(s):
    for kind in ('fired', 'stale', 'disabled'):
        p = event(s, 'NOTICE_' + kind, 'Notification')
        p['notification_type'] = 'quota_auto_resume_' + kind
        check(not s.hook('limit_logger.py', p), '通知 logger 有输出')
    rows = events(s)
    check(len(rows) == 3 and all('NOTICE_' + k in json.dumps(rows) for k in ('fired', 'stale', 'disabled')), '通知缺失', rows)


@case('LL04', '已有残缺尾行再追加', '新事件从独立行开始且为有效 JSON', 'headers.md：“末尾不是换行也先补一个”')
def ll_tail(s):
    (s.state / 'limit_events.jsonl').write_text('{partial')
    s.hook('limit_logger.py', event(s, 'NEW_TAIL_R5'))
    lines = (s.state / 'limit_events.jsonl').read_text().splitlines()
    check(lines[0] == '{partial' and 'NEW_TAIL_R5' in json.dumps(json.loads(lines[-1])), '尾行未分隔', lines[-2:])


@case('LL05', '超 5MiB 且 old 已存在', '既有 old 不覆盖；旧主文件与新事件均保留', 'headers.md：“_old 已存在就留在 时间戳.pid.随机 的名字下”')
def ll_rotation(s):
    old = s.state / 'limit_events_old.jsonl'
    old.write_text('PREEXISTING_ARCHIVE_R5\n')
    record = json.dumps({'padding': 'x' * 1024, 'r5': 'ROTATE_ORIGINAL'}) + '\n'
    (s.state / 'limit_events.jsonl').write_text(record * 5200)
    s.hook('limit_logger.py', event(s, 'AFTER_ROTATE_R5'))
    check(old.read_text() == 'PREEXISTING_ARCHIVE_R5\n', '已有 old 被覆盖')
    archives = [p for p in s.state.iterdir() if p.is_file() and p.name not in ('limit_events.jsonl', 'limit_events_old.jsonl', 'limit_events.lock')]
    check(any('ROTATE_ORIGINAL' in p.read_text(errors='replace') for p in archives), '未保留独立轮转归档')
    check('AFTER_ROTATE_R5' in json.dumps(events(s)), '轮转后追加缺失')


@case('LL06', '12 进程竞争轮转与追加', '所有新事件各出现一次、每行可解码', 'headers.md：“并发安全…别的进程新建的文件不可能被删”')
def ll_concurrent(s):
    line = json.dumps({'pad': 'x' * 1024}) + '\n'
    (s.state / 'limit_events.jsonl').write_text(line * 5200)
    outs = s.parallel('limit_logger.py', [event(s, f'CONCURRENT_R5_{i:02d}') for i in range(12)])
    check(not any(outs), '并发 logger 有输出', outs)
    records = []
    for p in s.state.iterdir():
        if p.is_file() and p.name != 'limit_events.lock':
            records.extend(json.loads(x) for x in p.read_text().splitlines() if x.strip())
    for i in range(12):
        check(sum(f'CONCURRENT_R5_{i:02d}' in json.dumps(r) for r in records) == 1, '并发事件丢失或重复', i)


@case('LL07', 'rate.json 是目录', '异常吞掉、无 stdout/stderr、退出 0', 'headers.md：“所有异常吞掉，始终 exit 0”')
def ll_directory(s):
    (s.state / 'rate.json').mkdir()
    check(not s.hook('limit_logger.py', event(s)), '坏状态产生输出')


@case('LL08', '合法 JSON 顶层类型错误', '安静放行', 'headers.md：“永不往 stdout/stderr 输出…所有异常吞掉”')
def ll_wrong_type(s):
    check(not s.hook('limit_logger.py', raw='[null,42,"r5"]'), '坏输入产生输出')


@case('LL09', 'USAGE_SAVER_STATE 覆盖', '只向覆盖目录追加事件', 'headers.md：“USAGE_SAVER_STATE，没有就用本套件的 state/”')
def ll_override(s):
    target = s.root / 'override'
    s.env['USAGE_SAVER_STATE'] = str(target)
    s.hook('limit_logger.py', event(s))
    check((target / 'limit_events.jsonl').is_file() and not (s.state / 'limit_events.jsonl').exists(), '状态覆盖未生效')


def settings(s):
    return json.loads(s.settings.read_text())


def own_commands(s, value):
    found = []
    target = str((s.suite / 'hooks/limit_logger.py').resolve())
    for groups in value.get('hooks', {}).values():
        for group in groups:
            for hook in group.get('hooks', []):
                for token in shlex.split(hook.get('command', '')):
                    token = token.replace('${HOME}', str(s.home)).replace('$HOME', str(s.home))
                    if token.startswith('~/'):
                        token = str(s.home / token[2:])
                    if os.path.realpath(token) == target:
                        found.append(hook)
    return found


def foreign_settings(s):
    v = settings(s)
    v['hooks'] = {'StopFailure': [{'matcher': '.*', 'hooks': [
        {'type': 'command', 'command': 'python3 /other/limit_logger.py', 'timeout': 17}]}],
        'Notification': [{'matcher': 'idle_prompt', 'hooks': [
            {'type': 'command', 'command': 'echo FOREIGN_R5'}]}]}
    dump(s.settings, v)
    return v


@case('IL01', '含外来同名脚本时安装两次', '第二次文件和备份目录逐字节不变', 'headers.md：“幂等…不再加”；“真要改时才备份”')
def il_idempotent(s):
    foreign_settings(s)
    s.tool('install_limit_logger.py')
    before = (s.settings.read_bytes(), tree(s.backups))
    s.tool('install_limit_logger.py')
    check(before == (s.settings.read_bytes(), tree(s.backups)), '第二次安装仍有写入')


@case('IL02', 'dry-run 面对尚未安装状态', '临时根所有文件及目录不变', 'headers.md：“--dry-run…不写任何文件”')
def il_dry(s):
    foreign_settings(s)
    before = tree(s.root)
    s.tool('install_limit_logger.py', '--dry-run')
    check(before == tree(s.root), 'dry-run 有落盘副作用')


@case('IL03', '外来同名脚本和 idle 通知共存', '保留原 hook 及其他键且添加本套件 hook', 'headers.md：“/other/limit_logger.py 不算”；“别的 hook…一律不动”')
def il_foreign(s):
    original = foreign_settings(s)
    s.tool('install_limit_logger.py')
    now = settings(s)
    check(now['env'] == original['env'] and now['other_r5'] == original['other_r5'], '无关键被修改', now)
    for name, groups in original['hooks'].items():
        for group in groups:
            check(group in now['hooks'][name], '外来 hook 组被修改', now)
    check(len(own_commands(s, now)) == 2, '未分别安装到两个事件', now)


@case('IL04', '$HOME 引号路径归属判断', '已有两事件的本套件命令均不重复', 'headers.md：“shlex 拆开…展开 ~ 和 $HOME 后取 realpath”')
def il_home(s):
    command = 'python3 "$HOME/Documents/Projects/claude-usage-saver/hooks/limit_logger.py"'
    v = settings(s)
    v['hooks'] = {name: [{'matcher': matcher, 'hooks': [{'type': 'command', 'command': command}]}]
                  for name, matcher in [('StopFailure', '.*'), ('Notification', 'quota_auto_resume_fired|quota_auto_resume_stale|quota_auto_resume_disabled')]}
    dump(s.settings, v)
    before = s.settings.read_bytes()
    s.tool('install_limit_logger.py')
    check(s.settings.read_bytes() == before and not tree(s.backups), '展开路径未识别、发生重复安装')


@case('IL05', '同组混合 hook 卸载', '只删除自己的命令并保留混组其他 hook', 'headers.md：“同组别的 hook 保留，组空了才删组”')
def il_uninstall(s):
    s.tool('install_limit_logger.py')
    v = settings(s)
    extra = {'type': 'command', 'command': 'echo KEEP_MIXED_R5', 'timeout': 13}
    v['hooks']['StopFailure'][0]['hooks'].append(extra)
    dump(s.settings, v)
    s.tool('install_limit_logger.py', '--uninstall')
    now = settings(s)
    check(not own_commands(s, now), '卸载后仍有自己的 hook', now)
    check(any(extra in g.get('hooks', []) for g in now['hooks']['StopFailure']), '同组外来 hook 被删', now)


@case('IL06', '安装→卸载的原始备份', '首次安装前完整 settings 的字节副本存在', 'headers.md：“真要改时才备份…同样先备份”')
def il_backup(s):
    foreign_settings(s)
    before = s.settings.read_bytes()
    s.tool('install_limit_logger.py')
    s.tool('install_limit_logger.py', '--uninstall')
    check(any(p.is_file() and p.read_bytes() == before for p in s.backups.rglob('*')), '未保留首次修改前的完整备份')


def scan_rows(s, rows):
    p = s.transcripts / 'scan.jsonl'
    p.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    return p


def scan_record(timestamp, n, output=0):
    return {'type': 'assistant', 'timestamp': timestamp, 'message': {'role': 'assistant',
        'content': 'r5', 'usage': {'input_tokens': n - 3000, 'cache_read_input_tokens': 2000,
        'cache_creation_input_tokens': 1000, 'output_tokens': output}}}


def scan(s):
    return s.tool('pro_switch_1121.py', '--scan-context', '--since', '2026-10-15',
                  '--transcripts', s.transcripts).stdout


def has_number(out, number):
    compact = out.replace(',', '').replace('_', '')
    return str(number) in compact or f'{number / 1000:.1f}k' in compact.lower()


@case('PS01', '旧 mtime 内含新记录', '按记录 timestamp 仍计入最大上下文', 'headers.md：“全量读，不按 mtime 跳过”')
def ps_mtime(s):
    p = scan_rows(s, [scan_record('2026-10-20T12:00:00Z', 321987)])
    os.utime(p, (1, 1))
    before = tree(s.root)
    out = scan(s)
    check(has_number(out, 321987), '旧 mtime 导致漏读最大值', out)
    check(before == tree(s.root), 'scan 不是只读')


@case('PS02', '同文件新旧记录混合且 output 极大', '排除 since 前记录及 output_tokens', 'contract.md：“一次请求的上下文 = input_tokens + cache_read… + cache_creation…”；headers.md：“按每条记录的 timestamp”')
def ps_record_time(s):
    scan_rows(s, [scan_record('2026-10-14T23:00:00Z', 876543),
                  scan_record('2026-10-16T00:00:00Z', 234567, 900000)])
    out = scan(s)
    check(has_number(out, 234567) and not has_number(out, 876543), '最大上下文未按记录时间/usage 计算', out)


def apply(s, *args):
    return s.tool('pro_switch_1121.py', '--apply', '--force-early', *args)


def show(s, which):
    return s.tool('pro_switch_1121.py', '--show-backup', '--which', which).stdout


def mentioned_backup(s, out):
    return [p for p in s.backups.rglob('*') if p.is_file() and str(p) in out]


@case('PS03', '两次不同 apply 后 first/last', 'first 指向原始副本、last 指向最近 apply 前副本', 'headers.md：“first=第一次 apply 之前…last=最近一次 apply 之前”')
def ps_first_last(s):
    original = settings(s)
    apply(s)
    mid = settings(s)
    apply(s, '--subagent-cap', '3')
    for which, expected in [('first', original), ('last', mid)]:
        out = show(s, which)
        paths = mentioned_backup(s, out)
        check(any(json.loads(p.read_text()) == expected for p in paths), '备份选择错误：' + which, out)


@case('PS04', '交换备份 mtime 后只读查询', 'first/last 查询输出保持不变且无写入', 'headers.md：“按 state 里记的路径选，不看文件 mtime”；“只读”')
def ps_show_mtime(s):
    apply(s)
    apply(s, '--subagent-cap', '2')
    outputs = [show(s, w) for w in ('first', 'last')]
    for i, p in enumerate(sorted(s.backups.rglob('*'))):
        if p.is_file():
            os.utime(p, (100 + i, 100 + i))
    before = tree(s.root)
    check(outputs == [show(s, w) for w in ('first', 'last')], 'mtime 改变了备份选择')
    check(tree(s.root) == before, 'show-backup 写入文件')


@case('PS05', '幂等 apply 不推进 last 记录', '第二次相同 apply 不写、不备份、last 不变', 'headers.md：“已经是目标值就不写文件、不备份”')
def ps_idempotent(s):
    apply(s, '--subagent-cap', '4')
    out = show(s, 'last')
    before = tree(s.root)
    apply(s, '--subagent-cap', '4')
    check(tree(s.root) == before and show(s, 'last') == out, '幂等 apply 推进了记录')


@case('PS06', 'apply 后无关设置被用户改动再 undo', '还原自己改过的键并保留后来的无关键', 'headers.md：“--undo：只撤回本脚本改过的键…其他键不动”')
def ps_undo(s):
    original = settings(s)
    apply(s, '--subagent-cap', '3')
    v = settings(s)
    v['env']['NEW_R5'] = 'later'
    v['other_r5'] = ['later']
    dump(s.settings, v)
    s.tool('pro_switch_1121.py', '--undo')
    wanted = copy.deepcopy(original)
    wanted['env']['NEW_R5'] = 'later'
    wanted['other_r5'] = ['later']
    check(settings(s) == wanted, 'undo 覆盖无关修改或未还原', settings(s))


@case('PS07', 'env 数组拒绝 apply', '非零退出且根目录内容不变', 'headers.md：“拒绝（什么都不改）…env 存在但不是对象”')
def ps_bad_env(s):
    dump(s.settings, {'env': ['R5']})
    before = tree(s.root)
    p = s.tool('pro_switch_1121.py', '--apply', '--force-early', good=False)
    check(p.returncode != 0 and tree(s.root) == before, '未拒绝或拒绝时有修改', (p.returncode, p.stdout, p.stderr))


@case('PS08', '备份记录属于另一个 settings', '拒绝且两份 settings/备份均不变', 'headers.md：“backup-dir 里的记录属于另一个 settings 文件”')
def ps_foreign_record(s):
    apply(s)
    other = s.home / '.claude/other-settings.json'
    dump(other, {'env': {}, 'other': 'R5'})
    before = tree(s.root)
    p = s.tool('pro_switch_1121.py', '--apply', '--force-early', settings=other, good=False)
    check(p.returncode != 0 and tree(s.root) == before, '异属记录未拒绝或产生修改', p.stderr)


@case('PS09', '已生成记录移除 keys 形成 E1', 'undo 拒绝且什么都不改', 'headers.md：“记录是 E1 旧格式（没有 keys）”')
def ps_legacy(s):
    apply(s)
    record = s.backups / 'pro_switch_1121.state.json'
    v = json.loads(record.read_text())
    check('keys' in v, '工具生成的记录缺少公开 keys', v)
    del v['keys']
    dump(record, v)
    before = tree(s.root)
    p = s.tool('pro_switch_1121.py', '--undo', good=False)
    check(p.returncode != 0 and tree(s.root) == before, 'E1 记录未拒绝或有写入', p.stderr)


def handoff(s, text):
    p = s.project / 'HANDOFF.md'
    p.write_text(text, encoding='utf-8')
    return p


@case('SR01', 'MARK 在第 3 个非空行且被代码围栏包住', 'compact 提取受信下一步', 'headers.md：“前 3 个非空行…标题在前、被 ``` 包住都算”')
def sr_mark_position(s):
    handoff(s, '# 交接\n\n```\n\n' + MARK + '\n```\n## 下一步\nTHIRD_MARK_R5\n')
    check('THIRD_MARK_R5' in s.start('old-r5', 'compact'), '第三非空行标记未识别')


@case('SR02', 'MARK 位于第 4 个非空行', '未编辑来源只给路径、不注入下一步正文', 'headers.md：“前 3 个非空行里有一行恰为 MARK…才注入正文”')
def sr_late_mark(s):
    handoff(s, '# 交接\n说明\n更多说明\n' + MARK + '\n## 下一步\nLATE_MARK_R5\n')
    out = s.start('old-r5', 'compact')
    check('HANDOFF.md' in out and 'LATE_MARK_R5' not in out, '位置超限标记获得信任', out)


@case('SR03', '加粗红线/下一步同行或独立列表节', '两节提取且不串入后续已完成节', 'headers.md：“在行首的加粗「红线/下一步」处结束”；improvements.md 第20项：“修加粗列表分段”')
def sr_bold(s):
    handoff(s, MARK + '\n- **红线/未决**\nRED_BOLD_R5\n- **下一步**\nNEXT_BOLD_R5\n## 已完成\nDONE_BOLD_R5\n')
    out = s.start('old-r5', 'compact')
    check('RED_BOLD_R5' in out and 'NEXT_BOLD_R5' in out and 'DONE_BOLD_R5' not in out, '加粗分节提取错误', out)


@case('SR04', '红线/下一步重复出现', '每类仅取第一节', 'improvements.md 第20项：“红线/下一步各取第一节”')
def sr_first_section(s):
    handoff(s, MARK + '\n## 红线/未决\nFIRST_RED_R5\n## 下一步\nFIRST_NEXT_R5\n'
            '## 红线/未决\nSECOND_RED_R5\n## 下一步\nSECOND_NEXT_R5\n')
    out = s.start('old-r5', 'compact')
    check(all(x in out for x in ('FIRST_RED_R5', 'FIRST_NEXT_R5')) and
          not any(x in out for x in ('SECOND_RED_R5', 'SECOND_NEXT_R5')), '没有仅选首节', out)


@case('SR05', '超长 Unicode 红线与下一步', '解码后≤2000字且有截断标注', 'headers.md：“≤2000 字”；improvements.md 第20项：“截断标注”')
def sr_truncate(s):
    handoff(s, MARK + '\n## 红线/未决\n' + '约束🧪' * 3000 + '\n## 下一步\n' + '执行🧪' * 3000)
    out = s.start('old-r5', 'compact')
    check(0 < len(out) <= 2000, 'compact 长度不符合限制', len(out))
    check(any(x in out.lower() for x in ('截断', '截取', '省略', 'truncat')), '超长提取没有截断标注', out[-300:])


@case('SR06', '相对 Edit 路径按 transcript cwd 解析', '无 MARK 的 compact 下一步仍受信', 'headers.md：“相对路径先按记录里的 cwd 解析”')
def sr_relative_edit(s):
    handoff(s, '# 交接\n## 下一步\nRELATIVE_EDIT_R5\n')
    s.transcript(edited='HANDOFF.md')
    out = s.start('old-r5', 'compact')
    check('RELATIVE_EDIT_R5' in out and '数据' in out and '指令' in out, '相对 Edit 来源未解析/缺少边界声明', out)


@case('SR07', 'MARK 只是行内片段', '不信任貌似标记的正文并提示先读路径', 'headers.md：“一行恰为 MARK”；“所有者说「接着做」时先读这个文件”')
def sr_inline_mark(s):
    handoff(s, '# 交接\n前缀 ' + MARK + '\n## 下一步\nINLINE_UNTRUSTED_R5\n')
    out = s.start('old-r5', 'compact')
    check('INLINE_UNTRUSTED_R5' not in out and 'HANDOFF.md' in out and '接着做' in out,
          '行内伪标记绕过信任边界', out)


@case('SR08', 'worktree 的 .git 文件阻断向上找交接', '不注入 worktree 上级仓库的正文', 'headers.md：“不越过项目根（.git 目录或文件…）”')
def sr_git_file(s):
    handoff(s, MARK + '\n## 下一步\nPARENT_PRIVATE_R5\n')
    nested = s.project / 'worktree'
    nested.mkdir()
    (nested / '.git').write_text('gitdir: ../metadata\n')
    s.project = nested
    out = s.start('old-r5', 'compact')
    check('PARENT_PRIVATE_R5' not in out, '越过 .git 文件边界', out)


@case('SR09', '同 cwd 两个 SessionEnd', '指针持久化同时保留两个 session_id', 'headers.md：“指针按旧 session_id 分条存”')
def sr_multiple_pointers(s):
    for sid in ('alpha-r5', 'beta-r5'):
        s.transcript(sid, marker=sid)
        s.end(sid)
    files = list((s.state / 'last_clear').glob('*.json'))
    text = '\n'.join(p.read_text() for p in files)
    check('alpha-r5' in text and 'beta-r5' in text, '后一次 SessionEnd 覆盖另一 session 指针', text)


@case('SR10', '同 cwd 两个异属指针候选', '新 owner 不得采用其他 owner 的摘要', 'headers.md：“SessionStart 只认同一拥有者…不按 cwd 猜”')
def sr_foreign_owners(s):
    for sid in ('foreign-a-r5', 'foreign-b-r5'):
        s.transcript(sid, marker='FOREIGN_OWNER_SECRET_R5')
        s.end(sid, foreign=True)
    out = s.start()
    check('FOREIGN_OWNER_SECRET_R5' not in out, '异属摘要泄漏', out)


@case('SR11', '本 owner 与较新 foreign 指针共存', '选择本 owner 摘要并消费，第二次不再重放', 'headers.md：“同一拥有者的指针”；“选择和消费在同一把锁里”', True)
def sr_select_consume(s):
    s.transcript('local-r5', marker='LOCAL_CHAIN_R5')
    s.end('local-r5')
    s.transcript('foreign-r5', marker='FOREIGN_CHAIN_R5')
    s.end('foreign-r5', foreign=True)
    first = s.start('new-one-r5')
    second = s.start('new-two-r5')
    check('LOCAL_CHAIN_R5' in first and 'FOREIGN_CHAIN_R5' not in first and
          'LOCAL_CHAIN_R5' not in second and 'FOREIGN_CHAIN_R5' not in second,
          'owner 选择或一次消费不正确', (first, second))


@case('SR12', '两个 SessionStart 并发消费同一指针', '原摘要恰好被一个消费者注入', 'headers.md：“选择和消费在同一把锁里…消费失败不出摘要”', True)
def sr_atomic_consume(s):
    s.transcript(marker='ATOMIC_CONSUME_R5')
    s.end()
    # Second foreign pointer also rules out ambiguous unowned transcript fallback.
    s.transcript('foreign-r5', marker='FOREIGN_ATOMIC_R5')
    s.end('foreign-r5', foreign=True)
    outs = s.parallel('session_resume.py', [s.payload(sid, hook_event_name='SessionStart', source='clear')
                                         for sid in ('consumer-a', 'consumer-b')])
    check(sum('ATOMIC_CONSUME_R5' in context(o) for o in outs) == 1, '摘要未被恰好一次消费', outs)


@case('SR13', '旧单指针文件无 owner', '兼容旧格式不报错', 'headers.md：“读旧版单指针文件不报错（当作无拥有者的一条）”', True)
def sr_legacy_pointer(s):
    s.transcript()
    s.end()
    paths = list((s.state / 'last_clear').glob('*.json'))
    check(bool(paths), 'SessionEnd 未生成指针')
    # Only a minimal legacy fixture; field layout was not published. No digest assertion.
    for p in paths:
        dump(p, {'session_id': 'old-r5', 'transcript_path': str(s.transcripts / 'old-r5.jsonl'),
                 'cwd': str(s.project), 'ts': time.time()})
    s.start()


@case('SR14', '指针文件 JSON 被截断且存在多个候选', '安静放行、不从歧义候选猜摘要', 'headers.md：“没有可靠关联且候选不止一个时不出摘要”；“任何异常一律放行”')
def sr_corrupt_pointer(s):
    s.transcript(marker='CORRUPT_POINTER_SECRET_R5')
    s.end()
    s.transcript('decoy-r5', marker='DECOY_POINTER_SECRET_R5')
    for p in (s.state / 'last_clear').glob('*.json'):
        p.write_text('{"cut":')
    out = s.start()
    check('CORRUPT_POINTER_SECRET_R5' not in out and 'DECOY_POINTER_SECRET_R5' not in out, '坏指针导致猜测摘要', out)


@case('CG01', '同 session 软线连续两次', '首次提醒、下一条不重复', 'improvements.md 第19项：“首次软/强各一次，之后每5万 token 或10条”')
def cg_soft_dedup(s):
    a, b = s.guard(), s.guard()
    check('/clear' in a and not b.strip(), '软线未去重', (a, b))


@case('CG02', '同 session 软线→强线→重复强线', '强线首次单独提醒且随后去重', 'improvements.md 第19项：“首次软/强各一次”')
def cg_strong_dedup(s):
    s.guard()
    a, b = s.guard(310000), s.guard(310000)
    check('/clear' in a and not b.strip(), '强线提醒/去重错误', (a, b))


@case('CG03', '无新增 token 连续 UserPromptSubmit', '不足10条不提醒，第10条再次提醒', 'improvements.md 第19项：“之后每5万 token 或10条”', True)
def cg_message_cadence(s):
    check('/clear' in s.guard(), '首次提醒缺失')
    outs = [s.guard() for _ in range(10)]
    check(all(not o.strip() for o in outs[:9]) and '/clear' in outs[9], '10条计数边界不符', outs)


@case('CG04', '五小时快用完时同档 token 增量', '增加49999不提醒、增加50000再次提醒', 'improvements.md 第19项：“之后每5万 token 或10条”')
def cg_token_cadence(s):
    s.hook('statusline.sh', s.status(s.rate(96, 50)))
    check('/clear' in s.guard(110000), '快用完档初始提醒缺失')
    a, b = s.guard(159999), s.guard(160000)
    check(not a.strip() and '/clear' in b, '5万 token 去重边界错误', (a, b))


@case('CG05', '同 cwd 不同 session 交错提交', '每个 session 首次分别提醒，回到原 session 不重复', 'improvements.md 第19项：“超线提醒按 session 去重”')
def cg_session_keys(s):
    outs = [s.guard(sid=x) for x in ('session-a', 'session-b', 'session-a')]
    check('/clear' in outs[0] and '/clear' in outs[1] and not outs[2].strip(), 'session 去重互相污染', outs)


def seed_state(s):
    dump(s.state / 'rate.json', {'ts': 1, 'rate_limits': {'five_hour': {'used_percentage': 73, 'resets_at': 2}}})
    dump(s.state / 'mode.json', {'r5': 'sentinel'})
    (s.state / 'unrelated.r5').write_bytes(b'R5\x00sentinel')


def sl_bad(s, payload=None, raw=None):
    seed_state(s)
    before = tree(s.state)
    out = s.hook('statusline.sh', payload, raw=raw)
    check(len(out.splitlines()) == 1 and bool(out.strip()) and 'traceback' not in out.lower(),
          '坏输入未返回单行兜底', out)
    check(tree(s.state) == before, '坏输入改写了 state', tree(s.state))


@case('SL01', '顶层数组 stdin', '单行兜底且 state 所有文件不变', 'headers.md：“stdin 损坏或字段类型不对…兜底；也不改写 state”')
def sl_array(s):
    sl_bad(s, raw='[1,{"model":"R5"}]')


@case('SL02', 'current_usage 是列表', '单行兜底且不改写 state', 'headers.md：“字段类型不对…只输出这一行兜底…不改写 state”')
def sl_usage_type(s):
    value = s.status()
    value['context_window']['current_usage'] = ['bad']
    sl_bad(s, value)


@case('SL03', '输入 token 为对象', '单行兜底且不改写 state', 'headers.md：“字段类型不对…不打 traceback…不改写 state”')
def sl_token_type(s):
    value = s.status()
    value['context_window']['current_usage']['input_tokens'] = {'bad': 1}
    sl_bad(s, value)


@case('SL04', '本次没有额度数据', 'rate.json 字节和 ts 均保持原样', 'improvements.md 第10项：“本会话没有额度数据时保留旧读数…没有新数据时不刷新 ts”')
def sl_no_rate(s):
    s.hook('statusline.sh', s.status(s.rate(81, 67)))
    p = s.state / 'rate.json'
    original = p.read_bytes()
    s.hook('statusline.sh', s.status())
    check(p.read_bytes() == original, '无新数据时刷新/丢弃缓存', p.read_text())


@case('SL05', '相同 reset 窗口较小值回写', '两个窗口均保留较大用量', 'improvements.md 第10项：“同一重置窗口取较大的用量”')
def sl_merge_max(s):
    rates = s.rate(88, 79)
    s.hook('statusline.sh', s.status(rates))
    lower = copy.deepcopy(rates)
    for window in lower.values():
        window['used_percentage'] = 12
    s.hook('statusline.sh', s.status(lower))
    now = json.loads((s.state / 'rate.json').read_text())['rate_limits']
    check(now['five_hour']['used_percentage'] == 88 and now['seven_day']['used_percentage'] == 79, '同窗口被较小值覆盖', now)


@case('SL06', '只更新一个新窗口', '另一窗口保留原读数与 sampled_at', 'improvements.md 第10项：“合并写 rate.json”；第19项：“每窗口独立 sampled_at”')
def sl_partial(s):
    rates = s.rate(82, 71)
    s.hook('statusline.sh', s.status(rates))
    p = s.state / 'rate.json'
    v = json.loads(p.read_text())
    v['rate_limits']['seven_day']['sampled_at'] = int(time.time()) - 123
    dump(p, v)
    original = copy.deepcopy(v['rate_limits']['seven_day'])
    five = dict(rates['five_hour'], used_percentage=86)
    s.hook('statusline.sh', s.status({'five_hour': five}))
    actual = json.loads(p.read_text())['rate_limits']
    check(actual['seven_day'] == original and actual['five_hour']['used_percentage'] == 86, '部分更新污染另一窗口', actual)


@case('SL07', '旧窗口已重置后写入较低新值', '旧高读数作废，接受新窗口低值', 'improvements.md 第10项：“窗口已重置的旧读数作废”')
def sl_new_window(s):
    now = int(time.time())
    dump(s.state / 'rate.json', {'ts': now - 100, 'rate_limits': {
        'five_hour': {'used_percentage': 99, 'resets_at': now - 1, 'sampled_at': now - 100}}})
    rates = s.rate(8, 40)
    s.hook('statusline.sh', s.status(rates))
    actual = json.loads((s.state / 'rate.json').read_text())['rate_limits']['five_hour']
    check(actual['used_percentage'] == 8 and actual['resets_at'] == rates['five_hour']['resets_at'], '新窗口继承旧高值', actual)


def filtered(s, command):
    out = s.hook('test_filter.py', s.payload(hook_event_name='PreToolUse',
                 tool_name='Bash', tool_input={'command': command}))
    actual = command
    if out.strip():
        value = json.loads(out)
        body = value['hookSpecificOutput']
        check(body.get('hookEventName') == 'PreToolUse', '响应事件错误', body)
        actual = body.get('updatedInput', {}).get('command', command)
        check(isinstance(actual, str), '改写 command 不是字符串', actual)
    # Every returned command, including unchanged commands, is syntax checked only.
    s.run(['/bin/bash', '--noprofile', '--norc', '-n'], actual)
    return actual


def tf_bypass(code, scene, command, source, inferred=False):
    @case(code, scene, '原样放行且 bash -n 通过', source, inferred)
    def run(s):
        actual = filtered(s, command)
        check(actual == command, '复杂结构不应改写', actual)


tf_bypass('TF01', 'case 分支内含测试', 'case r5 in\nr5) pytest -q ;;\n*) true ;;\nesac',
          'headers.md：“任何一段以 bash 保留字开头…看不准就放行”')
tf_bypass('TF02', '函数定义及函数调用', 'r5_fn() { pytest -q; }\nr5_fn',
          'headers.md：“括号…多行结构…看不准就放行”')
tf_bypass('TF03', '花括号命令组', '{\npytest -q;\necho R5;\n}',
          'headers.md：“任何一段以 bash 保留字开头（…{ 等多行结构）”')
tf_bypass('TF04', 'time 关键字前缀', 'time pytest -q',
          'headers.md：“任何一段以 bash 保留字开头”；“只在命令位置识别测试”')
tf_bypass('TF05', 'env 包装器', 'env R5=1 pytest -q',
          'headers.md：“每段的第一个词”；improvements.md 第8项列出的测试首词', True)
tf_bypass('TF06', '逻辑否定 ! 前缀', '! pytest -q',
          'headers.md：“任何一段以 bash 保留字开头”；“看不准就放行”')
tf_bypass('TF07', 'exec 前缀', 'exec pytest -q',
          'headers.md：“只在命令位置…每段的第一个词”', True)
tf_bypass('TF08', 'bash -c 嵌套多测试文本', "bash -c 'pytest -q; npm test'",
          'headers.md：“每段的第一个词”；improvements.md 第8项列出的测试首词', True)
tf_bypass('TF09', '进程替换作为测试参数', 'pytest <(printf R5)',
          'headers.md：“含…重定向…括号…看不准就放行”')
tf_bypass('TF10', 'Bash &> 合并重定向', 'pytest -q &> r5.log',
          'headers.md：“含…重定向…看不准就放行”')
tf_bypass('TF11', '仅 stderr 合并到 stdout', 'pytest -q 2>&1',
          'headers.md：“含…重定向…看不准就放行”')


def fake_bin(s):
    bindir = s.root / 'bin'
    bindir.mkdir()
    for name in ('pytest', 'npm', 'node'):
        p = bindir / name
        p.write_text('#!' + sys.executable + '\nimport sys\n'
                     'print("FAKE_R5_" + " ".join(sys.argv[1:]))\n'
                     'print("1 passed in 0.01s")\n'
                     'sys.exit(7 if "--fail-r5" in sys.argv else 0)\n')
        p.chmod(0o700)
    s.env['PATH'] = str(bindir) + os.pathsep + s.env.get('PATH', '/usr/bin:/bin')


def execute_filter(s, command, expected_code, markers, require_rewrite=False):
    fake_bin(s)
    actual = filtered(s, command)
    if require_rewrite:
        check(actual != command, '可识别的多测试段未被包装', actual)
    # These fixed payloads contain only fake tests and side-effect-free shell builtins.
    p = s.run(['/bin/bash', '--noprofile', '--norc', '-c', actual], good=False)
    check(p.returncode == expected_code and all(m in p.stdout + p.stderr for m in markers),
          '执行语义/退出码被破坏', (p.returncode, p.stdout, p.stderr))
    return p


@case('TF12', '注释包含分隔符和伪测试', "假 pytest 仍执行一次、注释内文本不执行", 'improvements.md 第1项：“带注释能跑，rc 保留”')
def tf_comment(s):
    p = execute_filter(s, "pytest -q # ; npm test && node --test\nprintf 'AFTER_COMMENT_R5\\n'", 0,
                       ['FAKE_R5_-q', 'AFTER_COMMENT_R5'])
    check((p.stdout + p.stderr).count('FAKE_R5_') == 1, '注释中的伪测试被执行', p.stdout + p.stderr)


@case('TF13', '反斜线续行参数', '续行保留参数及退出码', 'headers.md：“包装保留原退出码”；“看不准就放行”', True)
def tf_continuation(s):
    execute_filter(s, 'pytest \\\n --fail-r5', 7, ['FAKE_R5_--fail-r5'])


@case('TF14', 'ANSI-C 引号内含分隔符', '引号参数保留、不执行参数中的伪命令', 'headers.md：“只在命令位置识别测试…其余段输出原样保留”', True)
def tf_ansi_quote(s):
    execute_filter(s, "pytest -k $'alpha; npm test && beta'", 0,
                   ['FAKE_R5_-k alpha; npm test && beta'])


@case('TF15', 'pytest/npm/node 三段测试含中间普通输出', '三段测试和普通输出均执行且最终失败码保留', 'headers.md：“只包装测试那一段，其余段的输出原样保留”；“保留原退出码”')
def tf_multi(s):
    execute_filter(s, "pytest -q; printf 'BETWEEN_R5\\n'; npm test; node --test --fail-r5", 7,
                   ['FAKE_R5_-q', 'BETWEEN_R5', 'FAKE_R5_test', 'FAKE_R5_--test --fail-r5'],
                   require_rewrite=True)


def selected(code):
    only = tuple(x.strip() for x in os.environ.get('HELDOUT_ONLY', '').split(',') if x.strip())
    skip = tuple(x.strip() for x in os.environ.get('HELDOUT_SKIP', '').split(',') if x.strip())
    return (not only or code.startswith(only)) and not (skip and code.startswith(skip))


def main():
    passed = failed = 0
    for code, scene, expected, source, inferred, fn in CASES:
        if not selected(code):
            continue
        folder = None
        error = None
        try:
            folder = tempfile.mkdtemp(prefix='heldout-r5-' + code + '-')
            folder = os.path.realpath(folder)
            fn(Box(folder))
        except Exception as exc:
            error = ('RUNNER FAIL ' if isinstance(exc, RunnerFailure) else '') + type(exc).__name__ + ': ' + str(exc)
        finally:
            if folder is not None:
                try:
                    shutil.rmtree(folder)
                except Exception as exc:
                    error = (error or '') + ' 清理失败: ' + str(exc)
        if error:
            failed += 1
            print(f'FAIL {code} {scene} — ' + ' '.join(error.split())[:300], flush=True)
        else:
            passed += 1
            print(f'PASS {code} {scene} — 符合预期', flush=True)
    print(f'总数 {passed + failed} | PASS {passed} | FAIL {failed}', flush=True)
    return int(failed != 0)


if __name__ == '__main__':
    sys.exit(main())
