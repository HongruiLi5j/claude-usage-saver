#!/usr/bin/env python3
"""固定回归评分表：回答「当前版本比初版提升了百分之多少」。

用法：
  python3 tools/scorecard.py                 # 等于 --version all：初版和当前版都跑并对比
  python3 tools/scorecard.py --version v0
  python3 tools/scorecard.py --version current
  python3 tools/scorecard.py --only TF05     # 只跑某几个场景（逗号分隔），不写报告，调试用

场景来源：reports/round1_findings.md（A1–A9、C1–C9）与 reports/round2_review_AC.md（复现 + N1–N10），
另加若干「本来就该通过」的正常场景（来源标为「正常」）。每个场景写的是**预期的正确行为**。

隔离：每个场景一个 tempfile 临时目录（自动清理）：假 HOME、USAGE_SAVER_STATE 指向临时目录；
写死路径的脚本（statusline.sh 的 S、test_filter.py 的 LOGDIR、weekly_compare.py 的 now）复制到临时目录后替换再跑。
被测脚本只以副本运行，不改 hooks/ 与 tools/ 里的原文件；不读不写真实的 ~/.claude、state/、testlogs/。

输出：reports/scorecard.md（只在 --version all 时写）；reports/scorecard_history.json（每个版本每天一条，同一天重跑覆盖）。
「不适用」= 该场景测的功能在该版本里根本不存在（例如 v0 没有额度分档、没有对话摘要），不计入分母。
"""
import argparse, datetime as dt, json, os, re, shutil, stat, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_PATH = "~/Documents/Projects/claude-usage-saver"

VERSIONS = {
    "v0": {
        "test_filter": "backups/test_filter.py.pre-hotfix",
        "session_resume": "backups/session_resume.py.pre-homefix",
        "context_guard": "backups/context_guard.py.pre-tiers",
        "statusline": "backups/statusline.sh.pre-clearhint",
        "weekly_compare": "backups/weekly_compare.py.pre-cw",
        "config": "backups/config.json.pre-perproject",
    },
    "current": {
        "test_filter": "hooks/test_filter.py",
        "session_resume": "hooks/session_resume.py",
        "context_guard": "hooks/context_guard.py",
        "statusline": "hooks/statusline.sh",
        "weekly_compare": "tools/weekly_compare.py",
        "config": "config.json",
    },
}
LABEL = {"v0": "初版 v0", "current": "当前版"}
GROUPS = ["test_filter", "session_resume", "context_guard", "statusline", "weekly_compare"]
WC_NOW = "dt.datetime(2026, 11, 8, 20, 0, tzinfo=UTC)"   # weekly_compare 固定「现在」，保证结果不随运行日期变化
TIMEOUT = 15


class NA(Exception):
    """场景在该版本不适用。"""


# ====================================================================== 环境
class Env:
    def __init__(self, version, tmp):
        self.version, self.tmp = version, tmp
        self.home = os.path.join(tmp, "home")
        self.projects = os.path.join(self.home, "Documents", "Projects")
        self.suite = os.path.join(self.projects, "claude-usage-saver")     # 假 HOME 下的套件目录
        self.state = os.path.join(tmp, "state")
        self.bin = os.path.join(tmp, "bin")
        self.work = os.path.join(tmp, "work")
        for d in (self.home, self.projects, self.suite, self.state, self.bin, self.work,
                  os.path.join(self.suite, "hooks"), os.path.join(self.suite, "tools")):
            os.makedirs(d, exist_ok=True)
        self.files = VERSIONS[version]
        shutil.copy(os.path.join(ROOT, self.files["config"]), os.path.join(self.suite, "config.json"))
        self._write_fake_pytest()

    # ---- 被测脚本副本
    def script(self, comp, patches=()):
        src = os.path.join(ROOT, self.files[comp])
        txt = open(src, encoding="utf-8").read()
        for old, new in patches:
            if old not in txt:
                raise RuntimeError(f"{comp}: 替换目标不存在：{old!r}")
            txt = txt.replace(old, new)
        sub = "tools" if comp == "weekly_compare" else "hooks"
        name = os.path.basename(src).split(".pre-")[0]
        dst = os.path.join(self.suite, sub, name)
        open(dst, "w", encoding="utf-8").write(txt)
        os.chmod(dst, 0o755)
        return dst

    def environ(self, **extra):
        e = {"HOME": self.home, "USAGE_SAVER_STATE": self.state, "TZ": "America/Los_Angeles",
             "PATH": self.bin + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
             "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}
        e.update(extra)
        return e

    def run(self, argv, stdin="", cwd=None, **extra):
        p = subprocess.run(argv, input=stdin, capture_output=True, text=True, cwd=cwd or self.work,
                           env=self.environ(**extra), timeout=TIMEOUT)
        return p.returncode, p.stdout, p.stderr

    def _write_fake_pytest(self):
        fake = os.path.join(self.bin, "pytest")
        open(fake, "w").write(r'''#!/bin/bash
if [ "${FAKE_PYTEST_MODE:-basic}" = tb ]; then
  for i in $(seq 1 30); do echo "tests/test_x.py::test_ok_$i PASSED"; done
  echo "Traceback (most recent call last):"
  for i in 1 2 3 4 5; do echo "  File \"/src/mod.py\", line 1$i, in frame_$i"; echo "    call_$i()"; done
  echo "KeyError: 'k'"
  for i in $(seq 1 15); do echo "captured log line $i"; done
  echo "1 failed, 30 passed in 0.10s"; exit 1
fi
for i in $(seq 1 40); do echo "tests/test_x.py::test_ok_$i PASSED"; done
echo "FAILED tests/test_x.py::test_bad - AssertionError: assert 1 == 2"
echo "1 failed, 40 passed in 0.12s"
exit 1
''')
        os.chmod(fake, 0o755)


def touch(path, text="", age_h=0.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(text)
    t = time.time() - age_h * 3600
    os.utime(path, (t, t))
    return path


def jl(path, rows, age_h=0.0):
    return touch(path, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", age_h)


def iso(sec_ago=0):
    return dt.datetime.fromtimestamp(time.time() - sec_ago, dt.timezone.utc).isoformat().replace("+00:00", "Z")


# ====================================================================== 场景注册
SCEN = []


def sc(sid, group, source, desc, na=None):
    def deco(fn):
        SCEN.append(dict(id=sid, group=group, source=source, desc=desc, na=na or {}, fn=fn))
        return fn
    return deco


# ---------------------------------------------------------------- test_filter
def tf_hook(E, cmd):
    """跑 test_filter 副本；返回包装后的命令，没包装返回 None。"""
    s = E.script("test_filter", [(f'"{REAL_PATH}/state/testlogs"', json.dumps(os.path.join(E.state, "testlogs")))])
    rc, out, _ = E.run([sys.executable, s], json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd, "description": "t"}}))
    if not out.strip():
        return None
    return json.loads(out)["hookSpecificOutput"]["updatedInput"]["command"]


