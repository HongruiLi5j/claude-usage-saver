# 第 5 轮 F2：受控 token 基准（试点）

负责人：F2。范围按主会话 18:4x 的两次指示收缩为**试点**：8 个任务，每臂每任务 2 次，按 F1 §5.2 的设计执行（配对、随机顺序、两臂只差被测的一项、热身控制缓存）。另外按 E 的问题加了一组 **Opus effort high 对 medium**。正式的大规模 A/B **没有跑**，所需规模在第 6 节，交给所有者决定。

改动范围：
- 新建 `tools/bench/token_bench.py`（运行器和分析）、`tools/bench/bench_fixture.py`（固定小项目生成器）。
- 结果写在 `tools/bench/results/`：`token_bench_*.jsonl`、`summary_*.md`、`raw/`（4.6MB，gz 压缩的 stream-json）、`smoke/`（冒烟和作废的结果）、`pilot_*.log`。
- **没有改任何已有文件**，所以不需要备份和撤回。
- 没有写真实的 `~/.claude/settings.json`，只读过一次，用来核对 B 组的 hook 结构。

标注约定：【实测】是本轮跑出来的数；【估算】是从实测数推出来的，会写算法。

---

## 0. 结论先行

1. **隔离办法【实测】。** `--settings` 屏蔽不了用户级 hooks，加 `--setting-sources project,local` 才能屏蔽。两臂都用后者，再各自用 `--settings <临时文件>` 叠加本臂的配置。套件的 skills 用 `--plugin-dir` 加回来。不需要临时 HOME，也不需要 CLAUDE_CONFIG_DIR。
2. **套件 B 对 A（Sonnet，8 任务 × 2 次）【实测】：未检测到差别。**
   - 每任务成本几何均值比 R = **1.008**（95% CI 0.970–1.048），总量比 0.983。
   - 完成率两组都是 100%，有天花板效应，所以对质量没有检测力。
   - 唯一测到的机制效果在 long_ctx（长会话）：守卫触发后 /clear，峰值上下文从 325k 降到 205k（−37%），cache_read 降了 30–44%。但这个任务的**成本只降了 3%**，原因见 4.2 节。
3. **Opus effort medium 对 high（同样配对，两臂都装 B）【实测】：** R = **0.957**（95% CI 0.918–0.998），即每任务省约 4%。output −13%（总量比 0.83）。完成率 100% 对 100%，同样是天花板。按所有者"质量优先、质量不降才改"的规则，**现在的数据不足以证明质量不降，所以不建议改成 medium**，理由见第 5 节。
4. **方差比 F1 的假设小得多【实测】。** σ_ε（同任务重复之间 log 成本的标准差）是 0.05–0.06，sd(d_i) 是 0.046–0.050。按 F1 的公式，要测出 5% 的成本效应：
   - 用点估计，需要 **9–10 个任务 × r=3**；
   - 用保守值（σ_d 取 95% 上界），需要 **23–27 个任务**。

   但要检验**质量**非劣效，现在这组任务太容易（全部通过），必须先加难题。第 6 节给出正式 A/B 的建议规模和费用。
5. **过程中的意外，都已经修进运行器：**
   - ① `--resume` 时 `modelUsage` 和 `total_cost_usd` 是**会话累计**。按步相加会重复计数（冒烟时 long5 多算了约 2.7 倍）。现在改用 `model_usage_since_last_result`。
   - ② 跑的过程中，别的 agent 改了 context_guard、session_resume、test_filter 和 model-router。B 组 16 次运行里出现了 5 个配置指纹。现在开跑时会先给套件做快照。
   - ③ 18:50 到 19:03 的试点期间，所有 agent 合计把 **5 小时窗口用到了 99%**。我的 long_ctx medium r1 撞上限后失败，已经剔除。预算保护现在也会看 5 小时窗口（≥85% 就停），遇到额度错误立即停整轮。
6. **Codex：** 接口已写好（`--agent codex`），但实跑时 Codex 账号已经额度用完（提示 11:29 PM 重置），**没跑通**。解析格式在第 2 轮的 `codex_exercism.jsonl` 里已经验证过。

