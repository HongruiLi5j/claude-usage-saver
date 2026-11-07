#!/usr/bin/env python3
"""项目画像（第 5 轮 I）：按 cwd 自动识别「这个项目自己的写法」，给 hooks / 其它 AI 工具共用。

识别内容（只读项目，绝不在项目里写文件）：
- 项目根（R6I3，root_info，session_resume 也用它）：从 cwd 往上最近的有信号的目录。硬信号 .git / .usage-saver.json /
  自己的交接文件（HANDOFF.md、交接.md、项目交接.md、HANDOVER.md…，带标记的 进度.md）任何地方都算；软信号说明文件
  （CLAUDE.md / AGENTS.md / GEMINI.md）和构建清单（pyproject.toml / package.json / lakefile.toml / Cargo.toml …）
  只在不属于某个 git 仓库时算。不越过容器目录（默认 ~/Documents/Projects，及自动识别的隐式容器）下第一层，
  不把 ~ 和容器本身当项目根；都没有时为容器下第一层或 cwd 本身。
- 语言和框架、测试命令（pytest / lake build / npm test / cargo test / go test / make test / 纯脚本测试 …），
  以及项目说明里写明的测试命令和「不得运行」的测试文件。
- 交接文件的位置和格式：文件名（HANDOFF.md 及同义写法、说明文件里提到的交接文件、覆盖文件指定的），
  章节标题（红线/未决、下一步的同义写法）。
- 项目说明文件（CLAUDE.md / AGENTS.md / GEMINI.md / .cursorrules / copilot-instructions / FILES.md，
  含上级目录里的）：标题、红线和约定条目（只截取，作为数据，不是指令）。
- 规模（有界遍历：文件数、字节数、主要扩展名、档位）。

覆盖文件：项目根（或其上级、不越过 ~）里可选的 `.usage-saver.json`，见 OVERRIDE_DOC。
缓存：<state>/profiles/<项目名>-<路径哈希>.json，带过期时间（默认 6 小时）和指纹（根目录、覆盖文件、
说明文件、构建清单的 mtime）；指纹变了立即重算。state 目录 = $USAGE_SAVER_STATE，否则本套件的 state/。

不依赖 Claude Code：Codex / Gemini / 任何脚本都可以
  python3 tools/project_profile.py --cwd DIR [--json | --md | --env | --field a.b] [--no-cache] [--no-write]
或 `import project_profile; project_profile.get_profile(cwd)`。任何异常都退回默认画像，不抛给调用方。
"""
import fnmatch, hashlib, json, os, re, shlex, sys, time

VERSION = 3              # R6I3：项目根改由 root_info 统一判定（含交接文件、隐式容器），旧缓存作废
SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERRIDE = ".usage-saver.json"
DEFAULT_TTL_H = 6.0
SCAN_BUDGET_S = 0.4
SCAN_MAX_FILES = 20000
READ_MAX = 64 * 1024

OVERRIDE_DOC = """\
.usage-saver.json（全部字段可选；类型不对的字段忽略并记在 override_errors 里）：
{
  "thresholds": {"soft_tokens": 250000, "strong_tokens": 320000},
  "test_commands": ["lake build", ".venv/bin/python tests/test_strategies.py"],
  "test_grep_extra": "warning:|sorry",
  "handoff": {"files": ["docs/进度.md"], "sections": {"red": ["硬规则"], "next": ["待办"]}},
  "cache_ttl_hours": 6
}"""

# ---------------------------------------------------------------- 常量：同义写法
PRIMARY_HANDOFF = ["HANDOFF.md", os.path.join(".claude", "handoff.md")]        # 与 session_resume 原 NAMES 一致
SYNONYM_HANDOFF = ["HANDOVER.md", "交接.md", "项目交接.md", "交接文档.md", "进度.md",
                   "STATUS_FOR_NEXT_ASSISTANT.md", os.path.join("docs", "HANDOFF.md"),
                   os.path.join("handoff", "HANDOFF.md"), os.path.join("handoff", "STATUS_FOR_NEXT_ASSISTANT.md")]
# 主模式 = session_resume 原来的正则（保证没有覆盖时行为不变）；宽模式只在主模式找不到时才用
RED_PRIMARY = r"红线|未决|禁止事项|(?i:red ?lines?)"
NEXT_PRIMARY = r"下一步|(?i:next steps?)"
RED_BROAD = r"硬规则|约束|禁止|注意事项|不要做|(?i:guardrails?|constraints?|blockers?|open questions?|do not|don'?t)"
NEXT_BROAD = r"还没做|待办|接下来|后续步骤|(?i:todo|to-do|next|remaining|open items)"

INSTR_FILES = ["CLAUDE.md", "CLAUDE.local.md", os.path.join(".claude", "CLAUDE.md"), "AGENTS.md",
               "AGENTS.override.md", "GEMINI.md", ".cursorrules", os.path.join(".cursor", "rules"),
               os.path.join(".github", "copilot-instructions.md"), "CONVENTIONS.md", "FILES.md"]
STRONG_MARKERS = [OVERRIDE, "CLAUDE.md", "AGENTS.md", "GEMINI.md", "HANDOFF.md"]
MANIFESTS = ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile", "package.json",
             "Cargo.toml", "go.mod", "lakefile.toml", "lakefile.lean", "lean-toolchain", "Gemfile", "pom.xml",
             "build.gradle", "build.gradle.kts", "CMakeLists.txt", "Makefile", "mix.exs", "composer.json",
             "deno.json", "pytest.ini", "tox.ini", "noxfile.py", "uv.lock", "poetry.lock"]
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", ".lake", "lake-packages", "__pycache__",
             ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", "target", ".next", ".nuxt",
             "data", "_to_delete", "backups", ".secret", "sealed", ".wrangler", ".engines_lib", ".idea",
             ".vscode", ".tox", ".cache", "coverage", ".gradle"}
CODE_EXT = {"py": "python", "lean": "lean", "js": "javascript", "mjs": "javascript", "cjs": "javascript",
            "ts": "typescript", "tsx": "typescript", "jsx": "javascript", "rs": "rust", "go": "go",
            "rb": "ruby", "java": "java", "kt": "kotlin", "c": "c", "h": "c", "cc": "cpp", "cpp": "cpp",
            "hpp": "cpp", "cs": "csharp", "swift": "swift", "r": "r", "jl": "julia", "hs": "haskell",
            "ml": "ocaml", "ex": "elixir", "php": "php", "scala": "scala", "sh": "shell", "afl": "amibroker"}
