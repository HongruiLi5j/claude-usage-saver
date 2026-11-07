#!/usr/bin/env python3
"""R5I：项目画像 + 接入它的 hooks 的测试（临时目录里造项目，不碰真实项目、真实 ~/.claude、真实 state/）。

用法：python3 profiles/test_project_profile.py [-k 关键字]
造 5 种项目：Python（tests/ 直接跑的脚本 + pytest 配置）、Lean（lakefile，无 git）、Node（package.json）、
纯文档、无 git 的空目录；另有覆盖文件、安全、缓存、SR06、CG13、上下文窗口的用例。
所有 hook 以子进程运行真实的 hooks/*.py（它们按脚本位置找到真实的 tools/project_profile.py），
HOME 和 USAGE_SAVER_STATE 指向临时目录。
"""
import json, os, shutil, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import project_profile as P  # noqa: E402

MARK = "<!-- usage-saver-handoff -->"
CASES = []


def case(cid, desc):
    def deco(fn):
        CASES.append((cid, desc, fn))
        return fn
    return deco


def w(path, txt="", age_h=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(txt)
    if age_h is not None:
        t = time.time() - age_h * 3600
        os.utime(path, (t, t))
    return path


class Env:
    def __init__(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="r5I-"))
        self.home = os.path.join(self.root, "home")
        self.projects = os.path.join(self.home, "Documents", "Projects")
        self.state = os.path.join(self.root, "state")
        self.tdir = os.path.join(self.home, ".claude", "projects", "-x")
        for d in (self.projects, self.state, self.tdir):
            os.makedirs(d, exist_ok=True)

    def env(self, **kw):
        e = {**os.environ, "HOME": self.home, "USAGE_SAVER_STATE": self.state}
        e.pop("USAGE_SAVER_CONTAINERS", None)
        e.update(kw)
        return e

    def run(self, argv, stdin="", cwd=None, **kw):
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, cwd=cwd or self.root,
                           env=self.env(**kw), timeout=60)
        return p.returncode, p.stdout, p.stderr

    def hook(self, name, payload, **kw):
        return self.run([sys.executable, os.path.join(ROOT, "hooks", name)], json.dumps(payload), **kw)

    def profile(self, cwd, *extra):
        rc, out, err = self.run([sys.executable, os.path.join(ROOT, "tools", "project_profile.py"), "--cwd", cwd,
                                 "--json", *extra])
        return json.loads(out)

    def tf(self, cmd, cwd):
        rc, out, err = self.hook("test_filter.py", {"tool_input": {"command": cmd}, "cwd": cwd})
        if not out.strip():
            return None
        return json.loads(out)["hookSpecificOutput"]["updatedInput"]["command"]

    def transcript(self, name, users=("旧对话 OLDMSG",)):
        p = os.path.join(self.tdir, name + ".jsonl")
        with open(p, "w") as fh:
            for u in users:
                fh.write(json.dumps({"type": "user", "timestamp": "2026-11-06T10:00:00Z",
                                     "message": {"role": "user", "content": u}}) + "\n")
                fh.write(json.dumps({"type": "assistant", "timestamp": "2026-11-06T10:01:00Z",
                                     "message": {"role": "assistant", "content": [{"type": "text", "text": "好的 OLDREPLY"}]}}) + "\n")
        return p

    def resume(self, cwd, source="clear", sid="NEW", tp=None):
        tp = tp or self.transcript(sid, users=())
        rc, out, err = self.hook("session_resume.py", {"hook_event_name": "SessionStart", "source": source,
                                                       "cwd": cwd, "session_id": sid, "transcript_path": tp})
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""
        return rc, ctx, err


# ---------------------------------------------------------------- 造 5 种项目
def mk_python(E, name="py-proj", git=True):
    r = os.path.join(E.projects, name)
    if git:
        os.makedirs(os.path.join(r, ".git"), exist_ok=True)
    w(os.path.join(r, "pyproject.toml"), "[project]\nname='x'\ndependencies=['pandas']\n[tool.pytest.ini_options]\ntestpaths=['tests']\n")
    w(os.path.join(r, "tests", "test_core.py"), "import sys\nprint('\\n'.join('line %d' % i for i in range(80)))\n"
                                                 "print('FAILED test_core::x 失败')\nsys.exit(3)\n")
    w(os.path.join(r, "src", "core.py"), "x = 1\n")
    return r


