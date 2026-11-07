import pickle,collections,datetime as dt
R=pickle.load(open('r5.pkl','rb'))
CH=dt.datetime(2026,11,6,7,58,tzinfo=dt.timezone.utc); FROM=dt.datetime(2026,11,4,8,tzinfo=dt.timezone.utc)
byf=collections.defaultdict(list)
for r in R['req']: byf[r['f']].append(r)
def sim(rs,cap,reexp,base_after=45000):
    rs=sorted(rs,key=lambda r:r['T'])
    w=sum(r['cost_cw'] for r in rs)/max(sum(r['cw_raw'] for r in rs),1) or 1.25
    cost=0; ctx=None; prev=None; ncomp=0
    for r in rs:
        if prev is None: inc=r['ctx']; ctx=0
        else:
            inc=r['ctx']-prev['ctx']
            if inc<0: ctx=0; inc=r['ctx']        # 实际压缩：按实际重建
        prev=r
        if ctx+inc>cap and ctx>0:
            ncomp+=1
            cost+=ctx*0.1+5000*5                 # 压缩请求：读一遍 + 写摘要
            ctx=0; inc=base_after+reexp+max(inc,0)
        cost+=ctx*0.1+inc*w+r['cost_out']+r['cost_inp']
        ctx+=inc
    return cost,ncomp
for nm,lo,sel in (('3天-主会话',FROM,'main'),('3天-子agent',FROM,'sub'),('启用后-主会话',CH,'main'),('启用后-子agent',CH,'sub')):
    groups={f:[r for r in rs if r['T']>=lo and r['side']==sel] for f,rs in byf.items()}
    groups={f:v for f,v in groups.items() if v}
    actual=sum(r['cost'] for v in groups.values() for r in v)
    base=sum(sim(v,10**9,0)[0] for v in groups.values())
    out=[f"{nm}: 实际 {actual/1e6:.1f}M, 模型无上限 {base/1e6:.1f}M"]
    for cap in (300e3,200e3,150e3,120e3):
        for re_ in (0,30000):
            s=[sim(v,cap,re_) for v in groups.values()]
            c=sum(x[0] for x in s); n=sum(x[1] for x in s)
            out.append(f"  cap {cap/1e3:.0f}k 重探{re_//1000}k: {c/1e6:.1f}M ({100*(c-base)/base:+.0f}%, 压缩 {n} 次)")
    print('\n'.join(out))
