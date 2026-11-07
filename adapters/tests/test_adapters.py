#!/usr/bin/env python3
"""adapters/ 的离线测试（第 5 轮 H）。跑法：python3 -m pytest -q adapters/tests/test_adapters.py
（也可 python3 adapters/tests/test_adapters.py）。全部在临时目录里，不碰 ~/.codex、~/.claude。"""
import io, json, os, re, shutil, subprocess, sys, tempfile, unittest

T = os.path.dirname(os.path.realpath(__file__))
AD = os.path.dirname(T)
REPO = os.path.dirname(AD)
sys.path[:0] = [os.path.join(AD, "common"), os.path.join(AD, "codex"), AD, os.path.join(REPO, "hooks")]
import usaver_run as UR
import codex_usage as CU
BIN = os.path.join(AD, "bin", "usaver-run")
PY = sys.executable


def sh(args, env=None, cwd=None):
    e = dict(os.environ); e.update(env or {})
    return subprocess.run(args, capture_output=True, text=True, env=e, cwd=cwd, timeout=120)


class UsaverRun(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="ur-")
        self.env = {"USAVER_LOGDIR": os.path.join(self.d, "logs")}
        self.env.pop("USAGE_SAVER_RAW", None)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_bin(self, *args, env=None):
        e = dict(self.env); e.update(env or {})
        return sh([BIN, *args], env=e)

    def logs(self):
        p = self.env["USAVER_LOGDIR"]
        return [os.path.join(p, f) for f in os.listdir(p) if f.startswith("run-")] if os.path.isdir(p) else []

    def test_short_output_verbatim_and_exit_code(self):
        r = self.run_bin(PY, "-c", "print('a'); print('b'); raise SystemExit(3)")
        self.assertEqual(r.returncode, 3)
        self.assertIn("a\nb\n", r.stdout)
        self.assertNotIn("filtered", r.stdout)
        self.assertEqual(len(self.logs()), 1)

    def test_long_failing_output_is_filtered(self):
        code = ("import sys\nfor i in range(500): print('line', i)\n"
                "print('FAILED tests/test_x.py::test_y - AssertionError'); print('E   assert 1 == 2')\n"
                "for i in range(30): print('after', i)\nsys.exit(1)")
        r = self.run_bin(PY, "-c", code)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED tests/test_x.py::test_y", r.stdout)
        self.assertIn("----- 结尾 -----", r.stdout)          # R6H2：摘要改由 test_filter.summarize 生成
        self.assertIn("after 29", r.stdout)
        self.assertNotIn("line 100\n", r.stdout)
        self.assertIn("all 532 lines", r.stdout)
        self.assertLess(len(r.stdout.splitlines()), 60)
        with open(self.logs()[0]) as f:
            self.assertEqual(len(f.read().splitlines()), 532)       # 完整日志都在

    def test_long_passing_output_only_tail(self):
        r = self.run_bin(PY, "-c", "for i in range(300): print('ok', i)")
        self.assertEqual(r.returncode, 0)
        self.assertIn("ok 299", r.stdout)
        self.assertNotIn("ok 10\n", r.stdout)

    def test_sh_mode_chain_and_rc(self):
        r = self.run_bin("--sh", "echo one && false && echo never")
        self.assertEqual(r.returncode, 1)
        self.assertIn("one", r.stdout)
        self.assertNotIn("never", r.stdout)

    def test_command_not_found(self):
        r = self.run_bin("definitely-not-a-command-xyz")
        self.assertEqual(r.returncode, 127)
        self.assertIn("command not found", r.stdout)

    def test_signal_exit_code(self):
        r = self.run_bin(PY, "-c", "import os,signal; os.kill(os.getpid(), signal.SIGKILL)")
        self.assertEqual(r.returncode, 137)

    def test_raw_passthrough(self):
        r = self.run_bin(PY, "-c", "for i in range(300): print('raw', i)", env={"USAGE_SAVER_RAW": "1"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(r.stdout.splitlines()), 300)
        self.assertEqual(self.logs(), [])

    def test_interactive_flag_passthrough(self):
        r = self.run_bin(PY, "-c", "import sys; print(sys.argv[1:]); [print(i) for i in range(100)]", "--watch")
        self.assertEqual(len(r.stdout.splitlines()), 101)
        self.assertEqual(self.logs(), [])

    def test_unwritable_logdir_falls_back_to_tmp(self):
        """沙箱里套件目录写不了：退到 $TMPDIR/usaver-testlogs，仍然过滤（不是原样输出）。"""
        ro = os.path.join(self.d, "ro"); os.makedirs(ro); os.chmod(ro, 0o500)
        tmp = os.path.join(self.d, "tmp"); os.makedirs(tmp)
        try:
            r = self.run_bin(PY, "-c", "for i in range(300): print('x', i)",
                             env={"USAVER_LOGDIR": os.path.join(ro, "logs"), "TMPDIR": tmp})
        finally:
            os.chmod(ro, 0o700)
        self.assertEqual(r.returncode, 0)
        self.assertIn(os.path.join(tmp, "usaver-testlogs"), r.stdout)
        self.assertIn("filtered", r.stdout)

    def test_usage_without_command(self):
        r = self.run_bin()
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage:", r.stderr)

    def test_select_lines_matches_grep(self):
        """与 test_filter 里实际用的 `grep -nE -B2 -A12 PAT | head -120` 输出逐行一致。"""
        import random
        rnd = random.Random(7)
        words = ["ok", "FAIL x", "ERROR y", "Traceback (most recent call last):", "assert a", "plain", "not ok 3", "error: z"]
        for trial in range(40):
            lines = [rnd.choice(words) if rnd.random() < 0.08 else "row %d" % i for i in range(rnd.randint(1, 400))]
            p = os.path.join(self.d, "g%d.txt" % trial)
            with open(p, "w") as f:
                f.write("\n".join(lines) + "\n")
            g = subprocess.run("grep -nE -B2 -A12 '%s' %s | head -120" % (UR.GREP_PAT, p), shell=True,
                               capture_output=True, text=True).stdout.splitlines()
            self.assertEqual(UR.select_lines(lines), g, "trial %d" % trial)

    def test_constants_shared_with_test_filter(self):
        import test_filter
        self.assertEqual(UR.GREP_PAT, test_filter.GREP_PAT)
        self.assertEqual(UR.SHORT, test_filter.SHORT)

    def test_real_pytest_failure(self):
        proj = os.path.join(self.d, "proj")
        os.makedirs(proj)
        with open(os.path.join(proj, "test_m.py"), "w") as f:
            f.write("import pytest\n@pytest.mark.parametrize('i', range(60))\ndef test_ok(i):\n    print('noise' * 20)\n    assert True\n"
                    "def test_bad():\n    x = 41\n    assert x + 1 == 43\n")
        r = sh([BIN, PY, "-m", "pytest", "-v", "-p", "no:cacheprovider"], env=self.env, cwd=proj)
        self.assertEqual(r.returncode, 1)
        self.assertIn("test_bad", r.stdout)
        self.assertIn("assert", r.stdout)
        self.assertIn("1 failed, 60 passed", r.stdout)
        full = open(self.logs()[0]).read()
        self.assertLess(len(r.stdout), len(full))

    # ---- R6H2：摘要改调 test_filter.summarize（只保留一份实现），出错退回旧规则
    R28_FAILS = [10, 22, 34, 46, 58, 70, 82, 94, 106, 118, 125, 137, 149, 161, 173, 185, 197, 209, 221, 233, 245,
                 257, 269, 281, 293]

    def mk_r28(self):
        """300 个测试、25 个失败（分散在中段），每个失败的测试体 11 行（与 tools/tests_r6T.py 的 R28 形状相同）。"""
        proj = os.path.join(self.d, "r28")
        os.makedirs(proj)
        src = []
        for i in range(1, 301):
            if i in self.R28_FAILS:
                body = "".join("    v%d = %d * %d\n" % (j, i, j) for j in range(11))
                src.append("def test_%03d():\n%s    x = %d\n    assert x == %d + 1, 'value mismatch'\n" % (i, body, i, i))
            else:
                src.append("def test_%03d():\n    assert True\n" % i)
        with open(os.path.join(proj, "test_many.py"), "w") as f:
            f.write("\n".join(src))
        return proj

    def test_r28_real_pytest_all_25_names_visible(self):
        """真实 pytest，300 个测试 25 个失败：25 个名字全在、每个失败有 E 行、rc 保留、可见输出 < 30000 字节。
        旧的 grep -B2 -A12 | head -120 只能看到 11 个名字。"""
        proj = self.mk_r28()
        for args in (["-q"], ["-v"]):
            r = sh([BIN, PY, "-m", "pytest", "-p", "no:cacheprovider", *args], env=self.env, cwd=proj)
            out = r.stdout + r.stderr
            self.assertEqual(r.returncode, 1, args)
            names = set(re.findall(r"^FAILED test_many\.py::(test_\d+)", out, re.M))
            self.assertEqual(names, {"test_%03d" % i for i in self.R28_FAILS}, args)
            for i in self.R28_FAILS:
                self.assertRegex(out, r"(?m)^E\s+assert %d == \(?%d \+ 1" % (i, i))
            self.assertLess(len(out.encode("utf-8")), 30000, args)
            self.assertNotRegex(out, r"::test_\d+ PASSED")          # 进度行只计数
            self.assertIn("25 failed, 275 passed", out)
            self.assertIn("[usaver-run: output filtered; all ", out)

    def test_summary_is_test_filter_summarize_verbatim(self):
        """摘要正文与 hook 的 `test_filter.py --summarize 日志 模式` 逐字相同（只多首行路径和末行英文提示）。"""
        proj = self.mk_r28()
        r = sh([BIN, PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"], env=self.env, cwd=proj)
        log = self.logs()[0]
        hook = sh([PY, os.path.join(REPO, "hooks", "test_filter.py"), "--summarize", log, UR.GREP_PAT])
        self.assertEqual(hook.returncode, 0)
        lines = r.stdout.splitlines(True)
        self.assertEqual(lines[0], "[usaver-run: full output -> %s]\n" % log)
        self.assertEqual("".join(lines[1:-1]), hook.stdout)
        self.assertTrue(lines[-1].startswith("[usaver-run: output filtered;"))

    def run_inproc(self, cmd):
        buf = io.StringIO()
        rc = UR.run(cmd, logdir=os.path.join(self.d, "logs"), out=buf)
        return rc, buf.getvalue()

    def test_fallback_when_test_filter_summarize_raises(self):
        code = "import sys\nfor i in range(300): print('row', i)\nprint('FAILED t.py::test_z - boom')\nsys.exit(5)"
        orig = UR._tf_summarize
        def boom(*a, **k):
            raise RuntimeError("x")
        try:
            for fake in (boom, None, lambda *a, **k: "", lambda *a, **k: 123):
                UR._tf_summarize = fake
                rc, out = self.run_inproc([PY, "-c", code])
                self.assertEqual(rc, 5)
                self.assertIn("----- tail -----", out)              # 旧规则
                self.assertRegex(out, r"(?m)^301:FAILED t\.py::test_z")
                self.assertIn("all 301 lines", out)
        finally:
            UR._tf_summarize = orig

    def test_rc_kept_even_if_summarize_itself_fails(self):
        orig = UR.summarize
        def boom(*a, **k):
            raise RuntimeError("x")
        try:
            UR.summarize = boom
            rc, out = self.run_inproc([PY, "-c", "for i in range(300): print(i)\nraise SystemExit(7)"])
        finally:
            UR.summarize = orig
        self.assertEqual(rc, 7)
        self.assertIn("could not summarize", out)

    def test_standalone_copy_without_test_filter(self):
        """只拷走 adapters/（旁边没有 hooks/test_filter.py）：不崩，走旧规则，退出码保留。"""
        kit = os.path.join(self.d, "kit", "adapters", "common")
        os.makedirs(kit)
        shutil.copy(os.path.join(AD, "common", "usaver_run.py"), kit)
        code = "import sys\nfor i in range(300): print('row', i)\nprint('FAILED t.py::test_z')\nsys.exit(4)"
        e = dict(self.env, PYTHONPATH="")
        r = sh([PY, "-I", os.path.join(kit, "usaver_run.py"), PY, "-c", code], env=e)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("----- tail -----", r.stdout)
        self.assertIn("301:FAILED t.py::test_z", r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    # ---- R6H3：短日志（≤ 40 行）也要过字节和行长两道门槛（Codex 复现 r6rev_H2/reproduce_short.py）
    def mk_short_big(self):
        proj = os.path.join(self.d, "shortbig")
        os.makedirs(proj)
        with open(os.path.join(proj, "test_s.py"), "w") as f:
            f.write("def test_failure():\n    assert False, '中' * 12000\n\ndef test_second():\n    assert 1 == 2\n")
        return proj

    def check_short_big(self, out, rc):
        self.assertEqual(rc, 1)
        b = out.encode("utf-8")
        self.assertLess(len(b), 16000 + 1000)
        self.assertTrue(all(len(t) < 500 for t in out.splitlines()), max(len(t) for t in out.splitlines()))
        for n in ("test_failure", "test_second"):
            self.assertIn("FAILED test_s.py::" + n, out)
        self.assertIn("test_second", b[:2048].decode("utf-8", "ignore"))   # Claude Code 落盘时的 2KB 预览里也看得到

    def test_short_log_with_huge_line_is_summarized(self):
        proj = self.mk_short_big()
        r = sh([BIN, PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"], env=self.env, cwd=proj)
        log = self.logs()[0]
        raw = open(log, encoding="utf-8").read()
        self.assertLessEqual(len(raw.splitlines()), UR.SHORT)              # 行数门槛本身挡不住
        self.assertGreater(len(raw.encode("utf-8")), 30000)
        self.check_short_big(r.stdout, r.returncode)
        self.assertRegex(r.stdout, r"(?m)^E\s+assert 1 == 2")
        self.assertIn("[usaver-run: output filtered;", r.stdout)

    def test_short_log_gates_bytes_and_line_length(self):
        """三条门槛各自单独触发；全满足才原样。"""
        p = "/x/log"
        self.assertEqual(UR.summarize("a\nb\n", p), "a\nb\n")
        self.assertEqual(UR.summarize("x" * UR.LINE_CHARS + "\n", p), "x" * UR.LINE_CHARS + "\n")
        self.assertIn("output filtered", UR.summarize("x" * (UR.LINE_CHARS + 1) + "\n", p))   # 只有一行，但太长
        many = ("中" * 130 + "\n") * 39                                       # 39 行、每行 130 字符，约 15.2KB
        self.assertEqual(UR.summarize(many, p), many)
        more = ("中" * 140 + "\n") * 39                                       # 39 行，约 16.4KB > 16000 字节
        self.assertIn("output filtered", UR.summarize(more, p))

    def test_short_big_standalone_fallback_still_bounded(self):
        """没有 test_filter 时走旧规则，超长行也要截断，第二个失败仍在、输出远低于 30000 字节。"""
        proj = self.mk_short_big()
        kit = os.path.join(self.d, "kit2", "adapters", "common")
        os.makedirs(kit)
        shutil.copy(os.path.join(AD, "common", "usaver_run.py"), kit)
        r = sh([PY, "-I", os.path.join(kit, "usaver_run.py"), PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
               env=self.env, cwd=proj)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("----- tail -----", r.stdout)
        self.assertLess(len(r.stdout.encode("utf-8")), 30000)
        self.assertIn("test_second", r.stdout)


def rec_tur(ts, rid, inp, cached, out, reasoning=0):
    u = dict(input_tokens=inp, cached_input_tokens=cached, cache_write_input_tokens=0, output_tokens=out,
             reasoning_output_tokens=reasoning, total_tokens=inp + out)
    return {"timestamp": ts, "type": "token_usage_record", "payload": {"response_id": rid, "usage": u}}


def rec_tc(ts, last, total, rl=None):
    mk = lambda inp, cached, out: dict(input_tokens=inp, cached_input_tokens=cached, cache_write_input_tokens=0,
                                       output_tokens=out, reasoning_output_tokens=0, total_tokens=inp + out)
    return {"timestamp": ts, "type": "event_msg", "payload": {"type": "token_count",
            "info": {"last_token_usage": mk(*last), "total_token_usage": mk(*total)}, "rate_limits": rl}}


def meta(sid, cwd, source="exec", **kw):
    return {"timestamp": "2026-11-05T00:00:00Z", "type": "session_meta",
            "payload": dict(session_id=sid, cwd=cwd, source=source, originator="codex_exec" if source == "exec" else "x", **kw)}


def tctx(model):
    return {"timestamp": "2026-11-05T00:00:00Z", "type": "turn_context", "payload": {"model": model}}


class CodexUsage(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="cxhome-")
        self.sess = os.path.join(self.home, "sessions", "2026", "11", "05")
        os.makedirs(self.sess)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def write(self, name, rows, zst=False):
        p = os.path.join(self.sess, name)
        with open(p, "w") as f:
            for r in rows:
                f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
        if zst:
            subprocess.run(["zstd", "-q", "--rm", p], check=True)
            p += ".zst"
        return p

    def run_cu(self, *args):
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            CU.main(["--sessions", os.path.join(self.home, "sessions"), "--since", "2026-11-01", "--json", *args])
        finally:
            sys.stdout = old
        return json.loads(buf.getvalue())

    def test_token_usage_records_and_cost(self):
        self.write("rollout-a.jsonl", [meta("s1", "/Users/x/p"), tctx("model-new-9"),
                                       rec_tur("2026-11-05T01:00:00Z", "r1", 10000, 8000, 100),
                                       rec_tur("2026-11-05T01:01:00Z", "r2", 20000, 18000, 200, 50),
                                       "not json {", rec_tc("2026-11-05T01:01:01Z", (20000, 18000, 200), (30000, 26000, 300))])
        j = self.run_cu()
        t = j["summary"]["total"]
        self.assertEqual(t["calls"], 2)                    # token_count 不重复计
        # kit 权重：缓存 0.1、未缓存 1、输出 5 → r1 = 800+2000+500，r2 = 1800+2000+1000
        self.assertAlmostEqual(t["cost"], 3300 + 4800)
        self.assertEqual(t["reasoning_tokens"], 50)
        self.assertIn("model=model-new-9", j["summary"]["groups"])   # 新模型名原样记，不崩、不归错类
        j2 = self.run_cu("--weights", "openai")
        self.assertAlmostEqual(j2["summary"]["total"]["cost"], 800 + 2000 + 800 + 1800 + 2000 + 1600)

    def test_token_count_fallback_dedup(self):
        rl = {"primary": {"used_percent": 42.0, "window_minutes": 300, "resets_at": 1893456000},
              "secondary": {"used_percent": 19.0, "window_minutes": 10080, "resets_at": 1893999000}, "plan_type": "plus"}
        self.write("rollout-b.jsonl", [meta("s2", "/Users/x/q", source="vscode"),
                                       rec_tc("2026-11-05T02:00:00Z", (0, 0, 0), (0, 0, 0)),          # 开场空读数
                                       rec_tc("2026-11-05T02:00:01Z", (5000, 0, 10), (5000, 0, 10)),
                                       rec_tc("2026-11-05T02:00:02Z", (5000, 0, 10), (5000, 0, 10), rl),  # 重复
                                       rec_tc("2026-11-05T02:00:03Z", (6000, 5000, 20), (11000, 5000, 30))])
        j = self.run_cu()
        self.assertEqual(j["summary"]["total"]["calls"], 2)
        self.assertEqual(j["summary"]["groups"]["origin=interactive"]["calls"], 2)
        self.assertEqual(j["rate"]["rate_limits"]["five_hour"]["used_percentage"], 42.0)
        self.assertEqual(j["rate"]["rate_limits"]["seven_day"]["used_percentage"], 19.0)

    @unittest.skipUnless(shutil.which("zstd"), "zstd not installed")
    def test_zst_files(self):
        self.write("rollout-c.jsonl", [meta("s3", "/Users/x/r"), rec_tur("2026-11-05T03:00:00Z", "r9", 1000, 0, 10)], zst=True)
        self.assertEqual(self.run_cu()["summary"]["total"]["calls"], 1)

    def test_tmp_imported_subagent_window(self):
        self.write("rollout-d.jsonl", [meta("s4", "/private/tmp/bench"), rec_tur("2026-11-05T04:00:00Z", "r4", 1000, 0, 10)])
        self.write("rollout-e.jsonl", [meta("s5", "/Users/x/imp", source="vscode"),
                                       rec_tc("2026-11-05T04:00:00Z", (9000, 0, 9), (9000, 0, 9))])
        self.write("rollout-f.jsonl", [meta("s6", "/Users/x/sub", source={"subagent": {"thread_spawn": {}}}),
                                       rec_tur("2026-11-05T05:00:00Z", "r6", 2000, 0, 10),
                                       rec_tur("2026-10-01T05:00:00Z", "r7", 2000, 0, 10)])   # 窗口外
        with open(os.path.join(self.home, "external_agent_session_imports.json"), "w") as f:
            json.dump({"records": [{"imported_thread_id": "s5"}]}, f)
        j = self.run_cu()
        self.assertEqual(j["summary"]["total"]["calls"], 1)
        self.assertEqual(j["summary"]["groups"]["side=sub"]["calls"], 1)
        self.assertEqual(j["meta"]["imported"], 1)
        self.assertEqual(self.run_cu("--include-tmp", "--include-imported")["summary"]["total"]["calls"], 3)

    def test_empty_and_unreadable(self):
        self.write("rollout-g.jsonl", ["{}", "garbage"])
        with open(os.path.join(self.sess, "rollout-h.jsonl.zst"), "wb") as f:
            f.write(b"not zstd")
        j = self.run_cu()
        self.assertEqual(j["summary"]["total"]["calls"], 0)
        self.assertEqual(j["meta"]["skipped"], 1)
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            CU.main(["--sessions", os.path.join(self.home, "sessions"), "--since", "2026-11-01"])
        finally:
            sys.stdout = old
        self.assertIn("没有请求记录", buf.getvalue())

    def test_emit_rate_feeds_context_guard(self):
        """--emit-rate 写出的文件能被 context_guard.get_mode() 当 rate.json 读（额度分档跨工具复用）。"""
        import time
        now = time.time()
        rl = {"primary": {"used_percent": 95.0, "window_minutes": 300, "resets_at": now + 3600},
              "secondary": {"used_percent": 30.0, "window_minutes": 10080, "resets_at": now + 86400 * 3}}
        self.write("rollout-i.jsonl", [meta("s9", "/Users/x/z"),
                                       rec_tc("2026-11-05T06:00:00Z", (100, 0, 1), (100, 0, 1), rl)])
        st = os.path.join(self.home, "state")
        self.run_cu("--emit-rate", os.path.join(st, "rate.json"))
        r = sh([PY, "-c", "import sys; sys.path.insert(0, %r); import context_guard as G; print(G.get_mode()[0])"
                % os.path.join(REPO, "hooks")], env={"USAGE_SAVER_STATE": st})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "快用完")        # 5 小时窗口 95% ≥ near_limit_pct


class ApplyScript(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="apply-")
        self.env = {"USAVER_BACKUP_DIR": os.path.join(self.d, "bk"), "USAVER_TRASH_DIR": os.path.join(self.d, "trash")}

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def ap(self, *args):
        return sh([PY, os.path.join(AD, "apply.py"), *args], env=self.env)

    def test_apply_idempotent_and_revert_restores(self):
        tgt = os.path.join(self.d, "AGENTS.md")
        orig = "# My rules\n\n- keep it\n"
        with open(tgt, "w") as f:
            f.write(orig)
        self.assertEqual(self.ap("agents", "apply", "--target", tgt).returncode, 0)
        once = open(tgt).read()
        self.assertTrue(once.startswith(orig.rstrip("\n")))
        self.assertIn("usage-saver:begin", once)
        r = self.ap("agents", "apply", "--target", tgt)
        self.assertIn("unchanged", r.stdout)
        self.assertEqual(open(tgt).read(), once)
        self.ap("agents", "revert", "--target", tgt)
        self.assertEqual(open(tgt).read(), orig)
        self.assertEqual(len(os.listdir(self.env["USAVER_BACKUP_DIR"])), 2)   # apply 前、revert 前各一份

    def test_new_file_revert_moves_to_trash(self):
        tgt = os.path.join(self.d, "sub", "GEMINI.md")
        self.ap("agents", "apply", "--target", tgt)
        self.assertTrue(os.path.exists(tgt))
        self.ap("agents", "revert", "--target", tgt)
        self.assertFalse(os.path.exists(tgt))
        self.assertEqual(len(os.listdir(self.env["USAVER_TRASH_DIR"])), 1)

    def test_replaces_outdated_block(self):
        tgt = os.path.join(self.d, "AGENTS.md")
        with open(tgt, "w") as f:
            f.write("top\n\n<!-- usage-saver:begin v0 -->\nold rules\n<!-- usage-saver:end -->\n\nbottom\n")
        self.ap("agents", "apply", "--target", tgt)
        s = open(tgt).read()
        self.assertNotIn("old rules", s)
        self.assertEqual(s.count("usage-saver:begin"), 1)
        self.assertTrue(s.startswith("top\n") and s.rstrip().endswith("bottom"))

    def test_lite_variant_swaps_block(self):
        tgt = os.path.join(self.d, "AGENTS.md")
        self.ap("agents", "apply", "--target", tgt)
        self.ap("agents", "apply", "--target", tgt, "--variant", "lite")
        s = open(tgt).read()
        self.assertIn("lite-v1", s)
        self.assertEqual(s.count("usage-saver:begin"), 1)

    def test_dry_run_writes_nothing(self):
        tgt = os.path.join(self.d, "AGENTS.md")
        self.ap("agents", "apply", "--target", tgt, "--dry-run")
        self.assertFalse(os.path.exists(tgt))

    def test_bin_link_unlink(self):
        d = os.path.join(self.d, "bin")
        self.ap("bin", "link", "--dir", d)
        r = sh([os.path.join(d, "usaver-run"), "echo", "hi"], env={"USAVER_LOGDIR": os.path.join(self.d, "logs")})
        self.assertIn("hi", r.stdout)
        self.assertTrue(os.path.islink(os.path.join(d, "usaver-status")))
        self.ap("bin", "unlink", "--dir", d)
        self.assertFalse(os.path.lexists(os.path.join(d, "usaver-run")))
        self.assertFalse(os.path.lexists(os.path.join(d, "usaver-status")))
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "usaver-run"), "w").close()                # 别人的同名文件：拒绝覆盖
        self.assertEqual(self.ap("bin", "link", "--dir", d).returncode, 1)


class UsaverStatus(unittest.TestCase):
    """usaver-status：假 HOME 里放假会话，跑真实命令看输出行。阈值走真实 config.json + context_guard.lines()。"""
    def setUp(self):
        self.h = tempfile.mkdtemp(prefix="st-")
        self.proj = os.path.join(self.h, "work", "proj")
        os.makedirs(self.proj)
        self.env = {"HOME": self.h, "CODEX_HOME": os.path.join(self.h, ".codex"),
                    "CLAUDE_CONFIG_DIR": os.path.join(self.h, ".claude"),
                    "USAGE_SAVER_STATE": os.path.join(self.h, "state")}

    def tearDown(self):
        shutil.rmtree(self.h, ignore_errors=True)

    def codex(self, ctx, window, p5=42.0, p7=19.0, name="rollout-x.jsonl", model="model-future-1"):
        import time
        d = os.path.join(self.h, ".codex", "sessions", "2026", "11", "06")
        os.makedirs(d, exist_ok=True)
        now = time.time()
        rl = {"primary": {"used_percent": p5, "window_minutes": 300, "resets_at": now + 3600},
              "secondary": {"used_percent": p7, "window_minutes": 10080, "resets_at": now + 86400}}
        tc = rec_tc("2026-11-06T10:00:00Z", (ctx, ctx // 2, 10), (ctx, ctx // 2, 10), rl)
        tc["payload"]["info"]["model_context_window"] = window
        rows = [meta("s", self.proj, source="vscode"), tctx(model), "garbage{", tc]
        with open(os.path.join(d, name), "w") as f:
            f.write("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n")

    def claude(self, ctx):
        import time
        d = os.path.join(self.h, ".claude", "projects", "-work-proj")
        os.makedirs(d, exist_ok=True)
        row = {"type": "assistant", "cwd": self.proj, "message": {"model": "claude-new-7", "usage": {
            "input_tokens": 10, "cache_read_input_tokens": ctx - 10, "cache_creation_input_tokens": 0}}}
        with open(os.path.join(d, "abc.jsonl"), "w") as f:
            f.write(json.dumps({"type": "user", "cwd": self.proj}) + "\n" + json.dumps(row) + "\n")
        st = self.env["USAGE_SAVER_STATE"]; os.makedirs(st, exist_ok=True)
        with open(os.path.join(st, "rate.json"), "w") as f:
            json.dump({"ts": time.time(), "rate_limits": {"five_hour": {"used_percentage": 39, "resets_at": time.time() + 999},
                                                          "seven_day": {"used_percentage": 5, "resets_at": time.time() + 9999}}}, f)

    def st(self, *args, cwd=None):
        r = sh([os.path.join(AD, "bin", "usaver-status"), *args], env=self.env, cwd=cwd or self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_codex_line_format(self):
        self.codex(50000, 475000)
        self.assertEqual(self.st(), "model-future-1 | ctx 50k/475k | 5h 42% | 7d 19%")

    def test_codex_strong_line(self):
        self.codex(390000, 475000)
        self.assertIn("⚠⚠", self.st())                     # 强线 = min(300k, 80% 窗口)

    def test_small_window_new_model_still_warns(self):
        """新模型窗口只有 128k：套件软线 160k 在窗口外，按 60% 窗口收紧，80k 就提醒。"""
        self.codex(80000, 128000)
        out = self.st()
        self.assertIn("⚠ 到线", out)
        self.assertIn("/new", out)

    def test_codex_quota_drives_tier(self):
        self.codex(110000, 475000, p5=20.0)
        self.assertNotIn("⚠", self.st())
        self.codex(110000, 475000, p5=95.0)                  # 快用完：软线 ×0.6，下限 100k
        self.assertIn("⚠ 到线", self.st())

    def test_claude_line_and_auto_pick(self):
        import time
        self.claude(224000)
        self.assertEqual(self.st("--tool", "claude"), "claude-new-7 | ctx 224k | 5h 39% | 7d 5% | ⚠ 到线：做完这步就 /clear")
        self.codex(50000, 475000)                            # 更新的是 Codex 会话 → auto 选它
        self.assertTrue(self.st().startswith("model-future-1"))
        os.utime(os.path.join(self.h, ".claude", "projects", "-work-proj", "abc.jsonl"), (time.time() + 5, time.time() + 5))
        self.assertTrue(self.st().startswith("claude-new-7"))

    def test_no_session_fallback_and_other_cwd(self):
        self.assertIn("没找到会话", self.st())
        self.codex(50000, 475000)
        other = os.path.join(self.h, "elsewhere"); os.makedirs(other)
        self.assertIn("没找到会话", self.st(cwd=other))
        self.assertTrue(self.st("--any", cwd=other).startswith("model-future-1"))

    def test_watch_mode_prints_repeatedly(self):
        self.codex(50000, 475000)
        e = dict(os.environ); e.update(self.env)
        p = subprocess.Popen([os.path.join(AD, "bin", "usaver-status"), "--watch", "0.2"], stdout=subprocess.PIPE,
                             text=True, env=e, cwd=self.proj)
        lines = [p.stdout.readline() for _ in range(3)]
        p.terminate(); p.wait(5)
        self.assertTrue(all(l.startswith("model-future-1 | ctx 50k") for l in lines), lines)

    def test_eta(self):
        import usaver_status as US
        now = 1_800_000_000
        R = now + 7200
        self.assertAlmostEqual(US.eta_5h([(now - 1800, 40.0, R), (now, 70.0, R)], now), now + 1800)
        self.assertIsNone(US.eta_5h([(now - 1800, 40.0, now + 600), (now, 70.0, now + 600)], now))   # 重置前用不完
        self.assertIsNone(US.eta_5h([(now - 60, 40.0, R), (now, 70.0, R)], now))                    # 跨度太短
        self.assertIsNone(US.eta_5h([(now - 1800, 70.0, R), (now, 70.0, R)], now))                  # 没在涨


class SkillsSync(unittest.TestCase):
    """skills_sync.py 在临时 HOME 里：用真实 ~/.claude/skills 和 ~/.agents/skills 的副本跑 apply / check / revert。"""
    def setUp(self):
        self.h = tempfile.mkdtemp(prefix="sk-")
        for sk in ("model-router", "handoff"):
            for rel in (".claude/skills", ".agents/skills"):
                src = os.path.join(os.path.expanduser("~"), rel, sk, "SKILL.md")
                if os.path.exists(src):
                    os.makedirs(os.path.join(self.h, rel, sk))
                    shutil.copy2(src, os.path.join(self.h, rel, sk, "SKILL.md"))
        self.real = {sk: open(os.path.join(os.path.expanduser("~"), ".agents/skills", sk, "SKILL.md"), "rb").read()
                     for sk in ("model-router", "handoff") if os.path.exists(os.path.join(os.path.expanduser("~"), ".agents/skills", sk, "SKILL.md"))}

    def tearDown(self):
        shutil.rmtree(self.h, ignore_errors=True)
        for sk, b in self.real.items():                          # 真实 ~/.agents 没被动过
            self.assertEqual(open(os.path.join(os.path.expanduser("~"), ".agents/skills", sk, "SKILL.md"), "rb").read(), b)

    def ss(self, *args):
        return sh([PY, os.path.join(AD, "skills_sync.py"), *args, "--home", self.h])

    def test_apply_check_revert_roundtrip(self):
        if not os.path.exists(os.path.join(self.h, ".claude/skills/model-router/SKILL.md")):
            self.skipTest("no source skill on this machine")
        dst = os.path.join(self.h, ".agents/skills/model-router/SKILL.md")
        before = open(dst, "rb").read() if os.path.exists(dst) else None
        self.assertEqual(self.ss("check").returncode, 1)
        self.assertEqual(self.ss("apply").returncode, 0)
        new = open(dst).read()
        self.assertNotIn("Codex-usage-saver", new)
        self.assertIn("usaver-generated:", new)
        self.assertIn("For Codex.", new)
        self.assertTrue(new.startswith("---\nname: model-router"))   # frontmatter 原样在最前
        src = open(os.path.join(self.h, ".claude/skills/model-router/SKILL.md")).read()
        self.assertIn(src.split("\n---\n", 1)[1].strip()[:200].replace("~/Documents/Projects/claude-usage-saver", "~/Documents/Projects/claude-usage-saver"), new)
        self.assertEqual(self.ss("check").returncode, 0)
        self.assertNotIn("wrote", self.ss("apply").stdout)        # 已是最新：再 apply 不重复写
        with open(os.path.join(self.h, ".claude/skills/model-router/SKILL.md"), "a") as f:
            f.write("\n- new rule\n")
        self.assertIn("stale-generated", self.ss("check").stdout)
        self.ss("revert")
        self.assertEqual(open(dst, "rb").read() if os.path.exists(dst) else None, before)

    def test_render_rules(self):
        import skills_sync as S
        src = ("---\nname: x\ndescription: d\n---\n# T\nsee ~/Documents/Projects/claude-usage-saver/state/mode.json and "
               "/Users/bob/Documents/Projects/claude-usage-saver/tools\n<!-- only:claude -->\nclaude only\n<!-- /only -->\n"
               "<!-- only:codex -->\ncodex only\n<!-- /only -->\nClaude Code stays Claude Code.\n")
        out = S.render(src, "codex", "/opt/kit", "/home/me", "x", "~/.claude/skills/x/SKILL.md",
                       {"skill_notes": {"codex": {"x": "profile says hi"}}})
        self.assertIn("/opt/kit/state/mode.json", out)
        self.assertIn("/opt/kit/tools", out)
        self.assertNotIn("claude only", out)
        self.assertIn("codex only", out)
        self.assertNotIn("only:", out)
        self.assertIn("Claude Code stays Claude Code.", out)     # 不做 Claude→Codex 文字替换
        self.assertIn("profile says hi", out)
        self.assertTrue(out.startswith("---\nname: x\n"))


class Snippet(unittest.TestCase):
    def test_snippet_compatible_with_session_resume(self):
        # session_resume.py 导入时就会跑 hook 入口（没有 __main__ 保护），所以只从源码里取这三个常量
        src = open(os.path.join(REPO, "hooks", "session_resume.py"), encoding="utf-8").read()
        ns = {"re": re}
        for name in ("MARK", "RED_HEAD", "NEXT_HEAD"):
            exec(re.search(r"^%s = .*$" % name, src, re.M).group(0), ns)
        SR = type("SR", (), ns)
        for fn, limit in (("AGENTS.usage-saver.md", 4096), ("AGENTS.usage-saver.lite.md", 1024)):
            s = open(os.path.join(AD, "agents", fn)).read()
            self.assertIn(SR.MARK, s, fn)                       # 模板里有 session_resume 认的标记
            heads = re.findall(r"(?:^\s*|`)###\s+([^`\n]+)", s, re.M)
            self.assertTrue(any(SR.RED_HEAD.search(h) for h in heads), fn)
            self.assertTrue(any(SR.NEXT_HEAD.search(h) for h in heads), fn)
            self.assertLess(len(s.encode()), limit, fn)         # 每次请求都要带上，控制体积
            self.assertTrue(s.startswith("<!-- usage-saver:begin") and s.rstrip().endswith("<!-- usage-saver:end -->"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
