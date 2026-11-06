import json,os
BASE=os.path.expanduser('~/Documents/Projects/claude-usage-saver')
HANDOFF=8000
W=dict(cr=0.1,cw=1.25,inp=1.0,out=5.0)
def cost(cr,cw,inp,out): return cr*W['cr']+cw*W['cw']+inp*W['inp']+out*W['out']
sessions=json.load(open(BASE+'/state/frozen.json'))['sessions']
def sim(T,handoff=HANDOFF):
    base_total=sim_total=0; resets=0
    for rows in sessions:
        base=rows[0][1]+rows[0][2]+rows[0][3]
        off=0; prev_ctx2=0
        for (_,i,cw,cr,o,boundary) in rows:
            ctx=i+cw+cr
            base_total+=cost(cr,cw,i,o)
            if T and boundary and prev_ctx2>=T:      # 守卫只在用户发新消息时触发
                off=ctx-(base+handoff); resets+=1
                sim_total+=cost(0,base+handoff,0,o); ctx2=base+handoff
            else:
                ctx2=max(ctx-off,base); s=ctx2/ctx if ctx else 1
                sim_total+=cost(cr*s,cw*s,i*s,o)
            prev_ctx2=ctx2
    return base_total,sim_total,resets
if __name__=='__main__':
    b,_,_=sim(None); print('sessions',len(sessions),'baseline',round(b/1e6,1))
    for T in [80000,120000,160000,200000,250000]:
        b,s,r=sim(T); print(f'{T//1000}k saving {100*(1-s/b):.1f}% resets {r}')
