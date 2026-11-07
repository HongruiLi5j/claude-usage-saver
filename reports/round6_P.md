# 第 6 轮 P：记账 / 实时节约表

负责人：P。日期：2026-11-06（本地）。

标注约定：【实测】是跑出来的数；【估算】是从实测推出来的，写了算法；【模拟】是合成数据上的测试。

---

## 0. 结论先行

1. **节约表上线了**（状态栏已切到新版，真实状态栏在用）。显示样例【实测，本机真实 payload】：
   `Sonnet 5.5 | ctx 70k/1M | 5h 15% | 7d 13% | … | 省≈0.0%累计 本会话0.0%(估·上限)`
2. **F2 校准【实测】：节约表对 B 臂报 0.00%，F2 实测 B 相对 A 没有显著差别（R = 1.008，95% CI 0.970–1.048），两边一致。**
   - 但这个一致**没有检测力**：F2 的 16 次 B 运行里 test_filter 一次都没触发（模型自己加了 `| tail`），所以计入总数的那一项在 F2 上是 0 对 0。
   - 不计入总数的「守卫 /clear」估计上限在 long_ctx 上是 **12.2%**，而 F2 实测只省了 **2.7%**，**上限高估约 4.5 倍**。这正是不把它算进总数的理由。
   - 所以显示和 README 都写成「**估·上限**」：这是各机制的上限估计，不是 A/B 实测。
3. **在所有者真实 transcript 上离线回放【实测】**（487 个会话，API 标价合计约 $1,510）：test_filter 只触发 2 次，都是短日志，净值 **−338 token（≈0%）**。守卫后 /clear 有 4 次，估计上限 0.90%（不计入）。
4. **F1 的 0.05 核实了【实测，官方页 11-06】：** 官方定价页写的是「缓存读 = 0.1× 基础输入价」**是标准值**，但脚注规定 **Opus 5.5 和 Sonnet 5.5 是 0.05×**，Fable 5.1 / Mythos 5.1 是 0.025×。所以：
   - 所有者说的 0.1× 对旧模型成立；F1 说的 0.05 是**相对同一模型的基础输入价**，只对 5.5 代成立。两边都对，只是适用的模型不同。
   - claude-api skill 里 Sonnet 5.5 的缓存读写的是 $0.20（=0.1×），和官方页的 $0.10 不一致，F1 已指出；我按官方页实现。
   - 用节约表把 F2 的 32 次运行重算一遍，和 F2 记录的 `total_cost_usd` 的比值在 **0.9994–1.0007** 之间，说明价格表和 Claude Code 自己的计费一致。
5. **Codex 复核 I 阻塞项 1（并发状态栏丢窗口记录）已修**：读-改-写全程加锁、锁内重读、原子写。实跑 32 并发 × 3 轮，每轮保留 32/32（修复前 Codex 实测 8 并发 × 12 轮里有 8 轮丢记录）。
6. 测试：`tools/tests_r6P.py` **23/23**；`K_testall` **12 个套件全过，434/434**。

---

## 1. 做了什么（文件）

| 文件 | 新建/修改 | 内容 |
|---|---|---|
| `tools/pricing.py` | 新建 | 共享价格表，按模型族和版本。换算成「Sonnet 5.5 当量 token」（= API 美元 ÷ $2/M）。未知模型的处理见 §2。 |
| `hooks/savings_meter.py` | 新建 | 节约表：增量读 transcript，按逐次事件的反事实记账，写 `state/savings.jsonl`，给状态栏渲染。 |
| `hooks/statusline.sh` | 修改（已上线） | ① 加锁和原子写（修 Codex 阻塞项 1）；② 追加 `state/rate_history.jsonl`；③ 显示节约表；④ H §4：没有 current_usage 时用 used_percentage × context_window_size，ctx 后面显示窗口；⑤ 按 M 的提醒，`usage_unreadable` 只在 mode.json 记了同一个 session_id 时才显示。 |
| `tools/weekly_compare.py` | 修改 | 改用 pricing.py；两种口径都报；日数改成只算完整天；按实际出现的模型族动态分组；未知模型点名。 |
| `adapters/common/usaver_status.py` | 修改 | 显示同一个节约数（只读 savings.jsonl）。 |
| `README.md` | 修改 | 组件表加 pricing / savings_meter，改 statusline / weekly_compare 两行；新增「Savings meter」一节，写明是上限估计。 |
| `tools/tests_r6P.py` | 新建 | 23 个用例；`--calib` 是 F2 校准。 |
| `reports/weekly_20261106.md`、`reports/weekly_history.json` | 重新生成 | 用新版 weekly_compare 跑了一次，跑之前已备份。 |
| `hooks/test_filter.py` | **没改** | 节约表直接从 transcript 里认 test_filter 的输出（开头是 `[测试输出写入 <日志>]`），日志文件本身就是反事实的原文，不需要 hook 另外记录。这样不碰过滤行为，也就没有改坏的风险。 |

