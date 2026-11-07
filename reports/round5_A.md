# 第 5 轮 A：压缩固定开销（工具定义 / skill 列表 / MCP / 系统提示）

代号 A，2026-11-06。**没有改真实 `~/.claude/settings.json`**：所有实验都在 scratchpad 临时目录里跑，设置通过 `--settings <临时文件>` 或环境变量传入。`A_apply.py --apply` 只在临时副本上跑过；对真实路径执行时，脚本会要求加 `--yes`，否则拒绝（已验证，返回码 3）。

标注约定：**【实测】** 指首请求的 usage（input + cache_creation + cache_read），取自 transcript；**【估算】** 会写出计算过程。

---

## 0. 方法（以及两个会影响别人结论的发现）

- **测量工具**：
  - `tools/round5/A_probe.py`：在 pty 里启动一个**真实的交互式** `claude`，自动点过信任对话框，等 15 秒再输入 prompt（让 claude.ai 连接器先连上）。会话结束后读 transcript，取 `api-request-shape`（发给模型的工具列表和字符数）和首条 assistant 的 usage。加 `--sub` 时，会让主会话开一个 general-purpose 子 agent，同时量子 agent 的首请求。
  - `A_shape.py` 负责解析 transcript。`A_batch*.py` 是三批实验的定义。原始结果在 `tools/round5/A_results.jsonl`。
- **为什么不用 `claude -p`【实测】**：
  - `-p` 模式的首请求只有 21.9k token。工具集和交互式不一样：没有 Artifact、AskUserQuestion、WebFetch，也不连 claude.ai 的 MCP。
  - 在 `-p` 下测"去掉 Artifact"，结果会是 0，这个结论是错的。**F2 如果用 `claude -p` 做基准，固定开销那部分会系统性偏小。**
- **复现性【实测】**：
  - 同一配置跑两次，相差 1 token（42,266 / 42,265）。
  - 交互式探针的基线和真实会话对得上：主会话 42.3k，真实中位数 42.1k；子 agent 28.7k，真实中位数 30.8k。
- **模型**：
  - 前两批用 Opus 5.5。中途撞到 5 小时上限，剩下的按主会话的要求改用 Sonnet。
  - Sonnet 基线是主 42,158 / 子 28,837，Opus 是 42,266 / 28,708。
  - 同一开关（enableArtifact:false）的 Δ，Opus 是 −13,421，Sonnet 是 −13,486。**Δ 和模型基本无关**，两批数字可以合用。
- **版本**：实验期间 Claude Code 自动从 2.1.318 升到了 2.1.319，交互式实验都在 2.1.319 上跑。
- **全程没有用 `/context`**（2.1.318 有一个 bug：用过之后每轮都会带上网格）。
- **一个影响"每周"换算的发现【实测】**：
  - round5_where_tokens_go 里"启用后（≈11-06 一天）28.3M、1,351 次请求、44 个子 agent"，实际只覆盖 **11-06 07:58Z–11:00Z 这约 3 小时**。我用同样口径回放，在 11:00Z 截止时正好得到 28.33M、1,352 次请求、44 个子 agent。
  - 那份报告把这 3 小时当成一天，再 ×7 得出 198M/周。
  - 下面的每周换算会同时给出三种口径。

---

## 1. 哪些开关真的把东西从发给模型的内容里拿掉了（全部【实测】首请求 token）

基线：主会话 42,266，子 agent 28,708（Opus）。负数表示比基线少。

