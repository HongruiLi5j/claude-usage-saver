#!/usr/bin/env python3
"""把跨工具适配装上 / 撤回（第 5 轮 H）。默认 --dry-run 之外的操作都只动目标文件，改前备份，不删任何东西。

  python3 adapters/apply.py agents apply  [--target ~/.codex/AGENTS.md] [--variant full|lite] [--dry-run]
  python3 adapters/apply.py agents revert [--target ~/.codex/AGENTS.md]
  python3 adapters/apply.py bin link      [--dir ~/.local/bin]      # 把 usaver-run、usaver-status 放上 PATH（符号链接）
  python3 adapters/apply.py bin unlink    [--dir ~/.local/bin]

agents：把 adapters/agents/AGENTS.usage-saver.md 这一段（<!-- usage-saver:begin --> … <!-- usage-saver:end -->）
插到目标文件末尾；已有同标记的段就整段替换（幂等）。可用的目标：
  Codex 全局 ~/.codex/AGENTS.md；Gemini CLI 全局 ~/.gemini/GEMINI.md；任一项目的 AGENTS.md / GEMINI.md /
  CONVENTIONS.md（Aider 用 --read 加载）。
备份：改前把原文件 cp 到 <repo>/backups/adapters/<文件名>.<时间>.pre-r5H。撤回：去掉标记段；
文件因此变空（原本就是我们建的）时 mv 到 <repo>/_to_delete/，不 rm。
"""
import argparse, os, re, shutil, sys, time

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
SNIPPETS = {"full": os.path.join(HERE, "agents", "AGENTS.usage-saver.md"),
            "lite": os.path.join(HERE, "agents", "AGENTS.usage-saver.lite.md")}
BINS = ("usaver-run", "usaver-status")
BLOCK = re.compile(r"\n*<!-- usage-saver:begin[^>]*-->.*?<!-- usage-saver:end -->\n?", re.S)


def _home(p):
    return os.path.abspath(os.path.expanduser(p))


BACKUP_DIR = os.environ.get("USAVER_BACKUP_DIR") or os.path.join(REPO, "backups", "adapters")   # 测试时可改
TRASH_DIR = os.environ.get("USAVER_TRASH_DIR") or os.path.join(REPO, "_to_delete")


def _backup(path):
    d = BACKUP_DIR
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, "%s.%s.pre-r5H" % (os.path.basename(path), time.strftime("%Y%m%d-%H%M%S")))
    dst, i = base, 0
    while os.path.exists(dst):                  # 同一秒内多次备份不互相覆盖
        i += 1
        dst = "%s.%d" % (base, i)
    shutil.copy2(path, dst)
    return dst


def agents_apply(target, dry=False, variant="full"):
    snip = open(SNIPPETS[variant], encoding="utf-8").read().strip() + "\n"
    old = open(target, encoding="utf-8").read() if os.path.exists(target) else None
    if old is None:
        new = snip
    elif BLOCK.search(old):
        new = BLOCK.sub(lambda m: "\n\n" + snip if m.start() > 0 else snip, old, count=1)
    else:
        new = old.rstrip("\n") + ("\n\n" if old.strip() else "") + snip
    if new == old:
        print("unchanged: %s already has the current block" % target)
        return 0
    if dry:
        print("would write %s (%d -> %d bytes)" % (target, len(old or ""), len(new)))
        return 0
    bk = _backup(target) if old is not None else None
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".usaver.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, target)
    print("applied to %s%s" % (target, " (backup: %s)" % bk if bk else " (new file)"))
    print("revert: python3 %s agents revert --target %s" % (os.path.join(HERE, "apply.py"), target))
    return 0


def agents_revert(target, dry=False):
    if not os.path.exists(target):
        print("nothing to revert: %s does not exist" % target)
        return 0
    old = open(target, encoding="utf-8").read()
    if not BLOCK.search(old):
        print("nothing to revert: no usage-saver block in %s" % target)
        return 0
    new = BLOCK.sub("\n", old, count=1).strip("\n")
    new = new + "\n" if new else ""
    if dry:
        print("would remove block from %s" % target)
        return 0
    bk = _backup(target)
    if not new.strip():
        d = TRASH_DIR
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, "%s.%s" % (os.path.basename(target), time.strftime("%Y%m%d-%H%M%S")))
        shutil.move(target, dst)
        print("block removed; file became empty and was moved to %s (backup: %s)" % (dst, bk))
        return 0
    with open(target, "w", encoding="utf-8") as f:
        f.write(new)
    print("block removed from %s (backup: %s)" % (target, bk))
    return 0


def bin_link(d, dry=False):
    rc = 0
    for name in BINS:
        src = os.path.join(HERE, "bin", name)
        dst = os.path.join(d, name)
        if os.path.islink(dst) and os.path.realpath(dst) == os.path.realpath(src):
            print("already linked: %s" % dst)
            continue
        if os.path.lexists(dst):
            print("refusing: %s exists and is not our link" % dst)
            rc = 1
            continue
        if dry:
            print("would link %s -> %s" % (dst, src))
            continue
        os.makedirs(d, exist_ok=True)
        os.symlink(src, dst)
        print("linked %s -> %s" % (dst, src))
    print("revert: python3 %s bin unlink --dir %s" % (os.path.join(HERE, "apply.py"), d))
    return rc


def bin_unlink(d, dry=False):
    for name in BINS:
        src = os.path.join(HERE, "bin", name)
        dst = os.path.join(d, name)
        if not (os.path.islink(dst) and os.path.realpath(dst) == os.path.realpath(src)):
            print("nothing to unlink: %s is not our link" % dst)
            continue
        if dry:
            print("would move link %s to _to_delete/" % dst)
            continue
        os.makedirs(TRASH_DIR, exist_ok=True)
        shutil.move(dst, os.path.join(TRASH_DIR, "%s.link.%s" % (name, time.strftime("%Y%m%d-%H%M%S"))))
        print("unlinked %s (moved the symlink to _to_delete/)" % dst)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=["agents", "bin"])
    ap.add_argument("action", choices=["apply", "revert", "link", "unlink"])
    ap.add_argument("--target", default="~/.codex/AGENTS.md")
    ap.add_argument("--dir", default="~/.local/bin")
    ap.add_argument("--variant", choices=sorted(SNIPPETS), default="full", help="full（约 720 token，按实测 0.287 token/字节折算）或 lite（约 230 token）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.what == "agents" and a.action in ("apply", "revert"):
        if a.action == "apply":
            return agents_apply(_home(a.target), a.dry_run, a.variant)
        return agents_revert(_home(a.target), a.dry_run)
    if a.what == "bin" and a.action in ("link", "unlink"):
        return (bin_link if a.action == "link" else bin_unlink)(_home(a.dir), a.dry_run)
    ap.error("use: agents apply|revert, bin link|unlink")


if __name__ == "__main__":
    sys.exit(main())
