# 第 6 轮 T：test_filter（R28 + 扩大改写范围 + 核实 Claude Code 截断）

实施者 T，2026-11-06 ~ 11-07。只改了 `hooks/test_filter.py`（已上线），新建了 `tools/tests_r6T.py`，另外为了让旧测试和有意的改动不冲突，改了 `profiles/test_project_profile.py` 的 T05（理由见第 5 节）。没有 push，没有用 rm，没有读 tools/heldout/，没有改 settings.json。

下文凡是写"实测"的，都是真的跑过的；写"估算"的会给出算法。

## 1. R28：失败多的时候，中段的失败丢了（实测复现，已修好）

### 复现
- 用例：真实 pytest，300 个测试，其中 25 个失败，失败分散在第 10 到 293 号（包括 125、137、149、161）。每个失败的测试体有十几行，原输出 533 行（任务里给的是 505 行，形状相同）。
- 旧版（`backups/test_filter.py.pre-r6T`）实测：
  - 可见输出 135 行，就是 1 行路径 + `grep | head -120` + 1 行分隔 + `tail -12` + 1 行提示。
  - 25 个失败里只有 11 个能在 `FAILED` 行里看到，就是 tail 里那 11 个，**丢了 14 个**。
  - `short test summary info` 这个标题看不到。
  - 以 `E ` 开头的行一条都没有。
- 原因有两个：
  1. `head -120` 只截前面，中段和后段的失败都进不去。
  2. `grep -n` 会给每行加上行号前缀（`12:E       assert …`），所以"行首是 E"的报错行和"行首是 FAILED"的名字行全都认不出来了。

### 改法
现在长日志的摘要交给 hook 自己的 `--summarize` 模式来生成。包装命令跑完以后调用 `<python> hooks/test_filter.py --summarize 日志 模式`，选哪些行按下面的优先级：

| 优先级 | 内容 |
|---|---|
| 0 | 失败名字：short test summary 的标题和其中每一行 `FAILED/ERROR`。如果没有 summary，就用通用规则认 `FAIL:`、`not ok N`、`--- FAIL:`、cargo 的 `... FAILED` |
| 1 | 每个 pytest 失败小节：小节标题、第一行 `E`（没有 E 就取小节里第一条匹配行）、出错位置行（`x.py:30: AssertionError`） |
| 2 | 每个小节再多给 `>` 那一行，以及后面最多 4 行 `E` |
| 3 | 旧规则：匹配行加前 2 后 12 行的上下文，按出现顺序放，直到预算用完 |

其它要点：
- **结尾 12 行**总是给，前面照旧有 `----- 结尾 -----` 分隔。日志路径行和"已过滤"提示行的文字没变，`savings_meter` 照常能认。
- **预算**：
  - 正文行数最多 `min(300, 120 + 4×失败数)` 行，按失败数增长，但有上限。
  - 总字节数最多 `SUM_BYTES = 16000`，理由见第 3 节。
  - 单行超过 400 个字符就截断。名字都在行首，截断不会丢名字。
- **超出上限时**会明确写出来：
  - `[还有 N 个失败没显示，完整日志在 <路径>]`
  - `[另有 M 个失败只显示了名字，报错行没显示，完整日志在 <路径>]`
- 日志行**原样**打印，不再带行号前缀。省略的地方写 `…（省略第 a–b 行）…`；如果只省 1 行，就直接打印那一行。
- **保底**：python 跑不起来、出错或者日志超过 64MB 时，摘要模式什么都不打印并以 3 退出，外层 shell 就退回旧的 `grep | head -120 + tail -12`。整个摘要步骤后面都跟着 `|| true`，所以退出码仍然只由 `(exit $__usrc)` 决定。

### 结果（实测，真实 pytest）
| 用例 | 原输出 | 旧版可见 | **新版可见** |
|---|---|---|---|
| R28：300 个测试、25 个失败、533 行 | 533 行 | 135 行 / 5.3KB，名字 11/25，E 行 0 | **281 行 / 11.2KB，名字 25/25，每个失败都有 E 行（共 50 行），rc=1** |
| 同上，zsh | | 名字 11/25 | **25/25，rc=1** |
| 400 个失败（上限） | | 5.3KB，名字 11 个，没有提示 | **15.8KB，名字 232 个，并写明"还有 168 个失败没显示，完整日志在 …"** |
| 60 个失败、每行 5000 字符（-vv） | 66.8KB | 12.0KB，名字 11/60 | **15.8KB，名字 60/60** |

