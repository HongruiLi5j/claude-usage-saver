# 第 5 轮 K：自由探索（A–J 方向以外的机会）

日期 2026-11-06。只新建了文件，没有修改任何已有文件或配置。transcript 只做了统计（usage、工具名、时间、字段计数），报告里没有抄对话正文。

## 0. 口径

- **数据**：`~/.claude/projects` 下的 transcript，排除 `-private-tmp-`、.secret、sealed、test2，按 `message.id` 去重。
  - 全量窗口（10-21 PST 起）：567 个文件，20,766 次请求，646.2M 单位。
  - 启用后窗口（11-05 23:58 PST 起，约 18 小时）：67 个文件，1,969 次请求，42.8M 单位。
- **成本单位**和 weekly_compare 一样：cache_read×0.1，5 分钟写入×1.25，1 小时写入×2，未缓存 input×1，output×5。
- **每周外推**：沿用 round5_where_tokens_go 的基准 198M/周。这个基准只有约 1 天样本，所以下面所有"每周 xM"都只是数量级。
- **标注**：【实测】表示直接从 transcript 数出来的；【估算】会写出计算过程。
- **脚本**：`tools/round5/K_patterns.py`，跑全量约 5 秒，加 `--post` 只看启用后。一次性的探查脚本在 scratchpad `/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/r5K/`（bust.py、rej.py、subgap.py、breakeven.py、byproj.py、perreq.py），只出数字。

---

## 1. 机会清单（按估计收益排序）

### K1. 套件自己的开发成本：给每一轮定预算、算回本账
- **证据【实测】**：启用后不含 tmp 实验的 50.9M 里，cwd 在本套件目录或本会话 scratchpad 的请求占 **47.2M（93%）**。所有者其它工作合计只有 3.7M（165 次请求）。如果把 tmp 里的无头实验也算上：本会话（第 4–5 轮主会话加子 agent）25.0M，无头实验 20.6M，合计约 **45.6M**，约占启用后总量的 64%。
- **和 F1 的分工**：F1 已经指出"启用后 100% 是本项目自己的改进轮，前后不可比"，讲的是测量口径。这里讲的是另一件事：**开发本身就是现在最大的额度消耗**。
- **回本账【估算】**：
  - 所有者正常工作（不含 AQ 通宵）按 15–20M/天算；假设套件能省其中 10–20%，每周省约 10–28M。
  - 第 5 轮到目前约 46M，而且还在跑。回本需要 1.6–4.6 周。
  - 11-21 换 Pro 以后，周额度小很多（具体倍数未知）。照这个规模再跑一轮，可能直接吃掉一整周额度。
- **做法**：
  - 每轮开跑前写三项：预算（M 单位）、预期每周节省、回本周数。超过预算就停下来汇报。
  - 回本超过 4 周的方向不做。
  - 复核、调研默认交给 Codex（C 的方向）。
  - 这本账可以直接用 `K_patterns.py` 按 cwd 归属算出来（见第 3 节第 2 条）。
- **收益**：如果第 6 轮起每轮规模减半，**每轮省约 20–25M**。这是本清单里最大的一项，但要所有者拍板。
- **成本/风险**：不用写代码；代价是改进变慢。所有者说过"本轮不用省"，所以这条从第 6 轮或 Pro 以后开始执行。

### K2. 撞上限额之后的"冷恢复"
- **证据【实测】**：
  - 全量 65 次 `usage_limit_reached`，其中 51 次来自 AQ-sandbox-1104（11-04 02 时和 05 时，PST）。
  - 9 个并行会话一起撞上限，1.9–4.6 小时后一起恢复。恢复后的第一次请求要把 350k–820k 的上下文按 2× 整段重写。
  - 这样的事件有 14 个，多付 **14.9M，占全量成本的 2.3%**，是 K_patterns 第 9 节里最大的一类冷重写。
