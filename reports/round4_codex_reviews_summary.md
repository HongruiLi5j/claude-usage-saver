# Round 4 Codex independent reviews — conclusions only
Raw Codex logs are kept locally (they contain local file dumps). Each section below is the reviewer's final conclusion, verbatim.

## round4_codex_review.md
审查全程只读，未修改文件；未读 `.secret`、`sealed`、`test2`、`state/testlogs`，也未审 `test_filter.py`。下面的验证是**内存模拟**，没有运行会写真实 state 的 hook，也没有做 Claude Code 真机测试。

原报告的三个重点结论均成立；另外确认了几个遗漏。没有发现证据充分、需要标为“严重”的问题。

**对原报告结论的复核**

1. **[中] 过期 5h 数据仍被写入，并触发“快用完”——成立。**  
   位置：`hooks/statusline.sh:23–33`、`hooks/context_guard.py:25`。  
   **复现**：传入 `five_hour={used_percentage:95,resets_at:一小时前}`，周用量为 50%、明天重置。模拟结果为状态栏显示 `5h 95%`，守卫返回“快用完 0.6”。代码只清理旧数据 `b`，没有清理输入 `a`。  
   **改法**：合并前分别验证两个输入窗口的有效期；守卫也独立校验，避免依赖生产者永远正确。缺少重置时间应明确视为“未知”，不要混同正常有效窗口。

2. **[中] Projects 外的项目会拿到其他项目的交接——成立，当前影响比报告描述更大。**  
   位置：`hooks/session_resume.py:155–165`、`:376–390`。  
   **复现**：cwd 为 `/private/tmp/current`，Projects 下存在近期交接；即使当前目录是独立仓库，仍会选取其他项目的文件。内存模拟确认了这个选择结果。**如果被选文件带 MARK，正文会直接注入**，并非通常只给路径；本项目 HANDOFF 当前已有 MARK。  
   **改法**：只允许 cwd 恰为 HOME 或 Projects 时显式回退；普通目录、外部项目不跨项目寻找。交接、旧 transcript 都应验证属于当前项目。

3. **[中] 项目软线按子串匹配——成立。**  
   位置：`hooks/statusline.sh:44–45`、`hooks/context_guard.py:53–55`。  
   **复现**：cwd 为 `/virtual/quant-backtest-old`，上下文 200k；状态栏没有软线警告，因为误用了 `quant-backtest` 的 250k 阈值。  
   **改法**：按规范化项目根路径精确匹配；若配置继续存项目名，至少按完整路径段匹配。两个脚本共用同一匹配函数，避免后续分歧。

4. **[中] compact 分支未启用——当前仍成立；MARK 未落地的结论已过时。**  
   位置：`hooks/session_resume.py:354–355`、`:376`。  
   **复现**：只读核实实际 `~/.claude/settings.json`，SessionStart matcher 仍为 `clear`，因此正常安装入口不会调用 compact 分支。但是当前 handoff skill 已明确要求第一行写 MARK，本项目 HANDOFF 第一行也已有 MARK。  
   **改法**：安装配置改为覆盖 `clear|compact`，并增加安装状态检查。保留 MARK 门槛；不应依据旧报告建议关闭它。HANDOFF 中仍写“这些功能待做”的段落也应同步清理。

5. **[低] rate.json 并发读改写无锁——成立，但不能保证下次刷新自愈。**  
   位置：`hooks/statusline.sh:20–33`。  
   **复现**：两个进程都读到 70%，分别算出 90%、75%，让后者最后替换文件，最终即为 75%。原子替换只能防半文件，不能防丢更新；若 90% 的会话停止刷新，也没有保证自动恢复。  
   **改法**：锁住完整的“读—合并—写”事务，或各会话分别落样本，由单一聚合器合并。

6. **[低] 用 ctime 判断交接是否属于上一段对话——成立。**  
   位置：`hooks/session_resume.py:365`、`:381–389`。  
   **复现**：长会话中途写交接，之后继续运行超过三小时；transcript 持续追加使 ctime 更新，交接会被判不新鲜，正文预算降到 2500 字。这里的判断还受“三小时内一律算新鲜”的另一分支影响。  
   **改法**：使用明确的会话起止时间或记录交接的 session_id；文件创建时间也不能准确代表恢复、续写后的会话范围。

