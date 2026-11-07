#!/usr/bin/env python3
"""把套件的 skill 从单一来源生成给其他工具（第 5 轮 H）。默认只演示（plan），apply 前备份，revert 能还原到 apply 前。

问题（2026-11-06 实测）：Codex Desktop 的「外部 agent 导入同步」（config.toml [desktop] external-agent-import-sync-enabled=true）
在 11-06 00:42 把 ~/.claude/skills/* 拷到了 ~/.agents/skills/*（Codex 会加载这里），拷的时候把文本里的 Claude/claude
一律替换成 Codex，于是路径变成了不存在的 ~/Documents/Projects/Codex-usage-saver、.Codex/handoff.md；而且是旧快照，之后
Claude 侧的修改都没跟过去。

方案：
- 唯一来源 = ~/.claude/skills/<name>/SKILL.md（C 等人照常改这里，不用学新格式）。
- 生成给每个工具的版本（目前 codex → ~/.agents/skills/<name>/SKILL.md），规则：
  1. 路径不写死：来源里出现的本套件路径（~/Documents/Projects/claude-usage-saver、/Users/<任何人>/…/claude-usage-saver）
     一律换成运行时探测到的套件目录（本脚本所在仓库；或 --kit / $USAVER_KIT）。不做任何 Claude→Codex 的文字替换。
  2. 来源里可选的工具专属块：<!-- only:claude --> … <!-- /only -->、<!-- only:codex --> … <!-- /only -->；
     生成某工具时去掉其他工具的块、保留本工具的块（标记行本身去掉）。没写标记就全文保留。
  3. 正文开头（frontmatter 之后）加一段该工具的适配说明（adapters/skills/notes/<tool>.md，可选），
     和项目画像里的补充（--profile JSON 的 skill_notes.<tool> 或 skill_notes.<tool>.<skill>，可选）。
  4. frontmatter 后第一行写生成标记：来源路径 + 来源 sha256；check 用它发现「来源改了没重生成」或「被别的同步覆盖了」。
- 只处理 --skills 列出的（默认本套件自己的 model-router、handoff），不碰别的 skill。

  python3 adapters/skills_sync.py plan   [--tool codex] [--skills model-router,handoff] [--home DIR]
  python3 adapters/skills_sync.py apply  [...]     # 写生成版；目标原有文件先备份
  python3 adapters/skills_sync.py check  [...]     # 退出码 0 = 都是最新生成版；1 = 有过期 / 被覆盖 / 缺失
  python3 adapters/skills_sync.py revert [...]     # 用最近一次 apply 的备份还原（apply 前不存在的就移到 _to_delete/）
--home 改的是 ~ 的含义（测试用临时 HOME）；备份在 <home>/.usaver-backups/skills/，撤回记录在同目录 manifest.json。
"""
import argparse, hashlib, json, os, re, shutil, sys, time

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
TOOLS = {"codex": {"dir": os.path.join(".agents", "skills")}}   # 新工具：在这里加一行（目标目录相对 HOME）
DEFAULT_SKILLS = ("model-router", "handoff")
KIT_NAME = "claude-usage-saver"
KIT_PAT = re.compile(r"(?:~|/Users/[^/\s`'\"]+|/home/[^/\s`'\"]+)/Documents/Projects/" + KIT_NAME)
ONLY = re.compile(r"^[ \t]*<!--\s*only:([a-z0-9_-]+)\s*-->[ \t]*\n(.*?)^[ \t]*<!--\s*/only\s*-->[ \t]*\n?", re.S | re.M)
GEN_MARK = "<!-- usaver-generated: source=%s sha256=%s tool=%s; edit the source, then rerun adapters/skills_sync.py apply -->"
GEN_RE = re.compile(r"<!-- usaver-generated: source=(\S+) sha256=([0-9a-f]{64}) tool=(\S+);")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def tilde(path, home):
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def split_frontmatter(text):
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[:end + 5], text[end + 5:]
    return "", text


def render(src_text, tool, kit, home, skill, src_label, profile=None):
    body = ONLY.sub(lambda m: m.group(2) if m.group(1) == tool else "", src_text)
    body = KIT_PAT.sub(lambda m: tilde(kit, home), body)
    fm, rest = split_frontmatter(body)
    notes = []
    note_file = os.path.join(HERE, "skills", "notes", tool + ".md")
    if os.path.exists(note_file):
        notes.append(open(note_file, encoding="utf-8").read().strip().replace("{{KIT}}", tilde(kit, home)))
    sn = ((profile or {}).get("skill_notes") or {}).get(tool)
    if isinstance(sn, dict):
        sn = sn.get(skill)
    if isinstance(sn, str) and sn.strip():
        notes.append(sn.strip())
    head = GEN_MARK % (src_label, sha(src_text.encode()), tool)
    return fm + head + "\n" + ("\n\n".join(notes) + "\n\n" if notes else "") + rest.lstrip("\n")