def mk_lean(E, name="lean-proj"):
    r = os.path.join(E.home, "Documents", name)        # 和真实 lean-mc-variance 一样：在 Projects 外、无 git
    w(os.path.join(r, "lakefile.toml"), 'name = "X"\n[[require]]\nname = "mathlib"\n')
    w(os.path.join(r, "lean-toolchain"), "leanprover/lean4:v4.0.0\n")
    w(os.path.join(r, "X", "Basic.lean"), "theorem t : True := by sorry\n")
    return r


def mk_node(E, name="web-proj"):
    r = os.path.join(E.projects, name)
    os.makedirs(os.path.join(r, ".git"), exist_ok=True)
    w(os.path.join(r, "package.json"), json.dumps({"name": "web", "scripts": {"test": "vitest run", "test:e2e": "playwright test"},
                                                   "devDependencies": {"vitest": "1", "react": "18"}}))
    w(os.path.join(r, "pnpm-lock.yaml"), "lockfileVersion: 9\n")
    w(os.path.join(r, "src", "App.tsx"), "export const A = 1\n")
    w(os.path.join(r, "src", "App.test.ts"), "test('x',()=>{})\n")
    return r


def mk_docs(E, name="notes"):
    r = os.path.join(E.projects, name)
    os.makedirs(os.path.join(r, ".git"), exist_ok=True)
    for i in range(6):
        w(os.path.join(r, f"ch{i}.md"), f"# 第 {i} 章\n")
    w(os.path.join(r, "CLAUDE.md"), "# 说明\n## 约定\n- 不要改 ch0.md 的标题\n- 交接写在 `进度记录.md`\n")
    return r


def mk_empty(E, name="scratch"):
    r = os.path.join(E.home, "scratch", name)          # 无 git、无任何标记
    os.makedirs(r, exist_ok=True)
    return r


# ---------------------------------------------------------------- 画像识别
@case("P01", "Python 项目：语言 python、框架 pandas、测试命令含 pytest 和直接跑的 tests/test_*.py；根=git")
def _(E):
    p = E.profile(mk_python(E))
    cmds = [c["cmd"] for c in p["tests"]["commands"]]
    return (p["languages"] == ["python"] and "pandas" in p["frameworks"] and p["_meta"]["root_how"] == "git"
            and any("pytest" in c for c in cmds) and any("<test_*.py>" in c for c in cmds)), cmds


@case("P02", "Lean 项目（Projects 外、无 git）：根=manifest，lake build，grep 片段含 sorry，框架 mathlib")
def _(E):
    p = E.profile(os.path.join(mk_lean(E), "X"))
    return (p["languages"] == ["lean"] and p["_meta"]["root_how"] == "manifest" and p["tests"]["primary"] == "lake build"
            and "sorry" in (p["tests"]["grep_extra"] or "") and "mathlib" in p["frameworks"]), p["_meta"]


@case("P03", "Node 项目：pnpm test、pnpm run test:e2e、npx vitest；框架 react/vitest")
def _(E):
    p = E.profile(mk_node(E))
    cmds = [c["cmd"] for c in p["tests"]["commands"]]
    return ("pnpm test" in cmds and "pnpm run test:e2e" in cmds and "npx vitest" in cmds
            and "react" in p["frameworks"] and p["languages"][0] in ("typescript", "javascript")), cmds


@case("P04", "纯文档项目：kind=docs、没有测试命令；CLAUDE.md 的约定被抽成规则，提到的交接文件进候选")
def _(E):
    r = mk_docs(E)
    w(os.path.join(r, "进度记录.md"), "# 进度\n")
    p = E.profile(r)
    return (p["kind"] == "docs" and not p["tests"]["commands"] and any("不要改" in x["text"] for x in p["instructions"]["rules"])
            and "进度记录.md" in p["handoff"]["secondary_names"]), (p["kind"], p["handoff"]["secondary_names"][:3])


