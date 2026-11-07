#!/usr/bin/env python3
"""第 4 轮 F 的测试：hooks/limit_logger.py 和 tools/install_limit_logger.py。

隔离：每个用例一个 tempfile 临时目录（结束自动清理）；USAGE_SAVER_STATE 指向临时目录；
安装器只用 --settings / --backup-dir 指向临时副本，绝不碰 ~/.claude/settings.json。
用法：python3 tools/tests_r4F.py
临时目录（F2）：优先 $R4F_TMP；否则 tempfile；tempfile 不可用（沙箱拒绝 mktemp）时退回到
项目内 state/_test_tmp/，每个用例用完 mv 到 state/_to_delete/（不 rm，由所有者清理）。
R4F_FORCE_FALLBACK=1 可强制走退回路径（用来自测退回逻辑）。
F3 新增：L15 轮转交错（A 每一步之后暂停、B 轮转并新建 C）、L16 锁被占时 <2 秒返回、L17/L18/L19 短写与半条记录、
I13/I14 卸载只认本套件脚本的绝对路径。
"""
import contextlib, errno, fcntl, importlib.util, io, json, os, shutil, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "limit_logger.py")
INST = os.path.join(ROOT, "tools", "install_limit_logger.py")
REAL_SETTINGS = os.path.expanduser("~/.claude/settings.json")

SF = {"session_id": "s-rl", "transcript_path": "/x/t.jsonl", "cwd": "/Users/u/项目 a",
      "hook_event_name": "StopFailure", "error": "rate_limit",
      "error_details": "429 Too Many Requests", "last_assistant_message": "API Error: Rate limit reached"}
NT = {"session_id": "s-q", "transcript_path": "/x/t.jsonl", "cwd": "/Users/u",
      "hook_event_name": "Notification", "message": "Resuming after usage limit reset",
      "title": "Claude Code", "notification_type": "quota_auto_resume_fired"}
RATE = {"ts": 1793961649, "rate_limits": {"five_hour": {"used_percentage": 100, "resets_at": 1793968800},
                                          "seven_day": {"used_percentage": 87, "resets_at": 1794013200}}}


def hook(state, stdin):
    if not isinstance(stdin, str):
        stdin = json.dumps(stdin, ensure_ascii=False)
    env = dict(os.environ, USAGE_SAVER_STATE=state)
    r = subprocess.run([sys.executable, HOOK], input=stdin, capture_output=True, text=True, env=env, timeout=15)
    return r.returncode, r.stdout, r.stderr


def rows(state, name="limit_events.jsonl"):
    p = os.path.join(state, name)
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def inst(*args):
    r = subprocess.run([sys.executable, INST, *args], capture_output=True, text=True, timeout=15)
    return r.returncode, r.stdout + r.stderr


def silent(rc, out, err):
    return rc == 0 and out == "" and err == ""


CASES = []


def case(cid, desc):
    def deco(fn):
        CASES.append((cid, desc, fn))
        return fn
    return deco


# ------------------------------------------------------------------ limit_logger
@case("L01", "StopFailure rate_limit：写 1 行，字段齐全，kind=limit_hit，无输出，exit 0")
def _(t):
    s = os.path.join(t, "state"); os.makedirs(s)
    json.dump(RATE, open(os.path.join(s, "rate.json"), "w"))
    r = hook(s, SF); rs = rows(s)
    if not (silent(*r) and len(rs) == 1):
        return False
    x = rs[0]
    return (x["event"] == "StopFailure" and x["error"] == "rate_limit" and x["kind"] == "limit_hit"
            and x["session_id"] == "s-rl" and x["cwd"] == "/Users/u/项目 a"
            and x["error_details"] == "429 Too Many Requests" and x["five_hour_pct"] == 100
            and x["seven_day_pct"] == 87 and x["rate_ts"] == 1793961649 and isinstance(x["ts"], float))


@case("L02", "StopFailure overloaded：照样记，kind=api_error（周报可区分）")
def _(t):
    s = os.path.join(t, "s")
    r = hook(s, dict(SF, error="overloaded")); rs = rows(s)
    return silent(*r) and len(rs) == 1 and rs[0]["kind"] == "api_error"


