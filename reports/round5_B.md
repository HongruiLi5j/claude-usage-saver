# 第 5 轮 B：长上下文与无人值守（通宵）运行

代号 B，2026-11-06。**没有改** `~/.claude/settings.json`、`settings.local.json`、`~/.claude/agents/`（开始和结束各核对了一次 sha，见第 4 节）。所有实验都在 scratchpad 的临时目录里跑，transcript 只做统计，没有抄对话正文。

标注规则：**【实测】**＝跑出来的数字，或者 transcript 里读出来的数字；**【读码】**＝读 henchmen 源码或 Claude Code 二进制里的 JS 得出的结论；**【模拟】**＝用 B_sim.py 在真实请求序列上算出来的；**【未证实】**＝没能验证。

> 环境说明：实验中途（18:44）Claude Code 被**自动更新**成了 2.1.319，不是我升级的。E1–E4、E6、E7 跑在 2.1.318 上；E5b、Concise、跨目录缓存、权限测试跑在 2.1.319 上。每个实验用的版本都从 transcript 的 `version` 字段核对过。

---

## 0. 先看结论

1. **`autoCompactWindow=300000` 在四种会话里都生效**【实测】：交互会话、`claude -p`、子 agent、仿 henchmen 后台 agent。henchmen 的 window agent 本身就是交互会话。触发点大约是"设置值 − 33k"：
   - 300k：`-p` 在 252k→274k 之间触发，交互会话在 269k→292k 之间触发；
   - 150k：在 115k→138k 之间触发；
   - 子 agent 用 120k：在 87k→110k 之间触发。

   henchmen 删掉的只是进程**继承来的** `CLAUDE_CODE_*` 环境变量。settings 里的 `env` 块照样生效（实测 DISABLE_1M、AUTO_COMPACT_WINDOW 都传进去了），项目级 `.claude/settings*.json` 也会覆盖用户级。
2. **重新看 11-04 那一晚**【实测】：
   - 那 9 个 Opus 会话是 henchmen 的 **window（交互）agent**，模型名不带 `[1m]`。这个账号上 opus、sonnet、haiku 默认就是 1M 窗口，加不加 `[1m]` 一样。当时还没有设 autoCompactWindow，所以会话一路涨到 60–96 万都没有自动压缩。
   - 那晚 11 次压缩**全部是 Claude Code 自带的空闲压缩**：每次都发生在上一条回复之后 54–55 分钟，也就是 1 小时缓存寿命的 90%。
   - 26M 冷重写里，**约 70%（15.7M）是撞了用量上限之后的空闲造成的**。等限额期间空闲压缩不会执行（代码里遇到 `near_limit` 就放弃），限额恢复后会话自动接着跑，把 40–85 万上下文按 2× 整段重写。剩下 6.8M 是 9–18 万的会话，低于空闲压缩默认门槛 20 万，所以没有被压缩。
3. **无人值守模式**【模拟，11-04 数据，按 F2 的口径 cache_read=0.05×，见第 3 节】。那晚实际成本 151.2M，模型重放 155.0M（误差 +2.5%）：
   - 已设的 300k 上限能省 **37–44%**，即约 57–68M/晚。
   - 推荐方案 O：**240k 上限 + 空闲压缩门槛 10 万 + 撞限额后结束、恢复后新开**。比 300k 再省 **5–12M/晚**（那晚成本的 3–8%）。压缩后重读量取 0、30k、60k 三档，三档都比 300k 省。
   - 旧口径（0.1×）下我原来推荐 180k。换成新口径后，读缓存变便宜，压缩太勤反而多写缓存：180k 在重读 60k 时比 300k 贵 15M，150k 以下更差，所以**改推荐 240k**。
   - 撞限额后冷恢复（并入 K 的发现）：那晚有 14 次，写入成本 15.7M，占那晚的 10.4%。K 的统计是占总量 2.3%，分母不同。单加"撞限额后结束/新开"（方案 L）在三档重读下都比 300k 省 5–7M。
4. **产出**：
   - `tools/round5/B_unattended.py`：只改一个项目目录的 `.claude/settings.local.json`，默认值是 240k + 空闲门槛 10 万，支持 `--dry-run`、`--apply`、`--undo`、`--status`，在临时目录里测了 9 个用例，默认值改成 240k 之后又测了一遍 apply 和 undo；
   - `tools/round5/B_segment_run.py`：分段 + 交接 + 新会话的跑法，用 haiku 实跑了 3 段，撤回也测过；
   - `tools/round5/B_compact_audit.py`：通宵之后用它检查压缩和冷重写；
   - `tools/round5/B_sim.py`：模拟；
   - `tools/round5/B_probe.py`：实验脚本。
   - **没有新写 hook**，理由见 2.4。
