#!/usr/bin/env python3
"""B_sim.py — 在真实 transcript 上模拟"无人值守模式"各方案的成本（第 5 轮 B）。只读。

成本单位和 weekly_compare / round5_where_tokens_go 一样：
  cache_read×RM（默认 0.05，F2 实测口径；--read-mult 0.1 = 旧口径）+ 5 分钟写入×1.25 + 1 小时写入×2 + 未缓存 input×1 + output×5

做法（逐会话重放）：
  * 用实际的"每次请求上下文增量"重放；实际发生过压缩（上下文骤降）时模拟也跟着重置。
  * 压缩阈值用实测：autoCompactWindow=W 时，上下文超过约 W−33k 就压缩
    （实测：W=300k 时 -p 在 252k→274k 之间触发、交互在 269k→292k 之间；W=150k 在 115k→138k 之间；W=120k 子 agent 在 87k→110k 之间）。
  * 压缩一次的代价：读一遍当前上下文（缓存热，×0.1；若刚好冷则按写入）+ 摘要输出 SUMMARY_OUT×5；
    压缩后上下文 = POST（实测 11-04 压缩后第一次请求 48–61k）+ 重读 REEXP（写入一次，之后每次被读）。
  * 空闲：两次请求间隔 ≥60 分钟 → 1h 缓存已过期，整段按写入价重写。
      - idle_min=X：空闲压缩（Claude Code 自带，54 分钟时触发；要求上下文≥X、且**不在限额状态**）
      - limit_restart：撞限额后的空闲，改为"结束会话、限额恢复后用交接/room 新开"（重写的只是 POST+REEXP）
  * segment：到阈值时不压缩，而是写交接 + 新会话（交接输出 HANDOFF_OUT，新会话 NEW_BASE+交接 HANDOFF_IN+REEXP）

用法：
  python3 B_sim.py                         # 默认：AQ-sandbox-1104（11-04 通宵）主会话+子 agent
  python3 B_sim.py --dirs <项目目录>... --since 2026-11-04T08:00
  python3 B_sim.py --reread                # 另外统计"压缩后重读了多少"（实测）
"""
import argparse, datetime as dt, glob, json, os, re, sys, collections

PROJ = os.path.expanduser('~/.claude/projects')
DEFAULT_DIRS = [os.path.join(PROJ, '-Users-lihongrui-Documents-AQ-sandbox-1104')]
TOTAL_3DAY = 314.3e6  # round5_where_tokens_go.md 的 3 天总成本

SUMMARY_OUT = 8000     # 压缩摘要输出 token（post_tokens 13–21k 里含保留的消息，摘要本身取 8k，偏保守）
POST = 50000           # 压缩后第一次请求的上下文（系统提示+工具+摘要）
HANDOFF_OUT = 3000     # 写交接的输出
HANDOFF_IN = 5000      # 新会话读交接
NEW_BASE = 45000       # 新会话第一次请求上下文（主会话实测平均 42.9k）
RM = 0.05             # cache_read 单价（相对 input）；F2 实测 0.05
BUF = 33000            # autoCompactWindow − 实际触发点


def P(s):
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00'))


def load(f):
    reqs = []; seen = set(); limit_since = False
    for line in open(f, errors='replace'):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get('type') != 'assistant':
            continue
        if r.get('isApiErrorMessage') and r.get('error') == 'rate_limit':
            limit_since = True
            continue
        m = r.get('message') or {}
        u = m.get('usage'); mid = m.get('id')
        if not u or mid in seen or m.get('model') == '<synthetic>':
            continue
        seen.add(mid)
        cc = u.get('cache_creation') or {}
        cw = u.get('cache_creation_input_tokens', 0) or 0
        cw1 = cc.get('ephemeral_1h_input_tokens', None)
        if cw1 is None:
            cw1 = 0; cw5 = cw
        else:
            cw5 = cc.get('ephemeral_5m_input_tokens', 0) or 0
        cr = u.get('cache_read_input_tokens', 0) or 0
        inp = u.get('input_tokens', 0) or 0
        out = u.get('output_tokens', 0) or 0
        reqs.append(dict(T=P(r['timestamp']), ctx=cr + cw + inp, cr=cr, cw=cw, cw1=cw1, cw5=cw5, inp=inp, out=out,
                         limit=limit_since, cost=cr * RM + cw5 * 1.25 + cw1 * 2 + inp + out * 5))
        limit_since = False
    return reqs


