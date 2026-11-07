#!/usr/bin/env python3
"""第 5 轮 A：第二批实验。
  S7  skillOverrides = user-invocable-only（A_apply 用的模式）
  X*  AGENTS.md 注入来源和开关：临时目录 sub/AGENTS.md（无 CLAUDE.md），让会话 Read sub/x.txt，
      看 transcript 里有没有带标记的 hook_additional_context（内置插件 cc-plugin-agents-md 的 PostToolUse）
  C*  组合：用 A_apply.plan() 生成的同一份设置，经 --settings 传入，量组合后的首请求
结果追加到 <runs>/batch.jsonl。
"""
import json, os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from A_probe import run, brief, RUNS, find_transcript  # noqa: E402
import A_apply  # noqa: E402

MARK = 'R5A-AGENTS-MARKER-7f3e'
FILES = {'sub/AGENTS.md': f'# probe\n{MARK}: this line only exists to detect AGENTS.md injection.\n' + ('filler line for size.\n' * 40),
         'sub/x.txt': 'hello\n'}
READ_PROMPT = 'Use the Read tool with file_path exactly {CWD}/sub/x.txt (copy it character for character), then reply with exactly: OK'


def agents_hits(res):
    p = (res.get('main') or {}).get('path')
    if not p:
        return None
    hits = []
    for line in open(p):
        if MARK in line:
            d = json.loads(line)
            a = d.get('attachment') or {}
            hits.append((d.get('type'), a.get('type'), a.get('hookName'), a.get('hookEvent')))
    return [h for h in hits if h[0] == 'attachment']


def combo(groups):
    s, _ = A_apply.plan({}, groups)
    return s


EXPS = [
    ('S7_overrideUserInvocable', {'skillOverrides': {'anthropic-skills:' + n: 'user-invocable-only' for n in A_apply.ANTH}}, {}, False, None),
    ('X0_agentsBase', {}, {}, False, 'files'),
    ('X1_agentsPluginCfgCc', {'pluginConfigs': {'cc-plugin-agents-md@builtin': {'options': {'instructionFiles': 'claude-md'}}}}, {}, False, 'files'),
    ('X2_agentsPluginCfgShort', {'pluginConfigs': {'agents-md@builtin': {'options': {'instructionFiles': 'claude-md'}}}}, {}, False, 'files'),
    ('X3_agentsPluginOff', {'enabledPlugins': {'cc-plugin-agents-md@builtin': False, 'agents-md@builtin': False}}, {}, False, 'files'),
    ('C1_core', combo(['artifact', 'skills']), {}, True, None),
    ('C2_coreTools', combo(['artifact', 'skills', 'tools']), {}, True, None),
    ('C3_all', combo(['artifact', 'skills', 'tools', 'git', 'connectors']), {}, True, None),
]

if __name__ == '__main__':
    filt = sys.argv[1:]
    out = os.path.join(RUNS, 'batch.jsonl')
    for name, st, env, sub, kind in EXPS:
        if filt and not any(name.startswith(f) for f in filt):
            continue
        kw = dict(settings=st or None, env_extra=env, sub=sub, timeout=300)
        if kind == 'files':
            kw.update(files=FILES, prompt=READ_PROMPT)
        else:
            kw.update(sub=True)
        res = run(name, **kw)
        b = brief(res)
        if kind == 'files':
            b['agents_md_hits'] = agents_hits(res)
        with open(out, 'a') as f:
            f.write(json.dumps(b, ensure_ascii=False) + '\n')
        print(json.dumps({'name': name, 'done': b['done'], 'main': (b.get('main') or {}).get('first_input_total'),
                          'sub': (b.get('sub') or {}).get('first_input_total'), 'agents': b.get('agents_md_hits')}), flush=True)