其它形状的字节对比（实测，真实 pytest，`/tmp/r6T/cmp`）：

| 形状 | 原输出 | 旧版 | 新版 | 旧/新可见失败名 |
|---|---|---|---|---|
| 1 个失败（≤40 行，原样输出） | 569 | 619 | 619 | 1/1 |
| 3 个失败，traceback 较深 | 2885 | 2613 | 2150 | 3/3 |
| 10 个失败 | 6598 | 4236 | 5559 | 10/10 |
| 25 个失败，traceback 较深 | 19472 | 4222 | 9220 | **11/25** |
| 全部通过，带 -rA | 3472 | 505 | 652 | – |

代价：**失败多的时候，新版比旧版多花 token**，因为旧版是丢掉信息换来的短。
- 估算：25 个失败的那次运行多出约 5KB，大约 1.3k token；按 4 个字符约 1 token 粗算，中文和符号多时会更多。
- 失败少（≤10 个）时，新旧两版的大小差不多。
- 所有者定的是质量优先，这是满足"失败名一个不丢"必须付的成本。

## 2. 扩大改写范围（所有者已定，只加"简单的"）

### 2.1 `timeout`
- `strip_timeout()` 会认这些写法：`timeout` / `gtimeout`，加上选项 `--foreground`、`--preserve-status`、`-v`、`-s SIG`、`--signal=SIG`、`-k DUR`、`--kill-after=DUR`、`-sKILL`，再加一个时长（`60`、`2m`、`1.5s`）。时长后面的部分再按原规则判断是不是测试命令（也包括画像里登记的测试命令）。
- 下面这些都放行：选项认不出来（例如 `-x`）、没有给时长、timeout 后面没有命令、带 `--pdb` 一类的交互参数。

### 2.2 `简单前缀 && / ; / || pytest`
- 新增 `tail_split()`。整条命令拆不了段的时候（前面有管道、重定向、单行的 `[[ a && b ]]` 等），会找最后一个**顶层**的 `;`、`&&` 或 `||`，然后只包装最后一段，前缀**一个字符都不改**。拼回去的形式是 `前缀 分隔符 { 包装 }`。
- 只接受这样的前缀：单行；没有 heredoc；没有括号（子 shell、进程替换、zsh 通配限定符）；没有反引号、`$(…)`、`$'…'`、`$"…"`、`$[…]`；没有后台 `&`，也没有 `;;`；命令位置上不出现保留字（`if`、`{`、`!`、`time` 等）；`[[ … ]]` 在同一行里闭合，里面的 `&&`、`||`、`<`、`>` 不当作分隔符。
- 最后一个顶层分隔符如果是管道 `|`，就放行，因为测试的输出被管道接走了。
- `#` 只有在词首才算注释，单行里的注释一直到行尾。
- 语义不变的依据：bash 和 zsh 里 `&&`、`||` 同级、左结合，`;` 优先级最低，所以把最后一个简单命令换成 `{ … }` 不会改变结构。`{ }` 在当前 shell 里执行，所以 cwd 和环境变量照常传递。
- 多行 `[[` 和 heredoc 仍然放行，因为 `rewrite()` 一看到 `<<` 就直接放行，`tail_split` 一看到换行也放行。

### 2.3 真实执行对照（`tools/tests_r6T.py`，实测）
- 方法：17 种新形状加 1 条真超时，每条在 bash 和 zsh 下各跑一次原命令、一次改写后的命令，用的都是真实 pytest，比较以下几项：
  - `sh -n` 语法检查；
  - 退出码；
  - 失败名集合；
  - 短路行为：测试模块导入时往一个标记文件里写一行，原命令和改写后命令"跑没跑 pytest"必须一致；
  - 前缀命令的输出（`PRE:` 行）要原样都在；
  - cwd：用 `cd sub &&`；环境变量：用一个检查 `R6T_ENV` 的测试。
