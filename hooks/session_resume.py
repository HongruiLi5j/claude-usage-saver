#!/usr/bin/env python3
"""/clear 后自动接续（任何对话都适用，不依赖交接文件）。

同一个脚本挂两个事件：
- SessionEnd（reason=clear）：把旧对话的 transcript 路径记到 state/last_clear/<cwd 哈希>.json。
- SessionStart（source=clear）：注入「交接文件（如有）＋旧对话摘要」。
  摘要从旧 transcript 提取：标题、所有者最近的消息、Claude 最后几段回复、改过的文件、最后的工具调用。

找交接文件：从 cwd 往上找，不越过项目根（.git 或 ~/Documents/Projects 下第一层）；
cwd 不在具体项目里时，退回 Projects 下 FALLBACK_HOURS 小时内最新的一份。
任何异常一律放行（exit 0、不输出）。
"""
import json, sys, os, time, re, hashlib, glob

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, "Documents", "Projects")
NAMES = ("HANDOFF.md", os.path.join(".claude", "handoff.md"))
FALLBACK_HOURS = 12
SKIP_DIRS = {"_to_delete", "node_modules", ".git", "backups", ".venv"}
STATE = os.environ.get("USAGE_SAVER_STATE") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
PTR_DIR = os.path.join(STATE, "last_clear")
PTR_MAX_AGE = 30 * 60          # 指针超过 30 分钟视为无效
TOTAL_LIMIT = 9500             # additionalContext 上限 1 万字符，留余量
TAIL_BYTES = 3_000_000         # 只读旧 transcript 尾部


# ---------- 交接文件 ----------
def is_project_root(d):
    return os.path.isdir(os.path.join(d, ".git")) or os.path.dirname(d) == PROJECTS


def find_up(cwd):
    d = os.path.abspath(cwd or HOME)
    if d == HOME or not d.startswith(HOME + os.sep):
        return None
    while True:
        for n in NAMES:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
        if is_project_root(d) or d in (HOME, PROJECTS, "/"):
            return None
        d = os.path.dirname(d)


def find_recent():
    if not os.path.isdir(PROJECTS):
        return None
    cands = []
    for sub in [PROJECTS] + [os.path.join(PROJECTS, x) for x in sorted(os.listdir(PROJECTS))
                             if x not in SKIP_DIRS and os.path.isdir(os.path.join(PROJECTS, x))]:
        for n in NAMES:
            p = os.path.join(sub, n)
            if os.path.isfile(p):
                cands.append((os.path.getmtime(p), p))
    if not cands:
        return None
    mt, p = max(cands)
    return p if time.time() - mt <= FALLBACK_HOURS * 3600 else None


def age_text(sec):
    h = sec / 3600
    return f"{sec / 60:.0f} 分钟前" if h < 1 else (f"{h:.1f} 小时前" if h < 48 else f"{h / 24:.0f} 天前")


# ---------- 旧 transcript ----------
def ptr_path(cwd):
    return os.path.join(PTR_DIR, hashlib.sha1(os.path.abspath(cwd or HOME).encode()).hexdigest()[:16] + ".json")


def prev_transcript(data):
    """优先用 SessionEnd 记下的指针；否则同目录下除当前会话外最新的 .jsonl（30 分钟内）。"""
    cwd, cur = data.get("cwd"), data.get("session_id")
    try:
        ptr = json.load(open(ptr_path(cwd)))
        if time.time() - ptr.get("ts", 0) <= PTR_MAX_AGE and os.path.isfile(ptr.get("transcript_path", "")) \
                and ptr.get("session_id") != cur:
            return ptr["transcript_path"]
    except Exception:
        pass
    tp = data.get("transcript_path") or ""
    d = os.path.dirname(tp)
    if not os.path.isdir(d):
        return None
    cands = [(os.path.getmtime(f), f) for f in glob.glob(os.path.join(d, "*.jsonl"))
             if os.path.basename(f) != f"{cur}.jsonl" and os.path.abspath(f) != os.path.abspath(tp)]
    if not cands:
        return None
    mt, f = max(cands)
    return f if time.time() - mt <= PTR_MAX_AGE else None


NOISE = re.compile(r"<(system-reminder|local-command-[a-z]+|command-[a-z]+|task-notification|pasted_content[^>]*)>.*?</\1[^>]*>", re.S)


def clean(s):
    s = NOISE.sub("", s or "")
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def clip(s, n):
    s = s.strip()
    return s if len(s) <= n else s[:n] + "…"


