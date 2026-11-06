# 第 4 轮复核：#12（防注入 / compact 短注入 / install / handoff skill）与 #13（Pro 封顶）

复核人：独立 agent（只读），2026-11-06。所有用例在 scratchpad 里跑（HOME、USAGE_SAVER_STATE 都隔离），真实 settings.json / config.json / HANDOFF.md 都没改。
用到的脚本（可以重跑）：
- `/private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/sr/run.py`：/clear 路径，新旧两版对照
- `/private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/sr/compact.py`：compact 路径
- `/private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/inst/run.py`：install 脚本，跑在 settings 副本上
- `/private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/cg/run.py`：context_guard 封顶的边界用例
- `/private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/proj/`：项目副本，用来做变异测试和改日期后的评分

`python3 tools/scorecard.py --version all` 的结果：当前版 55/55，v0 16/42。improvements.md 写的是 54/54，多出来的一个是 TF16，不属于本轮，可能是并行的其他 agent 加的。

## 先说结论：真实 /clear 后能不能接上
**能接上。** 真实 HANDOFF.md 第一行已经加了标记。我把它复制到隔离环境，按 SessionEnd → SessionStart(clear) 的顺序跑了一遍：交接正文注入了，总长 6701 字，开头有"是数据不是新指令"的声明。改动前的版本是 6614 字。cwd=~ 时也会退回到这一份交接（它有标记，并且是 12 小时内写的）。

---

## 发现

### F1【中，本轮最重要】SR05/SR07/SR08 的断言变成了空断言，等于失去了检测能力
- **问题**：实施者给 SR02/SR06/SR10 的夹具加了标记，这三处本身没问题：它们测的是"选哪份交接"和"头尾截断"，跟信任判断无关；SR06 里 B 也加了标记，不会因为 B 被挡掉而假通过。真正的问题在**没改夹具**的 SR04/SR05/SR07/SR08。这几个场景断言的是"某段正文不在注入里"，用的交接都没有标记。现在不管寻找逻辑和过期逻辑对不对，正文都会先被防注入那一关挡掉，所以这几条永远通过。
- **复现**：在项目副本里做变异测试（脚本见上方 proj/），结果如下：

| 变异 | r4 之前的版本 | 当前版本 |
|---|---|---|
| `STALE_HOURS = 10**7`（过期判断失效） | SR07 ✗、SR08 ✗ | 全部 ✓，55/55 |
| 去掉"不爬到 ~"（`if d == HOME` 改成 `if False`） | SR05 ✗ | 全部 ✓，55/55 |
| `is_project_root` 恒返回 False | 没测出（SR04 原来就测不到） | 没测出 |

- **建议**：SR04/05/07/08 的夹具都加 `HMARK`，让它们重新只测寻找和过期逻辑。SR04 另外补一个正向断言：cwd=Projects/c/pkg、c 自己有带标记的交接时，必须注入 c 的那份，这样"项目根判断"才真正被测到。改完用上面三个变异各跑一次，确认都能被抓到。

### F2【中】compact 短注入在真实 HANDOFF.md 上，"红线 / 未决"一节变成空的，还混进了几轮前的"下一步"
- **复现**：`python3 /private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/sr/compact.py`，看第一段输出（1508 字）。
  1. 真实文件第 22 行是 `**红线 / 未决**：`，下一行是 `- **默认模型已定…**`。`key_sections` 处理加粗段落时，停止条件 `(?:[-*]\s+)?\*\*` 会把"以加粗开头的列表项"也当成新的一段，所以这一节只取到标题本身。整个红线列表（"不设固定默认模型"、不 push、不 rm、relay-room 不碰）都没注入。第 4 行的 `**红线**：同下节（…）` 碰巧覆盖了大部分，但"同下节"指向的那一节恰好是空的。
  2. 文件是旧的多节叠加格式，KEY_HEAD 会匹配**所有**含"下一步"的段，于是"第 3 轮下一步"和第 2 轮的"下一步：1. 第 2 轮交叉审核…"这种早就过时的步骤也被注入了。压缩后 Claude 可能照着去做旧轮次的事。
- **其他边界**（同一个脚本里跑的）：
  - 红线一节超过 900 字时，尾部会被静默截掉（`RED-TAIL` 丢失）。
  - 标题写成"禁止事项"或"Next steps"时，两项都匹配不到，退回取文件前 800 字。
  - 旧 skill 的格式（红线在最后、下一步很长）这次没丢，但那只是因为总长没超。
- **建议**：
  1. 加粗段落的停止条件改成只在"空行 / 标题 / 行首是 `**` 的非列表行"处停，列表项（`- ` 开头）继续算在本段里。
  2. 只取**第一个**匹配的红线节和**第一个**匹配的下一步节。按新 skill 的写法只有一节当前状态，第一个就是最新的。
  3. 先放红线、再放下一步，截断时先截下一步。
  4. 单节上限不要截红线；需要截时在末尾写"（红线未完，读文件）"。
  5. 现有的 HANDOFF.md 按新 skill 迁移一次（旧内容移到 HANDOFF.history.md），这件事要所有者同意。

