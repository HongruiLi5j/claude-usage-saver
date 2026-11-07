# 第 6 轮 P2：节约表返修（Codex 复核 P 第 1 次打回）+ 关 Artifact 计入总数 + 一直显示

负责人：P2。日期：2026-11-07（本地 PST）。接着 reports/round6_P.md 做。

标注约定：【实测】是跑出来的数；【估算】是从实测推出来的，写了算法；【模拟】是合成数据上的测试。

---

## 0. 结论先行

1. **Codex 打回的两项都修了，原样复现做成了测试，都通过【模拟，Codex 的复现脚本原样】：**
   - 第 1 项：本次节省 1.7k（不到 2000）。修前状态栏 43.0%，账本 0.0%。修后状态栏 43.0%，账本 43.0%，usaver-status 也是 43.0%。
   - 第 2 项：进度文件丢失后重建。修前实时值 5.7%，账本停在 17.2%。修后实时 5.7%，账本 5.7%，usaver-status 5.7%。
2. **关 Artifact 的节省已计入总数，并且已上线。** 判断依据是 transcript 里 Claude Code 自己写的 `api-request-shape`，也就是每次真正发给模型的工具表，逐条请求判断。Δ 主会话 13,421、子 agent 13,140，来自 round5_A 的实测。设置类节省留了一个通用登记接口，以后别的设置按同一个办法接入。
3. **节约表一直显示，0% 也显示**（所有者已定）。
4. **测试【实测】：**

   | 测试 | 结果 |
   |---|---|
   | `tools/tests_r6P2.py`（新建） | 15/15 |
   | `tools/tests_r6P.py` | 23/23 |
   | `K_testall` | 14 个套件全过，477/477 |
   | 状态栏稳态耗时 | 真实 payload 29–36ms；合成 2MB transcript、索引里另有 60 个会话时，中位数 29ms。都不超过 50ms |

5. **上线后的真实显示【实测，真实 statusline + 真实 payload，00:3x】：**
   ```
   … | 5h 25% | 7d 14% | ❄ 缓存已冷：… | 省≈1.5%累计 本会话3.8%(估·上限)
   usaver-status：… | 省≈1.5%累计 本会话3.8%(估·上限)（关Artifact 1.5、测试过滤 0.1 个百分点）
   ```
   - 「本会话」是当前总指挥的会话 15030f9e：实际 3.04M，省 0.12M（全部来自关 Artifact：主线程 31 次请求、子 agent 61 次）。
   - 「累计」是节约表启用以来状态栏见过的 4 个会话：实际 19.2M，省 0.30M，其中关 Artifact 0.286M，test_filter 0.011M。
   - 单位都是 Sonnet 5.5 当量 token。

---

## 1. 改了什么（文件）

| 文件 | 改动 |
|---|---|
| `hooks/savings_meter.py` | 修 Codex 第 1、2 项；补写（sweep）和 `--session-end`；设置类节省登记表和 artifact_off；一直显示；进度格式 VERSION 1→2 |
| `adapters/common/usaver_status.py`（只改节约表显示） | 有账本就一直显示（0% 也显示）；加分项（各机制占多少个百分点）；读账本出错时显示 `省≈?`；没装节约表或这台机器从没跑过节约表时不显示 |
| `tools/tests_r6P2.py` | 新建，15 个用例 |
| `README.md` | 「Savings meter」一节加了三条：设置类节省、账本一致性、一直显示 |
| `hooks/statusline.sh`、`tools/pricing.py`、`tools/tests_r6P.py` | **没改**。显示逻辑都在 `savings_meter.render()` 里，状态栏不用动 |

没碰：session_resume.py、project_profile.py、test_filter.py、context_guard.py、tools/heldout/、`~/.claude/settings.json`（只读了 `enableArtifact` 的当前值，是 `false`）。

---

## 2. Codex 第 1 项：变化不到 2000 就不写账本