没碰：`hooks/context_guard.py`、`~/.claude/skills/`、`tools/heldout/`、`~/.claude/settings.json`。

---

## 2. 价格表口径（tools/pricing.py）

来源：platform.claude.com/docs/en/about-claude/pricing，11-06 读取。

每百万 token 美元价（写入 5m = 1.25×，写入 1h = 2×，缓存读倍数按模型）：

| 模型 | input | 写入 5m | 写入 1h | 缓存读 | output | 缓存读 / 本模型 input |
|---|---|---|---|---|---|---|
| Fable 5.1 / Mythos 5.1 | 10 | 12.5 | 20 | 0.25 | 50 | 0.025× |
| Opus 5.5 | 4 | 5 | 8 | 0.20 | 20 | **0.05×** |
| Opus 5 / 4.5–4.8 | 5 | 6.25 | 10 | 0.50 | 25 | 0.1× |
| Sonnet 5.5 | 2 | 2.5 | 4 | 0.10 | 10 | **0.05×** |
| Sonnet 5 | 2 | 2.5 | 4 | 0.20 | 10 | 0.1× |
| Sonnet 4.6 / 4.5 | 3 | 3.75 | 6 | 0.30 | 15 | 0.1× |
| Haiku 5.5（提示 ≤100k / >100k） | 0.10 / 0.50 | | | 0.01 / 0.05 | 0.50 / 2.50 | 0.1× |
| Haiku 4.5 | 1 | 1.25 | 2 | 0.10 | 5 | 0.1× |

- **Opus 5.5 = 2 × Sonnet 5.5**，五类 token 都正好是 2 倍。所以用「Sonnet 5.5 当量」就等于按 F1 采用的额度折算（R = 2.0）来算。
- **未知模型不会被悄悄算错：**
  - 族认识、版本不认识（例如 `claude-opus-7`）：用该族最新已知价，标 `family_fallback`。
  - 族也不认识（例如 `gpt-6`）：按 Sonnet 5.5 计，标 `unknown`。
  - `<synthetic>`（客户端合成的报错消息，usage 全 0）：标 `synthetic`，不计。
  - 状态栏在有未知模型时末尾加 `?`；weekly_compare 在报告开头列出这些模型名和次数；不认识的族超过成本 5% 时另外警告。
- **认得的写法：** `claude-opus-5-5`、带日期后缀、`[1m]`、`anthropic.` / `us.anthropic.` 前缀、`@日期`、旧式 `claude-3-5-sonnet-…`。
- **没建模的（已知限制）：** fast mode（Opus 5.5 是 $8/$40，即 2 倍）、`inference_geo: "us"` 的 1.1 倍、Batch 的 5 折。本机 transcript 里没见到这些。

---

## 3. 节约% 的计算口径

**总节约% = 估算节省 ÷ (实际成本 + 估算节省)**，单位都是 Sonnet 5.5 当量 token。

- **实际成本**：transcript 里每条 assistant 的 usage（按 message.id 去重，跳过全 0 的报错行），用 pricing.py 计价。
  - 包括 `<会话>/subagents/*.jsonl` 里的子 agent。
  - 「本会话」= 当前 transcript 加上它的子 agent。
  - 「累计」= 节约表启用（11-06 23:49）以来，状态栏见过的所有会话。30 天不动的会话并进 archived 合计。

