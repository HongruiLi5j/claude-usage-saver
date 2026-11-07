#!/usr/bin/env python3
"""共享价格表（第 6 轮 P）：按模型族/版本给出 API 标价，并换算成「Sonnet 5.5 当量 token」。

来源：Anthropic 官方定价页 https://platform.claude.com/docs/en/about-claude/pricing（2026-11-06 读取）。
页上的规则（原文意思）：
  - 缓存写入：5 分钟档 = 1.25× 基础输入价，1 小时档 = 2×；
  - 缓存读（hit/refresh）：标准是 0.1× 基础输入价，**但** Opus 5.5 和 Sonnet 5.5 是 0.05×，Fable 5.1 / Mythos 5.1 是 0.025×；
  - Haiku 5.5 的提示超过 100k token 时，所有类别都按高一档价格（5×）。
所以「缓存读 = 0.1×」只对旧模型成立；F1 说的 0.05 是**相对同一模型的基础输入价**，只对 5.5 代成立。本表按模型逐个写。

单位：
  usd(model, usage)        → 美元
  weighted(model, usage)   → Sonnet 5.5 当量 token = 美元 ÷ ($2 / 1M)。Opus 5.5 的同样 token 正好是 Sonnet 5.5 的 2 倍（F1 采用的额度折算）。
  native(model, usage)     → 「本模型单位」：按本模型自己的基础输入价归一（input=1），跨模型不可比，只为兼容旧报表。
未知模型：族认识、版本不认识 → 用该族最新已知价并标 flag="family_fallback"；族也不认识 → 按 Sonnet 5.5 计并标 flag="unknown"。
调用方必须把 flag 显示出来（不能悄悄算错）。"<synthetic>"（客户端合成的报错消息，usage 全 0）标 "synthetic"。

本文件只用标准库，导入很快（状态栏每 5 秒会 import 一次）。
"""
import re

REF_INPUT_USD = 2.0          # Sonnet 5.5 基础输入价（$/MTok）：当量 token 的基准
SOURCE = "platform.claude.com/docs/en/about-claude/pricing（2026-11-06 读取）"


def _p(inp, cr_mult, out):
    """按官方规则从基础输入价生成一行：写入 5m=1.25×、1h=2×；缓存读倍数逐模型给。单位 $/MTok。"""
    return {"inp": inp, "cw5m": inp * 1.25, "cw1h": inp * 2.0, "cr": inp * cr_mult, "out": out}


# (族, 版本) → 价格。版本用 "5.5" 这样的字符串。数值与官方页逐项核对过（见 tools/tests_r6P.py 的 P01）。
TABLE = {
    ("fable", "5.1"): _p(10.0, 0.025, 50.0),
    ("mythos", "5.1"): _p(10.0, 0.025, 50.0),
    ("fable", "5"): _p(10.0, 0.1, 50.0),
    ("mythos", "5"): _p(10.0, 0.1, 50.0),
    ("opus", "5.5"): _p(4.0, 0.05, 20.0),
    ("opus", "5"): _p(5.0, 0.1, 25.0),
    ("opus", "4.8"): _p(5.0, 0.1, 25.0),
    ("opus", "4.7"): _p(5.0, 0.1, 25.0),
    ("opus", "4.6"): _p(5.0, 0.1, 25.0),
    ("opus", "4.5"): _p(5.0, 0.1, 25.0),
    ("opus", "4.1"): _p(15.0, 0.1, 75.0),
    ("opus", "4"): _p(15.0, 0.1, 75.0),
    ("sonnet", "5.5"): _p(2.0, 0.05, 10.0),
    ("sonnet", "5"): _p(2.0, 0.1, 10.0),
    ("sonnet", "4.6"): _p(3.0, 0.1, 15.0),
    ("sonnet", "4.5"): _p(3.0, 0.1, 15.0),
    ("sonnet", "4"): _p(3.0, 0.1, 15.0),
    ("haiku", "5.5"): _p(0.10, 0.1, 0.50),       # 提示 ≤100k；>100k 见 HAIKU55_LONG
    ("haiku", "4.5"): _p(1.0, 0.1, 5.0),
}
HAIKU55_LONG = _p(0.50, 0.1, 2.50)
HAIKU55_LONG_AT = 100_000
LATEST = {"fable": "5.1", "mythos": "5.1", "opus": "5.5", "sonnet": "5.5", "haiku": "5.5"}   # 族回退用
DEFAULT = ("sonnet", "5.5")
FAMILIES = ("fable", "mythos", "opus", "sonnet", "haiku")
KINDS = ("inp", "cw5m", "cw1h", "cr", "out")

_cache = {}


