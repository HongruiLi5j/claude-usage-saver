# 回归评分表（2026-11-06 02:03）

由 `python3 tools/scorecard.py --version all` 生成，不要手改。场景定义在 `tools/scorecard.py`，来源是 `reports/round1_findings.md` 与 `reports/round2_review_AC.md`；「正常」= 本来就该通过的基本功能场景。

- 初版 v0：backups/ 里最早的各文件（test_filter.py.pre-hotfix、session_resume.py.pre-homefix、context_guard.py.pre-tiers、statusline.sh.pre-clearhint、weekly_compare.py.pre-cw、config.json.pre-perproject）
- 当前版：hooks/ 与 tools/ 里的文件、config.json

## 汇总

| 组件 | v0 通过/适用 | 当前 通过/适用 | 提升（百分点） | 两版都适用的场景：v0 → 当前 |
|---|---|---|---|---|
| test_filter | 4/15（27%） | 6/15（40%） | +13 | 4/15 → 6/15 |
| session_resume | 4/10（40%） | 6/14（43%） | +3 | 4/10 → 5/10 |
| context_guard | 5/7（71%） | 6/9（67%） | -5 | 5/7 → 4/7 |
| statusline | 1/1（100%） | 2/5（40%） | -60 | 1/1 → 1/1 |
| weekly_compare | 2/7（29%） | 4/7（57%） | +29 | 2/7 → 4/7 |
| **总计** | 16/40（40%） | 24/50（48%） | +8 | 16/40 → 20/40 |