- **为什么前面没人看到**：where_tokens_go 把它算进了"空闲超过 1 小时后的冷重写"。B 的做法是上下文封顶和空闲压缩，管的是"闲着"；但这里的触发点是**撞限额**，等待时间由 5 小时窗口决定，不由人决定。
- **收益【估算】**：
  - 现在有 300k 上限：每个事件最多多付 (300k−50k)×1.9 ≈ 0.48M。一次通宵大约 9 个会话 × 1.5 次撞限，约 6.4M；能避免 60–80%，**每次通宵约省 4–5M**。
  - 换 Pro 以后（200k 上限）：每个事件最多约 0.29M。按每周 3–7 次撞限算，每周省约 0.6–1.5M。
- **做法**（待验证）：
  - 交互会话：5 小时用量 ≥90%（rate.json 里有）时，context_guard 注入一次"现在写交接；窗口重置后先 /clear 再继续"。
  - 无人值守会话：要先核实撞限后 Claude Code 的自动恢复（quota_auto_resume）和空闲压缩怎么配合。冷缓存时压缩按 1× input 读一遍，比 2× 重写便宜，**但没实测过**。
- **风险**：中。和 B 相邻，要请 B 确认有没有重复。通宵会话的行为没有在本机验证过。

### K3. 子 agent 的 5 分钟缓存过期：长时间等待和过期后续用
- **证据【实测】**：
  - 子 agent 的缓存写入全部是 `ephemeral_5m`，主会话是 1 小时。
  - 启用后有 7 次子 agent 的整段重写（K_patterns 第 9 节）：
    - 5 次发生在前台长命令之后（4 次是内联 python 实验，等了 9.5–26.5 分钟，上下文 135–150k）；
    - 2 次是交回后又被 SendMessage 续用，间隔 5–7 分钟。
  - 合计多付约 **0.99M，占启用后成本的 2.3%**。全量只有 1.33M，说明这是第 5 轮才冒出来的模式：agent 在跑 `claude -p` 实验和 `codex exec`。
- **反直觉的一点**：HANDOFF 写着"打回时优先 SendMessage 原实施者（有上下文更省）"，C 也加了"5 分钟内用 SendMessage 续用"。但打回通常发生在 Codex 复核**之后**（10 分钟以上），那时缓存一定已经冷了。
  - 续用一个上下文 C 的 agent，要多付约 C×1.15。
  - 新开一个 agent 约 21k×1.25，加上重读需要的文件 R×1.25。
  - C ≥ 80–100k 而且 R < 50k 时，**新开 agent 更便宜**。
- **做法**（给 C 的 model-router 加两句话，K 没有写权限）：
  1. 子 agent 等长任务时，每次最多等 4 分钟（Bash 超时 240000 的 until 轮询）。每轮询一次，缓存就续上一次。或者让主会话（1 小时缓存）去等。
  2. 子 agent 交回超过 5 分钟、上下文超过 8 万时，不要 SendMessage 续用，改开新 agent，给一份短的修复清单。
- **收益【估算】**：一次轮询约花 C×0.1 + 约 1k output×5。150k 上下文时约 16k，而一次重写约 172k。等 10–26 分钟能省 75–90%。考虑到有些等待避免不了，按 60–80% 算，约省启用后成本的 1.4–1.8%，**每周约 3–3.5M**。C 把更多活改派给 Codex 后，这个数会变大。
- **风险**：低，只是规则。轮询会多出几次请求，等待短于 5 分钟时没有收益。

### K4. 多个子 agent 重复读同一批背景文件
- **证据【实测】**：
  - 启用后，被至少 3 个子 agent 读过的文件，重复部分合计约 **223k token**（只算第一次读入）。
  - 最多的几个：session_resume.py（9 个 agent，81k）、round5_where_tokens_go.md（4 个，47.5k）、context_guard.py（8 个，31k）、test_filter.py（8 个，27k）、scorecard.py（3 个 agent 读了 8 次）。
