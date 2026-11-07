# 第 5 轮 J：新对话 / 新项目 / 新模型之外，套件还需要适应的维度（多模型讨论）

> 只做了讨论和读代码，**没有改任何项目文件**。实验只有一种：给隔离的 state 目录（`USAGE_SAVER_STATE` 指向 scratchpad）喂人为构造的 payload，复现 #7。
> 参与者：Claude Opus（J，读过源码）、Codex（gpt-6.1-sol，负责第 0、1 轮）、Claude Sonnet（第 2 轮的替补裁判，Codex 额度用完后接手，读过源码）。
> 讨论原话都存在 `/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/r5J/`，文件清单见文末。
> **评审基线**：Claude Code 2.1.318、codex-cli 0.163.0。行号对应 2026-11-06 18:50 的文件，sha1 前 12 位：context_guard b001c81f18e4、session_resume 330f9174a822、statusline b09f5a67e32d、test_filter 9c94c9f06cf0、limit_logger 2eae9ac40a5b、codex_task f5b7ccbbad50、codex_review 997916ea4332。讨论期间（18:41–18:48）这些文件被 C、D、I 改过，所以早期草稿里的行号已经作废。这件事本身就是 #1 的证据。

## 流程

1. **各自独立列清单**：Claude 先写 C1–C26（`01_claude_list.md`）。Codex 只拿到 README 和 brief 的"目标"一节，独立写出 X1–X30（`03_codex_list.md`）。双方事先都没看过对方的清单。
2. **第 1 轮**：Codex 挑 C 系列的毛病（`05_codex_r1_critique.md`，它自己注明"未核实源码"）；Claude 对照源码挑 X 系列的毛病（`06_claude_r1_critique.md`）。
3. **第 2 轮**：Claude 回应（`09_claude_r2_response.md`）。Codex 第 2 轮**撞上额度上限，失败了**（`08_codex_r2.log`："You've hit your usage limit … try again at 11:29 PM"）。为了仍有多个模型参与，改请 **Sonnet** 当独立第三方，对三条源码断言和分歧点 D1–D10 逐条裁定，并要求它亲自打开源码核实（`10_sonnet_r2_adjudication.md`）。
4. 标注含义：**[实测]** 是在隔离环境里跑过的；**[读码]** 只读了代码；**[推测]** 没有核实。

讨论过程本身就撞上了 #8：Codex 额度用完；有一次在后台跑 `codex exec`，一直停在 "Reading additional input from stdin..."，加 `< /dev/null` 后才继续；macOS 上没有 `timeout` 命令（rc=127）。

---

## 最终清单（按"对所有者的价值 × 出问题的概率"排序）

每条依次写：现在的表现（文件:行号）、建议怎么改、怎么验收（测试场景）、交给谁（"新开"表示 brief 的 A–I 里没有人负责）。

### 1. 套件自身升级与回滚：开发版就是生产版　价值 高 × 概率 高（三方一致）
- **现状 [读码]**：真实的 `~/.claude/settings.json` 把 hook 直接注册到项目的工作副本 `~/Documents/Projects/claude-usage-saver/hooks/*.py` 和 `statusline.sh`。任何 agent 改一行，所有者所有在跑的真实会话从下一次 hook 调用起就用上新代码（HANDOFF.md 写着"G2 已在真实环境生效"）。本次讨论的 10 分钟里，就有 6 个 hook/工具文件被不同的 agent 改过。hook 没有版本号；回滚只能手动从 `backups/*.pre-r5X` 拷回来。hook 文件如果有语法错误，会在 try 外面直接 exit 1 并打印 traceback：不会阻断会话，但每条消息都会报错。
- **建议**：把"安装版"和"工作副本"分开。用 `tools/release.py promote` 把通过复核的版本拷到 `~/.claude/usage-saver/releases/<日期-短哈希>/`，再让符号链接 `current` 指过去，settings 只认 `current/…`。`rollback` 把链接切回上一个版本。state 继续共用，由 `USAGE_SAVER_STATE` 指定（各 hook 现在都已支持）。
- **验收**：在临时 HOME 里 promote 出 v1，然后往工作副本的 `context_guard.py` 写一个语法错误，用 `--settings` 跑一次 UserPromptSubmit，期望 exit 0、行为和 v1 一样。执行 `rollback` 后，`readlink current` 应指向上一版。
- **交给**：新开。改 settings 的部分只做成"一条命令应用 + 一条命令撤回"的脚本，由主会话问过所有者再执行。

