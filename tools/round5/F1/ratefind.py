# 在 transcript 的结构化字段（不含 message.content / toolUseResult 正文）里找额度读数，只报路径和计数
import json, glob, os, collections
ROOT = os.path.expanduser('~/.claude/projects'); paths = collections.Counter(); ex = {}
KEYS = ('five_hour', 'seven_day', 'used_percentage', 'utilization', 'rateLimit', 'rate_limit', 'resetsAt', 'resets_at')
def walk(o, p, top):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ('content', 'toolUseResult', 'text', 'stdout', 'stderr', 'lastPrompt', 'snapshot', 'rendered', 'wireToolInputs'): continue
            q = p + '.' + k
            if any(x in k for x in KEYS):
                paths[(top, q)] += 1
                if (top, q) not in ex: ex[(top, q)] = (type(v).__name__, str(v)[:120] if not isinstance(v, (dict, list)) else list(v)[:8] if isinstance(v, dict) else len(v))
            walk(v, q, top)
    elif isinstance(o, list):
        for x in o[:50]: walk(x, p + '[]', top)
for f in glob.glob(ROOT + '/**/*.jsonl', recursive=True):
    for line in open(f, errors='ignore'):
        if not any(k in line for k in KEYS): continue
        try: j = json.loads(line)
        except Exception: continue
        walk(j, '', (j.get('type'), j.get('subtype')))
for k, v in paths.most_common(40): print(v, k, ex[k])