本轮所有 `claude -p` 的 API 标价合计约 **$31.6**：Sonnet 试点 $10.0，Opus effort 试点 $18.6，冒烟约 $3。

---

## 1. 隔离：实测了什么

环境：Claude Code 2.1.318，中途自动升级到 2.1.319，已记录在每条结果里。用 `--output-format stream-json --verbose --include-hook-events` 看 hook 事件。

| 做法 | 用户级 hooks 是否触发 | 其它影响 |
|---|---|---|
| 默认 | 触发（UserPromptSubmit、PreToolUse:Bash） | 加载 ~/.claude/skills、henchmen 插件 |
| `--settings '{"hooks":{}}'` | **仍然触发**，说明 hooks 是合并，不是覆盖 | — |
| `--settings '{"disableAllHooks":true}'` | 不触发 | 同时也关掉了本臂想加的 hooks，没法用 |
| **`--setting-sources project,local --settings <臂文件>`** | **不触发**；臂文件里的 hooks 正常触发 | 用户 skills、henchmen 插件、用户 agents 都不加载 |
| 再加 `--plugin-dir <临时插件>` | — | skills 变成 `usage-saver:handoff`、`usage-saver:model-router`，init 事件里能看到 |

其它实测结果：
- 在 `-p --resume <id>` 里发 `"/clear"`，会真的触发 `SessionStart:clear`，session_resume 正常注入。之后会拿到一个新的 session_id，可以接着 `--resume`。这样就能在无头模式下模拟"所有者按守卫的提示 /clear"。
- effort 写在临时 settings 的 `modelSettings.claude-opus-5-5.effortLevel` 里（和真实 settings 用同一个键）。一道推理题上 high 和 medium 的 output 是 1113 和 680，说明这个设置生效了（n=1，只作旁证）。试点里 medium 的 output 总量比 0.83，进一步印证。

**B 臂的定义**（和真实 settings 里的套件部分一致）：
- hooks：context_guard（UserPromptSubmit）、session_resume（SessionStart clear|compact、SessionEnd）、test_filter（PreToolUse Bash）、limit_logger（StopFailure）；
- skills：handoff、model-router；
- `autoCompactWindow=300000`。

statusLine 在 `-p` 下不运行，没有包括进来。

**A 臂**：上面这些都不装，其余完全相同。

两臂都设置了 `USAGE_SAVER_STATE=<每次运行自己的临时目录>`，所以 context_guard 的额度档位固定为"正常"，软线 160k，不受真实额度波动影响。

**隔离做不到的地方（如实说明）：**
- ① test_filter 的日志目录写死在本项目的 `state/testlogs/`。不影响结果，但 B 组运行会往里写日志（本次试点 test_filter 没有改写任何命令，所以实际没写）。
- ② context_guard 读的是本项目真实的 `config.json`（只读）。
- ③ 两臂都没有加载所有者的其它用户级 skills、henchmen 插件和 5 个用户 agents。所以绝对 token 比所有者的日常环境低：固定前置大约少 1–2k token。两臂之间的差不受影响。

---

## 2. 任务集（固定、可自动判定）

`bench_fixture.py` 用固定随机种子生成一个纯标准库加 pytest 的小项目 minishop，包括：
- 11 个模块、118 个测试；
- 大文件：6000 行日志（508KB）、85KB 的设计文档、10 份约 72KB 的手册；
- 2 个 CSV。

每次运行都在全新的临时副本里，先埋入种子 bug，再 `git init` 并做**唯一一次**提交。冒烟时看到模型跑了 `git log --stat`，如果种子另起一个提交，模型就能直接看出埋了什么，所以必须合成一次提交。

共定义了 15 个任务，试点用其中 8 个（下表标 ★）：

