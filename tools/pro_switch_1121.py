#!/usr/bin/env python3
"""11-21 换回 Pro 当天的配置包（由所有者运行）。

默认 --dry-run：只打印 diff，不写任何文件。
--apply：先把 settings.json 备份到 backups/，再写入；打印一行撤回命令。
--undo ：只撤回本脚本改过的键（记录在 backups/pro_switch_1121.state.json），其他键不动。
--show-backup [--which first|last]：只读，打印本轮记录里的备份路径（first=第一次 apply 之前的原始备份，
         last=最近一次 apply 之前的备份）。按 state 里记的路径选，不看文件 mtime。
--scan-context --since YYYY-MM-DD：只读，按每条记录的 timestamp 查最大单次上下文（全量读，不按 mtime 跳过）。

改的东西（其他键一律不动）：
  env.CLAUDE_CODE_DISABLE_1M_CONTEXT     = "1"       （已核实：1M 模型按 200K 窗口对待）
  env.CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS = "150000"  （已核实：默认 20 万在 200K 窗口上永不触发）
  autoCompactWindow                       = 200000    （原 300000，理由见 AUTOCOMPACT_REASON）
可选 --subagent-cap N：env.CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS = N（调研建议 4；默认不加）

幂等：已经是目标值就不写文件、不备份。测试时用 --settings 指向副本、--backup-dir 指向临时目录。

拒绝（什么都不改）的情况：settings 是符号链接；env 存在但不是对象；backup-dir 里的记录属于另一个
settings 文件；记录是 E1 旧格式（没有 keys）。
"""
import argparse
import copy
import datetime
import difflib
import glob
import json
import os
import shutil
import subprocess
import sys

HOME = os.path.expanduser("~")
DEFAULT_SETTINGS = os.path.join(HOME, ".claude", "settings.json")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BACKUP_DIR = os.path.join(PROJECT, "backups")
SWITCH_DATE = "2026-11-21"
SCRIPT = os.path.abspath(__file__)

ENV_TARGET = {
    "CLAUDE_CODE_DISABLE_1M_CONTEXT": "1",
    "CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS": "150000",
}
AUTOCOMPACT_TARGET = 200000
AUTOCOMPACT_REASON = (
    "autoCompactWindow 300000 → 200000：主会话开了 DISABLE_1M 后窗口就是 200K，"
    "300000 会被自动压到 200K，改不改都一样（无害）；但 henchmen 会删掉 agent 进程里的 "
    "CLAUDE_CODE_* 环境变量，settings.json env 块能否传到 henchmen agent 待核实。"
    "万一传不到、而 Pro 又给了 1M，henchmen agent 会一路涨到 30 万才压缩。"
    "autoCompactWindow 是 settings 键，henchmen agent 一定读得到，设成 200000 让两条路径的上限一致。"
    "不设得更低（如 18 万），是为了不抢在守卫软线 180K / 强线 195K 之前压缩。"
)

PENDING = [
    "Pro 上 Opus 5.5 / Sonnet 5.5 默认是 200K 还是 1M（官方套餐表只列了 4.6 系列）。开 DISABLE_1M 后两种情况行为一样，所以不影响本包。",
    "settings.json 的 env 块能否传到 henchmen 启动的 agent（henchmen 会删进程里的 CLAUDE_CODE_*）。所以同时保留 autoCompactWindow 这个 settings 键。",
    "11-21 后 Pro 能否用 Opus 5.5 / Fable；Fable 在 Pro 上是否走 usage credits。",
    "空闲压缩只在交互终端/VS Code/桌面端、1 小时 TTL、闲置约 1 小时时触发；headless 的 henchmen agent 没有空闲压缩，第二个变量对它们无效。",
    "200K 窗口下 skill 列表预算约 2000 token，handoff / model-router 描述可能被截掉，需 /doctor 实看。",
]


_MISSING = object()


USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def usage_total(u):
    """一次请求的上下文 = 三个输入字段之和。格式不对（usage 不是对象、字段不是非负整数）就抛 TypeError。"""
    if not isinstance(u, dict):
        raise TypeError("usage 不是对象")
    n = 0
    for k in USAGE_KEYS:
        v = u.get(k, 0)
        if type(v) is not int or v < 0:  # 排除 "100"、null、true 这类
            raise TypeError(f"{k}={v!r}")
        n += v
    return n


def scan_context(root, since):
    """按每条记录自己的 timestamp 找 since（本地日期，按本地零点算）之后最大的单次上下文。
    每个文件都全量读：文件 mtime 证明不了里面的记录都早于截止日，所以不用它跳过任何文件。
    每条记录单独 try：坏记录跳过并计数，不会让整次扫描中断。"""
    t0 = datetime.datetime.strptime(since, "%Y-%m-%d").astimezone()
    best, where, nrec, nbad, nfile = 0, "", 0, 0, 0
    for f in sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)):
        try:
            fh = open(f, encoding="utf-8", errors="ignore")
        except OSError:
            continue
        nfile += 1
        with fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    nbad += 1
                    continue
                msg = o.get("message") if isinstance(o, dict) else None
                if not isinstance(msg, dict) or "usage" not in msg:
                    continue  # 不是带 usage 的请求记录（例如正文里出现了 "usage" 这个词）
                try:
                    ts = datetime.datetime.fromisoformat(o["timestamp"].replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.astimezone()
                    if ts < t0:
                        continue
                    n = usage_total(msg["usage"])
                except (KeyError, TypeError, ValueError, AttributeError):
                    nbad += 1
                    continue
                nrec += 1
                if n > best:
                    best, where = n, f"{f} @ {o['timestamp']}"
    return best, where, nrec, nbad, nfile


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_text(d):
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


def write_atomic(path, d):
    tmp = path + ".tmp-pro1121"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(dump_text(d))
    json.loads(open(tmp, encoding="utf-8").read())
    os.replace(tmp, path)


def state_path(backup_dir):
    return os.path.join(backup_dir, "pro_switch_1121.state.json")


class Refuse(Exception):
    """拒绝执行：打印原因，什么都不改。"""


def now_tag():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + f"-p{os.getpid()}"


def unique_backup(src, backup_dir, suffix):
    """复制到一个新文件，绝不覆盖已有文件：名字带微秒和 pid，仍然重名就加序号；
    用 O_EXCL 创建，所以就算同一微秒连跑两次也不会覆盖最早那份。不保留原 mtime（不再 copy2）。"""
    base = os.path.join(backup_dir, "settings.json." + now_tag())
    for i in range(1000):
        p = base + (f"-{i}" if i else "") + suffix
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out)
        return p
    raise Refuse(f"在 {backup_dir} 里找不到不重名的备份文件名，什么都没改。")


def same_file(a, b):
    return os.path.realpath(a) == os.path.realpath(b)


E1_MSG = ("{sp} 是 E1 版本的记录（没有 keys 字段），本版不能安全地追加或撤回。"
          "E1 从来没有在真实 settings 上 apply 过；如果你在测试副本上用过，请整文件手动还原："
          "p=$(python3 {script} --show-backup --settings {settings} --backup-dir {bd}) && cp \"$p\" {settings}"
          "，然后把 {sp} 改名移走再重来。什么都没改。")


def read_state(path):
    try:
        st = load(path)
    except Exception as e:
        raise Refuse(f"记录文件 {path} 读不了（{e}），什么都没改。")
    if not isinstance(st, dict):
        raise Refuse(f"记录文件 {path} 顶层不是对象，什么都没改。")
    return st


def check_owner(st, path, settings):
    owner = st.get("settings")
    if not isinstance(owner, str) or not same_file(owner, settings):
        raise Refuse(f"记录文件 {path} 属于 {owner!r}，不是现在的 {os.path.abspath(settings)}。"
                     "同一个 --backup-dir 只能给一个 settings 用：请对原来那个文件操作（--settings "
                     f"{owner}），或者换一个 --backup-dir。什么都没改。")


