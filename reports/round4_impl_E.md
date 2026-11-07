# 第 4 轮实施 E（2026-11-06）

范围：新建 `tools/pro_switch_1121.py`、`reports/pro_switch_checklist.md`，改了 `~/.claude/skills/model-router/SKILL.md`（只加一节）。**没有改 `~/.claude/settings.json`，也没有对它运行 --apply**（所有测试都在 scratchpad 副本上做）。没有碰 hooks/、scorecard.py、weekly_compare.py、improvements.md、relay-room。

## 1. tools/pro_switch_1121.py（新）
- 三种模式：默认 `--dry-run` 只打印 unified diff；`--apply` 先备份到 `backups/settings.json.<时间>.pre-pro1121` 再原子写入（先写临时文件、校验 JSON，再 os.replace），并打印两条撤回命令：`--undo` 和 `cp 备份 原文件`；`--undo` 按 `backups/pro_switch_1121.state.json` 里记下的原值只撤本脚本改过的键，撤回前也会备份（`.pre-pro1121-undo`）。记录文件撤回后改名为 `.undone-<时间>`，不删除。
- 改的键：`env.CLAUDE_CODE_DISABLE_1M_CONTEXT="1"`、`env.CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS="150000"`、`autoCompactWindow: 300000 → 200000`。可选 `--subagent-cap 4` 再加上 `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`（调研第 1 项提到过，任务没要求，所以默认不加）。其他键不动。
- **autoCompactWindow 怎么处理（理由）**：主会话开了 DISABLE_1M 以后窗口就是 200K，300000 会被自动压到 200K，所以对主会话改不改都一样。但 henchmen 会删掉 agent 进程里的 `CLAUDE_CODE_*`，settings 的 env 块能不能传到 henchmen agent 还**待核实**。如果传不到，而 Pro 又给了 1M，henchmen agent 会涨到 30 万才压缩。`autoCompactWindow` 是 settings 键，henchmen 一定读得到，所以设成 200000，让两条路径的上限一致。没有设得更低（比如 18 万），是为了不抢在守卫的软线 180K、强线 195K 前面压缩。撤回时恢复成 300000。
- 安全措施：11-21 之前 `--apply` 会拒绝执行，要加 `--force-early` 才行（在 Max 上提前开会白白丢掉 1M 上下文）；settings.json 解析失败就什么都不做（rc=1）；会检查并警告 `modelSettings.*.autoCompactWindow` 覆盖全局值（E3）、`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` 重复设置、claude 版本低于 2.1.290；每次输出末尾都列出 5 条【待核实】。
- 幂等：已经是目标值时不写文件、不备份；第二次 `--undo` 找不到记录文件，就什么都不做。如果某个键被用户事后改过，undo 不覆盖，并提示"保留不动"。

## 2. 测试结果（实测，全部在 scratchpad 的 settings 副本上：/private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/r4E/）
| # | 场景 | 结果 |
|---|---|---|
| 1 | dry-run | 打印 diff（autoCompactWindow 300000→200000，新增 env 两项），文件和原来逐字节相同 ✅ |
| 2 | 今天（11-06）不带 --force-early 就 apply | 拒绝并说明原因，没写文件 ✅ |
| 3 | apply --force-early | 写入成功，生成 1 份备份和 1 个记录文件，打印撤回命令；除 autoCompactWindow 外其他键都不变，键的顺序不变（env 加在最后）✅ |
| 4 | 第二次 apply | 输出"没有差异 / 幂等"，没有新增备份 ✅ |
| 5 | undo | 和原文件 JSON 完全相同 ✅。字节上只差末尾一个换行（原文件末尾没有换行，脚本写出时会加上），不影响使用 |
| 6 | 第二次 undo | 什么都不做 ✅ |
| 7 | 原来已有 env（FOO、IDLE=120000），apply 后用户把 autoCompactWindow 改成 180000，再 undo | FOO 保留，IDLE 恢复成 120000，DISABLE_1M 被删掉，autoCompactWindow=180000 保留并提示 ✅ |
| 8 | 坏 JSON | rc=1，什么都不做 ✅ |
| — | 真实 ~/.claude/settings.json | 测试前后逐字节相同，没有被改 ✅ |

