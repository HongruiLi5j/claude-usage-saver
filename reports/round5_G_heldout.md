# 第 5 轮盲测（G）：新题 65 道 + 旧题 60 道

日期：2026-11-06。负责：G。所有数字都是**实测**：在本机跑真实 hook 子进程，没有用模拟或估算。

## 1. 出题过程

- **出题人**：Codex（`codex exec -s workspace-write`，工作目录在 scratchpad，消耗 55,075 个 Codex token，没用 Claude 额度）。
- **给 Codex 的材料**（只给了这些，副本存在 `tools/heldout/r5_codex_inputs/`）：
  - README.md、使用说明.md、reports/improvements.md、config.json
  - `headers.md`：5 个 hook 和 3 个工具的文件头 docstring，statusline.sh 开头的注释，pro_switch / install_limit_logger 的 `--help` 输出
  - `contract.md`：第 4 轮分诊时确认过的公开接口，包括 rate.json 格式、MARK 字面值、/clear 事件序列（新 session_id 和新 transcript）、各部件按什么方式定位套件目录、Claude Code 官方 hook 输入字段；另外列出 3 个等所有者决定的问题，明确说不要出依赖它们的题
  - 上一轮的 60 题和它们的 README，用来避免出重复的题
- **审计**：我检查了 Codex 的执行记录，它只运行过 `cat TASK.md`、`rg --files docs`、`cat docs/...`、写文件和 `py_compile` 这几类命令。没有读 docs/ 以外的文件，也没有运行任何 hook。
- **交付**：`tools/heldout/heldout_r5.py`（65 题）和 `tools/heldout/README_r5.md`（每道题的依据，其中 9 道标了“推断”：SR11、SR12、SR13、CG03、TF05、TF07、TF08、TF13、TF14）。

| 前缀 | 部件 | 题数 | 和旧 60 题相比，新在哪里 |
|---|---|---|---|
| LL | limit_logger | 9 | 全新部件。对象形式的 error_details、statusline 写 rate.json 后再撞限（组合）、文件残缺尾行、超过 5MB 且 _old 已存在时的轮转、12 个进程并发争同一个超限文件、rate.json 是目录、顶层是数组、USAGE_SAVER_STATE 覆盖 |
| IL | install_limit_logger | 6 | 全新部件。外来同名脚本、带引号的 `$HOME` 路径归属、同一组里混着别人的 hook 时卸载、安装后卸载的备份内容、dry-run 前后整棵目录不变 |
| PS | pro_switch_1121 | 9 | 全新部件。文件 mtime 是 epoch 但里面的记录较新、同一文件新旧记录混合、两次不同的 apply 后 first/last 分别指向哪里、改掉备份 mtime 后查询、幂等 apply 不推进 last、undo 保留无关改动、3 种拒绝条件 |
| SR | session_resume v2 指针 + compact | 14 | MARK 在第 3 个非空行且在代码围栏里、在第 4 行、只是行内片段；加粗列表格式的小节；小节重复出现；超长 Unicode 截断；相对路径的 Edit；.git 是文件；同一 cwd 两个 SessionEnd；拥有者不同的指针；本拥有者的指针与较新的外来指针共存；两个 SessionStart **并发**消费同一指针；旧格式指针；指针 JSON 截断 |
| CG | context_guard 去重 | 5 | 全新行为。同一 session 连续两次过软线、软线到强线再到重复强线、第 10 条边界、5 万 token 增量边界（49,999 / 50,000）、A/B/A 两个 session 交错 |
| SL | statusline 坏输入 + 合并写 | 7 | 顶层是数组、字段类型不对（列表、对象），并检查 state 整个目录不变；无额度数据时保留旧读数；同一窗口取较大值；只更新一个窗口；窗口重置后接受新的低读数 |
| TF | test_filter 复杂 shell | 15 | case、函数、`{ }` 组、time、env、`!`、exec、`bash -c`、进程替换、`&>`、单独 `2>&1`、注释、续行、ANSI-C 引号、三种测试穿插普通输出（实际执行假 pytest/npm/node 并检查退出码） |

## 2. 隔离

- **新题**：每道题单独建临时 HOME，把 hooks/、3 个工具和 config.json 复制到 `<临时HOME>/Documents/Projects/claude-usage-saver/`，只运行这份副本。这样不管某个部件是按 HOME、按脚本位置还是按 `USAGE_SAVER_STATE` 定位，都落在临时目录里，也和生产环境的目录结构一致。第 4 轮 CG13 那种“换了 HOME 就读不到 config”的夹具问题因此不会出现。pro_switch 和 install_limit_logger 每次都显式传临时的 `--settings` 和 `--backup-dir`。
- **旧题**：直接运行仓库里的 hooks/，HOME 换成临时目录，同时设了 `USAGE_SAVER_STATE`。不认这个变量的 test_filter 和 statusline 会写进临时 HOME，所以这次 TF 场景也全量跑了（第 4 轮是跳过的）。
- **没有污染的证据**：跑之前和跑之后各对 state/、backups/、hooks/、tools/、config.json、`~/.claude/settings*.json` 做了 mtime/大小快照。
  - `~/.claude/settings.json` 的 sha1 没变（1464f8e4…）。
  - state/ 下只有 rate.json、mode.json、remind.json 的 mtime 变了。这三个文件是所有者正在运行的会话里真实的 statusline 和守卫写的，内容是真实读数，没有测试标记。
  - state/ 里没有 limit_events.jsonl，last_clear/ 和 testlogs/ 也没变。其余新增文件都是其他 round5 agent 的。

