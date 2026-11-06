# 第 4 轮：收集新信息（2026-11-06）

读取日期：2026-11-06。本机 `claude --version` = **2.1.318**。官方 changelog 最新一条也是 2.1.318（11-04），**本机版本之后没有新的更新说明**。
本文只写 round3_research.md / round3_plan.md 里没有的内容。round3 已写过的（promptCacheTtl、subagentPromptCacheTtl、SessionStart 的 resume 字段、PostToolUse updatedToolOutput、bashOutputMaxChars、CLAUDE_CODE_SUBAGENT_MODEL_FORCE、子 agent autoCompactWindow/effort/memory、PreModelSwitch 等）不再重复。
标记：**已核实** = 我在官方文档原文里读到了这个名字和说法；**待核实** = 只有间接证据、社区说法，或文档没写到我关心的那一点。
本机只读检查：~/.claude/settings.json（只读，没有修改）、skills/agents 的 frontmatter、最近 14 天子 agent 的 transcript（统计脚本放在 scratchpad，用 `python3 -I` 运行）。

**本机状态更新（和 round3_plan 不同的地方）**：settings.json 里已经有 `autoCompactWindow: 300000`、`cleanupPeriodDays: 90`，SessionEnd hook 也已经装上，所以 D2、D6、D1 都已落实。settings.json 里**没有** `modelSettings`（见 E3，这一点很重要）。

---

## A. 换回 Pro 后的 20 万上下文（最直接相关）

