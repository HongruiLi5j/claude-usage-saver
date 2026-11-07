#!/usr/bin/env python3
"""受控 token 基准（第 5 轮 F2）：同一批固定任务，有/无本套件，对比 token 和完成率。

用法
  python3 tools/bench/token_bench.py --config A --repeats 3          # 只跑 A
  python3 tools/bench/token_bench.py --config A,B --repeats 3        # A、B 交错配对跑（推荐）
  python3 tools/bench/token_bench.py --config B --tasks fix_bulk,lookup --repeats 1
  python3 tools/bench/token_bench.py --config my_arm.json ...        # 自定义配置（见 ARM_SCHEMA）
  python3 tools/bench/token_bench.py --analyze --base A --treat B    # 汇总 results/ 里所有结果，配对 bootstrap
  python3 tools/bench/token_bench.py --agent codex --config codexA --tasks lookup --repeats 1
  python3 tools/bench/token_bench.py --list                         # 列出任务和配置

隔离（实测，2026-11-06，Claude Code 2.1.318）
  * `--settings <文件>` 只是"再叠加一层"：用户级 ~/.claude/settings.json 的 hooks 照样触发
    （实测 `--settings '{"hooks":{}}'` 后 UserPromptSubmit/PreToolUse 仍触发）。
  * `--setting-sources project,local` 不加载用户级设置：用户 hooks、henchmen 插件、~/.claude/skills 都不加载
    （实测 stream-json 里无任何 hook 事件）。所以两组都用它，再用 `--settings <临时文件>` 加各组自己的东西。
  * 套件的 skills（handoff、model-router）用 `--plugin-dir <临时插件>` 加回（名字变成 usage-saver:handoff）。
  * hook 的状态目录用 USAGE_SAVER_STATE 指到每次运行自己的临时目录（context_guard、session_resume、limit_logger
    都认这个变量）；test_filter 的日志目录写死在本项目 state/testlogs/（只写日志，不影响结果）。
  * 不读也不写真实 ~/.claude/settings.json。

工作目录默认放在 /private/tmp/usage_saver_bench/（可用 BENCH_WORK 改）：放在 /private/tmp 下，
tools/weekly_compare.py 会把这些会话当基准会话排除（它排除 -private-tmp- 项目），不污染真实用量统计。
"""
import argparse, collections, glob, gzip, hashlib, json, math, os, random, re, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))          # claude-usage-saver/
sys.path.insert(0, HERE)
import bench_fixture                                      # noqa: E402

RESULTS = os.path.join(HERE, "results")
WORK = os.environ.get("BENCH_WORK", "/private/tmp/usage_saver_bench")
PY = sys.executable
STOP_PCT = float(os.environ.get("BENCH_STOP_PCT", 92))
HOOKS = os.path.join(BASE, "hooks")
SUITE_SKILLS = [os.path.expanduser("~/.claude/skills/handoff"), os.path.expanduser("~/.claude/skills/model-router")]

# 权重同 tools/weekly_compare.py：cache_read×0.1，缓存写入 5m×1.25、1h×2，未缓存 input×1，output×5
W_IN, W_CR, W_CW5, W_CW1H, W_OUT = 1.0, 0.1, 1.25, 2.0, 5.0


# ============================================================== 配置（arm）
# 一个 arm = 叠加给 claude 的设置。两组共用 BASE_SETTINGS（模型、effort 由命令行给）。
#   settings:    写进临时 settings 文件（--settings）
#   plugin_dirs: 额外 --plugin-dir（skills 放在 <dir>/skills/<name>/SKILL.md）
#   skills:      要打包成临时插件的 skill 目录列表
#   extra_args:  其它 claude 命令行参数
ARM_SCHEMA = '{"name": "B_plus_x", "settings": {"hooks": {...}}, "skills": ["/abs/skill_dir"], "extra_args": []}'
BASE_SETTINGS = {"promptSuggestionEnabled": False}
MODEL_IDS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5"}