### 2. 无人值守和单次长回合：守卫只在提交消息时运行　价值 高 × 概率 高（三方一致）
- **现状 [读码]**：`context_guard.py` 只挂在 UserPromptSubmit 上，唯一的出口是"请所有者 /clear"（`context_guard.py:331-333`）。通宵运行、"一直修到测试通过"这类一句话带出几十轮工具调用的循环，守卫都不会再被触发，提醒没人接收。真正兜底的是真实 settings 里的 `autoCompactWindow=300000`。这一条合并了 C3、C21、X7、X25。
- **建议**：B 已经在做（上限压到 150k，空闲时先压缩）。J 补一条判据：最近 N 小时没有人工消息，或者是 `-p` / henchmen 会话时，守卫不再要求"请所有者 /clear"，改为"写好交接后继续，依赖自动压缩"，免得每轮都注入一段没人看的提醒。
- **验收**：回放一条通宵 transcript，统计注入次数、注入字符总数和此后的平均上下文。看的是平均上下文，不是提醒次数。
- **交给**：B。

### 3. 静默失效和上游 payload 格式漂移（可观测性）　价值 高 × 概率 中（三方一致）
- **现状 [读码]**：所有 hook 都吞掉异常并 exit 0（`context_guard.py:336`、`session_resume.py:783`、`test_filter.py:234`、`statusline.sh` 的 FALLBACK）。字段改名时：`context_guard.py:323` 把缺失的 usage 当 0，于是永远不提醒；`statusline.sh:22` 读 `context_window.current_usage`，改名后显示 `ctx 0k`；状态栏的 5h/7d 不标采样时间，旧读数照常显示（`statusline.sh:47`）。`last_status_sample.json` 只在文件不存在时写一次（`statusline.sh:49`），没法拿来比对新旧格式。payload 里有 `version` 字段可以利用。这一条合并了 C9、C10、C11、X3、X24。
- **建议**：每个 hook 往 `state/health.json` 写一条 `{hook, ts, ok, err_class, cc_version}`。Claude Code 版本一变，就自检一次关键字段（`context_window.current_usage`、`rate_limits`，以及 transcript 里的 `usage`、`isSidechain`）；缺字段时状态栏显示"套件⚠ 字段缺失"。"坏没坏"按**活动**判断：transcript 里有新的 assistant 记录，而守卫最近一次成功运行比它早，才算坏，不按时钟判断（Codex 的顾虑是：低频使用时几小时没心跳不代表坏了）。Sonnet 补了一点：health 文件自己也可能写失败，写失败时状态栏要显示"?"，不能显示正常。读数超过 6 小时就在状态栏标"(旧)"。
- **验收**：(a) statusline 收到 `current_usage` 改名的 payload，显示"字段缺失"，而不是 `ctx 0k`；(b) 守卫收到 usage 键改名的 transcript，`health.json` 里出现 `ok:false`；(c) payload 的 version 变了，触发一次自检。
- **交给**：新开（可以和 H 的"写死的模型名"检查合并做）。

