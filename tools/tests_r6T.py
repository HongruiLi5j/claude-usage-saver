#!/usr/bin/env python3
"""第 6 轮 T：test_filter 的 R28（失败多时中段失败丢失）+ 扩大改写范围（timeout、简单前缀 + 最后一段 pytest）。

全部用真实 pytest 执行（当前 python 自带的 pytest；没有就借 quant-backtest/.venv，只读），每个改写形状都在
bash 和 zsh 里各跑一次原命令、一次改写后的命令，比较：退出码、失败测试名集合、短路行为（标记文件）、cwd、环境变量。
不碰真实 state：日志目录、测试项目都在临时目录里。

用法：python3 tools/tests_r6T.py [--hook 路径]      默认测 hooks/test_filter.py
      R6T_HOOK=backups/test_filter.py.pre-r6T python3 tools/tests_r6T.py   # 看旧版哪些不过
输出最后一行：r6T: 通过数/总数 通过；全部通过退出码 0。
"""
import importlib.machinery, importlib.util, json, os, re, shutil, subprocess, sys, tempfile

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.environ.get("R6T_HOOK") or os.path.join(ROOT, "hooks", "test_filter.py")
if "--hook" in sys.argv:
    HOOK = sys.argv[sys.argv.index("--hook") + 1]
HOOK = os.path.abspath(HOOK)
ldr = importlib.machinery.SourceFileLoader("tf_r6T", HOOK)
spec = importlib.util.spec_from_loader("tf_r6T", ldr)
TF = importlib.util.module_from_spec(spec); ldr.exec_module(TF)

SHELLS = [s for s in ("/bin/bash", "/bin/zsh") if os.path.exists(s)]
TMP = tempfile.mkdtemp(prefix="r6T_")
LOGS = os.path.join(TMP, "logs"); os.makedirs(LOGS)
BIN = os.path.join(TMP, "bin"); os.makedirs(BIN)


def find_python():
    """带 pytest 的 python：优先当前解释器，其次 quant-backtest 的 venv（只读借用）。"""
    for py in (sys.executable, os.path.expanduser("~/Documents/Projects/quant-backtest/.venv/bin/python")):
        if py and os.access(py, os.X_OK) and subprocess.run([py, "-c", "import pytest"], capture_output=True).returncode == 0:
            return py
    return None


PY = find_python()
# 假 pytest 入口：PATH 里的 `pytest` 就是 `<带 pytest 的 python> -m pytest -p no:cacheprovider`（真实 pytest，不写缓存）
open(os.path.join(BIN, "pytest"), "w").write('#!/bin/sh\nexec "%s" -m pytest -p no:cacheprovider "$@"\n' % PY)
# macOS 没有 timeout：放一个按 GNU timeout 语义实现的小脚本（选项 -s/-k/--signal=/--kill-after=/--foreground/
# --preserve-status/-v；超时返回 124）
TIMEOUT_SRC = r'''#!%s
import re, subprocess, sys
a = sys.argv[1:]; i = 0
while i < len(a):
    x = a[i]
    if x in ("--foreground", "-v", "--verbose", "--preserve-status"): i += 1
    elif x in ("-s", "--signal", "-k", "--kill-after"): i += 2
    elif x.startswith(("--signal=", "--kill-after=")) or re.fullmatch(r"-[sk]\S+", x): i += 1
    else: break
d = a[i]; mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(d[-1], 1)
dur = float(d.rstrip("smhd")) * mult
p = subprocess.Popen(a[i + 1:])
try:
    sys.exit(p.wait(timeout=dur))
except subprocess.TimeoutExpired:
    p.terminate(); p.wait(); sys.exit(124)
''' % sys.executable
for name in ("timeout", "gtimeout"):
    p = os.path.join(BIN, name); open(p, "w").write(TIMEOUT_SRC)
for name in ("pytest", "timeout", "gtimeout"):
    os.chmod(os.path.join(BIN, name), 0o755)
