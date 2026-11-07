# 第 6 轮盲测 G2：test_filter 难题（必须真改写、真执行）

日期：2026-11-06。负责：G'（G2）。所有数字都是**实测**，在本机用真实子进程跑，没有模拟。

## 1. 为什么出这批题

第 5 轮 G 的报告说，TF 类 15 题里空实现能过 14 题。原因是那批题只检查"放行 / 语法有效 / 语义不变"，只能证明 hook 不会把命令弄坏，证明不了过滤真的生效。本轮新题反过来出：**hook 不改写，或者只拦截不改写，都判失败。**

## 2. 题目和判定

文件：`tools/heldout/heldout_tf2.py`。单文件，只用 Python 标准库，不 import 实现代码。共 38 题：

- **R01–R32：必须改写。** 判定步骤：
  1. 把命令交给 hook，拿到改写后的命令。
  2. 在两份完全相同的临时项目里，分别用 `bash` 执行原命令和改写后的命令。PATH 最前面放假的 pytest / npm(jest) / node --test(TAP) / python -m pytest / lake / cargo / timeout / uv，每个假程序会输出 250–600 行，其中带失败。
  3. 两边对比，以下全部满足才算通过：
     - 命令确实被改写，没有被拦截，改写后能通过 `bash -n`；
     - **退出码相同**；
     - 假测试的**调用序列相同**，包括 argv、cwd、FOO / BAR / PYTHONPATH；
     - **项目目录树相同**，包括 heredoc 写出的文件，也不能往项目里写日志；
     - 可见输出（stdout+stderr）**不超过原输出行数的一半**；
     - **关键行一行不丢**：失败的测试名、E / error 报错行、最后的汇总行；
     - 非测试段的输出逐行保留，顺序不变；不该出现的行（`set -e` 之后的段、`&&` 短路掉的分支）不出现。
- **N01–N06：不应改写**，改写或拦截都算错。N03（本来就短的失败输出）允许改写，但可见输出必须按原顺序包含原输出的每一行，最多多出 2 行。

覆盖的命令形状：

| 类别 | 题 |
|---|---|
| 基本：4 种测试工具、全通过、退出码 3 / 4 / 101 | R01–R05、R20、R22、R23 |
| 环境变量前缀（带空格和 `;` 的引号值）、PYTHONPATH | R06、R24 |
| 引号、括号、空格路径；`\ #`；续行；行尾注释里带分隔符 | R07、R12、R13、R14 |
| `; echo $?`、`&& \|\|`、`set -e`、`set -euo pipefail`、`; exit 3`、`cd && `、多行多次测试 | R08–R11、R15、R25、R29、R30 |
| 引号里出现测试词和分隔符 | R19 |
| 失败在输出中间（jest）；25 个失败 | R27、R28 |
| 日志落盘：完整日志存在 testlogs，可见输出给出路径 | R21 |
| 画像识别：Lean、Rust、uv 项目 | R22、R23、R32 |
| **[超出文档]**：单行和多行 `[[`、heredoc、非测试段带管道、`timeout` 前缀 | R16、R26、R17、R18、R31 |
| 反例：`--pdb`、`--watch`、短输出、非测试命令、用户已经接了 `\| tail`、`USAGE_SAVER_RAW=1` | N01–N06 |

标 **[超出文档]** 的 5 题，比当前 docstring 写的"含管道 / heredoc / `[[` 内分隔符就整条放行"更严格。这几题测的是"正确实现应当能安全改写"的目标，不是文档承诺过的行为，要不要当作目标由所有者决定。其余 27 道 R 题都在文档范围内：docstring、improvements.md 第 1、8、18 项，以及 R5I 画像。

## 3. 运行方法

```sh
cd ~/Documents/Projects/claude-usage-saver
python3 tools/heldout/heldout_tf2.py                                        # 测 hooks/test_filter.py
HELDOUT_TF2_HOOK=/path/other_hook.py python3 tools/heldout/heldout_tf2.py   # 换一个 hook 文件做对照
HELDOUT_ONLY=R2,N0  HELDOUT_SKIP=R3  HELDOUT_TF2_VERBOSE=1                  # 按前缀筛选；失败时打印改写后的命令
HELDOUT_SUITE=/path/suite_copy                                              # 换整套套件副本
```

- **隔离方式**：每题新建临时 HOME，把 hooks/\*.py、tools/\*.py、config.json 复制到 `<HOME>/Documents/Projects/claude-usage-saver/`，只运行这份副本。
- **耗时**：约 5 秒，不消耗 Claude token。
- **退出码**：全部通过时为 0，否则为 1。
- **没有污染（实测）**：跑之前打时间戳，跑之后仓库 state/ 下只有 rate.json 变了，那是所有者正在运行的状态栏写的。`~/.claude/` 没动。

## 4. 结果（实测，连跑 2 次结果相同）

| 实现 | 总通过 | R（必须改写） | N（不应改写） |
|---|---|---|---|
| **当前 hooks/test_filter.py** | **32/38（84%）** | 26/32 | 6/6 |
| **空实现**（读完 stdin 就 exit 0） | **6/38（15.8%）** | 0/32 | 6/6 |
| 只拦不改（遇到测试词就 deny） | 0/38 | 0/32 | 0/6 |
| 粗暴包装（整条命令包进 `{ }` 存日志，再 grep + tail 15） | 25/38 | 24/32 | 1/6 |

