# 第 6 轮 M：适应新模型（context_guard + model-router）

实施者 M，2026-11-06。依据：reports/round5_H.md 第 4 节"新模型写死清单"里属于 `hooks/context_guard.py` 和 `~/.claude/skills/model-router/SKILL.md` 的行，以及 reports/round5_K_explore.md 的 K3。
没有碰 statusline.sh、weekly_compare.py、test_filter.py、tools/pricing.py（这些归 P），没有看 tools/heldout/。

## 1. 改了什么

### 1.1 hooks/context_guard.py（已上线）
H 清单的 3 行（17–18 行的绝对阈值、`lines()` 不知道窗口、usage 读不到时静默）都处理了。

**窗口从哪来**（新函数 `resolve_window()`，按顺序取第一个能用的）：
1. hook payload 自带的 `context_window.context_window_size`（现在的 UserPromptSubmit 不带；以后带了或别的工具带了就直接用）；
2. 状态栏按 session_id 记的 `state/ctx_window.json`（R5I 已有，7 天有效）；
3. 模型 id 里显式写着 `[1m]` → 100 万；
4. 都没有、但 transcript 里有模型名 → **按 20 万假设**；
5. 连模型名都没有（旧格式、合成数据、别的工具）→ 不改阈值。

不按模型族名猜窗口。

**按窗口比例收紧**（`lines()` 新增 `assumed=False` 参数；`window` 参数 R5I 已经加过，签名向后兼容，状态栏不用改）：

| 情况 | 软线上限 | 强线上限 | 说明 |
|---|---|---|---|
| 知道窗口 | 窗口 × 0.6 | 窗口 × 0.8 | 原来是 0.85 / 0.95。Claude Code 大约在窗口 83% 处自动压缩（20 万窗口约 16.7 万），强线放在 95% 永远等不到。0.6 / 0.8 和 H 的 usaver-status 一致 |
| 假设 20 万 | 窗口 × 0.75 | 窗口 × 0.8 | 猜出来的窗口只用来保证强线出现在压缩之前；软线只稍微收一点，免得把真实 100 万的会话提前打扰 |
| 假设 20 万，但上下文 > 20 万 × 0.85 | 不收紧 | 不收紧 | 已经超过 20 万模型的压缩点还没被压缩，说明真实窗口更大（mode.json 里 `window_source=evidence`） |

比例写在 DEFAULTS，config.json 可以覆盖：`window_soft_ratio`、`window_strong_ratio`、`assumed_window_tokens`、`assumed_soft_ratio`、`assumed_lift_ratio`。写错了（不是数、不在 (0,1] 里、软 ≥ 强）就回退默认值。

**Pro 封顶**：逻辑没动。先算项目软线 × 档位倍数，套下限，再套封顶（11-21 起 180k / 195k），最后套窗口比例，取更紧的那个。

**usage 读不到不再静默**：新函数 `scan_usage()`（旧的 `last_usage()` 保留为包装）。mode.json 新增这些字段：
- `usage_unreadable`、`usage_why`、`usage_unknown_fields`；
- `window`、`window_source`、`model`、`ctx_tokens`。

| 情况 | usage_unreadable | usage_why |
|---|---|---|
| transcript 不存在或没给路径 | true | transcript 不存在或没给路径 |
| usage 里一个认识的字段都没有（字段改名） | true | usage 字段名不认识（列出字段） |
| 只有全 0 的 API 报错记录 | true | usage 全为 0 |
| assistant 记录里没有 usage | true | assistant 记录里没有 usage 字段 |
| 有 usage 但不是 Claude 的 assistant 格式（例如 Codex） | true | 不是 Claude assistant 记录的格式 |
| 新会话，还没有回复 | **false** | 新会话（这不算读不到） |
| 能读，但多出不认识的 token 字段 | false | 提示"可能少算"，并在 `usage_unknown_fields` 里列出 |

另外两处顺手修的：
- 原来的 `n = last.get(...) + ...` 遇到 null 或字符串会抛异常，然后被外层吞掉、静默不提醒。现在统一用 `_usage_total()`。
- stdin 不是 JSON 对象时直接返回，不再走到异常分支。

