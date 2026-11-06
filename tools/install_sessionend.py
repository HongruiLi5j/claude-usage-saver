#!/usr/bin/env python3
"""把 session_resume.py 挂到 SessionEnd（/clear 时记下旧对话路径，接续更准），
并把 SessionStart 的 matcher 改成 clear|compact（压缩后短注入红线/下一步，第 11 项 / SR17）。
先备份 settings.json；已装好的部分不重复改。由所有者运行。"""
import json, os, shutil, time
p = os.path.expanduser("~/.claude/settings.json")
bk = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups",
                  "settings.json." + time.strftime("%Y%m%d-%H%M%S") + "-pre-sessionend")
d = json.load(open(p))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cmd = "python3 " + os.path.join(ROOT, "hooks", "session_resume.py")
h = d.setdefault("hooks", {})
changed = []
if any(cmd in x.get("command", "") for e in h.get("SessionEnd", []) for x in e.get("hooks", [])):
    print("SessionEnd：已经装过，未改动")
else:
    h.setdefault("SessionEnd", []).append({"hooks": [{"type": "command", "command": cmd}]})
    changed.append("SessionEnd")
# SessionStart：挂本脚本的那一条，matcher 改成 clear|compact；没有就新加一条
ss = [e for e in h.get("SessionStart", []) if any(cmd in x.get("command", "") for x in e.get("hooks", []))]
if not ss:
    h.setdefault("SessionStart", []).append({"matcher": "clear|compact", "hooks": [{"type": "command", "command": cmd}]})
    changed.append("SessionStart（新加，matcher=clear|compact）")
for e in ss:
    if e.get("matcher") != "clear|compact":
        old = e.get("matcher")
        e["matcher"] = "clear|compact"
        changed.append(f"SessionStart matcher：{old!r} → 'clear|compact'")
if changed:
    shutil.copy2(p, bk)          # 只在真要改时备份，避免重复运行时同名备份被覆盖成已改版本
    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    print("已改：" + "；".join(changed))
    print("备份：", bk)
    print("撤回：cp", bk, p)
else:
    print("SessionStart：matcher 已是 clear|compact，未改动（未备份、未写文件）")
