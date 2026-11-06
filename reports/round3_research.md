# 第 3 轮：收集新信息（2026-11-06）

读取日期均为 2026-11-06。官方文档页（code.claude.com、support.claude.com）页面本身多数不标日期，只标"Updated this week"，所以日期栏写"读取日"；changelog 和社区文章写它们自己的日期。
标记：【官方】= Anthropic 文档明确写的；【社区】= 个人博客/实测/第三方；【推测】= 我的推断。
已核对但不重复写：round1 D 节和 HANDOFF 第 6 节里已有的设置名（autoCompactWindow、promptSuggestionEnabled、awaySummaryEnabled、effortLevel、additionalContext 1 万字符等）。本文只写新的、或与现有假设冲突的。

---

## 1. 订阅额度怎么算（缓存读取、子 agent、模型权重）

### 1.1 官方明确写的（只有定性，没有公式）
- 5 小时限额：从你发第一条消息开始计时，5 小时后整体重置；每周另有周限额，跨所有模型，账号有固定的周重置时间。Claude 与 Claude Code 共用同一份额度。来源：[support: How do usage and length limits work](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work)、[support: Using Claude Code with your Pro or Max plan](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan)（读取日 11-06）。
- 影响因素【官方】：所用模型、对话长度与复杂度、effort 等级、工具/连接器；"触发自动上下文管理的长对话更耗额度"。
- 缓存【官方，仅 Projects 场景】：[usage limit best practices](https://support.claude.com/en/articles/9797557-usage-limit-best-practices) 写"cached portions count less against your limits than new content"，并写缓存会因闲置过期、休息后第一条消息按全量计。这是网页端 Projects 的说法，没有对 Claude Code 缓存读取给出倍率。
- Claude Code 成本页【官方】：[costs](https://code.claude.com/docs/en/costs) "Why usage climbs in a long session" 写明：每次请求都把完整对话发一遍，缓存读取"按缓存读取价"重读，"所以在开了一整天的会话里问一句话也会按整段对话计用量"；缓存未命中（休息超过缓存寿命）会让整段上下文重新处理；子 agent 的请求"计入同一份额度"。
- 对 Pro/Max 的模型差异：[Claude Code with Pro/Max 文章](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan) 不写 Opus/Sonnet 倍率、不写子 agent、不写缓存。**官方没有公布 Opus 与 Sonnet 的额度权重，也没有公布缓存读取的折算倍率——找不到。**
- 该文还提到 Max/Ultra 有单独的 Fable 模型周限额；Team 文档提到可能出现"Opus limit/Sonnet limit"这类按模型族的提示（[costs](https://code.claude.com/docs/en/costs) 末尾 When a developer asks about a limit）。

### 1.2 社区实测（单人、单账号，必须当作"一个数据点"）
- 来源：Sam Stull，[How many tokens is 1% of your Claude Code limit?](https://dev.to/samstull9007/how-many-tokens-is-1-of-your-claude-code-limit-232n)，2026-10-26，数据期 10-23~25。方法：状态栏的 5 小时百分比 + transcript 的逐次 token，对 61 个观测做无截距最小二乘。
  - 新 token（输入 + 缓存写 + 输出）约 166k 对应 1%（另一天 200k）；缓存读取约 1120 万对应 1%；一个缓存读取约等于 1/67 个新 token。
  - 该账号里新 token 只占总 token 的 2.5%，却占已用额度的约 63%；缓存读取占 97.5% token、约 37% 额度。
  - 子 agent 占总 token 33%、新 token 48%、估计占额度 42%。
  - 作者自己的局限：套餐未写明、没区分模型（Opus/Sonnet 权重混在里面）、缓存写按新 token 算（可能不对）、百分比是整数读数噪声大、Anthropic 随时可改。
- 注意：搜索引擎摘要曾把这个数字错挂到另一篇文章（edtrackai / Stack & Ship，10-30），我逐篇核对后确认那篇**没有**任何实测，且作者自称是自动化账号、"没有订阅所以没测"。这篇不要引用。
- 我们 round1 的"缓存读取占 98%"与这个实测的 97.5% 几乎一致，说明我们本机的 token 构成并不特殊。
- Medium 那篇《98% were cache reads》抓取被 403，内容未读，不引用。
- 其他博客（truefoundry、portkey、faros 等）说"Opus 耗得快几倍"、给出每周小时数，**全是估算且互相矛盾**（Max 5x 的 Opus 小时数有 15–35 与约 40 两种说法），不采信。
- 额度本身的变动【社区，多篇一致但未在官方页核到】：5 小时限额在 2026-05-06 翻倍、Opus 5.5 发布时又上调（说法 20%）。需所有者看 Settings > Usage 的实际读数为准。

### 1.3 对"Sonnet 省 50%""缓存读取占 98%"的真实含义
- 缓存读取 98% 与"98% 的额度"不是一回事。如果社区折算大致成立（【推测】，需本机验证），缓存读取只占额度的三分之一左右，**真正吃额度的是新 token：缓存写入（缓存过期/改前缀/压缩后重建）、输出、以及子 agent 从零建的上下文**。
- 由此，上下文守卫的价值需要重新表述：它减少的是缓存读取（便宜的那部分）；它的另一半价值是避免"大上下文 + 缓存过期 = 一次性整段当新 token 重写"。round1 的"模拟省 41%"是按 token 或 API 价算的，按额度折算后可能偏高（也可能因缓存过期情形而偏低），两个方向都不确定。
- Sonnet：官方只承认"模型"是影响因素，不给倍率。API 单价 2 倍差不能直接搬到订阅额度。**结论不变：节省未知，要本机实测。**
- 子 agent：官方说计入同一额度；社区实测里它占大头。我们本机"子 agent 0.5% token"可能严重低估它的额度占比——因为子 agent 基本都是新 token（独立上下文、默认只有 5 分钟缓存，见 2.4）。

### 1.4 本机可以自己测出权重（建议见第 6 节 R1）
状态栏每 5 秒已经拿到 `rate_limits.five_hour.used_percentage`，又有 transcript 的逐次 usage，具备复现上述回归的全部数据；做法是只记（时间、百分比变化、期间的各类 token 与模型），不改任何行为。

---

## 2. Claude Code 2026 年新增、与省额度/上下文相关的功能（含与现有套件的关系）

版本号以 [changelog](https://code.claude.com/docs/en/changelog) 为准（读取日 11-06，本机当前版本我没查，需所有者 `claude --version` 确认）。

### 2.1 /usage 与缓存可见性【官方】
- `/usage`（订阅版）有"哪些行为占了你的额度"：按 skill/子 agent/插件/MCP 的归因百分比 + 建议；按 `f` 应用可自动执行的建议（v2.1.305+）。11-06 前的版本曾漏报大缓存未命中（v2.1.311 修）。[costs#plan-usage-breakdown](https://code.claude.com/docs/en/costs)。
- 会话块新增 `Prompt cache (main)` 行：请求数、缓存命中占比、未命中次数及原因（如 `likely cause: tool definitions changed`，v2.1.260+）、是否 warm、TTL（v2.1.251+）。状态栏 JSON 里有同样的 `prompt_cache` 对象（[statusline#prompt-cache-fields](https://code.claude.com/docs/en/statusline)）。只统计主会话，不含子 agent。
- `/clear` 后 `/usage` 的会话合计清零（v2.1.211+）。

### 2.2 缓存寿命与 TTL【官方】[prompt-caching](https://code.claude.com/docs/en/prompt-caching)
- 订阅内用量：主会话默认 **1 小时**缓存；"其他一切"（子 agent、workflow、teammate、fork、压缩、会话标题）默认 **5 分钟**，个别服务端控制的辅助请求除外。用了 usage credits 或 API key，主会话降到 5 分钟；一旦超出套餐、转用 usage credits，主会话也降到 5 分钟。
- 设置：`promptCacheTtl`（主会话，"5m"/"1h"）、`subagentPromptCacheTtl`（其余）、环境变量 `CLAUDE_CODE_PROMPT_CACHE_TTL`、`CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`、`ENABLE_PROMPT_CACHING_1H`、`FORCE_PROMPT_CACHING_5M`；子 agent frontmatter `experimental: {cacheTtl: "1h"}`（v2.1.248+）。均需 v2.1.242+。
- 会让缓存失效的动作（官方列表）：切模型（每个模型各有缓存）、改 effort（**Opus 5.5 / Sonnet 5.5 / Haiku 5.5 / Fable 5.1 在订阅下不失效**）、开 fast mode、中途连/断 MCP（工具延迟加载时不影响）、启停带 MCP 的插件、整个工具被 deny、压缩、累积大量图片、升级 Claude Code。不失效的：改 CLAUDE.md（也不生效，直到 /clear、/compact、重启）、切权限模式、切输出风格、调 skill、`/recap`、`/rewind`。
- `opusplan`：每次进出 plan 模式就是一次模型切换，缓存重建（官方明说）。
- 缓存范围：绑定"一台机器 + 一个目录"。不同目录的两个会话互相读不到对方缓存。
- **与现有套件的关系**：我们的"按任务选模型开子 agent"是子 agent 用不同模型——官方说子 agent 本来就不读父缓存，所以不额外损失；但**主会话中途切模型**会整段重读，使用说明里"别中途切模型"是对的，且有官方依据。

### 2.3 压缩相关【官方】
- 自动压缩窗口：`autoCompactWindow`（100000–1000000 或 "auto"）、`/autocompact 200k`、`--autocompact`、环境变量 `CLAUDE_CODE_AUTO_COMPACT_WINDOW`（优先级最高，且设了之后状态栏的 used_percentage 不再指示压缩时机）；按模型的 `modelSettings.<model>.autoCompactWindow`（v2.1.288+）。`autoCompactEnabled`（默认 true，关了则空闲压缩也关）。`DISABLE_COMPACT`（v2.1.311 起写进设置文件只能"关"不能"开"）。来源 [model-config#set-the-auto-compact-window](https://code.claude.com/docs/en/model-config)、[settings-reference](https://code.claude.com/docs/en/settings-reference)。
- 原生 1M 窗口的模型（Opus 4.7+、Sonnet 5+、Haiku 5.5、Fable）默认约 **967K** 才自动压缩。我们当前 settings.json 里**没有**设 autoCompactWindow（HANDOFF 第 6 节说要设 200000 作兜底，但本机文件里没有，也没有 env 块）——也就是说守卫 160k 若被无视，上限是 967K。
- 空闲压缩 `idleCompaction`（默认开；需交互终端 + 自动压缩开 + 1 小时 TTL）：缓存快过期前把长对话摘要掉，下条消息用摘要。changelog：2.1.314（10-30）把它扩到 VS Code/桌面端；2.1.318（11-04）修"用非摘要回复替换了对话"的 bug。触发阈值与时点（约 54 分钟、200K token）只见于社区博客，**官方页没写数字**：[dsebastien 10-16](https://www.dsebastien.net/2026-10-16-claude-code-idle-compaction-cheaper-to-resume-but-from-a-summary/)、[developersdigest](https://www.developersdigest.tech/blog/claude-code-idle-compaction-turn-off-cache-cost-2026)。版本号各处说法不一（2.1.290/2.1.299/2.1.302），不要写死。
- 压缩后保留什么【官方】[context-window](https://code.claude.com/docs/en/context-window)：根 CLAUDE.md、无 paths 的规则、auto memory、skill 描述（v2.1.304+）重新注入；最近改动的最多 5 个文件重读（超 5000 token 的只给路径）；被调用过的 skill 正文每个上限 5000、总 25000 token；**带 `paths:` 的规则和子目录 CLAUDE.md 被摘要掉，需重读触发文件才会再加载**；hook 之前加进去的上下文"和其他对话一起被摘要"；`SessionStart` 且 matcher 为 `compact` 的 hook 会在压缩后重新运行，输出加进压缩后的上下文。
- 压缩本身的代价【官方】：压缩时会发一个单独请求，缓存热时读缓存、很便宜；缓存冷了（长时间闲置后）整段按未缓存输入重算——所以"恢复旧会话后立刻 /compact"最贵。官方建议在任务自然间隙 /compact，想放弃一条路径用 `/rewind`（回到已缓存前缀，不重建）。
- PreCompact hook：stdout 变成"压缩摘要的指令"，exit 2 可阻止；输入含 `trigger`（manual/auto）和 `custom_instructions`。PostCompact 输入含 `compact_summary`。每个 hook 输出上限 1 万字符（PreCompact stdout 例外）。来源 [hooks](https://code.claude.com/docs/en/hooks)、changelog 2.1.314。

### 2.4 子 agent【官方】[sub-agents](https://code.claude.com/docs/en/sub-agents)
- 上下文隔离：每个子 agent 全新上下文，看不到主对话、已读文件、已调用的 skill；只有摘要返回。"子 agent 自己的请求仍然占你的额度"。
- **与 round1 D2 的修正**：内置 Explore 的默认模型是**主会话的模型**（不是 haiku；只有你自定义同名 Explore 并写 `model: haiku` 才是 haiku）。`CLAUDE_CODE_SUBAGENT_MODEL` 只是默认值，定义里的 `model` 或调用时传的 model 优先；要强制全部子 agent 用同一模型需加 `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`（v2.1.257+），此时忽略定义里的 model 字段。只设 FORCE 不设 MODEL 时，内置 Explore 除外。
- 新字段：`autoCompactWindow`（子 agent 级，v2.1.296+，只能调低）、`effort`、`maxTurns`、`memory`（user/project/local 跨会话持久目录）、`isolation: worktree`、`experimental.cacheTtl`。环境变量 `CLAUDE_CODE_SUBAGENT_EFFORT`（v2.1.316，默认继承主会话 effort）；2.1.317：用不同模型的子 agent 改用"该模型自己保存的 effort"而不是主会话的。
- 子 agent 默认 5 分钟缓存（见 2.2）：每个子 agent 的第一请求无缓存可读，且短 TTL 下每次停顿超过 5 分钟就重建。2.1.318：后台子 agent 的 shell 命令 3 分钟后转后台，避免缓存在等命令时过期。
- 子 agent 描述也占上下文：非内置描述合计超 1.5 万 token 启动时会警告。

### 2.5 模型/effort 默认值【官方】[model-config](https://code.claude.com/docs/en/model-config)
- `opus` 现解析为 Opus 5.5（v2.1.280），`sonnet` 为 Sonnet 5.5（v2.1.284），`haiku` 为 Haiku 5.5（v2.1.293）。Pro/Max/Ultra/Team/Enterprise 默认模型都是 Opus 5.5。
- **默认 effort：Opus 5.5 / Sonnet 5.5 / Haiku 5.5 默认 medium**（其余多数为 high）。且文档写："top-level `effortLevel` 对 Opus 5.5 不算数"——要按模型存在 `modelSettings` 里（`/effort` 命令会写）。含义：我们"不动 effortLevel"在 Opus 5.5 上本来就是 medium；以后若想"改 effort"，改 `effortLevel` 对 Opus 5.5 无效。
- 1M 上下文与订阅：文档只给了 Opus 4.6/Sonnet 4.6 的表（**Pro：两者 1M 都要 usage credits**；Max 起 Opus 4.6 含在订阅内）；对 Opus 5.5/Sonnet 5.5 在各套餐是否含 1M、是否更耗额度——**官方页没写，找不到**。changelog 2.1.311 修了"标准上下文会话超过 200K 时报 `Usage credits required for 1M context`"，现在改成自动压缩后继续。【推测】换回 Pro 后，会话可能在 200K 处被压缩或被要求 usage credits，守卫 160k 阈值恰好在它下面，反而是安全的；这点值得所有者 11-21 后实测一次。
- Sonnet 5.5、Opus 5.5、Haiku 5.5、Fable 关不掉思考。

### 2.6 Hook 新能力【官方】[hooks](https://code.claude.com/docs/en/hooks)（共 33 个事件）
- 与省额度直接相关的新事件：`PostToolBatch`（一批工具调用完成后、下次请求前，可注入一次 additionalContext）、`PreModelSwitch`/`PostModelSwitch`（可拦截或提示"切模型会重读约 18 万 token，继续？"，文档示例原文）、`PreCompact`/`PostCompact`、`SessionEnd`、`InstructionsLoaded`、`FileChanged`、`CwdChanged`、`SubagentStart/Stop`。
- **PostToolUse 的 `updatedToolOutput`**：可以替换任何工具（Bash、Read、Grep、MCP）返回给 Claude 的结果；形状必须与该工具的输出一致，否则忽略并用原输出（失败安全）。Bash 形状 `{stdout, stderr, interrupted, isImage}`。注意：工具已跑过，遥测记录原输出，只改变 Claude 看到的内容。
- PreToolUse `updatedInput` 替换整个 input 对象（我们现在用的路线，与官方 costs 页示例同款）。
- SessionStart：输入有 `source`（startup/resume/clear/compact/fork）、`model`、`session_title`；`resume`/`fork` 时多给 `seconds_since_last_response`、`context_tokens`、`prompt_cache_likely_expired`、`estimated_cache_write_usd`（v2.1.251+）。输出可含 `additionalContext`、`sessionTitle`（等同 /rename）、`watchPaths`、`reloadSkills`；`initialUserMessage` 仅 `-p` 模式。`CLAUDE_ENV_FILE` 可持久化环境变量。
- SessionEnd：输入 `reason`（clear/resume/logout/prompt_input_exit/other），**默认超时只有 1.5 秒**（/clear、退出、切会话时），可用 `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` 或 hook 自己的 `timeout` 放宽（v2.1.268 起有效）。我们的 SessionEnd 指针脚本若要读大 transcript，需注意这 1.5 秒。
- additionalContext / systemMessage / initialUserMessage / 纯 stdout 上限各 1 万字符，**没有设置可调高**；超出则存文件、只留路径和前 2000 字预览。（与 round1 一致，这里补充了"超限的真实行为"。）
- 提示/agent 类 hook 并发上限：每会话 24 个（v2.1.312）。

### 2.7 其他小项【官方】
- `bashOutputMaxChars`（v2.1.261+）：Bash 输出内联给 Claude 的上限，**默认 30000 字符**，可设 4000–128000；超出则存文件、给预览+路径。这是原生的"长输出截断"。设置它后 `BASH_MAX_OUTPUT_LENGTH` 被忽略。
- 空闲时的后台额度消耗：`/loop` 等定时任务、跨会话消息（`crossSessionInbound: "hold"`）、goal 检查（`CLAUDE_CODE_GOAL_CHECKIN_MINUTES=0`，每个目标最多 3 次空闲检查）。`/usage` 有"Loops"行。
- `/insights`：分析最近最多 200 个会话、写 `~/.claude/usage-data/report.html`；本身耗额度。
- `autoContinueAtUsageLimit`：用完 5 小时限额后在原会话等待并自动续（v2.1.234+）。Pro 的"收尾额度"（wrap-up allowance）每个周期仅一次，计入周限额（support 文，逐步放量）。
- `cleanupPeriodDays` 默认 30 天删 transcript——**我们的周报/回放脚本依赖 transcript，超过 30 天的数据会丢**（【推测】需所有者决定是否调大，以便做 4–6 周的 ABAB 对比）。
- Read 工具的"文件未变更"占位已在 2.1.317 去掉：再读未改动文件会返回全文（更费上下文）。无设置可关，需靠 CLAUDE.md/skill 提示"别重复读"。
- Agent teams 约 7 倍 token（官方）；需环境变量开启，默认关。

---

## 3. 社区同类工具：它们有什么我们没有、踩过什么坑
全部为【社区】，节省数字全是作者自报，我没找到任何独立基准。

| 工具 | 做法 | 我们没有的点子 | 已知坑 |
|---|---|---|---|
| [ccusage](https://github.com/ryoppippi/ccusage)（读本地 JSONL，MIT） | `blocks` 按 5 小时窗口聚合、`--live` 看实时进度和预测、`statusline`（beta） | 5 小时窗口视角（我们周报是按天）；按窗口预测"何时用完" | 它只用 token×价格估成本；按上面 1.2，这和订阅额度不是一回事（【推测】）。本次搜索未找到它的专项踩坑记录 |
| [rtk (rtk-ai/rtk)](https://github.com/rtk-ai/rtk) | 单个 Rust 二进制，hook 把 `git status` 改写成 `rtk git status`，按命令类型做专用压缩，号称 60–90% | 针对 git/ls/cargo/npm 等**各命令专用**的压缩（我们只做测试输出）；`discover` 扫历史找可省命令 | 仅 Bash，Read/Grep/Glob 绕过它；有压缩有损导致静默变差的评价；与别的 PreToolUse hook 顺序冲突（[#969](https://github.com/rtk-ai/rtk/issues/969)：别的 hook 先返回非空响应时 rtk 的 updatedInput 不生效，建议 rtk 放最后）；[#789](https://github.com/rtk-ai/rtk/issues/789)：discover 按原命令文本匹配，hook 开启后会虚报"漏省" |
| [context-mode](https://glama.ai/mcp/servers/mksglu/claude-context-mode) | 大输出写沙箱、只回摘要（MCP） | 把大工具输出隔离在上下文外 | 自报 58.9KB→1.1KB 仅单例；MCP 自身的工具定义也占上下文 |
| [claude-code-context-handoff](https://github.com/who96/claude-code-context-handoff) | PreCompact 和 SessionEnd(clear) 写交接，SessionStart 恢复 | **在 PreCompact 里先存一份**（压缩有损，先落盘）；覆盖 compact 而不只是 clear | 不改自动压缩时机，只保住压缩前后的信息 |
| [claude-remember](https://github.com/Digital-Process-Tools/claude-remember) | SessionStart 注入记忆、/remember 写交接；用嵌套的 `claude -p` 生成 | 无 | 生成交接本身要花一次 `claude -p`；hook 以完整 shell 权限运行；hook 只在会话启动时读取，改后要重启 |
| [claude-mem](https://github.com/thedotmack/claude-mem) | 记录工具使用观察、AI 压缩后注入未来会话 | 跨会话记忆自动化 | 注入内容本身占上下文；它自己的后台压缩也耗额度（【推测】，未找到量化） |
| Obsidian+Graphify 等记忆方案（[例](https://github.com/lucasrosati/claude-code-memory-setup)） | 知识图谱、少读文件 | — | 作者自己标注节省数是"未验证的载荷估算"，不是会话实测 |

对我们的共同启示：(1) 多数同类工具的节省数字是估算，我们坚持"模拟与实测分开"是对的；(2) 输出过滤类工具最常见的坑是**静默失败/有损**和**多个 PreToolUse hook 互相覆盖**——我们现在只有一个 Bash hook，但 henchmen 插件若也注册 PreToolUse，顺序问题要留意（我没读其配置）；(3) 记忆/交接类的坑是"注入内容过时"和"生成成本"，我们的 session_resume 已有过期标记，方向一致。

---

## 4. "省额度但伤质量"的反面证据与缓解

### 官方
- 压缩有损、且会丢子目录 CLAUDE.md/`paths:` 规则（见 2.3）；被调用 skill 正文有 5000/25000 token 上限，超出截断（保留开头，所以 SKILL.md 把关键指令放最前面）。
- 压缩后只重读最近 5 个文件，超 5000 token 的只给路径——压缩后常需要重新读，等于一部分"重复探索"。
- Anthropic 工程博客《Effective context engineering for AI agents》主张"用最小的高信噪比 token 集合"、结构化笔记（把状态写到上下文外再读回）和子 agent 隔离——**这三点正好是我们交接文件 + 子 agent 的依据**。但我只拿到该文的二手转述，原文链接 [anthropic.com/engineering/effective-context-engineering-for-ai-agents](https://anthropic.com/engineering/effective-context-engineering-for-ai-agents) 没能全文读取，措辞未核。
- 官方对 /clear 的态度：换无关任务用 /clear，"免费"；但同时官方也承认"/clear 后上下文要重建"，没有给出重复探索成本的数字。

### 社区（均为个案博客，无对照实验）
- 自动压缩在 80% 处触发、重构到一半后 Claude 回头去改一小时前已完成的文件（[Stop Claude Code from Lobotomizing Itself](https://ianlpaterson.com/blog/stop-claude-code-from-lobotomizing-itself-mid-task/)，日期未核）。
- 压缩时机之争：有人主张 60% 就压（摘要质量受当时上下文质量影响），有人主张在里程碑处压（[stacknotice](https://stacknotice.com/blog/claude-code-compact-clear-context-2026)、[nathanonn](https://www.nathanonn.com/claude-code-never-auto-compact/)）。
- 输出过滤会隐藏警告：Stack & Ship 文也承认"测试过滤会把通过项和警告藏掉"（该文不可信度高，但这一点与我们 round1 的 A-6 一致）。
- **没有找到**任何对比"压缩后质量 vs /clear+交接后质量"的对照实验，也没有量化"/clear 后重复探索的 token 成本"。→ 这是本套件自己该测的空白（见 R7）。

### 缓解办法（官方支持的）
1. 压缩前先落盘：PreCompact hook 提醒/生成交接；PreCompact stdout 可当压缩指令（"保留：所有者决定、红线、文件路径、未完成步骤"）。
2. 压缩后恢复：`SessionStart` matcher=`compact` 注入交接（官方 hooks-guide "re-inject context after compaction"）。
3. 需要在压缩后仍然生效的规则放根 CLAUDE.md 或去掉 `paths:`。
4. `/compact focus on …` 或根 CLAUDE.md 的 `# Compact instructions`（round1 已有）。
5. 放弃一条路径用 `/rewind` 而不是 /compact（回到已缓存前缀，不重建缓存）。
6. 不要把守卫阈值压得太低：每次 /clear 都要付一次"首条消息重建缓存 + 重新探索"，阈值太低的净收益可能为负（【推测】，守卫模拟里没有算这项，round1 B 也指出了）。

---

## 5. 与现有假设冲突、需要更正的点（汇总）
1. "Explore 用 haiku / `CLAUDE_CODE_SUBAGENT_MODEL` 不影响内置 Explore"（round1 D2）：官方现在写 Explore 默认**继承主会话模型**，强制统一需 `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`。
2. "缓存读取占 98%"= 额度大头：社区唯一实测提示缓存读取在额度里权重极低，额度大头是新 token。官方只说"缓存命中更便宜"，没给倍率。
3. "子 agent 只占 0.5%"：按 token 是，按额度可能不是（社区实测 42%）；且子 agent 默认只有 5 分钟缓存。
4. "不动 effortLevel"：在 Opus 5.5 上 `effortLevel` 本来无效，默认已经是 medium。
5. "autoCompactWindow 200000 兜底"：HANDOFF 写了要做，但本机 settings.json 里没有，仍是 967K 才压缩。
6. 套件只在 `clear` 触发 SessionStart；官方还有 `compact`、`resume`、`fork`，且 `resume` 带 cache 过期信息。
7. 使用说明里"别中途切模型"有了官方依据（每个模型各有缓存；PreModelSwitch hook 可直接提示）。

---

## 6. 对本套件的建议

格式：建议 / 预期影响 / 谁决定 / 与现有做法是否冲突。预期影响里凡无实测的都已标【推测】。

**R1 记录"额度读数 × token"，自己回归出权重（只加记录，不改行为）**
- 改什么：statusline.sh（或新的只读脚本）在 five_hour 百分比变化时，向 `state/` 追加一行（时间、百分比、窗口 resets_at、该时间段内各类 token 与模型，从 transcript 汇总）。几周后用 `tools/` 里一个小脚本做无截距回归，得到"每百万新 token / 缓存读 / 缓存写 / 输出 各占几 %"，并分模型。
- 影响：把第 1 节所有"未知"变成本机数据；决定守卫、Sonnet、子 agent 哪个是真杠杆。成本≈0。
- 谁决定：可以直接做（只读、只追加；但要给 statusline 加一次写文件，注意不要再引入 A-1 的覆盖/并发问题——按 session_id 分文件追加）。
- 冲突：无。样本需要几天，且百分比是整数、噪声大，别过度解读。

**R2 weekly_compare 增加"额度等价单位"的敏感性视图**
- 改什么：除现有 API 价单位外，再给一列：新 token×1 + 缓存读×(1/67) + 输出×(待定)，标注"社区单人估计，仅作敏感性分析"；R1 有本机权重后改用本机的。
- 影响：让"子 agent、缓存写、输出"在周报里不再被 98% 的缓存读取淹没；能暴露我们本机子 agent 的真实额度占比。
- 谁决定：可以直接做；报告里措辞需如实（不要把它当结论）。
- 冲突：与 round2 BD 刚落实的"按 API 价权重"并存，不冲突；只是多一列。

**R3 SessionStart 增加 `compact` 匹配（压缩后接续）**
- 改什么：settings.json 的 SessionStart 再加 `{"matcher":"compact", …}`（或把现有 matcher 改成 `clear|compact`），session_resume 在 source=compact 时只注入"交接文件的短指针 + 红线摘要"（别重复注入整段，压缩后本来就有摘要）；注入上限仍 ≤1 万字符。
- 影响：缓解"压缩丢决定/红线"；【推测】对质量有帮助、额度基本不变（几百到两三千 token）。
- 谁决定：改 settings.json 属全局设置，建议告知所有者后由我执行（遵守现有"先备份、给撤回命令"）；逻辑改动可直接做。
- 冲突：与现有"仅 clear 触发"不冲突，是补充；要测 source=compact 时不会重复注入已存在的内容。

**R4 PreCompact hook：压缩前落盘 + 给压缩指令**
- 改什么：新 hook，trigger=auto/manual 时输出 stdout 压缩指令（"保留：所有者原话要求、红线、改过的文件、下一步"），并把当前摘要所需信息写到 state/。**不要用 exit 2 阻止压缩**（阻止主动压缩会让会话继续膨胀；官方说明阻止在"恢复性压缩"时效果不同）。
- 影响：降低压缩有损的质量风险；成本极小。【推测】
- 谁决定：需所有者决定（新增一个全局 hook，且改变压缩摘要内容）。
- 冲突：与根 CLAUDE.md 的 `# Compact instructions`（round1 提议）功能重叠，二选一或合并；所有者 CLAUDE.md 改动此前"未经同意不改"。

**R5 resume 时的缓存过期提醒**
- 改什么：SessionStart(matcher=resume) 读 `prompt_cache_likely_expired`、`context_tokens`、`estimated_cache_write_usd`；若缓存已过期且 context_tokens 很大（如 >15 万），注入一句"恢复这个会话会按全量重写缓存约 N 万 token；若只是接着做，建议 /clear 后用交接继续"。另：UserPromptSubmit 里的 context_guard 可根据上条 assistant 时间戳判断"缓存已冷"并在大上下文时提醒（现在只看 token 数）。
- 影响：针对第 1 节推测的"最贵事件"（大上下文 + 缓存冷 = 整段按新 token 计）。【推测】缓存冷的次数在本机数据里有多少可以先从 transcript 的 cache_creation 尖峰统计，再决定值不值。
- 谁决定：先做统计（可直接做）；是否加提醒需所有者决定（会多一条提示打扰）。
- 冲突：与 idleCompaction 有交叠（它在约 54 分钟时自动摘要，使恢复变便宜但变成"从摘要继续"）；两者不冲突，但提醒文字要区分。

**R6 settings.json 候选项（均为官方设置名）**
- `autoCompactWindow: 200000`：兜底，防止守卫被忽略时跑到 967K。影响：对"最贵会话平均 30–47 万"的尾部封顶；代价是触发有损压缩。【所有者决定：HANDOFF 里早已列入计划但至今没写进，且会带来压缩质量问题，需明确同意。】与守卫 160k 不冲突（守卫先触发）。
- `bashOutputMaxChars`: 例如 12000（范围 4000–128000，默认 30000）。影响：所有 Bash 长输出（不止测试）原生截断，超出存文件给路径；与 test_filter 互补。风险：Claude 需要全文时多一次读文件。【可直接做的是先观察 `/context` 里 Bash 输出占比；改默认值建议所有者决定。】
- `subagentPromptCacheTtl`: 保持默认。把子 agent 缓存改 1 小时会让缓存写入变贵（API 价 2×），对订阅额度的影响未知；**不建议在 R1 数据出来前动**。
- `cleanupPeriodDays`: 调到 ≥60，保证 4–6 周 ABAB 对比与周报的 transcript 不被 30 天清理。所有者决定（涉及保留对话记录，隐私/磁盘）。
- `CLAUDE_CODE_SUBAGENT_EFFORT`（v2.1.316）：默认子 agent 继承主会话 effort；若 R1/周报显示子 agent 占额度大，可单独给子 agent 调低。先不动。
- `idleCompaction`：round1 决定保持默认；新证据（官方 changelog 11-04 修过一次"摘要不是摘要"的 bug）不足以改变决定。保持，列入每周观察。

**R7 补上"质量代价"的测量（项目内部测试，不用等外部基准）**
- 改什么：在 improvements.md 的指标里增加"/clear 后前 5 回合重读的文件数/token"（round1 B 已建议），并从历史 transcript 里统计：守卫触发 → 下一段会话的重复 Read 次数。官方称压缩后只重读 5 个文件，可作对照。
- 影响：给"阈值设多低"提供数据；目前没有任何外部对照实验可借（见第 4 节）。
- 谁决定：可以直接做（只读统计）。
- 冲突：无。

**R8 评估 PostToolUse `updatedToolOutput` 作为 test_filter 的另一条路线（先做原型，不替换）**
- 改什么：新写一个并行原型：PostToolUse 对 Bash 的 stdout 做"保留失败/报错行 + 头尾"，而不是 PreToolUse 改写命令。优点：不改命令语法，所以 A-2/A-3（注释、`&`、`pytest` 出现在非命令位置）这类 bug 整类消失，退出码也不受影响；形状不符时自动用原输出（失败安全）；还可扩展到 Read/Grep/MCP 的大结果。缺点：Claude 看到的是截断结果，但完整输出已丢失（可以先存到 testlogs/ 并给路径）；需要确认 `tool_response` 的形状与 stdout/stderr 字段，以及是否对所有 Bash 调用都能稳定替换。
- 影响：【推测】可靠性提升、覆盖面更大；节省量需实测。
- 谁决定：原型可以直接做；是否替换线上 hook 由所有者在测试对比后决定（涉及 round2 刚修好的 test_filter）。
- 冲突：与现有 PreToolUse 路线是替代关系；两者同时开会重复过滤，原型阶段只在隔离环境里跑。

**R9 henchmen 等其他 PreToolUse hook 的顺序检查**
- 改什么：查看已启用插件 `henchmen@henchmen` 是否也注册了 Bash PreToolUse；若有，确认不会像 rtk #969 那样互相覆盖 `updatedInput`。我没有读它的配置，所以这是待核对项，不是结论。
- 谁决定：可以直接做（只读）。冲突：无。

**R10 使用说明/报告措辞更新（如实化延续）**
- 改什么：使用说明里凡涉及"缓存读取占 98% 所以…"的因果，改写成"98% 是 token 构成，对额度的占比未知，社区单人实测提示缓存读取权重很低"；加一句"换成 Pro 后，超过约 20 万上下文的会话行为可能与 Max 不同，请所有者 11-21 后看一次 /usage 和压缩提示"。
- 谁决定：可以直接做，属事实更正。冲突：无。

**R11 11-21 换 Pro 的专项核对（所有者动作）**
- Pro 的默认模型仍是 Opus 5.5（官方 model-config）；Pro 上 1M 上下文在 4.6 系列需 usage credits，Opus 5.5/Sonnet 5.5 的 1M 是否含在订阅内**官方页未写**。建议 11-21 当天：开一个会话把上下文推过 20 万，观察是否被自动压缩或提示 usage credits；并在 Pro 上重新看 `/usage` 的"What's contributing"。
- 谁决定：所有者执行；套件侧只需保证守卫阈值 ≤ 20 万（现 16 万，满足）。注意 per_project 阈值里 quant-backtest 等设为 25 万，换 Pro 后可能越过 20 万——**这条与现有配置冲突**，建议 11-21 后复查（需所有者决定是否下调）。

---

## 7. 未找到 / 未能确认的清单
- Anthropic 官方公布的订阅额度折算公式、缓存读取倍率、Opus/Sonnet 权重：找不到。
- 官方对 Opus 5.5 / Sonnet 5.5 在 Pro 上 1M 上下文是否含在订阅内、是否更耗额度：model-config 页未写。
- ccusage 的专项踩坑、独立的 token-saver 对照基准：未找到。
- 压缩 vs /clear+交接的质量对照实验；/clear 后重复探索的量化成本：未找到。
- Medium《98% were cache reads》全文（403）、Anthropic 工程博客全文：未读到。
- 空闲压缩的精确触发阈值：官方页无数字，只有社区博客，且版本号互相矛盾。