def tf_exec(E, cmd, mode="basic"):
    """先过 hook，再用 bash 执行最终命令；返回 (是否被包装, 输出, rc)。"""
    w = tf_hook(E, cmd)
    rc, out, err = E.run(["/bin/bash", "-c", w or cmd], FAKE_PYTEST_MODE=mode)
    return w is not None, out + err, rc


@sc("TF01", "test_filter", "正常", "普通 pytest 失败：被过滤（行数变少、FAILED 行可见），完整输出存到日志目录")
def _(E):
    wrapped, out, rc = tf_exec(E, "pytest -q")
    logs = os.listdir(os.path.join(E.state, "testlogs")) if os.path.isdir(os.path.join(E.state, "testlogs")) else []
    full = open(os.path.join(E.state, "testlogs", logs[0])).read().count("\n") if logs else 0
    return wrapped and "FAILED tests/test_x.py::test_bad" in out and out.count("\n") < 42 and full >= 42


@sc("TF02", "test_filter", "正常", "过滤后退出码保留（pytest 失败 rc=1）")
def _(E):
    _, out, rc = tf_exec(E, "pytest -q")
    return rc == 1


@sc("TF03", "test_filter", "正常", "命令带 USAGE_SAVER_RAW=1 时不包装")
def _(E):
    return tf_hook(E, "USAGE_SAVER_RAW=1 pytest -q") is None


@sc("TF04", "test_filter", "正常", "非测试命令（ls -la）不包装")
def _(E):
    return tf_hook(E, "ls -la") is None


@sc("TF05", "test_filter", "A2", "`pytest -q  # note` 带行尾注释：测试真的跑了，FAILED 可见，rc=1")
def _(E):
    _, out, rc = tf_exec(E, "pytest -q  # note")
    return rc == 1 and "FAILED" in out


@sc("TF06", "test_filter", "A2", "`pytest -q &` 后台命令不包装")
def _(E):
    return tf_hook(E, "pytest -q &") is None


@sc("TF07", "test_filter", "A3", "`grep pytest f.txt`（50 行匹配）：50 行全部可见")
def _(E):
    touch(os.path.join(E.work, "f.txt"), "".join(f"pytest line {i}\n" for i in range(50)))
    _, out, rc = tf_exec(E, "grep pytest f.txt")
    return all(f"pytest line {i}\n" in out for i in range(50))


@sc("TF08", "test_filter", "A3 / C6", "`pip install pytest` 不包装")
def _(E):
    return tf_hook(E, "pip install pytest") is None


@sc("TF09", "test_filter", "A3 / A6", "`pytest; echo done`（pytest 紧跟分号）应被识别为测试并过滤")
def _(E):
    return tf_hook(E, "pytest; echo done") is not None


@sc("TF10", "test_filter", "A6", "`.venv/bin/pytest -q` 应被识别为测试并过滤")
def _(E):
    return tf_hook(E, ".venv/bin/pytest -q") is not None


@sc("TF11", "test_filter", "A6", "`pytest -q; exit 3`：仍能看到失败信息，rc=3")
def _(E):
    _, out, rc = tf_exec(E, "pytest -q; exit 3")
    return rc == 3 and "FAILED" in out


