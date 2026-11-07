#!/usr/bin/env python3
"""B_probe.py — 实测"压缩上限对哪种会话生效"（第 5 轮 B）。会消耗额度。

在 scratchpad 的临时目录里放 N 个约 19k token 的文本文件，让 Claude 逐个 Read，
上下文一路涨；结束后用 B_compact_audit 读 transcript，看 compact 在多少 token 发生。

模式：
  plain     : claude -p "<prompt>"                        （无头会话）
  henchmen  : 仿 henchmen 后台 agent：去掉 CLAUDE_CODE_* / CLAUDECODE 环境变量，
              -p --verbose --input-format stream-json --output-format stream-json
              --settings '<只含 PreToolUse hook 的 JSON>'，prompt 从 stdin 以 stream-json 送入
  subagent  : claude -p，让主会话派一个 general-purpose 子 agent 去读
  interactive: 用 B_probe_interactive.exp（expect）在伪终端里开交互式 claude（仿 henchmen window agent）；不支持 --settings-json/--env，用 --project-settings

不改任何真实设置；--settings-json / --env 只作用于这一次进程。
例：
  python3 B_probe.py plain --model sonnet --files 20 --tag e1
  python3 B_probe.py henchmen --model opus --files 20 --tag e2
  python3 B_probe.py henchmen --model sonnet --files 12 --settings-json '{"env":{"CLAUDE_CODE_AUTO_COMPACT_WINDOW":"150000"}}' --tag e3
"""
import argparse, json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import B_compact_audit as audit  # noqa: E402

SCRATCH = os.environ.get('B_SCRATCH', '/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/B')
DATA = os.path.join(SCRATCH, 'data')

PROMPT = ("This folder has text files f01.txt to f{n:02d}.txt. This is a context-size test. "
          "Use the Read tool to read them one at a time in order (f01.txt first). "
          "One Read call per message, never in parallel, no Bash, no other tools, and write no text between reads. "
          "If the conversation gets compacted, just continue with the next file you have not read yet. "
          "After the last file, reply with exactly one line: LAST=<last file> COMPACTED=<yes/no>.")
SUB_PROMPT = ("Use the Agent tool exactly once, subagent_type general-purpose, model {model}, and give it this task verbatim: \"{inner}\" "
              "Then reply with the subagent's one-line answer. Do not read the files yourself.")
HENCH_SETTINGS = json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Bash|Monitor', 'hooks': [{'type': 'command', 'command': 'true'}]}]}})


def make_workdir(tag, n):
    wd = os.path.join(SCRATCH, 'runs', tag)
    if os.path.exists(wd):
        shutil.move(wd, wd + f'.old{int(time.time())}')
    os.makedirs(wd)
    for i in range(1, n + 1):
        shutil.copy(os.path.join(DATA, f'f{i:02d}.txt'), wd)
    return wd


