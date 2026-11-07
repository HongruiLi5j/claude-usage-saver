#!/usr/bin/env python3
"""适应性验收矩阵（第 5 轮 I，所有者要求：新对话 / 新项目 / 新模型都要能适应）。以后每一轮都跑、都比通过率。

行：7 种项目 = 临时目录里造的 Python / Lean / Node / 纯文档 / 无 git 空目录，加 2 个真实项目
    （~/Documents/mc-option-pricer、~/Documents/lean-mc-variance，**只读**：「全新」列直接以真实路径为 cwd，
    「接续」列用只含元数据的影子副本——真实项目里不写任何文件）。
列：
  全新对话：state 空、没有交接文件、第一次进入。
  接续对话：有交接文件，格式是这个项目自己的写法（覆盖文件指定名 / 中文同义名 / 英文标题 / 说明文件里提到的名 /
           默认 handoff skill 格式），并有同进程 /clear 的上一段 transcript。
  换模型：payload 的 model 换成 opus、sonnet、haiku 和不认识的 claude-xyz-9（含窗口缺失 / 非法窗口）。
每格判断（每项一个子检查，全过才算这一格通过）：
  fail-open：每个 hook 退出码 0、stderr 无 Traceback、stdout 为空或合法 JSON（状态栏为一行）；
  用上本项目约定：画像识别对、本项目测试命令被认出、本项目的交接文件和章节被注入；
  没套用别的项目：兄弟项目的新鲜交接（DECOY）不出现、别的语言的测试命令不被包装；
  换模型安全回退：阈值在知道窗口时不超过窗口、不知道窗口时不变且为正，model_info 对未知模型保守，不崩。
用法：python3 tools/round5/I_adapt_matrix.py [--json 输出文件] [--hooks 目录（默认 hooks/）]
隔离：假 HOME、USAGE_SAVER_STATE 都在临时目录；hook 以子进程运行；真实项目只读。
"""
import argparse, json, os, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "profiles"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import test_project_profile as T  # noqa: E402  复用造项目和跑 hook 的工具
import project_profile as P       # noqa: E402

MARK = T.MARK
REAL = {"mc-option-pricer": os.path.expanduser("~/Documents/mc-option-pricer"),
        "lean-mc-variance": os.path.expanduser("~/Documents/lean-mc-variance")}
MODELS = [("claude-opus-5-5", "Opus 5.5", 1_000_000), ("claude-sonnet-5-5", "Sonnet 5.5", 1_000_000),
          ("claude-haiku-5", "Haiku 5", 200_000), ("claude-xyz-9", "XYZ 9", None), ("claude-xyz-9", "XYZ 9", "abc")]
FOREIGN = "cargo test"           # 7 行里没有一个是 Rust 项目：任何一行都不该包装它
HOOKS = os.path.join(ROOT, "hooks")


# ---------------------------------------------------------------- 工具
def clean_run(rc, out, err, kind="json"):
    """fail-open 检查：rc=0、无 Traceback、stdout 为空 / 合法 JSON（kind=json）或一行（kind=line）。"""
    if rc != 0 or "Traceback" in err:
        return False
    if kind == "line":
        return len(out.strip().splitlines()) == 1
    if not out.strip():
        return True
    try:
        json.loads(out)
        return True
    except Exception:
        return False


def hook(E, name, payload, raw=False):
    p = os.path.join(HOOKS, name)
    argv = ["/bin/sh", p] if name.endswith(".sh") else [sys.executable, p]
    return E.run(argv, payload if raw else json.dumps(payload))


def ctx_of(out):
    try:
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return ""