ENV = {**os.environ, "PATH": BIN + ":" + os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"}
ENV.pop("USAGE_SAVER_RAW", None)

FAILS_R28 = [10, 22, 34, 46, 58, 70, 82, 94, 106, 118, 125, 137, 149, 161, 173, 185, 197, 209, 221, 233, 245, 257,
             269, 281, 293]


def mkproj(name, nfail_ids=(), ntests=6, body_lines=0, extra=""):
    """建一个测试项目目录：test_x.py 里 ntests 个测试，nfail_ids 里的失败；每个失败的测试体多 body_lines 行（撑长输出）。
    模块导入时往 $R6T_MARK 追加一行（用来判断 pytest 到底有没有跑 = 短路行为）。"""
    d = os.path.join(TMP, name); os.makedirs(os.path.join(d, "tests"), exist_ok=True)
    src = ["import os\nif os.environ.get('R6T_MARK'):\n    open(os.environ['R6T_MARK'], 'a').write('ran\\n')\n", extra]
    for i in range(1, ntests + 1):
        if i in nfail_ids:
            body = "".join("    v%d = %d * %d  # 准备数据\n" % (j, i, j) for j in range(body_lines))
            src.append("def test_%03d():\n%s    x = %d\n    assert x == %d + 1, 'value mismatch'\n" % (i, body, i, i))
        else:
            src.append("def test_%03d():\n    assert True\n" % i)
    open(os.path.join(d, "tests", "test_x.py"), "w").write("\n".join(src))
    open(os.path.join(d, "conftest.py"), "w").write("")
    return d


def run(sh, cmd, cwd, mark=None, **env):
    e = dict(ENV, **env)
    if mark:
        e["R6T_MARK"] = mark
    p = subprocess.run([sh, "-c", cmd], cwd=cwd, env=e, capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout + p.stderr


def failed_names(text):
    """输出里所有失败测试名（FAILED/ERROR 行，pytest 的 -q 和 summary 都是这个格式）。"""
    return set(re.findall(r"^(?:FAILED|ERROR) (\S+::\S+)", text, re.M))


def compare(cmd, cwd, expect_wrap=True, **env):
    """原命令 vs 改写后命令，在每个 shell 里各跑一次。返回 (是否改写, 问题列表)。"""
    new = TF.rewrite(cmd, LOGS)
    probs = []
    if bool(new) != expect_wrap:
        probs.append("改写=%s，期望 %s" % (bool(new), expect_wrap))
    if not new:
        return False, probs
    for sh in SHELLS:
        m0, m1 = os.path.join(TMP, "mark0"), os.path.join(TMP, "mark1")
        for m in (m0, m1):
            if os.path.exists(m):
                os.replace(m, m + ".old")         # 不删文件，挪开
        rc0, o0 = run(sh, cmd, cwd, m0, **env)
        rc1, o1 = run(sh, new, cwd, m1, **env)
        ran0, ran1 = os.path.exists(m0), os.path.exists(m1)
        if subprocess.run([sh, "-n", "-c", new], capture_output=True).returncode != 0:
            probs.append("%s 语法错误" % sh)
        if rc0 != rc1:
            probs.append("%s rc %d→%d" % (sh, rc0, rc1))
        if ran0 != ran1:
            probs.append("%s 短路不同：原来 pytest %s，改写后 %s" % (sh, "跑了" if ran0 else "没跑", "跑了" if ran1 else "没跑"))
        if failed_names(o0) != failed_names(o1):
            probs.append("%s 失败名不同：少了 %s" % (sh, sorted(failed_names(o0) - failed_names(o1))[:5]))
        for line in o0.splitlines():              # 非测试部分（前缀命令）的输出要原样都在
            if line.startswith("PRE:") and line not in o1:
                probs.append("%s 前缀输出丢了：%s" % (sh, line))
    return True, probs


CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn)); return fn
    return deco


# ---------------------------------------------------------------- R28
def r28_run(sh="/bin/bash"):
    d = mkproj("r28", FAILS_R28, ntests=300, body_lines=11)
    cmd = "pytest -q"
    rc0, raw = run(sh, cmd, d)
    new = TF.rewrite(cmd, LOGS)
    rc1, out = run(sh, new, d) if new else (rc0, raw)
    return d, rc0, raw, new, rc1, out


@case("R28 300 个测试 25 个失败（分散在中段）：被改写；25 个失败名在 FAILED 行里全在、short test summary 标题在；"
      "每个失败至少一行 E（行首就是 E，没有行号前缀）；rc 原样；输出 < 原输出、< 30000 字节")