### 4. 同一项目多个会话并行：HANDOFF.md 只有一份，会互相覆盖　价值 高 × 概率 中高
- **现状 [读码]**：session_resume 的指针已经按 Claude Code 进程区分开了（`_choose`，`session_resume.py` 约 450 行），但交接文件每个项目只有一份（`session_resume.py:35` NAMES）。handoff skill 规定"整节替换"，两个会话（两个终端、henchmen、本轮 9 个 agent 同在一个项目）各写一次，后写的就把先写的盖掉；另一个会话 /clear 后，会拿着别人的交接"接着做"。rate.json 的无锁合并（C26）能自愈，不单列。
- **建议**：handoff skill 写之前先看：如果 HANDOFF.md 带标记、在 2 小时以内、又不是本会话写的，就改写到 `HANDOFF.<session 前 8 位>.md`。session_resume 优先选上一段 transcript 里用 Edit/Write 改过的那份（`edited_in()` 已经有了，`session_resume.py:175`）。
- **验收**：构造两段 transcript，A 用 Write 写 `HANDOFF.md`，B 写 `HANDOFF.b1234567.md`。A 的进程 /clear 后应注入 A 的交接，B 的进程应注入 B 的。
- **交给**：新开（session_resume + handoff skill）。这会改变交接文件的命名习惯，**需要所有者决定**。

### 5. 子 agent 和无头会话：守卫管不到，额度数据也不刷新　价值 高 × 概率 高（已知）
- **现状 [读码]**：子 agent 不触发 UserPromptSubmit，`last_usage` 会跳过 `isSidechain` 的记录（`context_guard.py:296`）。`claude -p` 和 Codex 不跑状态栏，所以通宵无头运行时 `rate.json` 不更新，6 小时后 `get_mode` 退回"正常"（"额度数据已过期"分支）。这一条是从 C5 拆出来的两件事。Codex 认为它和 X6/X9 重复；Claude 坚持拆开，因为一个是拓扑问题，一个是数据源问题，处理方法不同。Sonnet 认为可控性弱，但同意放进前 5。
- **建议**：拓扑交给 B/C（子 agent 上限、改派 Codex）。数据源方面：在无头会话里把"额度数据缺失"写进 `health.json`（并入 #3）；StopFailure 时 limit_logger 的记录也可以当额度锚点。
- **验收**：在 `-p` 隔离环境里跑 30 分钟，`health.json` 应标出"rate 数据过期"，而不是悄悄按"正常"档处理。
- **交给**：B（拓扑）；数据源部分新开，并入 #3。

### 6. 交接的质量：死路、膨胀、工作区漂移　价值 中高 × 概率 高，改动成本低
- **现状 [读码]**：handoff skill 的模板（`~/.claude/skills/handoff/SKILL.md`）只有 红线 / 目标 / 下一步 / 进度 / 关键文件，没有"试过不行的方案"这一栏，所以 /clear 后容易重走死路（X13）。新鲜度只看 mtime（`STALE_HOURS=72`，`session_resume.py:37`），不记 git 分支和 HEAD，切过分支后注入的"测试已通过"就是错的（X15）。多次交接会层层叠加（X17）：skill 规定 60/120 行，超长时 session_resume 只能截掉中间（`session_resume.py:753`）。这一条合并了 X13、X15、X17，都是 Codex 提出、C 系列漏掉的。
- **建议**：模板加一行"写于：分支 X @ 短哈希"，再加一节"已排除 / 死路"。session_resume 读到 HEAD 行就和当前 HEAD 比，不一致时在注入开头写"交接写于 X@abc，现在是 Y@def，进度里的测试结论可能已失效"。超过 120 行时提示把旧内容移到 history。
- **验收**：fixture 交接里写 `@abc123`，而当前仓库 HEAD 不是它，注入里应出现"分支/HEAD 已变"；交接有 200 行时应出现"超长"提示。
- **交给**：新开（handoff skill + session_resume），I 配合（没有 git 的项目跳过 HEAD 检查）。