DOC_EXT = {"md", "txt", "rst", "tex", "org", "adoc", "pdf", "docx", "html", "mhtml"}
RULE_HEAD = re.compile(r"红线|硬规则|规则|禁止|约定|安全|边界|纪律|注意|(?i:rules?|constraints?|guardrails?|"
                       r"conventions?|boundar(y|ies)|never|do not|don'?t|safety)")
STRONG_NEG = re.compile(r"不要|不得|禁止|绝不|严禁|勿|不准|不能|不许|(?i:\bnever\b|\bdo not\b|\bdon'?t\b|"
                        r"\bmust not\b)")
NO_RUN = re.compile(r"(不得|不要|勿|禁止|别|不能|(?i:do not|don'?t|never)).{0,12}(运行|跑|执行|(?i:run|execute))")
TESTFILE_TOK = re.compile(r"[\w./-]*test[\w./-]*\.py")
RUNNER_HEADS = re.compile(r"^(\S*/)?(python[0-9.]*|pytest|py\.test|npm|npx|yarn|pnpm|bun|node|deno|lake|cargo|"
                          r"go|make|uv|poetry|tox|nox|bundle|rspec|rake|mvn|gradle|gradlew|dotnet|jest|vitest)$")
# 覆盖文件里的测试命令：首词不能是 shell 内建/通用工具或纯通配（防止一个仓库用 "*" 把所有输出都藏起来）
DENY_HEADS = {"sh", "bash", "zsh", "fish", "cat", "echo", "printf", "ls", "grep", "rg", "git", "cd", "rm", "mv",
              "cp", "sed", "awk", "head", "tail", "find", "curl", "wget", "env", "sudo", "xargs", "less", "open",
              "ssh", "scp", "tee", "true", "false", "eval", "exec", "source", ".", "kill", "pkill"}
HEAD_OK = re.compile(r"^[\w./+-]+\*?$")
HANDOFF_NAME = re.compile(r"交接|进度|现状|状态|(?i:hand-?off|handover|status|progress|session)")
PY_SCRIPT_PATS = ["test_*.py", "tests_*.py", "*_test.py", "*_check.py", "check_*.py"]
CJK_FAIL = "失败|错误|不通过|FAILED|✗|❌"


# ---------------------------------------------------------------- 小工具
def _home():
    return os.path.realpath(os.path.expanduser("~"))


def state_dir():
    return os.environ.get("USAGE_SAVER_STATE") or os.path.join(SUITE, "state")


def containers():
    """「容器目录」：其下第一层算一个项目（与 session_resume 的 Projects 规则一致）。可用环境变量覆盖。"""
    raw = os.environ.get("USAGE_SAVER_CONTAINERS")
    if raw:
        return [os.path.realpath(os.path.expanduser(x)) for x in raw.split(os.pathsep) if x.strip()]
    return [os.path.join(_home(), "Documents", "Projects")]


def _read(path, n=READ_MAX):
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read(n)
    except Exception:
        return ""


def _isfile(*p):
    try:
        return os.path.isfile(os.path.join(*p))
    except Exception:
        return False


def _mtime(p):
    try:
        return os.stat(p).st_mtime_ns
    except Exception:
        return None


def _safe_rel(name):
    """覆盖文件 / 说明文件里给的相对路径：不许绝对路径、不许 ..，防止跨项目读文件。"""
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        return None
    n = os.path.normpath(name.strip())
    if os.path.isabs(n) or n.startswith("..") or "\0" in n:
        return None
    return n


def _uniq(xs):
    out = []
    for x in xs:
        if x and x not in out:
            out.append(x)
    return out


# ---------------------------------------------------------------- 项目根（R6I3：全套件唯一的判定来源）
# session_resume（找交接）和画像（测试命令、说明文件、交接 found）都调 root_info()，不再各写一套（Codex 复核 I2 打回）。
#
# 每一级目录的「项目信号」分两类：
#   硬信号：.git（目录或文件）、.usage-saver.json、自己的交接文件（见 own_handoff）。任何地方都算一个项目根。
#   软信号：说明文件（CLAUDE.md / AGENTS.md / GEMINI.md …）、构建清单（pyproject / package.json / lakefile …）。
#           不在 git 仓库里时算项目根；在 git 仓库的子目录里不另立项目（monorepo 子包、子目录 CLAUDE.md 仍属于这个仓库）。
# 从 cwd 往上，**最近的**有信号的目录就是项目根；不越过 ~，不越过容器目录下第一层。
#
# 容器（「放项目的目录」，本身不是项目，其下第一层各算一个项目）：
#   1) 配置的容器：$USAGE_SAVER_CONTAINERS，默认 ~/Documents/Projects。永远不当项目根，所以那里的 AGENTS.md /
#      HANDOFF.md 是「容器级」文件，不会把各子项目连成一个项目。
#   2) 隐式容器（没配置也认，见 is_container）：没有 .git、没有构建清单，并且
#      - 下面至少有 2 个独立的 git 仓库；或
#      - 自己只有说明文件（没有交接文件、没有覆盖文件），下面至少有 2 个有信号的子目录。
#      只在 cwd 位于它下面时才判（cwd 就是它本身时照常算它自己）。
OWN_HANDOFF_RE = re.compile(r"交接|(?i:hand-?off|handover|next_assistant)")
OWN_HANDOFF_STRONG = [n for n in PRIMARY_HANDOFF + SYNONYM_HANDOFF if OWN_HANDOFF_RE.search(os.path.basename(n))]
OWN_HANDOFF_GENERIC = [n for n in SYNONYM_HANDOFF if n not in OWN_HANDOFF_STRONG]   # 如 进度.md：带标记才算
INSTR_MARKERS = ["CLAUDE.md", "CLAUDE.local.md", os.path.join(".claude", "CLAUDE.md"), "AGENTS.md",
                 "AGENTS.override.md", "GEMINI.md"]
HARD_SIGNALS = ("git", "override", "handoff")
HANDOFF_MARK = "<!-- usage-saver-handoff -->"
CONTAINER_SCAN_MAX = 400          # 判隐式容器时最多看这么多个子项


def under_container(path):
    """path 是否在某个容器目录（默认 ~/Documents/Projects）之下。"""
    p = os.path.realpath(os.path.expanduser(path or "~"))
    return any(p.startswith(c + os.sep) for c in containers())


def _within(path, root):
    """path 的真实路径是否在 root 的真实路径之内（符号链接、绝对路径、.. 都按真实路径判）。"""
    try:
        rp, rr = os.path.realpath(path), os.path.realpath(root)
    except Exception:
        return False
    return rp == rr or rp.startswith(rr.rstrip(os.sep) + os.sep)