@case("L03", "Notification quota_auto_resume_fired / stale / disabled：各写 1 行，kind 不同")
def _(t):
    s = os.path.join(t, "s")
    for ty in ("quota_auto_resume_fired", "quota_auto_resume_stale", "quota_auto_resume_disabled"):
        if not silent(*hook(s, dict(NT, notification_type=ty))):
            return False
    ks = [x["kind"] for x in rows(s)]
    return ks == ["auto_resume", "auto_resume_stale", "auto_resume_disabled"] and rows(s)[0]["message"].startswith("Resuming")


@case("L04", "Notification idle_prompt（没加 matcher 时也会来）：不记")
def _(t):
    s = os.path.join(t, "s")
    return silent(*hook(s, dict(NT, notification_type="idle_prompt"))) and rows(s) == []


@case("L05", "坏输入（非 JSON / 空 / JSON 数组）：exit 0、无输出，各记一行 parse_error 或 unknown")
def _(t):
    s = os.path.join(t, "s")
    ok = all(silent(*hook(s, x)) for x in ("{not json", "", "[1,2]"))
    rs = rows(s)
    return ok and len(rs) == 3 and rs[0].get("parse_error") and rs[2].get("parse_error") and rs[1]["kind"] == "unknown"


@case("L06", "状态目录不可写（USAGE_SAVER_STATE 指向一个普通文件）：exit 0、无输出")
def _(t):
    f = os.path.join(t, "file"); open(f, "w").write("x")
    return silent(*hook(f, SF))


@case("L07", "error_details 是对象、last_assistant_message 超长：转成字符串并截断，不报错")
def _(t):
    s = os.path.join(t, "s")
    r = hook(s, dict(SF, error_details={"status": 429, "retry_after": 3600}, last_assistant_message="E" * 5000))
    x = rows(s)[0]
    return silent(*r) and json.loads(x["error_details"])["status"] == 429 and len(x["last_assistant_message"]) <= 301


@case("L08", "没有 rate.json / rate.json 损坏：百分比字段为 null，照样写")
def _(t):
    s = os.path.join(t, "s"); os.makedirs(s)
    hook(s, SF)
    open(os.path.join(s, "rate.json"), "w").write("{bad")
    r = hook(s, SF); rs = rows(s)
    return silent(*r) and len(rs) == 2 and all(x["five_hour_pct"] is None for x in rs)


@case("L09", "超过 5MB：原文件改名为 limit_events_old.jsonl（内容完整保留），新文件只有新的一行")
def _(t):
    s = os.path.join(t, "s"); os.makedirs(s)
    big = os.path.join(s, "limit_events.jsonl")
    with open(big, "w") as f:
        f.write(('{"pad": "' + "x" * 1000 + '"}\n') * 5300)
    size = os.path.getsize(big)
    r = hook(s, SF)
    old = os.path.join(s, "limit_events_old.jsonl")
    return silent(*r) and os.path.getsize(old) == size and len(rows(s)) == 1


@case("L10", "再次轮转而 _old 已存在：不覆盖旧 _old，另起带时间戳的文件名")
def _(t):
    s = os.path.join(t, "s"); os.makedirs(s)
    open(os.path.join(s, "limit_events_old.jsonl"), "w").write("FIRST-OLD\n")
    with open(os.path.join(s, "limit_events.jsonl"), "w") as f:
        f.write("y" * (5 * 1024 * 1024 + 10) + "\n")
    r = hook(s, SF)
    names = sorted(os.listdir(s))
    stamped = [n for n in names if n.startswith("limit_events_old.") and n != "limit_events_old.jsonl"]
    return (silent(*r) and open(os.path.join(s, "limit_events_old.jsonl")).read() == "FIRST-OLD\n"
            and len(stamped) == 1 and len(rows(s)) == 1)


@case("L11", "正好 5MB 不轮转（> 5MB 才轮转）")
def _(t):
    s = os.path.join(t, "s"); os.makedirs(s)
    with open(os.path.join(s, "limit_events.jsonl"), "w") as f:
        f.write("z" * (5 * 1024 * 1024 - 1) + "\n")
    hook(s, SF)
    return not os.path.exists(os.path.join(s, "limit_events_old.jsonl"))


@case("L12", "并发 20 个 hook 同时写：20 行都是完整 JSON")
def _(t):
    s = os.path.join(t, "s"); os.makedirs(s)
    env = dict(os.environ, USAGE_SAVER_STATE=s)
    ps = [subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env) for _ in range(20)]
    for i, p in enumerate(ps):
        p.communicate(json.dumps(dict(SF, session_id=f"c{i}")).encode(), timeout=15)
    rs = rows(s)
    return len(rs) == 20 and len({x["session_id"] for x in rs}) == 20