def _():
    d, rc0, raw, new, rc1, out = r28_run()
    want = {"tests/test_x.py::test_%03d" % i for i in FAILS_R28}
    probs = []
    if len(raw.splitlines()) < 450:
        probs.append("构造的原输出只有 %d 行（应 ≈505）" % len(raw.splitlines()))
    if not new:
        probs.append("没有被改写")
    if rc0 != 1 or rc1 != rc0:
        probs.append("rc %d→%d" % (rc0, rc1))
    if failed_names(raw) != want:
        probs.append("原输出失败名不对")
    lost = want - failed_names(out)
    if lost:
        probs.append("可见输出里丢了 %d 个失败名，如 %s" % (len(lost), sorted(lost)[:4]))
    if "short test summary info" not in out:
        probs.append("没有 short test summary 标题")
    e_lines = [l for l in out.splitlines() if l.startswith("E ")]
    no_e = [i for i in FAILS_R28 if not any(re.search(r"\b%d == \(?%d \+ 1" % (i, i), l) for l in e_lines)]
    if no_e:
        probs.append("%d 个失败没有 E 行，如 test_%s" % (len(no_e), no_e[:4]))
    if len(out.splitlines()) >= len(raw.splitlines()) or len(out.encode()) >= 30000:
        probs.append("输出没变短或超过 30000 字节（%d 行 / %d 字节）" % (len(out.splitlines()), len(out.encode())))
    return not probs, "原 %d 行 → 可见 %d 行 / %d 字节；%s" % (len(raw.splitlines()), len(out.splitlines()),
                                                      len(out.encode()), "; ".join(probs))


@case("R28-zsh 同一形状在 zsh 下：rc 原样、25 个失败名都在、每个失败都有 E 行")
def _():
    if "/bin/zsh" not in SHELLS:
        return True, "没有 zsh，跳过"
    d, rc0, raw, new, rc1, out = r28_run("/bin/zsh")
    want = {"tests/test_x.py::test_%03d" % i for i in FAILS_R28}
    e_ok = all(any(l.startswith("E ") and "%d == " % i in l for l in out.splitlines()) for i in FAILS_R28)
    return bool(new) and rc0 == rc1 == 1 and want <= failed_names(out) and e_ok, (rc0, rc1, len(want - failed_names(out)))


@case("上限：400 个失败（每个都很长）→ 输出 < 30000 字节、rc 原样，并明确写「还有 N 个失败没显示，完整日志在 <路径>」")
def _():
    ids = set(range(1, 401))
    d = mkproj("cap", ids, ntests=400, body_lines=6)
    new = TF.rewrite("pytest -q", LOGS)
    rc, out = run("/bin/bash", new, d)
    m = re.search(r"\[还有 (\d+) 个失败没显示，完整日志在 (\S+)\]", out)
    shown = failed_names(out)
    ok = (bool(new) and rc == 1 and len(out.encode()) < 30000 and m is not None and os.path.isfile(m.group(2))
          and int(m.group(1)) == 400 - len(shown) and len(shown) >= 100)
    return ok, "rc=%s 字节=%d 显示失败名 %d 个，提示=%s" % (rc, len(out.encode()), len(shown), m.group(0) if m else None)


@case("超长行：失败信息每行 5000 字符 × 60 行 → 输出 < 30000 字节（不会被 Claude Code 落盘只给 2KB 预览），失败名都在")
def _():
    d = os.path.join(TMP, "longline"); os.makedirs(d, exist_ok=True)
    src = "".join("def test_l%02d():\n    assert 'a' * 5000 == 'b' * 5000\n" % i for i in range(60))
    open(os.path.join(d, "test_long.py"), "w").write(src)
    rc0, raw = run("/bin/bash", "pytest -q -vv", d)
    new = TF.rewrite("pytest -q -vv", LOGS)
    rc, out = run("/bin/bash", new, d)
    return (len(raw.encode()) > 30000 and rc == rc0 == 1 and len(out.encode()) < 30000
            and failed_names(raw) == failed_names(out)), "原 %d 字节 → %d 字节，失败名 %d/%d" % (
        len(raw.encode()), len(out.encode()), len(failed_names(out)), len(failed_names(raw)))