def _has_mark(path):
    """与 session_resume.has_mark 相同：前 3 个非空行里有一行恰为标记。"""
    lines = [ln.strip() for ln in _read(path, 4096).lstrip("﻿").splitlines()[:12] if ln.strip()]
    return HANDOFF_MARK in lines[:3]


def own_handoff(d):
    """d 目录「自己的交接文件」：名字明确是交接的（HANDOFF.md、.claude/handoff.md、HANDOVER.md、交接.md、项目交接.md、
    交接文档.md、STATUS_FOR_NEXT_ASSISTANT.md、docs/HANDOFF.md …）；泛称（进度.md）要带 usage-saver 标记，或者同一目录
    有说明文件 / 覆盖文件（这时它显然是这个子项目自己的进度，不只是一份笔记）。
    真实路径必须在 d 内（指到别处的符号链接不算）。返回路径或 None。"""
    for n in OWN_HANDOFF_STRONG:
        p = os.path.join(d, n)
        if _isfile(p) and _within(p, d):
            return p
    declared = None
    for n in OWN_HANDOFF_GENERIC:
        p = os.path.join(d, n)
        if _isfile(p) and _within(p, d):
            if declared is None:
                declared = _isfile(d, OVERRIDE) or any(_isfile(d, m) for m in INSTR_MARKERS)
            if declared or _has_mark(p):
                return p
    return None


def root_signals(d, hard_only=False):
    """d 这一级的项目信号（按强弱排序）：git / override / handoff / marker / manifest。hard_only：只看硬信号（省 stat）。"""
    s = []
    if os.path.exists(os.path.join(d, ".git")):
        s.append("git")
    if _isfile(d, OVERRIDE):
        s.append("override")
    if own_handoff(d):
        s.append("handoff")
    if hard_only:
        return s
    if any(_isfile(d, m) for m in INSTR_MARKERS):
        s.append("marker")
    if any(_isfile(d, m) for m in MANIFESTS):
        s.append("manifest")
    return s


def is_container(d, sig=None):
    """d 是不是容器（放项目的目录，本身不算项目）。配置的容器直接算；其余见上面「隐式容器」。"""
    if d in containers():
        return True
    sig = root_signals(d) if sig is None else sig
    if "git" in sig or "manifest" in sig:
        return False
    marker_only = not any(s in sig for s in ("override", "handoff"))
    git_kids = proj_kids = 0
    try:
        with os.scandir(d) as it:
            for k, e in enumerate(it):
                if k >= CONTAINER_SCAN_MAX:
                    break
                if e.name.startswith(".") or e.name in SKIP_DIRS or "test2" in e.name.lower():
                    continue
                try:
                    if not e.is_dir():
                        continue
                except OSError:
                    continue
                if os.path.exists(os.path.join(e.path, ".git")):
                    git_kids += 1
                    proj_kids += 1
                elif marker_only and root_signals(e.path):
                    proj_kids += 1
                if git_kids >= 2 or (marker_only and proj_kids >= 2):
                    return True
    except OSError:
        return False
    return False


def _how(sig):
    return "git" if "git" in sig else ("manifest" if sig == ["manifest"] else "marker")


def root_info(cwd):
    """全套件唯一的项目根判定。返回 dict：
    root   项目根（真实路径）
    how    git / marker（覆盖文件、交接文件、说明文件）/ manifest / group（隐式容器下第一层）/ container（配置的
           容器下第一层，没有更近的信号）/ cwd / outside / home / container-self
    signals 根这一级的信号；repo：根所在的 git 仓库根（根本身有 .git 时等于根；不在仓库里为 None）
    chain  cwd 往上到边界的目录（含边界，不含 ~ 和容器本身）；outer：容器 / ~ 之外的上级不在 chain 里。"""
    d = os.path.realpath(os.path.expanduser(cwd or "~"))
    h = _home()
    cont = containers()
    base = {"root": d, "signals": [], "repo": None, "chain": [d]}
    if d == h:
        return {**base, "how": "home"}
    if d in cont:
        return {**base, "how": "container-self"}
    chain, x = [], d
    while x not in (h, "/") and x not in cont:
        chain.append(x)
        if os.path.dirname(x) in cont:
            break
        parent = os.path.dirname(x)
        if parent == x:
            break
        x = parent
    if not chain:
        chain = [d]
    g = next((k for k, x in enumerate(chain) if os.path.exists(os.path.join(x, ".git"))), None)
    for k, x in enumerate(chain):
        below_git = g is not None and k < g          # git 仓库的子目录：只认硬信号
        sig = root_signals(x, hard_only=below_git)
        if not sig:
            continue
        if k > 0 and is_container(x, None if below_git else sig):   # 隐式容器：路径上它的下一层是项目
            return {"root": chain[k - 1], "how": "group", "signals": [], "repo": None, "chain": chain[:k]}
        repo = chain[g] if g is not None and g >= k else None
        return {"root": x, "how": _how(sig), "signals": sig, "repo": repo, "chain": chain}
    if os.path.dirname(chain[-1]) in cont:
        return {"root": chain[-1], "how": "container", "signals": [], "repo": None, "chain": chain}
    return {"root": d, "how": "cwd" if d.startswith(h + os.sep) else "outside", "signals": [], "repo": None,
            "chain": chain}


def find_root(cwd):
    """旧接口：返回 (根目录, 判据)。规则见 root_info。"""
    r = root_info(cwd)
    return r["root"], r["how"]


def parent_handoff(info):
    """项目自己没有交接时，上一级目录（项目根 / 所在仓库之上，容器和 ~ 之下）里最近的一份「自己的交接文件」。
    只用来给一行路径提示，不注入正文（所有者 11-07 决定）。遇到容器就停（容器级交接不属于任何一个项目）。"""
    try:
        chain = info.get("chain") or []
        top = info.get("repo") or info.get("root")
        if top not in chain or info.get("how") in ("home", "container-self", "outside"):
            return None
        for x in chain[chain.index(top) + 1:]:
            p = own_handoff(x)
            if p:                                   # 先找交接、找到了再判是不是容器（判容器要列目录，较慢）
                return None if is_container(x) else p
            if is_container(x, root_signals(x)):    # 没有交接的上级：是容器就不再往上
                return None
    except Exception:
        return None
    return None