def digest(path, limit):
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if size > TAIL_BYTES:
            fh.seek(size - TAIL_BYTES)
            fh.readline()
        lines = fh.read().decode("utf-8", "ignore").splitlines()
    title, users, texts, files, last_tool, first_ts, last_ts = None, [], [], [], None, None, None
    for ln in lines:
        try:
            d = json.loads(ln)
        except Exception:
            continue
        t = d.get("type")
        if t == "ai-title":
            title = d.get("aiTitle") or title
            continue
        if t not in ("user", "assistant") or d.get("isSidechain"):
            continue
        ts = d.get("timestamp")
        first_ts = first_ts or ts
        last_ts = ts or last_ts
        msg = d.get("message") or {}
        content = msg.get("content")
        if t == "user":
            if d.get("isMeta"):
                continue
            if isinstance(content, str):
                s = clean(content)
            elif isinstance(content, list):
                s = clean("\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"))
            else:
                s = ""
            if s:
                users.append(s)
        else:
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    texts.append(b["text"])
                elif b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    fp = inp.get("file_path") or inp.get("notebook_path")
                    if b.get("name") in ("Edit", "Write", "NotebookEdit", "MultiEdit") and fp:
                        if fp in files:
                            files.remove(fp)
                        files.append(fp)
                    desc = inp.get("description") or inp.get("command") or fp or ""
                    last_tool = f"{b.get('name')}: {clip(str(desc), 160)}"
    if not users and not texts:
        return None
    parts = [f"## 上一段对话摘要（自动提取自 {path}）"]
    if title:
        parts.append(f"标题：{title}")
    if first_ts or last_ts:
        parts.append(f"时间：{first_ts} → {last_ts}")
    if files:
        parts.append("改过的文件（最近在后）：\n" + "\n".join(f"- {f}" for f in files[-12:]))
    if last_tool:
        parts.append(f"最后一个工具调用：{last_tool}")
    head = "\n".join(parts)
    # 剩余预算：所有者消息 45%，Claude 回复 55%（都取最近的）
    budget = max(limit - len(head) - 200, 600)
    ub, ab = int(budget * 0.45), int(budget * 0.55)
    u_out, used = [], 0
    for s in reversed(users[-15:]):
        item = "- " + clip(s, 400).replace("\n", " ")
        if used + len(item) > ub:
            break
        u_out.insert(0, item)
        used += len(item)
    a_out, used = [], 0
    for s in reversed(texts[-10:]):
        item = clip(clean(s), 1200)
        if used + len(item) > ab:
            item = clip(item, max(ab - used, 0))
        if not item:
            break
        a_out.insert(0, item)
        used += len(item)
        if used >= ab:
            break
    out = head
    if u_out:
        out += "\n\n所有者最近的消息（旧→新）：\n" + "\n".join(u_out)
    if a_out:
        out += "\n\nClaude 最后的回复（旧→新）：\n" + "\n---\n".join(a_out)
    return clip(out, limit)


# ---------- 事件处理 ----------
def on_end(data):
    if data.get("reason") != "clear":
        return
    tp = data.get("transcript_path")
    if not tp or not os.path.isfile(tp):
        return
    os.makedirs(PTR_DIR, exist_ok=True)
    p = ptr_path(data.get("cwd"))
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w") as fh:
        json.dump({"ts": time.time(), "transcript_path": tp, "session_id": data.get("session_id"),
                   "cwd": data.get("cwd")}, fh)
    os.replace(tmp, p)


def on_start(data):
    if data.get("source") != "clear":
        return
    cwd = data.get("cwd")
    hp = find_up(cwd)
    how = "当前目录所在项目"
    in_project = os.path.abspath(cwd or HOME).startswith(PROJECTS + os.sep)
    if not hp and not in_project:
        hp = find_recent()
        how = f"当前目录（{cwd}）没有交接文件，改用 Projects 下最近 {FALLBACK_HOURS} 小时内更新的一份"
    tp = prev_transcript(data)
    # 交接文件是否在上一段对话期间写的（更可信）
    fresh = False
    if hp and tp:
        try:
            fresh = os.path.getmtime(hp) >= os.path.getctime(tp) - 60 or time.time() - os.path.getmtime(hp) < 3 * 3600
        except Exception:
            pass
    sections = []
    if hp:
        h_limit = 6000 if tp else 8500
        if tp and not fresh:
            h_limit = 2500
        txt = open(hp, encoding="utf-8", errors="ignore").read()
        if len(txt) > h_limit:
            txt = txt[:h_limit] + "\n…（已截断，完整内容请读文件）"
        age = age_text(time.time() - os.path.getmtime(hp))
        note = "" if (fresh or not tp) else "（注意：这份交接不是上一段对话写的，可能过期；以下方对话摘要为准）"
        sections.append(f"## 交接文件 {hp}（来源：{how}；最后修改：{age}）{note}\n\n{txt}")
    if tp:
        used = sum(len(s) for s in sections)
        dg = digest(tp, max(TOTAL_LIMIT - used - 700, 1500))
        if dg:
            sections.append(dg)
    if not sections:
        return
    msg = ("[自动接续] 所有者刚执行了 /clear。下面是 clear 之前的进度。所有者的下一条消息如果很短或含糊"
           "（例如「1」「继续」「接着做」「ok」），就视为「接着做」：用 2–3 句复述目标和下一步，然后直接开始做，"
           "不要反问做什么，也不要重新探索整个项目；需要细节时读交接文件或上一段 transcript 的尾部。"
           "只有当下一条消息明显是一个新问题时，才按新问题处理。\n\n" + "\n\n".join(sections))
    msg = clip(msg, TOTAL_LIMIT)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}},
                     ensure_ascii=False))


try:
    data = json.load(sys.stdin)
    ev = data.get("hook_event_name")
    if ev == "SessionEnd":
        on_end(data)
    else:
        on_start(data)
except Exception:
    pass
sys.exit(0)