7. **[低] edited_in 只查尾部 3MB——成立。**  
   位置：`hooks/session_resume.py:96–101`、`:376`。  
   **复现**：无 MARK 的交接曾用 Write 修改，随后 transcript 又追加超过 3MB，写入记录就可能不可见，正文不再注入。  
   **改法**：优先让交接生成流程稳定写 MARK；若需要可靠支持旧文件，单独记录本会话成功写过的交接路径，避免反复扫描全文。

原报告第 13 项关于 `mode.json` 的判断也成立：`context_guard.py:67` 写入的软线属于最后一个提交消息的会话；当前状态栏只取 `mult`，所以这些调试字段**目前没有造成跨项目阈值误用**，宜标注归属，无需单独当成行为缺陷。

**原报告未提到的问题**

8. **[中] 过期周用量达到 90% 时，过期检查根本执行不到。**  
   位置：`hooks/context_guard.py:25–32`。  
   **复现**：rate 的 `ts` 新鲜，周用量 95%，周重置时间已过。模拟仍返回：
   ```
   快用完 0.6 周用量 95%,5小时窗口 0%
   ```
   原报告说“周窗口已经检查过期”，遗漏了检查顺序。  
   **改法**：先清理所有无效窗口，再判断近上限，最后才计算周进度。

9. **[中] 缺少周数据时，有效的 5h 高用量反而被忽略。**  
   位置：`hooks/context_guard.py:22–26`。  
   **复现**：只有尚未过期的 5h 95%，没有 seven_day。模拟返回“正常 1.0，无周用量数据”。  
   **改法**：有效的任一窗口达到近上限，都应先触发“快用完”；只有周进度分档需要依赖 seven_day。

10. **[中] 全局 ts 会让旧窗口数据重新变“新鲜”。**  
    位置：`hooks/statusline.sh:21–33`、`hooks/context_guard.py:17`。  
    **复现**：已有超过六小时的周用量 95%，周窗口尚未重置；只提供新的 5h 10%。合并后周用量被保留，顶层 `ts` 更新为当前时间，守卫又开始信任旧周数据。模拟确认如此。同窗口的重复旧 payload 也会通过第 31 行不断刷新 ts。  
    **改法**：每个窗口独立记录采样时间。区分“本次状态栏刷新时间”和“额度读数实际更新时间”；拿不到后者时，应明确承认无法识别重复旧样本。

11. **[中] Pro 封顶逻辑只在守卫中实现，状态栏会给出相反提示。**  
    位置：`hooks/statusline.sh:43–50`；对应 `hooks/context_guard.py:59–64`。  
    **复现**：日期达到配置中的 `2026-11-21`，普通档、`quant-backtest` 上下文为 200k。守卫已将强线封顶到 195k，会发强提醒；状态栏仍按软线 250k 判断，不显示警告。  
    **改法**：共享完整的阈值计算，包括项目覆盖、档位倍数、下限和日期封顶。不要让两边各维护一份近似算法。

12. **[中] 同 cwd 并发会话会串用上一段 transcript。**  
    位置：`hooks/session_resume.py:173–184`、`:323–328`。  
    **复现**：会话 A、B 在同一目录分别 `/clear`；A 写指针后，B 覆盖同一路径。A 的新会话读取指针时，只检查旧 session_id 不等于自己，因而接受 B。模拟中 A 得到 `/virtual/B.jsonl`。指针分支还绕过了后续“多个候选不猜”的保护。  
    **改法**：按旧 session_id 分别保存指针，通过可靠的会话关联选择；没有关联且存在多个候选时，放弃自动摘要，不应仅按 cwd 猜。

13. **[中] `.git` 是文件时，项目边界失效。**  
    位置：`hooks/session_resume.py:38–39`、`:53–60`。  
    **复现**：Projects 外的 Git worktree 或 submodule，根目录的 `.git` 为文件；本项目没有交接，上层目录有 HANDOFF。模拟中 `is_project_root` 返回 False，随后选中了上层交接。  
    **改法**：识别 `.git` 文件和目录，并规范化路径。项目根边界应在任何交接搜索、跨项目回退之前确定。

