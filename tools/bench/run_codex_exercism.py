import json,os,subprocess,sys,time,threading,collections
from concurrent.futures import ThreadPoolExecutor
src=open(os.path.dirname(os.path.abspath(__file__))+'/run_exercism.py').read().rsplit('\nmain()',1)[0]
ns={'__name__':'x'}; exec(compile(src,'run_exercism','exec'),ns)
tasks,setup,grade,PY,BASE=ns['tasks'],ns['setup'],ns['grade'],ns['PY'],ns['BASE']
RES=BASE+'/tools/bench/results/codex_exercism.jsonl'
EFFORT=os.environ.get('CODEX_EFFORT','high'); WORKERS=int(os.environ.get('BENCH_WORKERS',4))
LIMIT_WORDS=('usage limit','rate limit','quota','try again')
lock=threading.Lock(); stop=threading.Event(); fails=[0]
def done():
    s=set()
    if os.path.exists(RES):
        for l in open(RES):
            try: s.add(json.loads(l)['slug'])
            except Exception: pass
    return s
def one(slug,sol,tst):
    if stop.is_set(): return
    d=setup(slug,sol,tst,f'codex__{slug}__{int(time.time())}')
    prompt=(f"Implement the solution in {', '.join(sol)} so that the tests in {', '.join(tst)} pass. "
            f"Exercise instructions are in the .docs/ folder. Do NOT modify the test files. "
            f"Check with: {PY} -m pytest -q -x {' '.join(tst)} . When done, reply with one short line.")
    cmd=['codex','exec','--json','-s','workspace-write','--skip-git-repo-check','--ephemeral','-C',d,'-c',f'model_reasoning_effort="{EFFORT}"',prompt]
    t0=time.time()
    try: p=subprocess.run(cmd,capture_output=True,text=True,timeout=900,stdin=subprocess.DEVNULL); out=p.stdout+p.stderr; rc=p.returncode
    except subprocess.TimeoutExpired: out,rc='timeout',-1
    ok,note=grade(slug,sol,tst,d)
    low=out.lower()
    limited=(rc!=0 and any(w in low for w in LIMIT_WORDS) and not ok)
    if limited:
        fails[0]+=1
        if fails[0]>=3: stop.set(); print('疑似 Codex 额度用完，停止：',out[-200:],flush=True)
        return          # 不记录，续跑时会重做
    fails[0]=0
    tk=dict(inp=0,cached=0,out=0,reason=0,turns=0)
    for l in out.splitlines():
        try: e=json.loads(l)
        except Exception: continue
        if e.get('type')=='turn.completed':
            u=e.get('usage') or {}; tk['turns']+=1; tk['inp']+=u.get('input_tokens',0); tk['cached']+=u.get('cached_input_tokens',0); tk['out']+=u.get('output_tokens',0); tk['reason']+=u.get('reasoning_output_tokens',0)
    r=dict(slug=slug,arm='codex',ok=ok,rc=rc,**tk,dur=round(time.time()-t0),note=note if not ok else '')
    with lock: open(RES,'a').write(json.dumps(r,ensure_ascii=False)+'\n')
    print(slug,'OK' if ok else 'FAIL',r['dur'],flush=True)
if '--summary' in sys.argv:
    rs=[json.loads(l) for l in open(RES)]
    print('输入token',sum(r.get('inp',0) for r in rs),'缓存',sum(r.get('cached',0) for r in rs),'输出',sum(r.get('out',0) for r in rs))
    print('n=',len(rs),'通过=',sum(r['ok'] for r in rs),'平均耗时',sum(r['dur'] for r in rs)//max(1,len(rs)),'s')
    print('失败：',[r['slug'] for r in rs if not r['ok']]); sys.exit()
sk=done(); T=[t for t in tasks()[:int(os.environ.get('BENCH_N',142))] if t[0] not in sk]
print('待跑',len(T),'题，effort',EFFORT,flush=True)
with ThreadPoolExecutor(max_workers=WORKERS) as ex: list(ex.map(lambda t:one(*t),T))
print('结束'); 