@case("P05", "无 git 的空目录：kind=empty、无测试、root=cwd，交接默认名 HANDOFF.md；不报错")
def _(E):
    p = E.profile(mk_empty(E))
    return (p["kind"] == "empty" and p["tests"]["commands"] == [] and p["_meta"]["root_how"] == "cwd"
            and p["handoff"]["primary_names"][0] == "HANDOFF.md" and not p["_meta"].get("error")), p["_meta"]


@case("P06", "不存在的目录 / 文件当 cwd：返回画像、不抛异常")
def _(E):
    p1 = P.get_profile(os.path.join(E.root, "nope", "x"), write_cache=False)
    f = w(os.path.join(E.root, "afile.txt"), "x")
    p2 = P.get_profile(f, write_cache=False)
    return isinstance(p1, dict) and isinstance(p2, dict) and "tests" in p1 and "tests" in p2, None


# ---------------------------------------------------------------- 覆盖文件 + 安全
@case("O01", "覆盖文件：阈值、测试命令、grep、交接文件名、章节名都生效，且排在自动识别前面")
def _(E):
    r = mk_python(E, "ov")
    w(os.path.join(r, ".usage-saver.json"), json.dumps({
        "thresholds": {"soft_tokens": 210000, "strong_tokens": 330000}, "test_commands": ["python3 tools/scorecard.py"],
        "test_grep_extra": "不通过|✗", "handoff": {"files": ["NOTES/session.md"], "sections": {"red": ["Guardrails"], "next": ["Todo"]}}}))
    p = E.profile(r)
    return (p["thresholds"]["soft_tokens"] == 210000 and p["tests"]["commands"][0]["cmd"] == "python3 tools/scorecard.py"
            and p["tests"]["grep_extra"] == "不通过|✗" and p["handoff"]["primary_names"][0] == "NOTES/session.md"
            and "Guardrails" in p["handoff"]["section_patterns"]["red"] and not p["override"]["errors"]), p["override"]


@case("O02", "覆盖文件安全：'*'、bash、cat 当测试命令被拒；grep 含单引号被拒；../ 和绝对路径交接文件被拒；阈值夹在 [5万,100万]")
def _(E):
    r = mk_python(E, "evil")
    w(os.path.join(r, ".usage-saver.json"), json.dumps({
        "thresholds": {"soft_tokens": 10, "strong_tokens": "x"}, "test_commands": ["*", "bash -c x", "cat", "lake build"],
        "test_grep_extra": "x' ; rm -rf ~ ; '", "handoff": {"files": ["../other/HANDOFF.md", "/etc/passwd", "ok.md"]}}))
    p = E.profile(r)
    cm = [c["cmd"] for c in p["tests"]["commands"]]
    ok = ("*" not in cm and "bash -c x" not in cm and "cat" not in cm and "lake build" in cm
          and "'" not in (p["tests"]["grep_extra"] or "") and p["handoff"]["primary_names"][0] == "ok.md"
          and not any(".." in n or n.startswith("/") for n in p["handoff"]["primary_names"])
          and p["thresholds"]["soft_tokens"] == 50000 and len(p["override"]["errors"]) >= 4)
    return ok, (cm[:4], p["override"]["errors"])


@case("O03", "覆盖文件坏 JSON：不崩，记错误，其余按自动识别")
def _(E):
    r = mk_python(E, "badjson")
    w(os.path.join(r, ".usage-saver.json"), "{not json")
    p = E.profile(r)
    return p["override"]["errors"] and p["languages"] == ["python"], p["override"]


# ---------------------------------------------------------------- 缓存
@case("C01", "缓存：第二次命中；改覆盖文件 / 新建根目录文件后指纹变了重算；过期后重算")
def _(E):
    r = mk_python(E, "cache")
    a = E.profile(r)
    b = E.profile(r)
    time.sleep(0.01)
    w(os.path.join(r, ".usage-saver.json"), json.dumps({"thresholds": {"soft_tokens": 200000}}))
    c = E.profile(r)
    path = os.path.join(E.state, "profiles", a["_meta"]["key"] + ".json")
    d = json.load(open(path)); d["_meta"]["expires_at"] = 1; json.dump(d, open(path, "w"))
    e = E.profile(r)
    return ([x["_meta"]["cache"] for x in (a, b, c, e)] == ["miss", "hit", "miss", "miss"]
            and c["thresholds"]["soft_tokens"] == 200000 and os.path.isfile(path)), [x["_meta"]["cache"] for x in (a, b, c, e)]


