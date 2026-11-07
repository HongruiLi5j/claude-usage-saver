#!/usr/bin/env python3
"""节约表（第 6 轮 P）：边用边记「套件各机制省下的 usage」，给状态栏显示总节约百分比。

只把**有直接反事实**的机制算进总数，按「逐次事件」记：
  test_filter：测试输出被截掉的 token ×（截掉后第一次请求的缓存写入价 + 之后每次请求的缓存读价；
               那次请求本身是冷缓存重写时按写入价），直到本会话 compact 或 /clear（新 transcript）为止。
               反事实 = 不装 test_filter 时 Claude Code 自己会给模型看的量：日志 ≤ CC_PERSIST_CHARS 字符时是全文，
               更长时 CC 会把输出存文件、只给约 CC_PREVIEW_CHARS 字符的预览（本机 transcript 实测）。
               所以截掉的量可能是负数（过滤结果比 CC 的预览还长、或短日志多了一行路径提示），照实记。
不计入总数、只单独列「估计区间」的：
  context_guard 提醒后的 /clear：区间 [0, 上限]。上限 = clear 前的上下文 − clear 后首个请求的上下文，
               在 clear 后每次请求按缓存读价（距上次请求超过 TTL 按写入价）累加。下限 0：所有者可能本来就会 clear，
               也没扣交接本身的开销。F2 实测长会话这一项毛估明显高于净效果（见 reports/round6_P.md 校准表）。
  设置类（第 6 轮 P2，SETTING_SAVINGS 登记表）：经过实测的「关掉某个设置 → 每次请求少带 Δ token 固定前缀」。
               现在登记了 artifact_off（enableArtifact=false，Δ 主 13,421 / 子 13,140，reports/round5_A.md 实测）。
               判断依据是 transcript 里 Claude Code 自己记的 api-request-shape（真正发给模型的工具列表），逐条请求判断；
               节省 = Δ × 该请求的单价（前缀被写进缓存的请求按写入价 1.25×/2×，前缀命中缓存的按读价，没用缓存的按输入价）。
不记：提前压缩（autoCompactWindow）、model-router、session_resume、read_guard（没装）——反事实无法从记录里得到。

总节约% = 节省 / (实际 + 节省)，都用 Sonnet 5.5 当量 token（tools/pricing.py；Opus 5.5 = 2× Sonnet 5.5）。
实际成本 = transcript 里每条 assistant usage（按 message.id 去重），含 subagents/ 下的子 agent。

状态（都在 state/ 下）：
  savings/<session>.json     每个会话的增量读取进度（文件偏移、未结束的事件）——只有本会话的状态栏写，带会话锁
  savings_totals.json        各会话合计（显示用），短暂持全局锁
  savings.jsonl              逐事件记录（追加；同一 event_id 的新行带更大的 rev，读的人取最大 rev）。超过上限时在锁内压缩成每个 event 一行
                             P2：rev = 毫秒时间戳（同一毫秒内再 +1），进度文件丢了重建也比旧行大；值有任何变化（按写入精度 0.1）就写，
                             账本和显示一致。解析还没追上（pending）时不写，免得半截的值盖掉完整的旧值。
锁拿不到（别的进程正在写）就不更新、直接显示上次的结果；任何异常都返回 None，状态栏照常输出。
P2 补写：每次刷新顺带（最多每 SWEEP_EVERY 秒一次、最多 SWEEP_BUDGET_S）检查最近见过的其它会话，
  transcript 变长了 / 上次没追上 / 进度文件版本旧了，就替它补算并写账本（会话结束后最后几次请求也能进账）。
  另有 `--session-end`（读 SessionEnd hook 的 stdin）可以在会话结束时补写；挂不挂由所有者决定（要改 settings.json）。
"""
import collections, hashlib, json, os, re, sys, time

try:
    import fcntl
except Exception:                     # 非 POSIX：不加锁就不写（只显示）
    fcntl = None

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(HERE)
VERSION = 2                           # P2：进度文件加了设置类状态；旧版本的进度会被重算
CC_PERSIST_CHARS = int(os.environ.get("USAGE_SAVER_CC_PERSIST_CHARS") or 30000)
CC_PREVIEW_CHARS = int(os.environ.get("USAGE_SAVER_CC_PREVIEW_CHARS") or 2100)
BUDGET_S = 0.045                      # 每次状态栏刷新最多花在解析上的时间；没读完的下次接着读
LOCK_WAIT_S = 0.02
JSONL_MAX = 4 * 1024 * 1024           # savings.jsonl 超过就压缩
LOG_READ_MAX = 4 * 1024 * 1024        # 读测试日志最多这么多字节（再长按比例外推）
KEYS = ('"usage"', "测试输出写入", "SessionStart:clear", "[上下文守卫]", "compact_boundary", '"api-request-shape"')
MARK = re.compile(r"\[测试输出写入 ([^\]\s]+)\]")
FILTERED = "[测试输出已过滤"
TTL = {"5m": 300, "1h": 3600}
SWEEP_EVERY = 30                      # 补写检查的最小间隔（秒）
SWEEP_BUDGET_S = 0.012                # 每次补写最多花的时间；没做完下次接着做
SWEEP_MAX_AGE = 3 * 86400             # 只替最近 3 天里见过的会话补写
PREFIX_HIT_MIN = 4096                 # 一次请求的 cache_read 至少这么多，才认为固定前缀（工具定义+系统提示）是从缓存读的
                                      # （本机 5 天 26.7k 次请求：cache_read 要么 0，要么 ≥4096 的占 99.98%）

# ------------------------------------------------------------------ 设置类节省：登记表（通用接口）
def _tools_absent_detector(tool, main_needs=("AskUserQuestion",), sub_needs=("Write", "Edit")):
    """生成一个判断函数：这次发给模型的工具列表里**没有** tool，而且看得出「本来会有」它。
    - 主会话：要有 main_needs 里的工具（交互式才有 AskUserQuestion；`claude -p` 本来就没有 Artifact，不能算成省的）；
    - 子 agent：要有 sub_needs 里的工具（Explore/Plan 这类只读 agent 的工具表本来就不含 Artifact，不能算）。"""
    def detect(names, sys_chars, is_sub):
        if tool in names:
            return False
        return all(x in names for x in (sub_needs if is_sub else main_needs))
    return detect