def suite_hooks(hooks_dir=HOOKS):
    """当前套件在真实 settings.json 里装的 hooks（2026-11-06 读真实 settings 核对过结构；statusLine 在 -p 下不运行，略）。"""
    py = lambda f: f"python3 {hooks_dir}/{f}"
    return {
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": py("context_guard.py")}]}],
        "SessionStart": [{"matcher": "clear|compact", "hooks": [{"type": "command", "command": py("session_resume.py")}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": py("test_filter.py")}]}],
        "SessionEnd": [{"hooks": [{"type": "command", "command": py("session_resume.py")}]}],
        "StopFailure": [{"hooks": [{"type": "command", "command": py("limit_logger.py"), "timeout": 10}]}],
    }


ARMS = {
    "A": dict(name="A", desc="不装本套件：无套件 hooks、无套件 skills、默认 autoCompactWindow", settings={}, skills=[]),
    "B": dict(name="B", desc="当前套件：hooks（context_guard/session_resume/test_filter/limit_logger）+ skills（handoff/model-router）"
              " + autoCompactWindow=300000", settings={"hooks": suite_hooks(), "autoCompactWindow": 300000},
              skills=SUITE_SKILLS),
    "B_hooks_only": dict(name="B_hooks_only", desc="只装套件 hooks（不装 skills、不改 autoCompactWindow）",
                         settings={"hooks": suite_hooks()}, skills=[]),
    # effort 对照（E 的问题：真实 settings 里 Opus effortLevel=high，官方默认 medium）。两臂都是 B，只差 effort；
    # effort 写进临时 settings 的 modelSettings（和真实 settings 同一个键），不传 --effort 命令行参数。
    "B_opus_high": dict(name="B_opus_high", desc="B + Opus effortLevel=high（所有者现状）", model="opus", effort_setting="high",
                        settings={"hooks": suite_hooks(), "autoCompactWindow": 300000}, skills=SUITE_SKILLS),
    "B_opus_medium": dict(name="B_opus_medium", desc="B + Opus effortLevel=medium（官方默认）", model="opus", effort_setting="medium",
                          settings={"hooks": suite_hooks(), "autoCompactWindow": 300000}, skills=SUITE_SKILLS),
    # Codex：目前没有套件等价物，只有基线；H 做好 adapters/ 后在这里加 codexB（例如 -c 覆盖或 AGENTS.md）
    "codexA": dict(name="codexA", desc="Codex 基线", agent="codex", extra_args=[]),
}


def load_arm(spec):
    if spec in ARMS:
        return dict(ARMS[spec])
    if os.path.exists(spec):
        a = json.load(open(spec))
        a.setdefault("name", os.path.splitext(os.path.basename(spec))[0])
        a.setdefault("settings", {}); a.setdefault("skills", [])
        return a
    raise SystemExit(f"未知配置 {spec!r}；可用：{', '.join(ARMS)}，或给一个 JSON 文件（格式 {ARM_SCHEMA}）")


def snapshot_suite(arms, run_root):
    """把套件 hooks/ 和 skills 在开跑时复制一份，整轮都用这份（试点里实测：别的 agent 在跑的过程中改了
    context_guard/session_resume/test_filter 和 model-router，B 组 16 次里出现了 5 个不同版本）。
    arm 里指向 HOOKS 的命令和 skills 路径改指快照；hook 脚本里写死的绝对路径（config.json、testlogs）不受影响。"""
    snap = os.path.join(run_root, "_suite")
    if not os.path.exists(snap):
        shutil.copytree(HOOKS, snap + "/hooks", ignore=shutil.ignore_patterns("__pycache__"))
    out = []
    for arm in arms:
        a = json.loads(json.dumps(arm))
        a["settings"] = json.loads(json.dumps(a.get("settings", {})).replace(HOOKS, snap + "/hooks"))
        sk = []
        for d in a.get("skills", []):
            dst = os.path.join(snap, "skills", os.path.basename(d.rstrip("/")))
            if not os.path.exists(dst):
                shutil.copytree(d, dst)
            sk.append(dst)
        a["skills"] = sk
        out.append(a)
    return out


def arm_hash(arm):
    """配置指纹：hook 脚本内容 + 设置 + skill 内容。同名配置换了代码，结果文件里能看出来。"""
    st = json.dumps(arm.get("settings", {}), sort_keys=True)
    used = sorted(set(re.findall(r"(/[^\s\"]+?\.py)", st)))          # settings 里实际引用的 hook 脚本
    h = hashlib.sha1(re.sub(r"/[^\s\"]*/hooks/", "hooks/", st).encode())
    h.update(json.dumps({k: arm.get(k) for k in ("extra_args", "model", "effort_setting")}, sort_keys=True).encode())
    for f in used:
        if os.path.exists(f):
            h.update(open(f, "rb").read())
    for d in arm.get("skills", []):
        for f in sorted(glob.glob(d + "/**", recursive=True)):
            if os.path.isfile(f):
                h.update(open(f, "rb").read())
    return h.hexdigest()[:10]


# ============================================================== 工具函数
def sh(cmd, cwd, t=180, env=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, timeout=t, env=env)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return -9, "timeout"


def sub(path, old, new):
    s = open(path).read()
    assert old in s, (path, old)
    open(path, "w").write(s.replace(old, new, 1))


def git(d, *args):
    return subprocess.run(["git", *args], cwd=d, capture_output=True, text=True).stdout


def commit(d, msg):
    sh(f"git add -A && git -c user.email=bench@local -c user.name=bench commit -qm {msg!r}", d)


def changed(d):
    """相对 HEAD 改动/新增的文件（遵守 .gitignore）。"""
    out = git(d, "status", "--porcelain", "--untracked-files=all")
    return [l[3:].strip().strip('"') for l in out.splitlines() if l.strip()]


def pytest_ok(d, args="-q"):
    rc, out = sh(f"{PY} -m pytest {args} -p no:cacheprovider", d, env=dict(os.environ, PYTHONPATH=d))
    return rc == 0, out[-300:]


def hidden(d, name, code):
    p = os.path.join(d, "_hidden_" + name + ".py")
    open(p, "w").write(code)
    ok, out = pytest_ok(d, f"-q {p}")
    os.remove(p)
    return ok, out


def has_num(ans, n):
    return re.search(rf"(?<![\d.]){re.escape(str(n))}(?![\d])", ans) is not None


# ============================================================== 任务
# 每个任务：steps（提示词列表，>1 时用 --resume 接着同一会话）、seed(d)、grade(d, answers) -> (ok, note)
TASKS = collections.OrderedDict()


def task(tid, cat, steps, seed=None, timeout=1200, budget=6):
    def deco(fn):
        TASKS[tid] = dict(id=tid, cat=cat, steps=steps if isinstance(steps, list) else [steps], seed=seed,
                          grade=fn, timeout=timeout, budget=budget)
        return fn
    return deco


FIX_PROMPT = ("这个项目的测试有失败（用 python3 -m pytest -q 跑）。请找出根因，在 minishop/ 里修好，"
              "不要改 tests/ 下的文件。修好后确认全部测试通过，最后用不超过 3 行说明根因。")


def seed_bulk(d):
    sub(d + "/minishop/pricing.py", "if qty >= min_qty:", "if qty > min_qty:")


@task("fix_bulk", "修 bug", FIX_PROMPT, seed_bulk)
def g_fix_bulk(d, ans):
    ok, out = pytest_ok(d)
    ch = changed(d)
    tests_touched = [c for c in ch if c.startswith("tests/")]
    return ok and not tests_touched and "minishop/pricing.py" in ch, f"pytest={ok} changed={ch}"


def seed_three(d):
    sub(d + "/minishop/money.py",
        'return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))', "return round(x, 2)")
    sub(d + "/minishop/shipping.py", "if weight_kg <= max_kg:", "if weight_kg < max_kg:")
    sub(d + "/minishop/dates.py", "for i in range((b - a).days):", "for i in range((b - a).days + 1):")


@task("fix_three", "修 bug（多处）", FIX_PROMPT.replace("测试有失败", "测试有不少失败，可能不止一个原因"), seed_three)
def g_fix_three(d, ans):
    ok, out = pytest_ok(d)
    ch = changed(d)
    return ok and not [c for c in ch if c.startswith("tests/")], f"pytest={ok} changed={ch}"


def seed_triage(d):
    sub(d + "/minishop/shipping.py", "if subtotal >= FREE_SHIPPING_THRESHOLD:", "if subtotal > FREE_SHIPPING_THRESHOLD:")
    sub(d + "/minishop/inventory.py", "return self._stock.get(sku, 0) - self._reserved.get(sku, 0)",
        "return self._stock.get(sku, 0) + self._reserved.get(sku, 0)")


@task("triage", "跑测试读失败", "跑一下测试（python3 -m pytest -q），告诉我哪些测试失败了、每个失败的根因在哪个文件的哪个函数"
      "（给出函数名）。只诊断，不要修改任何文件。", seed_triage)
def g_triage(d, ans):
    a = ans.lower()
    ok1 = "shipping_cost" in a or ("shipping" in a and "75" in a)
    ok2 = "available" in a
    ch = changed(d)
    return ok1 and ok2 and not ch, f"shipping={ok1} available={ok2} changed={ch}"


HID_COUPON = '''
import pytest
from minishop.pricing import apply_coupon
def test_c():
    assert apply_coupon(100, "SAVE10") == 90.0
    assert apply_coupon(50, " save10 ") == 45.0
    assert apply_coupon(49.99, "SAVE10") == 49.99
    assert apply_coupon(12.34, "TAKE5") == 7.34
    assert apply_coupon(3, "take5") == 0.0
    assert apply_coupon(19.99, "SAVE10") == 19.99
    assert apply_coupon(66.66, "SAVE10") == 59.99
    for bad in ["", "SAVE20", "FREE"]:
        with pytest.raises(ValueError):
            apply_coupon(10, bad)
'''


@task("feature_coupon", "新功能 + 测试",
      "在 minishop/pricing.py 新增函数 apply_coupon(subtotal: float, code: str) -> float，返回用券后的小计（用 round_money 取整到分）。"
      "规则：'SAVE10' 打 9 折，但仅当 subtotal >= 50，否则原样返回；'TAKE5' 直接减 5，最低到 0；code 不区分大小写、忽略首尾空格；"
      "其它任何 code 抛 ValueError。再在 tests/test_pricing.py 里加测试，跑全部测试确认通过。")
def g_coupon(d, ans):
    ok, out = hidden(d, "coupon", HID_COUPON)
    ok2, _ = pytest_ok(d)
    added = "apply_coupon" in open(d + "/tests/test_pricing.py").read()
    return ok and ok2 and added, f"hidden={ok} pytest={ok2} tests_added={added}"


def _log_truth(d):
    n_err = 0
    for l in open(d + "/logs/app.log"):
        if " ERROR " in l and "svc=payments " in l:
            n_err += 1
    return n_err


@task("log_qa", "读大文件回答",
      "logs/app.log 是一份服务日志。请回答：(1) svc=payments 的 ERROR 行一共有多少条？(2) 整个文件里 latency_ms 最大的是哪个请求"
      "（req=…），值是多少？只读，不要改文件。")
def g_log(d, ans):
    n = _log_truth(d)
    ok = has_num(ans, n) and "R770431" in ans and "4981" in ans
    return ok and not changed(d), f"want {n}/R770431/4981"


@task("doc_qa", "读大文件回答",
      "根据 docs/DESIGN.md：支付 webhook（payment webhook）失败后最多重试几次？退避方式是什么、从几秒开始、每次等待的上限是多少秒？只读。")
def g_doc(d, ans):
    a = ans.lower()
    ok = has_num(a, 7) and has_num(a, 3) and has_num(a, 120) and ("exponential" in a or "指数" in a)
    return ok and not changed(d), "want 7/3s/120s/exponential"


@task("lookup", "小查询", "WA 地区的税率是多少？在哪个文件第几行定义的？默认地区 DEFAULT_REGION 是什么？一两句话回答。")
def g_lookup(d, ans):
    line = [i for i, l in enumerate(open(d + "/minishop/config.py"), 1) if '"WA"' in l][0]
    ok = ("0.065" in ans or "6.5%" in ans) and "config.py" in ans and has_num(ans, line) and "CA" in ans
    return ok, f"want 0.065 config.py:{line} CA"


def _round_sites(d):
    sites = []
    for f in sorted(glob.glob(d + "/minishop/*.py")):
        for i, l in enumerate(open(f), 1):
            if re.search(r"(?<![\w.])round\(", l):
                sites.append((os.path.basename(f), i))
    return sites


@task("round_sites", "搜索定位", "minishop/ 包里有哪些地方直接调用了 Python 内置的 round()（不是 round_money）？按 文件名:行号 列出每一处。只读。")
def g_round(d, ans):
    sites = _round_sites(d)
    hit = [s for s in sites if re.search(rf"{re.escape(s[0])}[^\n]{{0,30}}?(?<!\d){s[1]}(?!\d)", ans)]
    return len(hit) == len(sites) and not changed(d), f"sites={sites} hit={len(hit)}"


HID_ORDER_TOTAL = '''
from minishop.orders import Order, order_total
def test_t():
    o = Order("x", region="NY", weight_kg=2.0).add_line("ABC-0001", 2, 10.0).add_line("XYZ-0002", 10, 1.0)
    assert order_total(o) == 40.67
'''


@task("rename", "多文件重构", "把函数 calc_total 重命名为 order_total，项目里所有用到的地方（包括 tests/ 和文档）一起改，不要保留旧名字的别名。"
      "改完跑全部测试确认通过。")
def g_rename(d, ans):
    rc, out = sh("grep -rn calc_total --include=*.py --include=*.md . | grep -v '^./.git' || true", d)
    left = out.strip()
    ok, _ = hidden(d, "ot", HID_ORDER_TOTAL)
    ok2, _ = pytest_ok(d)
    return ok and ok2 and not left, f"hidden={ok} pytest={ok2} left={left[:120]}"


HID_VALIDATE = '''
import pytest
from minishop.validate import validate_sku
def test_v():
    assert validate_sku("ABC-0001") == "ABC-0001"
    for bad in ["abc-0001", "AB-0001", "ABC-00011", "", None, 7, "ABC-0001 "]:
        with pytest.raises(ValueError):
            validate_sku(bad)
'''


@task("extract_validate", "多步重构",
      "minishop/catalog.py、inventory.py、orders.py 里各有一份几乎一样的 SKU 格式校验（正则 ^[A-Z]{3}-\\d{4}$）。请新建 minishop/validate.py，"
      "提供 validate_sku(sku) -> str：合法时原样返回，不合法（包括非字符串、末尾带空格）抛 ValueError。然后让这三个模块都改用它，"
      "删掉各自的重复实现。对外行为保持不变，不要改 tests/，跑全部测试确认通过。")
def g_extract(d, ans):
    v = os.path.exists(d + "/minishop/validate.py")
    dup = [f for f in ("catalog", "inventory", "orders") if "[A-Z]{3}" in open(f"{d}/minishop/{f}.py").read()]
    uses = all("validate_sku" in open(f"{d}/minishop/{f}.py").read() for f in ("catalog", "inventory", "orders"))
    ok, _ = hidden(d, "v", HID_VALIDATE) if v else (False, "")
    ok2, _ = pytest_ok(d)
    tt = [c for c in changed(d) if c.startswith("tests/")]
    return v and not dup and uses and ok and ok2 and not tt, f"validate={v} dup={dup} uses={uses} hidden={ok} pytest={ok2} tests={tt}"


MUTANTS = [
    ("collapse", "minishop/textutil.py", 're.sub(r"[^a-z0-9]+", "-", s.lower())', 're.sub(r"[^a-z0-9]", "-", s.lower())'),
    ("strip", "minishop/textutil.py", 'return s.strip("-")', "return s"),
    ("eq_n", "minishop/textutil.py", "if len(s) <= n:", "if len(s) < n:"),
    ("suffix_n", "minishop/textutil.py", "if n <= len(suffix):", "if n < len(suffix):"),
]


@task("add_tests", "写测试（变异检验）",
      "给 minishop/textutil.py 的 slugify 和 truncate 补测试，写到新文件 tests/test_textutil_extra.py。要覆盖这些边界：连续的分隔符、"
      "首尾的分隔符、空字符串、长度恰好等于 n、n 不大于 suffix 长度时报错。不要改 minishop/ 下的代码。跑一下确认新测试通过。")
def g_add_tests(d, ans):
    tf = d + "/tests/test_textutil_extra.py"
    if not os.path.exists(tf):
        return False, "no test file"
    ok0, _ = pytest_ok(d, "-q tests/test_textutil_extra.py")
    src_touched = [c for c in changed(d) if c.startswith("minishop/")]
    killed = []
    for name, f, old, new in MUTANTS:
        m = d + "_mut_" + name
        shutil.rmtree(m, ignore_errors=True)
        shutil.copytree(d, m, ignore=shutil.ignore_patterns(".git"))
        sub(os.path.join(m, f), old, new)
        okm, _ = pytest_ok(m, "-q tests/test_textutil_extra.py")
        if not okm:
            killed.append(name)
        shutil.rmtree(m, ignore_errors=True)
    core = all(k in killed for k in ("collapse", "strip", "eq_n"))
    return ok0 and core and not src_touched, f"pass={ok0} killed={killed} src_touched={src_touched}"


NOTES = "# NOTES\n\n- [x] 加 CLI report 命令\n- [ ] 修 summary 的 avg_order（好像算错了）\n- [ ] 给 catalog 加按价格排序 by_price()\n"


def seed_handoff(d):
    sub(d + "/minishop/report.py", "avg = round(revenue / len(orders), 2) if orders else 0.0",
        "avg = round(revenue / len(rows), 2) if rows else 0.0")
    open(d + "/NOTES.md", "w").write(NOTES)


@task("handoff", "写交接文件",
      "我准备 /clear 换个新会话接着做。请先看一下项目现状（读 NOTES.md、跑测试），然后在项目根目录写 HANDOFF.md，让新会话能直接接手："
      "包括目标、当前状态（哪些测试失败、原因在哪）、下一步要做什么。这次不要修代码。", seed_handoff)
def g_handoff(d, ans):
    p = d + "/HANDOFF.md"
    if not os.path.exists(p):
        return False, "no HANDOFF.md"
    t = open(p).read()
    fact = "test_summary" in t and ("len(rows)" in t or "avg_order" in t)
    nxt = "下一步" in t or "next" in t.lower()
    todo = "by_price" in t
    src = [c for c in changed(d) if c.startswith("minishop/")]
    return fact and nxt and todo and not src, f"fact={fact} next={nxt} todo={todo} src={src} len={len(t)}"


def seed_cli(d):
    sub(d + "/minishop/report.py", 'out[r["sku"]] += int(r["quantity"]) * float(r["unit_price"])',
        'out[r["sku"]] += int(r["qty"]) * float(r["unit_price"])')


@task("cli_crash", "按报错修 bug",
      "运行 python3 -m minishop.cli report data/orders.csv 会报错崩溃。请修好它，让它正常打印报告，并确认全部测试通过。不要改 data/ 和 tests/。",
      seed_cli)
def g_cli(d, ans):
    import csv
    from decimal import Decimal, ROUND_HALF_UP
    r2 = lambda x: Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    per = collections.defaultdict(float)
    for r in csv.DictReader(open(d + "/data/orders.csv")):
        per[r["sku"]] += int(r["quantity"]) * float(r["unit_price"])
    rev = r2(sum(float(r2(v)) for v in per.values()))
    rc, out = sh(f"{PY} -m minishop.cli report data/orders.csv", d)
    ok2, _ = pytest_ok(d)
    bad = [c for c in changed(d) if c.startswith(("tests/", "data/"))]
    want = f"${rev:,.2f}"
    return rc == 0 and want in out and ok2 and not bad, f"rc={rc} want={want} pytest={ok2} bad={bad}"


HID_ITEMS = '''
from minishop.orders import Order
def test_i():
    assert Order("x").item_count() == 0
    assert Order("x").add_line("ABC-0001", 2, 1.0).add_line("XYZ-0002", 5, 1.0).item_count() == 7
'''


@task("long5", "长对话 5 步", [
    "读 minishop/orders.py 和 minishop/pricing.py，用不超过 4 行说明一个订单的总价是怎么算出来的。先不要改代码。",
    "给 Order 类加一个方法 item_count()，返回所有订单行 qty 之和（没有行时返回 0）。",
    "在 tests/test_orders.py 里给 item_count 加测试，然后跑全部测试，有失败就修好。",
    "在 README.md 里加一节 '## Order API'，用两三句话介绍 subtotal()、item_count() 和 calc_total()。",
    "用不超过 3 行总结这次对话一共改了哪些文件。"])
def g_long5(d, answers):
    ok, _ = hidden(d, "items", HID_ITEMS)
    ok2, _ = pytest_ok(d)
    rd = open(d + "/README.md").read()
    readme = "## Order API" in rd and "item_count" in rd
    last = answers[-1] if answers else ""
    summ = "orders.py" in last and "README" in last
    t1 = bool(answers) and ("tax" in answers[0].lower() or "税" in answers[0])
    return ok and ok2 and readme and summ and t1, f"hidden={ok} pytest={ok2} readme={readme} summary={summ} step1={t1}"


LONG_N = 10
LONG_STEPS = [f"请用 Read 工具完整读一遍 docs/handbook/part{k:02d}.md（要通读全文，不要用 grep、也不要只读一部分），然后回答："
              f"里程碑 {k} 的代号是什么？再用一句话概括这一部分讲什么。" for k in range(1, LONG_N + 1)]
LONG_STEPS.append(f"把前面找到的里程碑 1 到 {LONG_N} 的代号按顺序列出来，一行一个，格式「k: 代号」。")


@task("long_ctx", "长会话（上下文守卫场景）", LONG_STEPS, timeout=1500, budget=15)
def g_long_ctx(d, answers):
    names = bench_fixture.HANDBOOK_CODENAMES[:LONG_N]
    per = [i < len(answers) and names[i] in answers[i] for i in range(LONG_N)]
    final = answers[-1] if len(answers) > LONG_N else ""
    pos = [final.find(n) for n in names]
    recall = all(p >= 0 for p in pos) and pos == sorted(pos)
    return all(per) and recall, f"steps_ok={sum(per)}/{LONG_N} recall={recall}"


# 第 7 轮（所有者 00:05 定）：大上下文 + 多轮小追加。先通读两份大文件把上下文撑大，再做 6 次很小的改动。
BIGCTX_STEPS = [
    "请用 Read 工具完整通读 docs/DESIGN.md 和 logs/app.log（不要用 grep、不要只读一部分），然后用两句话概括支付流程。先不要改代码。",
    "在 minishop/config.py 的 TAX_RATES 里加一个地区 \"NV\"，税率 0.0685。",
    "把 minishop/config.py 里的 FREE_SHIPPING_THRESHOLD 改成 80.0。tests/ 里如果有依赖旧值 75 的测试，一起改成和 80 一致。",
    "在 minishop/textutil.py 新增函数 initials(s: str) -> str：取每个以空白分隔的单词的首字母并大写，空字符串返回空字符串。",
    "在 minishop/__init__.py 里把 __version__ 改成 \"1.1.0\"。",
    "在 minishop/money.py 新增 cents(x) -> int：先用 round_money 取整到分，再返回总分数（例如 2.675 -> 268）。",
    "跑一遍全部测试（python3 -m pytest -q），有失败就修好；最后用不超过 3 行列出这次对话一共改了哪些文件。"]

HID_BIGCTX = '''
import minishop
from minishop.config import TAX_RATES, FREE_SHIPPING_THRESHOLD
from minishop.textutil import initials
from minishop.money import cents
def test_b():
    assert TAX_RATES["NV"] == 0.0685 and TAX_RATES["CA"] == 0.0725
    assert FREE_SHIPPING_THRESHOLD == 80.0
    assert initials("hello big world") == "HBW" and initials("") == "" and initials("  a  b ") == "AB"
    assert minishop.__version__ == "1.1.0"
    assert cents(2.675) == 268 and cents(0.125) == 13 and cents(3) == 300
'''


@task("bigctx_edits", "大上下文 + 多轮小追加", BIGCTX_STEPS, timeout=1500, budget=12)
def g_bigctx(d, answers):
    ok, _ = hidden(d, "bigctx", HID_BIGCTX)
    ok2, _ = pytest_ok(d)
    last = answers[-1] if answers else ""
    summ = all(f in last for f in ("config.py", "textutil.py", "money.py"))
    t1 = bool(answers) and ("webhook" in answers[0].lower() or "支付" in answers[0])
    return ok and ok2 and summ and t1, f"hidden={ok} pytest={ok2} summary={summ} step1={t1}"


# 第 7 轮（所有者 00:05 定）：长测试输出、不加管道——验证 test_filter 在 -v 长输出里是否保住唯一的失败。
LONGOUT_N = 1500
LONGOUT_BAD = 977  # 第 977 个用例是 2.675；表里只有它失败（另有 7 个原有测试失败），评分要求点名这一条


def _longout_values():
    vals = [round(i * 0.01 + 0.004, 3) for i in range(LONGOUT_N)]  # x.xx4 -> half-up 和 round() 一致
    vals[LONGOUT_BAD] = 2.675
    return vals


def seed_longout(d):
    sub(d + "/minishop/money.py",
        'return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))', "return round(x, 2)")
    open(d + "/tests/test_money_table.py", "w").write(
        "import pytest\nfrom decimal import Decimal, ROUND_HALF_UP\nfrom minishop.money import round_money\n\n"
        f"VALUES = {_longout_values()!r}\n\n\n"
        "@pytest.mark.parametrize('x', VALUES, ids=[f'v{i:04d}' for i in range(len(VALUES))])\n"
        "def test_round_money_table(x):\n"
        "    want = float(Decimal(str(x)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))\n"
        "    assert round_money(x) == want\n")


@task("longout_triage", "长测试输出（不加管道）",
      "用 python3 -m pytest -v 跑全部测试（直接跑，不要加 | 管道、grep、tail 或 -q），告诉我哪些测试用例失败了"
      "（给出完整的测试 id 和输入值）、根因在哪个文件的哪个函数。只诊断，不要修改任何文件。", seed_longout)
def g_longout(d, ans):
    a = ans.lower()
    tid = f"v{LONGOUT_BAD:04d}" in a
    val = "2.675" in a
    root = "round_money" in a or "money.py" in a
    ch = changed(d)
    return tid and val and root and not ch, f"id={tid} val={val} root={root} changed={ch}"


# ============================================================== 跑一次
def fresh_project(path, t):
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    bench_fixture.make_fixture(path)
    if t["seed"]:
        t["seed"](path)            # 先埋种子再做唯一一次提交：git log/diff 里看不出埋了什么（smoke 里见过模型跑 git log --stat）
    sh("git init -q . && git add -A && git -c user.email=bench@local -c user.name=bench commit -qm init", path)


def build_plugin(arm, root):
    if not arm.get("skills"):
        return []
    pd = os.path.join(root, "plugin_usage_saver")
    os.makedirs(pd + "/.claude-plugin", exist_ok=True)
    os.makedirs(pd + "/skills", exist_ok=True)
    json.dump({"name": "usage-saver", "version": "0.0.0", "description": "bench copy of suite skills"},
              open(pd + "/.claude-plugin/plugin.json", "w"))
    for s in arm["skills"]:
        dst = os.path.join(pd, "skills", os.path.basename(s.rstrip("/")))
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(s, dst)
    return [pd]


def parse_stream(text):
    ev = []
    for l in text.splitlines():
        if l.startswith("{"):
            try:
                ev.append(json.loads(l))
            except Exception:
                pass
    res = next((e for e in reversed(ev) if e.get("type") == "result"), {})
    hooks = collections.Counter(e.get("hook_name") for e in ev if e.get("type") == "system" and e.get("subtype") == "hook_started")
    guard_inject = sum(1 for e in ev if e.get("subtype") == "hook_response" and e.get("hook_event") == "UserPromptSubmit"
                       and (str(e.get("output") or "") + str(e.get("stdout") or "")).strip())
    tools = collections.Counter()
    ctx, seen, first = [], set(), None
    filtered = 0
    for e in ev:
        if e.get("type") == "assistant":
            m = e.get("message", {})
            for c in m.get("content", []) or []:
                if c.get("type") == "tool_use":
                    tools[c.get("name")] += 1
            u = m.get("usage") or {}
            if m.get("id") not in seen and e.get("parent_tool_use_id") is None:
                seen.add(m.get("id"))
                if first is None:
                    first = dict(cache_creation=u.get("cache_creation_input_tokens", 0), cache_read=u.get("cache_read_input_tokens", 0))
                ctx.append(u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0))
        elif e.get("type") == "user":
            for c in (e.get("message", {}).get("content") or []):
                if isinstance(c, dict) and c.get("type") == "tool_result" and "[测试输出" in json.dumps(c.get("content"), ensure_ascii=False):
                    filtered += 1
    init = next((e for e in ev if e.get("subtype") == "init"), {})
    return dict(result=res, hooks=dict(hooks), guard_inject=guard_inject, tools=dict(tools), ctx=ctx, test_filtered=filtered,
                session_id=res.get("session_id") or init.get("session_id"), skills=init.get("skills"), model=init.get("model"),
                first=first, version=init.get("claude_code_version"))


def usage_of(res):
    """一个 result 事件 → 本进程的各 token 合计（含子 agent）。
    实测（CC 2.1.318）：--resume 接着跑时 modelUsage 和 total_cost_usd 是"整个会话累计"，
    model_usage_since_last_result 才是本进程这一段，所以优先用后者，费用也从它的 costUSD 加总。"""
    z = dict(input=0, cache_creation=0, cache_read=0, output=0, cw5m=0, cw1h=0, cost_usd=0.0)
    mu = res.get("model_usage_since_last_result") or ({} if res.get("is_error") else res.get("modelUsage")) or {}
    # 实测：出错（如 5 小时额度用完）的 result 没有 since 字段，modelUsage 又是会话累计，不能用，退回本进程的 usage
    if mu:
        for m in mu.values():
            z["input"] += m.get("inputTokens", 0); z["output"] += m.get("outputTokens", 0)
            z["cache_read"] += m.get("cacheReadInputTokens", 0); z["cache_creation"] += m.get("cacheCreationInputTokens", 0)
            z["cw5m"] += m.get("cacheCreationEphemeral5mInputTokens", 0); z["cw1h"] += m.get("cacheCreationEphemeral1hInputTokens", 0)
            z["cost_usd"] += m.get("costUSD", 0.0)
            z["thinking"] = z.get("thinking", 0) + m.get("thinkingTokens", 0)
        return z
    else:
        u = res.get("usage") or {}
        z.update(input=u.get("input_tokens", 0), output=u.get("output_tokens", 0), cache_read=u.get("cache_read_input_tokens", 0),
                 cache_creation=u.get("cache_creation_input_tokens", 0))
        cc = u.get("cache_creation") or {}
        z["cw5m"], z["cw1h"] = cc.get("ephemeral_5m_input_tokens", 0), cc.get("ephemeral_1h_input_tokens", 0)
    z["cost_usd"] = res.get("total_cost_usd") or 0.0
    return z


def add_usage(a, b):
    return {k: a.get(k, 0) + b.get(k, 0) for k in set(a) | set(b)}


def weighted(u):
    cw_other = max(0, u.get("cache_creation", 0) - u.get("cw5m", 0) - u.get("cw1h", 0))   # 未细分的按 5m
    return (W_IN * u.get("input", 0) + W_CR * u.get("cache_read", 0) + W_CW5 * (u.get("cw5m", 0) + cw_other)
            + W_CW1H * u.get("cw1h", 0) + W_OUT * u.get("output", 0))


class ClaudeAgent:
    name = "claude"

    def __init__(self, model, effort):
        self.model, self.effort = model, effort

    def run_step(self, prompt, cwd, arm, root, session, timeout, budget, raw_path):
        sfile = os.path.join(root, "settings.json")
        model = arm.get("model") or self.model
        if not os.path.exists(sfile):
            st = dict(BASE_SETTINGS, **arm.get("settings", {}))
            if arm.get("effort_setting"):
                st["modelSettings"] = {MODEL_IDS.get(model, model): {"effortLevel": arm["effort_setting"]}}
            json.dump(st, open(sfile, "w"), ensure_ascii=False, indent=1)
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--include-hook-events",
               "--setting-sources", "project,local", "--settings", sfile, "--model", model,
               "--permission-mode", "bypassPermissions", "--max-budget-usd", str(budget)]
        if not arm.get("effort_setting"):
            cmd += ["--effort", self.effort]
        for pd in build_plugin(arm, root):
            cmd += ["--plugin-dir", pd]
        cmd += arm.get("extra_args", [])
        if session:
            cmd += ["--resume", session]
        env = dict(os.environ, USAGE_SAVER_STATE=os.path.join(root, "state"))
        for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
            env.pop(k, None)
        os.makedirs(env["USAGE_SAVER_STATE"], exist_ok=True)
        t0 = time.time()
        try:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
            out, rc = p.stdout, p.returncode
            err = p.stderr[-500:]
        except subprocess.TimeoutExpired as e:
            out, rc, err = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), -9, "timeout"
        with gzip.open(raw_path, "wt") as f:
            f.write(out)
        ps = parse_stream(out)
        r0 = ps["result"]
        infra = None
        if not r0 or (r0.get("is_error") and any(w in str(r0.get("result", "")).lower() for w in ("usage limit", "rate limit", "limit reached", "overloaded"))):
            infra = (str(r0.get("result", "")) or err or "no result event")[:200]
        ps.update(infra_error=infra, rc=rc, stderr=err, dur=round(time.time() - t0, 1), usage=usage_of(ps["result"]),
                  answer=str(ps["result"].get("result", "")), is_error=ps["result"].get("is_error"),
                  num_turns=ps["result"].get("num_turns"))
        return ps


