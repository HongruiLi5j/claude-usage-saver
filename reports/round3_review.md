# 第 3 轮改动独立复核（2026-11-06 02:30–02:45，只读）

复核人：独立 Code Reviewer 子 agent。除本报告外未改任何文件。
范围：improvements.md #6–#11 涉及的 `hooks/test_filter.py`、`hooks/statusline.sh`、`hooks/context_guard.py`、`hooks/session_resume.py`、`tools/weekly_compare.py`，对照 backups/ 下的改前版本。

**怎么测的（实测，不是模拟）**
- test_filter：在 scratchpad 造了一个小项目（含中文带空格目录、一个通过的测试、一个失败的测试、一个 40/3000 个失败的参数化测试、一个 package.json），用 quant-backtest 的 `.venv` 里的**真实 pytest**（只借用解释器，没有在 quant-backtest 目录里跑任何东西）。写了对比脚本：同一条命令「原样」和「经 `rewrite()` 改写后」各在真实 `bash -c` 里跑一遍，比退出码和输出。共 67 条命令。脚本和用例：`/private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/harness.py`、`cases1.json`、`cases2.json`（同目录）。
- 另做了 1 次**真机**检查：在我自己的 Bash 里跑 pytest，确认已安装的 hook 在 Claude Code 2.1.316 上确实生效（updatedInput 被采用）。副作用：hook 照常在 `state/testlogs/` 留下 1 个日志 `run-LLXODK`。
- statusline / context_guard：用假 HOME（scratchpad/fakehome）喂造好的 payload 跑真实脚本。
- scorecard：只跑了 `--only`（54 个 ID，当前版 54/54 通过），没跑全量。
- **注意**：复核期间 `hooks/session_resume.py`（02:32 又改过，已含第 6 项防注入/第 11 项 compact）、`tools/scorecard.py`、`reports/improvements.md` 都在被别的会话同时修改。我复核的是 02:32 的快照（sha1 见文末）。

---

## 总评

- 第 3 轮方向是对的：test_filter 从「整条命令包起来」改成「只包测试那一段」，修掉了 TF07–TF15 那一批误包装、吃输出、退出码错的问题；我测的 67 条里，单行的常见写法（`cd x && pytest`、`pytest -k "a and b"`、`FOO=1 BAR="x y" pytest`、中文带空格路径、引号里的 `;`、行尾注释、`pytest; exit 3`、`set -e`、`||`/`&&` 链）**退出码全部与原命令一致**。看不懂就放行的保守策略也基本守住了（管道、重定向、`$(...)`、`case`、`$'...'` 都放行）。
- statusline 的合并写入、context_guard 的周重置判断、weekly_compare 按启用时刻切分都是对的修法，代码也清楚。
- **但评分表 100% 掩盖了两个会把正常命令弄坏的 test_filter 问题**（下面 🔴1、🔴2），都是真实 bash 复现的。原因是评分表的 TF 场景全是单行命令、用的是假 pytest、没有 `pipefail`。

| 严重度 | 数量 | 摘要 |
|---|---|---|
| 🔴 阻断 | 2 | 多行 if/for/while/{ } 里的测试被改出语法错误；`set -eo pipefail` 下测试通过也返回 1、输出全空 |
| 🟡 建议 | 7 | mktemp 失败时测试根本不跑；pipefail + 大量失败 → 141；超时时看不到日志路径；短输出被重复一遍；过期的 5h 窗口被当成新数据 → 误判「快用完」；session_resume 在 Projects 外的项目里拿别的项目的交接；HANDOFF 说明与代码状态不一致（MARK 还没进 handoff skill） |
| 💭 小问题 | 6 | 见各节 |

---

## 一、hooks/test_filter.py（风险最高）

### 🔴1 多行控制结构里的测试命令被改出语法错误，测试根本没跑