# ---------------------------------------------------------------- 覆盖文件
def _clamp_tokens(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(min(max(v, 50_000), 1_000_000))


def valid_grep(p):
    """grep 片段会放进单引号 shell 字符串：不许单引号/换行/NUL，长度 ≤ 200，且能当正则编译。"""
    if not isinstance(p, str) or not p or len(p) > 200 or any(c in p for c in "'\n\r\0"):
        return None
    try:
        re.compile(p)
    except re.error:
        return None
    return p


def valid_test_pattern(cmd):
    """覆盖文件里的测试命令 → token 列表（允许 fnmatch 通配）；不安全或看不懂返回 None。"""
    if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > 300:
        return None
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return None
    if not toks or not HEAD_OK.match(toks[0]):
        return None
    base = os.path.basename(toks[0].rstrip("*")) or toks[0]
    if base in DENY_HEADS or toks[0] in ("*", "?"):
        return None
    return toks[:6]


def load_override(root):
    """读 root（及其上级、不越过 ~）里最近的 .usage-saver.json。返回 (dict, 路径, 错误列表)。"""
    h, x, errs = _home(), root, []
    while True:
        p = os.path.join(x, OVERRIDE)
        if os.path.isfile(p):
            try:
                raw = json.loads(_read(p, 256 * 1024))
            except Exception as e:
                return {}, p, [f"JSON 解析失败：{type(e).__name__}"]
            if not isinstance(raw, dict):
                return {}, p, ["顶层不是对象"]
            out = {}
            th = raw.get("thresholds")
            if isinstance(th, dict):
                for k in ("soft_tokens", "strong_tokens"):
                    if k in th:
                        v = _clamp_tokens(th[k])
                        if v is None:
                            errs.append(f"thresholds.{k} 不是数字")
                        else:
                            out[k] = v
            elif th is not None:
                errs.append("thresholds 不是对象")
            tc = raw.get("test_commands")
            if tc is not None:
                if isinstance(tc, str):
                    tc = [tc]
                pats = []
                for c in tc if isinstance(tc, list) else []:
                    t = valid_test_pattern(c)
                    if t:
                        pats.append({"cmd": c, "match": t, "why": "覆盖文件"})
                    else:
                        errs.append(f"test_commands 忽略不安全或无法解析的一条：{str(c)[:60]!r}")
                out["test_commands"] = pats
            if "test_grep_extra" in raw:
                g = valid_grep(raw.get("test_grep_extra"))
                if g:
                    out["test_grep_extra"] = g
                else:
                    errs.append("test_grep_extra 含单引号/换行或不是合法正则，已忽略")
            ho = raw.get("handoff")
            if isinstance(ho, dict):
                fs = ho.get("files")
                if isinstance(fs, str):
                    fs = [fs]
                if isinstance(fs, list):
                    good = [_safe_rel(f) for f in fs]
                    out["handoff_files"] = [g for g in good if g]
                    if len(out["handoff_files"]) < len(fs):
                        errs.append("handoff.files 有绝对路径或 .. 路径，已忽略")
                secs = ho.get("sections")
                if isinstance(secs, dict):
                    for k in ("red", "next"):
                        v = secs.get(k)
                        if isinstance(v, str):
                            v = [v]
                        if isinstance(v, list):
                            out[f"sections_{k}"] = [s.strip() for s in v if isinstance(s, str) and 0 < len(s.strip()) <= 60]
            ttl = raw.get("cache_ttl_hours")
            if isinstance(ttl, (int, float)) and not isinstance(ttl, bool):
                out["cache_ttl_hours"] = float(min(max(ttl, 0), 168))
            return out, p, errs
        if x == h or x == "/" or os.path.dirname(x) == x:
            return {}, None, []
        x = os.path.dirname(x)
        if x == h:
            return {}, None, []


# ---------------------------------------------------------------- 遍历（有界）
def scan(root, budget_s=SCAN_BUDGET_S, max_files=SCAN_MAX_FILES):
    """数文件、字节、扩展名、关键文件名（不读内容）。跳过缓存/依赖/数据/封存目录和任何含 test2 的名字。"""
    t0 = time.time()
    n, size, ext, names, truncated, stack = 0, 0, {}, {}, False, [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            it = list(os.scandir(d))
        except Exception:
            continue
        for e in it:
            nm = e.name
            if "test2" in nm.lower():
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if nm in SKIP_DIRS or (nm.startswith(".") and nm not in (".github", ".claude", ".cursor")):
                        continue
                    if depth < 12:
                        stack.append((e.path, depth + 1))
                    continue
                if not e.is_file(follow_symlinks=False):
                    continue
                n += 1
                try:
                    size += e.stat(follow_symlinks=False).st_size
                except Exception:
                    pass
                x = nm.rsplit(".", 1)[1].lower() if "." in nm[1:] else ""
                ext[x] = ext.get(x, 0) + 1
                for pat in PY_SCRIPT_PATS + ["*.test.mjs", "*.test.js", "*.test.ts", "*.spec.ts", "*.spec.js",
                                             "*_test.go", "conftest.py"]:
                    if fnmatch.fnmatch(nm, pat):
                        names[pat] = names.get(pat, 0) + 1
            except Exception:
                continue
            if n >= max_files or time.time() - t0 > budget_s:
                truncated = True
                stack = []
                break
    tier = "empty" if n == 0 else "tiny" if n < 20 else "small" if n < 200 else "medium" if n < 2000 else "large"
    top = sorted(ext.items(), key=lambda kv: -kv[1])[:8]
    return {"files": n, "bytes": size, "tier": tier, "truncated": truncated, "top_ext": dict(top),
            "_ext": ext, "_names": names, "scan_ms": int((time.time() - t0) * 1000)}


# ---------------------------------------------------------------- 语言 / 框架
def detect_langs(root, sc):
    ext, langs, fw = sc["_ext"], [], []
    has = lambda f: _isfile(root, f)
    code = {}
    for x, c in ext.items():
        if x in CODE_EXT:
            code[CODE_EXT[x]] = code.get(CODE_EXT[x], 0) + c
    if any(has(f) for f in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile")) or code.get("python"):
        langs.append("python")
    if any(has(f) for f in ("lakefile.toml", "lakefile.lean", "lean-toolchain")) or code.get("lean"):
        langs.append("lean")
    if has("package.json") or code.get("javascript") or code.get("typescript"):
        langs.append("typescript" if code.get("typescript", 0) > code.get("javascript", 0) else "javascript")
    for f, lg in (("Cargo.toml", "rust"), ("go.mod", "go"), ("Gemfile", "ruby"), ("pom.xml", "java"),
                  ("build.gradle", "java"), ("build.gradle.kts", "kotlin"), ("mix.exs", "elixir"),
                  ("composer.json", "php"), ("CMakeLists.txt", "cpp")):
        if has(f) and lg not in langs:
            langs.append(lg)
    for lg, c in sorted(code.items(), key=lambda kv: -kv[1]):
        if lg not in langs and c >= 3 and lg not in ("shell",):
            langs.append(lg)
    docs = sum(c for x, c in ext.items() if x in DOC_EXT)
    total = max(sc["files"], 1)
    kind = "empty" if sc["files"] == 0 else ("docs" if not langs and docs / total >= 0.5 else
                                             "code" if langs else "other")
    # 框架：只读清单文件的依赖名
    txt = (_read(os.path.join(root, "pyproject.toml")) + _read(os.path.join(root, "requirements.txt"))).lower()
    for k in ("django", "flask", "fastapi", "streamlit", "torch", "tensorflow", "pandas", "numpy", "scikit-learn",
              "jupyter", "pytest"):
        if re.search(r"(^|[\"'\s,\[])" + re.escape(k) + r"\b", txt):
            fw.append(k)
    pkg = _pkg(root)
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})} if pkg else {}
    for k in ("next", "react", "vue", "svelte", "vite", "express", "electron", "jest", "vitest", "mocha",
              "@playwright/test", "typescript"):
        if k in deps:
            fw.append(k)
    lk = _read(os.path.join(root, "lakefile.toml")) + _read(os.path.join(root, "lakefile.lean"))
    if "mathlib" in lk.lower():
        fw.append("mathlib")
    return langs, _uniq(fw), kind