class CodexAgent:
    """codex exec --json：每个 turn.completed 带 usage（input_tokens 含缓存部分、cached_input_tokens、output_tokens）。
    多步任务用 `codex exec resume <thread_id>`。Codex 没有 hooks，arm 只能通过 extra_args（-c 覆盖等）区分。"""
    name = "codex"

    def __init__(self, model=None, effort="high"):
        self.model, self.effort = model, effort

    def run_step(self, prompt, cwd, arm, root, session, timeout, budget, raw_path):
        base = ["codex", "exec"]
        if session:
            base += ["resume", session]
        cmd = base + ["--json", "--skip-git-repo-check", "-c", f'model_reasoning_effort="{self.effort}"']
        if not session:
            cmd += ["-s", "workspace-write", "-C", cwd]
        if self.model:
            cmd += ["-m", self.model]
        cmd += arm.get("extra_args", []) + [prompt]
        t0 = time.time()
        try:
            p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                               env=dict(os.environ, TMPDIR=root))
            out, rc, err = p.stdout, p.returncode, p.stderr[-500:]
        except subprocess.TimeoutExpired:
            out, rc, err = "", -9, "timeout"
        with gzip.open(raw_path, "wt") as f:
            f.write(out + "\n#stderr\n" + err)
        u = dict(input=0, cache_creation=0, cache_read=0, output=0, cw5m=0, cw1h=0, cost_usd=0.0)
        answer, sid, turns, tools, infra = "", session, 0, collections.Counter(), None
        for l in out.splitlines():
            try:
                e = json.loads(l)
            except Exception:
                continue
            t = e.get("type")
            if t == "thread.started":
                sid = e.get("thread_id") or sid
            elif t == "turn.completed":
                x = e.get("usage") or {}
                turns += 1
                cached = x.get("cached_input_tokens", 0)
                u["input"] += x.get("input_tokens", 0) - cached
                u["cache_read"] += cached
                u["output"] += x.get("output_tokens", 0)
            elif t in ("error", "turn.failed"):
                msg = str(e.get("message") or (e.get("error") or {}).get("message") or "")
                if any(w in msg.lower() for w in ("usage limit", "rate limit", "quota")):
                    infra = msg[:200]
            elif t == "item.completed":
                it = e.get("item") or {}
                if it.get("type") == "agent_message":
                    answer = it.get("text", answer)
                elif it.get("type"):
                    tools[it["type"]] += 1
        return dict(rc=rc, stderr=err, dur=round(time.time() - t0, 1), usage=u, answer=answer, session_id=sid, infra_error=infra,
                    is_error=rc != 0, num_turns=turns, hooks={}, guard_inject=0, tools=dict(tools), ctx=[], test_filtered=0)


