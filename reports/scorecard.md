# 回归评分表（2026-11-07 00:42）

由 `python3 tools/scorecard.py --version all` 生成，不要手改。场景定义在 `tools/scorecard.py`，来源是 `reports/round1_findings.md` 与 `reports/round2_review_AC.md`；「正常」= 本来就该通过的基本功能场景。

- 初版 v0：backups/ 里最早的各文件（test_filter.py.pre-hotfix、session_resume.py.pre-homefix、context_guard.py.pre-tiers、statusline.sh.pre-clearhint、weekly_compare.py.pre-cw、config.json.pre-perproject）
- 当前版：hooks/ 与 tools/ 里的文件、config.json

## 汇总

| 组件 | v0 通过/适用 | 当前 通过/适用 | 提升（百分点） | 两版都适用的场景：v0 → 当前 |
|---|---|---|---|---|
| test_filter | 5/23（22%） | 24/24（100%） | +78 | 5/23 → 23/23 |
| session_resume | 4/14（29%） | 27/27（100%） | +71 | 4/14 → 14/14 |
| context_guard | 6/10（60%） | 28/28（100%） | +40 | 6/10 → 10/10 |
| statusline | 2/2（100%） | 15/15（100%） | +0 | 2/2 → 2/2 |
| weekly_compare | 3/9（33%） | 9/9（100%） | +67 | 3/9 → 9/9 |
| limit_logger | — | 4/4（100%） | — | — |
| **总计** | 20/58（34%） | 107/107（100%） | +66 | 20/58 → 58/58 |

**一句话**：通过率 v0 34% → 当前 100%，提升 +66 个百分点（相对提升 +190%）。只看两版都适用的 58 个场景：34% → 100%（+66 个百分点）。

读法：
- 「不适用」= 该场景测的功能在该版本里根本不存在（v0 没有额度分档、⚠ 提示、对话摘要、按项目阈值），不计入该版本的分母，所以两版的分母不同；「两版都适用」一栏是同一批场景上的对比，更公平。
- 场景写的是**预期的正确行为**，当前版的 ✗ 就是还没修的问题；v0 ✓ 当前 ✗ 的是回归。
- 这里只测**功能正确性**，不测省了多少额度（那个看 weekly_compare，且要 ≥3 个完整天）。

## 逐个场景