5. **E 那几项**【实测】：
   - Concise 输出风格在 `-p` 下 output **−40%**（3,241 → 1,936 token，n=3，只是小样本）；
   - `--exclude-dynamic-system-prompt-sections` 跨目录**只多共享约 0.8k token**，E 推测的约 2 万**不成立**；
   - `permissions.denyInsteadOfPrompting`：本机已被自动更新到 2.1.319，但我**构造不出会弹权限框的场景**（default 模式下 `touch`、`curl` 都直接放行），所以效果**未证实**。
6. **Codex CLI**【读码】：0.163.0 有 `model_auto_compact_token_limit`、`model_auto_compact_token_limit_scope`、`model_context_window`、`compact_prompt` / `experimental_compact_prompt_file`、`model_post_turn_compact_threshold_percent`、`tool_output_token_limit`，还有 PreCompact/PostCompact hook，可以写进 profile。**没找到空闲压缩**。实测因为 Codex 额度用完（到 23:29）没跑成。思路可以照搬：低压缩线 + 分段交接。

---

## 1. 实测：压缩和上下文设置对哪些会话生效

### 1.1 方法

- 在 scratchpad 里生成 40 个文本文件（每个约 110KB）。实测每次 Read 让上下文涨约 23k：sonnet 一次读完一个文件（被截断），opus/haiku 分段读。prompt 让模型逐个 Read，**不准并行**，上下文就会一路涨。
- 跑完以后用 `B_compact_audit.py` 读 transcript：每次请求的上下文 = input + cache_read + cache_write；`compact_boundary` 的 `trigger`、`preTokens`、`postTokens` 都是 transcript 原值。
- "仿 henchmen 后台 agent"严格照 henchmen 0.2.1 源码 `launch()` 的参数：
  - `-p --verbose --input-format stream-json --output-format stream-json --settings '<只有 PreToolUse hook 的 JSON>'`；
  - 进程环境里删掉 `CLAUDECODE` 和全部 `CLAUDE_CODE_*`；
  - prompt 按 stream-json 从 stdin 送进去。
- 交互会话用 expect 在伪终端里开 `claude`，自动点信任目录、输入 prompt，相当于 henchmen 的 window agent。**第一次交互实验（E5）作废**：子进程继承了我这个会话的 `CLAUDE_CODE_SESSION_ID` 等变量，没有写 transcript。E5b 把 `CLAUDE*` 变量全部清掉后重跑。

### 1.2 结果【实测】

| # | 会话类型 | 上限从哪来 | 版本 | 压缩前最大上下文 → 下一次会到 | compact（transcript 原值） | 结论 |
|---|---|---|---|---|---|---|
| E1b | `claude -p`（sonnet） | 用户级 settings 300000 | 318 | 252k → 约 275k | auto，pre 265,079 → post 21,258 | ✅ 生效 |
| E2 | 仿 henchmen 后台（opus） | 用户级 300000（`--settings` 只给了 hooks） | 318 | 250k → 约 273k | auto，pre 263,095 → post 20,923 | ✅ `--settings` 是叠加的，不会覆盖用户级 |
| E5b | 交互（sonnet，相当于 window agent） | 用户级 300000 | 319 | 269k → 约 292k | auto，pre 282,463 → post 22,196 | ✅ 生效，触发点比 `-p` 晚约 20k（交互的起始上下文 42k，`-p` 是 21k） |
| E3b | 仿 henchmen 后台（sonnet） | `--settings` 的 `env.CLAUDE_CODE_AUTO_COMPACT_WINDOW=150000`，进程环境里没有这个变量 | 318 | 115k → 约 138k | 两次，pre 128,811 / 130,392 | ✅ settings 的 env 块能传到 henchmen agent |
| E6b | `claude -p` | **项目级** `.claude/settings.json` 150000，用户级是 300000 | 318 | 115k → 约 138k | 两次，pre 128,915 / 130,369 | ✅ 项目级覆盖用户级 |
| E4 | **子 agent**（父会话 `-p`） | 父会话 `--settings` 120000 | 318 | 子 agent 87k → 约 110k | 子 agent 两次，pre 99,213 / 100,756 | ✅ 子 agent 也按这个上限压缩 |
| — | DISABLE_1M | 环境变量 / `--settings` 的 env 块（进程环境已清掉） | 318 | — | `modelUsage.contextWindow`：1,000,000 → 200,000（两种方式一样） | ✅ |
| — | `[1m]` 后缀 | `--model opus[1m]` / `sonnet[1m]` | 318 | — | haiku、sonnet、opus 不加后缀也都是 1,000,000 | 这个账号上加不加一样 |

还有两组空闲压缩实验在跑（见 1.6）。

**触发点规律**：Claude Code 先从窗口里扣掉 min(最大输出, 20k)，再留约 13k 余量【读码】。所以触发点约等于"设置值 − 33k"，和上表一致。