# ------------------------------------------------------------------ installer
def base_settings():
    return {"model": "opus", "theme": "dark",
            "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 /a/context_guard.py"}]}]},
            "statusLine": {"type": "command", "command": "bash /a/statusline.sh"}}


def setup(t, data):
    p = os.path.join(t, "settings.json")
    with open(p, "w") as f:
        if isinstance(data, str):
            f.write(data)
        else:
            json.dump(data, f, indent=1)
    bk = os.path.join(t, "bk"); return p, bk


def ll_groups(d, ev):
    return [g for g in d.get("hooks", {}).get(ev, []) if any("limit_logger.py" in h["command"] for h in g["hooks"])]


@case("I01", "首次安装：加 StopFailure + Notification(quota matcher)，其他键原样，备份 = 原文件，打印一行撤回命令")
def _(t):
    p, bk = setup(t, base_settings()); orig = open(p).read()
    rc, out = inst("--settings", p, "--backup-dir", bk)
    d = json.load(open(p)); b = os.listdir(bk)
    undo = [l for l in out.splitlines() if l.startswith("撤回：")]
    rest = {k: v for k, v in d.items() if k != "hooks"}
    want_rest = {k: v for k, v in base_settings().items() if k != "hooks"}
    return (rc == 0 and len(b) == 1 and open(os.path.join(bk, b[0])).read() == orig
            and len(ll_groups(d, "StopFailure")) == 1 and "matcher" not in ll_groups(d, "StopFailure")[0]
            and ll_groups(d, "Notification")[0]["matcher"] == "quota_auto_resume_fired|quota_auto_resume_stale|quota_auto_resume_disabled"
            and d["hooks"]["UserPromptSubmit"] == base_settings()["hooks"]["UserPromptSubmit"]
            and rest == want_rest and list(d) == list(base_settings())
            and len(undo) == 1 and undo[0] == f"撤回：cp {os.path.join(bk, b[0])} {p}" and "#" not in undo[0])


@case("I02", "第二次运行：不重复加、不备份、文件字节不变")
def _(t):
    p, bk = setup(t, base_settings())
    inst("--settings", p, "--backup-dir", bk)
    before = open(p, "rb").read(); nb = len(os.listdir(bk))
    rc, out = inst("--settings", p, "--backup-dir", bk)
    return rc == 0 and open(p, "rb").read() == before and len(os.listdir(bk)) == nb and "已经装过" in out


@case("I03", "已有别人的 StopFailure / Notification hook：原样保留，我们的追加在后面")
def _(t):
    s = base_settings()
    other_sf = {"matcher": "rate_limit", "hooks": [{"type": "command", "command": "/usr/local/bin/alert.sh"}]}
    other_nt = {"matcher": "idle_prompt", "hooks": [{"type": "command", "command": "say idle"}]}
    s["hooks"]["StopFailure"] = [other_sf]; s["hooks"]["Notification"] = [other_nt]
    p, bk = setup(t, s)
    rc, _o = inst("--settings", p, "--backup-dir", bk)
    d = json.load(open(p))
    return (rc == 0 and d["hooks"]["StopFailure"][0] == other_sf and len(d["hooks"]["StopFailure"]) == 2
            and d["hooks"]["Notification"][0] == other_nt and len(d["hooks"]["Notification"]) == 2)


@case("I04", "--dry-run：说明会改什么，但不写文件、不建备份")
def _(t):
    p, bk = setup(t, base_settings()); before = open(p, "rb").read()
    rc, out = inst("--settings", p, "--backup-dir", bk, "--dry-run")
    return rc == 0 and open(p, "rb").read() == before and not os.path.exists(bk) and "[dry-run]" in out


@case("I05", "settings 不是合法 JSON：exit 1、不写、不备份")
def _(t):
    p, bk = setup(t, '{"model": "opus",')
    rc, _o = inst("--settings", p, "--backup-dir", bk)
    return rc == 1 and open(p).read() == '{"model": "opus",' and not os.path.exists(bk)


@case("I06", "settings 没有 hooks 键：新建 hooks，只含我们的两条")
def _(t):
    p, bk = setup(t, {"model": "opus"})
    rc, _o = inst("--settings", p, "--backup-dir", bk)
    d = json.load(open(p))
    return rc == 0 and sorted(d["hooks"]) == ["Notification", "StopFailure"] and d["model"] == "opus"