读不到 usage 时只写 mode.json，**不往对话里注入提醒**，否则每条消息都会白花 token。

### 1.2 ~/.claude/skills/model-router/SKILL.md（已上线）
- **分派表**改成按能力档写（最便宜 / 中档 / 最强），表里不出现别名。新增一行"明确的小型编码题 → 中档"，这原来是写在实测段里的结论。
- **"档位与别名"单独一节**（换代时只改这一节）：
  - 对应关系：`haiku` = Haiku 5.5，`sonnet` = Sonnet 5.5，`opus` = Opus 5.5，`fable` = Fable 5.1（顶档）。
  - API 标价取自 claude-api skill 缓存的价目表（2026-10-06）：fable 约为 opus 的 2.5 倍；Haiku 的 prompt 超过 10 万后加价。
  - 写明"订阅额度怎么折算没有实测"。
- **未知新模型的处理**：
  1. 先查定位和价格，归到某一档，再更新这一节（改前先备份）；
  2. 查清之前不把它当便宜档用；
  3. 某个别名不能用了，就升一档，不降档；
  4. 窗口不知道时按 20 万算。
- **fable 的用法**：只在所有者点名、或 opus 在同一任务上已经明显做不好时才用，同时最多 1 个。这条是我定的，需要所有者确认，见第 5 节。
- **实测数字都标了模型版本和日期**：
  - Exercism 那组是 Opus 5.5 vs Sonnet 5.5，2026-11-06。50.2% 和标价比 2:4 吻合，写明换代后要重测。
  - Codex 配对那组是 Opus 5.5 medium，2026-11-06。
  - 启动开销 2 万是截至 2026-11-06 的 14 天均值。
- **绝对值改成比例**：
  - fork 门槛从"父上下文 < 8 万"改成"< 窗口的 8%"（100 万窗口约 8 万，和原规则一样；20 万窗口约 1.6 万）。
  - 拆分读取的规则从"每个约 10 万以内"改成"子 agent 窗口的一半以内，且不超过约 10 万"。10 万是成本线，所以保留。
  - 窗口从哪看：状态栏，或 mode.json 的 `window` / `ctx_tokens`。
- **K3 并入**，新小节"续用老 agent 还是开新的"：
  - 子 agent 缓存只保 5 分钟。缓存热（≤5 分钟）时续用约 C×0.1，用 SendMessage。
  - 缓存冷时：续用约 C×1.25，新开约 (S+B+R)×1.25（S≈2 万启动，B=打回清单，R=要重读的文件）。C ≤ S+B+R 时续用，否则开新 agent，只给精简的打回清单（问题、`文件:行号`、验收标准）和原报告路径。
  - 附了一个例子：C=15 万、B=3k、R=4 万 → 续用 18.8 万，新开 7.9 万。
  - 质量例外：要改的地方依赖老 agent 没写进报告的推理时，照样续用。
  - K3 第 1 条也一起并入：子 agent 等长命令时每次最多等 4 分钟（轮询续缓存），或者让主会话去等。
- "硬上限"里的"Opus agent 最多 2 个"改成"最强档及以上最多 2 个（其中顶档最多 1 个）"。

## 2. 测试结果（全部是实测）

**新测试 `tools/tests_r6M.py`：34/34 通过。** 改前版本（两个备份）只过 3/34，说明这些用例确实能区分新旧行为。
- 窗口（W01–W08）：
  - 200k：120k / 160k，165k 发强提醒；
  - 300k：160k / 240k；
  - 1M：不变；
  - 窗口来自 payload；
  - 缺失 + 有模型名：按假设窗口，140k 不提醒、155k 软、165k 强、180k 不再收紧；
  - 缺失 + 无模型名：不改阈值；
  - `[1m]`；
  - ctx_window 记录超过 7 天。