**复现**（`\n` 是真换行，Claude 写多行命令时很常见）：
```
if [ -d tests ]; then
  pytest -q tests/test_ok.py
fi
```
同样会坏的：`for f in ...; do⏎ pytest $f⏎ done`、`while ...; do⏎ pytest⏎ done`、`if false; then echo a⏎ else⏎ pytest⏎ fi`、`{⏎ pytest⏎ }`。

**期望**：照常跑测试，rc=0（或者放行不包装）。
**实际**：rc=2，输出
```
bash: -c: line 0: syntax error near unexpected token `;'
bash: -c: line 0: `if [ -d tests ] ; then ; { __usL=$(mktemp ...
```
**原因**：`split_segments` 把换行当成 `;`，于是 `then⏎` / `do⏎` / `else⏎` / `{⏎` 变成 `then ;` / `do ;` / `else ;` / `{ ;`——这在 bash 里是语法错误。只要命令里任何一段是测试，整条都会被改写，所以整条命令都坏了（不只是测试那段）。单行写法 `if x; then pytest; fi` 没事，是因为 `then pytest` 这一段的第一个词是 `then`，不会被识别为测试、整条放行。
**为什么严重**：这是改写**所有** Bash 命令的 hook，Claude 拿到语法错误后通常会换写法重试，浪费一次请求和上下文，正好违背省额度的目的；在 CI 风格的多行脚本里很常见。
**建议改法**（任选，第一种最简单）：
- 在 `split_segments` 返回前检查：如果有任何一段以 bash 保留字开头或就是保留字（`if then else elif fi for while until do done case esac { } function select !`），直接 `return None` 放行。
- 或者：分隔符记住原来是换行还是 `;`，拼回去时换行仍用 `\n`（`then⏎{ ...; }` 是合法的）。这样还能包装 then/do 里的测试。但要同时处理「段本身就是保留字」时不要在它后面加 `;`。

### 🔴2 `set -eo pipefail` 下测试**通过**也返回 1，而且什么都不打印

**复现**：`set -eo pipefail; pytest -q tests/test_ok.py; echo AFTER-OK`（Claude 写脚本时常加 `set -euo pipefail`）
**期望**：打印「2 passed」，rc=0，`AFTER-OK` 打印。
**实际**：rc=1，输出为空（连「结尾」和日志路径都没有），后面的命令也没执行。
**原因**：测试通过时日志里没有 FAIL/ERROR 等字样，`grep ... | head -120` 里 grep 返回 1；开了 pipefail 后整条管道返回 1，`set -e` 立刻退出 shell——退在打印摘要之前。Claude 看到「空输出 + exit 1」会以为测试失败或环境坏了。
**建议改法**：wrap 里的 grep 管道加兜底：`{ grep -nE ... "$__usL" | head -120; } || true`（`tail`/`wc` 那几句同理加 `|| true`），只让最后的 `(exit $__usrc)` 决定退出码。

### 🟡3 `pipefail` + 失败输出很大时返回 141，摘要和结尾丢失

**复现**：`set -eo pipefail; pytest -q tests/test_many.py; echo nope`，其中 test_many 有 3000 个失败（grep 输出 > 64KB）。
**期望**：rc=1，打印失败片段、结尾和日志路径。
**实际**：rc=141（SIGPIPE），只打印了 head 的前几十行，没有「结尾」和日志路径。40 个失败时不会触发（管道缓冲还装得下）。
**建议**：同 🔴2 的 `|| true` 一并修掉。

### 🟡4 mktemp 失败时测试根本不跑

**复现**：日志目录不可写或不存在（例如以后开了 Claude Code 沙箱、只允许写当前项目；或 state/ 被移走而 hook 进程与执行进程之间目录被删）。我用一个不存在的 logdir 调 `rewrite("cd tests && pytest -q test_ok.py && echo RAN", ...)`。
**期望**：至少原样把测试跑了（不过滤也行）。
**实际**：`mktemp: mkstemp failed`，`>""` 重定向失败 → **pytest 没执行**，rc=1，`echo RAN` 不执行，提示行变成「完整  行在  」。
**建议**：`__usL=$(mktemp ...) || __usL=`；`if [ -z "$__usL" ]; then { seg; }; else ...包装...; fi`。现在 hook 里的 `os.makedirs(LOGDIR)` 只保证 hook 进程那一刻目录存在，不保证命令执行时可写。