- 覆盖的形状：
  - `timeout 60 pytest -q`
  - `timeout -k 5 --signal=TERM --foreground 2m pytest -q`
  - `timeout -s KILL 60 …`
  - `gtimeout 30s …`
  - `R6T_ENV=hello timeout …`
  - `cd sub && timeout …`
  - `[[ -f conftest.py && -d tests ]] && pytest -q`，条件为真和为假（短路）两种
  - `[[ … ]] || pytest`
  - `printf … | sort; pytest -q`
  - `printf … | grep -q nope && pytest -q`（短路）
  - 前缀里有重定向
  - 引号里出现 `; | &&`
  - `cd + 管道 + env 前缀`
  - `set -e + 管道前缀`
  - 行尾注释
  - `2>&1 | tail && timeout …`
  - `timeout 1 pytest`（测试 sleep 5，原命令和改写后都是 rc=124）
- 反例 30 条，全部放行：heredoc、多行 `[[`（两种写法）、子 shell、`$(…)`、反引号、测试被 `| tee` 接走或被 `>` 重定向、`printf a | pytest`、后台 `&`、pytest 不是最后一段、if / `{` / `!` / time、注释里的 `;`、`--pdb`、`timeout --pdb`、`timeout pytest`、`timeout -x`、`$'…'`、`${x:-a;b}`、`|&` 加 `>|`、case、末尾悬空的 `;`、`USAGE_SAVER_RAW=1`、段尾反斜杠。
- 旧形状对照：`cd . && pytest`、`pytest; exit 3`、`set -e; pytest; echo`、`FOO=1 pytest`，rc 和失败名都不变。

**tests_r6T：新版 28/28 通过；旧版 4/28**（旧版只过了反例、旧形状、通过用例和 hook 入口这 4 条）。

## 3. 核实 P 的发现：Claude Code 的大输出截断（实测）

实验：用 `claude -p --safe-mode --model haiku --output-format stream-json --verbose`（2.1.319，临时目录 `/tmp/r6T/cc_exp`），让模型逐条运行会输出指定字节数的命令，再从 stream-json 的 `tool_result` 里读出模型实际收到的内容。两轮共 11 次 Bash 调用，花费约 $0.034。

| stdout 大小 | 模型收到的内容 |
|---|---|
| 20000 / 29900 / 29999 / **30000** 字节 | 原样全文 |
| **30001** / 30100 / 40000 / 60000 / 200000 字节 | `<persisted-output>Output too large (xx KB). Full output saved to: ~/.claude/projects/…/tool-results/xxx.txt … Preview (first 2KB): …前 2000 字节…</persisted-output>`，一共约 **2235 字符** |
| 12000 个**字符**的中文（35280 字节） | 也被落盘 → 阈值按 **UTF-8 字节**计算，不是按字符 |

结论：
- **阈值是 30000 字节（UTF-8）**：超过就落盘，模型只能看到**开头** 2KB 的预览。P 说的"约 3 万字符、约 2k 预览"基本对，但单位是字节，中文要按 3 倍算。
- 预览只有开头。对 pytest 来说，开头是一排点和第一个失败，**结尾的失败列表和 `N failed` 都看不到**。模型想知道哪些测试失败了，就只能再去 Read 那个文件，多一轮工具调用，读到的内容更多，反而更贵。
- **决定：不放行，也不压到 2KB 以内。** 改成任何情况下摘要都远低于 30000 字节：`SUM_BYTES = 16000`，加上单行 400 字符的上限。实测最坏的情况（400 个失败、每行 5000 字符）输出 15.8KB，不会被落盘。
  - 压到 2KB 做不到"失败名一个不丢"：25 个名字本身就有 1.1KB 以上，E 行更放不下，违反质量优先。
  - 放行（原样输出）一旦超过 30KB，模型只能看到开头，丢失的信息比摘要还多。
- 旧版其实也可能超过 30KB：`head -120` 只限行数不限字节，120 行长行就能超。新版用字节上限把这个口子补上了。
- 官方文档没有查（任务要求"文档或实验至少一样"，这里做了实验）。没有核实 `BASH_MAX_OUTPUT_LENGTH` 这类环境变量会不会改变这个阈值。如果所有者设置过它，`SUM_BYTES` 要相应调整。

## 4. 回归（全部实测，最终上线的版本）