def shadow(E, src):
    """真实项目的元数据影子副本（深度 ≤ 2、单文件 ≤ 256KB、跳过依赖/缓存/数据/封存目录；有 .git 就建空 .git）。
    只读源目录，写到假 HOME 下同样的相对位置。"""
    rel = os.path.relpath(src, os.path.expanduser("~"))
    dst = os.path.join(E.home, rel)
    os.makedirs(dst, exist_ok=True)
    if os.path.exists(os.path.join(src, ".git")):
        os.makedirs(os.path.join(dst, ".git"), exist_ok=True)
    for dp, dns, fns in os.walk(src):
        depth = os.path.relpath(dp, src).count(os.sep) + (0 if dp == src else 1)
        dns[:] = [d for d in dns if d not in P.SKIP_DIRS and not d.startswith(".") and "test2" not in d and depth < 2]
        for f in fns:
            s = os.path.join(dp, f)
            if f.startswith(".") or "test2" in f or os.path.getsize(s) > 256 * 1024:
                continue
            t = os.path.join(dst, os.path.relpath(s, src))
            os.makedirs(os.path.dirname(t), exist_ok=True)
            shutil.copy2(s, t)
    return dst


# ---------------------------------------------------------------- 行定义
def row_defs():
    """每行：(名字, 造项目函数(E)->cwd, 期望, 接续用的「本项目写法」交接写入函数(E, root))。"""
    def h_python(E, r):
        T.w(os.path.join(r, ".usage-saver.json"), json.dumps({"handoff": {"files": ["NOTES/session.md"],
                                                                          "sections": {"red": ["Guardrails"], "next": ["Todo"]}}}))
        T.w(os.path.join(r, "NOTES", "session.md"), f"{MARK}\n# Session\n## History\nOLD-HIST\n## Guardrails\n- no push OWN-RED\n## Todo\n- fix OWN-NEXT\n")

    def h_lean(E, r):
        T.w(os.path.join(r, "交接.md"), f"{MARK}\n# 交接\n## 以前\nOLD-HIST\n## 硬规则\n- 不改 lakefile OWN-RED\n## 接下来\n- 证 lemma2 OWN-NEXT\n")

    def h_node(E, r):
        T.w(os.path.join(r, "HANDOFF.md"), f"{MARK}\n# Handoff\n## Background\nOLD-HIST\n## Constraints\n- no deploy OWN-RED\n## Next steps\n- wire API OWN-NEXT\n")

    def h_docs(E, r):
        T.w(os.path.join(r, "进度记录.md"), f"{MARK}\n# 进度\n## 旧\nOLD-HIST\n## 注意事项\n- 不改标题 OWN-RED\n## 待办\n- 写第 7 章 OWN-NEXT\n")

    def h_default(E, r):
        T.w(os.path.join(r, "HANDOFF.md"), f"{MARK}\n## 当前状态\n### 红线 / 未决\n- 不 rm OWN-RED\n### 目标\nOLD-HIST\n### 下一步\n1. 跑 OWN-NEXT\n")

    def h_dot_claude(E, r):
        T.w(os.path.join(r, ".claude", "handoff.md"), f"{MARK}\n# 进度\n## 背景\nOLD-HIST\n## 未决\n- 别直接给答案 OWN-RED\n## Next steps\n- 提示 2 OWN-NEXT\n")

    return [
        ("Python（造）", lambda E: T.mk_python(E), {"lang": "python", "own_test": "python3 tests/test_core.py"}, h_python),
        ("Lean（造，无 git）", lambda E: T.mk_lean(E), {"lang": "lean", "own_test": "lake build"}, h_lean),
        ("Node（造）", lambda E: T.mk_node(E), {"lang": "javascript|typescript", "own_test": "pnpm test"}, h_node),
        ("纯文档（造）", lambda E: T.mk_docs(E), {"kind": "docs", "own_test": None}, h_docs),
        ("空目录无 git（造）", lambda E: T.mk_empty(E), {"kind": "empty", "own_test": None}, h_default),
        ("真实 mc-option-pricer（只读）", "mc-option-pricer", {"lang": "python", "own_test": ".venv/bin/python s01_check.py"}, h_default),
        ("真实 lean-mc-variance（只读）", "lean-mc-variance", {"lang": "lean", "own_test": "lake build"}, h_dot_claude),
    ]