@case("I07", "已有一半（只有 StopFailure 装了）：只补 Notification")
def _(t):
    p, bk = setup(t, base_settings())
    inst("--settings", p, "--backup-dir", bk)
    d = json.load(open(p)); del d["hooks"]["Notification"]; json.dump(d, open(p, "w"))
    rc, out = inst("--settings", p, "--backup-dir", bk)
    d = json.load(open(p))
    return rc == 0 and len(ll_groups(d, "StopFailure")) == 1 and len(ll_groups(d, "Notification")) == 1 and "StopFailure：已经装过" in out


@case("I08", "--uninstall：只删我们的，别人的 StopFailure 留着；回到安装前的结构")
def _(t):
    s = base_settings()
    other_sf = {"hooks": [{"type": "command", "command": "/usr/local/bin/alert.sh"}]}
    s["hooks"]["StopFailure"] = [other_sf]
    p, bk = setup(t, s)
    inst("--settings", p, "--backup-dir", bk)
    rc, _o = inst("--settings", p, "--backup-dir", bk, "--uninstall")
    return rc == 0 and json.load(open(p)) == s


@case("I09", "撤回命令真的能恢复：执行打印的 cp 后与安装前字节相同")
def _(t):
    p, bk = setup(t, base_settings()); before = open(p, "rb").read()
    _rc, out = inst("--settings", p, "--backup-dir", bk)
    undo = [l for l in out.splitlines() if l.startswith("撤回：")][0][len("撤回："):]
    subprocess.run(undo.split(), check=True)
    return open(p, "rb").read() == before


@case("I10", "真实 settings.json 的副本（只读复制到临时目录）：首次加 2 条，二次不变，其他键不变")
def _(t):
    if not os.path.exists(REAL_SETTINGS):
        return None
    p = os.path.join(t, "settings.json"); bk = os.path.join(t, "bk")
    with open(REAL_SETTINGS, "rb") as src, open(p, "wb") as dst:
        dst.write(src.read())
    orig = json.load(open(p))
    rc1, _ = inst("--settings", p, "--backup-dir", bk)
    after1 = open(p, "rb").read()
    rc2, _ = inst("--settings", p, "--backup-dir", bk)
    d = json.load(open(p))
    same_other = all(d.get(k) == v for k, v in orig.items() if k != "hooks")
    same_hooks = all(d["hooks"].get(ev) == v for ev, v in orig.get("hooks", {}).items()
                     if ev not in ("StopFailure", "Notification"))
    return (rc1 == 0 and rc2 == 0 and open(p, "rb").read() == after1 and same_other and same_hooks
            and len(ll_groups(d, "StopFailure")) == 1 and len(ll_groups(d, "Notification")) == 1)


@case("I11", "--uninstall：同一组里还有别人的 hook（/other/alert.sh）时只删我们的条目，组和 alert.sh 保留")
def _(t):
    s = base_settings()
    other = {"type": "command", "command": "/other/alert.sh"}
    p, bk = setup(t, s)
    inst("--settings", p, "--backup-dir", bk)
    d = json.load(open(p))
    for ev in ("StopFailure", "Notification"):
        d["hooks"][ev][0]["hooks"].insert(0, dict(other))      # 同组：别人的 + 我们的
    json.dump(d, open(p, "w"))
    rc, out = inst("--settings", p, "--backup-dir", bk, "--uninstall")
    d = json.load(open(p))
    sf, nt = d["hooks"]["StopFailure"], d["hooks"]["Notification"]
    return (rc == 0 and sf == [{"hooks": [other]}]
            and nt == [{"matcher": "quota_auto_resume_fired|quota_auto_resume_stale|quota_auto_resume_disabled", "hooks": [other]}]
            and "删掉 1 条" in out)


@case("I12", "--uninstall：组里只有我们的条目时删组；事件数组空了删事件；第二次 uninstall 不写文件")
def _(t):
    p, bk = setup(t, base_settings())
    inst("--settings", p, "--backup-dir", bk)
    inst("--settings", p, "--backup-dir", bk, "--uninstall")
    d = json.load(open(p)); before = open(p, "rb").read(); nb = len(os.listdir(bk))
    rc, _o = inst("--settings", p, "--backup-dir", bk, "--uninstall")
    return (rc == 0 and "StopFailure" not in d["hooks"] and "Notification" not in d["hooks"]
            and open(p, "rb").read() == before and len(os.listdir(bk)) == nb)


