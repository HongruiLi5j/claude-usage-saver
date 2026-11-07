# 第 6 轮 I2：修 Codex 复核 I 打回的两个交接选取问题（第 1 次打回）

日期：2026-11-06。范围只包括 REVIEW_I.md 里的阻塞项 2（符号链接越界）和阻塞项 3（无 git 子项目取错交接）。阻塞项 1（statusline 并发）归 P，本轮没有碰。

## 1. 改了什么

### 1.1 `hooks/session_resume.py`（真实 hook，已上线）
新增 `inside()`、`_levels()`、`_own_root()`、`find_handoff()`。`find_up()` 保留为旧接口，内部调用 `find_handoff`。`pick_handoff()` 改为一次调用 `find_handoff(主候选名, 同义名, stop)`。

- **阻塞项 2，越界**：每个候选文件先用 `os.path.realpath` 解析真实路径，结果必须落在当前查找范围的根目录（同样取 realpath）之内，否则跳过这个候选，继续按后面的默认名查找。相对路径、符号链接（目录链接或文件链接都算）、绝对路径、`..` 都走这一条规则，没有给它们单独写特例。项目内部的符号链接（例如 `notes -> docs`）不受影响（A08）。
- **阻塞项 3，嵌套子项目**：从 cwd 往上走，停在**最近的项目根**。项目根的判据是：`is_project_root`（.git，或 Projects 下的第一层）、构建清单（沿用画像的 `MANIFESTS`，画像不在时用内置的同一份子集），或者目录里有自己的交接文件。交接文件指主候选名，以及名字里带“交接 / handoff / handover / next_assistant”的同义名，例如 `交接.md`、`项目交接.md`、`HANDOVER.md`。“进度.md”这类泛称不算项目根。在这个范围里先找主候选名，再找同义名，所以 `交接.md` 和 `项目交接.md` 能在同一遍里被认出来，不会先被上一级的 HANDOFF.md 截走。
  - **唯一的例外**：如果最近的项目根位于同一个 git 仓库里（边界目录有 .git），而它自己没有交接文件，就再到仓库根去找。这是 monorepo 子包的情况（B07）。到仓库根这一段同样要做 realpath 边界检查。
  - 父目录没有 git（例如 Codex 夹具里的 `group/`）时，子项目找不到自己的交接就**安全回退**：什么都不注入，不拿父目录的文件（B05、B10）。
- 画像模块不在时，后备路径也执行同样两条规则，用内置的 `ZH_HANDOFF` 同义名（A10、B13）。
- 文件里 SR18 变异自检要用到的两处锚点（`        if d == HOME:          # 不爬到 ~…` 和 `def is_project_root(d):`）都保留了，并且仍然有效：`_own_root` 判 .git 时调用的就是 `is_project_root`。

### 1.2 `tools/project_profile.py`（一处 + 版本号）
Codex 给的阻塞项 2 位置也包括画像。这里只加了一处检查：`handoff_info()` 列 `handoff.found` 时同样做 realpath 边界检查，越界的文件既不读也不列出来。这样 skill 用 `--field handoff.found` 沿用交接写法时，不会被指到兄弟项目（A09）。`VERSION` 从 1 改成 2，让之前缓存里可能已经列出越界文件的画像作废。代价是每个项目下次调用时冷算一次，约 150ms。`_safe_rel()` 没有改。

两份完整 diff 见 `backups/r6I2_stage/session_resume.r6I2.diff`、`backups/r6I2_stage/project_profile.r6I2.diff`。

