#!/usr/bin/env python3
"""第 5 轮 D：Bash 读文件的重复/大小统计 + read_guard 在真实历史命令上的回放。

只读 ~/.claude/projects/ 的 transcript（排除 -private-tmp- 基准、.secret/sealed/test2/relay-room）。
终端和 summary JSON 里只有计数、动词、扩展名，不含对话正文和命令原文；
带命令原文的样例只写 state/round5_D/（state/ 已 gitignore）。

口径（和 reports/round5_where_tokens_go.md 一致）：
  token ≈ ASCII 字符×0.43 + 非 ASCII 字符×1.39（那份报告用 566 个样本标定，误差约 ±20%）；
  一条工具结果的成本 ≈ tok×w + 0.1×tok×N_after，w 是它进入上下文那次请求的缓存写入单价（1.25 或 2），
  N_after 是它之后、压缩/会话结束之前的请求数（每次都按 cache_read 0.1 再计一次）。
用法：
  python3 tools/round5/D_read_stats.py            # 默认窗口：11-04 起 和 启用后（11-06 07:58 UTC 起）
  python3 tools/round5/D_read_stats.py --all      # 全部历史 transcript（回放用，样本更大）
"""
import argparse, collections, datetime as dt, glob, importlib.machinery, importlib.util, json, os, re, sys

sys.dont_write_bytecode = True
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "state", "round5_D")
PROJ = os.path.expanduser("~/.claude/projects")
UTC = dt.timezone.utc
FROM = dt.datetime(2026, 11, 4, 8, 0, tzinfo=UTC)
CHANGE = dt.datetime(2026, 11, 6, 7, 58, tzinfo=UTC)
KA, KN = 0.43, 1.39


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


G = load(os.path.join(ROOT, "hooks", "read_guard.py"), "rg_under_test")
TF = load(os.path.join(ROOT, "hooks", "test_filter.py"), "tf_under_test")


def bad(f):
    rel = f.split("/projects/")[1]
    return rel.startswith("-private-tmp-") or any(x in f for x in (".secret", "sealed", "test2", "relay-room"))


def ts(j):
    try:
        return dt.datetime.fromisoformat(j["timestamp"].replace("Z", "+00:00"))
    except Exception:
        return None