### 🟡5 测试超时/被杀时 Claude 什么都看不到，也不知道日志在哪

**复现**：`npm test -- --watch`、`pytest --pdb`、`node --test --watch`，或任何超过 Bash 工具超时（默认 2 分钟）的测试。
**期望**：至少能看到已产生的输出或日志路径。
**实际**：所有输出都在文件里，进程被杀后包装脚本的后半段（含日志路径那句）不会执行，Claude 看到的是空输出 + 超时。不包装时 Claude 至少能看到超时前流出的输出。
**建议**：(a) 在跑测试**之前**先 `echo "[测试输出写入 $__usL]"`；(b) 命令含 `--watch`、`-w`（jest/vitest）、`--pdb`、`--looponfail`、`ptw` 时放行不包装。

### 🟡6 短输出被重复打印一遍，反而更费 token

**复现（真机）**：在我自己的 Bash 里跑 1 个失败测试，pytest 原输出 12 行；经 hook 后 Claude 看到约 27 行：grep 的 -B2 -A12 已经把 12 行全打出来了，「结尾」又把同样 12 行打一遍。
**期望**：输出很短时原样给出，不重复。
**建议**：`if [ "$(wc -l <"$__usL")" -le 40 ]; then cat "$__usL"; else grep…; tail…; fi`。阈值 40 左右（grep 上下文 + 结尾大约就这么多）。

### 💭7 识别范围的漏网（不会弄坏命令，只是没省到）

以下都**放行**（rc 与原命令一致，只是没过滤）：`time pytest`、`timeout 60 pytest`、`env FOO=1 pytest`、`xargs pytest`、`uv run pytest`、`poetry run pytest`、`npm run test:unit`、`npm t`、`yarn/pnpm test`、`npx jest/vitest`、`go test`、`cargo test`、`! pytest`、`echo a && \⏎pytest`（续行后的第一个词被 shlex 读成 `\npytest`）。
建议：如果想覆盖，跳过前缀词 `time env nice timeout(+数字) command exec uv run poetry run`；`npm run test:*`。但每加一个都要同时加反例场景。不急。

### 💭8 小误包装
- `python script.py -m pytest`（把 `-m pytest` 当参数传给脚本）会被包装——判断 `-m` 时应只看脚本名之前的选项（第一个不以 `-` 开头的参数之前）。实测不影响退出码。
- `node app.js --test` 同理会被包装。
- `"<<" in cmd` 也会让 `echo "a << b"; pytest` 放行——保守，没问题。

### 💭9 其他
- macOS 的 `wc -l` 输出带前导空格，提示行显示成「完整       12 行」，可用 `$(($(wc -l <"$__usL")))` 去空格。
- testlogs 只增不删（目前已有若干，D 类决定里也提到），建议加「保留最近 N 个/7 天」。

### 做得好的地方
- 引号内的 `;`、`"he said \"hi; there\""`、单引号里的反斜杠、行尾注释、`&&` 后换行、反斜杠续行都处理对了。
- `{ seg⏎}` 用组命令而不是子 shell，`cd` 能留在后续段生效；`&& rc=0 || rc=$?` 加 `(exit $rc)` 的写法在 `set -e` 下也先打印再退出，这一点很巧。

---

## 二、hooks/statusline.sh

### 🟡10 payload 里**已过期**的 5h 窗口被当成新数据写入，带新 ts → context_guard 误判「快用完」

