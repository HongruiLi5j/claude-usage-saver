#!/usr/bin/env python3
"""第 5 轮 A：在临时目录里起一个真实的交互式 claude 会话（pty），测首请求的固定开销。

为什么不用 `claude -p`：实测 -p 模式下工具集和交互式不同（没有 Artifact、AskUserQuestion、
WebFetch，也不连 claude.ai 的 MCP），量不出交互会话的真实开销。所以这里用 pty 驱动交互式会话，
发一句话，等 transcript 里出现首条 assistant usage，然后退出。

只做统计：输出工具名、字符数、附件类型、usage 数字，不输出对话正文。
实验目录在 scratchpad 下（/private/tmp/...），weekly_compare / r5 系列统计会排除 -private-tmp-。

用法：
  python3 A_probe.py <名字> [--settings-json '<json>'] [--env K=V ...] [--sub] [--arg <cli参数> ...]
    --sub   让主会话开一个 general-purpose 子 agent（任务只是回 OK），同时测子 agent 首请求。
输出：一行 JSON（main / sub 两个 summary），同时存到 <runs>/<名字>.json
"""
import json, os, pty, select, signal, sys, time, glob, re

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from A_shape import summarize  # noqa: E402

RUNS = os.environ.get('A_RUNS', '/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/A/runs')
PROJ = os.path.expanduser('~/.claude/projects')
MAIN_PROMPT = 'Reply with exactly: OK'
SUB_PROMPT = ('Call the Agent tool exactly once with subagent_type "general-purpose", description "probe", '
              'and prompt "Reply with exactly OK and nothing else. Do not use any tools." '
              'After it returns, reply with exactly: OK')


def enc(p):
    return re.sub(r'[^A-Za-z0-9]', '-', p)


def find_transcript(cwd, t0):
    d = os.path.join(PROJ, enc(os.path.realpath(cwd)))
    c = [f for f in glob.glob(os.path.join(d, '*.jsonl')) if os.path.getmtime(f) >= t0 - 1]
    return max(c, key=os.path.getmtime) if c else None


def n_assistant(path):
    n = 0
    try:
        with open(path) as f:
            for line in f:
                if '"type":"assistant"' in line and '"usage"' in line:
                    n += 1
    except FileNotFoundError:
        pass
    return n


