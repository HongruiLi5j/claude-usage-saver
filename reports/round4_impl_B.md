# 第 4 轮实施 B（2026-11-06）

范围：只改了 `~/.claude/skills/model-router/SKILL.md`、`hooks/test_filter.py`、`tools/scorecard.py`（只加了一个场景）。没有改 improvements.md、settings.json、relay-room。

## 1. 每个文件改了什么

### `~/.claude/skills/model-router/SKILL.md`（A）
只在"## 开与关"一节末尾加了两条，其它节一字没动：
- 读得多的任务要拆：预计读入 >10 万字符或 >25 轮的，按目录/模块拆成几个 agent，每个上下文约 10 万以内；prompt 写明先 `grep -n`/`rg` 定位，再用 Read 的 offset/limit 只读需要的段，不要整文件 `cat -n`；`ls`/`find`/`wc` 加 `| head`。
- 通读代码库、写文档/盘点类：先让 haiku 出"文件:行号 + 一句话作用"的摘录，再让 Sonnet/Opus 读摘录来写；注明质量影响未测，先做 A/B。

依据是 `reports/round4_subagent_cost.md` 的改法 1、2。改法 1 原文里"每写完一章就把细节写进文件"这句为了简短没有收进去；原文里的 `sed -n a,bp` 按你的要求换成了 Read offset/limit。

### `hooks/test_filter.py`（B，D7）
- 新增 `prune_old_logs(logdir=LOGDIR, days=7, every_s=3600)`：把 `state/testlogs/` 里超过 7 天（按修改时间）的 `run-*` 文件 `os.rename` 到 `state/testlogs/_old/`。不删任何东西；`_old/` 里已有同名文件就跳过，不覆盖。
- 节流：时间戳文件放在 `state/.testlogs_last_prune`，它的修改时间不到 1 小时就直接返回。先写时间戳再整理，所以即使整理中途出错，一小时内也不会重试。
- 异常：外层和每个文件各有一层 `try/except: pass`；函数不往 stdout/stderr 写任何东西。
- 调用点：只在 hook 确实要包装测试命令时调用（在 `os.makedirs(LOGDIR)` 之后、打印 JSON 之前）。被包装命令的文本、输出和退出码都不变，因为整理发生在 hook 进程里，与 bash 执行的命令无关。
- 顶部 import 加了 `time`，docstring 加了一行说明。

### `tools/scorecard.py`（C）
只加了场景 **TF16**（test_filter 组，来源 D7，v0 标为不适用）。它跑在 scorecard 自带的临时目录里（hook 副本的 LOGDIR 被替换成临时目录），步骤：
1. 放一个 8 天前的 `run-OLD8D` 和一个 1 天前的 `run-NEW1D`，执行 `pytest -q`（用的是假 pytest，rc=1）。
2. 检查 rc==1、FAILED 可见、`run-OLD8D` 已在 `_old/` 而原位置没有了、`run-NEW1D` 还在原处。
3. 节流：再放一个 8 天前的 `run-OLD8D-2`，第二次执行，rc 仍是 1，而且这个文件**没有**被移走。

## 2. scorecard 前后

| | 改前 | 改后 |
|---|---|---|
| 当前版 | 54/54（100%） | **55/55（100%）** |
| 初版 v0 | 16/42，不适用 12 | 16/42，不适用 13（多出的是 TF16） |

中间出过一次问题，已经修好：第一版把时间戳放在 `testlogs/.last_prune`，结果 TF01 失败（54/55）。TF01 用 `os.listdir(testlogs)[0]` 取日志，取到了这个空的时间戳文件。我没有改 TF01，而是把时间戳挪到了上一级 `state/.testlogs_last_prune`，让 testlogs 里只放日志，之后 55/55。

反向对照：在内存里把 current 的 test_filter 换成 `backups/test_filter.py.pre-r4B` 再跑 TF16，结果是 fail。说明这个场景确实在检查新功能，不是怎么跑都会过。另外 `prune_old_logs('/nonexistent/...')` 不抛异常。

## 3. 手工检查（实测）
在 scratchpad 写了一个两条用例的测试文件（一条过、一条 `assert 1 == 2`），通过真实 hook 跑了 `python3 -m pytest -q .../r4b/test_r4b.py`：
- 输出被过滤：有 grep 摘要、"----- 结尾 -----"、"[测试输出已过滤：完整 12 行在 .../state/testlogs/run-RUNcRn …]"。
- 退出码是 1，和原始 pytest 一致。
- 生成了 `state/.testlogs_last_prune`（02:37）；现有 6 个日志都是今天的，所以一个都没移，也没有建 `_old/`，这是预期行为。