def load_hook_module():
    spec = importlib.util.spec_from_file_location("limit_logger_ut", HOOK)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


@case("L13", "复现 Codex 竞态（两次轮转都以为要轮转、锁不起作用）：_old 已占用时依次落到 .0/.1，旧日志一份不丢")
def _(t):
    m = load_hook_module()
    real_getsize = os.path.getsize
    m.os.path.getsize = lambda _p: m.MAX_BYTES + 1          # 两次都“检查到需要轮转”（m.os 就是本进程的 os，用完还原）
    try:
        return _l13_body(m, t)
    finally:
        os.path.getsize = real_getsize


def _l13_body(m, t):
    s = os.path.join(t, "s"); os.makedirs(s)
    path = os.path.join(s, "limit_events.jsonl")
    open(os.path.join(s, "limit_events_old.jsonl"), "w").write("A\n")
    open(path, "w").write("B\n"); m._rotate(path)              # 进程 A 的轮转
    open(path, "w").write("C\n"); m._rotate(path)              # 进程 B 随后把 A 新建的文件也轮转
    m._rotate(path)                                             # 原名已不存在：静默返回
    texts = sorted(open(os.path.join(s, n)).read() for n in os.listdir(s))
    return texts == ["A\n", "B\n", "C\n"] and not os.path.exists(path)


@case("L14", "真实并发：30 个 hook 同时写一个 >5MB 文件：原大文件字节完整保存在某个 _old 里，30 行都在")
def _(t):
    s = os.path.join(t, "s"); os.makedirs(s)
    big = os.path.join(s, "limit_events.jsonl")
    payload = ('{"pad": "' + "x" * 1000 + '"}\n') * 5300
    open(big, "w").write(payload)
    env = dict(os.environ, USAGE_SAVER_STATE=s)
    ps = [subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env) for _ in range(30)]
    for i, p in enumerate(ps):
        p.communicate(json.dumps(dict(SF, session_id=f"c{i}")).encode(), timeout=30)
    olds = [n for n in os.listdir(s) if n.startswith("limit_events_old")]
    whole = [n for n in olds if open(os.path.join(s, n)).read() == payload]
    sids = set()
    for n in os.listdir(s):
        if n.endswith(".jsonl"):
            for l in open(os.path.join(s, n)):
                if '"session_id"' in l:
                    sids.add(json.loads(l)["session_id"])
    return len(whole) == 1 and len(sids) == 30


# ------------------------------------------------------------------ F3
_FS_OPS = ("rename", "link", "unlink")


def _interleave(m, src, pause_after):
    """跑进程 A 的 _rotate；A 第 pause_after 次改动文件系统之后暂停，此时进程 B（真实函数）
    完成一次轮转并在原名新建 C；然后 A 继续。返回 A 实际做了几次改动。"""
    real = {k: getattr(os, k) for k in _FS_OPS}
    n = [0]

    def b_runs():
        for k, f in real.items():
            setattr(os, k, f)
        if not os.path.exists(src):
            open(src, "w").write("B-BIG\n")
        m._rotate(src)
        with open(src, "a") as f:
            f.write("C-NEW\n")

    def wrap(k):
        def w(*a):
            r = real[k](*a)
            n[0] += 1
            if n[0] == pause_after:
                b_runs()
                for kk in _FS_OPS:          # A 剩下的步骤继续被计数（但不再触发 B）
                    setattr(os, kk, wrap(kk))
            return r
        return w
    for k in _FS_OPS:
        setattr(os, k, wrap(k))
    try:
        m._rotate(src)
    finally:
        for k, f in real.items():
            setattr(os, k, f)
    return n[0]


@case("L15", "轮转交错（Codex P1）：A 在每一步之后暂停、B 轮转并新建 C；_old 已有/没有两种：A、B、C 和旧 _old 都不丢")
def _(t):
    m = load_hook_module()
    m.MAX_BYTES = 5
    for pre_old in (True, False):
        for k in (1, 2):
            s = os.path.join(t, f"s-{pre_old}-{k}"); os.makedirs(s)
            src = os.path.join(s, "limit_events.jsonl")
            if pre_old:
                open(os.path.join(s, "limit_events_old.jsonl"), "w").write("OLD0\n")
            open(src, "w").write("A-BIG\n")
            ops = _interleave(m, src, k)
            if ops < k:
                continue                     # A 没有第 k 步（_old 已存在时 link 失败就结束）
            got = sorted(open(os.path.join(s, n)).read() for n in os.listdir(s))
            want = sorted((["OLD0\n"] if pre_old else []) + ["A-BIG\n", "C-NEW\n"]
                          + (["B-BIG\n"] if "B-BIG\n" in got else []))
            if got != want or "C-NEW\n" not in got or "A-BIG\n" not in got:
                print(f"    L15 pre_old={pre_old} pause_after={k}: {got}")
                return False
    return True