| 任务 | 类别 | 判定方式 |
|---|---|---|
| fix_bulk | 修 bug | pytest 全过，tests/ 没改，改了 pricing.py |
| ★ fix_three | 修 bug（3 处，日志长） | pytest 全过，tests/ 没改 |
| ★ triage | 跑测试读失败（只诊断） | 答案提到 shipping_cost 和 available，工作区没改动 |
| ★ feature_coupon | 新功能加测试 | 隐藏测试 7 个断言，pytest 全过，测试里新增了 apply_coupon |
| log_qa | 读大日志回答 | 计数、请求号、延迟值三项都对 |
| ★ doc_qa | 读大文档回答（有干扰项） | 7 次、3 秒、120 秒、指数退避 |
| lookup | 小查询 | 0.065、config.py:行号、CA |
| round_sites | 搜索定位 | 3 处 文件:行号 全部列出 |
| rename | 多文件重命名 | 没有残留，隐藏测试通过，pytest 全过 |
| ★ extract_validate | 多步重构 | validate.py 存在，三处重复删除，隐藏测试通过，tests/ 没改 |
| add_tests | 写测试 | 变异检验：新测试必须杀死 3 个核心变异体 |
| ★ handoff | 写交接文件 | HANDOFF.md 里有失败测试名和原因、"下一步"、NOTES 里的待办；src 没改 |
| cli_crash | 按报错修 bug | CLI 退出码为 0，输出含正确营收，pytest 全过 |
| ★ long5 | 长对话 5 步（`--resume` 接续） | 隐藏测试、README 新节、第 1 步和第 5 步答案的检查都过 |
| ★ long_ctx | 长会话（守卫场景） | 11 步：逐份通读 10 份手册各答一个代号，最后一步按顺序回忆全部代号。所有者模拟规则（两臂相同）：回复里出现 "/clear" 就照做；当步没答上就重问一次 |

`python3 tools/bench/token_bench.py --selfcheck` 不调用模型，检查每个任务"什么都不做"时必须判失败，3 个修 bug 任务撤回种子后必须判通过。结果是 15/15 正确。另外手工验证过 add_tests、log_qa、round_sites 的正例能判通过。

试点为什么选这 8 个：覆盖所有者的日常类型（修 bug、读测试失败、新功能、读大文件、重构、写交接、5 步长对话、守卫长会话），并且把最贵也最关键的守卫场景放进来。lookup、log_qa、round_sites、rename、add_tests、cli_crash、fix_bulk 留作以后的开发集或确认集。

---

## 3. 运行器（实测可用）

```
python3 tools/bench/token_bench.py --config A,B --pilot --repeats 2 --model sonnet     # 本次试点
python3 tools/bench/token_bench.py --config A --repeats 3                              # 单臂（以后补跑）
python3 tools/bench/token_bench.py --config A,B --repeats 3 --rep-offset 2             # 分批补跑时 rep 编号接着往下
python3 tools/bench/token_bench.py --config B,my_arm.json --repeats 3                  # 自定义臂：{"name","settings","skills","model","effort_setting","extra_args"}
python3 tools/bench/token_bench.py --analyze --base A --treat B --md [文件...]         # 汇总（默认读 results/ 里全部结果）
python3 tools/bench/token_bench.py --agent codex --config codexA --tasks lookup        # Codex
python3 tools/bench/token_bench.py --list | --selfcheck
```

按 F1 §5.2 做了这些：
- **配对 + 随机。** 每次重复重新打乱任务顺序。同一任务的各臂在同一个 worker 里背靠背跑，先后随机。3 个 worker 并行跑不同的任务。
- **控制缓存。** 每块开头每臂先跑一次热身（不计入），并记录每次运行首请求的 cache_creation 和 cache_read。试点中 32 次运行的首请求全部命中前缀缓存（cache_read = 11,382），两臂一致。
- **只差一项。** 同一个模型 id、同一个 CC 版本、同一个 effort、同一份 fixture（FIXTURE_VERSION=1），这些都写进了每条记录。
- **分析单位是任务。**
  - 先在任务内对重复取 log 均值，几何均值比用配对 t 区间。
  - 总量比和完成率差用按任务重抽的 bootstrap（n<20 时偏窄，只作参考）。
  - 同时报方差分量 σ_ε、σ_τ²，并按 F1 公式反推所需任务数。