**一句话**：通过率 v0 40% → 当前 48%，提升 +8 个百分点（相对提升 +20%）。只看两版都适用的 40 个场景：40% → 50%（+10 个百分点）。

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
| TF07 | test_filter | A3 | `grep pytest f.txt`（50 行匹配）：50 行全部可见 | ✗ | ✗ |
| TF08 | test_filter | A3 / C6 | `pip install pytest` 不包装 | ✗ | ✗ |
| TF09 | test_filter | A3 / A6 | `pytest; echo done`（pytest 紧跟分号）应被识别为测试并过滤 | ✗ | ✗ |
| TF10 | test_filter | A6 | `.venv/bin/pytest -q` 应被识别为测试并过滤 | ✗ | ✗ |
| TF11 | test_filter | A6 | `pytest -q; exit 3`：仍能看到失败信息，rc=3 | ✗ | ✗ |
| TF12 | test_filter | N6 | `set -e; pytest -q; echo after`：仍能看到失败信息，rc=1 | ✗ | ✗ |
| TF13 | test_filter | C6 | 5 帧的 traceback：所有帧在过滤后的输出里都可见 | ✗ | ✗ |
| TF14 | test_filter | N7 / C6 | `cat notes.txt && pytest -q`：非测试部分（30 行）的输出不被吃掉 | ✗ | ✗ |
| TF15 | test_filter | A3 | `which pytest` 不包装 | ✗ | ✗ |
| SR01 | session_resume | 正常 | 非 clear 启动（startup）不注入 | ✓ | ✓ |
| SR02 | session_resume | 正常 | 在有 .git 的项目根 /clear：注入本项目新鲜的 HANDOFF.md | ✓ | ✓ |
| SR03 | session_resume | 正常 | 20KB 交接 + 大段旧对话：注入总长 ≤ 10000 字符（官方上限） | ✓ | ✓ |
| SR04 | session_resume | A4 | cwd=Projects/c/pkg（c 有 .git、无交接）：不越过项目根去拿 Projects/HANDOFF.md | ✗ | ✓ |
| SR05 | session_resume | A4 | cwd=~/Documents/x：不往上爬拿 ~/HANDOFF.md 当「当前项目」（~ 单独算一个项目） | ✗ | ✗ |
| SR06 | session_resume | A4 | cwd=~ 且 ~/HANDOFF.md 是 10 分钟前写的：注入它，不改拿别的项目的交接 | ✓ | ✗ |
| SR07 | session_resume | A4 | cwd=~/Documents/Projects，Projects/HANDOFF.md 已 866 小时：不注入正文 | ✗ | ✗ |
| SR08 | session_resume | A4 / C2 | 项目内交接已 746 小时（31 天）：不整段注入正文（最多给路径和年龄） | ✗ | ✗ |
| SR09 | session_resume | C2 | 注入文字不劫持新问题：不写「无论写什么都视为接着做」，并说明明显是新问题时按新问题处理 | ✗ | ✓ |
| SR10 | session_resume | C3 | 9KB 交接、红线在最后一节：注入后「红线」一节仍在 | ✗ | ✗ |
| SR11 | session_resume | N2 | 没有 SessionEnd 指针、同目录另有一个 5 秒前还在写的并发会话：不把并发会话当成上一段对话 | 不适用 | ✗ |
| SR12 | session_resume | 正常（N2） | 有 SessionEnd 指针时：摘要取指针指向的上一段对话 | 不适用 | ✓ |
| SR13 | session_resume | N4 | 上一段 Claude 最后一段长回复（3000 字）结尾的「下一步」保留在摘要里 | 不适用 | ✗ |
| SR14 | session_resume | N4 | compact summary（isCompactSummary）不被当成「所有者最近的消息」 | 不适用 | ✗ |
| CG01 | context_guard | 正常 | 上下文 170k（普通项目、无额度数据）：注入软提醒 | ✓ | ✓ |
| CG02 | context_guard | 正常 | 上下文 50k：不提醒 | ✓ | ✓ |
| CG03 | context_guard | 正常 | 上下文 320k：注入强提醒（「上下文已很长」） | ✓ | ✓ |
| CG04 | context_guard | 正常 | quant-backtest 项目 200k：按项目软线 250k，不提醒 | 不适用 | ✓ |
| CG05 | context_guard | 正常 | rate.json 已 7 小时没更新：按正常档，200k 提醒 | ✓ | ✓ |
| CG06 | context_guard | A1 / N1 | rate.json 是新的但周重置时间已过去 2 天（用量 60%）：不能判成宽裕，200k 应提醒 | ✓ | ✗ |
| CG07 | context_guard | 正常（分档） | 周用量 20%、时间进度约 86%（宽裕）：软线 ×1.5=240k，200k 不提醒 | 不适用 | ✓ |
| CG08 | context_guard | A9 / C5 | 最后一行是 500KB 的 tool_result：仍能找到上一个 assistant 的 usage 并提醒（170k） | ✗ | ✗ |
| CG09 | context_guard | C8 | 强提醒不要求「在本次回答中」说明（避免打断正在做的工作） | ✗ | ✗ |
| SL01 | statusline | 正常 | 显示模型、ctx 123k、5h 40%、7d 50%，并把 ctx_tokens 写入 rate.json | ✓ | ✓ |
| SL02 | statusline | A1 | 本会话 payload 没有 rate_limits：不能把 rate.json 里别的会话写的有效周用量冲掉 | 不适用 | ✗ |
| SL03 | statusline | A1 | 同一 resets_at 下旧会话报 16%、rate.json 已有 84%：保留较大的 84% | 不适用 | ✗ |
| SL04 | statusline | 正常（⚠ 提示） | 普通项目 170k、软线 160k：状态栏显示 ⚠ | 不适用 | ✓ |
| SL05 | statusline | A5 / C4 | mode.json 是 ~ 会话写的（软线 160k），本会话在 quant-backtest（软线 250k）、200k：不显示 ⚠ | 不适用 | ✗ |
| WC01 | weekly_compare | 正常 | 排除 -private-tmp- 基准会话：改动前只算 1 次请求 | ✓ | ✓ |
| WC02 | weekly_compare | 正常 | 无 5m/1h 拆分的旧记录：缓存写入按 1.25×，每次请求成本 = 3.75k 单位 | ✓ | ✓ |
| WC03 | weekly_compare | A8 | 1 小时缓存写入按 2×：每次请求成本 = 4.5k 单位 | ✗ | ✓ |
| WC04 | weekly_compare | A8 | 「最近」窗口从改动时刻（11-05 23:58 PST）开始：11-05 21:00 PST 的请求不算进最近 | ✗ | ✗ |
| WC05 | weekly_compare | N10 | 同一天运行两次：weekly_history.json 里当天只有 1 条 | ✗ | ✗ |
| WC06 | weekly_compare | N10 / B | 报告按模型分开给每次请求成本（含 Sonnet 一行） | ✗ | ✓ |
| WC07 | weekly_compare | N10 | 按本地日期分天：10-25 16:00 与 18:00 PDT（跨 UTC 零点）算 1 天 | ✗ | ✗ |

## 当前仍失败（26 个，第 3 轮待修清单）

