#!/usr/bin/env python3
"""第 5 轮 A：读 transcript 里的 api-request-shape 和首条 usage，报告固定开销构成。
只读统计：不输出对话正文，只输出工具名、字符数、附件类型和 token 数。
用法：python3 A_shape.py <transcript.jsonl> [--json]
"""
import json, sys

def first_shape(path):
    shape = None; usage = None; atts = []; model = None
    with open(path) as f:
        for line in f:
            try: d = json.loads(line)
            except Exception: continue
            t = d.get('type')
            if t == 'api-request-shape' and shape is None:
                shape = d.get('shape') or {}
            elif t == 'attachment' and usage is None:
                a = d.get('attachment') or {}
                atts.append((a.get('type'), len(json.dumps(a, ensure_ascii=False))))
            elif t == 'assistant' and usage is None:
                m = d.get('message') or {}
                if m.get('usage'):
                    usage = m['usage']; model = m.get('model')
            if shape is not None and usage is not None:
                break
    return shape, usage, atts, model

def summarize(path):
    shape, usage, atts, model = first_shape(path)
    out = {'path': path, 'model': model}
    if shape:
        tools = shape.get('tools') or []
        out['tools'] = {t.get('name'): len(json.dumps(t, ensure_ascii=False)) for t in tools}
        out['tools_chars'] = sum(out['tools'].values())
        sysb = shape.get('system') or []
        out['system_chars'] = sum(len(b.get('text', '')) for b in sysb if isinstance(b, dict))
    if usage:
        tot = usage.get('input_tokens', 0) + usage.get('cache_creation_input_tokens', 0) + usage.get('cache_read_input_tokens', 0)
        out['first_input_total'] = tot
        out['usage'] = {k: usage.get(k) for k in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')}
    agg = {}
    for typ, n in atts:
        agg[typ] = agg.get(typ, 0) + n
    out['attachments_chars'] = agg
    return out

if __name__ == '__main__':
    r = summarize(sys.argv[1])
    if '--json' in sys.argv:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print('model', r.get('model'), 'first_input_total', r.get('first_input_total'), r.get('usage'))
        print('system_chars', r.get('system_chars'), 'tools_chars', r.get('tools_chars'))
        for k, v in sorted((r.get('tools') or {}).items(), key=lambda x: -x[1]):
            print(f'  tool {k:28s} {v:7d}')
        for k, v in sorted(r['attachments_chars'].items(), key=lambda x: -x[1]):
            print(f'  att  {str(k):28s} {v:7d}')