- **成本【估算】**：每份重复内容要付 1.25（写入）+ 0.1×约 25 次后续请求 ≈ 3.75 倍，合计约 0.84M，**约占启用后成本的 2.0%**。
- **做法**：一轮派多个 agent 时，父会话先写一份"共享简报包"：≤3k token，放关键事实和 `文件:行号` 索引，各 agent 先读它，需要时再按行号读原文。也可以做一个自动生成的代码地图（每个文件的函数、行号、一句话说明）。
- **收益【估算】**：重复读入减少一半，约省 1%，**每周约 2M**（按多 agent 轮次的工作方式算；普通单会话工作基本没有这项）。
- **风险**：简报过期或写错，agent 会漏掉细节；靠行号指针和"关键处读原文"来兜底。和 D 的区别：D 管的是单次读取的大小，这里管的是**跨 agent 的重复**。

### K5. "清理回本"：只在回得了本的时候建议 /clear（Codex 独立提出，我用数据核实）
- **模型【估算】**：在上下文 c 时清理，一次性成本 R ≈ 160k 单位，包括交接 output、新会话前置写入、接续注入、重读文件。之后每次请求省 (c − 50k)×0.1。回本需要的请求数 N\* = R / ((c − 50k)×0.1)：c = 160k 时约 14.5 次，c = 300k 时约 6.4 次。
- **数据【实测剩余请求数 + 估算收益】**：
  - 全量：87 个主会话第一次越过 16 万，越线后剩余请求的中位数是 30。其中 **32 个（37%）清理了会亏**，合计亏 3.0M；会赚的合计 96.6M。
  - 启用后：6 个会话，剩余中位数 13，其中 3 个会亏（0.18M）。
- **收益**：token 上最多是守卫收益的 3%（约占总成本 0.5%，每周 ≤1M）。主要好处是体验：约三分之一"不值得"的 /clear 提醒可以不发。另外它是简历里最好讲的一个数学点（见第 4 节）。
- **做法**：守卫提醒里写上"按当前上下文，再有约 N\* 次请求才回本"，如果任务快结束就不必清理，让 Claude 和所有者自己判断。不要自动预测剩余请求数。
- **风险**：低。只是改提醒文字；判断错了的代价是少清理一次。

### K6. 一键诊断 `doctor`【原型已完成】
- **做什么**：一条命令回答三件事："套件装好了没有""它在不在工作""它会不会拖慢 Claude"。详见第 2 节。
- **实测发现**：用它在真实 transcript 里找到 1 次历史事故。11-06 01:46（PST）test_filter.py 在被边改边用时出现 SyntaxError（f-string 没闭合），Claude Code 把它当成非阻塞错误。测试命令照常跑了，只是没有被过滤。因为 hook 是 fail-open 的，没有改坏任何命令，但那段时间的节省没了。
- **收益**：不直接省 token，价值在于防止部件**静默失效**，同时也是可以演示的工程能力。
- **成本/风险**：已完成；只读，hook 都在沙箱副本里跑。

### K7. 改 hook 的安全发布流程
- **证据**：K6 发现的那次事故。第 5 轮有 12 个 agent 同时在改 `hooks/` 下正在生效的文件：文件写到一半时，每一次 Bash 调用都会触发它。
- **做法**：先改副本，跑 `python3 tools/round5/K_doctor.py --kit <副本目录>`（已支持）和测试，再用 `mv`/`os.replace` 一次性替换。把这条写进 brief 的红线。
- **收益**：可靠性。成本几乎为零。

### K8. 统一测试入口 `K_testall`【原型已完成】
- 自动发现并运行散在各处的 9 套测试，输出一张汇总表，见第 2 节。
- **收益**：可维护性。一条命令就能做回归，也方便 Codex 复核。
- **风险**：零（只读运行）。

