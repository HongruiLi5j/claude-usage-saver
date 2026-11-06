#!/bin/sh
python3 -c '
import json,sys,os,time
d=json.load(sys.stdin)
S=os.path.expanduser("~/Documents/Projects/claude-usage-saver/state")
def w(name,obj):
    try:
        os.makedirs(S,exist_ok=True)
        t=os.path.join(S,name+".tmp"); open(t,"w").write(json.dumps(obj,ensure_ascii=False)); os.replace(t,os.path.join(S,name))
    except Exception: pass
c=(d.get("context_window") or {}).get("current_usage") or {}
n=sum(c.get(k,0) for k in ("input_tokens","cache_creation_input_tokens","cache_read_input_tokens"))
rl=d.get("rate_limits") or {}
w("rate.json",{"ts":int(time.time()),"rate_limits":rl,"ctx_tokens":n,"model":(d.get("model") or {}).get("id")})
if not os.path.exists(os.path.join(S,"last_status_sample.json")): w("last_status_sample.json",d)
def pct(k):
    v=(rl.get(k) or {}).get("used_percentage")
    return "?" if v is None else "%d%%"%round(v)
hint=""
try:
    m=json.load(open(os.path.join(S,"mode.json")))
    if n>=m.get("strong_line",10**9): hint=" | ⚠⚠ 很长了：等我写完交接就 /clear"
    elif n>=m.get("soft_line",10**9): hint=" | ⚠ 到线：做完这步就 /clear"
except Exception: pass
print("%s | ctx %dk | 5h %s | 7d %s" % ((d.get("model") or {}).get("display_name","?"), n//1000, pct("five_hour"), pct("seven_day"))+hint)
'