def merged_settings(base_json, extra):
    d = json.loads(base_json) if base_json else {}
    if extra:
        e = json.loads(extra)
        for k, v in e.items():
            if isinstance(v, dict) and isinstance(d.get(k), dict):
                d[k] = {**d[k], **v}
            else:
                d[k] = v
    return json.dumps(d) if d else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['plain', 'henchmen', 'subagent', 'interactive'])
    ap.add_argument('--model', default='sonnet')
    ap.add_argument('--files', type=int, default=20)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--settings-json', help='额外 --settings（JSON 字符串），henchmen 模式下和 hook JSON 合并')
    ap.add_argument('--env', action='append', default=[], help='K=V，加到子进程环境（henchmen 模式会先去掉 CLAUDE_CODE_*，再加这些）')
    ap.add_argument('--project-settings', help='写到临时工作目录 .claude/settings.json 的 JSON（测项目级设置）')
    ap.add_argument('--idle-wait', type=int, default=0, help='henchmen 模式：第一轮结束后空闲多少秒再发第二条（测空闲压缩）')
    ap.add_argument('--timeout', type=int, default=3000)
    a = ap.parse_args()

    wd = make_workdir(a.tag, a.files)
    if a.project_settings:
        os.makedirs(os.path.join(wd, '.claude'))
        with open(os.path.join(wd, '.claude', 'settings.json'), 'w') as fh:
            fh.write(a.project_settings)
    prompt = PROMPT.format(n=a.files)
    env = dict(os.environ)
    env.pop('CLAUDECODE', None)
    for k in list(env):
        if k.startswith('CLAUDE_CODE_') and k not in ('CLAUDE_CODE_OAUTH_TOKEN',):
            env.pop(k)  # 这里一律去掉父会话传下来的 CLAUDE_CODE_*，避免污染实验
    for kv in a.env:
        k, v = kv.split('=', 1); env[k] = v
    t0 = time.time()
    if a.mode in ('plain', 'subagent'):
        if a.mode == 'subagent':
            prompt = SUB_PROMPT.format(model=a.model, inner=prompt.replace('"', "'"))
        cmd = ['claude', '-p', prompt, '--model', a.model, '--output-format', 'json',
               '--permission-mode', 'bypassPermissions']
        s = merged_settings(None, a.settings_json)
        if s:
            cmd += ['--settings', s]
        p = subprocess.run(cmd, cwd=wd, env=env, capture_output=True, text=True, timeout=a.timeout)
        out = p.stdout
        try:
            res = json.loads(out); sid = res.get('session_id'); print('result:', (res.get('result') or '')[:200])
        except Exception:
            sid = None; print('stdout 解析失败：', out[-500:], p.stderr[-500:])
    elif a.mode == 'henchmen':
        cmd = ['claude', '-p', '--verbose', '--input-format', 'stream-json', '--output-format', 'stream-json',
               '--model', a.model, '--permission-mode', 'bypassPermissions',
               '--settings', merged_settings(HENCH_SETTINGS, a.settings_json)]
        msg = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': prompt}]}})
        p = subprocess.Popen(cmd, cwd=wd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        p.stdin.write(msg + '\n'); p.stdin.flush()
        sid = None; compact_events = 0
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            sid = sid or ev.get('session_id')
            if ev.get('type') == 'system' and ev.get('subtype') != 'init' and 'compact' in line.lower():
                compact_events += 1
                print(time.strftime('%H:%M:%S'), 'event:', ev.get('subtype'), {k: ev.get(k) for k in ('compact_metadata', 'status')}, flush=True)
            if ev.get('type') == 'result':
                print('result:', str(ev.get('result'))[:200], flush=True)
                if a.idle_wait and not getattr(a, '_waited', False):
                    # 仿 henchmen：会话开着等 room 消息；空闲 idle_wait 秒后再送一条
                    a._waited = True
                    print(f'空闲等待 {a.idle_wait}s …', flush=True)
                    time.sleep(a.idle_wait)
                    p.stdin.write(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'Reply with exactly: ok'}]}}) + '\n')
                    p.stdin.flush()
                    continue
                p.stdin.close()  # 结束会话（henchmen 是一直开着等下一条消息的）
                break
        p.wait(timeout=120)
    else:  # interactive：用 expect 开交互会话（screen 版点不了信任框，已弃用）
        exp = os.path.join(HERE, 'B_probe_interactive.exp')
        logf = os.path.join(SCRATCH, 'logs', f'{a.tag}_expect.log')
        os.makedirs(os.path.dirname(logf), exist_ok=True)
        subprocess.run(['expect', exp, wd, a.model, prompt, logf, str(a.timeout)], timeout=a.timeout + 120)
        sid = None
    print(f'用时 {time.time()-t0:.0f}s  session={sid}  workdir={wd}')
    pdir = audit.proj_dir_for(wd)
    for f in audit.files_for(pdir):
        r = audit.audit_file(f)
        tag = '[sub] ' if '/subagents/' in f else ''
        print(f"{tag}{os.path.basename(f)[:20]} 请求 {r['n']} 最大 {r['max_ctx']} 模型 {r['models']}")
        for c in r['compacts']:
            print(f"   compact trigger={c['trigger']} pre={c['pre']} post={c['post']}")
        print('   ctx(k):', ' '.join(str(x // 1000) for x in r['ctx_series']))


if __name__ == '__main__':
    main()