**复现**（假 HOME，rate.json 为空）：payload `five_hour={used:95, resets_at: 1 小时前}`, `seven_day={used:50, resets_at: 明天}`。
**期望**：过期的 5h 读数不写入（或不刷新 ts）；`context_guard --mode` 按 7d 判档（50% → 宽裕/正常）。
**实际**：rate.json 写入 `five_hour 95%`、`ts=now`；状态栏显示 `5h 95%`；`context_guard --mode` 输出 `快用完 0.6 周用量 50%,5小时窗口 95%`，mode.json 软线被压到 100k。之后每 5 秒刷新一次：旧读数虽然因为过期被作废（`b={}`），但马上又从 payload 写回并刷新 ts，所以会一直保持。
**原因**：合并逻辑只检查 rate.json 里旧读数 `b` 是否过期，没检查本会话 payload 的 `a` 是否过期；context_guard 也只对 seven_day 检查了 `resets_at <= now`（CG06 的修法），5h 没有。
**前提要真机确认**：空闲很久的会话，Claude Code 是否继续在 payload 里带上一次请求时的 rate_limits（我推测是，因为它来自最后一次 API 响应）。但两处防御都只要一行。
**建议**：statusline 里 `if a and (a.get("resets_at") or 0) <= now: a = {}`；context_guard 里 `fh` 同样 `resets_at <= now` 时当作 0。

### 💭11 项目软线按子串匹配
`if k in cwd`：`quant-backtest-old`、`quant-backtest2` 也会用 quant-backtest 的 250k（实测 200k 时不显示 ⚠）。context_guard 是同一写法，两边至少一致。建议按路径段匹配：`k in cwd.split(os.sep)`。

### 💭12 并发读改写无锁
两个会话同时刷新可能互相覆盖（读到同一个旧值），但下一次刷新会自愈，影响很小，可不改。

### 做得好的地方
同一窗口取较大值、旧窗口作废、没新数据不刷新 ts——三条规则把 A-1「84% 被写成 16%」的根因修掉了；❄ 只显示不注入，不会破坏缓存。

---

## 三、hooks/context_guard.py

- 第 9 项（400KB → 4MB → 16MB 逐步扩大读取）逻辑正确，先用 `'"usage"' not in line` 粗筛再解析，性能没问题。
- 🟡 同 🔴10 的另一半：`fh.get("used_percentage") >= 90` 之前没检查 `fh.resets_at`。
- 💭13 每次提交都把 mode.json 写成「本会话的软线」，但 mode.json 是全局文件；statusline 现在只读其中的 `mult`，这是对的。建议把 `soft_line/strong_line` 字段删掉或注明「仅供调试、属于最后写它的会话」，免得以后又有人拿它当本会话的软线（就是 SL05 当初的 bug）。

---

## 四、hooks/session_resume.py（复核的是 02:32 快照）

### 🟡14 cwd 在 Projects 外的独立项目（有 .git、没交接）时，会拿 Projects 下别的项目的交接
**复现**：cwd=`~/Documents/mc-option-pricer`（有 .git、无 HANDOFF.md），Projects 下 claude-usage-saver/HANDOFF.md 是 1 小时前写的。
**期望**：不注入别的项目的交接（最多提一句「这个项目没有交接」）。
**实际**：`find_up` 在项目根停下返回 None；`in_project` 只看是否在 `~/Documents/Projects/` 下，为 False → 退回 `find_recent()`，拿 claude-usage-saver 的交接。cwd 在 `/private/tmp/...` 时也一样。现在有了防注入，正文多半不会注入，但仍会说「改用 Projects 下……的一份」，引导 Claude 去读无关项目。
**建议**：只有 cwd 恰好是 `~` 或 `~/Documents/Projects` 本身时才走 find_recent；其他情况（找到了项目根但没交接、或在 HOME 外）不退回。