def sim(rs, cap=None, reexp=0, idle_min=None, limit_restart=False, segment=False):
    """返回 (成本, 压缩/分段次数, 冷重写成本)"""
    if not rs:
        return 0, 0, 0
    w1 = sum(r['cw1'] for r in rs); w5 = sum(r['cw5'] for r in rs)
    w = (w1 * 2 + w5 * 1.25) / max(w1 + w5, 1) or 2.0
    ttl_min = 60 if w1 >= w5 else 5
    thr = cap - BUF if cap else 10 ** 12
    cost = 0; ncomp = 0; cold_cost = 0; ctx = 0; prev = None
    for r in rs:
        if prev is None:
            inc = r['ctx']; ctx = 0; gap = 0
        else:
            inc = r['ctx'] - prev['ctx']
            gap = (r['T'] - prev['T']).total_seconds() / 60
            if inc < 0:                      # 实际压缩过：模拟也重置成实际值
                ctx = 0; inc = r['ctx']
        cold = prev is not None and gap >= ttl_min
        prev = r
        # —— 空闲处理（发生在这次请求之前的空闲期里）——
        if gap >= ttl_min and ctx > 0:
            if r['limit'] and limit_restart:
                # 撞限额：结束会话，恢复后新开（交接已由 room/HANDOFF 持续维护）
                ncomp += 1
                ctx = 0; inc = NEW_BASE + HANDOFF_IN + reexp + max(inc, 0)
            elif (not r['limit']) and idle_min is not None and ctx >= max(idle_min, 100000):
                # 空闲压缩：54 分钟时缓存还热
                ncomp += 1
                cost += ctx * RM + SUMMARY_OUT * 5
                ctx = 0; inc = POST + reexp + max(inc, 0)
        # —— 上下文上限 ——
        if ctx + inc > thr and ctx > 0:
            ncomp += 1
            if segment:
                cost += (ctx * RM if not cold else ctx * w) + HANDOFF_OUT * 5
                ctx = 0; inc = NEW_BASE + HANDOFF_IN + reexp + max(inc, 0)
            else:
                cost += (ctx * RM if not cold else ctx * w) + SUMMARY_OUT * 5
                ctx = 0; inc = POST + reexp + max(inc, 0)
            cold = False
        if cold:
            c = (ctx + inc) * w; cold_cost += c; cost += c
        else:
            cost += ctx * RM + inc * w
        cost += r['out'] * 5 + r['inp']
        ctx += inc
    return cost, ncomp, cold_cost


# ---------- 压缩后重读（实测，统计） ----------
PATH_RE = re.compile(r'[\w./~-]+\.(?:py|md|txt|js|ts|json|tsv|csv|html|css|yaml|yml|toml|sh|mjs)\b')
READ_CMD = re.compile(r'(^|[;&|(]\s*)(cat|sed|head|tail|less|nl|awk|grep|rg)\b')