### 7. 手动刷新额度后，状态卡在旧的高读数　价值 中 × 概率 未知（这一条有分歧）
- **现状 [实测，隔离 state，当前版本复现了两次]**：statusline 合并 rate.json 时，同一 `resets_at` 窗口里只接受**更大**的读数（`statusline.sh:39-40`）。如果刷新后 `resets_at` 不变，used% 从 93 降到 3，rate.json 仍然是 93。守卫的"快用完"判断不看读数新旧（`context_guard.py:99`），所以 `--mode` 输出"快用完 0.6"，一直持续到窗口结束。复现目录：`scratchpad/r5J/exp_refresh/`、`exp_refresh2/`。
- **分歧**：Codex 第 0 轮把它（X10）列为高×高；Claude 读码后一度列为最高优先级之一。Sonnet 指出两点：(1) 这是 **CG11 所有者已经定下的取舍**（`context_guard.py:17`，"保持现状"，理由是窗口内用量只增不减），只是当时没考虑外部刷新；(2) 会不会卡住，取决于一个**没验证的前提**：刷新时服务端改不改 `resets_at`。如果 `resets_at` 往后推超过 60 秒，`statusline.sh:37` 会把它当新窗口接受，自己就解开了。而且后果只是软线 ×0.6、提醒提前，不会拦截任何操作。Claude 接受这两点，降为中。
- **建议**：先在下次刷新时验证：刷新前后各看一眼 `state/rate.json` 和 payload 里的 `resets_at`（只读）。如果真的不变，可选的修法有：(A) 同一窗口内连续 N 次（比如 3 次、跨两个会话）读到明显更小的值，就接受；(B) 收到 `quota_auto_resume_fired` 时作废旧读数；(C) 维持现状，在使用说明里写"刷新后删掉 state/rate.json"。
- **验收**：在 exp_refresh 场景下，修复后 seven_day 应为 3%、档位不再是"快用完"；SL02/SL03 回归不变：另一个空闲会话带着 90% 的旧读数，不能把它顶回去。
- **交给**：新开（第 4 轮 statusline 和守卫由 D 维护）。**要不要推翻 CG11，需要所有者决定。**

### 8. Codex 不可用、网络卡住、后台跑时卡在 stdin　价值 中 × 概率 高（本次讨论中实际发生）
- **现状 [实测]**：(a) Codex 额度用完时 `codex exec` 返回 rc=1，不生成 `-o` 文件。`tools/codex_task.sh:78-81`（C 在 18:45 刚加的）已经能认出 "usage limit"，并在结论第一行写"额度已用完"。但 `tools/codex_review.sh` 是把整份日志写进报告，末尾加一行 "exited N"，ERROR 要读者自己找。(b) 两个脚本都**没有超时**（macOS 没有 `timeout`），也**没有 `< /dev/null`**。codex_task.sh:17 推荐后台跑，而后台跑时我实际遇到过卡在 "Reading additional input from stdin..."。
- **建议**：两个脚本都加 `< /dev/null`；超时用"后台进程 + kill"实现（默认 15 分钟），超时就写"Codex 超时"；codex_review.sh 也要认出 usage limit。在 model-router 里写明：结论第一行是"不可用"或"超时"的，**不能当作通过**。不自动改派 Claude（三方一致：那会悄悄花 Claude 的额度），由主会话决定。
- **验收**：在 PATH 前面放一个假 `codex`：(1) 打印 usage-limit 错误并 exit 1，结论第一行应含"额度已用完"；(2) 一直读 stdin，脚本应在超时内返回、第一行写"超时"；(3) sleep 超过超时时间，进程应被杀掉，不留残余。
- **交给**：C（这两个脚本归 C）。