def advises_clear(ans):
    return "/clear" in ans


def run_one(agent, t, arm, rep, run_id):
    root = os.path.join(WORK, run_id, f"{t['id']}__{arm['name']}__r{rep}")
    proj = os.path.join(root, "proj")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    fresh_project(proj, t)
    rawdir = os.path.join(RESULTS, "raw", run_id)
    os.makedirs(rawdir, exist_ok=True)
    total = {}
    answers, steps, session, clears, infra = [], [], None, 0, None
    t0 = time.time()
    i = 0
    retried = set()
    while i < len(t["steps"]):
        raw = os.path.join(rawdir, f"{t['id']}__{arm['name']}__r{rep}__s{len(steps)}.jsonl.gz")
        st = agent.run_step(t["steps"][i], proj, arm, root, session, t["timeout"], t["budget"], raw)
        total = add_usage(total, st["usage"])
        session = st.get("session_id") or session
        if st.get("infra_error"):
            infra = st["infra_error"]
            break                                  # 额度/限流：后面的步骤不再跑，整条记录不进统计
        steps.append({k: st.get(k) for k in ("rc", "dur", "usage", "hooks", "guard_inject", "tools", "test_filtered",
                                             "num_turns", "is_error", "stderr", "first", "model", "version")} | {"max_ctx": max(st.get("ctx") or [0]),
                                                                                     "n_req": len(st.get("ctx") or []),
                                                                                     "answer_head": st["answer"][:300]})
        # 所有者模拟：回复里建议 /clear 就照做（两组同一规则）。多步任务才有意义。
        if len(t["steps"]) > 1 and agent.name == "claude" and advises_clear(st["answer"]) and i < len(t["steps"]) - 1:
            raw = os.path.join(rawdir, f"{t['id']}__{arm['name']}__r{rep}__s{len(steps)}_clear.jsonl.gz")
            cl = agent.run_step("/clear", proj, arm, root, session, 300, 1, raw)
            total = add_usage(total, cl["usage"])
            session = cl.get("session_id") or session
            clears += 1
            steps.append(dict(clear=True, usage=cl["usage"], hooks=cl.get("hooks"), dur=cl.get("dur")))
            # 这一步若没答上（被守卫打断去写交接），清空后重问一次
            if t["id"] == "long_ctx" and i < LONG_N and bench_fixture.HANDBOOK_CODENAMES[i] not in st["answer"] and i not in retried:
                retried.add(i)
                continue
        answers.append(st["answer"])
        i += 1
    try:
        ok, note = t["grade"](proj, answers if len(t["steps"]) > 1 else answers[-1])
    except Exception as e:
        ok, note = False, f"grader error {e!r}"
    rec = dict(run_id=run_id, ts=time.strftime("%Y-%m-%d %H:%M:%S"), agent=agent.name, model=arm.get("model") or getattr(agent, "model", None),
               effort=arm.get("effort_setting") or getattr(agent, "effort", None), fixture=bench_fixture.FIXTURE_VERSION, task=t["id"], cat=t["cat"],
               arm=arm["name"], arm_hash=arm_hash(arm), rep=rep, ok=bool(ok), note=note, dur=round(time.time() - t0),
               infra_error=infra, clears=clears, usage=total, model_id=steps[0].get("model"), cc_version=steps[0].get("version"),
               first_req=steps[0].get("first"), skills_loaded=[x for x in (st.get("skills") or []) if x.startswith("usage-saver")], weighted=round(weighted(total)), cost_usd=round(total.get("cost_usd", 0), 4),
               n_req=sum(s.get("n_req", 0) for s in steps), max_ctx=max([s.get("max_ctx", 0) for s in steps] or [0]),
               hooks=dict(sum((collections.Counter(s.get("hooks") or {}) for s in steps), collections.Counter())),
               guard_inject=sum(s.get("guard_inject", 0) for s in steps), test_filtered=sum(s.get("test_filtered", 0) for s in steps),
               tools=dict(sum((collections.Counter(s.get("tools") or {}) for s in steps), collections.Counter())), steps=steps)
    return rec


