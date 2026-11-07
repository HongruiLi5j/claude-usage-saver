# 已确认的公开接口（第 4 轮盲测分诊时与实施方确认；可以直接当契约用）

## 安装布局
- 生产环境：套件在 `$HOME/Documents/Projects/claude-usage-saver/`，hooks 在其 `hooks/`，配置 `config.json` 在套件根目录。
- `context_guard.py`、`test_filter.py`、`statusline.sh` 按 `$HOME/Documents/Projects/claude-usage-saver` 定位套件（config、state、testlogs）。
  `session_resume.py`、`limit_logger.py`、`tools/*.py` 按脚本自身所在位置定位套件。
  `context_guard.py`、`session_resume.py`、`limit_logger.py` 认 `USAGE_SAVER_STATE`；`statusline.sh`、`test_filter.py` 不认。
  因此：**测试应把套件复制到 `<临时HOME>/Documents/Projects/claude-usage-saver/`，并运行复制出来的那份**，
  这样所有定位方式都落在同一个临时目录里，和生产布局一致。默认**不要**设 `USAGE_SAVER_STATE`
  （生产环境也没设；设了以后 statusline 写 rate.json 和守卫读 rate.json 不在同一个目录，这是已知问题，不要再测）。
  个别专门测 `USAGE_SAVER_STATE` 覆盖的场景可以设。
- state 目录 = `<套件>/state/`。test_filter 完整日志在 `<套件>/state/testlogs/`。

## state/rate.json（statusline.sh 写，context_guard 读）
```json
{"ts": 1762400000,
 "rate_limits": {
   "five_hour": {"used_percentage": 46, "resets_at": 1762414400, "sampled_at": 1762400000},
   "seven_day": {"used_percentage": 84, "resets_at": 1762700000, "sampled_at": 1762400000}}}
```
`used_percentage` 是 0–100 的百分数，`resets_at`、`sampled_at`、`ts` 是 Unix 秒。

## 交接文件标记 MARK
字面值：`<!-- usage-saver-handoff -->`（handoff skill 写在交接文件第一行）。

## /clear 的事件序列
真实 /clear：旧会话先收到 `SessionEnd`（`reason=clear`，带旧 session_id 和旧 transcript_path），
然后新会话收到 `SessionStart`（`source=clear`，**新的** session_id 和**新的** transcript_path）。
SessionEnd 和 SessionStart 由**同一个 Claude Code 进程**触发（hook 的祖先进程相同）。
compact：`SessionStart`（`source=compact`），session_id 和 transcript_path **不变**。

## 已知、等所有者决定的问题（不要再出依赖它们的题）
- 读数很旧、但窗口还没重置时的高用量是否还算“快用完”（没有公开 TTL）。
- 同一进程、cwd 换到另一个项目后 /clear，摘要要不要带过去。
- HOME 换掉后读不到 config 的行为（复制套件到临时 HOME 就不会碰到）。

## Claude Code hook 输入（官方文档，所有事件都有 session_id、transcript_path、cwd、hook_event_name）
- UserPromptSubmit：`prompt`。输出：stdout 文本或 `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":...}}` 会进对话；无输出 = 不提醒。
- SessionStart：`source`（startup/resume/clear/compact）。输出同上（additionalContext）。
- SessionEnd：`reason`（clear/logout/prompt_input_exit/other）。
- PreToolUse：`tool_name`、`tool_input`（Bash 时 `{"command": ...}`）。改写命令用
  `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","updatedInput":{"command":...}}}`；无输出 = 原样放行。
- StopFailure：`error`（如 `rate_limit`、`overloaded`、`server_error`…）、`error_details`（字符串，可能是对象）、`last_assistant_message`。
- Notification：`notification_type`（如 `quota_auto_resume_fired`、`quota_auto_resume_stale`、`quota_auto_resume_disabled`、`idle_prompt`、`permission_prompt`）、`title`、`message`。
- statusLine（stdin JSON）：`model.display_name`、`workspace.current_dir`、`cwd`、`session_id`、`transcript_path`、
  `context_window.current_usage.{input_tokens,cache_creation_input_tokens,cache_read_input_tokens,output_tokens}`、
  `rate_limits.five_hour/seven_day.{used_percentage,resets_at}`（resets_at 为 Unix 秒）、可选 `prompt_cache`。

## transcript（JSONL）
每行一个对象，顶层 `type`（user/assistant）、`cwd`、`sessionId`、`timestamp`（ISO 8601，例如 `2026-11-06T10:00:00.000Z`）、
可选 `isSidechain`；`message` 内含 `role`、`content`（字符串或块列表：text / tool_use{name,input} / tool_result）、
assistant 消息有 `usage`（input_tokens、cache_read_input_tokens、cache_creation_input_tokens、output_tokens）。
一次请求的上下文 = input_tokens + cache_read_input_tokens + cache_creation_input_tokens。
`~/.claude/projects/<项目名>/<session>.jsonl` 是 Claude Code 存 transcript 的位置。
