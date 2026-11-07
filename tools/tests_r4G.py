#!/usr/bin/env python3
"""R4G：session_resume.py 的复现用例（改前 fail、改后 pass）。

用法：
  python3 tools/tests_r4G.py                                    # 测 hooks/session_resume.py
  python3 tools/tests_r4G.py --hook backups/session_resume.py.pre-r4G   # 测改前版本
  python3 tools/tests_r4G.py --base <目录>                       # 假 HOME 等放在哪
  R4G_TMP=<目录> python3 tools/tests_r4G.py                      # 同上（环境变量）

临时目录回退（R4G2）：--base → R4G_TMP → tempfile 默认目录 → 项目内 state/_test_tmp/；
用的是项目内目录时，跑完整体 mv 到 state/_to_delete/（不 rm）。
隔离：每个用例在 base 下新建一个目录（mkdtemp，不自动删除）：假 HOME、USAGE_SAVER_STATE 都指向它；
被测脚本以子进程运行，不读写真实的 ~/.claude、state/。
「拥有者」测试：直接由本进程调用 = 会话 A；经过一个中间 python 进程调用 = 另一个 Claude Code 进程（会话 B/C）。
"""
import argparse, datetime as dt, hashlib, json, os, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = "<!-- usage-saver-handoff -->"
HMARK = MARK + "\n"
HOOK = None
BASE = None
CASES = []


def case(cid, desc):
    def deco(fn):
        CASES.append((cid, desc, fn))
        return fn
    return deco


class Env:
    def __init__(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="r4G-", dir=BASE))
        self.home = os.path.join(self.root, "home")
        self.projects = os.path.join(self.home, "Documents", "Projects")
        self.state = os.path.join(self.root, "state")
        self.tdir = os.path.join(self.home, ".claude", "projects", "-x")
        for d in (self.projects, self.state, self.tdir):
            os.makedirs(d, exist_ok=True)

    def env(self, no_ps=False):
        env = {**os.environ, "HOME": self.home, "USAGE_SAVER_STATE": self.state}
        if no_ps:
            # PATH 最前放一个总是失败的假 ps：owner_id() 拿不到拥有者、返回 None（模拟 ps 被沙箱拒）
            fake = os.path.join(self.root, "fakebin")
            os.makedirs(fake, exist_ok=True)
            p = os.path.join(fake, "ps")
            open(p, "w").write("#!/bin/sh\nexit 1\n")
            os.chmod(p, 0o755)
            env["PATH"] = fake + os.pathsep + env.get("PATH", "")
        return env

    def argv(self, via_other=None):
        argv = [sys.executable, HOOK]
        if via_other:
            argv = [sys.executable, "-c",
                    "import subprocess,sys;sys.exit(subprocess.run(sys.argv[1:]).returncode)"] + argv
        return argv

    @staticmethod
    def ctx_of(stdout):
        if not stdout.strip():
            return ""
        return json.loads(stdout)["hookSpecificOutput"]["additionalContext"]

    def run(self, data, via_other=None, no_ps=False):
        """via_other=None：本进程直接调用（会话 A 的拥有者）；否则经过一个中间进程（另一个拥有者）。"""
        p = subprocess.run(self.argv(via_other), input=json.dumps(data), capture_output=True, text=True,
                           env=self.env(no_ps), timeout=30)
        self.rc = p.returncode
        return self.ctx_of(p.stdout)

    def start_data(self, cwd, sid="cur", source="clear", tp=None):
        return {"hook_event_name": "SessionStart", "source": source, "cwd": cwd, "session_id": sid,
                "transcript_path": tp or os.path.join(self.tdir, f"{sid}.jsonl")}

    def start(self, cwd, sid="cur", source="clear", tp=None, via_other=None, no_ps=False):
        return self.run(self.start_data(cwd, sid, source, tp), via_other, no_ps)

    def ptr_file(self, cwd):
        d = os.path.join(self.state, "last_clear"); os.makedirs(d, exist_ok=True)
        return os.path.join(d, hashlib.sha1(os.path.realpath(cwd).encode()).hexdigest()[:16] + ".json")

    def end(self, cwd, tp, sid, via_other=None):
        return self.run({"hook_event_name": "SessionEnd", "reason": "clear", "cwd": cwd, "session_id": sid,
                         "transcript_path": tp}, via_other)

    def proj(self, *parts, git="dir"):
        d = os.path.join(self.projects, *parts) if not os.path.isabs(parts[0]) else os.path.join(*parts)
        os.makedirs(d, exist_ok=True)
        if git == "dir":
            os.makedirs(os.path.join(d, ".git"), exist_ok=True)
        elif git == "file":
            open(os.path.join(d, ".git"), "w").write("gitdir: /somewhere/.git/worktrees/x\n")
        return d