- **原因**：`_due()` 要求变化同时 ≥5% 且 ≥2000 当量 token 才写，也没有会话结束时的补写。
- **修法 1：值有任何变化就写。** 按账本的写入精度 0.1 比较。这样账本的每个值都等于最后一次算出的值，和显示一致。
  - 代价【实测】：每次请求追加约 2 行（actual 一行 + artifact_off 一行；test_filter 事件很少），约 1.1KB。
  - 到 4MB 时在锁内压缩（原有机制）。实测 4MB 压缩一次 28ms，约每 3,600 次请求一次。
- **修法 2：解析还没追上（pending）时不写账本，也不覆盖合计里已有的完整值。** 免得半截的值盖掉完整的旧值。
- **修法 3：补写（sweep）。**
  - 每次状态栏刷新时顺带做，最多每 30 秒一次，每次最多 12ms。
  - 检查 `savings_totals.json` 里的索引 `idx`：最近 3 天见过的其它会话，只要 transcript（含子 agent）的字节数和上次读到的不同、上次没追上、或者进度文件版本旧，就替它补算并写账本。
  - 那个会话自己的会话锁拿不到（它自己的状态栏正在算）就跳过。
  - 这样「会话最后几次请求之后状态栏没再刷新」的情况，会在任意一个会话的下一次刷新时补上（测试 C4）。
- **修法 4：`python3 hooks/savings_meter.py --session-end`。**
  - 读 SessionEnd hook 的 stdin，不限时补算一次；出错也静默退出 0。
  - **没挂**：挂上要改 `~/.claude/settings.json`。这只是建议，见 §8。有了补写以后，挂不挂只影响「关掉最后一个会话之后到下次开会话之前」账本是否最新。

## 3. Codex 第 2 项：进度文件丢失后，修订号从 1 重来

- **原因**：rev 是「上次的 rev + 1」，存在进度文件里。进度文件丢了 rev 就回到 1，而合并时取 rev 最大的那行，结果一直取到旧行。
- **修法**：`rev = max(毫秒时间戳, 上次 rev + 1, 本进程上次 rev + 1)`。
  - 重建以后新行的 rev 也一定更大。旧版本写的 rev 是 1、2、3……，也一定更小，所以上线时不用迁移旧账本。
  - rev 相同时取文件里靠后的那行（追加都在全局锁里，靠后就是更新）。
  - 测试 C2、C3 覆盖。
- **上线时的处理**：进度格式升到 VERSION 2。旧进度会被重算：当前会话在它自己的下一次刷新时重算，其它会话由补写重算。重算后的行 rev 更大，会自动盖掉旧行。

---

## 4. 关 Artifact 计入总数

### 4.1 怎么判断某次请求「已经关了 Artifact」：选 transcript 里的工具表，不读 settings.json

**做法**：Claude Code 在工具表或系统提示变化时，会往 transcript 里写一行 `api-request-shape`，内容是真正发给模型的 tools 和 system。【实测】本机近期的 transcript 和子 agent 文件里都有这一行。对每条请求，取它所在对话链最近的一次 shape。满足下面三条才计：

1. 工具表里**没有** `Artifact`；
2. 看得出它「本来会有」Artifact：
   - 主线程：要有 `AskUserQuestion`。交互式才有它；`claude -p` 本来就没有 Artifact（round5_A §0），不能算成省的。
   - 子 agent：要有 `Write` 和 `Edit`。Explore / Plan 这类只读 agent 的工具表本来就不含 Artifact，不能算。
3. 请求时间不早于 `since = 2026-11-07T07:42:00Z`，也就是所有者改设置的时间：settings.json 的修改时间 11-06 23:42 PST。

**为什么不读 settings.json 的当前值：**
- **它看不出某个会话开始时设置是什么。** 【实测】当前两个会话都是 23:42 之前开的：
  - 主线程的工具表里**仍然有 Artifact**；
  - 但 23:42 之后新开的子 agent 工具表里**已经没有了**（Claude Code 对新子 agent 热加载了设置）。
  - 只看 settings.json，要么把这些主线程请求也算成省了（多算），要么把整个会话都排除（漏掉子 agent 那部分）。