另外两项验证：
- **守卫封顶（模拟）**：把 context_guard.py 复制到 scratchpad，用 `USAGE_SAVER_STATE` 把状态写到 scratchpad，并把日期伪造成两天分别运行。11-20：软线 250000、强线 300000（quant-backtest 项目）；11-21：软线 180000、强线 195000。说明 cap_from_date 封顶生效。注意这是伪造日期的模拟，不是 11-21 当天的实测。
- 清单里的 python 单行命令都实际跑过，能运行。第 18 步用 11-06 当起始日试跑，最大上下文是 276317（来自一个子 agent），说明这条命令能把超过 200K 的情况找出来。

## 3. reports/pro_switch_checklist.md（新）
共 22 步，每步一行命令（没有 `#` 注释）：预览 → `/logout` `/login` `/status` `/usage` `claude auth status` → `--apply` 并确认写入 → 重开会话 → `/context`、`/model opus`（不行就 `/model sonnet`）、`/doctor` 看 skill 描述有没有被截掉 → 看 config 的 cap_from_date，`python3 hooks/context_guard.py --mode`，`cat state/mode.json` 确认软线 ≤180000、强线 ≤195000 → `claude --version` → henchmen：让 agent 跑 `env | grep CLAUDE_CODE_...`，再扫一遍 11-21 之后的 transcript，看最大上下文有没有超过 200K → Fable 和按模型族的限额 → 撤回。
说明：`--mode` 只打印额度档。封顶是在处理消息的路径里做的，结果写在 `state/mode.json` 的 soft_line/strong_line，所以清单把它和 `cat state/mode.json` 放在一起。

## 4. model-router SKILL.md
备份在 `backups/model-router.SKILL.md.pre-r4E`。在"开与关"和"按剩余额度调节"之间加了一节"子 agent 怎么开更省"，共 5 行：启动税约 2 万 token、fork 的代价；父上下文 <8 万且需要父背景时优先 fork；≥8 万时不用 fork，自成一体的任务用新 agent 加便宜模型；细节写进文件，最终只回短报告；skill frontmatter 不写 `model:`。其他节一字没动，frontmatter 里也没有 `model:`。全文 4277 字节。

## 5. 待核实（脚本输出和清单里都已标出）
1. Pro 上 Opus 5.5 / Sonnet 5.5 默认是 200K 还是 1M。开了 DISABLE_1M 以后两种情况行为一样，所以不影响这个配置包。
2. settings.json 的 env 块能不能传到 henchmen agent（清单第 17、18 步）。
3. 11-21 以后 Pro 能不能用 Opus 5.5 / Fable，Fable 是否走 usage credits（第 11、19 步）。
4. 空闲压缩只在交互会话里触发，对 headless 的 henchmen agent 无效（文档已写明，这里只是提醒）。
5. 200K 窗口下 handoff、model-router 的描述会不会被截掉（第 12 步 `/doctor`）。

## 6. 撤回命令
- model-router：`cp ~/Documents/Projects/claude-usage-saver/backups/model-router.SKILL.md.pre-r4E ~/.claude/skills/model-router/SKILL.md`
- 两个新文件不想要的话：`mkdir -p ~/Documents/Projects/claude-usage-saver/_to_delete; mv ~/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py ~/Documents/Projects/claude-usage-saver/reports/pro_switch_checklist.md ~/Documents/Projects/claude-usage-saver/_to_delete/`
- settings.json 本轮没有改，不需要撤回。11-21 apply 之后要撤回：`python3 ~/Documents/Projects/claude-usage-saver/tools/pro_switch_1121.py --undo`

