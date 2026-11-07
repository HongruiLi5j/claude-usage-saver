# 第 6 轮 H2：usaver-run 改用 test_filter.summarize（只保留一份摘要实现）

实施者 H，2026-11-07。只改了 `adapters/common/usaver_run.py` 和 `adapters/tests/test_adapters.py`。`hooks/test_filter.py` 没动（改前改后 sha1 都是 `21d57a29…`）。没有 push/commit，没有用 rm，没有读 .secret/、sealed/、test2、tools/heldout/，没有改 ~/.claude/settings.json 和 ~/.codex，没有把 adapters 放上 PATH。

凡写"实测"的都真跑过；写"估算"的给了算法。

## 1. 改了什么

`adapters/common/usaver_run.py`（165 → 186 行）：
- 导入 test_filter 时顺带取 `_tf_summarize = getattr(_tf, "summarize", None)`；导入失败或没有这个函数就是 `None`。
- `summarize()`：日志超过 SHORT（40）行时，先调 `test_filter.summarize(lines, GREP_PAT, logpath)`，参数和 hook 的 `summarize_main` 完全一样：按 `\n` 切行、去掉末尾空行（和 `wc -l` 的数法一致），`GREP_PAT` 也取自 test_filter。返回的正文原样打印，后面再加原来那行英文提示 `[usaver-run: output filtered; all N lines are in <log>; rerun with USAGE_SAVER_RAW=1 for raw output]`。保留这行有两个原因：Codex 的 AGENTS 片段是英文的；提示行里写着日志路径，Codex 如果把中间截掉了，模型在末尾还能看到路径。
- 保底：以下情况退回旧规则（`select_lines`，也就是 `grep -B2 -A12 | head -120`，再加 tail 12 行），不会崩：
  - 找不到 test_filter，或者它没有 `summarize`；
  - `summarize` 抛异常，或者返回的不是非空字符串；
  - 日志超过 64M 字符。这条和 `summarize_main` 里 64MB 的规则一致。
- `run()`：`summarize` 本身如果抛异常，就打印一行 `could not summarize … full output is in <log>`，退出码照样返回。
- 代码没有复制，新摘要的逻辑全在 `hooks/test_filter.py` 里。`select_lines` 保留，只作保底用。
- 模块开头的说明文字同步改了。

`adapters/tests/test_adapters.py`（36 → 41 条）：
- 改了 1 条：`test_long_failing_output_is_filtered` 里的分隔线断言从 `----- tail -----` 改成 `----- 结尾 -----`，因为新摘要用的是 test_filter 的分隔线。
- 新增 5 条：
  1. `test_r28_real_pytest_all_25_names_visible`：用真实 pytest 跑 300 个测试、25 个失败，`-q` 和 `-v` 各一次。断言：25 个名字全部可见；每个失败都有 `E assert i == i + 1` 行；rc=1；可见输出小于 30000 字节；没有 PASSED 进度行；`25 failed, 275 passed` 在。
  2. `test_summary_is_test_filter_summarize_verbatim`：usaver-run 的正文和 `hooks/test_filter.py --summarize 日志 模式` 的输出逐字相同，只多了首行路径和末行英文提示。这条用来证明只有一份实现。
  3. `test_fallback_when_test_filter_summarize_raises`：`summarize` 换成抛异常、`None`、返回空串、返回非字符串 4 种情况，都退回旧规则，rc=5 保留。
  4. `test_rc_kept_even_if_summarize_itself_fails`：整个 `summarize` 都抛异常时，rc=7 保留。
  5. `test_standalone_copy_without_test_filter`：只拷走 `usaver_run.py`，旁边没有 hooks，用 `python -I` 跑。不崩，走旧规则，rc=4 保留，stderr 里没有 Traceback。
- 反向检查（实测）：把这批用例放到旧版 usaver_run 上跑，5 条失败、4 条通过，说明新用例确实能区分新旧两版。通过的 4 条是保底类和独立拷贝类的用例，旧版本来就满足。

## 2. 改前 / 改后数字（全部实测，真实 pytest 7.4.4，Python 3.12.7）

