#!/usr/bin/env python3
"""第 5 轮 A：依次跑一批开关实验（每个都在 scratchpad 临时目录、用 --settings 临时文件或环境变量，
不碰真实 settings），每个实验都开一个子 agent，同时得到主会话和子 agent 的首请求 token 数。
结果追加到 <runs>/batch.jsonl。用法：python3 A_batch.py [实验名前缀过滤 ...]
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from A_probe import run, brief, RUNS  # noqa: E402

ANTH = ['built-in-browser', 'chrome-browser', 'computer-use', 'deep-research', 'docs', 'docx', 'google-workspace',
        'import-memory', 'morning', 'pdf', 'pptx', 'skill-creator', 'xlsx']
UNUSED_MAIN_TOOLS = ['ScheduleWakeup', 'ReportFindings', 'SendFeedback', 'Workflow', 'ListAgents']

EXPS = [
    ('B1_base', {}, {}, [], False),
    ('B2_base', {}, {}, [], False),
    ('A1_denyArtifact', {'permissions': {'deny': ['Artifact']}}, {}, [], False),
    ('A2_enableArtifactFalse', {'enableArtifact': False}, {}, [], False),
    ('A3_envDisableArtifact', {}, {'CLAUDE_CODE_DISABLE_ARTIFACT': '1'}, [], False),
    ('A4_cliDisallowedArtifact', {}, {}, ['--disallowedTools', 'Artifact'], False),
    ('S1_overrideOffPrefixed', {'skillOverrides': {'anthropic-skills:' + n: 'off' for n in ANTH}}, {}, [], False),
    ('S2_overrideOffBare', {'skillOverrides': {n: 'off' for n in ANTH}}, {}, [], False),
    ('S3_overrideNameOnly', {'skillOverrides': {'anthropic-skills:' + n: 'name-only' for n in ANTH}}, {}, [], False),
    ('S4_syncSkillsFalse', {'syncClaudeAiSkills': False}, {}, [], False),
    ('S5_disableBundled', {'disableBundledSkills': True}, {}, [], False),
    ('S6_maxDesc250', {'skillListingMaxDescChars': 250}, {}, [], False),
    ('M1_disableConnectors', {'disableClaudeAiConnectors': True}, {}, [], False),
    ('M2_envNoClaudeAiMcp', {}, {'ENABLE_CLAUDEAI_MCP_SERVERS': 'false'}, [], False),
    ('T1_denyUnusedMain', {'permissions': {'deny': UNUSED_MAIN_TOOLS}}, {}, [], False),
    ('G0_gitBase', {}, {}, [], True),
    ('G1_gitNoInstr', {'includeGitInstructions': False}, {}, [], True),
]

if __name__ == '__main__':
    filt = sys.argv[1:]
    out = os.path.join(RUNS, 'batch.jsonl')
    for name, st, env, args, git in EXPS:
        if filt and not any(name.startswith(f) for f in filt):
            continue
        res = run(name, settings=st or None, env_extra=env, extra_args=args, sub=True, git=git, timeout=300)
        b = brief(res)
        with open(out, 'a') as f:
            f.write(json.dumps(b, ensure_ascii=False) + '\n')
        print(json.dumps({'name': name, 'done': b['done'],
                          'main': (b.get('main') or {}).get('first_input_total'),
                          'sub': (b.get('sub') or {}).get('first_input_total')}), flush=True)