## 总结
1. 新建 pro_switch_1121.py：默认只预览；--apply 会先备份再写，并打印撤回命令；--undo 只撤自己改的键；11-21 之前要加 --force-early 才能写。
2. 内容：DISABLE_1M=1、IDLE_COMPACT=150000，autoCompactWindow 改成 200000（为 henchmen 拿不到 env 的情况兜底）。
3. 在副本上实测 8 个场景全部通过，真实 settings.json 没有改动；守卫 11-21 封顶已用伪造日期模拟验证（180K/195K）。
4. 新建 22 步当天清单；model-router 加了 5 行"子 agent 怎么开更省"，已备份为 .pre-r4E。
5. 5 项待核实留给 11-21 当天，主要是 henchmen 能不能拿到 env，以及 Pro 上 Opus/Fable 能不能用。

---

## E2（打回后，2026-11-06）
依据：reports/round4_codex_review_CEF.md 的"2．E"节，这是第 1 次打回。改之前先备份了 4 个文件：`backups/{pro_switch_1121.py,pro_switch_checklist.md,model-router.SKILL.md,round4_impl_E.md}.pre-r4E2`。真实的 settings.json 仍然没有改（测试 T13 逐字节比对过）。

### 改了什么
1. **再次 apply 时撤回记录没更新**（例如第二次加 `--subagent-cap 4`）：撤回记录改成"每个键单独记一条"（`keys.<键名> = {existed, prior, applied}`）。每次 apply 只记这次真正改动的键：新改的键补记原值；已经记过的键只更新 applied，原值保留最早那次的。undo 逐个键恢复：原来就有的键写回原值，原来没有的键删掉；用户事后改过的键保留不动并提示。旧版问题复现过：旧版"apply → apply --subagent-cap 4 → undo"之后，settings 里还剩 `env.CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS: "4"`；新版没有残留。
2. **第 18 步按文件 mtime 筛**：脚本新增只读子命令 `--scan-context --since 2026-11-21 [--transcripts 目录]`。它按每条记录自己的 `timestamp`（UTC 转成本地时间）跟本地零点比较；文件 mtime 只用来跳过"整个文件都更早"的文件，这一步不会漏掉新记录。清单第 18 步改成调用这个子命令，原来那条很长的 exec 单行命令删掉了。
3. **第 22 步**：补上整文件还原命令 `cp "$(ls -t ~/Documents/Projects/claude-usage-saver/backups/settings.json.*.pre-pro1121 | head -1)" ~/.claude/settings.json`。它还原到最近一次 apply 之前的版本；glob 的结尾是 `.pre-pro1121`，所以不会选到 `-undo` 备份。
4. **model-router 第 16 行和第 33 行冲突**：分派表那行改成"父上下文 <8 万：fork；≥8 万：全新 agent 并把背景写完整（以'子 agent 怎么开更省'那节为准）"；第 33 行改成"即使任务需要完整上下文也不用 fork"。两处现在说法一致，都只改了半句。

### 测试（实测，脚本 /private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/r4E2/test_e2.sh，结果 15/15 PASS）
| 编号 | 场景 | 结果 |
|---|---|---|
| T1–T6、T9、T10 | 原来的场景：dry-run 不写文件、提前 apply 被拒、apply、幂等、undo 后 JSON 相同、二次 undo 什么都不做、用户改过的键保留、坏 JSON | PASS |
| T7a/b/c | apply → apply --subagent-cap 4 → undo：cap 写进了 settings 和撤回记录，undo 后和原文件 JSON 相同，没有残留 | PASS（旧版在同一流程下残留 `"4"`） |
| T8 | 原来已有 FOO、IDLE=120000、MAX_CONCURRENT=8，两次 apply 后 undo | 恢复成和原来完全一样（IDLE 回到 120000，MAX_CONCURRENT 回到 8）PASS |
| T11 | "新文件里含旧记录"：mtime 是 11-23 的文件里有 11-10（90 万）、11-20（50 万）、11-21 18:00Z（15 万）三条，另一个文件有 11-22（12 万） | 只统计到 2 条，最大 150005，"没有超过 200000"。旧记录被排除了 PASS |
| T12 | 同一批文件，把 since 改成 11-01 | 找到 900005 PASS |
| T13 | 真实 ~/.claude/settings.json | 逐字节没变 PASS |
| — | 第 22 步的 cp 单行命令（zsh，在 scratchpad 里放了 3 个假备份） | 选中最新的 `.pre-pro1121`，跳过了 `-undo` 备份 |