形状都是 300 个测试、25 个失败（失败号和 tools/tests_r6T.py 的 R28 相同）。测量脚本是 `/tmp/r6H2/scripts/measure.py`（不在项目里，/tmp 重启后会清掉）。

| 形状 | 原输出 | 旧版可见 | **新版可见** | 失败名 旧→新 | 有 E 行的失败 旧→新 | 可见 PASSED 行 旧→新 | rc |
|---|---|---|---|---|---|---|---|
| `-q`，失败测试体 11 行（R28） | 533 行 / 14750 B | 135 行 / 4439 B | **196 行 / 8465 B** | 11 → **25** | 5 → **25** | 0 → 0 | 1→1 |
| `-v` | 560 行 / 32976 B | 135 行 / 11114 B | **197 行 / 8620 B** | 11 → **25** | 0 → **25** | 109 → **0** | 1→1 |
| `-q -s` + 插件逐行打印 | 554 行 / 18810 B | 135 行 / 5536 B | **197 行 / 8570 B** | 11 → **25** | 0 → **25** | 109 → **0** | 1→1 |
| `-v -rA` | 836 行 / 42135 B | 135 行 / 11114 B | **198 行 / 8701 B** | 11 → **25** | 0 → **25** | 109 → **0** | 1→1 |

- 新版每种形状都比 30000 字节低得多。上限由 test_filter 的 `SUM_BYTES = 16000` 加我这边约 300 字节的首行和末行保证。
- 代价：`-q` 形状比旧版多 4 KB，按 4 字节约 1 token 估算多约 1k token（**估算**）。`-v` 一类有进度行的形状，新版反而比旧版小。
- 测试套件：

| | 改前（实测） | 改后（实测） |
|---|---|---|
| `python3 -m pytest -q adapters/tests/test_adapters.py` | 36 passed | **41 passed** |
| `python3 tools/round5/K_testall.py` | 15/15 个套件，520/520 | **15/15 个套件，525/525**（adapters 41/41，tests_r6T 32/32，scorecard 107/107，I_adapt_matrix 21/21） |

改前那次 K_testall 是在替换文件之前跑完 adapters 套件的（当时用的还是旧测试文件，36/36），所以是干净的基线。

## 3. 备份与撤回

备份：
- `backups/usaver_run.py.pre-r6H2`（sha1 `0b42e077…`）
- `backups/test_adapters.py.pre-r6H2`（sha1 `8cf6c896…`）

撤回（一条命令，zsh 可直接粘贴）：
```
cd ~/Documents/Projects/claude-usage-saver; cp -p backups/usaver_run.py.pre-r6H2 adapters/common/usaver_run.py; cp -p backups/test_adapters.py.pre-r6H2 adapters/tests/test_adapters.py; python3 -m pytest -q -p no:cacheprovider adapters/tests/test_adapters.py
```
撤回后应该是 36 passed。`adapters/bin/usaver-run` 是指向 `../common/usaver_run.py` 的符号链接，不用另外处理。

## 4. 需要所有者决定的事

1. **项目画像的 grep 片段没接进来**。Claude Code 的 hook 会把画像里的 `grep_extra` 并进匹配模式（例如 Lean 的 `warning:|sorry`、中文的"失败"），usaver-run 只用默认的 `GREP_PAT`。这一点和改前一样，没有变坏。要接的话，可以调 `test_filter.load_profile(cwd)` 和 `grep_for()`，大约 5 行，但画像在 Codex 沙箱里能不能读到还没验证。要不要做？
2. **英文提示行要不要保留**。现在摘要末尾同时有 test_filter 的中文提示和 usaver-run 的英文提示，后者约 150 字节，内容有点重复。我倾向保留：Codex 用的是英文片段，而且这行带着日志路径。
3. **超大日志（超过 64M 字符）的保底仍是旧规则**。旧规则只限 120 行、不限字节，极端情况下可能超过 30000 字节。这和 hook 的保底一致，所以没改。
4. **Codex 上的实际收益没有实测**。按 round5_H 的数据，Codex 自己有 `max_output_tokens` 截断（中位数约 5000 token）。新摘要在 R28 上约 8.5 KB，按 4 字节/token 估算约 2.1k token，在截断线以内；最坏情况 16 KB 约 4k token，会接近这条线（**估算**）。真机 Codex 复测（live_codex.py）会消耗 Codex 额度，这次没跑。要不要跑？