# 每一项：一个经过实测的「设置类」节省。要接入新的设置，往这里加一项（或调用 register_setting）即可：
#   id          唯一名字（写进账本的 event_id 和 mechanism = "setting:<id>"）
#   delta_main  主会话每次请求少带的 token（实测）；delta_sub 子 agent 的
#   since       从什么时候起才算（ISO 时间，UTC）；之前的请求即使工具表对得上也不算
#   detect      detect(工具名集合, 系统提示字符数, 是否子 agent) → True 表示这次请求的形状说明该设置在起作用
#   source      Δ 的出处（必须是实测）
# Δ、since 可以在 config.json 的 "savings_meter": {"settings": {"<id>": {"delta_main": …, "delta_sub": …, "since": …, "enabled": false}}} 里覆盖。
# 解析时只记「每 token 的单价之和」（与 Δ 无关），显示时才乘 Δ，所以改 Δ 立即生效；改 since / enabled 会自动重算（进度里记了设置指纹 sfp），不再产生的记录追加撤销行；改 detect 规则要升 VERSION。
SETTING_SAVINGS = [
    {"id": "artifact_off",
     "label": "关掉 Artifact（enableArtifact=false）",
     "delta_main": 13421, "delta_sub": 13140,
     "since": "2026-11-07T07:42:00Z",          # 所有者 2026-11-06 23:42（PST）在真实 settings 里设的（settings.json 修改时间）
     "detect": _tools_absent_detector("Artifact"),
     "source": "reports/round5_A.md §1：交互式探针首请求实测，Opus 主 −13,421 / 子 −13,140（Sonnet 主 −13,486）"},
]
_settings_cache = None


def register_setting(entry):
    """接入一个新的设置类节省（字段见 SETTING_SAVINGS 的说明）。同 id 的会被替换。"""
    global _settings_cache
    need = ("id", "delta_main", "delta_sub", "detect", "source")
    if not all(k in entry for k in need) or not callable(entry["detect"]):
        raise ValueError("设置项缺字段：%s" % [k for k in need if k not in entry])
    SETTING_SAVINGS[:] = [e for e in SETTING_SAVINGS if e["id"] != entry["id"]] + [entry]
    _settings_cache = None


def settings_fp():
    """P3：影响解析结果的设置状态（哪些设置启用 + 各自的 since）。变了 → 进度重算，账本里消失的记录追加撤销行。
    Δ 不在里面（显示时才乘，改了立即生效）。"""
    return json.dumps(sorted((k, e.get("since")) for k, e in settings().items()), ensure_ascii=False)


def settings():
    """→ {id: 设置项}（已合并 config.json 的覆盖、算好 since 的时间戳）。"""
    global _settings_cache
    if _settings_cache is None:
        over = {}
        try:
            with open(os.path.join(SUITE, "config.json")) as f:
                over = ((json.load(f).get("savings_meter") or {}).get("settings") or {})
        except Exception:
            over = {}
        out = {}
        for e in SETTING_SAVINGS:
            e = dict(e)
            o = over.get(e["id"]) if isinstance(over.get(e["id"]), dict) else {}
            for k in ("delta_main", "delta_sub", "since", "enabled"):
                if k in o:
                    e[k] = o[k]
            if e.get("enabled") is False:
                continue
            e["_since_ts"] = _ts(e["since"]) if e.get("since") else None
            out[e["id"]] = e
        _settings_cache = out
    return _settings_cache


_pricing = None