另外用真实 transcript 只读跑了一次 `--scan-context --since 2026-11-06`：4277 条请求，最大 276317（一个子 agent）。说明这个子命令在真实数据上能跑通。

### E2 撤回命令
`cd ~/Documents/Projects/claude-usage-saver; cp backups/pro_switch_1121.py.pre-r4E2 tools/pro_switch_1121.py; cp backups/pro_switch_checklist.md.pre-r4E2 reports/pro_switch_checklist.md; cp backups/model-router.SKILL.md.pre-r4E2 ~/.claude/skills/model-router/SKILL.md; cp backups/round4_impl_E.md.pre-r4E2 reports/round4_impl_E.md`

### E2 总结
1. 再次 apply 时撤回记录不更新：改成按键记账，加 --subagent-cap 后 undo 没有残留（T7、T8）。
2. 第 18 步改为按记录 timestamp 筛选（新子命令 --scan-context），"新文件含旧记录"的用例通过（T11、T12）。
3. 第 22 步补上了整文件还原的 cp 单行命令，已在 zsh 里实测。
4. model-router 的 fork 规则统一成"<8 万 fork，≥8 万不 fork"，分派表注明以新节为准。
5. 15/15 测试通过，真实 settings.json 没有改动；待核实项和第一版相同。

---

## E3（第 2 次打回后，2026-11-06）
依据：reports/round4_codex_review_E2.md 末尾的复核结论和协调者的分诊（必须修 a–f；只需拒绝并提示的 g、h）。改之前先备份了：`backups/{pro_switch_1121.py,pro_switch_checklist.md,round4_impl_E.md}.pre-r4E3`。model-router 这次没有改。真实 ~/.claude/settings.json 前后 sha1 都是 1464f8e4…，一个字节没动。

### 先复现（对 E2 代码 backups/pro_switch_1121.py.pre-r4E3 实跑，结果 3/15 PASS）
测试脚本是 /private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/r4E3/test_e3.py，它在进程内调用 main()，所有 settings 都是临时目录里的假文件。为了复现"同一秒"，把时间冻结在同一微秒。旧版的输出保存在同目录的 repro_old.txt。
| 项 | E2 代码的实际表现 |
|---|---|
| a 同秒连续 apply | 只剩 1 个备份文件，内容已经是第一次 apply 之后的版本，原始备份被覆盖 |
| b 第 22 步 | 没有 --show-backup；原 settings 的 mtime 设成 2030 年时，`ls -t` 选中较早那份，不是"最近一次" |
| c 旧 mtime 文件里的新记录 | 0 条，最大 0（漏掉了 300001） |
| d 坏 usage | `usage=[]` 时抛 `AttributeError`，整次扫描中断 |
| e A、B 共用备份目录 | B 被 apply 了；undo 时 B 被写成 A 的原值 300000 |
| f env 非对象，或被整个删除 | env 原来是 null、"x"、[1] 时都被覆盖；env 被删后 undo 插回 `"env": {}`；env 被改成 null 后 undo 把它换成 {} |
| g 符号链接 | apply 后链接变成了普通文件 |
| h E1 格式的 state | apply 和 undo 都抛 `KeyError: 'keys'` |