- **主指标是 `total_cost_usd`**（API 标价，F1 §5.2.10）。同时报 weighted token（同 weekly_compare 的权重）、各类 token、请求数、峰值上下文、耗时。
- **快照。** 开跑时把 hooks 和 skills 复制到 `<WORK>/<run_id>/_suite/`，整轮都用这一份。如果想直接用项目里正在改的版本，加 `--live-suite`。配置指纹只对 settings 实际引用的脚本取 hash，路径做了归一化。
- **预算保护。** 周用量 ≥ 92% 或 5 小时窗口 ≥ 85% 就停；遇到额度或限流错误，整条记录标 `infra_error`，不进统计，整轮停止。
- **工作目录。** 默认放在 `/private/tmp/usage_saver_bench/`，可以用 `BENCH_WORK` 改。这样 weekly_compare 会按 `-private-tmp-` 把这些会话排除掉。

**token 记账的坑【实测】。** 第 2 步起用 `--resume` 接续时，result 里的 `modelUsage` 和 `total_cost_usd` 是从会话开头累计的：long5 第 2 步 cache_read 累计 74,936，但这一段只有 45,539。按步相加会重复计数。现在优先用 `model_usage_since_last_result`（这一段的量，含子 agent），费用也从它的 costUSD 加总。出错的 result 没有这个字段，就退回到本进程的 `usage`。

**价格（由试点数据回归出来的 API 标价，残差约 1e-15）：**

| 模型 | input | cache read | cache 写入（1h） | output |
|---|---|---|---|---|
| Sonnet 5.5 | $2/M | $0.10/M | $4/M | $10/M |
| Opus 5.5 | $4/M | $0.20/M | $8/M | $20/M |

也就是说 cache read 只有 input 的 0.05 倍。weekly_compare 的权重按 0.1 倍算，把 cache read 的分量**高估了一倍**。这一点建议转给 F1 和 weekly_compare 的维护者。

---

## 4. 试点 1：套件 B 对 A（Sonnet 5.5，effort high，8 任务 × 2 次，18:50–18:57）

结果文件：`tools/bench/results/token_bench_20261106-185012_claude_A+B.jsonl`；汇总：`summary_B_vs_A_20261106-190423.md`。

### 4.1 主结果【实测】

- **成本几何均值比 R = 1.008（95% CI 0.970–1.048）**；总量比 0.983（bootstrap 0.975–1.037）。
- 完成率 A 100%，B 100%，差 0（天花板，没有检测力）。
- 次指标（几何均值比）：weighted 1.001，请求数 1.041，峰值上下文 0.949（总量比 0.767），cache_read 0.989（总量比 0.697），output 1.050。

| 任务 | A $/次 | B $/次 | 比值 | 组内 sd(log) A/B | 峰值上下文 A→B | 守卫注入 | test_filter 改写 | /clear |
|---|---|---|---|---|---|---|---|---|
| doc_qa | 0.058 | 0.060 | 1.036 | 0.01/0.02 | 22.8k→23.2k | 0 | 0 | 0 |
| extract_validate | 0.096 | 0.094 | 0.982 | 0.04/0.03 | 26.6k→26.1k | 0 | 0 | 0 |
| feature_coupon | 0.088 | 0.087 | 0.980 | 0.11/0.12 | 25.9k→25.5k | 0 | 0 | 0 |
| fix_three | 0.087 | 0.097 | 1.117 | 0.01/0.05 | 26.4k→27.9k | 0 | 0 | 0 |
| handoff | 0.107 | 0.105 | 0.984 | 0.03/0.01 | 27.8k→27.6k | 0 | 0 | 0 |
| long5 | 0.140 | 0.141 | 1.007 | 0.01/0.01 | 33.2k→33.4k | 0 | 0 | 0 |
| long_ctx | 1.837 | 1.788 | 0.970 | 0.00/0.10 | **324.6k→204.6k** | 3 | 0 | 2 |
| triage | 0.073 | 0.073 | 0.994 | 0.10/0.06 | 24.2k→24.2k | 0 | 0 | 0 |

注意：试点 1 是在加快照之前跑的。B 组 16 次运行横跨了别的 agent 在 18:52–18:55 对 hooks 和 model-router 的修改（出现 5 个指纹），所以 **B 是新旧版本混合**。这对估计方差影响不大，但效应的点估计不能当作"当前套件"的定论。