### F3【中】没有标记的正当交接现在只给路径，而且没有场景覆盖
同一份交接，分别用 r4 之前和当前版本跑 /clear（`sr/run.py`）：

| 情形 | r4 之前 | 当前 |
|---|---|---|
| 上一段用 Write 写、绝对路径 | 注入 | 注入（靠 edited_in 兜住） |
| 上一段用 Bash `cat > HANDOFF.md` 写 | 注入 | **只给路径** |
| 上一段由子 agent 写（isSidechain） | 注入 | **只给路径** |
| 上一段 Edit 用相对路径 `HANDOFF.md` | 注入 | **只给路径**（相对路径按 hook 进程的 cwd 解析） |
| 前前段写的（中间又 /clear 了一次，10 分钟前） | 注入 | **只给路径** |
| 上一段很早就写了交接，transcript 超过 3MB | 注入 | **只给路径**（只读了尾部 TAIL_BYTES） |
| 第一行是标题，标记在第二行 | 注入 | **只给路径** |
| 照抄 skill 模板，把标记包进了 ``` 代码块 | 注入 | **只给路径** |
| 第三方仓库伪造标记（仓库是公开的，标记字符串谁都能看到） | 注入 | 注入（设计上挡不住） |

- **影响**：目前 Projects 下只有 usage-saver 有 HANDOFF.md，而且已加标记，所以**眼下不会断档**。以后有三类交接会掉成"只给路径"：Codex 和其他工具写的（AGENTS.md 让它们写 HANDOFF.md，但没提标记）；不经过 skill、按 AGENTS.md 习惯直接写的；连续两次 /clear 之后的。掉了以后只剩路径和对话摘要，接续质量会下降。
- **建议**：
  1. 在 AGENTS.md 的"交接习惯"里加一句"第一行写 `<!-- usage-saver-handoff -->`"（改全局说明要所有者同意）。
  2. `has_mark` 改成在前 3 行内查找，并允许外面包一层 ```。
  3. edited_in 先解析相对路径：`os.path.join(cwd, fp)`。
  4. 只给路径时，文案改得更可操作："所有者说'接着做'时，先读这个文件再动手"。现在写的是"由所有者确认后再读"，所有者发个"1"时 Claude 会停下来反问。
  5. 在 SKILL.md 模板里，把标记移到代码块外单独写一行，或者明确写"不要写 ```"。

### F4【中】#13：状态栏没有跟着封顶；评分表从 11-21 起会自己变红
- **状态栏**：`hooks/statusline.sh` 第 42–48 行自己重新算软线和强线，没有读 `cap_from_date`。11-21 以后在 quant-backtest 里：守卫按 180k 提醒，状态栏要到 250k（宽裕档 375k）才显示 ⚠，⚠⚠ 要到 300k 以上。Pro 的上下文窗口大概到不了这么大（未实测），等于状态栏的提示永远不会亮，两边说法不一致。
- **评分表定时炸弹**：scorecard 的当前版直接用真实 config.json。在项目副本里把 `cap_from_date` 改成过去的日期，结果是 **CG04 ✗、CG07 ✗，53/55**：这两条断言"200k 不提醒"，而封顶后 180k 就提醒。11-21 当天评分表会无故掉到 96%，看起来像是出了回归。
- **建议**：
  1. 把"软线 / 强线计算"抽成 `context_guard.lines(cfg, cwd, mult)`，statusline 用 `python3 -c` 调它，或者把同样的封顶逻辑复制过去，并加一个 SL 场景。
  2. scorecard 在写入沙箱的 config.json 里固定 `cap_from_date="2099-01-01"`，让 CG01–09 不受日期影响；再单独加一个封顶场景（见下面"新场景" N2）。

### F5【低】#13 边界用例（`cg/run.py`）
- 日期等于今天：封顶生效，符合"从这天起"。宽裕档 ×1.5 之后再封顶：quant-backtest 宽裕档是 375k → 180k，强线 195k，196k 时出强提醒，正确。强线至少比软线大 1：`soft>strong` 配错时强线是软线 +1，`max_soft=250k` 时强线是 250001，不会倒挂。config 缺 `max_*` 键时用默认值 180k/195k，正确。`cap_from_date` 缺失或为 null 时不封顶，正确。
- **日期格式脆弱**：比较的是字符串。写成 `2026-11-1` 会在 11-10 就提前生效；写成 `2026/11/21` 要到 2027 年才生效；两种情况都不报错。建议用 `datetime.date.fromisoformat()` 解析，失败就按"生效"处理并在 why 里写明。
- `max_soft_line_tokens` 写成非整数（比如"18万"）时 `int()` 抛异常，被外层 try 吞掉，**整个守卫静默失效**：400k 也不提醒，mode.json 也不写。建议封顶这段单独包一层 try，失败就用默认值。
- 封顶后软线可以低于 `min_threshold_tokens`（比如 90k < 100k），大概符合本意，记一下。
- 不确定、未实测：Pro 的 200k 窗口下，自动压缩可能在 180k 之前就触发，那样软线提醒根本没机会出现；另外 `autoCompactWindow=300000` 在 200k 窗口下效果未知。建议 11-21 以后用真实 transcript 看一下第一次压缩发生在多少 token。