14. **[中] 上下文用量扫描没有排除 sidechain。**  
    位置：`hooks/context_guard.py:83–85`。  
    **复现**：合成 transcript 先放主会话 200k usage，再放 `isSidechain:true` 的 1k usage。主记录单独存在时会提醒，加上后者便不提醒。反过来也能造成误提醒。  
    **改法**：排除 sidechain，并按可用的 session 标识限定主会话记录。是否有真实 transcript 混入这种记录，需要后续真机核实；目前确认的是解析器缺少隔离。

15. **[低] mode.json 非原子写入，读取方可能短暂读到空文件或半个 JSON。**  
    位置：`hooks/context_guard.py:67`。  
    **复现**：写进程以 `w` 打开后暂停，状态栏恰好读取；解析失败后使用默认倍数，提示可能暂时消失或跳档。  
    **改法**：同目录临时文件写完、关闭，再 `os.replace`。当前 statusline 对 rate.json 已采用类似方式。

**最值得做的三项省额度改进**

1. **统一额度有效性和阈值计算。** 先修窗口过期、独立采样时间、项目匹配和日期封顶，让守卫与状态栏给出一致且可信的交接时机。
2. **把自动接续限定到正确会话，并缩短默认注入。** 先修跨项目和并发串线；优先注入当前目标、红线、下一步，有可靠交接时减少重复 transcript 摘录，避免每次 clear 都重新背负大量历史。
3. **给守卫提醒加会话级去重。** 当前 `context_guard.py:91–97` 超线后每次提交都重复注入。按首次越软线、首次越强线、必要的间隔升级提醒，减少重复交接动作和上下文增长，同时保留自然停顿时再清理的原则。

## round4_codex_review_CEF.md
结论：**C 的计算与旧节对比通过；E、F 打回。完整落盘验证未完成，不能确认实施报告中的“全部测试通过”。**

本轮未修改任何文件，未读取指定禁区，未写真实 settings，未执行安装器写入模式。环境连 `mktemp -d` 都报 `Operation not permitted`，因此以下明确区分真实进程测试与内存模拟。

**1．C：通过（报告计算范围）**

亲自读取 549 个允许访问的会话文件，提取 42,681 条 usage 记录，以同一快照、同一时刻执行新旧代码；将写入重定向到内存。

- 删除新增节后，新旧报告逐字一致，原统计量完全一致。
- 两窗口分别为 19,128、1,133 次请求；明细分组请求数、成本分项均能汇总到总计。
- `all·all` 每次成本为 **32.1k、21.1k**，与顶部一致。
- `sub·sonnet` 仍为 160→218 次、13.5k→25.8k。

注意：现文件还包含 [R4D 的 history 日期去重改动](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/weekly_compare.py:97)，所以“相较指定备份只新增明细”的描述已不准确。history 落盘行为本轮未验。

**2．E：打回**

真实 `--dry-run` 成功；普通 apply→二次 apply→undo 的**内存模拟**显示二次幂等、撤回后 JSON 恢复。但发现：

- **撤回遗漏后续新增配置。**复现：默认 apply→再次 apply 并指定 `--subagent-cap 4`→undo。结果仍残留 `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS=4`。原因是[已有 state 时未更新撤回字段](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py:142)。
- **清单第 18 步误计旧请求。**原命令仅筛文件 mtime。用“11-21 后修改的文件，内含 11-01 的 300001-token 请求”运行原 Python 命令，实际输出 **300001**，错误判断上限失效。应按每条记录的 timestamp 筛选。[位置](/Users/lihongrui/Documents/Projects/claude-usage-saver/reports/pro_switch_checklist.md:41)
- **第 22 步没有还原操作。**给出的 `ls | grep` 只列备份，不能完成整文件还原。[位置](/Users/lihongrui/Documents/Projects/claude-usage-saver/reports/pro_switch_checklist.md:51)
- **路由规则冲突。**[第 16 行](/Users/lihongrui/.claude/skills/model-router/SKILL.md:16)要求需要完整上下文时 fork，[第 33 行](/Users/lihongrui/.claude/skills/model-router/SKILL.md:33)却规定 ≥8 万一律不用 fork，未说明优先级。