def pricing():
    """按本文件位置加载 tools/pricing.py（用独立模块名，不污染 sys.path）。缺失时抛异常，由调用方降级。"""
    global _pricing
    if _pricing is None:
        import importlib.util
        p = os.path.join(SUITE, "tools", "pricing.py")
        spec = importlib.util.spec_from_file_location("usaver_pricing", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _pricing = m
    return _pricing


# ------------------------------------------------------------------ 小工具
def est_tokens(text):
    """字符数 → token 估计：ASCII 约 3.5 字符/token，其他字符（中文等）约 1 token/字符。误差约 ±25%。"""
    if not text:
        return 0.0
    n = len(text)
    a = len(text.encode("ascii", "ignore"))
    return a / 3.5 + (n - a)


def _ts(s):
    try:
        import datetime as dt
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _atomic(path, obj):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _load(path, default):
    try:
        with open(path) as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


class Lock:
    """flock 文件锁，等待最多 wait 秒；拿不到 → ok=False（调用方跳过写入）。"""

    def __init__(self, path, wait=LOCK_WAIT_S):
        self.path, self.wait, self.fd, self.ok = path, wait, None, False

    def __enter__(self):
        if fcntl is None:
            return self
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
            end = time.time() + self.wait
            while True:
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.ok = True
                    break
                except OSError:
                    if time.time() >= end:
                        break
                    time.sleep(0.002)
        except Exception:
            self.ok = False
        return self

    def __exit__(self, *a):
        if self.fd is not None:
            try:
                os.close(self.fd)          # 关闭即释放锁
            except Exception:
                pass
        return False


def _tool_result_text(b):
    t = b.get("content")
    if isinstance(t, list):
        t = "".join(x.get("text", "") for x in t if isinstance(x, dict) and isinstance(x.get("text"), str))
    return t if isinstance(t, str) else None


def _read_log(path):
    """测试日志 → (反事实下 CC 会给模型看的文本的 token 估计, 方法说明, 置信度)。日志缺失返回 None。"""
    cands = [path, os.path.join(os.path.dirname(path), "_old", os.path.basename(path))]   # test_filter 7 天后移到 _old/
    for p in cands:
        try:
            size = os.path.getsize(p)
            with open(p, "rb") as f:
                raw = f.read(LOG_READ_MAX)
            txt = raw.decode("utf-8", "replace")
            chars = len(txt) * (size / max(len(raw), 1))
            if chars <= CC_PERSIST_CHARS:
                return est_tokens(txt) * (size / max(len(raw), 1)), "全文", "中"
            return est_tokens(txt[:CC_PREVIEW_CHARS]), "CC 预览", "中"
        except Exception:
            continue
    return None


# ------------------------------------------------------------------ 单个 transcript 的增量解析
def new_fstate():
    return {"off": 0, "ids": [], "actual": 0.0, "n_req": 0, "flags": {}, "items": {}, "open": {},
            "last_ctx": 0, "prev_ts": None, "ttl": "5m", "guard_seen": False, "started": False,
            "start_ts": None, "glink": None, "guard_hi": 0.0, "events": 0,
            "shape": {}, "set": {}}                       # P2：shape = 每条链当前起作用的设置 id；set = 各设置的单价累计


def _chain(fs, j):
    """同一个 transcript 里的哪条对话链：子 agent 文件整个是一条链；主 transcript 里旧格式的 sidechain 行按 agentId 分开。"""
    if fs.get("sub"):
        return "sub"
    return j.get("agentId") if j.get("isSidechain") else "main"


def _req_ttl(p):
    if p["cw1h"] > p["cw5m"]:
        return "1h"
    if p["cw5m"] > 0:
        return "5m"
    return None


def _on_request(fs, j, m, u, P):
    p = P.parts(u)
    if not any(p.values()):
        return                                            # API 报错时写的全 0 usage
    model = m.get("model") or ""
    prompt = p["inp"] + p["cw5m"] + p["cw1h"] + p["cr"]
    w, flag = P.weighted(model, u)
    fs["actual"] += w
    fs["n_req"] += 1
    if flag:
        fs["flags"][flag] = fs["flags"].get(flag, 0) + 1
    ttl = _req_ttl(p) or fs["ttl"]
    fs["ttl"] = ttl
    cw_kind = "cw1h" if ttl == "1h" else "cw5m"
    cold = (p["cw5m"] + p["cw1h"]) > p["cr"]              # 这次请求大半前缀是重写的：缓存冷了
    chain = _chain(fs, j)
    ts = _ts(j.get("timestamp"))
    act = fs.setdefault("shape", {}).get(chain)
    if act:
        _on_setting_request(fs, act, chain, p, prompt, model, ts, P)
    # test_filter 事件：截掉的 token 在这次请求里本该怎么计价
    for eid in fs["open"].get(chain, []):
        it = fs["items"].get(eid)
        if not it:
            continue
        kind = cw_kind if (it["fresh"] or cold) else "cr"
        it["saved"] += it["tok"] * P.per_token(model, kind, prompt)
        it["fresh"] = False
        it["n"] += 1
    if chain == "main":
        g = fs.get("glink")
        if g is not None:
            if g.get("D") is None:
                g["D"] = max(0.0, float(g.get("pred_ctx") or 0) - prompt)
                prev = g.get("pred_ts")
                # clear 带来的额外开销（只算能直接看到的一项）：新会话首个请求把新前缀整段写进缓存
                g["rewrite"] = p["cw5m"] * P.per_token(model, "cw5m", prompt) + p["cw1h"] * P.per_token(model, "cw1h", prompt)
            else:
                prev = fs["prev_ts"]
            gap = (ts - prev) if (ts and prev) else 0
            kind = cw_kind if gap > TTL.get(ttl, 300) else "cr"     # 反事实（没 clear）里，空闲超过 TTL 就要重写
            fs["guard_hi"] += g["D"] * P.per_token(model, kind, prompt)
        fs["last_ctx"] = prompt
        if ts:
            fs["prev_ts"] = ts
    fs["started"] = True


def prefix_kind(p):
    """固定前缀（工具定义 + 系统提示，在缓存顺序的最前面）这次请求是怎么计价的。
    cache_read ≥ PREFIX_HIT_MIN → 前缀是读的（缓存读价）；否则有缓存写入 → 前缀是写的（按这次的 TTL：1h 档 2×、5m 档 1.25×）；
    都没有 → 没用缓存（输入价）。注意：一般的热请求也有少量 cache_creation（新消息），但前缀本身是读的，不能按写入算。"""
    if p["cr"] >= PREFIX_HIT_MIN:
        return "cr"
    if p["cw5m"] + p["cw1h"] > 0:
        return "cw1h" if p["cw1h"] > p["cw5m"] else "cw5m"
    return "inp"


def _on_setting_request(fs, act, chain, p, prompt, model, ts, P):
    """设置类：这次请求在反事实里会多带 Δ token 的前缀，按这次请求自己的计价方式记「每 token 单价」（乘 Δ 在 summarize 里做）。"""
    reg = settings()
    is_sub = chain != "main"
    kind = prefix_kind(p)
    for sid_ in act:
        e = reg.get(sid_)
        if not e:
            continue
        if e["_since_ts"] is not None and (ts is None or ts < e["_since_ts"]):
            continue                                      # 生效时间之前（或请求没时间戳）：不算
        d = float(e["delta_sub"] if is_sub else e["delta_main"])
        acc = fs["set"].setdefault(sid_, {"w": 0.0, "w_sub": 0.0, "n": 0, "n_sub": 0, "n_write": 0, "n_inp": 0})
        per = P.per_token(model, kind, prompt + d)
        if is_sub:
            acc["w_sub"] += per; acc["n_sub"] += 1
        else:
            acc["w"] += per; acc["n"] += 1
        if kind in ("cw5m", "cw1h"):
            acc["n_write"] += 1
        elif kind == "inp":
            acc["n_inp"] += 1


def _on_shape(fs, j):
    """api-request-shape（Claude Code 在发给模型的工具表/系统提示变化时记一行）→ 这条链当前有哪些设置在起作用。"""
    sh = j.get("shape")
    if not isinstance(sh, dict) or not isinstance(sh.get("tools"), list):
        return
    names = {t.get("name") for t in sh["tools"] if isinstance(t, dict)}
    sysb = sh.get("system") if isinstance(sh.get("system"), list) else []
    sys_chars = sum(len(b.get("text") or "") for b in sysb if isinstance(b, dict))
    chain = "sub" if fs.get("sub") else (j.get("agentId") or "main")
    is_sub = chain != "main"
    act = []
    for e in settings().values():
        try:
            if e["detect"](names, sys_chars, is_sub):
                act.append(e["id"])
        except Exception:
            continue
    fs.setdefault("shape", {})[chain] = act


def _on_tool_results(fs, j, sid, cwd):
    c = (j.get("message") or {}).get("content")
    if not isinstance(c, list):
        return []
    out = []
    chain = _chain(fs, j)
    for b in c:
        if not isinstance(b, dict) or b.get("type") != "tool_result":
            continue
        t = _tool_result_text(b)
        if not t:
            continue
        m = MARK.match(t.lstrip())
        if not m:
            continue
        tuid = b.get("tool_use_id") or j.get("uuid") or ""
        eid = "tf:" + hashlib.sha1(("%s|%s" % (sid, tuid)).encode()).hexdigest()[:16]
        if eid in fs["items"]:
            continue                                      # 重复行（同一个 tool_use_id）：去重
        shown = est_tokens(t)
        cf = _read_log(m.group(1))
        if cf is None:                                    # 日志找不到：按「完整 N 行」× 显示部分的平均行长外推，置信度低
            mm = re.search(r"完整 (\d+) 行", t)
            lines = max(t.count("\n"), 1)
            cf_tok = shown * (int(mm.group(1)) / lines) if mm else shown
            cf = (min(cf_tok, est_tokens("x" * CC_PERSIST_CHARS)), "日志缺失，按行数外推", "低")
        tok = cf[0] - shown
        fs["items"][eid] = {"tok": tok, "saved": 0.0, "fresh": True, "n": 0, "ts": j.get("timestamp"),
                            "method": "test_filter 截掉 %.0f token（反事实=%s；%s）" % (tok, cf[1], "已过滤" if FILTERED in t else "短日志未过滤"),
                            "conf": cf[2], "closed": False}
        fs["open"].setdefault(chain, []).append(eid)
        fs["events"] += 1
        out.append(eid)
    return out


def _link_clear(fs, path, j):
    """本 transcript 是 /clear 之后的新会话：找同目录里 clear 之前的那个会话，看它有没有被守卫提醒过。"""
    start = _ts(j.get("timestamp")) or fs.get("start_ts") or time.time()
    d = os.path.dirname(path)
    try:
        cands = []
        for e in os.scandir(d):
            if e.name.endswith(".jsonl") and e.path != path:
                mt = e.stat().st_mtime
                if mt <= start + 5:
                    cands.append((mt, e.path))
        cands.sort(reverse=True)
    except Exception:
        return
    for _, p in cands[:3]:
        info = _tail_info(p)
        if info and info["last_ts"] and info["last_ts"] <= start + 5:
            if info["guard"]:
                fs["glink"] = {"pred": os.path.basename(p), "pred_ctx": info["ctx"], "pred_ts": info["last_ts"], "D": None}
            return


def _tail_info(p, size=2 * 1024 * 1024):
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            end = f.tell()
            f.seek(max(0, end - size))
            data = f.read().decode("utf-8", "replace")
    except Exception:
        return None
    ctx, last_ts, guard = 0, None, False
    for l in data.splitlines():
        if "[上下文守卫]" in l and "hook_additional_context" in l:
            guard = True
        if '"usage"' not in l:
            continue
        try:
            j = json.loads(l)
        except Exception:
            continue
        if j.get("type") != "assistant" or j.get("isSidechain"):
            continue
        u = (j.get("message") or {}).get("usage") or {}
        n = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        if n > 0:
            ctx, last_ts = n, _ts(j.get("timestamp")) or last_ts
    return {"ctx": ctx, "last_ts": last_ts, "guard": guard}


def scan_file(fs, path, sid, cwd, deadline, P):
    """从 fs['off'] 读到文件尾（或到 deadline）。只推进到最后一个完整行。返回 (是否读完, 新事件 id 列表)。"""
    new = []
    try:
        size = os.path.getsize(path)
    except Exception:
        return True, new
    if size < fs["off"]:                                  # 文件被截短/替换：从头重算
        fs.clear(); fs.update(new_fstate())
    if size == fs["off"]:
        return True, new
    ids = collections.deque(fs["ids"], maxlen=64)
    fs["sub"] = "/subagents/" in path
    done = True
    with open(path, "rb") as f:
        f.seek(fs["off"])
        while True:
            if time.time() > deadline:
                done = False
                break
            chunk = f.read(1 << 20)
            if not chunk:
                break
            cut = chunk.rfind(b"\n")
            if cut < 0:
                if len(chunk) < (1 << 20):
                    break                                 # 最后一行还没写完：下次再读
                # 超长行（>1MB）：读到行尾
                rest = f.readline()
                if not rest.endswith(b"\n"):
                    break
                chunk += rest
                cut = len(chunk) - 1
            else:
                f.seek(fs["off"] + cut + 1)
            body = chunk[:cut + 1]
            fs["off"] += len(body)
            for raw in body.split(b"\n"):
                if not raw:
                    continue
                try:
                    line = raw.decode("utf-8")
                except Exception:
                    line = raw.decode("utf-8", "replace")
                if not any(k in line for k in KEYS):
                    if not fs["started"] and fs["start_ts"] is None and '"timestamp"' in line:
                        mm = re.search(r'"timestamp":\s*"([^"]+)"', line)
                        fs["start_ts"] = _ts(mm.group(1)) if mm else None
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                if not isinstance(j, dict):
                    continue
                typ = j.get("type")
                if typ == "assistant":
                    m = j.get("message") or {}
                    u = m.get("usage") if isinstance(m, dict) else None
                    mid = m.get("id") if isinstance(m, dict) else None
                    if not isinstance(u, dict) or (mid and mid in ids):
                        continue
                    if mid:
                        ids.append(mid)
                    _on_request(fs, j, m, u, P)
                elif typ == "user" and "测试输出写入" in line:
                    new += _on_tool_results(fs, j, sid, cwd)
                elif typ == "attachment":
                    a = j.get("attachment") or {}
                    if a.get("hookName") == "SessionStart:clear" and not fs["started"] and fs.get("glink") is None:
                        _link_clear(fs, path, j)
                    elif a.get("type") == "hook_additional_context" and "[上下文守卫]" in line:
                        fs["guard_seen"] = True
                elif typ == "api-request-shape":
                    _on_shape(fs, j)
                elif typ == "system" and j.get("subtype") == "compact_boundary":
                    chain = _chain(fs, j)
                    for eid in fs["open"].pop(chain, []):
                        if eid in fs["items"]:
                            fs["items"][eid]["closed"] = True
                    if chain == "main" and fs.get("glink"):
                        fs["glink"]["closed"] = True
                        fs["glink"] = dict(fs["glink"], D=0.0)   # 压缩之后两条时间线的上下文差不再可估：不再累加
    fs["ids"] = list(ids)
    return done, new


def session_files(tp, sid):
    """主 transcript + 同名目录 subagents/ 下的子 agent transcript。"""
    out = [tp]
    sub = os.path.join(os.path.splitext(tp)[0], "subagents")
    try:
        for e in os.scandir(sub):
            if e.name.endswith(".jsonl"):
                out.append(e.path)
    except Exception:
        pass
    return out


def summarize(sess):
    """会话状态 → 合计。saved 含 test_filter 和设置类；by_mech 是分项。"""
    actual = saved = ghi = grw = 0.0
    flags, ev = {}, 0
    by = {"test_filter": 0.0}
    sets = {}
    reg = settings()
    for fs in sess["files"].values():
        for sid_, acc in (fs.get("set") or {}).items():
            e = reg.get(sid_)
            if not e:
                continue
            v = acc["w"] * float(e["delta_main"]) + acc["w_sub"] * float(e["delta_sub"])
            t = sets.setdefault(sid_, {"saved": 0.0, "n": 0, "n_sub": 0, "n_write": 0, "n_inp": 0})
            t["saved"] += v
            for k in ("n", "n_sub", "n_write", "n_inp"):
                t[k] += int(acc.get(k) or 0)
            saved += v
            by["setting:" + sid_] = by.get("setting:" + sid_, 0.0) + v
        actual += fs["actual"]
        ghi += fs.get("guard_hi", 0.0)
        grw += float((fs.get("glink") or {}).get("rewrite") or 0.0)
        ev += fs.get("events", 0)
        for k, v in fs["flags"].items():
            flags[k] = flags.get(k, 0) + v
        for it in fs["items"].values():
            saved += it["saved"]
            by["test_filter"] += it["saved"]
    return {"actual": actual, "saved": saved, "guard_hi": ghi, "guard_rewrite": grw, "flags": flags, "events": ev,
            "by_mech": by, "settings": sets}


def pct(saved, actual):
    d = actual + saved
    return 100.0 * saved / d if d > 0 else None


# ------------------------------------------------------------------ savings.jsonl
def _records(sess, sid, cwd):
    """当前应写入 savings.jsonl 的记录（每个 event_id 一条最新值）。"""
    recs = []
    s = summarize(sess)
    recs.append(("actual:" + sid, {"mechanism": "actual", "saved_weighted_tokens": 0.0,
                                   "actual_weighted_tokens": round(s["actual"], 1),
                                   "method": "transcript usage × tools/pricing.py（Sonnet 5.5 当量）",
                                   "confidence": "实测" if not s["flags"].get("unknown") else "含未知模型（按 Sonnet 5.5 计）",
                                   "counted": True, "model_flags": s["flags"]}))
    for fs in sess["files"].values():
        for eid, it in fs["items"].items():
            recs.append((eid, {"mechanism": "test_filter", "saved_weighted_tokens": round(it["saved"], 1),
                               "actual_weighted_tokens": None, "saved_raw_tokens": it["tok"], "requests_after": it["n"],
                               "method": it["method"], "confidence": it["conf"], "counted": True,
                               "event_ts": it["ts"], "closed": it["closed"]}))
    reg = settings()
    for sid_, t in s["settings"].items():
        e = reg[sid_]
        recs.append(("set:%s:%s" % (sid_, sid), {
            "mechanism": "setting:" + sid_, "saved_weighted_tokens": round(t["saved"], 1), "actual_weighted_tokens": None,
            "requests_main": t["n"], "requests_sub": t["n_sub"], "prefix_writes": t["n_write"], "no_cache": t["n_inp"],
            "delta_main": e["delta_main"], "delta_sub": e["delta_sub"], "since": e.get("since"),
            "method": "%s：工具表（api-request-shape）显示已生效的每次请求 × Δ（主 %s / 子 %s token）×"
                      "（前缀写缓存的请求按写入价，命中缓存按读价，没用缓存按输入价）" % (e.get("label", sid_), e["delta_main"], e["delta_sub"]),
            "source": e["source"], "confidence": "中（Δ 为首请求实测；每次请求按同一固定前缀计）", "counted": True}))
    if s["guard_hi"] > 0:
        recs.append(("guard:" + sid, {"mechanism": "context_guard_clear", "saved_weighted_tokens": 0.0,
                                      "estimate_range": [0.0, round(s["guard_hi"], 1)], "actual_weighted_tokens": None,
                                      "upper_minus_rewrite": round(s["guard_hi"] - s["guard_rewrite"], 1),
                                      "method": "守卫提醒后 /clear：区间 [0, 上限]，上限 = 上下文差 × 之后每次请求的缓存读价",
                                      "confidence": "很低（反事实不确定）", "counted": False}))
    return recs


def _due(last, val):
    """P2（Codex 复核 P 第 1 项）：值有任何变化就写（按账本的写入精度 0.1 比较），账本和显示不会再对不上。
    写入次数的上限 = 请求次数 × 记录数，超过 JSONL_MAX 会压缩。"""
    if last is None:
        return True
    return round(val, 1) != round(last, 1)


_last_rev = [0]


def _next_rev(prev):
    """P2（Codex 复核 P 第 2 项）：rev 用毫秒时间戳，同一毫秒内再 +1。进度文件丢了从头重建时，
    新行的 rev 仍然比旧行大（旧版本的 rev 是 1、2、3…，也一定更小），合并时取到的是最新值。"""
    r = max(int(time.time() * 1000), int(prev or 0) + 1, _last_rev[0] + 1)
    _last_rev[0] = r
    return r


def _mech_of(eid):
    if eid.startswith("actual:"):
        return "actual"
    if eid.startswith("set:"):
        return "setting:" + eid.split(":")[1]
    if eid.startswith("guard:"):
        return "context_guard_clear"
    return "test_filter"


def _row_val(r):
    m = r.get("mechanism")
    v = r.get("actual_weighted_tokens") if m == "actual" else (
        (r.get("estimate_range") or [0, 0])[1] if m == "context_guard_clear" else r.get("saved_weighted_tokens"))
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _seed_flushed(state_dir, sid):
    """P3：进度文件丢了（或第一次见这个会话）时，从账本里找出这个会话已有的记录，免得重建后旧记录没人撤销。
    只解析含这个会话 id 的行。→ {event_id: [rev, 值, closed, mechanism, 已撤销]}"""
    p = os.path.join(state_dir, "savings.jsonl")
    best = {}
    needle = json.dumps(sid, ensure_ascii=False)
    for name in (p + ".1", p):
        try:
            with open(name, errors="replace") as f:
                for l in f:
                    if needle not in l:
                        continue
                    try:
                        r = json.loads(l)
                        if r.get("session_id") != sid:
                            continue
                        eid = r["event_id"]
                        if eid not in best or int(r.get("rev") or 0) >= int(best[eid].get("rev") or 0):
                            best[eid] = r
                    except Exception:
                        continue
        except Exception:
            continue
    return {eid: [int(r.get("rev") or 0), _row_val(r) if r.get("counted") else 0.0, bool(r.get("closed")),
                  r.get("mechanism") or _mech_of(eid), int(bool(r.get("revoked")) or not r.get("counted"))]
            for eid, r in best.items()}


def flush(state_dir, sess, sid, cwd, now, force=False):
    """把变化了的记录追加到 savings.jsonl（调用方已持全局锁）。
    P3（Codex 复核 P2）：以前写过、现在不再产生的记录（设置停用、被排除、since 改了、重算后没了）追加一行撤销
    （counted=false、节省 0），rev 更大，合并时盖掉旧值，账本和 usaver-status 跟状态栏的实时值一致。"""
    if not isinstance(sess.get("flushed"), dict):
        sess["flushed"] = _seed_flushed(state_dir, sid)
    fl = sess["flushed"]
    lines = []
    cur = _records(sess, sid, cwd)
    live = {eid for eid, _ in cur}
    for eid in sorted(fl):
        last = fl[eid]
        if eid in live or not isinstance(last, list) or (len(last) > 4 and last[4]):
            continue
        mech = last[3] if len(last) > 3 and last[3] else _mech_of(eid)
        rev = _next_rev(last[0])
        row = {"ts": int(now), "session_id": sid, "cwd": cwd, "event_id": eid, "rev": rev, "mechanism": mech,
               "saved_weighted_tokens": 0.0, "actual_weighted_tokens": 0.0 if mech == "actual" else None,
               "counted": False, "revoked": True,
               "method": "撤销：这条记录按现在的设置不再产生（设置停用、被排除、生效时间改了，或重算后没有这个事件），节省归零"}
        lines.append(json.dumps(row, ensure_ascii=False))
        fl[eid] = [rev, 0.0, False, mech, 1]
    for eid, r in cur:
        val = r.get("actual_weighted_tokens") if r["mechanism"] == "actual" else (
            (r.get("estimate_range") or [0, 0])[1] if r["mechanism"] == "context_guard_clear" else r["saved_weighted_tokens"])
        val = float(val or 0)
        last = fl.get(eid)
        lv = last[1] if isinstance(last, list) else None
        closed_now = r.get("closed") and not (isinstance(last, list) and len(last) > 2 and last[2])
        if not (force or _due(lv, val) or closed_now):
            continue
        rev = _next_rev(last[0] if isinstance(last, list) else 0)
        row = {"ts": int(now), "session_id": sid, "cwd": cwd, "event_id": eid, "rev": rev}
        row.update(r)
        lines.append(json.dumps(row, ensure_ascii=False))
        fl[eid] = [rev, val, bool(r.get("closed")), r["mechanism"], 0]
    if not lines:
        return 0
    p = os.path.join(state_dir, "savings.jsonl")
    with open(p, "a") as f:
        f.write("\n".join(lines) + "\n")
    try:
        if os.path.getsize(p) > JSONL_MAX:
            compact_jsonl(p)
    except Exception:
        pass
    return len(lines)


def read_jsonl(p):
    """→ {event_id: 最新一行}（取 rev 最大的；一样大取文件里靠后的那行——追加都在全局锁里，靠后 = 更新；坏行跳过）。"""
    best = {}
    for name in (p + ".1", p):
        try:
            with open(name, errors="replace") as f:
                for l in f:
                    try:
                        r = json.loads(l)
                        eid = r["event_id"]
                        if eid not in best or int(r.get("rev") or 0) >= int(best[eid].get("rev") or 0):
                            best[eid] = r
                    except Exception:
                        continue
        except Exception:
            continue
    return best


def compact_jsonl(p):
    best = read_jsonl(p)
    tmp = "%s.%d.tmp" % (p, os.getpid())
    with open(tmp, "w") as f:
        for r in sorted(best.values(), key=lambda r: r.get("ts") or 0):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


def totals_from_jsonl(p, session_id=None):
    """只读 savings.jsonl 算总节约%（给 usaver-status 用）。→ dict 或 None。"""
    best = read_jsonl(p)
    if not best:
        return None
    A = S = 0.0
    a = s = 0.0
    by, by_s = {}, {}
    for r in best.values():
        if not r.get("counted"):
            continue
        sv = float(r.get("saved_weighted_tokens") or 0)
        A += float(r.get("actual_weighted_tokens") or 0)
        S += sv
        m = r.get("mechanism") or "?"
        if m != "actual":
            by[m] = by.get(m, 0.0) + sv
        if session_id and r.get("session_id") == session_id:
            a += float(r.get("actual_weighted_tokens") or 0)
            s += sv
            if m != "actual":
                by_s[m] = by_s.get(m, 0.0) + sv
    return {"cum_pct": pct(S, A), "sess_pct": pct(s, a) if session_id else None, "cum_actual": A, "cum_saved": S,
            "by_mech": by, "sess_by_mech": by_s if session_id else None, "sess_actual": a if session_id else None}


# ------------------------------------------------------------------ 入口：状态栏每次刷新调一次
def _safe_sid(sid):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", sid)[:80]


def update(payload, state_dir, now=None, budget_s=BUDGET_S, sweep=True):
    """→ {'cum_pct','sess_pct','pending','flags',...} 或 None（没法算，例如没有价格表）。
    P2：中途出别的异常时退回「只读 savings_totals.json 的上次结果」，节约表照样显示（所有者要求一直显示）。"""
    now = now or time.time()
    try:
        P = pricing()
    except Exception:
        return None
    try:
        return _update(payload, state_dir, now, budget_s, P, sweep)
    except Exception:
        try:
            return _totals_view(state_dir, None, payload.get("session_id") if isinstance(payload, dict) else None)
        except Exception:
            return None


def _sess_size(tp):
    """主 transcript + 子 agent transcript 的总字节数（判断会话有没有新内容）。"""
    n = 0
    for p in session_files(tp, None):
        try:
            n += os.path.getsize(p)
        except Exception:
            pass
    return n


def _scan_session(S, sid, tp, cwd, now, deadline, P):
    """调用方持会话锁：读进度、增量解析到 deadline。→ (sess, pending)。"""
    sp = os.path.join(S, "savings", _safe_sid(sid) + ".json")
    sess = _load(sp, {})
    fp = settings_fp()
    if sess.get("v") == VERSION and "sfp" not in sess:
        sess["sfp"] = fp                                  # P3 上线前的 v2 进度：当时的设置就是现在的，不用重算
    if sess.get("v") != VERSION or sess.get("sfp") != fp:
        keep = sess.get("flushed") if isinstance(sess.get("flushed"), dict) else None
        sess = {"v": VERSION, "sfp": fp, "files": {}, "cwd": cwd, "tp": tp}
        if keep is not None:
            sess["flushed"] = keep                        # P3：重算后没再出现的记录要靠它追加撤销行
    pending = False
    for p in session_files(tp, sid):
        fs = sess["files"].setdefault(p, new_fstate())
        done, _ = scan_file(fs, p, sid, cwd, deadline, P)
        pending = pending or not done
    sess["last"] = int(now)
    sess["pending"] = pending
    return sess, pending


def _commit(S, sess, sid, tp, cwd, now, pending, T_out=None):
    """调用方持会话锁：在全局锁里写账本、合计和索引，然后写进度文件。拿不到全局锁 → 只写进度（下次再写账本）。
    pending（解析没追上）时不写账本、不覆盖合计里已有的完整值，免得半截的值盖掉完整的旧值。"""
    tot_path = os.path.join(S, "savings_totals.json")
    with Lock(os.path.join(S, "savings.lock")) as g:
        if g.ok:
            T = _load(tot_path, {})
            T.setdefault("sessions", {})
            if not pending:
                flush(S, sess, sid, cwd, now)
            if not pending or sid not in T["sessions"]:
                s = summarize(sess)
                T["sessions"][sid] = [round(s["actual"], 1), round(s["saved"], 1), round(s["guard_hi"], 1), int(now),
                                      int(bool(s["flags"].get("unknown") or s["flags"].get("family_fallback")))]
            idx = _ensure_idx(S, T)
            idx[sid] = [tp, sum(fs.get("off", 0) for fs in sess["files"].values()), int(now), VERSION, int(bool(pending)), cwd,
                        sess.get("sfp")]
            _archive(T, now)
            _atomic(tot_path, T)
            if T_out is not None:
                T_out.update(T)
    _atomic(os.path.join(S, "savings", _safe_sid(sid) + ".json"), sess)


def _update(d, S, now, budget_s, P, sweep=True):
    sid = d.get("session_id")
    tp = d.get("transcript_path")
    cwd = d.get("cwd") or (d.get("workspace") or {}).get("current_dir") or ""
    os.makedirs(os.path.join(S, "savings"), exist_ok=True)
    t0 = time.time()
    sess_res = None
    pending = False
    T = {}
    if isinstance(sid, str) and sid and isinstance(tp, str) and tp and os.path.isfile(tp):
        sp = os.path.join(S, "savings", _safe_sid(sid) + ".json")
        with Lock(sp + ".lock") as lk:
            if lk.ok:
                sess, pending = _scan_session(S, sid, tp, cwd, now, t0 + budget_s, P)
                _commit(S, sess, sid, tp, cwd, now, pending, T)
                s = summarize(sess)
                sess_res = {"sess_pct": pct(s["saved"], s["actual"]), "pending": pending, "flags": s["flags"],
                            "sess_by_mech": s["by_mech"],
                            "sess_guard_hi_pct": pct(s["guard_hi"], s["actual"]) if s["guard_hi"] else None}
            else:
                old = _load(sp, {})
                if old.get("v") == VERSION:
                    s = summarize(old)
                    sess_res = {"sess_pct": pct(s["saved"], s["actual"]), "pending": True, "flags": s["flags"],
                                "sess_by_mech": s["by_mech"]}
    if sweep and not pending:
        left = min(SWEEP_BUDGET_S, max(0.0, t0 + budget_s - time.time()))
        if left > 0.002 and _sweep(S, now, sid, time.time() + left, P):
            T = {}                                        # 补写改了合计：重读
    return _totals_view(S, T or None, sid, sess_res)


def _totals_view(S, T, sid, sess_res=None):
    """合计（savings_totals.json）→ 显示用的结果。"""
    if T is None:
        T = _load(os.path.join(S, "savings_totals.json"), {})
    A = S_ = 0.0
    unk = 0
    for v in (T.get("sessions") or {}).values():
        try:
            A += float(v[0]); S_ += float(v[1]); unk += int(v[4]) if len(v) > 4 else 0
        except Exception:
            continue
    arc = T.get("archived") or {}
    A += float(arc.get("actual") or 0); S_ += float(arc.get("saved") or 0)
    res = {"cum_pct": pct(S_, A), "cum_actual": A, "cum_saved": S_, "unknown_sessions": unk}
    if sess_res is None:
        sv = (T.get("sessions") or {}).get(sid) if isinstance(sid, str) else None
        sess_res = {"sess_pct": pct(float(sv[1]), float(sv[0])) if isinstance(sv, list) and len(sv) > 1 else None,
                    "pending": False, "flags": {}}
        if sess_res["sess_pct"] is None and isinstance(sid, str) and sid:
            sess_res["sess_pct"] = 0.0                     # 有会话、还没有请求：本会话就是 0
    res.update(sess_res)
    if res.get("sess_pct") is None and isinstance(sid, str) and sid:
        res["sess_pct"] = 0.0                             # 有会话、还没有可计的请求：本会话就是 0（一直显示）
    return res


def _ensure_idx(S, T):
    """补写用的索引 T["idx"] = {会话: [transcript, 已读字节, 最后处理时间, 进度版本, 是否没追上, cwd]}。
    第一次（P2 上线时）从合计里的各会话的进度文件补出来；旧版本进度的版本号 ≠ VERSION，会被补写重算。调用方持全局锁。"""
    idx = T.get("idx")
    if isinstance(idx, dict):
        return idx
    idx = {}
    for k, v in (T.get("sessions") or {}).items():
        old = _load(os.path.join(S, "savings", _safe_sid(k) + ".json"), {})
        if isinstance(old.get("tp"), str):
            idx[k] = [old["tp"], -1, int(v[3]) if isinstance(v, list) and len(v) > 3 else 0,
                      int(old.get("v") or 0), 1, old.get("cwd") or ""]
    T["idx"] = idx
    return idx


def _sweep(S, now, exclude, deadline, P):
    """P2 补写：替最近见过的其它会话补算（它的 transcript 变长了、上次没追上、或进度文件版本旧了）。
    最多每 SWEEP_EVERY 秒做一次；每个会话的会话锁拿不到就跳过（它自己的状态栏正在算）。→ 有没有改动。"""
    tot_path = os.path.join(S, "savings_totals.json")
    cands = []
    with Lock(os.path.join(S, "savings.lock")) as g:
        if not g.ok:
            return False
        T = _load(tot_path, {})
        if now - float(T.get("sweep_ts") or 0) < SWEEP_EVERY:
            return False
        idx = _ensure_idx(S, T)
        T["sweep_ts"] = int(now)
        _atomic(tot_path, T)
    fp = settings_fp()
    for k, v in sorted(idx.items(), key=lambda kv: -(kv[1][2] if isinstance(kv[1], list) and len(kv[1]) > 2 else 0)):
        if k == exclude or not isinstance(v, list) or len(v) < 5:
            continue
        if not isinstance(v[0], str) or not os.path.isfile(v[0]):
            continue
        stale_fp = (v[6] if len(v) > 6 else None) != fp   # P3：设置停用 / since 改了：不管多久以前的会话都要重算、撤销旧记录
        if now - float(v[2] or 0) > SWEEP_MAX_AGE and not stale_fp:
            continue
        if stale_fp or v[3] != VERSION or v[4] or _sess_size(v[0]) != v[1]:
            cands.append((k, v[0], v[5] if len(v) > 5 else ""))
    changed = False
    for k, tp, cwd in cands:
        if time.time() > deadline:
            break
        sp = os.path.join(S, "savings", _safe_sid(k) + ".json")
        with Lock(sp + ".lock", wait=0) as lk:
            if not lk.ok:
                continue
            sess, pending = _scan_session(S, k, tp, cwd, now, deadline, P)
            _commit(S, sess, k, tp, cwd, now, pending)
            changed = True
    return changed


def _archive(T, now, keep_s=30 * 86400, max_n=2000):
    """30 天没动的会话并进 archived 合计，免得 totals 越来越大。索引里的旧会话一并去掉。"""
    ss = T.get("sessions") or {}
    old = [k for k, v in ss.items() if not isinstance(v, list) or len(v) < 4 or now - v[3] > keep_s]
    if len(ss) - len(old) > max_n:
        old += [k for k, _ in sorted(((k, v) for k, v in ss.items() if k not in old), key=lambda kv: kv[1][3])][:len(ss) - len(old) - max_n]
    idx = T.get("idx")
    if isinstance(idx, dict):
        for k in [k for k, v in idx.items() if k not in ss or k in old]:
            idx.pop(k, None)
    if not old:
        return
    arc = T.setdefault("archived", {"actual": 0.0, "saved": 0.0, "n": 0})
    for k in old:
        v = ss.pop(k)
        if isinstance(v, list) and len(v) >= 2:
            arc["actual"] = arc.get("actual", 0.0) + float(v[0]); arc["saved"] = arc.get("saved", 0.0) + float(v[1])
            arc["n"] = arc.get("n", 0) + 1


def render(res):
    """状态栏片段。标明是估算、是上限（见 README「节约表」）。
    P2：所有者定了一直显示，0% 也显示：还没有任何数据时显示 0.0%。res 是 None（节约表没装全，例如缺 pricing.py）时不显示。"""
    if not res:
        return ""
    cum = res.get("cum_pct")
    s = " | 省≈%.1f%%累计" % (cum if cum is not None else 0.0)
    if res.get("sess_pct") is not None:
        s += " 本会话%.1f%%" % res["sess_pct"]
    s += "(估·上限)"
    if res.get("pending"):
        s += "…"
    if (res.get("flags") or {}).get("unknown") or res.get("unknown_sessions"):
        s += "?"                                       # 有未知模型按 Sonnet 5.5 计价
    return s


MECH_LABEL = {"test_filter": "测试过滤", "setting:artifact_off": "关Artifact"}


def breakdown(by_mech, actual, saved):
    """分项文字（给 usaver-status 用）：各机制占「实际 + 节省」的百分点。没有非零项返回 ""。"""
    d = (actual or 0) + (saved or 0)
    if not by_mech or d <= 0:
        return ""
    parts = ["%s %.1f" % (MECH_LABEL.get(k, k), 100.0 * v / d) for k, v in sorted(by_mech.items()) if abs(v) >= 0.0005 * d]
    return ("（%s 个百分点）" % "、".join(parts)) if parts else ""


# ------------------------------------------------------------------ 离线：对一组 transcript 跑节约表（校准用，不写 state）
def offline(paths):
    """paths：transcript 列表（主 transcript；子 agent 自动带上）。按文件开始时间顺序处理。→ 每个文件的结果 + 合计。"""
    P = pricing()
    out = []
    def first_ts(p):
        try:
            with open(p, errors="replace") as f:
                for l in f:
                    m = re.search(r'"timestamp":\s*"([^"]+)"', l)
                    if m:
                        return _ts(m.group(1)) or 0
        except Exception:
            pass
        return 0
    for tp in sorted(paths, key=first_ts):
        sess = {"files": {}}
        for p in session_files(tp, os.path.basename(tp)):
            fs = sess["files"].setdefault(p, new_fstate())
            scan_file(fs, p, os.path.basename(tp), "", float("inf"), P)
        s = summarize(sess)
        s["path"] = tp
        s["glink"] = (sess["files"][tp].get("glink") or None)
        out.append(s)
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--offline":
        res = offline(a[1:])
        A = sum(r["actual"] for r in res); Sv = sum(r["saved"] for r in res); G = sum(r["guard_hi"] for r in res)
        for r in res:
            print("%-60s actual=%10.0f saved=%8.0f guard_hi=%8.0f events=%d flags=%s" % (
                os.path.basename(r["path"])[:60], r["actual"], r["saved"], r["guard_hi"], r["events"], r["flags"]))
        print("合计 actual=%.0f saved=%.0f (%.2f%%) guard_hi=%.0f (%.2f%%)" % (A, Sv, pct(Sv, A) or 0, G, pct(G, A) or 0))
    elif a and a[0] == "--session-end":
        # P2：给 SessionEnd hook 用（挂不挂由所有者决定，要改 settings.json）：读 stdin 的 session_id / transcript_path，
        # 不限时补算一次并写账本。任何错误都静默退出 0，不影响 Claude Code 退出。
        try:
            d = json.load(sys.stdin)
            st = os.environ.get("USAGE_SAVER_STATE") or os.path.join(SUITE, "state")
            if isinstance(d, dict):
                update(d, st, budget_s=10.0, sweep=False)
        except Exception:
            pass
        sys.exit(0)
    elif a and a[0] == "--status":
        st = os.environ.get("USAGE_SAVER_STATE") or os.path.join(SUITE, "state")
        print(json.dumps(totals_from_jsonl(os.path.join(st, "savings.jsonl")), ensure_ascii=False))
    else:
        print(__doc__)
