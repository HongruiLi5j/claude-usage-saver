# 第 6 轮 I3：项目根统一判定（Codex 复核 I2 打回，第 2 次打回后的修复）

日期：2026-11-07。范围：Codex `REVIEW_I2.md` 的唯一阻塞项（无 git、用 CLAUDE.md / AGENTS.md / .usage-saver.json 标出的子项目被注入父目录交接），以及所有者 11-07 的新决定（子项目没有交接时只提一行上级交接的路径）。只改了 `hooks/session_resume.py`、`tools/project_profile.py`，追加了 `tools/tests_r6I2.py`，新建了 `tools/tests_r6I3.py`。归 P2 / T 的文件、context_guard、heldout 都没有动。

## 1. 根因

I2 版里，“项目根”有两套判定：
- 画像 `find_root`：先找最近的 .git，再找最近的标记或构建清单。它把 docs-child 判对了。
- hook `_own_root`：只认 .git、构建清单和几个交接文件名，不认 CLAUDE.md / AGENTS.md / .usage-saver.json。另外，`pick_handoff` 在 `under_container` 时会把画像给的根丢掉（原 :402）。

结果是画像说根在 docs-child，hook 却爬到了 group，先拿到了 group/HANDOFF.md。光给 `_own_root` 补上这三种标记仍然是两套规则，以后还会出现别的不一致。所以这次把判定合成**一个函数**。

## 2. 改了什么

### 2.1 唯一来源：`tools/project_profile.py` 的 `root_info(cwd)`
画像（`find_root` / `build` / `get_profile` / `project_thresholds`）和 hook（`find_handoff_ex`）都调用它。hook 里不再有任何根判定规则：`_own_root`、`_MANIFESTS_FALLBACK`、`OWN_HANDOFF` 已删除，原 :402 那行按 `under_container` 丢掉画像根的逻辑也已删除。返回值里有 `root`、`how`、`signals`、`repo`（所在 git 仓库根）、`chain`（从 cwd 往上到边界的目录）。画像的 `_meta` 新增了 `root_signals` 和 `repo` 两个字段。

**规则**（从 cwd 往上，**最近的**有信号的目录就是项目根）：

| 信号 | 种类 | 在哪里算根 |
|---|---|---|
| `.git`（目录或文件） | 硬 | 任何地方 |
| `.usage-saver.json` | 硬 | 任何地方 |
| 自己的交接文件：HANDOFF.md、.claude/handoff.md、HANDOVER.md、交接.md、项目交接.md、交接文档.md、STATUS_FOR_NEXT_ASSISTANT.md、docs/HANDOFF.md、handoff/…；泛称 `进度.md` 要带 usage-saver 标记，或同目录有说明文件/覆盖文件。真实路径必须在本目录内，指出去的符号链接不算 | 硬 | 任何地方 |
| 说明文件：CLAUDE.md、CLAUDE.local.md、.claude/CLAUDE.md、AGENTS.md、AGENTS.override.md、GEMINI.md | 软 | 只在不属于任何 git 仓库时 |
| 构建清单：lakefile、pyproject、package.json、Cargo.toml、go.mod、Makefile 等（沿用 `MANIFESTS`） | 软 | 只在不属于任何 git 仓库时 |

边界：不越过 `~`（~ 单独算），不越过容器下第一层。所有信号都没有时，根是容器下第一层（`container`），否则是 cwd 本身（`cwd`）。

**软信号为什么在 git 仓库内不另立项目**：一个仓库的子目录有自己的 package.json（monorepo 子包），或者有子目录级的 CLAUDE.md，它仍然属于这个仓库。画像要按仓库根识别测试命令，这和改前的“git 优先”一致。I2 已定的“子包没有交接时回到仓库根”也保持不变。硬信号（自己的交接文件、覆盖文件、子模块的 .git）明确表示“这里单独接续”，所以在仓库里也另立项目根。