- **回放** `python3 tools/replay/replay_test_filter.py`：**退出码 0**。
  - 19,951 条去重命令里，改写从 82 条 / 99 次变成 **86 条 / 104 次**。
  - **orig_ok_new_bad = 0**、rewrite_exception = 0、zsh_new_bad_while_orig_ok = 0（新增的语法错误为 0）。
  - misfire 5，没变；miss_inscope 从 1562 降到 1558。
  - 新增的 4 条全部走 `tail_split`：3 条是 `cd X; printf '…' > f.py; python -m pytest -q -x …`（前缀有重定向），1 条是 `…| head; wc …; lsof … 2>&1 | head; … ; .venv/bin/python -m pytest --version`。逐条看过，前缀都原样保留。
  - 以上是只看结构的统计，没有抄命令原文。
- **K_testall** `python3 tools/round5/K_testall.py`：**13 个套件 13 个通过，可识别用例 462/462**，退出码 0。其中 tests_r6T 28/28，scorecard current 107/107，I_adapt_matrix 21/21，adapters 36/36，D_tests 47/47。
- **scorecard** `python3 tools/scorecard.py --version all`：**当前版 107/107（100%），不适用 0**；v0 是 20/58。TF01–TF24 全过。按惯例重写了 reports/scorecard.md 和 history 里今天的条目。
- **profiles/test_project_profile.py**：33/33（T05 改过，见下一节）。
- **第 4 轮的对照工具**（`scratchpad/r4B4/compare.py`，B4/B5 的 97+103 条）：bash 全一致，zsh 全一致，**MISMATCH 为空**。包装数多了 1 条，是单行 `[[ a && b ]] && pytest`，旧版保守放行，现在能正确包装。`entry_b5.py`（真实 hook 入口 + 画像路径 + 真实 pytest，22 次）：**BAD 0/22**。
- 端到端（实测）：通过真实 hook 入口（stdin JSON）改写 `[[ -f test_many.py && -d . ]] && timeout 120 python3 -m pytest -q …`，再在 zsh 里执行，rc=1，25/25 个失败名，50 行 E。

## 5. 改了哪些文件、备份、撤回

| 文件 | 改动 | 备份 |
|---|---|---|
| `hooks/test_filter.py` | 新增 `tail_split`、`strip_timeout`、`summarize` / `summarize_main`、`rewrite_last`、`grep_for`；`wrap` 改成调用 `--summarize`，失败时退回旧 grep；`rewrite` 拆段失败时改走 `rewrite_last`；main 里先处理 `--summarize`。`split_segments` 和 `prune_old_logs` 一行没动。264 行 → 572 行 | `backups/test_filter.py.pre-r6T` |
| `profiles/test_project_profile.py` | 只改了 T05：原来是"和 `pre-r5I` 备份逐字相同"，本轮有意改了摘要，这一条必然不成立；改成比较"同一个 hook 有画像 vs 没画像（不给 cwd）逐字相同"，这才是 R5I 要守的不变量 | `backups/test_project_profile.py.pre-r6T` |
| `tools/tests_r6T.py` | 新建 | – |

上线方式：先备份，再在副本里改和测（`/tmp/r6T/stage`，以及去掉 state/heldout/_to_delete 的项目镜像 `/tmp/r6T/mirror`）。镜像里先跑过 TF01–24、K_testall、回放、compare、entry_b5，测过以后才 `cp -p` 替换。替换后在真实目录里把第 4 节的全部回归再跑了一遍。

撤回（一条命令）：
```
cd ~/Documents/Projects/claude-usage-saver; cp -p backups/test_filter.py.pre-r6T hooks/test_filter.py; cp -p backups/test_project_profile.py.pre-r6T profiles/test_project_profile.py; python3 tools/replay/replay_test_filter.py; python3 tools/scorecard.py --version all
```
撤回以后 `tools/tests_r6T.py` 会失败，这是预期的；不想要它就 `mv tools/tests_r6T.py _to_delete/`。