### 3.1 计入总数的机制：test_filter（有直接反事实）

对每个 `[测试输出写入 <日志>]` 开头的 tool_result，记一个事件：

- **截掉的 token = 反事实下模型会看到的 token − 实际看到的 token。**
  - 反事实 = 不装 test_filter 时 Claude Code 自己的行为。【实测，本机 transcript 5 个样本】Bash 输出约 38KB 以上时，Claude Code 存成文件，只给约 1.7–2.3k 字符的预览；26KB 的输出是全文给的。
  - 所以设定：日志 ≤ 30,000 字符 → 反事实是全文；更长 → 反事实是约 2,100 字符的预览。
  - 后果：日志很长、过滤结果又比预览长时，**节省是负数**；≤40 行的短日志多了一行路径提示，也是小负数。都照实记。
- **截掉的 token 在之后每次请求里按下面计价**（用那次请求自己的模型和 TTL）：
  - 截掉后的第一次请求：缓存写入价（1h 或 5m 档，按那次请求的 cache_creation 拆分）；
  - 之后每次请求：缓存读价；
  - 那次请求本身是冷缓存重写（cache_creation > cache_read，K3 说的「超过 5 分钟就冷了」就是这种情况）：缓存写入价。
  - 遇到 `compact_boundary` 就停。`/clear` 会开新 transcript，自然也就停了。
- **token 估计**：ASCII 字符 ÷ 3.5，其余字符按 1 token/字符。误差约 ±25%【估算】。
- **去重**：事件 id = hash(session_id, tool_use_id)。同一个 tool_use_id 重复出现只记一次；message.id 重复只算一次；进度文件丢了从头重算时，savings.jsonl 里同一个 event_id 取最大 rev，不会算两次（M03）。
- **置信度「中」**：日志在就用日志原文；日志找不到时按「完整 N 行」外推，标「低」。
- **为什么仍然是上限**：
  - 没装 test_filter 时，模型常常自己加 `| tail`（F2 实测），反事实可能比全文小；
  - compaction 有时会保留最近几条消息（preservedSegment），这部分没处理。

### 3.2 不计入总数、单独列「估计区间」的：context_guard 提醒后的 /clear

- 条件：新 transcript 一开头是 `SessionStart:clear`，并且同目录里紧挨着它之前的会话末尾有 `[上下文守卫]` 注入。
- **区间 [0, 上限]**：
  - 上限 = (clear 前最后一次请求的上下文 − clear 后第一次请求的上下文) × clear 后每次请求的缓存读价；距上次请求超过 TTL 的按写入价。
  - 下限 0：所有者本来可能也会 clear；而且交接本身的开销没扣。
- savings.jsonl 里记 `counted: false`、`estimate_range`，另记 `upper_minus_rewrite`（上限减去 clear 后首个请求整段重写的开销）。
- **为什么不计入**：F2 校准显示上限高估约 4.5 倍（§4）。

### 3.3 不记的

- 提前压缩（autoCompactWindow）：反事实里压缩发生在哪，从记录里得不到。
- model-router：不知道「本来会用什么模型」。
- session_resume：它是成本，不是节省；它的开销已经在实际成本里。
- read_guard：D 的结论是不装。

### 3.4 记录格式（state/savings.jsonl，追加写）

每行字段：`{ts, session_id, cwd, event_id, rev, mechanism, saved_weighted_tokens, actual_weighted_tokens, method, confidence, counted, …}`。

- `mechanism` 取 `actual`、`test_filter`、`context_guard_clear` 三种之一。
- 同一个 event_id 的值变了才追加新行：变化 ≥5% 且 ≥2000，或者事件结束。读的人取 rev 最大的那行。
- 文件超过 4MB 时，在锁内压缩成每个 event 一行（M09）。

---

## 4. F2 校准对照表【实测】

做法：用 F2 已有的 transcript（运行 20261106-185012，Sonnet 5.5，8 任务 × 2 臂 × 2 次，32 次运行）离线跑节约表。命令：`python3 tools/tests_r6P.py --calib`。**没有新跑基准。**