### 2.2 容器和项目怎么区分（写在 `is_container` 里）
容器是“放项目的目录”，本身不是项目，它下面第一层各算一个项目。容器级的 AGENTS.md / HANDOFF.md 不属于任何一个子项目。
1. **配置的容器**：`$USAGE_SAVER_CONTAINERS`，默认 `~/Documents/Projects`。它永远不当项目根，链在它下面第一层就停。所以 `~/Documents/Projects/AGENTS.md`、`Projects/HANDOFF.md` 不会把各子项目连成一个项目（S01）。cwd 恰为 Projects 时，仍按原规则跨项目找 12 小时内最新的交接（R4G #1）。
2. **隐式容器**（没配置也能认出来，只在 cwd 位于它下面时才判）：目录本身没有 .git、没有构建清单，并且满足下面任一条：
   - 下面至少有 2 个独立的 git 仓库。例如没把 Projects 配置成容器时的 Projects（S02），以及真实的 `~/Documents`（实测判为容器，1.5ms）。
   - 自己只有说明文件（没有交接文件、没有覆盖文件），下面至少有 2 个有信号的子目录。例如 `group/AGENTS.md` 下面有 p1、p2 两个子项目（S03）。

   被判为隐式容器时，cwd 所在的那一个下层目录就是项目根（`how=group`）。
3. **为什么不把“有交接文件的目录”当容器**：交接文件说明这里有一段要接续的工作，是项目，不是容器。Codex 夹具里的 `group/HANDOFF.md` 就是这种情况：cwd=group 时照常用它（S22）；cwd=group/docs-child 时，docs-child 有更近的信号，按“最近的根”规则选子项目。
4. 判容器要列目录：最多看 400 项，凑够 2 个就停。只在两种情况下才判：最近的信号目录在 cwd 之上；或者在找上级提示时，某一级确实有交接文件或要决定是否继续往上。

### 2.3 hook 怎么用这个根（`hooks/session_resume.py`）
`find_handoff_ex` 的查找顺序：
1. 在 cwd 到项目根这一段里，先找主候选名，再找同义名，真实路径必须在根内（R6I2 的越界检查保留）。
2. 如果根在某个 git 仓库的子目录里、自己又没有交接，就到仓库根找（monorepo，R6I2 已定）。来源写“当前目录所在 git 仓库的根目录”。子模块自己有 .git，不会回到上级仓库。
3. 都没有时，执行**所有者新决定**：`parent_handoff` 找上级目录（项目根或仓库根之上、容器和 ~ 之下）最近的一份“自己的交接文件”，**只输出一行**：
   - clear：`[自动接续] 当前项目没有自己的交接文件。上一级目录有交接文件：<路径>（不属于当前项目，未注入内容），需要的话自己读。`
   - compact：`[压缩后提醒] 当前项目没有自己的交接文件。上一级目录有交接文件：<路径>…`
   - 有上一段对话摘要时，摘要照常输出，交接一节只放这一行（C08）。
   - 遇到容器就停，不提示容器级交接（S01、S16、S25）。`~/HANDOFF.md` 永远不提示（S16、S17）。

`pick_handoff(cwd, prof)` 的旧接口保留（返回前两项），新增 `pick_handoff_ex`。`find_handoff` 和 `find_up` 的旧签名也保留。

**降级模式**：hook 旁边没有 `tools/project_profile.py`，只在 scorecard 那种只拷 hook 的沙箱里出现。这时 hook 无从判根，沿用最早的规则：以 .git 或 Projects 第一层为界，不爬到 ~。它不是第二套根判定，只是安装不完整时的保底。SR18 的三个变异锚点（`STALE_HOURS = 72 `、`if d == HOME:  # 不爬到 ~`、`def is_project_root(d):`）都在这条路径上，仍然有效（scorecard 107/107）。S21 测了这个模式：不报错，不越过 Projects。

### 2.4 画像其它改动
- `VERSION` 从 2 改成 3：根的语义变了，旧缓存一次性作废，每个项目下次冷算一次，约 75–160ms。
- 根这一级在 git 仓库的子目录里时，只做硬信号的 stat，省时间。
- `STRONG_MARKERS` 常量保留，没人再用它。

