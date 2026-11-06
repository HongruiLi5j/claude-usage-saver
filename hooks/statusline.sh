#!/bin/sh
python3 -c '
import json,sys,os,time
S=os.path.expanduser("~/Documents/Projects/claude-usage-saver/state")
now=time.time()
# fail-open（D2/SL03）：stdin 损坏或字段类型不对时不打 traceback、退出码 0，只输出这一行兜底；也不改写 state 里的文件
FALLBACK="? | ctx ? | 5h ? | 7d ? | 状态栏输入无法解析"
def w(name,obj):
    try:
        os.makedirs(S,exist_ok=True)
        t=os.path.join(S,name+".%d.tmp"%os.getpid()); open(t,"w").write(json.dumps(obj,ensure_ascii=False)); os.replace(t,os.path.join(S,name))
    except Exception: pass
def rd(p):
    try: return json.load(open(p))
    except Exception: return {}
def main(d):
    c=(d.get("context_window") or {}).get("current_usage") or {}
    n=sum(c.get(k,0) for k in ("input_tokens","cache_creation_input_tokens","cache_read_input_tokens"))
    rl=d.get("rate_limits") or {}
    # 合并写 rate.json：别的会话（或旧会话）的读数不能把更新、更大的读数冲掉（SL02/SL03）
    old=rd(os.path.join(S,"rate.json")); orl=old.get("rate_limits") or {}
    # 每个窗口自带 sampled_at = 这个读数最后一次变化（或首次出现）的时间；守卫按它判陈旧（R4D/Codex 10）。
    # 同一窗口、同样数值的重复 payload 不刷新 sampled_at：空闲会话反复带着上次的旧读数时，读数会正常变旧。
    merged={}
    for k in ("five_hour","seven_day"):
        a=rl.get(k) or {}; b=orl.get(k) or {}
        if a.get("resets_at") and a["resets_at"]<=now: a={}           # 本会话 payload 的读数所属窗口已过期（空闲会话带着上次的旧读数）：不当新数据（R4D/SL07）
        if b and (b.get("resets_at") or 0)<=now: b={}                 # 旧窗口已重置，作废
        if b and not b.get("sampled_at"): b=dict(b,sampled_at=old.get("ts",0))   # 旧格式：用顶层 ts
        if a.get("used_percentage") is None: pick=b
        elif not b: pick=dict(a,sampled_at=int(now))
        elif (a.get("resets_at") or 0)>(b.get("resets_at") or 0)+60: pick=dict(a,sampled_at=int(now))   # 新窗口
        elif (b.get("resets_at") or 0)>(a.get("resets_at") or 0)+60: pick=b
        elif a["used_percentage"]>(b.get("used_percentage") or 0): pick=dict(a,sampled_at=int(now))     # 同窗口、读数变大
        else: pick=b                                                   # 同窗口、相同或更小：保留旧读数和它的采样时间
        if pick: merged[k]=pick
    ts=max([int(v.get("sampled_at") or 0) for v in merged.values()]+[0]) or old.get("ts",0)   # 顶层 ts 只为兼容旧读法
    line_head="%s | ctx %dk" % ((d.get("model") or {}).get("display_name","?"), n//1000)   # 先把会出错的格式化做完，再写 state
    def pct(k):
        v=(merged.get(k) or {}).get("used_percentage")
        return "?" if v is None else "%d%%"%round(v)
    line=line_head+" | 5h %s | 7d %s" % (pct("five_hour"), pct("seven_day"))
    w("rate.json",{"ts":ts,"rate_limits":merged,"ctx_tokens":n,"model":(d.get("model") or {}).get("id")})
    if not os.path.exists(os.path.join(S,"last_status_sample.json")): w("last_status_sample.json",d)
    # ⚠ 用本会话自己的软线：本会话 cwd × 全账户额度档倍数（SL05）；阈值算法和守卫共用 context_guard.lines()（R4D/Codex 11）
    hint=""
    try:
        sys.path.insert(0,os.path.expanduser("~/Documents/Projects/claude-usage-saver/hooks"))
        import context_guard as G
        m=rd(os.path.join(S,"mode.json"))
        cwd=d.get("cwd") or (d.get("workspace") or {}).get("current_dir") or ""
        mult=m.get("mult",1.0) if now-m.get("ts",0)<6*3600 else 1.0
        soft,strong,_=G.lines(cwd,mult)
        if n>=strong: hint=" | ⚠⚠ 很长了：等我写完交接就 /clear"
        elif n>=soft: hint=" | ⚠ 到线：做完这步就 /clear"
    except Exception: pass
    # ❄ 缓存已冷：下一条消息会把整段上下文重写一遍（只显示，不注入对话）
    try:
        pc=d.get("prompt_cache") or {}
        cold=pc.get("warm") is False or (pc.get("expires_at") and pc["expires_at"]<now)
        if cold and n>=20000:
            hint+=" | ❄ 缓存已冷：下条消息重写约 %dk，换任务就先 /clear" % ((pc.get("recache_tokens_if_cold") or n)//1000)
    except Exception: pass
    return line+hint
try:
    d=json.load(sys.stdin)
    if not isinstance(d,dict): raise ValueError("payload 不是 JSON 对象")
    out=main(d)
except Exception:
    out=FALLBACK
print(out)
sys.exit(0)
'