def _pkg(root):
    try:
        d = json.loads(_read(os.path.join(root, "package.json"), 512 * 1024))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------- 项目说明文件
def instructions(root):
    """root 和上级目录（不含 ~）里的说明文件：标题、红线/约定条目（截断）、提到的交接文件和测试命令。"""
    h, dirs, x = _home(), [root], os.path.dirname(root)
    while x.startswith(h + os.sep):       # 上级目录（不含 ~ 本身）
        dirs.append(x)
        x = os.path.dirname(x)
    out, rules, mentions, tcmds, no_run = [], [], [], [], []
    for d in dirs:
        for name in INSTR_FILES:
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            txt = _read(p)
            scope = "project" if d == root else "ancestor"
            heads = []
            cur_rule, kept = False, 0
            for ln in txt.splitlines():
                m = re.match(r"\s*#{1,6}\s+(.*)", ln)
                if m:
                    heads.append(m.group(1).strip()[:80])
                    cur_rule = bool(RULE_HEAD.search(m.group(1)))
                    continue
                s = ln.strip()
                if not s:
                    continue
                bullet = bool(re.match(r"([-*+]|\d+[.)、])\s+", s))
                if kept < 15 and ((cur_rule and bullet) or (bullet and STRONG_NEG.search(s))):
                    rules.append({"file": os.path.relpath(p, root) if scope == "project" else p,
                                  "scope": scope, "text": re.sub(r"\s+", " ", s)[:200]})
                    kept += 1
                if NO_RUN.search(s):
                    for t in TESTFILE_TOK.findall(s):
                        no_run.append(t.strip("./") if not t.startswith("tests/") else t)
                if scope == "project" and re.search(r"交接|(?i:hand-?off|handover)", s):
                    for f in re.findall(r"`([^`\s]+\.(?:md|txt))`|([\w\u4e00-\u9fff./-]+\.(?:md|txt))", s):
                        f = _safe_rel(f[0] or f[1])
                        # 只收文件名本身像交接/进度记录的（README、PROTOCOL 之类只是被顺带提到）
                        if f and HANDOFF_NAME.search(os.path.basename(f)) and os.path.isfile(os.path.join(root, f)) \
                                and f not in INSTR_FILES:
                            mentions.append(f)
                if scope == "project":
                    for span in re.findall(r"`([^`]{3,160})`", s):
                        tk = span.split()
                        if tk and RUNNER_HEADS.match(tk[0]) and re.search(r"test|lake build|check", span):
                            tcmds.append(span)
            out.append({"file": p, "scope": scope, "name": name, "bytes": len(txt.encode("utf-8")),
                        "headings": heads[:30]})
    return {"files": out, "rules": rules[:40], "handoff_mentions": _uniq(mentions),
            "documented_test_commands": _uniq(tcmds)[:10], "no_run_tests": _uniq(no_run)}


# ---------------------------------------------------------------- 测试命令
def _t(cmd, match, why):
    return {"cmd": cmd, "match": match, "why": why}