@case("C02", "--no-write 完全只读：不写 state/profiles")
def _(E):
    r = mk_python(E, "ro")
    E.profile(r, "--no-write", "--no-cache")
    return not os.path.exists(os.path.join(E.state, "profiles")), None


@case("C03", "缓存键含路径哈希：两个同名目录（AQ-sandbox 与 AQ-archive/AQ-sandbox）不共用缓存")
def _(E):
    a = os.path.join(E.projects, "AQ-sandbox"); b = os.path.join(E.home, "Documents", "AQ-archive", "AQ-sandbox")
    for x in (a, b):
        os.makedirs(os.path.join(x, ".git"), exist_ok=True)
    return P.cache_key(a) != P.cache_key(b) and P.cache_key(a).startswith("AQ-sandbox-"), None


# ---------------------------------------------------------------- test_filter 接入
@case("T01", "Python：`python3 tests/test_core.py`（内置规则不认）被画像认出并包装；rc=3 保留，FAILED/失败 行可见，80 行被过滤")
def _(E):
    r = mk_python(E)
    new = E.tf("python3 tests/test_core.py", r)
    if not new:
        return False, "未包装"
    rc, out, err = E.run(["/bin/bash", "-c", new], cwd=r)
    return rc == 3 and "FAILED test_core::x" in out and "line 30" not in out, (rc, out[-300:])


@case("T02", "Lean：`lake build` 在 Lean 项目里包装，grep 含 sorry；同一命令在 Python 项目里不包装（不套用别的项目）")
def _(E):
    ln, py = mk_lean(E), mk_python(E)
    a, b = E.tf("lake build", ln), E.tf("lake build", py)
    return bool(a) and "sorry" in a and b is None, (bool(a), b)


@case("T03", "Node：`pnpm test`、`npx vitest run` 包装；`pnpm install`、`npm run dev` 不包装")
def _(E):
    r = mk_node(E)
    return (bool(E.tf("pnpm test", r)) and bool(E.tf("npx vitest run", r)) and E.tf("pnpm install", r) is None
            and E.tf("npm run dev", r) is None), None


@case("T04", "纯文档、空目录：`make test`、`lake build`、`python3 x.py` 都不包装；pytest 仍按内置规则包装（与改前一致）")
def _(E):
    d, e = mk_docs(E), mk_empty(E)
    none = all(E.tf(c, x) is None for c in ("make test", "lake build", "python3 x.py") for x in (d, e))
    return none and bool(E.tf("pytest -q", d)) and bool(E.tf("pytest -q", e)), None


@case("T05", "内置规则认出的命令：有画像时改写结果与没有画像（不给 cwd）时逐字相同")
def _(E):
    # R6T：原来和 backups/test_filter.py.pre-r5I 逐字比；第 6 轮 T 有意改了摘要（wrap 调 --summarize），
    # 和旧备份必然不同，所以改成比「同一个 hook 有画像 vs 没画像」——这才是 R5I 要守的不变量（画像不影响内置规则）。
    r = mk_lean(E)
    cmds = ["pytest -q", "npm test", "node --test x.mjs", "python3 -m pytest -x; echo ok", "cd a && pytest"]
    hook = os.path.join(ROOT, "hooks", "test_filter.py")
    same = []
    for c in cmds:
        a = E.run([sys.executable, hook], json.dumps({"tool_input": {"command": c}, "cwd": r}))[1]
        b = E.run([sys.executable, hook], json.dumps({"tool_input": {"command": c}}))[1]
        same.append(a == b and bool(a))
    return all(same), same