清单提取出的 12 条终端命令均通过 `zsh -n`，没有 `#` 注释，所需命令均存在。登录、配置写入、撤回属于有副作用步骤，本轮未执行；会话斜杠命令也未实测。真实临时副本上的 apply／undo 仍待补验。

**3．F：打回**

实际运行 `tests_r4F.py`，在创建首个临时目录时退出，**没有完成任何用例**。安装器真实 `--dry-run` 显示两条 hook 已存在。

另亲自复现：

- **卸载误删其它 hook。**在同一组内放入 logger 与 `/other/alert.sh`，以该内存配置运行原安装器的 `--uninstall --dry-run` 逻辑，整组被移除，`alert.sh` 未保留。[删除逻辑](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/install_limit_logger.py:60)
- **并发轮转可能覆盖旧日志。**对原 `_rotate()` 控制两个线程交错：两者都检查到目标不存在→A 轮转并创建新日志→B 将新日志 rename 到同一旧文件名。内存文件系统按覆盖式 rename 语义执行后，原始大文件丢失。[竞态位置](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/limit_logger.py:108)；尚未做真实磁盘并发复现。
- 实际启动 hook，测试坏 JSON、空输入、StopFailure、无关 Notification，并令状态目录不可用：四次均 **exit 0，stdout/stderr 为空**。正常追加及串行 5MB 轮转未完成落盘验证。

| 项 | 结论 | 主要问题 |
|---|---|---|
| C | 通过（计算与旧节） | 新旧输出一致、总计自洽；history 落盘未验，另含 R4D 改动 |
| E | 打回 | 二次新增配置撤回遗漏；清单日期筛选错误、还原命令缺失；fork 规则冲突 |
| F | 打回 | 卸载误删同组其它 hook；并发轮转存在覆盖风险；22 项测试未能重跑完成 |

## round4_codex_review_B.md
**结论：打回。** 当前仍有两类命令被改坏，B3 的一个新断言也存在漏检。两类 shell 缺陷在 B2 前备份中同样存在，属于未修复问题，并非本次新增回归。

本次没有修改文件，也未读取禁止的目录。上层只读沙箱导致 `mktemp -d` 失败，因此无法按要求创建临时假 pytest 脚本。替代验证使用了内存中的假 pytest 函数：按参数输出、返回指定退出码，并分别测试 mktemp 失败回退和 `/dev/null` 日志路径。**这不等于完成真实日志过滤验证。**

亲自构造并执行了 **30 条命令**，覆盖要求中的全部 shell 类型；每条均比较原命令、旧版改写、当前改写的 `bash -n` 和执行退出码。当前版 **27 条一致，3 条不一致**。旧版多行 if/for/while/until/function/braces 的语法错误已确认修复。

两个可复现问题：

1. **多行 `[[ … ]]` 被插入分号。** [test_filter.py:44](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/test_filter.py:44)

   ```bash
   [[
     x = x
   ]]; pytest --rc=7
   ```

   原命令：`bash -n=0`、执行 rc=7；改写后：`bash -n=2`、执行 rc=2。改写开头变成 `[[ ; x = x ; ]]`。另一条 `[[ x = x` 换行后接 `]] && pytest --rc=7` 也同样失败。B2 报告关于 `[[ … ]]` 拼回不会出语法错误的判断不成立。

2. **转义空格后的 `#` 被误判为注释，吞掉后续命令。** [test_filter.py:37](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/test_filter.py:37)

   ```bash
   pytest --rc=0; echo foo\ #literal; exit 7
   ```

   原命令输出 `foo #literal`、rc=7；改写后输出 `foo`、rc=0。两边语法检查都通过，属于静默改变执行语义。该结果另用真实 mktemp 失败回退路径重跑确认过。

已尝试执行 `python3 tools/scorecard.py --version all`，但在创建第一个临时目录时抛出 `FileNotFoundError: No usable temporary directory found`。**本次无法确认 69/69**，也没有重写评分报告。

抽查了 CG16、LL02、SR19，直接执行其原断言函数并注入内存反例。这是断言层验证，未冒充完整场景重跑：

