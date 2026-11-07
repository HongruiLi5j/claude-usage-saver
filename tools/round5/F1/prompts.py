# 统计主会话里"真人 prompt"条数（排除 <task-notification>/<command-…>/系统标签开头的、isMeta、子 agent），只看首字符和标签名，不存正文
import json, glob, os, datetime as dt, collections, re
ROOT = os.path.expanduser('~/.claude/projects'); CH = dt.datetime(2026, 11, 6, 7, 58, tzinfo=dt.timezone.utc); B0 = dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc)
cnt = collections.Counter(); tags = collections.Counter()
for f in glob.glob(ROOT + '/**/*.jsonl', recursive=True):
    if '-private-tmp-' in f or '/subagents/' in f: continue
    aq = 'AQ-' in f
    for line in open(f, errors='ignore'):
        try: j = json.loads(line)
        except Exception: continue
        if j.get('type') != 'user' or j.get('isMeta') or j.get('isSidechain'): continue
        m = j.get('message') or {}; c = m.get('content') if isinstance(m, dict) else None
        if isinstance(c, list):
            if not c or not all(isinstance(x, dict) and x.get('type') == 'text' for x in c): continue
            c = c[0].get('text', '')
        if not isinstance(c, str): continue
        try: t = dt.datetime.fromisoformat(j['timestamp'].replace('Z', '+00:00'))
        except Exception: continue
        if t < B0: continue
        w = 'after' if t >= CH else ('before_AQ' if aq else 'before_exAQ')
        s = c.lstrip()
        if s.startswith('<'):
            tg = re.match(r'<([a-zA-Z_-]+)', s); tags[(w, tg.group(1) if tg else '?')] += 1
        else: cnt[w] += 1
print('human-typed prompts', dict(cnt)); print(tags.most_common(20))