### 4.2 为什么套件在这些任务上几乎不起作用（机制，【实测】）

- **test_filter 0 次改写。** 模型跑测试时自己加了 `| tail -40`、`2>&1 | tail`，带管道的命令 test_filter 按设计直接放行。这和第 4 轮回放的"只改写约 5% 的测试命令"一致。
- **context_guard 只在上下文超过 160k 时起作用。** 7 个短任务的峰值都在 23–34k，守卫不会触发。B 组每次多出约 200 token 的 skill 列表（首请求 cache_creation 6,694 对 6,497），这是 B 在小任务上唯一确定的额外开销，约占成本的 0.5%。
- **long_ctx 守卫正常工作。** 第 6 步上下文约 200k 时守卫注入提醒，模型建议 /clear，模拟的所有者执行 /clear，session_resume 注入摘要。之后 4 步都答对，最后的顺序回忆也全对（2/2），**质量没有因为 /clear 下降**。
- **但成本只降了 3%，原因是价格结构。** A 的 cache_read 是 5.25M，B 是 2.9–3.8M，少了 30–44%。可是 cache read 只要 $0.10/M，省下的不到 $0.2。而 long_ctx 的大头是每份手册第一次进上下文时的缓存写入（10 份 × 约 30k × $4/M = 约 $1.2），这部分无论 clear 与否都要付。再加上 B 的 /clear 那一步、交接文件，以及 r1 多出来的 Skill、Write、Bash 调用，r1 反而贵了 4%。
- **对所有者的含义【估算】。** 在"每步都读新的大文件"这类长会话里，守卫在 200k 处 clear 只能省 cache read 那部分。真正省得多的场景是"上下文已经很大、之后很多轮都只追加少量内容"，例如 11-04 通宵那种 500k 以上、每条结果被读几百次的会话：那里 cache read 占 75%。本试点的 long_ctx 没有覆盖这种形态。**建议以后加一个 long_idle 任务**：先读进 150k，再做 20 轮小问答。

---

## 5. 试点 2：Opus effort medium 对 high（两臂都装 B，8 任务 × 2 次，18:50–19:03）

结果文件：`token_bench_20261106-185017_claude_B_opus_high+B_opus_medium.jsonl`；汇总：`summary_B_opus_medium_vs_B_opus_high_20261106-190422.md`。

- **R = 0.957（95% CI 0.918–0.998）**，总量比 0.947。
- output 的几何均值比 0.873（0.796–0.958），总量比 0.829。请求数 0.964，耗时 0.918。
- 完成率 high 100%，medium 100%。
- 有 1 次运行（long_ctx medium r1）在第 6 步撞了 5 小时额度，已标 `infra_error` 剔除。所以 long_ctx 是 2 对 1。
- 各任务比值：doc_qa 1.025、extract_validate 0.978、feature_coupon 1.004、fix_three 0.972、handoff 0.880、long5 0.949、long_ctx 0.944、triage 0.911。
- σ_ε = 0.049，sd(d) = 0.050。

**判定（按 F1 §5.3 和所有者规则）：**
- 省额度路径：CI 上界 0.998 < 1，点估计 0.957 ≤ 0.95？**不满足**（0.957 > 0.95）。这是试点数据，本来也不能用来做判定。
- 质量：两臂都是 100%，属于 F1 §5.5 第 6 条的天花板，只能写"未见质量下降（无检测力）"。按 F1 的非劣效公式，要排除 10 个百分点的质量下降大约需要 62 个任务。
- **所以不建议现在改成 medium。** 收益约 4%【实测，试点】；在真实使用中，Opus 约占启用后成本的 67%（round5_where_tokens_go 第 1 节），折算下来约占总成本的 3%【估算：0.043 × 0.67 ≈ 2.9%，不确定性至少 ±2 个百分点】。而质量风险目前完全没有测到，因为这些任务对 Opus 来说太容易。
- 如果要继续验证，需要能分出质量的难题（基线通过率 30–90%），见第 6 节。

---

## 6. 正式 A/B 需要多少（给所有者决定，【估算】）