三个对照 hook 都放在 scratchpad 里（`/private/tmp/claude-501/-Users-lihongrui/f250ba1e-a895-48a2-95d2-043932615e49/scratchpad/tf2ctl/`），没有放进项目。

**区分力**：
- 空实现只能过 6 道反例题，占 15.8%，达到了 ≤20% 的目标。只拦不改的实现一题都过不了。
- 粗暴包装能过 24/32 道 R 题，说明 R 题的主要作用是区分"有没有真的过滤"，不能单独区分"过滤做得细不细"。但粗暴包装在 N 题上 5 题全挂，在 `set -e`、两次测试、非测试段输出、日志落盘这几题上也挂了，所以总分 25/38，和当前版本的 32/38 拉得开。

## 5. 当前版本没过的题（只写症状）

| 题 | 命令形状 | 期望 | 实际 |
|---|---|---|---|
| R28（文档内） | `pytest -q`：300 个测试里有 25 个失败，原输出 505 行 | 可见输出变短，并且 25 个失败的测试名每个至少出现一次（进度行或汇总行都算） | 命令被改写，可见输出 135 行。但其中 4 个失败测试（test_125、test_137、test_149、test_161，都在中段）在可见输出里一处都没有。另外观察到：25 个 `E       assert …` 报错行一个都不可见（这一点不在判定条件里） |
| R16 [超出文档] | `[[ -f conftest.py && -d tests ]] && pytest -q` | pytest 那一段被过滤，退出码 1 | 整条原样放行，可见 260 行 |
| R26 [超出文档] | 多行 `[[ -f conftest.py &&⏎ -d tests ]]`，下一行 `pytest -q` | 同上 | 整条原样放行 |
| R17 [超出文档] | 先用 heredoc（`<<'EOF'`，正文里有 `;`、`&&`、`$HOME`、反引号、`$(…)`）写一个文件，下一行 `pytest -q` | 文件内容不变，pytest 只跑一次并被过滤 | 整条原样放行 |
| R18 [超出文档] | `printf 'b\na\n' \| sort; pytest -q`（管道在非测试段） | sort 的输出保留，pytest 段被过滤 | 整条原样放行 |
| R31 [超出文档] | `timeout 120 pytest -q` | pytest 被过滤，退出码 1 | 整条原样放行 |

文档内的 27 道 R 题，当前版本只挂了 R28 一题。这些题包括 `set -euo pipefail`、`; exit 3`、`$?` 传递、`\ #`、续行、注释、引号里的分隔符、stderr 里的关键报错，以及 Lean / Rust / uv 画像，都在真实执行下验证过语义和退出码。

## 6. 读这些数字要注意什么

1. 假测试程序的输出格式是按 pytest 8 / jest / node TAP / lake / cargo 的常见样子手写的，不是真实工具的输出。真实输出里关键行的位置和措辞可能不一样。
2. "变短"的门槛定为不超过一半，是我选的数，文档没有给。"关键行"的取法也是我定的：测试名、E / error 行、汇总行。
3. [超出文档] 的 5 题和第 5 轮 TF 题、文档里"看不准就放行"的原则方向相反（例如旧题 TF11 要求带重定向的命令放行）。如果所有者决定维持保守策略，这 5 题应当归为 ③（文档决定）而不是 bug，届时当前版本按文档口径是 27/28 R 题 + 6/6 N 题。
4. 盲测纪律：本报告不写修复建议。test_filter.py 没有改动。

## 额外发现

- **R32 通过，R31 不通过**：同样是"包装前缀 + pytest"，`uv run pytest` 能被画像认出来，`timeout 120 pytest` 不能。Claude 跑长测试时常用 `timeout` 前缀，这类命令目前完全不过滤。收益没有测：要先在真实 transcript 里统计 `timeout … pytest` 出现的频率才能估算，这一步本轮没做。
- **粗暴包装能过 24/32 道 R 题**：如果以后有人想简化 test_filter，N 题和 R10 / R11 / R18 / R19 / R21 是主要的防线，应当一直保留在回归集里。
- **运行器可复用**：`HELDOUT_TF2_HOOK` 可以直接测 adapters/bin/usaver-run 这类跨工具版本，只要把它包成同样的 PreToolUse 接口即可（这一点我没有实测）。

## 总结

- **做了什么**：新建 `tools/heldout/heldout_tf2.py`，共 38 题，其中 R 题 32 道（真实执行并对比前后结果），N 反例 6 道。在 `tools/heldout/README.md` 末尾追加了运行说明。
- **数字（实测）**：
  - 当前版本 32/38（R 26/32，N 6/6）；
  - 空实现 6/38（15.8%）；
  - 只拦不改 0/38；
  - 粗暴包装 25/38。
- **当前版本没过的题**：
  - 文档内 1 题：R28，多失败时有 4 个失败测试名不可见；
  - 超出文档 5 题：R16、R26、R17、R18、R31，全部整条放行。
- **要所有者决定**：[超出文档] 的 5 种形状（`[[`、heredoc、非测试段带管道、`timeout` 前缀）要不要列为改写目标。
- **写的文件**：
  - `tools/heldout/heldout_tf2.py`（新建）
  - `tools/heldout/README.md`（末尾追加，备份在 `backups/heldout_README.md.pre-r6G2`）
  - 本报告
- **撤回命令**：`cd ~/Documents/Projects/claude-usage-saver && cp -p backups/heldout_README.md.pre-r6G2 tools/heldout/README.md && mkdir -p _to_delete && mv tools/heldout/heldout_tf2.py reports/round6_G2_tf.md _to_delete/`

交付完成