def load_state(args):
    """apply / undo 用：没有记录返回 None；E1 格式、属于别的 settings 都拒绝。"""
    sp = state_path(args.backup_dir)
    if not os.path.exists(sp):
        return None
    st = read_state(sp)
    check_owner(st, sp, args.settings)
    if not isinstance(st.get("keys"), dict):
        raise Refuse(E1_MSG.format(sp=sp, script=SCRIPT, settings=os.path.abspath(args.settings),
                                   bd=os.path.abspath(args.backup_dir)))
    return st


def save_state(sp, st):
    tmp = sp + ".tmp-pro1121"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2, ensure_ascii=False)
    os.replace(tmp, sp)


def targets(subagent_cap):
    env = dict(ENV_TARGET)
    if subagent_cap:
        env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] = str(subagent_cap)
    return env


def build_new(cur, env_t):
    new = copy.deepcopy(cur)
    env = new.get("env")
    if not isinstance(env, dict):
        env = {}
    for k, v in env_t.items():
        env[k] = v
    new["env"] = env
    new["autoCompactWindow"] = AUTOCOMPACT_TARGET
    return new


def show_diff(old, new, path):
    lines = list(difflib.unified_diff(
        dump_text(old).splitlines(), dump_text(new).splitlines(),
        fromfile=path + "（现在）", tofile=path + "（改后）", lineterm="", n=1))
    print("\n".join(lines) if lines else "（没有差异：已经是目标状态）")


def warnings(cur):
    out = []
    ms = cur.get("modelSettings")
    if isinstance(ms, dict):
        for m, v in ms.items():
            if isinstance(v, dict) and "autoCompactWindow" in v:
                out.append(f"modelSettings.{m}.autoCompactWindow={v['autoCompactWindow']} 会覆盖全局 autoCompactWindow（/autocompact 写的）。如需统一，在会话里对该模型再跑一次 /autocompact。")
    env = cur.get("env") if isinstance(cur.get("env"), dict) else {}
    if "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE" in env:
        out.append("env 里有 CLAUDE_AUTOCOMPACT_PCT_OVERRIDE，和 autoCompactWindow 功能重叠，建议只留一个。")
    try:
        v = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        ver = tuple(int(x) for x in v.split()[0].split(".")[:3])
        if ver < (2, 1, 290):
            out.append(f"claude 版本 {v} < 2.1.290，CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS 不生效。")
    except Exception:
        out.append("没能读到 claude --version（需 ≥2.1.290 才支持空闲压缩阈值），请手动确认。")
    return out


def print_pending():
    print("\n待核实（本包按保守方式处理，当天请对照 reports/pro_switch_checklist.md 实看）：")
    for i, p in enumerate(PENDING, 1):
        print(f"  [待核实{i}] {p}")


def do_apply(args, cur, new):
    st = load_state(args)  # 先校验记录，拒绝时不留下多余备份
    if cur == new:
        print("已是目标状态，未写文件、未备份（幂等）。")
        return 0
    today = datetime.date.today().isoformat()
    if today < SWITCH_DATE and not args.force_early:
        print(f"今天 {today} 早于 {SWITCH_DATE}：Max 上提前开会白白损失 1M 长上下文。确认已换 Pro 再加 --force-early。")
        return 2
    os.makedirs(args.backup_dir, exist_ok=True)
    bak = unique_backup(args.settings, args.backup_dir, ".pre-pro1121")
    sp = state_path(args.backup_dir)
    env0 = cur.get("env")  # main() 已保证：要么没有，要么是对象
    if st is None:
        st = {"settings": os.path.abspath(args.settings), "first_backup": bak,
              "env_existed": env0 is not None, "keys": {}}
    elif env0 is None:
        # 中途用户删掉了整个 env、这次由脚本重新建：撤回后 env 若为空就删掉，不留 "env": {}
        st["env_existed"] = False
    # 每个键单独记账：第一次改它时记下原值；再次 apply（比如加 --subagent-cap）时
    # 新改的键补记原值，已记过的键只更新 applied，原值保持最早那次的
    changes = [("env." + k, k in (env0 or {}), (env0 or {}).get(k), v)
               for k, v in new["env"].items() if (env0 or {}).get(k, _MISSING) != v]
    if cur.get("autoCompactWindow", _MISSING) != new["autoCompactWindow"]:
        changes.append(("autoCompactWindow", "autoCompactWindow" in cur, cur.get("autoCompactWindow"), new["autoCompactWindow"]))
    for key, existed, prior, applied in changes:
        if key in st["keys"]:
            st["keys"][key]["applied"] = applied
        else:
            st["keys"][key] = {"existed": existed, "prior": prior, "applied": applied}
    st.setdefault("backups", []).append(bak)
    save_state(sp, st)  # 先写记录再写 settings：中途失败时 undo 只会"跳过"，不会误改
    write_atomic(args.settings, new)
    sa, ba = os.path.abspath(args.settings), os.path.abspath(args.backup_dir)
    print(f"已写入 {args.settings}；这次的备份 {bak}；最初的原始备份 {st['first_backup']}；记录 {sp}")
    print("撤回命令（只撤本脚本改的键）：")
    print(f"python3 {SCRIPT} --undo --settings {sa} --backup-dir {ba}")
    print("整文件还原到第一次 apply 之前（会丢掉之后的其他改动）：")
    print(f'p=$(python3 {SCRIPT} --show-backup --settings {sa} --backup-dir {ba}) && cp "$p" {sa}')
    print("新会话才生效：退出当前 claude 重开。")
    return 0


