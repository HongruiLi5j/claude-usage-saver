#!/usr/bin/env python3
"""B_unattended.py — "无人值守模式"配置包（第 5 轮 B）。默认 --dry-run，只打印 diff。

只改**一个项目目录**的 `.claude/settings.local.json`（项目级、本机、一般不进 git），
不碰 ~/.claude/settings.json。用在通宵 / henchmen room 的工作目录（例如 ~/Documents/AQ-sandbox-1104）。
实测（reports/round5_B.md 第 1 节）：项目级 autoCompactWindow 会覆盖用户级 300000；
settings 里的 env 块对 henchmen 后台 agent 也生效（henchmen 只删进程继承来的 CLAUDE_CODE_*）。

写入的键（其他键一律不动）：
  autoCompactWindow                        = 240000   （实测触发点约 = 设置值 − 33k，即约 207k；按 F2 的 cache_read=0.05 口径，
                                                        模拟里 240k 在重读 0/30k/60k 三档都比 300k 省，180k 在重读 60k 时反而更贵）
  env.CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS  = "100000" （空闲压缩门槛；默认 200000，代码里最低 100000）
可选：
  --concise        outputStyle = "Concise"（实测 -p 下 output −40%，n=3；只适合输出给机器/room 读的运行）
  --deny-prompts   permissions.denyInsteadOfPrompting = true（**需 Claude Code ≥ 2.1.319**，本机 2.1.318 会拒绝写入）
  --window N       改上限（100000–1000000；不建议低于 200000：cache_read 很便宜，压缩太勤反而多写缓存）

用法：
  python3 B_unattended.py --target ~/Documents/AQ-sandbox-1104              # dry-run
  python3 B_unattended.py --target ~/Documents/AQ-sandbox-1104 --apply      # 备份后写入
  python3 B_unattended.py --target ~/Documents/AQ-sandbox-1104 --undo       # 只撤回本脚本改过的键
  python3 B_unattended.py --target DIR --status                             # 显示当前值和记录
记录与备份放在 <项目>/backups/round5_B_unattended/（按目标路径区分）。
"""
import argparse, copy, datetime, difflib, hashlib, json, os, re, shutil, subprocess, sys

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKUP_ROOT = os.path.join(PROJECT, 'backups', 'round5_B_unattended')
REAL_USER = [os.path.realpath(os.path.expanduser(p)) for p in
             ('~/.claude/settings.json', '~/.claude/settings.local.json')]
MIN_VER_DENY = (2, 1, 319)
MISSING = '__missing__'


def claude_version():
    try:
        out = subprocess.run(['claude', '--version'], capture_output=True, text=True, timeout=20).stdout
        m = re.search(r'(\d+)\.(\d+)\.(\d+)', out)
        return tuple(int(x) for x in m.groups()) if m else None
    except Exception:
        return None


def targets(a):
    t = {('autoCompactWindow',): a.window,
         ('env', 'CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS'): str(a.idle_min)}
    if a.concise:
        t[('outputStyle',)] = 'Concise'
    if a.deny_prompts:
        t[('permissions', 'denyInsteadOfPrompting')] = True
    return t