def parse(model):
    """模型 id → (族, 版本 或 None)。认不出族返回 (None, None)。
    认：claude-opus-5-5、claude-opus-5-5-20260101、claude-opus-5-5[1m]、anthropic.claude-…、us.anthropic.…、…@2026…、
    旧式 claude-3-5-sonnet-2024…；只给族名（"opus"、"Sonnet 5.5"）也行。"""
    s = str(model or "").strip().lower()
    if not s:
        return None, None
    s = re.sub(r"\[.*?\]$", "", s)                 # [1m] 之类后缀
    s = s.split("@")[0]
    s = s.split("/")[-1]
    s = re.sub(r"^(?:[a-z]{2}\.)?anthropic\.", "", s)
    s = s.replace(" ", "-").replace("_", "-")
    fam = next((f for f in FAMILIES if re.search(r"(?:^|[-.])" + f + r"(?:$|[-.])", s)), None)
    if fam is None:
        return None, None
    i = s.find(fam)
    after = s[i + len(fam):]
    m = re.match(r"[-.]?(\d{1,2})(?:[-.](\d{1,2}))?(?=$|[-.])", after)
    if m:
        major, minor = m.group(1), m.group(2)
        return fam, major + ("." + minor if minor and minor != "0" else "")
    m = re.search(r"claude-(\d)(?:-(\d))?-" + fam, s)        # 旧式 claude-3-5-sonnet
    if m:
        return fam, m.group(1) + ("." + m.group(2) if m.group(2) else "")
    return fam, None


def lookup(model, prompt_tokens=None):
    """→ (价格 dict, flag)。flag：None（已知）/ "family_fallback" / "unknown" / "synthetic"。"""
    if str(model or "").strip() == "<synthetic>":
        return TABLE[DEFAULT], "synthetic"
    key = (model, prompt_tokens is not None and prompt_tokens > HAIKU55_LONG_AT)
    hit = _cache.get(key)
    if hit:
        return hit
    fam, ver = parse(model)
    if fam is None:
        res = (TABLE[DEFAULT], "unknown")
    elif (fam, ver) in TABLE:
        res = (TABLE[(fam, ver)], None)
    else:
        res = (TABLE[(fam, LATEST[fam])], "family_fallback")
    if res[0] is TABLE[("haiku", "5.5")] and key[1]:
        res = (HAIKU55_LONG, res[1])
    if len(_cache) < 512:
        _cache[key] = res
    return res


def family(model):
    """报表分组用的族名：opus / sonnet / haiku / fable / mythos；不认识的原样返回（不并进 other）。"""
    if str(model or "").strip() == "<synthetic>":
        return "<synthetic>"
    fam, _ = parse(model)
    return fam or (str(model) if model else "<无模型名>")


def _n(v):
    try:
        x = float(v)
        return x if x > 0 else 0.0
    except Exception:
        return 0.0


def parts(u):
    """transcript 的 usage → {inp, cw5m, cw1h, cr, out}（token 数）。
    有 cache_creation 拆分就按拆分；拆分之和小于总写入（或没有拆分）时，差额按 5 分钟档（官方默认 TTL）。"""
    u = u if isinstance(u, dict) else {}
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    tot = _n(u.get("cache_creation_input_tokens"))
    w5, w1 = _n(cc.get("ephemeral_5m_input_tokens")), _n(cc.get("ephemeral_1h_input_tokens"))
    if w5 + w1 < tot:
        w5 += tot - (w5 + w1)
    return {"inp": _n(u.get("input_tokens")), "cw5m": w5, "cw1h": w1,
            "cr": _n(u.get("cache_read_input_tokens")), "out": _n(u.get("output_tokens"))}


def prompt_tokens(u):
    p = parts(u)
    return p["inp"] + p["cw5m"] + p["cw1h"] + p["cr"]


def usd(model, u):
    """→ (美元, flag)。"""
    p = parts(u)
    price, flag = lookup(model, p["inp"] + p["cw5m"] + p["cw1h"] + p["cr"])
    return sum(p[k] * price[k] for k in KINDS) / 1e6, flag


def weighted(model, u):
    """→ (Sonnet 5.5 当量 token, flag)。"""
    d, flag = usd(model, u)
    return d * 1e6 / REF_INPUT_USD, flag


def native(model, u):
    """→ (本模型单位, flag)：每类 token × (该类价 / 本模型基础输入价)。兼容旧 weekly_compare 的口径。"""
    p = parts(u)
    price, flag = lookup(model, p["inp"] + p["cw5m"] + p["cw1h"] + p["cr"])
    return sum(p[k] * price[k] for k in KINDS) / price["inp"], flag


def per_token(model, kind, prompt=None):
    """一个 token 在某一类（inp/cw5m/cw1h/cr/out）里值多少 Sonnet 5.5 当量 token。"""
    price, _ = lookup(model, prompt)
    return price[kind] / REF_INPUT_USD


def describe():
    """给报告用的一张表（Sonnet 5.5 当量 / token）。"""
    rows = ["| 模型 | input | 写入 5m | 写入 1h | 缓存读 | output | 缓存读 / 本模型 input |", "|---|---|---|---|---|---|---|"]
    for (f, v), p in TABLE.items():
        rows.append("| %s %s | %s | %s | %s | %s | %s | %.3g× |" % (
            f, v, *("%.4g" % (p[k] / REF_INPUT_USD) for k in KINDS), p["cr"] / p["inp"]))
    return "\n".join(rows)


if __name__ == "__main__":
    print("来源：" + SOURCE)
    print(describe())
