#!/bin/bash
# usage: cx_probe.sh name [codex -c args...]
S=/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/A
name=$1; shift
d=$S/cxruns/$name; mkdir -p $d; cd $d
codex exec --skip-git-repo-check "$@" "Reply with exactly: OK" < /dev/null > $d.out 2>&1
f=$(grep -rl "\"cwd\":\"$d\"" ~/.codex/sessions/2026/11/ 2>/dev/null | xargs ls -t 2>/dev/null | head -1)
python3 - "$f" "$name" <<'PY'
import json,sys,re
first=None; dev=0; skills=0; n=0
for l in open(sys.argv[1]):
    d=json.loads(l); p=d.get('payload') or {}
    if d.get('type')=='response_item' and p.get('type')=='message' and p.get('role')=='developer':
        txt=''.join(c.get('text','') for c in p.get('content',[]) if isinstance(c,dict)); dev+=len(txt)
        m=re.search(r'<skills_instructions>(.*?)</skills_instructions>',txt,re.S)
        if m: skills=len(m.group(1)); n=len(re.findall(r'^- ',m.group(1),re.M))
    if d.get('type')=='event_msg' and p.get('type')=='token_count' and p.get('info') and first is None:
        first=p['info']['last_token_usage']['input_tokens']
print(json.dumps({'name':sys.argv[2],'first_input':first,'developer_chars':dev,'skills_chars':skills,'skill_entries':n}))
PY