### 🟡15 交接说明与代码状态不一致（需要总指挥确认）
- HANDOFF.md「下一步（第 4 轮）」说第 6 项防注入、第 11 项 compact 还没做，但 02:32 的 session_resume.py 已经实现了（MARK 门槛、on_compact），评分表 SR15–SR17 也已存在并通过。
- 但 `~/.claude/skills/handoff/SKILL.md` 里**还没有**写 MARK 的指令，现有 HANDOFF.md 第一行也都没有 MARK。所以现在的实际效果是：只要上一段对话没用 Edit/Write 改过交接（比如交接是子 agent 写的、用 Bash 写的、或者连续 /clear 两次），/clear 后只给路径不注入正文——比第 3 轮之前退步了。
- `on_compact` 需要 settings.json 里 SessionStart 的 matcher 包含 `compact`，现在只有 `clear`，所以 compact 分支目前不会运行。
- 建议：这三件事（skill 写 MARK、matcher 改成 `clear|compact`、更新 HANDOFF）要同一批落地，否则别启用 MARK 门槛。

### 💭16 `fresh` 用 `getctime(tp)` 判断「交接是上一段对话写的」
macOS 上 ctime 是 inode 最后变更时间（每次追加 transcript 都会变），不是创建时间，所以这个条件实际是「交接在 transcript 最后一次写入前 60 秒内改过」。超过 3 小时的长会话里中途写的交接会被当成不新鲜、截到 2500 字。改前就有，不是第 3 轮引入。建议用 `os.stat(tp).st_birthtime`（macOS）或 transcript 第一条的 timestamp。

### 💭17 `edited_in` 只看 transcript 尾部 3MB
1M 上下文的会话 transcript 很容易超过 3MB；交接若在较早时写、之后又跑了很多大输出，就认不出来。一般交接是在最后写的，影响小。

### 做得好的地方
SR05/SR06 把 `~` 当成独立项目、和 Projects 最新一份比新旧，逻辑清楚；SR11「多个候选不猜」的取舍写在注释里，代价也在 improvements.md 里如实写了；clip_mid 保留结尾很实用。

---

## 五、tools/weekly_compare.py

- 按启用时刻切分、本地日期分天、加平均值都对。
- 🟡18（小）同一天去重用的是 **UTC 日期**（`now.isoformat()[:10]`），而报告文件名和分天用的是**本地日期**。PST 下午 4 点以后跑一次、第二天上午再跑一次，会被当成同一天、只留一条；同一本地日下午 3 点和 5 点跑会留两条。建议改成 `dt.datetime.fromisoformat(x['ts']).astimezone().date() != now.astimezone().date()`。
- 💭19 同一 message.id 只取第一行的 usage：我统计了本机 43,794 条 usage，同 id 不同 output_tokens 只有 1 条，可忽略。没有缺 id 的记录，`None in seen` 的隐患目前不会触发。
- 💭20 统计口径（提醒，不是 bug）：「改动前」包含 11-04 通宵（占全月 52%、8 个 Opus agent），「启用后」只有约 2 个不完整天，任务组成完全不同。每次请求成本的变化里混着任务类型的变化，-31% / -47% 不能算作套件的因果效果；子 agent·Sonnet +92% 很可能也是任务组成造成的。建议在报告里按「同类会话」（例如只比主会话、按项目分组）再给一版。

---

## 六、评分表 tools/scorecard.py 没覆盖到的情况（建议新增）

现有 TF 场景全是单行命令、用 bash 写的假 pytest、没有 pipefail，这是 🔴1/🔴2 漏掉的直接原因。建议至少加一个用**真实 pytest**（或至少输出与真 pytest 同格式且通过时不含 FAIL 字样）的场景组。