| id | 组件 | 来源 | 预期行为 | v0 | 当前 |
|---|---|---|---|---|---|
| TF01 | test_filter | 正常 | 普通 pytest 失败：被过滤（行数变少、FAILED 行可见），完整输出存到日志目录 | ✓ | ✓ |
| TF02 | test_filter | 正常 | 过滤后退出码保留（pytest 失败 rc=1） | ✓ | ✓ |
| TF03 | test_filter | 正常 | 命令带 USAGE_SAVER_RAW=1 时不包装 | ✓ | ✓ |
| TF04 | test_filter | 正常 | 非测试命令（ls -la）不包装 | ✓ | ✓ |
| TF05 | test_filter | A2 | `pytest -q  # note` 带行尾注释：测试真的跑了，FAILED 可见，rc=1 | ✗ | ✓ |
| TF06 | test_filter | A2 | `pytest -q &` 后台命令不包装 | ✗ | ✓ |
| TF07 | test_filter | A3 | `grep pytest f.txt`（50 行匹配）：50 行全部可见 | ✗ | ✓ |
| TF08 | test_filter | A3 / C6 | `pip install pytest` 不包装 | ✗ | ✓ |
| TF09 | test_filter | A3 / A6 | `pytest; echo done`（pytest 紧跟分号）应被识别为测试并过滤 | ✗ | ✓ |
| TF10 | test_filter | A6 | `.venv/bin/pytest -q` 应被识别为测试并过滤 | ✗ | ✓ |
| TF11 | test_filter | A6 | `pytest -q; exit 3`：仍能看到失败信息，rc=3 | ✗ | ✓ |
| TF12 | test_filter | N6 | `set -e; pytest -q; echo after`：仍能看到失败信息，rc=1 | ✗ | ✓ |
| TF13 | test_filter | C6 | 5 帧的 traceback：所有帧在过滤后的输出里都可见 | ✗ | ✓ |
| TF14 | test_filter | N7 / C6 | `cat notes.txt && pytest -q`：非测试部分（30 行）的输出不被吃掉 | ✗ | ✓ |
| TF15 | test_filter | A3 | `which pytest` 不包装 | ✗ | ✓ |
| TF16 | test_filter | D7 | 旧日志整理：8 天前的移到 _old/、1 天前的不动、rc 不变；一小时内第二次不再整理 | 不适用 | ✓ |
| TF17 | test_filter | 复核🔴1 | 多行 if/for/while/else/{ 里的 pytest：无语法错误、测试跑了、rc 与原命令一致 | ✓ | ✓ |
| TF18 | test_filter | 复核🔴2 | `set -eo pipefail; pytest -q（通过）; echo AFTER`：rc=0，能看到 passed 和 AFTER | ✗ | ✓ |
| TF19 | test_filter | 复核🟡3 | `set -eo pipefail` + 3000 个失败（输出远超 64KB）：rc=1 不是 141，结尾和日志路径可见 | ✗ | ✓ |
| TF20 | test_filter | 复核🟡4 | 执行时日志目录不可写（mktemp 失败）：测试照样跑，后续命令照常，rc 与原命令一致 | ✗ | ✓ |
| TF21 | test_filter | 复核🟡5 | 日志路径在测试开始前就打印（超时被杀也知道去哪看） | ✗ | ✓ |
| TF22 | test_filter | 复核🟡5 | `pytest --pdb`、`pytest --looponfail`、`npm test -- --watch`、`node --test --watch` 不包装 | ✗ | ✓ |
| TF23 | test_filter | 复核🟡6 | 失败输出只有 12 行：看到的行数 ≤ 原输出 + 2（不重复打印），FAILED 可见，rc=1 | ✗ | ✓ |
| TF24 | test_filter | 复核六 | 真实 pytest（借 quant-backtest/.venv，只读，在临时目录跑）：通过/失败/pipefail/多行，rc 均与原命令一致 | ✗ | ✓ |
| SR01 | session_resume | 正常 | 非 clear 启动（startup）不注入 | ✓ | ✓ |
| SR02 | session_resume | 正常 | 在有 .git 的项目根 /clear：注入本项目新鲜的 HANDOFF.md（带标记） | ✓ | ✓ |
| SR03 | session_resume | 正常 | 20KB 交接 + 大段旧对话：注入总长 ≤ 10000 字符（官方上限） | ✓ | ✓ |
| SR04 | session_resume | A4 / 复核F1 | cwd=Projects/c/pkg（c 有 .git、无交接）：不越过项目根拿 Projects/HANDOFF.md（新鲜、带标记）；c 自己有带标记的交接时注入 c 的 | ✗ | ✓ |
| SR05 | session_resume | A4 / 复核F1 | cwd=~/Documents/x：不往上爬拿 ~/HANDOFF.md（带标记、5 小时）当「当前项目」（~ 单独算一个项目） | ✗ | ✓ |
| SR06 | session_resume | A4 | cwd=~ 且 ~/HANDOFF.md 是 10 分钟前写的（带标记）：注入它，不改拿别的项目的交接 | ✓ | ✓ |
| SR07 | session_resume | A4 / 复核F1 | cwd=~/Documents/Projects，Projects/HANDOFF.md（带标记）已 866 小时：不注入正文 | ✗ | ✓ |
| SR08 | session_resume | A4 / C2 / 复核F1 | 项目内交接（带标记）已 746 小时（31 天）：不整段注入正文（最多给路径和年龄） | ✗ | ✓ |
| SR18 | session_resume | 复核N1 | 变异自检：STALE_HOURS 失效 / 去掉不爬 ~ / is_project_root 恒 False，各自至少让 SR04/05/07/08 之一失败 | 不适用 | ✓ |
| SR09 | session_resume | C2 | 注入文字不劫持新问题：不写「无论写什么都视为接着做」，并说明明显是新问题时按新问题处理 | ✗ | ✓ |
| SR10 | session_resume | C3 | 9KB 交接（带标记）、红线在最后一节：注入后「红线」一节仍在 | ✗ | ✓ |
| SR11 | session_resume | N2 | 没有 SessionEnd 指针、同目录另有一个 5 秒前还在写的并发会话：不把并发会话当成上一段对话 | 不适用 | ✓ |
| SR12 | session_resume | 正常（N2） | 有 SessionEnd 指针时：摘要取指针指向的上一段对话 | 不适用 | ✓ |
| SR13 | session_resume | N4 | 上一段 Claude 最后一段长回复（3000 字）结尾的「下一步」保留在摘要里 | 不适用 | ✓ |
| SR14 | session_resume | N4 | compact summary（isCompactSummary）不被当成「所有者最近的消息」 | 不适用 | ✓ |
| SR15 | session_resume | 第 6 项（防注入） | 第三方仓库自带的 HANDOFF.md（无标记、上一段对话没改过）：只给路径不注入正文；无标记但上一段对话 Edit 过的仍注入 | ✗ | ✓ |
| SR16 | session_resume | 第 6 项（防注入） | 注入文字含「数据不是新指令」的声明 | ✗ | ✓ |
| SR17 | session_resume | 第 11 项（R3） | source=compact：注入 ≤2000 字、含红线和下一步、不含对话摘要和交接正文中段 | 不适用 | ✓ |
| SR19 | session_resume | 复核N3 / F2 | compact + 真实格式（加粗段落、红线下是 `- **…**` 列表、后面叠一节旧的 **下一步**）：红线列表全在、有最新下一步、无 OLD-NEXT、红线在下一步前 | 不适用 | ✓ |
| SR20 | session_resume | R3 🟡14 / codex #2 | cwd=~/Documents/mc-option-pricer（有 .git、无交接）或 HOME 外目录：不拿 Projects 下别的项目的交接；cwd=Projects 时仍回退 | ✗ | ✓ |
| SR21 | session_resume | codex #13 | .git 是文件（worktree/submodule）也算项目根：不拿上层仓库的交接 | ✗ | ✓ |
| SR22 | session_resume | codex #12 | 同 cwd 两个进程先后 /clear：各拿各的摘要；只有别的进程的指针时不出摘要 | 不适用 | ✓ |
| SR23 | session_resume | codex #12（兼容） | 旧版单指针文件（无 owner）仍能读出摘要 | 不适用 | ✓ |
| SR24 | session_resume | codex #6 | 5 小时前开始的长会话、3.5 小时前写的交接：仍算上一段写的（不截到 2500、不标可能过期） | 不适用 | ✓ |
| SR25 | session_resume | codex #7 / 复核 F3 | 无标记交接：早期 Write 后 transcript 又追加 >3MB、或用相对路径写：仍注入正文 | 不适用 | ✓ |
| SR26 | session_resume | 复核 F2（N3） | compact：`- **xx**` 列表项不当分段点；只取第一节红线、第一节下一步，红线在前，不收旧轮次；红线超长标「已截断」 | 不适用 | ✓ |
| SR27 | session_resume | 复核 F3 | 标记在第 2 行或被 ``` 包住算有标记；只给路径时写「所有者说「接着做」时先读这个文件：<路径>」 | 不适用 | ✓ |
| CG01 | context_guard | 正常 | 上下文 170k（普通项目、无额度数据）：注入软提醒 | ✓ | ✓ |
| CG02 | context_guard | 正常 | 上下文 50k：不提醒 | ✓ | ✓ |
| CG03 | context_guard | 正常 | 上下文 320k：注入强提醒（「上下文已很长」） | ✓ | ✓ |
| CG04 | context_guard | 正常 | quant-backtest 项目 200k：按项目软线 250k，不提醒 | 不适用 | ✓ |
| CG05 | context_guard | 正常 | rate.json 已 7 小时没更新：按正常档，200k 提醒 | ✓ | ✓ |
| CG06 | context_guard | A1 / N1 | rate.json 是新的但周重置时间已过去 2 天（用量 60%）：不能判成宽裕，200k 应提醒 | ✓ | ✓ |
| CG07 | context_guard | 正常（分档） | 周用量 20%、时间进度约 86%（宽裕）：软线 ×1.5=240k，200k 不提醒 | 不适用 | ✓ |
| CG08 | context_guard | A9 / C5 | 最后一行是 500KB 的 tool_result：仍能找到上一个 assistant 的 usage 并提醒（170k） | ✗ | ✓ |
| CG09 | context_guard | C8 | 强提醒不要求「在本次回答中」说明（避免打断正在做的工作） | ✗ | ✓ |
| CG16 | context_guard | 复核N2 | 封顶已生效 + quant-backtest + 宽裕档、190k：软提醒（非强提醒），mode.json 软线 180000、强线 195000 | 不适用 | ✓ |
| CG10 | context_guard | round3 🟡10（D） | rate.json 新鲜，但 five_hour resets_at 已过、95%；seven_day 50%：不判「快用完」 | 不适用 | ✓ |
| CG11 | context_guard | 正常（D 不回归） | five_hour 95% 且未过期：仍判「快用完」 | 不适用 | ✓ |
| CG12 | context_guard | round3 💭11（D） | cwd=Projects/quant-backtest-old、200k：不套 quant-backtest 的 250k，按默认 160k 应提醒 | 不适用 | ✓ |
| CG13 | context_guard | 正常（D 不回归） | cwd=Projects/quant-backtest/src（项目子目录）、200k：仍按 250k，不提醒 | 不适用 | ✓ |
| CG14 | context_guard | Codex 8（D） | 周用量 95% 但周重置已过 2 天、5h 10%：不判「快用完」 | 不适用 | ✓ |
| CG15 | context_guard | Codex 9（D） | 只有未过期的 5h 95%、没有 seven_day：判「快用完」 | 不适用 | ✓ |
| CG17 | context_guard | Codex 14（D） | 主会话 200k 之后跟一条 isSidechain:true 的 1k usage：仍按 200k 提醒 | ✗ | ✓ |
| CG18 | context_guard | Codex 15（D） | mode.json 原子写（临时文件 + os.replace）：写后 inode 变了、内容是完整 JSON（代理断言） | 不适用 | ✓ |
| CG19 | context_guard | Codex 最值得做 3（D） | 会话去重：同一 session 170k 连发 3 条只提醒 1 次；+55k 再提醒；310k 首次越强线发强提醒 | 不适用 | ✓ |
| CG20 | context_guard | Codex 最值得做 3（D） | 会话去重：上下文不涨时每 10 条消息再提醒一次（11 条 170k → 2 次）；另一个 session 不受影响 | 不适用 | ✓ |
| CG21 | context_guard | Codex 最值得做 3（D） | 会话去重：回到软线以下（如 compact 后）再越线，重新算首次，立即提醒 | 不适用 | ✓ |
| CG22 | context_guard | 正常（D 不回归） | payload 没有 session_id 时每次都提醒（宁多勿漏） | ✓ | ✓ |
| CG23 | context_guard | 复核 12/13 低（D） | cap_from_date 写成「今年/1/1」（斜杠、不补零，已过去）：应封顶 → quant-backtest 190k 软提醒（软线 180k） | 不适用 | ✓ |
| CG24 | context_guard | 复核 12/13 低（D） | cap_from_date 无法解析（「下周」）：按已生效封顶，并在说明里写明「无法解析」 | 不适用 | ✓ |
| CG25 | context_guard | 复核 12/13 低（D） | max_soft_line_tokens 写成「18万」：回退默认 180k，守卫照常工作 | 不适用 | ✓ |
| CG26 | context_guard | 复核 12/13 低（D） | handoff_threshold_tokens 写成「abc」、项目阈值写成 null：都回退默认，170k 仍按软线 160k 提醒 | 不适用 | ✓ |
| CG27 | context_guard | 复核 12/13 低（D） | 强提醒文案与 handoff skill 一致：写「最多 120 行」，不写「不限行数」 | ✗ | ✓ |
| CG28 | context_guard | Codex 11（D） | 状态栏与守卫在 3 个 cwd × 6 个上下文长度 × 封顶开/关 上判定一致（无 ⚠ ⇔ 不提醒，⚠ ⇔ 软，⚠⚠ ⇔ 强） | 不适用 | ✓ |
| SL01 | statusline | 正常 | 显示模型、ctx 123k、5h 40%、7d 50%，并把 ctx_tokens 写入 rate.json | ✓ | ✓ |
| SL02 | statusline | A1 | 本会话 payload 没有 rate_limits：不能把 rate.json 里别的会话写的有效周用量冲掉 | 不适用 | ✓ |
| SL03 | statusline | A1 | 同一 resets_at 下旧会话报 16%、rate.json 已有 84%：保留较大的 84% | 不适用 | ✓ |
| SL04 | statusline | 正常（⚠ 提示） | 普通项目 170k、软线 160k：状态栏显示 ⚠ | 不适用 | ✓ |
| SL05 | statusline | A5 / C4 | mode.json 是 ~ 会话写的（软线 160k），本会话在 quant-backtest（软线 250k）、200k：不显示 ⚠ | 不适用 | ✓ |
| SL06 | statusline | 新信息（prompt_cache） | 缓存已冷（warm=false）且 ctx 80k：显示 ❄ 和重写量；缓存热时不显示 | 不适用 | ✓ |
| SL14 | statusline | 复核N2 / F4 | 与守卫一致：封顶已生效 + quant-backtest + 宽裕档、185k：守卫写的软线 180k → 状态栏显示 ⚠（不是 ⚠⚠） | 不适用 | ✓ |
| SL07 | statusline | round3 🟡10（D） | payload 的 five_hour 已过期（1 小时前、95%）、rate.json 为空：不写入 95%，状态栏不显示 5h 95% | 不适用 | ✓ |
| SL08 | statusline | round3 🟡10（D） | payload 的 five_hour 已过期，rate.json 里有别的会话写的有效 5h 30%：保留 30% | 不适用 | ✓ |
| SL09 | statusline | 正常（D 不回归） | payload 的 five_hour 没有 resets_at（字段缺失）：照常显示和写入 | ✓ | ✓ |
| SL10 | statusline | round3 💭11（D） | 状态栏 cwd=Projects/quant-backtest-old、200k：按默认软线 160k 显示 ⚠（不套 quant-backtest 的 250k） | 不适用 | ✓ |
| SL11 | statusline | 正常（D 不回归） | 状态栏 cwd=Projects/quant-backtest、200k：按 250k 不显示 ⚠ | 不适用 | ✓ |
| SL12 | statusline | Codex 10（D） | rate.json 里周用量 20%（7 小时前的读数，本可判宽裕），新 payload 只带 5h 10%：周读数仍算过期，不判「宽裕」 | 不适用 | ✓ |
| SL13 | statusline | Codex 10（D） | 同一窗口、同样数值的重复旧 payload（周 20%）不刷新采样时间：7 小时后仍算过期，不判「宽裕」 | 不适用 | ✓ |
| SL15 | statusline | Codex 10（D 不回归） | 周读数从 20% 变成 21%（真有新数据）：采样时间刷新，按时间进度判「宽裕」 | 不适用 | ✓ |
| WC01 | weekly_compare | 正常 | 排除 -private-tmp- 基准会话：改动前只算 1 次请求 | ✓ | ✓ |
| WC02 | weekly_compare | 正常 | 无 5m/1h 拆分的旧记录：缓存写入按 1.25×，每次请求成本 = 3.75k 单位 | ✓ | ✓ |
| WC03 | weekly_compare | A8 | 1 小时缓存写入按 2×：每次请求成本 = 4.5k 单位 | ✗ | ✓ |
| WC04 | weekly_compare | A8 | 「最近」窗口从改动时刻（11-05 23:58 PST）开始：11-05 21:00 PST 的请求不算进最近 | ✗ | ✓ |
| WC05 | weekly_compare | N10 | 同一天运行两次：weekly_history.json 里当天只有 1 条 | ✗ | ✓ |
| WC06 | weekly_compare | N10 / B | 报告按模型分开给每次请求成本（含 Sonnet 一行） | ✗ | ✓ |
| WC07 | weekly_compare | N10 | 按本地日期分天：10-25 16:00 与 18:00 PDT（跨 UTC 零点）算 1 天 | ✗ | ✓ |
| WC08 | weekly_compare | round3 🟡18（D） | 现在 11-08 12:00 PST（UTC 11-08 20:00）；已有 11-07 17:00 PST（UTC 11-08 01:00）一条：本地是不同天，两条都留 | ✓ | ✓ |
| WC09 | weekly_compare | round3 🟡18（D） | 现在 11-08 18:00 PST（UTC 11-09 02:00）；已有同一本地日 11-08 14:00 PST（UTC 11-08 22:00）一条：只留最新 1 条 | ✗ | ✓ |
| LL01 | limit_logger | R4 D1 | StopFailure rate_limit：写 1 行 kind=limit_hit，带当时 5h 百分比，不输出 | 不适用 | ✓ |
| LL02 | limit_logger | R4 D1 | Notification quota_auto_resume_fired 记 1 行；idle_prompt 不记 | 不适用 | ✓ |
| LL03 | limit_logger | R4 F | 坏 JSON / 状态目录不可写：exit 0，不输出 | 不适用 | ✓ |
| LL04 | limit_logger | R4 F | 超过 5MB 轮转到 limit_events_old.jsonl（改名不删），新文件 1 行 | 不适用 | ✓ |

## 当前仍失败（0 个，第 3 轮待修清单）

（无）

## 不适用说明

- TF16：初版 v0 — v0 没有旧日志整理
- SR18：初版 v0 — 变异针对当前版代码
- SR11：初版 v0 — v0 没有对话摘要功能
- SR12：初版 v0 — v0 没有对话摘要/SessionEnd 功能
- SR13：初版 v0 — v0 没有对话摘要功能
- SR14：初版 v0 — v0 没有对话摘要功能
- SR17：初版 v0 — v0 没有 compact 重注入
- SR19：初版 v0 — v0 没有 compact 重注入
- SR22：初版 v0 — v0 没有对话摘要/SessionEnd 功能
- SR23：初版 v0 — v0 没有对话摘要/SessionEnd 功能
- SR24：初版 v0 — v0 没有对话摘要功能
- SR25：初版 v0 — v0 没有防注入
- SR26：初版 v0 — v0 没有 compact 重注入
- SR27：初版 v0 — v0 没有防注入
- CG04：初版 v0 — v0 的 config.json（pre-perproject）没有按项目阈值
- CG07：初版 v0 — v0 没有额度分档功能
- CG16：初版 v0 — v0 没有封顶
- CG10：初版 v0 — v0 没有读取 rate.json 的组件（无额度分档）
- CG11：初版 v0 — v0 没有读取 rate.json 的组件（无额度分档）
- CG12：初版 v0 — v0 的 config.json（pre-perproject）没有按项目阈值
- CG13：初版 v0 — v0 的 config.json（pre-perproject）没有按项目阈值
- CG14：初版 v0 — v0 没有读取 rate.json 的组件（无额度分档）
- CG15：初版 v0 — v0 没有读取 rate.json 的组件（无额度分档）
- CG18：初版 v0 — v0 没有 mode.json 的 mult 字段/原子写（无额度分档）
- CG19：初版 v0 — v0 没有会话去重
- CG20：初版 v0 — v0 没有会话去重
- CG21：初版 v0 — v0 没有会话去重
- CG23：初版 v0 — v0 没有封顶
- CG24：初版 v0 — v0 没有封顶
- CG25：初版 v0 — v0 没有封顶
- CG26：初版 v0 — v0 的 config.json（pre-perproject）没有按项目阈值
- CG28：初版 v0 — v0 没有封顶，状态栏与守卫的阈值来源各自独立（无一致性可言）
- SL02：初版 v0 — v0 没有读取 rate.json 的组件（无额度分档），覆盖无后果
- SL03：初版 v0 — v0 没有读取 rate.json 的组件（无额度分档），覆盖无后果
- SL04：初版 v0 — v0 状态栏没有 ⚠ 提示功能
- SL05：初版 v0 — v0 状态栏没有 ⚠ 提示功能
- SL06：初版 v0 — v0 状态栏没有缓存冷热提示
- SL14：初版 v0 — v0 没有封顶
- SL07：初版 v0 — v0 没有读取 rate.json 的组件（无额度分档）
- SL08：初版 v0 — v0 没有读取 rate.json 的组件（无额度分档）
- SL10：初版 v0 — v0 的 config.json（pre-perproject）没有按项目阈值
- SL11：初版 v0 — v0 的 config.json（pre-perproject）没有按项目阈值
- SL12：初版 v0 — v0 没有读取 rate.json 的组件（无额度分档）
- SL13：初版 v0 — v0 没有读取 rate.json 的组件（无额度分档）
- SL15：初版 v0 — v0 没有读取 rate.json 的组件（无额度分档）
- LL01：初版 v0 — v0 没有 limit_logger
- LL02：初版 v0 — v0 没有 limit_logger
- LL03：初版 v0 — v0 没有 limit_logger
- LL04：初版 v0 — v0 没有 limit_logger

## 隔离方式

每个场景一个临时目录（tempfile，结束自动删除）：假 HOME、`USAGE_SAVER_STATE` 指向临时目录、TZ=America/Los_Angeles；被测脚本复制到假 HOME 下再跑，statusline.sh 的 S、test_filter.py 的 LOGDIR 替换成临时目录，weekly_compare.py 的「现在」固定为 2026-11-08 20:00 UTC（结果不随运行日期变化）。测试命令用假的 pytest（临时 bin 目录，放在 PATH 最前），用 /bin/bash 执行。不碰真实的 ~/.claude、state/、testlogs/。

总耗时 15.7 秒。
