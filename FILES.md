# 文件地图与权限（给任何接手的 AI：Claude / ChatGPT 等）

目标：在不牺牲质量的前提下，降低 Claude Code 订阅额度消耗，并按剩余额度自适应。先读 HANDOFF.md 了解背景和进度，本文件说明每个文件做什么、能不能动。
说明：ChatGPT 读不到本机磁盘，需要所有者把文件内容粘贴过去；改完后由所有者或 Claude 写回。

## 权限图例
- 🟢 可改：普通脚本/文档，改前备份
- 🟡 谨慎：被系统实际使用，改坏会影响 Claude Code 运行，改前必须备份并测试
- 🔴 不要碰：数据/备份/自动生成，手改会破坏结果的可比性

## 文件
| 路径 | 作用 | 权限 |
|---|---|---|
| HANDOFF.md | 总交接：证据、决定、进度、待办 | 🟢 只追加，不重写 |
| FILES.md | 本文件 | 🟢 |
| config.json | 守卫阈值（默认 16 万；强提醒 30 万；quant-backtest/AQ-sandbox/AQ-o44 为 25 万） | 🟡 改数值可以，保持 JSON 合法；`tools/auto_optimize.py --apply` 会自动改其中的默认阈值 |
| hooks/context_guard.py | UserPromptSubmit hook：上下文超线时提醒写交接；按剩余额度分四档调整软线（宽裕×1.5/正常×1/紧张×0.75/快用完×0.6，下限 10 万）；`--mode` 查看当前档 | 🟡 由 `~/.claude/settings.json` 调用，语法错误会让每条消息都出错；改后用 `python3 -m py_compile` 检查 |
| hooks/statusline.sh | 状态栏：显示模型、上下文、5 小时和周用量，并写 `state/rate.json` | 🟡 同上，由 settings.json 调用 |
| tools/usage_report.py | 按模型/项目/日/会话统计 token | 🟢 只读脚本 |
| tools/freeze_data.py | 把会话记录冻结成 `state/frozen.json` | 🟡 重跑会改变冻结数据，使各轮结果不可比；除非有意换基准，不要运行 |
| tools/replay_guard.py | 在冻结数据上回放守卫策略，算模拟节省 | 🟢 可改策略逻辑；改后必须记录到报告 |
| tools/auto_optimize.py | 扫描阈值、选最优、写 `reports/`；`--apply` 才写 config.json | 🟢 |
| reports/latest.md | 最新优化报告 | 🔴 自动生成 |
| reports/history.json | 每轮优化记录，用于对比 | 🔴 只追加，不手改 |
| state/frozen.json | 冻结的 473 个会话（10-21..11-05），保证每轮上下文一致 | 🔴 不要改、不要重新生成 |
| state/rate.json | 实时额度（状态栏每次刷新写入） | 🔴 自动生成 |
| state/mode.json | 当前额度档（宽裕/正常/紧张/快用完）与软硬线，守卫每条消息写入，model-router 读取 | 🔴 自动生成 |
| state/last_status_sample.json | 第一次收到的状态栏完整 JSON 样本 | 🔴 参考用 |
| backups/ | 改动前的备份（`~/.claude/settings.json`、config.json） | 🔴 永远保留，用于撤回 |

## 本项目之外、但被它使用的文件
| 路径 | 作用 | 权限 |
|---|---|---|
| ~/.claude/settings.json | 接入 statusLine、UserPromptSubmit hook、关提示建议、默认 Sonnet | 🟡 改前备份到 backups/ |
| ~/.claude/skills/handoff/SKILL.md | 写交接 / "接着做" | 🟢 |
| ~/.claude/skills/model-router/SKILL.md | 按任务和剩余额度分派模型；最多 2 个并行 Opus agent | 🟢 |

## 硬规则（所有者要求）
- 质量优先，不为省额度降低复杂任务的模型或推理强度；不改 CLAUDE.md 中"回复要详细"。
- 绝不删除文件；要清理的移到 `~/Documents/Projects/_to_delete/`，由所有者自己决定。
- 不读 `.secret`；不 push；每个改动先备份并给撤回命令。
- 给所有者的命令不带 `#` 注释，单行用 `;`。

## 已确认的事实（2026-11-06）
- 状态栏 JSON 的 `rate_limits.five_hour` / `seven_day` 含 `used_percentage` 和 `resets_at`（Unix 秒），可据此判断"用量是否领先或落后于时间进度"。
- 当天实测：周用量 79%，距重置约 17 小时，低于时间进度（约 90%）→ 按"宽裕"档处理，不要过度节省。
