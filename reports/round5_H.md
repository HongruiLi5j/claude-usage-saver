# 第 5 轮 H：跨工具适配（Codex CLI / Gemini CLI / Cursor / Aider）

2026-11-06。标注：**[实测]** = 本机跑过；**[文档]** = 官方文档（URL 见第 1 节）；**[未证实]** = 没查到或没法跑；**[估算]** = 带计算过程。
本机只装了 Codex CLI（codex-cli 0.163.0；Desktop 0.164.0-alpha 也往同一个 `~/.codex` 写会话）。gemini、aider、cursor-agent、tmux、ccusage 都**没有安装**，所以这几个工具的结论都只到「文档」这一级。
**没有改** `~/.codex/` 和 `~/.claude/` 下的任何配置。`~/.codex/config.toml` 只看了键名，认证相关内容没有写进本报告。

---

## 0. 结论先说

1. **能共用的部件**：①交接文件 HANDOFF.md 加上 AGENTS.md 规则，所有工具都能直接用（Codex、Cursor 读 AGENTS.md；Gemini 读 GEMINI.md，也能配置成读 AGENTS.md；Aider 用 `--read` 加载）；②`usaver-run` 测试瘦身命令，任何能跑 shell 的工具都能调；③用量统计和状态显示：Codex 的 rollout 日志里有每次请求的 token、上下文窗口和 5 小时 / 7 天额度百分比，**[实测]** 可以直接读。
2. **按数字算，在 Codex 上省得很少**。在这位所有者的 Codex 使用方式下，测试瘦身**估计只省约 0.4%** 的 Codex 成本。原因：Codex 自己已经给每次工具输出设了 `max_output_tokens` 上限（中位数 5000），而且历史 16 天里只有 60 次测试调用。完整版 AGENTS.md 片段每次请求多带 **677 token [实测]**，约占 Codex 每周成本的 0.8%，**比它省下的还多**。所以**推荐只装精简版（lite，约 230 token，约 0.3%）**；完整版只建议放进测试很多的项目。跨工具这部分真正的价值在**测量**（codex_usage、usaver-status），不在省 token。
3. **三个以前不知道的情况 [实测]**：
   - `~/.codex/hooks.json` 里已经注册了 `context_guard.py`（Codex 的 UserPromptSubmit 事件，11-06 00:42 写入）。但 `config.toml` 里没有 `hooks.state.*.trusted_hash`，所以它**很可能从来没有运行过** [未证实]。就算运行了，也只是**静默空转**，因为它只会解析 Claude 的 transcript，而且还会顺手改写套件的 `state/mode.json`。
   - `~/.codex/sessions` 里的 607 个会话文件中，有 **409 个是 Codex Desktop 从 Claude Code 导入的会话**（`external_agent_session_imports.json`）。它们的 token 都是 0，codex_usage 默认把它们排除。
   - 跑实测的时候，Codex 的 **5 小时额度被打满了**（这一轮别的 agent 也在用 Codex 复核）。live2 的后 4 组因此失败，要到 23:29 才能恢复。
4. 交付物都在 `adapters/` 下：`usaver-run`、`usaver-status`（新增，按所有者的补充要求做）、`codex/codex_usage.py`、`agents/AGENTS.usage-saver{,.lite}.md`、`apply.py`（应用 / 撤回）、`skills_sync.py`（第 7 节）、`matrix.json`（给 I 的验收矩阵对接用）。离线测试 **36/36 通过**；真机 Codex 跑了 6 组（4 + 2）。

---

## 1. 各工具的等价机制