## 6. 疑虑
- 摘要里写死了 `sys.executable` 和 hook 的绝对路径。如果那个 python 不在了或者 hook 被移走，会自动退回旧的 grep/tail，测试已经覆盖了这种情况，只是摘要质量会回到旧版水平。
- 名字规则主要对着 pytest 调过。unittest、TAP、go、cargo 只做了通用识别（`FAIL:`、`not ok`、`--- FAIL:`、`... FAILED`），没有用真实的 jest/vitest 输出测过，那些仍然靠第 3 级的旧 grep 规则。
- 通用名字规则 `^ERROR\s+\S` 也会认到普通程序日志里的 `ERROR xxx` 行。后果只是这类行优先显示，失败数的估计偏大，预算跟着变大，不会丢信息。
- `tail_split` 只认单行。多行脚本里最后一行是 pytest 的写法，如果 `split_segments` 拆不了（例如中间有管道），仍然放行。这是按所有者"只加简单的"的要求做的。
- tests_r6T 每次会在系统临时目录留下 `r6T_*` 目录（不许 rm，所以没有自动清理），一次约几百 KB。

## 额外发现
1. **adapters/common/usaver_run.py 有同样的 R28 问题**（实测读代码：它的 `select_lines` 就是 `grep -B2 -A12 | head -120`）。Codex、Gemini 这些工具用的就是它。它已经 `import test_filter` 了，改成调用 `test_filter.summarize(lines, GREP_PAT, log)` 大约只要 5 行。归 H 负责，我没有改。收益：别的工具上失败多的时候也不会丢名字。
2. **其它大输出同样会被落盘、只给开头 2KB**。统计 transcript 里的 `persistedOutputSize`（只数个数，不看正文）：**94 次，分布在 74 个会话里，平均 106KB**。这些情况下模型只看到开头，通常还要再 Read 一次。可以考虑给 D 的 read_guard 或者一个通用的 Bash 输出 hook 加"超过 30000 字节时保留开头和结尾"。收益没有估算，需要先看这 94 次里有几次后面跟了 Read。
3. 阈值按字节算：中文输出（例如画像的"失败 / 不通过"行，或者中文的测试名）只要 1 万字左右就会被落盘。所有往 Bash 输出里写中文提示的部件（test_filter、context_guard 的提示等）都应该按字节估算长度。

## 总结
- **做了什么**：
  - 修了 R28：失败名按优先级全部保留，每个失败至少一行 E，去掉了 `grep -n` 的行号前缀，预算随失败数增长并有上限，超出上限时明确写出还有几个失败没显示、完整日志在哪。
  - 新增 `timeout` / `gtimeout` 写法，以及"单行简单前缀 + 最后一段 pytest"的改写。heredoc 和多行 `[[` 继续放行。
  - 实测确认 Claude Code 的输出超过 30000 **字节**就落盘、只给开头 2KB。据此把摘要的上限定为 16000 字节，不放行，也不压到 2KB。
- **数字（实测）**：
  - R28 可见失败名从 11/25 提高到 **25/25**，E 行从 0 提高到每个失败都有，rc 保留。
  - tests_r6T 新版 **28/28**，旧版 4/28。
  - 回放：退出码 0，改写 82→86 条，**新增语法错误 0**。
  - K_testall **462/462**（13/13 个套件），scorecard **107/107**，compare 的 bash/zsh 全一致，entry_b5 0/22 坏。
  - 代价：失败很多时摘要比旧版大，25 个失败约多 5KB。
- **要所有者决定**：
  - `SUM_BYTES = 16000` 和 `120 + 4×失败数（上限 300 行）` 这两个预算要不要调。调小更省，但失败多时会更早出现"还有 N 个没显示"。
  - 是否让 H 把 adapters 也换成新摘要（额外发现第 1 条）。
- **撤回**：见第 5 节那一条 cp 命令。

## T2：打回 1 次，逐行进度行（PASSED）没有去掉（2026-11-07）

### 症状和原因（实测复现）
- 复现：300 个测试、25 个失败，`pytest -q` 加一个插件，插件每个测试打印一行 `nodeid PASSED/FAILED`（conftest 里的 `pytest_runtest_logreport`）。用 R6T1 版实测：原输出 554 行，可见 **287 行**，和盲测报告的数字完全一样。其中 73 行 PASSED，同一个失败名最多出现 2 次。
- 原因：
  1. 进度里的 `… FAILED` 行能被 grep 模式匹配上，第 3 级会带上它前 2 行、后 12 行的上下文，于是把周围的 PASSED 行一起带了进来。
  2. 行数预算没有把"省略标记"行算进去，也没有"不超过原来一半"这个上限。