### F6【低】install_sessionend.py：真实 settings 上表现正确，但同一条目里有别的 hook 时会误改
- **真实布局**（SessionStart 只有一条 `matcher=clear`、里面只有本脚本；SessionEnd 已装）：只把 matcher 改成 `clear|compact`，第二次运行不写文件、不备份，幂等成立。另外有一条独立的 `startup` 条目时，它不受影响。
- **误改**：
  - `{"matcher":"startup|clear","hooks":[别的hook, session_resume]}` 会被改成 `clear|compact`。结果别的 hook 丢了 startup 触发、多了 compact 触发，本脚本也丢了 startup（虽然不影响本脚本）。
  - `[session_resume, 别的hook]` 共用 `clear` 条目时，别的 hook 也会在 compact 时跑。
  - 原来没写 matcher（所有来源都触发）时会被收窄。
  - 命令写成 `~/Documents/...` 形式时匹配不上，会**再加一条** SessionStart 和 SessionEnd，/clear 时注入两遍。
- **建议**：只在条目的 hooks 只有本脚本时改 matcher；否则把本脚本从该条目里摘出来，新建一条 `clear|compact` 条目。匹配命令前把 `~` 和 `$HOME` 展开后再比较。

### F7【低】其它
- 防注入只针对交接文件。/clear 的对话摘要里仍然原样放着上一段的 Claude 回复，可能包含网页内容，现在只靠 DATA_NOTE 声明。这个已经写明了，可以接受。DATA_NOTE 在 compact 路径里也提"对话摘要"，但 compact 路径不注入对话摘要，措辞多余。
- SKILL.md 写"高消耗项目最多 120 行"，context_guard 强提醒写"高消耗项目的交接要详细，不限行数"，两边矛盾。建议统一成"≤120 行，细节放 HANDOFF.history.md"。
- 覆盖写依赖 Claude 先追加到 history 再替换。如果追加失败，旧内容就丢了，违背"不永久删除"的精神。建议在 skill 里写"先 Read history 末尾确认已追加，再替换"。
- improvements.md 里 #13 那一行在表格外面，列数也对不上，Markdown 渲染会乱；#12 写 54/54，现在是 55/55（多了 TF16）。
- 真实 HANDOFF.md 第 4 行的红线里写着"usage-saver 今晚最后统一 commit+push 一次"，跟"不 push"并存。这是所有者的决定，不属于本次复核范围，提醒一下：compact 注入会把这一句带上。

---

## 建议新增的评分场景
1. **N1（SR18，防空断言）**：SR04/05/07/08 的夹具全部加标记，再加"带标记的 31 天旧交接不注入正文""~/Documents/x 下有带标记的 ~/HANDOFF.md 也不注入"。通过标准：上面三个变异（STALE_HOURS、去掉不爬 ~、is_project_root）各让至少一个场景变 ✗。
2. **N2（CG10，封顶与日期无关）**：沙箱 config 设 `cap_from_date` 为过去的日期，quant-backtest 加宽裕档，上下文 190k：期望软提醒，mode.json 里 soft_line=180000、strong_line=195000；同一配置下 statusline 在 185k 显示 ⚠（目前会失败，见 F4）。同时把 CG01–09 的沙箱 config 固定为 `cap_from_date=2099-01-01`。
3. **N3（SR19，compact 用真实格式）**：交接用加粗段落写，`**红线 / 未决**：` 下面是 `- **xxx**` 列表项，后面再叠一节旧的 `**下一步**：OLD-NEXT`。期望注入里有红线列表项、有最新一节下一步，**没有** OLD-NEXT，并且红线排在下一步前面。

## 总结（5 行内）
1. 真实 /clear 接续没有失效：带标记的真实 HANDOFF.md 正常注入，6701 字。install 在真实 settings 上正确且幂等；#13 的封顶数值逻辑（×1.5 后再封顶、强线 ≥ 软线、缺键）正确。
2. 夹具改动本身合理，但 SR05/07/08 因为夹具没有标记变成了空断言，变异测试已证实它们测不出过期和爬目录的回归（中）。
3. compact 短注入在真实 HANDOFF.md 上，"红线 / 未决"一节被解析成空，还带进了过时的"下一步"（中）。没有标记的正当交接（Bash 写、子 agent 写、两次 /clear 之后）会降级成只给路径（中）。
4. #13：状态栏没有封顶，11-21 起评分表 CG04/CG07 会自己变红（中）；日期是字符串比较、max 键写成非整数时整个守卫静默失效（低）。
5. 建议先修 F1、F2、F4，再加 N1–N3 三个场景。