def make(E, mk, fresh):
    """返回 cwd。真实项目：全新 = 真实路径（只读）；接续 = 影子副本（交接写在副本里）。"""
    if isinstance(mk, str):
        return REAL[mk] if fresh else shadow(E, REAL[mk])
    return mk(E)


def decoy(E):
    """兄弟项目里放一份新鲜、带标记的交接（别的项目的格式）：任何一行都不该注入它。"""
    d = os.path.join(E.projects, "zz-other")
    os.makedirs(os.path.join(d, ".git"), exist_ok=True)
    T.w(os.path.join(d, "HANDOFF.md"), f"{MARK}\n### 红线 / 未决\n- DECOY-RED\n### 下一步\n- DECOY-NEXT\n")
    T.w(os.path.join(E.projects, "HANDOFF.md"), f"{MARK}\nDECOY-PROJECTS-LEVEL\n")


def base_checks(E, cwd, exp, checks):
    p = P.get_profile(cwd, write_cache=False)
    if exp.get("lang"):
        checks["画像语言对"] = bool(set(exp["lang"].split("|")) & set(p["languages"]))
    if exp.get("kind"):
        checks["画像类型对"] = p["kind"] == exp["kind"]
    # test_filter：本项目测试命令被认出；别的语言的不包装
    rc, out, err = hook(E, "test_filter.py", {"tool_input": {"command": exp["own_test"] or "lake build"}, "cwd": cwd})
    checks["test_filter fail-open"] = clean_run(rc, out, err)
    checks["本项目测试命令被认出" if exp["own_test"] else "无测试项目不乱包装"] = bool(out.strip()) == bool(exp["own_test"])
    rc, out, err = hook(E, "test_filter.py", {"tool_input": {"command": FOREIGN}, "cwd": cwd})
    checks["别的语言测试命令不包装"] = clean_run(rc, out, err) and not out.strip()
    return p


# ---------------------------------------------------------------- 三列
def col_fresh(E, mk, exp, _h):
    checks = {}
    decoy(E)
    cwd = make(E, mk, fresh=True)
    base_checks(E, cwd, exp, checks)
    tp = E.transcript("FRESH", users=())
    outs = []
    for src in ("startup", "clear", "compact"):
        rc, out, err = hook(E, "session_resume.py", {"hook_event_name": "SessionStart", "source": src, "cwd": cwd,
                                                    "session_id": "FRESH", "transcript_path": tp})
        checks[f"session_resume({src}) fail-open"] = clean_run(rc, out, err)
        outs.append(ctx_of(out))
    checks["没注入别的项目的交接"] = not any("DECOY" in o for o in outs)
    rc, out, err = hook(E, "context_guard.py", {"cwd": cwd, "session_id": "FRESH", "transcript_path": tp})
    checks["context_guard fail-open（空 transcript）"] = clean_run(rc, out, err) and not out.strip()
    rc, out, err = hook(E, "statusline.sh", {"session_id": "FRESH", "cwd": cwd})
    checks["statusline fail-open（最少字段）"] = clean_run(rc, out, err, "line") and "无法解析" not in out
    return checks