- **TF07**（A3）`grep pytest f.txt`（50 行匹配）：50 行全部可见
- **TF08**（A3 / C6）`pip install pytest` 不包装
- **TF09**（A3 / A6）`pytest; echo done`（pytest 紧跟分号）应被识别为测试并过滤
- **TF10**（A6）`.venv/bin/pytest -q` 应被识别为测试并过滤
- **TF11**（A6）`pytest -q; exit 3`：仍能看到失败信息，rc=3
- **TF12**（N6）`set -e; pytest -q; echo after`：仍能看到失败信息，rc=1
- **TF13**（C6）5 帧的 traceback：所有帧在过滤后的输出里都可见
- **TF14**（N7 / C6）`cat notes.txt && pytest -q`：非测试部分（30 行）的输出不被吃掉
- **TF15**（A3）`which pytest` 不包装
- **SR05**（A4）cwd=~/Documents/x：不往上爬拿 ~/HANDOFF.md 当「当前项目」（~ 单独算一个项目）
- **SR06**（A4）cwd=~ 且 ~/HANDOFF.md 是 10 分钟前写的：注入它，不改拿别的项目的交接　**← 回归：v0 通过**
- **SR07**（A4）cwd=~/Documents/Projects，Projects/HANDOFF.md 已 866 小时：不注入正文
- **SR08**（A4 / C2）项目内交接已 746 小时（31 天）：不整段注入正文（最多给路径和年龄）
- **SR10**（C3）9KB 交接、红线在最后一节：注入后「红线」一节仍在
- **SR11**（N2）没有 SessionEnd 指针、同目录另有一个 5 秒前还在写的并发会话：不把并发会话当成上一段对话
- **SR13**（N4）上一段 Claude 最后一段长回复（3000 字）结尾的「下一步」保留在摘要里
- **SR14**（N4）compact summary（isCompactSummary）不被当成「所有者最近的消息」
- **CG06**（A1 / N1）rate.json 是新的但周重置时间已过去 2 天（用量 60%）：不能判成宽裕，200k 应提醒　**← 回归：v0 通过**
- **CG08**（A9 / C5）最后一行是 500KB 的 tool_result：仍能找到上一个 assistant 的 usage 并提醒（170k）
- **CG09**（C8）强提醒不要求「在本次回答中」说明（避免打断正在做的工作）
- **SL02**（A1）本会话 payload 没有 rate_limits：不能把 rate.json 里别的会话写的有效周用量冲掉
- **SL03**（A1）同一 resets_at 下旧会话报 16%、rate.json 已有 84%：保留较大的 84%
- **SL05**（A5 / C4）mode.json 是 ~ 会话写的（软线 160k），本会话在 quant-backtest（软线 250k）、200k：不显示 ⚠
- **WC04**（A8）「最近」窗口从改动时刻（11-05 23:58 PST）开始：11-05 21:00 PST 的请求不算进最近
- **WC05**（N10）同一天运行两次：weekly_history.json 里当天只有 1 条
- **WC07**（N10）按本地日期分天：10-25 16:00 与 18:00 PDT（跨 UTC 零点）算 1 天

## 不适用说明

- SR11：初版 v0 — v0 没有对话摘要功能
- SR12：初版 v0 — v0 没有对话摘要/SessionEnd 功能
- SR13：初版 v0 — v0 没有对话摘要功能
- SR14：初版 v0 — v0 没有对话摘要功能
- CG04：初版 v0 — v0 的 config.json（pre-perproject）没有按项目阈值
- CG07：初版 v0 — v0 没有额度分档功能
- SL02：初版 v0 — v0 没有读取 rate.json 的组件（无额度分档），覆盖无后果
- SL03：初版 v0 — v0 没有读取 rate.json 的组件（无额度分档），覆盖无后果
- SL04：初版 v0 — v0 状态栏没有 ⚠ 提示功能
- SL05：初版 v0 — v0 状态栏没有 ⚠ 提示功能

## 隔离方式

每个场景一个临时目录（tempfile，结束自动删除）：假 HOME、`USAGE_SAVER_STATE` 指向临时目录、TZ=America/Los_Angeles；被测脚本复制到假 HOME 下再跑，statusline.sh 的 S、test_filter.py 的 LOGDIR 替换成临时目录，weekly_compare.py 的「现在」固定为 2026-11-08 20:00 UTC（结果不随运行日期变化）。测试命令用假的 pytest（临时 bin 目录，放在 PATH 最前），用 /bin/bash 执行。不碰真实的 ~/.claude、state/、testlogs/。

总耗时 3.3 秒。