def targets(a):
    home = os.path.realpath(os.path.expanduser(a.home)) if a.home else os.path.realpath(os.path.expanduser("~"))
    kit = os.path.realpath(a.kit or os.environ.get("USAVER_KIT") or REPO)
    profile = json.load(open(a.profile)) if a.profile else None
    out = []
    for tool in a.tool.split(","):
        if tool not in TOOLS:
            raise SystemExit("unknown tool %s (known: %s)" % (tool, ",".join(TOOLS)))
        for sk in a.skills.split(","):
            src = os.path.join(home, ".claude", "skills", sk, "SKILL.md")
            dst = os.path.join(home, TOOLS[tool]["dir"], sk, "SKILL.md")
            if not os.path.exists(src):
                out.append(dict(tool=tool, skill=sk, src=src, dst=dst, new=None)); continue
            text = open(src, encoding="utf-8").read()
            out.append(dict(tool=tool, skill=sk, src=src, dst=dst,
                            new=render(text, tool, kit, home, sk, tilde(src, home), profile)))
    return home, out


def state_of(t):
    if t["new"] is None:
        return "no-source"
    if not os.path.exists(t["dst"]):
        return "missing"
    cur = open(t["dst"], encoding="utf-8").read()
    if cur == t["new"]:
        return "current"
    return "stale-generated" if GEN_RE.search(cur) else "foreign"     # foreign = 不是我们生成的（如 Codex 导入的旧副本）


def bdir(home):
    return os.path.join(home, ".usaver-backups", "skills")


def cmd_plan(home, ts, apply=False):
    rows = []
    for t in ts:
        st = state_of(t)
        rows.append((t, st))
        bad = re.findall(r"Codex-usage-saver|\.Codex/", open(t["dst"]).read()) if os.path.exists(t["dst"]) else []
        print("%-6s %-14s %-16s %s%s" % (t["tool"], t["skill"], st, t["dst"],
                                         "  (目标里有 %d 处错误路径)" % len(bad) if bad else ""))
    if not apply:
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    man = {"stamp": stamp, "items": []}
    for t, st in rows:
        if st in ("current", "no-source"):
            continue
        item = {"dst": t["dst"], "backup": None}
        if os.path.exists(t["dst"]):
            b = os.path.join(bdir(home), stamp, t["tool"], t["skill"], "SKILL.md")
            os.makedirs(os.path.dirname(b), exist_ok=True)
            shutil.copy2(t["dst"], b)
            item["backup"] = b
        os.makedirs(os.path.dirname(t["dst"]), exist_ok=True)
        tmp = t["dst"] + ".usaver.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(t["new"])
        os.replace(tmp, t["dst"])
        man["items"].append(item)
        print("wrote %s%s" % (t["dst"], " (backup %s)" % item["backup"] if item["backup"] else " (new)"))
    if man["items"]:
        os.makedirs(bdir(home), exist_ok=True)
        hist = os.path.join(bdir(home), "manifest.json")
        try:
            allm = json.load(open(hist))
        except Exception:
            allm = []
        allm.append(man)
        json.dump(allm, open(hist, "w"), indent=1)
        print("revert: python3 %s revert%s" % (os.path.join(HERE, "skills_sync.py"),
                                              "" if home == os.path.realpath(os.path.expanduser("~")) else " --home " + home))
    return 0


def cmd_check(ts):
    bad = 0
    for t in ts:
        st = state_of(t)
        print("%-6s %-14s %s" % (t["tool"], t["skill"], st))
        bad += st not in ("current", "no-source")
    return 1 if bad else 0


def cmd_revert(home):
    hist = os.path.join(bdir(home), "manifest.json")
    try:
        allm = json.load(open(hist))
    except Exception:
        print("nothing to revert"); return 0
    if not allm:
        print("nothing to revert"); return 0
    man = allm.pop()
    trash = os.path.join(home, ".usaver-backups", "_to_delete", man["stamp"])
    for it in man["items"]:
        if it["backup"]:
            shutil.copy2(it["backup"], it["dst"])
            print("restored %s" % it["dst"])
        elif os.path.exists(it["dst"]):
            os.makedirs(trash, exist_ok=True)
            shutil.move(it["dst"], os.path.join(trash, os.path.basename(os.path.dirname(it["dst"])) + ".SKILL.md"))
            print("moved new file %s to %s" % (it["dst"], trash))
    json.dump(allm, open(hist, "w"), indent=1)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate per-tool copies of the kit's skills from ~/.claude/skills.")
    ap.add_argument("action", choices=["plan", "apply", "check", "revert"])
    ap.add_argument("--tool", default="codex")
    ap.add_argument("--skills", default=",".join(DEFAULT_SKILLS))
    ap.add_argument("--home", help="把这个目录当 ~（测试用）")
    ap.add_argument("--kit", help="套件目录（默认本仓库；或 $USAVER_KIT）")
    ap.add_argument("--profile", help="项目画像 JSON，可带 skill_notes.<tool>[.<skill>] 补充说明")
    a = ap.parse_args(argv)
    if a.action == "revert":
        home = os.path.realpath(os.path.expanduser(a.home)) if a.home else os.path.realpath(os.path.expanduser("~"))
        return cmd_revert(home)
    home, ts = targets(a)
    if a.action == "check":
        return cmd_check(ts)
    return cmd_plan(home, ts, apply=(a.action == "apply"))


if __name__ == "__main__":
    sys.exit(main())