### K9. 统一成本口径模块
- **证据【实测】**：有 **14 个脚本**各自写了一遍成本权重（weekly_compare、scorecard、replay_guard、bench/*、round5 里的 r5、C_measure、D_read_stats、B_sim、F1/extract、sim、K_patterns 等）。第 2 轮就出过口径漂移：weekly_compare 改成了 1 小时写入 ×2，replay_guard 还是 ×1.25。
- **做法**：新建 `tools/costmodel.py`（一个 `cost(usage)` 函数），新脚本都用它，旧脚本由各自负责人逐步迁移。
- **收益**：可维护性，避免数字前后不一致。
- **风险**：低，只要不一次性改所有旧文件。

### K10. Claude 侧的一键安装 / 卸载
- **现状**：有 install_sessionend.py、install_limit_logger.py、pro_switch_1121.py 三个单项安装器；H 的 adapters/apply.py 只管其它工具；README 的 Install 一节只有一句"已经在 settings.json 里注册"。
- **做法**：`install.py --dry-run / --apply / --uninstall`，幂等，改前先备份，自动识别套件目录。注册检查可以直接复用 doctor 的 settings 解析。
- **收益**：别人能装上用（README、简历都需要）。不省 token。
- **风险**：要动 settings，只能在临时 settings 上做原型，**应用前要问所有者**。没有做。

### K11. 可视化面板
- 把 K_patterns 和 weekly_compare 的 JSON 渲染成一页 HTML：成本瀑布、上下文时间线、冷重写事件、doctor 状态。面试演示用。没有做。

### K12. 子 agent 报告的长度预算
- **证据【实测】**：启用后 Write 写入的正文约 455k token，按 output 价格 ×5 是 2.27M，**占 5.3%**。第 5 轮每个 agent 都写长报告，而且会被很多 agent 反复读。
- **做法**：给 agent 的报告定格式，开头一页结论加数字，细节放附录。所有者要求的"回复要详细"指的是**对所有者的回复**，不受影响。
- **收益【估算】**：约 1–1.5%。
- **风险**：报告不够细，复核时要多读源码。优先级低。

### 用数据否掉的（不值得做）

| 想法 | 数据【实测】 | 结论 |
|---|---|---|
| hook 延迟/开销（Codex #17） | 沙箱里每个 hook p50 16–31ms；状态栏每 5 秒跑一次，每次约 28ms，约占 0.6% 的一个 CPU 核 | 不用优化 |
| 权限提示被拒 | 全量 966 次 user-rejected，**全部在 11-01 之前**（那段时间的 permission-mode 字段为空，原因没查），之后只有 2 次 | 已经不是问题 |
| 所有者纠正 / 重来 | 1,603 条所有者消息里只有 11 条含纠正关键词（0.7%），其后整轮成本 0.4% | 不是杠杆 |
| Edit 匹配失败、文件未读 | Edit 未匹配全量只有 4 次 | 可以忽略 |
| effort high 改成 medium | 子 agent·Opus 每次 output：high 1,184，medium 1,381；每次成本 21.8k 对 21.1k | high 并不更贵，不是杠杆 |
| 一天中的时段 | 00–05 时（PST）占全量 46%，但那就是通宵运行 | 归 B |
| 主会话 5 分钟内的缓存失效 | 全量 108 次，0.9%。其中 56 次和 MCP/deferred tools 变动同时发生，5 次和中途换模型同时发生 | 很小。顺带记一条：会话中途别连接或断开 MCP（换模型的提醒已经写进使用说明） |
| 测试失败后的重试循环（Codex #10） | 启用后测试失败率 11.5%；失败之后下一次请求的成本占 1.5%；连续失败再跑只有 13 次 | 能拦的 ≤0.5%，暂不做 |
| session_resume 注入去重（Codex #3） | session_resume 注入本身只占启用后 0.9% | 去重最多省约 0.2% |

### Codex 独立头脑风暴（只给了 README 和目标），合并去重的结果
Codex 共给了 29 条，日志在 scratchpad `/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/r5K/codex_brainstorm.md`。
- **并入本清单**：#1→K5，#20→K10，#21→K6（已做），#23→K11，#24→K9，#26→K8（部分），#27→K1，#29→第 4 节。
- **被数据否掉**：#3、#10、#17（见上表）。
- **属于其它代号**：模型路由和 effort 实验（C/F2）；非劣效实验（F2）；交接预算和恢复并发保护（I/G 已在做）；按时段安排实验（B）。
- **Codex 想到、我原来没想到的**：#1"清理回本"。数据核实后它成立，但收益小于 Codex 估计的 3–15%（实际是守卫收益的约 3%）。

---

## 2. 原型（都在 tools/round5/K_*，只新建文件）

| 文件 | 做什么 | 状态 | 测试 |
|---|---|---|---|
| `K_doctor.py` | 一键诊断：环境；settings 注册（缺失、重复、路径不存在、指向别的副本、matcher、执行权限）；hooks 语法（用 `compile()`，不写 pycache）；**沙箱冒烟和延迟**（复制套件，用临时 HOME 和 `USAGE_SAVER_STATE`，合成输入，取 p50/max）；transcript 证据（最近 72 小时的注入次数和 hook 报错，报错和脚本修改时间对比，判断"已修"还是"故障还在"）；state 新鲜度和体积；`--json`；有 ❌ 时退出码为 1 | **可用**。真实环境实跑 1.8 秒：✅ 27、⚠ 1（就是 01:46 那次已修好的 test_filter 报错）、❌ 0。实跑前后真实 settings.json 的 sha1 没变 | `K_test_doctor.py` **15/15**：13 个假套件用例 + 2 个真实 hooks 沙箱冒烟。**变异自检 5/5**：把"用沙箱副本跑"改成"跑原文件"、去掉退出码检查、去掉执行权限检查、去掉重复注册检查、去掉报错时间判断，每一处都有测试失败 |
| `K_testall.py` | 自动发现 `tools/tests_*.py`、`tools/round5/*_tests.py`、`K_test_*.py`、`adapters/tests/test_*.py`、scorecard、I_adapt_matrix（跳过 heldout 和 live_*），逐个在子进程里跑（带超时），认出"通过数/总数"的 6 种写法，最后汇总 | **可用**。实跑（11-06 晚）：**9 套测试全部通过，可识别的用例 353/353，用时 43 秒**。第一次实跑时发现解析器会被测试名里的"1 failed, 4 passed"字样骗到，已修，并加了回归用例 | 在 `K_test_patterns.py` 里，解析 9 例 + 自动发现 1 例 |
| `K_patterns.py` | 本报告的统计工具：报错类别、测试循环、纠正和中断、Write 覆盖写、跨 agent 重复读、模型×effort、时段、空闲重写、**冷重写按原因分类**（撞限后恢复、SendMessage 续用、等待某个工具）、**清理回本**；`--post`、`--json` | **可用**。全量约 5 秒 | `K_test_patterns.py` **27/27**：合成 transcript，覆盖 message.id 去重、成本公式、测试循环计数、覆盖写、TTL 分类、撞限恢复、回本算术 |

**怎么用**：
- `python3 tools/round5/K_doctor.py`；只想快速看一眼加 `--quick`。
- `python3 tools/round5/K_testall.py`。
- `python3 tools/round5/K_patterns.py --post`。
- 改 hook 之前，先在副本上跑一遍：`python3 tools/round5/K_doctor.py --kit <副本目录>`。

**还没做 / 局限**：
- doctor 只认本套件的 6 个部件（注册表在 `COMPONENTS`）。新加部件要在这里加一行。
- evidence 只数 transcript 里有记录的 hook 报错（`hook_non_blocking_error` 等）。Claude Code 不记的失败看不到。
- K_testall 不会并行跑；某一套测试如果写共享文件，要由那套测试自己隔离。
- 建议正式启用时把 doctor 改名 `tools/doctor.py`，并在 README 里加一节"Verify it works"。**这需要改已有文件，留给主会话决定。**

---

## 3. 额外发现

1. **SendMessage 续用的规则要补条件**（K3）。HANDOFF 里"打回优先 SendMessage"只有在 5 分钟内才成立；Codex 复核之后再打回，缓存一定是冷的。证据：启用后 2 次续用都在 5–7 分钟后，每次整段重写约 7 万。
2. **成本归属可以按 cwd 自动算**（K1）。`perreq.py` 的思路（cwd 在套件目录或会话 scratchpad 就算"套件开发"）可以并进 weekly_compare 的一个分组。这样"真实用量"里就不会混进开发本身。这件事和 F1 的口径问题互补。
3. **hook 是 fail-open 的，这点应该写进 README**。Python 语法错误时退出码是 1，Claude Code 把它当非阻塞错误，所以 hook 坏了不会卡住对话，只是不再起作用。这正说明需要 doctor 这样的检查。
4. **11-04 通宵在 02 时和 05 时连续两次耗尽 5 小时窗口**。9 个并行 Opus 会话一起撞上限，又一起恢复，这是 K2 的主要来源。以后通宵运行可以错开会话，或者减少并行数。

## 4. 简历里怎么讲（英文，因为是对外材料）

**一句话**：*Built a measurement-driven cost optimizer for LLM coding agents: modeled per-request token cost under prompt-cache TTL economics, mined 20k+ logged requests for waste patterns, and shipped guarded policies validated by paired benchmarks and an independent model's blind tests.*

**三条要点**（可以挑着用）：
- *Modeled the cost of an agent session as cache-read / cache-write (5-min vs 1-hour TTL) / output terms; derived a break-even rule for context resets (N\* = R / ((c − c₀)·0.1)) and showed 37% of naive resets at 160k tokens would not pay back.*
- *Attributed 646M cost-units across 567 sessions; found cold-cache rewrites after rate-limit resumes cost 2.3% and sub-agent 5-minute TTL expiries 2.3% of recent spend, and turned them into concrete policies.*
- *Engineered for reliability: fail-open hooks, a sandboxed `doctor` that smoke-tests every hook with latency budgets (p50 16–31 ms), a unified test runner (353 checks), and mutation tests proving the checks catch regressions; publicly retracted two early claims after independent review.*

**面试时能讲的三个点**：
1. 缓存 TTL 的经济学：为什么"等 6 分钟"比"等 4 分钟"贵 10 倍。
2. 回本规则的推导，以及数据上它告诉我们什么。
3. 为什么前后对比的 −33% 不可信：工作量构成变了。这一点由 F1 给出，体现的是统计素养。

---

## 总结

- **做了什么**：
  - 用 K_patterns 统计了 20,766 次请求（全量）和 1,969 次（启用后），找出 A–J 以外的 12 个机会和 9 个被数据否掉的想法。
  - Codex 独立头脑风暴 29 条，合并去重后，有 1 条新想法（清理回本）经数据核实成立。
  - 做了 3 个原型（doctor、统一测试入口、模式统计），共 42 个测试用例全部通过；doctor 的变异自检 5/5 都能抓到。
- **主要数字**：
  - 启用后 93% 的用量是套件自己的开发【实测】。
  - 撞限后冷恢复占全量 2.3%【实测】。
  - 子 agent 5 分钟缓存过期占启用后 2.3%【实测】。
  - 跨 agent 重复读约占 2.0%【估算】。
  - 160k 线上 37% 的清理不回本【估算】。
  - hook 延迟 p50 16–31ms【实测，沙箱】。
  - 全部测试 353/353【实测】。
- **要所有者决定**：
  1. 第 6 轮起（或 11-21 换 Pro 后）是否执行"每轮预算 + 回本 ≤4 周"（K1）。
  2. 是否让 C 把 K3 的两句规则加进 model-router。
  3. 是否把 doctor 正式化为 `tools/doctor.py` 并写进 README（要改已有文件）。
  4. K2 要不要交给 B 做撞限前交接 / 撞限后先压缩。
- **撤回**：本轮只新建了文件，撤回就是把它们移走：
  `mkdir -p _to_delete/r5K; mv tools/round5/K_doctor.py tools/round5/K_test_doctor.py tools/round5/K_testall.py tools/round5/K_patterns.py tools/round5/K_test_patterns.py reports/round5_K_explore.md _to_delete/r5K/`
  `tools/round5/__pycache__/` 里的 K_*.pyc 也可以一起移走。
