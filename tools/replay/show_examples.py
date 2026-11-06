#!/usr/bin/env python3
"""从 state/replay/result_<label>.json 打印每类前 N 个例子（已打码/截断），用于写报告。
用法：python3 tools/replay/show_examples.py current [N]"""
import json, os, re, sys, collections

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

def redact(s, width=150):
    s = re.sub(r"/private/tmp/claude-501/-Users-lihongrui/[0-9a-f-]{36}/scratchpad", "$SCRATCH", s)
    s = s.replace("/Users/lihongrui", "~")
    s = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>", s)
    s = re.sub(r"\b(sk|ghp|gho|xox[bp]|AKIA)[-_A-Za-z0-9]{8,}", "<token>", s)
    s = re.sub(r"\b[0-9a-f]{20,}\b", "<hex>", s)
    s = re.sub(r"\b[A-Za-z0-9+]{32,}={0,2}", "<long>", s)
    s = re.sub(r"([?&]t=|token=|TOKEN=|password=|Bearer )\S+", r"\1***", s)
    s = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<email>", s)
    s = s.replace("\n", "⏎").replace("|", "\\|")
    return s if len(s) <= width else s[:width] + "…"

def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "current"
    N = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    d = json.load(open(os.path.join(ROOT, "state", "replay", "result_%s.json" % label), encoding="utf-8"))
    ex = d["examples"]
    for k, v in ex.items():
        print("\n### %s (%d 条 unique, %d 次调用)" % (k, len(v), sum(r["n"] for r in v)))
        if k == "miss_inscope":
            by = collections.defaultdict(list)
            for r in v: by[r["miss"]].append(r)
            for reason, rs in sorted(by.items(), key=lambda x: -len(x[1])):
                print("  -- %s: %d" % (reason, len(rs)))
                for r in rs[:min(N, 4)]:
                    print("     `%s`" % redact(r["cmd"]))
            continue
        for r in v[:N]:
            extra = r.get("misfire") or ""
            if r.get("new_syntax") and not r["new_syntax"]["bash"][0]:
                extra += " bash错误: " + redact(r["new_syntax"]["bash"][1], 120)
            print("  - `%s` %s" % (redact(r["cmd"]), extra))
    print("\nzsh_only_bad:", len(d["zsh_only_bad"]))

if __name__ == "__main__":
    main()