- **CG16**：正确结果通过；取消封顶、改成强提醒、无提醒均失败，非恒真。
- **LL02**：正确结果通过；误记 idle、漏记恢复、重复记录、错误 kind 均失败，非恒真。
- **SR19**：缺红线、混入旧下一步会失败；但 `N3-RED-1 N3-NEXT-NEW N3-RED-2 N3-RED-3` 仍通过。它只比较第一条红线的位置，未完整验证“三条红线都在下一步之前”。见 [scorecard.py:628](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/scorecard.py:628)。当前该场景仍标为不适用。

| 检查项 | 结论 | 问题 |
|---|---|---|
| B2 常见控制结构修复 | 已验证 | 所测 if/for/while/until/function/braces 已修复 |
| 30 条 shell 对照 | 打回 | 27 条一致，3 条退出码不一致 |
| 多行 `[[ … ]]` | 打回 | 两条合法命令被改成语法错误 |
| 转义空格后的 `#` | 打回 | 后续 `exit 7` 被吞，rc 从 7 变 0 |
| CG16 断言 | 断言层通过 | 反例能触发失败；未完成集成重跑 |
| LL02 断言 | 断言层通过 | 反例能触发失败；未完成集成重跑 |
| SR19 断言 | 打回 | 第二、第三条红线放错位置仍通过 |
| scorecard 全量通过数 | 未确认 | 只读沙箱阻止创建临时目录 |
| 临时脚本及真实长日志过滤 | 未完成 | 需要可写临时目录才能完成指定验证 |

## round4_codex_review_D.md
**结论：打回。** 发现 2 类提醒去重问题，其中损坏的记录会让超过强线的 hook 静默漏提醒。

已亲自阅读报告，并逐一对比三个实现与 `backups/*.pre-r4D`；当前文件和备份的 SHA-1 均与报告一致。未改任何文件，未读取禁读路径，未改 `~/.claude/settings.json`。

原测试已尝试执行：

```text
PYTHONDONTWRITEBYTECODE=1 python3 tools/tests_r4D.py
FileNotFoundError: [Errno 2] No usable temporary directory found …
```

脚本在第一个用例前退出，故**未独立确认报告的 34/34**。随后按你的备用要求，设置虚拟 `HOME`、`USAGE_SAVER_STATE`，将文件读写替换为内存对象，直接执行原实现代码；新增 **32 条断言，28 通过、4 失败**，4 条失败归属以下两类问题。这些是内存模拟结果，不是落盘或真机实测。

1. **过期提醒记录未在去重前清理。**  
   位置：[context_guard.py:170](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/context_guard.py:170)、[context_guard.py:187](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/context_guard.py:187)。

   复现：预置会话 A 的记录为 `level="soft", at_n=170000, since=0, ts=now-604801`；再次以 A、170000 token、软线 160000 调用。预期过期记录清除后返回 `"soft"`，实际返回 `None`，并将旧记录的 `ts` 刷成当前时间。报告所述“超过 7 天自动清掉”对本次访问的会话不成立。

2. **损坏的单条记录会造成静默漏提醒，且能影响其他会话。**  
   位置：[context_guard.py:175](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/context_guard.py:175)、[context_guard.py:187](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/context_guard.py:187)、[context_guard.py:247](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/context_guard.py:247)。

   两个独立复现：
   - `remind.json={"A":"broken"}`，会话 A 的主会话 usage 为 320000：内部出现 `AttributeError`，`main()` 捕获后 stdout 为空，强提醒丢失。
   - 会话 B 的记录为 `{"level":"soft","at_n":170000,"since":0,"ts":"broken"}`，会话 A 为 320000：清理 B 时发生类型错误，A 的强提醒同样丢失。

   建议先校验、清理记录，再参与去重；坏记录按缺失处理，去重状态异常时退回本次应有的提醒。