def run(name, settings=None, env_extra=None, extra_args=None, sub=False, timeout=240, wait=15, git=False, prompt=None, files=None):
    cwd = os.path.join(RUNS, name)
    os.makedirs(cwd, exist_ok=True)
    for rel, text in (files or {}).items():
        fp = os.path.join(cwd, rel); os.makedirs(os.path.dirname(fp), exist_ok=True)
        open(fp, 'w').write(text)
    if git and not os.path.isdir(os.path.join(cwd, '.git')):
        import subprocess
        subprocess.run(['git', 'init', '-q', cwd])
        open(os.path.join(cwd, 'README.md'), 'w').write('probe\n')
        subprocess.run(['git', '-C', cwd, 'add', '.']); subprocess.run(['git', '-C', cwd, '-c', 'user.name=p', '-c', 'user.email=p@p', 'commit', '-qm', 'init'])
    args = ['claude']
    if settings is not None:
        sp = os.path.join(RUNS, name + '.settings.json')
        with open(sp, 'w') as f:
            json.dump(settings, f)
        args += ['--settings', sp]
    args += list(extra_args or [])
    # 不把 prompt 放在命令行：真实使用时人会先等几秒再打字，claude.ai 的 MCP 连接器要几秒才连上。
    # 所以等 wait 秒再把 prompt 打进输入框。
    prompt = (prompt or (SUB_PROMPT if sub else MAIN_PROMPT)).replace('{CWD}', os.path.realpath(cwd))
    want_main = 2 if (files and not sub) else 1
    sent = False
    # 去掉父会话的标记（否则子进程认为自己是 child session，不写 transcript、继承 auto mode 等）
    env = {k: v for k, v in os.environ.items() if not (k.startswith('CLAUDE_CODE_') or k in ('CLAUDECODE', 'CLAUDE_EFFORT', 'CLAUDE_PID'))}
    env['USAGE_SAVER_STATE'] = os.path.join(RUNS, '_state')
    env.update(env_extra or {})
    t0 = time.time()
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execvpe(args[0], args, env)
    os.set_blocking(fd, False)
    buf = b''
    trusted = False
    tr = None
    done = False
    screen = os.path.join(RUNS, name + '.screen.txt')

    def drain():
        nonlocal buf
        while True:
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                return True
            except OSError:
                return False
            if not chunk:
                return False
            buf = (buf + chunk)[-30000:]

    def send(b):
        for _ in range(50):
            try:
                os.write(fd, b); return
            except BlockingIOError:
                drain(); time.sleep(0.05)
            except OSError:
                return

    last_dump = 0
    while time.time() - t0 < timeout:
        select.select([fd], [], [], 1.0)
        if not drain():
            break
        low = re.sub(rb'\s+', b' ', re.sub(rb'\x1b\[[0-9;?]*[A-Za-z]', b' ', buf)).lower()
        if not trusted and b'trust this folder' in low and b'to confirm' in low:
            # 信任对话框默认选中 "No, exit"，要先按下箭头再回车
            time.sleep(0.8); drain()
            send(b'\x1b[B'); time.sleep(0.5); drain()
            send(b'\r')
            trusted = True
        if not sent and time.time() - t0 > wait:
            send(prompt.encode()); time.sleep(0.8); drain(); send(b'\r'); sent = True
        if time.time() - last_dump > 5:
            with open(screen, 'wb') as f:
                f.write(re.sub(rb'\x1b\[[0-9;?]*[A-Za-z]', b' ', buf)[-6000:])
            last_dump = time.time()
        tr = tr or find_transcript(cwd, t0)
        if tr:
            if not sub and n_assistant(tr) >= want_main:
                done = True
            if sub:
                sd = os.path.join(tr[:-6], 'subagents')
                subs = glob.glob(os.path.join(sd, '*.jsonl'))
                if subs and any(n_assistant(s) >= 1 for s in subs) and n_assistant(tr) >= 2:
                    done = True
        if done:
            time.sleep(1.5)
            break
    send(b'\x1b'); time.sleep(0.3); drain()
    send(b'/exit\r')
    for sig in (None, signal.SIGTERM, signal.SIGKILL):
        if sig:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        t1 = time.time()
        while time.time() - t1 < 3:
            drain()
            try:
                if os.waitpid(pid, os.WNOHANG)[0]:
                    break
            except ChildProcessError:
                break
            time.sleep(0.2)
        else:
            continue
        break
    with open(screen, 'wb') as f:
        f.write(re.sub(rb'\x1b\[[0-9;?]*[A-Za-z]', b' ', buf)[-6000:])
    res = {'name': name, 'done': done, 'settings': settings, 'env': env_extra, 'args': extra_args}
    if tr:
        res['main'] = summarize(tr)
        if sub:
            subs = sorted(glob.glob(os.path.join(tr[:-6], 'subagents', '*.jsonl')), key=os.path.getmtime)
            if subs:
                res['sub'] = summarize(subs[0])
    with open(os.path.join(RUNS, name + '.json'), 'w') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return res


def brief(res):
    out = {'name': res['name'], 'done': res['done']}
    for k in ('main', 'sub'):
        s = res.get(k)
        if s:
            out[k] = {'first_input_total': s.get('first_input_total'), 'tools_chars': s.get('tools_chars'),
                      'system_chars': s.get('system_chars'), 'tools': sorted((s.get('tools') or {}).keys()),
                      'skill_listing_chars': s['attachments_chars'].get('skill_listing'),
                      'deferred_chars': s['attachments_chars'].get('deferred_tools_delta'),
                      'mcp_instr_chars': s['attachments_chars'].get('mcp_instructions_delta'),
                      'agent_listing_chars': s['attachments_chars'].get('agent_listing_delta')}
    return out


if __name__ == '__main__':
    a = sys.argv[1:]
    name = a.pop(0)
    settings = None; env = {}; extra = []; sub = False; git = False; wait = 15
    while a:
        x = a.pop(0)
        if x == '--settings-json':
            settings = json.loads(a.pop(0))
        elif x == '--env':
            k, v = a.pop(0).split('=', 1); env[k] = v
        elif x == '--sub':
            sub = True
        elif x == '--git':
            git = True
        elif x == '--wait':
            wait = float(a.pop(0))
        elif x == '--arg':
            extra.append(a.pop(0))
    print(json.dumps(brief(run(name, settings, env, extra, sub, wait=wait, git=git)), ensure_ascii=False))