### 9. 换安装位置、换机器、别人 clone　价值 中 × 概率 高（只要有人 clone，就一定会遇到）
- **现状 [读码]**：I 在讨论期间（R5I，CG13）已经把 `context_guard.py:21-22` 和 `statusline.sh:4-9` 改成按脚本所在目录找路径，并支持 `USAGE_SAVER_STATE`。**还剩下这些写死的路径**：`test_filter.py:15` 的 LOGDIR、`statusline.sh:9/53` 的回退路径、`tools/weekly_compare.py:5`、`tools/codex_task.sh:26`、`tools/codex_review.sh:9`。README 的 Install 一节仍然写着"路径假定在这里"。
- **建议**：把剩下几处都改成按脚本目录找，或读 `USAGE_SAVER_HOME`。和 #1 一起做，因为安装版本来就不在 Projects 下。
- **验收**：`cp -R` 到 `/tmp/x/kit`，设 `HOME=/tmp/h`，用 `--settings` 注册，逐个跑 5 个 hook。state 只应写到 `/tmp/x/kit/state`（或 `USAGE_SAVER_STATE` 指定的位置），真实 `~/Documents/…/state` 的 mtime 不变。
- **交给**：I（扫尾，CG13 已经在他手上），H 配合（跨机器）。

### 10. 短回复"1"有歧义　价值 中 × 概率 低到中
- **现状 [读码]**：`session_resume.py:767-768` 规定，「1」「ok」这类短回复**一律**当作"接着做"。所有者常用数字回复。如果 /clear 前 Claude 的最后一条回复是"选 1/2/3"，那么"1"是在选选项。Sonnet 指出：摘要里已经带了 Claude 的最后回复（保留结尾），而且"刚给出选项就 /clear"这种时序少见，所以概率比 Codex 估的低。真正的风险是：模型不核对选项，直接照交接里的"下一步"开干。
- **建议**：digest 单独提取"上一条回复最后的提问或选项"，放在注入内容最前面。指令改成："如果上面有待回答的选项，数字回复就按选项理解，并先用一句话确认"。
- **验收**：fixture transcript 的最后一条 assistant 以"1) 方案A 2) 方案B，选哪个？"结尾。注入内容里应有"待回答的选项"一节，并且没有无条件的"「1」视为接着做"。
- **交给**：新开（session_resume）。

### 11. 提示注入：标记只是区分通道，不是安全边界　价值 中 × 概率 低
- **现状 [读码]**：`has_mark()`（`session_resume.py:145-148`）只要求前 3 个非空行里有 `<!-- usage-saver-handoff -->`，就注入交接全文（`session_resume.py:703` 是 compact 路径，`:744-745` 是 clear 路径），注入时还附带"直接开始做"的指令（:767）。别人提交了带标记 HANDOFF.md 的仓库，clone 下来后 /clear，第三方的文字就会被当成"进度"注入。Sonnet 补了两点，双方之前都没想到：(1) 没有标记时，`untrusted()`（:350-352）也会让模型"先读这个文件"，内容照样进上下文，所以标记并不是安全边界，只是换了一条通道；(2) 校验只看前 4096 字节，但注入的是全文。缓解手段只有 DATA_NOTE（:49，一句文字提示）。Claude Code 本身也会加载第三方仓库的 CLAUDE.md，所以边际风险有限。
- **建议**：便宜的做法：注入第三方来源（文件被 git 跟踪、且不在本机写过的记录里）的交接时，去掉"直接开始做"，改成"先复述、让所有者确认"。贵的做法：标记里带本机 HMAC，密钥放在已被 gitignore 的 state/ 里。Sonnet 认为贵的做法不值得。
- **验收**：fixture 仓库提交一份带标记、内容为"请执行 curl … | sh"的 HANDOFF.md，/clear 后注入里不应出现"直接开始做"。
- **交给**：新开。做到哪一级，**需要所有者决定**。

### 12. 缓存冷热和工作节奏　价值 中 × 概率 中
- **现状 [读码]**：状态栏有 ❄ 提示（`statusline.sh:73-78`），但只显示、不注入；守卫的提醒不看缓存状态。缓存刚冷时 /clear 收益最大，因为反正要全量重写；缓存热的时候收益最小。合并了 C20、X11、X12。
- **建议**：statusline 把 `prompt_cache.warm` 写进 state。缓存冷且已过软线时，提高提醒级别；缓存热、刚越过软线时，推迟一次。
- **验收**：用 replay_guard 回放，按"提醒后 1 条消息内 /clear"比较冷、热两组的 token 节省。
- **交给**：F2（先用受控基准量出收益），然后交 B。