@case("T06", "覆盖文件的测试命令（`python3 tools/scorecard.py`）被包装；中文「不通过」行在摘要里可见")
def _(E):
    r = mk_python(E, "ovtf")
    w(os.path.join(r, "tools", "scorecard.py"), "print('\\n'.join('ok %d' % i for i in range(60)))\nprint('SC09 不通过')\n")
    w(os.path.join(r, ".usage-saver.json"), json.dumps({"test_commands": ["python3 tools/scorecard.py"], "test_grep_extra": "不通过"}))
    new = E.tf("python3 tools/scorecard.py", r)
    if not new:
        return False, "未包装"
    rc, out, err = E.run(["/bin/bash", "-c", new], cwd=r)
    return "SC09 不通过" in out and "ok 20\n" not in out, out[-200:]


@case("T07", "画像模块坏掉（覆盖文件让画像抛异常也一样）：test_filter 退出码 0，不包装非内置命令")
def _(E):
    r = mk_lean(E)
    rc, out, err = E.hook("test_filter.py", {"tool_input": {"command": "lake build"}, "cwd": r},
                          USAGE_SAVER_STATE="/dev/null/nope")      # 缓存写不了
    return rc == 0 and "Traceback" not in err, (rc, err[-200:])


# ---------------------------------------------------------------- session_resume 接入
@case("S01", "同义写法：项目只有 `交接.md`（带标记）→ clear 注入它；compact 用宽同义标题「硬规则 / 接下来」")
def _(E):
    r = mk_python(E, "zh")
    w(os.path.join(r, "交接.md"), f"{MARK}\n# 交接\n## 背景\n很长的背景 BG\n## 硬规则\n- 不 push ZH-RED\n## 接下来\n1. 跑测试 ZH-NEXT\n")
    _, a, _ = E.resume(r)
    _, b, _ = E.resume(r, source="compact", sid="C1")
    return "ZH-RED" in a and "ZH-RED" in b and "ZH-NEXT" in b and "BG" not in b, b[-300:]


@case("S02", "HANDOFF.md 和 交接.md 都在：仍用 HANDOFF.md（原名优先，行为与改前一致）")
def _(E):
    r = mk_python(E, "both")
    w(os.path.join(r, "交接.md"), f"{MARK}\nZH-BODY\n")
    w(os.path.join(r, "HANDOFF.md"), f"{MARK}\nEN-BODY\n")
    _, a, _ = E.resume(os.path.join(r, "src"))
    return "EN-BODY" in a and "ZH-BODY" not in a, None


@case("S03", "覆盖文件指定交接文件和章节名：注入 NOTES/session.md；compact 取「Guardrails / Todo」")
def _(E):
    r = mk_node(E, "ovr")
    w(os.path.join(r, ".usage-saver.json"), json.dumps({"handoff": {"files": ["NOTES/session.md"],
                                                                    "sections": {"red": ["Guardrails"], "next": ["Todo"]}}}))
    w(os.path.join(r, "NOTES", "session.md"), f"{MARK}\n# Session\n## History\nOLD-HIST\n## Guardrails\n- no deploy OV-RED\n## Todo\n- wire API OV-NEXT\n")
    _, a, _ = E.resume(r)
    _, b, _ = E.resume(r, source="compact", sid="C2")
    return "OV-RED" in a and "OV-RED" in b and "OV-NEXT" in b and "OLD-HIST" not in b, b[-300:]


@case("S04", "无 git 的 Lean 项目：不爬到 ~/Documents/HANDOFF.md（别的项目的交接）")
def _(E):
    r = mk_lean(E)
    w(os.path.join(E.home, "Documents", "HANDOFF.md"), f"{MARK}\nOTHER-PROJECT\n")
    _, a, _ = E.resume(os.path.join(r, "X"))
    return "OTHER-PROJECT" not in a, a[:200]


@case("S05", "说明文件里提到的交接文件（文档项目 CLAUDE.md 写「交接写在 `进度记录.md`」）被找到；无标记 → 只给路径")
def _(E):
    r = mk_docs(E)
    w(os.path.join(r, "进度记录.md"), "# 进度\n- 第 3 章写完 DOC-BODY\n")
    _, a, _ = E.resume(r)
    return "进度记录.md" in a and "DOC-BODY" not in a, a[:300]