| 检查项 | 结论 | 问题 |
|---|---|---|
| 三个实现与 pre-r4D 差异、报告哈希 | 通过 | 与报告一致 |
| `tools/tests_r4D.py` 原测试 | 未完成 | 临时目录不可用，未进入用例 |
| 过期窗口、reset 恰等于当前时间 | 通过（模拟） | 过期窗口不参与判档、不写入新读数 |
| 缺失窗口、缺失 reset | 通过（模拟） | 按正常档处理 |
| 陈旧 sampled_at、6 小时边界 | 通过（模拟） | 顶层新 ts 不会掩盖旧周样本 |
| 相同值与增长值的采样时间 | 通过（模拟） | 同值保留、增长刷新 |
| 项目后缀、子目录、路径规范化 | 通过（模拟） | 符合路径组件匹配规则 |
| cap 斜杠、点号、未来及非法日期 | 通过（模拟） | 非法日期按报告约定封顶 |
| 坏数值配置、损坏 config JSON | 通过（模拟） | 回退默认阈值 |
| sidechain 高值及仅有 sidechain | 通过（模拟） | 不计入主会话上下文 |
| 跨会话去重、compact 后重新提醒 | 通过（模拟） | 顺序调用符合预期；未验证并发 |
| 超过 7 天的同会话记录 | **打回** | 先抑制提醒，再刷新旧记录时间 |
| 当前会话记录损坏 | **打回** | 320k 强提醒静默丢失 |
| 其他会话记录的 ts 损坏 | **打回** | 可连带导致当前会话漏强提醒 |
| context_guard 缺失或语法损坏 | 通过（模拟） | 基础状态栏整行保留，警告消失 |
| 仅撤回旧版 context_guard | 已确认限制（模拟） | `SystemExit` 导致整行消失；报告已明确警告 |
| weekly 本地日期、DST、坏日期 | 通过（模拟） | 日期转换符合预期；未落盘重跑完整周报 |

## round4_codex_review_E2.md
结论：**打回**。E2 修复了普通配置下多次 apply 的撤回遗漏，也统一了 fork 规则；但备份、扫描和撤回仍有可复现问题，不能通过。

已对比三组指定文件及备份，并亲自执行原实现代码。全程未修改文件、未读取禁区、未读写真实 `~/.claude/settings.json`。所有 apply／undo 均使用 `/virtual/` 内存文件系统；磁盘写入和真实符号链接操作未实跑。

| 检查项 | 方式 | 结果、复现及代码位置 |
|---|---|---|
| 多次 apply、cap、幂等、undo | 内存执行原代码 | **通过**：默认→cap 4→cap 2→重复 apply→undo，四组初始配置均恢复原 JSON；二次 undo 无操作。 |
| 用户事后改值、删键、新增键 | 内存执行原代码 | **通过**：逐键修改保留，未修改的脚本目标键正常撤回。 |
| 整个 `env` 的改动与恢复 | 内存执行原代码 | **失败**：原有 `env`→apply→用户删除整个 `env`→undo，结果重新插入 `"env": {}`。原值为 `null`、字符串、数组时，apply→undo 也会丢失原值。[原值记录处](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py:179)、[撤回处](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py:216)。 |
| 不同 settings 共用备份目录 | 内存执行原代码 | **失败**：A 原窗口 300000，apply；B 原窗口 900000，同目录 apply→undo，B 被恢复成 **300000**。state 保存了 settings 路径，却未校验。[读取 state](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py:180)、[undo](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py:214)。 |
| 秒内连续 apply 的备份 | 内存执行原代码 | **失败**：默认 apply→同秒 apply cap 4，两次备份路径相同，首份原始备份被覆盖。[秒级命名及复制](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py:175)。 |
| 第 22 步备份选择 | 内存模拟元数据及排序；语法实跑 | **失败**：原 settings 带未来 mtime，连续两次 apply；`copy2` 保留该时间，`ls -t` 选中较早备份，而非最近一次 apply 的备份。结尾 glob 能排除 `-undo`，但不能保证选对。[复制处](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py:177)、[清单命令](/Users/lihongrui/Documents/Projects/claude-usage-saver/reports/pro_switch_checklist.md:52)。 |
| 时区、本地零点、坏 timestamp | 内存记录＋系统时区计算 | **通过**：洛杉矶、加尔各答、夏令时切换日边界正确；UTC、显式偏移、无时区记录符合本地零点规则；坏 timestamp、坏 JSON 被跳过。 |
| 新记录搭配旧 mtime | 内存执行原代码 | **失败**：文件含 `2026-11-22T12:00:00Z`、300001 tokens，mtime 设为旧值，扫描返回 **0 条、最大 0**。文件 mtime 不能证明记录都早于截止日。[提前跳过处](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py:67)。 |
| 异常 usage | 内存执行原代码 | **失败**：合法 timestamp，`usage=[]` 抛 `AttributeError`；`input_tokens="100"` 或 `null` 抛 `TypeError`，整次扫描终止。[未受异常保护的计算](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py:87)。 |
| settings 为符号链接 | 内存模拟链接语义 | **失败**：apply 后链接变成普通文件，原链接目标未更新；undo 也不能恢复链接。[替换路径处](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py:107)。 |
| E1 state 升级兼容 | 用旧代码生成内存 state，再执行 E2 | **失败**：E2 apply 和 undo 均抛 `KeyError: 'keys'`，没有迁移或明确拒绝提示。[apply](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py:192)、[undo](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py:218)。 |
| 坏 settings、空目录、命令语法 | **真实子进程** | 坏 JSON、顶层数组和 `null` 均 exit 1；不存在目录扫描 exit 0、输出 0 条；12 条终端命令均通过 `zsh -n`。未执行真实 cp。 |
| model-router fork 一致性 | 指定新旧文件静态对比 | **通过**：第 16、32、33 行一致采用“需要父背景且 <8 万优先 fork；≥8 万新建 agent”。[规则位置](/Users/lihongrui/.claude/skills/model-router/SKILL.md:16)。 |