### A1. `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`：把 1M 模型固定在 20 万窗口
- 来源：[model-config#turn-off-1m-context](https://code.claude.com/docs/en/model-config)、[env-vars](https://code.claude.com/docs/en/env-vars)、[errors#the-200k-limit-isnt-enforced](https://code.claude.com/docs/en/errors)
- 名字和行为：**已核实**。对原生 1M 的模型（Opus 4.7 及以后、Sonnet 5 及以后、Fable）"按 200K 窗口对待"：自动压缩开着时，会话在 200K 边界压缩；`autoCompactWindow` 设得比 200K 大也不能突破，因为窗口上限就是模型的上下文窗口。自动压缩关着时，会话停在 200K 并报 context-limit 错误。
- 相关的官方说法（**已核实**，errors#usage-credits-required-for-1m-context）：如果会话"因为上下文超过 200K"触发 `Usage credits required for 1M context`，Claude Code 会**自动压缩回标准上限，之后整个会话都停在这个上限**。这只是一次权限检查，不消耗额度。
- **仍然待核实**：Pro 上 Opus 5.5 / Sonnet 5.5 默认是不是"标准上下文"。官方的套餐表只列了 4.6 系列（Pro 上两者的 1M 都要 usage credits）。所以 11-21 之后可能有两种情况：一种是自动在 200K 处压缩（体验和这个开关一样），另一种是照样 1M（这时这个开关就是主动把窗口降下来）。
- 本套件怎么用：11-21 换 Pro 之后，在 settings.json 的 `env` 块里加 `"CLAUDE_CODE_DISABLE_1M_CONTEXT": "1"`，这样不管服务端怎么处理，行为都是确定的；状态栏的 `context_window.context_window_size` 会变成 200000，守卫的百分比读数也就有了意义。现在的 `autoCompactWindow: 300000` 届时会被自动压到 200K，不用改。
- 收益：把"Pro 上到底是 200K 还是 1M"这个不确定性直接消掉，防止大上下文在 Pro 上把 5 小时额度一次吃光。【推测】按 round3 的 V12，本机 36% 的请求在 20 万以上，换 Pro 之后这部分会全部变成"压缩后继续"。
- 风险：压缩更频繁（有损）。11-21 之前**不要开**，Max 上会白白损失长上下文。henchmen 会删掉 agent 进程里的 `CLAUDE_CODE_*` 环境变量（round3_plan 1.2），settings.json `env` 块里的值能不能传到 henchmen agent 里：**待核实**。保险起见，同时保留 `autoCompactWindow` 这个 settings 键。

### A2. 空闲压缩在 20 万窗口上默认**永远不会触发**
- 来源：[settings-reference#idlecompaction](https://code.claude.com/docs/en/settings-reference)（"Change the idle compaction threshold"一节）
- 名字：`CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`（环境变量，默认 200000，低于 100000 按 100000 算，需要 v2.1.290+）。**已核实**。另外文档写明了触发条件：交互终端或 VS Code/桌面端、`autoCompactEnabled` 开、主会话是 1 小时 TTL、上下文达到阈值、**闲置约 1 小时**（"about an hour"，比社区说的"约 54 分钟"更权威）。
- 关键原文：「At the default, a conversation on a 200K context window auto-compacts before it reaches 200,000 tokens」，文档给的例子正是把阈值调到 150000。
- 本套件怎么用：和 A1 一起，在 11-21 设 `"CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS": "150000"`（略低于守卫的 16 万）。round3 V6 发现本机 22% 的主会话缓存写入来自"闲置 ≥1 小时后整段重写"。换 Pro 之后，空闲压缩是官方唯一一个能自动拦住这类事件的机制。
- 收益：【推测】所有者自己会话里的冷重写（V6 里约 20 次）会变成"回来时只发约 2 万 token 的摘要"。
- 风险：空闲压缩有损，回来后是从摘要接着做；2.1.318 刚修过一次"摘要不是摘要"的 bug。henchmen 的 headless agent 本来就没有空闲压缩，这一项对它们无效。

### A3. 换 Pro 当天要做的账号动作
- 来源：[errors#claude-opus-is-not-available-with-the-claude-pro-plan](https://code.claude.com/docs/en/errors)，changelog 2.1.318
- **已核实**：存在错误 `Claude Opus is not available with the Claude Pro plan…run /logout and /login`；"本地存的 token 反映的是登录时的套餐"，改套餐后要重新登录才生效。model-config 同时写着 Pro 的默认模型是 Opus 5.5，两处并不矛盾：这个错误针对的是"套餐里没有的模型"。**待核实**：11-21 后 Pro 能不能用 Opus 5.5 / Fable。
- 2.1.318 新增 `claude auth status` 里的 `loginExpiresAt`，`claude -p` 的 JSON 里有 `credential_expires_at`（**已核实**）。
- 本套件怎么用：使用说明里加一条"11-21：`/logout` → `/login` → `/status` 看套餐 → `/usage` 看额度条"。如果 Opus 报这个错，统一改用 `/model sonnet`。注意切模型会让整段缓存失效，所以要在会话开头切，或者 /clear 之后再切。
- 风险：无。

### A4. Pro 上可能出现"按模型族的限额"
- 来源：[errors#youve-hit-your-session-limit](https://code.claude.com/docs/en/errors)
- **已核实**：有 `You've hit your Opus limit` / `Sonnet limit` 两种提示，只限这个模型族，换到别的族可以继续用；5 小时和周限额是所有模型共用的。用到约 85% 时会提示 `You've used 85% of your session limit`。
- 本套件怎么用：context_guard 的额度档目前只看 rate_limits 的百分比。可以在使用说明里写明：撞到 Opus 限额时换 Sonnet 要付一次整段重读，**先 /clear 加交接，再换模型**，比在大上下文里直接切便宜。
- 风险：无（只改文字）。

### A5. 200K 窗口下 skill 列表会被截短，我们的 skill 可能不再自动触发
- 来源：[settings-reference#skilllistingbudgetfraction](https://code.claude.com/docs/en/settings-reference)
- 名字：`skillListingBudgetFraction`（默认 0.01，也就是上下文窗口的 1%）、`skillListingMaxDescChars`（默认 1536）。**已核实**：超出预算时，**用得最少的 skill 只保留名字、去掉描述**，"Claude 不太会主动选它"。用 `/doctor` 可以看占用。
- 含义：1M 窗口下预算约 1 万 token，200K 下只剩约 2000 token。本机 skill 很多（插件自带的 + handoff、model-router 等）。【推测】换 Pro 后，handoff / model-router 的描述可能被截掉，守卫提醒"用 handoff skill"时 Claude 可能找不到它的说明。
- 本套件怎么用：11-21 后跑一次 `/doctor` 看哪些描述被丢了。必要时把 handoff、model-router 的 description 压到 200 字以内；或者设 `skillListingBudgetFraction: 0.02`（多占约 2000 token/请求，都是缓存读取，很便宜）。
- 风险：调大会让每个请求多一些上下文；不调可能让交接流程失灵。

---

## B. 子 agent 成本控制

### B1. fork（分叉）：唯一能读父会话缓存的子 agent
- 来源：[sub-agents#fork-the-current-conversation](https://code.claude.com/docs/en/sub-agents)、[prompt-caching#subagents-and-the-cache](https://code.claude.com/docs/en/prompt-caching)
- **已核实**：fork 继承完整对话、同样的系统提示、工具和模型，"它的第一个请求复用父会话的 prompt cache……所以对于需要同样上下文的任务，fork 比新的子 agent 便宜"。fork mode 从 v2.1.302 起默认开（Claude 可以自己发起 fork）。手动发起用 `/subtask <任务>`；只禁 fork 用 `permissions.deny: ["Agent(fork)"]`；整个关掉用 `CLAUDE_CODE_FORK_SUBAGENT=0`。fork 不能再 fork。
- 本机数据（最近 14 天，只读统计）：子 agent 59 个，其中 general-purpose 47、web-fetch 5、Code Reviewer 3、claude-code-guide 2、**fork 只有 1 个**、Statistician 1。按模型分的请求数：Sonnet 5.5 378、Opus 5.5 294、Haiku 5.5 20、Fable 5.1 14。**每个子 agent 第一个请求的缓存写入合计约 126 万 token，平均每次启动约 2.1 万 token**，这是"启动税"。
- 本套件怎么用：在 model-router skill 里加一条规则：**父会话上下文小（比如 <8 万）、任务需要大量背景时用 fork**，省掉重新解释和约 2 万 token 的启动写入；任务自成一体（搜索、抓网页、跑测试）时用新的子 agent 加便宜模型。
- 风险：fork 的每一轮都要带上父会话的全部上下文（按缓存读取计）。父会话 30 万时，fork 每一轮都相当于读 30 万，而且 fork 用的是父会话的模型（大多是 Opus）。所以"父会话大时不要 fork"必须写清楚。fork 属于"其他"那一组，TTL 是 5 分钟。

### B2. `omitClaudeMd: true`（子 agent frontmatter，v2.1.271+）
- 来源：[sub-agents 字段表](https://code.claude.com/docs/en/sub-agents)。**已核实**。
- 作用：子 agent 启动时不加载用户、项目、local 的 CLAUDE.md（托管策略文件照常加载）。
- 本套件怎么用：只适合"所需信息全在委派提示里"的只读研究型 agent。本机 `Projects/AGENTS.md` 约 4.3KB（约 1100 token），~/.claude 下没有 CLAUDE.md，所以**收益很小**（每次启动省约 1000 到几千 token）。
- 风险：**子 agent 会丢掉红线**（不 rm、不 push、不读 .secret）。**不建议**给能写文件或跑 Bash 的 agent 开。本轮只作为备选。

### B3. SubagentStart 的 `additionalContext`：让子 agent 返回短摘要
- 来源：[hooks#subagentstart](https://code.claude.com/docs/en/hooks)。**已核实**：SubagentStart 不能阻止子 agent，但可以在它的第一个提示之前注入上下文；同一个子 agent 再次触发时不会重复注入，"保持子 agent 的 prompt cache 不变"。matcher 匹配 agent 类型。
- 本套件怎么用：注入一句"最终报告 ≤40 行；不要粘贴大段原文，给文件路径和行号"。子 agent 的返回会进入主会话，而主会话之后每一轮都要重读它，所以返回越短，之后每一轮越省。
- 收益：【推测】每次委派少 1–5 千 token 进入主上下文，之后每一轮按缓存读取省下来。
- 风险：有些任务需要长报告（比如本报告这种调研）。所以应该用 matcher 只对 `general-purpose|Explore|web-fetch` 生效，或者措辞写成"除非任务要求完整报告"。

### B4. 并发数和深度上限
- 来源：[sub-agents#limiting-subagents](https://code.claude.com/docs/en/sub-agents)、env-vars。**已核实**：`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`（默认 20）、`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`（默认最多 3 层）。`CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` **在 v2.1.224 已移除、现在不起作用，不要用**。`maxTurns` 到上限时返回"部分结果"，之后可以续跑（v2.1.246+）。
- 本套件怎么用：Pro 下一次大扇出就可能把 5 小时额度用完（官方原文："A single burst of heavy activity, such as a large workflow fanout, can exhaust the weekly allowance"）。建议 11-21 后设 `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS=4`、`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1`。
- 风险：model-router 里"多 agent 并行"的做法会被限速；超过上限时 Agent 工具直接报错，并告诉 Claude 不要重试。

### B5. skill / 命令的 frontmatter 里写 `model` 等于切一次模型
- 来源：[prompt-caching#switching-models](https://code.claude.com/docs/en/prompt-caching)。**已核实**："When a skill or command's frontmatter names a `model` other than the session's current model, that turn is also a model switch"，下一个请求会整段重读、没有缓存命中。
- 本机检查：handoff、model-router、quant-backtest-guide、shixiseng-auto-apply 四个 skill 和 5 个 agent 都**没有**写 `model:`（已核实）。
- 本套件怎么用：在 scorecard 里加一条静态检查："~/.claude/skills/*/SKILL.md 不允许出现 `model:`"，防止以后无意中引入。成本约等于 0。
- 风险：无。

### B6. Fable 子 agent
- 来源：model-config："Depending on your plan and seat tier, Fable usage can bill to usage credits instead of drawing on your plan's included limits"。**已核实**这句话，但它在 Pro 上具体怎么算**待核实**。
- 本机 14 天里有 14 个 Fable 5.1 的子 agent 请求。换 Pro 后，model-router 里建议用 Fable 的地方应该先确认会不会走 usage credits。

---

## C. 缓存过期与冷重写

### C1. 状态栏会在缓存到期那一刻自动刷新
- 来源：[statusline#when-the-status-line-updates](https://code.claude.com/docs/en/statusline)。**已核实**：除了新消息、/compact 等事件，状态栏还会在"上次收到的 rate-limit 窗口到达 `resets_at`"和"上次收到的热缓存到达 `prompt_cache.expires_at`"时重新运行。
- 含义：round3 第 11 项的 ❄ 提示会**准时**出现，不依赖 `refreshInterval`。本机 settings 里 `refreshInterval: 5` 只是为了时钟类的显示，可以保留。
- 本套件怎么用：在 ❄ 之前加一档"缓存还剩 ≤10 分钟"的倒计时（`expires_at - now`），提示"要离开就先 /compact（缓存热的时候便宜）或者写交接后 /clear"。
- 收益：官方博客说缓存热时的压缩请求约为"十分之一价格"、摘要约 2 万 token（见 C3）。离开前先压缩，就能避免回来时整段冷重写（V6 的大头）。【推测】
- 风险：状态栏多一段字，可能显得吵。只在上下文 ≥10 万时显示。

### C2. `/rewind` 的局部压缩，以及 /usage 里的"离开前先压缩"
- 来源：[context-window#when-your-context-fills-up](https://code.claude.com/docs/en/context-window)、[costs#plan-usage-breakdown](https://code.claude.com/docs/en/costs)。**已核实**：`/rewind` → 选一条消息 → **Summarize from here / Summarize up to here**，只压缩一部分对话。`/usage` 的建议行里有 `Compact before you step away · run /compact` 这种写法（官方示例）。
- 本套件怎么用：使用说明里加"放弃一段走错的路用 /rewind（读的是已缓存的前缀，最便宜）；前半段已经没用了用 Summarize up to here"。守卫强提醒里可以把它列为 /clear 之外的选项。
- 风险：无（只改文字）。

### C3. 官方博客《Prompt caching is everything》（2026-04-30）
- 来源：[claude.dev/blog/lessons-from-building-claude-code-prompt-caching-is-everything](https://claude.dev/blog/lessons-from-building-claude-code-prompt-caching-is-everything/)（round3 没有读到）。
- 要点（博客原话，属于官方工程博客，但不是文档）：缓存热时的压缩输入约为"one tenth the price"，摘要约 2 万 token；"Don't change tools or models mid-conversation"；在 Opus 10 万上下文里为一个简单问题切到 Haiku，反而比让 Opus 回答更贵；要换便宜模型就用子 agent。
- 本套件怎么用：给 round3 的"别中途切模型"补上具体例子，并给 C1 的"离开前压缩"提供依据。
- 风险：无。

### C4. 升级 Claude Code 后第一条会话要整段重建缓存
- 来源：[prompt-caching#upgrading-claude-code](https://code.claude.com/docs/en/prompt-caching)。**已核实**：新版本一般会改系统提示或工具定义，升级后第一个新会话从头建缓存；自动更新在下次启动时才生效；`DISABLE_AUTOUPDATER=1` 可以控制升级时机。恢复旧会话时沿用旧的系统提示，第一次压缩时换成新的，这一层缓存重建一次。
- 本套件怎么用：周报统计"冷重写"时，把"版本号变化后的第一个请求"单独标出来，不要算成所有者的习惯问题。
- 收益很小，风险无。

### C5. `prompt_cache` 里可以拿来统计的新字段
- 来源：[statusline#prompt-cache-fields](https://code.claude.com/docs/en/statusline)。**已核实**的字段：`hit_ratio`、`misses`（定义：超过 5% 且至少 2000 token 本可以读缓存却没读）、`expected_rebuilds`（压缩或"清除旧工具结果"之后的重建）、`cache_write_tokens`、`miss_recache_tokens`、`last_miss_at`、`last_miss_cause.causes`（如 `tools_changed`、`system_prompt_changed`、`ttl_expired_5m`、`likely_server_side`，外加 `tools_added`/`tools_removed`/`system_char_delta`）、`miss_causes` 计数。
- 文档提到 Claude Code 会"clearing old tool results from context"（清除旧工具结果），但**我没找到任何设置名或触发条件**（待核实）。
- 本套件怎么用：statusline 在会话结束前（或 miss 数变化时）把一份 `prompt_cache` 快照追加到 `state/quota_samples/<session_id>.jsonl`，和 round3 第 12 项第 5 条合并。周报就能按原因统计 miss，不用再从 transcript 里推断。
- 风险：低。只追加写文件。

---

## D. Hook 新字段与新事件

### D1. `StopFailure` 和 `Notification` 的额度相关类型：记录"真正撞上限的时刻"
- 来源：[hooks#stopfailure](https://code.claude.com/docs/en/hooks)、hooks#notification。**已核实**：StopFailure 在 API 出错导致回合结束时运行，`error` 取值包括 `rate_limit`、`billing_error`、`account_on_hold`、`max_output_tokens` 等，另有 `error_details`；输出会被忽略，只能拿来记录。Notification 的 matcher 有 `quota_auto_resume_fired`、`quota_auto_resume_stale`、`quota_auto_resume_disabled`。
- 本套件怎么用：加一个只写日志的 hook，向 `state/limit_events.jsonl` 追加（时间、error 或通知类型、session_id、当时 rate.json 的百分比）。这是 quota_fit 回归最好的"锚点"：100% 对应的时刻是真实的，不是整数百分比读数。
- 风险：极低。不注入、不拦截。改 settings.json 需要所有者执行。

### D2. hook 的 `if` 字段：让 test_filter 只在疑似测试命令时启动
- 来源：hooks 的 `if` 一节。**已核实**：用权限规则语法（例如 `"Bash(git *)"`），只在 PreToolUse/PostToolUse 等工具事件上生效，一条 hook 只能写一条规则，"best-effort"。
- 本套件怎么用：test_filter 现在每次调用 Bash 都会启动一个 Python 进程。可以拆成几条 `if: "Bash(pytest *)"`、`"Bash(*pytest*)"`、`"Bash(npm test*)"`。省的只是延迟和 CPU，**不省 token**。
- 风险：`if` 的匹配规则和 test_filter 自己的切分器可能不一致，造成漏包装。**不建议本轮做**，列在这里备查。

### D3. 其他会影响现有 hook 的细节
- **已核实**：
  - `UserPromptSubmit` 的 command hook 默认超时是 **30 秒**（不是通用的 600 秒）。
  - UserPromptSubmit "fires also on turns Claude starts on its own"：Claude 自己发起的回合（goal check-in、跨会话消息等）也会触发 context_guard。【推测】守卫可能在这些回合里注入提醒，需要确认不会重复刷屏（待核实）。
  - SessionStart 的 `model` 字段**在 /clear 之后可能缺失**，session_resume 不能依赖它。
  - PreCompact 的 `auto` 也包括空闲压缩；它的 stdout 每个 hook 上限 1 万字符（2.1.314 起超长会截断）。
  - 2.1.314 起，Bash 的 hook 也会匹配 Monitor 工具启动的命令，但"can block or ask, but not approve or rewrite"，**test_filter 对 Monitor 命令改写无效**。
  - 2.1.311 修了 SubagentStop hook 每分钟跑两次的 bug。以后如果加 SubagentStop，要求版本 ≥2.1.311（本机满足）。
  - `asyncRewake: true`：后台运行，exit 2 时把 stderr 作为 system reminder 唤醒 Claude。
- 本套件怎么用：在 scorecard 里加两个场景：CG 守卫在"Claude 自发回合"里不重复注入；SR 在 source=clear 且没有 model 字段时不报错。

---

## E. 其他设置

### E1. `showClearContextOnPlanAccept: true`
- 来源：[settings-reference](https://code.claude.com/docs/en/settings-reference)。**已核实**：plan 模式批准时多出第一个选项"Yes, clear context and …"，批准后清空上下文，只凭计划开始实现，并显示规划用了多少上下文。默认 false。
- 本套件怎么用：所有者用 plan 模式做大任务时，探索阶段的上下文常有十几万。批准后清空，等于官方版的"交接后 /clear"。
- 收益：【推测】大任务实现阶段每一轮少读十几万缓存。
- 风险：实现阶段看不到探索时读过的细节，可能要重读文件。只是多一个选项、不强制，所以风险低。是否开由所有者决定。

### E2. `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` / `MAX_MCP_OUTPUT_TOKENS`
- **已核实**：Read 单次默认上限 25000 token；MCP 输出默认上限 25000 token（超过 1 万会警告，超过 5 万字符的文本存成文件）。
- 本套件怎么用：可以考虑把 Read 上限降到 15000。不过 round3 V9 已经说明 Bash 大输出很少，Read 的分布本机还没统计。**先统计再决定**，本轮不动。

### E3. `/autocompact` 写的按模型窗口会覆盖 `autoCompactWindow`
- 来源：model-config#set-the-auto-compact-window。**已核实**："A window you saved for a model with `/autocompact` takes precedence over this key in the same file for that model"（v2.1.288 起保存在 `modelSettings.<model>` 下）。
- 本机：settings.json 现在没有 `modelSettings`，所以 300000 生效（已核实）。
- 本套件怎么用：scorecard 或周报加一项检查："modelSettings 下有没有 autoCompactWindow，有就报告它覆盖了全局值"。避免所有者哪天随手 `/autocompact 500k` 让兜底失效，而自己不知道。
- 风险：无。

### E4. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`
- **已核实**：设置在压缩窗口的百分之几处触发，只能调低，**对主会话和子 agent 都生效**。和 `autoCompactWindow` 功能重叠，不建议同时用，列在这里备查。

---

## F. 与 round3 计划冲突或需要修正的点

1. **round3 第 3 项（statusline 不冲掉 rate.json）**：官方写明 "Claude Code drops a window once its `resets_at` time passes"（**已核实**）。所以 payload 里缺 `rate_limits.five_hour` **可能是因为窗口刚重置**，不只是"这次没带"。第 3 项"保留旧值"的逻辑**必须**同时检查旧值的 `resets_at <= now`，过期就丢掉（第 1 项在 context_guard 里做了，statusline 也要做）。否则会把重置前的 90% 一直显示下去。
2. **round3 §2.4 / round3_plan V2 的"子 agent 默认 5 分钟缓存所以很贵"**：fork 是例外，第一个请求读父缓存（B1）。本机几乎没用过 fork。
3. **round3 R6 的"空闲压缩保持默认"**：在 Max（1M 窗口）上没问题；换 Pro 之后，默认阈值 20 万会让它**完全失效**（A2），必须重新决定。
4. **round3 引用的空闲压缩"约 54 分钟"**：官方写的是"about an hour"，以官方为准。

## G. 社区来源（可靠性有限）
- GitHub [#32671](https://github.com/anthropics/claude-code/issues/32671)（Bedrock 缓存 TTL）、[claudeissues 镜像：子 agent 缓存 TTL 可配置的请求](https://claudeissues.com/issue/62217-support-configurable-prompt-cache-ttl-for-subagent-launches)、[recca0120 的 TTL 审计博客](https://recca0120.github.io/en/2026/04/26/claude-code-cache-ttl-17-days/)：都是 4–5 月的旧问题，现在已有 `subagentPromptCacheTtl`、`experimental.cacheTtl`，**没有新信息**。其中"子 agent 缓存写入占一次会话成本约 1/3"只是单例。
- [PCWorld](https://www.pcworld.com/article/3099983/) 说 Anthropic 在"调整"高峰时段的 5 小时限额、周限额不变：日期和细节**待核实**，不引用。
- Reddit（镜像快照，2–5 月）：常见建议是 CLAUDE.md 精简、换话题就开新会话、提示写具体。和官方一致，**没有新做法**。没有找到 2026-11 关于"Max 换 Pro"的可靠帖子。
- 结论：这一轮真正的新信息几乎全部来自官方文档；社区没有提供可以验证的新技巧。

---

## 建议纳入第 4 轮（前 5 项，按优先级）

| # | 内容 | 依据 | 谁做 | 预估收益 / 风险 |
|---|---|---|---|---|
| 1 | **11-21 Pro 配置包**（先写好，11-21 由所有者运行）：`env` 里加 `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`、`CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=150000`、`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS=4`；当天的检查清单：/logout → /login → /status → /usage → /doctor（看 skill 描述有没有被截掉）。配套的撤回命令 | A1、A2、A3、A5、B4（均已核实） | 套件写安装/撤回脚本；所有者 11-21 运行 | 让 Pro 上的行为确定下来，并让空闲压缩重新生效，直接针对 V6 里 22% 的冷重写。风险：压缩更频繁；henchmen 能不能拿到 env 待核实，所以保留 `autoCompactWindow` |
| 2 | **状态栏"缓存倒计时 + 离开前压缩"**：在 round3 第 11 项 ❄ 的基础上，`expires_at - now ≤ 10 分钟` 且上下文 ≥10 万时显示"⏳ 缓存 N 分钟后过期：要离开先 /compact 或交接后 /clear" | C1、C2、C3（已核实；"1/10"来自官方博客） | 可以直接做（只改显示） | 【推测】把冷重写变成热压缩，单次省十几到几十万新 token。风险：提示比较吵，用 10 万门槛限制 |
| 3 | **model-router 子 agent 规则更新**：父会话小且需要背景时用 fork，父会话大时禁用 fork；自成一体的任务用新的子 agent 加 Sonnet/Haiku；SubagentStart 对 `general-purpose|Explore|web-fetch` 注入"报告 ≤40 行、给路径不贴原文"；scorecard 静态检查 skill 里不能写 `model:` | B1、B3、B5（已核实）；本机：14 天 59 个子 agent、启动税约 2.1 万 token/个、fork 只有 1 次 | skill 文字和 scorecard 可以直接做；SubagentStart hook 改 settings，需所有者同意 | 【推测】每次委派省 1–2 万启动写入（fork 场景），主上下文每次少进几千 token。风险：父会话大时 fork 反而更贵；短报告不适合调研型任务 |
| 4 | **额度测量锚点**：StopFailure（`rate_limit`/`billing_error`）、Notification（`quota_auto_resume_*`）只写日志；statusline 追加 `prompt_cache` 快照（`miss_causes`、`hit_ratio`、`cache_write_tokens`），和 round3 第 12 项 quota_samples 合并 | D1、C5（已核实） | 只追加写文件，可以直接做；新增 hook 要所有者执行 settings 命令 | 给 quota_fit 提供真实的 100% 时刻，按原因统计 miss，周报不再靠推断。风险极低 |
| 5 | **修正 round3 第 3 项的过期窗口逻辑，再加两条配置体检**：statusline 合并 rate_limits 时，旧窗口 `resets_at <= now` 就丢弃；scorecard/周报检查 `modelSettings.*.autoCompactWindow` 是否覆盖全局兜底；新增场景"守卫在 Claude 自发回合里不重复注入"、"SessionStart 没有 model 字段时不报错" | F1、E3、D3（已核实） | 可以直接做 | 防止状态栏和守卫按已经重置的旧额度误判（会影响"宽裕/紧张"档位）。风险低 |

备选（没进前 5）：`showClearContextOnPlanAccept: true`（E1，所有者决定）、`omitClaudeMd`（B2，收益小且会丢红线）、hook `if` 字段（D2，不省 token）、Read 上限（E2，先统计）。
