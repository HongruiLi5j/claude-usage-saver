#!/usr/bin/env python3
"""把 hooks/limit_logger.py 挂到 StopFailure（全部错误类型）和
Notification（只订阅 quota_auto_resume_fired|stale|disabled）。由所有者运行。

- 幂等：某个事件下已有命令指向「本套件的」hooks/limit_logger.py，就不再加。
- 归属判断（F3）：把 command 用 shlex 拆开，每一段展开 ~ 和 $HOME 后取 realpath，
  等于本套件 hooks/limit_logger.py 的 realpath 才算「我们的」。/other/limit_logger.py 不算。
- 别的 hook（包括别人的 StopFailure / Notification hook）和其他键一律不动，只追加。
- 真要改时才备份（默认放到本套件 backups/），并打印一行撤回命令。
- --dry-run：只打印会做什么，不写任何文件。
- --uninstall：只删掉指向本套件 limit_logger.py 的 command 条目，同组别的 hook 保留，组空了才删组（同样先备份）。
- --settings / --backup-dir：测试时指向副本，默认 ~/.claude/settings.json 和 <套件>/backups。
"""
import argparse, json, os, shlex, shutil, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "hooks", "limit_logger.py")
CMD = "python3 " + SCRIPT
QUOTA_MATCHER = "quota_auto_resume_fired|quota_auto_resume_stale|quota_auto_resume_disabled"
WANT = {
    "StopFailure": {"hooks": [{"type": "command", "command": CMD, "timeout": 10}]},
    "Notification": {"matcher": QUOTA_MATCHER, "hooks": [{"type": "command", "command": CMD, "timeout": 10}]},
}


def _real(p):
    return os.path.realpath(os.path.abspath(os.path.expandvars(os.path.expanduser(p))))


SCRIPT_REAL = _real(SCRIPT)


def is_ours_cmd(cmd):
    """command 里有一段（展开 ~ / $HOME 后）正好是本套件的 hooks/limit_logger.py 才算我们的。"""
    if not isinstance(cmd, str) or "limit_logger.py" not in cmd:
        return False
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
    return any(x.endswith("limit_logger.py") and _real(x) == SCRIPT_REAL for x in parts)


def is_ours_hook(x):
    return isinstance(x, dict) and is_ours_cmd(x.get("command"))


def ours(group):
    return any(is_ours_hook(x) for x in (group.get("hooks") or []))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", default=os.path.expanduser("~/.claude/settings.json"))
    ap.add_argument("--backup-dir", default=os.path.join(ROOT, "backups"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args(argv)
    p = os.path.abspath(os.path.expanduser(a.settings))

    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise ValueError("顶层不是 JSON 对象")
    except FileNotFoundError:
        print(f"找不到 {p}，未改动")
        return 1
    except Exception as e:
        print(f"{p} 不是合法 JSON（{e}），未改动")
        return 1
    hooks = d.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        print("settings 里的 hooks 不是对象，未改动")
        return 1

    changed = []
    if a.uninstall:
        for ev in WANT:
            lst = (hooks or {}).get(ev)
            if not isinstance(lst, list):
                continue
            # 只删组里指向本套件脚本的 command 条目；同组别的 hook 留着，组空了才删组（F2/F3 修正）
            keep, removed = [], 0
            for g in lst:
                if not (isinstance(g, dict) and isinstance(g.get("hooks"), list)):
                    keep.append(g)
                    continue
                hs = [x for x in g["hooks"] if not is_ours_hook(x)]
                removed += len(g["hooks"]) - len(hs)
                if len(hs) == len(g["hooks"]):
                    keep.append(g)
                elif hs:
                    g["hooks"] = hs
                    keep.append(g)
            if removed:
                changed.append(f"{ev}：删掉 {removed} 条 limit_logger")
                if keep:
                    hooks[ev] = keep
                else:
                    del hooks[ev]
        if hooks == {} and changed:
            del d["hooks"]
    else:
        if hooks is None:
            hooks = d["hooks"] = {}
        for ev, group in WANT.items():
            lst = hooks.get(ev)
            if lst is not None and not isinstance(lst, list):
                print(f"hooks.{ev} 不是数组，未改动")
                return 1
            if any(isinstance(g, dict) and ours(g) for g in (lst or [])):
                print(f"{ev}：已经装过，跳过")
                continue
            hooks.setdefault(ev, []).append(json.loads(json.dumps(group)))
            changed.append(ev + ("（matcher=" + group["matcher"] + "）" if "matcher" in group else "（全部错误类型）"))

    if not changed:
        print("没有需要改的，未写文件、未备份")
        return 0
    if a.dry_run:
        print("[dry-run] 将会改：" + "；".join(changed))
        print("[dry-run] 未写文件、未备份")
        return 0
    os.makedirs(a.backup_dir, exist_ok=True)
    tag = "pre-limitlogger-uninstall" if a.uninstall else "pre-limitlogger"
    bk = os.path.join(a.backup_dir, "settings.json." + time.strftime("%Y%m%d-%H%M%S") + "-" + tag)
    n = 1
    while os.path.exists(bk):
        n += 1
        bk = bk.rsplit(".v", 1)[0] + f".v{n}"
    shutil.copy2(p, bk)
    tmp = p + ".tmp-limitlogger"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    print("已改：" + "；".join(changed))
    print("撤回：cp " + bk + " " + p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
