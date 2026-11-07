#!/usr/bin/env python3
"""R6I3：项目根统一判定（Codex 复核 I2 打回）的回归测试。

C 组：Codex 复现原样（group/HANDOFF.md + group/docs-child/{CLAUDE.md | AGENTS.md | .usage-saver.json, 进度.md}）
      × clear / compact，共 6 次事件；以及子项目没有交接时只提一行路径（所有者 11-07 决定）。
S 组：其它根判定形状：容器级 AGENTS.md、隐式容器、多层嵌套、父目录 git + 子目录只有标记、标记和交接都在父目录、
      符号链接（cwd 链接、交接文件链接、说明文件链接）、git 子模块、monorepo、~ 边界、降级模式……
      每个用例都同时核对「hook 选中的文件」和「画像的 _meta.root」是同一个根算出来的。

全部在系统临时目录里造夹具，HOME、USAGE_SAVER_STATE 指向临时目录；不碰真实项目、真实 ~/.claude、真实 state。
测试只新建文件，不删除任何东西（临时目录留给系统清理，路径在最后打印）。
用法：python3 tools/tests_r6I3.py [--suite 套件根目录（默认本套件）] [-k 关键字]
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = "<!-- usage-saver-handoff -->"
CASES = []
RUN_ROOT = os.path.realpath(tempfile.mkdtemp(prefix="r6I3-run-"))


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
    def __init__(self, suite, cid):
        self.suite = suite
        self.root = os.path.join(RUN_ROOT, cid)
        self.home = os.path.join(self.root, "home")
        self.projects = os.path.join(self.home, "Documents", "Projects")
        self.state = os.path.join(self.root, "state")
        self.tdir = os.path.join(self.home, ".claude", "projects", "-x")
        self.extra = {}
        for d in (self.projects, self.state, self.tdir):
            os.makedirs(d, exist_ok=True)

    def env(self):
        e = {**os.environ, "HOME": self.home, "USAGE_SAVER_STATE": self.state, "PYTHONDONTWRITEBYTECODE": "1"}
        e.pop("USAGE_SAVER_CONTAINERS", None)
        e.update(self.extra)
        return e

    def run(self, argv, stdin=""):
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, cwd=self.root, env=self.env(), timeout=60)
        return p.returncode, p.stdout, p.stderr

    def hook_path(self):
        return os.path.join(self.suite, "hooks", "session_resume.py")

    def resume(self, cwd, source="clear", hook=None):
        sid = f"S{time.time_ns()}"
        tp = os.path.join(self.tdir, sid + ".jsonl")
        open(tp, "w").close()
        payload = {"hook_event_name": "SessionStart", "source": source, "cwd": cwd, "session_id": sid,
                   "transcript_path": tp}
        rc, out, err = self.run([sys.executable, hook or self.hook_path()], json.dumps(payload))
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""
        return rc, ctx

    def both(self, cwd, hook=None):
        r1, a = self.resume(cwd, "clear", hook)
        r2, b = self.resume(cwd, "compact", hook)
        return r1 == 0 and r2 == 0, a, b

    def unit(self, code, hook=None):
        """在虚拟 HOME 的子进程里加载 session_resume（去掉末尾读 stdin 的主程序），执行 code，返回它 print 的 JSON。"""
        hp = hook or self.hook_path()
        drv = ("import json,sys\n"
               f"src=open({hp!r},encoding='utf-8').read()\n"
               "src=src[:src.rindex('\\ntry:\\n    data = json.load(sys.stdin)')]\n"
               f"g={{'__file__':{hp!r},'__name__':'sr'}}\n"
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

    def pick(self, cwd):
        """hook 的选择结果：[路径, 来源, 提示路径]。"""
        return self.unit(f"print(json.dumps(pick_handoff_ex({cwd!r})))")

    def agree(self, cwd):
        """hook 选中的文件必须在画像的项目根里，或（monorepo）在画像给的 repo 里；返回 (是否一致, 说明)。"""
        hp, how, hint = self.pick(cwd)
        meta = self.profile(cwd)["_meta"]
        root, repo = meta["root"], meta.get("repo")
        if hp is None:
            return True, f"无（root={root}）"
        d = os.path.realpath(hp)
        ok = d.startswith(root + os.sep) or (repo and d.startswith(repo + os.sep) and "git 仓库" in how)
        return bool(ok), f"hp={hp} root={root} repo={repo}"


def one_line_hint(ctx, path):
    return "\n" not in ctx and path in ctx and "上一级目录有交接文件" in ctx


# ---------------------------------------------------------------- C 组：Codex 复现
def codex_fixture(E, marker):
    group = os.path.join(E.projects, "group")
    child = os.path.join(group, "docs-child")
    os.makedirs(child, exist_ok=True)
    w(os.path.join(group, "HANDOFF.md"), MARK + "\n## 下一步\n- UNRELATED-PARENT\n")
    w(os.path.join(child, marker), "{}" if marker == ".usage-saver.json" else "# Standalone documentation project\n")
    w(os.path.join(child, "进度.md"), MARK + "\n## 下一步\n- CHILD-OWN\n")
    return group, child


for _k, _marker in enumerate(("CLAUDE.md", "AGENTS.md", ".usage-saver.json")):
    def _mk(marker):
        def fn(E):
            g, c = codex_fixture(E, marker)
            prof = E.profile(c)
            ok, a, b = E.both(c)
            good = (ok and prof["_meta"]["root"] == c and any(f["path"] == "进度.md" for f in prof["handoff"]["found"])
                    and "CHILD-OWN" in a and "CHILD-OWN" in b and "UNRELATED-PARENT" not in a + b
                    and "当前目录所在项目" in a)
            return good, f"root={prof['_meta']['root']} | clear={a[:160]} | compact={b[:160]}"
        return fn
    case(f"C0{_k + 1}", f"Codex 复现原样，标记={_marker}：clear 和 compact 都只注入子项目 进度.md（CHILD-OWN），"
                        f"不含 UNRELATED-PARENT；画像根=docs-child")(_mk(_marker))


for _k, _marker in enumerate(("CLAUDE.md", "AGENTS.md", ".usage-saver.json")):
    def _mk2(marker):
        def fn(E):
            g, c = codex_fixture(E, marker)
            os.rename(os.path.join(c, "进度.md"), os.path.join(E.root, "moved-进度.md"))   # 子项目没有交接
            ok, a, b = E.both(c)
            hp = os.path.join(g, "HANDOFF.md")
            good = ok and one_line_hint(a, hp) and one_line_hint(b, hp) and "UNRELATED-PARENT" not in a + b
            return good, f"clear={a!r} | compact={b!r}"
        return fn
    case(f"C0{_k + 4}", f"同上但子项目没有交接（标记={_marker}）：父目录无 git → clear/compact 都只提一行父目录交接的路径，"
                        f"不注入内容")(_mk2(_marker))


@case("C07", "I2 的 B05 形状（子项目只有 lakefile、无交接，父目录无 git）：按所有者新决定只提一行路径")
def _(E):
    g = os.path.join(E.projects, "group")
    w(os.path.join(g, "HANDOFF.md"), ho("PARENT-UNRELATED"))
    c = os.path.join(g, "lean-child")
    w(os.path.join(c, "lakefile.toml"))
    ok, a, b = E.both(c)
    hp = os.path.join(g, "HANDOFF.md")
    return ok and one_line_hint(a, hp) and one_line_hint(b, hp) and "PARENT-UNRELATED" not in a + b, a + " || " + b


@case("C08", "提示行和上一段对话摘要并存：clear 时有上一段 transcript → 摘要照常出，交接一节只有一行路径")
def _(E):
    g, c = codex_fixture(E, "CLAUDE.md")
    os.rename(os.path.join(c, "进度.md"), os.path.join(E.root, "moved-进度.md"))
    old = os.path.join(E.tdir, "old.jsonl")
    rows = [{"type": "ai-title", "aiTitle": "旧对话标题 OLDTITLE"},
            {"type": "user", "timestamp": "2026-11-07T00:00:00Z", "message": {"role": "user", "content": "做文档 OLDUSER"}},
            {"type": "assistant", "timestamp": "2026-11-07T00:01:00Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "好的 OLDREPLY"}]}}]
    w(old, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    rc, a = E.resume(c, "clear")
    hp = os.path.join(g, "HANDOFF.md")
    good = rc == 0 and "OLDREPLY" in a and hp in a and "UNRELATED-PARENT" not in a and "上一级目录有交接文件" in a
    return good, a[:400]


# ---------------------------------------------------------------- S 组：其它根判定形状
@case("S01", "容器级 AGENTS.md：Projects/{AGENTS.md, HANDOFF.md}，cwd=Projects/foo/sub（无信号）→ 根=foo，"
             "不注入也不提示 Projects 的交接；兄弟 git 项目 a 仍用自己的")
def _(E):
    w(os.path.join(E.projects, "AGENTS.md"), "# 容器级说明\n")
    w(os.path.join(E.projects, "HANDOFF.md"), ho("PROJECTS-LEVEL"))
    a = git(os.path.join(E.projects, "a"))
    w(os.path.join(a, "HANDOFF.md"), ho("A-OWN"))
    sub = os.path.join(E.projects, "foo", "sub")
    os.makedirs(sub)
    ok1, x, y = E.both(sub)
    ok2, p, q = E.both(a)
    root = E.profile(sub)["_meta"]["root"]
    good = ok1 and ok2 and x == "" and y == "" and root == os.path.join(E.projects, "foo") and "A-OWN" in p + q
    return good, f"root={root} sub-clear={x[:120]!r} a={q[:80]!r}"


@case("S02", "隐式容器：USAGE_SAVER_CONTAINERS 指到别处（Projects 不是配置的容器），Projects 有 AGENTS.md+HANDOFF.md "
             "和 2 个 git 项目 → cwd=Projects/foo/sub 根=foo（group），不把 Projects 当项目根")
def _(E):
    E.extra = {"USAGE_SAVER_CONTAINERS": os.path.join(E.root, "elsewhere")}
    w(os.path.join(E.projects, "AGENTS.md"), "# 容器级说明\n")
    w(os.path.join(E.projects, "HANDOFF.md"), ho("PROJECTS-LEVEL"))
    git(os.path.join(E.projects, "a"))
    git(os.path.join(E.projects, "b"))
    sub = os.path.join(E.projects, "foo", "sub")
    os.makedirs(sub)
    meta = E.profile(sub)["_meta"]
    r = E.pick(sub)
    good = meta["root"] == os.path.join(E.projects, "foo") and meta["root_how"] == "group" and r == [None, "当前目录所在项目", None]
    return good, f"meta={meta['root']},{meta['root_how']} pick={r}"


@case("S03", "只有说明文件的分组目录（group/AGENTS.md，无 git、无交接）下有 2 个有信号的子项目 → group 是隐式容器："
             "cwd=group/c（无信号）根=c；cwd=group 本身仍是 group；只有 1 个子项目时不算容器")
def _(E):
    g = os.path.join(E.projects, "group")
    w(os.path.join(g, "AGENTS.md"), "# 分组说明\n")
    w(os.path.join(g, "p1", "CLAUDE.md"), "x")
    c = os.path.join(g, "c")
    os.makedirs(c)
    m1 = E.profile(c)["_meta"]
    w(os.path.join(g, "p2", "lakefile.toml"))
    m2 = E.profile(c)["_meta"]
    m3 = E.profile(g)["_meta"]
    good = m1["root"] == g and m2["root"] == c and m2["root_how"] == "group" and m3["root"] == g
    return good, f"一个子项目={m1['root']} 两个={m2['root']},{m2['root_how']} self={m3['root']}"


@case("S04", "多层嵌套：g(HANDOFF)/mid(CLAUDE.md+交接.md)/leaf(AGENTS.md+带标记的 进度.md) → cwd=leaf 用 LEAF，"
             "cwd=mid 和 mid/notes 用 MID，cwd=g/x 用 G；hook 与画像一致")
def _(E):
    g = os.path.join(E.projects, "g")
    w(os.path.join(g, "HANDOFF.md"), ho("G-OWN"))
    mid = os.path.join(g, "mid")
    w(os.path.join(mid, "CLAUDE.md"), "x")
    w(os.path.join(mid, "交接.md"), ho("MID-OWN"))
    leaf = os.path.join(mid, "leaf")
    w(os.path.join(leaf, "AGENTS.md"), "x")
    w(os.path.join(leaf, "进度.md"), ho("LEAF-OWN"))
    notes = os.path.join(mid, "notes")
    gx = os.path.join(g, "x")
    os.makedirs(notes)
    os.makedirs(gx)
    res, good = {}, True
    for cwd, want, bad in ((leaf, "LEAF-OWN", ("MID-OWN", "G-OWN")), (mid, "MID-OWN", ("LEAF-OWN", "G-OWN")),
                           (notes, "MID-OWN", ("LEAF-OWN", "G-OWN")), (gx, "G-OWN", ("LEAF-OWN", "MID-OWN"))):
        ok, a, b = E.both(cwd)
        agr, info = E.agree(cwd)
        res[os.path.basename(cwd)] = (want in a and want in b, agr)
        good &= ok and want in a and want in b and not any(t in a + b for t in bad) and agr
    return good, str(res)


@case("S05", "多层嵌套、最里层没有交接：mid(交接.md)/leaf(CLAUDE.md) → leaf 只提一行 mid 交接的路径")
def _(E):
    mid = os.path.join(E.projects, "g", "mid")
    w(os.path.join(mid, "交接.md"), ho("MID-OWN"))
    leaf = os.path.join(mid, "leaf")
    w(os.path.join(leaf, "CLAUDE.md"), "x")
    ok, a, b = E.both(leaf)
    hp = os.path.join(mid, "交接.md")
    return ok and one_line_hint(a, hp) and one_line_hint(b, hp) and "MID-OWN" not in a + b, a + " || " + b


@case("S06", "父目录有 git、子目录只有标记（repo/.git+HANDOFF.md，repo/child/CLAUDE.md）→ 同一仓库：根=repo，"
             "注入 repo 的交接（子目录 CLAUDE.md 不另立项目）")
def _(E):
    repo = git(os.path.join(E.projects, "repo"))
    w(os.path.join(repo, "HANDOFF.md"), ho("REPO-OWN"))
    child = os.path.join(repo, "child")
    w(os.path.join(child, "CLAUDE.md"), "x")
    w(os.path.join(child, "package.json"), "{}")
    ok, a, b = E.both(child)
    meta = E.profile(child)["_meta"]
    agr, info = E.agree(child)
    return ok and "REPO-OWN" in a and "REPO-OWN" in b and meta["root"] == repo and agr, f"{meta['root']} {info}"


@case("S07", "父目录有 git、子目录有标记+带标记的 进度.md → 子目录自己的交接（硬信号在仓库里也另立项目根）")
def _(E):
    repo = git(os.path.join(E.projects, "repo"))
    w(os.path.join(repo, "HANDOFF.md"), ho("REPO-OWN"))
    child = os.path.join(repo, "child")
    w(os.path.join(child, "CLAUDE.md"), "x")
    w(os.path.join(child, "进度.md"), ho("CHILD-OWN"))
    ok, a, b = E.both(child)
    meta = E.profile(child)["_meta"]
    agr, info = E.agree(child)
    good = ok and "CHILD-OWN" in a and "CHILD-OWN" in b and "REPO-OWN" not in a + b and meta["root"] == child \
        and meta["repo"] == repo and agr
    return good, f"{meta['root']} {info}"


@case("S08", "父目录有 git、子目录只有 .usage-saver.json（无交接）→ 画像根=子目录（读它的覆盖文件），"
             "hook 回到仓库根的交接（monorepo 规则），来源写明是仓库根")
def _(E):
    repo = git(os.path.join(E.projects, "repo"))
    w(os.path.join(repo, "HANDOFF.md"), ho("REPO-OWN"))
    child = os.path.join(repo, "pkg")
    w(os.path.join(child, ".usage-saver.json"), json.dumps({"thresholds": {"soft_tokens": 123456}}))
    ok, a, b = E.both(child)
    p = E.profile(child)
    agr, info = E.agree(child)
    good = ok and "REPO-OWN" in a + b and p["_meta"]["root"] == child and p["thresholds"]["soft_tokens"] == 123456 \
        and "git 仓库的根目录" in b and agr
    return good, f"{p['_meta']['root']} {info} {b[:120]}"


@case("S09", "标记和交接都在父目录：group(CLAUDE.md+HANDOFF.md)/sub（无信号）→ sub 属于 group，注入 group 的交接")
def _(E):
    g = os.path.join(E.projects, "group")
    w(os.path.join(g, "CLAUDE.md"), "x")
    w(os.path.join(g, "HANDOFF.md"), ho("GROUP-OWN"))
    sub = os.path.join(g, "docs", "sub")
    os.makedirs(sub)
    ok, a, b = E.both(sub)
    agr, info = E.agree(sub)
    return ok and "GROUP-OWN" in a and "GROUP-OWN" in b and agr, info


@case("S10", "符号链接 cwd：~/link → Projects/group/docs-child（Codex 夹具）→ 按真实路径，用子项目的 进度.md")
def _(E):
    g, c = codex_fixture(E, "CLAUDE.md")
    link = os.path.join(E.home, "link")
    os.symlink(c, link)
    ok, a, b = E.both(link)
    return ok and "CHILD-OWN" in a and "CHILD-OWN" in b and "UNRELATED-PARENT" not in a + b, b[:200]


@case("S11", "符号链接交接：child(CLAUDE.md, HANDOFF.md -> ../HANDOFF.md) → 链接指出子项目，不算子项目的交接、"
             "不注入；只提一行父目录交接的路径")
def _(E):
    g = os.path.join(E.projects, "group")
    w(os.path.join(g, "HANDOFF.md"), ho("PARENT-UNRELATED"))
    c = os.path.join(g, "child")
    w(os.path.join(c, "CLAUDE.md"), "x")
    os.symlink("../HANDOFF.md", os.path.join(c, "HANDOFF.md"))
    ok, a, b = E.both(c)
    hp = os.path.join(g, "HANDOFF.md")
    return ok and "PARENT-UNRELATED" not in a + b and one_line_hint(b, hp), a + " || " + b


@case("S12", "符号链接说明文件：child/CLAUDE.md -> ../CLAUDE.md（共用说明）仍标出子项目根；子项目自己的 交接.md 优先")
def _(E):
    g = os.path.join(E.projects, "group")
    w(os.path.join(g, "CLAUDE.md"), "x")
    w(os.path.join(g, "HANDOFF.md"), ho("PARENT-UNRELATED"))
    c = os.path.join(g, "child")
    os.makedirs(c)
    os.symlink("../CLAUDE.md", os.path.join(c, "CLAUDE.md"))
    w(os.path.join(c, "notes", "x.md"), "x")
    w(os.path.join(c, "交接.md"), ho("CHILD-OWN"))
    ok, a, b = E.both(os.path.join(c, "notes"))
    meta = E.profile(os.path.join(c, "notes"))["_meta"]
    return ok and "CHILD-OWN" in a + b and "PARENT-UNRELATED" not in a + b and meta["root"] == c, meta["root"]


@case("S13", "没带标记的 进度.md 不单独立项目根（泛称，可能只是笔记）：group/HANDOFF.md + group/notes/进度.md → "
             "cwd=notes 属于 group，用 group 的 HANDOFF.md；带上标记后 notes 自成项目")
def _(E):
    g = os.path.join(E.projects, "group")
    w(os.path.join(g, "HANDOFF.md"), ho("GROUP-OWN"))
    n = os.path.join(g, "notes")
    w(os.path.join(n, "进度.md"), "# 进度\n- NOTES-PLAIN\n")
    ok1, a, b = E.both(n)
    r1 = E.profile(n)["_meta"]["root"]
    w(os.path.join(n, "进度.md"), ho("NOTES-MARKED"))
    ok2, x, y = E.both(n)
    r2 = E.profile(n)["_meta"]["root"]
    good = ok1 and ok2 and "GROUP-OWN" in a + b and r1 == g and "NOTES-MARKED" in x + y and "GROUP-OWN" not in x + y \
        and r2 == n
    return good, f"r1={r1} r2={r2}"


@case("S14", "git 子模块（.git 是文件）没有交接、上级仓库有 → 不注入上级仓库正文（子模块是独立仓库），只提一行路径")
def _(E):
    sup = git(os.path.join(E.projects, "super"))
    w(os.path.join(sup, "HANDOFF.md"), ho("SUPER-OWN"))
    sub = os.path.join(sup, "vendor", "lib")
    w(os.path.join(sub, ".git"), "gitdir: ../../.git/modules/lib\n")
    ok, a, b = E.both(sub)
    hp = os.path.join(sup, "HANDOFF.md")
    return ok and "SUPER-OWN" not in a + b and one_line_hint(b, hp), a + " || " + b


@case("S15", "monorepo：repo(.git+项目交接.md)/packages/a(package.json+CLAUDE.md)/src → 用仓库根 项目交接.md；画像根=repo")
def _(E):
    repo = git(os.path.join(E.projects, "mono"))
    w(os.path.join(repo, "项目交接.md"), ho("MONO-OWN"))
    a_ = os.path.join(repo, "packages", "a")
    w(os.path.join(a_, "package.json"), "{}")
    w(os.path.join(a_, "CLAUDE.md"), "x")
    src = os.path.join(a_, "src")
    os.makedirs(src)
    ok, a, b = E.both(src)
    meta = E.profile(src)["_meta"]
    return ok and "MONO-OWN" in a and "MONO-OWN" in b and meta["root"] == repo, meta["root"]


@case("S16", "~/Documents（不是配置的容器）下有 HANDOFF.md 和 2 个 git 仓库 → 它是隐式容器：cwd=~/Documents/x 不注入它的"
             "交接也不提示；~/HANDOFF.md 永远不当上一级（~ 单独算）")
def _(E):
    docs = os.path.join(E.home, "Documents")
    w(os.path.join(docs, "HANDOFF.md"), ho("DOCS-LEVEL"))
    w(os.path.join(E.home, "HANDOFF.md"), ho("HOME-LEVEL"))
    git(os.path.join(docs, "r1"))
    git(os.path.join(docs, "r2"))
    x = os.path.join(docs, "x")
    w(os.path.join(x, "CLAUDE.md"), "x")
    ok, a, b = E.both(x)
    y = os.path.join(docs, "y", "deep")
    os.makedirs(y)
    ok2, c, d = E.both(y)
    good = ok and ok2 and a == b == c == d == ""
    return good, f"{a!r} {b!r} {c!r} {d!r}"


@case("S17", "~/Documents 不是容器（没有 git 仓库）但有 HANDOFF.md：cwd=~/Documents/x（CLAUDE.md）→ 只提一行路径；"
             "~/HANDOFF.md 不提示")
def _(E):
    docs = os.path.join(E.home, "Documents")
    w(os.path.join(docs, "HANDOFF.md"), ho("DOCS-LEVEL"))
    w(os.path.join(E.home, "HANDOFF.md"), ho("HOME-LEVEL"))
    x = os.path.join(docs, "x")
    w(os.path.join(x, "CLAUDE.md"), "x")
    ok, a, b = E.both(x)
    hp = os.path.join(docs, "HANDOFF.md")
    return ok and one_line_hint(a, hp) and one_line_hint(b, hp) and "HOME-LEVEL" not in a + b \
        and os.path.join(E.home, "HANDOFF.md") not in a + b, a + " || " + b


@case("S18", "quant-backtest 形状：git + CLAUDE.md + AGENTS.md + 项目交接.md + requirements.txt，cwd=src/engine → 项目交接.md")
def _(E):
    q = git(os.path.join(E.projects, "qb"))
    for f in ("CLAUDE.md", "AGENTS.md", "requirements.txt"):
        w(os.path.join(q, f), "x")
    w(os.path.join(q, "项目交接.md"), ho("QB-OWN"))
    s = os.path.join(q, "src", "engine")
    w(os.path.join(s, "x.py"))
    ok, a, b = E.both(s)
    agr, info = E.agree(s)
    return ok and "QB-OWN" in a and "QB-OWN" in b and agr, info


@case("S19", "不在任何容器里的独立项目：~/Documents/lean-x（lakefile、交接.md），cwd=lean-x/Src → 用它；~/Documents 不越过")
def _(E):
    lx = os.path.join(E.home, "Documents", "lean-x")
    w(os.path.join(lx, "lakefile.lean"))
    w(os.path.join(lx, "交接.md"), ho("LX-OWN"))
    src = os.path.join(lx, "Src")
    os.makedirs(src)
    ok, a, b = E.both(src)
    agr, info = E.agree(src)
    return ok and "LX-OWN" in a and "LX-OWN" in b and agr, info


@case("S20", "一致性：同一批 cwd 上 hook 内部算的根 == 画像 _meta.root（同一个 root_info），逐个比较")
def _(E):
    g, c = codex_fixture(E, "AGENTS.md")
    repo = git(os.path.join(E.projects, "repo"))
    w(os.path.join(repo, "pkg", "package.json"), "{}")
    w(os.path.join(repo, "child", "交接.md"), ho("X"))
    cwds = [c, g, os.path.join(repo, "pkg"), os.path.join(repo, "child"), repo, os.path.join(E.projects, "plain")]
    os.makedirs(cwds[-1])
    bad = []
    for d in cwds:
        r = E.unit(f"m=_pp(); print(json.dumps(m.root_info({d!r})['root']))")
        meta = E.profile(d)["_meta"]["root"]
        if r != meta:
            bad.append((d, r, meta))
    return not bad, str(bad)


@case("S21", "降级模式（hooks 旁边没有 tools/project_profile.py）：不报错、照旧规则（.git 为界、不越过 Projects）")
def _(E):
    lone = os.path.join(E.root, "lone", "hooks")
    os.makedirs(lone)
    shutil.copy(E.hook_path(), lone)
    hook = os.path.join(lone, "session_resume.py")
    w(os.path.join(E.projects, "HANDOFF.md"), ho("PROJECTS-LEVEL"))
    a = git(os.path.join(E.projects, "a"))
    pkg = os.path.join(a, "pkg")
    os.makedirs(pkg)
    ok1, x, y = E.both(pkg, hook)
    w(os.path.join(a, "HANDOFF.md"), ho("A-OWN"))
    ok2, p, q = E.both(pkg, hook)
    return ok1 and ok2 and "PROJECTS-LEVEL" not in x + y + p + q and "A-OWN" in p and "A-OWN" in q, q[:150]


@case("S22", "cwd=group 本身（Codex 夹具的父目录）→ 仍用 group/HANDOFF.md（不受子项目影响）")
def _(E):
    g, c = codex_fixture(E, "CLAUDE.md")
    ok, a, b = E.both(g)
    return ok and "UNRELATED-PARENT" in a and "UNRELATED-PARENT" in b and "CHILD-OWN" not in a + b, b[:150]


@case("S23", "git 仓库里的子目录有 CLAUDE.md + 没带标记的 进度.md → 是子项目自己的进度：根=子目录，不注入仓库根的正文"
             "（进度.md 没标记，只给路径）")
def _(E):
    repo = git(os.path.join(E.projects, "repo"))
    w(os.path.join(repo, "HANDOFF.md"), ho("REPO-OWN"))
    child = os.path.join(repo, "child")
    w(os.path.join(child, "CLAUDE.md"), "x")
    w(os.path.join(child, "进度.md"), "# 进度\n- CHILD-PLAIN\n")
    ok, a, b = E.both(child)
    meta = E.profile(child)["_meta"]
    hp = os.path.join(child, "进度.md")
    good = ok and "REPO-OWN" not in a + b and hp in a and hp in b and "CHILD-PLAIN" not in a + b and meta["root"] == child
    return good, f"{meta['root']} {b[:160]}"


@case("S24", "Codex 夹具的变体：子项目 进度.md 没带标记 → 仍是子项目的（同目录有 CLAUDE.md），只给路径；不注入父目录")
def _(E):
    g, c = codex_fixture(E, "CLAUDE.md")
    w(os.path.join(c, "进度.md"), "## 下一步\n- CHILD-PLAIN\n")
    ok, a, b = E.both(c)
    hp = os.path.join(c, "进度.md")
    return ok and "UNRELATED-PARENT" not in a + b and hp in a and hp in b, b[:200]


@case("S25", "提示不越过容器：top(HANDOFF)/hub(AGENTS.md，下有 2 个子项目，隐式容器)/p1(CLAUDE.md，无交接) → 不提示 top 的交接；"
             "hub 换成普通目录（只有 1 个子项目）时提示 top 的交接")
def _(E):
    top = os.path.join(E.projects, "top")
    w(os.path.join(top, "HANDOFF.md"), ho("TOP-OWN"))
    hub = os.path.join(top, "hub")
    w(os.path.join(hub, "AGENTS.md"), "x")
    p1 = os.path.join(hub, "p1")
    w(os.path.join(p1, "CLAUDE.md"), "x")
    ok1, a, b = E.both(p1)
    w(os.path.join(hub, "p2", "lakefile.toml"))
    ok2, x, y = E.both(p1)
    hp = os.path.join(top, "HANDOFF.md")
    good = ok1 and ok2 and one_line_hint(b, hp) and x == "" and y == "" and "TOP-OWN" not in a + b
    return good, f"一个子项目：{b!r} 两个：{y!r}"


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
        E = Env(suite, cid)
        try:
            good, info = fn(E)
        except Exception as e:
            good, info = False, f"{type(e).__name__}: {e}"
        ok += bool(good)
        print(f"{cid}  {'PASS' if good else 'FAIL'}  {desc}")
        if not good:
            print(f"      → {str(info)[:500]}")
    print(f"\nR6I3 测试（套件 {suite}；夹具在 {RUN_ROOT}，未删除）：{ok}/{n} 通过")
    sys.exit(0 if ok == n else 1)


if __name__ == "__main__":
    main()