### 改了什么（tools/pro_switch_1121.py）
1. **(a) 备份不再覆盖**：新函数 `unique_backup()`。文件名带微秒和 pid（例如 `settings.json.20261121-100000-123456-p84707.pre-pro1121`），仍重名就加 `-1`、`-2`；用 O_EXCL 创建，已存在的文件绝不覆盖。不再用 copy2，备份的 mtime 就是备份的时间。undo 前的备份和 `.undone-*` 记录文件也用同样的命名。
2. **(b) 只读子命令 `--show-backup [--which first|last]`**：按 state 里记的路径选备份，不看 mtime。stdout 只打印一个路径，提示信息都写到 stderr。没有记录、备份文件不存在或不是合法 JSON 时返回 1，什么都不打印，所以 `p=$(...) && cp "$p" ...` 不会执行 cp。当前没有生效的记录时，会用最近一份 `.undone-*` 记录（按文件名里的时间排序）。它也会校验 settings 路径。
   - first（默认）= 本轮第一次 apply 之前的原始文件；last = 最近一次 apply 之前的文件。只 apply 过一次时两者相同。
   - 清单第 22 步改成 `p=$(python3 …/pro_switch_1121.py --show-backup) && cp "$p" ~/.claude/settings.json`，并写清了两者的区别，另附一条 `--which last` 的命令。apply 打印的整文件还原命令也改成这一条。
3. **(c) 扫描全量读**：去掉了按 mtime 跳过文件。真实 transcript 有 918 个文件、955MB，只读全量扫一遍用 2.7 秒。
4. **(d) 每条记录单独 try**：`usage_total()` 要求 usage 是对象、三个字段是非负整数（不接受 "100"、null、true），否则算坏记录，跳过并计数。输出多了"读了 N 个文件"和"坏记录 N 条"两行。早于截止日的记录不检查格式，也不计入坏记录。
5. **(e) state 路径校验**：apply、undo、show-backup 都用 realpath 比较 state 里记的 settings 路径和当前的 `--settings`。不一致就拒绝（返回 3），并提示"用 --settings 原文件，或者换一个 --backup-dir"。拒绝发生在创建备份之前，所以不会留下多余的备份文件。
6. **(f) env 的处理**：
   - env 存在但不是对象时，dry-run 和 apply 都拒绝（返回 1），提示先手动改成对象或删掉。
   - undo 时如果整个 env 已被删除，或者被改成非对象，所有 env.* 键都保留不动，也不插回 env。
   - 补了同类的一种情况：原来有 env → apply → 用户删掉 env → 再次 apply → undo。现在不会留下空的 `"env": {}`：再次 apply 时如果 env 不存在，会把 env_existed 记为 False。
7. **(g) 符号链接**：settings 是符号链接时，apply 和 undo 都拒绝（返回 3），并提示改用 `--settings <链接指向的真实文件>`。dry-run 不受影响。真实的 ~/.claude/settings.json 是普通文件（已用 ls -la 确认）。
8. **(h) E1 格式的 state**：有 state 但没有 keys 时，apply 和 undo 都拒绝（返回 3），不会抛 KeyError。提示里说明 E1 从没在真实 settings 上 apply 过，并给出手动还原的单行命令（`--show-backup` 可以读 E1 的 first_backup），再让用户把 state 改名移走。backups/ 里没有任何 pro_switch 的 state 文件，所以真实环境不会遇到这种情况。
9. 顺手改了一处：apply 现在先写 state 再写 settings，两者都是写临时文件再改名。这样中途失败时，undo 只会"跳过并提示"，不会改错。

