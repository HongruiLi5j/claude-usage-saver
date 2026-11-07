# 第 5 轮独立文档盲测

交付 `heldout_r5.py`，共 **65 题**：LL 9、IL 6、PS 9、SR 14、CG 5、SL 7、TF 15。仅依据本目录 `docs/` 的公开材料出题，已对照 `old_heldout_codex_fixed.py` 的 60 种输入；不读取、导入实现，不复用其测试代码。新题围绕新增部件、变异输入、多步组合、并发和坏状态。

本次仅执行 `python3 -m py_compile heldout_r5.py`，语法检查通过；没有运行测试脚本或任何 hook。PASS/FAIL 需在套件仓库运行后才能获得。失败表示该场景观察与预期不同，需结合文档和推断项分诊。

## 运行

将两个交付文件放到仓库的 `tools/heldout/`，从仓库根执行：

```sh
python3 tools/heldout/heldout_r5.py
HELDOUT_ONLY=SR,CG python3 tools/heldout/heldout_r5.py
HELDOUT_ONLY=PS03,LL06 python3 tools/heldout/heldout_r5.py
HELDOUT_SKIP=TF python3 tools/heldout/heldout_r5.py
HELDOUT_SUITE=/absolute/path/to/claude-usage-saver python3 tools/heldout/heldout_r5.py
```

只依赖 Python 3 标准库，以及套件自身所需的 `python3`、`/bin/sh`、`/bin/bash`。默认来源目录严格使用 `Path(os.environ.get("HELDOUT_SUITE") or Path(__file__).resolve().parents[2])`。ONLY/SKIP 以逗号分隔，按编号前缀匹配；SKIP 优先。未选题不计入总数。每道选中题打印一行 `PASS/FAIL 编号 场景 — 原因`，最后打印 `总数 N | PASS x | FAIL y`；存在失败退出 1。源文件缺失在对应题原因中标记 `RUNNER FAIL`，仍继续其他题。错误原因压缩到 300 字以内。

## 隔离方式

每题使用独立 `tempfile.mkdtemp` 并 `os.path.realpath`，创建临时 HOME。复制全部 `hooks/*.py`、`hooks/statusline.sh`、三个指定工具和 `config.json` 到 `<临时HOME>/Documents/Projects/claude-usage-saver/`，只运行这些副本。必须的源文件缺失记 RUNNER FAIL。默认 state 在副本内；仅 LL09 显式覆盖 `USAGE_SAVER_STATE`。

所有子进程清除继承的 `USAGE_SAVER_*`、`CLAUDE_*`，设置临时 HOME、`PYTHONDONTWRITEBYTECODE=1`、`BASH_ENV=/dev/null`、`ENV=/dev/null`。所有安装器/Pro 工具调用显式携带临时 `--settings` 与 `--backup-dir`；scan 显式携带临时 `--transcripts`。SR10–12 用独立 Python 父进程生成异属指针，模拟文档中的不同拥有者；不伪造未公开 owner schema。SR09 只检查生成的指针是否保留两个会话，不要求 v2 内部键布局。

LL06/SR12 以 `subprocess.Popen` 同时启动消费者，全部结束后才作业务断言；发生异常时杀掉并回收剩余进程。每题共享 29 秒截止时间，单次调用最长 8 秒。结束时删除整个临时根。

全部 test_filter 结果（包括放行结果）过 `bash -n`。仅 TF12–15 执行固定 payload，PATH 前置临时假 pytest/npm/node；假命令只打印标记并按 `--fail-r5` 决定退出 7 或 0。payload 的其余命令只有 printf。其他复杂 shell 输入仅检查语法，不执行。脚本不执行 git、rm 或网络命令。

不伪造系统时间。PS apply 显式加 `--force-early`；CG 软线测试使用 170k、强线用 310k，在封顶日期两侧预期均成立；CG04 用有效的高额度读数稳定到 100k 软线。rate 的 reset 基于运行时当前时间计算，不依赖某个日历日。

## 契约边界与推断

标“推断”的题号：**SR11、SR12、SR13、CG03、TF05、TF07、TF08、TF13、TF14**。

- SR11/12：从“选择和消费在同一把锁里”推导同一指针至多消费一次，并结合同 owner 的有效指针推导至少消费一次；加入异属候选避免依赖含糊的 transcript 回退。
- SR13：旧格式的具体字段没有公开，使用最小单指针对象 `session_id/transcript_path/cwd/ts`；仅断言不报错，不断言恢复摘要。此题只能验证该兼容性输入，不证明所有旧格式都兼容。
- CG03：“每10条”推断为首次提醒后的第10次 UserPromptSubmit，再次提醒；若公开接口另有计数定义，应先分诊边界含义。
- TF05/07/08：按“每段的第一个词”及公开测试命令列表推断不穿透 env、exec、bash -c 包装器。
- TF13/14：复杂词法未明示必须改写或必须放行，因此只断言语义和退出码，不要求一定改写。