def touch(path, text, age_h=0.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write(text)
    t = time.time() - age_h * 3600
    os.utime(path, (t, t))
    return path


def iso(sec_ago):
    return dt.datetime.fromtimestamp(time.time() - sec_ago, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def transcript(path, title, start_ago=900, extra=(), tail=()):
    rows = [{"type": "ai-title", "aiTitle": title},
            {"type": "user", "timestamp": iso(start_ago), "message": {"role": "user", "content": "问题 " + title}}]
    rows += list(extra)
    rows.append({"type": "assistant", "timestamp": iso(60),
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "回复 " + title}]}})
    rows += list(tail)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def edit_row(fp, cwd=None):
    r = {"type": "assistant", "timestamp": iso(800),
         "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Write", "id": "w1",
                                                       "input": {"file_path": fp, "content": "x"}}]}}
    if cwd:
        r["cwd"] = cwd
    return r


# ------------------------------------------------------------------ #1 跨项目回退
@case("G1a", "cwd=~/Documents/mc-option-pricer（有 .git、无交接），Projects/b 有 1 小时前带标记的交接：不注入、不提 b")
def _(E):
    b = E.proj("b"); touch(os.path.join(b, "HANDOFF.md"), HMARK + "B-BODY", 1)
    mc = E.proj(os.path.join(E.home, "Documents", "mc-option-pricer"))
    ctx = E.start(mc)
    return "B-BODY" not in ctx and b not in ctx


@case("G1b", "cwd 在 HOME 外（/private/tmp 下的独立仓库）：不跨项目拿 Projects/b 的交接")
def _(E):
    b = E.proj("b"); touch(os.path.join(b, "HANDOFF.md"), HMARK + "B-BODY", 1)
    t = E.proj(os.path.join(E.root, "tmp", "current"))
    ctx = E.start(t)
    return "B-BODY" not in ctx and b not in ctx


@case("G1c", "对照：cwd 恰为 Projects 或 ~ 时仍退回 Projects 下最新的交接")
def _(E):
    b = E.proj("b"); touch(os.path.join(b, "HANDOFF.md"), HMARK + "B-BODY", 1)
    return "B-BODY" in E.start(E.projects) and "B-BODY" in E.start(E.home)


# ------------------------------------------------------------------ #2 项目根
@case("G2a", "Projects 外的 worktree（.git 是文件）、上层仓库有带标记交接：不越过 worktree 根去拿上层的")
def _(E):
    outer = E.proj(os.path.join(E.home, "Documents", "outer"))
    touch(os.path.join(outer, "HANDOFF.md"), HMARK + "OUTER-BODY", 0.5)
    wt = E.proj(os.path.join(outer, "wt"), git="file")
    return "OUTER-BODY" not in E.start(wt)


@case("G2b", "Projects/super 下的 submodule（.git 是文件）：不拿 super 的交接")
def _(E):
    sup = E.proj("super"); touch(os.path.join(sup, "HANDOFF.md"), HMARK + "SUPER-BODY", 0.5)
    sub = E.proj("super", "sub", git="file")
    return "SUPER-BODY" not in E.start(sub)


@case("G2c", "cwd 是指向 Projects/a 的符号链接（链接在 HOME 外）：realpath 后认出是项目 a，注入 a 的交接（不拿更新的 b）")
def _(E):
    a = E.proj("a"); touch(os.path.join(a, "HANDOFF.md"), HMARK + "A-BODY", 0.2)
    b = E.proj("b"); touch(os.path.join(b, "HANDOFF.md"), HMARK + "B-BODY", 0.05)   # 更新，跨项目回退会选它
    link = os.path.join(E.root, "link-a"); os.symlink(a, link)
    ctx = E.start(link)
    return "A-BODY" in ctx and "B-BODY" not in ctx


# ------------------------------------------------------------------ #3 并发串线
@case("G3a", "同 cwd 两个会话先后 /clear（A 先、B 后写指针）：A 的新会话拿 A 的摘要，不拿 B 的")
def _(E):
    a = E.proj("a")
    ta = transcript(os.path.join(E.tdir, "A.jsonl"), "TITLE-A")
    tb = transcript(os.path.join(E.tdir, "B.jsonl"), "TITLE-B")
    E.end(a, ta, "A")
    E.end(a, tb, "B", via_other=True)
    ctx = E.start(a, sid="A2")
    return "TITLE-A" in ctx and "TITLE-B" not in ctx