## round4_codex_review_F2.md
结论：**打回。** F2 修复了同组其它 hook 和 matcher 被整组删除的问题，但锁失效时仍可丢失新事件，新增的阻塞锁也不满足“不阻断”。真实 settings 已安装这份代码，两条 hook 的超时均为 10 秒，后续触发即受影响。

具体问题及复现：

1. **P1：锁不可用时，轮转仍会丢数据。**  
   [limit_logger.py:104](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/limit_logger.py:104) 的 `link`、`unlink` 是两步操作。内存模拟内核硬链接语义并执行原 `_rotate()`：
   A 建立旧文件硬链接后暂停 → B 完成轮转、删除原名并创建新事件文件 C → A 恢复，`unlink(src)` 删除 C。断言结果：旧大文件保留在两个归档名下，**C 完全丢失**。目标不覆盖，不代表数据不丢失；L13 的顺序调用没有覆盖此交错。

2. **P2：锁等待没有内部超时。**  
   [limit_logger.py:136](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/limit_logger.py:136) 使用阻塞式 `LOCK_EX`。亲自实跑：父进程对既有源码文件的只读描述符持锁，子进程执行原入口，仅将锁路径映射到同一文件；**10.3 秒后仍未返回**，由复核进程终止。真实触发若锁持有者停滞，会耗尽已配置的 10 秒超时，无法自行静默退出 0。

3. **P2：短写会留下截断 JSON，且静默成功。**  
   [limit_logger.py:160](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/limit_logger.py:160) 忽略 `os.write()` 返回值。内存注入合法短写：请求写入 367 字节，仅写入并返回 183；原代码直接关闭文件，留下无换行的半条 JSON，后续追加会进一步破坏记录边界。

4. **P2：卸载仍可能误删其它目录的同名脚本。**  
   [install_limit_logger.py:66](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/install_limit_logger.py:66) 用子串判断归属。内存 settings 同组放入本项目命令及 `python3 /other/limit_logger.py`，执行原安装器卸载逻辑后，**两条均被删除**。应按本项目脚本路径识别归属。

| 检查项 | 亲自重跑的证据与结果 |
|---|---|
| 同组其它 hook、matcher | 内存执行原 I11/I12 断言，保留 `/other/alert.sh` 和 matcher，通过 |
| 已有轮转目标 | 原 L10/L13 内存断言通过，已有归档未覆盖 |
| 硬链接不支持 | 内存注入 `EOPNOTSUPP`，原文件保留 |
| 原文件已转走 | 内存模拟 stat 后源文件消失，静默返回 |
| 锁失效交错 | 内存执行原轮转代码，新事件丢失 |
| 异常 fail-open | 实际子进程测试 6 组输入、7 个内存异常注入路径，均退出 0、无输出；阻塞锁另见上述实跑 |
| 临时目录回退 | 内存验证 tempfile 失败后退回项目目录并移入回收目录；项目目录也只读时，入口在首个用例前抛错、没有汇总，见 [tests_r4F.py:409](/Users/lihongrui/Documents/Projects/claude-usage-saver/tools/tests_r4F.py:409) |
| 原测试覆盖 | 24 个串行用例的原断言经内存 I/O 适配后全部通过；L12/L14 的真实落盘并发未运行，不能认定 26/26 |
| 真实安装状态 | 只读核对 settings，实跑安装及卸载 `--dry-run`，两条均已安装 |

