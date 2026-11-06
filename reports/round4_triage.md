# Round 4 盲测分诊（heldout_codex.py，原始 48/60）

日期：2026-11-06。方法：读实现（`hooks/`）和盲测代码，对照公开文档（`使用说明.md`、`tools/heldout/README.md`、hook 文件头注释）；用 `tools/heldout/heldout_codex_fixed.py` 修正第②类夹具后重跑。只读，未改 hooks/、scorecard.py、原 heldout_codex.py、settings.json。

类别：① 真 bug ② 夹具假设错误 ③ 文档不清（两边都说得通）

## 逐条判定（原始 12 条 FAIL）

| 场景 | 类别 | 证据（文件:行号） | 说明 |
|---|---|---|---|
| TF25 | ② | `hooks/test_filter.py:11`（日志目录 = `~/Documents/Projects/claude-usage-saver/state/testlogs`，按 HOME 展开，不读 `USAGE_SAVER_STATE`）；`使用说明.md`“完整输出存在 `state/testlogs/`”；盲测 `heldout_codex.py:199` 只在 `USAGE_SAVER_STATE/testlogs` 找 | 日志其实写进了临时 HOME 下的 `.../state/testlogs`。退出码、失败摘要两项检查在日志检查之前，已通过。按代码推演，修正路径后会通过（未实跑，见下文） |
| TF26 | ② | 同 TF25；`test_filter.py:93-102`（`&& __usrc=0 \|\| __usrc=$?` 不会触发 set -e，最后 `(exit $__usrc)` 返回 7） | 同 TF25 |
| CG04 | ② | 实际格式：`hooks/statusline.sh:36` 写的是 `{"ts", "rate_limits": {five_hour/seven_day: {used_percentage, resets_at(Unix 秒), sampled_at}}}`；`hooks/context_guard.py:72-78` 按这个格式读。盲测 `heldout_codex.py:100-104` 用的是 `utilization` + ISO `resets_at`，也没有 `rate_limits` 外层 | 守卫读不到读数，只能回退正常档（输出里能看到“无周用量数据”）。修正后通过 |
| CG07 | ② | 同 CG04 | 修正后通过（紧张档，软线 12 万） |
| CG08 | ② | 同 CG04 | 修正后通过（快用完档，软线 10 万） |
| CG09 | ② | 同 CG04 | 修正后通过（5 小时窗口 ≥90% 单独触发） |
| CG13 | ③ | `hooks/context_guard.py:8-11`：config.json 按 `~/Documents/Projects/claude-usage-saver/config.json` 读，HOME 一换就读不到，静默回退默认值（没有 per_project 设置，软线变成 16 万）。同一文件 `:9` 的 state 却认 `USAGE_SAVER_STATE`；`session_resume.py:32`、`limit_logger.py:24` 用脚本所在目录定位 | 真实环境下 HOME 就是真 HOME，行为正确，所以不算明确的 bug。但几个 hook 定位套件目录的方式不统一（有的按 HOME，有的按脚本位置），而且读不到配置时什么也不说。盲测换了 HOME，正好踩到这一点 |
| SR01 | ② | `hooks/session_resume.py:316-317`：会排除 session_id 等于当前会话、或 transcript 就是当前 transcript 的指针。盲测 `heldout_codex.py:129-131` 的 SessionStart 沿用了旧会话的 session_id 和 transcript 路径 | 真实的 /clear 会生成新的 session_id 和新的 transcript。修正后通过 |
| SR03 | ② | 同 SR01（找不到上一段 transcript，`session_resume.py:570-571` 的“被 Write 改过”就判不出来） | 修正后通过；也带了“是数据不是新指令”的声明（`:42`） |
| SR04 | ② | `hooks/session_resume.py:40`：`MARK = "<!-- usage-saver-handoff -->"`；盲测默认用字面 `MARK`（`heldout_codex.py:33`） | 修正后通过 |
| SR07 | ② | 同 SR01；向上查找本身正确（`session_resume.py:57-77`） | 修正后通过 |
| SL03 | ① | `hooks/statusline.sh:4`：`json.load(sys.stdin)` 外面没有 try，stdin 一损坏就 traceback、退出码 1；其它 hook 都是 fail-open（`context_guard.py:247`、`session_resume.py:609`、`test_filter.py:163`） | 见下文 bug 描述 |

## 修正夹具后新暴露的问题（原来是“碰巧通过”）