**设置的优先级**【读码，E3b/E6b 实测印证】：
1. 环境变量 `CLAUDE_CODE_AUTO_COMPACT_WINDOW`；
2. 按模型的 `modelSettings.<模型>.autoCompactWindow`；
3. 顶层 `autoCompactWindow`；后面的设置来源覆盖前面的，顺序是用户 < 项目 < 本地 < `--settings` 参数；
4. 服务端或实验下发的值；
5. 模型默认值。

允许范围是 **100k–1M**：低于 100k 按 100k 算，高于 1M 按 1M 算。子 agent 的 agent 定义还可以再加一个更低的上限（代码里叫 `agent_definition` 来源）。

### 1.3 henchmen 怎么拉起会话【读码，henchmen 0.2.1 `src/server.mjs`】

| | 后台 agent（`launch()`） | window agent（`startWindowAgent()`，**11-04 用的就是这种**） |
|---|---|---|
| 怎么启动 | `spawn('claude', ['-p','--verbose','--input-format','stream-json','--output-format','stream-json','--max-budget-usd',…,'--append-system-prompt-file',…,'--allowedTools',…,'--model',…,'--effort',…,'--add-dir',uploads,'--settings','{"hooks":{"PreToolUse":[kill guard]}}', …])` | 在 Terminal 新开一个标签页，执行 `runs/<id>.sh`：`exec claude --name <room/agent> --append-system-prompt-file … <同一套 sessionArgs> -- "<第一条 prompt>"` |
| 环境变量 | `process.env` 去掉 `CLAUDECODE` 和全部 `CLAUDE_CODE_*`，再加 GIT_* 和 HENCHMEN_* | Terminal 登录 shell 的环境，加上脚本 export 的 GIT_* 和 HENCHMEN_*（不删 `CLAUDE_CODE_*`） |
| 读不读用户、项目 settings | 读。`--settings` 是额外叠一层【E2 实测】 | 读【E5b 实测】 |
| room 的 Compacting 选项 | `compact_at>0`：agent 空闲 ≥2 分钟、上下文 ≥ 这个值、距上次压缩 ≥30 分钟时，henchmen 往会话里打一条 `/compact …`。`compact_at=-1`（Off）：给 agent 设 `DISABLE_AUTO_COMPACT=1`，**连 autoCompactWindow 也一起关掉**。0（默认）：交给 Claude Code | 同左 |

11-04 那晚所有 run 的 `mode` 都是 `window`，`site-1104` room 的 `compact_at=0`（只读 henchmen.db 得到）。

### 1.4 11-04 那晚的压缩和冷重写到底是怎么回事【实测】

用 `python3 tools/round5/B_compact_audit.py ~/.claude/projects/-Users-lihongrui-Documents-AQ-sandbox-1104 --min-req 20` 可以复现。

- 9 个 Opus 会话从 09:22 UTC 开始，10:38–10:43 撞到 `rate_limit`，12:30 恢复，13:42 又撞上，17:31 恢复。两次恢复的时候，每个会话都把 37–85 万上下文**整段按 1 小时缓存写入（2×）重写**一遍。
- 19:15 UTC，9 个会话**同时**压缩：`trigger=auto`，pre 598k–962k → post 13–17k。这时距它们最后一条回复（18:20）正好 55 分钟，`system/informational` 提示也在同一分钟。这就是 Claude Code 的空闲压缩：在 1 小时缓存寿命的 90% 处触发，要求上下文 ≥ max(10 万, `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`，默认 20 万)、缓存是 1h TTL、**没有处在限额状态**、设置里 `idleCompaction` 不是 false【读码】。
- 前两次空闲（限额期间）空闲压缩没有发生，对应代码里的 `near_limit` 拒绝条件。
- 冷重写共 48 次、11.2M token，按 2× 算约 22.5M 单位：
  - **撞限额后的 14 次：15.7M（70%）**；
  - 其余 34 次 6.8M，多数是 9–18 万的 Sonnet 接力会话，低于 20 万门槛。

所以 where_tokens_go 第 4 节说"冷重写几乎都是空闲 1 小时以上"是对的，但**主因是限额，不是单纯没人说话**。单纯没人说话的时候，空闲压缩是起作用的。

### 1.5 其它相关设置（速查）【读码，2.1.318/319 二进制】