@case("退回旧摘要：摘要用的 python 不存在时（把包装命令里的解释器路径换成不存在的）仍走旧 grep/tail，rc 原样，结尾和提示在")
def _():
    d = mkproj("fb", {3}, ntests=60)
    new = TF.rewrite("pytest -q -rA", LOGS)
    bad = new.replace(json.dumps(sys.executable)[1:-1], "/nonexistent/python3") if new else None
    if not bad or bad == new:
        return False, "包装命令里没找到解释器路径"
    rc, out = run("/bin/bash", bad, d)
    return rc == 1 and "----- 结尾 -----" in out and "[测试输出已过滤" in out and "test_003" in out, (rc, out[-200:])


@case("通过的长输出（-rA，60 个通过）：rc=0，结尾 passed 可见，不写「还有 N 个失败」")
def _():
    d = mkproj("pass", (), ntests=60)
    new = TF.rewrite("pytest -q -rA", LOGS)
    rc, out = run("/bin/bash", new, d)
    return bool(new) and rc == 0 and "60 passed" in out and "还有" not in out, (rc, out[-200:])


@case("真实 hook 入口（stdin JSON）：pytest -q 被改写，输出的 updatedInput 能执行，rc 原样")
def _():
    d = mkproj("entry", {2}, ntests=50)
    p = subprocess.run([sys.executable, HOOK], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "pytest -q"},
                                                                 "cwd": d}), capture_output=True, text=True,
                       env=dict(ENV, USAGE_SAVER_STATE=os.path.join(TMP, "st")))
    try:
        new = json.loads(p.stdout)["hookSpecificOutput"]["updatedInput"]["command"]
    except Exception:
        return False, p.stdout[:200] + p.stderr[:200]
    rc, out = run("/bin/bash", new, d)
    return p.returncode == 0 and rc == 1 and "tests/test_x.py::test_002" in failed_names(out), rc


# ---------------------------------------------------------------- R6T3：短日志里的超长行
@case("R6T3 短日志（≤40 行）里有一行 12000 个中文字符的断言消息 + 第二个失败：不能直接 cat；"
      "输出 < 16000+余量 字节、没有超过 400 字符+截断说明的行、两个失败名和第二个失败的 E 行都在、rc 原样（bash、zsh）")
def _():
    d = os.path.join(TMP, "r6T3_short_long"); os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "test_s.py"), "w", encoding="utf-8").write(
        "def test_failure():\n    assert False, '中' * 12000\n\n\ndef test_second():\n    assert 1 == 2\n")
    open(os.path.join(d, "conftest.py"), "w").write("")
    cmd = "pytest -q"
    new = TF.rewrite(cmd, LOGS)
    probs = []
    if not new:
        return False, "没有被改写"
    for sh in SHELLS:
        rc0, raw = run(sh, cmd, d)
        rc1, out = run(sh, new, d)
        if len(raw.splitlines()) > 40 or len(raw.encode()) <= 30000:
            probs.append("%s 构造的原输出不对（%d 行 / %d 字节）" % (sh, len(raw.splitlines()), len(raw.encode())))
        if rc0 != 1 or rc1 != rc0:
            probs.append("%s rc %d→%d" % (sh, rc0, rc1))
        nb = len(out.encode())
        if nb > TF.SUM_BYTES + 1000:
            probs.append("%s 输出 %d 字节" % (sh, nb))
        longest = max(len(l) for l in out.splitlines())
        if longest > TF.LINE_CHARS + 40:
            probs.append("%s 有一行 %d 字符" % (sh, longest))
        want = {"test_s.py::test_failure", "test_s.py::test_second"}
        if not want <= failed_names(out):
            probs.append("%s 失败名缺 %s" % (sh, sorted(want - failed_names(out))))
        if not any(l.startswith("E ") and "1 == 2" in l for l in out.splitlines()):
            probs.append("%s 第二个失败的 E 行不在" % sh)
        info = "原 %d 行 / %d 字节 → 可见 %d 行 / %d 字节" % (len(raw.splitlines()), len(raw.encode()), len(out.splitlines()), nb)
    return not probs, info + "；" + "; ".join(probs)