## 3. 上线过程
1. 备份：`backups/session_resume.py.pre-r6I3.20261107-000858`、`backups/project_profile.py.pre-r6I3.20261107-000858`、`backups/tests_r6I2.py.pre-r6I3.20261107-000858`。
2. 在副本 `backups/r6I3_stage/suite` 里修改和测试。这份副本用 rsync 生成，排除了 .git、.secret、sealed、test2、heldout、state、_to_delete；backups 里的单个文件另外拷了进去，scorecard 要用。改前的版本放在 `backups/r6I3_stage/old`。
3. 在副本里，新测试、I2 测试、画像测试、矩阵、scorecard 全部通过，Codex 原复现脚本退出码 0，之后才替换。替换前用 sha256 核对过，原文件从备份到替换期间没被别人改过（`sha_before.txt`）。替换方式是先 cp 成临时名，再 mv 覆盖；替换后逐个 cmp，与副本一致（`sha_after.txt`）。
4. diff：`backups/r6I3_stage/{session_resume,project_profile,tests_r6I2}.r6I3.diff`。

**撤回**（一条命令。tests_r6I3.py 是新建的，要撤的话 mv 到 _to_delete）：
`cd /Users/lihongrui/Documents/Projects/claude-usage-saver; cp -p backups/session_resume.py.pre-r6I3.20261107-000858 hooks/session_resume.py; cp -p backups/project_profile.py.pre-r6I3.20261107-000858 tools/project_profile.py; cp -p backups/tests_r6I2.py.pre-r6I3.20261107-000858 tools/tests_r6I2.py; mkdir -p _to_delete; mv tools/tests_r6I3.py _to_delete/tests_r6I3.py.r6I3`

## 4. 验证【全部实测】

### 4.1 Codex 复现
- 原样脚本 `repro_marker.py`（拷到 `backups/r6I3_stage/`，只改了夹具位置和套件路径）：**改后退出码 0，改前退出码 1**。改后 clear 和 compact 都只出现 `CHILD-OWN`，来源是“当前目录所在项目”。
- 三种标记 × clear/compact 做成了测试：tests_r6I3 C01–C03（共 6 次事件，6/6），tests_r6I2 B14（同样 6 次）。

### 4.2 新测试 `python3 tools/tests_r6I3.py`：**33/33**
- C01–C03：Codex 复现原样，同时核对画像根 = docs-child，以及 found 里有 进度.md。
- C04–C06：同样三种标记，子项目没有交接时，clear 和 compact 都只出一行父目录交接的路径（所有者新决定）。C07：I2 的 B05 形状（只有 lakefile）。C08：提示行和对话摘要同时出现。
- 自造形状 25 个：
  - S01 容器级 AGENTS.md；S02 未配置时 Projects 被识别为隐式容器；S03 只有说明文件的分组目录（1 个子项目不算容器，2 个才算）。
  - S04 三层嵌套，4 个 cwd 都核对 hook 与画像一致；S05 三层嵌套、最里层没有交接，只提示。
  - S06 父目录有 git、子目录只有标记，用仓库的；S07 父目录有 git、子目录有标记加带标记的 进度.md，用子目录的；S08 子目录只有 .usage-saver.json，画像根是子目录、覆盖文件生效，hook 回到仓库根。
  - S09 标记和交接都在父目录。
  - S10 cwd 是符号链接；S11 交接文件链接到父目录，不算本项目的交接，只提示；S12 说明文件是链接，仍标出根。
  - S13 没带标记的 进度.md 不单独立根，带上标记后才立根；S14 git 子模块；S15 monorepo 下 packages/a/src。
  - S16 `~/Documents` 有 2 个 git 仓库，是隐式容器，不注入也不提示；S17 `~/Documents` 不是容器，只提示，并且不提示 ~/HANDOFF.md。
  - S18 quant-backtest 形状；S19 Projects 之外的 Lean 项目。
  - S20 一致性：hook 内 `root_info` 的结果与画像 `_meta.root` 逐个比较，完全相同。
  - S21 降级模式；S22 cwd=group 本身不受影响。
  - S23 仓库子目录有 CLAUDE.md 加没标记的 进度.md，算子项目，只给路径；S24 Codex 夹具里的 进度.md 没带标记；S25 上级提示不越过隐式容器。
- 改前版本跑这份测试：5/33。28 项失败里，9 项（S02、S04、S06–S09、S18–S20）是旧版没有 `pick_handoff_ex` / `root_info`，报 RuntimeError，不能算行为差异；另 19 项是真实的行为差异：C01–C08、S03、S05、S10、S11、S13、S14、S16、S17、S23–S25。其中 C04–C06 和 S16 的改前输出里有父目录或 ~/Documents 的正文。