- **能自动纠错。** 以后所有者用 `A_apply.py --restore artifact` 重新打开 Artifact，工具表里就又有它了，自动停止计入。settings.json 的读数只反映「现在」。
- **每次刷新不用读 settings.json。**

**代价**：没有 shape 记录的请求不计（保守）。旧版 Claude Code 或别的工具可能没有这一行。

### 4.2 每次请求怎么计价【公式】

节省 = Δ × 这次请求里「固定前缀」的单价。用这次请求自己的模型价格（tools/pricing.py），换算成 Sonnet 5.5 当量。

| 这次请求的固定前缀是怎么处理的 | 判断条件 | 单价 |
|---|---|---|
| 从缓存读的 | `cache_read ≥ 4096` | 缓存读价 |
| 被写进缓存（首请求或缓存冷了） | 不满足上一行，并且有缓存写入 | 写入价：这次请求 1h 档写入多于 5m 档时按 2×，否则按 1.25× |
| 完全没用缓存 | 既没有缓存读，也没有缓存写入 | 输入价 |

Δ：主线程 13,421，子 agent 13,140（round5_A §1，交互式探针首请求实测，Opus；Sonnet 主线程是 13,486，差 0.5%）。

**为什么「缓存写」要按前缀判断，而不是「有 cache_creation 就算写」：**
- 热请求几乎每次都有少量 cache_creation，那是新消息写入；前缀本身是读的。
- 如果有 cache_creation 就按写入价算，会高估 12–20 倍。
- 工具定义和系统提示在缓存顺序的最前面。只要 cache_read 不小，前缀就一定是读的。
- 门槛 4096 的依据【实测】：本机 5 天 26.7k 次请求里，cache_read 要么是 0，要么 ≥4096，占 99.98%。

**一个直观的数【估算】：**
- Opus 5.5 的热请求，每次省 13,421 × $0.20/M ÷ $2/M ≈ 1,342 当量 token。
- 按 round6_P 测的每次请求成本 33.2k 当量算，约 3.9%。
- 首请求或缓存冷了重写的那次，按 1h 档省 13,421 × 4 ≈ 53.7k，按 5m 档省 13,421 × 2.5 ≈ 33.6k。
- 所以新开的会话会比 1.5% 高。当前会话（主线程开于改设置之前）是 3.8%。

### 4.3 通用接口（以后其它经过实测的「设置类」节省）

- **登记表** `SETTING_SAVINGS`，或者调用 `register_setting({...})`。每项的字段：

  | 字段 | 含义 |
  |---|---|
  | `id` | 设置名 |
  | `delta_main`、`delta_sub` | 主线程、子 agent 每次请求少带的 token（实测） |
  | `since` | 从什么时候起算 |
  | `detect(工具名集合, 系统提示字符数, 是否子 agent)` | 根据工具表判断这个设置是否在起作用 |
  | `source` | 实测出处，必填 |

- 判断函数可以直接用 `_tools_absent_detector("工具名")`。系统提示类的设置（例如 skillOverrides 会让 skill 列表变短）可以按 `系统提示字符数` 写判断函数。
- 计价完全复用 §4.2。账本里 `mechanism = "setting:<id>"`，usaver-status 自动按它出分项。
- **可配置**：`config.json` 里写 `"savings_meter": {"settings": {"artifact_off": {"delta_main": …, "delta_sub": …, "since": …, "enabled": false}}}` 就能覆盖。
  - 解析时只累计「每 token 单价之和」，显示时才乘 Δ，所以**改 Δ 立即生效，不用重算**（测试 A6）。
  - 改 since 或判断规则要重算（升 VERSION）。
  - config.json 不归我，我没写进去，只读这个键（没有就用默认）。

### 4.4 已知限制（不确定的都在这）