## 4. 备份与撤回
备份：
- `backups/model-router.SKILL.md.pre-r4B`
- `backups/test_filter.py.pre-r4B`
- `backups/scorecard.py.pre-r4B`

撤回（一行）：
```
cd ~/Documents/Projects/claude-usage-saver; cp -p backups/model-router.SKILL.md.pre-r4B ~/.claude/skills/model-router/SKILL.md; cp -p backups/test_filter.py.pre-r4B hooks/test_filter.py; cp -p backups/scorecard.py.pre-r4B tools/scorecard.py; mkdir -p _to_delete; mv state/.testlogs_last_prune _to_delete/ 2>/dev/null; python3 tools/scorecard.py --version all
```

## 5. 疑虑
- 只有在包装测试命令时才整理。长期不跑测试的话日志不会被移动，不过那时也不会产生新日志。
- `_old/` 本身永远不会清理（按 D7，不删），会一直变大；单个日志通常是几 KB，问题不大。
- 时间戳放在 `state/` 而不是 `testlogs/` 里，这一点和最直接的写法不同，原因见第 2 节。
- 按要求跑 `--version all` 时会重写 `reports/scorecard.md`，并覆盖 `scorecard_history.json` 里今天的条目，这是 scorecard 本来的行为。改前我在 `$TMPDIR` 留了这两个文件的副本，可以忽略。
- 同一时间轮次 C 改了 `tools/weekly_compare.py`（有 `.pre-r4C` 备份）。我最后一次 scorecard 的 WC01–WC07 全过，但如果 C 在那之后又改过，要重跑一次确认。
- model-router 的第二条（haiku 摘录）的质量影响没测过，已在条目里写明"先 A/B"。

---

# 第 4 轮实施 B2：修 test_filter 的 🔴1/🔴2 与 🟡3–6（2026-11-06）

依据 `reports/round3_review.md` 第一节 🔴/🟡 与第六节场景表。只改了 `hooks/test_filter.py` 与 `tools/scorecard.py`。

## 1. hooks/test_filter.py 改了什么
- **🔴1 多行控制结构**：选了更安全的"放行"方案。`split_segments` 拆完后，只要任何一段的第一个词是 bash 保留字（`if then else elif fi for while until do done case esac in { } function select ! time coproc`），整条命令放行不包装。代价：`for …; do pytest; done` 这类单行结构里的测试也不再过滤了（以前能过滤且不坏），属于少省一点、但绝不改坏。`[[ … ]]` 没放进保留字表：它是完整命令，拼回后不会出语法错误，照常包装。
- **🔴2 / 🟡3 pipefail**：wrap 里 `{ grep … | head -120; } || true`、`{ tail …; } || true`，行数用 `__usn=$(wc -l … | tr -d ' ') || __usn=0`。退出码只由最后的 `(exit $__usrc)` 决定。顺带去掉了 macOS `wc` 的前导空格。
- **🟡4 mktemp 失败**：`__usL=$(mktemp … 2>/dev/null) || __usL=`；为空就 `{ 原段 }` 原样执行（不过滤）。
- **🟡5**：(a) 运行前先打印 `[测试输出写入 <日志路径>]`；(b) 测试参数里有 `--watch*`（含 `--watchAll`）、`--pdb`、`--trace`、`--looponfail` 时这一段不包装。没有处理 `-w`，因为含义有歧义。
- **🟡6 短输出**：日志 ≤40 行（常量 `SHORT`）直接 `cat`，不再 grep 一遍再 tail 一遍。看到的行数 = 原输出 + 1 行（路径头）。长输出仍走 grep + 结尾 + 提示行。
- 提示行改为"完整 N 行在上面的日志里"，因为路径已经在开头打印过了。

## 2. scorecard.py 新场景（TF17–TF24，编号接在 TF16 之后）
所有场景都在临时目录里跑。原命令和改写后的命令都用真实 `/bin/bash -c` 执行并比较 rc。每个场景单独写假 pytest（`fake()`），通过时只输出 `2 passed`，不含 FAIL/ERROR 等字样。