| 开关（经 `--settings` 临时文件或环境变量传入） | 主会话 Δ | 子 agent Δ | 实际拿掉了什么 |
|---|---|---|---|
| `enableArtifact: false` | **−13,421** | **−13,140** | Artifact 工具定义 38.4k 字符，外加 3 个 artifact-* skill 的列表项（1.27k 字符）、ArtifactComments/ArtifactData 两个 deferred 名、环境说明里的 artifact 段 |
| `CLAUDE_CODE_DISABLE_ARTIFACT=1`（环境变量） | −13,423 | −13,141 | 和上一行完全一样 |
| `permissions.deny: ["Artifact"]` | −12,761 | −12,477 | **会移除工具定义**（不只是拦截调用），但 artifact-* skill 还留在列表里 |
| `--disallowedTools Artifact`（命令行） | −12,745 | −12,466 | 和 deny 一样，适合无头运行或 henchmen |
| `skillOverrides`：13 个 `anthropic-skills:*` 设为 `off` | −3,130 | −3,139 | skill 列表从 19.9k 字符降到 9.7k。键名带不带 `anthropic-skills:` 前缀都行（−3,135） |
| 同上，设为 `user-invocable-only`（Sonnet） | −3,205 | —（同机制） | 模型看不到，所有者仍可手动 `/anthropic-skills:pdf` |
| 同上，设为 `name-only` | −2,957 | −2,964 | 只留名字，省得少一点 |
| `syncClaudeAiSkills: false` | −3,127 | −3,135 | 和 `off` 效果一样。**但写进 user 设置会把 synced 目录移到 .trash**；只放在 `--settings` 里就不会移动文件（官方说明） |
| `disableBundledSkills: true` | **+3,580** | −2,687 | **主会话反而变贵**：Workflow 工具定义从 5.4k 字符涨到 22.4k。还会让 `/doctor prompt-audit` 失效。**不要用** |
| `permissions.deny` SendFeedback、ReportFindings、ScheduleWakeup（Sonnet） | −4,523 | 0（子 agent 没有这些工具） | 工具定义 12.6k 字符。按字符比例拆：SendFeedback ≈2.0k、ScheduleWakeup ≈1.7k、ReportFindings ≈0.8k【估算】 |
| `disableClaudeAiConnectors: true`（Sonnet） | −2,711 | −1,969 | 55 个 Gmail/Calendar/Docs/Drive/Sheets/Slides/Claude Docs 的 deferred 名，外加 Claude Docs 的 MCP 说明（2.1k 字符） |
| `includeGitInstructions: false`（git 仓库内，Sonnet） | −551 | 未测 | commit/PR 流程说明和 git status 快照 |
| 组合 core+tools（Artifact + 13 skill + 3 个工具，Sonnet） | **−21,059** | **−16,301** | 各项**可以直接相加**（−13.5k − 3.2k − 4.5k ≈ −21.2k） |

**没测到的**（第一次撞 5 小时上限时停掉了，之后为了省额度没有补）：
- `skillListingMaxDescChars`
- `ENABLE_CLAUDEAI_MCP_SERVERS=false` 这个环境变量写法（官方说和 `disableClaudeAiConnectors` 等价，我没有实测）
- `/doctor prompt-audit`：它是审计工具，不是开关，本身就要花额度；而且依赖 bundled 的 claude-api skill。所以只做了记录，没有运行。

**结论**：
- 能真正"从发给模型的内容里拿掉"的方法有：`enableArtifact`（或等价的环境变量）、`permissions.deny`、`--disallowedTools`、`skillOverrides`、`syncClaudeAiSkills`、`disableClaudeAiConnectors`、`includeGitInstructions`。
- `deny` 和 `--disallowedTools` 确实会移除工具定义，比 `enableArtifact` 少省约 660 token。

---

## 2. 其它固定开销的大小和来源

### MCP 连接器（claude.ai）【实测】
- deferred 列表共 7.6k 字符（73 个名字，其中 55 个是 MCP 的），另有 Claude Docs 的 MCP 说明 2.1k 字符。
- 全关掉省 2.7k（主）/ 2.0k（子）token。工具 schema 本身是 deferred 的，不占工具定义。
- 实际调用次数（全部历史的 tool_use 计数）：Gmail 33 次（最近一次 11-03）、Calendar 1 次、Claude Docs 2 次。所以**不建议全局关掉**，只建议在 henchmen/无头运行里关。

### 系统提示和工具定义【实测】
- 主会话：系统提示 9.1k 字符，工具定义 78.5k 字符（16 个工具；Artifact 38.4k，占 49%）。
- 子 agent：系统提示 2.7k 字符，工具定义 54.8k 字符（Artifact 占 70%）。
- agent 列表 6.3k 字符（只有主会话有）。

### CLAUDE.md 和记忆
- 用户级 `~/.claude/CLAUDE.md` 不存在。
- `MEMORY.md` 索引 1,050 字符，约 0.46k token【估算】，每个主会话都带（instructions 附件 1.2k 字符）。
- 各项目的 CLAUDE.md 只在碰到该项目文件时才加载（quant-backtest 约 2.0k，regime-statarb 约 3.8k token【估算】）。