def get(d, path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return MISSING
        d = d[k]
    return d


def put(d, path, v):
    for k in path[:-1]:
        d = d.setdefault(k, {})
        if not isinstance(d, dict):
            raise SystemExit(f'拒绝：{".".join(path[:-1])} 存在但不是对象')
    if v is MISSING:
        d.pop(path[-1], None)
    else:
        d[path[-1]] = v


def prune(d, path):
    """撤回后如果 env/permissions 变成空对象，并且是本脚本建的，就删掉空对象"""
    if len(path) > 1 and isinstance(d.get(path[0]), dict) and not d[path[0]]:
        d.pop(path[0])


def state_paths(settings_file):
    h = hashlib.sha1(os.path.realpath(settings_file).encode()).hexdigest()[:10]
    d = os.path.join(BACKUP_ROOT, h)
    return d, os.path.join(d, 'state.json')


def load(f):
    if not os.path.exists(f):
        return {}
    txt = open(f).read()
    if not txt.strip():
        return {}
    d = json.loads(txt)
    if not isinstance(d, dict):
        raise SystemExit('拒绝：settings 不是 JSON 对象')
    return d


def dump(d):
    return json.dumps(d, indent=2, ensure_ascii=False) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--target', help='项目目录（写 <目录>/.claude/settings.local.json）')
    ap.add_argument('--settings-file', help='直接指定 settings 文件（测试用）')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--dry-run', action='store_true', help='默认：只打印 diff')
    g.add_argument('--apply', action='store_true')
    g.add_argument('--undo', action='store_true')
    g.add_argument('--status', action='store_true')
    ap.add_argument('--window', type=int, default=240000)
    ap.add_argument('--idle-min', type=int, default=100000)
    ap.add_argument('--concise', action='store_true')
    ap.add_argument('--deny-prompts', action='store_true')
    ap.add_argument('--backup-root', help='测试用：换一个备份目录')
    a = ap.parse_args()
    global BACKUP_ROOT
    if a.backup_root:
        BACKUP_ROOT = a.backup_root
    if not (a.target or a.settings_file):
        ap.error('要给 --target 或 --settings-file')
    f = a.settings_file or os.path.join(os.path.expanduser(a.target), '.claude', 'settings.local.json')
    f = os.path.abspath(f)
    if os.path.realpath(f) in REAL_USER:
        sys.exit('拒绝：这个脚本不改用户级 ~/.claude/settings*.json（第 5 轮红线）。请指向项目目录。')
    if os.path.islink(f):
        sys.exit('拒绝：settings 是符号链接')
    if not (100000 <= a.window <= 1000000):
        sys.exit('拒绝：--window 要在 100000–1000000（Claude Code 的允许范围）')
    if a.idle_min < 100000:
        print('提示：Claude Code 会把空闲压缩门槛提到最少 100000，这里照写但实际按 100000。')
    sdir, sfile = state_paths(f)
    cur = load(f)

    if a.status:
        for p in [('autoCompactWindow',), ('env', 'CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS'), ('outputStyle',),
                  ('permissions', 'denyInsteadOfPrompting')]:
            print(f'{".".join(p):45s} = {get(cur, p)}')
        print('记录：', sfile if os.path.exists(sfile) else '无')
        return

    if a.undo:
        if not os.path.exists(sfile):
            print('没有本脚本的记录，什么都不做。'); return
        st = json.load(open(sfile))
        new = copy.deepcopy(cur); notes = []
        for item in st['keys']:
            p = tuple(item['path']); now = get(new, p)
            if now != item['new']:
                notes.append(f'{".".join(p)} 已被改成 {now!r}（不是本脚本写的 {item["new"]!r}），保留不动')
                continue
            put(new, p, MISSING if item['old'] == MISSING else item['old'])
            if item['old'] == MISSING:
                prune(new, p)
        print(''.join(difflib.unified_diff(dump(cur).splitlines(True), dump(new).splitlines(True), f, f + ' (undo)')) or '（无变化）')
        for n in notes:
            print('注意：', n)
        if new == {} and st.get('file_existed') is False:
            os.makedirs(sdir, exist_ok=True)
            dst = os.path.join(sdir, f'settings.local.json.{datetime.datetime.now():%Y%m%d-%H%M%S}.removed-by-undo')
            shutil.move(f, dst)
            print(f'文件原来不存在，已移到 {dst}（不 rm）')
        else:
            open(f, 'w').write(dump(new))
        os.rename(sfile, sfile + f'.undone-{datetime.datetime.now():%Y%m%d-%H%M%S}')
        print('已撤回。'); return

    want = targets(a)
    if a.deny_prompts:
        v = claude_version()
        if v is None or v < MIN_VER_DENY:
            sys.exit(f'拒绝：permissions.denyInsteadOfPrompting 需要 Claude Code ≥ 2.1.319，本机是 {".".join(map(str, v)) if v else "未知"}。先升级再加 --deny-prompts。')
    new = copy.deepcopy(cur); changes = []
    for p, v in want.items():
        old = get(cur, p)
        if old != v:
            changes.append({'path': list(p), 'old': old, 'new': v})
            put(new, p, v)
    if not changes:
        print('已经是目标值，不写文件。'); return
    diff = ''.join(difflib.unified_diff(dump(cur).splitlines(True), dump(new).splitlines(True), f, f + ' (new)'))
    print(diff)
    print('提醒：henchmen 的 Room actions → Compacting 不要选 "Off"（会设 DISABLE_AUTO_COMPACT=1，连这里的上限一起关掉）。')
    if not a.apply:
        print('（dry-run，没有写文件。加 --apply 写入。）'); return
    os.makedirs(sdir, exist_ok=True)
    existed = os.path.exists(f)
    if existed:
        bk = os.path.join(sdir, f'settings.local.json.{datetime.datetime.now():%Y%m%d-%H%M%S}.pre-r5B')
        shutil.copy2(f, bk); print('备份：', bk)
    prev = json.load(open(sfile)) if os.path.exists(sfile) else None
    if prev:  # 第二次 apply：old 用第一次记录的原值
        first = {tuple(k['path']): k['old'] for k in prev['keys']}
        for c in changes:
            c['old'] = first.get(tuple(c['path']), c['old'])
        keep = [k for k in prev['keys'] if tuple(k['path']) not in {tuple(c['path']) for c in changes}]
        changes = keep + changes
    os.makedirs(os.path.dirname(f), exist_ok=True)
    open(f, 'w').write(dump(new))
    json.dump({'settings_file': f, 'file_existed': prev['file_existed'] if prev else existed,
               'applied_at': datetime.datetime.now().isoformat(), 'keys': changes}, open(sfile, 'w'), indent=1, ensure_ascii=False)
    print(f'已写入 {f}')
    print(f'撤回：python3 {os.path.abspath(__file__)} --settings-file {f} --undo' + (f' --backup-root {BACKUP_ROOT}' if a.backup_root else ''))


if __name__ == '__main__':
    main()
