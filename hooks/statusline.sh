#!/bin/sh
# CG13（R5I）：套件目录按本脚本位置定（hooks/ 的上一级）；state = $USAGE_SAVER_STATE，没有就用 套件/state，
# 和 context_guard.py 同一规则，所以这里写的 rate.json 就是守卫读的那个。拿不到脚本目录时退回旧的固定路径。
# R6P：共享状态（rate.json、ctx_window.json、rate_history.jsonl）的读-改-写全程持 state/.state.lock（最多等 80ms，
#      拿不到就这次不写、下次刷新再写），写入一律「临时文件 + rename」；节约表（hooks/savings_meter.py）有自己的锁。
USAGE_SAVER_HOOKS_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd) || USAGE_SAVER_HOOKS_DIR=
export USAGE_SAVER_HOOKS_DIR
python3 -c '
import json,sys,os,time
H=os.environ.get("USAGE_SAVER_HOOKS_DIR") or ""
S=os.environ.get("USAGE_SAVER_STATE") or (os.path.join(os.path.dirname(H),"state") if H and os.path.isfile(os.path.join(H,"context_guard.py")) else os.path.expanduser("~/Documents/Projects/claude-usage-saver/state"))
now=time.time()
# fail-open（D2/SL03）：stdin 损坏或字段类型不对时不打 traceback、退出码 0，只输出这一行兜底；也不改写 state 里的文件
FALLBACK="? | ctx ? | 5h ? | 7d ? | 状态栏输入无法解析"
RH_MAX=2*1024*1024        # rate_history.jsonl 超过 2MB 就轮转成 .1（只留一份旧的）
def w(name,obj):
    try:
        os.makedirs(S,exist_ok=True)
        t=os.path.join(S,name+".%d.tmp"%os.getpid()); open(t,"w").write(json.dumps(obj,ensure_ascii=False)); os.replace(t,os.path.join(S,name))
    except Exception: pass
def rd(p):
    try: return json.load(open(p))
    except Exception: return {}
class L:
    # R6P：flock 互斥锁，非阻塞轮询，最多等 wait 秒；拿不到 ok=False（调用方跳过写入）。关闭文件即释放。
    def __init__(s,wait=0.08): s.wait=wait; s.fd=None; s.ok=False
    def __enter__(s):
        try:
            import fcntl
            os.makedirs(S,exist_ok=True)
            s.fd=os.open(os.path.join(S,".state.lock"),os.O_RDWR|os.O_CREAT,0o644); end=time.time()+s.wait
            while True:
                try: fcntl.flock(s.fd,fcntl.LOCK_EX|fcntl.LOCK_NB); s.ok=True; break
                except OSError:
                    if time.time()>=end: break
                    time.sleep(0.002)
        except Exception: s.ok=False
        return s
    def __exit__(s,*a):
        try:
            if s.fd is not None: os.close(s.fd)
        except Exception: pass
def merge(rl,old):
    orl=old.get("rate_limits") or {}
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
    return merged
def last_line(p):
    try:
        with open(p,"rb") as f:
            f.seek(0,2); n=f.tell(); f.seek(max(0,n-4096)); t=f.read().decode("utf-8","replace").strip().splitlines()
        return json.loads(t[-1]) if t else {}
    except Exception: return {}
def rate_history(merged,d):
    # R6P ⑤：额度读数（合并后的账户级读数）变化时追加一行，给以后重估「额度 % ↔ token」折算用。调用方持锁。
    try:
        fh=merged.get("five_hour") or {}; sd=merged.get("seven_day") or {}
        if fh.get("used_percentage") is None and sd.get("used_percentage") is None: return
        rec={"ts":int(now),"five_hour":fh.get("used_percentage"),"five_hour_resets_at":fh.get("resets_at"),
             "seven_day":sd.get("used_percentage"),"seven_day_resets_at":sd.get("resets_at"),
             "model":(d.get("model") or {}).get("id"),"session_id":d.get("session_id")}
        p=os.path.join(S,"rate_history.jsonl"); key=("five_hour","five_hour_resets_at","seven_day","seven_day_resets_at")
        last=last_line(p)
        if all(last.get(k)==rec[k] for k in key): return                # 去重：和上一行同样的读数不写
        if os.path.exists(p) and os.path.getsize(p)>RH_MAX: os.replace(p,p+".1")
        with open(p,"a") as f: f.write(json.dumps(rec,ensure_ascii=False)+"\n")
    except Exception: pass