@case("G3b", "同 cwd 有两个别的进程的指针、自己没有指针：不出摘要（不按 cwd 猜）")
def _(E):
    a = E.proj("a")
    ta = transcript(os.path.join(E.tdir, "A.jsonl"), "TITLE-A")
    tb = transcript(os.path.join(E.tdir, "B.jsonl"), "TITLE-B")
    E.end(a, ta, "A", via_other=True)
    E.end(a, tb, "B", via_other=True)
    ctx = E.start(a, sid="C2")
    return "TITLE-A" not in ctx and "TITLE-B" not in ctx


@case("G3c", "兼容：state/last_clear 里是旧版单指针文件（无 owner）：照常读出摘要、不报错")
def _(E):
    a = E.proj("a")
    tl = transcript(os.path.join(E.tdir, "L.jsonl"), "TITLE-LEG")
    d = os.path.join(E.state, "last_clear"); os.makedirs(d, exist_ok=True)
    h = hashlib.sha1(os.path.abspath(a).encode()).hexdigest()[:16]
    json.dump({"ts": time.time() - 60, "transcript_path": tl, "session_id": "L", "cwd": a},
              open(os.path.join(d, h + ".json"), "w"))
    return "TITLE-LEG" in E.start(a, sid="L2")


@case("G3d", "对照：同一进程连续两次 /clear（A1→A2→A3）：A3 拿 A2 的摘要，不拿已被 A2 用过的 A1")
def _(E):
    a = E.proj("a")
    t1 = transcript(os.path.join(E.tdir, "A1.jsonl"), "TITLE-A1")
    E.end(a, t1, "A1"); E.start(a, sid="A2", tp=os.path.join(E.tdir, "A2.jsonl"))
    t2 = transcript(os.path.join(E.tdir, "A2.jsonl"), "TITLE-A2")
    E.end(a, t2, "A2")
    ctx = E.start(a, sid="A3")
    return "TITLE-A2" in ctx and "TITLE-A1" not in ctx


@case("G3e", "A 的指针已被 A2 用过、B 的指针属于别的进程：第三个进程的新会话 C 不出摘要（glob 兜底也不拿指针里的 transcript）")
def _(E):
    a = E.proj("a")
    ta = transcript(os.path.join(E.tdir, "A.jsonl"), "TITLE-A")
    tb = transcript(os.path.join(E.tdir, "B.jsonl"), "TITLE-B")
    E.end(a, ta, "A")
    E.end(a, tb, "B", via_other=True)
    E.start(a, sid="A2")
    ctx = E.start(a, sid="C2", via_other=True)
    return "TITLE-A" not in ctx and "TITLE-B" not in ctx


# ------------------------------------------------------------------ #4 fresh / edited_in
@case("G4a", "长会话（5 小时前开始）3.5 小时前写了带标记的交接、之后继续跑：仍算上一段写的（不降到 2500 字、不标「可能过期」）")
def _(E):
    a = E.proj("a")
    body = HMARK + "# 交接\n" + "头部。" * 700 + "MID-MARK" + "尾部。" * 900 + "\nEND\n"
    touch(os.path.join(a, "HANDOFF.md"), body, 3.5)
    t = transcript(os.path.join(E.tdir, "O.jsonl"), "T", start_ago=5 * 3600)
    E.end(a, t, "O")
    ctx = E.start(a, sid="N")
    return "MID-MARK" in ctx and "不是上一段对话写的" not in ctx


@case("G4b", "无标记交接在上一段很早就用 Write 写过，之后 transcript 又追加 >3MB：仍认作上一段改过，注入正文")
def _(E):
    m = E.proj("m")
    hp = touch(os.path.join(m, "HANDOFF.md"), "# 交接\nEDITED-BODY", 0.1)
    filler = [{"type": "assistant", "timestamp": iso(500),
               "message": {"role": "assistant", "content": [{"type": "text", "text": "输出" * 5000}]}}] * 400
    t = transcript(os.path.join(E.tdir, "O.jsonl"), "T", extra=[edit_row(hp)] + filler)
    E.end(m, t, "O")
    t0 = time.time()
    ctx = E.start(m, sid="N")
    E.note = f"transcript {os.path.getsize(t) / 1e6:.1f}MB，SessionStart 耗时 {time.time() - t0:.2f}s"
    return "EDITED-BODY" in ctx


