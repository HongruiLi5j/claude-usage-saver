# 目标"节约 50%"：主会话 Claude 的独立方案（先写，再与 Codex 对照）

## 1. 现在省了多少（实测）
- F2 第 7 轮（Sonnet，10 任务×r=2）：套件 B 对 A 几何均值比 **R=0.984**（95% CI 0.941–1.029），总量比 **0.920**；完成率 100%/100%。省在大上下文任务（bigctx_edits 0.841，峰值上下文 381k→240k）；小任务持平。
- longout_triage（长测试输出、不加管道）：B 比 A 便宜 36%（$0.139→$0.087，2/2 全对）。
- 真实用量：每次请求成本 34.8k→33.3k（−4%）；main·opus 每次请求 51.6k→31.7k（−39%，混杂了工作类型变化）。
- 结论：**套件本身（hooks/设置）在重任务上约省 8–16%，在轻任务上约 0；靠它做到 50% 不可能。**

## 2. 钱花在哪（weekly_20261107，Sonnet 当量）
- 启用后 Opus 占 93%（改动前 67%）。最近窗口：sub·opus 2017 次×37.2k≈75M，main·opus 635×31.7k≈20M，Sonnet 合计≈7M，总≈102M。
- 每次请求 33.3k = cache read 11.6 + cache write 10.5 + output 11.2。output 占 1/3（Opus effort=high）。

## 3. 能到 50% 的杠杆（按预计节省排序）
| # | 做法 | 预计节省（占总额度） | 依据 | 质量风险 | 怎么验证 |
|---|---|---|---|---|---|
| 1 | 子 agent 默认 Sonnet（`CLAUDE_CODE_SUBAGENT_MODEL=sonnet`；复核/难题由 model-router 点名 opus） | 25–37%（开发期）；平时 5–15% | Opus 单价 2×；sub·opus 占最近 73% | 中：通读/搜索/实施不难的部分 Sonnet 足够，难设计会降 | F2 加难题，比 Opus vs Sonnet 子 agent |
| 2 | 主会话 Sonnet + Opus advisor（`advisorModel: opus`，官方推荐搭配；实验功能） | 15–30%（若主会话 Opus 占大头） | 官方："typically costs less than running the stronger model throughout"，无数字 | 中：advisor 在关键节点把关，但每次咨询读整段对话，大上下文时贵 | F2 arm：B_sonnet_advisor vs B_opus |
| 3 | `opusplan`（计划用 Opus，执行用 Sonnet） | 10–25% | 官方别名 | 中低 | F2 arm |
| 4 | Opus effort high→medium（官方默认 medium） | 3–4% | F2 实测 R=0.957，output −13% | 测不出（任务太易） | 难题上再测 |
| 5 | 套件现有措施（守卫、test_filter、关 Artifact、300k） | 已在，重任务 8–16% | 实测 | 无 | 已测 |
| 6 | 行为：少开并行 agent、会话短、简单问题 /btw | 难量化，开发期最大 | K：93% 用量是开发本身 | 无 | — |

- 粗算（乘法叠加，最近的工作结构）：#1 取 30% × #2 取 15%（只作用于主会话那 20%）× #4 3% × #5 8% → 1−0.70×0.97×0.97×0.92≈**39%**；再加 #6 才可能到 50%。
- 若按改动前（所有者日常、Opus 67%）结构：全换 Sonnet 最多省 33%，加套件和 effort 约 40%。**50% 要靠模型搭配 + 用法，且必须先证明质量不降。**

## 4. 质量怎么测（这是关键）
- F2 现有任务对 Sonnet 都是 100%，测不出差别。需要 8–12 道"Sonnet 有时会错"的难题：多文件 bug（两个相互掩盖的 bug）、需要读懂设计文档再改、带边界条件的数值代码（量化回测类：前视偏差、复权）、Lean 小引理补全、要求先写失败测试再修。
- 质量指标：隐藏测试通过率 + 不改 tests/ + 盲评答案正确性。
- 实验（1 个 5h 窗口内）：4 臂 B_opus_high（现状）/ B_opus_medium / B_sonnet_advisor_opus / B_sonnet，10 难题 × r=2 = 80 次运行，Opus 臂成本高，预计占 5h 额度 30–40%。
- 判定：某臂成本 ≤0.6×现状 且完成率不低于现状（差的 95% CI 下限 ≥ −5 个百分点）→ 推荐；否则不采用。

## 5. 需要所有者决定
- 是否同意做这个实验（花约 1/3 个 5h 窗口 + 出难题的开发量）。
- 若实验通过：是否把主会话默认模型改成 Sonnet + Opus advisor、子 agent 默认 Sonnet（改 settings.json，带备份和撤回）。
