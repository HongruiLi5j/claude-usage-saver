#!/usr/bin/env python3
"""Independent black-box scenarios. Implementers should not inspect this suite.

Only public README/user guide/config and leading hook comments informed this file.
No hook is imported. Every scenario invokes a subprocess in its own temporary HOME.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
try:
    CONFIG = json.loads((ROOT / "config.json").read_text())
    for _key in ("handoff_threshold_tokens", "strong_threshold_tokens"):
        require_config = int(CONFIG[_key])
    if not CONFIG["per_project_threshold_tokens"]:
        raise ValueError("per_project_threshold_tokens 为空")
except Exception as exc:
    print(f"FAIL RUNNER 读取公开配置失败：{type(exc).__name__}: {exc}")
    print("总数 1 | PASS 0 | FAIL 1")
    sys.exit(1)
CASES = []
# Public comments call the sentinel MARK but do not give its literal value.
# An evaluator can supply the published value without reading the implementation.
MARK = os.environ.get("HELDOUT_HANDOFF_MARK", "MARK")


def case(name, description):
    def register(fn):
        CASES.append((name, description, fn))
        return fn
    return register


def require(condition, reason):
    if not condition:
        raise AssertionError(reason)


class Sandbox:
    def __init__(self, root):
        self.root = Path(root)
        self.home = self.root / "home"
        self.state = self.root / "state"
        self.project = self.home / "Documents/Projects/heldout-project"
        for path in (self.home, self.state, self.project):
            path.mkdir(parents=True, exist_ok=True)
        (self.project / ".git").mkdir()
        self.env = os.environ.copy()
        # Remove inherited knobs that could silently bypass or alter the hooks.
        for key in list(self.env):
            if key.startswith(("USAGE_SAVER_", "CLAUDE_")):
                self.env.pop(key)
        self.env.update(HOME=str(self.home), USAGE_SAVER_STATE=str(self.state),
                        PYTHONDONTWRITEBYTECODE="1", BASH_ENV="/dev/null",
                        ENV="/dev/null", XDG_CACHE_HOME=str(self.home / ".cache"))
        self.transcript = self.project / "conversation.jsonl"

    def call(self, hook, payload=None, raw=None):
        command = (["/bin/sh", str(ROOT / "hooks" / hook)] if hook.endswith(".sh")
                   else [sys.executable, str(ROOT / "hooks" / hook)])
        result = subprocess.run(command, input=raw if raw is not None else json.dumps(payload),
                                text=True, capture_output=True, cwd=self.project,
                                env=self.env, timeout=12)
        require(result.returncode == 0,
                f"hook 退出 {result.returncode}: {result.stderr[-180:]!r}")
        return result.stdout

    def payload(self, **extra):
        return dict(cwd=str(self.project), session_id="heldout-session",
                    transcript_path=str(self.transcript), **extra)

    def transcript_with(self, tokens=1000, user="OWNER_GOAL_H73", edited=None):
        content = [{"type": "text", "text": "ASSISTANT_NEXT_STEP_H73"}]
        if edited:
            content.append({"type": "tool_use", "id": "write-h73", "name": "Write",
                            "input": {"file_path": str(edited), "content": "handoff"}})
        rows = [
            {"type": "user", "sessionId": "heldout-session", "cwd": str(self.project),
             "message": {"role": "user", "content": user}},
            {"type": "assistant", "sessionId": "heldout-session", "cwd": str(self.project),
             "message": {"id": "msg-h73", "role": "assistant", "content": content,
                         "usage": {"input_tokens": tokens, "output_tokens": 100,
                                   "cache_read_input_tokens": 0,
                                   "cache_creation_input_tokens": 0}}},
        ]
        self.transcript.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def rate(self, week, five=10, age=0):
        now = dt.datetime.now(dt.timezone.utc)
        # Assumed public-cache format: Anthropic utilization objects plus Unix ts.
        value = {"ts": time.time() - age,
                 "five_hour": {"utilization": five,
                               "resets_at": (now + dt.timedelta(hours=4)).isoformat()},
                 "seven_day": {"utilization": week,
                               "resets_at": (now + dt.timedelta(days=3.5)).isoformat()}}
        path = self.state / "rate.json"
        path.write_text(json.dumps(value))
        os.utime(path, (time.time() - age, time.time() - age))

    def filter(self, command):
        out = self.call("test_filter.py", self.payload(
            hook_event_name="PreToolUse", tool_name="Bash", tool_input={"command": command}))
        if not out.strip():
            return command
        data = json.loads(out)
        body = data.get("hookSpecificOutput", data)
        updated = body.get("updatedInput", {})
        return updated.get("command", command)

    def syntax(self, command):
        result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-n"],
                                input=command, text=True, capture_output=True,
                                cwd=self.project, env=self.env, timeout=5)
        require(result.returncode == 0, f"改写结果 bash -n 失败: {result.stderr[-200:]!r}")

    def end(self):
        return self.call("session_resume.py", self.payload(
            hook_event_name="SessionEnd", reason="clear"))

    def start(self, source="clear"):
        return self.call("session_resume.py", self.payload(
            hook_event_name="SessionStart", source=source))


def filter_case(name, command, rewrite, description):
    @case(name, description)
    def run(s):
        actual = s.filter(command)
        s.syntax(actual)
        require((actual != command) == rewrite,
                "应改写但保持原样" if rewrite else "应放行却改写了命令")
        if rewrite:
            require("testlogs" in actual, "改写没有指向约定的 testlogs 日志目录")


for args in [
    ("TF01", "pytest -q", True, "普通 pytest 应改写"),
    ("TF02", "python -m pytest tests/unit -q", True, "python -m pytest 应改写"),
    ("TF03", "npm test", True, "npm test 应改写"),
    ("TF04", "node --test tests/a.test.js", True, "node --test 应改写"),
    ("TF05", "pytest -q\npytest tests/other", True, "多行独立测试应改写且语法有效"),
    ("TF06", "set -e\npytest -q", True, "set -e 下应改写且语法有效"),
    ("TF07", "pytest -q && pytest tests/other", True, "&& 测试链应改写"),
    ("TF08", "pytest -q || pytest tests/fallback", True, "|| 测试链应改写"),
    ("TF09", "pytest -k 'alpha or beta' 'tests/a b.py'", True, "测试参数含引号和空格应改写"),
    ("TF10", "pytest -q | cat", False, "管道按注释契约原样放行"),
    ("TF11", "pytest -q > output.txt 2>&1", False, "重定向按契约原样放行"),
    ("TF12", "(pytest -q)", False, "括号子 shell 原样放行"),
    ("TF13", "pytest <<'END'\nhello\nEND\n", False, "heredoc 原样放行"),
    ("TF14", "if true; then\n pytest -q\nfi", False, "保留字开头的多行结构放行"),
    ("TF15", "echo 'pytest -q'", False, "普通命令参数中的 pytest 不得触发"),
    ("TF16", "printf '%s\\n' 'npm test && pytest'", False, "引号内命令文本不得触发"),
    ("TF17", "USAGE_SAVER_RAW=1 pytest -q", False, "RAW 显式绕过过滤"),
    ("TF18", "pytest --pdb", False, "交互调试参数放行"),
    ("TF19", "npm test -- --watch", False, "watch 参数放行"),
    ("TF20", "pytest $(printf tests)", False, "命令替换放行"),
    ("TF21", "pytest `printf tests`", False, "反引号替换放行"),
    ("TF22", "pytest -q &", False, "后台运行放行"),
    ("TF23", "git status && printf '%s' pytest", False, "非测试命令链不得改写"),
    ("TF24", "npm install", False, "npm 非 test 子命令不得改写"),
]:
    filter_case(*args)


def fake_execution(name, code, errexit=False):
    @case(name, f"假 pytest 退出 {code}，验证退出码、失败信息及完整日志" + ("（set -e）" if errexit else ""))
    def run(s):
        bindir = s.root / "bin"
        bindir.mkdir()
        fake = bindir / "pytest"
        fake.write_text("#!" + sys.executable + "\nimport sys\n"
                        "print('FULL_LOG_BEGIN_H73')\n"
                        "for i in range(250): print('PASSED filler_%d' % i)\n"
                        "print('FAILED tests/test_fake.py::test_x - AssertionError H73')\n"
                        "print('FULL_LOG_END_H73')\n"
                        f"sys.exit({code})\n")
        fake.chmod(0o700)
        s.env["PATH"] = str(bindir) + os.pathsep + s.env.get("PATH", "/usr/bin:/bin")
        command = ("set -e\n" if errexit else "") + "pytest -q"
        actual = s.filter(command)
        require(actual != command, "假 pytest 未被改写")
        s.syntax(actual)
        # Only these fixed payloads execute: pytest resolves to our inert fake.
        result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", actual],
                                text=True, capture_output=True, env=s.env,
                                cwd=s.project, timeout=12)
        require(result.returncode == code, f"退出码应为 {code}，实际 {result.returncode}")
        visible = result.stdout + result.stderr
        require("AssertionError H73" in visible, "失败摘要丢失")
        logs = list((s.state / "testlogs").rglob("*")) if (s.state / "testlogs").exists() else []
        require(any(p.is_file() and "FULL_LOG_BEGIN_H73" in p.read_text(errors="replace")
                    and "FULL_LOG_END_H73" in p.read_text(errors="replace") for p in logs),
                "临时状态目录没有保留完整测试日志")
        require(len(visible.splitlines()) < 253, "测试输出没有缩减")


fake_execution("TF25", 1)
fake_execution("TF26", 7, True)


@case("TF28", "混合命令只包装测试段，非测试段及其引号保持原样")
def filter_mixed(s):
    prefix = "printf '%s\\n' 'prefix && literal'"
    suffix = "printf '%s\\n' 'suffix || literal'"
    original = prefix + "; pytest -q; " + suffix
    actual = s.filter(original)
    s.syntax(actual)
    require(actual != original, "混合命令中的测试段未改写")
    require(prefix in actual and suffix in actual, "非测试段或其中引号被改动")


@case("TF29", "假 pytest 成功退出应保持状态码 0")
def fake_success(s):
    bindir = s.root / "bin"
    bindir.mkdir()
    fake = bindir / "pytest"
    fake.write_text("#!" + sys.executable + "\nprint('3 passed in 0.01s')\n")
    fake.chmod(0o700)
    s.env["PATH"] = str(bindir) + os.pathsep + s.env.get("PATH", "/usr/bin:/bin")
    actual = s.filter("pytest -q")
    s.syntax(actual)
    require(actual != "pytest -q", "成功测试未被包装")
    result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", actual],
                            text=True, capture_output=True, env=s.env, cwd=s.project, timeout=12)
    require(result.returncode == 0, f"成功测试退出码变为 {result.returncode}")
    require("3 passed" in result.stdout + result.stderr, "成功摘要丢失")


def guard_case(name, tokens, warning, description, week=None, five=10, age=0, project=None):
    @case(name, description)
    def run(s):
        if project:
            s.project = s.project.parent / project
            s.project.mkdir()
            (s.project / ".git").mkdir()
            s.transcript = s.project / "conversation.jsonl"
        if week is not None:
            s.rate(week, five, age)
        s.transcript_with(tokens)
        out = s.call("context_guard.py", s.payload(hook_event_name="UserPromptSubmit", prompt="继续"))
        # A real warning must mention the documented user action, not arbitrary output.
        actual = "/clear" in out
        require(actual == warning,
                f"ctx={tokens} 应{'提醒' if warning else '不提醒'} /clear，输出 {out[-160:]!r}")


soft = CONFIG["handoff_threshold_tokens"]
strong = CONFIG["strong_threshold_tokens"]
guard_case("CG01", soft - 1, False, "无额度缓存：基础软线前一 token 不提醒")
guard_case("CG02", soft + 1, True, "无额度缓存：越过基础软线应提醒")
guard_case("CG03", strong + 1, True, "超过强线必须提醒且 hook 不拦截")
guard_case("CG04", 180001, False, "宽裕档：周用量 10%、周进度约 50%，18 万未到 24 万", week=10)
guard_case("CG05", 240001, True, "宽裕档：越过 24 万应提醒", week=10)
guard_case("CG06", 160001, True, "正常档：周用量 50%、周进度约 50%", week=50)
guard_case("CG07", 120001, True, "紧张档：周用量 80%、周进度约 50%，越过 12 万", week=80)
guard_case("CG08", 100001, True, "快用完档：周用量 95%，越过 10 万", week=95)
guard_case("CG09", 100001, True, "快用完档：5 小时用量 95% 独立触发", week=10, five=95)
guard_case("CG10", 99999, False, "快用完档：低于最低 10 万软线不提醒", week=95)
guard_case("CG11", 130000, False, "过期 48 小时的高用量缓存应回退正常档", week=95, age=48*3600)
guard_case("CG12", 180001, True, "过期 48 小时的宽裕缓存不能继续提高软线", week=10, age=48*3600)
project_name, project_soft = next(iter(CONFIG["per_project_threshold_tokens"].items()))
guard_case("CG13", project_soft - 1, False, "项目专属软线前不提醒", project=project_name)
guard_case("CG14", project_soft + 1, True, "项目专属软线后提醒", project=project_name)


@case("CG15", "真实上下文应包含 cache_read_input_tokens")
def guard_cached(s):
    s.transcript_with(1000)
    rows = [json.loads(line) for line in s.transcript.read_text().splitlines()]
    rows[-1]["message"]["usage"]["cache_read_input_tokens"] = soft + 1
    s.transcript.write_text("".join(json.dumps(row) + "\n" for row in rows))
    out = s.call("context_guard.py", s.payload(hook_event_name="UserPromptSubmit", prompt="继续"))
    require("/clear" in out, "忽略缓存读取 token，未提醒")


@case("SR01", "clear 无交接文件：从上一段 transcript 恢复目标和下一步")
def resume_digest(s):
    s.transcript_with()
    s.end()
    out = s.start()
    require("OWNER_GOAL_H73" in out and "ASSISTANT_NEXT_STEP_H73" in out,
            "clear 后未同时带回用户目标和助手下一步")


@case("SR02", "clear 未标记、未编辑的交接文件：只给路径，不注入正文")
def resume_untrusted(s):
    path = s.project / "HANDOFF.md"
    path.write_text("# 普通交接\nUNTRUSTED_BODY_K91\n")
    s.transcript_with()
    s.end()
    out = s.start()
    require("UNTRUSTED_BODY_K91" not in out, "未受信的交接正文被注入")
    require("HANDOFF.md" in out, "未给出可供查看的交接路径")


@case("SR03", "clear 上段对话经 Write 编辑的交接文件可注入，且声明数据属性")
def resume_edited(s):
    path = s.project / "HANDOFF.md"
    path.write_text("# 交接\nTRUSTED_BODY_K91\n## 下一步\nNEXT_TASK_K91\n")
    s.transcript_with(edited=path)
    s.end()
    out = s.start()
    require("TRUSTED_BODY_K91" in out, "有 Write 来源的交接正文未恢复")
    require(("数据" in out and "指令" in out) or
            ("data" in out.lower() and "instruction" in out.lower()), "缺少数据/指令边界声明")


@case("SR04", "clear 带公开 MARK 的交接正文可恢复（MARK 字面值为夹具假设）")
def resume_marked(s):
    (s.project / "HANDOFF.md").write_text(MARK + "\nMARKED_BODY_K91\n")
    s.transcript_with()
    s.end()
    require("MARKED_BODY_K91" in s.start(), f"带标记正文未注入；本次 MARK={MARK!r}")


@case("SR05", "compact 仅注入受信交接的红线/未决、下一步，不重复旧对话")
def resume_compact(s):
    path = s.project / "HANDOFF.md"
    path.write_text("# 交接\n## 已完成\nCOMPLETED_SECRET_K91\n" + "无关背景\n" * 1800 +
                    "## 红线/未决\nREDLINE_K91\n## 下一步\nNEXT_TASK_K91\n")
    s.transcript_with(edited=path)
    out = s.start("compact")
    require("REDLINE_K91" in out and "NEXT_TASK_K91" in out, "compact 缺少红线或下一步")
    require("COMPLETED_SECRET_K91" not in out and "OWNER_GOAL_H73" not in out,
            "compact 重复了无关正文或对话摘要")
    # Count the decoded context rather than JSON escaping overhead.
    try:
        data = json.loads(out)
        context = data.get("hookSpecificOutput", {}).get("additionalContext", out)
    except ValueError:
        context = out
    require(len(context) <= 2000, f"compact 注入 {len(context)} 字，超过 2000")


@case("SR06", "跨项目 clear 记录与交接不得泄漏")
def resume_cross_project(s):
    (s.project / "HANDOFF.md").write_text(MARK + "\nPROJECT_A_PRIVATE_K91\n")
    s.transcript_with(user="PROJECT_A_OWNER_K91")
    s.end()
    s.project = s.project.parent / "another-project"
    s.project.mkdir()
    (s.project / ".git").mkdir()
    s.transcript = s.project / "new.jsonl"
    out = s.start()
    require("PROJECT_A_PRIVATE_K91" not in out and "PROJECT_A_OWNER_K91" not in out,
            "项目 B 收到了项目 A 的交接或摘要")


@case("SR07", "从项目子目录 clear 可向上找到项目内的受信交接")
def resume_nested(s):
    path = s.project / "HANDOFF.md"
    path.write_text("# 交接\nNESTED_BODY_K91\n")
    s.project = s.project / "src" / "nested"
    s.project.mkdir(parents=True)
    s.transcript = s.project / "conversation.jsonl"
    s.transcript_with(edited=path)
    s.end()
    require("NESTED_BODY_K91" in s.start(), "未找到项目根内上级交接")


@case("SR08", "没有交接且没有旧 transcript 的 clear 应安静放行")
def resume_empty(s):
    require(not s.start().strip(), "没有可恢复数据时仍注入了内容")


@case("SR09", "compact 未标记且无编辑来源时不注入交接正文")
def compact_untrusted(s):
    (s.project / "HANDOFF.md").write_text("# 普通交接\n## 下一步\nUNTRUSTED_COMPACT_K91\n")
    s.transcript_with()
    out = s.start("compact")
    require("UNTRUSTED_COMPACT_K91" not in out, "compact 注入了未受信正文")
    require("HANDOFF.md" in out, "compact 未提供交接路径")


@case("SR10", "startup 不应当作 clear 重复注入上一段摘要")
def resume_startup(s):
    s.transcript_with()
    s.end()
    require("OWNER_GOAL_H73" not in s.start("startup"), "普通 startup 重放了 clear 摘要")


@case("SL01", "statusline 正常 payload 应产生非空输出且不崩溃")
def status_normal(s):
    out = s.call("statusline.sh", s.payload(
        model={"id": "claude-sonnet", "display_name": "Sonnet"},
        context_window={"context_window_size": 1000000, "used_percentage": 17,
                        "current_usage": {"input_tokens": 170000, "output_tokens": 30,
                                          "cache_read_input_tokens": 0,
                                          "cache_creation_input_tokens": 0}},
        workspace={"current_dir": str(s.project), "project_dir": str(s.project)}))
    require(bool(out.strip()), "正常状态栏输出为空")


@case("SL02", "statusline 空对象不崩溃")
def status_empty(s):
    s.call("statusline.sh", {})


@case("SL03", "statusline 损坏 stdin 不崩溃")
def status_bad(s):
    s.call("statusline.sh", raw="{broken")


@case("SL04", "statusline 额度缓存损坏不崩溃")
def status_bad_rate(s):
    (s.state / "rate.json").write_text("{broken")
    s.call("statusline.sh", s.payload(model={"display_name": "Sonnet"}))


for hook, label in [("test_filter.py", "TF27"), ("context_guard.py", "CG16"),
                    ("session_resume.py", "SR11")]:
    def register_bad(hook=hook, label=label):
        @case(label, f"{hook} 损坏 JSON 应 fail-open")
        def bad_json(s):
            require(not s.call(hook, raw="{broken").strip(), "损坏输入未安静放行")
    register_bad()


def main():
    passed = failed = 0
    for name, description, fn in CASES:
        try:
            with tempfile.TemporaryDirectory(prefix=f"heldout-{name}-") as folder:
                fn(Sandbox(folder))
        except Exception as exc:
            failed += 1
            reason = " ".join(str(exc).split())[:420] or type(exc).__name__
            print(f"FAIL {name} {description}：{reason}", flush=True)
        else:
            passed += 1
            print(f"PASS {name} {description}：符合预期", flush=True)
    print(f"总数 {passed + failed} | PASS {passed} | FAIL {failed}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"FAIL RUNNER 脚本自身异常：{type(exc).__name__}: {exc}", flush=True)
        print("总数 1 | PASS 0 | FAIL 1", flush=True)
        sys.exit(1)