@case("G4c", "无标记交接上一段用相对路径 Write HANDOFF.md（行内 cwd=项目）：解析成绝对路径后认作改过")
def _(E):
    m = E.proj("m")
    touch(os.path.join(m, "HANDOFF.md"), "# 交接\nREL-BODY", 0.1)
    t = transcript(os.path.join(E.tdir, "O.jsonl"), "T", extra=[edit_row("HANDOFF.md", cwd=m)])
    E.end(m, t, "O")
    return "REL-BODY" in E.start(m, sid="N")


# ------------------------------------------------------------------ 复核意见（round4_review_12_13 F2/F3）
REAL_FMT = (HMARK + "# 最新状态（以本节为准）\n**目标**：x\n**红线 / 未决**：\n- **默认模型已定**：RED-ITEM-1\n"
            "- 不 push RED-ITEM-2\n- relay-room 不碰 RED-ITEM-3\n**下一步（第 4 轮）**：\n1. NEW-NEXT\n2. 再做 y\n\n"
            "# 历史\n**下一步**：OLD-NEXT\n\n**红线**：OLD-RED\n")


@case("G5a", "compact：`- **xx**` 加粗列表项不当分段点；只取第一节红线和第一节下一步，红线在前，不收旧轮次")
def _(E):
    a = E.proj("a"); touch(os.path.join(a, "HANDOFF.md"), REAL_FMT, 0.2)
    ctx = E.start(a, source="compact", tp=transcript(os.path.join(E.tdir, "cur.jsonl"), "C"))
    ok = all(k in ctx for k in ("RED-ITEM-1", "RED-ITEM-2", "RED-ITEM-3", "NEW-NEXT"))
    return ok and "OLD-NEXT" not in ctx and "OLD-RED" not in ctx and ctx.index("RED-ITEM-1") < ctx.index("NEW-NEXT")


@case("G5b", "compact：红线一节超长（>2000 字）时截断并标「已截断，见文件」，不静默")
def _(E):
    a = E.proj("a")
    red = "### 红线 / 未决\n" + "\n".join(f"- 红线条目 {i}：不要动 xxx 目录里的 yyy 文件，除非所有者明确同意" for i in range(80))
    touch(os.path.join(a, "HANDOFF.md"), HMARK + "## 当前状态\n" + red + "\n- RED-TAIL\n### 下一步\n1. NX\n", 0.2)
    ctx = E.start(a, source="compact", tp=transcript(os.path.join(E.tdir, "cur.jsonl"), "C"))
    return 0 < len(ctx) <= 2000 and "已截断，见文件" in ctx


@case("G5c", "标记在第 2 行（标题在前）或被 ``` 包住：算有标记，注入正文；标记在第 5 行不算")
def _(E):
    a = E.proj("a"); touch(os.path.join(a, "HANDOFF.md"), "# 交接\n" + HMARK + "L2-BODY", 0.2)
    b = E.proj("b"); touch(os.path.join(b, "HANDOFF.md"), "```markdown\n" + HMARK + "FENCE-BODY\n```\n", 0.2)
    c = E.proj("c"); touch(os.path.join(c, "HANDOFF.md"), "# t\na\nb\nc\n" + HMARK + "L5-BODY", 0.2)
    return "L2-BODY" in E.start(a) and "FENCE-BODY" in E.start(b) and "L5-BODY" not in E.start(c)


@case("G5d", "只给路径时的文案：「所有者说「接着做」时先读这个文件：<路径>」")
def _(E):
    a = E.proj("cloned"); hp = touch(os.path.join(a, "HANDOFF.md"), "# 交接\nTHIRD-BODY", 0.1)
    ctx = E.start(a)
    return "THIRD-BODY" not in ctx and f"所有者说「接着做」时先读这个文件：{hp}" in ctx


# ------------------------------------------------------------------ G2：Codex 打回（round4_codex_review_G）
@case("G6a", "compact 混合标题：`### 下一步` 后空一行接 `**下一步（第 3 轮）**：OLD-NEXT`：只带 NEW-NEXT，不带 OLD-NEXT")
def _(E):
    a = E.proj("a")
    touch(os.path.join(a, "HANDOFF.md"), HMARK + "### 红线 / 未决\n- RED\n### 下一步\n1. NEW-NEXT\n\n"
          "**下一步（第 3 轮）**：OLD-NEXT\n", 0.2)
    ctx = E.start(a, source="compact", tp=transcript(os.path.join(E.tdir, "cur.jsonl"), "C"))
    return "RED" in ctx and "NEW-NEXT" in ctx and "OLD-NEXT" not in ctx