按 F1 的公式 `MDE = 1 − exp(−(t_{1−α/2} + t_{1−β})·σ_d/√n)`（α=0.05 双侧，检验力 80%），以 `σ_d² = σ_τ² + 2σ_ε²/r` 计算：

| 依据 | σ_d（r=3） | MDE 5% 需要的任务数 | MDE 10% | MDE 20% |
|---|---|---|---|---|
| 试点 1 点估计（σ_ε=0.061，σ_τ²≈0） | 0.050 | 10 | 4 | 3 |
| 试点 2 点估计（σ_ε=0.049） | 0.040 | 7 | 4 | 3 |
| **保守**：sd(d) 取 χ² 95% 上界（df=7，×1.80） | 0.083–0.090 | **23–27** | 7–8 | 4 |
| F1 对"真实 agent 任务"的假设 | 0.32 | 308 | 75 | 19 |

为什么试点的方差这么小：fixture 是确定性的小项目，每个任务的做法基本固定（组内 sd 只有 0.01–0.12）。σ_τ² 只用 8 个任务估计，估出来是 0 附近，不可靠，所以要用保守行。真实工作（多 agent、开放式调研）的方差会大得多，**这里的 n 只对"这类固定任务"成立**（F1 §5.5 第 9 条）。

**我的建议：**

1. **只测成本：** 确认集 **25 个任务 × r=3 × 2 臂 = 150 次运行**。需要把任务从 15 个扩到 25 个以上，开发集和确认集分开（F1 §5.2.6）。
   - 费用【估算】：Sonnet 试点每次运行平均 $0.28，Opus $0.52，但都被 long_ctx 拉高（它占费用的 59–73%）。正式跑预计 Sonnet 约 $40–60，Opus 约 $80–110（API 标价）。
   - 本次试点约 72 次运行（含热身）期间，5 小时窗口从 18:49 的 10% 涨到 19:03 的 99%。但同一时段还有其它 agent 在并行，我这部分占多少拆不出来。所以**要分 2–3 个 5 小时窗口跑**，或者先用 `--repeats 1` 分批，再用 `--rep-offset` 补齐。
2. **要测质量（effort 这类问题必须测）：** 需要先加基线通过率 30–90% 的难题，例如多处隐蔽 bug、要读懂才能改的重构、开放式审查加隐藏评分表。然后按 F1，排除 10 个百分点的质量下降要约 62 个任务，排除 15 个百分点要约 28 个。**这比测成本贵 2–3 倍。** 在没有这批难题之前，任何"质量不降"的说法都没有检测力。
3. **确定性改动不要走端到端。** 例如 A 的"去掉工具定义"、B 加 skills 带来的约 200 token 前置：直接比首请求的 cache_creation（本运行器已经记录），方差约为 0（F1 §5.2.8）。端到端基准只用来证明完成率不下降。

**是否跑正式 A/B，由所有者决定。** 我的倾向是：先用开发集把任务扩到 25 个以上并加入难题，单独跑一次 r=1 确认基线通过率在 30–90%，然后再决定。

---

## 7. 跨工具（Codex 等）

- `CodexAgent` 已经写好：`codex exec --json -s workspace-write --skip-git-repo-check -C <副本>`，多步任务用 `codex exec resume <thread_id>`（`--allow-multi`）。
  - 记账方式：从每个 `turn.completed.usage` 取数，input 减去 cached 得到未缓存的 input，cached 记为 cache_read，再加 output。
  - 遇到 `error` 或 `turn.failed`，且信息里有 usage limit 字样时，标为 infra_error。