1. Δ 是**首请求**实测的，这里假设整个会话里固定前缀都一样大。会话中途通过 ToolSearch 加载的工具，在有没有 Artifact 的两种情况下一样多，所以不影响 Δ。【估算】误差主要来自模型差别（0.5%）。
2. 反事实假设：两种情况下缓存命中与否完全一样（同一次请求要么都读、要么都写）。Artifact 不改变缓存断点，这个假设合理，但没有 A/B 验证过。
3. 自定义 agent 如果工具表里有 Write/Edit，但定义里本来就排除了 Artifact，会被多算。本机现在的自定义 agent 都是 All tools，不受影响。
4. 没有 shape 记录的请求不计。compaction 的摘要请求不在 transcript 里，也不计（少算）。
5. 离线回放（`--offline`）里，`claude -p` 的子 agent 可能被多算（主线程有 AskUserQuestion 检查，子 agent 没有）。状态栏看不到 `-p` 会话，所以实时数不受影响。
6. 显示文字仍然是「(估·上限)」。Artifact 这一项其实更接近点估计（实测 Δ × 每次请求的价格），test_filter 那项仍是上限。README 写明了。

---

## 5. 一直显示（所有者已定）

| 情况 | 状态栏 | usaver-status |
|---|---|---|
| 没有任何数据（新会话还没有请求、transcript 不存在） | `省≈0.0%累计 本会话0.0%(估·上限)` | 有账本就显示，0% 也显示 |
| 中途出异常 | 退回显示上次的合计 | 读账本出错时显示 `省≈?` |
| 节约表没装全（缺 savings_meter.py 或 pricing.py；或者只拷了 adapters、这台机器从没跑过节约表，例如只用 Codex） | 不显示这一段 | 不显示这一段 |

最后一行是原测试 S02 和 adapters 测试的约定，不算「节约表在跑却不显示」。

---

## 6. 测试【实测】

### tests_r6P2.py，15/15

| 用例 | 内容 |
|---|---|
| C1 | Codex 复现 1 原样：状态栏 (0.0→43.0)，账本 43.0%，usaver-status 43.0%；节省 1.7k < 2000 |
| C2 | Codex 复现 2 原样：重建前账本 17.2%；重建后实时 5.7%，账本 5.7%，usaver-status 5.7% |
| C3 | rev 单调：旧版本的 rev 1/2/3、同一毫秒连写、rev 相同取靠后的行 |
| C4 | 补写：会话 X 最后两次请求之后没再刷新；Y 刷新，不到 30 秒不补，30 秒后补，X 的账本 0.0% → 48.5%，等于离线全量；状态栏子进程路径也测了 |
| C5 | 旧 v1 进度文件 → 第一次补写建索引并重算；`--session-end` 正常补算；坏输入退出 0 |
| A1 | 生效前：工具表里没有 Artifact，但请求在 07:42Z 之前 → 不计；工具表里有 Artifact → 不计 |
| A2 | 生效后（主线程）：1h 写 + 2 次读 + 5m 冷重写 = 13,421 × (2.0 + 2×0.05 + 1.25) = 44,960，和预期完全相等；账本有 `setting:artifact_off` |
| A3 | 子 agent：通用子 agent 按 13,140 计；Explore 类不计；带 Artifact 的不计 |
| A4 | 没有缓存写的会话：全是缓存读，每次按读价；完全不用缓存的按输入价；Opus 是 Sonnet 的 2 倍 |
| A5 | 工具表中途从有 Artifact 变成没有：只算变化之后的请求；`-p` 形状不计；没有 shape 记录不计 |
| A6 | config.json 改 Δ 立即生效（Δ 翻倍，节省翻倍）；`enabled=false` 就不计 |
| A7 | `register_setting` 接入一个假设置（Foo），走同一套计价；字段缺失时报错 |
| A8 | 状态栏端到端：状态栏 = 账本 = usaver-status，usaver-status 带「关Artifact」分项 |
| D1 | 一直显示：没有数据时显示 0.0%；从没跑过节约表时 usaver-status 不显示 |
| T1 | 状态栏稳态耗时（含补写检查）：12 次中位数 29ms，最大 32ms |

### 其它