def col_resume(E, mk, exp, write_handoff):
    checks = {}
    decoy(E)
    cwd = make(E, mk, fresh=False)
    p = base_checks(E, cwd, exp, checks)
    write_handoff(E, p["_meta"]["root"])
    old = E.transcript("OLD", users=("上一段 OLDMSG",))
    rc, out, err = hook(E, "session_resume.py", {"hook_event_name": "SessionEnd", "reason": "clear", "cwd": cwd,
                                                "session_id": "OLD", "transcript_path": old})
    checks["SessionEnd fail-open"] = clean_run(rc, out, err) and not out.strip()
    rc, out, err = hook(E, "session_resume.py", {"hook_event_name": "SessionStart", "source": "clear", "cwd": cwd,
                                                "session_id": "NEW", "transcript_path": E.transcript("NEW", users=())})
    a = ctx_of(out)
    checks["clear fail-open"] = clean_run(rc, out, err)
    checks["clear 注入本项目写法的交接"] = "OWN-RED" in a and "OWN-NEXT" in a
    checks["clear 带上一段对话摘要"] = "OLDMSG" in a
    rc, out, err = hook(E, "session_resume.py", {"hook_event_name": "SessionStart", "source": "compact", "cwd": cwd,
                                                "session_id": "NEW", "transcript_path": E.transcript("NEW2", users=())})
    b = ctx_of(out)
    checks["compact fail-open"] = clean_run(rc, out, err)
    checks["compact 取本项目的红线/下一步节"] = "OWN-RED" in b and "OWN-NEXT" in b and "OLD-HIST" not in b
    checks["没注入别的项目的交接"] = "DECOY" not in a + b
    return checks


def col_models(E, mk, exp, _h):
    checks = {}
    cwd = make(E, mk, fresh=True)
    sys.path.insert(0, HOOKS)
    for mid, name, win in MODELS:
        tag = f"{mid}{'' if win in (None,) else '@' + str(win)}"
        cw = {"current_usage": {"input_tokens": 1, "cache_read_input_tokens": 150_000}}
        if win is not None:
            cw["context_window_size"] = win
        sid = "M-" + tag
        rc, out, err = hook(E, "statusline.sh", {"session_id": sid, "cwd": cwd, "model": {"id": mid, "display_name": name},
                                                 "context_window": cw})
        checks[f"{tag} statusline fail-open"] = clean_run(rc, out, err, "line") and name in out
        tp = T.guard_tp(E, 150_000, mid)
        rc, out, err = hook(E, "context_guard.py", {"cwd": cwd, "session_id": sid, "transcript_path": tp, "model": mid})
        checks[f"{tag} context_guard fail-open"] = clean_run(rc, out, err)
        rc, out, err = hook(E, "session_resume.py", {"hook_event_name": "SessionStart", "source": "clear", "cwd": cwd,
                                                    "session_id": sid, "transcript_path": tp, "model": mid})
        checks[f"{tag} session_resume fail-open"] = clean_run(rc, out, err)
        # 阈值：知道窗口 → 不超过窗口；不知道 → 与不给窗口时相同且为正
        # 旧版 lines() 没有 window 参数：退回不带窗口的调用（= 旧版实际行为），不因签名不同判它失败
        code = ("import sys,json; sys.path.insert(0,%r); import context_guard as G; w=%r\n"
                "try: a=G.lines(%r,1.5,window=w if isinstance(w,int) else None)[:2]\n"
                "except TypeError: a=G.lines(%r,1.5)[:2]\n"
                "b=G.lines(%r,1.5)[:2]; print(json.dumps([a,b]))" % (HOOKS, win, cwd, cwd, cwd))
        rc, out, err = E.run([sys.executable, "-c", code])
        try:
            (s1, g1), (s0, g0) = json.loads(out)
            if isinstance(win, int):
                ok = s1 <= win * 0.85 and g1 <= win * 0.95 and 0 < s1 < g1
            else:
                ok = (s1, g1) == (s0, g0) and 0 < s1 < g1
        except Exception:
            ok = False
        checks[f"{tag} 阈值安全（窗口内/不猜）"] = ok
        mi = P.model_info(mid, win)
        checks[f"{tag} model_info 回退"] = (mi["family"] != "unknown") if "xyz" not in mid else (
            mi["family"] == "unknown" and mi["price_weight"] == 1.0 and mi["context_window"] > 0)
    # 小窗口 + 宽裕档：状态栏和守卫都要在压缩前提醒（R5I 修复前这一项会失败）
    T.w(os.path.join(E.state, "mode.json"), json.dumps({"ts": int(time.time()), "mult": 1.5}))
    now = time.time()
    T.w(os.path.join(E.state, "rate.json"), json.dumps({"ts": int(now), "rate_limits": {
        "seven_day": {"used_percentage": 5, "resets_at": int(now + 3600), "sampled_at": int(now)}}}))
    pl = {"session_id": "SMALL", "cwd": cwd, "model": {"id": "claude-haiku-5", "display_name": "Haiku 5"},
          "context_window": {"context_window_size": 200000, "current_usage": {"input_tokens": 1, "cache_read_input_tokens": 180000}}}
    rc, out, err = hook(E, "statusline.sh", pl)
    rc2, out2, err2 = hook(E, "context_guard.py", {"cwd": cwd, "session_id": "SMALL",
                                                   "transcript_path": T.guard_tp(E, 180_000, "claude-haiku-5")})
    checks["200k 窗口 + 宽裕档 180k：状态栏 ⚠ 且守卫提醒"] = "⚠" in out and "上下文守卫" in out2
    return checks