FIVE_STOP_PCT = float(os.environ.get("BENCH_STOP_5H_PCT", 85))


def week_used():
    """预算保护：周用量 ≥ STOP_PCT 或 5 小时窗口 ≥ FIVE_STOP_PCT 都停（试点里实测：只看周用量不够，
    并行 agent 一起把 5 小时窗口用到了 99%）。返回"超出程度"最大的那个，超线时返回 ≥ STOP_PCT 的数。"""
    try:
        rl = json.load(open(BASE + "/state/rate.json"))["rate_limits"]
        w = rl["seven_day"]["used_percentage"]
        f = (rl.get("five_hour") or {}).get("used_percentage") or 0
        return max(w, STOP_PCT if f >= FIVE_STOP_PCT else 0)
    except Exception:
        return None


PILOT = ["fix_three", "triage", "feature_coupon", "doc_qa", "extract_validate", "handoff", "long5", "long_ctx"]
WARMUP_PROMPT = "只回复 ok 两个字母，不要调用任何工具。"


def schedule(tasks, arms, reps, seed, rep_offset=0):
    """F1 §5.2：每次重复重新打乱任务顺序；同一任务的各臂背靠背、先后随机。返回 [(rep, [(task, arm), ...]), ...]。"""
    rnd = random.Random(seed)
    blocks = []
    for r in range(rep_offset, rep_offset + reps):
        pairs = []
        for t in rnd.sample(tasks, len(tasks)):
            order = list(arms)
            rnd.shuffle(order)
            pairs.append((t, order))
        blocks.append((r, pairs))
    return blocks