@case("L16", "锁被别人一直拿着（Codex P2）：hook 2 秒内返回、exit 0、无输出、照常追加、这次不轮转")
def _(t):
    s = os.path.join(t, "s"); os.makedirs(s)
    big = os.path.join(s, "limit_events.jsonl")
    open(big, "w").write("y" * (5 * 1024 * 1024 + 10) + "\n")
    fd = os.open(os.path.join(s, "limit_events.lock"), os.O_WRONLY | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        t0 = time.monotonic()
        r = hook(s, dict(SF, session_id="locked"))
        dt = time.monotonic() - t0
    finally:
        os.close(fd)
    last = open(big).read().splitlines()[-1]
    print(f"    L16 耗时 {dt:.2f}s")
    return (silent(*r) and dt < 2.0 and json.loads(last)["session_id"] == "locked"
            and not any(n.startswith("limit_events_old") for n in os.listdir(s)))


@contextlib.contextmanager
def _inproc(m, state, stdin, fake_write):
    """在本进程跑 m.main()：stdin、USAGE_SAVER_STATE、os.write 临时替换，用完还原。"""
    old = (sys.stdin, os.environ.get("USAGE_SAVER_STATE"), os.write)
    sys.stdin = io.StringIO(json.dumps(stdin)); os.environ["USAGE_SAVER_STATE"] = state
    if fake_write:
        os.write = fake_write
    try:
        yield
    finally:
        sys.stdin, os.write = old[0], old[2]
        if old[1] is None:
            os.environ.pop("USAGE_SAVER_STATE", None)
        else:
            os.environ["USAGE_SAVER_STATE"] = old[1]


@case("L17", "短写（Codex P3）：os.write 每次只写 1/3：循环写完，文件里恰好一行完整 JSON")
def _(t):
    m = load_hook_module(); s = os.path.join(t, "s")
    real_write = os.write; calls = [0]

    def third(fd, b):
        calls[0] += 1
        return real_write(fd, bytes(b[: max(1, len(b) // 3)]))
    with _inproc(m, s, SF, third):
        m.main()
    data = open(os.path.join(s, "limit_events.jsonl")).read()
    return calls[0] >= 3 and data.count("\n") == 1 and data.endswith("\n") and json.loads(data)["error"] == "rate_limit"


@case("L18", "写到一半磁盘满（ENOSPC）：半条单独成行；下一条 hook 的记录是完整的一行")
def _(t):
    m = load_hook_module(); s = os.path.join(t, "s")
    real_write = os.write; calls = [0]

    def half_then_full(fd, b):
        calls[0] += 1
        if calls[0] == 1:
            return real_write(fd, bytes(b[: len(b) // 2]))
        if calls[0] == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(fd, b)
    with _inproc(m, s, SF, half_then_full):
        m.main()
    r = hook(s, dict(SF, session_id="next"))
    lines = open(os.path.join(s, "limit_events.jsonl")).read().split("\n")
    return silent(*r) and len(lines) == 3 and lines[2] == "" and json.loads(lines[1])["session_id"] == "next"


@case("L19", "文件末尾残留半条（上次进程被杀，没有换行）：新记录先补换行，自成一行")
def _(t):
    s = os.path.join(t, "s"); os.makedirs(s)
    open(os.path.join(s, "limit_events.jsonl"), "w").write('{"ts": 1, "half')
    r = hook(s, SF)
    lines = open(os.path.join(s, "limit_events.jsonl")).read().split("\n")
    return silent(*r) and lines[0] == '{"ts": 1, "half' and json.loads(lines[1])["kind"] == "limit_hit" and lines[2] == ""


@case("I13", "--uninstall / 安装：/other/limit_logger.py（同组和单独一组）不算我们的：卸载保留它，安装照样加我们的")
def _(t):
    other = {"type": "command", "command": "python3 /other/limit_logger.py"}
    s = base_settings(); s["hooks"]["StopFailure"] = [{"hooks": [dict(other)]}]
    p, bk = setup(t, s)
    rc1, out1 = inst("--settings", p, "--backup-dir", bk)
    d = json.load(open(p))
    added = "StopFailure：已经装过" not in out1 and len(d["hooks"]["StopFailure"]) == 2
    d["hooks"]["StopFailure"][1]["hooks"].insert(0, dict(other))      # 再在我们那组里塞一个同名别处脚本
    json.dump(d, open(p, "w"))
    rc2, out2 = inst("--settings", p, "--backup-dir", bk, "--uninstall")
    d = json.load(open(p))
    return (rc1 == 0 and rc2 == 0 and added and d["hooks"]["StopFailure"] == [{"hooks": [other]}, {"hooks": [other]}]
            and "Notification" not in d["hooks"] and "StopFailure：删掉 1 条" in out2)


@case("I14", "本套件脚本换种写法（~/…、$HOME/…、带引号）也认得：安装说已装过，卸载能删掉")
def _(t):
    home = os.path.expanduser("~")
    if not HOOK.startswith(home + os.sep):
        return None
    rel = HOOK[len(home) + 1:]
    forms = ["python3 ~/" + rel, "python3 $HOME/" + rel, 'python3 "' + HOOK + '"']
    for i, cmd in enumerate(forms):
        s = base_settings()
        s["hooks"]["StopFailure"] = [{"hooks": [{"type": "command", "command": cmd}]}]
        sub = os.path.join(t, str(i)); os.makedirs(sub)
        p, bk = setup(sub, s)
        _rc, out = inst("--settings", p, "--backup-dir", bk, "--dry-run")
        if "StopFailure：已经装过" not in out:
            print(f"    I14 安装没认出：{cmd}")
            return False
        rc, _o = inst("--settings", p, "--backup-dir", bk, "--uninstall")
        if rc != 0 or "StopFailure" in json.load(open(p))["hooks"]:
            print(f"    I14 卸载没删掉：{cmd}")
            return False
    return True


FALLBACK_USED = []


@contextlib.contextmanager
def case_dir(cid):
    """临时目录：$R4F_TMP → tempfile → 项目 state/_test_tmp（用完 mv 到 state/_to_delete）。"""
    base = os.environ.get("R4F_TMP")
    if not base and os.environ.get("R4F_FORCE_FALLBACK") != "1":
        try:
            t = tempfile.mkdtemp(prefix="r4F-")
        except Exception:
            t = None
        if t:
            try:
                yield os.path.realpath(t)
            finally:
                shutil.rmtree(t, ignore_errors=True)
            return
    if not base:
        base = os.path.join(ROOT, "state", "_test_tmp")
        FALLBACK_USED.append(cid)
    name = f"r4F-{cid}-{os.getpid()}-{time.strftime('%Y%m%d-%H%M%S')}"
    t = os.path.join(base, name)
    os.makedirs(t)
    try:
        yield t
    finally:
        trash = os.path.join(ROOT, "state", "_to_delete") if base.startswith(os.path.join(ROOT, "state")) else os.path.join(base, "_to_delete")
        os.makedirs(trash, exist_ok=True)
        os.rename(t, os.path.join(trash, name))


def main():
    res = []
    for cid, desc, fn in CASES:
        try:
            with case_dir(cid) as t:
                try:
                    ok = fn(t)
                except Exception as e:
                    ok = False; desc += f"  [异常 {type(e).__name__}: {e}]"
        except Exception as e:          # 连临时目录都建不了 / 收不走：记为失败，继续跑完并出汇总（F3）
            ok = False; desc += f"  [临时目录不可用 {type(e).__name__}: {e}]"
        tag = "跳过" if ok is None else ("通过" if ok else "失败")
        res.append(tag)
        print(f"{tag}  {cid}  {desc}")
    if FALLBACK_USED:
        print(f"（tempfile 不可用或被强制退回：{len(FALLBACK_USED)} 个用例用了 state/_test_tmp，已 mv 到 state/_to_delete/）")
    print(f"\n合计 {len(res)}：通过 {res.count('通过')}，失败 {res.count('失败')}，跳过 {res.count('跳过')}")
    return 1 if "失败" in res else 0


if __name__ == "__main__":
    sys.exit(main())