@case("S06", "SR06：同一进程 cwd=A /clear 后在 cwd=B 开新会话 → 不带 A 的对话摘要；对照：同 cwd → 带")
def _(E):
    a, b = mk_python(E, "A"), mk_python(E, "B")
    tp = E.transcript("OLD1")
    E.hook("session_resume.py", {"hook_event_name": "SessionEnd", "reason": "clear", "cwd": a,
                                 "session_id": "OLD1", "transcript_path": tp})
    _, ctx_b, _ = E.resume(b, sid="N1", tp=E.transcript("N1", users=()))
    tp2 = E.transcript("OLD2")
    E.hook("session_resume.py", {"hook_event_name": "SessionEnd", "reason": "clear", "cwd": a,
                                 "session_id": "OLD2", "transcript_path": tp2})
    _, ctx_a, _ = E.resume(a, sid="N2", tp=E.transcript("N2", users=()))
    return "OLDMSG" not in ctx_b and "OLDMSG" in ctx_a, (ctx_b[:120], ctx_a[:120])


# ---------------------------------------------------------------- context_guard / statusline（CG13、窗口）
def guard_tp(E, n, model="claude-opus-5-5"):
    p = os.path.join(E.tdir, "g.jsonl")
    with open(p, "w") as fh:
        fh.write(json.dumps({"type": "assistant", "message": {"model": model, "usage": {
            "input_tokens": 10, "cache_read_input_tokens": n - 10, "cache_creation_input_tokens": 0}}}) + "\n")
    return p


def suite_copy(E, with_cfg=True):
    """把 hooks + tools/project_profile.py 复制到假 HOME 下的套件目录（CG13：按脚本位置找 config）。"""
    s = os.path.join(E.root, "suite")
    for sub in ("hooks", "tools"):
        os.makedirs(os.path.join(s, sub), exist_ok=True)
    for f in ("context_guard.py", "statusline.sh"):
        shutil.copy(os.path.join(ROOT, "hooks", f), os.path.join(s, "hooks", f))
    shutil.copy(os.path.join(ROOT, "tools", "project_profile.py"), os.path.join(s, "tools"))
    if with_cfg:
        c = json.load(open(os.path.join(ROOT, "config.json"))); c["cap_from_date"] = "2099-01-01"
        json.dump(c, open(os.path.join(s, "config.json"), "w"))
    return s


@case("G01", "CG13：套件放在任意目录也按脚本位置读 config（不看 ~/Documents/Projects/…）；读不到 config → stderr 一行提示 + 默认值，退出码 0")
def _(E):
    s = suite_copy(E)
    c = json.load(open(os.path.join(s, "config.json"))); c["handoff_threshold_tokens"] = 120000
    json.dump(c, open(os.path.join(s, "config.json"), "w"))
    r = mk_python(E, "g1")
    pl = {"cwd": r, "session_id": "g", "transcript_path": guard_tp(E, 130_000)}
    rc1, o1, e1 = E.run([sys.executable, os.path.join(s, "hooks", "context_guard.py")], json.dumps(pl))
    os.rename(os.path.join(s, "config.json"), os.path.join(s, "config.json.off"))
    rc2, o2, e2 = E.run([sys.executable, os.path.join(s, "hooks", "context_guard.py")], json.dumps(dict(pl, session_id="g2")))
    return (rc1 == 0 and "上下文守卫" in o1 and rc2 == 0 and o2 == "" and "读不到" in e2
            and len(e2.strip().splitlines()) == 1), (o1[:60], o2[:60], e2)


@case("G02", "CG13：状态栏写 rate.json 的目录 = 守卫读的目录（USAGE_SAVER_STATE 覆盖；不设时都是 套件/state）")
def _(E):
    s = suite_copy(E)
    r = mk_python(E, "g2")
    pl = {"session_id": "x", "cwd": r, "model": {"id": "claude-opus-5-5", "display_name": "Opus"},
          "context_window": {"current_usage": {"input_tokens": 1, "cache_read_input_tokens": 1000}},
          "rate_limits": {"seven_day": {"used_percentage": 95, "resets_at": int(time.time() + 86400)}}}
    env = {k: v for k, v in E.env().items() if k != "USAGE_SAVER_STATE"}
    p = subprocess.run(["/bin/sh", os.path.join(s, "hooks", "statusline.sh")], input=json.dumps(pl), capture_output=True,
                       text=True, env=env, timeout=20)
    q = subprocess.run([sys.executable, os.path.join(s, "hooks", "context_guard.py"), "--mode"], capture_output=True,
                       text=True, env=env, timeout=20)
    return os.path.isfile(os.path.join(s, "state", "rate.json")) and q.stdout.startswith("快用完"), (p.stdout, q.stdout)