def warmup(agent, arm, run_id, tag):
    """每块开头每臂一次热身（不计入结果）：让系统提示/工具定义前缀的缓存两臂一样热。"""
    root = os.path.join(WORK, run_id, f"_warmup__{arm['name']}__{tag}")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root + "/proj")
    bench_fixture.make_fixture(root + "/proj")
    raw = os.path.join(RESULTS, "raw", run_id, f"_warmup__{arm['name']}__{tag}.jsonl.gz")
    os.makedirs(os.path.dirname(raw), exist_ok=True)
    st = agent.run_step(WARMUP_PROMPT, root + "/proj", arm, root, None, 300, 1, raw)
    return dict(run_id=run_id, warmup=True, arm=arm["name"], tag=tag, usage=st["usage"], first=st.get("first"))


def cmd_run(a):
    agent = CodexAgent(a.codex_model, a.effort) if a.agent == "codex" else ClaudeAgent(a.model, a.effort)
    arms = [load_arm(x) for x in a.config.split(",")]
    run_id = time.strftime("%Y%m%d-%H%M%S") + f"_{a.agent}_{'+'.join(x['name'] for x in arms)}"
    if not a.live_suite:
        arms = snapshot_suite(arms, os.path.join(WORK, run_id))
    names = a.tasks.split(",") if a.tasks else (PILOT if a.pilot else list(TASKS))
    tasks = [TASKS[x] for x in names]
    if a.agent == "codex":
        tasks = [t for t in tasks if len(t["steps"]) == 1 or a.allow_multi]
    os.makedirs(RESULTS, exist_ok=True)
    out = os.path.join(RESULTS, f"token_bench_{run_id}.jsonl")
    blocks = schedule(tasks, arms, a.repeats, a.seed, a.rep_offset)
    n = sum(len(p) * len(arms) for _, p in blocks)
    print(f"run {run_id}: {n} 次（{len(tasks)} 任务 × {len(arms)} 组 × {a.repeats} 次）+ 每块每臂 1 次热身，"
          f"workers={a.workers}，model={a.model if a.agent=='claude' else a.codex_model} → {out}", flush=True)
    lock = threading.Lock()
    stop = threading.Event()

    def write(r):
        with lock:
            open(out, "a").write(json.dumps(r, ensure_ascii=False) + "\n")

    def pair_job(x):
        rep, t, order = x
        for arm in order:                       # 同一任务的各臂背靠背
            if stop.is_set():
                return
            u = week_used()
            if u is not None and u >= STOP_PCT:
                stop.set(); print(f"周用量 {u}% ≥ {STOP_PCT}%，停止", flush=True); return
            try:
                r = run_one(agent, t, arm, rep, run_id)
            except Exception as e:
                r = dict(run_id=run_id, task=t["id"], arm=arm["name"], rep=rep, ok=False, error=repr(e), agent=agent.name)
            r["order"] = [x["name"] for x in order].index(arm["name"])
            write(r)
            if r.get("infra_error"):
                stop.set(); print("额度/限流错误，停止整轮：", r["infra_error"], flush=True)
            print(f"{t['id']:16s} {arm['name']:6s} r{rep} ok={r.get('ok')} cost=${r.get('cost_usd')} weighted={r.get('weighted')} "
                  f"req={r.get('n_req')} maxctx={r.get('max_ctx')} clears={r.get('clears')} guard={r.get('guard_inject')} "
                  f"tf={r.get('test_filtered')} {r.get('dur')}s {str(r.get('note') or r.get('error'))[:100]}", flush=True)

    for rep, pairs in blocks:
        if stop.is_set():
            break
        if not a.no_warmup:
            for arm in arms:
                write(warmup(agent, arm, run_id, f"r{rep}"))
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            list(ex.map(pair_job, [(rep, t, order) for t, order in pairs]))
    print("完成 →", out)
    if len(arms) >= 2 and not a.no_analyze:
        cmd_analyze(argparse.Namespace(files=[out], base=arms[0]["name"], treat=arms[1]["name"], boot=10000, md=True,
                                       agent=a.agent, metric="cost_usd"))


