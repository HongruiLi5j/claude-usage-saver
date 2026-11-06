#!/usr/bin/env python3
"""把 session_resume.py 挂到 SessionEnd（/clear 时记下旧对话路径，接续更准）。先备份 settings.json。"""
import json, os, shutil, time
p = os.path.expanduser("~/.claude/settings.json")
bk = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups",
                  "settings.json." + time.strftime("%Y%m%d-%H%M%S") + "-pre-sessionend")
shutil.copy2(p, bk)
d = json.load(open(p))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cmd = "python3 " + os.path.join(ROOT, "hooks", "session_resume.py")
h = d.setdefault("hooks", {})
if any(cmd in x.get("command", "") for e in h.get("SessionEnd", []) for x in e.get("hooks", [])):
    print("已经装过，未改动")
else:
    h.setdefault("SessionEnd", []).append({"hooks": [{"type": "command", "command": cmd}]})
    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    print("已装好 SessionEnd hook")
print("备份：", bk)
print("撤回：cp", bk, p)
