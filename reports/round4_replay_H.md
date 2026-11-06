# Round 4 / H：test_filter 真实命令回放

日期：2026-11-06　执行者：子 agent H
被测：`hooks/test_filter.py`（sha1 前 12 位 `d1d6c570eec2`，02:46 时的状态）与 `backups/test_filter.py.pre-r4B`（修 bug 之前）

## 做法和安全边界

- 从 `~/.claude/projects/**/*.jsonl`（含 `subagents/`）里取出所有 `tool_use.name == "Bash"` 的 `input.command`。共 909 个文件，路径含 relay-room、.secret、sealed、test2 的文件都跳过（实际一个都没碰到）。
- **没有执行任何历史命令**。只做了两件事：一是调用 `rewrite()` 做字符串改写，二是用 `bash -n -c` 和 `zsh -n -c` 检查语法（只解析、不执行，已用 `touch /nonexistent` 确认过）。被测 hook 用 `SourceFileLoader` 只读加载，并关掉了 .pyc 写入。
- 系统里只有 `/bin/bash` 3.2，分类以它为准。另外加做了 zsh 检查，因为本机 Claude Code 的 shell 是 zsh。
- 写入了这些地方：
  - 脚本：`tools/replay/replay_test_filter.py`、`tools/replay/show_examples.py`
  - 数据：`state/replay/`（`commands.jsonl`、`result_<label>.json`、`syntax_cache.json`、`extract_meta.json`）。已确认 `.gitignore` 第 2 行 `state/` 覆盖了它们。
- 重跑方法：
  - `python3 tools/replay/replay_test_filter.py [--hook PATH] [--label X] [--refresh]`：默认复用已提取的命令，加 `--refresh` 才重新扫描。发现「原 OK / 改写坏」或 rewrite 抛异常时，退出码为 1。
  - `python3 tools/replay/show_examples.py <label> [N]`：打印打码后的例子。

## 总量

| 项 | 数 |
|---|---|
| Bash 调用总次数 | 22,901 |
| 去重后命令 | 19,951 |
| 原命令本身 bash 语法就坏的 | 2 |
| 文本里出现 pytest / npm test / node --test 字样的 | 1,869 |
| 其中只是提到这些字样（在字符串、heredoc 正文、commit 信息里，并没有在跑测试） | 225 |
| 其中确实在命令位置跑测试的 | 1,644（= 82 被改写 + 1,562 未改写） |

## 分类结果（两版对比）

| 类别 | 当前版 | pre-r4B |
|---|---|---|
| 被改写（unique / 次数） | 82 / 99 | 83 / 100 |
| 原 OK、改写 OK | 82 | 82 |
| **原 OK、改写坏（bug）** | **0** | **1**（bash 下坏，zsh 下仍能解析） |
| 原坏、改写 OK / 原坏、改写坏 | 0 / 0 | 0 / 0 |
| rewrite 抛异常 | 0 | 0 |
| zsh 专有的改写坏 | 0 | 0 |
| 误包装嫌疑 | 5 | 5 |
| 像测试却没改写（漏网，含故意放行） | 1,562 | 1,561 |
| 范围外的测试命令（unittest 等） | 274 | 274 |

改写比例：在确实跑测试的 1,644 条里，只有 82 条（5.0%）被改写。按全部 Bash 调用算是 0.43%。

### 漏网原因（当前版，1,562 条）

| 原因 | 条数 | 评价 |
|---|---|---|
| `2>&1 \| tail/head/grep` | 821 | 已自带过滤，放行合理 |
| heredoc，且测试那行本身带 `\| tail` | 532 | 已自带过滤，放行合理 |
| `\| tail/head/grep`（不带 2>&1） | 65 | 已自带过滤，放行合理 |
| 重定向 `>file` 等 | 90 | 86 条是测试段自己写文件，放行合理；**4 条是别的段有 `>`（如 `printf … > x.py; pytest`），因此整条放弃** |
| `$(...)` / 反引号 | 23 | 大多是测试段自己用到，放行合理 |
| 括号 / 子 shell / 函数定义 | 15 | 放行合理 |
| **heredoc + 测试行是裸跑** | **12** | **真漏网**：`cat > x.py <<'EOF' … EOF` 之后接 `pytest -q` |
| 管道到其他命令 | 2 | 放行合理 |
| 保留字结构（`if … then pytest … fi`） | 1 | r4B 有意放行，正是修掉的那个 bug 的用例 |
| heredoc + 测试行带重定向 | 1 | 放行合理 |

