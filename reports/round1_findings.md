# 第二轮优化 · 第 1 轮调查结果（2026-11-06）

A（代码，Opus）、B（测量，Opus）：待到。C（可用性/质量，Sonnet）、D（遗漏手段，Sonnet）：已到，要点如下。

## 需要所有者决定
- **默认模型**：`~/.claude/settings.json` 现在是 `"model": "opus"`（推测是所有者在会话里 /model 切换后被保存；使用说明写的"默认 Sonnet"因此不成立）。5 个自定义 agent（~/.claude/agents）都没有 `model:`，跟随主模型 → 现在都跑 Opus。选项：改回 sonnet（实测小任务省 50%）/ 保持 opus 并改文档 / `opusplan`。

## C：可用性与质量风险
1. 使用说明"默认 Sonnet"与设置不符（见上）。
2. session_resume：任何下一条消息都被当"接着做"，会劫持新问题；无过期检查，每次 /clear 都重注入；向上找到 HOME，会捡到无关/过期 HANDOFF.md。修：注入时附文件修改时间和天数；"消息明显是新问题就按新问题做"；skill 改为覆盖写并带日期标题。
3. 截断在 8500 字符，红线/未决在最后一节会被截掉；skill 限 60 行与强提醒"不限行数"矛盾。修：节顺序改为 目标→红线/未决→下一步→进度→文件；上限约 120 行；截断保留头尾。
4. mode.json 全局共享，多会话时状态栏 ⚠ 用错别的项目的线。修：按 session_id 分。
5. 守卫只读 transcript 最后 400KB，读不到 usage 就静默不提醒。修：回退用 rate.json 的 ctx_tokens 或扩大窗口。
6. 测试过滤：-A6/head -80 可能截掉长 traceback 中段；`pip install pytest`、`which pytest` 也会被匹配；整条 `a && pytest; b` 被包装。修：-A12、head -150，只匹配以测试命令开头的段。
7. 使用说明缺：怎么判断在工作、怎么临时关掉、额度快完时怎么办；"紧张"定义不精确（差 >5 点）；撤回命令只恢复设置不撤 skill。
8. model-router 描述太宽（几乎每轮加载）；强提醒措辞"本次回答中说明"可能打断工作 → 改为"做完这一步后"。
9. quant-backtest-guide：23 个路径核对全部存在；建议加"过滤后不够就单独重跑该文件"。

## D：遗漏的省额度手段（官方文档核对过）
1. 默认模型 sonnet（同上，最大收益）。
2. 自定义 agent 加 `model:`（统计/QA 用 sonnet，搜索类 haiku）；`CLAUDE_CODE_SUBAGENT_MODEL` 只是默认值，不影响内置 Explore/Plan。
3. `autoCompactWindow: 200000` 作为兜底（Sonnet 5.5 原生 1M 窗口，否则约 967K 才自动压缩）；守卫 160k 仍先触发。压缩有损。
4. idleCompaction 保持默认（未核实默认值）。
5. 小的后台消耗：`awaySummaryEnabled: false`、`CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1`、`CLAUDE_CODE_GOAL_CHECKIN_MINUTES=0`。
6. 常驻上下文已经很小（约 1–1.5 万 token，主要是系统提示），不值得再削。
7. 定期看 `/usage`（按 skill/agent/插件分解，按 f 应用建议）。
8. Sonnet 5.5 思考关不掉；effort 不动（质量优先）。

## B：测量方法（Opus）
- **"Sonnet 省 50%"**：成对 142 题，费用比 0.498（95% CI 0.487–0.510），142/142 题 Sonnet 都更便宜。但两边 token 几乎相同（Sonnet 回合反而多 4.5 vs 4.0），省的完全是 API 单价差（Opus 恰好 2 倍）。订阅额度是否也按 2 倍计 Anthropic 未公开 → 对额度的真实节省未知。"质量没降"不成立：两边都 100%，天花板效应，测不出。诚实说法："简单题上 token 一样、API 价格一半，质量未能测量。"
- **"new_sonnet 贵 55%"**：成立（1.551，CI 1.508–1.594），94% 来自每会话固定约 6.4k token 的前置内容（skill/CLAUDE.md/hook）。只有约 4 回合的玩具题才显得大；真实 109k 上下文里只占约 6%。那 26 题也不是"难题"。
- **"守卫省 41%"**：模拟。交接成本 8k–60k 时 41–35%；按会话 bootstrap CI 29–50%；前 10 个会话占 56% 成本；阈值在同一批数据上调出来（过拟合）。诚实说法："容易省下的上下文上限约 30–50%，真实效果未知。"
- **"首日每次请求 -21%"**：不成立。recent 实际只有 11-06 一天，且当天大量子 agent（上下文小）拉低均值；单位不区分模型，看不到 Opus 占比从 58% 降到 20% 的影响。
- **下一步质量测试**：Aider Polyglot（225 道难题）或 SWE-bench Verified 子集 100–150 题；成对 McNemar、随机顺序、交错批次、同并发；测 10 个点差距需约 155 对。
- **每周对比方法**：主指标"每条用户消息的成本"，按模型分开；主会话与子 agent 分开；按项目分层；只用完整天；守卫开/关交替（ABAB，每段 3–4 天），至少 4–6 周才下结论。
- **改进记录应跟踪的指标**：每周按主会话/子 agent 分：每条消息成本（Opus/Sonnet 单位）、各类 token、上下文 p50/p90、/clear 次数、clear 后 5 回合的重读 token、前 10 会话成本占比；质量代理：返工/撤回率、所有者纠正次数。

## A：代码（Opus）
1. **rate.json 异常值的原因（已复现）**：几个闲置的旧会话（10-31、11-01、11-02 启动，Sonnet）状态栏每 5 秒用旧的 rate_limits 覆盖 rate.json，并刷新 ts，过期检查失效 → 档位误判"宽裕"，软线 160k→240k。修：同一 resets_at 取最大用量；没有 five_hour 的条目忽略；或按 session_id 各存一份取最大。
2. **test_filter 破坏合法命令（已复现）**：`pytest -q  # 注释` 语法错误、测试根本没跑；`pytest &` 出错/空日志。→ **已紧急修复**（命令单独一行、结尾 & 不包装），备份 backups/test_filter.py.pre-hotfix。
3. **非测试命令被误过滤（已复现）**：`grep pytest f`、`git commit -m "fix pytest"`、`pip install pytest`、`which pytest`。修：只在命令位置匹配。
4. **session_resume 注入无关交接（已复现）**：在 quant-backtest、relay-room 里会捡到 ~/Documents/Projects/HANDOFF.md。修：停在项目根（.git 或 Projects 下第一层），超过约 24 小时的不注入。
5. mode.json 全局共享，状态栏 ⚠ 用别的会话的线（同 C-4）。修：按 session_id。
6. `pytest; exit 3` 无输出；--pdb/watch 模式会卡住无输出（推测）；testlogs 不清理；`.venv/bin/pytest` 没匹配到。
7. statusline 所有会话共用同一个 rate.json.tmp；mode.json 不是原子写入。修：`name.<pid>.tmp` + os.replace。
8. weekly_compare：recent 与基线重叠到 11-13（首份报告只算到 1 天）；去重保留每个 id 的第一行，可能少算输出，应保留最后一行；1 小时缓存写入应为 2 倍不是 1.25 倍。
9. context_guard 只读最后 400KB，单行 1.36MB 的工具结果会让它找不到 usage 而静默（推测）。
- 延迟：各 hook 中位数 14–19ms，最大 34ms，不是问题。
- 做得好的：全部出错放行；退出码保留；状态栏原子写；守卫只读有限尾部。