## 3. 结果

| 套件 | 本轮 | 上一轮 | 变化 |
|---|---|---|---|
| **新题 heldout_r5.py** | **65/65（100%）**，连跑 4 次结果相同，每次约 4.3 秒 | —（新题） | — |
| **旧题 heldout_codex_fixed.py** | **57/60（95%）**，60 题全部实跑，连跑 3 次结果相同 | 56/60（其中 27/31 实跑，TF 的 29 题是推演） | +1：SL03（状态栏遇坏 stdin 崩溃）已修好，现在 PASS。TF 29/29 实跑确认，第 4 轮的推演成立 |
| 两套合计 | 122/125（97.6%） | — | — |

新题分类：LL 9/9、IL 6/6、PS 9/9、SR 14/14、CG 5/5、SL 7/7、TF 15/15。

### 新题抓不抓得到回归（验证题目本身有没有效）

全部通过本身说明不了什么，可能只是题目太松。所以我用 `HELDOUT_SUITE` 把同一套题跑在两个对照版本上。

| 对照版本 | 通过 | LL | IL | PS | SR | CG | SL | TF |
|---|---|---|---|---|---|---|---|---|
| 当前版本 | 65/65 | 9/9 | 6/6 | 9/9 | 14/14 | 5/5 | 7/7 | 15/15 |
| 第 4 轮修复前（backups 里最早的 pre-r4* 版本*） | 36/65 | 8/9 | 4/6 | 1/9 | 5/14 | 0/5 | 4/7 | 14/15 |
| 空实现（每个 hook/工具读完 stdin 就 exit 0） | 25/65 | 2/9 | 3/6 | 3/9 | 3/14 | 0/5 | 0/7 | 14/15 |

\* 具体用的是 context_guard.pre-r4D、session_resume.pre-r4G、statusline.pre-r4D、test_filter.pre-r4B，以及 limit_logger / install_limit_logger 的 pre-r4F2 和 pro_switch 的 pre-r4E2。后三个是第 4 轮新写部件的中间版，不是“没有这个部件”。

结论：

- **LL、IL、PS、SR、CG、SL 这几类题有区分力**。第 4 轮之前的版本挂了 28 题，正好落在第 4 轮修过的地方：CG 去重 5/5 题挂、SR 的 v2 指针和并发消费、PS 的 `--scan-context`/`--show-backup`、SL 的坏输入崩溃、LL 的残缺尾行、IL 的混合组卸载。
- **TF 类区分力很弱**。空实现也能过 14/15，因为这批复杂 shell 题按文档的“看不准就放行”，只断言“放行 / 语法有效 / 语义不变”。这些题证明的是“不会弄坏命令”，不能证明“过滤真的在起作用”。只有 TF15 要求必须改写。

## 4. 失败分诊

类别：① 真 bug ② 夹具或题目写错 ③ 文档不清，要所有者决定。

**新题没有失败，所以没有需要分诊的题。**

旧题剩下的 3 条失败和第 4 轮分诊（`reports/round4_triage.md`）完全一样，实现没变，所有者也还没决定：

| 场景 | 类别 | 现状 |
|---|---|---|
| CG11 | ③ | 48 小时前的 95% 周读数（窗口还没重置）仍判为“快用完”，软线 10 万。要所有者定：快用完档要不要加 TTL，并写进文档 |
| CG13 | ③（偏夹具） | 旧题用临时 HOME 直接跑仓库里的 hook，context_guard 按 `$HOME/...` 找 config，找不到，于是静默回退到 16 万。新题因为复制了套件，没有碰到这个问题。要所有者定：套件目录统一怎么定位、读不到 config 时要不要提示 |
| SR06 | ③ | 同一个进程把 cwd 换到项目 B 以后 clear，B 会收到 A 的对话摘要（交接文件不会串）。要所有者定：跨 cwd 时带、不带，还是带上并加提示 |

**真 bug（①）：本轮 0 条。** 上一轮唯一的 ① SL03 已修复，旧题和新题 SL01–03 都验证通过。

## 5. 读这些数字要注意什么

