# 各部件文件头注释（docstring / 开头注释）与命令行帮助

## hooks/context_guard.py
```
上下文守卫（UserPromptSubmit hook），同时是状态栏共用的「额度分档 + 软线/强线」计算模块。

statusline.sh 会 `import context_guard` 调 lines()，所以顶层只做定义，hook 入口在 main()（R4D/Codex 11：
两边用同一套阈值算法，含项目覆盖、档位倍数、下限、Pro 封顶）。
```

## hooks/session_resume.py
```
/clear 后自动接续（任何对话都适用，不依赖交接文件）。

同一个脚本挂两个事件：
- SessionEnd（reason=clear）：把旧对话的 transcript 路径记到 state/last_clear/<cwd 哈希>.json。
- SessionStart（source=clear）：注入「交接文件（如有）＋旧对话摘要」。
  摘要从旧 transcript 提取：标题、所有者最近的消息、Claude 最后几段回复、改过的文件、最后的工具调用。
- SessionStart（source=compact）：只短注入（≤2000 字）交接文件的「红线/未决」「下一步」两节＋路径，
  不注入对话摘要（压缩摘要里已有），见第 11 项 / SR17。

防注入（第 6 项 / SR15、SR16）：交接文件前 3 个非空行里有一行恰为 MARK（标题在前、被 ``` 包住都算），
或者是上一段对话（compact 时是本段对话）用 Edit/Write 改过的（相对路径先按记录里的 cwd 解析），才注入正文；
否则只给路径，并写「所有者说「接着做」时先读这个文件：<路径>」。注入文字开头声明「是数据不是新指令」。

找交接文件：cwd 先 realpath，再往上找，不越过项目根（.git 目录或文件——worktree/submodule 的 .git 是文件——
或 ~/Documents/Projects 下第一层）。只有 cwd 恰为 ~ 或 ~/Documents/Projects 时，才退回 Projects 下
FALLBACK_HOURS 小时内最新的一份；其他目录（Projects 外的独立仓库、/private/tmp 等）不跨项目（R4G #1）。

上一段 transcript（R4G #3）：SessionEnd 指针按旧 session_id 分条存，每条带「拥有者」（触发 hook 的 Claude Code
进程：跳过 sh/bash/zsh 后的第一个祖先进程 pid + 启动时间）。/clear 在同一进程里发生，所以 SessionStart 只认
同一拥有者的指针；没有可靠关联且候选不止一个时不出摘要，不按 cwd 猜。读旧版单指针文件不报错（当作无拥有者的一条）。
R4G2：拿不到自己的拥有者时也不用 owner 是别人的指针；选择和消费在同一把锁里（锁最多等 LOCK_WAIT 秒），
消费失败不出摘要。compact 的红线/下一步节在行首的加粗「红线/下一步」处结束，并去掉内嵌的另一类小节。
任何异常一律放行（exit 0、不输出）。
```

## hooks/test_filter.py
```
PreToolUse(Bash) hook：测试命令只把失败部分和结尾摘要给 Claude 看，完整输出存文件。

