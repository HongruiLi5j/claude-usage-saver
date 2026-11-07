# 11-21 换回 Pro 当天清单

按顺序做。每步一行命令，可以直接复制到 zsh（命令里没有 `#` 注释）。带"（会话里）"的是在 claude 会话里输入的斜杠命令。
依据：reports/round4_research.md 的 A1–A5、B4。标【待核实】的步骤是去确认调研里还没确认的事。

## 0. 先预览（不写任何文件）
1. 看配置包会改什么：
   `python3 ~/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py`

## 1. 账号（会话里）
2. 退出登录：`/logout`
3. 重新登录（本地 token 是登录时的套餐，不重登不生效）：`/login`
4. 看套餐是不是 Pro：`/status`
5. 看额度条：`/usage`
6. 也可以在终端看登录状态：
   `claude auth status`

## 2. 写配置（终端）
7. 写入 settings.json（自动备份到 backups/，会打印撤回命令，记下来）：
   `python3 ~/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py --apply`
8. 确认写进去了：
   `python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/.claude/settings.json')));print(d.get('env'),d.get('autoCompactWindow'))"`
9. 退出所有正在跑的 claude 会话，再开新会话（env 只在新会话生效）。

## 3. 新会话里验证
10. （会话里）状态栏的上下文百分比应当按 200K 算；也可以看：`/context`
11. （会话里）【待核实】Opus 能不能用：`/model opus`；如果报 `Claude Opus is not available with the Claude Pro plan`，改用：`/model sonnet`（在会话开头或 /clear 之后切，避免整段重读）
12. （会话里）看 skill 列表有没有被截短（200K 下预算约 2000 token），重点看 handoff、model-router 的描述还在不在：`/doctor`
13. 守卫封顶：先确认今天日期已到 cap_from_date：
    `python3 -c "import json;c=json.load(open('/Users/lihongrui/Documents/Projects/claude-usage-saver/config.json'));print(c.get('cap_from_date'),c.get('max_soft_line_tokens'),c.get('max_strong_line_tokens'))"`
14. 看守卫当前额度档：
    `cd ~/Documents/Projects/claude-usage-saver; python3 hooks/context_guard.py --mode`
15. 在新会话里发一条消息后，看守卫写下的线（soft_line 应 ≤180000，strong_line 应 ≤195000）：
    `cat ~/Documents/Projects/claude-usage-saver/state/mode.json`
16. 看 Claude Code 版本（空闲压缩阈值需要 ≥2.1.290）：
    `claude --version`

## 4. henchmen【待核实：settings.json 的 env 块能否传到 henchmen agent】
17. 开一个 henchmen 房间，让其中一个 agent 运行下面这条命令并原样回报输出（有两行 CLAUDE_CODE_ 就说明传到了）：
    `env | grep -E "CLAUDE_CODE_(DISABLE_1M_CONTEXT|IDLE_COMPACT_MIN_TOKENS)"`
18. 跑过一段 henchmen 任务后，按每条记录的时间戳（不是文件修改时间）查 11-21 以后最大的单次上下文；超过 200000 说明某条路径上 200K 上限没生效，输出会给出文件名：
    `python3 ~/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py --scan-context --since 2026-11-21`

## 5. 其他待核实（看到就记下来）
19. 【待核实】Fable 在 Pro 上是否走 usage credits：（会话里）`/usage`，用一次 Fable 子 agent 前后各看一次
20. 【待核实】Pro 上是否出现按模型族的限额（`You've hit your Opus limit`）：撞到时先交接再 `/clear`，再 `/model sonnet`

## 撤回
21. 只撤本脚本改的键（其他键不动）：
    `python3 ~/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py --undo`
22. 整文件还原到第一次 --apply 之前的原始状态（会丢掉之后的所有其他改动；只在第 21 步不行时用）。路径由脚本从记录文件里读出，不按文件时间猜；找不到记录时什么都不复制：
    `p=$(python3 ~/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py --show-backup) && cp "$p" ~/.claude/settings.json`
    - "最初那份"（默认，`--which first`）：当天第一次 --apply 之前的 settings，也就是还没被本脚本改过的原始文件。
    - "最近一次"（`--which last`）：最后一次 --apply 之前的 settings。只 apply 过一次时两者相同；如果当天 apply 过两次（比如第二次加了 `--subagent-cap 4`），"最近一次"只撤掉第二次，第一次的改动还在。
    - 只想撤掉最后一次 apply：`p=$(python3 ~/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py --show-backup --which last) && cp "$p" ~/.claude/settings.json`
