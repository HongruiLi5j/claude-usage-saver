import json,os,sys,datetime as dt
sys.path.insert(0,os.path.dirname(__file__))
import replay_guard as R
BASE=os.path.expanduser('~/Documents/Projects/claude-usage-saver')
CFG=BASE+'/config.json'; HIST=BASE+'/reports/history.json'
LAMBDA=300000   # 质量优先：每次打断成本调高
apply='--apply' in sys.argv
b,_,_=R.sim(None)
res=[]
for T in range(120000,320000,20000):
    _,s,r=R.sim(T); res.append((T,s,r,s+LAMBDA*r))
best=min(res,key=lambda x:x[3])
cfg=json.load(open(CFG)); old=cfg.get('handoff_threshold_tokens',120000)
oldres=min(res,key=lambda x:abs(x[0]-old))
hist=json.load(open(HIST)) if os.path.exists(HIST) else []
prev=hist[-1] if hist else None
now=dt.datetime.now().strftime('%Y-%m-%d %H:%M')
entry=dict(ts=now,sessions=len(R.sessions),baseline=b,best_T=best[0],saving_pct=100*(1-best[1]/b),resets=best[2],old_T=old,old_saving_pct=100*(1-oldres[1]/b))
hist.append(entry); json.dump(hist,open(HIST,'w'),indent=1)
if apply and best[0]!=old: cfg['handoff_threshold_tokens']=best[0]; json.dump(cfg,open(CFG,'w'))
L=[f'# 优化报告 {now}','',f'- 样本：{len(R.sessions)} 个会话（10-21 至今），基线成本 {b/1e6:.1f}M 单位（按 API 价格比折算，不等于订阅额度）',
f'- 本轮最优阈值：**{best[0]//1000}k**，模拟节省 **{entry["saving_pct"]:.1f}%**，触发 /clear {best[2]} 次',
f'- 当前使用阈值：{old//1000}k，模拟节省 {entry["old_saving_pct"]:.1f}%',
('- 已自动更新阈值' if apply and best[0]!=old else '- 未更改阈值（已最优，或未加 --apply）')]
if prev: L.append(f'- 与上一轮（{prev["ts"]}）相比：节省 {prev["saving_pct"]:.1f}% → {entry["saving_pct"]:.1f}%，最优阈值 {prev["best_T"]//1000}k → {best[0]//1000}k')
L+=['','| 阈值 | 模拟节省 | /clear 次数 |','|---|---|---|']+[f'| {T//1000}k | {100*(1-s/b):.1f}% | {r} |' for T,s,r,_ in res]
L+=['','注意：这是回放模拟，假设每次交接额外约 8k token 且不影响质量；真实效果以周报前后对比为准。']
open(BASE+'/reports/latest.md','w').write('\n'.join(L)); print('\n'.join(L[:8]))
