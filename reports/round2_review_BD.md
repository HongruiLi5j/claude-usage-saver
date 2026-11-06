# 第 2 轮交叉审核：B（测量方法）与 D（遗漏手段）

审核日期 2026-11-06。方法：用 python3 对 exercism.jsonl 和 ~/.claude/projects 下的会话记录（排除 -private-tmp-）自己重算；设置项用 WebFetch 读官方文档（settings、settings-reference、env-vars、model-config、sub-agents；其中 settings-reference 和 model-config 页面超长被截断，只读到部分，凡没读到的标"未核实"）。没有花额度，没改任何项目文件。

## B 逐条核实

### B1 "Sonnet 省 50%"（成对 142 题，费用比 0.498，CI 0.487-0.510，142/142 更便宜）
- 数字：**成立**。我重算：总费用比 0.4978；按题自举 5000 次 CI 0.4865-0.5094（B 报 0.487-0.510，吻合）；Sonnet 更便宜 142/142；平均每题 $0.0986 vs $0.0491。
- "两边 token 几乎相同，省的是单价差"：**成立**。每题总 token 57.1k vs 56.6k；回合 4.0 vs 4.5；输出 812 vs 862；cache 读 47.2k vs 46.7k。对 cost 做回归得到的隐含单价，Sonnet 的每一类 token 恰好是 Opus 的一半。
- "订阅额度是否也按 2 倍计未知"：**成立（且是核心缺陷）**。cost 是 CLI 报的 total_cost_usd（API 标价），不是订阅额度。
- "质量没降不成立（天花板）"：**成立**。两边 142/142，只能说"没发现差异"，且 Sonnet 回合更多、耗时更长（12.8s vs 11.0s）。
- B 没说的补充：
  1. **每题只跑 1 次（rep=0）**，没有重复，无法估计同题随机波动；CI 是跨题的。
  2. **不是严格交错**：结果文件里 opus_plain 的 `week_used` 主要是 27/None，sonnet_plain 主要是 82/None，另有 opus_rest/sonnet_rest 分开的日志 → 两个臂有一部分在不同时间批次跑。价格比不受影响，但时间/并发/限流相关的指标（耗时、回合）被混淆。
  3. `week_used` 字段（取自 state/rate.json）出现 82 → 16 → 27 → 81 这种来回跳，与 A-1 说的"旧会话覆盖 rate.json"一致 → **run_exercism.py 的预算保护（BENCH_STOP_PCT / MAX_DELTA）依赖的数据本身不可靠**，这份基准的 week_used 不能当作额度消耗证据。
  4. 题目是公开的 exercism python 小题（3-5 回合、输出约 800 token），极可能被训练见过，只能代表"小任务"。

### B2 "new_sonnet 贵 55%"
- 数字：**成立**。26 题成对：费用比 1.551，CI 1.508-1.594，26/26 都更贵；平均每题多 cache 写 6457 token + 多 cache 读 17247 token（约 2.7 回合 × 6.4k）。
- "94% 来自每会话固定约 6.4k token 前置内容"：**部分成立**。按 API 标价我算 cache 写部分约占增量的 90-96%（取决于用的单价），固定前置约 6.4k token 成立；"94%" 这个具体数字对单价敏感，写成"约九成多"更稳。
- "26 题不是难题"：**成立**（26 题的 Opus 平均费用 $0.0974，与全体 $0.0986 几乎相同）。
- B 没说的补充：new_sonnet 与 plain 的差别不只是"skill/hook"——plain 臂用了 `--disable-slash-commands --setting-sources project`，把用户级 settings（hooks、statusLine、henchmen 插件、CLAUDE.md、记忆）一起关了。所以 +55% 是"整套用户配置"的总开销，不能全归给 usage-saver 的 hook/skill。要拆分需要用 /context（免费）看各部分 token，而不是再花额度跑基准。

### B3 "守卫省 41%"（模拟）
- 代码核对（replay_guard.py）：**"模拟"定性成立**。模型：触发后上下文变成 base+8000，之后按比例缩小；基线与阈值在同一批冻结数据上调。
- "过拟合/CI 29-50%/前 10 个会话占 56%"：**未复现**（需要 state/frozen.json 和自举，我没重跑；标"未核实"，但与代码结构一致）。
- B 没说的模拟缺陷：
  1. **完全没算 /clear 之后的重新探索成本**：交接只固定 8k，但真实情况模型要重读文件、重做 grep；这是守卫的主要真实成本，直接让 41% 偏乐观。
  2. 假设**不影响质量**（报告里写了，但"节省%"的标题没带这个前提）。
  3. 价格权重 cache 写 1.25 倍。我数了本机会话：cache 写入里 **95.3% 是 1 小时缓存**（65.6M vs 3.2M token），1 小时缓存应是 2 倍，混合约 1.95。cache 写的权重偏低，对"守卫后重新写 base+handoff"这一项的成本也偏低。
  4. 按"边界"才触发，且假设触发后下一条消息就能继续；没模拟 5 分钟缓存过期（用户离开后一次 cache 重写）。