def main(d):
    cw_=d.get("context_window") or {}
    c=cw_.get("current_usage") or {}
    n=sum(c.get(k,0) for k in ("input_tokens","cache_creation_input_tokens","cache_read_input_tokens"))
    win=cw_.get("context_window_size")
    win=win if isinstance(win,(int,float)) and not isinstance(win,bool) and win>0 else None
    up=cw_.get("used_percentage")
    if not c and win and isinstance(up,(int,float)) and not isinstance(up,bool):
        n=int(win*up/100)                                              # R6P（H §4）：没有 current_usage 时用 payload 的 used_percentage × 窗口
    rl=d.get("rate_limits") or {}
    line_head="%s | ctx %dk" % ((d.get("model") or {}).get("display_name","?"), n//1000)   # 先把会出错的格式化做完，再写 state
    if win: line_head+="/"+("%gM"%(win/1e6) if win>=1e6 else "%dk"%(win//1000))
    # 合并写 rate.json：别的会话（或旧会话）的读数不能把更新、更大的读数冲掉（SL02/SL03）；R6P：读-改-写全程持锁
    with L() as lk:
        old=rd(os.path.join(S,"rate.json")); merged=merge(rl,old)
        ts=max([int(v.get("sampled_at") or 0) for v in merged.values()]+[0]) or old.get("ts",0)   # 顶层 ts 只为兼容旧读法
        def pct(k):
            v=(merged.get(k) or {}).get("used_percentage")
            return "?" if v is None else "%d%%"%round(v)
        line=line_head+" | 5h %s | 7d %s" % (pct("five_hour"), pct("seven_day"))   # 格式化先做完（出错就走兜底、不写 state）
        if lk.ok:
            w("rate.json",{"ts":ts,"rate_limits":merged,"ctx_tokens":n,"model":(d.get("model") or {}).get("id")})
            rate_history(merged,d)
            if not os.path.exists(os.path.join(S,"last_status_sample.json")): w("last_status_sample.json",d)
    # ⚠ 用本会话自己的软线：本会话 cwd × 全账户额度档倍数（SL05）；阈值算法和守卫共用 context_guard.lines()（R4D/Codex 11）
    hint=""
    try:
        sys.path.insert(0,H if H and os.path.isfile(os.path.join(H,"context_guard.py")) else os.path.expanduser("~/Documents/Projects/claude-usage-saver/hooks"))
        import context_guard as G
        m=rd(os.path.join(S,"mode.json"))
        cwd=d.get("cwd") or (d.get("workspace") or {}).get("current_dir") or ""
        mult=m.get("mult",1.0) if now-m.get("ts",0)<6*3600 else 1.0
        sid=d.get("session_id")
        if win and isinstance(sid,str) and sid:
            # R5I：记下本会话的上下文窗口，守卫按 session_id 读（小窗口模型、不认识的新模型也能在压缩前提醒）
            # R6P（Codex 复核 I 阻塞项 1）：读-改-写全程持锁、锁内重读，并发的状态栏不再互相冲掉别的会话的记录
            cw=rd(os.path.join(S,"ctx_window.json")); cw=cw if isinstance(cw,dict) else {}
            if (cw.get(sid) or {}).get("w")!=win or now-((cw.get(sid) or {}).get("ts") or 0)>86400:
                with L() as lk:
                    if lk.ok:
                        cw=rd(os.path.join(S,"ctx_window.json")); cw=cw if isinstance(cw,dict) else {}
                        cw={k:v for k,v in cw.items() if isinstance(v,dict) and now-(v.get("ts") or 0)<7*86400}
                        cw[sid]={"w":int(win),"ts":int(now)}
                        if len(cw)>300: cw=dict(sorted(cw.items(),key=lambda kv:kv[1].get("ts",0))[-300:])
                        w("ctx_window.json",cw)
        soft,strong,_=G.lines(cwd,mult,window=win)
        if n>=strong: hint=" | ⚠⚠ 很长了：等我写完交接就 /clear"
        elif n>=soft: hint=" | ⚠ 到线：做完这步就 /clear"
        # 守卫读不到 usage（M 的 usage_unreadable）：mode.json 是「最后一个提交消息的会话」写的，
        # 只有它明确记了 session_id 且等于本会话时才显示，否则会串到别的会话（现在的 mode.json 没记 session_id → 不显示）
        if m.get("usage_unreadable") is True and isinstance(sid,str) and sid and m.get("session_id")==sid:
            hint+=" | ⚠ 守卫读不到本会话 usage，不会按上下文提醒"
    except Exception: pass
    # ❄ 缓存已冷：下一条消息会把整段上下文重写一遍（只显示，不注入对话）
    try:
        pc=d.get("prompt_cache") or {}
        cold=pc.get("warm") is False or (pc.get("expires_at") and pc["expires_at"]<now)
        if cold and n>=20000:
            hint+=" | ❄ 缓存已冷：下条消息重写约 %dk，换任务就先 /clear" % ((pc.get("recache_tokens_if_cold") or n)//1000)
    except Exception: pass
    # 省≈X%：节约表（R6P，hooks/savings_meter.py）。缺文件、出错、超时都只是不显示这一段
    sv=""
    try:
        if H and os.path.isfile(os.path.join(H,"savings_meter.py")) and os.environ.get("USAGE_SAVER_METER","1")!="0":
            sys.path.insert(0,H)
            import savings_meter as SM
            sv=SM.render(SM.update(d,S,now))
    except Exception: sv=""
    return line+hint+sv
try:
    d=json.load(sys.stdin)
    if not isinstance(d,dict): raise ValueError("payload 不是 JSON 对象")
    out=main(d)
except Exception:
    out=FALLBACK
print(out)
sys.exit(0)
'