@case("G03", "优先级：项目覆盖 > 套件 config 的 per_project > 默认（quant-backtest 目录里放 soft=200k 覆盖 → 用 200k）")
def _(E):
    s = suite_copy(E)
    qb = os.path.join(E.projects, "quant-backtest"); os.makedirs(os.path.join(qb, ".git"), exist_ok=True)
    code = ("import sys,json; sys.path.insert(0,%r); import context_guard as G; print(json.dumps(G.lines(%r,1.0)[:2]))"
            % (os.path.join(s, "hooks"), qb))
    a = json.loads(E.run([sys.executable, "-c", code])[1])
    w(os.path.join(qb, ".usage-saver.json"), json.dumps({"thresholds": {"soft_tokens": 200000, "strong_tokens": 400000}}))
    b = json.loads(E.run([sys.executable, "-c", code])[1])
    return a[0] == 250000 and b == [200000, 400000], (a, b)


@case("G04", "上下文窗口：状态栏 payload 给 200k 窗口、宽裕档（×1.5=240k）、ctx 180k → 状态栏 ⚠；守卫读 ctx_window.json 也提醒")
def _(E):
    s = suite_copy(E)
    r = mk_python(E, "g4")
    w(os.path.join(E.state, "mode.json"), json.dumps({"ts": int(time.time()), "mult": 1.5}))
    now = time.time()
    w(os.path.join(E.state, "rate.json"), json.dumps({"ts": int(now), "rate_limits": {
        "seven_day": {"used_percentage": 5, "resets_at": int(now + 3600), "sampled_at": int(now)}}}))
    pl = {"session_id": "small", "cwd": r, "model": {"id": "claude-haiku-5", "display_name": "Haiku"},
          "context_window": {"context_window_size": 200000,
                             "current_usage": {"input_tokens": 1, "cache_read_input_tokens": 180000}}}
    rc, out, err = E.run(["/bin/sh", os.path.join(s, "hooks", "statusline.sh")], json.dumps(pl))
    g = E.run([sys.executable, os.path.join(s, "hooks", "context_guard.py")],
              json.dumps({"cwd": r, "session_id": "small", "transcript_path": guard_tp(E, 180_000, "claude-haiku-5")}))
    g2 = E.run([sys.executable, os.path.join(s, "hooks", "context_guard.py")],
               json.dumps({"cwd": r, "session_id": "other", "transcript_path": guard_tp(E, 180_000)}))
    return "⚠" in out and "上下文守卫" in g[1] and g2[1] == "", (out, g[1][:80], g2[1][:80])


@case("M01", "model_info：opus/sonnet/haiku 识别；claude-xyz-9 → unknown、价格权重 1.0（保守）、窗口默认 200k；payload 给窗口时用 payload")
def _(E):
    a, b, c = P.model_info("claude-opus-5-5"), P.model_info("claude-xyz-9"), P.model_info("claude-xyz-9", 1_000_000)
    d = P.model_info(None, "abc")
    return (a["family"] == "opus" and b["family"] == "unknown" and b["price_weight"] == 1.0 and b["context_window"] == 200000
            and c["context_window"] == 1_000_000 and d["context_window"] == 200000), (a, b, c)