真正可以收的漏网大约 **17 条（约 1%）**：12 条 heredoc、4 条是别的段有重定向、1 条 if 结构。约 91%（1,418 条）已经被模型用 `| tail/head/grep` 自己截短了，剩下的是测试段自己带重定向、子 shell 之类，放行是合理的。这里有一个值得注意的点：`| tail -5` 会把失败细节截掉，而 hook 本来想做的是「留失败、去噪音」。但这属于模型习惯问题，不是 hook 的 bug。

另外 1,644 条里有 257 条漏网和 39 条改写来自 bench / exercism 的 `ex_work` 跑法，不是日常工作。

## 每类前 10 个例子

已打码：`$SCRATCH` 代表 scratchpad 目录，`~` 代表 home 目录，uuid、长 token 已替换，`⏎` 表示换行，并做了截断。

### 原 OK、改写 OK（当前版，82 条）
1. `~/Documents/Projects/quant-backtest/.venv/bin/python -m pytest -q -x rail_fence_cipher_test.py`
2. `cd $SCRATCH/ex_work/rail-fence-cipher__… && ~/…/.venv/bin/python -m pytest -q -x rail_fence_ci…`
3. `python3 -m pytest -q $SCRATCH/r4b/test_r4b.py`
4. `python3 -m pytest -q -p no:cacheprovider $SCRATCH/r4b/test_pass.py`
5. `set -eo pipefail; python3 -m pytest -q -p no:cacheprovider $SCRATCH/r4b/test_pass.py; echo AFTER-OK`
6. `cd $SCRATCH/proj && ~/…/.venv/bin/pytest -q -p no:cacheprovider tests/test_bad.py`
7. `~/…/.venv/bin/python -m pytest -q -x isogram_test.py`
8. `cd $SCRATCH/ex_work/isogram__… && ~/…/.venv/bin/python -m pytest -q -x isogram_test.py`
9. `~/…/.venv/bin/python -m pytest -q -x resistor_color_test.py`
10. `cd $SCRATCH/ex_work/resistor-color__… && ~/…/.venv/bin/python -m pytest -q -x resistor_color_t…`

### 原 OK、改写坏（bug）
- 当前版：**无**。
- pre-r4B 有 1 条：`if [ -d $SCRATCH/r4b ]; then⏎  python3 -m pytest -q -p no:cacheprovider $SCRATCH/r4b/test_pass.py⏎fi`
  - 改写后变成 `… ] ; then ; { …`，bash 报错 `syntax error near unexpected token ';'`。
  - zsh 能容忍 `then ;`，所以在本机 zsh 下其实不会炸，但在 bash 下会。
  - 当前版通过 RESERVED 规则整条放行，已修好。

### 误包装嫌疑（两版相同，5 条 / 9 次）
1. `.venv/bin/python -m pytest --version`
2. `~/…/.venv/bin/python -m pytest --version`
3. `.venv/bin/python -m pytest --collect-only -q -s -p no:cacheprovider . --ignore=tests; echo "exit=$?"`
4. `.venv/bin/python -m pytest --collect-only -q -s -p no:cacheprovider . --ignore=tests`
5. `cd ~/Documents/AQ-o44/run2/stage/E && .venv/bin/python -m pytest --version`

危害很小。`--version` 的输出不到 40 行，当前版会直接 cat 出来，等于没过滤。`--collect-only` 的输出可能超过 40 行，这时会被 grep 和 tail 截掉，测试清单就看不全了。