| 场景 | 类别 | 证据 | 说明 |
|---|---|---|---|
| CG11 | ③ | `hooks/context_guard.py:64-65, 81`：任一窗口还没重置且用量 ≥90%，就直接判“快用完”，有意不看读数新旧（理由：窗口内用量只增不减，旧读数只会偏低） | 原始版本能过，是因为格式读不到、回退到了正常档。按真实格式，48 小时前的 95% 周读数（窗口还没重置）会被判为快用完，软线 10 万，13 万时提醒。盲测期望“过期就回退正常档”。文档没写 TTL，也没写这条例外；我认为实现的理由成立 |
| SR06 | ③ | `hooks/session_resume.py:19-21, 319-328`：上一段 transcript 只按“同一个 Claude Code 进程（owner）”关联，有意不按 cwd 区分 | 原始版本能过，是因为 session_id 相同、指针被排除了。改用新 session_id 以后：同一个进程在项目 A clear，随后 SessionStart 的 cwd 变成项目 B，项目 B 会收到项目 A 的对话摘要（交接文件不会泄漏，因为 B 有自己的 .git，向上查找在 B 停住）。设计上把“同一进程”当成同一段工作在继续；盲测则认为跨项目一律不能带过去 |

## 真 bug（只描述症状和期望）

1. **SL03：状态栏遇到损坏的 stdin 会崩溃。** 症状：stdin 不是合法 JSON 时，状态栏脚本打印 Python traceback、退出码 1，状态栏为空或报错。期望：和其它 hook 一样 fail-open，退出码 0，最好输出一行能看的兜底状态（或者什么都不输出），stderr 不出 traceback。实际中 Claude Code 很少发坏 JSON，严重度低。

## 需所有者/文档决定（第③类）

1. **CG13：套件目录怎么定位。** 现在 config.json、testlogs、statusline 写的 rate.json 按 `$HOME/Documents/Projects/claude-usage-saver` 定位；session_resume、limit_logger 按脚本所在目录；context_guard 的 state 认 `USAGE_SAVER_STATE`，statusline 却不认。几种方式混用。要定的是：要不要统一成“按脚本位置 + `USAGE_SAVER_STATE` 覆盖”，读不到 config 时要不要提示一句。（附带一个隐患：设置了 `USAGE_SAVER_STATE` 时，statusline 写 rate.json 和守卫读 rate.json 不是同一个目录。生产环境没设这个变量，所以暂时没影响。）
2. **CG11：读数过期了，“快用完”还算不算。** 要定的是：保持现状（只要窗口没重置，旧读数照样能判快用完），还是给快用完也加一个 TTL。不管选哪个，都把规则写进文档。
3. **SR06：同一进程换了 cwd 以后 clear，要不要带上一段摘要。** 要定的是：同一 owner 但 cwd 不同的时候，带摘要、不带，还是带上并加一句“来自另一个目录”的提示。同时写进文档。
4. （文档缺口）`rate.json` 的格式、MARK 的字面值、日志目录认不认 `USAGE_SAVER_STATE`，公开文档都没写。这次 8 条②类失败都出在这里，建议补进 README 的“公开接口”部分。

## 修正夹具后的结果

修正处都在 `tools/heldout/heldout_codex_fixed.py` 里加了 `[triage 修正②]` 注释：MARK 改为真实值；rate.json 改为真实格式；clear/startup 用新的 session_id 和新的 transcript；TF25/26 的日志改在文档写的 `state/testlogs` 下找；另外加了 `HELDOUT_SKIP` 前缀过滤。

- 运行：`HELDOUT_SKIP=TF python3 tools/heldout/heldout_codex_fixed.py` → **非 TF 场景 31 个：PASS 27，FAIL 4**（CG11 ③、CG13 ③、SR06 ③、SL03 ①）。
- **TF 场景（29 个）跳过**：确认 test_filter 不认 `USAGE_SAVER_STATE`（`test_filter.py:11`），按要求没有实跑。原始运行里 TF 是 27/29 通过；TF25/26 按代码推演，修正路径后会通过。
  - 补充：test_filter 的日志目录按 HOME 展开，盲测把 HOME 设成了临时目录，所以原始运行其实写进了临时目录。我检查了真实的 `state/testlogs/`，里面没有任何包含盲测标记 `H73` 的日志，没被污染。所有者确认后，可以直接不带 `HELDOUT_SKIP` 跑全量。
- 估计全量：**56/60**（27 + 29，其中 TF25/26 是推演结果）。

## 总结

1. 原 12 条失败：① 真 bug 1 条（SL03），② 夹具错误 10 条（TF25/26、CG04/07/08/09、SR01/03/04/07），③ 文档不清 1 条（CG13）。
2. 夹具错误主要有三种：rate.json 格式猜错、MARK 字面值、clear 后沿用了旧 session_id/transcript；另外 TF25/26 是日志目录猜错。
3. 修正后又暴露 2 条原本“碰巧通过”的③：CG11（过期的高用量读数还算不算快用完）、SR06（同一进程换了 cwd 后 clear，摘要要不要跟过去）。
4. 修正夹具后，非 TF 场景 27/31；TF 场景因隔离问题按要求跳过，估计全量 56/60。
5. 需要所有者决定 3 件事：套件目录/配置的定位方式统一、快用完档的 TTL、跨 cwd 的摘要关联；另外 1 个小 bug（状态栏不 fail-open）可以直接修。