@sc("TF12", "test_filter", "N6", "`set -e; pytest -q; echo after`：仍能看到失败信息，rc=1")
def _(E):
    _, out, rc = tf_exec(E, "set -e; pytest -q; echo after")
    return rc == 1 and "FAILED" in out


@sc("TF13", "test_filter", "C6", "5 帧的 traceback：所有帧在过滤后的输出里都可见")
def _(E):
    _, out, rc = tf_exec(E, "pytest -q", mode="tb")
    return all(f"in frame_{i}" in out for i in range(1, 6))


@sc("TF14", "test_filter", "N7 / C6", "`cat notes.txt && pytest -q`：非测试部分（30 行）的输出不被吃掉")
def _(E):
    touch(os.path.join(E.work, "notes.txt"), "".join(f"NOTE-LINE-{i}\n" for i in range(30)))
    _, out, rc = tf_exec(E, "cat notes.txt && pytest -q")
    return all(f"NOTE-LINE-{i}\n" in out for i in range(30))


@sc("TF15", "test_filter", "A3", "`which pytest` 不包装")
def _(E):
    return tf_hook(E, "which pytest") is None


# ---------------------------------------------------------------- session_resume
def sr(E, cwd, source="clear", tp=None, event="SessionStart", **kw):
    s = E.script("session_resume")
    data = {"hook_event_name": event, "source": source, "cwd": cwd, "session_id": kw.get("sid", "cur"),
            "transcript_path": tp or os.path.join(E.home, ".claude", "projects", "-none", "cur.jsonl")}
    data.update(kw.get("extra", {}))
    rc, out, _ = E.run([sys.executable, s], json.dumps(data))
    if not out.strip():
        return ""
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def proj(E, name, git=True):
    d = os.path.join(E.projects, name)
    os.makedirs(os.path.join(d, ".git") if git else d, exist_ok=True)
    return d


def tdir(E):
    d = os.path.join(E.home, ".claude", "projects", "-Users-x-a")
    os.makedirs(d, exist_ok=True)
    return d


def old_transcript(E, path, title, users=(), replies=(), age_h=5 / 60, extra_rows=()):
    rows = [{"type": "ai-title", "aiTitle": title}]
    for u in users:
        rows.append({"type": "user", "timestamp": iso(900), "message": {"role": "user", "content": u}})
    for r in replies:
        rows.append({"type": "assistant", "timestamp": iso(800),
                     "message": {"role": "assistant", "content": [{"type": "text", "text": r}]}})
    rows.extend(extra_rows)
    return jl(path, rows, age_h)


@sc("SR01", "session_resume", "正常", "非 clear 启动（startup）不注入")
def _(E):
    a = proj(E, "a"); touch(os.path.join(a, "HANDOFF.md"), "HANDOFF-A-MARK", 0.2)
    return sr(E, a, source="startup") == ""


@sc("SR02", "session_resume", "正常", "在有 .git 的项目根 /clear：注入本项目新鲜的 HANDOFF.md")
def _(E):
    a = proj(E, "a"); touch(os.path.join(a, "HANDOFF.md"), "HANDOFF-A-MARK", 0.2)
    return "HANDOFF-A-MARK" in sr(E, a)


@sc("SR03", "session_resume", "正常", "20KB 交接 + 大段旧对话：注入总长 ≤ 10000 字符（官方上限）")
def _(E):
    a = proj(E, "a"); touch(os.path.join(a, "HANDOFF.md"), "交接内容。" * 4000, 0.2)
    d = tdir(E)
    old_transcript(E, os.path.join(d, "old.jsonl"), "T", users=["问题" * 300] * 10, replies=["回复" * 1500] * 10)
    ctx = sr(E, a, tp=os.path.join(d, "cur.jsonl"))
    return 0 < len(ctx) <= 10000


@sc("SR04", "session_resume", "A4", "cwd=Projects/c/pkg（c 有 .git、无交接）：不越过项目根去拿 Projects/HANDOFF.md")
def _(E):
    c = proj(E, "c"); os.makedirs(os.path.join(c, "pkg"))
    touch(os.path.join(E.projects, "HANDOFF.md"), "PROJECTS-LEVEL-MARK", 866)
    return "PROJECTS-LEVEL-MARK" not in sr(E, os.path.join(c, "pkg"))


@sc("SR05", "session_resume", "A4", "cwd=~/Documents/x：不往上爬拿 ~/HANDOFF.md 当「当前项目」（~ 单独算一个项目）")
def _(E):
    x = os.path.join(E.home, "Documents", "x"); os.makedirs(x)
    touch(os.path.join(E.home, "HANDOFF.md"), "HOME-MARK", 5)
    return "HOME-MARK" not in sr(E, x)