### 测试（实测）
**新用例：test_e3.py，结果 15/15 PASS**（输出在 result_new.txt）。
| 编号 | 场景 | 结果 |
|---|---|---|
| Ta | 冻结在同一微秒连续 apply 两次 | 2 个备份文件，其中一份是原始内容 PASS |
| Tb | 原 settings 的 mtime 是 2030 年，apply 两次 | first 指向原始内容，last 指向第一次 apply 之后的内容 PASS（同一场景下 `ls -t` 选错） |
| Tb2 | 第 22 步单行命令在 zsh 里实跑（假路径） | 还原后和原始文件逐字节相同 PASS |
| Tb3 | 没有记录时运行第 22 步单行命令 | 返回非 0，没有 cp，没有 Traceback PASS |
| Tc | 文件 mtime 是 2020 年，里面有 11-22 的记录 300001 | 找到 300001 PASS |
| Td | usage 分别是 []、"100"、null、true，加 1 条正常记录和 1 条早于截止日的记录 | 不崩；最大 5007；坏记录 4 条；早于截止日的 999999 没被计入 PASS |
| Te | A、B 共用备份目录 | B 的 apply 被拒，B 没变；B 的 undo 被拒，没有被写成 A 的值；A 的记录还在 PASS |
| Tf1 | env 分别是 null、"x"、[1] | 都被拒，文件没变 PASS |
| Tf2 | apply 后用户删掉整个 env，再 undo | 没有 env 键，autoCompactWindow 回到 300000 PASS |
| Tf3 | apply 后用户把 env 改成 null，再 undo | env 仍是 null，没被覆盖 PASS |
| Tf4 | apply → 删 env → 再 apply → undo | 没有空 env PASS |
| Tg | settings 是符号链接 | apply 和 undo 都被拒，仍是链接，目标文件没变 PASS |
| Th | 先用 E1 代码 apply，再用新版 apply 和 undo | 都被拒，提示里有"E1"，没有 KeyError；--show-backup 给出原始备份 PASS |
| Treg | 原来已有 IDLE=120000、MAX=8：apply → apply cap 4 → 用户加了一个自己的键 → undo → 再 undo | 恢复原值，用户的键保留，第二次 undo 什么都不做 PASS |
| Treal | 真实 settings.json 的 sha1 | 没变 PASS |

**E2 原来的 15 个用例回归：15/15 PASS。** 说明一点：我在同一个目录里重跑 E2 脚本时，T4 失败过一次。原因是那个脚本的 `new()` 不清理上次留下的 bk 目录，T4 数到了上一轮的备份文件，不是代码的问题。把旧目录 mv 到 e2reg.run1 后，在干净目录里重跑是 15/15。
**清单命令**：其中 15 条真正的命令都通过 `zsh -n` 语法检查。被跳过的 4 段是正文里的引用文字或单独的参数，不是命令。
**真实数据（只读）**：`--scan-context --since 2026-11-06` 读了 918 个文件，4740 条请求，最大 276317（一个子 agent），坏记录 0，耗时 2.7 秒。`--show-backup` 对真实 backups 目录返回 1，提示"没有本脚本的记录"，和预期一致。dry-run 只预览，没有写文件。

### E3 撤回命令
`cd ~/Documents/Projects/claude-usage-saver; cp backups/pro_switch_1121.py.pre-r4E3 tools/pro_switch_1121.py; cp backups/pro_switch_checklist.md.pre-r4E3 reports/pro_switch_checklist.md; cp backups/round4_impl_E.md.pre-r4E3 reports/round4_impl_E.md`

### E3 总结
1. 必须修的 a–f 都先在 E2 代码上复现了失败，修好后各有对应用例通过。g、h 现在会明确拒绝并提示，不会再出错或抛 KeyError。
2. 第 22 步不再靠 `ls -t` 猜备份：路径记在 state 里，用 `--show-backup` 读出来；默认是"最初那份"，也可以用 `--which last` 取"最近一次"，两者的区别已写进清单。
3. 扫描改成全量读、逐条容错，真实数据 2.7 秒跑完。
4. 新用例 15/15，E2 回归 15/15，真实 settings.json 没有改动。待核实项和第一版相同。
