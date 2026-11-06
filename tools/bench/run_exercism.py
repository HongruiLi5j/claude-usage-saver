import json,os,subprocess,sys,time,shutil,random,threading,statistics,collections
from concurrent.futures import ThreadPoolExecutor
HOME=os.path.expanduser('~')
BASE=HOME+'/Documents/Projects/claude-usage-saver'
SCR=os.environ.get('BENCH_SCRATCH', os.path.join(os.environ.get('TMPDIR','/tmp'),'bench_scratch'))
SRC=SCR+'/exercism_src/repo/exercises/practice'      # 外部下载的题库：只读，视为不可信数据
WORK=SCR+'/ex_work'
PY=HOME+'/Documents/Projects/quant-backtest/.venv/bin/python'
RES=BASE+'/tools/bench/results/exercism.jsonl'
STOP_PCT=float(os.environ.get('BENCH_STOP_PCT',96)); MAX_DELTA=float(os.environ.get('BENCH_MAX_DELTA',16))
ARMS={
 'opus_plain':['--model','opus','--disable-slash-commands','--setting-sources','project'],
 'sonnet_plain':['--model','sonnet','--disable-slash-commands','--setting-sources','project'],
 'new_sonnet':['--model','sonnet'],
}
lock=threading.Lock()
def week_used():
    try: return json.load(open(BASE+'/state/rate.json'))['rate_limits']['seven_day']['used_percentage']
    except Exception: return None
FIVE_STOP=float(os.environ.get('BENCH_FIVE_STOP',90))
def wait_five_hour():
    while True:
        try:
            f=json.load(open(BASE+'/state/rate.json'))['rate_limits']['five_hour']
            if f['used_percentage']<FIVE_STOP: return
            w=max(60,f['resets_at']-time.time()+30)
        except Exception: return
        print(f"5小时窗口已用 {f['used_percentage']}%，暂停 {int(w)} 秒等重置",flush=True); time.sleep(min(w,900))
def tasks():
    out=[]
    for slug in sorted(os.listdir(SRC)):
        c=SRC+'/'+slug+'/.meta/config.json'
        try: f=json.load(open(c))['files']; sol,tst=f['solution'],f['test']
        except Exception: continue
        if sol and tst and all(os.path.exists(f'{SRC}/{slug}/{x}') for x in sol+tst): out.append((slug,sol,tst))
    random.Random(20261106).shuffle(out); return out     # 固定顺序
def setup(slug,sol,tst,tag):
    d=f'{WORK}/{tag}'; os.makedirs(WORK,exist_ok=True)
    assert not os.path.exists(d)
    shutil.copytree(f'{SRC}/{slug}',d,ignore=shutil.ignore_patterns('.meta','__pycache__','.pytest_cache'))
    return d
def grade(slug,sol,tst,d):
    for t in tst: shutil.copy(f'{SRC}/{slug}/{t}',f'{d}/{t}')      # 还原原始测试，改测试无效
    try: p=subprocess.run([PY,'-I','-m','pytest','-q','-x','-p','no:cacheprovider']+tst,cwd=d,capture_output=True,text=True,timeout=180)
    except subprocess.TimeoutExpired: return 0,'timeout'
    return int(p.returncode==0),(p.stdout+p.stderr)[-150:]