def detect_tests(root, langs, sc, instr, ov):
    has = lambda f: _isfile(root, f)
    names, tests, grep_extra = sc["_names"], [], []
    py = ".venv/bin/python" if has(".venv/bin/python") else "venv/bin/python" if has("venv/bin/python") else "python3"
    if "python" in langs:
        pyproj = _read(os.path.join(root, "pyproject.toml"))
        cfg = ("pytest.ini" if has("pytest.ini") else "pyproject [tool.pytest]" if "[tool.pytest" in pyproj else
               "setup.cfg [tool:pytest]" if "[tool:pytest]" in _read(os.path.join(root, "setup.cfg")) else
               "tox.ini [pytest]" if "[pytest]" in _read(os.path.join(root, "tox.ini")) else
               "conftest.py" if names.get("conftest.py") else
               ".pytest_cache（这个项目跑过 pytest）" if os.path.isdir(os.path.join(root, ".pytest_cache")) else None)
        scripts_first = []
        if not cfg:                           # 没有 pytest 配置：直接运行的测试脚本更可能是这个项目的真实用法，排前面
            scripts_first = [_t(f"{py} <{pat}>", ["python*", pat], f"发现 {names[pat]} 个 {pat}（直接运行的脚本，无 pytest 配置）")
                             for pat in PY_SCRIPT_PATS if names.get(pat)]
            tests += scripts_first
        if cfg or names.get("test_*.py") or names.get("*_test.py"):
            tests.append(_t(f"{py} -m pytest", ["python*", "-m", "pytest"], cfg or "发现 test_*.py"))
            tests.append(_t("pytest", ["pytest"], cfg or "发现 test_*.py"))
        if has("uv.lock"):
            tests.append(_t("uv run pytest", ["uv", "run", "pytest"], "uv.lock"))
        if has("poetry.lock"):
            tests.append(_t("poetry run pytest", ["poetry", "run", "pytest"], "poetry.lock"))
        if has("tox.ini"):
            tests.append(_t("tox", ["tox"], "tox.ini"))
        if has("noxfile.py"):
            tests.append(_t("nox", ["nox"], "noxfile.py"))
        for pat in PY_SCRIPT_PATS:            # 直接运行的测试/检查脚本（如 python tests/test_x.py、s01_check.py）
            if names.get(pat) and cfg:
                tests.append(_t(f"{py} <{pat}>", ["python*", pat], f"发现 {names[pat]} 个 {pat}（直接运行的脚本）"))
        if not cfg and (names.get("test_*.py") or names.get("*_test.py")):
            tests.append(_t(f"{py} -m unittest", ["python*", "-m", "unittest"], "有测试文件、无 pytest 配置"))
    if "lean" in langs:
        tests.append(_t("lake build", ["lake", "build"], "lakefile"))
        tests.append(_t("lake test", ["lake", "test"], "lakefile"))
        tests.append(_t("lake env lean <file>", ["lake", "env", "lean"], "lakefile"))
        grep_extra.append("warning:|sorry")
    pkg = _pkg(root)
    if pkg:
        scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
        pm = "pnpm" if has("pnpm-lock.yaml") else "yarn" if has("yarn.lock") else "bun" if (
            has("bun.lockb") or has("bun.lock")) else "npm"
        t = scripts.get("test")
        if isinstance(t, str) and "no test specified" not in t:
            tests.append(_t(f"{pm} test", [pm, "test"], "package.json scripts.test"))
            if pm != "npm":
                tests.append(_t("npm test", ["npm", "test"], "package.json scripts.test"))
        for k in scripts:
            if isinstance(k, str) and re.fullmatch(r"test[:\w-]*", k) and k != "test":
                tests.append(_t(f"{pm} run {k}", [pm, "run", k], f"scripts.{k}"))
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
        for tool in ("vitest", "jest", "mocha"):
            if tool in deps:
                tests.append(_t(f"npx {tool}", ["npx", tool], f"devDependencies.{tool}"))
                tests.append(_t(tool, [tool], f"devDependencies.{tool}"))
        if "@playwright/test" in deps:
            tests.append(_t("npx playwright test", ["npx", "playwright", "test"], "@playwright/test"))
    if names.get("*.test.mjs") or names.get("*.test.js") or (pkg and "node --test" in json.dumps(pkg.get("scripts"))):
        tests.append(_t("node --test", ["node", "--test"], "发现 *.test.mjs / node --test"))
    if has("Cargo.toml"):
        tests.append(_t("cargo test", ["cargo", "test"], "Cargo.toml"))
        tests.append(_t("cargo nextest run", ["cargo", "nextest", "run"], "Cargo.toml"))
    if has("go.mod"):
        tests.append(_t("go test ./...", ["go", "test"], "go.mod"))
    mk = _read(os.path.join(root, "Makefile"))
    for tgt in ("test", "check"):
        if re.search(rf"(?m)^{tgt}\s*:", mk):
            tests.append(_t(f"make {tgt}", ["make", tgt], f"Makefile 有 {tgt}: 目标"))
    if has("Gemfile"):
        tests.append(_t("bundle exec rspec", ["bundle", "exec", "rspec"], "Gemfile"))
    if has("pom.xml"):
        tests.append(_t("mvn test", ["mvn", "test"], "pom.xml"))
    if has("build.gradle") or has("build.gradle.kts"):
        tests.append(_t("gradle test", ["gradle*", "test"], "build.gradle"))
    # 项目说明里写明的测试命令（只用来「认得」，不执行）
    for span in instr["documented_test_commands"]:
        t = valid_test_pattern(span)
        if t:
            head = [t[0]] + [x for x in t[1:3] if not x.startswith("-") or x in ("-m", "--test")]
            tests.append(_t(span, head[:3], "项目说明文件里写明"))
    # 覆盖文件优先
    tests = list(ov.get("test_commands") or []) + tests
    seen, uniq = set(), []
    for t in tests:
        k = tuple(t["match"])
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    primary = uniq[0]["cmd"] if uniq else None
    warnings = []
    if instr["no_run_tests"]:
        warnings.append("项目说明里有「不得运行」的测试文件：" + "、".join(instr["no_run_tests"]))
        if primary and "pytest" in primary:
            primary += "".join(f" --ignore={f}" for f in instr["no_run_tests"])
    g = ov.get("test_grep_extra") or "|".join(_uniq(grep_extra + [CJK_FAIL]))
    return {"commands": uniq[:20], "primary": primary, "grep_extra": valid_grep(g), "warnings": warnings,
            "python": py if "python" in langs else None}


# ---------------------------------------------------------------- 交接
def handoff_info(root, instr, ov):
    primary = _uniq(list(ov.get("handoff_files") or []) + PRIMARY_HANDOFF)
    secondary = [n for n in _uniq(instr["handoff_mentions"] + SYNONYM_HANDOFF) if n not in primary]
    red_extra = [re.escape(s) for s in ov.get("sections_red") or []]
    next_extra = [re.escape(s) for s in ov.get("sections_next") or []]
    pats = {"red": "|".join(red_extra + [RED_PRIMARY]), "next": "|".join(next_extra + [NEXT_PRIMARY]),
            "red_broad": RED_BROAD, "next_broad": NEXT_BROAD}
    found = []
    for n in primary + secondary:
        p = os.path.join(root, n)
        # R6I2：真实路径必须仍在项目根内（符号链接指到兄弟项目时不读、不列，Codex 复核 I 阻塞项 2）
        rp, rr = os.path.realpath(p), os.path.realpath(root)
        if not rp.startswith(rr.rstrip(os.sep) + os.sep):
            continue
        if os.path.isfile(p) and not any(f["path"] == n for f in found):
            txt = _read(p, 256 * 1024)
            heads = re.findall(r"(?m)^\s*#{1,6}\s+(.*)$", txt) + re.findall(r"(?m)^\s*\*\*([^*]+)\*\*\s*$", txt)
            fmt = lambda pat: [x[:40] for x in heads if re.search(pat, x)][:3]
            red_p, next_p = fmt(pats["red"]), fmt(pats["next"])
            found.append({"path": n, "tier": "primary" if n in primary else "secondary",
                          "bytes": os.path.getsize(p), "age_h": round((time.time() - os.path.getmtime(p)) / 3600, 1),
                          "has_mark": "<!-- usage-saver-handoff -->" in txt[:2000],
                          "red_headings": red_p or [("(宽) " + x) for x in fmt(pats["red_broad"])],
                          "next_headings": next_p or [("(宽) " + x) for x in fmt(pats["next_broad"])]})
    return {"primary_names": primary, "secondary_names": secondary, "section_patterns": pats, "found": found}


# ---------------------------------------------------------------- 模型（换模型时的安全回退）
MODEL_FAMILIES = (("opus", 1.0), ("sonnet", 0.6), ("haiku", 0.2))