@sc("SR06", "session_resume", "A4", "cwd=~ 且 ~/HANDOFF.md 是 10 分钟前写的：注入它，不改拿别的项目的交接")
def _(E):
    touch(os.path.join(E.home, "HANDOFF.md"), "HOME-FRESH-MARK", 10 / 60)
    b = proj(E, "b"); touch(os.path.join(b, "HANDOFF.md"), "B-MARK", 1)
    ctx = sr(E, E.home)
    return "HOME-FRESH-MARK" in ctx and "B-MARK" not in ctx


@sc("SR07", "session_resume", "A4", "cwd=~/Documents/Projects，Projects/HANDOFF.md 已 866 小时：不注入正文")
def _(E):
    touch(os.path.join(E.projects, "HANDOFF.md"), "PROJ-OLD-BODY", 866)
    return "PROJ-OLD-BODY" not in sr(E, E.projects)


@sc("SR08", "session_resume", "A4 / C2", "项目内交接已 746 小时（31 天）：不整段注入正文（最多给路径和年龄）")
def _(E):
    a = proj(E, "a"); touch(os.path.join(a, "HANDOFF.md"), "STALE-BODY-MARK", 746)
    return "STALE-BODY-MARK" not in sr(E, a)


@sc("SR09", "session_resume", "C2", "注入文字不劫持新问题：不写「无论写什么都视为接着做」，并说明明显是新问题时按新问题处理")
def _(E):
    a = proj(E, "a"); touch(os.path.join(a, "HANDOFF.md"), "HANDOFF-A-MARK", 0.2)
    ctx = sr(E, a)
    return bool(ctx) and "无论写什么" not in ctx and "新问题" in ctx


@sc("SR10", "session_resume", "C3", "9KB 交接、红线在最后一节：注入后「红线」一节仍在")
def _(E):
    a = proj(E, "a")
    touch(os.path.join(a, "HANDOFF.md"), "# 交接\n" + "进度说明。" * 1800 + "\n## 红线\n不要 push RED-LINE-MARK\n", 0.2)
    return "RED-LINE-MARK" in sr(E, a)


@sc("SR11", "session_resume", "N2", "没有 SessionEnd 指针、同目录另有一个 5 秒前还在写的并发会话：不把并发会话当成上一段对话",
    na={"v0": "v0 没有对话摘要功能"})
def _(E):
    a = proj(E, "a"); d = tdir(E)
    old_transcript(E, os.path.join(d, "old.jsonl"), "TITLE-OLD", users=["旧问题"], replies=["旧回复"], age_h=5 / 60)
    old_transcript(E, os.path.join(d, "other.jsonl"), "TITLE-OTHER", users=["别的会话"], replies=["别的回复"], age_h=5 / 3600)
    return "TITLE-OTHER" not in sr(E, a, tp=os.path.join(d, "cur.jsonl"))


@sc("SR12", "session_resume", "正常（N2）", "有 SessionEnd 指针时：摘要取指针指向的上一段对话",
    na={"v0": "v0 没有对话摘要/SessionEnd 功能"})
def _(E):
    a = proj(E, "a"); d = tdir(E)
    old = old_transcript(E, os.path.join(d, "old.jsonl"), "TITLE-OLD", users=["旧问题"], replies=["旧回复"], age_h=5 / 60)
    old_transcript(E, os.path.join(d, "other.jsonl"), "TITLE-OTHER", users=["别的会话"], replies=["别的回复"], age_h=5 / 3600)
    sr(E, a, event="SessionEnd", tp=old, sid="old", extra={"reason": "clear"})
    ctx = sr(E, a, tp=os.path.join(d, "cur.jsonl"))
    return "TITLE-OLD" in ctx and "TITLE-OTHER" not in ctx


@sc("SR13", "session_resume", "N4", "上一段 Claude 最后一段长回复（3000 字）结尾的「下一步」保留在摘要里",
    na={"v0": "v0 没有对话摘要功能"})
def _(E):
    a = proj(E, "a"); d = tdir(E)
    old = old_transcript(E, os.path.join(d, "old.jsonl"), "T", users=["USER-ASK-MARK"],
                         replies=["分析过程。" * 600 + "\n下一步：END-NEXT-STEPS"])
    sr(E, a, event="SessionEnd", tp=old, sid="old", extra={"reason": "clear"})
    return "END-NEXT-STEPS" in sr(E, a, tp=os.path.join(d, "cur.jsonl"))


@sc("SR14", "session_resume", "N4", "compact summary（isCompactSummary）不被当成「所有者最近的消息」",
    na={"v0": "v0 没有对话摘要功能"})
def _(E):
    a = proj(E, "a"); d = tdir(E)
    comp = {"type": "user", "isCompactSummary": True, "timestamp": iso(700),
            "message": {"role": "user", "content": "This session is being continued from a previous conversation. COMPACT-MARK"}}
    old = old_transcript(E, os.path.join(d, "old.jsonl"), "T", users=["真正的所有者消息"], replies=["回复"], extra_rows=[comp])
    sr(E, a, event="SessionEnd", tp=old, sid="old", extra={"reason": "clear"})
    return "COMPACT-MARK" not in sr(E, a, tp=os.path.join(d, "cur.jsonl"))


