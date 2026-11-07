#!/usr/bin/env python3
"""把 hooks/read_guard.py 挂到 PreToolUse（matcher = Bash|Read）。本轮只准备、不对真实 settings 执行 --apply。

用法（三选一，不带参数等于 --dry-run）：
  python3 tools/round5/D_install.py --dry-run            # 只打印会改什么，不写任何文件
  python3 tools/round5/D_install.py --apply              # 备份 settings 到 backups/，追加一个 PreToolUse 组
  python3 tools/round5/D_install.py --undo               # 只删掉指向本套件 read_guard.py 的条目（同样先备份）
  --settings <文件>   测试时指向副本（默认 ~/.claude/settings.json）
  --backup-dir <目录> 默认 <套件>/backups

规则：
- 幂等：已经有指向本套件 read_guard.py 的条目就不再加。
- 归属：command 用 shlex 拆开，每段展开 ~ / $HOME 后取 realpath，等于本套件 hooks/read_guard.py 才算「我们的」。
- 只追加一个独立的组 {"matcher": "Bash|Read", ...}；test_filter（matcher=Bash）等别的 hook 和其它键一律不动。
  两个 PreToolUse 组会被 Claude Code 并行调用：read_guard 只会 deny、从不输出 updatedInput，
  所以不会和 test_filter 的命令改写打架；任何一个 deny 都会让这次调用不执行（deny 优先）。
- --undo：只删我们的 command 条目，同组别的 hook 保留，组空了才删组，hooks 空了才删 hooks 键。
- 写入用临时文件 + os.replace；每次真改都打印一行撤回命令（cp 备份回去）。
"""
import argparse, json, os, shlex, shutil, sys, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(ROOT, "hooks", "read_guard.py")
CMD = "python3 " + SCRIPT
GROUP = {"matcher": "Bash|Read", "hooks": [{"type": "command", "command": CMD, "timeout": 5}]}
EVENT = "PreToolUse"


def _real(p):
    return os.path.realpath(os.path.abspath(os.path.expandvars(os.path.expanduser(p))))


SCRIPT_REAL = _real(SCRIPT)


def is_ours(h):
    if not isinstance(h, dict):
        return False
    c = h.get("command")
    if not isinstance(c, str) or "read_guard.py" not in c:
        return False
    try:
        parts = shlex.split(c)
    except ValueError:
        parts = c.split()
    return any(x.endswith("read_guard.py") and _real(x) == SCRIPT_REAL for x in parts)


def plan(d, undo):
    """在 d 上就地改，返回改动说明列表；d 结构不对时抛 ValueError。"""
    hooks = d.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise ValueError("settings 里的 hooks 不是对象")
    lst = (hooks or {}).get(EVENT)
    if lst is not None and not isinstance(lst, list):
        raise ValueError("hooks.%s 不是数组" % EVENT)
    changed = []
    if undo:
        if not lst:
            return changed
        keep, removed = [], 0
        for g in lst:
            if not (isinstance(g, dict) and isinstance(g.get("hooks"), list)):
                keep.append(g); continue
            hs = [x for x in g["hooks"] if not is_ours(x)]
            removed += len(g["hooks"]) - len(hs)
            if len(hs) == len(g["hooks"]):
                keep.append(g)
            elif hs:
                g["hooks"] = hs; keep.append(g)
        if removed:
            changed.append("%s：删掉 %d 条 read_guard" % (EVENT, removed))
            if keep:
                hooks[EVENT] = keep
            else:
                del hooks[EVENT]
            if not hooks:
                del d["hooks"]
        return changed
    if any(isinstance(g, dict) and any(is_ours(x) for x in (g.get("hooks") or [])) for g in (lst or [])):
        return changed
    if hooks is None:
        hooks = d["hooks"] = {}
    hooks.setdefault(EVENT, []).append(json.loads(json.dumps(GROUP)))
    changed.append("%s：追加 matcher=Bash|Read → read_guard.py（timeout 5s）" % EVENT)
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--undo", action="store_true")
    ap.add_argument("--settings", default=os.path.expanduser("~/.claude/settings.json"))
    ap.add_argument("--backup-dir", default=os.path.join(ROOT, "backups"))
    a = ap.parse_args(argv)
    write = a.apply or a.undo
    p = os.path.abspath(os.path.expanduser(a.settings))
    if not os.path.isfile(SCRIPT):
        print("找不到 %s，未改动" % SCRIPT); return 1
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise ValueError("顶层不是 JSON 对象")
    except FileNotFoundError:
        print("找不到 %s，未改动" % p); return 1
    except Exception as e:
        print("%s 不是合法 JSON（%s），未改动" % (p, e)); return 1
    try:
        changed = plan(d, a.undo)
    except ValueError as e:
        print("%s，未改动" % e); return 1
    if not changed:
        print("没有需要改的（%s），未写文件、未备份" % ("没装过" if a.undo else "已经装过")); return 0
    if not write:
        print("[dry-run] 将会改 %s：%s" % (p, "；".join(changed)))
        print("[dry-run] 改后的 %s：" % EVENT)
        print(json.dumps((d.get("hooks") or {}).get(EVENT), ensure_ascii=False, indent=1))
        print("[dry-run] 未写文件、未备份。真要装：python3 %s --apply" % os.path.abspath(__file__))
        return 0
    os.makedirs(a.backup_dir, exist_ok=True)
    tag = "pre-r5D-undo" if a.undo else "pre-r5D"
    bk = os.path.join(a.backup_dir, os.path.basename(p) + "." + time.strftime("%Y%m%d-%H%M%S") + "." + tag)
    n = 1
    while os.path.exists(bk):
        n += 1; bk = bk.rsplit(".v", 1)[0] + ".v%d" % n
    shutil.copy2(p, bk)
    tmp = p + ".tmp-r5D"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2); f.write("\n")
    os.replace(tmp, p)
    print("已改 %s：%s" % (p, "；".join(changed)))
    print("备份：" + bk)
    print("撤回（二选一）：python3 %s --undo%s ； 或 cp %s %s" % (os.path.abspath(__file__),
          "" if p == os.path.expanduser("~/.claude/settings.json") else " --settings " + p, bk, p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