- **tests_r6P.py：23/23**，原有用例一个没改。S03 的 62MB 追赶两次 84/67ms，稳态 27ms。
- **K_testall：14 个套件全过，477/477**（上线前基线 13 个套件 462/462，加上新增的 tests_r6P2 15 个）。
- **adapters 测试**：开发中曾有 2 个失败。原因是我起初让 usaver-status 在「没有账本」时也显示 0%，和 H 的测试约定冲突。改成「有账本才显示」以后通过。我没改 H 的测试。

---

## 7. 上线过程和烟测【实测】

1. **备份**到 `backups/*.pre-r6P2`：savings_meter.py、statusline.sh、pricing.py、usaver_status.py、tests_r6P.py、README.md。另外存了上线前账本的前 69 行（新代码写的第一行之前的部分）：`backups/savings.jsonl.pre-r6P2`。
2. **在副本里改和测**：副本在 `/private/tmp/claude-501/r6P2_dev/suite`。
   - Codex 的复现脚本改成指向副本以后跑了一遍；
   - 用**真实 state 的拷贝**加真实 transcript 干跑了一遍。
3. **替换**：替换前确认真实文件和备份逐字节相同（没有别人在这期间改过），然后拷入。savings_meter.py 先写临时文件再 mv，原子替换。
4. **烟测**：真实 statusline + 真实 payload 形状（`state/last_status_sample.json`，去掉 rate_limits，免得往额度历史里写旧读数）：
   ```
   Sonnet 5.5 | ctx 70k/1M | 5h 25% | 7d 14% | ❄ 缓存已冷：下条消息重写约 70k，换任务就先 /clear | 省≈1.5%累计 本会话3.8%(估·上限)
   claude-opus-5-5 | ctx 90k | 5h 25% (约 04:12 用完) | 7d 14% | 省≈1.5%累计 本会话3.8%(估·上限)（关Artifact 1.5、测试过滤 0.1 个百分点）
   ```
   - 第一行的模型名来自样例 payload。
   - 8 次耗时 29–36ms（第一次 55ms，是冷磁盘缓存）。
   - 4 个会话的账本和合计逐个一致：f250ba1e 1.30%/1.30%，15030f9e 3.74%/3.74%，另外两个旧会话都是 0/0。
5. **补写已在真实 state 上跑过**：两个 11-01、11-03 的旧会话（v1 进度）已经按 v2 重算完。它们开在改设置之前，所以 Artifact 节省为 0，符合预期。

## 8. 撤回命令（在项目根目录执行）

```sh
cp -p backups/savings_meter.py.pre-r6P2 hooks/savings_meter.py; cp -p backups/usaver_status.py.pre-r6P2 adapters/common/usaver_status.py; cp -p backups/README.md.pre-r6P2 README.md; mkdir -p _to_delete/r6P2; mv tools/tests_r6P2.py _to_delete/r6P2/; mv state/savings.jsonl _to_delete/r6P2/savings.jsonl.after-r6P2; cp -p backups/savings.jsonl.pre-r6P2 state/savings.jsonl
```

- **账本要一起换回去**：新行的 rev 是毫秒时间戳，旧代码写的 rev 是小整数，会一直输给新行。
- 进度文件（v2）旧代码会认成版本不对，自动重算，不用处理。
- `savings_totals.json` 里多出的 `idx`、`sweep_ts` 旧代码会忽略。
- 状态栏没改，不用撤。

**建议（所有者决定，要改 settings.json，我没改）**：在 SessionEnd 里再挂一条 `python3 /Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/savings_meter.py --session-end`，和现有的 session_resume.py 并列。收益很小：有补写以后，只影响「关掉最后一个会话之后到下次开会话之前」账本是否最新。

## 9. 需要所有者决定的事

1. 要不要挂 SessionEnd（见 §8）。我的建议是可挂可不挂。
2. 显示文字要不要从「(估·上限)」改成「(估)」？现在总数的大头是关 Artifact，它更接近点估计。我没改，因为原测试和 README 都按现在的文字写的。
3. 当前总指挥会话的主线程是改设置之前开的，主线程每次请求仍带着约 13.4k 的 Artifact 前缀。【估算】Opus 热请求每次多花约 1.3k 当量 token。下次换会话（/clear 或新开）时这部分就没了。