def do_undo(args, cur):
    sp = state_path(args.backup_dir)
    st = load_state(args)
    if st is None:
        print(f"没有找到 {sp}：本脚本没有 apply 过（或已撤回），什么都不做。")
        return 0
    new = copy.deepcopy(cur)
    env_now = new.get("env", _MISSING)
    env_ok = isinstance(env_now, dict)
    skipped = []
    env_keys = [k for k in st["keys"] if k.startswith("env.")]
    if env_keys and not env_ok:
        what = "已被整个删除" if env_now is _MISSING else f"现在不是对象（{json.dumps(env_now, ensure_ascii=False)}）"
        skipped.append(f"env {what}，{', '.join(env_keys)} 都保留不动（不会重新插入 env）")
    for key, rec in st["keys"].items():
        if key.startswith("env."):
            if not env_ok:
                continue
            holder, k = env_now, key[4:]
        else:
            holder, k = new, key
        now = holder.get(k, _MISSING)
        if now != rec["applied"]:
            skipped.append(f"{key} 现在是 {None if now is _MISSING else now!r}，不是本脚本写的 {rec['applied']!r}，保留不动")
            continue
        if rec["existed"]:
            holder[k] = rec["prior"]
        else:
            holder.pop(k, None)
    if env_ok and not env_now and not st.get("env_existed", True):
        new.pop("env")  # env 是脚本建的，撤回后空了就删掉
    show_diff(cur, new, args.settings)
    for s in skipped:
        print("注意：" + s)
    if new != cur:
        os.makedirs(args.backup_dir, exist_ok=True)
        bak = unique_backup(args.settings, args.backup_dir, ".pre-pro1121-undo")
        write_atomic(args.settings, new)
        print(f"已撤回；撤回前的备份 {bak}")
    os.replace(sp, sp + ".undone-" + now_tag())
    print("记录文件已改名为 .undone-*（没有删除）。再次 apply 会重新记录。")
    return 0


def do_show_backup(args):
    """只读。stdout 只打印一个路径（方便 $(...) 取用），说明都写到 stderr。
    first：本轮记录里第一次 apply 之前的原始备份；last：最近一次 apply 之前的备份。
    当前没有记录时，用最近一份 .undone-* 记录（按文件名里的时间排序，不看 mtime）。"""
    sp = state_path(args.backup_dir)
    src = sp if os.path.exists(sp) else (sorted(glob.glob(sp + ".undone-*")) or [None])[-1]
    if src is None:
        print(f"{args.backup_dir} 里没有本脚本的记录（{os.path.basename(sp)} 或 .undone-*），没有可还原的备份。", file=sys.stderr)
        return 1
    if src != sp:
        print(f"提示：当前没有生效的记录，用的是已撤回的记录 {src}。", file=sys.stderr)
    st = read_state(src)
    check_owner(st, src, args.settings)
    if args.which == "first":
        p = st.get("first_backup")
    else:
        b = st.get("backups")
        p = b[-1] if isinstance(b, list) and b else None
    if not isinstance(p, str) or not os.path.isfile(p):
        print(f"记录里的 {args.which} 备份 {p!r} 不存在，不输出路径。", file=sys.stderr)
        return 1
    try:
        load(p)
    except Exception as e:
        print(f"备份 {p} 不是合法 JSON（{e}），不输出路径。", file=sys.stderr)
        return 1
    print(p)
    return 0