### 4.3 回归（替换后，在真实位置跑）
```
python3 tools/tests_r6I2.py               → 25/25（原 23 项 + 追加 B14、B15；改前版本 23/25）
python3 profiles/test_project_profile.py  → 33/33
python3 tools/round5/I_adapt_matrix.py    → 格子 21/21（100%）；子检查 329/329
python3 tools/round5/K_testall.py         → 15 个套件，15 个通过、0 个失败/超时；可识别的用例 512/512
   r4D 42/42 · r4F 33/33 · r4G 26/26 · r6I2 25/25 · r6I3 33/33 · r6M 34/34 · r6P 23/23 · r6P2 15/15 · r6T 28/28
   D_tests 47/47 · K_test_doctor 15/15 · K_test_patterns 27/27 · adapters 36/36 · scorecard 107/107 · I_adapt_matrix 21/21
```
在副本里跑时，画像测试 T05、G05 失败过一次，原因是副本缺 backups/ 里的改前文件。把 backups 的单个文件拷进副本后两项都通过；真实位置也通过。

### 4.4 真实目录对比（只读；画像缓存写到临时 state，不写真实 state/）
脚本是 `backups/r6I3_stage/realcmp.py`，用了 **39 个 cwd**：
- `~`、`~/Documents`、Projects，以及 Projects 下各第一层（不含 relay-room）：_管理、ai_layer、claude-records-backup、claude-usage-saver、quant-backtest、regime-statarb、其他。
- `其他/exports`、`其他/lean-axler` 及其子目录 `LeanAxler`。
- claude-usage-saver 的 hooks、tools/round5、adapters、profiles、reports。
- quant-backtest 的 6 个子目录：__pycache__、amibroker、build、dashboard、docs、engines。
- regime-statarb 的 data、docs、reference。这里只 stat，并读它根目录的说明文件，和线上 hook 的行为相同。
- ~/Documents 下的 lean-mc-variance（含 MCVR）、mc-option-pricer（含子目录）、accessiblequant-site、AQ-sandbox-1104、agency-agents、Codex、Lean、insider-exchange、CS61a、Claude outputs。

结果：
- **选中的交接文件：0 处变化**（0/39）。quant-backtest 根目录和 6 个子目录仍然选 `项目交接.md`；claude-usage-saver 的各子目录仍然选它的 `HANDOFF.md`；AQ-sandbox-1104 仍然选它的 `项目交接.md`；cwd 是 ~ 或 Projects 时仍然选最新的那份。
- **画像根（find_root）：0 处变化**（0/39，`backups/r6I3_stage/rootcmp.py`）。
- 上级提示：39 个里 0 个出现提示行。这些真实目录里没有“子项目没有交接、而上级有”的情况。

因为没有变化，没有需要逐个说明的条目。

### 4.5 延迟（子进程整体耗时；改前、改后交替跑两轮；本机实测，绝对值受负载影响）
脚本是 `backups/r6I3_stage/lat.py 30`。每格热缓存 n=60、冷缓存 n=20。

| 场景 | 事件 | 改前 热 / 冷（ms） | 改后 热 / 冷（ms） |
|---|---|---:|---:|
| git 项目 2.2 万文件，cwd=src/d3 | compact | 31.7 / 160.9 | 32.0 / 162.4 |
| 同上 | clear | 34.1 / 164.6 | 35.5 / 165.3 |
| group/docs-child（Codex 夹具） | compact | 31.5 / 33.9 | 32.3 / 34.4 |
| 同上 | clear | 34.4 / 37.0 | 34.8 / 37.3 |
| group/bare-child（只出提示行） | compact | 31.2 / 33.3 | 31.2 / 33.5 |
| 同上 | clear | 34.1 / 36.0 | 34.0 / 36.4 |
| 真实 quant-backtest/engines（只读） | compact | 31.3 / 74.3 | 32.2 / 75.3 |
| 同上 | clear | 35.6 / 79.9 | 36.1 / 79.0 |

热缓存中位数变化在 0 到 +1.4ms 之间，冷缓存在噪声以内。进程内 `find_root` 单次（statusline、context_guard 每次都会调）：quant-backtest/engines 从 0.027 变成 0.105ms，claude-usage-saver/tools/round5 从 0.036 变成 0.171ms。这是在 git 子目录只做硬信号 stat 的优化之后测的，优化前分别是 0.19 和 0.33ms。`is_container(~/Documents)` 1.5ms，只在“没有交接、要找上级提示”时才会走到。

