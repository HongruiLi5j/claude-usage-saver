#!/usr/bin/env python3
"""R6I2：session_resume 交接文件选取的两个打回问题（Codex 复核 I 阻塞项 2、3）的回归测试。

阻塞项 2：覆盖文件给的相对路径经符号链接（或绝对路径、..）指到兄弟项目 → 注入了别的项目的正文。
阻塞项 3：Projects/group/lean-child（无 git，有 lakefile 和自己的 交接.md）→ 拿了 group/HANDOFF.md。

全部在临时目录里造项目，HOME、USAGE_SAVER_STATE 指向临时目录，不碰真实项目 / 真实 ~/.claude / 真实 state。
hook 以子进程运行（端到端）；少数用例直接调用 find_handoff / pick_handoff（单元，同样在虚拟 HOME 的子进程里）。

用法：python3 tools/tests_r6I2.py [--suite 套件根目录（默认本套件；用来先测副本）] [-k 关键字]
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = "<!-- usage-saver-handoff -->"
CASES = []


def case(cid, desc):
    def deco(fn):
        CASES.append((cid, desc, fn))
        return fn
    return deco


def w(path, txt=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(txt)
    return path


def ho(tag):
    """带标记的交接文件：红线 + 下一步都带 tag，clear 和 compact 都能看到。"""
    return f"{MARK}\n# 交接\n## 红线\n- 不 push {tag}-RED\n## 下一步\n- 接着做 {tag}\n"


def git(d):
    os.makedirs(os.path.join(d, ".git"), exist_ok=True)
    return d


class Env:
    def __init__(self, suite):
        self.suite = suite
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="r6I2-"))
        self.home = os.path.join(self.root, "home")
        self.projects = os.path.join(self.home, "Documents", "Projects")
        self.state = os.path.join(self.root, "state")
        self.tdir = os.path.join(self.home, ".claude", "projects", "-x")
        for d in (self.projects, self.state, self.tdir):
            os.makedirs(d, exist_ok=True)

    def env(self):
        e = {**os.environ, "HOME": self.home, "USAGE_SAVER_STATE": self.state}
        e.pop("USAGE_SAVER_CONTAINERS", None)
        return e

    def run(self, argv, stdin=""):
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, cwd=self.root, env=self.env(), timeout=60)
        return p.returncode, p.stdout, p.stderr

    def transcript(self, sid):
        p = os.path.join(self.tdir, sid + ".jsonl")
        open(p, "w").close()
        return p

    def resume(self, cwd, source="clear", sid=None):
        sid = sid or f"S{time.time_ns()}"
        payload = {"hook_event_name": "SessionStart", "source": source, "cwd": cwd, "session_id": sid,
                   "transcript_path": self.transcript(sid)}
        rc, out, err = self.run([sys.executable, os.path.join(self.suite, "hooks", "session_resume.py")],
                                json.dumps(payload))
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""
        return rc, ctx

    def both(self, cwd):
        """clear 和 compact 各跑一次，返回 (rc 都为 0, clear 文本, compact 文本)。"""
        r1, a = self.resume(cwd, "clear")
        r2, b = self.resume(cwd, "compact")
        return r1 == 0 and r2 == 0, a, b

    def unit(self, code):
        """在虚拟 HOME 的子进程里加载 session_resume（去掉末尾读 stdin 的主程序），执行 code，返回它 print 的 JSON。"""
        drv = ("import json,sys\n"
               f"src=open({os.path.join(self.suite, 'hooks', 'session_resume.py')!r},encoding='utf-8').read()\n"
               "src=src[:src.rindex('\\ntry:\\n    data = json.load(sys.stdin)')]\n"
               f"g={{'__file__':{os.path.join(self.suite, 'hooks', 'session_resume.py')!r},'__name__':'sr'}}\n"
               "exec(compile(src,'session_resume.py','exec'),g)\n"
               "globals().update(g)\n" + code)
        rc, out, err = self.run([sys.executable, "-c", drv])
        if rc != 0:
            raise RuntimeError(err[-600:])
        return json.loads(out.strip().splitlines()[-1])

    def profile(self, cwd):
        rc, out, err = self.run([sys.executable, os.path.join(self.suite, "tools", "project_profile.py"), "--cwd", cwd,
                                 "--json", "--no-cache", "--no-write"])
        return json.loads(out)


def sibling_pair(E, name="symlink-escape"):
    """Codex 阻塞项 2 的夹具：两个兄弟 git 项目，other/STATUS.md 带标记和 OTHER-PROJECT-CANARY。"""
    other = git(os.path.join(E.projects, "other"))
    w(os.path.join(other, "STATUS.md"), ho("OTHER-PROJECT-CANARY"))
    me = git(os.path.join(E.projects, name))
    return me, other


# ---------------------------------------------------------------- 阻塞项 2：越界
@case("A01", "Codex 复现 2 原样：覆盖文件 notes/STATUS.md，notes -> ../other（兄弟项目）→ clear/compact 都不注入兄弟项目")
def _(E):
    me, other = sibling_pair(E)
    w(os.path.join(me, ".usage-saver.json"), json.dumps({"handoff": {"files": ["notes/STATUS.md"]}}))
    os.symlink("../other", os.path.join(me, "notes"))
    ok, a, b = E.both(me)
    return ok and "OTHER-PROJECT-CANARY" not in a + b, (a + " || " + b)[:300]


@case("A02", "同上，但本项目自己有带标记的 HANDOFF.md → 拒绝越界的覆盖文件后回退到默认的 HANDOFF.md")
def _(E):
    me, other = sibling_pair(E)
    w(os.path.join(me, ".usage-saver.json"), json.dumps({"handoff": {"files": ["notes/STATUS.md"]}}))
    os.symlink("../other", os.path.join(me, "notes"))
    w(os.path.join(me, "HANDOFF.md"), ho("OWN-FALLBACK"))
    ok, a, b = E.both(me)
    return ok and "OTHER-PROJECT-CANARY" not in a + b and "OWN-FALLBACK" in a and "OWN-FALLBACK" in b, b[:300]


@case("A03", "realpath 越界：HANDOFF.md 本身是指向兄弟项目文件的符号链接 → 不注入（clear/compact）")
def _(E):
    me, other = sibling_pair(E)
    os.symlink(os.path.join(other, "STATUS.md"), os.path.join(me, "HANDOFF.md"))
    ok, a, b = E.both(me)
    return ok and "OTHER-PROJECT-CANARY" not in a + b, (a + " || " + b)[:300]


@case("A04", "realpath 越界：.claude 目录是指向兄弟项目的符号链接（.claude/handoff.md 是默认名）→ 不注入")
def _(E):
    me, other = sibling_pair(E)
    w(os.path.join(other, "cfg", "handoff.md"), ho("OTHER-PROJECT-CANARY"))
    os.symlink(os.path.join(other, "cfg"), os.path.join(me, ".claude"))
    ok, a, b = E.both(me)
    return ok and "OTHER-PROJECT-CANARY" not in a + b, (a + " || " + b)[:300]


@case("A05", "`..`：find_handoff 收到 ../other/STATUS.md（绕过画像的 _safe_rel）→ 跳过，回退到本项目 HANDOFF.md；"
             "端到端：覆盖文件写 ../other/STATUS.md 也不注入")
def _(E):
    me, other = sibling_pair(E)
    w(os.path.join(me, "HANDOFF.md"), ho("OWN-DOTDOT"))
    r = E.unit(f"print(json.dumps(find_handoff({me!r}, ['../other/STATUS.md', 'HANDOFF.md'], [])))")
    w(os.path.join(me, ".usage-saver.json"), json.dumps({"handoff": {"files": ["../other/STATUS.md"]}}))
    ok, a, b = E.both(me)
    good = r[0] == os.path.join(me, "HANDOFF.md") and ok and "OTHER-PROJECT-CANARY" not in a + b and "OWN-DOTDOT" in b
    return good, f"unit={r} compact={b[:200]}"


@case("A06", "`..` 但仍在项目内（sub/../HANDOFF.md）→ 照常接受（同一条规则，不误伤）")
def _(E):
    me, _ = sibling_pair(E)
    os.makedirs(os.path.join(me, "sub"))
    w(os.path.join(me, "HANDOFF.md"), ho("OWN"))
    r = E.unit(f"print(json.dumps(find_handoff({me!r}, ['sub/../HANDOFF.md'], [])))")
    return r[0] is not None and os.path.realpath(r[0]) == os.path.join(me, "HANDOFF.md"), str(r)


@case("A07", "绝对路径：指到兄弟项目 → 跳过；指到本项目内 → 接受；端到端覆盖文件写绝对路径也不注入兄弟项目")
def _(E):
    me, other = sibling_pair(E)
    w(os.path.join(me, "docs", "进度.md"), ho("OWN-ABS"))
    out_abs = os.path.join(other, "STATUS.md")
    in_abs = os.path.join(me, "docs", "进度.md")
    r1 = E.unit(f"print(json.dumps(find_handoff({me!r}, [{out_abs!r}], [])))")
    r2 = E.unit(f"print(json.dumps(find_handoff({me!r}, [{in_abs!r}], [])))")
    w(os.path.join(me, ".usage-saver.json"), json.dumps({"handoff": {"files": [out_abs]}}))
    ok, a, b = E.both(me)
    good = r1 == [None, None] and r2[0] == in_abs and ok and "OTHER-PROJECT-CANARY" not in a + b
    return good, f"out={r1} in={r2}"


@case("A08", "项目内的符号链接（notes -> docs，同一项目里）→ 照常接受并注入（不过度拒绝）")
def _(E):
    me, _ = sibling_pair(E)
    w(os.path.join(me, "docs", "STATUS.md"), ho("IN-PROJECT-LINK"))
    os.symlink("docs", os.path.join(me, "notes"))
    w(os.path.join(me, ".usage-saver.json"), json.dumps({"handoff": {"files": ["notes/STATUS.md"]}}))
    ok, a, b = E.both(me)
    return ok and "IN-PROJECT-LINK" in a and "IN-PROJECT-LINK" in b, b[:300]


@case("A09", "画像的 handoff.found 不列出经符号链接越界的文件（skill 按它沿用交接写法，不能指到兄弟项目）")
def _(E):
    me, other = sibling_pair(E)
    w(os.path.join(me, ".usage-saver.json"), json.dumps({"handoff": {"files": ["notes/STATUS.md"]}}))
    os.symlink("../other", os.path.join(me, "notes"))
    found = [f["path"] for f in E.profile(me)["handoff"]["found"]]
    return "notes/STATUS.md" not in found, str(found)


@case("A10", "画像模块缺失时的后备路径（pick_handoff 传 (None, None)）也拒绝符号链接越界")
def _(E):
    me, other = sibling_pair(E)
    os.symlink(os.path.join(other, "STATUS.md"), os.path.join(me, "HANDOFF.md"))
    r = E.unit(f"print(json.dumps(pick_handoff({me!r}, (None, None))[0]))")
    return r is None, str(r)


# ---------------------------------------------------------------- 阻塞项 3：嵌套子项目
def group_child(E, child_files=("lakefile.toml",), child_ho="交接.md", parent_git=False):
    g = os.path.join(E.projects, "group")
    if parent_git:
        git(g)
    w(os.path.join(g, "HANDOFF.md"), ho("PARENT-UNRELATED"))
    c = os.path.join(g, "lean-child")
    for f in child_files:
        w(os.path.join(c, f), "")
    if child_ho:
        w(os.path.join(c, child_ho), ho("CHILD-OWN"))
    return g, c


@case("B01", "Codex 复现 3 原样：group/HANDOFF.md + group/lean-child（lakefile.toml、交接.md、无 git）→ clear/compact 都用 CHILD-OWN")
def _(E):
    g, c = group_child(E)
    ok, a, b = E.both(c)
    good = ok and "CHILD-OWN" in a and "CHILD-OWN" in b and "PARENT-UNRELATED" not in a + b
    return good, (a[:150] + " || " + b[:150])


@case("B02", "同上，cwd 在子项目的更深一层（lean-child/Src/X）→ 仍用子项目的 交接.md")
def _(E):
    g, c = group_child(E)
    deep = os.path.join(c, "Src", "X")
    os.makedirs(deep)
    ok, a, b = E.both(deep)
    return ok and "CHILD-OWN" in a + b and "PARENT-UNRELATED" not in a + b, b[:200]


@case("B03", "项目交接.md（quant-backtest 的写法）：无 git 嵌套子项目，pyproject + 项目交接.md → 用它")
def _(E):
    g, c = group_child(E, child_files=("pyproject.toml",), child_ho="项目交接.md")
    ok, a, b = E.both(c)
    return ok and "CHILD-OWN" in a and "CHILD-OWN" in b and "PARENT-UNRELATED" not in a + b, b[:200]


@case("B04", "子项目只有自己的 交接.md（没有构建清单、没有 git）→ 也算项目根，用它")
def _(E):
    g, c = group_child(E, child_files=())
    ok, a, b = E.both(c)
    return ok and "CHILD-OWN" in a + b and "PARENT-UNRELATED" not in a + b, b[:200]


@case("B05", "子项目有构建清单、没有任何交接，父目录无 git → 安全回退：不注入父目录的 HANDOFF.md")
def _(E):
    g, c = group_child(E, child_ho=None)
    ok, a, b = E.both(c)
    return ok and "PARENT-UNRELATED" not in a + b, (a + " || " + b)[:200]


@case("B06", "只有 git 的父项目：父目录 .git + HANDOFF.md，cwd 是没有任何标记的子目录 → 用父项目 HANDOFF.md（同一仓库）")
def _(E):
    g, c = group_child(E, child_files=(), child_ho=None, parent_git=True)
    deep = os.path.join(c, "notes")
    os.makedirs(deep)
    ok, a, b = E.both(deep)
    return ok and "PARENT-UNRELATED" in a and "PARENT-UNRELATED" in b, b[:200]


@case("B07", "只有 git 的父项目 + 子目录有构建清单、没有交接（monorepo 子包）→ 到仓库根找，用父项目 HANDOFF.md")
def _(E):
    g, c = group_child(E, child_files=("package.json",), child_ho=None, parent_git=True)
    ok, a, b = E.both(c)
    return ok and "PARENT-UNRELATED" in a and "PARENT-UNRELATED" in b, b[:200]


@case("B08", "父目录有 git + HANDOFF.md，子目录有 lakefile + 自己的 交接.md → 最近的项目根优先，用子目录的")
def _(E):
    g, c = group_child(E, parent_git=True)
    ok, a, b = E.both(c)
    return ok and "CHILD-OWN" in a and "CHILD-OWN" in b and "PARENT-UNRELATED" not in a + b, b[:200]


@case("B09", "父目录只有 .git（没有交接文件），子目录无 git、有 交接.md → 用子目录的")
def _(E):
    g = git(os.path.join(E.projects, "gitonly"))
    c = os.path.join(g, "child")
    w(os.path.join(c, "交接.md"), ho("CHILD-OWN"))
    ok, a, b = E.both(c)
    return ok and "CHILD-OWN" in a and "CHILD-OWN" in b, b[:200]


@case("B10", "嵌套两层：group/mid（Makefile、无交接）/leaf（lakefile + 交接.md），cwd=leaf → leaf；cwd=mid → 不拿 group 的")
def _(E):
    g = os.path.join(E.projects, "group")
    w(os.path.join(g, "HANDOFF.md"), ho("PARENT-UNRELATED"))
    mid = os.path.join(g, "mid")
    w(os.path.join(mid, "Makefile"), "")
    leaf = os.path.join(mid, "leaf")
    w(os.path.join(leaf, "lakefile.toml"), "")
    w(os.path.join(leaf, "交接.md"), ho("LEAF-OWN"))
    ok1, a, b = E.both(leaf)
    ok2, c, d = E.both(mid)
    good = ok1 and ok2 and "LEAF-OWN" in a + b and "PARENT-UNRELATED" not in a + b + c + d
    return good, (b[:120] + " || mid:" + d[:120])


@case("B11", "不变的行为：cwd 是 group 本身 → 用 group/HANDOFF.md；HANDOFF.md 和 交接.md 同在 → 仍用 HANDOFF.md")
def _(E):
    g, c = group_child(E)
    ok1, a, b = E.both(g)
    w(os.path.join(c, "HANDOFF.md"), ho("CHILD-PRIMARY"))
    ok2, x, y = E.both(c)
    good = ok1 and ok2 and "PARENT-UNRELATED" in a and "PARENT-UNRELATED" in b and "CHILD-PRIMARY" in x + y \
        and "CHILD-OWN" not in y
    return good, (b[:100] + " || " + y[:100])


@case("B12", "不变的行为：普通 git 项目 cwd 在 src/（无标记）→ 用项目根的 项目交接.md（无 HANDOFF.md 时的同义名）")
def _(E):
    r = git(os.path.join(E.projects, "qb"))
    w(os.path.join(r, "项目交接.md"), ho("QB-OWN"))
    src = os.path.join(r, "src")
    os.makedirs(src)
    ok, a, b = E.both(src)
    return ok and "QB-OWN" in a and "QB-OWN" in b, b[:200]


@case("B13", "画像模块缺失时的后备路径：Codex 复现 3 的夹具也选子项目的 交接.md")
def _(E):
    g, c = group_child(E)
    r = E.unit(f"print(json.dumps(pick_handoff({c!r}, (None, None))[0]))")
    return r == os.path.join(c, "交接.md"), str(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default=KIT)
    ap.add_argument("-k", default="")
    a = ap.parse_args()
    suite = os.path.realpath(a.suite)
    n = ok = 0
    for cid, desc, fn in CASES:
        if a.k and a.k not in cid and a.k not in desc:
            continue
        n += 1
        E = Env(suite)
        try:
            good, info = fn(E)
        except Exception as e:
            good, info = False, f"{type(e).__name__}: {e}"
        finally:
            shutil.rmtree(E.root, ignore_errors=True)
        ok += bool(good)
        print(f"{cid}  {'PASS' if good else 'FAIL'}  {desc}")
        if not good:
            print(f"      → {str(info)[:400]}")
    print(f"\nR6I2 测试（套件 {suite}）：{ok}/{n} 通过")
    sys.exit(0 if ok == n else 1)


# ---------------------------------------------------------------- R6I3 追加（Codex 复核 I2 打回；完整用例见 tests_r6I3.py）
@case("B14", "R6I3：Codex 复核 I2 复现——group/HANDOFF.md + group/docs-child/{CLAUDE.md|AGENTS.md|.usage-saver.json, 进度.md}"
             "（无 git）→ 三种标记 × clear/compact 都只用子项目的 进度.md")
def _(E):
    res = []
    for k, marker in enumerate(("CLAUDE.md", "AGENTS.md", ".usage-saver.json")):
        g = os.path.join(E.projects, f"group{k}")
        c = os.path.join(g, "docs-child")
        w(os.path.join(g, "HANDOFF.md"), ho("UNRELATED-PARENT"))
        w(os.path.join(c, marker), "{}" if marker.endswith(".json") else "# 独立文档项目\n")
        w(os.path.join(c, "进度.md"), ho("CHILD-OWN"))
        ok, a, b = E.both(c)
        res.append(ok and "CHILD-OWN" in a and "CHILD-OWN" in b and "UNRELATED-PARENT" not in a + b)
    return all(res), str(res)


@case("B15", "R6I3（所有者 11-07 决定）：B05 形状（子项目有构建清单、无交接，父目录无 git）→ 只提一行父目录交接的路径，不注入内容")
def _(E):
    g, c = group_child(E, child_ho=None)
    ok, a, b = E.both(c)
    hp = os.path.join(g, "HANDOFF.md")
    one = lambda s: "\n" not in s and hp in s and "上一级目录有交接文件" in s
    return ok and one(a) and one(b) and "PARENT-UNRELATED" not in a + b, (a + " || " + b)[:300]


if __name__ == "__main__":
    main()