- 结论：**部分成立**（作为上限有参考价值）。

### B4 "首日每次请求 -21% 不成立"
- **成立，而且比 B 说得更严重**。我重算复现了 29.56k vs 22.56k（周报 23.29k 是 09:31 UTC 时跑的，现在样本已增加）。关键事实：
  1. recent 窗口实际是 **2026-11-06 07:45-09:49 UTC，只有约 2 小时**（不是"1 天"，更不是"7 天"）。周报的 "有记录的天数: 1" 是个 date 集合，误导。
  2. 窗口"重叠"：代码里 before 是 [10-21, 11-06 00:00 UTC)，recent 是 now-7d 之后，但 `k` 的判断先判 before，所以数据上**不重叠**，recent 只剩 11-06 UTC 那一段。A-8 说的"重叠到 11-13"指的是定义上的重叠；实际结果是 recent 被前者"吃掉"，只剩 2 小时。**两位说法各对一半**。
  3. 子 agent 占比：recent 里 252/515 = 49% 的调用来自 subagents 路径，before 只有 1.4%。B 说"子 agent 上下文小拉低均值"：我算子 agent 每次 25.4k，**反而比主会话（19.9k）高**，所以这个机制说法**不成立**；真正的变化是主会话每次 19.9k vs before 29.7k（-33%），而主会话 recent 里还有大量 Sonnet（366 Sonnet / 139 Opus）。
  4. 单位不区分模型：before 里 Sonnet 调用就占 10205/19096（53%），用统一单位算 "Opus 占比 58%" 是 token 权重而非价格权重；按 Opus=1/Sonnet≈0.5-0.6/Haiku≈0.2 加权，每次请求成本 24.8k → 15.9k，变化幅度完全不同。-21% 这个数字没有任何解释力。
  5. 去重"保留第一行 vs 最后一行"（A-8）：我两种都算了，before 总成本差 0.001%，**影响可忽略**（建议降级为低优先级）。

### B5 其他 B 条目
- 质量测试建议（Aider Polyglot / SWE-bench 子集 + McNemar，10 个点差约 155 对）：**方向正确，数字未核实**。注意此类基准要花额度，需所有者批准。
- 每周对比方法（每条用户消息成本、按模型/主-子分开、ABAB、4-6 周）：**合理**。补充：目前 weekly_compare 的"请求"是 API 调用，不是用户消息；一条用户消息可含几十次调用，应以用户消息为分母。

## D 逐条核实

