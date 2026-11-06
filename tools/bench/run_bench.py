import json,os,subprocess,sys,time,shutil,re,statistics
HOME=os.path.expanduser('~')
BASE=HOME+'/Documents/Projects/claude-usage-saver'
SRC=HOME+'/Documents/Projects/其他/exports/quant-backtest-collab'   # 静态原件，只读，每次 clone
WORK=os.environ.get('BENCH_WORK', os.path.join(os.environ.get('TMPDIR','/tmp'),'bench_work'))
PY=HOME+'/Documents/Projects/quant-backtest/.venv/bin/python'
OUT=BASE+'/tools/bench/results'
os.makedirs(OUT,exist_ok=True); os.makedirs(WORK,exist_ok=True)
STOP_PCT=float(os.environ.get('BENCH_STOP_PCT',92)); MAX_DELTA=float(os.environ.get('BENCH_MAX_DELTA',12))
RUN_CMD=f'PYTHONPATH=. {PY}'
def week_used():
    try: return json.load(open(BASE+'/state/rate.json'))['rate_limits']['seven_day']['used_percentage']
    except Exception: return None
def sh(cmd,cwd,t=300):
    p=subprocess.run(cmd,cwd=cwd,shell=True,capture_output=True,text=True,timeout=t); return p.returncode,p.stdout+p.stderr
def sub(path,old,new):
    s=open(path).read(); assert old in s,(path,old); open(path,'w').write(s.replace(old,new,1))
# ---------- 任务 ----------
def seedA(d): sub(d+'/src/portfolio.py','held = tgt.shift(1).fillna(0.0)','held = tgt.fillna(0.0)')
def seedB(d): sub(d+'/src/strategies.py','.rolling(entry_len).max().shift(1).to_numpy()','.rolling(entry_len).max().to_numpy()')
def gradeA(d,ans):
    rc1,o1=sh(f'{RUN_CMD} tests/test_strategies.py',d); rc2,o2=sh(f'{RUN_CMD} tests/test_basic.py',d)
    _,df=sh('git diff --stat',d)
    ok=rc1==0 and rc2==0 and 'Traceback' not in o1+o2 and 'src/portfolio.py' in df and 'tests/' not in df
    return int(ok),df.strip()[-120:]
def gradeB(d,ans):
    _,df=sh('git status --short',d)
    ok=bool(re.search(r'\b80\b',ans)) and ('strategies.py' in ans) and not df.strip().startswith(('M','A'))
    return int(ok),'modified' if df.strip() else 'readonly'
def gradeC(d,ans): return int('0.0005' in ans and 'band' in ans.lower()),''
GRADE_D='''
import pandas as pd
from src.metrics import max_drawdown_duration as f
assert f(pd.Series([1,2,1.5,1.2,2.1,1.9,2.2]))==2
assert f(pd.Series([1,2,3]))==0
assert f(pd.Series([3,2,1,2,2.5]))==4
print("HIDDEN_OK")
'''
def gradeD(d,ans):
    open(d+'/_hidden.py','w').write(GRADE_D)
    rc,o=sh(f'{RUN_CMD} _hidden.py',d); rc2,o2=sh(f'{RUN_CMD} tests/test_basic.py',d)
    return int(rc==0 and 'HIDDEN_OK' in o and rc2==0),o[-80:]
TASKS={
 'A_bugfix':dict(seed=seedA,grade=gradeA,prompt=f'tests/test_strategies.py 里有测试失败了。请找出根因并在 src/ 里修复（不要修改 tests/）。用这条命令跑测试：{RUN_CMD} tests/test_strategies.py ；修好后确认 tests/test_basic.py 也通过。最后用不超过 5 行说明根因。'),
 'B_lookahead_review':dict(seed=seedB,grade=gradeB,prompt='请审查 src/strategies.py 是否存在未来函数（用到决策时点还拿不到的信息）。每个发现写成 文件:行号 + 一句话原因。只读，不要修改任何文件。'),
 'C_lookup':dict(seed=None,grade=gradeC,prompt='src/portfolio.py 的 run_weights 默认的交易成本参数是多少？band 参数是做什么的？各用一句话回答，并给出行号。'),
 'D_feature':dict(seed=None,grade=gradeD,prompt=f'在 src/metrics.py 新增函数 max_drawdown_duration(equity: pd.Series) -> int：返回净值序列中"连续处于历史最高点之下的最长根数"（创新高当根不算在内；单调上升返回 0）。并在 tests/test_basic.py 加一个测试。用 {RUN_CMD} tests/test_basic.py 验证。'),
}
ARMS={
 'baseline_opus':['--model','opus','--disable-slash-commands','--setting-sources','project'],
 'new_sonnet':['--model','sonnet'],
}
def run_one(task,arm,rep,smoke=False):
    d=f'{WORK}/{task}_{arm}_{rep}'; shutil.rmtree(d,ignore_errors=True)
    subprocess.run(['git','clone','-q',SRC,d],check=True)
    if TASKS[task]['seed']:
        TASKS[task]['seed'](d)
        if task=='A_bugfix':
            rc,_=sh(f'{RUN_CMD} tests/test_strategies.py',d)
            assert rc!=0,'埋入的 bug 没有让测试失败，种子无效'
        sh('git add -A && git -c user.email=a@b -c user.name=bench commit -qm seeded',d)
    cmd=['claude','-p',TASKS[task]['prompt'],'--output-format','json','--permission-mode','acceptEdits',
         '--allowedTools','Bash,Read,Edit,Write,Glob,Grep,Agent','--max-budget-usd','2' if smoke else '4']+ARMS[arm]
    t0=time.time()
    try: p=subprocess.run(cmd,cwd=d,capture_output=True,text=True,timeout=1200); raw=p.stdout
    except subprocess.TimeoutExpired: raw='{}'
    try: j=json.loads(raw)
    except Exception: j={'_parse_error':raw[-300:]}
    ans=str(j.get('result',''))
    ok,note=TASKS[task]['grade'](d,ans)
    mu=j.get('modelUsage') or {}
    return dict(task=task,arm=arm,rep=rep,pass_=ok,note=note,cost_usd=j.get('total_cost_usd'),turns=j.get('num_turns'),
        dur_s=round(time.time()-t0),usage=j.get('usage'),models={k:round(v.get('costUSD',0),4) for k,v in mu.items()},
        err=j.get('_parse_error'),is_error=j.get('is_error'))
def main():
    smoke='--smoke' in sys.argv
    reps=int(os.environ.get('BENCH_REPS',2))
    plan=[('C_lookup','new_sonnet',0)] if smoke else [(t,a,r) for r in range(reps) for t in TASKS for a in ARMS]
    start=week_used(); res=[]; ts=time.strftime('%Y%m%d-%H%M%S')
    print('周用量起点',start,flush=True)
    for (t,a,r) in plan:
        u=week_used()
        if u is not None and (u>=STOP_PCT or (start is not None and u-start>=MAX_DELTA)):
            print(f'预算保护触发：周用量 {u}%（起点 {start}%），停止',flush=True); break
        x=run_one(t,a,r,smoke); x['week_used_after']=week_used(); res.append(x)
        print(json.dumps(x,ensure_ascii=False)[:400],flush=True)
        json.dump(dict(start=start,results=res),open(f'{OUT}/bench_{ts}{"_smoke" if smoke else ""}.json','w'),ensure_ascii=False,indent=1)
    print('完成',len(res),'次；周用量',start,'->',week_used())
main()
