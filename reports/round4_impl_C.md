# Round 4 实施 C：weekly_compare 新增"子 agent 明细"一节

> 2026-11-06。依据 `reports/round4_subagent_cost.md` 的"改法 3"。只改了 `tools/weekly_compare.py`，并重新生成了 `reports/weekly_20261106.md` 和 `reports/weekly_history.json`。

## 改了什么

`tools/weekly_compare.py` 只做了新增，没有改动原有逻辑：
- 新增两个累加器：`AG` 按（窗口, 主/子, 模型）记录每个 agent 每次请求的（时间, 上下文）；`PARTS` 记录 cache read、cache write、输出和合计成本。另外再累加一份（窗口, all, all）作为主会话和子 agent 的合计。
- agent 的划分：`/subagents/` 下的文件，一个文件算一个 agent；主会话文件里的 sidechain 按 `文件#agentId` 算；主会话按会话文件算。这与 `sub_cost.py` 的划分一致。
- 新增 `row()` 函数，以及一节"### 子 agent 明细"，放在原来的分组表之后、"读法"之前。列：agent 数、每 agent 请求数、第一次请求上下文（各 agent 在该窗口内最早一次请求的上下文，再取均值）、平均上下文（按请求平均）、cache read / cache write / 输出三项的每次请求成本、每次合计。行：main/sub × opus/sonnet/other（某个窗口没有数据就不出这一行），最后是 `all·all`（主会话 + 子 agent 合计）。
- 口径沿用原有的：1 小时缓存 2×、5 分钟缓存 1.25×，排除 `-private-tmp-`，按 message.id 去重，窗口切分点相同。`weekly_history.json` 的结构不变，新的明细没有写进 history。

## 新旧输出对比

**旧的各节没有变。** 我把旧版和新版复制到 scratchpad（只改了 BASE 路径），同时运行，然后去掉新增的那一节，两份输出逐字节相同（`old==new minus section: True`）。
- 改前的基线：`/private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/old_out.txt`
- 同时运行的对照：同目录下的 `old_out3.txt` 和 `new_out3.txt`
- 基线和后面几次运行之间有微小差异（启用后每天请求数 448 → 453，sub·opus 210 → 219 次），原因是当前这些会话还在不断写入新数据，与代码无关。

**子 agent·Sonnet 与调查报告一致：**

| | agent 数 | 每 agent 请求数 | 第一次请求上下文 | 平均上下文 | cache read | cache write | 输出 | 每次合计 |
|---|---|---|---|---|---|---|---|---|
| 改动前 | 16 | 10.0 | 29.7k | 42k | 3.6 | 7.3 | 2.6 | 13.5 |
| 启用后 | 11 | 19.8 | 29.7k | 119k | 11.3 | 8.4 | 6.1 | 25.8 |

这一行与 round4_subagent_cost.md 的表 1 和表 2 完全一致：160 次 13.5k，218 次 25.8k，第一次请求上下文 29.7k 对 29.7k，每 agent 请求数 10.0 对 19.8，平均上下文 42k 对 119k，三项拆分 3.6/7.3/2.6 和 11.3/8.4/6.1。今天的 Sonnet 子 agent 没有新增请求，所以没有偏差。

合计（all·all）：改动前每次请求 32.1k，启用后 21.5k。这与顶部"每次请求成本"一行一致，可以作为自检。

## 备份和撤回

备份：`backups/weekly_compare.py.pre-r4C`、`backups/weekly_20261106.md.pre-r4C`、`backups/weekly_history.json.pre-r4C`

撤回（在项目目录下执行）：
```
cp backups/weekly_compare.py.pre-r4C tools/weekly_compare.py && cp backups/weekly_20261106.md.pre-r4C reports/weekly_20261106.md && cp backups/weekly_history.json.pre-r4C reports/weekly_history.json
```

## 疑虑

1. **主会话的"第一次请求上下文"意义不大。** 跨越启用时刻的会话，在"启用后"窗口里的第一次请求其实是会话中途的请求，所以 main·opus 启用后显示 61.8k，比改动前的 35.0k 高。这不说明 prompt 变长了。套件真正要盯的是 sub 行的这个数。all·all 的 41.4k 也受这个影响。
2. **一个 agent 用了多个模型时，会在每个模型的分组里各算一次**，所以 all·all 的 agent 数不一定等于各行之和。这与 an.py 的做法相同。
3. **"other" 行**（haiku、`<synthetic>` 等）是新出现的，旧表里没有。我保留了这一行，因为以后改法 2 会把读取类任务交给 haiku，这时就需要看到它。
4. 改法 3 原文还提到"合计每天的成本"。顶部表里已经有每天成本，所以这里只加了每次请求的合计，没有重复加。
5. 分项里没有单列未缓存的 input（每次 < 0.1k），所以三项之和略小于合计。这一点已经在读法里写明。

## 总结
- 只改了 weekly_compare.py，只做了新增：多了"子 agent 明细"一节（主/子 × 模型，以及 all·all 合计）。
- 和旧版同时运行，旧的各节逐字节相同。前后几次运行之间的差异来自会话在实时写入新数据。
- sub·sonnet：16/160 → 11/218，13.5k → 25.8k，第一次请求上下文 29.7k → 29.7k，与调查报告完全一致。
- 已备份 3 个文件，后缀 .pre-r4C，一行 cp 就能撤回。
- 疑虑：主会话的"第一次请求上下文"会受跨窗口会话影响，只看 sub 行。