@case("G05", "last_usage：200k 之后跟一条 API 报错的全 0 usage → 仍按 200k 提醒，且不清掉本会话的去重记录（改前版本：不提醒）")
def _(E):
    s = suite_copy(E)
    r = mk_python(E, "g5")
    p = os.path.join(E.tdir, "z.jsonl")
    with open(p, "w") as fh:
        fh.write(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 199_990,
                                                                        "cache_creation_input_tokens": 0}}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 0, "cache_read_input_tokens": 0,
                                                                        "cache_creation_input_tokens": 0, "output_tokens": 0}}}) + "\n")
    w(os.path.join(E.state, "remind.json"), json.dumps({"z": {"level": "soft", "at_n": 190000, "since": 3, "ts": int(time.time())}}))
    pl = json.dumps({"cwd": r, "session_id": "z2", "transcript_path": p})
    rc, out, err = E.run([sys.executable, os.path.join(s, "hooks", "context_guard.py")], pl)
    rc2, out2, _ = E.run([sys.executable, os.path.join(s, "hooks", "context_guard.py")],
                         json.dumps({"cwd": r, "session_id": "z", "transcript_path": p}))
    rem = json.load(open(os.path.join(E.state, "remind.json")))
    shutil.copy(os.path.join(ROOT, "backups", "context_guard.py.pre-r5I"), os.path.join(s, "hooks", "context_guard.py"))
    rc3, out3, _ = E.run([sys.executable, os.path.join(s, "hooks", "context_guard.py")], json.dumps({"cwd": r, "session_id": "z3", "transcript_path": p}))
    return ("约 200k" in out and "z" in rem and rem["z"]["at_n"] == 190000 and out3 == ""), (out[:50], rem.get("z"), out3[:40])


def opt_transcript(E, name, text):
    p = os.path.join(E.tdir, name + ".jsonl")
    with open(p, "w") as fh:
        fh.write(json.dumps({"type": "user", "timestamp": "2026-11-06T10:00:00Z", "message": {"role": "user", "content": "怎么改 OLDMSG"}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "timestamp": "2026-11-06T10:01:00Z",
                             "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "timestamp": "2026-11-06T10:01:01Z", "isSidechain": True,
                             "message": {"role": "assistant", "content": [{"type": "text", "text": "子 agent 的话"}]}}) + "\n")
    return p


def clear_with(E, cwd, old_tp, sid):
    E.hook("session_resume.py", {"hook_event_name": "SessionEnd", "reason": "clear", "cwd": cwd,
                                 "session_id": "O" + sid, "transcript_path": old_tp})
    return E.resume(cwd, sid=sid, tp=E.transcript(sid, users=()))[1]


@case("S07", "上一条回复在给编号选项（1. 2. 3. + 「选哪个？」）：clear 后提示按选项编号理解短回复，不说「视为接着做」")
def _(E):
    r = mk_python(E, "opt")
    ctx = clear_with(E, r, opt_transcript(E, "OPT", "有三种改法：\n1. 只改 hook\n2. 改 hook + 测试\n3. 全部重写\n你选哪个？"), "N7")
    return "按选项编号理解" in ctx and "2. 改 hook + 测试" in ctx and "视为「接着做」" not in ctx, ctx[:200]


@case("S08", "上一条回复只是列了编号的下一步、没有提问：仍按「接着做」处理（不误报选项）；字母选项 A) B) + which 也算选项")
def _(E):
    r = mk_python(E, "noopt")
    a = clear_with(E, r, opt_transcript(E, "NO", "做完了。下一步：\n1. 跑测试\n2. 写报告\n我接下来先做第 1 步。"), "N8")
    b = clear_with(E, mk_python(E, "abc"), opt_transcript(E, "AB", "Two ways:\nA) keep it\nB) revert it\nWhich do you prefer"), "N9")
    return "视为「接着做」" in a and "按选项编号理解" not in a and "按选项编号理解" in b, (a[:80], b[:80])


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("-k")
    a = ap.parse_args()
    ok = n = 0
    for cid, desc, fn in CASES:
        if a.k and a.k not in cid:
            continue
        E = Env()
        n += 1
        try:
            res = fn(E)
            passed, info = res if isinstance(res, tuple) else (res, None)
        except Exception as e:
            passed, info = False, f"{type(e).__name__}: {e}"
        ok += bool(passed)
        print(f"{cid:4} {'PASS' if passed else 'FAIL'}  {desc}" + ("" if passed else f"\n      〔{str(info)[:400]}〕"))
        shutil.rmtree(E.root, ignore_errors=True)
    print(f"\nR5I 项目画像测试：{ok}/{n} 通过")
    return 0 if ok == n else 1


if __name__ == "__main__":
    sys.exit(main())