### 改法（只改 `hooks/test_filter.py` 的 `summarize`，以及新增 `progress()` 和几条正则）
- **认进度行**，两种格式：
  - `nodeid 状态 [ 1%]`：`-v` 和插件都是这种。允许后面跟 `(原因)`，也允许行首粘着 `-q -s` 的进度字符 `.` 或 `F`。
  - `状态 nodeid`：`-rA` 的 summary 和 xdist 的 `[gw0] [ 1%] PASSED …` 是这种。
- **PASSED、SKIPPED、XFAIL 一律丢掉**，在"结尾"分隔线前写一行计数，例如 `…（省略 275 行 PASSED 进度、25 行 FAILED/ERROR 进度（名字已在汇总里））…`。XPASS 保留。
- **FAILED/ERROR 进度行去重**：short test summary 里已经有同名的就丢掉，summary 自己的那一行永远不丢。没有 summary 时（比如关掉了 summary），FAILED/ERROR 进度行本身就当失败名，第 0 级保留。
- 省略区间里如果全是被丢掉的进度行，就不再打印"省略第 a–b 行"。
- **50% 上限**：
  - 可选内容受"可见行数不超过原来一半"的约束（日志很短时至少允许 40 行，结尾和提示行要扣掉）。可选内容指：出错位置行、第 2 级、第 3 级。
  - **必保内容**只受原来的行数预算约束：失败名、小节标题、每个失败第一行 E 加上紧挨着它的第二行 E（通常是 `assert 209 == (209 + 1)` 这种带具体值的行）。宁可超过一半，也不丢这些。
  - 省略标记也算进行数。
- **只对 pytest 形状的日志生效**：有 summary、FAILURES 小节或进度行才算 pytest 形状。jest、cargo、lake、go 不受影响，实测 4 种合成日志（493、426、412、525 行）和 R6T1 版的输出**逐字节相同**。

### 结果（实测，真实 pytest，`/tmp/r6T/t2/shapes.py`）
| 形状（300 个测试、25 个失败） | 原输出 | R6T1 可见 | **T2 可见** | 名字 | E 覆盖 | 可见 PASSED | 同名 FAILED 最多出现 |
|---|---|---|---|---|---|---|---|
| `-q -s` + 插件逐行打印（盲测形状） | 554 | 287（52%） | **196（35%）** | 25/25 | 25/25 | 73 → **0** | 2 → **1** |
| `-v` | 560 | 264（47%） | **196（35%）** | 25/25 | 25/25 | 52 → **0** | 2 → **1** |
| `-v -rA` | 836 | 266（32%） | **197（24%）** | 25/25 | 25/25 | 52 → **0** | 2 → **1** |
| `-v`，失败测试体较长 | 760 | 264 | **196（26%）** | 25/25 | 25/25 | 0 | 1 |
| `-q`（R28 形状，tests_r6T） | 533 | 281 | **195（37%）** | 25/25 | 25/25 | – | 1 |
| `-q`，失败测试体很短 | 258 | 248（96%） | **156（60%）** | 25/25 | 25/25 | – | 1 |

- 以上各形状的退出码都是 1→1，`25 failed, 275 passed` 汇总行都在。
- 端到端（实测）：用真实 hook 入口（stdin JSON）改写盲测形状，在 zsh 里执行：rc=1，196 行，PASSED 0 行，25 个失败名。
- **没做到 50% 的一种形状**：纯 `-q`、没有进度行、每个失败原本只有约 8 行（258 行）的情况，可见是 60%。原因：必保内容本身就是每个失败 5 行（标题 + 省略标记 + 2 行 E + 省略标记），再加 26 行名字和 12 行结尾。要压到一半只能丢 E 行，和"E 行都要保留"冲突，所以按质量优先保留了。R6T1 在这个形状上是 96%。如果所有者要求任何形状都硬性不超过 50%，可以把"第二行 E"放回可选内容：这个形状会降到约 51%，代价是 8 个失败只剩 `AssertionError: value mismatch`，看不到具体值。