def main():
    ap = argparse.ArgumentParser(description="11-21 换 Pro 配置包")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="默认：只打印 diff")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--undo", action="store_true")
    ap.add_argument("--settings", default=DEFAULT_SETTINGS)
    ap.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)
    ap.add_argument("--subagent-cap", type=int, default=0, help="可选：CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS，调研建议 4")
    ap.add_argument("--force-early", action="store_true", help=f"{SWITCH_DATE} 之前也允许 --apply")
    g.add_argument("--scan-context", action="store_true", help="只读：按记录时间戳查 --since 之后最大的单次上下文")
    g.add_argument("--show-backup", action="store_true", help="只读：打印备份路径（--which first=最初的原始备份，last=最近一次 apply 前）")
    ap.add_argument("--which", choices=["first", "last"], default="first")
    ap.add_argument("--since", default=SWITCH_DATE)
    ap.add_argument("--transcripts", default=os.path.join(HOME, ".claude", "projects"))
    args = ap.parse_args()

    if args.scan_context:
        best, where, nrec, nbad, nfile = scan_context(args.transcripts, args.since)
        verdict = "超过 200000：有会话没被压在 200K（查这个文件属于主会话、子 agent 还是 henchmen）" if best > 200000 else "没有超过 200000"
        print(f"读了 {nfile} 个文件；{args.since} 之后的请求 {nrec} 条，最大上下文 {best}（{where or '无'}）：{verdict}")
        print(f"坏记录 {nbad} 条（JSON 解析失败、时间戳无效或 usage 格式不对，已跳过）")
        return 0

    try:
        if args.show_backup:
            return do_show_backup(args)
        return run_settings(args)
    except Refuse as e:
        print("拒绝：" + str(e), file=sys.stderr if args.show_backup else sys.stdout)
        return 3


def run_settings(args):
    if (args.apply or args.undo) and os.path.islink(args.settings):
        raise Refuse(f"{args.settings} 是符号链接（指向 {os.path.realpath(args.settings)}）。本脚本用"
                     "\"写临时文件再改名\"的方式写入，会把链接换成普通文件，所以不处理。需要的话请对真实文件运行："
                     f"--settings {os.path.realpath(args.settings)}")

    try:
        cur = load(args.settings)
    except Exception as e:
        print(f"读不了 {args.settings}（{e}），什么都不做。")
        return 1
    if not isinstance(cur, dict):
        print("settings.json 顶层不是对象，什么都不做。")
        return 1

    if args.undo:
        return do_undo(args, cur)
    if "env" in cur and not isinstance(cur["env"], dict):
        print(f"settings 里的 env 是 {json.dumps(cur['env'], ensure_ascii=False)}，不是对象。脚本不知道该怎么合并，也没法在撤回时原样还原，"
              "所以不处理：请先手动把 env 改成对象（或删掉这个键）再运行。什么都没改。")
        return 1

    new = build_new(cur, targets(args.subagent_cap))
    print(("[apply]" if args.apply else "[dry-run，不写文件]") + f" 目标文件 {args.settings}")
    show_diff(cur, new, args.settings)
    print("\n" + AUTOCOMPACT_REASON)
    for w in warnings(cur):
        print("警告：" + w)
    print_pending()
    print()
    if args.apply:
        return do_apply(args, cur, new)
    print(f"这是预览。确认后运行：python3 {SCRIPT} --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