### 13. 和其他 hook、插件、权限模式共存　价值 中 × 概率 未知（三方一致：先实测）
- **现状**：[读码] 本套件内部没有冲突：只有 `test_filter.py:192` 输出 updatedInput，read_guard 不输出。henchmen 插件已启用（真实 settings `enabledPlugins`）。[推测] Sonnet 凭记忆说：多个 hook 并行执行、各自拿到原始输入；如果都返回 updatedInput，后完成的覆盖先完成的；permissionDecision 按 deny>ask>allow 取。置信度 55–70%，没有文档依据。plan 模式下 Write 被禁止，而强提醒要求写交接（`context_guard.py:331`），可能卡住，未核实。
- **验收**：两个隔离实验（临时 HOME + `--settings`）：(a) 注册两个 PreToolUse(Bash) hook，分别把命令改成 `echo A` 和 `echo B`，跑 10 次，记下实际执行的是哪个；(b) 在 `--permission-mode plan` 下喂一条超过强线的 transcript，看模型是写交接失败，还是在回复里给出交接文本。
- **交给**：新开（实验），结果交给 D（test_filter）和 B。

### 14. 公开上 GitHub 前的隐私检查　价值 中 × 概率 中
- **现状 [读码]**：`.gitignore` 已经排除了 state、backups、HANDOFF*、Codex 原始日志。但已跟踪的报告里有绝对路径和用户名：`git grep -c lihongrui` 在 `round4_codex_reviews_summary.md` 里有 29 处，另外几份报告各有 1–6 处。`tools/heldout/` 整个在 git 里，也就是说盲测题已经公开，会影响以后的盲测还算不算"盲"。合并了 C15。
- **建议**：写 `tools/prepush_scan.py`，扫已跟踪文件里的用户名、邮箱、`/Users/`、`.secret` 和 transcript 正文特征，命中就非零退出。push 仍然要所有者点头。
- **验收**：fixture 文件里放一个邮箱，脚本应非零退出并列出位置。
- **交给**：新开（主会话 push 前用）。heldout 要不要移出 git，由 G 决定。

### 15. 语言：交接标题识别　价值 中低 × 概率 中，**I 已经在做**
- **现状 [读码]**：默认的 `RED_HEAD`/`NEXT_HEAD`（`session_resume.py:208-209`）只认 红线 / 未决 / 禁止事项 / red lines 和 下一步 / next steps。I 在讨论期间加了 `adaptive_key_sections`（`session_resume.py:300-322`），先用项目画像给的标题，找不到再用宽松的同义写法。是否覆盖了繁体的"紅線"和英文的 Blockers / Open questions，没有核实。
- **验收**：英文 fixture 用 `## Blockers` 和 `## Next`，繁体 fixture 用 `### 紅線`，compact 注入里应包含对应的节。
- **交给**：I。

### 16. 手机远程　价值 低 × 概率 中（有分歧）
- 手机上看不到状态栏，⚠ 和 ❄ 都失效。但守卫是通过 additionalContext 注入的，不受影响。Codex 和 Sonnet 都认为收益低；Claude 原先评中低，现在接受降为低。建议只做一次验收：所有提醒都不依赖状态栏。手机上打 /clear 是否方便，没有核实。交给：新开（只做验证）。

### 17. 操作系统和 shell　价值 低 × 概率 低（对所有者而言）
- Windows 上没有 fcntl 和 `ps -o lstart`，`owner_id()` 返回 None（`session_resume.py:360` 起），`PtrLock`（:401）在 strict 模式下抛异常，于是不出摘要。这是降级，不是崩溃。Linux 上没有 `st_birthtime`，已经有 try 兜住（:665）。fish 不受影响（[推测]：Bash 工具用的是 bash/zsh）。三方一致的折中是：不做移植，只在降级时往 health 写一次原因，外加一条在 Linux 容器里跑 scorecard 的测试（可选）。交给 H。

