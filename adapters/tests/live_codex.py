#!/usr/bin/env python3
"""用真的 Codex CLI 跑适配层（第 5 轮 H）。会消耗 Codex 额度（每组约 1–3 万 token）。

  python3 adapters/tests/live_codex.py [工作目录，默认 $TMPDIR 下新建] [--reps N]

每组建两个临时项目（同一个有 1 个失败、60 个吵闹通过的 pytest 项目）：
  treat：项目根放 usage-saver 的 AGENTS.md 片段（用 adapters/apply.py 写入）；
  ctrl ：不放 AGENTS.md。
两组都把 adapters/bin 放进 PATH、USAVER_LOGDIR 指向项目内。然后 `codex exec` 同一个任务，检查：
  1. Codex 读到了 AGENTS.md 并改用 usaver-run 跑测试（rollout 里的工具调用含 usaver-run；日志目录里有 run-*）；
  2. 两组都答对失败的测试名（test_bad）；
  3. codex_usage.py 按 cwd 统计出的 token 与 codex exec 打印的 "tokens used" 一致（= 输入 − 缓存命中 + 输出）。
不读写 ~/.codex 下的配置；只读 ~/.codex/sessions 里这几次运行自己的 rollout。
"""
import json, os, re, shutil, subprocess, sys, tempfile, time

T = os.path.dirname(os.path.realpath(__file__))
AD = os.path.dirname(T)
PROMPT = "Run this project's test suite and tell me the name of the failing test and the reason, in two sentences."
WRAPPED = re.compile(r"usaver-run\s+(?:--\S+\s+)*['\"]?(?:\S*/)?(?:pytest|python[0-9.]*\s+-m\s+pytest)")
RAW_TEST = re.compile(r"(?:^|[;&|(\s\"])(?:\S*/)?(?:pytest|python[0-9.]*\s+-m\s+pytest)\b")
TEST = ("import pytest\n\n@pytest.mark.parametrize('i', range(60))\ndef test_ok(i):\n    print('noise ' * 30)\n    assert i >= 0\n\n"
        "def test_bad():\n    total = sum([1, 2, 3])\n    assert total == 7, 'sum mismatch'\n")


def setup(root, name, with_agents):
    w = os.path.join(root, name)
    os.makedirs(os.path.join(w, "tests"))
    with open(os.path.join(w, "tests", "test_m.py"), "w") as f:
        f.write(TEST)
    with open(os.path.join(w, "pytest.ini"), "w") as f:      # 项目自带的吵闹配置：-v -rA 会打印通过用例的输出
        f.write("[pytest]\naddopts = -v -rA -p no:cacheprovider\n")
    if with_agents:
        env = dict(os.environ, USAVER_BACKUP_DIR=os.path.join(root, "bk"), USAVER_TRASH_DIR=os.path.join(root, "trash"))
        subprocess.run([sys.executable, os.path.join(AD, "apply.py"), "agents", "apply", "--target",
                        os.path.join(w, "AGENTS.md")], check=True, env=env, capture_output=True)
    return w


def run_codex(w):
    env = dict(os.environ, PATH=os.path.join(AD, "bin") + os.pathsep + os.environ["PATH"],
               USAVER_LOGDIR=os.path.join(w, ".usaver-logs"), TMPDIR=w)
    env.pop("USAGE_SAVER_RAW", None)
    t0 = time.time()
    r = subprocess.run(["codex", "exec", "--skip-git-repo-check", "-s", "workspace-write",
                        "-c", 'model_reasoning_effort="low"', "-C", w, PROMPT],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=900)
    out = r.stdout + r.stderr
    with open(os.path.join(os.path.dirname(w), os.path.basename(w) + ".codex.log"), "w") as f:
        f.write(out)
    m = re.findall(r"tokens used\s*\n\s*([\d,]+)", out)
    return dict(rc=r.returncode, printed=int(m[-1].replace(",", "")) if m else None, out=out, secs=time.time() - t0)


