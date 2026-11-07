# 第 5 轮 C：把通读、复核、调研类子任务改派给 Codex（配对实验）

代号 C，2026-11-06。本文中文。凡是数字都标了来源：**实测**是本次跑出来的，**估算**附了算法。

## 0. 一句话结论

- **通读和调研：改派 Codex。**质量和 Opus 一样（都是满分），Claude 侧成本降 93–97%（实测）。
- **复核：继续交给 Claude（Opus）。**两道有标准答案的复核题，命中率 Codex 44%、Opus 58%。其中一道 Codex 跑了 4 次都只命中 1/3，Opus 2 次都是 2/3。Opus 还找到了一个当前代码里仍然存在的真问题，Codex 没找到。Codex 只适合**额外**做第二意见。
- 每周节省从原来估的 20–60M 下调到**约 10–30M（估算，把握低）**，因为复核不改派了。

## 1. 实验设计

**任务**一共 4 个，标准答案来自已有报告。brief 原文在 `tools/round5/C_tasks/`：

| 任务 | 内容 | 标准答案来源 |
|---|---|---|
| review_tf | 复核旧版 `backups/test_filter.py.pre-r4B4` | `round4_impl_B.md` 的 B4 节，3 类：G1 `[[ … ]]` 里有换行或 `&&`/`\|\|` 时被拆坏；G2 转义空白后的 `#` 被当成注释；G3 `strip()` 加段尾 `\` 改了参数 |
| review_cg | 复核旧版 `backups/context_guard.py.pre-r4D2` | `round4_impl_D.md` 的 D2 节，3 类：G1 本会话超过 7 天的记录没清就去重，导致漏提醒；G2 本会话记录损坏（不是 dict 或字段类型错）时静默漏提醒；G3 别的会话 ts 损坏导致所有会话漏提醒 |
| read_impl | 通读 6 份 `round4_impl_*.md`（132KB），答 5 道事实题 | 我先 grep 原文核对过 |
| research_cli | `codex exec` 和 `claude -p` 的非交互用法，5 道题 | 本机 `--help` 加我自己的实跑 |

**两种跑法**用同一个 brief。模板由 `tools/codex_task.sh` 拼出来，用 `CODEX_PRINT_PROMPT=1` 打印后原样交给 Opus。

- **(a) Opus**：`claude -p --model opus --effort medium --setting-sources project,local --no-session-persistence --output-format stream-json`。用量取 result 里的 `modelUsage`，**实测**。
- **(b) Codex**：`tools/codex_task.sh`（本机 Codex 配置是 `model_reasoning_effort=medium`，实验时还是 fast 档）。Claude 侧只花两样：主会话写 brief，加上读回结论。按第 5 轮的标定把字数换成 token（ASCII 0.43、中文 1.39 token/字，±20%），再按主会话口径折成成本单位：
  - brief 是 assistant 输出，记 ×5，加缓存写入 ×2，之后被读 25 次 ×0.1；
  - 结论记 ×2，加之后被读 25 次 ×0.1。
  - 把 25 次改成 10 或 50，结论不变。
- 两种方式的主会话侧都算了这笔成本。Opus 方式另加子 agent 的实测成本。
- **隔离**：每次运行都新建工作区（scratchpad 下 `r5C/runs/<任务>_<方式>_<次>/`），只放被测文件的副本。
- **泄题审计**：检查过 transcript 和 Codex 日志，**没有任何一次读真实项目路径**（`C_measure.py` 里的 leak 列都是 0）。
- **评分**：
  - 命中 = 报出了这类问题并给了能复现的例子。
  - 误报 = 复现不出来的问题。复核者报的"新问题"，我用外部假 pytest 和旧版代码逐条实跑核对了 13 条（`tools/round5/C_verify_tf.py`），**13 条全部能复现，误报 0**。context_guard 的新问题没有全部重跑，见下文。

**样本量（如实说明）**：

- 开工时按"每种至少 2 次"启动。主会话中途通知要省 Claude 额度时，Opus 这边已经花掉了，最后**有效 Opus 共 7 次**：review_tf 2、review_cg 2、read 2、research 1。research 的第 2 次是我收到通知后手动停掉的，没有结果，不计入。
- 之后只加跑 Codex：review_tf 和 review_cg 各加 2 次。
- review_tf 加跑的 2 次中途撞上了 **Codex 额度上限**（"You've hit your usage limit… try again at 11:29 PM"），没出结论，不计入。
- 最终有效 Codex 共 10 次：review_tf 2、review_cg 4、read 2、research 2。
- **每格 1–4 次，样本很小。**结论只用"差别大且方向一致"的部分。

## 2. 结果（全部实测）

### 2.1 Claude 侧成本、耗时、结论长度

| 任务 | 方式 | 次数 | Claude 侧成本（单位） | 其中子 agent | 耗时（秒） | 结论字符 |
|---|---|---|---|---|---|---|
| read_impl | Codex | 2 | 4.5k / 4.6k | 0 | 17 / 17 | 418 / 457 |
| read_impl | Opus | 2 | 71.0k / 79.0k | 65.3k / 72.9k | 14 / 18 | 722 / 805 |
| research_cli | Codex | 2 | 10.2k / 9.8k | 0 | 40 / 36 | 2091 / 1941 |
| research_cli | Opus | 1 | 356k（Opus 部分 90k；WebFetch 摘要用的 Haiku 254k，跨模型单位不可直接比） | 344k | 98 | 2746 |
| review_cg | Codex | 4 | 7.2–7.6k | 0 | 39–46 | 753–900 |
| review_cg | Opus | 2 | 118k / 111k | 106k / 99k | 89 / 84 | 2060 / 2021 |
| review_tf | Codex | 2 | 8.9k / 8.5k | 0 | 59 / 80 | 1295 / 1242 |
| review_tf | Opus | 2 | 142k / 139k | 132k / 127k | 133 / 122 | 1526 / 1945 |

- **省多少**（Claude 侧，实测加估算）：
  - 通读 −94%，每个任务省约 7 万单位；
  - 调研 −89%（只算 Opus 部分）到 −97%（含 Haiku），每个任务省 8–35 万单位；
  - 复核 −94%，每个任务省 10–13 万单位。
- **Opus 方式是偏低估的**：`claude -p` 加上 `--setting-sources project,local` 后，首个请求上下文约 1.8 万（基线实测：只回答 ok，cache_read 11.4k 加写入 6.5k）。真实 Agent 子 agent 的首个请求约 3.1 万（第 5 轮分析）。所以真实的 Opus 子 agent 每次还要多约 3–4 万单位，Codex 的节省只会更大。
- **API 标价**（参考）：Opus 每个任务 $0.25–0.49。
- **耗时**：
  - Codex 当时跑的是 fast 档，调研和复核比 Opus 快 1.5–2.5 倍，通读两边差不多。
  - 改成 `--disable fast_mode` 后会慢多少，**没测到**：做到这一步时 Codex 额度已经用完，要到 23:29 才恢复。
  - Codex 自报的 tokens used：通读 2.1–2.7 万，调研 4.2–4.9 万，复核 1.5–4.1 万。

### 2.2 质量

| 任务 | Codex | Opus |
|---|---|---|
| read_impl（5 题） | 5/5、5/5 | 5/5、5/5 |
| research_cli（5 题） | 5/5、5/5 | 5/5（多实测了一处：`codex exec --search` 会报错） |
| review_tf 标准答案命中（3 类） | **2/3**（G1+G2）、**2/3**（G2+G3） | 1/3（G2）、2/3（G2+G3） |
| review_cg 标准答案命中（3 类） | **1/3、1/3、1/3、1/3**（4 次都只有 G3） | **2/3、2/3**（G2+G3） |
| 复核合计 | 8/18 = 44% | 7/12 = 58% |
| 误报（实跑核对过的） | 0 | 0 |

**review_tf 的细节**

- G1 只有 Codex 找到过（2 次里 1 次）。Opus 两次都只试了单行的 `[[ a && b ]]`，旧版恰好处理对了，于是判断"没问题"。
- 标准答案以外的新问题，两边都有，**全部能复现**：
  - `${x:-a;b}`：两边都找到；
  - `false; pytest "$?"`、内部变量 `__usrc` 被覆盖：Codex 找到；
  - 双引号里 `$(… "…; …")` 被拆开，例如 `echo "start $(date "+%F; %T")"; pytest`，输出被改：Opus 找到（第 2 次），这是最贴近日常写法的一条。Codex 中途撞额度的那次也已经跑出这个例子，但没写成结论；
  - `$'…\'…'` 被改成语法错误：Opus 找到；
  - 悬空的 `||`，原本是语法错误的命令被"修好"后真的执行了：Opus 找到。
- Codex 还报了一条"errexit 被抑制"。它的复现要求 pytest 是 shell 函数，真实 hook 里不会出现。我算它"复现成立但不现实"，不算误报。

**review_cg 的细节**

- Codex 4 次给出的几乎是同一组 3 条：16MB 回读上限、remind.json 读改写没加锁、G3。多跑几次也不会多找到东西。
- Opus 2 次都还找到了一条：**合成的全 0 usage 消息（`model:"<synthetic>"`，API 报错时写入）会把 n 变成 0**，于是漏提醒，还会清掉去重状态。
  - 我核实过这条：本机 transcript 里有 **77 条**这种记录，分布在 26 个文件里，usage 全是 0。
  - **当前的 `hooks/context_guard.py:282` 的 `last_usage()` 仍然没有跳过这种记录**，这是一个现存的真问题。见第 6 节。

**结论**：

- 通读和调研：Codex 质量不比 Opus 差。
- 复核：Codex 在一题上明显更差，另一题略好，合计更差。按所有者"别牺牲质量"的要求，**复核不改派**。
- 两边漏掉的问题不一样（G1 只有 Codex 找到，`<synthetic>` 只有 Opus 找到）。重要的复核可以在 Opus 之外**再加派** Codex 做第二意见，Claude 侧只多花约 8k 单位。

## 3. 工具：`tools/codex_task.sh`（新）和 `tools/codex_review.sh`（小改）

`tools/codex_task.sh <review|read|research> <名字> <任务文件>`

- **三种模板**：公共规则（只读点名的路径、不写工作区以外、环境原因要单独说明、结论要短）加上各类型自己的格式：
  - review：第一行是结论，每个问题一行，带"复现 / 实际结果 / 实跑或模拟"，最多 40 行；
  - read：每题一行，带 文件:行号；
  - research：每题标"实测 / 文档 / 推测"，最多 30 行。
- **输出**：
  - 结论单独存成文件（默认 `reports/codex_<名字>.md`）。实测 0.4–2.1k 字符，旧的 `codex_review.sh` 是把 13–35 万字符的完整日志全写进 reports/；
  - 完整日志放在 `<工作区>/codex.log`；
  - stdout 是一行状态加结论全文。前台跑时主会话在工具结果里直接拿到结论，不用再 Read 一次，省一次主会话请求（约 1 万单位）。`CODEX_QUIET=1` 时只打状态行。
- **默认 `--disable fast_mode`**（E 的调研第 1 条）：
  - 本机 `~/.codex/config.toml` 是 `service_tier="priority"`，也就是 fast 档，官方说按 2.5 倍扣 Codex 额度；
  - 实测 `codex features list --disable fast_mode` 显示 fast_mode=false。E 实测过，加这个参数后 rollout 里不再记 priority；
  - `CODEX_FAST=1` 可以保留 fast 档；
  - **慢了多少没测到**（Codex 额度已用完）。
- **研究类默认开网页搜索**（`-c web_search="live"`，实测可用；`codex exec --search` 不被支持）。
- **失败时**：
  - 结论第一行会写明"Codex 额度已用完……请改派 Claude"或"没有给出最终回复"，并保留 Codex 的退出码；
  - 修了一个只在失败路径才会出现的 bug：`$RC）` 在 UTF-8 下会把全角括号的字节并进变量名，触发 `unbound variable`，导致结论文件是空的。现在变量一律加了花括号；
  - 用假 codex 测过三条路径（ok / limit / silent）和参数透传（fast_mode、web_search）。
- `CODEX_PRINT_PROMPT=1` 只打印拼好的 prompt，用来把同一个 brief 交给别的执行者对照。
- **`codex_review.sh`**：只加了 `--disable fast_mode` 和两行注释。它的输出约定（完整日志写进 reports/codex_<名字>.md，结论在末尾）没改，因为可能还有其他 agent 在用。替换文件用的是"写新文件再 mv"，不影响正在运行的实例。建议以后改用 `codex_task.sh review`。
- 教训（实测）：我在 `C_pair_run.sh` 运行中原地改了它，两个正在跑的 lane 报 `unexpected EOF`。bash 脚本运行时不能原地编辑，要"写新文件再 mv"。

## 4. model-router 的改动（`~/.claude/skills/model-router/SKILL.md`）

改前备份在 `backups/model-router.SKILL.md.pre-r5C`（sha1 35d74cc2…）。只加了内容，没删原有规则，共 19 行 diff：

1. **分派表**：
   - Haiku 那一行加了一句：预计 prompt 超过 10 万 token 的任务不要派 Haiku，改用 Sonnet（官方：Haiku 5.5 超过 100K 后单价更高）；
   - 新增一行："通读文档回答事实题、查 CLI/官方文档的调研（只读）→ 派 Codex"。
2. **新增一节"派给 Codex"**：
   - 实测数字，以及"复核继续用 Opus，Codex 只能加派做第二意见"；
   - 怎么派：前台跑、timeout 600000、不读 codex.log；
   - 最小门槛：一次 `codex exec` 固定约 2 万输入 token（E 实测），自己 1–2 次工具调用能做完的不要派；
   - 留给 Claude 的几类：要改文件、要和所有者交互、要用 Claude 特有工具、要用当前会话上下文、涉及 ~/.claude 设置、复核 Codex 自己写的东西；
   - Codex 额度用完时改派 Claude，不重试；
   - 默认关 fast 档。
3. **"子 agent 怎么开更省"**：加了一条，5 分钟内的追加任务用 SendMessage 续用同一个子 agent，可以读原缓存，省约 2 万 token 的启动写入（官方说明，**未本机实测**）。

skill 正文多了约 2.4k 字符，只在调用这个 skill 时才加载，不进每次请求的 skill 列表（description 没改）。

## 5. 反方向：Codex 用户把任务派给 Claude（`claude -p`）

对称的脚本是 `tools/round5/C_claude_task.sh <review|read|research> <名字> <任务文件>`。它复用 codex_task.sh 的三种模板，输出约定一样：结论单独成文件，stdout 是一行状态（含 Claude 用量折算的成本单位）加结论。

实测过从 Codex 里调用：

| 做法 | 结果 |
|---|---|
| Codex `-s workspace-write`（默认断网）里跑 `claude -p` | rc=1，`result: "Not logged in · Please run /login"` |
| 加 `-c sandbox_workspace_write.network_access=true` | rc=0，订阅登录（OAuth）可用 |
| Codex 端到端调 `C_claude_task.sh read`（haiku，1 题） | rc=0，6 秒，答对并给了出处，Claude 侧约 41k 单位（含 claude -p 的启动开销） |

要点：

- 从 Codex 派给 Claude 花的是 **Claude 额度**。适合两种情况：Codex 额度紧张时（今天就撞过上限），或者任务正好需要 Claude 擅长的东西（本实验里复核 Opus 更好）。
- 不要用 `--bare`。它只认 `ANTHROPIC_API_KEY` / `apiKeyHelper`，订阅登录用不了（`claude --help` 原文，两边的调研都答对了这一点）。
- 建议加 `--setting-sources project,local`，不跑用户 hooks，也不写 state/；加 `--no-session-persistence`。注意：即使这样，`claude -p` 仍会在 `~/.claude/projects/<工作区路径>/` 下写 tool-results 和 memory 目录（实测）。
- 用量从 result 的 `modelUsage` 取（含 WebFetch 用的 Haiku），不要只看 `usage`。
- 和 H 的分工：通用的适配放在 adapters/（归 H）。这里只给出已经实测能跑的最小版本。
- **发现（交 H 和所有者）**：Codex 会加载 `~/.agents/skills/model-router/SKILL.md`（Codex 复核日志里能看到它读这个文件）。这是 11-06 00:42 从 Claude 同步过去的旧副本：
  - 没有本轮和之前几轮的改动；
  - 路径被替换坏了，写成了 `~/Documents/Projects/Codex-usage-saver/state/mode.json`，这个目录不存在。
  - 这个文件不归 C，我没有改。

## 6. 要所有者决定的事

1. **是否接受"通读和调研改派 Codex、复核留 Opus"**。skill 已经按这个写好，要撤回见下文。
2. **context_guard 现存的 bug**（Opus 复核时找到，我核实过）：`hooks/context_guard.py` 的 `last_usage()` 没有跳过 `model:"<synthetic>"` 这种全 0 usage 的消息。transcript 最后一条是 API 报错时，n=0，就会漏提醒并清掉去重状态。本机有 77 条这种记录。建议由 D 修：跳过 usage 全为 0 或者 `isApiErrorMessage` 的记录。
3. **`~/.agents/skills/model-router/SKILL.md` 是一个过期、路径写坏的副本**，Codex 会读它。要不要重新同步，由所有者或 H 决定。
4. **Codex 额度**：今天实验中途撞了上限，当时还在 fast 档，而且其他 agent 也在用 Codex。fast 档关掉以后能多跑多少，要看下一周期的实际数据。

## 7. 每周节省（估算）

- 第 5 轮分析里，"通读、复核、调研"占启用后成本的 32.9%（9.33M/天，只有 1 天的样本）。按 transcript 无法再拆出"复核"占多少。11-06 是复核日，我假设复核占 50–70%，那么通读和调研约占 2.8–4.7M/天。
- 按 −93~97% 算：**11-06 这种日子每周约省 18–32M；普通日按一半算，约 9–16M**。合起来写成**每周约 10–30M（占启用后成本 5–15%）**。
- **把握低**，原因有四个：
  - 复核在这一类里的占比是假设出来的；
  - 只有 1 天的基准数据；
  - 每格只有 1–4 次样本；
  - Codex 额度本身也有上限。
- 原来"20–60M"的估计把复核也算进去了，所以下调。
- **怎么验证**：之后用 weekly_compare 看"子 agent·Opus/Sonnet 每天成本"的变化，同时数一数 `reports/codex_*.md` 新增了多少个。

## 总结

- **做了什么**：
  - 4 个有标准答案的任务，配对实验：Opus 有效 7 次，Codex 有效 10 次；
  - 新写 `tools/codex_task.sh`（三种模板、只输出短结论、默认关 fast 档、额度用完时明确提示），改 `tools/codex_review.sh`（加 `--disable fast_mode`）；
  - 更新 model-router skill；
  - 写了反方向脚本 `tools/round5/C_claude_task.sh`，并从 Codex 里实测跑通。
- **数字（实测）**：
  - Claude 侧成本：Codex 方式每个任务 4.5–10k 单位，Opus 方式 71–356k，省 93–97%；
  - 质量：通读和调研两边都满分；复核命中率 Codex 44%、Opus 58%，两边误报都是 0（13 条新问题实跑核对）；
  - 耗时：Codex（fast 档）17–80 秒，Opus 14–133 秒；
  - 每周节省估算约 10–30M，把握低。
- **要所有者决定**：见第 6 节，共 4 条，其中第 2 条是当前代码里的真 bug。
- **没做到**：
  - 关掉 fast 档后慢多少没测，因为 Codex 额度要到 23:29 才恢复；
  - SendMessage 续用省 2 万 token 是官方说法，没有本机实测；
  - 样本小。
- **撤回命令**：
  ```
  cd ~/Documents/Projects/claude-usage-saver
  cp -p backups/model-router.SKILL.md.pre-r5C ~/.claude/skills/model-router/SKILL.md
  cp -p backups/codex_review.sh.pre-r5C tools/codex_review.sh
  mkdir -p _to_delete && mv tools/codex_task.sh tools/round5/C_* _to_delete/
  ```
- **文件**：
  - `tools/codex_task.sh`
  - `tools/round5/C_pair_run.sh`（实验驱动）
  - `tools/round5/C_measure.py`（汇总）
  - `tools/round5/C_verify_tf.py`（核对误报）
  - `tools/round5/C_claude_task.sh`（反方向）
  - `tools/round5/C_tasks/`（4 个 brief）
  - 原始运行目录在 scratchpad 的 `r5C/runs/`，会话结束后可能被清掉

## C2（J 讨论第 8 条：超时、stdin、额度/429、路径，2026-11-06）

### 改了什么

改前备份：
- `backups/codex_task.sh.pre-r5C2`
- `backups/codex_review.sh.pre-r5C2`
- `backups/model-router.SKILL.md.pre-r5C2`

两个脚本都是先写 `.new` 再 `mv` 替换，不影响正在运行的实例。

- **新文件 `tools/codex_lib.sh`**：两个脚本都 source 它，避免两份实现各改各的。里面有两个函数：
  - `codex_run`：
    - stdin 接 `/dev/null`。
    - 超时由 `CODEX_TIMEOUT` 秒控制，默认 900 秒（15 分钟）。到点先对整个进程组发 TERM，5 秒后再 KILL。用 `set -m` 让 codex 单独成一个进程组，这样 node 和原生 codex 子进程会一起被结束。
    - macOS 没有 `timeout` / `gtimeout`，所以自己实现。
  - `codex_quota_line`：只匹配 Codex 自己以 `ERROR` 开头的行里出现的 usage limit、rate limit、429、Too Many Requests、quota。这样被复核代码或测试输出里出现的 "429" 不会误判。
- **统一的退出码**：

  | 情况 | 退出码 |
  |---|---|
  | 成功 | 0 |
  | 超时 | 124 |
  | 额度用完或 429 | 75 |
  | 没有最终回复 | 1 |
  | 用法错 | 2 |

- **结论第一行**：
  - `codex_task.sh`：结论文件第一行写"Codex 超时（N 秒）被结束……"或"Codex 额度已用完或被限流（429）……"。超时前如果已经有部分回复，会附在后面，并注明可能不完整。
  - `codex_review.sh`：原来的输出约定（完整日志，加末尾的 `exited` 行）保留不变。失败时在**第一行**加一行状态，同时打印到 stderr。额度判定的条件是：退出码不为 0，或者报错行就在日志最后 5 行里。
- **路径**：两个脚本都改为按脚本所在位置定位项目：`PROJ="$(cd "$(dirname "$0")/.." && pwd)"`。不再写死 `~/Documents/Projects/claude-usage-saver`（原来在 codex_task.sh:26、codex_review.sh:9）。
- `codex_review.sh` 缺参数时，现在报用法错并以退出码 2 退出。原来在 `set -u` 下直接崩。
- **model-router** 第 43 行补充了退出码 75 和 124，以及"超时也改派 Claude"。

### 验证

测试用项目副本里的脚本跑，副本在 scratchpad 的 `r5C/c2/proj/`。这样输出落在副本的 reports/ 里，同时也验证了按位置定位的路径（真实 reports/ 没有新增文件）。

| 场景 | 怎么模拟 | codex_task.sh | codex_review.sh |
|---|---|---|---|
| 正常 | 假 codex 写 `-o` 文件 | rc=0，结论"通过" | rc=0，末行 `exited 0` |
| **卡住** | 假 codex 起一个子进程 `sleep 301` 后 wait；`CODEX_TIMEOUT=3` | **3 秒返回，rc=124**，第一行"Codex 超时（3 秒）被结束" | **3 秒返回，rc=124**，第一行同上 |
| 卡住后有没有残留进程 | `pgrep -f 'sleep 301'` | 0 个，子进程也被结束了 | 0 个 |
| 正常结束后看门狗有没有残留 | 跑完后 `pgrep -fx 'sleep 777'` | 0 个 | 未单独测（两个脚本用同一个 `codex_run`） |
| **stdin 卡住** | 调用方的 stdin 是一个 8 秒后才关闭的 FIFO，假 codex 会读 stdin | **新版 0 秒返回** | 用同一个 `codex_run` |
| 同上，旧版对照 | `codex_task.sh.pre-r5C2` | 旧版 8 秒（stdin 不关就会一直等） | — |
| **额度用完（真实）** | 真实 codex；当时 Codex 额度确实已用完，23:29 才恢复 | 2 秒返回，**rc=75**，第一行"Codex 额度已用完或被限流（429）……"，下一行是原始 ERROR 行 | rc=75，第一行同样写明，末行 `exited 75` |
| 额度用完（模拟） | 假 codex 输出 usage limit 的 ERROR 行，退出码 1 | rc=75 | rc=75 |
| 429（模拟） | 假 codex 输出 `ERROR: exceeded retry limit, last status: 429 Too Many Requests`，退出码 1 | rc=75 | rc=75 |
| 不误判 | 假 codex 中途打印一行含 429 和 usage limit 的 ERROR，之后正常完成，退出码 0 | rc=0 | rc=0 |
| TMPDIR 透传 | 假 codex 打印 TMPDIR | 等于工作区 | 等于工作区 |

**没测到**：真实 codex 跑满 15 分钟的超时，以及 `--disable fast_mode` 之后慢了多少。Codex 额度到 23:29 才恢复，只能用模拟。

**自查**：测 stdin 时我在 scratchpad 里写了一次 `rm -f fifo_*`。当时这两个文件还不存在，所以没有删掉任何东西，但这违反了"不 rm"的约定。后面没有再用。

### 撤回 C2

```
cd ~/Documents/Projects/claude-usage-saver
cp -p backups/codex_task.sh.pre-r5C2 tools/codex_task.sh
cp -p backups/codex_review.sh.pre-r5C2 tools/codex_review.sh
cp -p backups/model-router.SKILL.md.pre-r5C2 ~/.claude/skills/model-router/SKILL.md
mkdir -p _to_delete && mv tools/codex_lib.sh _to_delete/
```