scan 的文本格式未公开：PS01/02 接受完整十进制数字（可含逗号、下划线）或一位小数 k 形式；如果工具采用其他合法展示形式，这两题应作为输出适配问题分诊，不能直接认定 scan 算法有错。show-backup 检查其输出所指文件内容，不依赖备份文件命名或 mtime。

状态栏文档只提“这一行兜底”，未给字面值。因此 SL01–03 只检查非空单行、无 traceback、exit 0、stderr 为空和 state 全树不变，不猜具体措辞。日志 schema 未公开，因此 LL 以逐行可解析 JSON、唯一内容标记和百分比观察业务性质，不断言内部字段名称。只读/拒绝/幂等比较文件字节与目录清单，不要求访问时间、mtime 不变。

## 逐题依据

| 编号 | 场景 | 预期 | 预期依据 |
|---|---|---|---|
| LL01 | 对象 error_details 含 Unicode | 有效 JSON 保留对象中的唯一文本且无输出 | docs/headers.md："字符串或对象都接受"；"永不往 stdout/stderr 输出"。 |
| LL02 | statusline 写 rate 后记录撞限 | 日志含 97% 和 83% 快照 | docs/headers.md："同时附上当时 state/rate.json 里的 5 小时 / 7 天百分比快照"；docs/contract.md："statusline.sh 写，context_guard 读"及 rate schema。 |
| LL03 | fired/stale/disabled 三类通知 | 三条 JSON 事件且安静 | docs/headers.md："Notification quota_auto_resume_*"；"永不往 stdout/stderr 输出"。 |
| LL04 | 事件文件末尾只有残缺 JSON | 新记录另起一行且可解析 | docs/headers.md："写之前若文件末尾不是换行也先补一个"。 |
| LL05 | 超 5MiB，已有 old 归档 | 旧 old 不变，原主文件另存且追加成功 | docs/headers.md："文件超过 5MB 先轮转"；"_old 已存在就留在 时间戳.pid.随机 的名字下"。 |
| LL06 | 12 进程同时竞争超限文件 | 所有新事件各一条、跨归档无丢失且 JSON 有效 | docs/headers.md："并发安全"；"别的进程新建的文件不可能被删"；"循环 os.write 直到写完"。 |
| LL07 | rate.json 是目录 | 无输出、exit 0 | docs/headers.md："所有异常吞掉，始终 exit 0"。 |
| LL08 | 顶层数组输入 | 无输出、exit 0 | docs/headers.md："永不往 stdout/stderr 输出…所有异常吞掉"。 |
| LL09 | 显式 state 覆盖 | 覆盖目录有日志，默认目录无日志 | docs/headers.md："状态目录：USAGE_SAVER_STATE，没有就用本套件的 state/"。 |
| IL01 | 外来同名 hook 共存时安装两次 | 第二次 settings/备份不变 | docs/headers.md："幂等…就不再加"；"真要改时才备份"。 |
| IL02 | dry-run 尚未安装 | 全临时根目录清单和字节不变 | docs/headers.md："--dry-run：只打印会做什么，不写任何文件"。 |
| IL03 | 外来 StopFailure 与 idle 通知 | 原组与其他设置保留、两事件新增自身 hook | docs/headers.md："/other/limit_logger.py 不算"；"别的 hook…和其他键一律不动，只追加"。 |
| IL04 | 两事件已有带引号 $HOME 路径 | 不重复、不备份 | docs/headers.md："command 用 shlex 拆开…展开 ~ 和 $HOME 后取 realpath"。 |
| IL05 | 自有与外来命令在同组 | 卸载仅删自有，混组继续保留 | docs/headers.md："同组别的 hook 保留，组空了才删组"。 |
| IL06 | 安装再卸载 | 备份内仍有首次安装前完整 settings | docs/headers.md："真要改时才备份"；"--uninstall…同样先备份"。 |
| PS01 | 文件 mtime 为 epoch、记录在 since 后 | 仍统计 321987，且只读 | docs/headers.md："按每条记录的 timestamp 查最大单次上下文（全量读，不按 mtime 跳过）"。 |
| PS02 | 同文件跨 since，output 极大 | 只统计 since 后的 234567 上下文 | docs/headers.md："按每条记录的 timestamp"；docs/contract.md："一次请求的上下文 = input_tokens + cache_read_input_tokens + cache_creation_input_tokens"。 |
| PS03 | apply 后再变更 subagent cap | first 为原始、last 为最近 apply 前内容 | docs/headers.md："first=第一次 apply 之前的原始备份，last=最近一次 apply 之前的备份"。 |
| PS04 | 修改全部备份的 mtime 再 show | first/last 不变，所有文件不变 | docs/headers.md："按 state 里记的路径选，不看文件 mtime"；"只读"。 |
| PS05 | 相同目标 apply 两次 | 不写、不备份、不推进 last | docs/headers.md："已经是目标值就不写文件、不备份"。 |
| PS06 | apply 后改无关 env/顶层键再 undo | 还原自有键，保留后来无关修改 | docs/headers.md："只撤回本脚本改过的键…其他键不动"。 |
| PS07 | env 为数组 | 非零拒绝，所有文件不变 | docs/headers.md："拒绝（什么都不改）…env 存在但不是对象"。 |
| PS08 | 用相同备份目录操作另一 settings | 非零拒绝，所有文件不变 | docs/headers.md："backup-dir 里的记录属于另一个 settings 文件"。 |
| PS09 | 移除真实记录 keys 后 undo | 非零拒绝，所有文件不变 | docs/headers.md："记录是 E1 旧格式（没有 keys）"。 |
| SR01 | MARK 在第三非空行、代码围栏中 | compact 信任并提取下一步 | docs/headers.md："前 3 个非空行…标题在前、被 ``` 包住都算"。 |
| SR02 | MARK 在第四非空行且无 Edit | 只给路径，不给正文 | docs/headers.md："前 3 个非空行里有一行恰为 MARK…才注入正文"。 |
| SR03 | 加粗列表红线/下一步后跟已完成 | 提取两类目标内容，排除已完成 | docs/headers.md："在行首的加粗「红线/下一步」处结束"；docs/improvements.md 第20项："compact 注入修加粗列表分段"。 |
| SR04 | 两类小节重复出现 | 各类只保留第一节 | docs/improvements.md 第20项："红线/下一步各取第一节"。 |
| SR05 | 两节都有巨量 Unicode | 解码后≤2000字，明确截断 | docs/headers.md："≤2000 字"；docs/improvements.md 第20项："截断标注"。 |
| SR06 | 相对 Edit 路径，无 MARK | 正文受信、声明数据/指令边界 | docs/headers.md："相对路径先按记录里的 cwd 解析"；"注入文字开头声明「是数据不是新指令」"。 |
| SR07 | MARK 行有额外前缀 | 不信任正文，提示接着做时读路径 | docs/headers.md："一行恰为 MARK"；"所有者说「接着做」时先读这个文件"。 |
| SR08 | 子仓库 .git 为文件 | 不越界取父仓库交接正文 | docs/headers.md："不越过项目根（.git 目录或文件…）"。 |
| SR09 | 同 cwd 两个旧 session 各自结束 | 两个 session_id 同时存于指针文件 | docs/headers.md："指针按旧 session_id 分条存"。 |
| SR10 | 两个异属 owner 指针 | 不注入异属摘要 | docs/headers.md："SessionStart 只认同一拥有者的指针"；"不按 cwd 猜"。 |
| SR11 | 本属旧指针和较新异属指针 | 首次选本属摘要、第二次不重放 | **推断**；docs/headers.md："只认同一拥有者"；"选择和消费在同一把锁里"；以消费表示一次性使用。 |
| SR12 | 两个并发 Start 消费一个本属指针 | 恰好一个注入该摘要 | **推断**；docs/headers.md："选择和消费在同一把锁里…消费失败不出摘要"；有效本属指针应可被一方成功消费。 |
| SR13 | 无 owner 最小旧单指针 | 无异常、exit 0 | **推断（夹具格式）**；docs/headers.md："读旧版单指针文件不报错（当作无拥有者的一条）"；仅验证最小对象兼容，不要求摘要。 |
| SR14 | 截断指针 JSON，存在两份 transcript | 不报错、不猜任一摘要 | docs/headers.md："没有可靠关联且候选不止一个时不出摘要"；"任何异常一律放行"。 |
| CG01 | 同 session 软线两次 | 首次提醒、第二次静默 | docs/improvements.md 第19项："首次软/强各一次，之后每 5 万 token 或 10 条"。 |
| CG02 | 首次软线后首次强线再重复 | 强线单独提醒一次、随后静默 | docs/improvements.md 第19项："首次软/强各一次"。 |
| CG03 | 首次提醒后十次同 token 提交 | 前九次静默、第十次提醒 | **推断（计数边界）**；docs/improvements.md 第19项："之后每 5 万 token 或 10 条"。 |
| CG04 | statusline 高额度数据后增加 token | 增49999静默、增50000提醒 | docs/improvements.md 第19项："之后每 5 万 token 或 10 条"；docs/使用说明.md："周或 5 小时用量 ≥90%…10万"。 |
| CG05 | session A/B/A 同 cwd 交错 | A、B 各首次提醒，A 再次静默 | docs/improvements.md 第19项："超线提醒按 session 去重"。 |
| SL01 | 顶层数组且已有 state | 单行兜底、state 全树不变 | docs/headers.md："stdin 损坏或字段类型不对…只输出这一行兜底；也不改写 state 里的文件"。 |
| SL02 | current_usage 是列表 | 单行兜底、state 不变 | docs/headers.md："字段类型不对…只输出这一行兜底…不改写 state"。 |
| SL03 | input_tokens 是对象 | 单行兜底、state 不变 | docs/headers.md："字段类型不对时不打 traceback、退出码 0…不改写 state"。 |
| SL04 | 已有 rate 后收到无 rate payload | rate 字节及 ts 不变 | docs/improvements.md 第10项："本会话没有额度数据时保留旧读数"；"没有新数据时不刷新 ts"。 |
| SL05 | 同 reset 窗口较低新读数 | 两窗口保留较大值 | docs/improvements.md 第10项："同一重置窗口取较大的用量"。 |
| SL06 | 仅更新 five_hour | seven_day 读数及 sampled_at 原样保留 | docs/improvements.md 第10项："合并写 rate.json"；第19项："每窗口独立 sampled_at"。 |
| SL07 | 高用量旧 reset 已过，新窗口低值 | 接受新低值与新 reset | docs/improvements.md 第10项："窗口已重置的旧读数作废"。 |
| TF01 | case 多分支含测试 | 原样放行，语法有效 | docs/headers.md："任何一段以 bash 保留字开头…看不准就放行"。 |
| TF02 | 函数定义再调用 | 原样放行，语法有效 | docs/headers.md："括号…多行结构…看不准就放行"。 |
| TF03 | 花括号组含测试与 echo | 原样放行，语法有效 | docs/headers.md："任何一段以 bash 保留字开头（if/for/while/{ 等多行结构）"。 |
| TF04 | time 前缀 | 原样放行，语法有效 | docs/headers.md："任何一段以 bash 保留字开头"；"只在命令位置识别测试"。 |
| TF05 | env 赋值包装 pytest | 原样放行，语法有效 | **推断**；docs/headers.md："每段的第一个词"；docs/improvements.md 第8项列出 pytest/python/npm/node 测试首词，未列 env。 |
| TF06 | ! 否定测试 | 原样放行，语法有效 | docs/headers.md："任何一段以 bash 保留字开头"；"看不准就放行"。 |
| TF07 | exec 包装 pytest | 原样放行，语法有效 | **推断**；docs/headers.md："只在命令位置…每段的第一个词"，不穿透 exec。 |
| TF08 | bash -c 字符串内两种测试 | 原样放行，语法有效 | **推断**；docs/headers.md："每段的第一个词"；docs/improvements.md 第8项列出的测试首词不含 bash。 |
| TF09 | 测试参数含进程替换 | 原样放行，语法有效 | docs/headers.md："含…重定向…括号…看不准就放行"。 |
| TF10 | &> 合并重定向 | 原样放行，语法有效 | docs/headers.md："含…重定向…看不准就放行"。 |
| TF11 | 单独 2>&1 | 原样放行，语法有效 | docs/headers.md："含…重定向…看不准就放行"。 |
| TF12 | 注释里含分隔符和测试文本 | 真测试仍跑，后续 printf 不丢失 | docs/improvements.md 第1项："带注释能跑，rc 保留"。 |
| TF13 | 反斜线续行带失败参数 | 参数不丢，保留退出7 | **推断（复杂词法下的语义保留）**；docs/headers.md："包装保留原退出码"；"看不准就放行"；不要求一定改写。 |
| TF14 | ANSI-C 引号含分隔符和伪测试 | 参数文本与成功码保留 | **推断（复杂词法下的语义保留）**；docs/headers.md："只在命令位置识别测试"；"其余段的输出原样保留"；不要求一定改写。 |
| TF15 | 三种测试穿插普通 printf | 测试段被改写、四个输出都保留、最终退出7 | docs/headers.md："只包装测试那一段，其余段的输出原样保留"；"包装保留原退出码"。 |