## 5. 限制和取舍（请复核时注意）
1. **git 仓库里的软信号不另立项目**（S06、S15）：子目录只有 CLAUDE.md 或 package.json 时，仍算仓库的一部分。如果所有者希望仓库里带 CLAUDE.md 的子目录也算独立项目，只需改 `root_info` 里一行（`hard_only=below_git`）。交接的选择结果不会变（monorepo 规则会回到仓库根），变的是画像的测试命令识别范围。
2. **没带标记、同目录也没有说明文件的 进度.md 不算项目根**（S13）：这个泛称可能只是一份笔记。这种情况下该目录属于上级项目，按原规则先找上级的 HANDOFF.md。
3. **隐式容器是启发式**：例如“没有 git 的总目录，下面只有说明文件，带两个子项目”会被当成容器。误判的代价是：cwd 在它下面一个没有信号的目录里时，不会拿到上级的交接（它本来也没有交接文件，否则不会被判为容器）。
4. **降级模式**（画像模块文件不存在）沿用最早的规则，不识别 marker 型子项目。真实安装里，画像模块和 hook 在同一个套件里，doctor 会检查。
5. HOME 之外的目录（/private/tmp 等）仍然不找交接（R4G #1，没改）。
6. 测试按“不用 rm”的要求，夹具留在系统临时目录（`$TMPDIR/r6I3-run-*`、`r6I3lat-*`），没有删除。tests_r6I2 原有的 rmtree 只清理它自己的临时沙箱，我没改那部分。

## 额外发现
1. **K_testall 会自动收入 `tools/tests_r6*.py`**：这次新增的 tests_r6I3 不用改登记就进了统一入口（15 个套件）。以后加测试只要按这个命名就行。
2. **画像缓存键只按根算**：根的语义一变（比如加了一个交接文件，使某个子目录成为根），就会换一个新的缓存文件，旧文件留在 state/profiles/ 里不再使用。建议 doctor 定期把 30 天没用的缓存 mv 到 _to_delete。收益是磁盘和整洁，不省 token。
3. **上级提示行省 token**：改前，在 Codex 这类形状里（子项目没有交接、父目录有），clear 会注入父目录约 2–6k 字符的正文，而且是错的；现在只注入约 80 字符。这省的不止是 token：错误接续会让下一轮做错事、再返工，那部分成本更大。真实目录里目前 0 例触发，收益只在出现这种结构时才有。
4. **session_resume 每次启动约 5ms 花在编译主脚本上**（I2 已提）：这次脚本行数基本不变（903→945 行），这个问题仍然存在。

## 总结
- **做了什么**：项目根合成一个函数 `project_profile.root_info`，hook 和画像都调用它。它覆盖 .git、构建清单、CLAUDE.md、AGENTS.md、.usage-saver.json、中英文交接文件名，并区分配置的容器、隐式容器和项目。hook 原来在容器里丢掉画像根的逻辑已删除。按所有者新决定，子项目没有交接时只提一行上级交接的路径。已替换上线。
- **数字**：Codex 复现 6/6 修好，原脚本退出码 0（改前 1）；tests_r6I3 33/33（改前 5/33，其中 9 项是缺函数）；tests_r6I2 25/25；画像测试 33/33；矩阵 21/21（329/329）；K_testall 15/15 套件、512/512；scorecard 107/107。39 个真实 cwd 的选中文件 0 处变化，画像根 0 处变化，quant-backtest 仍然选 项目交接.md。延迟：热缓存 +0–1.4ms，冷缓存持平。
- **要所有者决定的事**：git 仓库里只带 CLAUDE.md 的子目录要不要算独立项目（见第 5 节第 1 条，默认不算，和改前一致）。
- **撤回**：`cd /Users/lihongrui/Documents/Projects/claude-usage-saver; cp -p backups/session_resume.py.pre-r6I3.20261107-000858 hooks/session_resume.py; cp -p backups/project_profile.py.pre-r6I3.20261107-000858 tools/project_profile.py; cp -p backups/tests_r6I2.py.pre-r6I3.20261107-000858 tools/tests_r6I2.py; mkdir -p _to_delete; mv tools/tests_r6I3.py _to_delete/tests_r6I3.py.r6I3`

交付完成
