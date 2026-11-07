#!/usr/bin/env python3
"""第 6 轮 TF 难题（G2）：test_filter 必须「真的改写并执行」才能通过。实施者不应查看本文件。

每道 R 题：把命令交给 hook，拿到改写后的命令；在两份完全相同的临时项目里分别用 bash 执行
「原命令」和「改写后命令」（PATH 最前面是假 pytest/npm/node/python/lake/cargo，输出几百行、
带失败），然后比较：
  - 必须改写（hook 原样放行 / 拦截 = 失败）；改写后 bash -n 通过；
  - 退出码相同；假测试被调用的序列（argv、cwd、关键环境变量）相同；项目目录树（含 heredoc 写出的文件）相同；
  - 可见输出（stdout+stderr）行数 ≤ 原输出的一半；
  - 关键行（失败的测试名、报错行、最后的汇总）一行不丢；非测试段的输出逐行保留；不该出现的行不出现。
每道 N 题（不应改写的反例）：命令文本必须原样不变，或（短输出题）可见输出与原输出逐行一致。

运行（仓库根目录）：
  python3 tools/heldout/heldout_tf2.py                  # 测仓库里的 hooks/test_filter.py
  HELDOUT_TF2_HOOK=/path/x.py python3 tools/heldout/heldout_tf2.py   # 用别的 hook 文件替换 test_filter.py（对照用）
  HELDOUT_SUITE=/path/suite  ...                        # 换整套套件副本
  HELDOUT_ONLY=R01,N0  /  HELDOUT_SKIP=R2               # 按编号前缀筛选
  HELDOUT_TF2_VERBOSE=1                                 # 失败时多打印改写后的命令
隔离：每题新建临时 HOME，把 hooks/*.py、tools/*.py、config.json 复制到 <HOME>/Documents/Projects/claude-usage-saver/，
只运行副本；不读写仓库 state/，不碰 ~/.claude/。只依赖 Python 3 标准库和 /bin/bash。
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

SUITE = Path(os.environ.get("HELDOUT_SUITE") or Path(__file__).resolve().parents[2])
HOOK_OVERRIDE = os.environ.get("HELDOUT_TF2_HOOK")
VERBOSE = os.environ.get("HELDOUT_TF2_VERBOSE") == "1"
BASH = "/bin/bash"
CASES = []
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def case(code, scene, kind, basis):
    """kind: 'R' 必须改写；'N' 不应改写。basis: 'doc' 文档内 / 'beyond' 超出现有文档（更严格的目标）。"""
    def deco(fn):
        CASES.append((code, scene, kind, basis, fn))
        return fn
    return deco


def check(ok, message, actual=None):
    if not ok:
        raise AssertionError(message + (f"；实际 {str(actual)[:400]}" if actual is not None else ""))


# ---------------------------------------------------------------- 假测试程序
FAKE = r'''#!__PY__
import json, os, sys
REAL_PY = "__PY__"
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
if name == "timeout":
    os.execvp(args[1], args[1:])
if name == "uv":
    if args[:1] == ["run"]:
        os.execvp(args[1], args[1:])
    sys.exit(0)
if name in ("python", "python3"):
    if args[:2] != ["-m", "pytest"]:
        os.execv(REAL_PY, [REAL_PY] + args)
    name, args = "pytest", args[2:]
root = os.environ.get("TF2_ROOT", os.getcwd())
spec = {}
try:
    with open(os.path.join(root, ".tf2spec.json")) as f:
        spec = json.load(f)
except Exception:
    pass
kind = {"pytest": "pytest", "npm": "jest", "node": "tap", "lake": "lake", "cargo": "cargo"}.get(name, name)
if name == "npm" and args[:1] != ["test"]:
    kind = "npm_other"
if name == "node" and "--test" not in args:
    os.execvp("true", ["true"])
mode = spec.get("mode", "fail")
for k, v in spec.get("by_arg", {}).items():
    if k in args:
        mode = v
cwd = os.path.relpath(os.getcwd(), root)
env = {k: os.environ.get(k) for k in ("FOO", "BAR", "PYTHONPATH") if k in os.environ}
with open(os.path.join(root, ".calls.jsonl"), "a") as f:
    f.write(json.dumps({"tool": name, "argv": args, "cwd": cwd, "env": env}) + "\n")
out, err = sys.stdout, sys.stderr
N = int(spec.get("n", 240))
rc = 0
if kind == "npm_other":
    print("up to date, audited 1 package in 120ms")
elif kind == "pytest":
    if mode == "many_fail":
        print("=" * 29 + " test session starts " + "=" * 30)
        print("collected 300 items")
        for i in range(300):
            st = "FAILED" if i % 12 == 5 else "PASSED"
            print(f"tests/test_m.py::test_{i:03d} {st}")
        bad = [i for i in range(300) if i % 12 == 5]
        print("=" * 35 + " FAILURES " + "=" * 35)
        for i in bad:
            print("_" * 30 + f" test_{i:03d} " + "_" * 30)
            print(f"    def test_{i:03d}():")
            print(f">       assert f({i}) == {i + 1}")
            print(f"E       assert {i} == {i + 1}")
            print(f"E        +  where {i} = f({i})")
            print()
            print(f"tests/test_m.py:{i + 10}: AssertionError")
        print("=" * 28 + " short test summary info " + "=" * 27)
        for i in bad:
            print(f"FAILED tests/test_m.py::test_{i:03d} - assert {i} == {i + 1}")
        print("=" * 24 + f" {len(bad)} failed, {300 - len(bad)} passed in 0.90s " + "=" * 24)
        rc = 1
    elif mode == "short_fail":
        print("..F                                                                      [100%]")
        print("FAILED tests/test_calc.py::test_short_bad - assert 2 == 3")
        print("E       assert 2 == 3")
        print("1 failed, 2 passed in 0.02s")
        rc = 1
    else:
        print("=" * 29 + " test session starts " + "=" * 30)
        print("platform darwin -- Python 3.12.4, pytest-8.3.3, pluggy-1.5.0")
        print("rootdir: /proj")
        print(f"collected {N + (1 if mode == 'fail' else 0)} items")
        print()
        for i in range(N):
            print(f"tests/test_calc.py::test_case_{i:03d} PASSED" + " " * 30 + f"[{i * 100 // (N + 1):3d}%]")
        if mode == "fail":
            print("tests/test_calc.py::test_divide_by_zero FAILED" + " " * 28 + "[100%]")
            print()
            print("=" * 35 + " FAILURES " + "=" * 35)
            print("_" * 29 + " test_divide_by_zero " + "_" * 30)
            print()
            print("    def test_divide_by_zero():")
            print(">       assert calc.div(1, 0) == 0")
            print("E       ZeroDivisionError: division by zero")
            print()
            print("tests/test_calc.py:42: ZeroDivisionError")
            print("=" * 28 + " short test summary info " + "=" * 27)
            print("FAILED tests/test_calc.py::test_divide_by_zero - ZeroDivisionError: division by zero")
            print("=" * 24 + f" 1 failed, {N} passed in 0.42s " + "=" * 24)
            rc = 1
        elif mode == "pass":
            print("=" * 30 + f" {N} passed in 0.40s " + "=" * 30)
        elif mode == "rc3":
            out.flush()
            print("INTERNALERROR> Traceback (most recent call last):")
            print('INTERNALERROR>   File "/site/_pytest/main.py", line 283, in wrap_session')
            print("INTERNALERROR> RuntimeError: plugin tf2 crashed")
            rc = 3
        elif mode == "stderr":
            out.flush()
            print("ImportError while loading conftest '/proj/tests/conftest.py'.", file=err)
            print("E   ModuleNotFoundError: No module named 'missing_dep_tf2'", file=err)
            err.flush()
            print("=" * 30 + " 1 error in 0.30s " + "=" * 30)
            rc = 4
elif kind == "jest":
    print("\n> tf2-proj@1.0.0 test\n> jest\n")
    out.flush()
    print("PASS src/a.test.js", file=err)
    for i in range(N if mode != "mid_fail" else 40):
        print(f"  ✓ case {i:03d} (1 ms)", file=err)
    print("FAIL src/math.test.js", file=err)
    print("  math", file=err)
    print("    ✓ adds (2 ms)", file=err)
    print("    ✕ subtracts (3 ms)", file=err)
    print("", file=err)
    print("  ● math › subtracts", file=err)
    print("", file=err)
    print("    expect(received).toBe(expected) // Object.is equality", file=err)
    print("", file=err)
    print("    Expected: 1", file=err)
    print("    Received: 2", file=err)
    print("", file=err)
    print("      at Object.<anonymous> (src/math.test.js:7:21)", file=err)
    print("", file=err)
    if mode == "mid_fail":
        for j in range(20):
            print(f"PASS src/z{j:02d}.test.js", file=err)
            for i in range(10):
                print(f"  \u2713 z{j:02d} case {i} (1 ms)", file=err)
    print("Test Suites: 1 failed, 1 passed, 2 total", file=err)
    print(f"Tests:       1 failed, {N + 1} passed, {N + 2} total", file=err)
    print("Snapshots:   0 total", file=err)
    print("Time:        1.234 s", file=err)
    print("Ran all test suites.", file=err)
    rc = 1
elif kind == "tap":
    print("TAP version 13")
    for i in range(N):
        print(f"# Subtest: case {i:03d}\nok {i + 1} - case {i:03d}\n  ---\n  duration_ms: 0.1\n  ...")
    print(f"# Subtest: subtracts\nnot ok {N + 1} - subtracts\n  ---\n  duration_ms: 0.5")
    print("  failureType: 'testCodeFailure'")
    print("  error: 'Expected values to be strictly equal:\\n\\n2 !== 1\\n'")
    print("  code: 'ERR_ASSERTION'\n  ...")
    print(f"1..{N + 1}\n# tests {N + 1}\n# pass {N}\n# fail 1")
    rc = 1
elif kind == "lake":
    for i in range(N):
        print(f"✔ [{i + 1}/{N + 3}] Built Mathlib.Part{i:03d}")
    print(f"⚠ [{N + 1}/{N + 3}] Built Proj.Aux")
    print("warning: ./Proj/Aux.lean:5:8: declaration uses 'sorry'")
    print(f"✖ [{N + 2}/{N + 3}] Building Proj.Main")
    print("error: ./Proj/Main.lean:3:2: unsolved goals")
    print("⊢ 1 + 1 = 3")
    print("error: Lean exited with code 1")
    print("Some required builds logged failures:")
    print("- Proj.Main")
    print("error: build failed")
    rc = 1
elif kind == "cargo":
    print("   Compiling tf2 v0.1.0 (/proj)", file=err)
    print("    Finished `test` profile [unoptimized + debuginfo] target(s) in 0.50s", file=err)
    print("     Running unittests src/lib.rs (target/debug/deps/tf2-abc)", file=err)
    err.flush()
    print(f"\nrunning {N + 1} tests")
    for i in range(N):
        print(f"test tests::case_{i:03d} ... ok")
    print("test tests::sub ... FAILED")
    print("\nfailures:\n\n---- tests::sub stdout ----")
    print("thread 'tests::sub' panicked at src/lib.rs:10:5:")
    print("assertion `left == right` failed\n  left: 2\n right: 1")
    print("\nfailures:\n    tests::sub\n")
    print(f"test result: FAILED. {N} passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n")
    out.flush()
    print("error: test failed, to rerun pass `--lib`", file=err)
    rc = 101
out.flush(); err.flush()
sys.exit(rc)
'''


class Box:
    def __init__(self, root):
        self.root = Path(os.path.realpath(root))
        self.home = self.root / "home"
        self.suite = self.home / "Documents/Projects/claude-usage-saver"
        src_hook = Path(HOOK_OVERRIDE) if HOOK_OVERRIDE else SUITE / "hooks/test_filter.py"
        if not src_hook.is_file():
            raise RuntimeError(f"RUNNER FAIL hook 不存在：{src_hook}")
        for src in list((SUITE / "hooks").glob("*.py")) + list((SUITE / "tools").glob("*.py")) + [SUITE / "config.json"]:
            if src.is_file():
                dst = self.suite / src.relative_to(SUITE)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        shutil.copy2(src_hook, self.suite / "hooks/test_filter.py")
        (self.suite / "state").mkdir()
        self.logdir = self.suite / "state/testlogs"
        bindir = self.root / "bin"
        bindir.mkdir()
        body = FAKE.replace("__PY__", sys.executable)
        for name in ("pytest", "npm", "node", "python", "python3", "lake", "cargo", "timeout", "uv"):
            p = bindir / name
            p.write_text(body)
            p.chmod(0o700)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("USAGE_SAVER_", "CLAUDE_", "PYTEST", "FOO", "BAR", "PYTHONPATH", "TF2_"))}
        self.env.update(HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1", BASH_ENV="/dev/null", ENV="/dev/null",
                        XDG_CACHE_HOME=str(self.home / ".cache"), LC_ALL="en_US.UTF-8", LANG="en_US.UTF-8",
                        PATH=str(bindir) + os.pathsep + "/usr/bin:/bin:/usr/sbin:/sbin")
        base = self.home / "Documents/Projects"
        self.A = base / "tf2-raw"          # 跑原命令
        self.B = base / "tf2-proj"         # 跑改写后的命令；hook 看到的 cwd
        for p in (self.A, self.B):
            for d in (".git", "tests", "sub", "src", "tests/dir with space", "tests/odd #dir", "tests/a", "tests/b", "test"):
                (p / d).mkdir(parents=True, exist_ok=True)
            (p / "conftest.py").write_text("# tf2\n")
            (p / "pytest.ini").write_text("[pytest]\n")

    def project(self, spec=None, files=None):
        for p in (self.A, self.B):
            (p / ".tf2spec.json").write_text(json.dumps(spec or {}))
            for rel, text in (files or {}).items():
                (p / rel).parent.mkdir(parents=True, exist_ok=True)
                (p / rel).write_text(text)

    def hook(self, command):
        payload = {"session_id": "tf2", "transcript_path": str(self.root / "t.jsonl"), "cwd": str(self.B),
                   "hook_event_name": "PreToolUse", "tool_name": "Bash",
                   "tool_input": {"command": command, "description": "tf2"}}
        p = subprocess.run([sys.executable, str(self.suite / "hooks/test_filter.py")], input=json.dumps(payload),
                           text=True, capture_output=True, env=self.env, cwd=self.B, timeout=15)
        if p.returncode == 2:
            return None, "exit2:" + p.stderr.strip()[:100]
        check(p.returncode == 0, f"hook 退出码 {p.returncode}", p.stderr)
        check(not p.stderr.strip(), "hook 不应输出 stderr", p.stderr)
        if not p.stdout.strip():
            return command, None
        body = json.loads(p.stdout).get("hookSpecificOutput", {})
        if body.get("permissionDecision") in ("deny", "ask"):
            return None, "decision:" + body.get("permissionDecision")
        new = (body.get("updatedInput") or {}).get("command", command)
        check(isinstance(new, str), "updatedInput.command 不是字符串", new)
        return new, None

    def bash(self, command, cwd):
        env = dict(self.env, TF2_ROOT=str(cwd))
        p = subprocess.run([BASH, "--noprofile", "--norc", "-c", command], cwd=cwd, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        text = ANSI.sub("", p.stdout.decode("utf-8", "replace")).replace(str(cwd), "<P>")
        return p.returncode, text


def tree(path):
    out = {}
    for p in sorted(path.rglob("*")):
        rel = str(p.relative_to(path))
        out[rel] = "dir" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def calls(path):
    f = path / ".calls.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def lines(text):
    return [x.rstrip() for x in text.splitlines()]


def must_rewrite(s, command, spec=None, files=None, keys=(), plain=(), absent=(), rc=None, ratio=0.5):
    """R 题的统一判定。返回 (原输出, 新输出, 改写后命令)。"""
    s.project(spec, files)
    new, blocked = s.hook(command)
    check(blocked is None, "命令被拦截（只拦不改）", blocked)
    check(new != command, "未改写（原样放行）")
    syn = subprocess.run([BASH, "--noprofile", "--norc", "-n"], input=new, text=True, capture_output=True)
    check(syn.returncode == 0, "改写后 bash -n 失败", syn.stderr)
    rc_a, out_a = s.bash(command, s.A)
    rc_b, out_b = s.bash(new, s.B)
    ctx = f"\n改写后：{new!r}" if VERBOSE else ""
    if rc is not None:
        check(rc_a == rc, f"夹具自检：原命令退出码应为 {rc}", (rc_a, out_a[-300:]))
    check(rc_b == rc_a, f"退出码不同：原 {rc_a} 改写后 {rc_b}" + ctx, out_b[-300:])
    ca, cb = calls(s.A), calls(s.B)
    check(ca, "夹具自检：原命令没有调用假测试", out_a[-300:])
    check(ca == cb, "测试调用序列（argv/cwd/env）不同" + ctx, (ca, cb))
    ta, tb = tree(s.A), tree(s.B)
    check(ta == tb, "项目目录树不同（副作用不一致或在项目里写了文件）" + ctx,
          sorted(set(ta.items()) ^ set(tb.items()))[:6])
    la, lb = lines(out_a), lines(out_b)
    check(len(la) >= 100, "夹具自检：原输出应足够长", len(la))
    check(len(lb) <= ratio * len(la), f"输出没有变短：原 {len(la)} 行，改写后 {len(lb)} 行" + ctx, out_b[:300])
    for k in keys:
        check(any(k in x for x in la), f"夹具自检：原输出缺关键行 {k!r}")
        check(any(k in x for x in lb), f"关键行丢失：{k!r}" + ctx, out_b[-600:])
    pos = -1
    for k in plain:
        check(k in la, f"夹具自检：原输出缺普通行 {k!r}")
        check(k in lb, f"非测试段输出被改动/丢失：{k!r}" + ctx, out_b[-600:])
        i = lb.index(k)
        check(i > pos, f"非测试段输出顺序变了：{k!r}", out_b[-600:])
        pos = i
    for k in absent:
        check(not any(k in x for x in lb), f"不应出现的行出现了：{k!r}", out_b[-600:])
    return out_a, out_b, new


def must_keep(s, command, spec=None, files=None):
    s.project(spec, files)
    new, blocked = s.hook(command)
    check(blocked is None, "不应拦截", blocked)
    check(new == command, "不应改写", new)


PY_KEYS = ("FAILED tests/test_calc.py::test_divide_by_zero", "E       ZeroDivisionError: division by zero",
           "tests/test_calc.py:42: ZeroDivisionError", "1 failed, 240 passed in 0.42s")

# ---------------------------------------------------------------- R：必须改写
@case("R01", "pytest -q 长输出 1 个失败", "R", "doc")
def r01(s):
    must_rewrite(s, "pytest -q", keys=PY_KEYS, rc=1)


@case("R02", "python -m pytest -v 长输出全通过（rc 0）", "R", "doc")
def r02(s):
    must_rewrite(s, "python -m pytest -v tests", spec={"mode": "pass"}, keys=("240 passed in 0.40s",), rc=0)


@case("R03", "npm test（jest 风格，结果在 stderr）1 个失败", "R", "doc")
def r03(s):
    must_rewrite(s, "npm test", keys=("FAIL src/math.test.js", "● math › subtracts",
                                      "Tests:       1 failed, 241 passed, 242 total"), rc=1)


@case("R04", "node --test（TAP）1 个失败", "R", "doc")
def r04(s):
    must_rewrite(s, "node --test test/", keys=("not ok 241 - subtracts", "Expected values to be strictly equal",
                                              "# fail 1"), rc=1)


@case("R05", "pytest 退出码 3（INTERNALERROR）", "R", "doc")
def r05(s):
    must_rewrite(s, "pytest -q", spec={"mode": "rc3"}, keys=("INTERNALERROR> RuntimeError: plugin tf2 crashed",), rc=3)


@case("R06", "环境变量前缀含带空格的单引号值", "R", "doc")
def r06(s):
    must_rewrite(s, "FOO=1 BAR='a b; c' pytest -q", keys=PY_KEYS, rc=1)
    check(calls(s.B)[0]["env"] == {"FOO": "1", "BAR": "a b; c"}, "环境变量值被改", calls(s.B))


@case("R07", "参数里有引号、括号、空格路径", "R", "doc")
def r07(s):
    must_rewrite(s, "pytest -q -k 'not slow and (a or b)' \"tests/dir with space\"", keys=PY_KEYS, rc=1)
    check(calls(s.B)[0]["argv"] == ["-q", "-k", "not slow and (a or b)", "tests/dir with space"], "argv 被改", calls(s.B))


@case("R08", "测试段后 ; echo \"$?\" 读上一段退出码", "R", "doc")
def r08(s):
    must_rewrite(s, 'pytest -q; echo "after rc=$?"', keys=PY_KEYS, plain=("after rc=1",), rc=0)


@case("R09", "pytest && A || B 链，失败走 B 分支", "R", "doc")
def r09(s):
    must_rewrite(s, 'pytest -q && echo ALLOK_TF2 || echo "CHAIN_FAILED rc=$?"', keys=PY_KEYS,
                 plain=("CHAIN_FAILED rc=1",), absent=("ALLOK_TF2",), rc=0)


@case("R10", "set -e 下失败：后续不执行、退出码 1、摘要可见", "R", "doc")
def r10(s):
    must_rewrite(s, "set -e; pytest -q; echo SHOULD_NOT_RUN_TF2", keys=PY_KEYS, absent=("SHOULD_NOT_RUN_TF2",), rc=1)


@case("R11", "多行：两次 pytest（先失败后通过）夹着 echo", "R", "doc")
def r11(s):
    must_rewrite(s, "echo START_TF2\npytest -q tests/a\npytest -q tests/b\necho \"END_TF2 $?\"",
                 spec={"by_arg": {"tests/a": "fail", "tests/b": "pass"}},
                 keys=PY_KEYS + ("240 passed in 0.40s",), plain=("START_TF2", "END_TF2 0"), rc=0)


@case("R12", "反斜线续行的多行参数", "R", "doc")
def r12(s):
    must_rewrite(s, "pytest \\\n  -q \\\n  -k bad \\\n  tests", keys=PY_KEYS, rc=1)
    check(calls(s.B)[0]["argv"] == ["-q", "-k", "bad", "tests"], "argv 被改", calls(s.B))


@case("R13", "参数里的 \\ 转义空格加 #（不是注释）", "R", "doc")
def r13(s):
    must_rewrite(s, "pytest -q tests/odd\\ #dir", keys=PY_KEYS, rc=1)
    check(calls(s.B)[0]["argv"] == ["-q", "tests/odd #dir"], "argv 被改", calls(s.B))


@case("R14", "行尾注释里有 ; && 和别的测试命令", "R", "doc")
def r14(s):
    must_rewrite(s, "pytest -q   # ; npm test && node --test", keys=PY_KEYS, rc=1)
    check([c["tool"] for c in calls(s.B)] == ["pytest"], "注释里的命令被执行", calls(s.B))


@case("R15", "cd 子目录 && pytest（cwd 必须保持）", "R", "doc")
def r15(s):
    must_rewrite(s, "cd sub && pytest -q", keys=PY_KEYS, rc=1)
    check(calls(s.B)[0]["cwd"] == "sub", "pytest 不在 sub 里运行", calls(s.B))


@case("R16", "单行 [[ … && … ]] && pytest", "R", "beyond")
def r16(s):
    must_rewrite(s, "[[ -f conftest.py && -d tests ]] && pytest -q", keys=PY_KEYS, rc=1)


@case("R17", "heredoc 写文件后再跑 pytest", "R", "beyond")
def r17(s):
    cmd = "cat > notes_tf2.txt <<'EOF'\nstep 1; pytest -q && npm test\n$HOME `date` $(id)\nEOF\npytest -q"
    must_rewrite(s, cmd, keys=PY_KEYS, rc=1)
    check([c["tool"] for c in calls(s.B)] == ["pytest"], "heredoc 正文里的命令被执行", calls(s.B))
    check((s.B / "notes_tf2.txt").read_text() == "step 1; pytest -q && npm test\n$HOME `date` $(id)\n",
          "heredoc 写出的内容不对", (s.B / "notes_tf2.txt").read_text())


@case("R18", "非测试段有管道，测试段单独", "R", "beyond")
def r18(s):
    must_rewrite(s, "printf 'b_tf2\\na_tf2\\n' | sort; pytest -q", keys=PY_KEYS, plain=("a_tf2", "b_tf2"), rc=1)


@case("R19", "双引号 echo 里有 ; && 和测试词，后接真测试", "R", "doc")
def r19(s):
    must_rewrite(s, 'echo "step; pytest -q && npm test" && pytest -q', keys=PY_KEYS,
                 plain=("step; pytest -q && npm test",), rc=1)
    check([c["tool"] for c in calls(s.B)] == ["pytest"], "引号里的命令被执行", calls(s.B))


@case("R20", "关键报错只写在 stderr（conftest 导入失败，rc 4）", "R", "doc")
def r20(s):
    must_rewrite(s, "pytest -q", spec={"mode": "stderr"},
                 keys=("E   ModuleNotFoundError: No module named 'missing_dep_tf2'", "1 error in 0.30s"), rc=4)


@case("R21", "完整日志落盘且可见输出给出日志路径", "R", "doc")
def r21(s):
    out_a, out_b, _ = must_rewrite(s, "pytest -q", keys=PY_KEYS, rc=1)
    logs = [p for p in s.logdir.rglob("*") if p.is_file()] if s.logdir.exists() else []
    check(logs, "state/testlogs/ 下没有日志文件")
    want = set(lines(out_a)) - {""}
    full = [p for p in logs if want <= set(lines(ANSI.sub("", p.read_text("utf-8", "replace"))))]
    check(full, "没有一个日志文件包含完整原输出", [str(p) for p in logs])
    home_rel = "~/" + str(full[0].relative_to(s.home))
    check(str(full[0]) in out_b or home_rel in out_b or full[0].name in out_b, "可见输出里没有日志路径", out_b[:300])


@case("R22", "Lean 项目 lake build（画像识别），error 和 sorry 都要可见", "R", "doc")
def r22(s):
    must_rewrite(s, "lake build", files={"lakefile.toml": 'name = "proj"\n', "lean-toolchain": "leanprover/lean4:v4.12.0\n"},
                 keys=("error: ./Proj/Main.lean:3:2: unsolved goals", "declaration uses 'sorry'", "error: build failed"),
                 rc=1)


@case("R23", "Rust 项目 cargo test（画像识别），rc 101", "R", "doc")
def r23(s):
    must_rewrite(s, "cargo test", files={"Cargo.toml": '[package]\nname = "tf2"\nversion = "0.1.0"\n'},
                 keys=("test tests::sub ... FAILED", "panicked at src/lib.rs:10:5",
                       "test result: FAILED. 240 passed; 1 failed"), rc=101)


@case("R24", "PYTHONPATH=src python -m pytest", "R", "doc")
def r24(s):
    must_rewrite(s, "PYTHONPATH=src python -m pytest -q tests", keys=PY_KEYS, rc=1)
    check(calls(s.B)[0]["env"] == {"PYTHONPATH": "src"}, "PYTHONPATH 丢失", calls(s.B))


@case("R25", "node --test 失败 && npm test（后者不应执行）", "R", "doc")
def r25(s):
    must_rewrite(s, "node --test test/ && npm test", keys=("not ok 241 - subtracts", "# fail 1"), rc=1)
    check([c["tool"] for c in calls(s.B)] == ["node"], "&& 短路被破坏", calls(s.B))


@case("R26", "多行 [[ ]]（条件里换行）后接 pytest", "R", "beyond")
def r26(s):
    must_rewrite(s, "[[ -f conftest.py &&\n   -d tests ]]\npytest -q", keys=PY_KEYS, rc=1)


@case("R27", "jest：失败块在中间，后面还有 200 行通过", "R", "doc")
def r27(s):
    must_rewrite(s, "npm test", spec={"mode": "mid_fail"},
                 keys=("FAIL src/math.test.js", "\u25cf math \u203a subtracts", "Expected: 1", "Received: 2",
                       "Tests:       1 failed"), rc=1)


@case("R28", "25 个失败：每个失败的测试名都要可见", "R", "doc")
def r28(s):
    _, out_b, _ = must_rewrite(s, "pytest -q", spec={"mode": "many_fail"}, keys=("25 failed, 275 passed in 0.90s",), rc=1)
    vis = lines(out_b)
    # 任意一种形式都算：进度行 "…::test_005 FAILED" 或汇总行 "FAILED …::test_005 - …"
    lost = [f"test_{i:03d}" for i in range(300) if i % 12 == 5
            if not any(f"::test_{i:03d}" in x and "FAILED" in x for x in vis)]
    check(not lost, f"{len(lost)}/25 个失败的测试名在可见输出里一处都没有", lost)


@case("R29", "set -euo pipefail 下多行测试", "R", "doc")
def r29(s):
    must_rewrite(s, "set -euo pipefail\necho BEGIN_TF2\npytest -q\necho NEVER_TF2", keys=PY_KEYS,
                 plain=("BEGIN_TF2",), absent=("NEVER_TF2",), rc=1)


@case("R30", "测试通过后 ; exit 3", "R", "doc")
def r30(s):
    must_rewrite(s, "pytest -q; exit 3", spec={"mode": "pass"}, keys=("240 passed in 0.40s",), rc=3)


@case("R31", "timeout 120 pytest（常见包装前缀）", "R", "beyond")
def r31(s):
    must_rewrite(s, "timeout 120 pytest -q", keys=PY_KEYS, rc=1)


@case("R32", "uv run pytest（pyproject 项目）", "R", "beyond")
def r32(s):
    must_rewrite(s, "uv run pytest -q", files={"pyproject.toml": '[project]\nname = "tf2"\nversion = "0.1"\n',
                                                "uv.lock": "version = 1\n"}, keys=PY_KEYS, rc=1)


# ---------------------------------------------------------------- N：不应改写
@case("N01", "pytest --pdb（交互）", "N", "doc")
def n01(s):
    must_keep(s, "pytest -q -x --pdb tests")


@case("N02", "node --test --watch（常驻）", "N", "doc")
def n02(s):
    must_keep(s, "node --test --watch test/")


@case("N03", "本来就短的失败输出：可见输出必须和原输出逐行一致", "N", "doc")
def n03(s):
    cmd = "pytest -q"
    s.project({"mode": "short_fail"})
    new, blocked = s.hook(cmd)
    check(blocked is None, "不应拦截", blocked)
    rc_a, out_a = s.bash(cmd, s.A)
    rc_b, out_b = s.bash(new, s.B)
    check(rc_a == rc_b == 1, "退出码不同", (rc_a, rc_b))
    la, lb = lines(out_a), lines(out_b)
    it = iter(lb)
    check(all(x in it for x in la), "短输出有行丢失或顺序变化", out_b)
    check(len(lb) <= len(la) + 2, "短输出被加了太多行", out_b)
    check(calls(s.A) == calls(s.B), "调用序列不同", (calls(s.A), calls(s.B)))


@case("N04", "不是测试的命令（含 pytest 字样）", "N", "doc")
def n04(s):
    must_keep(s, 'which pytest; echo "pytest -q && npm test"; npm install; cat pytest.ini; ls tests')


@case("N05", "用户自己已经 | tail 截了输出", "N", "doc")
def n05(s):
    must_keep(s, "pytest -q | tail -n 20")


@case("N06", "USAGE_SAVER_RAW=1 显式要原样输出", "N", "doc")
def n06(s):
    must_keep(s, "USAGE_SAVER_RAW=1 pytest -q")


def selected(code):
    only = tuple(x.strip() for x in os.environ.get("HELDOUT_ONLY", "").split(",") if x.strip())
    skip = tuple(x.strip() for x in os.environ.get("HELDOUT_SKIP", "").split(",") if x.strip())
    return (not only or code.startswith(only)) and not (skip and code.startswith(skip))


def main():
    res = {"R": [0, 0], "N": [0, 0]}
    for code, scene, kind, basis, fn in CASES:
        if not selected(code):
            continue
        folder = tempfile.mkdtemp(prefix="heldout-tf2-" + code + "-")
        error = None
        try:
            fn(Box(folder))
        except Exception as exc:
            error = type(exc).__name__ + ": " + str(exc)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        tag = "" if basis == "doc" else "[超出文档]"
        res[kind][1] += 1
        if error:
            print(f"FAIL {code}{tag} {scene} — " + " ".join(error.split())[:400], flush=True)
        else:
            res[kind][0] += 1
            print(f"PASS {code}{tag} {scene}", flush=True)
    p = res["R"][0] + res["N"][0]
    t = res["R"][1] + res["N"][1]
    print(f"总数 {t} | PASS {p} | FAIL {t - p} | R(必须改写) {res['R'][0]}/{res['R'][1]} | N(不应改写) {res['N'][0]}/{res['N'][1]}",
          flush=True)
    return int(p != t)


if __name__ == "__main__":
    sys.exit(main())