- 未知模型（U01–U04）：
  - claude-zeta-9 按假设窗口；
  - 未知模型 + 128k 小窗口：76.8k / 102.4k，以窗口为准、低于 100k 下限也照用；
  - payload 里 model 是字典；
  - `<synthetic>` 不算已知模型。
- usage（F01–F09）：字段全改名、没有 usage、transcript 不存在、Codex 格式、新会话、多出未知字段、只有 input、全 0、数值是字符串或 null。
- Pro 封顶（P01–P05）：
  - 封顶日 11-21：11-20 不封顶，11-21 和 11-22 封顶；
  - 封顶 + 200k 窗口取更紧的；封顶 + 宽裕档仍封在 180k / 195k；
  - 封顶前只按窗口收紧；
  - 封顶已生效、没有窗口信息时原意不变；
  - 封顶 + 假设窗口时强线 160k。
- 配置与兼容（R01–R04）：比例写错回退默认；比例可配置；`lines(cwd, mult)` 等于 `lines(..., window=None)`；坏 stdin 退出码 0。
- SKILL 静态检查（S01–S04）：能力档、别名节、版本和日期、K3 公式。

**回归**：
```
python3 tools/round5/K_testall.py
PASS        42/42    tools/tests_r4D.py
PASS        33/33    tools/tests_r4F.py
PASS        26/26    tools/tests_r4G.py
PASS        34/34    tools/tests_r6M.py
PASS        47/47    tools/round5/D_tests.py
PASS        15/15    tools/round5/K_test_doctor.py
PASS        27/27    tools/round5/K_test_patterns.py
PASS        36/36    adapters/tests/test_adapters.py
PASS      107/107    tools/scorecard.py --version current
PASS        21/21    tools/round5/I_adapt_matrix.py
10 个测试套件，10 个通过、0 个失败/超时；可识别的用例 388/388
```
- 改前基线是 354/354。新增的 34 个就是 tests_r6M。
- `I_adapt_matrix.py`：21/21 格子，子检查 329/329。替换前先用 `--hooks <副本>` 跑过一次，也是 21/21。
- K_testall 没有收录 `profiles/test_project_profile.py`（里面有守卫的 G01–G05），我单独跑了：33/33。

**真实环境冒烟**：用新守卫读本会话的真实 transcript，state 指到隔离目录，跑完已移到 `_to_delete/`。
- 结果：window=1000000（来源 statusline），model=claude-opus-5-5，ctx 125k，usage_unreadable=false，阈值和改前一样。
- 也就是说，现在 Max + 100 万窗口的日常会话行为不变。

**上线方式**：两个文件都先在副本里改，副本测过 34/34 和 21/21 之后才替换。替换前用 `cmp` 确认线上文件和备份一致，没有被别人改过。

## 3. 撤回命令
```
cd ~/Documents/Projects/claude-usage-saver; cp -p backups/context_guard.py.20261106-2335.pre-r6M hooks/context_guard.py; cp -p backups/model-router.SKILL.md.20261106-2335.pre-r6M ~/.claude/skills/model-router/SKILL.md; mkdir -p _to_delete/r6M; mv tools/tests_r6M.py _to_delete/r6M/
```
撤回后 mode.json 里多出来的字段会在下一条消息时被旧版覆盖掉，不需要另外清理。

## 4. 已知限制
- **"无模型名就不做 20 万假设"这条门槛，一半是为了和旧语义兼容。**
  - scorecard CG28 要求状态栏和守卫在 payload 不带窗口时判定一致；test_project_profile 的 G01 / G04 要求"不知道窗口时不改阈值"。如果连没有模型名的情况也按 20 万假设，这几条会失败。
  - 真实的 Claude Code transcript 总带 model 字段，所以真实环境里假设照常生效；只有合成数据和别的工具不受影响。