def model_info(model_id=None, window=None):
    """由 model id（及状态栏给的 context_window_size）得到族、上下文窗口、相对价格权重。
    不认识的模型：族 unknown、价格按最贵（保守，宁可多提醒），窗口用 payload 给的，没有就按 20 万（保守）。"""
    mid = str(model_id or "").lower()
    fam, w = "unknown", 1.0
    for f, wt in MODEL_FAMILIES:
        if f in mid:
            fam, w = f, wt
            break
    win = None
    if isinstance(window, (int, float)) and not isinstance(window, bool) and window > 0:
        win = int(window)
    elif "[1m]" in mid or "-1m" in mid:
        win = 1_000_000
    return {"id": model_id, "family": fam, "known": fam != "unknown", "price_weight": w,
            "context_window": win or 200_000, "window_source": "payload" if win else "default-200k"}


def clamp_lines(soft, strong, window):
    """软线/强线不超过上下文窗口（软线 ≤ 窗口 85%，强线 ≤ 窗口 95%），保证小窗口模型在自动压缩前能看到提醒。"""
    try:
        window = int(window)
        s = min(int(soft), int(window * 0.85))
        g = min(int(strong), int(window * 0.95))
        return s, max(g, s + 1)
    except Exception:
        return soft, strong


# ---------------------------------------------------------------- 组装 + 缓存
def cache_key(root):
    base = re.sub(r"[^\w.-]", "_", os.path.basename(root) or "root")[:40]
    return f"{base}-{hashlib.sha1(root.encode()).hexdigest()[:10]}"


def _fingerprint(root, ov_path):
    files = [root] + [os.path.join(root, f) for f in INSTR_FILES + MANIFESTS] + ([ov_path] if ov_path else [])
    h = _home()
    x = os.path.dirname(root)
    while x.startswith(h + os.sep):
        files += [os.path.join(x, f) for f in ("CLAUDE.md", "AGENTS.md", "GEMINI.md", OVERRIDE)]
        x = os.path.dirname(x)
    return hashlib.sha1(json.dumps([(f, _mtime(f)) for f in files]).encode()).hexdigest()[:16]


def build(cwd, budget_s=SCAN_BUDGET_S):
    ri = root_info(cwd)
    root, how = ri["root"], ri["how"]
    ov, ov_path, ov_errs = load_override(root) if how not in ("home", "container-self") else ({}, None, [])
    sc = scan(root, budget_s) if how not in ("home", "container-self") else scan(root, 0.05, 500)
    langs, fw, kind = detect_langs(root, sc)
    instr = instructions(root)
    tests = detect_tests(root, langs, sc, instr, ov)
    ho = handoff_info(root, instr, ov)
    now = time.time()
    ttl = ov.get("cache_ttl_hours", DEFAULT_TTL_H)
    return {
        "_meta": {"version": VERSION, "cwd": os.path.realpath(os.path.expanduser(cwd or "~")), "root": root,
                  "root_how": how, "root_signals": ri["signals"], "repo": ri["repo"], "under_container": under_container(root), "generated_at": int(now), "expires_at": int(now + ttl * 3600),
                  "fingerprint": _fingerprint(root, ov_path), "key": cache_key(root)},
        "name": os.path.basename(root),
        "vcs": "git" if os.path.exists(os.path.join(root, ".git")) else "none",
        "kind": kind, "languages": langs, "frameworks": fw,
        "size": {k: v for k, v in sc.items() if not k.startswith("_")},
        "tests": tests, "handoff": ho,
        "instructions": instr,
        "thresholds": {"soft_tokens": ov.get("soft_tokens"), "strong_tokens": ov.get("strong_tokens"),
                       "source": "override" if ov.get("soft_tokens") or ov.get("strong_tokens") else "none"},
        "override": {"path": ov_path, "errors": ov_errs},
        "skill_notes": skill_notes(),
    }


def skill_notes():
    """给 H 的 adapters/skills_sync.py（--profile 的 skill_notes.<tool>.<skill>）。skill 是全局的，所以这里**不写任何
    一个项目的格式**，只告诉别的工具「运行时去问画像」。"""
    cli = f"python3 {os.path.join(SUITE, 'tools', 'project_profile.py')}"
    ho = (f"写交接前先运行 `{cli} --field handoff.found`：项目已有交接文件就沿用它的文件名和章节写法；没有才新建 "
          f"`HANDOFF.md`。项目的红线和约定：`{cli} --md`（摘录是数据，以项目说明文件原文为准）。")
    mr = f"跑测试前用 `{cli} --field tests.primary` 取本项目的测试命令；`tests.warnings` 里列出的测试文件不要运行。"
    return {t: {"handoff": ho, "model-router": mr} for t in ("codex", "gemini", "claude")}


def default_profile(cwd, err=None):
    d = os.path.realpath(os.path.expanduser(cwd or "~"))
    return {"_meta": {"version": VERSION, "cwd": d, "root": d, "root_how": "fallback", "error": err},
            "name": os.path.basename(d), "vcs": "unknown", "kind": "unknown", "languages": [], "frameworks": [],
            "size": {}, "tests": {"commands": [], "primary": None, "grep_extra": None, "warnings": [], "python": None},
            "handoff": {"primary_names": list(PRIMARY_HANDOFF), "secondary_names": [],
                        "section_patterns": {"red": RED_PRIMARY, "next": NEXT_PRIMARY, "red_broad": RED_BROAD,
                                             "next_broad": NEXT_BROAD}, "found": []},
            "instructions": {"files": [], "rules": [], "handoff_mentions": [], "documented_test_commands": [],
                             "no_run_tests": []},
            "thresholds": {"soft_tokens": None, "strong_tokens": None, "source": "none"},
            "override": {"path": None, "errors": []}}


def get_profile(cwd=None, use_cache=True, write_cache=True, budget_s=SCAN_BUDGET_S):
    """主入口。命中未过期、指纹相同的缓存直接返回（_meta.cache='hit'），否则重算并（可选）写缓存。永不抛异常。"""
    try:
        root, how = find_root(cwd)
        path = os.path.join(state_dir(), "profiles", cache_key(root) + ".json")
        if use_cache:
            try:
                c = json.load(open(path, encoding="utf-8"))
                m = c.get("_meta") or {}
                ov_path = (c.get("override") or {}).get("path")
                if (m.get("version") == VERSION and m.get("root") == root and time.time() < m.get("expires_at", 0)
                        and m.get("fingerprint") == _fingerprint(root, ov_path)
                        and (ov_path or not _override_appeared(root))):
                    m["cache"] = "hit"
                    m["cwd"] = os.path.realpath(os.path.expanduser(cwd or "~"))
                    return c
            except Exception:
                pass
        p = build(cwd, budget_s)
        p["_meta"]["cache"] = "miss"
        if write_cache:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                tmp = f"{path}.{os.getpid()}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(p, fh, ensure_ascii=False, indent=1)
                os.replace(tmp, path)
            except Exception:
                p["_meta"]["cache"] = "miss-nowrite"
        return p
    except Exception as e:
        return default_profile(cwd, f"{type(e).__name__}: {e}"[:200])


