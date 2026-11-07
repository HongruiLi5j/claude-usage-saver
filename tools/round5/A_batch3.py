#!/usr/bin/env python3
"""第 5 轮 A：第三批（额度重置后补测，按主会话要求用 sonnet、只跑最少次数）。
固定开销的增量和模型关系不大，所以 sonnet 下的 Δ 用来补 opus 那批没跑完的开关；
同时跑一次 sonnet 基线 + 一次 sonnet 下的 enableArtifact:false，用来核对 sonnet 和 opus 的 Δ 是否一致。
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from A_probe import run, brief, RUNS  # noqa: E402
import A_apply  # noqa: E402
from A_batch2 import FILES, READ_PROMPT, agents_hits, combo  # noqa: E402

M = ['--model', 'sonnet']
# (名字, settings, env, sub, kind)
EXPS = [
    ('Z0_sonBase', {}, {}, True, None),
    ('Z1_sonEnableArtifactFalse', {'enableArtifact': False}, {}, False, None),
    ('Z2_sonUserInvocable', {'skillOverrides': {'anthropic-skills:' + n: 'user-invocable-only' for n in A_apply.ANTH}}, {}, False, None),
    ('Z3_sonDenyUnused', {'permissions': {'deny': A_apply.UNUSED_TOOLS}}, {}, False, None),
    ('Z4_sonNoConnectors', {'disableClaudeAiConnectors': True}, {}, True, None),
    ('Z5_sonGitBase', {}, {}, False, 'git'),
    ('Z6_sonGitNoInstr', {'includeGitInstructions': False}, {}, False, 'git'),
    ('Z7b_sonAgentsBase', {}, {}, False, 'files'),
    ('Z8b_sonAgentsClaudeMdOnly', {'pluginConfigs': {'cc-plugin-agents-md@builtin': {'options': {'instructionFiles': 'claude-md'}}}}, {}, False, 'files'),
    ('Z9_sonCoreTools', combo(['artifact', 'skills', 'tools']), {}, True, None),
]

if __name__ == '__main__':
    filt = sys.argv[1:]
    out = os.path.join(RUNS, 'batch.jsonl')
    for name, st, env, sub, kind in EXPS:
        if filt and not any(name.startswith(f) for f in filt):
            continue
        kw = dict(settings=st or None, env_extra=env, extra_args=M, sub=sub, timeout=300)
        if kind == 'files':
            kw.update(files=FILES, prompt=READ_PROMPT)
        if kind == 'git':
            kw.update(git=True)
        res = run(name, **kw)
        b = brief(res)
        if kind == 'files':
            b['agents_md_hits'] = agents_hits(res)
        with open(out, 'a') as f:
            f.write(json.dumps(b, ensure_ascii=False) + '\n')
        print(json.dumps({'name': name, 'done': b['done'], 'main': (b.get('main') or {}).get('first_input_total'),
                          'sub': (b.get('sub') or {}).get('first_input_total'), 'agents': b.get('agents_md_hits')}), flush=True)