def rollout_calls(w, since):
    """这次运行自己的 rollout 里：shell 类工具调用的输入文本、每次工具输出的 original_token_count、每次请求的输入。"""
    calls, outs, inputs = [], [], []
    base = os.path.expanduser("~/.codex/sessions")
    for dp, _, fs in os.walk(base):
        for fn in fs:
            p = os.path.join(dp, fn)
            if not fn.endswith(".jsonl") or os.path.getmtime(p) < since:
                continue
            lines = open(p, errors="replace").read().splitlines()
            try:
                meta = json.loads(lines[0]).get("payload") or {}
            except Exception:
                continue
            if os.path.realpath(meta.get("cwd") or "/") != os.path.realpath(w):
                continue
            for l in lines:
                try:
                    j = json.loads(l)
                except Exception:
                    continue
                p2 = j.get("payload") or {}
                if j.get("type") == "response_item" and p2.get("type") in ("custom_tool_call", "function_call"):
                    calls.append(str(p2.get("input") or p2.get("arguments") or ""))
                if j.get("type") == "response_item" and p2.get("type") in ("custom_tool_call_output", "function_call_output"):
                    m = re.search(r"original_token_count\D+(\d+)", str(p2.get("output")))
                    outs.append(int(m.group(1)) if m else None)
                if j.get("type") == "token_usage_record":
                    inputs.append((p2.get("usage") or {}).get("input_tokens"))
    return calls, outs, inputs


def usage(w, since):
    r = subprocess.run([sys.executable, os.path.join(AD, "codex", "codex_usage.py"), "--json", "--cwd", w,
                        "--since", time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(since - 5))],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)["summary"]["total"]


def main():
    args = sys.argv[1:]
    reps = 1
    if "--reps" in args:
        i = args.index("--reps"); reps = int(args[i + 1]); del args[i:i + 2]
    root = args[0] if args else tempfile.mkdtemp(prefix="usaver-live-")
    os.makedirs(root, exist_ok=True)
    rows, ok = [], True
    for k in range(reps):
        for name, agents in (("treat%d" % k, True), ("ctrl%d" % k, False)):
            w = setup(root, name, agents)
            since = time.time() - 1
            res = run_codex(w)
            calls, outs, inputs = rollout_calls(w, since)
            test_calls = [c for c in calls if RAW_TEST.search(c) or WRAPPED.search(c)]
            used_wrapper = any(WRAPPED.search(c) for c in test_calls)
            ld = os.path.join(w, ".usaver-logs")
            logs = os.listdir(ld) if os.path.isdir(ld) else []
            test_out = [o for c, o in zip(calls, outs) if c in test_calls]
            u = usage(w, since)
            if u.get("calls"):
                calc = int(round(u["mean_ctx"] * u["calls"] * (1 - u["cache_hit"]))) + u["out_tokens"]
            else:
                calc = None
            answer = res["out"].rsplit("codex", 1)[-1]
            row = dict(group=name, agents_md=agents, rc=res["rc"], secs=round(res["secs"]), shell_calls=len(calls),
                       test_calls=len(test_calls), used_usaver_run=used_wrapper, test_output_tokens=test_out,
                       first_request_input=inputs[0] if inputs else None, logs=len(logs), answered=("test_bad" in answer),
                       requests=u.get("calls"), input_tokens=int(u["mean_ctx"] * u["calls"]) if u.get("calls") else None,
                       cached_share=round(u.get("cache_hit", 0), 3), output_tokens=u.get("out_tokens"),
                       printed_tokens=res["printed"], parsed_tokens=calc,
                       cost_units=round(u.get("cost", 0)))
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False)); sys.stdout.flush()
            checks = [row["rc"] == 0, row["answered"], row["printed_tokens"] is not None and row["parsed_tokens"] is not None
                      and abs(row["printed_tokens"] - row["parsed_tokens"]) <= 2]
            if agents:
                checks += [used_wrapper, len(logs) >= 1]
            ok &= all(checks)
    with open(os.path.join(root, "live_results.json"), "w") as f:
        json.dump(rows, f, indent=1)
    print("RESULT:", "PASS" if ok else "FAIL", "->", os.path.join(root, "live_results.json"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