# ============================================================== 汇总（按 F1 §5.2–5.3：分析单位 = 任务）
def _load(files, agent):
    recs = []
    for f in files:
        for l in open(f):
            try:
                r = json.loads(l)
            except Exception:
                continue
            if r.get("warmup") or "usage" not in r or r.get("infra_error"):
                continue                       # 额度/限流等基础设施失败不算任务失败，也不进统计（要补跑）
            if r.get("agent", "claude") == agent and r.get("fixture", bench_fixture.FIXTURE_VERSION) == bench_fixture.FIXTURE_VERSION:
                r["u_input"], r["u_cache_creation"] = r["usage"].get("input", 0), r["usage"].get("cache_creation", 0)
                r["u_cache_read"], r["u_output"] = r["usage"].get("cache_read", 0), r["usage"].get("output", 0)
                recs.append(r)
    return recs


def task_table(recs, base, treat, key):
    """{task: dict(la, lb, ca, cb, pa, pb, ra, rb, sa, sb)}：任务内对重复取平均（log 均值、算术均值、通过率、组内 sd）。"""
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in recs:
        by[r["task"]][r["arm"]].append(r)
    T = {}
    for t, d in by.items():
        A, B = d.get(base, []), d.get(treat, [])
        if not A or not B:
            continue
        la = [math.log(max(1e-9, r[key])) for r in A]; lb = [math.log(max(1e-9, r[key])) for r in B]
        sd = lambda v: (sum((x - sum(v) / len(v)) ** 2 for x in v) / (len(v) - 1)) ** 0.5 if len(v) > 1 else None
        T[t] = dict(la=sum(la) / len(la), lb=sum(lb) / len(lb), ca=sum(r[key] for r in A) / len(A), cb=sum(r[key] for r in B) / len(B),
                    pa=sum(r["ok"] for r in A) / len(A), pb=sum(r["ok"] for r in B) / len(B), ra=len(A), rb=len(B),
                    sa=sd(la), sb=sd(lb), wa=[x for x in la], wb=[x for x in lb])
    return T


def _tq(p, df):
    try:
        from scipy import stats
        return stats.t.ppf(p, df)
    except Exception:
        return {0.975: 1.96, 0.8: 0.84}.get(p, 1.96)


def n_needed(sigma_d, mde, alpha=0.05, power=0.8):
    """F1 公式：MDE(相对) = 1 − exp(−(t_{1−α/2,n−1} + t_{power,n−1}) σ_d / √n)，解最小 n。"""
    target = -math.log(1 - mde)
    for n in range(3, 5000):
        if (_tq(1 - alpha / 2, n - 1) + _tq(power, n - 1)) * sigma_d / math.sqrt(n) <= target:
            return n
    return None


def analyze_core(recs, base, treat, key, boot=10000, seed=0):
    T = task_table(recs, base, treat, key)
    ts = sorted(T)
    n = len(ts)
    d = [T[t]["lb"] - T[t]["la"] for t in ts]
    m = sum(d) / n
    sdd = (sum((x - m) ** 2 for x in d) / (n - 1)) ** 0.5 if n > 1 else float("nan")
    tq = _tq(0.975, n - 1) if n > 1 else float("nan")
    geo = (math.exp(m), math.exp(m - tq * sdd / math.sqrt(n)), math.exp(m + tq * sdd / math.sqrt(n)))
    ca = [T[t]["ca"] for t in ts]; cb = [T[t]["cb"] for t in ts]
    dp = [T[t]["pb"] - T[t]["pa"] for t in ts]
    rng = random.Random(seed)
    sr, DP = [], []
    for _ in range(boot):
        idx = [rng.randrange(n) for _ in range(n)]
        sa = sum(ca[i] for i in idx)
        sr.append(sum(cb[i] for i in idx) / sa if sa else float("nan"))
        DP.append(sum(dp[i] for i in idx) / n)
    sr.sort(); DP.sort()
    q = lambda v, p: v[min(len(v) - 1, max(0, int(p * len(v))))]
    # 方差分量：σ_ε = 同一任务同一臂重复之间 log 成本的合并 sd；σ_τ² = Var(d) − σ_ε²(1/rA + 1/rB)
    ss, dfree = 0.0, 0
    for t in ts:
        for w in (T[t]["wa"], T[t]["wb"]):
            if len(w) > 1:
                mu = sum(w) / len(w)
                ss += sum((x - mu) ** 2 for x in w); dfree += len(w) - 1
    s_eps = (ss / dfree) ** 0.5 if dfree else None
    r_mean = sum(T[t]["ra"] + T[t]["rb"] for t in ts) / (2 * n)
    s_tau2 = (sdd ** 2 - (2 * s_eps ** 2 / r_mean)) if s_eps is not None and n > 1 else None
    return dict(T=T, tasks=ts, n=n, d=d, sd_d=sdd, geo=geo, sum_ratio=(sum(cb) / sum(ca), q(sr, 0.025), q(sr, 0.975)),
                pass_a=sum(T[t]["pa"] for t in ts) / n, pass_b=sum(T[t]["pb"] for t in ts) / n,
                pass_diff=(sum(dp) / n, q(DP, 0.025), q(DP, 0.975)), s_eps=s_eps, s_tau2=s_tau2, r_mean=r_mean)


def plan_table(s_eps, s_tau2, rs=(3, 5), mdes=(0.05, 0.10, 0.20)):
    rows = []
    st2 = max(0.0, s_tau2 or 0.0)
    for r in rs:
        sd = (st2 + 2 * s_eps ** 2 / r) ** 0.5
        rows.append((r, sd, [n_needed(sd, m) for m in mdes]))
    return rows


