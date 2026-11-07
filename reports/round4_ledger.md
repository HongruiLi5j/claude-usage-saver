# 第 4 轮任务账（配对制：每项 = 实施者 + 独立复核者）

规则：
- 实施者交付后，必须由**另一个** agent（优先 Codex，跨模型更独立）复核；复核者要亲自重跑证据（测试/命令），不能只读报告。
- 复核结论只有三种：通过 / 打回（附具体问题）/ 需所有者决定。打回 → 原实施者修 → 再复核。**打回 3 次 → 交所有者**。
- 没有复核通过记录的项，improvements.md 里标「待复核」，不算进提升数字。
- 每个文件同一时间只有一个实施者（见"文件归属"）。

| 项 | improvements # | 实施者 | 复核者 | 状态 | 打回次数 | 证据 |
|---|---|---|---|---|---|---|
| 防注入 / compact 注入 / handoff 覆盖写 | 12 | 实施 A | Claude 复核（round4_review_12_13.md） | 打回 → 实施 G 修 compact 解析等 | 1 | round4_review_12_13.md |
| Pro 封顶 + D2/D6 | 13 | 主会话 | Claude 复核 | 打回 → 状态栏跟随（实施 D）、评分表固定日期（实施 B） | 1 | 同上 |
| weekly 子 agent 明细 | 14 | 实施 C | Codex | **通过** | 0 | round4_impl_C.md |
| router 拆分 + D7 | 15 | 实施 B | Claude 复核（第 3 轮复核的 test_filter 部分） | 打回 → 已修（#18） | 1 | round3_review.md |
| test_filter 阻断修复 + 评分表夹具/变异 | 18 | 实施 B | Codex + 回放 H 重跑 | **打回**（11-06 03:05）：多行 `[[ … ]]` 被插分号致语法错；`\ #` 被当注释吞掉后续命令（两者 B2 前即存在）；SR19 断言漏检 → 实施 B4 修 test_filter，并入 agent 修 SR19 | 1 | round4_codex_review_B.md |
| Pro 配置包 + 清单 + router fork 节 | 16 | 实施 E | Codex | **打回第 2 次**（03:25）：同秒备份覆盖、第 22 步 ls -t 选错、scan 按 mtime 跳过漏新记录、坏 usage 崩、state 路径未校验、env 非 dict、符号链接、E1 state 兼容 → E3 修（分诊过的清单） | 2 | round4_impl_E.md E2 节 |
| limit_logger | 17 | 实施 F | Codex | **打回第 2 次**（03:25）：阻塞 LOCK_EX 无超时（实测 10.3s 不返回，真实已装！）、link/unlink 交错丢新事件、短写半条 JSON、卸载子串误删 → F3 修 | 2 | round4_impl_F.md F2 节 |
| 状态栏/守卫一批 bug（含 Codex 8 条） | 19 | 实施 D | Codex | **打回**（11-06 03:00）：过期提醒记录没在去重前清理；坏记录导致强提醒静默丢失、连累其它会话 → 实施 D2 修（连同 SL03）→ **D2 已交付**（03:30，tests_r4D 改前 36/42→改后 42/42，scorecard 107/107）→ Codex 复核 D2 中（新写法，round4_codex_review_D2.md） | 1 | round4_codex_review_D.md |
| session_resume 一批 bug | 20 | 实施 G | Codex | **打回**（11-06 03:15）：compact 仍混入旧轮次下一步；ps 失败时读他人 owner 的 v2 指针；指针选择与消费非原子 → 实施 G2 修 | 1 | round4_codex_review_G.md |
| 第 5 轮：额度花在哪（真实数据分析） | — | 分析 agent（Opus） | — | 进行中 | — | round5_where_tokens_go.md |
| 状态栏坏 stdin 崩溃（盲测 SL03） | 19 附 | 实施 D2 | Codex | 修复中 | 0 | round4_triage.md |
| 评分表并入 G/D 建议场景 + SR19 断言修 | — | 并入 agent（Sonnet） | 主会话核数 | 完成：69/69 → **107/107**（G 8 个顺延为 SR20–27，D 28 个；SL14/SR19 去 na；SR19 改为三条红线都在下一步前） | — | round4_scorecard_merge.md |
| 真实命令回放 | — | 回放 H | — | 19,951 条改坏 0 | — | round4_replay_H.md |
| 盲测场景 | — | Codex | 分诊 agent | 修正夹具后 56/60（93%）；真 bug SL03；待所有者 CG11/CG13/SR06 | — | round4_triage.md |

文件归属（同一时间只有一人改，11-06 03:00 更新）：scorecard.py → 并入 agent；statusline.sh、context_guard.py、weekly_compare.py、tests_r4D.py → 实施 D2；test_filter.py → 实施 B4；session_resume.py → 空闲（等 Codex 复核 G）；pro_switch/清单/router → 空闲（等复核 E2）；limit_logger 系 → 空闲（等复核 F2）；improvements.md、本账 → 主会话。

## 第 5 轮复核结果（11-06，Codex 新写法实跑）
| 项 | 结论 | 打回次数 | 说明 |
|---|---|---|---|
| D2（#19 状态栏/守卫） | **通过** | 1 | tests_r4D 42/42，坏输入 20 次全过，合法输出 16 组逐字节一致 |
| E3（#16 Pro 切换） | **通过** | 2 | 12 组检查全过；11-21 当天还要真机确认 |
| G2（#20 session_resume） | **通过** | 1 | 三个打回问题都已复现并确认修好；真实 compact 输出核对过 |
| B4（#18 test_filter） | **打回** | 2 | `$'…'` 引号拆错，测试没跑却 rc=0 → B5 修（再被打回就交所有者） |
| F3（#17 limit_logger 安装器） | **打回（第 3 次）→ 交所有者** | 3 | 命令参数里出现 logger 路径（如 `/bin/echo .../limit_logger.py`）会被认成 logger，导致漏装、卸载误删。hook 本身没问题，真实 settings 里没有这种配置 |
| C（第 5 轮：通读/调研改派 Codex） | **通过**（主会话审阅 model-router 的 19 行改动：只加不删，复核仍留给 Opus，符合质量优先） | 0 | 通读、调研质量相同，Claude 侧少花 93–97%；复核留给 Opus（命中率 Codex 44%、Opus 58%）|
| C2（Codex 脚本超时/stdin/额度识别/路径） | 实施者实跑：模拟卡住 3s 返回 124、真实额度用完返回 75、stdin 不再卡；15 分钟真超时未测 | 0 | 实施者自报在 scratchpad 用过一次 rm -f（目标不存在，未删任何东西） |
| B5（#18 test_filter，基线 B4+R5I） | 实施者实测：对照 bash 97/97、zsh 103/103；入口 0/22 坏；回放 0 坏；scorecard 107/107 → **待复核（第 3 次）**：Codex 额度 23:29 恢复后跑 `tools/codex_review.sh r5rev_B5 tools/round5/review_queue/B5.txt` | 2 | |
| I（第 5 轮：项目画像，已在真实 hook 生效） | 实施者实测：适应性矩阵 3/21→21/21；全套 354/354；回放 0 坏 → **待复核**：`tools/codex_review.sh r5rev_I tools/round5/review_queue/I.txt` | 0 | |
