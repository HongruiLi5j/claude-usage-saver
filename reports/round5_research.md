# 第 5 轮：收集新资料（代号 E，2026-11-06）

## 0. 口径

- 所有网页读取日都是 **2026-11-06**。code.claude.com 和 support.claude.com 的页面大多没有标日期，所以"日期"一栏写读取日；changelog、GitHub release、PyPI 写它们自己的日期。
- 本机版本：`claude --version` = **2.1.318**，`codex --version` = **codex-cli 0.163.0**。gemini、aider、cursor-agent、ccusage **本机都没有安装**，所以这几个工具的内容全部来自文档，没有实测。
- **官方 changelog 在 11-06 发布了 2.1.319**（round4 写的"2.1.318 是最新"已经过时）。下文凡标"2.1.319"的功能，本机暂时用不了。
- 去重：round3_research.md 和 round4_research.md 已写过的内容不再重复，包括 promptCacheTtl 系列、autoCompactWindow 系列、CLAUDE_CODE_DISABLE_1M_CONTEXT、IDLE_COMPACT_MIN_TOKENS、skillListingBudgetFraction、fork、omitClaudeMd、SubagentStart、MAX_CONCURRENT_SUBAGENTS、StopFailure、showClearContextOnPlanAccept、FILE_READ_MAX_OUTPUT_TOKENS、bashOutputMaxChars、prompt_cache 字段等。我用 grep 查过，下文出现的设置名在 reports/ 里都是第一次出现（只有 `seven_day` 和 `recache_tokens_if_cold` 已经被 statusline 用上，下文只是顺带提到）。
- 标记说明：
  - 【官方】：官方文档原文里有这个名字和说法。
  - 【实测】：我在本机跑过命令、亲眼看到的。
  - 【社区】：第三方的说法，数字基本都是作者自报。
  - 【推测】：我自己的推断。
  - 【未证实】：找不到可靠来源。
- 文档原文我下载到了 scratchpad，方便复查（会话结束可能被清掉）：`/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/e_docs/`。
- **只读检查了本机配置，什么都没改**：
  - `~/.claude/settings.json` 里现在有 `modelSettings: {"claude-opus-5-5": {"effortLevel": "high"}}`。round4 写的是"没有 modelSettings"，所以**这是 round4 之后新加的**，见 1.2。
  - `~/.codex/config.toml` 里有 `service_tier = "priority"`，见 2.1。

---

## 1. Claude Code：新的或还没用上的设置和功能

### 1.1 固定开销（skill 列表、连接器、git 说明、系统提示）：对应 A

round5_where_tokens_go 的数据（启用后）：固定前置约占成本 26.5%。其中系统提示加工具定义 16.6%、skill 列表 5.3%、agent 列表 1.7%、deferred tools 1.0%、MCP 说明 0.9%。