def _override_appeared(root):
    """缓存时没有覆盖文件、现在上级目录里出现了一个（上级的覆盖文件不在根目录 mtime 里）。"""
    h, x = _home(), os.path.dirname(root)
    while x.startswith(h + os.sep):
        if os.path.isfile(os.path.join(x, OVERRIDE)):
            return True
        x = os.path.dirname(x)
    return False


# ---------------------------------------------------------------- 给 hooks 用的小接口
def project_thresholds(cwd):
    """轻量入口（守卫、状态栏每次都调）：只找项目根并读 .usage-saver.json，不遍历、不写缓存。
    返回 {"soft_tokens": int|None, "strong_tokens": int|None, "override": 路径|None}。"""
    try:
        root, how = find_root(cwd)
        if how in ("home", "container-self"):
            return {"soft_tokens": None, "strong_tokens": None, "override": None}
        ov, path, _ = load_override(root)
        return {"soft_tokens": ov.get("soft_tokens"), "strong_tokens": ov.get("strong_tokens"), "override": path}
    except Exception:
        return {"soft_tokens": None, "strong_tokens": None, "override": None}


def _tok_match(pat, tok, head=False):
    if head:
        return fnmatch.fnmatchcase(os.path.basename(tok), os.path.basename(pat))
    return fnmatch.fnmatchcase(tok, pat) or ("/" not in pat and fnmatch.fnmatchcase(os.path.basename(tok), pat))


def match_test(argv, profile):
    """argv（已去掉 FOO=1 前缀）是否是画像里的测试命令之一。"""
    try:
        for t in (profile.get("tests") or {}).get("commands") or []:
            m = t.get("match") or []
            if m and len(argv) >= len(m) and _tok_match(m[0], argv[0], head=True) \
                    and all(_tok_match(p, a) for p, a in zip(m[1:], argv[1:len(m)])):
                return True
    except Exception:
        return False
    return False


def handoff_names(profile):
    ho = (profile or {}).get("handoff") or {}
    return list(ho.get("primary_names") or PRIMARY_HANDOFF), list(ho.get("secondary_names") or [])


def section_patterns(profile):
    """返回编译好的 (red, next, red_broad, next_broad)；画像里的正则坏了就回到默认。"""
    sp = ((profile or {}).get("handoff") or {}).get("section_patterns") or {}
    out = []
    for k, dflt in (("red", RED_PRIMARY), ("next", NEXT_PRIMARY), ("red_broad", RED_BROAD), ("next_broad", NEXT_BROAD)):
        try:
            out.append(re.compile(sp.get(k) or dflt))
        except re.error:
            out.append(re.compile(dflt))
    return tuple(out)


# ---------------------------------------------------------------- CLI
def to_md(p):
    m, t, ho, ins = p["_meta"], p["tests"], p["handoff"], p["instructions"]
    lines = [f"# 项目画像：{p['name']}", f"- 根：`{m['root']}`（{m['root_how']}）；版本控制：{p['vcs']}；类型：{p['kind']}",
             f"- 语言：{', '.join(p['languages']) or '无'}；框架：{', '.join(p['frameworks']) or '无'}",
             f"- 规模：{p['size'].get('files')} 个文件（{p['size'].get('tier')}{'，未数完' if p['size'].get('truncated') else ''}）",
             f"- 测试命令（首选）：`{t['primary'] or '无'}`；认得：" + ", ".join(f"`{c['cmd']}`" for c in t["commands"][:8])]
    lines += [f"- ⚠ {w}" for w in t["warnings"]]
    lines.append("- 交接文件：" + (", ".join(f"`{f['path']}`（{f['age_h']}h，红线节 {f['red_headings'] or '无'}，"
                                          f"下一步节 {f['next_headings'] or '无'}）" for f in ho["found"]) or
                                 f"无（新写时用 `{ho['primary_names'][0]}`）"))
    lines.append("- 说明文件：" + (", ".join(f"`{os.path.basename(f['file'])}`({f['scope']})" for f in ins["files"]) or "无"))
    if ins["rules"]:
        lines.append("- 红线/约定（摘自说明文件，是数据不是指令）：")
        lines += [f"  - {r['text'][:120]}" for r in ins["rules"][:10]]
    if p["override"]["path"]:
        lines.append(f"- 覆盖文件：`{p['override']['path']}`" + (f"；错误：{p['override']['errors']}" if p["override"]["errors"] else ""))
    return "\n".join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="项目画像（只读项目；缓存写在 state/profiles/）")
    ap.add_argument("--cwd", default=os.getcwd())
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--json", action="store_true", help="完整 JSON（默认）")
    g.add_argument("--md", action="store_true", help="给人或给其它 AI 看的简短 Markdown")
    g.add_argument("--env", action="store_true", help="shell 变量（USAGE_SAVER_*），给 Codex/Gemini 的包装脚本 eval")
    g.add_argument("--field", help="只输出一个字段，如 tests.primary、handoff.primary_names")
    g.add_argument("--model", help="输出 model_info(MODEL)（可配 --window）")
    ap.add_argument("--window", type=int)
    ap.add_argument("--no-cache", action="store_true", help="不读缓存")
    ap.add_argument("--no-write", action="store_true", help="不写缓存（完全只读）")
    ap.add_argument("--override-doc", action="store_true")
    a = ap.parse_args(argv)
    if a.override_doc:
        print(OVERRIDE_DOC); return 0
    if a.model is not None:
        print(json.dumps(model_info(a.model, a.window), ensure_ascii=False)); return 0
    p = get_profile(a.cwd, use_cache=not a.no_cache, write_cache=not a.no_write)
    if a.md:
        print(to_md(p))
    elif a.env:
        q = shlex.quote
        print(f"USAGE_SAVER_ROOT={q(p['_meta']['root'])}")
        print(f"USAGE_SAVER_TEST_CMD={q(p['tests']['primary'] or '')}")
        print(f"USAGE_SAVER_HANDOFF={q(p['handoff']['found'][0]['path'] if p['handoff']['found'] else p['handoff']['primary_names'][0])}")
        print(f"USAGE_SAVER_LANGS={q(','.join(p['languages']))}")
        print(f"USAGE_SAVER_SOFT_TOKENS={q(str(p['thresholds']['soft_tokens'] or ''))}")
    elif a.field:
        v = p
        for k in a.field.split("."):
            v = v.get(k) if isinstance(v, dict) else None
        print(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    else:
        print(json.dumps(p, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(json.dumps(default_profile(os.getcwd(), str(e)[:200]), ensure_ascii=False))
        sys.exit(0)