## 2. 上线过程
1. 先备份（带时间戳）：`backups/session_resume.py.pre-r6I2.20261106-234125`、`backups/project_profile.py.pre-r6I2.20261106-234125`。
2. 在副本里修改和测试，副本放在 `backups/r6I2_stage/suite`（改后）和 `backups/r6I2_stage/old`（改前）。另外做了一份不含 .secret/sealed/test2/heldout/state 的全套件副本，在里面跑完全部回归。副本里出现过两处差异：tests_r4F I10 和画像 T05/G05，原因都是副本不在真实位置、缺少 backups。在真实位置都通过，见第 3 节。这份副本已经 mv 到 `_to_delete/r6I2_full_copy_20261106`。
3. 替换前核对过 sha256：原文件从备份到替换期间没有被别人改过。替换方式是先 cp 成临时名，再 mv 覆盖。
4. 在真实目录上只读对比了改前和改后选中的文件：~、Projects、各第一层项目、其他/*、claude-usage-saver/hooks、tools/round5、quant-backtest/src、~/Documents/lean-mc-variance、mc-option-pricer、~/Documents，共 16 个 cwd，**选中的文件 0 处变化**。relay-room 跳过。quant-backtest 仍然选 `项目交接.md`。【实测】

**撤回**（两条一起执行）：
`cd /Users/lihongrui/Documents/Projects/claude-usage-saver; cp -p backups/session_resume.py.pre-r6I2.20261106-234125 hooks/session_resume.py; cp -p backups/project_profile.py.pre-r6I2.20261106-234125 tools/project_profile.py`

## 3. 验证【全部实测】

### 3.1 新测试 `python3 tools/tests_r6I2.py`：**23/23**（改前版本 6/23）
- A01：Codex 复现 2 原样（`notes -> ../other`，覆盖文件写 `notes/STATUS.md`），clear 和 compact 都不含 OTHER-PROJECT-CANARY。
- A02：同一夹具，本项目另有 HANDOFF.md 时回退到它。
- A03、A04：文件链接，以及默认名所在的 `.claude` 目录链接到兄弟项目，都拒绝。
- A05、A06：`..` 越界的拒绝，`sub/../HANDOFF.md` 这种仍在项目内的接受，并测了端到端。
- A07：绝对路径指向项目外的拒绝，指向项目内的接受。
- A08：项目内部的链接照常注入。
- A09：画像的 found 列表不列越界文件。
- A10：画像模块不在的后备路径。
- B01：Codex 复现 3 原样，clear 和 compact 都只出现 CHILD-OWN。
- B02：更深一层的 cwd。B03：`项目交接.md`。B04：子项目只有 `交接.md`。
- B05：子项目有构建清单但没有交接，父目录无 git，安全回退。
- B06：只有 git 的父项目，cwd 在没有标记的子目录，用父项目的。
- B07：git 仓库里的子包没有交接，到仓库根找。
- B08：git 父项目和子项目各有交接，最近的优先。
- B09：父目录只有 .git，子目录有交接。
- B10：两层嵌套。
- B11、B12：行为不变的情况：cwd 是 group 本身；HANDOFF.md 比 交接.md 优先；git 项目 cwd 在 src/ 时找到 项目交接.md。
- B13：后备路径下的复现 3。

改前版本有 17 项失败：A05–A07 的失败是因为旧版本没有 `find_handoff` 这个函数，其余是真实的错选。

### 3.2 回归（替换后，在真实位置）
```
python3 profiles/test_project_profile.py   → R5I 项目画像测试：33/33 通过
python3 tools/round5/I_adapt_matrix.py     → 格子通过率：21/21（100%）；子检查：329/329（100.0%）
python3 tools/round5/K_testall.py          → 12 个测试套件，12 个通过、0 个失败/超时；可识别的用例 434/434
   tests_r4D 42/42 · tests_r4F 33/33 · tests_r4G 26/26 · tests_r6I2 23/23 · tests_r6M 34/34 · tests_r6P 23/23
   D_tests 47/47 · K_test_doctor 15/15 · K_test_patterns 27/27 · test_adapters 36/36 · scorecard 107/107 · I_adapt_matrix 21/21
```
改动前的基线是 10 个套件、357/388，失败的只有 tests_r6M 3/34。那份是 M 正在改的文件，和本轮无关，后来它自己变成了 34/34。

### 3.3 延迟（子进程整体耗时的中位数，毫秒；改前和改后交替跑两轮；本机实测，绝对值受机器负载影响）
| 场景 | 事件 | 改前 热 / 冷 | 改后 热 / 冷 |
|---|---|---:|---:|
| git 项目，2.2 万文件，cwd=src/d3 | compact | 27.2 / 148.7 | 29.2 / 148.2 |
| 同上 | clear | 29.8 / 152.6 | 30.7 / 151.3 |
| group/lean-child（复现 3 夹具） | compact | 27.6 / 29.0 | 28.4 / 30.5 |
| 同上 | clear | 30.4 / 32.0 | 30.8 / 32.9 |

热缓存每格 n=80，冷缓存每格 n=26（每次用全新的 state）。热缓存慢了 0.4–2.0ms（约 1–7%），冷缓存的差别在噪声以内。拆开来看：`pick_handoff` 在进程内从 0.055ms 变成 0.21ms，多出来的是每一级目录约 30 次 stat；脚本变大了，每次启动编译多约 0.6ms（主脚本不走 pyc 缓存）。没有明显变慢。复现脚本：`python3 backups/r6I2_stage/lat.py 40`。

## 4. 没有做的 / 限制
- 阻塞项 1（statusline 并发丢窗口记录）归 P，本轮没有碰。
- 安全回退（B05）的结果是什么都不注入。父目录虽然有交接文件，但不属于本项目，所以连路径也不给。如果所有者希望至少提示一句“上级目录有一份交接”，需要另外决定。
- 符号链接循环、非 UTF-8 文件名没有新增测试。realpath 遇到链接循环会返回一个不存在的路径，isfile 判为 False，于是跳过这个候选。
- 自检时发现一处失误：在一条整理命令里误带了 `rm -f /dev/null`。普通用户无权删除，命令静默失败，`/dev/null` 完好（已用 ls 确认）。本轮没有删除任何项目文件，临时副本都是 mv 到 `_to_delete/` 的。测试脚本按现有测试的惯例，只清理自己在系统临时目录里建的沙箱。

## 额外发现
1. **session_resume 每次启动约 5ms 花在编译主脚本上**（进程内实测 compile 4.8–5.3ms，整个进程约 25ms）。原因是主脚本不走 pyc 缓存。把主体移到一个被 import 的模块里（pyc 会缓存），入口只留几行，预计每次 SessionStart 省约 4ms。这只省延迟，不省 token。context_guard、statusline 等 Python hook 情况相同，可以一起检查。
2. `find_recent()`（cwd 恰好是 ~ 或 Projects 时的跨项目兜底）没有做 realpath 检查。它本来就是跨项目的，风险低。但如果 Projects 第一层某个项目的 HANDOFF.md 是指向 Projects 之外的链接，也会被选中，可以顺手加上同样的边界检查。
3. 画像缓存的指纹不覆盖“符号链接目标变了”的情况。本轮把 VERSION 改成 2，让旧缓存一次性作废；以后再改 found 规则时，也要记得改版本号。

## 总结
- **做了什么**：session_resume 的交接选取加了 realpath 边界检查，并改为“最近项目根优先”（能认出 交接.md 和 项目交接.md）；画像的 found 列表加了同样的边界检查。已经替换上线。
- **数字**：新测试 23/23（改前 6/23）；回归 33/33、21/21（329/329）、K_testall 12/12 套件 434/434；真实目录 16 个 cwd 的选中文件 0 处变化；热缓存延迟 +0.4–2.0ms，冷缓存持平。
- **要所有者决定的事**：B05 的安全回退要不要提示上级目录有交接（默认不提示）。
- **撤回**：`cd /Users/lihongrui/Documents/Projects/claude-usage-saver; cp -p backups/session_resume.py.pre-r6I2.20261106-234125 hooks/session_resume.py; cp -p backups/project_profile.py.pre-r6I2.20261106-234125 tools/project_profile.py`

交付完成