| 设置 / 功能 | 作用（官方原文要点） | 来源 | 和本机的关系 |
|---|---|---|---|
| `skillOverrides`（对象，值为 `on` / `name-only` / `user-invocable-only` / `off`） | 不改 SKILL.md，就能让某个 skill 只显示名字、只允许手动 `/name` 调用，或者完全隐藏。`/skills` 菜单会把它写到 `.claude/settings.local.json`。**对插件 skill 不生效**（插件 skill 要用 `/plugin` 管） | [settings-reference#skilloverrides](https://code.claude.com/docs/en/settings-reference)【官方】 | 不常用的 skill（比如 shixiseng-auto-apply）可以设成 `user-invocable-only`：Claude 看不到它，但所有者照样能手动 `/` 调用 |
| `syncClaudeAiSkills: false` | 不再下载、也不再加载 claude.ai 账号同步来的 skill（`~/.claude/skills/synced/`）。**在 user 设置里设 false，会把已同步的 skill 移到 `~/.claude/skills/.trash/`** | 同上【官方】 | 本机有一个 `synced/` 目录。本会话的 skill 列表里有十几个 `anthropic-skills:*`（docx、pptx、xlsx、pdf、deep-research 等），描述都很长。【推测】这是 skill 列表 5.3% 里的一大块。风险：所有者如果在 CLI 里用到 docx/pdf 这类 skill，就会失去它们 |
| `disableBundledSkills` / `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1` | 去掉 Claude Code 自带的 skill 和 workflow | 同上【官方】 | **注意**：`/doctor prompt-audit` 依赖自带的 `/claude-api` skill，关掉之后就不能用了（[memory#audit-your-instruction-files](https://code.claude.com/docs/en/memory)） |
| `disableClaudeAiConnectors: true` / `ENABLE_CLAUDEAI_MCP_SERVERS=false` | 不抓取、也不连接 claude.ai 上的 MCP 连接器 | [settings-reference#disableclaudeaiconnectors](https://code.claude.com/docs/en/settings-reference)、[env-vars](https://code.claude.com/docs/en/env-vars)【官方】 | 本会话里有 Gmail、Calendar、Google Docs/Drive/Sheets/Slides、Claude Docs 连接器：约 60 个 deferred 工具名，外加较长的服务器说明。官方 support 文章也建议关掉用不到的连接器（[11647753](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work)："Tools and connectors are token-intensive"）。用环境变量的写法适合只对 henchmen 或无头运行关掉 |
| `includeGitInstructions: false` / `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1` | 去掉 Bash 工具描述里的提交/PR 流程说明，以及启动时的 git status 快照；子 agent 启动时的 git 快照也随之消失 | [settings-reference#includegitinstructions](https://code.claude.com/docs/en/settings-reference)、[sub-agents#what-loads-at-startup](https://code.claude.com/docs/en/sub-agents)【官方】 | 每个请求能省多少，要用 `/context` 实测【未证实】。附带作用：官方说"相邻两次会话只有在启动时的 git 快照相同时才共享前缀缓存"（[prompt-caching](https://code.claude.com/docs/en/prompt-caching)），去掉快照后，【推测】同一目录下前后两个会话更容易命中彼此的缓存 |
| `CLAUDE_CODE_SIMPLE_SYSTEM_PROMPT` | 在完整系统提示和带缩略工具描述的短提示之间切换。**不设置时，比 Opus 4.7 / Sonnet 5 更新的模型默认就用短提示** | [env-vars](https://code.claude.com/docs/en/env-vars)【官方】 | Opus 5.5 / Sonnet 5.5 已经在用短提示，**不用再设**。列在这里是为了避免有人去设成 0（那会改回完整提示） |
| `ENABLE_TOOL_SEARCH`（`true` / `auto` / `auto:N` / `false`） | MCP 工具延迟加载，默认开启 | [mcp#configure-tool-search](https://code.claude.com/docs/en/mcp)【官方】 | 默认值已经是最省的。**不要设 false**，也不要设 `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`（它会顺带关掉 tool search） |
| `CLAUDE_CODE_DISABLE_CFC_PROMPT=1` | 保留 Chrome 工具，但去掉系统提示里的 Chrome 一节和 `/claude-in-chrome` 这个 skill | [env-vars](https://code.claude.com/docs/en/env-vars)【官方】 | `~/.claude.json` 里 `claudeInChromeDefaultEnabled=false`，所以普通 CLI 会话本来就不带 Chrome；只有用 `--chrome` 或桌面端启动的会话才有这部分开销 |
| `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` | 同时跳过"生成会话标题"那次后台小模型请求 | [env-vars](https://code.claude.com/docs/en/env-vars)【官方】 | 收益很小（每个会话少一次 Haiku 请求） |
| `/doctor prompt-audit [path]`（v2.1.283+） | 让 Claude 检查 CLAUDE.md、AGENTS.md、skills、agents、output styles 里过时、互相矛盾或引用了不存在文件的内容，只出报告，不直接改文件 | [memory#audit-your-instruction-files](https://code.claude.com/docs/en/memory)【官方】 | A 可以用它来审 handoff、model-router 的描述长度和冲突。它本身要消耗额度 |
| AGENTS.md 原生读取（v2.1.277+） | **没有 CLAUDE.md 时，Claude Code 直接读 AGENTS.md**。在子目录里用 Read 打开文件时，也会加载那个子目录的 AGENTS.md。`/config` 的 **Project instructions** 有三个值：`claude-md-or-agents-md`（默认）、`claude-md-and-agents-md`、`claude-md` | [memory#agents-md](https://code.claude.com/docs/en/memory)【官方】 | 本会话里，我 Read 项目文件时被自动注入了 `Projects/AGENTS.md`（约 4.3KB）。round5_where_tokens_go 把它记成"hook 注入 AGENTS.md 0.7%"。**需要 A 核对**：这是原生加载，还是套件的 hook 注入？如果两者都有，就是重复加载【未证实】 |

### 1.2 输出与思考（启用后 output 占成本 26%，assistant 内容合计占 37%）

- **本机 Opus 5.5 的 effort 被设成了 high**【实测，只读】：`settings.json` 里有 `modelSettings.claude-opus-5-5.effortLevel = "high"`。
  - 官方原文（[model-config#adjust-effort-level](https://code.claude.com/docs/en/model-config)）：Opus 5.5 默认是 `medium`；"In Anthropic's testing, Opus 5.5 at `medium` matches or exceeds Opus 5 at `high`"；"At a given level, Opus 5.5 tends to think more per turn than Opus 5"。
  - 官方 support 文章：**"Higher effort uses more tokens."**
  - 所以这一项直接影响 output 那 26%。具体能省百分之多少，**没有官方数字【未证实】**，需要 F2 做受控对比。
  - 这项设置可能是所有者自己通过 `/effort` 选的，所以要问所有者，不能直接改。
- `maxEffortLevel`（v2.1.267+，可以全局设，也可以按模型设）：给 effort 加一个上限，每次请求前生效（[settings-reference](https://code.claude.com/docs/en/settings-reference)【官方】）。用途：防止 skill 或 `ultrathink` 把某一轮推到 xhigh/max。环境变量 `CLAUDE_CODE_EFFORT_LEVEL` 的优先级**高于子 agent frontmatter 里的 `effort`**（[sub-agents 字段表](https://code.claude.com/docs/en/sub-agents)【官方】），所以想单独压低子 agent 的 effort 时，不要在全局设这个环境变量。
- **Concise 输出风格**（v2.1.237+）：`/output-style concise` 或 settings 里写 `"outputStyle": "Concise"`（区分大小写）。
  - 官方原文："The Concise style does the opposite by instructing Claude to keep responses short by default"，而 Explanatory/Learning 会让回复更长。
  - 只作用于主会话和 fork，**普通子 agent 不受影响**。会话中途切换不会让缓存失效（v2.1.251 起立即生效）。
  - 来源：[output-styles](https://code.claude.com/docs/en/output-styles)【官方】。
  - 和所有者"回复要详细"的要求冲突，所以**只建议用在无头 / henchmen 运行**（输出给机器读的场景）。
- `/btw <问题>`：问一个旁支问题，**答案不进入对话历史**，可以看到整段对话，但没有工具（[interactive-mode#side-questions-with-btw](https://code.claude.com/docs/en/interactive-mode)【官方】）。好处是避免"顺口一问"在之后每一轮都被重读。
- `MAX_THINKING_TOKENS` 只对固定思考预算的模型有效；Opus 5.5 / Sonnet 5.5 / Haiku 5.5 / Fable 关不掉思考，只能用 effort 来调（[costs](https://code.claude.com/docs/en/costs)【官方】）。

### 1.3 模型搭配：advisor 工具（实验功能，前几轮都没提过）

- 来源：[advisor](https://code.claude.com/docs/en/advisor)【官方】。
- 怎么开：`/advisor opus`、settings 里写 `advisorModel`，或者启动参数 `--advisor opus`（文档明说 `--advisor` 不会出现在 `claude --help` 里；我本机 `claude --help` 确实搜不到【实测】）。
- 机制：主模型在关键节点（定方案、反复报错、宣布完成之前）把**完整对话**交给更强的模型咨询。
  - 订阅下，advisor 的用量**计入套餐额度**；Fable 当 advisor 时走 usage credits。
  - 子 agent 会继承 advisor 设置。
  - advisor 的工具定义放在缓存断点之后，所以开关 `/advisor` 不会让缓存失效。
- 官方推荐的搭配：**Sonnet 做主模型 + Opus 做 advisor**；Haiku + Opus 则"比只用 Haiku 贵，但比把主模型换成 Sonnet 或 Opus 便宜"。官方原话："pairing a faster main model with a stronger advisor typically costs less than running the stronger model throughout"。
- 风险：
  - 每次咨询都要读完整对话，大上下文里很贵。
  - 是实验功能。
  - 主会话切到 Sonnet 等于一次模型切换，要在会话开头或 /clear 之后切。
- 能省多少：【未证实】，需要 F2 实测。

### 1.4 子 agent / workflow / 无人值守

- **续用子 agent 可以读到它原来的缓存**：官方原文"when Claude resumes a subagent, the resumed run's first request can read the cache the original run warmed"（[prompt-caching#subagents-and-the-cache](https://code.claude.com/docs/en/prompt-caching)【官方】）。
  - 子 agent 的 TTL 是 5 分钟，所以这个好处只在 5 分钟内有效。
  - 2.1.319 在 `/tasks` 里加了"Earlier in this session"一行，已结束的子 agent 也能重新打开、发消息（changelog 2.1.319【官方】）。
  - 和 round4 的"启动税约 2.1 万 token/个"对照：【推测】5 分钟内追加修改时用 SendMessage 续用，比新开一个子 agent 省掉这约 2 万 token 的写入。
- workflow 扇出共享前缀：模型、effort、agent 类型、工具、输出 schema、工作目录都相同的 agent，后启动的那个可以读先启动的那个的缓存。Claude Code 会先把其余 agent 扣住，最多 `CLAUDE_CODE_WORKFLOW_PREFIX_STAGGER_MS`（默认 5000）毫秒（[workflows#prompt-caching-in-a-fan-out](https://code.claude.com/docs/en/workflows)【官方】）。默认值已经合适，不用改。
- `workflowSizeGuideline`（`small` / `medium` / `large` / `unrestricted`）：建议 Claude 写的 workflow 用多少个 agent（small 是少于 5 个）。**登录 Pro 时默认就是 `small`（v2.1.271+）**，Max 默认 `medium`（[settings-reference#workflowsizeguideline](https://code.claude.com/docs/en/settings-reference)【官方】）。这只是建议，不是硬上限。
- `--exclude-dynamic-system-prompt-sections`（本机 `claude --help` 有【实测】）：把每台机器各不相同的部分（cwd、环境、memory 路径、git status）从系统提示挪到第一条用户消息，"Improves prompt-cache reuse across different users and machines running the same task"（[cli-reference](https://code.claude.com/docs/en/cli-reference)【官方】）。
  - 官方又说"缓存实际上只在同一台机器、同一个目录内共享"，原因正是系统提示里带着这些动态段。
  - 【推测】henchmen 或 codexws 在**不同目录里并行**跑 `claude -p` 时，加这个参数可能让它们共享系统提示加工具定义这段前缀（约 2 万 token/个）。需要 B 在临时目录里用两个 `-p` 跑一次，看 `cache_read_input_tokens` 验证。
- `permissions.denyInsteadOfPrompting`（**2.1.319 新增，目前只出现在 changelog，settings-reference 还没有这一条**）：本来要等权限弹窗的工具调用改成直接拒绝，回合继续。终端会话、子 agent、teammate 都适用。用途：通宵运行时不会卡在弹窗上，等到缓存过期。本机 2.1.318 不支持，【未证实】要升级后实测。
- `CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS=1`：去掉内置的 Explore 和 Plan 子 agent，也就去掉了它们在 agent 列表里的描述（[env-vars](https://code.claude.com/docs/en/env-vars)【官方】）。model-router 会用到 Explore，所以**不建议**开，列在这里备查。
- **Haiku 5.5 在超过 100K 的 prompt 上更贵**："A Haiku 5.5 request costs more per token when its prompt is longer than 100K tokens"（[model-config#haiku-5-5-context-window-and-pricing](https://code.claude.com/docs/en/model-config)【官方】）。订阅额度上怎么折算【未证实】。model-router 的"便宜模型"规则应该注明：大上下文的任务不要派给 Haiku。

### 1.5 /context、/usage、/cost

- `/cost` 和 `/stats` 都是 `/usage` 的别名（[commands](https://code.claude.com/docs/en/commands)【官方】）。
  - `/usage` 里按 `d` / `w` 切换 24 小时和 7 天视图，按 `f` 让 Claude 执行建议（v2.1.305+），这些 round3 已写过。
  - 新补充：数据只来自本机会话历史，**不含其他设备和 claude.ai 网页端**。
- **≤2.1.318 的 /context 有 bug**：2.1.319 修复了"`/context` sending its full visual grid to Claude on every later turn"，同时把给 Claude 看的表限制为每类最多 10 行，按需加载的 MCP 工具每个服务器合并成一行（changelog 2.1.319【官方】）。
  - 含义：**本机 2.1.318 上运行一次 /context，之后每一轮都会多带一张大网格**。
  - 建议：升级前用 `/usage` 代替 `/context`；需要看 `/context` 时，看完就 /clear。
  - 升级本身会让第一个会话整段重建缓存（round4 C4），所以升级要放在任务的自然间隙。
- spinner 旁边的 `↓ 5.5k tokens` 只统计本轮的输出（含思考、前台子 agent 的输出），不含上下文（[costs#read-the-token-count-beside-the-spinner](https://code.claude.com/docs/en/costs)【官方】）。所有者可以用它当"本轮输出"的现场读数，判断 effort 偏高的影响。

### 1.6 statusline 能拿到、但我们还没用的字段

来源：[statusline](https://code.claude.com/docs/en/statusline)【官方】。已在用：`rate_limits.five_hour` / `seven_day`、`prompt_cache.recache_tokens_if_cold`、`expires_at`。

| 字段 | 用途建议 |
|---|---|
| `effort.level` | 显示当前 effort。和 1.2 一起用，所有者能一眼看出自己在 high 上 |
| `thinking.enabled`、`fast_mode` | fast mode 会提高单价（settings-reference 原话："`fastMode` raises cost per token"），开着时应该醒目提示 |
| `output_style.name` | 无头运行时核对是不是 Concise |
| `exceeds_200k_tokens` | 固定按 20 万判断，和窗口大小无关。换 Pro 之后，可以直接当"快到 200K 窗口了"的信号 |
| `rate_limits.spend_limit.*` | 只在 Claude apps gateway 后面才有，本机用不上 |

社区的 ccstatusline v2.2.32–2.2.34 做了几个我们没有的组件（第 3 节）："Block Forecast / Block Limit Timer"（按最近的速度预测 5 小时窗口到重置时会用到多少、还有多久撞线）、"Weekly Limit Timer"、"Cache Timer 倒计时"、"Cache If Cold"。

### 1.7 额度窗口规则和 Pro / Max 差异（官方能确认的部分）

- 5 小时窗口："The period starts when you send your first message, and your limit resets in full five hours later."
- 周限额："Weekly limits reset at a fixed time each week that is assigned to your account"；周限额覆盖所有模型，"some plans also have a separate weekly limit for Fable"。
- 以上两条来源：[support 11647753](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work)【官方】，页面标"Updated this week"。
- Pro 和 Max 都有 5 小时加每周两种限额，Max 另有 Fable 的周限额。Ultra 只有周限额。IDE（VS Code、Cursor、JetBrains）里的用量也计入同一份额度（[support 11145838](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan)【官方】，页面标"Updated over a week ago"）。
- 收尾额度（wrap-up）：5 小时限额在回复中途用完时，允许把当前这一步做完，额度计入周限额。**Pro 每个周期只有一次**，Max 每次撞 5 小时限额都可以用（同上【官方】，逐步放量）。
- 影响用量的因素，官方原文："Higher effort uses more tokens"、"Longer conversations that trigger automatic context management consume more"、"Tools and connectors are token-intensive"（11647753【官方】）。
- Pro 上开 workflow 要在 `/config` 里手动打开，默认规模是 small（[workflows](https://code.claude.com/docs/en/workflows)【官方】）。
- **仍然查不到的**：
  - 5x / 20x 倍率、周限额的倍数，support 原文都没写（只有社区博客说，比如 [contextbolt](https://contextbolt.com/blog/claude-max-vs-pro/)）【未证实】。
  - Pro 上 Opus 5.5 / Sonnet 5.5 的 1M 上下文是否含在订阅内，model-config 只给了 4.6 系列的表【未证实，和 round3、round4 结论相同】。
  - Pro 的 Fable 是否全部走 usage credits：model-config 只写了"Depending on your plan and seat tier"【未证实】。

### 1.8 prompt cache TTL（5 分钟 / 1 小时）对订阅额度的影响

- 官方能确认的（[prompt-caching#which-ttl-each-request-gets](https://code.claude.com/docs/en/prompt-caching)）：订阅额度内，主会话用 1 小时，其余（子 agent、workflow、fork、压缩、标题）用 5 分钟；用 API 计费时，1 小时缓存写入"charges a higher rate"。
- 1 小时 TTL 的利弊，官方原话："It costs more on short bursts of work that never idle past five minutes"。
- **订阅额度里 1 小时写入和 5 分钟写入的权重是否不同：官方没写，社区也没有可靠测量【未证实】。**
- 社区有不少"Anthropic 偷偷改 TTL"的文章，例如 [lilting.ch](https://lilting.ch/en/articles/claude-code-cache-ttl-quota-exhaustion)、[propakistani 10-16](https://propakistani.pk/2026/10/16/claude-reportedly-resets-your-cache-to-charge-you-more/)，时间线互相矛盾，不采信。
- 结论不变：round3 R6 说的"subagentPromptCacheTtl 保持默认"仍然成立。

---

## 2. 其它工具里和省 token 相关的机制（给 H 做跨工具适配）

### 2.1 Codex CLI（本机 0.163.0，有实测）

**最重要的发现：本机 Codex 一直在 fast 档跑，fast 档按 2.5 倍消耗额度。**

- 本机 `~/.codex/config.toml` 里有 `service_tier = "priority"`【实测，只读】。官方 config reference 写明 "`fast` maps to the request value `priority`"，也就是说 priority 就是 fast 档（[config-reference](https://learn.chatgpt.com/docs/config-file/config-reference)【官方】）。
- 倍率，官方 Speed 页原文（[speed](https://learn.chatgpt.com/docs/agent-configuration/speed)【官方】）：
  - "For supported models, Fast mode uses included subscription limits at **2.5x** the Standard rate."
  - 用购买的 credits 时是 2 倍；Ultrafast 是 8 倍。
  - 支持 fast 的模型里有 GPT-6.1 Sol，而本机配置的 `model = "gpt-6.1-sol"` 正是这个模型。
- 我实测了两次最小调用（各 1 次，样本很少）：
  - 默认配置：rollout 里记录 `"service_tier":"priority"`。
  - 加 `--disable fast_mode`：rollout 里**没有** service_tier 字段（即标准档）。
  - `codex features list` 也确认 `--disable fast_mode` 或 `-c features.fast_mode=false` 会把这个功能变成 false。
  - 我**没有**测到"关掉之后额度百分比真的少涨"。2.5 倍这个数来自官方，没有本机回归数据。
- 现状：`tools/codex_task.sh` 和 `tools/codex_review.sh` 都没有覆盖 service_tier（我 grep 过）。也就是说，C 把复核改派给 Codex 的所有任务，都在按 2.5 倍消耗 Codex 额度。
  - 本会话里 Codex 的 5 小时窗口从 42% 涨到了 59%（rollout 里的 `rate_limits.primary`）【实测】，说明这一轮 Codex 用得很多。
- 其它 config.toml 选项（同一页 config-reference【官方】）：
  - `model_auto_compact_token_limit`、`model_auto_compact_token_limit_scope`（`total` / `body_after_prefix`）、`model_post_turn_compact_threshold_percent`
  - `compact_prompt` / `experimental_compact_prompt_file`
  - `tool_output_token_limit`、`mcp_servers.<id>.tools.<tool>.output_token_limit`
  - `skills.max_context_tokens`（默认是上下文窗口的 2%）、`skills.config`（按名字禁用 skill）
  - `agents.default_subagent_model`、`agents.default_subagent_reasoning_effort`、`agents.max_concurrent_threads_per_session`
  - `model_reasoning_summary`、`model_verbosity`
  - `tui.auto_recap`（默认 true，终端失焦时生成回顾）
  - `goals.max_goal_token_budget`
  - `memories.min_rate_limit_remaining_percent`（默认 25）
- AGENTS.md（[agents-md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)【官方】）：
  - 读取顺序：全局 `~/.codex/AGENTS.override.md` 或 `AGENTS.md`，然后从项目根往下逐层读。
  - 每层先找 `AGENTS.override.md`，再找 `AGENTS.md`，再找 `project_doc_fallback_filenames` 里列的文件名。
  - 多个文件拼接，合计到 `project_doc_max_bytes` 为止，**默认 32 KiB**，超出部分直接丢掉。
- hooks（[hooks](https://learn.chatgpt.com/docs/hooks)【官方】，本机 `features.hooks` = stable/true）：
  - 事件：`PreToolUse`、`PermissionRequest`、`PostToolUse`、`PreCompact`、`PostCompact`、`UserPromptSubmit`、`SubagentStart`、`SubagentStop`、`Stop`、`SessionStart`（source 取值 startup / resume / clear / compact / fork）。
  - **PreToolUse 可以用 `updatedInput` 改写命令**（Bash 时必须带字符串 `command` 字段），所以 test_filter 的"改写命令"路线可以直接移植。
  - **PostToolUse 不能替换输出**：`updatedMCPToolOutput` 只解析、尚未支持。
  - `additionalContext` 默认超过约 2500 token 就存到磁盘（`additionalContextLimit` 可调）。
- 用量查询：
  - `/status` 显示会话配置、token 用量和剩余上下文；`/statusline` 可以选 limits/tokens 等 footer 项；`/compact`、`/new`、`/fast` 开关（[developer-commands](https://learn.chatgpt.com/docs/developer-commands.md?surface=cli)【官方】）。
  - 【实测】rollout JSONL（`~/.codex/sessions/YYYY/MM/DD/*.jsonl`）里有 `rate_limits.primary`（`window_minutes: 300`）和 `secondary`（`window_minutes: 10080`）的 `used_percent` 与 `resets_at`，还有 `total_token_usage`（`input_tokens`、`cached_input_tokens`、`output_tokens`、`reasoning_output_tokens`）。**这些就是 Codex 版本的额度采样数据**，H 可以直接读。
- 【实测，1 个样本】一次只回答"OK"的 `codex exec` 用了 20,248 输入 token（其中 7,168 是缓存命中）。也就是说，**每次 codex exec 的固定开销约 2 万 token**，把很碎的小任务派给 Codex 并不划算。

### 2.2 Gemini CLI（未安装，全部来自文档）

来源：[docs/reference/configuration.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/configuration.md)、[hooks/reference.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/reference.md)、[commands.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/commands.md)、[token-caching.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/token-caching.md)、[gemini-md.md](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md)（main 分支）。全部【官方】。

- 指令文件：默认是 `GEMINI.md`。`context.fileName` 可以写成数组，比如 `["AGENTS.md","GEMINI.md"]`，这样可以和其它工具共用 AGENTS.md。`/memory show` 和 `/memory reload` 用来查看、重新加载。
- 压缩：
  - `model.compressionThreshold`（默认 0.5，即上下文用到一半就压缩）。
  - `/compress`（用摘要替换整个对话）。
  - 实验功能 `experimental.contextManagement`，配套 `contextManagement.historyWindow.maxTokens`（默认 150000）和 `retainedTokens`（默认 40000）。
- 工具输出：
  - `tools.truncateToolOutputThreshold`（默认 40000 字符）。
  - `model.summarizeToolOutput`（目前只支持 run_shell_command，可设 tokenBudget）。
  - 实验功能 `contextManagement.tools.outputMasking.*`：**对较旧的工具输出做遮蔽**，最近 5 万 token 受保护，可剪掉的部分至少 3 万 token 才会触发一次。
  - Claude Code 内部有"clearing old tool results"，但没有对外的设置（statusline 文档只在 `expected_rebuilds` 的定义里提到它）。
- 其它：`skills.disabled`、`tools.exclude`、`mcp.excluded`、`model.maxSessionTurns`。
- hooks：
  - `BeforeTool` 的 `hookSpecificOutput.tool_input` 会**合并进**原参数，所以能改写命令。
  - `AfterTool` 用 `decision: "deny"` 加 `reason` 时，**reason 会替换掉发给模型的工具结果**，可以做输出过滤，相当于 Claude Code 的 updatedToolOutput。
  - 另有 `PreCompress`、`BeforeModel`、`AfterModel`、`BeforeToolSelection`、`SessionStart`（`additionalContext`）。
- 用量：`/stats model` 显示 token 和配额信息。**token 缓存只对 API key / Vertex 用户有效，OAuth（个人 Google 账号）用户没有缓存**。所以在 Gemini OAuth 下，"缓存读取便宜"的前提不成立，长上下文的代价更直接。

### 2.3 Cursor（未安装）

- AGENTS.md：项目根和子目录都支持，嵌套文件在对应目录下生效。规则分四种：Always Apply、Apply to Specific Files（按 glob）、Apply Intelligently（按描述）、Apply Manually（@ 提及），只有 Always Apply 每次都加载。规则里引用的文件"not inlined into the prompt"。来源：[cursor.com/docs/context/rules](https://cursor.com/docs/context/rules)【官方】。
- hooks（[cursor.com/docs/agent/hooks](https://cursor.com/docs/agent/hooks)【官方】，页面没有日期，示例里写的版本是 1.7.2）：
  - 配置文件：`.cursor/hooks.json` 或 `~/.cursor/hooks.json`。
  - **`preToolUse` 的 `updated_input` 能改写 Shell 命令**；`beforeShellExecution` 只能 allow / deny / ask。
  - `postToolUse` 的 `updated_mcp_tool_output` **只对 MCP 工具有效**，另外可以用 `additional_context` 追加内容。
  - 有 `preCompact`、`sessionStart`（`additional_context`）、`subagentStart` / `subagentStop`、`beforeReadFile`。

### 2.4 Aider（未安装）

- 选项（[aider.chat/docs/config/options](https://aider.chat/docs/config/options.html)【官方】）：
  - `--map-tokens`（0 表示关掉 repo map）、`--map-refresh`、`--map-multiplier-no-files`（默认 2）。
  - `--cache-prompts`（默认关）、`--cache-keepalive-pings N`（每 5 分钟 ping 一次，保持缓存热）。
  - `--max-chat-history-tokens`（超过就开始摘要）、`--weak-model`（负责提交说明和历史摘要）、`--read FILE`（只读上下文文件，可以用它挂 AGENTS.md / CONVENTIONS.md）、`--subtree-only`。
- 对话内命令（[commands](https://aider.chat/docs/usage/commands.html)【官方】）：`/tokens`、`/drop`、`/clear`、`/reset`、`/read-only`、`/think-tokens`、`/reasoning-effort`。
- 我**没有**在 aider 官方文档里找到"自动读取 AGENTS.md"的说法，用 `--read AGENTS.md` 是稳妥做法【未证实是否原生支持】。

### 2.5 跨工具对照（H 用）

| 机制 | Claude Code | Codex CLI | Gemini CLI | Cursor | Aider |
|---|---|---|---|---|---|
| 指令文件 | CLAUDE.md；没有 CLAUDE.md 时读 AGENTS.md（v2.1.277+） | AGENTS.md，上限 32KiB | GEMINI.md；`context.fileName` 可加 AGENTS.md | AGENTS.md（可嵌套）+ rules | `--read` |
| 改写命令（test_filter 路线） | PreToolUse `updatedInput` | PreToolUse `updatedInput` | BeforeTool `tool_input` 合并 | preToolUse `updated_input` | 无 hooks |
| 替换工具输出 | PostToolUse `updatedToolOutput` | 不支持 | AfterTool deny + reason | 只限 MCP | 无 |
| 压缩前 hook | PreCompact | PreCompact | PreCompress | preCompact | 无 |
| 会话开始时注入（交接） | SessionStart（clear/compact/resume） | SessionStart（startup/resume/clear/compact/fork） | SessionStart | sessionStart | 无 |
| 额度读数 | statusline `rate_limits` | rollout `rate_limits.primary/secondary` | `/stats model` | 未查 | 无 |
| 本地日志统计 | transcript JSONL | rollout JSONL | ccusage 支持 | 未查 | 无 |

---

## 3. 社区工具和做法（数字都是作者自报）

| 工具 | 新信息 | 值得借鉴的 | 来源和日期 |
|---|---|---|---|
| ccusage | v20.0.48。一个 CLI 同时统计 Claude Code、Codex、Gemini CLI、OpenCode、Copilot CLI 等十几种来源；有 `weekly`、`blocks`（5 小时窗口）、`statusline`（beta）；`codex daily --speed fast` 会按 fast 计价 | H 可以先用 `bunx ccusage codex daily --json` 和 `ccusage gemini daily` 做跨工具统计，不必自己写解析器。注意：它算的是 API 价格，**不等于订阅额度** | [README](https://github.com/ryoppippi/ccusage/blob/main/apps/ccusage/README.md)，release 2026-11-02 |
| ccstatusline | v2.2.32–2.2.34：Block Forecast / Block Limit Timer（按最近的速度预测 5 小时窗口）、Weekly Limit Timer、Weekly Fable Limit Timer、Cache Timer 倒计时、Cache Misses / Cache If Cold；会丢掉已经重置的旧窗口（和 round4 F1 的修复思路一样） | **我们没有"按速度预测撞线时间"**。可以借鉴到 statusline 或守卫："按最近 30 分钟的速度，5 小时窗口约 X 分钟后用完" | [GitHub](https://github.com/sirmalloc/ccstatusline)，11-06 仍有提交，约 1.35 万星 |
| claude-hud | 插件形式的 HUD，显示上下文、额度、正在运行的工具和 agent；Node 进程做了权限隔离 | 无新的省额度机制 | [GitHub](https://github.com/jarrodwatts/claude-hud)，11-03 有提交 |
| Claude-Code-Usage-Monitor | 带预测的用量监视器 | 最后一次推送是 2026-07，可能已过时 | [GitHub](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) |
| claude-token-saver | 1.14.0（2026-09-22）。**PreToolUse 拦截对大文件的整文件 Read，返回结构大纲，让 agent 再按行范围读**；它说这样做是因为"editing tools need the original source bytes"，所以不在读之后改结果。可选的"大 Read 代理"（PostToolUse updatedToolOutput，默认关）；还指出 UserPromptSubmit 不能替换用户输入 | **和 D 的 read_guard 思路一致，可以对照它的阈值（min_tokens 2500）和"拒绝加大纲"的做法**。作者自己明说"不声称通用节省百分比"，诚实度较高 | [PyPI](https://pypi.org/project/claude-token-saver/) |
| caveman | 让 Claude 说得更少的 skill/插件，自称输出减少约 75% | 和官方 Concise 输出风格是同一方向；官方的更可靠 | [GitHub](https://github.com/juliusbrussee/caveman)，via [theburningmonk](https://newsletter.theburningmonk.com/posts/three-ways-to-save-on-claude-code-tokens)（页面只写"7 months ago"） |
| rtk-plugin、context-mode | round3 已写过。rtk-plugin 只是把 rtk 接进 hook，2 星 | 无新信息 | [rtk-plugin](https://github.com/enixCode/rtk-plugin) |

结论：社区这一轮真正新的点子有两个：**按速度预测撞线**（ccstatusline）、**拒绝大文件整读并给大纲**（claude-token-saver）。其余都已覆盖，或者是没有对照数据的自报数字。

---

## 4. 可行动清单

"每周省多少"的估算口径：用 round5_where_tokens_go 里套件启用后的数据，约 28.3M 单位/天，乘 7 约 198M 单位/周。**这个基数只有 1 天的数据，误差很大**。单位按 API 价折算，**不等于订阅额度**。

| # | 是什么 | 证据 | 估计收益 | 谁做 | 风险 / 要谁决定 |
|---|---|---|---|---|---|
| 1 | **Codex 调用关掉 fast 档**：`tools/codex_task.sh` 和 `codex_review.sh` 的 `codex exec` 加 `--disable fast_mode`（不改 `~/.codex/config.toml`，只影响套件的调用） | 强：官方 Speed 页写明 2.5 倍；本机 config 是 priority；加参数后 rollout 不再记录 priority【实测】 | 套件派给 Codex 的任务，Codex 额度消耗**约 -60%**（1 − 1/2.5）。按本轮约 70–90k token/次复核计算。代价是更慢 | C（codex_review.sh 归 C）；H 写进适配说明 | 低。所有者自己的交互式 Codex 是否也关 fast，由所有者决定 |
| 2 | **Opus 5.5 effort 从 high 回到默认 medium**（`modelSettings.claude-opus-5-5.effortLevel`） | 中：官方写明默认 medium，"medium matches or exceeds Opus 5 at high"，"Higher effort uses more tokens"；具体能省多少没有官方数字 | output 占成本 26%，思考 token 是其中一部分。【推测】省 output 的 10–30%，即总成本的 3–8%，约 6–16M 单位/周，**不确定性很大** | F2 先做受控对比（同一批任务 high vs medium），再由所有者决定 | 质量可能下降；这项设置可能是所有者有意选的 |
| 3 | 升级到 2.1.319 之前不用 `/context`（用 `/usage`），或者在任务间隙升级 | 强：changelog 2.1.319 明写修复了"每一轮都带上完整网格" | 每次误用省"网格大小 × 之后的轮数"（大小未测）。升级本身是一次性的整段缓存重建 | 写进使用说明（主会话或 B） | 无 |
| 4 | 关掉 claude.ai 连接器：`disableClaudeAiConnectors: true`，或者只对无头运行设 `ENABLE_CLAUDEAI_MCP_SERVERS=false` | 强（官方设置），收益中等 | deferred tools 加 MCP 说明约占 1.9%，约 3.8M 单位/周（估算）；子 agent 和 henchmen 也会带上这部分【推测】 | A（做成可应用、可撤回的补丁） | CLI 里不能再用 Gmail、日历、Docs，所有者决定 |
| 5 | 压缩 skill 列表：`skillOverrides` 把很少用的 skill 设成 `user-invocable-only`；考虑 `syncClaudeAiSkills: false` | 强（官方设置） | skill 列表占 5.3%。【推测】去掉同步来的 anthropic-skills 能减一半，约 5M 单位/周。换 Pro（200K 窗口）后还能避免 handoff 被挤到"只剩名字"（round4 A5） | A | syncClaudeAiSkills 会把文件移到 `.trash`，要先备份；所有者决定 |
| 6 | `includeGitInstructions: false` | 强（官方设置），收益未测 | 需要 A 用 `/context` 或 A_probe 实测大小【未证实】；附带好处是同目录前后会话更容易共享缓存【推测】 | A | Claude 不再自带提交规范；所有者本来就"只在要求时才提交"，影响小 |
| 7 | 评估 advisor：Sonnet 主会话 + Opus advisor，先用于长重构或通宵任务 | 中：官方明写"typically costs less than running the stronger model throughout"，但是实验功能，没有数字 | 【未证实】 | F2 做对比，C 写进 model-router 的备选项 | 质量和成本都要测；在大上下文里每次咨询都很贵 |
| 8 | 子 agent 在 5 分钟内续用（SendMessage），不新开 | 强：官方写明续用的第一个请求可以读原来的缓存 | 每次续用省约 2.1 万 token 的启动写入（round4 实测的均值） | C（model-router 规则） | 超过 5 分钟就没有这个好处 |
| 9 | 无头 / henchmen 运行用 Concise 输出风格（`--settings '{"outputStyle":"Concise"}'`），并考虑加 `--exclude-dynamic-system-prompt-sections` | 中：Concise 是官方功能，但收益没有数字；跨目录共享缓存是我根据官方说法的推测 | 【推测】减少 henchmen agent 的 output；并行多目录时每个 agent 少写约 2 万 token | B（先用两个 `-p` 实测 cache_read） | 报告变短，可能漏信息 |
| 10 | `permissions.denyInsteadOfPrompting`（2.1.319）用于通宵运行 | 中：只出现在 changelog，settings 文档还没有 | 避免卡在弹窗上等到缓存过期再整段重写 | B（升级后实测） | 被拒绝的操作可能让任务半途而废 |
| 11 | statusline 增加 `effort.level`、fast_mode 警示，以及"按速度预测 5 小时撞线时间" | 强（官方字段）加社区先例（ccstatusline） | 不直接省 token，帮所有者做决定 | 主会话指定（statusline 不在 A–H 的归属表里） | 状态栏变吵 |
| 12 | 跨工具：test_filter 移植到 Codex PreToolUse（`updatedInput.command`）、Gemini BeforeTool、Cursor preToolUse；输出端过滤只有 Gemini AfterTool 可以做 | 强（三家官方文档） | 视各工具的测试输出量而定 | H | Codex PostToolUse 不能改输出；Cursor 的 beforeShellExecution 不能改写 |
| 13 | 跨工具用量：H 直接读 Codex rollout 的 `rate_limits` 和 `total_token_usage`，或者用 ccusage v20 | 强（本机实测字段，加 ccusage README） | 让周报覆盖 Codex 的消耗（本轮 Codex 5 小时窗口已到 59%） | H | ccusage 的成本是 API 价 |
| 14 | 统一 AGENTS.md：确认 Claude Code 在 `Projects/` 下没有重复加载（原生加载加 hook 注入）；控制在 32KiB 以内（Codex 上限）；Gemini 设 `context.fileName` | 强（官方）加本会话现象 | 如果有重复，省 0.7% 左右 | A 核对，H 写适配 | 无 |
| 15 | read_guard 对照 claude-token-saver：拒绝大文件整读并返回大纲，阈值约 2500 token | 社区（作者诚实声明没有通用节省数字） | Read 占成本 7.6%，具体看 D 的实测 | D | 可能让 Claude 多读几次 |
| 16 | model-router 注明：大于 100K 上下文的任务不派给 Haiku 5.5 | 强（官方原文） | 避免误用 | C | 无 |

**没有列入清单的项**：
- `ENABLE_TOOL_SEARCH` 和 `CLAUDE_CODE_SIMPLE_SYSTEM_PROMPT`：默认值已经是最省的，不用动。
- `CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS`：model-router 要用 Explore。
- `subagentPromptCacheTtl`：订阅权重未知。
- Gemini 那样的"旧工具输出遮蔽"：Claude Code 没有对外设置，做不了。

---

## 总结

- **做了什么**：
  - 读了 Claude Code 官方文档 16 页以上（下载原文后 grep）、support 文章 2 篇、2.1.319 changelog。
  - 读了 Codex 官方文档 6 页、Gemini CLI 文档 5 页、Cursor 文档 2 页、Aider 文档 2 页、社区工具 7 个。
  - 本机只读核对了 settings.json、~/.claude.json、~/.codex/config.toml、Codex rollout。
  - **跑了 2 次最小的 `codex exec`**（用掉约 4 万 token 的 Codex 额度），用来确认 fast 档的开关。
- **最重要的 3 个发现**：
  1. 本机 Codex 一直跑在 fast 档（`service_tier="priority"`）。官方写明 fast 档按 **2.5 倍**消耗订阅额度，而套件的 codex 脚本没有覆盖它。加 `--disable fast_mode` 已实测可以切回标准档，预计套件派给 Codex 的任务能省约 60% 的 Codex 额度。
  2. 本机 Opus 5.5 的 effort 被设成 high（官方默认 medium，官方说 medium 已经足够）。这会直接影响占成本 26% 的 output，需要 F2 先测，再由所有者决定。
  3. 新的官方开关：`skillOverrides`、`syncClaudeAiSkills`、`disableClaudeAiConnectors`、`includeGitInstructions`，都针对 A 负责的固定开销（合计约 26.5%）；以及 advisor（Sonnet + Opus 顾问）。另外，2.1.319 修复了 `/context` 让上下文膨胀的 bug，本机 2.1.318 仍受影响。
- **数字**：这一节没有 token 节省的实测数字。上面的百分比来自官方倍率，或者 round5_where_tokens_go 的占比乘以假设，都是估算，基数只有 1 天的数据。
- **要所有者决定的事**：
  - 第 2 项：effort 改回 medium。
  - 第 4 项：关掉 claude.ai 连接器。
  - 第 5 项：syncClaudeAiSkills / skillOverrides。
  - 第 6 项：includeGitInstructions。
  - 第 1 项的延伸：要不要把所有者自己交互式 Codex 的 fast 档也关掉。
- **撤回命令**：本人只新建了这一个文件，没有修改任何已有文件或配置。测试 Codex 时生成的 rollout 在 `~/.codex/sessions/2026/11/06/rollout-2026-11-06T18-37-*.jsonl`，scratchpad 目录在 `/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/e_codex/`。要撤回本报告：`mkdir -p /Users/lihongrui/Documents/Projects/claude-usage-saver/_to_delete; mv /Users/lihongrui/Documents/Projects/claude-usage-saver/reports/round5_research.md /Users/lihongrui/Documents/Projects/claude-usage-saver/_to_delete/`