### 18. 额度档位和计费方式（Max→Pro、API）　价值 中 × 概率 低（Pro 部分归 E）
- 没有 `rate_limits` 时一律按"正常"档（`context_guard.py:104`）；payload 里的 `cost` 字段没有用上。Pro 的情况由 `cap_from_date`（`context_guard.py:135-143`）和 `pro_switch_1121.py`（autoCompactWindow 300k→200k）处理。API 模式不做（三方一致），只在 README 里写明"API 计费时分档不生效"。交给 E。

### 19. 时区和夏令时　价值 低 × 概率 低
- `resets_at` 是 epoch，不受时区影响；`cap_from_date` 用本地日期（`context_guard.py:143`），在外地时 Pro 切换日可能差 ±1 天；`weekly_compare.py:9-11` 的时间点写死为 UTC，分天按本地日期（WC07）。没发现受夏令时影响的代码。不单独开项，E 在 pro_switch 里注明一下即可。

### 20. state 增长和损坏　价值 低 × 概率 低
- 坏 JSON 一律有回退；mktemp 失败就不过滤，原样执行（`test_filter.py:136`）。`state/` 现在 49MB（`_to_delete` 23M、`replay` 24M），`testlogs/_old` 从不清理（`test_filter.py:191` 起）。并入 #3，在 health 里报告 state 大小。

### 治理和评估维度（不算运行时适应，交给 F1 和所有者；三方一致这样分区）
- **G1 省下的额度与优化本身的开销（X28）**：第 5 轮开了 9 个以上 Opus agent，再加上 Codex。需要一条约束："每轮开销 < 预期周节省 × N 周"，N 由所有者定。
- **G2 每次请求成本 vs 每个任务总成本（X27）**：/clear 会把一个任务拆成更多请求，每次更便宜，但总量未必少。F2 的基准应按任务记账。
- **G3 质量损失会延迟出现（X30）**：可以用"/clear 后 3 条消息内重新读了多少文件"作为返工的代理指标。交给 F2。
- **G4 回归集的代表性（X29）**：G 的新一轮盲测负责；另见 #14（heldout 已公开）。
- **G5 评审基线不固定（Sonnet 补充）**：被评审的文件在评审期间就在变。复核报告应该带被评文件的 sha1，ledger 也只认"这个哈希通过了复核"。这一条和 #1 是同一个根源。

---

## 讨论记录摘要

**第 0 轮（各自独立）**：Claude 列了 26 条，偏环境和机制（路径、payload、OS、权限模式、hook 共存）。Codex 列了 30 条，偏使用过程和质量（交接衰减、"1"的歧义、工作区漂移、测试诊断信息丢失、归因和治理）。两边大约一半重合。

**第 1 轮**：
- Codex 挑 C 系列：26 条里判 17 条"重复"（它注明"重复是说应当合并，不是说问题不存在"），判 C6、C8、C17、C18"收益低"，没有一条判"不现实"。同时它撤回或降级了自己的 X1、X2、X5、X6、X21、X22、X29。
- Claude 对照源码挑 X 系列：驳回或大幅降级 X4（mktemp 失败时原样执行）、X9、X16、X19、X22（mktemp 每次生成唯一文件名，旧日志只是移走、不删）；X8 转给 D；X27–X30 归入治理。Claude 接受 X10、X14、X5（交接文件被覆盖）、X15、X17、X11，认为这些是自己漏掉的。