- **假设窗口下的软线只收到 75%**：理由见 1.1。这样设也正好让 G01（130k 不提醒）不受影响。代价是：一个真实 20 万的未知会话，软提醒要到 150k 才出现，而知道窗口时是 120k。
- **无头 `claude -p`**（没有状态栏、拿不到窗口）跑的 100 万模型，上下文在 160k–170k 之间会收到一次"偏早"的强提醒，超过 170k 后恢复正常。这是按保守假设付的代价，和"宁多勿漏"一致。
- **自动压缩点 83% 是推算值**，没有在本机测过；Claude Code 换算法、或者设了 `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` 时会不准。比例都可以在 config 里改。
- **mode.json 是全局的**（最后一次提交消息的会话），没有记 session_id。P 如果要在状态栏显示"ctx ?"，要么只在本会话也读不到时显示，要么请守卫在 mode.json 里加 session_id（一行改动，本轮没做）。
- **`tools/project_profile.py` 的 `clamp_lines()` 还是 0.85 / 0.95**。目前没有任何调用方，不影响行为，但和守卫不一致。它属于 I 的文件，我没改。
- **`~/.agents/skills/model-router/SKILL.md`**（Codex 读的副本）还是旧内容，没有同步。是否用 adapters/skills_sync.py 重新同步，由所有者或 H 决定。
- **SKILL 里的价格来自 claude-api skill 的缓存价目（2026-10-06）**，不是本机实测；订阅额度的折算未知。

## 5. 需要所有者决定的事
1. **已知 20 万窗口时，阈值从 170k / 190k 收紧到 120k / 160k。** 11-21 换回 Pro 后，如果交互会话的窗口是 20 万，就会这样生效：强提醒能赶在自动压缩之前出现，但软提醒会早一些。觉得太早可以在 config.json 里设 `"window_soft_ratio": 0.7`（软线变 140k）。
2. **fork 门槛改为窗口的 8%。** 20 万窗口下，fork 门槛从 8 万降到 1.6 万，基本等于"小窗口不 fork、写 brief 开新 agent"。
3. **fable（顶档）的使用条件**：我写的是"所有者点名，或 opus 明显做不好时才用；同时最多 1 个"。需要确认。
4. **没有模型名的 transcript 是否也按 20 万假设**（见第 4 节第一条）。如果要，需要同时修改 scorecard CG28 和 test_project_profile G01 / G04 的预期。
5. **Codex 那份 model-router 副本是否重新同步。**

## 额外发现
- **K_testall 漏了 `profiles/test_project_profile.py`（33 个用例）**：它的发现规则不包含 `profiles/`，守卫的 G01–G05 不在"一条命令全测"里。在 K_testall 的 PATTERNS 里加 `profiles/test_*.py` 就能补上。收益：回归覆盖 +33 条，防止下次改守卫时漏测（我这次差点只跑 K_testall）。
- **mode.json 可以加 `session_id`**。加上以后状态栏和 doctor 能判断这份 mode.json 是不是本会话写的，"ctx ?"之类的显示才不会串会话。改动 1 行。
- **假设窗口只影响拿不到窗口的会话。** 本机 ctx_window.json 里 12 个会话都记的是 100 万【实测】，所以对当前用量的影响约为 0。它的价值在 11-21 之后的 Pro 和无头运行。
- **model-router 的分派表里原来缺"小型编码题 → 中档"这一行**：那条结论只写在实测段里，分派时容易漏看。已补进表里。如果 Exercism 的 50.2% 对订阅额度也成立，这类任务改用中档，大约能省一半成本【估算，订阅折算未证实】。

## 总结
- **做了什么**：
  - context_guard 改为按窗口比例收紧（知道窗口 0.6 / 0.8，拿不到窗口按 20 万保守假设，有证据时放开）；
  - usage 读不到时在 mode.json 写 `usage_unreadable` 和原因；
  - Pro 封顶原意不变；
  - model-router 改为按能力档写，别名单独一节，写了未知新模型的处理办法，实测数字标上版本和日期，绝对值改成比例，并入 K3 的续用 / 新开公式。
- **数字**：新测试 34/34（旧版 3/34）；K_testall 10 个套件 388/388；I_adapt_matrix 21/21（子检查 329/329）；test_project_profile 33/33。
- **要所有者决定**：见第 5 节，共 5 条。
- **撤回**：见第 3 节，一条命令。

交付完成