@case("R6T3 正常的短日志（≤40 行、字节和行长都不超限）仍然原样输出，不加摘要标记")
def _():
    d = mkproj("r6T3_short_ok", {2}, ntests=4)
    new = TF.rewrite("pytest -q", LOGS)
    rc0, raw = run("/bin/bash", "pytest -q", d)
    rc1, out = run("/bin/bash", new, d)
    body = out.split("\n", 1)[1] if out.startswith("[测试输出写入") else out
    ok = rc0 == rc1 == 1 and "----- 结尾 -----" not in out and body.strip() == raw.strip()
    return ok, "rc %d→%d，原样=%s" % (rc0, rc1, body.strip() == raw.strip())


# ---------------------------------------------------------------- R6T2：逐行进度（PASSED 行）
PLUGIN_CONFTEST = ("def pytest_runtest_logreport(report):\n"
                   "    if report.when == 'call':\n"
                   "        print('%s %s' % (report.nodeid, report.outcome.upper()))\n")


def progress_check(label, cmd, plugin):
    d = mkproj("prog_" + label, FAILS_R28, ntests=300)
    if plugin:
        open(os.path.join(d, "conftest.py"), "w").write(PLUGIN_CONFTEST)
    probs = []
    for sh in SHELLS:
        rc0, raw = run(sh, cmd, d)
        new = TF.rewrite(cmd, LOGS)
        rc1, out = run(sh, new, d) if new else (rc0, raw)
        L, R = out.splitlines(), raw.splitlines()
        want = {"tests/test_x.py::test_%03d" % i for i in FAILS_R28}
        if sum(1 for l in R if re.search(r"::test_\d+ PASSED", l)) < 250:
            probs.append("%s 构造的原输出里 PASSED 进度行不够" % sh)
        if not new or rc0 != 1 or rc1 != rc0:
            probs.append("%s 改写=%s rc %d→%d" % (sh, bool(new), rc0, rc1))
        vis_pass = [l for l in L if re.search(r"\b(PASSED|SKIPPED|XFAIL)\b", l) and "省略" not in l]
        if vis_pass:
            probs.append("%s 还能看到 %d 行 PASSED 进度，如 %r" % (sh, len(vis_pass), vis_pass[0][:60]))
        if not any(re.search(r"省略 \d+ 行 PASSED 进度", l) for l in L):
            probs.append("%s 没有写「省略 N 行 PASSED 进度」" % sh)
        if len(L) > len(R) // 2:
            probs.append("%s 可见 %d 行 > 原 %d 行的一半" % (sh, len(L), len(R)))
        lost = want - failed_names(out)
        if lost:
            probs.append("%s 丢了 %d 个失败名" % (sh, len(lost)))
        dup = [i for i in FAILS_R28 if sum(1 for l in L if "test_%03d" % i in l and "FAILED" in l) > 1]
        if dup:
            probs.append("%s %d 个失败名出现不止一次（FAILED 进度行没去重），如 test_%03d" % (sh, len(dup), dup[0]))
        no_e = [i for i in FAILS_R28 if not any(l.startswith("E ") and "%d == " % i in l for l in L)]
        if no_e:
            probs.append("%s %d 个失败没有 E 行" % (sh, len(no_e)))
        if not any(re.search(r"25 failed, 275 passed", l) for l in L):
            probs.append("%s 汇总行不在" % sh)
        info = "原 %d 行 → 可见 %d 行（%d%%）" % (len(R), len(L), 100 * len(L) // max(1, len(R)))
    return not probs, info + "；" + "; ".join(probs)


@case("R6T2 -q 加插件逐行打印 `nodeid PASSED`（盲测形状）：PASSED 进度行一律不显示并计数；可见行数 ≤ 原来一半；"
      "25 个失败名都在且各只出现一次；每个失败有 E 行；汇总行和 rc 原样（bash、zsh）")
def _():
    return progress_check("qplug", "pytest -q -s", True)


@case("R6T2 -v（pytest 自己的逐行进度 `nodeid PASSED [ 1%]`）：同上")
def _():
    return progress_check("v", "pytest -v", False)


@case("R6T2 -v -rA（summary 里还有 `PASSED nodeid` 行）：同上")
def _():
    return progress_check("vrA", "pytest -v -rA", False)


@case("R6T2 SKIPPED/XFAIL 进度行也丢掉并计数；XPASS 和没有 summary 时的 FAILED 进度行保留（summarize 直接调用）")
def _():
    lines = ["tests/a.py::t%d PASSED   [ %d%%]" % (i, i % 100) for i in range(60)]
    lines += ["tests/a.py::s1 SKIPPED (no db)   [ 61%]", "tests/a.py::x1 XFAIL (known)   [ 62%]",
              "tests/a.py::x2 XPASS   [ 63%]", "tests/a.py::f1 FAILED   [ 64%]"]
    lines += ["done %d" % i for i in range(15)]
    out = TF.summarize(lines, TF.GREP_PAT, "/tmp/x.log")
    probs = []
    for bad in ("::t3 PASSED", "SKIPPED (no db)", "XFAIL (known)"):
        if bad in out:
            probs.append("还能看到 %r" % bad)
    for good in ("::x2 XPASS", "::f1 FAILED", "60 行 PASSED 进度", "1 行 SKIPPED 进度", "1 行 XFAIL 进度"):
        if good not in out:
            probs.append("缺 %r" % good)
    return not probs, "; ".join(probs)


# ---------------------------------------------------------------- 新改写形状（真实执行对照）
SHAPES = [
    ("timeout 60 pytest -q", "timeout 60 pytest -q"),
    ("timeout 带选项", "timeout -k 5 --signal=TERM --foreground 2m pytest -q"),
    ("timeout -s KILL", "timeout -s KILL 60 pytest -q tests"),
    ("gtimeout", "gtimeout 30s pytest -q"),
    ("env 前缀 + timeout", "R6T_ENV=hello timeout 60 pytest -q"),
    ("cd && timeout", "cd sub && timeout 60 pytest -q"),
    ("[[ && ]] && pytest（条件真）", "[[ -f conftest.py && -d tests ]] && pytest -q"),
    ("[[ && ]] && pytest（条件假→短路）", "[[ -f conftest.py && -d no_such_dir ]] && pytest -q"),
    ("[[ ]] || pytest", "[[ -f no_such && -d tests ]] || pytest -q"),
    ("printf | sort; pytest", "printf 'PRE:b\\nPRE:a\\n' | sort; pytest -q"),
    ("管道前缀失败 && pytest（短路）", "printf 'PRE:x\\n' | grep -q nope && pytest -q"),
    ("重定向前缀; pytest", "printf 'junk\\n' > /dev/null 2>&1; echo PRE:after; pytest -q"),
    ("引号里的 ; | && 不算分隔符", "printf '%s\\n' 'PRE:a;b|c && d' | cat; pytest -q"),
    ("cd + 管道 + env 前缀", "cd sub && printf 'PRE:z\\n' | cat; R6T_ENV=hello pytest -q"),
    ("set -e + 管道前缀", "set -e; printf 'PRE:q\\n' | cat; pytest -q"),
    ("pytest; 行尾注释", "printf 'PRE:c\\n' | cat; pytest -q  # 注释 ; echo NO"),
    ("2>&1 | tail 前缀", "printf 'PRE:t\\n' 2>&1 | tail -n 1 && timeout 60 pytest -q"),
]


def shape_case(label, cmd):
    @case("形状：%s — `%s`" % (label, cmd))
    def _():
        base = mkproj("shape_%d" % len(CASES), {2, 5}, ntests=60,
                      extra="def test_env():\n    assert os.environ.get('R6T_ENV', 'hello') == 'hello'\n")
        sub = os.path.join(base, "sub")
        if not os.path.exists(sub):
            shutil.copytree(base, sub)            # cd sub 的用例：sub 里也是同样的项目
        wrapped, probs = compare(cmd, base)
        return wrapped and not probs, "; ".join(probs)


for _l, _c in SHAPES:
    shape_case(_l, _c)


@case("timeout 真超时：`timeout 1 pytest -q`（测试 sleep 5）→ 原命令和改写后都是 rc=124")
def _():
    d = mkproj("slow", (), ntests=2, extra="import time\ndef test_slow():\n    time.sleep(5)\n")
    wrapped, probs = compare("timeout 1 pytest -q", d)
    rc, _ = run("/bin/bash", "timeout 1 pytest -q", d)
    return wrapped and not probs and rc == 124, "; ".join(probs) or rc


# ---------------------------------------------------------------- 反例：不应改写
NEGATIVE = [
    "cat <<'EOF' > conftest.py\n# x\nEOF\npytest -q",                 # heredoc 后接 pytest
    "[[ -f conftest.py &&\n   -d tests ]] && pytest -q",               # 多行 [[
    "[[ -f conftest.py\n]] && pytest -q",
    "(cd sub; printf a | cat); pytest -q",                              # 子 shell
    "printf a | cat; (pytest -q)",
    "x=$(printf a | cat); pytest -q",                                    # $(...)
    "x=`printf a`; printf b | cat; pytest -q",                           # 反引号
    "printf a | cat; pytest -q | tee out.txt",                           # 测试输出被管道接走
    "printf a | cat; pytest -q > out.txt",                               # 测试输出重定向
    "printf a | pytest -q",                                              # pytest 在管道里
    "sleep 0 & pytest -q",                                               # 后台 &
    "printf a | cat; pytest -q &",
    "printf a | cat; pytest -q; echo done",                              # pytest 不是最后一段
    "if [ -f x ]; then printf a | cat; fi; pytest -q",                   # 保留字
    "{ printf a | cat; }; pytest -q",
    "! printf a | grep -q b; pytest -q",
    "time printf a | cat; pytest -q",
    "printf a | cat # 注释; pytest -q",                                  # 注释里的 ; 不是分隔符：pytest 本来就不跑
    "printf a | cat; pytest -q --pdb",                                   # 交互参数
    "timeout 60 pytest --pdb",
    "timeout pytest -q",                                                 # 没有时长
    "timeout -x 5 pytest -q",                                            # 认不出来的 timeout 选项
    "timeout 60",
    "printf $'a\\'b' | cat; pytest -q",                                  # $'…'
    "printf \"${x:-a;b}\" | cat; pytest -q",                              # ${…} 里有分隔符
    "printf a |& cat; pytest -q >| out",
    "case x in x) printf a | cat;; esac; pytest -q",
    "[[ a < b ]] && printf a | cat; pytest -q; ",                        # 末尾悬空分隔符：最后一段为空
    "printf a | cat; USAGE_SAVER_RAW=1 pytest -q",                       # 显式要原样
    "printf a | cat && pytest -q \\",                                     # 段尾反斜杠
]


@case("反例 %d 条：heredoc / 多行 [[ / 子 shell / $(…) / 反引号 / 测试被管道或重定向 / 后台 & / 保留字 / 注释 / "
      "--pdb / 认不出的 timeout / pytest 不在最后 —— 都放行（rewrite 返回 None）" % len(NEGATIVE))
def _():
    bad = [c for c in NEGATIVE if TF.rewrite(c, LOGS) is not None]
    return not bad, "被改写了：%r" % bad[:3]


@case("以前就会改写的形状：改写与否不变，执行 rc 不变（cd && pytest、pytest; exit 3、set -e; pytest; echo after）")
def _():
    d = mkproj("old", {4}, ntests=60)
    probs = []
    for c in ("cd . && pytest -q", "pytest -q; exit 3", "set -e; pytest -q; echo PRE:after", "FOO=1 pytest -q tests"):
        w, p = compare(c, d)
        probs += [c + "：" + x for x in p]
    return not probs, "; ".join(probs)


@case("tail_split 单元：前缀一个字符都不改；分隔符就是最后一个顶层 ; && ||")
def _():
    exp = {
        "[[ -f a && -d b ]] && pytest -q": ("[[ -f a && -d b ]] ", "&&", " pytest -q"),
        "printf 'x;y' | sort; pytest": ("printf 'x;y' | sort", ";", " pytest"),
        "a 2>&1 | b || pytest -q tests": ("a 2>&1 | b ", "||", " pytest -q tests"),
        "foo\\ ; pytest": ("foo\\ ", ";", " pytest"),
    }
    got = {c: TF.tail_split(c) for c in exp}
    return got == exp, got


def main():
    if not PY:
        print("找不到带 pytest 的 python，全部跳过"); print("r6T: 0/0 通过"); return 0
    print("被测 hook：%s\n临时目录：%s\npytest：%s\nshell：%s" % (HOOK, TMP, PY, SHELLS))
    ok = 0
    for name, fn in CASES:
        try:
            res, info = fn()
        except Exception as e:
            res, info = False, "异常 %r" % e
        ok += bool(res)
        print("%s  %s%s" % ("PASS" if res else "FAIL", name, ("\n      " + str(info)) if info and (not res or "→" in str(info)) else ""))
    print("r6T: %d/%d 通过" % (ok, len(CASES)))
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