### 漏网（当前版，按原因挑选，共 10 例）
1. `[2>&1|tail]` `~/…/.venv/bin/python -m pytest -q -x parallel_letter_frequency_test.py 2>&1 | tail -5`
2. `[2>&1|tail]` `~/…/.venv/bin/python -m pytest -q -x crypto_square_test.py 2>&1 | tail -2`
3. `[|tail]` `cd $SCRATCH/ex_work/matrix__…; ~/…/.venv/bin/python -m pytest -q -x matrix_test.py | tail -3`
4. `[heredoc 裸跑, 真漏网]` `cd $SCRATCH/ex_work/binary__…; cat > binary.py <<'EOF'⏎def parse_binary(…)…EOF⏎… pytest -q -x …`
5. `[heredoc 裸跑, 真漏网]` `cd $SCRATCH/ex_work/series__…; cat > series.py <<'EOF'⏎…`
6. `[别段重定向, 真漏网]` `printf 'def test_ok():\n    assert 1\n' > $SCRATCH/r4b/test_pass.py; set -eo pipefail; python3 -m pytest -q …`
7. `[别段重定向, 真漏网]` `cd $SCRATCH/ex_work/reverse-string__…; printf 'def reverse(text):…' > reverse_string.py; ~/…/pytest …`
8. `[$(...)]` `B=$(ls -dt /var/folders/…/w | head -1); R=~/Documents/Projects/quant-backtest; cd $B; …; $R/.venv/bin/python -m pytest -q tests/…`
9. `[子shell/后台]` `cd ~/Documents/Projects/quant-backtest; (.venv/bin/python -m pytest -q > ~/Documents/AQ-night/n05_full.txt 2>&1 &); …`
10. `[保留字]` `if [ -d $SCRATCH/r4b ]; then⏎  python3 -m pytest -q …⏎fi`

### 只是提到测试字样（225 条，不算漏网，供核对启发式）
举例：henchmen `post note "…全量 pytest 跑完…"`、`cat >> reports/improvements.md <<'EOF' … test_filter …`、`python3 - <<'EOF'` 正文里出现 `node --test`、`python -c "import pytest; print(pytest.__version__)"`。

### 范围外的测试命令（274 条，hook 不管）
- 265 条是 `python -m unittest`，几乎都带 `2>&1 | tail/grep`。不带管道、可能值得包装的只有 1 条。
- 2 条 `make test`，7 条其他。
- 本机历史里没有 jest、vitest、cargo、go 的测试命令。

## 对另一个 agent 正在做的修复的意见

1. **语法安全已经达标。** 当前版在 82 条真实改写上，bash 和 zsh 都是 0 坏。r4B 的保留字规则修掉了唯一一条历史上真实出现过的 bug。修复时请保持这个基线：改完跑一次 `replay_test_filter.py`，退出码必须是 0。
2. **覆盖率很低（5%），但大部分是故意放弃的。** 真正值得收的只有两种，加起来约 17 条，优先级不高：
   - 「别的段有重定向就整条放弃」：可以改成只在**测试段本身**含 `|<>&` 时放弃，其他段原样保留。这需要 split_segments 能把带重定向的段当作不透明文本保留下来，改动不小，要谨慎。
   - 「heredoc 在前、测试在后」：heredoc 正文跨行，拆段很容易出错。**建议继续放行，不要冒险。**
3. **误包装**：建议在 `is_test` 里排除 `--version`、`--help`、`-h`、`--collect-only`、`--co`、`--fixtures`、`--markers`（类似 INTERACTIVE 的写法）。这 5 条 / 9 次都会因此消失。
4. **不建议为了覆盖率去拆 `2>&1 | tail -N`。** `2>&1 | tail` 和 `| tail` 合计占漏网的 57%，但输出已经短了。一定要做的话，应该另开一个提示类的功能，例如提醒「tail 会丢失败细节」，不要改写用户的管道。
5. **unittest**：每次 265 条都带管道，加进 `is_test` 几乎收益为零，暂不需要。
6. 注意：这台机器的 Bash 工具跑在 zsh 里，zsh 对 `then ;` 等写法更宽容，所以「bash 坏、zsh 好」的 bug 在本机可能看不到。回放同时检查了两种 shell，正好能兜住这一点。

## 总结（5 行）
1. 19,951 条去重命令（22,901 次调用）全部回放，没有执行任何命令，只做了 bash/zsh 的 `-n` 解析。
2. 当前版改写 82 条，bash 和 zsh 下都是 0 条语法损坏；pre-r4B 有 1 条（`if … then pytest … fi`）在 bash 下损坏，已被 r4B 修好。
3. 确实跑测试的命令里只有 5% 被改写，约 91% 的漏网是模型自己已经 `| tail` 过的，真正可以收的漏网约 17 条。
4. 误包装 5 条（`pytest --version` / `--collect-only`），建议 is_test 排除这类只查询、不跑测试的参数。
5. 回放脚本可以重复跑，退出码 1 表示有改写 bug。另一个 agent 修完后，跑一下 `python3 tools/replay/replay_test_filter.py` 就能复测。
