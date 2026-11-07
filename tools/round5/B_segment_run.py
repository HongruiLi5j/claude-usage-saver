#!/usr/bin/env python3
"""B_segment_run.py — 长任务"分段 + 交接 + 新会话"跑法（第 5 轮 B 的无人值守模式之三）。

每一段是一个全新的 `claude -p` 会话：先读 HANDOFF.md，做一段，结束前把进度写回 HANDOFF.md，
最后一行写 STATUS: DONE / STATUS: CONTINUE。脚本据此决定是否开下一段。
好处（见 reports/round5_B.md 第 2 节）：
  * 每段上下文都从约 3–4 万开始，不会一路涨到几十万（11-04 那晚 9 个会话停在 37–53 万）；
  * 段与段之间没有空闲，不会出现"等一小时后整段 2× 重写"；
  * 撞到用量上限：不续用旧会话（旧会话上下文大、缓存已冷），而是等限额恢复后**新开一段**，只重写约 4–5 万。
  * 每段内部仍有 autoCompactWindow（默认 240000）兜底。

默认 --dry-run（只打印将要执行的命令）。
  python3 B_segment_run.py --dir DIR --task-file TASK.md                 # dry-run
  python3 B_segment_run.py --dir DIR --task-file TASK.md --run --model sonnet --max-segments 6 --turns 60
  python3 B_segment_run.py --dir DIR --undo                              # 把 HANDOFF.md 恢复成开跑前的版本
写的文件：DIR/HANDOFF.md（由 Claude 写）、DIR/.usage_saver_segments.jsonl（每段的 usage）。
开跑前的 HANDOFF.md 备份到 <项目>/backups/round5_B_segment/<hash>/。
"""
import argparse, datetime, hashlib, json, os, re, shutil, subprocess, sys, time

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BK_ROOT = os.path.join(PROJECT, 'backups', 'round5_B_segment')
MARK = '<!-- usage-saver-handoff -->'
SEG_PROMPT = """{task}

—— 分段运行规则（usage-saver 无人值守模式）——
这是第 {i}/{n} 段，是一个全新的会话，看不到之前的对话。
1. 先读 HANDOFF.md（如果有），从"下一步"接着做；不要重做"进度"里已完成的事。
2. 这一段最多约 {turns} 次工具调用。读文件先 grep -n 定位再读需要的行，不要整文件 cat。
3. 结束这一段之前（做完一块、或觉得已经做了很多步），**覆盖写** HANDOFF.md：第一行写 {mark}，
   然后按顺序写这几节：目标、红线/未决、下一步、进度、关键文件。写具体（文件路径、命令、数字），不超过 80 行。
4. 最后一条回复的最后一行只写 STATUS: DONE（整个任务完成）或 STATUS: CONTINUE（还要下一段）。"""


def cost_units(u):
    cc = u.get('cache_creation') or {}
    w1 = cc.get('ephemeral_1h_input_tokens'); w5 = cc.get('ephemeral_5m_input_tokens')
    cw = u.get('cache_creation_input_tokens', 0) or 0
    if w1 is None and w5 is None:
        w1, w5 = 0, cw
    return (u.get('cache_read_input_tokens', 0) or 0) * 0.1 + (w5 or 0) * 1.25 + (w1 or 0) * 2 + \
        (u.get('input_tokens', 0) or 0) + (u.get('output_tokens', 0) or 0) * 5