---

## 额外发现

1. **transcript 自带「每次请求发了哪些工具、系统提示多长」（`api-request-shape`）【实测】。**
   - 这意味着固定开销不用再靠 A 那样的探针去测：每个真实会话都能连续测，而且能按设置自动归因。
   - 以后 skillOverrides、disableClaudeAiConnectors 这类设置，都能用系统提示字符数或工具表接入 §4.3 的登记表。
   - 估计收益：让所有设置类优化都有实时、按会话的账，不用再单独花额度做实验。
2. **设置是热加载的，但只对新开的子 agent 生效，主线程不变【实测】。**
   - 23:42 改了设置以后，23:28 开的会话的主线程仍然带 Artifact，之后开的子 agent 已经没有了。
   - 建议：以后应用设置类优化，主会话提示一句「换会话后才对主线程生效」。
   - 估计收益：主线程 Opus 每次请求约 1.3k 当量 token（≈4%），直到换会话为止。
3. **不同会话的子 agent 文件可能同名**（例如 agent-ade5ca148ee01e72 同时出现在 f250ba1e 和 15030f9e 下），但 message.id 完全不重叠，所以没有重复计费【实测】。以后谁要按 agent 文件名去重，要注意这一点：**不能按文件名去重**。
4. 补写机制顺带修了 round6_P §9 限制 6 的一部分：之前状态栏没刷新到的会话尾巴，现在会被补上。但 `claude -p` 无头会话仍然不在「累计」里（状态栏看不到它们）。

---

## 总结

- **做了什么**：
  - 修了 Codex 打回的两项：账本「有变化就写」、pending 时不写、跨会话补写、`--session-end`；rev 改成毫秒时间戳。
  - 关 Artifact 的节省计入总数：按 transcript 的工具表逐条请求判断，Δ 主 13,421 / 子 13,140（A 实测），按每次请求的前缀单价计；留了通用的设置类登记接口，Δ 可以在 config.json 里覆盖。
  - 一直显示，0% 也显示。
  - usaver-status 加了分项。
- **数字**：
  - 测试：tests_r6P2 15/15，tests_r6P 23/23，K_testall 477/477。
  - 状态栏稳态 29–36ms（≤50ms）。
  - 真实显示：`省≈1.5%累计 本会话3.8%(估·上限)`。累计 19.2M 里省 0.30M，其中关 Artifact 0.286M。
- **要所有者决定**：见 §9（SessionEnd 挂不挂、显示文字、换会话）。
- **撤回**：见 §8，一条命令。

交付完成

---

## P3：Codex 复核 P2 打回（P 这条线第 2 次）——设置停用后旧记录不撤销

日期 2026-11-07。只修这一项，其余不动。

### 问题和原因
- **问题**：设置停用后，旧的节省记录一直留在账本里。复现【模拟，Codex 原脚本】：关 Artifact 的节省是 26,842，然后 `artifact_off.enabled=false`。状态栏变成 0.0%，账本和 usaver-status 停在 30.7%，连刷 5 次都不变。改 since 加重建进度文件，也一样留着旧记录。
- **原因**：`flush()` 只遍历「现在产生的记录」。不再产生的 event_id 没有新行去盖它。另外进度文件重建以后 `flushed` 就没了，根本不知道账本里有哪些旧记录。

### 修法（只改 `hooks/savings_meter.py`）
1. **撤销行**：`flush()` 先检查以前写过、现在不再产生的记录，各追加一行 `counted:false, revoked:true, saved_weighted_tokens:0`。rev 更大，所以会盖掉旧值；每个事件只写一次。之后这个事件又出现（例如重新打开设置），按正常的「值变了」规则再写一行 counted 的记录。
2. **重建时找回旧记录**：进度里没有 `flushed`（进度文件丢了，或者第一次见这个会话）时，从账本里只解析含这个会话 id 的行，补出 `flushed`。每个会话最多一次。
3. **设置指纹 `sfp`**（启用了哪些设置，以及各自的 since）记在进度文件和补写索引里：
   - 指纹变了，这个会话自动重算，重算时保留 `flushed`，然后撤销不再产生的记录。
   - 补写也会挑出指纹过期的其它会话，**不受 3 天限制**。所以停用以后，所有会话的总数都会被撤销。
   - Δ 不在指纹里（显示时才乘，改了立即生效，跟以前一样）。
   - P3 上线前的 v2 进度没有 `sfp`，直接记上当前指纹、不重算（上线前后的设置相同）。