| # | D 的说法 | 结论 | 证据 |
|---|---|---|---|
| D1 | 默认模型改 sonnet 收益最大 | 部分成立 | 本机 settings.json 确实是 `"model": "opus"`。API 标价省一半有实测；订阅额度折算与质量均未知（见 B1）。涉及质量取舍，须所有者决定，不应写成自动措施 |
| D2 | agent frontmatter 加 `model:`；`CLAUDE_CODE_SUBAGENT_MODEL` 只是默认值，不影响内置 Explore/Plan | 成立 | sub-agents 文档：`model` 可取 `sonnet/opus/haiku/fable`、完整 ID 或 `inherit`；省略时按顺序：调用参数 > frontmatter > `CLAUDE_CODE_SUBAGENT_MODEL` > 主会话模型。该变量 v2.1.251 前会覆盖 frontmatter，现在不会；要强制需 `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`。文档原话："doesn't change the model the built-in Explore and Plan subagents run on"。5 个 agent 文件头部确实都没有 `model:`。**风险**：D 建议统计/QA agent 改 sonnet，与硬规则"不牺牲质量"冲突（质量未测量），只有搜索类改 haiku 较安全 |
| D3 | `autoCompactWindow: 200000` 兜底；Sonnet 5.5 约 967K 才压缩 | 部分成立 | 设置名、范围正确：数字，100000-1000000，"Default: unset, so Claude Code picks a window tuned for your model"，Any file。**967K 是文档里 Haiku 5.5 的数字**；Sonnet 5.5/Opus 5.5 的默认压缩点文档（读到的部分）未写明，标"未核实"。另有：env `CLAUDE_CODE_AUTO_COMPACT_WINDOW`（优先级最高）、`--autocompact`、`/autocompact`；v2.1.288+ 可在 `modelSettings` 按模型设置，并优先于顶层键。1M 默认窗口：Sonnet 5 及以后、Opus 4.7 及以后默认 1M（文档原话）。`CLAUDE_CODE_DISABLE_1M_CONTEXT=1` 可用 200K |
| D4 | idleCompaction 保持默认（未核实默认值） | 现已核实，成立 | settings-reference：Boolean，**默认 `true`**，需 v2.1.290+（本机 2.1.318，满足）；作用："Your next message then sends a summary instead of the whole conversation"；阈值 env `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` 默认 200000（下限 100000）；`autoCompactEnabled=false` 或 `DISABLE_AUTO_COMPACT` 会连带关掉它。它本身就是省额度机制（缓存过期后不重读整段对话），但有损——应与守卫的 160k 配合：守卫先触发则它基本不会出现 |
| D5a | `awaySummaryEnabled: false` | 部分成立 | 设置名存在（索引行："Turn off the session recap shown when you come back to the terminal"，Any file）。详细条目没读到 → 默认值（推测 true）、类型**未核实**。节省量很小；关掉会改变"回来时的提示"体验（不是回复风格） |
| D5b | `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` | 成立 | 文档：设为 `1` 禁用终端标题自动更新，同时跳过后台小模型生成会话标题的请求，并关闭向终端的状态报告。副作用：标题不再更新、会话命名受影响。节省极小 |
| D5c | `CLAUDE_CODE_GOAL_CHECKIN_MINUTES=0` | 成立但无用 | 文档：默认 30，0 关闭，需 v2.1.234+。只在用 `/goal` 且有后台任务时才有影响；若不使用 /goal 则无收益。**关掉会失去"检查后台任务"的提示，对长任务可能是负面**，不建议默认关 |
| D6 | 常驻上下文已小（1-1.5 万） | 部分成立 | 与 B2 自己的数据冲突：基准里 new_sonnet 比 plain 多 6.4k，plain 的 cache 读平均约 47k/4 回合 ≈ 12k/回合；加上 6.4k 用户配置，常驻约 1.8 万。另外本会话可见的 skill 列表就有约 25 项，每轮都携带 |
| D7 | `/usage` 按 skill/agent/插件分解 | 未核实（没读该页），不予采信 |
| D8 | Sonnet 5.5 思考关不掉；effort 不动 | 未核实；与硬规则一致 |

## 新发现（B、D 都没提）

测量缺陷：
1. **cache 写入权重用 1.25，但本机 95.3% 的 cache 写入是 1 小时缓存（2 倍）**：weekly_compare.py 和 replay_guard.py 都低估 cache 写成本约 35%，守卫模拟的"交接重写成本"、周报的费用单位都受影响。（A-8 提到 1 小时 2 倍，但没量化占比。）
2. **周报单位不区分模型价格**：Sonnet/Haiku 与 Opus 同价计入，所以"Opus 占比 58%→20%"和"每次请求成本"都不是钱，也不是额度。建议至少加按价格加权的第二列。
3. **周报的分母是 API 调用，不是用户消息**；一条消息的调用数会随工作方式变化。
4. recent 窗口只有约 2 小时，且包含当天夜间大量 subagent；before 里包含 11-04 的 5653 次调用（Opus，sandbox overnight）等极端日 → 均值被少数日子支配。应报告日级中位数和主会话/子 agent 分列。
5. 基准的 `--max-budget-usd 1.5` 和 week_used 保护数据不可靠（见 B1-3）。
6. 没有任何**订阅额度的直接测量**。办法：用 rate.json 的 five_hour / seven_day 的 used_percentage 在"静止窗口"内跑一批 Opus 与 Sonnet 小任务，看百分点变化（精度只有整数百分点，需要足够多的任务，并且必须保证无其他会话在跑；又要花额度，需所有者批准）。