# ---------------------------------------------------------------- context_guard
def cg(E, ctx_tokens, cwd=None, rate=None, big_tail=False):
    s = E.script("context_guard")
    if rate is not None:
        open(os.path.join(E.state, "rate.json"), "w").write(json.dumps(rate))
    rows = [{"type": "user", "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}],
             "usage": {"input_tokens": 1000, "cache_read_input_tokens": ctx_tokens - 1000, "cache_creation_input_tokens": 0}}}]
    if big_tail:
        rows.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "y" * 500_000}]}})
    tp = jl(os.path.join(E.tmp, "t.jsonl"), rows)
    cwd = cwd or proj(E, "a")
    rc, out, _ = E.run([sys.executable, s], json.dumps({"hook_event_name": "UserPromptSubmit", "transcript_path": tp,
                                                        "cwd": cwd, "prompt": "hi"}))
    if rc != 0:
        return None
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""


def rate(used, resets_in_h, ts_ago_h=0.0, five=10):
    now = time.time()
    return {"ts": int(now - ts_ago_h * 3600), "ctx_tokens": 0, "rate_limits": {
        "five_hour": {"used_percentage": five, "resets_at": int(now + 3600)},
        "seven_day": {"used_percentage": used, "resets_at": int(now + resets_in_h * 3600)}}}


@sc("CG01", "context_guard", "正常", "上下文 170k（普通项目、无额度数据）：注入软提醒")
def _(E):
    return "[上下文守卫]" in (cg(E, 170_000) or "")


@sc("CG02", "context_guard", "正常", "上下文 50k：不提醒")
def _(E):
    return cg(E, 50_000) == ""


@sc("CG03", "context_guard", "正常", "上下文 320k：注入强提醒（「上下文已很长」）")
def _(E):
    return "上下文已很长" in (cg(E, 320_000) or "")


@sc("CG04", "context_guard", "正常", "quant-backtest 项目 200k：按项目软线 250k，不提醒",
    na={"v0": "v0 的 config.json（pre-perproject）没有按项目阈值"})
def _(E):
    return cg(E, 200_000, cwd=proj(E, "quant-backtest")) == ""


@sc("CG05", "context_guard", "正常", "rate.json 已 7 小时没更新：按正常档，200k 提醒")
def _(E):
    return "[上下文守卫]" in (cg(E, 200_000, rate=rate(20, 24, ts_ago_h=7)) or "")


@sc("CG06", "context_guard", "A1 / N1", "rate.json 是新的但周重置时间已过去 2 天（用量 60%）：不能判成宽裕，200k 应提醒")
def _(E):
    return "[上下文守卫]" in (cg(E, 200_000, rate=rate(60, -48)) or "")


@sc("CG07", "context_guard", "正常（分档）", "周用量 20%、时间进度约 86%（宽裕）：软线 ×1.5=240k，200k 不提醒",
    na={"v0": "v0 没有额度分档功能"})
def _(E):
    return cg(E, 200_000, rate=rate(20, 24)) == ""


@sc("CG08", "context_guard", "A9 / C5", "最后一行是 500KB 的 tool_result：仍能找到上一个 assistant 的 usage 并提醒（170k）")
def _(E):
    return "[上下文守卫]" in (cg(E, 170_000, big_tail=True) or "")


@sc("CG09", "context_guard", "C8", "强提醒不要求「在本次回答中」说明（避免打断正在做的工作）")
def _(E):
    msg = cg(E, 320_000) or ""
    return "上下文已很长" in msg and "本次回答中" not in msg


# ---------------------------------------------------------------- statusline
def sl(E, n, cwd=None, rl="default", pre_rate=None, mode=None):
    s = E.script("statusline", [(f'"{REAL_PATH}/state"', json.dumps(E.state))])
    if pre_rate is not None:
        open(os.path.join(E.state, "rate.json"), "w").write(json.dumps(pre_rate))
    if mode is not None:
        open(os.path.join(E.state, "mode.json"), "w").write(json.dumps(mode))
    cwd = cwd or proj(E, "a")
    payload = {"session_id": "s1", "cwd": cwd, "workspace": {"current_dir": cwd, "project_dir": cwd},
               "model": {"id": "claude-opus", "display_name": "Opus"},
               "context_window": {"current_usage": {"input_tokens": 1000, "cache_read_input_tokens": n - 1000,
                                                    "cache_creation_input_tokens": 0}}}
    if rl == "default":
        rl = {"five_hour": {"used_percentage": 40, "resets_at": int(time.time() + 3600)},
              "seven_day": {"used_percentage": 50, "resets_at": int(time.time() + 86400)}}
    if rl is not None:
        payload["rate_limits"] = rl
    rc, out, _ = E.run(["/bin/sh", s], json.dumps(payload))
    try:
        r = json.load(open(os.path.join(E.state, "rate.json")))
    except Exception:
        r = {}
    return out, r


@sc("SL01", "statusline", "正常", "显示模型、ctx 123k、5h 40%、7d 50%，并把 ctx_tokens 写入 rate.json")
def _(E):
    out, r = sl(E, 123_000)
    return "ctx 123k" in out and "5h 40%" in out and "7d 50%" in out and r.get("ctx_tokens") == 123_000