@case("G6b", "compact 嵌套标题：红线节内嵌 `#### 下一步`（当前）、历史节另有下一步：选当前的，不选历史的，红线在前")
def _(E):
    a = E.proj("a")
    touch(os.path.join(a, "HANDOFF.md"), HMARK + "## 当前\n### 红线 / 未决\n- RED-NOW\n#### 下一步\n1. NEW-NEXT\n"
          "## 历史\n### 下一步\n1. OLD-NEXT\n### 红线\n- OLD-RED\n", 0.2)
    ctx = E.start(a, source="compact", tp=transcript(os.path.join(E.tdir, "cur.jsonl"), "C"))
    return ("RED-NOW" in ctx and "NEW-NEXT" in ctx and "OLD-NEXT" not in ctx and "OLD-RED" not in ctx
            and ctx.index("RED-NOW") < ctx.index("NEW-NEXT") and ctx.count("NEW-NEXT") == 1)


@case("G6c", "对照：红线节里的普通加粗段落（`**特别注意**：RED-B`）、加粗列表项 `- **下一步前先问**` 仍算红线，不被截掉")
def _(E):
    a = E.proj("a")
    touch(os.path.join(a, "HANDOFF.md"), HMARK + "### 红线 / 未决\n- RED-A\n- **下一步前先问所有者**：RED-L\n\n"
          "**特别注意**：RED-B\n### 下一步\n1. NEW-NEXT\n# 历史\n**下一步**：OLD-NEXT\n", 0.2)
    ctx = E.start(a, source="compact", tp=transcript(os.path.join(E.tdir, "cur.jsonl"), "C"))
    return all(k in ctx for k in ("RED-A", "RED-L", "RED-B", "NEW-NEXT")) and "OLD-NEXT" not in ctx


@case("G7a", "ps 失败（owner_id()=None）：同 cwd 未过期的 v2 指针 owner=OTHER：不拿它的摘要（glob 兜底也不拿）")
def _(E):
    a = E.proj("a")
    tb = transcript(os.path.join(E.tdir, "B.jsonl"), "TITLE-B")
    json.dump({"v": 2, "entries": [{"ts": time.time() - 60, "transcript_path": tb, "session_id": "B",
                                    "cwd": os.path.realpath(a), "owner": "OTHER"}]}, open(E.ptr_file(a), "w"))
    ctx = E.start(a, sid="N", no_ps=True)
    return "TITLE-B" not in ctx and E.rc == 0


@case("G7b", "对照：ps 失败时，同 cwd 只有一条旧版单指针（无 owner）：照常给摘要（兼容旧格式）")
def _(E):
    a = E.proj("a")
    tl = transcript(os.path.join(E.tdir, "L.jsonl"), "TITLE-LEG")
    json.dump({"ts": time.time() - 60, "transcript_path": tl, "session_id": "L", "cwd": a}, open(E.ptr_file(a), "w"))
    return "TITLE-LEG" in E.start(a, sid="L2", no_ps=True)


def _hold_lock(E):
    import fcntl
    d = os.path.join(E.state, "last_clear"); os.makedirs(d, exist_ok=True)
    fh = open(os.path.join(d, ".lock"), "a")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


@case("G8a", "消费交错：外部先拿住指针锁，同一拥有者的两个新会话 N1、N2 同时启动，1.5 秒后放锁：恰好一个拿到摘要")
def _(E):
    a = E.proj("a")
    ta = transcript(os.path.join(E.tdir, "A.jsonl"), "TITLE-A")
    E.end(a, ta, "A")
    fh = _hold_lock(E)
    ps = []
    try:
        for sid in ("N1", "N2"):
            p = subprocess.Popen(E.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=E.env())
            p.stdin.write(json.dumps(E.start_data(a, sid=sid))); p.stdin.close()
            ps.append(p)
        time.sleep(1.5)       # 旧版：两者都在锁外选好了同一条，卡在 _mark_used 的锁上
    finally:
        fh.close()            # 放锁
    outs = [E.ctx_of(p.stdout.read()) for p in ps]
    for p in ps:
        p.wait(timeout=30)
    hits = sum("TITLE-A" in o for o in outs)
    used = [e.get("used_by") for e in json.load(open(E.ptr_file(a)))["entries"]]
    E.note = f"拿到摘要的会话数={hits}，指针 used_by={used}"
    return hits == 1 and used in (["N1"], ["N2"]) and ("TITLE-A" in outs[0]) == (used == ["N1"])