### AGENTS.md 注入的来源：已查清【实测】
- **来源**：Claude Code 内置插件 **`cc-plugin-agents-md@builtin`**。它就是 2.1.277 起"原生读取 AGENTS.md"的实现方式。
  - 会话 init 的 plugins 列表里能看到它。
  - transcript 里的记录是 `hook_additional_context`，hookName `tool.call`，PostToolUse。
  - 它**不是**套件的 hook，也**不在** settings.json 里。
- **触发逻辑**：默认模式是 `claude-md-or-agents-md`。`~/Documents/Projects/` 没有 CLAUDE.md，所以任何工具第一次碰到 Projects/ 下的路径时，就把 `Projects/AGENTS.md` 当作项目说明注入。本次会话里我第一次读项目文件时也触发了。
- **有没有重复注入**：近 3 天 29 个会话里，28 个只注入 1 次，1 个注入 2 次（两次之间没有压缩，原因不明）。
  - 没有发现内置机制和套件 hook 重复注入。
  - regime-statarb 的 CLAUDE.md 和 AGENTS.md 大小几乎一样（4,391 / 4,389 字节），但它有 CLAUDE.md，默认模式下不会再读它的 AGENTS.md，所以也不重复。
- **开关【实测】**：`pluginConfigs: {"cc-plugin-agents-md@builtin": {"options": {"instructionFiles": "claude-md"}}}`。
  - 在临时目录里放 `sub/AGENTS.md`（不放 CLAUDE.md）再 Read `sub/x.txt`：基线有注入，设了这个开关就没有注入。
  - 测试文件让第二次请求多了 384 token。真实的 `Projects/AGENTS.md` 有 2,163 字符（其中 1,080 个中文），约 2.0k token/会话【估算】，约占成本 0.7%（沿用 round5_where_tokens_go 的数）。
- **取舍**：这个文件里有红线和交流方式，是 11-06 专门为 Codex 和其它工具写的，和记忆有重复但不完全一样。**建议保留**（每会话 2k token 换红线提醒）。`A_apply.py` 里把它做成可选组 `agentsmd`，默认不启用。

---

## 3. 方案：`tools/round5/A_apply.py`（可应用、可撤回）

```
python3 tools/round5/A_apply.py --dry-run [--profile core]          # 默认只打印改动
python3 tools/round5/A_apply.py --apply --yes [--profile core]      # 真实 settings 必须加 --yes（本轮未执行）
python3 tools/round5/A_apply.py --undo --yes                        # 逐键还原到 apply 前的原值
python3 tools/round5/A_apply.py --restore artifact --yes            # 只临时恢复某一组：artifact|skills|tools|git|connectors|agentsmd
python3 tools/round5/A_apply.py --status
python3 tools/round5/A_apply.py --print-lean-settings --profile core+tools   # "别名模式"用的 --settings 文件内容
```

- **分组**：
  - `artifact` = `enableArtifact:false`
  - `skills` = 13 个 `anthropic-skills:*` 设为 `user-invocable-only`
  - `tools` = deny `SendFeedback`、`ReportFindings`。这两个历史上 0 次调用；ScheduleWakeup 被 /loop 用过 3 次，所以保留
  - `git` = `includeGitInstructions:false`
  - `connectors` = `disableClaudeAiConnectors:true`
  - `agentsmd` = 上面那个插件选项
- **profile**：`core` = artifact + skills（**推荐**）；`core+tools`；`core+tools+git`；`all`。
- **安全性**：
  - apply 前把目标文件 `cp` 到 `backups/settings.json.pre-r5A-<时间>`，原值记录在 `backups/A_apply_state.<哈希>.json`。
  - undo 只还原本脚本动过的键：skillOverrides 逐个 skill 还原，deny 只删掉自己加的那几项。之后别人改的其它键不受影响。
  - 写入是原子操作，写完会再读一遍，确认是合法 JSON。