@sc("SL02", "statusline", "A1", "本会话 payload 没有 rate_limits：不能把 rate.json 里别的会话写的有效周用量冲掉",
    na={"v0": "v0 没有读取 rate.json 的组件（无额度分档），覆盖无后果"})
def _(E):
    pre = rate(84, 24)
    out, r = sl(E, 50_000, rl=None, pre_rate=pre)
    return ((r.get("rate_limits") or {}).get("seven_day") or {}).get("used_percentage") == 84


@sc("SL03", "statusline", "A1", "同一 resets_at 下旧会话报 16%、rate.json 已有 84%：保留较大的 84%",
    na={"v0": "v0 没有读取 rate.json 的组件（无额度分档），覆盖无后果"})
def _(E):
    pre = rate(84, 24)
    res = pre["rate_limits"]["seven_day"]["resets_at"]
    rl = {"five_hour": {"used_percentage": 5, "resets_at": int(time.time() + 3600)},
          "seven_day": {"used_percentage": 16, "resets_at": res}}
    out, r = sl(E, 50_000, rl=rl, pre_rate=pre)
    return ((r.get("rate_limits") or {}).get("seven_day") or {}).get("used_percentage") == 84


@sc("SL04", "statusline", "正常（⚠ 提示）", "普通项目 170k、软线 160k：状态栏显示 ⚠",
    na={"v0": "v0 状态栏没有 ⚠ 提示功能"})
def _(E):
    out, _ = sl(E, 170_000, mode={"ts": int(time.time()), "mode": "正常", "soft_line": 160_000, "strong_line": 300_000})
    return "⚠" in out


@sc("SL05", "statusline", "A5 / C4", "mode.json 是 ~ 会话写的（软线 160k），本会话在 quant-backtest（软线 250k）、200k：不显示 ⚠",
    na={"v0": "v0 状态栏没有 ⚠ 提示功能"})
def _(E):
    out, _ = sl(E, 200_000, cwd=proj(E, "quant-backtest"),
                mode={"ts": int(time.time()), "mode": "正常", "soft_line": 160_000, "strong_line": 300_000})
    return "⚠" not in out


# ---------------------------------------------------------------- weekly_compare
def msg(mid, ts, model="claude-opus-5", usage=None, side=False):
    u = usage or {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 10000,
                  "cache_creation_input_tokens": 1000}
    return {"type": "assistant", "timestamp": ts, "isSidechain": side,
            "message": {"id": mid, "model": model, "role": "assistant", "content": [], "usage": u}}


def wc(E, projects_rows, runs=1):
    """projects_rows = {项目目录名: [行]}。返回 (最后一条 history, 报告 md, 全部 history)。"""
    for pname, rows in projects_rows.items():
        jl(os.path.join(E.home, ".claude", "projects", pname, "s.jsonl"), rows)
    s = E.script("weekly_compare", [("dt.datetime.now(UTC)", WC_NOW)])
    for _ in range(runs):
        E.run([sys.executable, s])
    rep = os.path.join(E.suite, "reports")
    h = json.load(open(os.path.join(rep, "weekly_history.json")))
    md = "".join(open(os.path.join(rep, f)).read() for f in os.listdir(rep) if f.startswith("weekly_") and f.endswith(".md"))
    return h[-1], md, h


def calls(stat_):
    return round(stat_["calls_per_day"] * max(stat_["days"], 1))


@sc("WC01", "weekly_compare", "正常", "排除 -private-tmp- 基准会话：改动前只算 1 次请求")
def _(E):
    last, _, _ = wc(E, {"-Users-x-proj": [msg("m1", "2026-10-25T20:00:00Z")],
                        "-private-tmp-bench": [msg("m2", "2026-10-25T20:00:00Z")]})
    return calls(last["before"]) == 1


@sc("WC02", "weekly_compare", "正常", "无 5m/1h 拆分的旧记录：缓存写入按 1.25×，每次请求成本 = 3.75k 单位")
def _(E):
    last, _, _ = wc(E, {"-Users-x-proj": [msg("m1", "2026-10-25T20:00:00Z")]})
    return abs(last["before"]["cost_per_call"] - 3.75) < 1e-6


@sc("WC03", "weekly_compare", "A8", "1 小时缓存写入按 2×：每次请求成本 = 4.5k 单位")
def _(E):
    u = {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 10000, "cache_creation_input_tokens": 1000,
         "cache_creation": {"ephemeral_1h_input_tokens": 1000, "ephemeral_5m_input_tokens": 0}}
    last, _, _ = wc(E, {"-Users-x-proj": [msg("m1", "2026-10-25T20:00:00Z", usage=u)]})
    return abs(last["before"]["cost_per_call"] - 4.5) < 1e-6