| 设置 | 作用 | 备注 |
|---|---|---|
| `autoCompactWindow` / `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | 压缩上限 | 见上；env 优先 |
| `modelSettings.<模型>.autoCompactWindow` | 按模型设上限 | 比顶层优先 |
| `CLAUDE_CODE_DISABLE_1M_CONTEXT=1` | 窗口变成 200k | 实测；env 块也行 |
| `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` | 空闲压缩门槛 | 默认 200000，最低 100000 |
| `idleCompaction: false` | 关掉空闲压缩 | 默认开 |
| `autoCompactEnabled: false` / `DISABLE_AUTO_COMPACT` / `DISABLE_COMPACT` | 关掉自动压缩（空闲压缩也一起关） | henchmen 选 Off 时就是这个 |
| `precomputeCompactionEnabled` | 后台预先算好摘要 | 默认开；无头会话的空闲压缩走这条路【读码，见 1.6】 |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | 按百分比改阈值 | fork 不吃这个；不建议用 |
| `CLAUDE_CODE_MAX_CONTEXT_TOKENS` | 只给不认识的模型或第三方用 | 和我们无关 |
| `CLAUDE_CODE_SLEEP_COMPACT(_AFTER_MS)`、`CLAUDE_CODE_COLD_COMPACT` | 内部或实验开关，没有文档 | **不建议用**，没测 |

### 1.6 无头会话有没有空闲压缩（实验设计）

从代码看，空闲压缩有两条路：交互会话走 `host.compact()`；`sdk`（`-p`、stream-json）走"预先算好摘要再替换"，前提是 `precomputeCompactionEnabled` 和替换功能都可用。所以 round4_impl_E 第 4 条"headless 的 henchmen agent 没有空闲压缩"**不一定对**，要实测。

- E7b：仿 henchmen 后台 agent，`env.CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=100000`，读到约 136k 后会话一直开着，58 分钟后再发一条消息。（E7 第一次只读到 93k，低于 10 万门槛，作废重跑。）
- E8：交互会话，项目级 `settings.local.json` 的 env 块设 100000，读到约 157k 后空闲 58 分钟。

### 1.7 空闲压缩实测结果【实测，但结论被限额干扰】

- E7b（无头）和 E8（交互）都读到 13.8 万 / 15.6 万，空闲 58 分钟，**两边都没有发生空闲压缩**（transcript 里没有 compact_boundary）。
- 58 分钟后发的那条消息两边都返回 "You've hit your 5-hour limit"：当时本轮多个 agent 一起把 5 小时额度用完了。
- 在 11-04 的数据里，交互会话的空闲压缩是正常工作的。所以这次交互会话也没压缩，最可能的原因是空闲期间账号已经处在"接近或已到限额"状态，代码里遇到 `near_limit` 就放弃。这正好复现了 11-04 的现象：**撞限额时空闲压缩不会救场**。
- 所以"无头会话有没有空闲压缩"这次**没有测出结论**【未证实】。按主会话"要省"的要求，没有重跑。要补测的话，在额度充足时跑一次：`python3 tools/round5/B_probe.py henchmen --model sonnet --files 5 --settings-json '{"env":{"CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS":"100000"}}' --idle-wait 3480 --tag e7c`。
- 11-04 那晚的 henchmen agent 都是 window（交互）模式，用的就是交互会话的空闲压缩，所以无人值守方案不依赖这一条。

---

## 2. "无人值守模式"设计

### 2.1 四个部件

| 部件 | 做什么 | 怎么落地 | 证据 |
|---|---|---|---|
| ① 低压缩线 | 通宵 / henchmen 的工作目录用 `autoCompactWindow=240000`，约 207k 时压缩 | `B_unattended.py --target <目录> --apply` 写项目级 `.claude/settings.local.json`；用户级保持 300000，白天的交互会话不受影响 | E6b：项目级覆盖用户级；E2、E3b：henchmen agent 读得到 |
| ② 空闲先压缩 | `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=100000`：10 万以上、空闲 54 分钟的会话在缓存过期前就压缩（本来是 20 万） | 同一个文件的 env 块 | 11-04：空闲压缩在交互会话里确实触发了；门槛 10 万的效果看 1.7 |
| ③ 撞限额：结束、恢复后新开，错开恢复 | 限额期间没法压缩（压缩本身也要调模型），所以别让大会话在限额恢复后接着跑。11-04 是 9 个会话一起撞限、一起整段重写（K：占总量 2.3%），而且恢复后的整段重写一下子又把新的 5 小时额度吃掉一大块，12:30 恢复、13:42 又撞上 | `-p` 长任务：`B_segment_run.py` 撞限额后等一段时间，**用 HANDOFF.md 新开一段**，不续用旧会话。henchmen：没有现成开关，靠 ① 把重写量压到 ≤21 万；运行规范里写"撞限额后 leader 用 room 消息让各 agent 交接、结束，限额恢复后**错开几分钟**依次新开" | 11-04 的 70% 冷重写来自这里 |
| ④ 长任务分段 | 一个会话只做一段，结束前写交接，下一段是新会话 | `B_segment_run.py --run`（每段 `--max-turns`，最后一行 `STATUS: DONE/CONTINUE`） | haiku 实跑 3 段 3/3 完成，交接都带标记；每段启动约 50k 单位 |

可选：
- `--concise`：outputStyle Concise，只适合输出给机器或 room 看的运行；
- `--deny-prompts`：`permissions.denyInsteadOfPrompting`，需要 ≥2.1.319，本机现在已经是 2.1.319，但效果没测出来。

### 2.2 henchmen 运行规范（写给所有者，不用改代码）

1. 开 room 之前：`python3 tools/round5/B_unattended.py --target <room 工作目录> --apply`。
2. Room actions → Compacting：保持 "Let Claude Code decide"，**不要选 Off**（Off 会把 ① 也关掉）。想再积极一点可以设"空闲 agent 超过 150000 就压缩"。henchmen 会在 agent 空闲 2 分钟后打 `/compact`，比 Claude Code 自己的 54 分钟早得多，适合"agent 等 room 消息一等就是几小时"的场景。代价是 agent 只是短暂空闲时也会被压缩、丢掉细节。这一项我没有模拟，【未证实】。
3. 不要 9 个 Opus 同时跑：11-04 两次撞限额都是 9 个并行 1M 会话造成的。建议 ≤4 个 Opus，其余用 Sonnet。这一条我没能模拟，因为改并发会改变请求时间线。
4. 跑完：`python3 tools/round5/B_compact_audit.py <room 工作目录> --since <日期>`，看最大上下文、compact 次数和冷重写。
5. 撤回：`python3 tools/round5/B_unattended.py --target <目录> --undo`。

### 2.3 "压缩后要重读文件"的成本

- **实测**（`B_sim.py --reread`，11-04 的 11 次压缩）：
  - 压缩后 40 次请求里，读的是"压缩前读过的文件"的结果共 191k，平均每次压缩 17k，范围 0–50k；
  - 对照段（压缩前 40 次请求）里重读更早文件的有 116k，平均 10.5k；
  - **扣掉对照，每次压缩引起的额外重读约 7k，中位数约 17k 原始量**。
  - 局限：那 11 次都是空闲压缩，压缩后 agent 往往开始新任务。到上限压缩发生在任务中途，重读可能更多。从 Bash 命令里认文件路径只是近似。
- 模拟里每次压缩的代价：读一遍当前上下文（缓存热，×0.05）+ 摘要 8k output（×5）+ 压缩后 50k 起步 + 重读 REEXP（写一次，之后每次请求都要读）。REEXP 取 0、30k、60k 三档。

### 2.4 为什么没有新写 hook

需求是"空闲超过 N 分钟时先写交接并压缩或结束"。hook 做不到：
- hook 不能让会话执行 `/compact`，也不能结束一个交互会话；
- UserPromptSubmit hook 能挡住下一条消息，但这条消息进来时缓存已经冷了，挡住它不会让重写变小，还会让 henchmen 的 room 消息丢失；
- 限额期间任何调用模型的动作都会失败。

能做到的已经有现成机制：Claude Code 的空闲压缩（②）、henchmen 的 compact_at、分段跑法（③④）。所以没有新写 hook。

---

## 3. 模拟：各方案在 11-04 数据上省多少【模拟】

**口径**（按主会话转来的 F2 实测改过）：cache_read×**0.05**，5 分钟写入×1.25，1 小时写入×2，未缓存 input×1，output×5。`--read-mult 0.1` 可以切回旧口径。新口径下，那晚的成本构成是：读 63%，写 25%，output 12%。读的单价减半以后，**压缩线省钱主要靠少写缓存、避免冷缓存整段重写**，压缩本身带来的"压缩后重写 + 重读"在账上的分量变大了。

数据是 `-Users-lihongrui-Documents-AQ-sandbox-1104` 里 11-04 08:00 UTC 之后的 23 个文件（主会话加子 agent），5,405 次请求。实际成本 **151.2M**，模型重放 155.0M（+2.5%），其中冷重写 25.0M。

运行 `python3 tools/round5/B_sim.py --reread` 可以复现。百分比都相对重放基线 155.0M。

| 方案 | 压缩后重读 0 | 重读 30k | 重读 60k | 次数（30k 档） | 冷重写（30k 档） |
|---|---|---|---|---|---|
| A 已设的 300k | 87.4M（−44%） | 94.1M（−39%） | 98.4M（−37%） | 27 | 12.4M |
| B 200k | 79.3M（−49%） | 88.7M（−43%） | 104.0M（−33%） | 66 | 10.0M |
| C 180k | 78.3M（−50%） | 90.2M（−42%） | 116.4M（−25%） | 87 | 9.5M |
| D 150k | 78.3M（−49%） | 102.6M（−34%） | 320.4M（+107%） | 169 | 8.5M |
| E 120k | 85.9M（−45%） | 275.4M（+78%） | 1306M（+743%） | 1063 | 6.9M |
| F 300k + 空闲门槛 10 万 | 84.5M（−45%） | 91.9M（−41%） | 97.4M（−37%） | 37 | 10.6M |
| H 180k + 空闲 + 撞限额后新开 | 73.6M（−53%） | 86.6M（−44%） | 113.8M（−27%） | 101 | 7.5M |
| J 180k 分段交接 + 空闲 + 限额 | 72.2M（−53%） | 84.6M（−45%） | 110.1M（−29%） | 101 | 7.5M |
| K 只加"撞限额后新开"，不改上限 | 99.4M（−36%） | 102.7M（−34%） | 106.0M（−32%） | 14 | 10.1M |
| L 300k + 撞限额后新开 | 81.6M（−47%） | 87.3M（−44%） | 93.5M（−40%） | 34 | 9.3M |
| M 300k + 空闲 + 撞限额后新开 | 78.7M（−49%） | 85.0M（−45%） | 92.1M（−41%） | 44 | 7.5M |
| N 200k + 空闲 + 撞限额后新开 | 73.7M（−52%） | 84.5M（−45%） | 100.0M（−36%） | 81 | 7.6M |
| **O 240k + 空闲 + 撞限额后新开（推荐）** | **75.3M（−51%）** | **83.4M（−46%）** | **93.3M（−40%）** | 58 | 7.6M |

旧口径（0.1×）对照：A 是 −43% 到 −50%；O 是 105.6 / 117.2 / 130.4M，也就是 −58% / −53% / −48%；M 是 −54% / −51% / −46%。

怎么读这张表：
- **已设的 300k 是最大的一块**：每次这样的通宵约省 57–68M（37–44%）。它在所有会话类型上都已经生效（第 1 节），不存在"300k 没生效"的情况。
- **在 300k 之上再省多少**（O 对 A）：重读 0 / 30k / 60k 分别是 **12.1M / 10.7M / 5.1M**。实测每次压缩额外重读约 7–17k，所以估计约 **10–12M/晚**（那晚成本的 6–8%）。按每周 1 次通宵，就是约 10–12M/周；不跑通宵就是 0。
- **为什么不再推荐 180k 或 150k**：读便宜了，压缩一次的代价相对变大（压缩后的重写加上重读，都是写入价）。180k 在重读 60k 时比 300k 贵 15M。150k 以下会反复压缩。Claude Code 自己有"连续快速回涨"的熔断（`rapid_refill_breaker`），实际不会像 E 档那么极端，但方向不变。
- **"撞限额后新开"本身就稳赚**（L 对 A：5–7M），因为它只少写缓存，不增加压缩。并到 K 的发现里：那晚撞限额后冷恢复的写入是 15.7M（14 次，占那晚 10.4%；K 按总量算是 2.3%）。另外，9 个会话同时恢复、同时整段重写，会让新的 5 小时额度很快又被吃光。所以方案里加了一条"恢复后错开新开"。这一条没有量化，因为固定时间线的模拟算不了它。
- **分段交接（J）和压缩（H）差不多**。分段的好处在质量和跨工具，代价是每段要重付约 45k 的启动写入。
- 不确定性：
  1. 时间线是固定的，没有算"成本降了 → 少撞限额"，这会让真实节省**更多**；
  2. 压缩丢细节以后多走的弯路，只用重读量近似；
  3. 摘要 8k、压缩后起步 50k 这些常数误差约 ±20%；
  4. 只有一晚样本；
  5. 0.05 是 F2 的实测比例，我没有自己核对。

**Concise**【实测 + 估算】：
- 实测（`-p`、sonnet，同一个"审阅脚本"任务各跑 3 次）：output 3,231 / 3,068 / 3,425 → 1,556 / 1,544 / 2,709，平均 −40%；回复字数 −20%。
- 新口径下那晚 output 占 12%（约 18M）。按 −40% 算约省 **5–7M/晚**；在已经设了上限的前提下，大概只剩一半。
- 很不确定：n=3，任务单一；room 汇报变短可能漏信息。**建议只在 henchmen / `-p` 运行用，不改主会话**（所有者要详细回复）。

**`--exclude-dynamic-system-prompt-sections`**【实测，2.1.319，sonnet】：
- 两个新目录先后跑同样的 `-p`：
  - 不加：第二个目录 cache_read 11,382，写入 13,230；
  - 加了：cache_read 12,147，写入 12,225。
- 只多共享 **765 token**。同一个目录重复跑能读满 21.8k，写入 0。
- 原因推测：其余的首条消息内容（skill 列表、CLAUDE.md 等）排在被挪进来的动态段后面，跨目录照样对不上。
- **每个新会话省约 1.5k 单位（新口径下也差不多），不值得为它改 henchmen 的启动参数。** E 的推测（约 2 万）**不成立**。

**`permissions.denyInsteadOfPrompting`**【未证实】：
- 本机在实验中途自动更新到了 2.1.319。我在 default 权限模式的交互会话里试了 `touch`（工作目录内）和 `curl`：开和不开这个设置，都是**直接执行、没有弹框**。原因可能是本机的自动放行或沙箱规则，我没有再查。
- 没有构造出"会弹框"的场景，所以它的效果没测到。
- 写进了 `B_unattended.py --deny-prompts`，版本低于 2.1.319 时拒绝写入。
- 意义：henchmen 的 window agent 如果在半夜卡在权限框上，等一小时缓存就冷了。**建议所有者下次通宵前，用一个会弹框的命令在沙箱里试一次再决定开不开。**

---

## 4. 产出和测试

| 文件 | 作用 | 测试【实测】 |
|---|---|---|
| `tools/round5/B_unattended.py` | 写 `<目录>/.claude/settings.local.json`：`autoCompactWindow=240000`、`env.CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS=100000`，可选 `--concise`、`--deny-prompts`、`--window`。默认 dry-run；`--apply` 先备份，并记录到 `backups/round5_B_unattended/<hash>/state.json`；`--undo` 只撤回自己改过、而且之后没被别人改过的键；`--status` 显示当前值 | 在 scratchpad 里跑了 T1–T9：dry-run 不写文件 ✅；新建文件后 apply ✅；重复 apply 什么都不做 ✅；undo 时原文件不存在就把文件移走（不 rm）✅；在已有文件（有 permissions 和 env，IDLE 原值 120000）上 apply ✅；用户改了 window 并加了 env.BAR 之后 undo：保留用户的改动，IDLE 恢复成 120000，BAR 保留 ✅；指向 `~/.claude/settings.json` 时拒绝，rc=1 ✅；window 超出范围时拒绝，rc=1 ✅；`--deny-prompts` 在 2.1.319 上能出 diff（2.1.318 上应该拒绝；当时版本已经更新，这条分支没测到）。**真实 settings.json 的 sha 前后一致** ✅ |
| `tools/round5/B_segment_run.py` | 分段 + 交接 + 新会话；撞限额后新开；`--engine codex` 可以换成 `codex exec`（**未实测**） | haiku、3 段任务：3/3 完成，每段都写了带标记的 HANDOFF，每段约 50–56k 单位；`--undo` 把 HANDOFF.md 恢复成原内容，运行产物移到备份目录 ✅；dry-run 只打印命令 ✅ |
| `tools/round5/B_compact_audit.py` | 只读：每个会话的最大上下文、compact（trigger、pre、post）、冷重写、API 报错 | 在 11-04 的数据和本轮 10 个实验上都跑过 |
| `tools/round5/B_sim.py` | 第 3 节的模拟和重读统计 | 重放误差 +2.5%（0.05 口径）/ +1.5%（0.1 口径）；`--read-mult` 可切换 |
| `tools/round5/B_probe.py`、`B_probe_interactive.exp` | 第 1 节的实验（plain / henchmen / subagent / interactive；`--idle-wait` 测空闲压缩）| plain、henchmen、subagent 三种模式就是 E1–E4、E6、E7 用的脚本。interactive 原来用 screen，点不了信任框，已经改成调用 expect 脚本（就是 E5b 用的那份 `inter.exp`），改完以后**没有再完整跑一遍** |

**没有对任何真实项目执行 `--apply`**。对 AQ-sandbox-1104 应用的命令留给主会话问所有者，见总结。

---

## 5. 跨工具：Codex CLI【读码，codex-cli 0.163.0；实测没跑成】

- **配置项**（二进制里的 ConfigToml 结构，`config.toml` 顶层，也可以写进 `[profiles.<名字>]`）：
  - `model_auto_compact_token_limit`：上下文到多少 token 时自动压缩。对应 Claude 的 autoCompactWindow；Codex 直接写触发点，不需要再扣 33k；
  - `model_auto_compact_token_limit_scope`：`total` 或 `body_after_prefix`（只算前缀之后的部分）；
  - `model_context_window`：手动指定窗口。本机 models_cache：gpt-6.1-sol 是 500k，其它多数 272k，最大 872k，按 95% 算有效窗口；
  - `model_post_turn_compact_threshold_percent`、`model_async_compact_threshold_percent`（1–100）：回合结束后或异步压缩的百分比阈值；
  - `compact_prompt` / `experimental_compact_prompt_file`：自定义压缩提示，可以要求"按 目标/红线/下一步/进度/关键文件 写"，和我们的交接格式一致；
  - `tool_output_token_limit`：单条工具输出上限；
  - hook 事件里有 `PreCompact`、`PostCompact`、`SessionStart`、`SessionEnd`、`Stop`；
  - 压缩触发类型有 `pre_turn` / `mid_turn` / `post_turn` / `user_requested` / `context_limit`；rollout 里压缩记为 `compacted` 项。
- **没找到"空闲压缩"**（二进制里搜 idle 和 compact，只找到 `prevent_idle_sleep`、`stream_idle_timeout_ms`）。【未证实】Codex 那边缓存过期有没有"写入加价"我没有核实；就算没有，冷缓存也意味着整段要按全价重新输入。
- **同一套思路能不能用**：
  1. 低压缩线：可以，写一个 `[profiles.unattended]`，`model_auto_compact_token_limit = 150000`（触发点直接就是 15 万），通宵时用 `codex exec --profile unattended` 跑；
  2. 空闲压缩：没有对应功能。Codex 的 `exec` 本来就是一次性进程，用分段跑法（④）就不会有空闲；
  3. 撞限额后新开、分段交接：`B_segment_run.py --engine codex` 已经留了接口（每段 `codex exec -c model_auto_compact_token_limit=<window−30000>`）。**没实测**：Codex 额度本轮已经用完，`codex exec` 报"You've hit your usage limit … try again at 11:29 PM"；
  4. 交接文件：Codex 读 AGENTS.md，HANDOFF.md 要在任务里写明"先读 HANDOFF.md"（分段脚本的 prompt 已经写了）。
- 建议给 H（adapters/）的 profile 片段（**没写进 ~/.codex/config.toml**）：

```toml
[profiles.unattended]
model_auto_compact_token_limit = 150000
experimental_compact_prompt_file = "<项目>/adapters/codex/compact_handoff.md"
tool_output_token_limit = 8000
```

---

## 6. 不确定和没做的

- 第 3 节的节省是**一晚样本上的模拟**。真实效果要等下一次通宵后用 `B_compact_audit.py` 核对：每个会话最大上下文应该 ≤约 21 万；冷重写每次 ≤约 21 万；撞限额后不应该再出现大于 21 万的整段重写。
- 无头会话的空闲压缩没测出结论（1.7）。
- henchmen 的 `compact_at` 方案（空闲 2 分钟就压缩）没有模拟。
- 限制并发（9 个 → ≤4 个 Opus）的效果没法用固定时间线模拟。
- `denyInsteadOfPrompting` 效果没测出来；Codex 没有实测。
- 本轮实验大约用了几 M 单位的额度（主要是 E2 用的 opus、E5b 和 E8 的交互会话）。我看到状态栏上 5 小时窗口在 66–67%。

---

## 总结

**做了什么**：
- 实测 10 组压缩实验加 4 组附加实验，覆盖 `-p`、仿 henchmen 后台 agent、交互会话、子 agent、项目级设置、settings 的 env 块、DISABLE_1M 和 `[1m]`、Concise、跨目录缓存、权限设置；
- 读了 henchmen 的启动代码和 Claude Code 的压缩代码；
- 重新解读了 11-04 那一晚；
- 在 11-04 数据上模拟了 15 个方案，并按 F2 的 0.05 口径重算；
- 写了 5 个脚本，测过 apply 和 undo。

**数字**：
- 300k 上限在所有会话类型都生效【实测】，每次通宵约省 57–68M（37–44%）【模拟】。
- 再加无人值守模式（240k + 空闲 10 万 + 撞限额后新开），每晚再省约 10–12M（范围 5–12M）【模拟】。
- Concise：output −40%（n=3）【实测】，约 5–7M/晚【估算】。
- `--exclude-dynamic…`：只多共享 0.8k token，不值得【实测】。
- denyInsteadOfPrompting、无头会话的空闲压缩、Codex：都没测出结论。

**要所有者决定**：
1. 下次通宵前要不要对 room 工作目录应用：`python3 /Users/lihongrui/Documents/Projects/claude-usage-saver/tools/round5/B_unattended.py --target ~/Documents/AQ-sandbox-1104 --apply`（先不加参数跑一遍看 diff；henchmen 的 room 用的是只给机器看的输出时，可以加 `--concise`）。
2. 通宵最多开几个 Opus（建议 ≤4 个）；撞限额后是否按规范让 agent 交接、结束、错开新开。
3. denyInsteadOfPrompting 要不要先在沙箱里用会弹框的命令测一次。
4. 本机已经被自动更新到 2.1.319，知悉即可。

**撤回**：
- `python3 /Users/lihongrui/Documents/Projects/claude-usage-saver/tools/round5/B_unattended.py --target <目录> --undo`
- `python3 /Users/lihongrui/Documents/Projects/claude-usage-saver/tools/round5/B_segment_run.py --dir <目录> --undo`
- 本轮没有改任何已有文件，只新建了 `tools/round5/B_*` 和本报告，所以没有 `.pre-r5B` 备份。