**第 2 轮**：Claude 回应：大部分"重复"接受为合并；C5 坚持拆成两件；C6、C17 不同意降为低；C16 升为"开发版就是生产版"。Codex 额度用完后，由 Sonnet 独立裁定：D1、D2、D4–D10 同意 Claude，D3（手机远程）同意 Codex。三条源码断言（刷新卡住、标记授予信任、"1"的歧义）**事实都成立，但严重度都比 Claude 说的低**。另外指出了 Claude 和 Codex 都漏掉的 4 点：基线漂移、CG11 是所有者已经做出的决定、标记校验和注入范围不对称、无标记的路径也没有真正隔离。Claude 据此把"刷新卡住"从前 3 降到第 7，把"注入"降到第 11，把"1"降到第 10，手机远程降为低。

## 意见不一致、需要所有者拍板的点

1. **额度刷新后卡在旧读数，要不要推翻 CG11（#7）**：Codex 认为高×高，Claude 中，Sonnet 中偏低。建议先在下次刷新时只读地看一眼 `resets_at` 变没变，再决定选 A（连续 N 次更小的读数就接受）、B（收到 auto_resume 时作废旧读数）还是 C（维持现状，在说明里写"刷新后删 rate.json"）。
2. **安装版和工作副本分开（#1）**：要把真实 settings 里的 hook 路径改成指向 `~/.claude/usage-saver/current/`。这碰到了红线，需要所有者点头；做了以后，"已复核"才真正等于"已上线"。三方一致认为这是第一优先。
3. **交接文件按会话分开命名（#4）**：会改变"每个项目一个 HANDOFF.md"的习惯，AGENTS.md 里也写着这个习惯。
4. **无人值守时守卫不再要求 /clear（#2）**：改话术之后，通宵会话完全依赖自动压缩。
5. **注入防护做到哪一级（#11）**：Claude 主张"第三方来源去掉'直接开始做'"，成本低。Sonnet 认为 HMAC 不值得做，Codex 认为标记不一定构成漏洞。
6. **每轮优化开销的上限 N 周（G1）**。
7. **盲测题 `tools/heldout/` 已经在 git 里公开（#14）**：要不要移出 git（交给 G）。
8. **手机远程是不是真实的使用方式（#16）**：决定这一条要不要保留验收。

## 总结

- **做了什么**：组织了 Claude Opus、Codex、Claude Sonnet 三个模型的讨论（各自独立列清单 → 两轮质询 → 合成），得到 20 条运行时维度和 5 条治理维度，每条都标了文件:行号（附基线哈希）、修改建议、验收场景和负责人。**没有改任何项目文件。**
- **数字**：Claude 独立列了 26 条，Codex 独立列了 30 条，合并去重后是 20 + 5 条。其中 6 条是 Codex 提出、C 系列漏掉的（X10、X14、X15、X17、X13，以及 X5 里"交接文件被覆盖"的角度），4 条是 Sonnet 补充、Claude 和 Codex 都漏掉的。实测 2 项：#7 在当前版本上两次复现（只是逻辑复现，payload 是人为构造的）；#8 在讨论中真实遇到。其余都是读码判断。
- **时效提醒**：讨论期间 I 已经修掉了 #9 的大半（CG13），并做了 #15 的一部分；C 修了 #8 的一部分。其余各条仍是 18:50 那一刻的状态。
- **撤回**：只新建了本报告，以及 scratchpad 里的文件。要撤回本报告：`mkdir -p _to_delete; mv reports/round5_J_dimensions.md _to_delete/`

### 讨论记录文件（都在 `/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/r5J/`）
`01_claude_list.md`（C1–C26）、`02_prompt.txt`（发给 Codex 的独立出题）、`03_codex_list.md`（X1–X30 原话）、`04_prompt_r1.txt`、`05_codex_r1_critique.md`（Codex 第 1 轮原话）、`06_claude_r1_critique.md`、`07_prompt_r2.txt`、`08_codex_r2.log`（Codex 第 2 轮撞额度上限的报错）、`09_claude_r2_response.md`、`10_sonnet_r2_adjudication.md`（Sonnet 第 2 轮原话）、`exp_refresh/`、`exp_refresh2/`（#7 的复现）。