1. **文档写得很细，而且是实现者写的。** 第 4 轮各部件的 docstring 很详细（比如 limit_logger 的轮转步骤、session_resume 的拥有者规则），出题又只依据这些 docstring，所以题目测的是“实现和它自己的文档一致”，测不出“这个行为对所有者有没有用”。100% 应理解为“文档描述的行为都兑现了，包括并发和坏状态”，不代表没有设计缺陷。
2. **contract.md 去掉了夹具靠猜的部分。** 第 4 轮 12 条原始失败里有 10 条是夹具猜错了接口。这次把已确认的接口直接告诉了 Codex，所以没有出现 ② 类失败。代价是 contract.md 里的信息（rate.json 格式、MARK、各部件怎么定位套件目录）原本不在公开文档里，相当于我替文档补了一节。**建议把 contract.md 的前四节并进 README 的“公开接口”部分**（第 4 轮分诊也提过）。
3. **TF 类见上一节**：只证明“安全”，不证明“有效”。
4. **9 道“推断”题全部通过**，说明 Codex 对文档没写死的地方（第 10 条边界、不穿透 env/exec/bash -c、一次性消费）推断的结果和实现一致。但推断本身没有文档背书。如果以后实现改了，这几题应先归为 ③，不要直接当 bug。

## 6. 省额度的量化

盲测是质量保障，**本身不省 token**。成本如下（实测）：

- 出题一次：Codex 55k token，用的是 Codex 额度，不是 Claude 额度。
- 跑一次两套题：新题约 4.3 秒，旧题约 10 秒，都是本地子进程，**0 个 Claude token**。
- 间接作用：以后每轮改 hook 后先跑这 125 题，能在不花 Claude token 的情况下抓到回归。比如第 4 轮修复前的版本在新题上会挂 29 题。

## 7. 跨工具适配（给 H 参考）

- 两套题都是“stdin 灌 JSON → 子进程调 hook → 看 stdout、退出码和文件”的黑盒，不依赖 Claude Code 本身。将来 H 做 Codex CLI / Gemini CLI 适配器时，只要把适配器包成同样的 stdin/stdout 接口，就能直接用 `HELDOUT_SUITE` 指向适配后的副本复用这些题。
- test_filter 的 TF 题只检查改写后的命令文本，和工具无关，任何复用这段命令改写的工具都能拿来用。
- limit_logger、statusline、SessionStart/End 的题绑定 Claude Code 的事件名和字段名，别的工具要靠适配层先把字段映射过来。

## 8. 给下一轮的建议（不针对具体题目）

- **TF**：多出“必须改写，并且真的执行”的题，比如复杂但可以安全改写的结构、多段测试中有一段失败、日志真的落到了 testlogs，弥补第 3 节说的区分力不足。
- **测试对象**：盲测只覆盖 hook 和工具，没覆盖 skills（handoff / model-router）的文字规则。这部分只能靠 F2 那类真实 `claude -p` 对照来测。
- **出题方式**：下一轮可以先让另一个模型（Gemini 或另一轮 Codex）只看 contract.md 加使用说明.md、不看 docstring 来出题，测“所有者视角”的行为，减轻第 5 节第 1 点的偏差。

## 总结

- **做了什么**：让 Codex 只根据文档出了 65 道新盲测题，覆盖第 4 轮新增或改动的 7 类部件，题型包括变异输入、组合、并发、坏状态，执行记录审计确认它没读实现。新旧两套题都在隔离环境里实跑，并用“第 4 轮修复前版本”和“空实现”两个对照验证了题目的区分力。
- **数字（实测）**：
  - 新题 **65/65**，连跑 4 次稳定。
  - 旧题 **57/60**，比上一轮 56/60 多 1 条（SL03 修好）。上一轮的 56 里有 29 条是推演，这次 60 条全部实跑。
  - 对照版本：第 4 轮修复前 36/65，空实现 25/65；TF 类的区分力弱。
- **新发现的真 bug**：0。
- **要所有者决定的事**（都是第 4 轮遗留）：
  1. CG11 快用完档要不要加 TTL；
  2. CG13 套件目录统一怎么定位、读不到 config 时要不要提示；
  3. SR06 同一进程跨 cwd clear 时摘要怎么处理；
  4. 要不要把 `tools/heldout/r5_codex_inputs/contract.md` 的接口说明并进 README。
- **本轮写的文件**：
  - `tools/heldout/heldout_r5.py`、`tools/heldout/README_r5.md`、`tools/heldout/r5_codex_inputs/`（新建）
  - `tools/heldout/README.md`（末尾追加了一节）
  - 本报告
- **撤回命令**：`cp -p backups/heldout_README.md.pre-r5G tools/heldout/README.md; mkdir -p _to_delete; mv tools/heldout/heldout_r5.py tools/heldout/README_r5.md tools/heldout/r5_codex_inputs reports/round5_G_heldout.md _to_delete/`
- **复现命令**：`cd ~/Documents/Projects/claude-usage-saver; python3 tools/heldout/heldout_r5.py; python3 tools/heldout/heldout_codex_fixed.py`