### 回归（全部实测，最终上线版本）
- `tools/tests_r6T.py`：新增 4 条（`-q` + 插件，bash 和 zsh；`-v`；`-v -rA`；SKIPPED/XFAIL/XPASS 以及没有 summary 时的 FAILED 进度行）。**新版 32/32；最初的版本 4/32**。
- `replay_test_filter.py`：**退出码 0**。改写 86 条 / 104 次，orig_ok_new_bad 0，rewrite_exception 0，zsh_new_bad_while_orig_ok 0，misfire 5，miss_inscope 1558，都和 T1 一样。
- `K_testall`：**15 个套件 15 个通过，516/516**（这期间别人新加了 tests_r6I3 和 tests_r6P2，也都通过）。
- `scorecard --version all`：当前版 **107/107**。profiles 测试 33/33。
- 第 4 轮的 `compare.py`：bash 和 zsh 下 MISMATCH 都为空。`entry_b5.py`：BAD 0/22。

### 文件和撤回
- 改了 `hooks/test_filter.py`（572 行 → 636 行）和 `tools/tests_r6T.py`（新增 4 条用例）。
- 按要求没有新建备份，撤回仍然用第 5 节那条命令（回到 pre-r6T）。T1 版本另存了两份副本在 `/tmp/r6T/test_filter.py.r6T1` 和 `/tmp/r6T/tests_r6T.py.r6T1`，只想退回 T1 的话：`cp -p /tmp/r6T/test_filter.py.r6T1 ~/Documents/Projects/claude-usage-saver/hooks/test_filter.py`（/tmp 重启后会被清掉）。

## T3：打回第 2 次，短日志直接 cat 时绕过了字节和行长限制（2026-11-07）

### 问题（Codex 复现，我也实测复现了）
- 日志不超过 40 行时，wrap 走的是直接 `cat` 的分支，16000 字节和单行 400 字符两条限制都不经过。
- 例子：`assert False, '中' * 12000` 再加一个 `assert 1 == 2`。原输出 21 行、36,811 字节（不同测量方法下是 36,796 到 36,906 字节，差别在路径行），T2 版改写后照样是 22 行、36,895 字节。
- 超过 30000 字节就会被 Claude Code 落盘，模型只看到开头 2KB，第二个失败就看不到了。

### 改法（只改了 `wrap` 里这一个判断，摘要逻辑没动）
- 直接 `cat` 改为三条都满足才走：行数 ≤ 40（`wc -l`）、字节数 ≤ `SUM_BYTES` = 16000（`wc -c`，按 UTF-8 字节）、没有长于 `LINE_CHARS` = 400 的行（`awk 'length($0)>400{f=1;exit} END{exit f}'`）。
- 任何一条不满足，或者 awk 出错、字节数为空，都改走 `--summarize` 摘要。
- awk 的 `length` 在不支持 UTF-8 的环境里按字节算，那样只会更早走摘要，不会漏判。

### 结果（实测）
- Codex 的复现脚本 `independent_review.py`（我在 `/tmp/r6T/t3/review_project/hooks/` 放了指向已安装 hook 的符号链接，没有动 Codex 的目录）：
  - bash 和 zsh 都是：原输出 36,811 字节、最长一行 12,024 字符 → 改写后 **2,191 字节、24 行、最长一行 420 字符**（400 字符加截断说明），rc 1→1，第二个失败出现在开头 2000 字节里。
  - 脚本里其余 3 项（`simulated_400_unicode` 15,737 字节并有提示、`simulated_xdist`、非 pytest 4/4 逐字节相同）都通过。
- `tools/tests_r6T.py` 新增 2 条：
  - 本复现，bash 和 zsh 都跑：字节数、行长、两个失败名、第二个失败的 E 行、rc。
  - 正常的短日志仍然原样输出。
  - **34/34 通过**；同样的用例在 T2 版上失败（36,895 字节）。
- `replay_test_filter.py`：**退出码 0**。改写 86 条 / 104 次，orig_ok_new_bad 0，rewrite_exception 0，zsh_new_bad_while_orig_ok 0。
- `K_testall`：**15 个套件 15 个通过，527/527**。

### 文件和撤回
- 改了 `hooks/test_filter.py` 的 `wrap`（新增 3 行 shell 判断和注释），`tools/tests_r6T.py` 新增 2 条用例。
- 撤回仍然用第 5 节那条命令（回到 pre-r6T）。T2 版另存了一份副本在 `/tmp/r6T/test_filter.py.r6T2`。
- 已知但没改（不在这次范围里）：python 不能用时退回的旧 `grep | head -120 + tail` 路径也不限字节，和 T1 之前的行为一样。

交付完成