4. 实际情况：所有者执行 `A_apply.py --undo` 重新打开 Artifact 以后，新请求的工具表里又有 Artifact，自动不再计入；undo 之前已经省下的照常保留，那部分确实省了。在 config 里停用、改 since，以及重算后消失的事件，都会被撤销。

### 测试【实测】
- **tests_r6P2.py 新增 4 个用例，共 19/19：**

  | 用例 | 内容 | 结果 |
  |---|---|---|
  | P1 | Codex DISABLE_SETTING 原样 | 30.7% → 停用 → 状态栏、账本、usaver-status 都是 0.0%；连刷 5 次不变；账本行数 3→3（撤销只写一次）；重新打开后三处都回到 30.7% |
  | P2 | Codex RECOMPUTE_REMOVES_EVENT 原样（since 改到 2027 + 删进度） | 三处都 0.0% |
  | P2 | 只改 since、不动进度文件 | 自动重算，三处都 0.0% |
  | P3 | 另一个 5 天前的会话 | 停用后，补写之前累计 23.4% 三处一致（都还是旧值）；补写之后三处都 0.0% |
  | P4 | transcript 被改写、test_filter 事件消失 | 撤销，61.0% → 0.0%；只改 Δ 不会误撤销 |

- **Codex 原脚本**：独立脚本 `independent_checks.py` 指向修好的副本跑。DISABLE_SETTING 修后是 `{statusline (0.0,0.0), ledger_saved 0.0, ledger_pct 0.0, cli (0.0,0.0)}`，RECOMPUTE_REMOVES_EVENT 也一样。原脚本里断言「ledger_saved>0」（也就是断言 bug 存在）的那两句因此失败；改成断言 =0 以后跑完（`INDEPENDENT_CHECKS_COMPLETED`）。改过的副本在 `/private/tmp/claude-501/r6P3_codexcheck/independent_checks.py`。
- **K_testall**：15 个套件全过，**520/520**。期间别人加了 tests_r6I3，r6T、r6I2 的用例数也变了。
- **上线后在真实 state 上烟测**：状态栏稳态 27–31ms；没有误撤销的记录；4 个会话的账本和合计逐个相等（1.30/1.30、3.92/3.92、0/0、0/0）；索引里已经记上指纹。

### 上线和撤回
- **备份**：`backups/savings_meter.py.pre-r6P3`、`backups/tests_r6P2.py.pre-r6P3`、`backups/README.md.pre-r6P3`、`backups/savings.jsonl.pre-r6P3`。
- **上线**：替换前确认真实文件和备份逐字节相同。savings_meter.py 先写临时文件再 mv。README 只在 Savings meter 一节改了一句（改 since 或停用会自动重算并撤销）。
- **撤回到 P2**（在项目根目录执行）：
  ```sh
  cp -p backups/savings_meter.py.pre-r6P3 hooks/savings_meter.py; cp -p backups/tests_r6P2.py.pre-r6P3 tools/tests_r6P2.py; cp -p backups/README.md.pre-r6P3 README.md
  ```
  账本不用换：撤销行是 counted:false，P2 的代码读到也会按 rev 正常合并；P2 的代码会忽略 `sfp` 和索引的第 7 项。

### 已知限制
- 停用以后、补写还没轮到的其它会话，在这段时间里状态栏累计、账本、usaver-status **三处一致地**显示旧值（测试 P3 的「补写前」）。补写每 30 秒一次，每次最多 12ms，会话多、transcript 大时要几轮才能全部追上。
- transcript 已被删掉的会话没法重算，旧记录保留。30 天后归档的会话不再变。

P3 交付完成