def text_of(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join((b.get("text") or "") if isinstance(b, dict) else (b if isinstance(b, str) else "") for b in c)
    return ""


def toks(s):
    na = sum(1 for ch in s if ord(ch) > 127)
    return (len(s) - na) * KA + na * KN


RO_VERBS = {"grep", "egrep", "rg", "ls", "wc", "find", "git", "echo", "printf", "pwd", "cat", "nl", "head", "tail", "sed",
            "stat", "file", "du", "df", "ps", "which", "date", "jq", "sort", "uniq", "cut", "awk", "tree", "diff", "cmp",
            "md5", "shasum", "realpath", "basename", "dirname", "test", "[", "cd", "true", "less", "more", "column", "tr",
            "readlink", "type", "command", "env", "uname", "whoami", "sw_vers", "id", "lsof", "pgrep", "sleep", "xxd", "od"}
GIT_RO = {"status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "blame", "remote", "config", "describe"}


def bash_readonly(cmd):
    """这条命令看起来不会改任何文件（用来判断两次读之间文件有没有可能被改过）。看不懂 → False。"""
    t = G.lex(cmd)
    if t is None:
        return False
    L = G.structure(t)
    if L is None:
        return False
    for x in L:
        for c in x["pipe"]:
            for r in c["redirs"]:
                if not ((r[0] in ("2>", "2>>") and r[1] == "/dev/null") or r[0] == "2>&"):
                    return False
            if not c["words"]:
                continue
            w = [y[0] for y in c["words"]]
            v = os.path.basename(w[0])
            if v not in RO_VERBS:
                return False
            if v == "sed" and any(a.startswith("-i") for a in w[1:]):
                return False
            if v == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok") for a in w[1:]):
                return False
            if v == "git" and (len(w) < 2 or w[1] not in GIT_RO):
                return False
            if v == "sort" and any(a.startswith("-o") for a in w[1:]):
                return False
            if v == "awk" and any(re.search(r"[>|]|system", a) for a in w[1:]):
                return False
    return True


def size_bucket(t):
    return "<1k" if t < 1000 else "1-2k" if t < 2000 else "2-5k" if t < 5000 else "5-10k" if t < 10000 else ">=10k"


def verb_class(a, cmd):
    """按读法分类：cat 整读 / sed -n 行段 / head / tail / nl / python / 管道过滤 / 混合。"""
    vs = sorted({u["verb"] + ("|过滤" if u["filtered"] else "") for u in a["units"]})
    k = "+".join(vs)
    if not a["pure"]:
        k = "混合(" + k + ")"
    return k


def parse_file(f, seen_mid):
    """→ (requests, events)。events: ('use', idx, name, input, cwd, T) / ('res', tool_use_id, text, is_error, T) / ('req', r) / ('compact',)"""
    lines = []
    try:
        for l in open(f, errors="ignore"):
            try:
                lines.append(json.loads(l))
            except Exception:
                pass
    except OSError:
        return []
    final = {}
    for j in lines:
        if j.get("type") == "assistant":
            m = j.get("message") or {}
            u, mid = m.get("usage"), m.get("id")
            if u and mid and (mid not in final or u.get("output_tokens", 0) > final[mid].get("output_tokens", 0)):
                final[mid] = u
    ev, local = [], set()
    for j in lines:
        t = j.get("type"); T = ts(j)
        if t == "system" and j.get("subtype") == "compact_boundary":
            ev.append(("compact",)); continue
        if t == "assistant":
            m = j.get("message") or {}
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    ev.append(("use", b.get("id"), b.get("name"), b.get("input") or {}, j.get("cwd"), T))
            mid = m.get("id"); u = final.get(mid)
            if u and mid and mid not in seen_mid and mid not in local and T is not None:
                seen_mid.add(mid); local.add(mid)
                cc = u.get("cache_creation") or {}
                cw_raw = u.get("cache_creation_input_tokens", 0)
                cw5 = cc.get("ephemeral_5m_input_tokens", cw_raw if not cc else 0); cw1 = cc.get("ephemeral_1h_input_tokens", 0)
                cr = u.get("cache_read_input_tokens", 0); inp = u.get("input_tokens", 0); out = u.get("output_tokens", 0)
                cw = 1.25 * cw5 + 2.0 * cw1
                ev.append(("req", dict(T=T, ctx=cr + cw_raw + inp, cost=cr * 0.1 + cw + inp + out * 5,
                                      w=(cw / cw_raw) if cw_raw else 1.25, side="sub" if "/subagents/" in f else "main")))
            continue
        if t == "user":
            c = (j.get("message") or {}).get("content")
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        ev.append(("res", b.get("tool_use_id"), text_of(b.get("content")), bool(b.get("is_error")), T))
    return ev


def result_ok_text(txt, err):
    if err:
        return False
    if "<persisted-output>" in txt or "Output too large" in txt[:400] or len(txt) >= G.INLINE_LIMIT:
        return False
    if "PARTIAL view" in txt[-600:] or "exceeds maximum allowed" in txt[:400]:
        return False
    if txt.startswith("File unchanged since last read") or txt.startswith("Wasted call"):
        return False
    return True


WRITE_CMD = re.compile(r"\b(sed\s+-i|perl\s+-\w*i|mv|cp|rm|touch|truncate|patch|ln|rsync|install|unzip|tar|"
                       r"git\s+(checkout|restore|reset|stash|apply|merge|pull|rebase|am|cherry-pick|switch))\b")


def writes_file(cmd, b):
    """命令是不是明确写了名叫 b 的文件：重定向/tee 到它、mv/cp/sed -i 等带上它、python open(..'w') 它。"""
    e = re.escape(b)
    if re.search(r"(>>?|\btee\b(\s+-a)?)\s*['\"]?[^\s;|&]*" + e, cmd):
        return True
    if WRITE_CMD.search(cmd):
        return True
    if re.search(r"open\([^)]*" + e + r"[^)]*['\"][wax]", cmd) or re.search(r"write_text|to_csv|json\.dump\(", cmd):
        return True
    return False


def relation(prior, u, cur_ver, epoch, utok):
    """同一文件读过、但这次不被覆盖时，和之前（同版本、没压缩、结果正常）的读法是什么关系；
    行段读估算和之前行段重叠的 token（按行数比例）。"""
    if not [x for x in prior if x[5] == epoch]:
        return ("之前的读在压缩前", utok, 0.0)
    if not [x for x in prior if x[2] == cur_ver and x[5] == epoch]:
        return ("文件之后改过", utok, 0.0)
    good = [x for x in prior if x[2] == cur_ver and x[5] == epoch and x[6]]
    if not good:
        return ("之前那次出错/落盘预览", utok, 0.0)
    sp = tuple(u["spec"])
    kinds = {tuple(x[0])[0] for x in good}
    if sp[0] == "lines" and sp[2] is not None:
        span = sp[2] - sp[1] + 1
        cov = set()
        full_seen = False
        for x in good:
            o = tuple(x[0])
            if o[0] == "lines" and o[2] is not None:
                cov.update(range(max(o[1], sp[1]), min(o[2], sp[2]) + 1))
            elif o[0] == "full":
                full_seen = True
        if full_seen:
            return ("段读_之前整读过(无行号)", utok, utok)
        ov = len(cov) / span if span > 0 else 0
        return ("段读_与之前段重叠" if ov > 0 else "段读_新段(翻页)", utok, utok * ov)
    if sp[0] == "full" and "lines" in kinds:
        return ("整读_之前读过其中几段", utok, 0.0)
    if sp[0] == "full" and "full" in kinds:
        return ("整读_行号形式不同", utok, utok)
    if sp[0] == "cmd":
        return ("过滤/管道_写法不同", utok, 0.0)
    return ("其他:" + sp[0] + "_after_" + "/".join(sorted(kinds)), utok, 0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    files = [f for f in glob.glob(PROJ + "/**/*.jsonl", recursive=True) if not bad(f)]
    if not a.all:
        files = [f for f in files if os.path.getmtime(f) >= FROM.timestamp()]
    files.sort()
    seen_mid = set()
    win_cost = collections.Counter()
    items = []          # 每条 Bash/Read 读文件结果
    rep = collections.Counter()
    replay = {"deny": [], "tf_overlap": 0, "updatedInput": 0, "exc": 0, "big": collections.Counter()}
    unparsed_read_like = collections.Counter()
    RE_READLIKE = re.compile(r"(?:^|[\s;&|])(cat|sed\s+-n|head|tail|nl|less)\s")
    deny_msg_tok = toks("[read_guard] 没有执行这条命令：xxxxxxxx.py 自你 12 次工具调用前读过之后内容没变（sha1 相同），上次的输出还在你的上下文里，请直接用那份，不必再读。如果确实找不到那份输出，原样再发一次同一条命令就会放行。")
    for f in files:
        ev = parse_file(f, seen_mid)
        side = "sub" if "/subagents/" in f else "main"
        # ---- 第一遍：位置、请求、压缩 ----
        req_pos = [i for i, e in enumerate(ev) if e[0] == "req"]
        comp_pos = [i for i, e in enumerate(ev) if e[0] == "compact"]
        for i in req_pos:
            r = ev[i][1]
            for wname, w0 in (("3d", FROM), ("post", CHANGE), ("all", None)):
                if w0 is None or r["T"] >= w0:
                    win_cost[wname] += r["cost"]

        def after(i):
            """位置 i 之后：(第一次请求, 到下一次压缩/结束前的请求数)"""
            nxt = [p for p in comp_pos if p > i]
            end = nxt[0] if nxt else len(ev)
            rs = [p for p in req_pos if i < p < end]
            return (ev[rs[0]][1] if rs else None), len(rs) - 1 if rs else 0

        uses = {}
        res_by = {}
        for i, e in enumerate(ev):
            if e[0] == "use":
                uses[e[1]] = (i, e)
            elif e[0] == "res":
                res_by[e[1]] = (i, e)
        # ---- 第二遍：按顺序模拟（统计口径的追踪器 + hook 的 decide 回放） ----
        ver = collections.Counter()          # 宽口径：路径 → 被 Edit/Write 或「明确写它」的命令改过的次数
        vers = collections.Counter()         # 严口径：再加上任何提到这个文件名的非只读命令（如 python x.py）

        def bump(cmd, skip):
            for p in list(recs):
                b = os.path.basename(p)
                if not b or p in skip or b not in cmd:
                    continue
                vers[p] += 1
                if writes_file(cmd, b):
                    ver[p] += 1
        impure = 0                           # 至今「可能写文件的 Bash 命令」个数（判断 A/B 类）
        epoch = 0                            # 压缩次数
        recs = collections.defaultdict(list) # 统计口径：path → [(spec, num, ver, impure, epoch, ok, tool)]
        agent_clean, agent_likely, agent_prob = {}, {}, {}   # 三种指纹口径下 hook 的状态
        item_ok = {}
        seq = 0
        for i, e in enumerate(ev):
            if e[0] == "compact":
                epoch += 1; continue
            if e[0] != "use":
                continue
            _, tuid, name, inp, cwd, T = e
            r = res_by.get(tuid)
            txt, err = (r[1][2], r[1][3]) if r else ("", True)
            item_ok[tuid] = result_ok_text(txt, err) if r else False
            if name in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
                p = inp.get("file_path") or inp.get("notebook_path")
                if p:
                    ver[os.path.normpath(p)] += 1; vers[os.path.normpath(p)] += 1
                continue
            if name not in ("Bash", "Read"):
                continue
            seq += 1
            if name == "Bash":
                cmd = inp.get("command") or ""
                an = G.analyze(cmd, cwd or "/")
                if an is None or not an["units"]:
                    if an is None and RE_READLIKE.search(cmd):
                        unparsed_read_like["heredoc/子shell等看不懂" if "<<" in cmd or "$(" in cmd or "`" in cmd else "其他看不懂"] += 1
                    if not bash_readonly(cmd):
                        impure += 1
                        bump(cmd, set())
                    # 回放：hook 对非读取命令必须什么都不做
                    if G.analyze(cmd, cwd or "/") is not None:
                        pass
                    continue
                units = [u for u in an["units"] if not u["unsafe"]]
                pure = an["pure"] and len(units) == len(an["units"])
                if not pure and not bash_readonly(cmd):
                    impure += 1
                    bump(cmd, {u["path"] for u in units})
                vc = verb_class(an, cmd)
            else:
                units = G.read_units(inp)
                pure = False
                vc = "Read" + (" 分段" if units and units[0]["spec"][0] != "full" else " 整读")
                cmd = ""
            if not units:
                continue
            tk = toks(txt) if r else 0
            first_req, n_after = after(r[0] if r else i)
            w = first_req["w"] if first_req else 1.25
            cost = tk * w + 0.1 * tk * n_after
            ctx_next = first_req["ctx"] if first_req else 0
            # ---- 统计口径分类 ----
            st, rel = [], []
            for u in units:
                p = u["path"]
                prior = recs.get(p, [])
                s = "new"
                if prior:
                    s = "other_view"
                    for (sp, num, v, vs, imp, ep, ok, tool) in reversed(prior):
                        if not G.covers(sp, u["spec"], num, u.get("num", False)):
                            continue
                        if ep != epoch:
                            s = "after_compact"; break
                        if v != ver[p]:
                            s = "changed"; break
                        if not ok:
                            s = "prior_bad"; break
                        exact = tuple(sp) == tuple(u["spec"])
                        s = ("exact_" if exact else "covered_") + ("clean" if imp == impure else "likely" if vs == vers[p] else "possible") + ("_fromRead" if tool == "Read" else "")
                        break
                if s == "other_view":
                    rel.append(relation(prior, u, ver[p], epoch, tk / max(len(units), 1)))
                st.append(s)
            ok_now = item_ok[tuid]
            for u in units:
                recs[u["path"]].append((u["spec"], u.get("num", False), ver[u["path"]], vers[u["path"]], impure, epoch, ok_now, name))
            dup_all = all(s.startswith(("exact_", "covered_")) for s in st)
            cls = ("dup_" + ("clean" if all("clean" in s for s in st) else "likely" if all(("clean" in s or "likely" in s) for s in st) else "possible")) if dup_all else \
                  ("changed" if any(s == "changed" for s in st) else "after_compact" if any(s == "after_compact" for s in st)
                   else "partial_dup" if any(s.startswith(("exact_", "covered_")) for s in st) else "new_or_other")
            T0 = T or (r[1][4] if r else None)
            items.append(dict(tool=name, vc=vc, pure=pure, tok=tk, cost=cost, n_after=n_after, cls=cls, st=st, side=side,
                              T=T0.isoformat() if T0 else None, post=bool(T0 and T0 >= CHANGE), d3=bool(T0 and T0 >= FROM),
                              ext=sorted({os.path.splitext(u["path"])[1] or "(无)" for u in units}), nunits=len(units),
                              ok=ok_now, ctx_next=ctx_next, f=f, paths=[u["path"] for u in units], rel=rel, w=w,
                              ro=(name == "Bash" and bash_readonly(cmd)), full=any(tuple(u["spec"]) == ("full",) for u in units),
                              chars=len(txt)))
            # ---- 回放：hook 的 decide（两种指纹口径） ----
            for label, agent, fpv in (("clean", agent_clean, lambda p: [ver[p], impure]), ("likely", agent_likely, lambda p: [vers[p]]),
                                      ("possible", agent_prob, lambda p: [ver[p]])):
                fps = {}
                est = len(txt.encode("utf-8", "ignore")) / max(len(units), 1) if r else None
                for u in units:
                    fps[u["path"]] = (fpv(u["path"]), est)
                try:
                    act, msg, info = G.decide(agent, name, cmd, cwd or "/", units, pure, an["raw"] if name == "Bash" else False, fps,
                                              lambda rec: item_ok.get(rec["tuid"], False) and rec["toff"].get("epoch") == epoch,
                                              {"dup": "deny", "big": "off"}, seq, tuid, {"epoch": epoch})
                except Exception:
                    replay["exc"] += 1; act, info = "allow", None
                if act == "deny":
                    replay["deny"].append(dict(label=label, tok=tk, cost=cost, n_after=n_after, ctx_next=ctx_next, vc=vc, side=side,
                                               post=bool(T0 and T0 >= CHANGE), d3=bool(T0 and T0 >= FROM), ago=info.get("ago"),
                                               st=st, ok=ok_now, cmd=cmd, f=f, T=T0.isoformat() if T0 else None,
                                               saved=cost - deny_msg_tok * (w + 0.1 * n_after),
                                               retry_extra=deny_msg_tok * (w + 0.1 * n_after) + 0.1 * ctx_next + 100 * 5))
                elif info and info.get("retry_after_deny"):
                    rep["retry_" + label] += 1
            if name == "Bash":
                if TF.rewrite(cmd, "/tmp/x"):
                    replay["tf_overlap_candidates"] = replay.get("tf_overlap_candidates", 0) + 1
                # 大文件规则（只计数）：纯读命令里有整读、结果 ≥ 阈值字符
                if pure and any(tuple(u["spec"]) == ("full",) for u in units) and r:
                    for thr in (20000, 40000):
                        if len(txt) >= thr * 0.7:
                            replay["big"]["full_ge_%d" % thr] += 1
    os.makedirs(OUT, exist_ok=True)
    import pickle
    pickle.dump(items, open(os.path.join(OUT, "items%s.pkl" % ("_all" if a.all else "")), "wb"))
    # ---------------- 汇总 ----------------
    S = {"files": len(files), "window_cost": dict(win_cost), "token_coef": [KA, KN], "unparsed_read_like": dict(unparsed_read_like)}
    for wn, key in (("3d", "d3"), ("post", "post"), ("all", None)):
        sel = [x for x in items if (key is None or x[key])]
        tot = win_cost[wn] or 1
        B = [x for x in sel if x["tool"] == "Bash"]
        Rd = [x for x in sel if x["tool"] == "Read"]
        d = {"bash_reads": len(B), "bash_read_tok": round(sum(x["tok"] for x in B)), "bash_read_cost": round(sum(x["cost"] for x in B)),
             "bash_read_cost_pct": round(100 * sum(x["cost"] for x in B) / tot, 2),
             "read_tool": len(Rd), "read_tool_cost_pct": round(100 * sum(x["cost"] for x in Rd) / tot, 2)}
        # 读法 × 大小
        vcs = collections.defaultdict(collections.Counter)
        for x in B:
            k = x["vc"]
            vcs[k]["n"] += 1; vcs[k]["tok"] += x["tok"]; vcs[k]["cost"] += x["cost"]; vcs[k]["nafter"] += x["n_after"]
        d["by_verb"] = {k: dict(n=v["n"], avg_tok=round(v["tok"] / v["n"]), cost_pct=round(100 * v["cost"] / tot, 2),
                                avg_after=round(v["nafter"] / v["n"], 1)) for k, v in sorted(vcs.items(), key=lambda kv: -kv[1]["cost"])}
        sb = collections.defaultdict(collections.Counter)
        for x in B:
            b = size_bucket(x["tok"]); sb[b]["n"] += 1; sb[b]["cost"] += x["cost"]; sb[b]["tok"] += x["tok"]
        d["by_size"] = {k: dict(n=v["n"], tok=round(v["tok"]), cost_pct=round(100 * v["cost"] / tot, 2)) for k, v in sb.items()}
        # 重复分类
        cl = collections.defaultdict(collections.Counter)
        for x in sel:
            k = x["tool"] + ":" + x["cls"]
            cl[k]["n"] += 1; cl[k]["tok"] += x["tok"]; cl[k]["cost"] += x["cost"]
            if x["pure"]:
                cl[k]["n_pure"] += 1; cl[k]["cost_pure"] += x["cost"]
        d["dup_classes"] = {k: dict(n=v["n"], tok=round(v["tok"]), cost=round(v["cost"]), cost_pct=round(100 * v["cost"] / tot, 3),
                                    n_pure=v["n_pure"], cost_pure_pct=round(100 * v["cost_pure"] / tot, 3)) for k, v in sorted(cl.items())}
        rr = collections.defaultdict(collections.Counter)
        for x in B:
            for k, t, ov in x["rel"]:
                rr[k]["n"] += 1; rr[k]["tok"] += t; rr[k]["overlap_tok"] += ov
                rr[k]["overlap_cost"] += x["cost"] * (ov / x["tok"] if x["tok"] else 0)
        d["other_view_relation"] = {k: dict(n=v["n"], tok=round(v["tok"]), overlap_tok=round(v["overlap_tok"]),
                                            overlap_cost_pct=round(100 * v["overlap_cost"] / tot, 3)) for k, v in rr.items()}
        us = collections.Counter(s for x in B for s in x["st"])
        d["unit_status"] = dict(us)
        # 同一会话（= 同一个 transcript 文件，子 agent 各算各的）同一路径读了几次
        for lab, src in (("bash", B), ("bash+read", sel)):
            per = collections.Counter((x["f"], p) for x in src for p in x["paths"])
            h = collections.Counter("1" if c == 1 else "2" if c == 2 else "3" if c == 3 else "4-5" if c <= 5 else "6-10" if c <= 10 else ">10" for c in per.values())
            d["reads_per_path_" + lab] = dict(paths=len(per), hist=dict(h), reads=sum(per.values()),
                                              repeat_reads=sum(c - 1 for c in per.values()))
            # 第 2 次及以后的读（不管有没有改过）占的成本
            seenp, rc = set(), 0.0
            for x in src:
                ks = [(x["f"], p) for p in x["paths"]]
                if all(k in seenp for k in ks):
                    rc += x["cost"]
                seenp.update(ks)
            d["repeat_read_cost_pct_" + lab] = round(100 * rc / tot, 2)
        # 回放
        D = [x for x in replay["deny"] if key is None or x[key]]
        for lab in ("clean", "likely", "possible"):
            dd = [x for x in D if x["label"] == lab]
            d["replay_" + lab] = dict(
                deny=len(dd), deny_sub=sum(1 for x in dd if x["side"] == "sub"),
                tok=round(sum(x["tok"] for x in dd)), saved=round(sum(x["saved"] for x in dd)),
                saved_pct=round(100 * sum(x["saved"] for x in dd) / tot, 3),
                retry_extra_all=round(sum(x["retry_extra"] for x in dd)),
                retry_extra_pct=round(100 * sum(x["retry_extra"] for x in dd) / tot, 3),
                by_verb=dict(collections.Counter(x["vc"] for x in dd)),
                by_size=dict(collections.Counter(size_bucket(x["tok"]) for x in dd)),
                ago=sorted(x["ago"] for x in dd if x["ago"] is not None),
                covered_not_exact=sum(1 for x in dd if any(s.startswith("covered") for s in x["st"])),
                from_read=sum(1 for x in dd if any("fromRead" in s for s in x["st"])))
        S[wn] = d
    S["replay_exceptions"] = replay["exc"]
    S["tf_overlap_candidates"] = replay.get("tf_overlap_candidates", 0)
    S["retry"] = dict(rep)
    S["big_rule_counts"] = dict(replay["big"])
    S["note"] = "dup_clean=两次读之间没有任何可能写文件的命令；dup_probable=中间有别的写命令但没提到这个文件名"
    json.dump(S, open(os.path.join(OUT, "summary%s.json" % ("_all" if a.all else "")), "w"), ensure_ascii=False, indent=1)
    # 样例（含命令原文）只写 state/
    json.dump([{k: x[k] for k in ("label", "cmd", "vc", "tok", "ago", "st", "T", "f", "saved")} for x in replay["deny"]],
              open(os.path.join(OUT, "deny_examples%s.json" % ("_all" if a.all else "")), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(S, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