@sc("WC04", "weekly_compare", "A8", "「最近」窗口从改动时刻（11-05 23:58 PST）开始：11-05 21:00 PST 的请求不算进最近")
def _(E):
    last, _, _ = wc(E, {"-Users-x-proj": [msg("m1", "2026-11-06T05:00:00Z"), msg("m2", "2026-11-08T10:00:00Z")]})
    return calls(last["recent"]) == 1


@sc("WC05", "weekly_compare", "N10", "同一天运行两次：weekly_history.json 里当天只有 1 条")
def _(E):
    _, _, h = wc(E, {"-Users-x-proj": [msg("m1", "2026-10-25T20:00:00Z")]}, runs=2)
    return sum(1 for x in h if str(x.get("ts", "")).startswith("2026-11-08")) == 1


@sc("WC06", "weekly_compare", "N10 / B", "报告按模型分开给每次请求成本（含 Sonnet 一行）")
def _(E):
    _, md, _ = wc(E, {"-Users-x-proj": [msg("m1", "2026-10-25T20:00:00Z"),
                                        msg("m2", "2026-10-25T21:00:00Z", model="claude-sonnet-5")]})
    return re.search(r"^\|[^\n]*sonnet[^\n]*\|\s*\d", md, re.I | re.M) is not None


@sc("WC07", "weekly_compare", "N10", "按本地日期分天：10-25 16:00 与 18:00 PDT（跨 UTC 零点）算 1 天")
def _(E):
    last, _, _ = wc(E, {"-Users-x-proj": [msg("m1", "2026-10-25T23:00:00Z"), msg("m2", "2026-10-26T01:00:00Z")]})
    return last["before"]["days"] == 1


# ====================================================================== 运行与报告
def run_one(s, version):
    if version in s["na"]:
        return "na", s["na"][version], 0.0
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="scorecard-") as tmp:
        E = Env(version, os.path.realpath(tmp))
        try:
            ok = bool(s["fn"](E))
            return ("pass" if ok else "fail"), "", time.time() - t0
        except NA as e:
            return "na", str(e), time.time() - t0
        except Exception as e:
            return "fail", f"出错：{type(e).__name__}: {e}"[:200], time.time() - t0


def tally(res, version, ids=None):
    out = {}
    for g in GROUPS + ["总计"]:
        rows = [r for r in res if (g == "总计" or r["group"] == g) and (ids is None or r["id"] in ids)]
        app = [r for r in rows if r[version][0] != "na"]
        out[g] = (sum(r[version][0] == "pass" for r in app), len(app), len(rows) - len(app))
    return out


def pct(p, n):
    return 100.0 * p / n if n else float("nan")


def fmt_rate(p, n):
    return f"{p}/{n}（{pct(p, n):.0f}%）" if n else "—"


def mark(v):
    return {"pass": "✓", "fail": "✗", "na": "不适用"}[v[0]]


def write_history(res, versions):
    path = os.path.join(ROOT, "reports", "scorecard_history.json")
    try:
        h = json.load(open(path))
    except Exception:
        h = []
    now = dt.datetime.now().astimezone()
    for v in versions:
        t = tally(res, v)["总计"]
        h = [x for x in h if not (x.get("date") == now.strftime("%Y-%m-%d") and x.get("version") == v)]
        h.append({"date": now.strftime("%Y-%m-%d"), "time": now.isoformat(timespec="seconds"), "version": v,
                  "passed": t[0], "total": t[1], "na": t[2],
                  "failed_ids": [r["id"] for r in res if r[v][0] == "fail"]})
    h.sort(key=lambda x: (x["date"], x["version"]))
    json.dump(h, open(path, "w"), ensure_ascii=False, indent=1)
    return path