| 机制 | Codex CLI | Gemini CLI | Cursor | Aider |
|---|---|---|---|---|
| **命令执行前后的钩子** | 事件：PreToolUse / PostToolUse / SessionStart / SessionEnd / UserPromptSubmit / Stop / SubagentStart / SubagentStop / PreCompact / PostCompact / PermissionRequest / Interrupt。本机二进制 `strings` 里就有这些名字 **[实测]**；文档还多列了 PreModelRequest 和 PostModelRequest **[文档]**。配置写在 `~/.codex/hooks.json`、`config.toml [hooks]` 或项目里的 `.codex/hooks.json`。**改写命令**：文档说 `permissionDecision:"allow"` 加上 `updatedInput.command` 就行 **[文档]**，但本机 0.163.0 的二进制里**找不到 `updatedInput` 这个字符串**，只有 `updatedMCPToolOutput` **[实测]**，所以本机版本到底支不支持 **[未证实]**。`additionalContext` 支持 **[实测：字符串；文档]**。**钩子要先信任**：用 `/hooks` 审核后，按哈希值存进 `config.toml`。项目里的钩子不信任就不运行 **[实测：临时项目里的 .codex/hooks.json 没有触发]** | BeforeTool / AfterTool / BeforeAgent / SessionStart / PreCompress 等事件，配置在 settings.json 的 `hooks` 里。BeforeTool 用 `hookSpecificOutput.tool_input` 改写参数，shell 工具叫 `run_shell_command`；AfterTool 可以替换工具结果 **[文档]** | `~/.cursor/hooks.json` 和 `.cursor/hooks.json`。preToolUse 用 `updated_input` 改写；beforeShellExecution 只能 allow / deny **[文档]**。CLI 里能不能用 **[未证实]** | 没有钩子 **[文档]**。最接近的是 `--test-cmd`、`--lint-cmd`、`--auto-test` |
| **项目说明文件** | 读 `~/.codex/AGENTS.md`，再从 git 根目录一路往下读 `AGENTS.override.md` 或 `AGENTS.md`；总长受 `project_doc_max_bytes` 限制（默认 32KiB）；还有 `project_doc_fallback_filenames` **[文档；二进制里有这些键名，实测]**。本机实测 Codex 读到了临时项目里的 AGENTS.md：两组都先跑了 `command -v usaver-run` **[实测]** | 读 `~/.gemini/GEMINI.md` 和各级目录的 GEMINI.md；设置 `context.fileName` 可以改成读 `AGENTS.md` **[文档]** | 读 `AGENTS.md` 和 `.cursor/rules/*.mdc`；CLI 还读 CLAUDE.md **[文档]** | 不会自动读；要用 `--read CONVENTIONS.md`，或者写进 `.aider.conf.yml` 的 `read:` **[文档]** |
| **上下文压缩** | `/compact`；`model_auto_compact_token_limit`、`model_post_turn_compact_threshold_percent`、`compact_prompt` **[文档；二进制里有键名，实测]**。rollout 里能看到 `compacted` 记录（7 次）**[实测]** | `/compress`，`model.compressionThreshold` 默认 0.5 **[文档]** | 自动摘要；CLI 里用 `/summarize` **[文档]** | `--max-chat-history-tokens`，用 weak model 做摘要 **[文档]** |
| **会话恢复** | `codex resume [--last]`、`codex exec resume`、`fork` **[实测：--help]** | `gemini --resume`，`/resume`、`/chat` **[文档]** | `agent resume`、`--continue` **[文档]** | `--restore-chat-history` **[文档]** |
| **用量记录** | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`，旧文件压成 `.jsonl.zst`。每次请求一条 `token_usage_record`，较早的 Desktop 格式用 `event_msg/token_count` 代替。其中还有 `model_context_window`（本机 475000）和 `rate_limits`：primary 是 300 分钟窗口，secondary 是 10080 分钟窗口，带 `used_percent`、`resets_at`、`plan_type` **[实测]**。这个路径官方文档没写，只在 issue 里出现过 **[文档：未写]**。另外 `codex exec --json` 有 `turn.completed.usage`，也有 OTel **[文档]** | 本地 telemetry 文件（`telemetry.target=local`），事件 `gemini_cli.api_response` 带 token 计数；文件格式 **[未证实]**。E 的调研说 OAuth 登录时没有 token 缓存 | 本地没有逐请求记录；只能在网页仪表盘导出 CSV **[未证实]** | `--analytics-log` 写 jsonl，具体结构 **[未证实]** |
| **状态栏** | `tui.status_line` 只能从内置条目里挑，**不能挂自定义命令**（已有 feature request）**[文档；二进制里有 status_line 键，实测]**。`/status` 显示 token | 只有 `ui.footer.items` 内置条目 **[文档]** | 没查到 | 没查到 |
| **notify** | `notify=[程序]`，每轮结束时把 JSON 传给这个程序（type、thread-id、cwd、last-assistant-message）**[文档]** | Notification 钩子 | — | `--notifications-command` |

来源：learn.chatgpt.com/docs/hooks、/docs/agent-configuration/agents-md、/docs/config-file/config-reference、/docs/config-file/config-advanced、/docs/cli/slash-commands、/docs/non-interactive-mode；geminicli.com/docs/hooks/、/docs/hooks/reference/、/docs/cli/gemini-md/、/docs/cli/telemetry/、/docs/cli/session-management/；cursor.com/docs/agent/hooks、/docs/context/rules、/docs/cli/using；aider.chat/docs/config/options.html、/docs/usage/conventions.html、/docs/more/analytics.html。另外 developers.openai.com/codex 现在会 308 跳转到 learn.chatgpt.com。Gemini 文档顶部写着：2026-06-18 起，免费层和 Google One 用户的 Gemini CLI 被 Antigravity CLI 取代。这是平台风险。

### 本机实测细节（Codex）
- **钩子测试受阻**：用 `--dangerously-bypass-hook-trust` 跑的那次被 Claude Code 的自动安全分类器拒绝了。我没有绕过，也没有往 `~/.codex/config.toml` 写信任记录。不加这个参数时，临时项目里的 `.codex/hooks.json` 一个事件都没触发 **[实测]**。所以 Codex 钩子的 payload，以及能不能用 `updatedInput` 改写命令，**都没有实跑**。要验证，需要所有者二选一：在 Codex TUI 里对临时项目用 `/hooks` 手动信任；或者允许 bypass 参数只用在 scratchpad 临时项目里。
- **`codex exec` 打印的 "tokens used" 等于 输入 − 缓存命中 + 输出**。codex_usage 按 cwd 算出来的数，和 6 次运行打印的数**逐个完全相等**（13,820 那次差 1，是四舍五入）**[实测]**。
- **Codex 自己会截断工具输出**：模型每次调用 `exec_command` 时会带一个 `max_output_tokens`。在 1729 次调用里，这个值的中位数是 5000，最小 100，最大 37000；输出里还会带上 `original_token_count` **[实测]**。这是 Codex 自带的输出瘦身，会把 usaver-run 能省的空间压小。

---

## 2. 适配矩阵

三档：**直接用** / **适配层**（要写一层适配才能用）/ **不适用**。机器可读版本在 `adapters/matrix.json`，每一行都带 evidence 字段（measured / doc / unverified），I 的 `tools/round5/I_adapt_matrix.py` 可以按 (component, tool) 直接对接。

| 部件 | Codex CLI | Gemini CLI | Cursor | Aider |
|---|---|---|---|---|
| test_filter（测试输出瘦身） | **直接用**：走 AGENTS.md 规则加 `usaver-run` **[实测]**。钩子路线算适配层：要加 `permissionDecision:"allow"`，还要先信任钩子，本机版本支不支持也没确认 | 适配层：写进 GEMINI.md 让它用 usaver-run，或者用 BeforeTool 改写 `tool_input` | 适配层：写进 AGENTS.md，或者用 preToolUse 的 `updated_input` | **直接用**：`--test-cmd "usaver-run pytest -q"` **[未实测]**。Aider 会把测试失败的输出塞进对话，正好需要瘦身 |
| context_guard（上下文提醒） | 适配层：UserPromptSubmit 能注入 `additionalContext`，但守卫得能解析 rollout（`last_token_usage.input_tokens` 和 `model_context_window`）。现在挂着的守卫在 Codex 上是空转。也可以改用 Codex 自带的 `model_auto_compact_token_limit` | 适配层：BeforeAgent 事件；transcript 格式未证实；也可以用自带的 `compressionThreshold` | 不适用：beforeSubmitPrompt 不能注入上下文 | 不适用：没有钩子；可以用自带的 `--max-chat-history-tokens` |
| 交接文件 HANDOFF.md | **直接用**：片段里的标题能被 session_resume 的正则解析 **[实测：离线测试]**，所以 Codex 写的交接，换回 Claude Code 也能自动注入 | 直接用 | 直接用 | 直接用（`--read`） |
| session_resume（自动接续） | 适配层：SessionStart 能注入，但旧对话摘要要重写成 rollout 版；「拥有者进程」那套逻辑是 Claude 专用的 | 适配层 | 适配层（sessionStart 的 `additional_context`） | 不适用 |
| limit_logger（额度锚点） | 适配层 **[实测]**：额度百分比就在 rollout 里（还有 `rate_limit_reached_type`）。`codex_usage --emit-rate` 输出和 rate.json 一样的格式，离线测试确认 `context_guard.get_mode()` 能读 | 不适用（没有本地额度数据） | 不适用 | 不适用（按 API 计费） |
| weekly_compare（用量统计） | 适配层，已做完 **[实测]**：`adapters/codex/codex_usage.py` | 适配层：解析 telemetry 本地文件，格式未证实 | 不适用 | 适配层：解析 analytics-log，格式未证实 |
| model-router 规则 | 适配层：把与工具无关的部分提炼进片段的 Delegating 一条；Claude 的 haiku / sonnet / opus 别名不适用 | 同左 | 同左 | 同左，再加 `--weak-model` |
| statusline（状态栏） | 不适用（状态栏不能挂自定义命令）→ 用 **usaver-status** 代替 **[实测]** | 不适用 → usaver-status（Gemini 数据源未实现） | 不适用 | 不适用 |

---

## 3. 实施的适配

### 3.1 `usaver-run`：任何工具都能调用的测试瘦身命令（`adapters/common/usaver_run.py`，入口 `adapters/bin/usaver-run`）
- 用法：`usaver-run pytest -q`，或 `usaver-run --sh 'pytest && npm test'`。规则和 test_filter 完全一样：常量直接从 `hooks/test_filter.py` 导入（GREP_PAT、SHORT=40、交互参数放行、日志 7 天后移到 `_old/`）。另外：保留退出码，被信号杀死时返回 128+N；设置 `USAGE_SAVER_RAW=1` 或加 `--raw` 时原样输出。
- 日志目录按顺序尝试：`$USAVER_LOGDIR` → 套件的 `state/testlogs` → `$TMPDIR/usaver-testlogs`。这是实测后改的：Codex 沙箱写不了套件目录。
- 测试结果：①40 组随机输入下，过滤结果和 `grep -nE -B2 -A12 | head -120` **逐行一致**；②真 pytest（60 个通过 + 1 个失败）的输出比完整日志短；③目录不可写时会退到临时目录。

### 3.2 `codex_usage.py`：Codex 用量统计（`adapters/codex/`）
- 读 `.jsonl` 和 `.jsonl.zst`。有 `token_usage_record` 时只用它，按 response_id 去重；没有就退回 `token_count`，按「会话 + 累计值」去重。按天、模型、主会话 / 子 agent、交互 / exec 分组统计。成本单位默认和 weekly_compare 一样（缓存 ×0.1，输出 ×5）；`--weights openai` 把输出改成 ×8。默认排除 `/tmp` 下的会话和导入的会话。`--cwd` 只看某个项目；`--emit-rate` 输出和 rate.json 一样格式的额度读数。
- **本机真实数字 [实测]**，统计范围 10-02 → 11-06，排除 tmp 和导入，共 15 天有数据：
  - 2931 次请求，49.5M 单位，平均每次 16.9k；
  - 上下文中位数 71k、平均 93k；缓存命中率 94%；
  - 成本构成：缓存命中 52% / 未缓存输入 31% / 输出 17%；
  - 按模型：gpt-6.1-sol 占 83%；
  - exec 和交互各占约一半。
- 这些单位是 API 价格比，**不是订阅额度**；而且跨厂商不能直接比。gpt-6.1-sol 的真实价格比 **[未证实]**。
- 还没有和 ccusage 对照：本机没装，装要联网 npx。替代做法是和 Codex 自己打印的 "tokens used" 对照，6/6 完全一致。

### 3.3 AGENTS.md 片段（`adapters/agents/`）和应用 / 撤回脚本（`adapters/apply.py`）
- 完整版 2.5KB，内容：用 usaver-run、窄读、不回贴大段输出、分派模型、交接格式、接着做的规矩。精简版 0.8KB，只留 usaver-run 和交接两条。交接模板的标题用英文（Red lines / Next steps），session_resume 的正则本来就认英文，**Codex 写的交接 Claude 也能接** **[离线测试]**。
- `apply.py agents apply|revert --target <文件> [--variant lite]`：在目标文件里插入或替换带标记的那一段，重复运行结果不变。改之前先备份到 `backups/adapters/`。撤回会去掉这一段，如果文件因此变空，就 mv 到 `_to_delete/`，不用 rm。`apply.py bin link|unlink` 用符号链接把两个命令放上 PATH。
- 默认目标：Codex 是 `~/.codex/AGENTS.md`（这个文件现在不存在）；Gemini 是 `--target ~/.gemini/GEMINI.md`；Aider 是项目里的 `CONVENTIONS.md`。**都还没有应用到真实配置。**

### 3.4 `usaver-status`：其他工具里的实时状态行（所有者的补充要求）
- (1) Codex 状态栏只能放内置条目，挂不了自定义命令 **[文档]**；`/status` 是交互命令，exec 里看不到。但 rollout 里的数据足够 **[实测]**：上下文 = 最近一次请求的 input_tokens；窗口 = `model_context_window`；额度 = `rate_limits`；模型 = `turn_context.model`。
- (2) 通用命令：`usaver-status [--tool auto|codex|claude] [--cwd DIR|--any] [--watch 秒] [--json]`。自动识别：取 cwd 下最近活动的 Codex 或 Claude 会话，比较文件修改时间。只读文件尾部 512KB，0.2 秒内出结果。出任何异常都输出兜底行，并且 exit 0。
- (3) 格式和 statusline.sh 一样，**本机实测输出**：
  - `claude-opus-5-5 | ctx 236k | 5h 47% | 7d 6% | ⚠ 到线：做完这步就 /clear`（本会话）
  - `gpt-6.1-sol | ctx 22k/475k | 5h 95% | 7d 27%`（live2 的 Codex 会话）
- 阈值直接调 `context_guard.lines(cwd, 倍数)`，和状态栏、守卫同一套：config.json + 项目覆盖 + Pro 封顶。**如果 I 把项目画像加进了 `lines()`，这里会自动跟上。** 档位倍数这样来：Claude 用 `state/mode.json`；Codex 把自己的额度读数写到 `state/codex/rate.json`，再交给 `get_mode()` 算。所以 Codex 的 5 小时用到 95% 时，会按「快用完」收紧提醒线（有离线测试）。
- **已知窗口时另外收紧**：软线和强线分别不超过窗口的 60% 和 80%。比如新模型窗口只有 128k，套件软线 160k 就落在窗口外，永远不会提醒；收紧后 77k 就开始提醒（有离线测试）。到线提醒里的命令按工具换：Claude 是 `/clear`，Codex 是 `/new`。
- **按 E 的建议加了 5 小时窗口用完时间的预测**：取同一重置窗口内最近 90 分钟、跨度 ≥5 分钟的读数，算消耗斜率。只有在重置前就会用完时才显示「(约 HH:MM 用完)」。读数历史存在 `state/status_hist_<tool>.json`，最多保留 6 小时。
- 接入方法：
  - 另开一个终端窗格跑 `usaver-status --watch`；
  - tmux：`set -g status-right '#(usaver-status --cwd "#{pane_current_path}")'` 加 `set -g status-interval 5`（本机没装 tmux，**未实测**）；
  - zsh：`setopt prompt_subst; RPROMPT='$(usaver-status 2>/dev/null)'`（未实测）。
- 给 statusline.sh 的方案（文件归 I，我没改）：在合并写 rate.json 的同时，追加 `(ts, five_hour.used_percentage, resets_at)` 到 `state/status_hist_claude.json`，然后直接调 `usaver_status.eta_5h()`，在 5h 那一项后面加上「(约 HH:MM 用完)」。

### 3.5 测试
- **离线**：`python3 -m pytest -q adapters/tests/test_adapters.py` → **34 passed** **[实测]**。全部在临时目录里跑；会伪造 HOME、CODEX_HOME 和 USAGE_SAVER_STATE，不碰真实配置。覆盖：
  - usaver-run 12 项；
  - codex_usage 6 项：两种日志格式、zst、tmp / 导入 / 子 agent 的分类、坏行、未知模型名、emit-rate 接 context_guard；
  - apply 6 项；
  - usaver-status 8 项：格式、强线、小窗口新模型、Codex 额度带动档位、自动挑工具、找不到会话、watch、ETA；
  - 片段兼容性 1 项。
- **真机 Codex**：`python3 adapters/tests/live_codex.py <目录> --reps N`。每组两个项目：treat 放片段，ctrl 不放；任务一样，都是让 Codex 跑测试并说出哪个测试失败。

| 运行 | 组 | 用 usaver-run 跑测试 | 测试调用的输出 token（original_token_count） | 本次总输入 token | Codex 打印 = 解析 | 答对 |
|---|---|---|---|---|---|---|
| live1（提示语带 "verbose"、"Do not modify"） | treat0 | ✗ 读了片段，但还是直接跑了 pytest | 1449 | 88.0k | 31389 = 31389 | ✓ |
| | ctrl0 | — | 1449 | 106.7k | 23580 = 23580 | ✓ |
| | treat1 | ✓，但**跑完把日志删了**（遵守了 "Do not modify"） | **444** | 106.6k | 16100 = 16100 | ✓ |
| | ctrl1 | — | 1449 | 106.8k | 29877 = 29877 | ✓ |
| live2（中性提示语，项目自带 `-v -rA`） | treat0 | ✓，日志保留 | 41 + **771** | 87.1k | 16032 = 16032 | ✓ |
| | ctrl0 | — | 105 + **7158** | 87.3k | 11418 = 11418 | ✓ |
| | 后 4 组 | 都失败：**Codex 报 "You've hit your usage limit"**（5 小时窗口满了） | | | | |

- 解读（n 很小，只当方向看）：
  - 遵守率：片段写了用 usaver-run，实际用了的是 2/3。有一次 Codex 读到了片段却没照做。
  - 测试输出本身确实少了 69–89%（1449→444，7158→771）。但**总输入几乎没变**（87.1k vs 87.3k）：ctrl 那 7158 token 被 Codex 自己的 `max_output_tokens` 截掉了，模型其实只看到了其中一部分。
  - 每组总成本主要被缓存命中率的随机波动左右（65%–87%），这么小的样本测不出差别。
  - 片段的固定开销：两组第一次请求的输入分别是 21324 和 20647，**差 677 token [实测]**（v1 片段 2358 字节，约 0.287 token/字节）。
  - 实测中发现两个问题，已经修好：日志写进工作区后，会被当成「改了文件」删掉 → 日志改放 `.usaver-logs` 或临时目录，片段里也写明「日志不是项目改动」；原来的检测把 `command -v usaver-run` 也算成「用了」 → 改成严格的正则。

### 3.6 每周能省多少（**估算**，Codex，kit 单位）
- 分母：Codex 10-02 → 11-06 去掉导入会话后共 50.9M 单位，16 天，折合每周约 **22M**；请求数 3013，折合每周约 1320 次。
- 片段开销 = 每周请求数 × 片段 token × (缓存命中率 × 0.1 + 未命中率 × 1)。缓存命中率取 90%（实测 88–94%）：
  - 完整版 677 × 1320 × 0.19 ≈ **0.17M/周 ≈ 0.8%**；
  - 精简版约 233 token，≈ **0.06M/周 ≈ 0.3%**。
- usaver-run 能省的：重放全部 215 个非导入会话，找出 60 次测试调用。每次能省的量 = min(原始输出, Codex 截断上限) − min(原始输出, 800)，第一次出现按 ×1 计，之后每次请求作为已缓存内容按 ×0.1 计 → 16 天共 **0.21M**，每周约 **0.09M ≈ 0.4%**。
  - 这个估计假设 100% 遵守、瘦身后约 800 token；改成 1500 token 时结果是 0.18M。
  - 不确定性：实测遵守率只有 2/3，所以真实值可能更低；测试多的项目会更高。
- **净效果**：完整版约 **−0.4%**（开销大于收益），精简版约 **+0.1%**（基本持平）。交接规则和窄读规则的收益**没测出来**。Codex 上下文中位数只有 71k，窗口有 475k，「上下文太长」这个主要杠杆在 Codex 上不明显。
- **省额度的结论**：这位所有者主要花额度的地方是 Claude Code，那里的杠杆是上下文长度，已经有守卫。跨工具这部分**主要是让用量看得见**：codex_usage 和 usaver-status。省 token 上只建议装精简版。

---

## 4. 适应新模型：只读检查清单（修复由主会话另派人）

| 文件:行 | 写死了什么 | 遇到新模型会怎样 | 建议的安全回退 |
|---|---|---|---|
| tools/weekly_compare.py:7 | 所有模型共用一套价格比 `W=dict(cr=0.1,cw=1.0,inp=1.0,out=5.0)` | 新模型价格比不同时会**悄悄算错**，不提示 | 按模型族建价格表；查不到的模型用默认值，并在报告里列出「未知模型：按默认价格比，请核对」 |
| tools/weekly_compare.py:36、38 | 缓存写入倍数 1.25 / 2.0（Anthropic 的价格） | 同上 | 一起放进价格表 |
| tools/weekly_compare.py:43、45 | 用子串 `'opus'` / `'sonnet'` 分类，其他一律归 other | **haiku 和 fable** 以及以后的新族都被并进 other；Opus 占比会偏低 | 分类改用「族名表 + 原样保留未知名」；other 占比超过 5% 时在报告里提示 |
| tools/weekly_compare.py:76、80、84、90 | 表头和分组只有 Opus / Sonnet | 新族在第一张分组表里根本不出现 | 按实际出现的模型动态生成表 |
| hooks/context_guard.py:17–18 | 绝对阈值：软线 160k、强线 300k、下限 100k、Pro 封顶 180k / 195k | 新模型窗口小于 300k 时（例如 200k）强线落在窗口外，**永远不会触发**，会先被自动压缩；而 Pro 封顶 11-21 之前不生效 | 用 statusline payload 里的 `context_window.context_window_size`（本机样本里有）把软线和强线压到窗口的 60% / 80% 以内，usaver-status 已经这样做；拿不到窗口时用保守的 200k 假设 |
| hooks/context_guard.py:140–151（`lines()`） | 不知道模型窗口 | 同上 | 给 `lines()` 加 `window=None` 参数 |
| hooks/context_guard.py:232、257 | 只认 Claude transcript 的 `type=="assistant"` 和 `input/cache_read/cache_creation` 这几个字段 | 字段改名或换工具时 n 是 0 或者读不到 usage，然后**静默不提醒**（Codex 上就是这样） | 读不到 usage 时在 mode.json 里写 `usage_unreadable`，状态栏显示「ctx ?」 |
| hooks/statusline.sh:17–18 | ctx 只按 current_usage 的三个字段相加，没用 payload 里的 `context_window_size` 和 `used_percentage` | 新字段会被忽略；阈值和窗口无关 | 同 context_guard |
| hooks/statusline.sh:38 | display_name 缺失时显示 "?" | 安全 | — |
| ~/.claude/skills/model-router/SKILL.md:13、15、19、21、25、28、38、40、44、45 | 写死了 haiku / sonnet / opus 三档别名，以及「Sonnet 便宜 50.2%」这个针对特定版本的实测值 | 新档位（比如 Agent 工具现在已经有的 `fable`）不在表里；换代以后价格比也会过期 | 改成按能力档描述（最便宜 / 中档 / 最强），别名表单独一节；实测数字标上模型版本和日期 |
| ~/.claude/skills/model-router/SKILL.md:16、31–33 | 「父上下文 8 万」这类绝对值 | 窗口变了以后比例就不对 | 改成占窗口的比例 |

我自己的适配层：codex_usage 和 usaver-status 都原样显示模型名，不做分类，未知模型有离线测试覆盖。usaver-status 用窗口比例收紧阈值；codex_usage 的价格比是单一一套，报告里写明了「不是订阅额度，模型价格比未证实」。

---

## 5. 「新增工具适配」步骤清单（不写死任何版本）

1. **找机制**：对照第 1 节那 6 行（钩子、说明文件、压缩、恢复、用量记录、状态栏），每一项标上 [实测] / [文档] / [未证实]。先看 `--help` 和本机的配置目录，再看官方文档。
2. **零成本接入，先做**：把片段写进这个工具读的说明文件：`python3 adapters/apply.py agents apply --target <它的说明文件> --variant lite`。再让 PATH 里有 `usaver-run` 和 `usaver-status`：`apply.py bin link`。
3. **用量数据源**：在 `adapters/<工具>/` 下写一个 `<工具>_usage.py`。输出统一的记录：ts、model、cwd、side、origin、inp（含缓存）、cached、cw、out、reasoning。可以直接复用 `codex_usage.summarize()` 和 `render()`。会话只用文件的修改时间和 cwd 来识别，**不依赖版本号**。字段缺失就按 0 算并计数，不要崩。
4. **状态显示**：在 `usaver_status.py` 里加一对函数 `<工具>_candidates(cwd)` 和 `<工具>_state(path)`，返回 `{model, window, ctx, rate, samples}`，然后在 `status()` 里登记，在 `CLEAR_CMD` 里写上这个工具的「开新会话」命令。
5. **额度**：如果工具日志里有窗口百分比，就转成 rate.json 的格式（five_hour / seven_day，各带 used_percentage、resets_at），这样 `context_guard.get_mode()` 不用改就能分档。没有额度数据就不显示，不要编。
6. **钩子（可选）**：只有当这个工具能**改写命令**（相当于 updatedInput / tool_input）并且能**注入上下文**时，才写薄的钩子适配，复用 test_filter 的 `rewrite()` 和 context_guard 的 `lines()`。钩子要信任的工具，把信任步骤写进说明，不要绕过。
7. **测试**：离线测试用伪造的日志（新格式 + 坏行 + 未知模型名 + 空文件）；真机冒烟复制 `live_codex.py` 的结构，分 treat / ctrl 两组，并用这个工具自己打印的 token 数核对解析结果。
8. **登记**：在 `adapters/matrix.json` 里加这个工具的列，I 的验收矩阵会读它；README 的 "Using with other AI coding tools" 表加一行。

---

## 7. ~/.agents/skills 同步（C 发现的问题，归 H）

**原因已查明 [实测]**：Codex Desktop 打开了「外部 agent 导入同步」（`config.toml [desktop]` 里 `external-agent-import-sync-enabled = true`，`item-types = "all"`）。11-06 00:42 它把 `~/.claude/skills/*` 拷到了 `~/.agents/skills/*`，拷的时候**把文本里的 Claude / claude 全部替换成了 Codex**，于是出现了 `~/Documents/Projects/Codex-usage-saver/state/mode.json`（model-router）和 `.Codex/handoff.md`（handoff）这样的错误路径。这份拷贝还是旧快照，model-router 少了之后加的「派给 Codex」「实测依据」等节。同一天同一时间点，它还导入了 409 个 Claude 会话（见第 0 节）。以后会不会再同步一次、把文件覆盖掉 **[未证实]**。

**方案：`adapters/skills_sync.py`（plan / apply / check / revert）**
- **单一来源**：`~/.claude/skills/<name>/SKILL.md`。C 等人照常改这里，不需要学新格式。默认只处理套件自己的 `model-router` 和 `handoff`，别的 skill 不碰。
- **生成规则**：
  - **路径不写死**：来源里出现的套件路径（`~/…/claude-usage-saver`，或任何 `/Users/<名>/…/claude-usage-saver`）一律换成运行时探测到的套件目录（本仓库位置，或 `--kit` / `$USAVER_KIT`）；
  - **不做** Claude→Codex 的文字替换；
  - 可选的工具专属块 `<!-- only:claude -->…<!-- /only -->` 和 `<!-- only:codex -->…`；
  - frontmatter 后面加一段 Codex 适配说明（`adapters/skills/notes/codex.md`：Claude 的模型别名对应到哪一档，以及 Agent、fork 这些工具在 Codex 里没有）；
  - **接项目画像**：`--profile <JSON>` 里的 `skill_notes.codex[.<skill>]` 会追加进去，I 的画像文件只要加这个键就能接上；
  - 写一行生成标记，带来源 sha256。
- **check**：退出码 1 时有三种情况：`stale-generated`（来源改了但没重新生成）、`foreign`（被别的同步覆盖了，或者是旧的导入副本）、`missing`。以后可以挂进 weekly 流程或 SessionStart，发现漂移就提醒。
- **加新工具**：在 `TOOLS` 里加一行目标目录，再写一个 `notes/<tool>.md`。
- **测试**：离线测试 2 项，在**临时 HOME** 里用真实两份文件的副本，跑 apply → check(0) → 改来源 → check(stale) → revert，最后**逐字节恢复到 apply 前**。每个测试结束都会核对真实 `~/.agents` 没有变化。全套离线测试现在是 **36/36**。
- **对真实 HOME 只跑了 `plan`（只读）**：两个文件都是 `foreign`，各有 1 处错误路径。**没有 apply。**
- **请所有者决定**：
  - 要不要应用：`python3 ~/Documents/Projects/claude-usage-saver/adapters/skills_sync.py apply`。原文件会先备份到 `~/.usaver-backups/skills/<时间>/`；撤回：`python3 ~/Documents/Projects/claude-usage-saver/adapters/skills_sync.py revert`。
  - 要不要在 Codex Desktop 里把 skill 的导入同步关掉（`external-agent-import-sync-item-types` 不再包含 skills），免得它下次又用错误替换覆盖。这一项是改 `~/.codex/config.toml`，由所有者自己在 Desktop 设置里改。我没改。

---

## 6. 写过的文件和副作用
- 新建：`adapters/` 整个目录；`reports/round5_H.md`；`backups/README.md.pre-r5H`；在 README.md 末尾追加了一节。
- 运行时产生（都在套件的 state/ 里）：`state/codex/rate.json`、`state/status_hist_claude.json`、`state/status_hist_codex.json`（usaver-status 写的）。
- `~/.codex/sessions` 里多了一些 rollout：10 次 live 运行（其中 4 次因为额度满了失败），加 1 次钩子探测。这是 Codex 的正常记录，**没有改 ~/.codex 的配置**。临时项目都在 scratchpad 里：`/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/live1`、`/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/live2`、`/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/cx1`。

## 总结
- **做了什么**：
  - 查清了 4 个工具 × 6 项机制；
  - 做了适配矩阵（8 个部件，另有 matrix.json）；
  - 实现了 usaver-run、codex_usage、AGENTS 片段（full / lite）、apply.py（应用 / 撤回）、usaver-status（含 5 小时用完预测）；
  - 列出了适应新模型的只读问题清单，写了新增工具的步骤清单。
- **数字**：
  - 离线测试 36/36（含 skills 同步在临时 HOME 里的往返测试）；
  - 真机 Codex 6 组：解析出的 token 和 Codex 打印的数 6/6 完全一致；遵守片段用 usaver-run 2/3；测试输出 −69% 到 −89%，但总输入基本不变（Codex 自己会截断）；片段开销实测 +677 token/请求；
  - 估算每周：完整版净 −0.4%，精简版净 +0.1%，基本是零。
  - Codex 历史：49.5M 单位 / 15 天，每次请求 16.9k，缓存命中 94%；607 个会话文件里有 409 个是导入的 Claude 会话。
- **要所有者决定的事**：
  0. 要不要用 `skills_sync.py apply` 修 `~/.agents/skills` 里那两份导入时被改坏的 skill，要不要关掉 Codex Desktop 的 skill 导入同步（第 7 节）。
  1. 要不要把精简版片段装进 Codex 全局：`python3 ~/Documents/Projects/claude-usage-saver/adapters/apply.py agents apply --variant lite`。
  2. 要不要把命令放上 PATH：`python3 ~/Documents/Projects/claude-usage-saver/adapters/apply.py bin link`。
  3. `~/.codex/hooks.json` 里现有的 context_guard 条目：大概率因为没信任而不运行，运行了也是空转。是留着、删掉（由所有者 mv），还是等 rollout 版守卫做好再信任？
  4. 是否允许在 scratchpad 临时项目里用 `/hooks` 信任或者用 bypass 参数，以便实测 Codex 的 PreToolUse 改写（本机 0.163.0 二进制里没有 `updatedInput` 字符串）。
  5. Codex 5 小时额度恢复后（23:29 以后），是否补跑 `live_codex.py --reps 3`，把 n 加大。
- **撤回命令**：`cp -p ~/Documents/Projects/claude-usage-saver/backups/README.md.pre-r5H ~/Documents/Projects/claude-usage-saver/README.md; mkdir -p ~/Documents/Projects/claude-usage-saver/_to_delete; mv ~/Documents/Projects/claude-usage-saver/adapters ~/Documents/Projects/claude-usage-saver/_to_delete/adapters-r5H; mv ~/Documents/Projects/claude-usage-saver/state/codex ~/Documents/Projects/claude-usage-saver/state/status_hist_claude.json ~/Documents/Projects/claude-usage-saver/state/status_hist_codex.json ~/Documents/Projects/claude-usage-saver/_to_delete/`。如果已经应用了片段，或者已经链接了命令，先跑 `python3 ~/Documents/Projects/claude-usage-saver/adapters/apply.py agents revert; python3 ~/Documents/Projects/claude-usage-saver/adapters/apply.py bin unlink`；如果已经应用了 skills 同步，再跑 `python3 ~/Documents/Projects/claude-usage-saver/adapters/skills_sync.py revert`。