| 任务 | A 实测 $/次 | B 实测 $/次 | F2 实测 B 相对 A | 节约表·计入总数（B） | 守卫区间上限（B，不计入） | 上限−重写开销 | 节约表算的成本 / F2 cost_usd |
|---|---|---|---|---|---|---|---|
| doc_qa | 0.058 | 0.060 | +3.6% | 0.00%（0 个事件） | 0.0% | 0.0% | 0.9994–1.0007 |
| extract_validate | 0.096 | 0.094 | -1.8% | 0.00%（0 个事件） | 0.0% | 0.0% | 0.9998–1.0004 |
| feature_coupon | 0.088 | 0.086 | -1.9% | 0.00%（0 个事件） | 0.0% | 0.0% | 0.9996–1.0006 |
| fix_three | 0.086 | 0.097 | +11.8% | 0.00%（0 个事件） | 0.0% | 0.0% | 0.9998–1.0001 |
| handoff | 0.107 | 0.105 | -1.6% | 0.00%（0 个事件） | 0.0% | 0.0% | 0.9996–1.0002 |
| long5 | 0.140 | 0.141 | +0.7% | 0.00%（0 个事件） | 0.0% | 0.0% | 0.9997–1.0001 |
| long_ctx | 1.837 | 1.788 | -2.7% | 0.00%（0 个事件） | **12.2%** | 10.8% | 1.0000–1.0000 |
| triage | 0.073 | 0.073 | -0.8% | 0.00%（0 个事件） | 0.0% | 0.0% | 0.9994–1.0006 |
| **合计（各任务均值相加）** | 2.487 | 2.445 | -1.7% | **0.00%** | 9.2% | 8.1% | 0.9994–1.0007 |

（节约表的 % = 节省 ÷ (实际 + 节省)。F2 实测列 = B 臂均值 ÷ A 臂均值 − 1，负数表示 B 更省。各任务的差别在 F2 的报告里都不显著，整体几何均值比 R = 1.008，95% CI 0.970–1.048。）

**怎么读：**

- **计入总数的那一项**：节约表报 0%，F2 实测在 0 附近，**没有偏差**。但 F2 里 test_filter 触发了 0 次，所以这只说明「没事件时不会凭空报节省」，**test_filter 的反事实算法本身没有被 A/B 验证过**。要验证，需要一个「测试输出很长、命令不带管道」的基准任务（见 §9）。
- **守卫上限**：long_ctx 上，节约表的毛估上限 12.2%（减去重写开销后 10.8%），实测只省 2.7%。高估的原因：
  - B 臂为 /clear 付出的交接、复述、重新读文件，上限都没扣（F2 §4.2 说 r1 反而贵了 4%）；
  - 缓存读本来就便宜（Sonnet 5.5 是 $0.10/M）。
  - 结论：**这一项不能计入总数**，只作为参考区间存进 jsonl。
- **价格表的正确性**：节约表算的成本和 Claude Code 报的 `total_cost_usd` 的比值在 0.9994–1.0007 之间（32 次运行）。`--resume` 多段运行（long5）也对得上。

**所以显示文字是「省≈X%累计 本会话Y%(估·上限)」，README 也写明「这是各机制的上限估计，不是 A/B 实测」。**

---

## 5. 显示样例和耗时

**显示样例【实测，真实 payload】：**
- 真实 Claude Code 状态栏在切换后已经在跑新版本：会话 4e12779f…、93712c58…、本会话主线程都写了 `savings_totals.json`，4e12779f… 写了第一行 `rate_history.jsonl`。
- 用真实的 payload 形状喂进去的输出：
  ```
  Sonnet 5.5 | ctx 70k/1M | 5h 15% | 7d 13% | ❄ 缓存已冷：下条消息重写约 70k，换任务就先 /clear | 省≈0.0%累计 本会话0.0%(估·上限)
  ```
- 有 test_filter 事件时【模拟】：`… | 省≈73.2%累计 本会话73.2%(估·上限)`。这是合成小会话，只用来测格式，不代表真实比例。
- 解析还没追上（大 transcript 第一次出现）时末尾加 `…`；有未知模型时加 `?`。

**耗时：**