| ID | 覆盖 | 断言 |
|---|---|---|
| TF17 | 🔴1 | if/for/while/else/{/until 六种多行写法：rc 与原命令一致、无 syntax error、测试真的跑了 |
| TF18 | 🔴2 | `set -eo pipefail; pytest -q（通过）; echo AFTER`：被包装、rc=0、passed 和 AFTER 都可见 |
| TF19 | 🟡3 | pipefail + 3000 个失败（>64KB）：rc=1（不是 141），结尾、"3000 failed"、日志目录可见，`nope` 不执行 |
| TF20 | 🟡4 | hook 之后把 testlogs 设为 0555（mktemp 失败）：测试照样跑，`&& echo RAN` 照常，rc 一致 |
| TF21 | 🟡5a | 日志路径出现在测试第一行输出之前 |
| TF22 | 🟡5b | `pytest --pdb`、`pytest -q --looponfail`、`npm test -- --watch`、`node --test --watch` 不包装 |
| TF23 | 🟡6 | 12 行失败输出：看到的行数 ≤ 原输出 + 2，FAILED 可见，rc=1 |
| TF24 | 第六节 | 只读借用 `quant-backtest/.venv/bin/python -m pytest -p no:cacheprovider`（cwd 是临时目录，加了 PYTHONDONTWRITEBYTECODE=1）：通过、失败、pipefail 通过、pipefail 失败、多行 if 五条，rc 都一致；venv 不存在时标"不适用" |

## 3. 结果（实测）
- **harness.py 67 条**（cases1 有 47 条，cases2 有 20 条；用 `scratchpad/harness_r4b2.py`，与原 harness 一样，只是通过 `TF_PATH` 环境变量指向 `hooks/test_filter.py`）：
  - 改前：6 条 RC-MISMATCH，其中 5 条是 SYNTAX-ERROR。
  - 改后：**0 条不一致**；放行从 18 条增加到 26 条，多出来的是多行/单行控制结构和 `npm test -- --watch`。
- **scorecard `--version all`**：

  | | 改前 | 改后 |
  |---|---|---|
  | 当前版 | 55/55 | **63/63** |
  | v0 | 16/42 | 17/50（v0 恰好通过 TF17，因为 v0 不拆换行，遇到多行命令直接放行） |

- **反向对照**：在内存里把 current 换成 `backups/test_filter.py.pre-r4B2`，**TF17–TF24 全部 fail**（TF16 pass）。说明 🔴 场景确实能抓到旧 bug。
- **手工（真实 hook）**：
  - `set -eo pipefail; python3 -m pytest -q …/test_pass.py; echo AFTER-OK`：先打印日志路径，然后 `1 passed`、`AFTER-OK`，rc=0。按复核报告，旧版在这种情况下 rc=1、没有任何输出。
  - 多行 `if …; then⏎ pytest⏎ fi`：放行，正常 `1 passed`。
  - 普通 `python3 -m pytest -q …`：被包装，路径在前。
  - 有一次以为 hook 没生效，查下来是因为那条命令前面带了 `printf … > file`，重定向本来就会让整条放行，属于正确行为。

## 4. 备份与撤回
备份：`backups/test_filter.py.pre-r4B2`、`backups/scorecard.py.pre-r4B2`

撤回本节改动（一行）：
```
cd ~/Documents/Projects/claude-usage-saver; cp -p backups/test_filter.py.pre-r4B2 hooks/test_filter.py; cp -p backups/scorecard.py.pre-r4B2 tools/scorecard.py; python3 tools/scorecard.py --version all
```

## 5. 疑虑
- 保留字放行偏保守，`for/if` 里的测试不再省 token。如果以后想覆盖这类命令，可以改成"拼回时保留原换行"，但那需要单独一轮来做和测试。
- TF24 依赖 quant-backtest 的 venv（只读调用）；venv 被移走时该场景标"不适用"，不会变红。
- 💭7–9（`time/uv run` 等漏网、`python script.py -m pytest` 误包装）没有处理，不在本次范围内。

---

# 第 4 轮实施 B3：scorecard 夹具 / 封顶固定 / LL 合并 / N1–N3（2026-11-06）

依据：`reports/round4_review_12_13.md`（F1、F4、建议新场景 N1–N3）和 `reports/round4_impl_F.md` 第 5 节。只改了 `tools/scorecard.py`，备份为 `backups/scorecard.py.pre-r4B3`（在 B2 之后、本节之前拍的）。顺序按总指挥要求：先修 test_filter（B2）→ 夹具/cap 固定 → LL 合并 → N1–N3。

## 1. 改了什么
- **夹具（F1）**：SR04/05/07/08 的交接文件都加上了 `HMARK`，正文不会再先被防注入挡掉，断言才测得到找文件和判过期的逻辑。
  - SR04 的 Projects 级交接改成新鲜（0.5h）+ 带标记，并补了正向断言：c 自己有带标记的交接时，必须注入 c 的那份，且不能有 Projects 级的那份。
- **封顶日期固定（F4）**：`Env` 新增 `set_cfg()`，沙箱 config 一律写 `cap_from_date="2099-01-01"`。这样 11-21 起 CG04/CG07 不会无故变红；封顶本身改由 CG16 用过去的日期单独测。
  - 验证：把真实封顶日期 11-21 换成过去日期的情况，已经由 CG16 覆盖（`set_cfg(cap_from_date="2000-01-01")`）。
- **Env 基础设施**：
  - `Env.patches`：`script()` 会额外套用这里的替换，供 SR18 做变异用。
  - `sl()` 里加了 `E.script("context_guard")`。原因是实施 D 在 02:46 改了 statusline.sh，改成 `import` 同目录的 `context_guard.lines()`，而沙箱里只有跑过 CG 场景才会放这份文件，于是 import 静默失败，⚠ 永远不显示，SL04 变红（69 条里 68 条过）。
  - 我确认过 SL04 变红不是我的封顶固定造成的：在内存里去掉 `set_cfg` 后 SL04 一样失败。这是沙箱布局问题，补上之后和真实安装一致，没有改任何断言。
- **LL01–LL04 合并**：按 F 报告第 5 节原样并入。`VERSIONS["current"]` 加了 `limit_logger`，`GROUPS` 末尾加了 `limit_logger`，v0 标不适用。
- **N1 → SR18（变异自检，普通场景）**：对 session_resume 做三个变异，每个都在新的子沙箱里跑 SR04/05/07/08，要求至少一条失败；替换目标找不到时本场景 fail，提示变异已过时。
  - 中途 G 在 02:45 改了 `is_project_root` 的函数体（`.git` 是文件也算项目根），原来按函数体匹配的变异失效。现在改成在 `def is_project_root(d):` 后面直接插 `return False`，不依赖函数体怎么写。
- **N2 → CG16 + SL14**：编号避开了 D 预留的 CG10–15、SL07–13。
  - CG16：封顶已生效 + quant-backtest + 宽裕档、190k。期望软提醒、不是强提醒，mode.json 里 soft_line=180000、strong_line=195000。
  - SL14：同一配置、185k，状态栏显示 ⚠、不显示 ⚠⚠。按要求标为"待 D"。
- **N3 → SR19**：compact + 真实格式（加粗段落；红线下面是 `- **…**` 列表；后面还叠了一节旧的 `**下一步**：… OLD-NEXT`）。期望三条红线都在、有最新的下一步、没有 OLD-NEXT、红线排在下一步前面。按要求标为"待 G"。

## 2. 结果（实测）
- `python3 tools/scorecard.py --version all`：**当前版 69/69 通过，不适用 2（SL14 待 D、SR19 待 G）**；v0 为 17/50，不适用 21。
  - 和 B2 的 63/63 比：多出 SR18、CG16、LL01–04 这 6 条可运行场景。分母变了，和历史分数比较时要注意。
- **变异矩阵**（SR04/05/07/08，✗ 表示抓到了）：

  | 变异 | SR04 | SR05 | SR07 | SR08 |
  |---|---|---|---|---|
  | STALE_HOURS = 10**7 | ✓ | ✓ | **✗** | **✗** |
  | 去掉「不爬到 ~」 | ✓ | **✗** | ✓ | ✓ |
  | is_project_root 恒 False | **✗** | ✓ | ✓ | ✓ |

  三个变异都被抓到了，第三个是复核报告里原来测不出的那个。
- **待 D / 待 G 两条的预检**（在内存里去掉 na 后跑）：

  | 场景 | 对当前文件 | 对改前备份 |
  |---|---|---|
  | SL14 | **pass**（D 的 statusline 已在用 `context_guard.lines()`） | `statusline.sh.pre-r4D` + `context_guard.py.pre-r4D`：fail |
  | SR19 | **pass**（G 已改 key_sections） | `session_resume.py.pre-r4G`：fail |

  两条都能区分改前和改后，D、G 宣布完成后去掉 na 即可。
- harness 67 条重跑：0 条不一致（test_filter 在 B2 之后没有再改）。

## 3. 撤回
```
cd ~/Documents/Projects/claude-usage-saver; cp -p backups/scorecard.py.pre-r4B3 tools/scorecard.py; python3 tools/scorecard.py --version all
```
（撤回后 SL04 会因为 D 的 statusline 改动而变红，原因见第 1 节。）

## 4. 疑虑 / 待办
- D、G 都还在改文件。我的 SR18 变异、SL14、SR19 都是对着 02:45–02:46 的版本测的；它们完成后需要再跑一次。
- 尚未合并 `tools/tests_r4D.py` / `tools/tests_r4G.py` 的建议场景（等通知）。其中 D 的 CG14/15 和我的 CG16 都在测封顶，部分重叠；CG16 多测了宽裕档和 mode.json 里写的线。
- `--only` 加 `--version all` 会用部分场景重写 scorecard.md。我中途这样跑过一次，最后一次完整运行已经把它覆盖了。

## 总结（5 行）
1. SR04/05/07/08 夹具加了标记，SR04 补了正向断言；SR18 变异自检证实三个变异都能被抓到。
2. 沙箱里固定 `cap_from_date=2099-01-01`，11-21 起不会再无故变红；封顶改由 CG16 单独测（过去的日期 + 宽裕档 + mode.json）。
3. LL01–04 已并入，全过；顺手修了 D 的 statusline 改动带来的沙箱 import 问题（SL04），没有改断言。
4. scorecard 当前版 69/69 通过，SL14 标待 D、SR19 标待 G；预检显示两条对当前文件都已 pass、对改前备份都 fail。
5. 等 D、G 完成后去掉这两条的 na 并合并 tests_r4D/G。

---

## B4（Codex 打回后，2026-11-06）

依据：`reports/round4_codex_review_B.md` 末尾结论（打回第 1 次）的两个 shell 问题。只改了 `hooks/test_filter.py` 的 `split_segments()`（+31 行），没有动 scorecard.py、settings.json，也没有动其它 hook。SR19 断言漏检那一条不归我（scorecard 不归 B4），没有处理。

### 1. 复现（旧版 = `backups/test_filter.py.pre-r4B4`，实测）
对照脚本：`/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/r4B4/compare.py`。用法：`python3 compare.py <hook>`。
- 用例：Codex 报告里的 30 条（照抄原文），另加两个问题的 26 条变体（V1a–V1l 是 `[[`，V2a–V2n 是 `#`/反斜杠）。
- 每条比较原命令和改写后命令的 `bash -n`，以及三种模式下的执行结果，测试命令用假 pytest 函数（`--rc=N` 控制退出码，`--lines=N` 控制输出行数）：
  - **fallback**（mktemp 失败、原样跑）：rc 一致，stdout 和 stderr 逐字一致；
  - **null**（日志写到 /dev/null）：rc 一致，非测试行按顺序都在；
  - **real**（真实可写日志目录）：rc 一致；剥掉包装加的提示行后，原输出的关键行按顺序都在（≤40 行时连 ARGS 行也要在）。
- 旧版结果：**Codex 30 条里 27 条一致**，不一致的是 26-bracket、27-escaped-comment、28-bracket-and，和 Codex 的数字一样；**变体 26 条里 17 条一致**。新发现的同根问题：
  - `[[` 里含换行或 `&&`/`||`：V1a/f/g/h/j/l 都变成语法错误，rc=2；
  - 转义制表符后的 `#`（V2c）、`\` 续行后紧接 `#`（V2e，bash 里是 `foo#bar`）：被当成注释，后面的 `exit` 被吞掉；
  - `foo \⏎#注释`（V2f）：注释去掉后，段尾留下的 `\` 碰上 `strip()`，被拼成了 `foo \ ;`，即转义空格，输出多了一个空格。

### 2. 改法（`split_segments`）
1. **`#` 只在词首才算注释**：新增状态 `ws`（当前是否在词首）。段首或者未转义的空格/制表符之后为真；任何字符、引号、反斜杠转义（`\x`）之后为假；`\⏎` 续行不改变它（bash 会直接删掉续行）。原来的判断 `buf[-1].isspace()` 看不出这个空格是不是被转义了。
2. **`[[ … ]]` 内不拆段**：逐词跟踪，段的第一个词是 `[[` 时进入条件表达式，遇到词 `]]` 退出。在 `]]` 之前遇到 `;`、`&&`、`||` 或换行，**整条放行**；命令结束时 `[[` 还没闭合也放行。带引号的 `"]]"` 和不在命令位置的 `echo [[` 不算。
3. **段尾有未转义的 `\` 就整条放行**：`strip()` 之后，如果段尾的反斜杠是奇数个，拼回去会改变含义，所以不改写。这一条覆盖了 V2f 这种续行和注释交错的情况。

代价：单行的 `[[ a && b ]] && pytest` 现在也会放行（旧版恰好能正确改写）。这是按"安全优先"取的保守做法，在真实历史命令里出现 0 次（见第 4 节）。

### 3. 对照测试（实测，bash）
| | Codex 30 条 | 变体 26 条 |
|---|---|---|
| 旧版 | 27/30 一致（包装 14） | 17/26 一致（包装 23） |
| **新版** | **30/30 一致**（包装 13） | **26/26 一致**（包装 15） |

包装状态的变化：26、28 和 V1a–V1l 里含 `[[` 的那些改成放行（共 12 条）；V2f 改成放行；22、V2a、V2k（带转义空格加 `#` 参数的测试）现在能正确包装，参数和原命令一致。
补充（实测，zsh 跑同一套用例）：新版 Codex 30/30，变体 25/26。唯一的不同是 V1i（`echo [[`），zsh 的 glob 报错 "bad pattern" 里行号是 8 还是 9；rc 和 stdout 都一致。旧版也有同样的差异，不是这次引入的。

### 4. 真实命令回放（实测，`python3 tools/replay/replay_test_filter.py`）
- 退出码：改前 0，**改后 0**。
- 19,951 条去重命令（22,901 次调用）：改写 **82 条 / 99 次 → 82 条 / 99 次，没有变化**；orig_ok_new_bad 改前改后都是 **0**；zsh_new_bad_while_orig_ok = 0；misfire 5，miss_inscope 1562，都没变。
- 额外逐条比对：新旧两版对这 19,951 条命令的 `rewrite()` 输出 **19,951 条完全相同**，`split_segments()` 的结果也全部相同。也就是说，历史命令里没有一条碰到这两类结构，本次修改对真实流量没有任何影响。

### 5. scorecard（实测，`python3 tools/scorecard.py --version all`）
- 当前版 **107/107 通过（100%），不适用 0**，其中 test_filter 的 24 条 TF 场景全过；v0 是 20/58。分母比 B3 的 69 大，是因为其他实施者之后又加了场景。
- 运行时按惯例重写了 `reports/scorecard.md` 和 `scorecard_history.json` 里今天的条目。
- `tools/tests_r4*.py` 里没有和 test_filter 相关的测试（grep 结果为空）。
- 另外重跑了 B2 的 67 条 harness（复制到 `scratchpad/r4B4/b2h/` 里跑，没有动原目录）：新旧两版分类完全相同，**0 条 RC-MISMATCH**，放行 26 条，和 B2 报告一致。
- 真实 hook 入口（stdin JSON）：多行 `[[` 那条没有输出，即放行；`foo\ #literal` 那条被改写，改写后末尾保留 `echo foo\ #literal ; exit 7`。

### 6. 撤回
```
cd ~/Documents/Projects/claude-usage-saver; cp -p backups/test_filter.py.pre-r4B4 hooks/test_filter.py; python3 tools/replay/replay_test_filter.py; python3 tools/scorecard.py --version all
```

### 7. 疑虑
- 没有给 scorecard 加对应场景（scorecard 不归我）。建议由 scorecard 的负责人把 26/27/28、V1g（`[[` 里出现 `pytest` 这个词）、V2e、V2f 加成 TF25+；用例和断言可以直接照搬 compare.py。
- `[[` 只在段的第一个词时识别。`! [[`、`if [[` 等写法本来就因为保留字整条放行，不受影响；`FOO=1 [[` 在 bash 里本身就不是条件表达式。
- 没处理的同类边角：`$'…'` 里的 `\'`（ANSI-C 引号），这个函数的引号状态机本来就不认识，不在本次范围内，历史命令里也没出现过导致改坏的情况（回放 0 改坏）。
- 对照测试是 bash 实测加 zsh 实测；Claude Code 实际是用 zsh 执行的，zsh 的结果见第 3 节。