- **在临时副本上验证过【实测】**：
  - core、core+tools、all 三个 profile 依次 apply，每次都是合法 JSON。
  - `--restore agentsmd,artifact` 只撤这两组。
  - `--undo` 之后，文件和原件**逐字节相同**（cmp 通过）。
  - 真实 `~/.claude/settings.json` 前后 cmp 一致，没有被碰过。

### 能省多少

**每次请求【实测】**：

| profile | 主会话首请求 | 子 agent 首请求 |
|---|---|---|
| core | 42.3k → 25.6k（**−16.7k，−39%**） | 28.7k → 12.4k（**−16.3k，−57%**） |
| core+tools | 再少约 2.8k（不含 ScheduleWakeup；按字符比例拆分【估算】），合计约 −19.5k | 同 core |

这些 token 会出现在会话的每一次请求里：读缓存时按 0.1× 计，写缓存（首请求、缓存过期后重写）时按 2× 计。

**换算成成本**（`A_weekly.py` 按真实 transcript 逐次请求回放【估算】）：
- **每个主会话约省 17–21 万单位**。这些会话平均约 90 次请求。
- **每个子 agent 约省 4.7–8.5 万单位**。
- 范围的两端来自两种判定"这段前缀是读缓存还是写缓存"的规则。

| 口径 | 窗口成本 | core 省 | 占比 | 每周 |
|---|---|---|---|---|
| round5_where_tokens_go 的口径（7 个主会话 + 44 个子 agent 当作一天，×7） | 28.3M | 3.25–3.40M | **11.5–12.0%** | **约 23–24M/周** |
| 11-06 全天（0.98 天，7 个主会话 + 57 个子 agent，含第 5 轮自己） | 60.8M | 5.7–6.3M | 9.3–10.4% | 约 41–45M/周 |
| 11-03 至 11-06 三天（以通宵长会话为主） | 293M | 14.2M | 4.8% | 约 33M/周 |

- core+tools 在三种口径下分别是 12.2–12.7% / 9.7–10.8% / 5.6%，**比 core 只多 0.3–0.8 个百分点**。
- 不确定性主要来自"一周开几个会话、几个子 agent"，这个没有真实的一周样本。缓存读写判定带来的误差约 ±10%。
- 和 round5_where_tokens_go 的估计（第 3 项 6.7–8.5%）比，我这里更高。原因是我把每个子 agent 开局按 2× 写缓存算了进去（每个子 agent 都要重写一次前缀）。

---

## 4. 风险和"按需临时恢复"

| 去掉的东西 | 会失效的功能 | 临时恢复 |
|---|---|---|
| Artifact（core） | 不能发布 claude.ai 页面。ArtifactComments/ArtifactData、artifact-design/-capabilities/-diagramming 三个 skill 一起消失。**Claude Docs 连接器不受影响**（实测 deferred 列表里还在）。历史上 Artifact 只用过 5 次，最近一次是 11-01 | 官方说明是"user 设置里 off 优先"，所以 `--settings '{"enableArtifact":true}'` **开不回来**（这是官方文档的说法，我没法在不动真实 settings 的情况下实测）。办法：`A_apply.py --restore artifact --yes`，新开会话发页面，用完再 `--apply --yes`。期间新开的其它会话也会带上 Artifact。也可以用下面的"别名模式" |
| 13 个 anthropic-skills（core） | Claude 不会再自动去用 docx/pptx/xlsx/pdf/google-workspace/deep-research/浏览器类 skill，做 Office 文件或浏览器任务时质量可能下降 | `user-invocable-only` 的设计是：所有者输入 `/anthropic-skills:pdf` 这类命令仍能调用（官方说明；**手动调用我没实测**）。或者 `--restore skills --yes` |
| SendFeedback / ReportFindings（tools） | 不能发反馈、不能用 ReportFindings（历史 0 次） | `--restore tools --yes` |
| git 说明（不推荐，只省 0.55k） | 失去内置的 commit/PR 流程说明 | — |
| 连接器（不推荐全局关） | Gmail（用过 33 次）、Calendar 等全部不可用 | henchmen/无头运行可以单独 `--settings '{"disableClaudeAiConnectors":true}'` |

**别名模式**（不动 settings.json，恢复最方便）：
1. 把 `A_apply.py --print-lean-settings --profile core+tools` 的输出存成 `~/.claude/lean-settings.json`。
2. 在 zshrc 里加 `alias claude='claude --settings ~/.claude/lean-settings.json'`。
3. 要完整功能时用 `command claude` 启动。