## 5. 涉及文件
- `adapters/common/usaver_run.py`（已改）
- `adapters/tests/test_adapters.py`（已改）
- `backups/usaver_run.py.pre-r6H2`、`backups/test_adapters.py.pre-r6H2`（新建备份）
- `reports/round6_H2.md`（本报告）
- 临时文件：`/tmp/r6H2/`（暂存副本、旧版和新版测量用的目录、measure.py），以及系统临时目录下的 `r6H2-old-*`、`r6H2-new-*`（按红线没有删）

## H3：被打回 1 次，短日志绕过了字节和行长限制（2026-11-07）

### 问题（Codex 复现，我也实测复现了）
- 旧版 `summarize` 只要日志不超过 40 行（`n <= short`）就原样返回，不检查字节数和单行长度。
- 复现：两个失败的测试，第一个的断言消息是 `'中' * 12000`，第二个是 `assert 1 == 2`。日志只有 21 行、36,796 字节，H2 版原样打印出 36,880 字节。第二个失败不在开头 2KB 里，一旦被 Claude Code 落盘就看不到了。
- 这和 test_filter 在 T3 修掉的是同一个问题。

### 改法（只改 `adapters/common/usaver_run.py`；`hooks/test_filter.py` 仍然没动，sha1 是 `0e4c30a3…`，即 T3 版）
- 原样输出改成三条都满足才走，和 test_filter.wrap 的 T3 判断一致：
  - 不超过 `SHORT` = 40 行；
  - 不超过 `SUM_BYTES` = 16000 字节（按 UTF-8 计）；
  - 没有超过 `LINE_CHARS` = 400 字符的行。
- 任何一条不满足就走摘要。
- T3 的判断是写在 wrap 生成的 shell 里的（`wc -c` 加 `awk`），test_filter 里没有能直接调用的 Python 判断函数，所以没法复用函数本身。我复用的是常量：`SUM_BYTES` 和 `LINE_CHARS` 从 test_filter 读，读不到就用同样的值。
- 顺带改了保底路径（没有 test_filter 时走的旧规则）：输出的每行也截到 400 字符，否则同一个复现在保底路径下也会超过 30000 字节。

### 结果（实测）
- Codex 的复现脚本（我在 `/tmp/r6H2/h3repro` 里搭了一份 review-copy，用符号链接指向已安装的文件，没有动 Codex 的目录）：
  - adapter 输出 36,880 → **2,332 字节**，rc 1→1；
  - 两个失败名都在；
  - 第二个失败出现在开头 2KB 里：false → **true**。
- adapters 测试新增 3 条：
  1. 用真实 pytest 跑本复现；
  2. 三道门槛分别单独触发（行太长、字节太多），以及全部满足时原样输出（包括 39 行、约 15.2KB 的中文日志）；
  3. 只拷走 `usaver_run.py`（没有 test_filter）时，保底输出也在 30000 字节以内，第二个失败仍在。
- 这 3 条放到 H2 版上跑，3 条全部失败（实测），说明能区分新旧版。
- 数字：**adapters 44 passed**（41 → 44）；**K_testall 15/15 个套件、530/530**。

### 备份与撤回
- 备份：`backups/usaver_run.py.pre-r6H3`、`backups/test_adapters.py.pre-r6H3`（都是 H2 版）。
- 只退回 H2：
```
cd ~/Documents/Projects/claude-usage-saver; cp -p backups/usaver_run.py.pre-r6H3 adapters/common/usaver_run.py; cp -p backups/test_adapters.py.pre-r6H3 adapters/tests/test_adapters.py
```
- 全部退回到改前：用第 3 节那条命令（`.pre-r6H2`）。

### 需要所有者决定
- 保底路径截断超长行，是我在要求之外加的。不想要的话，删掉 `summarize` 里 `R6H3：保底路径也截断超长行` 那两行就行。

交付完成