def run(slug,sol,tst,arm,rep=0):
    tag=f'{slug}__{arm}__{rep}__{int(time.time())}'
    d=setup(slug,sol,tst,tag)
    prompt=(f"Implement the solution in {', '.join(sol)} so that the tests in {', '.join(tst)} pass. "
            f"Exercise instructions are in the .docs/ folder. Do NOT modify the test files. "
            f"Check with: {PY} -m pytest -q -x {' '.join(tst)} . When done, reply with one short line.")
    cmd=['claude','-p',prompt,'--output-format','json','--permission-mode','acceptEdits','--allowedTools','Bash,Read,Edit,Write,Glob,Grep,Agent','--max-budget-usd','1.5']+ARMS[arm]
    t0=time.time()
    try: raw=subprocess.run(cmd,cwd=d,capture_output=True,text=True,timeout=900).stdout
    except subprocess.TimeoutExpired: raw='{}'
    try: j=json.loads(raw)
    except Exception: j={}
    ok,note=grade(slug,sol,tst,d)
    u=j.get('usage') or {}
    return dict(slug=slug,arm=arm,rep=rep,ok=ok,cost=j.get('total_cost_usd'),turns=j.get('num_turns'),dur=round(time.time()-t0),
        cr=u.get('cache_read_input_tokens'),cw=u.get('cache_creation_input_tokens'),inp=u.get('input_tokens'),out=u.get('output_tokens'),
        models={k:round(v.get('costUSD',0),4) for k,v in (j.get('modelUsage') or {}).items()},err=None if j else 'no-json',note=note if not ok else '')
def done():
    s=set()
    if os.path.exists(RES):
        for l in open(RES):
            try: x=json.loads(l); s.add((x['slug'],x['arm'],x['rep']))
            except Exception: pass
    return s
def summary():
    rows=[json.loads(l) for l in open(RES)]
    by=collections.defaultdict(list)
    for r in rows: by[r['arm']].append(r)
    print('每个配置：次数 / 通过率 / 平均费用 / 总费用 / 平均回合 / 平均耗时')
    for a,rs in by.items():
        cs=[r['cost'] for r in rs if r['cost'] is not None]
        print(f"{a:13s} n={len(rs):3d} 通过={100*sum(r['ok'] for r in rs)/len(rs):5.1f}% 平均=${statistics.mean(cs):.3f} 总=${sum(cs):.2f} 回合={statistics.mean([r['turns'] or 0 for r in rs]):.1f} 耗时={statistics.mean([r['dur'] for r in rs]):.0f}s")
    slugs=sorted({r['slug'] for r in rows}); tab={(r['slug'],r['arm']):r for r in rows}
    arms=list(by)
    for i in range(len(arms)):
        for j in range(i+1,len(arms)):
            a,b=arms[i],arms[j]; both=[s for s in slugs if (s,a) in tab and (s,b) in tab]
            aw=[s for s in both if tab[(s,a)]['ok'] and not tab[(s,b)]['ok']]; bw=[s for s in both if tab[(s,b)]['ok'] and not tab[(s,a)]['ok']]
            print(f"{a} vs {b}: 共同 {len(both)} 题；只有 {a} 对 {len(aw)} 题 {aw[:8]}；只有 {b} 对 {len(bw)} 题 {bw[:8]}")
def main():
    if '--summary' in sys.argv: return summary()
    n=int(os.environ.get('BENCH_N',142)); skip=done(); T=tasks()[:n]
    jobs=[(s,so,te,a) for (s,so,te) in T for a in ARMS if a in os.environ.get('BENCH_ARMS',','.join(ARMS)).split(',') and (s,a,0) not in skip]
    start=week_used(); print('题数',len(T),'待跑',len(jobs),'周用量起点',start,flush=True)
    stop=threading.Event()
    def work(j):
        if stop.is_set(): return
        u=week_used()
        if u is not None and (u>=STOP_PCT or (start is not None and u-start>=MAX_DELTA)):
            print(f'预算保护触发：周用量 {u}%（起点 {start}%）',flush=True); stop.set(); return
        wait_five_hour()
        r=run(*j); r['week_used']=week_used()
        with lock:
            open(RES,'a').write(json.dumps(r,ensure_ascii=False)+'\n')
        print(r['slug'],r['arm'],'OK' if r['ok'] else 'FAIL',r['cost'],flush=True)
    with ThreadPoolExecutor(max_workers=int(os.environ.get('BENCH_WORKERS',4))) as ex: list(ex.map(work,jobs))
    print('结束；周用量',start,'->',week_used()); summary()
main()