@case("G8b", "消费失败（指针目录只读，记不了 used_by）：不出摘要、不返回旧的选择，exit 0")
def _(E):
    a = E.proj("a")
    ta = transcript(os.path.join(E.tdir, "A.jsonl"), "TITLE-A")
    E.end(a, ta, "A")
    d = os.path.join(E.state, "last_clear")
    open(os.path.join(d, ".lock"), "a").close()
    os.chmod(d, 0o555)
    try:
        ctx = E.start(a, sid="N1")
    finally:
        os.chmod(d, 0o755)
    return "TITLE-A" not in ctx and E.rc == 0


@case("G8c", "指针锁被别人长时间占住（4 秒）：最多等约 3 秒就放弃摘要、正常退出（不卡住 SessionStart，也不在没锁时硬读）")
def _(E):
    import threading
    a = E.proj("a")
    touch(os.path.join(a, "HANDOFF.md"), HMARK + "LOCK-HANDOFF", 0.1)
    ta = transcript(os.path.join(E.tdir, "A.jsonl"), "TITLE-A")
    E.end(a, ta, "A")
    fh = _hold_lock(E)
    timer = threading.Timer(4.0, fh.close)     # 4 秒后放锁
    timer.start()
    t0 = time.time()
    try:
        p = subprocess.Popen(E.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=E.env())
        out, _ = p.communicate(json.dumps(E.start_data(a, sid="N1")), timeout=30)
        el = time.time() - t0
    finally:
        timer.join()
    ctx = E.ctx_of(out)
    E.note = f"耗时 {el:.1f}s"
    return el < 3.9 and p.returncode == 0 and "TITLE-A" not in ctx and "LOCK-HANDOFF" in ctx


def _try_base(d):
    """在 d 下建本次运行的目录；建不了返回 None。"""
    try:
        os.makedirs(d, exist_ok=True)
        return os.path.realpath(tempfile.mkdtemp(prefix=f"r4G-run-{time.strftime('%Y%m%d-%H%M%S')}-", dir=d))
    except Exception:
        return None


def pick_base(cli_base):
    """临时目录回退顺序：--base → 环境变量 R4G_TMP → tempfile 默认目录 → 项目内 state/_test_tmp/。
    返回 (本次运行目录, 是否项目内)。"""
    local = os.path.join(ROOT, "state", "_test_tmp")
    for d in (cli_base, os.environ.get("R4G_TMP")):
        if d:
            r = _try_base(d)
            if r:
                return r, os.path.realpath(d) == os.path.realpath(local)
    try:
        r = _try_base(tempfile.gettempdir())
        if r:
            return r, False
    except Exception:
        pass
    r = _try_base(local)
    if r:
        return r, True
    raise SystemExit("没有可用的临时目录（--base / R4G_TMP / tempfile / state/_test_tmp 都不可写）")


def main():
    global HOOK, BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--hook", default="hooks/session_resume.py")
    ap.add_argument("--base", default=None)
    ap.add_argument("--only")
    a = ap.parse_args()
    HOOK = a.hook if os.path.isabs(a.hook) else os.path.join(ROOT, a.hook)
    BASE, local = pick_base(a.base)
    print(f"临时目录：{BASE}")
    passed = 0
    sel = [c for c in CASES if not a.only or c[0] in a.only.split(",")]
    for cid, desc, fn in sel:
        E = Env(); E.note = ""; E.rc = None
        try:
            ok = bool(fn(E))
        except Exception as e:
            ok, E.note = False, f"异常 {type(e).__name__}: {e}"
        passed += ok
        print(f"{cid:5} {'PASS' if ok else 'FAIL'}  {desc}" + (f"  〔{E.note}〕" if E.note else ""))
    print(f"{os.path.relpath(HOOK, ROOT)}：{passed}/{len(sel)} 通过")
    if local:
        # 项目内的临时目录用完 mv 到 state/_to_delete/（不 rm）
        dst = os.path.join(ROOT, "state", "_to_delete", os.path.basename(BASE))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(BASE, dst)
            print(f"已移到 {os.path.relpath(dst, ROOT)}")
        except Exception as e:
            print(f"没能移走临时目录（{e}），请手动 mv：{BASE}")
    return 0 if passed == len(sel) else 1


if __name__ == "__main__":
    sys.exit(main())