lean 文件里的 skill 部分用 `syncClaudeAiSkills:false`。官方说明它在 `--settings` 里只对本次启动生效、不移动文件；实测效果和 `off` 相同，−3.13k。

缺点：桌面端、henchmen 这类不经过 shell 别名启动的会话吃不到这个设置。可以给 henchmen 的启动命令单独加 `--settings`。

---

## 5. Codex CLI 的固定开销（跨工具）

- **大小【实测】**：一次只回答"OK"的 `codex exec`，首请求输入 **20,254 token**。
  - base_instructions 21.8k 字符。
  - developer 消息 23.5k 字符，其中 skills_instructions 占 18.5k，共 34 个 skill。
- **来源【实测】**：skill 列表里约 10.7k 字符来自 `~/.agents/skills/synced/`。这是 10-12 生成的一份 **Claude 同步 skill 的副本**（docx/pdf/morning 等 13 个），Codex 会读 `~/.agents/skills`。其余来自 Codex 自带的插件（documents、spreadsheets、sites、work-pets 等）和 `.system`。
- **能裁剪【实测】**：
  - 方法：`codex exec -c 'skills.config=[{name="docx",enabled=false},…13 个]'`。
  - 结果：首请求 20,254 → **16,853（−3.4k，−17%）**，skill 列表从 18.5k 字符降到 5.4k。
  - 局限：插件自带的 skill（documents、spreadsheets 等）按名字关不掉。试过同时关插件，结果没测完：Codex 额度用完了，23:29 才重置。
- **建议**：
  - 在 `tools/codex_review.sh` 里加上这串 `-c`。这个文件归 C 管，我没有改。
  - `A_codex_probe.sh` 可以复现上面的测量。
  - 另外，research 第 2.1 节已经指出 `service_tier="priority"` 按 2.5 倍计费。
- **其它工具**：Gemini CLI、Cursor 这类没装，没测。

---

## 总结

**做了什么**：
- 用交互式 pty 探针实测了 20 多种开关的首请求 token：Opus 17 次、Sonnet 12 次。
- 查清了 AGENTS.md 的注入来源。
- 量了 Codex 的固定开销和一个裁剪办法。
- 写了 `A_apply.py`（dry-run/apply/undo/restore/status/别名模式），只在临时副本上验证。

**关键数字【实测】**：
- `enableArtifact:false`：主会话 −13.4k token/请求，子 agent −13.1k。
- 13 个 anthropic-skills 设为 user-invocable-only 或 off：−3.1~3.2k。
- core 合计：主 −16.7k（−39%），子 −16.3k（−57%）。
- `disableBundledSkills` 反而 +3.6k，不要用。
- Codex 关掉同步 skill：−3.4k/次（−17%）。

**每周节省【估算】**：
- 按 round5_where_tokens_go 的会话数和子 agent 数换算，约 **23–24M/周（11.5–12%）**。
- 按 11-06 全天外推约 41–45M/周（约 10%）；按以通宵为主的三天约 33M/周（4.8%）。
- 另外，"启用后一天"那个窗口其实只有 3 小时，建议 F1 复核 198M/周 这个基数。

**要所有者决定的事**：
1. 是否执行 `python3 tools/round5/A_apply.py --apply --yes --profile core`（或 core+tools）。还是改用别名模式？
2. 要不要关掉 AGENTS.md 对 Claude 的注入（`agentsmd` 组，约 2k token/会话；建议保留）。
3. 是否让 C 在 `codex_review.sh` 里加 `skills.config` 的 `-c` 参数。

**撤回**：
- `python3 tools/round5/A_apply.py --undo --yes`（逐键还原）。
- 或者 `cp -p backups/settings.json.pre-r5A-<时间> ~/.claude/settings.json`。
- 本轮没有改任何已有文件，只新建了 `tools/round5/A_*` 和本报告，所以没有其它要撤回的东西。

**其它**：
- 一次误打的 `rm -rf /dev/null`（混在一条测试命令里），没有删除任何东西。
- 实验目录都在 scratchpad 下（属于 `-private-tmp-`，不进统计）。