| 场景 | 改前 | 改后 |
|---|---|---|
| 真实 payload，1.4MB transcript，稳态【实测】 | 约 20ms | 约 20ms（首次 50ms） |
| 真实 20MB transcript（本机最大的会话）【实测】 | 约 20ms | 前两次刷新 92ms、83ms（追赶中），之后稳态 36ms |
| 合成 62MB transcript【模拟，S03】 | — | 追赶两次 82ms、56ms，稳态 25–26ms；结果与一次性离线解析完全一致 |

- 解析每次最多花 45ms，没读完的下次刷新接着读（每 5 秒一次）。
- 锁最多等 20ms（节约表）/ 80ms（共享状态），等不到就这次不写、只显示上次的结果。

---

## 6. 修 Codex 复核 I 阻塞项 1（并发状态栏丢窗口记录）

- **原问题**（`REVIEW_I.md` 第 1 项）：`hooks/statusline.sh` 读整个 `ctx_window.json`、改内存副本、再整个替换，读-改-写的过程没有锁。8 个并发进程 × 12 轮里有 8 轮丢记录；丢了记录的会话在 200k 窗口、180k 上下文时不提醒。
- **修法：**
  - `rate.json`、`ctx_window.json`、`rate_history.jsonl` 的读-改-写全程持 `state/.state.lock`（flock，非阻塞轮询，最多等 80ms）。
  - 进锁以后重新读一遍再改；写入一律「临时文件（带 pid）+ rename」。
  - 拿不到锁就这次不写。ctx_window 的记录缺了，下次刷新（5 秒后）条件仍然成立，会补写，所以只会晚一次，不会丢。
  - 节约表用自己的锁：每个会话一把会话锁，用于耗时的解析；另有一把全局锁，持锁很短，用于 totals 和 jsonl。加锁顺序固定（先会话锁、后全局锁），不会死锁。
  - **没有改 context_guard**：守卫读的 `ctx_window.json` 格式不变。
- **验证【模拟，实际进程】：**
  - **S04**：32 个状态栏并发 × 3 轮，每轮保留的记录数 [32, 32, 32]；随后用其中一个会话跑守卫（200k 窗口、180k 上下文），照常提醒。
  - **S06**：16 会话并发写 `rate.json`，始终是合法 JSON，保留同窗口最大的读数。
  - **M07**：16 进程在锁内读-改-写计数器，结果正好 16。
  - **M08**：16 个状态栏并发写 `savings.jsonl` × 2 轮，每行都是合法 JSON，16 个会话都在，合计不重复。

---

## 7. weekly_compare 的修改（第 6 轮 ③）和 H §4

- **价格**：改用 `tools/pricing.py`。pricing.py 缺失（例如 scorecard 的沙箱）时，退回内置的通用倍数，并在报告开头写明（W03）。所以 scorecard 的 WC01–WC09 不受影响，107/107。
- **两种口径都报：**
  - 「本模型单位」：每个模型按自己的输入价归一，和旧报告的数字连续；
  - 「Sonnet 5.5 当量」：= API 美元 ÷ $2/M，Opus 5.5 记 2 倍。
  - 按族的分组表四列并排。
- **日数口径：选「剔除不完整的天」。**
  - 规则：每天成本只用窗口覆盖 ≥98% 的本地日，零头天单独列出来。现在启用后窗口剔除的是 11-05（只有 23:58 之后那 17 次请求）。
  - 理由：每天成本要同样长度的天才可比；按活跃小时折算会把「用了多久」和「用得多狠」混在一起。另外单独加了一行「每活跃小时成本」作参考。
  - history 里的旧字段（days、cost_per_day、calls_per_day）保留原意不变，给旧的读者和测试用；新字段是 full_days、partial_days、*_per_full_day、seq_*、usd_total。
  - 注意：`opus_share` 现在按 Sonnet 当量算（Opus 记 2 倍），比旧口径高。
- **H §4 里属于我的行：**
  - 族名表：opus / sonnet / haiku / fable / mythos，不认识的原样列出，不再并进 other。
  - 动态分组：按实际出现的族生成表。
  - 不认识的族超过 5% 时报警。
  - 状态栏用 payload 的 `context_window_size` / `used_percentage`（S07）。