省额度手段（不违反"质量/effort/回复风格"硬规则）：
1. **model 切换会让整个缓存失效**（settings 文档："Each model has its own prompt cache, so the first request after a switch re-reads the whole conversation uncached"）。所以：不要在长会话中途 /model 来回切；切换应在 /clear 之后。可写进使用说明。
2. **5 分钟缓存过期**：离开超过 5 分钟再回来，下一轮是重读整段上下文。这正是 idleCompaction（默认开，200k 起）在做的事；对大上下文，主动 /clear 比等它更省（守卫已做）。
3. **审计常驻开销**：henchmen 插件 + 大量 skill 描述每轮都在上下文里；用 `/context`（免费）查看各部分 token，再决定是否禁用不用的插件/skill 描述（不影响质量，因为没用到）。model-router 描述过宽（C-8）同理。
4. 守卫之外的上下文来源：大文件读取、长工具输出（test_filter 已处理测试）；可通过 `permissions.deny` 禁读大日志/数据目录（需所有者确认路径）。
5. 内置 Explore 在订阅账户下，主会话是 Fable 时会跑在 Opus；用户可建同名 `Explore` agent 并设 `model: haiku` 覆盖（文档明确允许）。这对搜索类最安全，但"haiku 搜索质量"需所有者同意。

## 分歧

| 议题 | B | D | 我的判断 |
|---|---|---|---|
| 默认模型 | 省的只是 API 单价，质量未测，订阅额度节省未知 | "最大收益"，实测省 50% | B 对；D 的 50% 只对 API 标价成立。必须交所有者决定 |
| 子 agent 用 sonnet/haiku | 未涉及质量 | 统计/QA 用 sonnet、搜索用 haiku | 搜索类 haiku 可行；统计/QA 违反"不降质量"前提，除非先测 |
| 常驻上下文 | new_sonnet 多 6.4k（占 +55%） | 已很小不值得再削 | 两者相容但结论相反：对小任务显著，对长会话占约 6%。应以 `/context` 数据为准 |
| 压缩时机 | 守卫模拟 41% | autoCompactWindow 200k 兜底 + idleCompaction 默认 | 二者互补；注意 idleCompaction 默认开且有损，不要说"没有压缩" |
| "首日 -21%" | 不成立（子 agent 拉低均值） | 未涉及 | 结论成立，机制说法不成立（子 agent 单次更贵）；真实原因是窗口只有 2 小时、模型构成变化 |
| recent 窗口重叠 | 说窗口只有 1 天 | — | 与 A-8"重叠到 11-13"：定义重叠、数据不重叠（先判 before），所以只剩 2 小时 |

## improvements.md 需改的措辞

基线表逐行（原文 → 建议）：

1. "小型编码题每题费用：Opus → 纯 Sonnet | $0.099 → $0.049（-50.2%），通过率都是 142/142 | 实测"
   → "小型编码题（exercism，142 题，每题 1 次）按 API 标价：Opus $0.099 → 纯 Sonnet $0.049（费用比 0.498，CI 0.487-0.510）；两边 token 几乎相同（约 5.7 万/题），差异完全来自单价；通过率都是 142/142，天花板效应，**质量差异未能测量**；订阅额度的实际节省未知 | 实测（API 标价，非订阅额度）"
2. "Sonnet + skill/hook 比纯 Sonnet | +55%（26 题）"
   → "Sonnet 加上全部用户级配置（hooks、插件、CLAUDE.md、记忆等；对照臂关闭了这些）比纯 Sonnet 贵 55%（26 题，CI +51%~+59%），约九成来自每会话固定多出的约 6.4k token 前置内容；只在约 4 回合的小任务里显得大，在 10 万 token 级真实会话里约占 6% | 实测，样本小，不能全归因于 usage-saver"
3. "上下文守卫 | 模拟省 41.1%（阈值 160k）| 模拟"
   → "上下文守卫：在 473 个历史会话上回放，**假设**每次交接只多 8k token、不影响质量、不含 /clear 后重新探索成本，容易省下的上下文上限约 30-50%（阈值在同一批数据上选出，缓存写权重按 1.25 而实际多为 1 小时缓存）；真实效果未知 | 模拟"
4. "每次请求成本（真实日常）| 29.6k → 23.3k（-21%）| 实测，只有 1 天新数据"
   → 删除这行，或改为："对比无效：'最近'窗口只有 2026-11-06 约 2 小时（约 500 次调用，一半是子 agent），单位不区分模型价格，且当天模型构成变了；仅作记录，不作为效果证据 | 无效"
5. "每次请求上下文中位数 | 140k → 109k（-22%）| 同上"
   → 同上标"无效"；等有 ≥4 周、按主会话/子 agent、按模型分列的数据再填。
6. "测试输出给 Claude 的行数 | 313 行 → 约 30 行（-90%）| 实测，一次真机测试"
   → 加上"单次、单个测试套件；样本 n=1；test_filter 另有已修复的误包装 bug"。（此行我没有重算，未核实。）

另：improvements.md 开头"模拟与实测分开写"的原则很好，建议在每个数字后注明"单位：API 标价折算，不是订阅额度"；还应补一行"未知项：订阅额度换算比、质量差异"。