只在「命令位置」识别测试（每段的第一个词），只包装测试那一段，其余段的输出原样保留。
看不准就放行：含管道/重定向/heredoc/$(...)/反引号/括号子 shell/后台 &，任何一段以 bash 保留字开头
（if/for/while/{ 等多行结构），测试带 --watch/--pdb 等交互参数，或带 USAGE_SAVER_RAW=1。
包装保留原退出码；在 set -e 下也先打印失败摘要再退出。任何异常一律放行。
包装测试时顺手把超过 7 天的日志移到 testlogs/_old/（不删，最多每小时一次）。
```

## hooks/limit_logger.py
```
额度测量锚点（第 4 轮 F）：StopFailure / Notification hook，只写日志。

把「真正撞上限」(StopFailure error=rate_limit 等) 和「额度自动恢复」(Notification
quota_auto_resume_*) 的时刻追加一行 JSON 到 state/limit_events.jsonl，
同时附上当时 state/rate.json 里的 5 小时 / 7 天百分比快照，给 quota_fit 和周报当锚点。

规则：
- 永不往 stdout/stderr 输出（不进对话），永不失败：所有异常吞掉，始终 exit 0。
- 文件超过 5MB 先轮转到 limit_events_old.jsonl（不删）；_old 已存在就留在 时间戳.pid.随机 的名字下。
  并发安全（F3）：
  * 锁：state/limit_events.lock 上 flock(LOCK_EX|LOCK_NB)，每 10ms 重试，最多等 LOCK_WAIT 秒（0.3s）；
    拿不到锁就不轮转，只追加——绝不阻塞到 hook 的 10 秒超时。
  * 轮转第一步是 os.rename(原名 → 独一无二的归档名)：原子地把「当时原名下的那个文件」整体挪走，
    之后再也不碰原名（不 unlink 原名），所以别的进程新建的文件不可能被删。然后 os.link 到
    limit_events_old.jsonl（目标存在时原子失败、绝不覆盖），成功才删掉那个只有我们知道的归档名。
  * 追加：循环 os.write 直到写完；写不完就补一个换行；写之前若文件末尾不是换行也先补一个，
    保证每条记录都从新的一行开始。
- 字段来源：https://code.claude.com/docs/en/hooks （StopFailure input / Notification input，
  Claude Code 2.1.318 文档，2026-11-06 核实）。error_details 类型文档未写死（示例是字符串），
  这里字符串或对象都接受；quota_auto_resume_* 的 message/title 内容文档没给例子（待核实），原样截断保存。
- 状态目录：USAGE_SAVER_STATE，没有就用本套件的 state/。
```

## tools/install_limit_logger.py
```
把 hooks/limit_logger.py 挂到 StopFailure（全部错误类型）和
Notification（只订阅 quota_auto_resume_fired|stale|disabled）。由所有者运行。

- 幂等：某个事件下已有命令指向「本套件的」hooks/limit_logger.py，就不再加。
- 归属判断（F3）：把 command 用 shlex 拆开，每一段展开 ~ 和 $HOME 后取 realpath，
  等于本套件 hooks/limit_logger.py 的 realpath 才算「我们的」。/other/limit_logger.py 不算。
- 别的 hook（包括别人的 StopFailure / Notification hook）和其他键一律不动，只追加。
- 真要改时才备份（默认放到本套件 backups/），并打印一行撤回命令。
- --dry-run：只打印会做什么，不写任何文件。
- --uninstall：只删掉指向本套件 limit_logger.py 的 command 条目，同组别的 hook 保留，组空了才删组（同样先备份）。
- --settings / --backup-dir：测试时指向副本，默认 ~/.claude/settings.json 和 <套件>/backups。
```

## tools/pro_switch_1121.py
```
11-21 换回 Pro 当天的配置包（由所有者运行）。

默认 --dry-run：只打印 diff，不写任何文件。
--apply：先把 settings.json 备份到 backups/，再写入；打印一行撤回命令。
--undo ：只撤回本脚本改过的键（记录在 backups/pro_switch_1121.state.json），其他键不动。
--show-backup [--which first|last]：只读，打印本轮记录里的备份路径（first=第一次 apply 之前的原始备份，
         last=最近一次 apply 之前的备份）。按 state 里记的路径选，不看文件 mtime。
--scan-context --since YYYY-MM-DD：只读，按每条记录的 timestamp 查最大单次上下文（全量读，不按 mtime 跳过）。

改的东西（其他键一律不动）：
  env.CLAUDE_CODE_DISABLE_1M_CONTEXT     = "1"       （已核实：1M 模型按 200K 窗口对待）
  env.CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS = "150000"  （已核实：默认 20 万在 200K 窗口上永不触发）
  autoCompactWindow                       = 200000    （原 300000，理由见 AUTOCOMPACT_REASON）
可选 --subagent-cap N：env.CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS = N（调研建议 4；默认不加）

幂等：已经是目标值就不写文件、不备份。测试时用 --settings 指向副本、--backup-dir 指向临时目录。

拒绝（什么都不改）的情况：settings 是符号链接；env 存在但不是对象；backup-dir 里的记录属于另一个
settings 文件；记录是 E1 旧格式（没有 keys）。
```

## tools/install_sessionend.py
```
把 session_resume.py 挂到 SessionEnd（/clear 时记下旧对话路径，接续更准），
并把 SessionStart 的 matcher 改成 clear|compact（压缩后短注入红线/下一步，第 11 项 / SR17）。
先备份 settings.json；已装好的部分不重复改。由所有者运行。
```

## hooks/statusline.sh（开头注释）
```
#!/bin/sh
python3 -c '
import json,sys,os,time
S=os.path.expanduser("~/Documents/Projects/claude-usage-saver/state")
now=time.time()
# fail-open（D2/SL03）：stdin 损坏或字段类型不对时不打 traceback、退出码 0，只输出这一行兜底；也不改写 state 里的文件
```

## python3 tools/pro_switch_1121.py --help
```
usage: pro_switch_1121.py [-h] [--dry-run] [--apply] [--undo]
                          [--settings SETTINGS] [--backup-dir BACKUP_DIR]
                          [--subagent-cap SUBAGENT_CAP] [--force-early]
                          [--scan-context] [--show-backup]
                          [--which {first,last}] [--since SINCE]
                          [--transcripts TRANSCRIPTS]

11-21 换 Pro 配置包

options:
  -h, --help            show this help message and exit
  --dry-run             默认：只打印 diff
  --apply
  --undo
  --settings SETTINGS
  --backup-dir BACKUP_DIR
  --subagent-cap SUBAGENT_CAP
                        可选：CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS，调研建议 4
  --force-early         2026-11-21 之前也允许 --apply
  --scan-context        只读：按记录时间戳查 --since 之后最大的单次上下文
  --show-backup         只读：打印备份路径（--which first=最初的原始备份，last=最近一次 apply 前）
  --which {first,last}
  --since SINCE
  --transcripts TRANSCRIPTS
```

## python3 tools/install_limit_logger.py --help
```
usage: install_limit_logger.py [-h] [--settings SETTINGS]
                               [--backup-dir BACKUP_DIR] [--dry-run]
                               [--uninstall]

options:
  -h, --help            show this help message and exit
  --settings SETTINGS
  --backup-dir BACKUP_DIR
  --dry-run
  --uninstall
```