- **新报告的数字【实测，11-06 23:5x 跑】：**

  | 指标 | 改动前 | 启用后 | 变化 |
  |---|---|---|---|
  | 每次请求成本（Sonnet 当量） | 34.8k | 33.2k | **−4%** |
  | 每次请求成本（本模型单位，缓存读已按 0.05× 修正） | 21.7k | 18.0k | −17% |
  | Opus 成本占比 | 67% | 92% | |

  - 「启用后」只有 1 个完整天（11-06 到 23:5x 覆盖已超过 98%），所以**变化百分比没有意义**，只看趋势。
  - 旧报告的「32.1k→21.5k（−33%）」用的是缓存读 0.1×，高估了缓存读的分量。

---

## 8. 撤回命令（在项目根目录执行）

```sh
cp -p backups/statusline.sh.pre-r6P hooks/statusline.sh                # 状态栏回到改前（立即生效）
cp -p backups/weekly_compare.py.pre-r6P tools/weekly_compare.py
cp -p backups/usaver_status.py.pre-r6P adapters/common/usaver_status.py
cp -p backups/README.md.pre-r6P README.md
cp -p backups/weekly_20261106.md.pre-r6P reports/weekly_20261106.md
cp -p backups/weekly_history.json.pre-r6P reports/weekly_history.json
mkdir -p _to_delete/r6P && mv hooks/savings_meter.py tools/pricing.py tools/tests_r6P.py state/savings state/savings.jsonl state/savings_totals.json state/savings.lock state/rate_history.jsonl state/.state.lock _to_delete/r6P/
```

- 只想关掉节约表、保留其它修复：`mv hooks/savings_meter.py _to_delete/`。状态栏会自动不显示这一段。也可以在状态栏的环境里设 `USAGE_SAVER_METER=0`，但那要改 settings.json，归所有者。
- 只撤回状态栏、保留 pricing / weekly_compare 也可以：只执行第一行。

**副作用说明：**
- 烟测时我用合成 payload 跑过一次真实 state，`rate_history.jsonl` 因此多了一行模型名不对的记录。已经移到 `_to_delete/rate_history.jsonl.r6P-smoketest-synthetic-model`，现在的 `rate_history.jsonl` 是真实状态栏写的。
- 开发副本已移到 `_to_delete/r6P_dev/`。
- 测试只在 `state/_test_tmp/` 下建临时目录，用完删掉的是它自己建的目录。

---

## 9. 已知限制（不确定的都在这）

1. **test_filter 的反事实没有经过 A/B 验证**：F2 里 0 个事件；真实使用里只有 2 个，都是短日志。
2. **Claude Code「存文件 + 预览」的阈值是估的**：26KB 全给、38KB 以上存文件，取 30,000 字符；预览约 2,100 字符（5 个样本 1.7–2.3k）。Claude Code 升级后可能变，可以用环境变量 `USAGE_SAVER_CC_PERSIST_CHARS` / `USAGE_SAVER_CC_PREVIEW_CHARS` 覆盖。
3. token 是用字符数估的，误差约 ±25%。
4. 节约表认 test_filter 事件靠它的输出格式（`[测试输出写入 …]`、`[测试输出已过滤：完整 N 行…]`）。以后谁改了这两行字，要同步改 `savings_meter.MARK`。
5. 守卫和 /clear 的配对靠「同目录、修改时间早于新会话开始」的前一个 transcript。几个会话在同一目录交替运行时可能配错。它不计入总数，影响有限。
6. 「累计」只包括状态栏跑过的会话：`claude -p` 无头运行、Codex、启用（11-06 23:49）之前的会话都不在里面。离线回放可以用 `python3 hooks/savings_meter.py --offline <transcript…>`。
7. 没建模：fast mode、`inference_geo` 1.1×、Batch 5 折、compaction 保留段。
8. 一个会话第一次被看到、transcript 又很大（20MB 以上）时，前一两次刷新是 80–90ms，会显示 `…`。
9. 日数口径的 98% 容差：当天快结束（23:31 以后）时，当天会被算成完整天。
10. `使用说明.md`（中文用户说明）不归我，没改；里面若提到状态栏格式，需要归属人补一句节约表。

