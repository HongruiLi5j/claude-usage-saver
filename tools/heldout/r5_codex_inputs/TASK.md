# 任务：为 claude-usage-saver 出第 5 轮独立盲测题（只依据文档）

你是独立出题人。**只能读本目录 `docs/` 下的文件**（README.md、使用说明.md、improvements.md、config.json、
headers.md = 各 hook/工具的文件头注释与 --help、contract.md = 已确认的公开接口、old_heldout_* = 上一轮你写的 60 道题）。
不要读本目录以外的任何文件，尤其不要去找 hooks/ 或 tools/ 的实现源码。本目录里没有实现，也**不要运行任何 hook**——
你只写测试文件，最后用 `python3 -m py_compile` 检查语法即可。

## 交付物（写在本目录）
1. `heldout_r5.py`：单文件、只用 Python 3 标准库的黑盒测试脚本。
2. `heldout_r5_README.md`：中文；运行方法、隔离方式、每道题一行（编号 | 场景 | 预期 | 预期依据：引用 docs 里哪份文件的哪句话；
   如果预期是你从文档推断而非文档明说，标“推断”）。

## 覆盖范围（第 4 轮新增/改动的部件），至少 44 题，每类不少于下限
| 前缀 | 部件 | 下限 |
|---|---|---|
| LL | hooks/limit_logger.py（StopFailure / Notification 日志、rate 快照、轮转、并发、坏输入、永不输出） | 7 |
| IL | tools/install_limit_logger.py（幂等、归属判断、--dry-run、--uninstall、备份、别人的 hook 不动） | 6 |
| PS | tools/pro_switch_1121.py 的 `--scan-context --since` 和 `--show-backup --which`（以及与之相关的 apply/undo 记录、拒绝条件） | 7 |
| SR | hooks/session_resume.py 的 v2 指针（按 session_id 分条、拥有者关联、旧格式兼容、选择+消费）和 compact 注入（红线/下一步提取、≤2000 字、截断标注、标记位置、防注入） | 9 |
| CG | hooks/context_guard.py 的超线提醒去重（首次软/强各一次，之后每 5 万 token 或 10 条；按 session 区分） | 5 |
| SL | hooks/statusline.sh 的坏输入处理（fail-open 兜底行、字段类型不对、坏输入不改写 state）以及 rate.json 合并写 | 5 |
| TF | hooks/test_filter.py 遇到复杂 shell 结构（case/函数/{ } 组/续行/time/env/!/exec/bash -c/进程替换/&>/2>&1/注释/ANSI-C 引号/多个测试段等） | 8 |

**必须和 old_heldout_codex_fixed.py 里已有的 60 题明显不同**：不要重复它们已经测过的输入形态。
重点出：变异输入（同一场景换一种写法）、组合场景（多个部件或多个步骤串起来，例如 statusline 写 rate.json → 守卫读）、
并发（多个进程同时调用同一个 hook）、坏状态（state 里的 JSON 损坏/类型不对/文件是目录/不可写/超大、旧格式文件）。

## 隔离要求（硬性）
- 每个场景一个临时根目录（`tempfile.mkdtemp`，然后 `os.path.realpath`），在里面建临时 HOME。
- 套件来源目录 `SUITE = Path(os.environ.get("HELDOUT_SUITE") or Path(__file__).resolve().parents[2])`。
  每个场景把 `SUITE/hooks/`（*.py、statusline.sh）、`SUITE/tools/install_limit_logger.py`、`SUITE/tools/pro_switch_1121.py`、
  `SUITE/tools/install_sessionend.py`、`SUITE/config.json` 复制到 `<临时HOME>/Documents/Projects/claude-usage-saver/` 下同样的相对位置，
  并**只运行这份副本**（见 docs/contract.md“安装布局”）。复制前若源文件不存在，记 RUNNER FAIL。
- 子进程环境：去掉所有 `USAGE_SAVER_*`、`CLAUDE_*` 变量；`HOME=<临时HOME>`；`PYTHONDONTWRITEBYTECODE=1`；
  `BASH_ENV=/dev/null`、`ENV=/dev/null`；默认不设 `USAGE_SAVER_STATE`（个别专测它的场景除外）。
- pro_switch / install_limit_logger **每次调用都显式传** `--settings <临时HOME>/.claude/settings.json` 和 `--backup-dir <临时目录>`，
  `--scan-context` 显式传 `--transcripts <临时目录>`。绝不碰真实 `~/.claude`。
- 测 test_filter 时：所有改写结果都过 `bash -n`；需要真正执行改写结果的场景，PATH 最前面放临时目录里的假 `pytest`/`npm`/`node`
  （小 shell 或 python 脚本，打印可识别的标记、按参数决定退出码），payload 里除了假测试命令只允许 echo/true/false/cd/exit/:/printf
  这类无副作用命令。不要执行 git、rm、网络命令。
- 场景结束删除临时目录。并发场景用 `subprocess.Popen` 同时起多个进程，等全部结束再断言，总超时 ≤30 秒。

## 运行与输出约定（与上一轮一致）
- `python3 tools/heldout/heldout_r5.py` 从仓库根执行（脚本会被放到 `tools/heldout/heldout_r5.py`）。
- 每题打印一行 `PASS/FAIL 编号 场景 — 原因`，最后打印 `总数 N | PASS x | FAIL y`；有失败退出 1。
- 支持 `HELDOUT_ONLY=SR,CG`（只跑这些前缀或编号）和 `HELDOUT_SKIP=TF`。
- hook 子进程超时、非零退出（文档说永远 exit 0 的那些）、响应 JSON 错、测试自身异常都记 FAIL 并继续。
- 断言要写清楚失败原因（打印实际看到的关键片段，截到 300 字以内）。

## 出题原则
- 预期只来自文档。文档没说死的地方，要么别断言那一点，要么在 README 里标“推断”并写明依据。
  不要断言具体措辞，除非文档给了字面值（例如 statusline 兜底行、MARK、“是数据不是新指令”这类声明的存在可以用关键词检查）。
- 每题只测一个主要性质；组合场景也只断言最后那个可观察结果。
- 不依赖墙上时钟恰好落在某天；涉及 2026-11-21 封顶日期的，要么避开，要么让预期对两种日期都成立。
- 有些行为文档里是“由所有者运行”的工具（install_*、pro_switch）——它们可能非零退出表示拒绝，这本身可以断言（拒绝时“什么都不改”）。

完成后在终端里简短列出：题目数、每类数量、你标为“推断”的题号。