def is_limit(res, stderr):
    txt = json.dumps(res, ensure_ascii=False).lower() + stderr.lower()
    return any(k in txt for k in ('usage limit', 'rate_limit', 'rate limit', '429', 'limit reached'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dir', required=True)
    ap.add_argument('--task-file')
    ap.add_argument('--model', default='sonnet')
    ap.add_argument('--engine', choices=['claude', 'codex'], default='claude',
                    help='codex：用 codex exec 跑每一段（model_auto_compact_token_limit=--window；未实测，Codex 额度当时用完）')
    ap.add_argument('--max-segments', type=int, default=6)
    ap.add_argument('--turns', type=int, default=60, help='每段 --max-turns')
    ap.add_argument('--window', type=int, default=240000, help='每段内的 autoCompactWindow')
    ap.add_argument('--permission-mode', default=None, help='传给 claude 的 --permission-mode（默认不传）')
    ap.add_argument('--limit-sleep', type=int, default=1800, help='撞限额后等多少秒再新开一段')
    ap.add_argument('--max-limit-waits', type=int, default=6)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--run', action='store_true')
    g.add_argument('--undo', action='store_true')
    a = ap.parse_args()
    d = os.path.abspath(os.path.expanduser(a.dir))
    hof = os.path.join(d, 'HANDOFF.md'); log = os.path.join(d, '.usage_saver_segments.jsonl')
    bdir = os.path.join(BK_ROOT, hashlib.sha1(d.encode()).hexdigest()[:10])

    if a.undo:
        st = os.path.join(bdir, 'state.json')
        if not os.path.exists(st):
            print('没有记录，什么都不做。'); return
        s = json.load(open(st)); ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        for f in (hof, log):
            if os.path.exists(f):
                shutil.move(f, os.path.join(bdir, os.path.basename(f) + f'.after-run-{ts}'))
        if s.get('handoff_backup'):
            shutil.copy2(s['handoff_backup'], hof)
            print('HANDOFF.md 已恢复成开跑前的版本。')
        else:
            print('开跑前没有 HANDOFF.md；运行后产生的 HANDOFF.md 已移到', bdir)
        os.rename(st, st + f'.undone-{ts}')
        return

    if not a.task_file:
        ap.error('--task-file 必填（--undo 除外）')
    task = open(a.task_file).read().strip()
    settings = json.dumps({'autoCompactWindow': a.window, 'env': {'CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS': '100000'}})

    def cmd(i):
        prompt = SEG_PROMPT.format(task=task, i=i, n=a.max_segments, turns=a.turns, mark=MARK)
        if a.engine == 'codex':
            return ['codex', 'exec', '--skip-git-repo-check', '--json', '-c', f'model_auto_compact_token_limit={a.window - 30000}', prompt]
        c = ['claude', '-p', SEG_PROMPT.format(task=task, i=i, n=a.max_segments, turns=a.turns, mark=MARK),
             '--model', a.model, '--max-turns', str(a.turns), '--output-format', 'json', '--settings', settings]
        if a.permission_mode:
            c += ['--permission-mode', a.permission_mode]
        return c

    if not a.run:
        c = cmd(1)
        print('dry-run：每段会在', d, '里执行（第 1 段示例，prompt 省略）：')
        longest = max(c, key=len)
        print(' ', ' '.join(x if x != longest else '"<任务 + 分段规则>"' for x in c))
        print(f'最多 {a.max_segments} 段；撞限额后等 {a.limit_sleep}s 再**新开**一段（最多 {a.max_limit_waits} 次）。加 --run 执行。')
        return

    os.makedirs(bdir, exist_ok=True)
    st = {'dir': d, 'started': datetime.datetime.now().isoformat(), 'handoff_backup': None}
    if os.path.exists(hof):
        st['handoff_backup'] = os.path.join(bdir, 'HANDOFF.md.pre-r5B-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
        shutil.copy2(hof, st['handoff_backup'])
    json.dump(st, open(os.path.join(bdir, 'state.json'), 'w'), indent=1)
    env = {k: v for k, v in os.environ.items() if k != 'CLAUDECODE' and not k.startswith('CLAUDE_CODE_')}
    i = 1; waits = 0; total = 0.0
    while i <= a.max_segments:
        t0 = time.time()
        p = subprocess.run(cmd(i), cwd=d, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if a.engine == 'codex':
            # codex exec --json 输出 JSONL 事件；取最后一条 agent 消息文本，usage 不在这里统计（见 H 的用量适配）
            texts = re.findall(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', p.stdout)
            res = {'result': json.loads('"' + texts[-1] + '"') if texts else '', 'is_error': p.returncode != 0}
        else:
            try:
                res = json.loads(p.stdout)
            except Exception:
                res = {'parse_error': p.stdout[-300:]}
        u = res.get('usage') or {}
        c = cost_units(u); total += c
        text = res.get('result') or ''
        status = 'DONE' if re.search(r'STATUS:\s*DONE', text) else 'CONTINUE' if re.search(r'STATUS:\s*CONTINUE', text) else None
        rec = {'segment': i, 'session': res.get('session_id'), 'subtype': res.get('subtype'), 'is_error': res.get('is_error'),
               'turns': res.get('num_turns'), 'secs': round(time.time() - t0), 'status': status, 'cost_units': round(c),
               'cache_read': u.get('cache_read_input_tokens'), 'cache_write': u.get('cache_creation_input_tokens'),
               'output': u.get('output_tokens'), 'handoff_marked': os.path.exists(hof) and MARK in open(hof, errors='replace').read(2000)}
        open(log, 'a').write(json.dumps(rec, ensure_ascii=False) + '\n')
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        if res.get('is_error') and is_limit(res, p.stderr):
            waits += 1
            if waits > a.max_limit_waits:
                print('撞限额次数太多，停止。'); break
            print(f'撞用量上限：不续用旧会话，{a.limit_sleep}s 后用 HANDOFF.md 新开一段。', flush=True)
            time.sleep(a.limit_sleep)
            continue  # 同一段号重来（新会话）
        if status == 'DONE':
            print('任务完成。'); break
        if not rec['handoff_marked']:
            print('警告：这一段没有写出带标记的 HANDOFF.md，下一段可能接不上。', flush=True)
        i += 1
    print(f'合计成本约 {total/1e3:.0f}k 单位；记录在 {log}；撤回：python3 {os.path.abspath(__file__)} --dir {d} --undo')


if __name__ == '__main__':
    main()