全程未修改文件或真实 settings，未读取指定禁区。正常磁盘追加及并发轮转未完成实跑验证。

## round4_codex_review_G.md
结论：**打回**。真实 HANDOFF 的 `### 红线 / 未决` 已能正确注入，但补充复现仍发现旧轮次混入和指针串线问题。全程只读，未改文件或 settings，未读取指定禁区。

1. **compact 仍会带入旧轮次“下一步”**。位置：[session_resume.py:180](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/session_resume.py:180)、[session_resume.py:211](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/session_resume.py:211)。  
   在带标记的交接中使用以下正文，执行 compact，实得输出仍含 `OLD-NEXT`：

   ```markdown
   ### 红线 / 未决
   - RED
   ### 下一步
   1. NEW-NEXT

   **下一步（第 3 轮）**：OLD-NEXT
   ```

   原因是 Markdown 标题段只在同级或更高级 `#` 标题处结束，把旧加粗段落整个包含进去。另测“红线节内嵌 `#### 下一步`，历史节另有下一步”时，第 211 行还会跳过已包含的当前下一步，选中历史下一步。应同时修正段落边界与候选选择，补混合标题测试。

2. **`ps` 失败后，会读取有其他 owner 的新格式指针**。位置：[session_resume.py:335](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/session_resume.py:335)。  
   复现：让 `owner_id()` 返回 `None`，同 cwd 放一条未过期的 v2 指针，明确设 `owner="OTHER"`；当前新会话仍取得该 transcript。**本沙箱实跑 `ps` 确实被拒，`owner_id()` 返回 `None`**；串线选择过程由内存模拟验证。旧格式兼容不应顺带信任带其他 owner 的 v2 条目。

3. **指针选择与消费不是原子操作，两个新会话可重复取得同一摘要**。位置：[session_resume.py:339](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/session_resume.py:339)、[session_resume.py:358](/Users/lihongrui/Documents/Projects/claude-usage-saver/hooks/session_resume.py:358)。  
   确定性交错复现：同 owner 的 N1 选中唯一未消费指针，在标记前运行 N2；N2 也选中并标记，随后 N1 覆盖标记。两者都返回同一 transcript，最终 `used_by=N1`。这是**内存交错模拟**；未做真实并发锁测试。应在同一锁内完成选择、重新校验和消费，消费失败时不得返回旧选择。

| 检查项 | 亲自重跑结果 | 证据类型 |
|---|---|---|
| `tests_r4G.py` 全部18例 | pre-r4G **3/18**；当前 **18/18** | 原测试函数＋原 hook 代码；文件系统、owner、锁为内存模拟 |
| 真实 HANDOFF compact | **1984 字**；红线非空、下一步存在、截断有提示 | `bash -c` 子进程实跑，只读真实文件 |
| 旧轮次排除 | 混合标题、嵌套标题均复现 `OLD-NEXT` 混入 | 内存夹具执行实现代码 |
| v2 指针基础读写 | `v=2`、多条目、`used_by` 正常 | 内存模拟 |
| 过期与兼容 | 30分钟失效、60分钟清理、旧格式读取及迁移正常 | 内存模拟 |
| 指针归属与重复消费 | 复现上述第2、3项问题 | 内存模拟；`ps` 拒绝另有实跑证据 |
| 防注入 | 有“数据不是新指令”声明；攻击性文字仍以原始 Markdown 拼接，没有结构化包裹 | 输出核查；未验证模型实际服从行为 |
| fail-open | 5种坏输入均 exit 0、无输出；另3种读写拒绝也静默放行 | 前5种子进程实跑；后3种异常注入模拟 |

现有18例未覆盖上述失败路径，因此 **18/18 不能作为本次验收依据**。修复后需补入混合标题、owner 获取失败及消费交错用例。