---

## 10. 需要所有者决定的事

1. **节约表要不要一直显示？** 现在真实数据下它一直是 0.0%。这是诚实的，但占地方。选项：
   - (a) 照现在一直显示；
   - (b) 只在有事件时显示；
   - (c) 关掉（`mv hooks/savings_meter.py _to_delete/`）。
2. **守卫区间要不要在状态栏显示？** 我的建议是不显示（上限高估约 4.5 倍），只留在 jsonl 里。
3. **要不要加一个基准任务来验证 test_filter 的反事实**：测试输出 200–2000 行、模型不加管道，例如在任务说明里要求原样跑 `pytest -v`。费用估计每对约 $0.2（API 标价）。这是让「省≈X%」有 A/B 依据的唯一办法。
4. **test_filter 对超长日志可能反而更贵**（见额外发现 1）。要不要改，归 test_filter 的负责人。
5. 本轮 weekly_compare 的「每次请求成本」按 Sonnet 当量只降了 4%。报告/README 里旧的「−33%」口径是否撤下，由主会话统一处理。

---

## 额外发现

1. **Claude Code 自己会截断大输出，test_filter 只在一个窄区间里省钱【实测】。**
   - 本机 transcript 里，约 38KB 以上的 Bash 输出被存成文件、只给约 2k 字符预览（5 个样本）。
   - 所以 test_filter 只在「日志 > 40 行、且 < 约 3 万字符」之间省钱。日志更长时，它给的「120 行 grep 片段 + 12 行结尾」（最多约 1 万字符）比 CC 的 2k 预览还长，**可能反而更贵**。
   - 估计收益：很小，因为真实使用里只触发过 2 次，都是短日志。
   - 建议：test_filter 对超长日志把 grep 片段的上限压到约 2k 字符。这是改过滤行为，不归我。
2. **测试命令几乎总是被模型自己加了管道**（F2：0/16 次改写；真实回放：只改写约 5%）。如果要让 test_filter 起作用，比改规则更有效的是在 CLAUDE.md 里写「跑测试不要加 `| tail`」，让 hook 接手。
   - 未测：这会不会让模型看到的内容变多，需要 A/B。
3. **额度读数从现在开始有历史了**（`state/rate_history.jsonl`）。F1 重估 R（Opus/Sonnet 额度比）时缺的就是这个。积累 1–2 周后，可以用 F1 的 NNLS 重算。
4. **价格表的一致性检查可以做成每轮的回归项**：节约表算的成本 / Claude Code 的 `total_cost_usd` = 0.9994–1.0007。以后模型换代、价格变了，这个比值会先变，比读文档更早发现。

---

## 总结

- **做了什么：**
  - 共享价格表 `tools/pricing.py`（按官方页；5.5 代缓存读 0.05×，旧模型 0.1×；Opus 5.5 = 2× Sonnet 5.5；未知模型带标记）。
  - 实时节约表 `hooks/savings_meter.py`：逐事件反事实，写 `state/savings.jsonl`，状态栏和 usaver-status 显示同一个数。
  - 状态栏：加锁和原子写，修了 Codex 阻塞项 1；新增 `rate_history.jsonl`；完成 H §4 里属于状态栏的两行。
  - weekly_compare：新价格、两种口径、只算完整天、动态族分组。
- **数字：**
  - F2 校准：B 臂节约表报 0.00%，实测 −1.7%（不显著，R = 1.008）。守卫上限 12.2% 对实测 2.7%，所以不计入。
  - 真实回放：test_filter 净 −338 token（≈0%）。
  - 价格表和 CC 计费的比值：0.9994–1.0007。
  - 状态栏稳态 20–36ms；大 transcript 追赶时最多 92ms。
  - 测试：tests_r6P 23/23；K_testall 12/12 套件，434/434。
- **要所有者决定：** 见 §10（节约表是否常显、守卫区间是否显示、是否加验证 test_filter 的基准任务、test_filter 超长日志的问题）。
- **撤回：** 见 §8；第一条 `cp -p backups/statusline.sh.pre-r6P hooks/statusline.sh` 就能让状态栏立即回到改前。

交付完成