COLS = [("全新对话", col_fresh), ("接续对话", col_resume), ("换模型", col_models)]


def main():
    global HOOKS
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--hooks", help="被测 hooks 目录（默认本套件 hooks/；比较旧版时指向一个放了备份副本的目录）")
    a = ap.parse_args()
    if a.hooks:
        HOOKS = os.path.abspath(a.hooks)
    t0 = time.time()
    res, cells_ok, cells, sub_ok, sub = [], 0, 0, 0, 0
    for rname, mk, exp, hw in row_defs():
        if isinstance(mk, str) and not os.path.isdir(REAL[mk]):
            res.append({"row": rname, "skipped": "真实项目不存在"})
            continue
        row = {"row": rname, "cols": {}}
        for cname, fn in COLS:
            E = T.Env()
            try:
                ch = fn(E, mk, exp, hw)
            except Exception as e:
                ch = {f"异常 {type(e).__name__}: {str(e)[:120]}": False}
            finally:
                shutil.rmtree(E.root, ignore_errors=True)     # 只删本脚本自己在系统临时目录里建的沙箱
            ok = all(ch.values())
            row["cols"][cname] = {"ok": ok, "checks": ch}
            cells += 1; cells_ok += ok
            sub += len(ch); sub_ok += sum(ch.values())
        res.append(row)
    print("| 项目 \\ 情况 | " + " | ".join(c for c, _ in COLS) + " |")
    print("|---|" + "---|" * len(COLS))
    for r in res:
        if "skipped" in r:
            print(f"| {r['row']} | " + " | ".join("跳过" for _ in COLS) + " |")
            continue
        cells_txt = []
        for c, _ in COLS:
            ch = r["cols"][c]["checks"]
            cells_txt.append(("✅" if r["cols"][c]["ok"] else "❌") + f" {sum(ch.values())}/{len(ch)}")
        print(f"| {r['row']} | " + " | ".join(cells_txt) + " |")
    print(f"\n格子通过率：{cells_ok}/{cells}（{100 * cells_ok / max(cells, 1):.0f}%）；子检查：{sub_ok}/{sub}"
          f"（{100 * sub_ok / max(sub, 1):.1f}%）；耗时 {time.time() - t0:.0f} 秒")
    for r in res:
        for c, v in (r.get("cols") or {}).items():
            bad = [k for k, ok in v["checks"].items() if not ok]
            if bad:
                print(f"  ✗ {r['row']} / {c}：" + "；".join(bad))
    if a.json:
        json.dump({"ts": int(time.time()), "cells_ok": cells_ok, "cells": cells, "sub_ok": sub_ok, "sub": sub,
                   "rows": res}, open(a.json, "w"), ensure_ascii=False, indent=1)
    return 0 if cells_ok == cells else 1


if __name__ == "__main__":
    sys.exit(main())