- **实跑结果：** 18:50 跑 lookup 和 fix_bulk，Codex 都返回 "You've hit your usage limit … try again at 11:29 PM"。**本轮没跑通 1 个任务**，原始输出在 `results/raw/20261106-185019_codex_codexA/`，结果文件移到了 `results/smoke/`。重置后跑这一条即可：`python3 tools/bench/token_bench.py --agent codex --config codexA --tasks lookup --repeats 1 --no-warmup`。
- Codex 的 token 字段解析在第 2 轮的 `results/codex_exercism.jsonl`（142 题）里已经实际用过。
- 适配其它工具要做的事：
  - 写一个带 `run_step(prompt, cwd, arm, root, session, timeout, budget, raw_path)` 的类，返回 usage、answer、session_id、infra_error；
  - 臂的差异通过 `extra_args` 表达。Codex 没有 hooks，套件的等价物（例如 H 的 adapters/AGENTS.md）做好以后，在 `ARMS` 里加一个 `codexB`，内容是往副本里放 AGENTS.md，或者加 `-c` 覆盖。
  - 任务、评分器、fixture、分析都可以共用。
  - Gemini CLI 如果有 JSON 用量输出，也可以同样接入（未验证）。

---

## 8. 局限

1. 两个试点都有**完成率天花板**（几乎全部 100%），所以对质量没有检测力。
2. 任务是固定的小项目，不含多 agent 和开放式调研，而 round5 的分析里子 agent 占成本的 62%。所以这里的方差和效应只对"这类任务"成立。
3. 试点 1 的 B 是混合版本（快照是在它之后加的）。
4. 两臂都没加载所有者的其它用户级 skills、插件和 agents。
5. 主指标用的是 API 标价，**订阅额度怎么折算不知道**。
6. long_ctx 的"所有者模拟"规则（看到 /clear 就照做、没答上就重问一次）是我定的，是简化版的人。
7. 试点 2 有 1 次运行因额度失败被剔除，long_ctx 只有 2 对 1。

---

## 总结

- **做了什么：**
  - 实测确认了隔离办法：用 `--setting-sources project,local` 加 `--settings` 临时文件，再加 `--plugin-dir`；单靠 `--settings` 屏蔽不了用户 hooks。
  - 写了可复用的基准：15 个固定、可自动判定的任务（评分器经过 selfcheck），以及运行器。运行器支持 A/B/自定义臂、Claude 和 Codex、配对随机、热身、套件快照、预算保护、F1 式分析和所需 n 的推算。
  - 跑了两个试点：Sonnet 上 B 对 A，Opus 上 effort medium 对 high，各 8 任务 × 2 次。
- **数字【实测，试点】：**
  - 套件 B 对 A：R = 1.008（0.970–1.048），**未检测到差别**。长会话里峰值上下文从 325k 降到 205k，但成本只省 3%，因为 cache read 很便宜（$0.10/M）。完成率 100% 对 100%。
  - Opus medium 对 high：R = 0.957（0.918–0.998），output −13%。完成率 100% 对 100%（无检测力），所以**不建议改成 medium**。
  - σ_ε 是 0.05–0.06。正式 A/B 要测出 5% 的成本效应，需要 10 个任务（点估计）到 23–27 个任务（保守），r=3；测质量需要另加难题，约 62 个任务。
  - 本轮花费约 $31.6（API 标价）。
- **要所有者决定的事：**
  1. 是否跑正式 A/B：建议 25 个任务以上 × r=3，分 2–3 个 5 小时窗口跑，Sonnet 约 $40–60 或 Opus 约 $80–110。我的倾向是先扩任务、加难题，再决定。
  2. effort 是否改成 medium：建议**暂不改**，等有难题的质量对比结果再定。
  3. 是否把"cache read 实际按 0.05 倍计价"这一点告诉 weekly_compare 和 F1（现在的权重是 0.1）。
  4. 是否加一个 long_idle 任务，覆盖通宵那种"大上下文、很多轮小追加"的形态，守卫真正能省钱的是这种场景。
- **撤回：** 没有改任何已有文件。不想要本轮产物的话：`mkdir -p _to_delete && mv tools/bench/token_bench.py tools/bench/bench_fixture.py tools/bench/results/token_bench_2026110* tools/bench/results/summary_* tools/bench/results/pilot_*.log tools/bench/results/raw tools/bench/results/smoke reports/round5_F2_bench.md _to_delete/`（在项目根目录执行）。临时工作目录在 scratchpad 的 `bench_work/` 下，会话结束后可能被清掉，不影响结果文件。
- **注意：** 本轮（含其它 agent）在 19:03 已经把 **5 小时窗口用到 99%**，11:20pm 重置。重置前不要再跑基准。