| ID | 描述 | 期望 |
|---|---|---|
| TF16 | 多行 `if [ -d tests ]; then⏎ pytest -q⏎ fi` | 无语法错误；测试跑了；rc 与原命令一致（放行或包装都可） |
| TF17 | 多行 `for f in a b; do⏎ pytest -q $f⏎ done` 和 `{⏎ pytest⏎ }`、`else⏎ pytest` | 同上 |
| TF18 | `set -eo pipefail; pytest -q（全部通过）; echo AFTER` | rc=0，能看到「passed」和 `AFTER` |
| TF19 | `set -eo pipefail; pytest -q（3000 个失败）` | rc=1（不是 141），能看到结尾摘要和日志路径 |
| TF20 | 日志目录不可写/不存在 | 测试照常运行（不过滤也行），rc 与原命令一致 |
| TF21 | 失败输出只有 12 行 | Claude 看到的行数 ≤ 原输出 + 2 行（不重复） |
| TF22 | 测试运行中被杀（模拟：假 pytest `sleep` 后被 timeout 杀掉，或检查日志路径是否在运行前打印） | 输出里能看到日志路径 |
| TF23 | `npm test -- --watch`、`pytest --pdb` | 不包装 |
| TF24 | `python script.py -m pytest`、`node app.js --test` | 不包装 |
| TF25 | `cd '中文 目录' && pytest -q && echo 完成` | 包装、rc 一致、`完成` 可见（目前已通过，加上防回归） |
| TF26 | `time pytest`、`env X=1 pytest`、`uv run pytest`、`npm run test:unit` | 明确期望（包装或放行），防止以后改识别规则时悄悄变化 |
| SL07 | payload 的 five_hour 已过期（resets_at 1 小时前、95%），rate.json 为空 | rate.json 不写入该读数或不刷新 ts；状态栏不显示 95% |
| CG10 | rate.json 新鲜，但 five_hour resets_at 已过、95%；seven_day 50% | 不判「快用完」 |
| SL08 | cwd=`.../quant-backtest-old`，200k | 按普通软线 160k 显示 ⚠（或明确写出期望） |
| SL09 | payload 没有 prompt_cache / rate_limits 字段是 null / resets_at 缺失 | 不报错，正常显示 |
| SR18 | cwd=`~/Documents/mc-option-pricer`（有 .git、无交接），Projects 下有 1 小时前的交接 | 不注入、不推荐别的项目的交接 |
| SR19 | cwd=`/private/tmp/xxx` 下 /clear | 同上 |
| SR20 | 交接由子 agent 写（isSidechain 或 subagents/ 下的 transcript）或用 Bash 写，无 MARK | 期望要先定：是注入还是只给路径（目前只给路径，算退步还是有意为之？） |
| SR21 | SessionEnd 时 cwd 与 SessionStart 时 cwd 不同（上一段对话里 cd 过） | 仍能找到指针（或明确不支持） |
| SR22 | settings.json 的 SessionStart matcher 不含 compact | 评分表应报告「on_compact 未启用」，而不是只测函数本身 |
| WC08 | 同一本地日 15:00 与 17:00 PST 各运行一次；以及 17:00 与次日 10:00 | 前者 1 条，后者 2 条 |

另外两点建议：
- 评分表现在测的是 hook 脚本本身，不测「它在 Claude Code 里是否生效」。可以加一个只读的「安装检查」组：settings.json 里 4 个 hook 的路径存在、matcher 正确（clear|compact、Bash）、statusLine 指向的文件可执行。
- TF 场景用的假 pytest 应该在通过时**不输出**任何 FAIL/ERROR/assert 字样（现在若输出含这些词，就测不出 🔴2）。

---

## 七、建议的修改顺序

1. 🔴1 + 🔴2 + 🟡3 + 🟡4：都在 `wrap()`/`split_segments()` 里，十几行改动，一次改完；先补 TF16–TF21 再改（先看到失败）。
2. 🟡10（statusline 与 context_guard 各一行）+ SL07/CG10。
3. 🟡15：和第 4 轮的 skill 写 MARK、matcher 改 compact 一起落地。
4. 其余 💭 有空再说。

改动前请照例备份到 backups/，并给出撤回命令。

---

复核快照 sha1（scratchpad/snap/）：test_filter.py da5414be…、statusline.sh b6f5e5e0…、context_guard.py 56fed630…、session_resume.py 206eb31a…、weekly_compare.py 627aafd6…