def cmd_analyze(a):
    files = a.files or sorted(glob.glob(os.path.join(RESULTS, "token_bench_*.jsonl")))
    recs = _load(files, a.agent)
    hashes = collections.defaultdict(set)
    meta = collections.defaultdict(set)
    for r in recs:
        hashes[r.get("arm")].add(r.get("arm_hash"))
        meta["model"].add(r.get("model_id")); meta["cc"].add(r.get("cc_version")); meta["effort"].add(r.get("effort"))
    key = a.metric
    R = analyze_core(recs, a.base, a.treat, key, a.boot)
    if R["n"] < 2:
        print("可配对的任务不足 2 个"); return
    L = [f"# token 基准：{a.treat} vs {a.base}（{time.strftime('%Y-%m-%d %H:%M')}）", "",
         f"- 结果文件：{', '.join(os.path.basename(f) for f in files)}",
         f"- 任务数 n={R['n']}，每任务每臂平均重复 {R['r_mean']:.1f} 次；fixture v{bench_fixture.FIXTURE_VERSION}；"
         f"模型 {sorted(map(str, meta['model']))}；CC {sorted(map(str, meta['cc']))}；effort {sorted(map(str, meta['effort']))}",
         f"- 配置指纹 { {k: sorted(v) for k, v in hashes.items() if k in (a.base, a.treat)} }",
         f"- 主指标 = {key}；分析单位 = 任务（任务内先对重复求平均）。几何均值比用配对 t 区间；总量比、完成率差用按任务重抽的 bootstrap"
         f"（n<20 时百分位 bootstrap 偏窄，只作参考）", "",
         f"**几何均值比 R = {R['geo'][0]:.3f}（95% CI {R['geo'][1]:.3f}–{R['geo'][2]:.3f}）**；"
         f"总量比 = {R['sum_ratio'][0]:.3f}（{R['sum_ratio'][1]:.3f}–{R['sum_ratio'][2]:.3f}）", "",
         f"完成率（任务等权）：{a.base} {R['pass_a']:.1%}，{a.treat} {R['pass_b']:.1%}，差 {R['pass_diff'][0]*100:+.1f} 个百分点"
         f"（95% CI {R['pass_diff'][1]*100:+.1f}–{R['pass_diff'][2]*100:+.1f}）", ""]
    L += ["次指标（只解释不判定）：", "", "| 指标 | 几何均值比 | 95% CI | 总量比 |", "|---|---|---|---|"]
    for k2 in ("cost_usd", "weighted", "n_req", "max_ctx", "u_input", "u_cache_creation", "u_cache_read", "u_output", "dur"):
        if k2 == key:
            continue
        try:
            S = analyze_core(recs, a.base, a.treat, k2, 2000)
            L.append(f"| {k2} | {S['geo'][0]:.3f} | {S['geo'][1]:.3f}–{S['geo'][2]:.3f} | {S['sum_ratio'][0]:.3f} |")
        except Exception as e:
            L.append(f"| {k2} | 无法计算 {e!r} | | |")
    L += ["", f"| 任务 | r({a.base}/{a.treat}) | {a.base} {key} 均值 | {a.treat} 均值 | 比值(几何) | 组内 sd(log) A/B | 完成 A | 完成 B "
          f"| maxctx A→B | guard注入 B | test_filter B | /clear B |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    by = collections.defaultdict(list)
    for r in recs:
        by[(r["task"], r["arm"])].append(r)
    for t in R["tasks"]:
        x = R["T"][t]; Bs = by[(t, a.treat)]; As = by[(t, a.base)]
        f = lambda v: "-" if v is None else f"{v:.2f}"
        L.append(f"| {t} | {x['ra']}/{x['rb']} | {x['ca']:,.4g} | {x['cb']:,.4g} | {math.exp(x['lb']-x['la']):.3f} | {f(x['sa'])}/{f(x['sb'])} "
                 f"| {x['pa']:.0%} | {x['pb']:.0%} | {sum(r['max_ctx'] for r in As)//len(As):,}→{sum(r['max_ctx'] for r in Bs)//len(Bs):,} "
                 f"| {sum(r.get('guard_inject',0) for r in Bs)} | {sum(r.get('test_filtered',0) for r in Bs)} | {sum(r.get('clears',0) for r in Bs)} |")
    if R["s_eps"] is not None:
        L += ["", f"方差分量（log {key}）：σ_ε（重复噪声）= {R['s_eps']:.3f}；sd(d_i) = {R['sd_d']:.3f}；"
              f"σ_τ²（效应的任务间异质性）= {R['s_tau2']:.4f}（负值按 0 处理）", "",
              "正式 A/B 所需任务数（F1 公式，α=0.05 双侧，检验力 80%）：", "",
              "| 每任务每臂重复 r | σ_d | MDE 5% | MDE 10% | MDE 20% |", "|---|---|---|---|---|"]
        for r, sd, ns in plan_table(R["s_eps"], R["s_tau2"]):
            L.append(f"| {r} | {sd:.3f} | {ns[0]} | {ns[1]} | {ns[2]} |")
    txt = "\n".join(L) + "\n"
    print(txt)
    if a.md:
        p = os.path.join(RESULTS, f"summary_{a.treat}_vs_{a.base}_{time.strftime('%Y%m%d-%H%M%S')}.md")
        open(p, "w").write(txt)
        print("→", p)


def cmd_selfcheck():
    """不调模型：每个任务 ① 种子后"什么都不做"必须判失败；② 修 bug 类任务把种子撤回后必须判通过。"""
    bad = 0
    for t in TASKS.values():
        d = os.path.join(WORK, "_selfcheck", t["id"])
        fresh_project(d, t)
        ans = "" if len(t["steps"]) == 1 else [""] * len(t["steps"])
        ok0, note0 = t["grade"](d, ans)
        line = f"{t['id']:18s} 空操作→{'通过(错!)' if ok0 else '失败(对)'}"
        if ok0:
            bad += 1
        if t["seed"] and t["id"] in ("fix_bulk", "fix_three", "cli_crash"):
            o = d + "_orig"
            shutil.rmtree(o, ignore_errors=True); bench_fixture.make_fixture(o)
            shutil.rmtree(d + "/minishop"); shutil.copytree(o + "/minishop", d + "/minishop"); shutil.rmtree(o)
            ok1, note1 = t["grade"](d, ans)
            line += f"；撤回种子→{'通过(对)' if ok1 else '失败(错!) ' + note1}"
            bad += not ok1
        print(line, "|", note0[:100])
    print("selfcheck", "OK" if not bad else f"{bad} 项不对")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default="A", help="A | B | B_hooks_only | codexA | 路径.json；逗号分隔多个则交错配对")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--rep-offset", type=int, default=0, help="rep 编号起点（分批补跑时用，保证配对编号不重复）")
    ap.add_argument("--tasks", default="")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--agent", default="claude", choices=["claude", "codex"])
    ap.add_argument("--model", default="opus", help="claude 模型别名；试点用 sonnet")
    ap.add_argument("--codex-model", default=None)
    ap.add_argument("--effort", default="high")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--allow-multi", action="store_true", help="codex 也跑多步任务（用 codex exec resume）")
    ap.add_argument("--no-analyze", action="store_true")
    ap.add_argument("--analyze", action="store_true")
    ap.add_argument("--base", default="A")
    ap.add_argument("--treat", default="B")
    ap.add_argument("--boot", type=int, default=10000)
    ap.add_argument("--md", action="store_true", help="--analyze 时把汇总写进 results/summary_*.md")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--pilot", action="store_true", help="只跑试点 8 个任务（PILOT）")
    ap.add_argument("--no-warmup", action="store_true")
    ap.add_argument("--live-suite", action="store_true", help="不做快照，直接用项目里正在被改的 hooks/skills")
    ap.add_argument("--metric", default="cost_usd", help="主指标：cost_usd（F1 §5.2.10）| weighted | n_req ...")
    ap.add_argument("--selfcheck", action="store_true", help="不调模型，检查评分器")
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    if a.list:
        for t in TASKS.values():
            print(f"{t['id']:18s} {t['cat']:20s} steps={len(t['steps'])}")
        for k, v in ARMS.items():
            print(f"[{k}] {v['desc']}")
        return
    if a.selfcheck:
        sys.exit(cmd_selfcheck())
    if a.analyze:
        return cmd_analyze(a)
    cmd_run(a)


if __name__ == "__main__":
    main()