def reread_stats(f, window=40):
    """压缩后 window 次请求里，读的是"压缩前读过的文件"的结果有多少 token（字符×0.5 估算）。
    对照组：压缩前同样长度的一段里，重读更早读过文件的比例。"""
    rows = [json.loads(l) for l in open(f, errors='replace') if l.strip()]
    uses = {}; events = []  # (idx, kind, files, result_chars)
    req_idx = 0; seen_ids = set(); comp_at = []
    for r in rows:
        if r.get('subtype') == 'compact_boundary':
            comp_at.append(req_idx)
        m = r.get('message') or {}
        if r.get('type') == 'assistant' and m.get('id') not in seen_ids and m.get('usage'):
            seen_ids.add(m.get('id')); req_idx += 1
        c = m.get('content')
        if not isinstance(c, list):
            continue
        for b in c:
            if b.get('type') == 'tool_use':
                i = b.get('input') or {}
                if b['name'] == 'Read':
                    uses[b['id']] = {os.path.basename(i.get('file_path', ''))}
                elif b['name'] == 'Bash' and READ_CMD.search(i.get('command', '')):
                    uses[b['id']] = {os.path.basename(x) for x in PATH_RE.findall(i.get('command', ''))}
            elif b.get('type') == 'tool_result' and b.get('tool_use_id') in uses:
                cc = b.get('content'); n = len(cc) if isinstance(cc, str) else len(json.dumps(cc))
                events.append((req_idx, uses[b['tool_use_id']], n))
    out = []
    for ci in comp_at:
        before = set().union(*[e[1] for e in events if e[0] < ci]) if any(e[0] < ci for e in events) else set()
        post = [e for e in events if ci <= e[0] < ci + window]
        re_tok = sum(e[2] * 0.5 for e in post if e[1] & before)
        all_tok = sum(e[2] * 0.5 for e in post)
        pre = [e for e in events if ci - window <= e[0] < ci]
        early = set().union(*[e[1] for e in events if e[0] < ci - window]) if any(e[0] < ci - window for e in events) else set()
        pre_re = sum(e[2] * 0.5 for e in pre if e[1] & early)
        pre_all = sum(e[2] * 0.5 for e in pre)
        out.append(dict(at=ci, post_reread_tok=re_tok, post_read_tok=all_tok, pre_reread_tok=pre_re, pre_read_tok=pre_all))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dirs', nargs='*', default=DEFAULT_DIRS)
    ap.add_argument('--since', default='2026-11-04T08:00')
    ap.add_argument('--reread', action='store_true')
    ap.add_argument('--read-mult', type=float, default=0.05, help='cache_read 单价倍数（F2 实测 0.05；旧口径 0.1）')
    a = ap.parse_args()
    global RM
    RM = a.read_mult
    since = P(a.since + ('Z' if 'Z' not in a.since and '+' not in a.since else ''))
    files = []
    for d in a.dirs:
        files += glob.glob(os.path.join(d, '*.jsonl')) + glob.glob(os.path.join(d, '*', 'subagents', '*.jsonl'))
    sess = {}
    for f in files:
        rs = [r for r in load(f) if r['T'] >= since]
        if rs:
            sess[f] = rs
    actual = sum(r['cost'] for rs in sess.values() for r in rs)
    base = sum(sim(rs)[0] for rs in sess.values())
    nreq = sum(len(v) for v in sess.values())
    print(f'口径：cache_read×{RM}');print(f'会话文件 {len(sess)}（含子 agent），请求 {nreq}；实际成本 {actual/1e6:.1f}M，模型重放 {base/1e6:.1f}M（误差 {100*(base-actual)/actual:+.1f}%）')
    cold_base = sum(sim(rs)[2] for rs in sess.values())
    print(f'其中冷重写（空闲≥TTL 后整段重写）重放 {cold_base/1e6:.1f}M')
    scen = [
        ('A 已设 300k（11-06 的设置）', dict(cap=300000)),
        ('B 200k', dict(cap=200000)),
        ('C 180k（触发点≈150k）', dict(cap=180000)),
        ('D 150k（触发点≈117–130k）', dict(cap=150000)),
        ('E 120k', dict(cap=120000)),
        ('F 300k + 空闲压缩阈值 100k', dict(cap=300000, idle_min=100000)),
        ('G 180k + 空闲压缩 100k', dict(cap=180000, idle_min=100000)),
        ('H 180k + 空闲压缩 100k + 撞限额后结束/新开', dict(cap=180000, idle_min=100000, limit_restart=True)),
        ('I 150k + 空闲压缩 100k + 撞限额后结束/新开', dict(cap=150000, idle_min=100000, limit_restart=True)),
        ('J 180k 分段交接（不压缩，新会话）+ 空闲 + 限额', dict(cap=180000, idle_min=100000, limit_restart=True, segment=True)),
        ('K 只加"撞限额后结束/新开"（不改上限）', dict(limit_restart=True)),
        ('L 300k + 撞限额后结束/新开', dict(cap=300000, limit_restart=True)),
        ('M 300k + 空闲 100k + 撞限额后结束/新开', dict(cap=300000, idle_min=100000, limit_restart=True)),
        ('N 200k + 空闲 100k + 撞限额后结束/新开', dict(cap=200000, idle_min=100000, limit_restart=True)),
        ('O 240k + 空闲 100k + 撞限额后结束/新开', dict(cap=240000, idle_min=100000, limit_restart=True)),
    ]
    print(f"\n{'方案':<44}{'重读0':>16}{'重读30k':>16}{'重读60k':>16}  次数   冷重写(30k)")
    for nm, kw in scen:
        cells = []
        for re_ in (0, 30000, 60000):
            res = [sim(rs, reexp=re_, **kw) for rs in sess.values()]
            c = sum(x[0] for x in res); n = sum(x[1] for x in res); cc = sum(x[2] for x in res)
            cells.append((c, n, cc))
        s = ''.join(f"{c/1e6:7.1f}M({100*(c-base)/base:+4.0f}%)" for c, _, _ in cells)
        print(f"{nm:<44}{s}  {cells[1][1]:4d}   {cells[1][2]/1e6:5.1f}M")
    if a.reread:
        print('\n压缩后重读（实测；字符×0.5 估算 token；窗口=压缩前后各 40 次请求）')
        tot = collections.Counter()
        for f, rs in sess.items():
            for x in reread_stats(f):
                for k, v in x.items():
                    if k != 'at':
                        tot[k] += v
                print(f"  {os.path.basename(f)[:10]} 压缩在第{x['at']}次请求: 之后重读 {x['post_reread_tok']/1000:.1f}k / 之后读文件共 {x['post_read_tok']/1000:.1f}k；对照(压缩前) 重读 {x['pre_reread_tok']/1000:.1f}k / {x['pre_read_tok']/1000:.1f}k")
        print(f"  合计：压缩后重读 {tot['post_reread_tok']/1000:.0f}k / 读 {tot['post_read_tok']/1000:.0f}k；对照段重读 {tot['pre_reread_tok']/1000:.0f}k / 读 {tot['pre_read_tok']/1000:.0f}k")


if __name__ == '__main__':
    main()