def write_report(res, elapsed):
    T0, T1 = tally(res, "v0"), tally(res, "current")
    both = {r["id"] for r in res if r["v0"][0] != "na" and r["current"][0] != "na"}
    B0, B1 = tally(res, "v0", both), tally(res, "current", both)
    now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    L = [f"# 回归评分表（{now}）", "",
         "由 `python3 tools/scorecard.py --version all` 生成，不要手改。场景定义在 `tools/scorecard.py`，"
         "来源是 `reports/round1_findings.md` 与 `reports/round2_review_AC.md`；「正常」= 本来就该通过的基本功能场景。", "",
         "- 初版 v0：backups/ 里最早的各文件（test_filter.py.pre-hotfix、session_resume.py.pre-homefix、context_guard.py.pre-tiers、"
         "statusline.sh.pre-clearhint、weekly_compare.py.pre-cw、config.json.pre-perproject）",
         "- 当前版：hooks/ 与 tools/ 里的文件、config.json", "",
         "## 汇总", "",
         "| 组件 | v0 通过/适用 | 当前 通过/适用 | 提升（百分点） | 两版都适用的场景：v0 → 当前 |", "|---|---|---|---|---|"]
    for g in GROUPS + ["总计"]:
        a, b = T0[g], T1[g]
        d = pct(b[0], b[1]) - pct(a[0], a[1]) if a[1] and b[1] else float("nan")
        ds = f"{d:+.0f}" if d == d else "—"
        both_s = f"{B0[g][0]}/{B0[g][1]} → {B1[g][0]}/{B1[g][1]}" if B0[g][1] else "—"
        name = f"**{g}**" if g == "总计" else g
        L.append(f"| {name} | {fmt_rate(a[0], a[1])} | {fmt_rate(b[0], b[1])} | {ds} | {both_s} |")
    r0, r1 = pct(T0["总计"][0], T0["总计"][1]), pct(T1["总计"][0], T1["总计"][1])
    rb0, rb1 = pct(B0["总计"][0], B0["总计"][1]), pct(B1["总计"][0], B1["总计"][1])
    L += ["", f"**一句话**：通过率 v0 {r0:.0f}% → 当前 {r1:.0f}%，提升 {r1 - r0:+.0f} 个百分点"
          + (f"（相对提升 {100 * (r1 / r0 - 1):+.0f}%）" if r0 else "") + "。"
          f"只看两版都适用的 {B0['总计'][1]} 个场景：{rb0:.0f}% → {rb1:.0f}%（{rb1 - rb0:+.0f} 个百分点）。", "",
          "读法：", "- 「不适用」= 该场景测的功能在该版本里根本不存在（v0 没有额度分档、⚠ 提示、对话摘要、按项目阈值），"
          "不计入该版本的分母，所以两版的分母不同；「两版都适用」一栏是同一批场景上的对比，更公平。",
          "- 场景写的是**预期的正确行为**，当前版的 ✗ 就是还没修的问题；v0 ✓ 当前 ✗ 的是回归。",
          "- 这里只测**功能正确性**，不测省了多少额度（那个看 weekly_compare，且要 ≥3 个完整天）。", "",
          "## 逐个场景", "", "| id | 组件 | 来源 | 预期行为 | v0 | 当前 |", "|---|---|---|---|---|---|"]
    for r in res:
        L.append(f"| {r['id']} | {r['group']} | {r['source']} | {r['desc'].replace('|', '／')} | {mark(r['v0'])} | {mark(r['current'])} |")
    fails = [r for r in res if r["current"][0] == "fail"]
    regress = [r for r in res if r["v0"][0] == "pass" and r["current"][0] == "fail"]
    L += ["", f"## 当前仍失败（{len(fails)} 个，第 3 轮待修清单）", ""]
    L += [f"- **{r['id']}**（{r['source']}）{r['desc']}" + ("　**← 回归：v0 通过**" if r in regress else "")
          + (f"　〔{r['current'][1]}〕" if r["current"][1] else "") for r in fails] or ["（无）"]
    na_rows = [r for r in res if r["v0"][0] == "na" or r["current"][0] == "na"]
    L += ["", "## 不适用说明", ""]
    L += [f"- {r['id']}：" + "；".join(f"{LABEL[v]} — {r[v][1]}" for v in ("v0", "current") if r[v][0] == "na") for r in na_rows] or ["（无）"]
    L += ["", "## 隔离方式", "",
          "每个场景一个临时目录（tempfile，结束自动删除）：假 HOME、`USAGE_SAVER_STATE` 指向临时目录、TZ=America/Los_Angeles；"
          "被测脚本复制到假 HOME 下再跑，statusline.sh 的 S、test_filter.py 的 LOGDIR 替换成临时目录，"
          "weekly_compare.py 的「现在」固定为 2026-11-08 20:00 UTC（结果不随运行日期变化）。"
          "测试命令用假的 pytest（临时 bin 目录，放在 PATH 最前），用 /bin/bash 执行。不碰真实的 ~/.claude、state/、testlogs/。",
          "", f"总耗时 {elapsed:.1f} 秒。"]
    path = os.path.join(ROOT, "reports", "scorecard.md")
    open(path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", choices=["v0", "current", "all"], default="all")
    ap.add_argument("--only", help="只跑这些场景 id（逗号分隔），不写报告/历史")
    a = ap.parse_args()
    versions = ["v0", "current"] if a.version == "all" else [a.version]
    scen = [s for s in SCEN if not a.only or s["id"] in a.only.split(",")]
    t0 = time.time()
    res = []
    for s in scen:
        r = {k: s[k] for k in ("id", "group", "source", "desc")}
        for v in ["v0", "current"]:
            r[v] = run_one(s, v) if v in versions else ("na", "未运行", 0.0)
        res.append(r)
        print(f"{s['id']:5} {s['group']:15} " + "  ".join(f"{v}:{mark(r[v]):3}" for v in versions)
              + "".join(f"  [{v}] {r[v][1]}" for v in versions if r[v][1] and r[v][0] == "fail"))
    elapsed = time.time() - t0
    for v in versions:
        t = tally(res, v)["总计"]
        print(f"{LABEL[v]}：{t[0]}/{t[1]} 通过（{pct(t[0], t[1]):.0f}%），不适用 {t[2]}")
    print(f"耗时 {elapsed:.1f} 秒")
    if a.only:
        return
    print("历史：", write_history(res, versions))
    if a.version == "all":
        print("报告：", write_report(res, elapsed))


if __name__ == "__main__":
    main()
