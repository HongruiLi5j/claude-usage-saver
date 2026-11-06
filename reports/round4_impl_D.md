# 第 4 轮实现 D：过期窗口 / 项目匹配 / weekly 去重 + Codex 条目 + 复核【低】

日期：2026-11-06（PST）。改动文件只有 `hooks/statusline.sh`、`hooks/context_guard.py`、`tools/weekly_compare.py`，另新建 `tools/tests_r4D.py`（独立用例）。
没有改 scorecard.py、session_resume.py、improvements.md、config.json、~/.claude/settings.json；没有 push，没有 rm。

**注意：hooks 是直接生效的。** 改完以后真实状态栏和守卫已经在用新代码（见文末「真机状态」）。

## 备份与撤回

| 文件 | 备份（改前，sha1） | 改后 sha1 |
|---|---|---|
| hooks/statusline.sh | backups/statusline.sh.pre-r4D（b6f5e5e0…） | f209596a… |
| hooks/context_guard.py | backups/context_guard.py.pre-r4D（01c2a12b…） | f8a2b9ca… |
| tools/weekly_compare.py | backups/weekly_compare.py.pre-r4D（62fa7d48…） | 0760ec5f… |
| tools/tests_r4D.py | 新建 | 020cf47d… |

全部撤回（一行）：

```
cd ~/Documents/Projects/claude-usage-saver; cp backups/statusline.sh.pre-r4D hooks/statusline.sh; cp backups/context_guard.py.pre-r4D hooks/context_guard.py; cp backups/weekly_compare.py.pre-r4D tools/weekly_compare.py; mkdir -p _to_delete; mv tools/tests_r4D.py _to_delete/tests_r4D.py.r4D; mv state/remind.json _to_delete/remind.json.r4D
```

只撤回 weekly_compare：

```
cd ~/Documents/Projects/claude-usage-saver; cp backups/weekly_compare.py.pre-r4D tools/weekly_compare.py
```

**statusline.sh 和 context_guard.py 必须一起撤回。** 新的状态栏会 `import context_guard`。只撤回守卫的话，旧守卫在被 import 时会直接跑 hook 主流程并 `sys.exit(0)`，状态栏就整行不显示了。一起撤回：

```
cd ~/Documents/Projects/claude-usage-saver; cp backups/statusline.sh.pre-r4D hooks/statusline.sh; cp backups/context_guard.py.pre-r4D hooks/context_guard.py
```

## 验证方式

- `python3 tools/tests_r4D.py --version pre` 跑改前版（backups/*.pre-r4D），`python3 tools/tests_r4D.py` 跑当前版。每个用例都在独立临时目录里跑：假 HOME（套件目录、config.json、statusline 的 state 都在假 HOME 下），`USAGE_SAVER_STATE` 指向同一个假 state，`TZ=America/Los_Angeles`。被测脚本只以副本运行。沙箱 config 默认去掉 `cap_from_date`，需要封顶的用例自己设，所以结果不随运行日期变化。
- **结果：改前 12/34，改后 34/34。** 改前失败的 22 条正好是 bug 用例。改前也通过的 12 条是「不回归」用例（SL10、SL11、CG11、CG13–15、SL13、WC10、CX10C、CXR3、CXR4、LA2）。完整输出在 /private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/r4D/tests_pre.txt 和 /private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/r4D/tests_post.txt。
- `python3 tools/scorecard.py --version all`：**当前版 69/69（100%），不适用 2**；v0 17/50。TF 场景这次没有波动。有两点要说明：
  - 另一个 agent 已经在 scorecard 的 `sl()` 里加了 `E.script("context_guard")`，所以 SL04/SL05 在新的 import 结构下仍然通过。
  - SL14（状态栏跟着封顶）在 scorecard 里标着「current 不适用：待 D」。我在 scratchpad 里复制了一份 scorecard，去掉这个 na 后用 `--only SL14,CG16,SL04,SL05` 跑，**4/4 通过**。合并的人可以直接去掉 SL14 的 `"current"` na。

---

## 一、原定 3 个 bug

### 1. 已过期的 5h 窗口被当成新数据（round3 🟡10）

**复现（改前）**：rate.json 为空。payload 里 `five_hour={95%, resets_at 1 小时前}`，`seven_day={50%, 明天}`。
- SL07 ✗：rate.json 写进了 five_hour 95%，状态栏显示 `5h 95%`。
- SL07E ✗：接着跑 `context_guard --mode`，得到「快用完」。
- CG10 ✗：rate.json 新鲜，但 5h 的 resets_at 已过、95%，也判「快用完」。

**改法**：
- statusline 合并前加一行：payload 里的窗口如果带了 resets_at 且已过期，就当成 `{}`，不写入。rate.json 里别的会话写的有效读数照常保留（SL10）。
- 我选的是「直接不写这个窗口」，没有选「写 null」。理由是读 rate.json 的三个地方都把「缺这个窗口」处理得最安全：守卫当没有，bench 的 `wait_five_hour` 遇到 KeyError 会直接放行，状态栏显示 `?`。写 null 的话，bench 里 `None < 90` 会抛 TypeError。
- 守卫这边独立再校验一次：只有 resets_at 在将来的窗口才参与判断。
- payload **没有** resets_at 时照旧显示和写入（SL11，与 SL09 一致）。

**结果**：SL07、SL07E、CG10 都是 ✗→✓；SL10、SL11、CG11 前后都是 ✓。

### 2. 项目软线按子串匹配（round3 💭11）

**复现（改前）**：
- CG12 ✗：cwd=`Projects/quant-backtest-old`，200k，没有提醒（误用了 250k）。
- SL12 ✗：同样情况，状态栏不显示 ⚠。

**改法**：`k in os.path.normpath(cwd).split(os.sep)`，也就是 cwd 的某一级目录名等于键才算命中。项目子目录照常命中（CG13：`quant-backtest/src` 仍按 250k）。守卫和状态栏现在调用同一个 `project_soft()`。

**封顶没被破坏**：#13 的 cap_from_date 逻辑原样保留，挪进了 `lines()`。CG14（封顶后软线 180k、190k 时软提醒）和 CG15（强线 195k、196k 时强提醒）前后都是 ✓，scorecard 的 CG16 也是 ✓。

**结果**：CG12、SL12 都是 ✗→✓。

### 3. weekly_compare 去重按 UTC 日期（round3 🟡18）

**复现（改前）**：
- WC08 ✗：「现在」= 11-08 12:00 PST；history 里已有一条 11-07 17:00 PST（UTC 日期也是 11-08）。本地是两天，却被当成同一天删掉了。
- WC09 ✗：「现在」= 11-08 18:00 PST（UTC 11-09）；已有同一本地日 14:00 那条（UTC 11-08），结果留了两条。

**改法**：新增 `local_day(ts)`，把 ts 解析成 aware 时间再 `.astimezone().date()`，和 `now.astimezone().date()` 比较。这和报告文件名、分天（WC07）用同一个口径。坏 ts 或没有 ts 的旧条目会保留，不报错（WC10）。history 里存的 ts 仍是 UTC isoformat，格式不变，读它的地方不受影响。分天本来就是本地日期，不用改。

**结果**：WC08、WC09 都是 ✗→✓；scorecard 的 WC05、WC07 仍是 ✓。

**真实数据前后对比**：两次运行都用 scratchpad 副本，BASE 指向 scratchpad，history 用真实文件的拷贝，所以不碰真实 reports/。
- 改前（scratchpad/r4D/wc_pre/）和改后（wc_post/）分别在 02:40 和 02:42 PST 跑。「改动前」窗口所有数字完全一样（17 天、36.1M/天、32.1k/次……）。「启用后」窗口每天请求数 492→504、每次请求成本 21.2→21.1k 等有小幅变化。
- **原因不是这次修改**：「最近」窗口没有上界，两次运行之间我和总指挥的会话还在不断写 transcript。为了确认，我又把 now 固定在同一时刻、前后背靠背跑了一遍（fx_pre/、fx_post/）：「改动前」完全一样，「启用后」只差 1 次请求（main·opus 327→328），就是两次运行之间新写进来的那一次。
- 去重：真实 history 里唯一一条是 10:37 UTC（= 02:37 PST），按 UTC 和按本地都是 11-06，所以两版都替换成 1 条，结果相同。差别只会出现在 PST 16:00–24:00 运行的时候。

---

## 二、Codex 条目（round4_codex_review.md 第 8–11、14、15 条 + 「最值得做」第 1、3 条）

| 条目 | 用例（改前→改后） | 改法 |
|---|---|---|
| 8 周用量 95% 但已过重置，仍判「快用完」 | CX08 ✗→✓ | `get_mode` 先丢掉已重置的窗口，再判近上限，最后才算周进度 |
| 9 只有有效 5h 95%、没有 seven_day 时判「正常」 | CX09 ✗→✓ | 任一未重置窗口 ≥ near_limit_pct 都判「快用完」；只有时间进度分档需要 seven_day |
| 10 全局 ts 让旧窗口重新变「新鲜」 | CX10A ✗→✓，CX10B ✗→✓，CX10C ✓→✓（不回归） | 见下 |
| 11 状态栏不跟封顶，与守卫说法相反 | CX11 ✗→✓，CX11G ✗→✓（改前在 4 个点不一致） | 软线/强线整套计算抽成 `context_guard.lines(cwd, mult)`（项目匹配 → 档位倍数 → 下限 → 强线 → 封顶）。statusline `import context_guard` 后调用它，不再自己维护一份 |
| 14 扫描 usage 没排除 sidechain | CX14 ✗→✓ | `isSidechain:true` 的行跳过 |
| 15 mode.json 非原子写 | CX15 ✗→✓ | `_atomic_write`：同目录临时文件写完再 `os.replace`。用例是代理断言（inode 变了、内容是完整 JSON），竞态本身不好稳定复现 |
| 最值得做 3：超线后每条消息都注入 | CXR1 ✗→✓，CXR2 ✗→✓，CXR3/CXR4 ✓→✓ | 见下 |

**第 10 条的做法**：rate.json 里每个窗口自带 `sampled_at`，意思是「这个读数最后一次**变化**（或首次出现）的时间」。
- 同一窗口、同样数值的重复 payload 不刷新它。这样空闲会话反复带来的旧读数会正常变旧，这是 Codex 说的「识别不了重复旧样本」的折中办法。
- 读数变大或进入新窗口时，刷新为当前时间。
- 顶层 ts 改成各窗口 sampled_at 的最大值，只为兼容旧读法。
- 旧格式的 rate.json（没有 sampled_at）会回退到顶层 ts。
- 守卫的规则是：**判「快用完」时不看陈旧**，因为窗口内用量只增不减，旧读数是下限，95% 的旧读数说明现在至少 95%；**算「宽裕/紧张」时要求周读数新鲜**（≤ rate_stale_seconds），旧的低读数会高估富余。
- CX10C 确认真有新数据（20%→21%）时仍能判「宽裕」。

**会话级提醒去重**：状态存在 `state/remind.json`，按 session_id 分条，超过 7 天的条目自动清掉。
- 首次越软线提醒一次（直接越过强线就发强提醒）。
- 首次越强线再提醒一次。
- 之后上下文每涨 `remind_every_tokens`（默认 5 万）或每 `remind_every_prompts`（默认 10）条消息再提醒一次。
- 回到软线以下（比如 compact 之后）就清掉记录，下次越线重新算首次（CXR3）。
- 没有 session_id 或状态文件写不进去时，退回旧行为，每次都提醒，宁多勿漏（CXR4）。
- 两个新配置键都是可选的，缺省就用默认值；config.json 我没改。

## 三、复核【低】（round4_review_12_13.md F5/F7）

| 条目 | 用例 | 改法 |
|---|---|---|
| a. cap_from_date 按字符串比较 | LA1 ✗→✓（`今年/1/1` 改前不封顶），LA2 ✓→✓（`明年/1/1` 不封顶），LA3 ✗→✓（`下周`） | `parse_day()` 支持 `YYYY-M-D`，分隔符可以是 `-`、`/`、`.`，不要求补零。**解析不了时按已生效封顶**（按 F5 的建议偏保守，因为 Pro 额度更紧），并在提示和 mode.json 的 why 里写明「cap_from_date=… 无法解析,按已生效封顶」 |
| a. 数值配置写错导致守卫整体静默失效 | LA4 ✗→✓（`max_soft_line_tokens:"18万"`、max_strong 为 null），LA5 ✗→✓（`handoff_threshold_tokens:"abc"`、项目阈值为 null） | 所有数值配置都经过 `num()`：缺失、非数字、布尔、负数都回退默认值；tier_multipliers 也逐项校验；项目阈值无效时跳过这个键 |
| b. 强提醒写「不限行数」，与 skill 矛盾 | LB1 ✗→✓ | 改为「高消耗项目最多 120 行,更早的细节放 HANDOFF.history.md」，与 ~/.claude/skills/handoff/SKILL.md 第 13 行一致 |

---

## 建议并入 scorecard.py 的场景（由别人合并）

`tools/tests_r4D.py` 里的用例都能直接搬。scorecard 已有的 `cg()`/`sl()`/`wc()` 辅助函数够用，只需补 `session_id` 和额外行两个参数。建议的 ID 和代码如下（ID 避开了 scorecard 已用的 CG01–09、CG16、SL01–06、SL14、WC01–07）：

| 建议 ID | 来自 tests_r4D | 组 |
|---|---|---|
| SL07 / SL08 / SL09 | SL07、SL10、SL11 | statusline |
| SL10 / SL11 | SL12、SL13（项目匹配） | statusline |
| SL12 / SL13 / SL15 | CX10A、CX10B、CX10C（sampled_at） | statusline |
| CG10 / CG11 | CG10、CG11 | context_guard |
| CG12 / CG13 | CG12、CG13 | context_guard |
| CG14 / CG15 | CX08、CX09 | context_guard |
| CG17 / CG18 | CX14、CX15 | context_guard |
| CG19 / CG20 / CG21 / CG22 | CXR1–CXR4（会话去重） | context_guard |
| CG23 / CG24 / CG25 / CG26 / CG27 | LA1、LA3、LA4、LA5、LB1 | context_guard |
| CG28 | CX11G（状态栏 × 守卫一致性网格） | context_guard |
| WC08 / WC09 | WC08、WC09 | weekly_compare |

另外：**SL14 去掉 `"current": "待 D…"` 这个 na**（已验证通过）。

scorecard 的 `cg()` 需要的改动（示例）：

```python
def cg(E, ctx_tokens, cwd=None, rate=None, big_tail=False, sid=None, extra_rows=()):
    ...
    rows = [..., *extra_rows]
    payload = {"hook_event_name": "UserPromptSubmit", "transcript_path": tp, "cwd": cwd, "prompt": "hi"}
    if sid: payload["session_id"] = sid
```

几个代表性场景（scorecard 风格）：

```python
@sc("CG14", "context_guard", "Codex 8", "周用量 95% 但周重置已过 2 天、5h 10%：不判快用完")
def _(E):
    now = time.time()
    open(os.path.join(E.state, "rate.json"), "w").write(json.dumps({"ts": int(now), "rate_limits": {
        "five_hour": {"used_percentage": 10, "resets_at": int(now + 3600)},
        "seven_day": {"used_percentage": 95, "resets_at": int(now - 2 * 86400)}}}))
    out = E.run([sys.executable, E.script("context_guard"), "--mode"])[1]
    return out.strip() != "" and not out.startswith("快用完")

@sc("CG19", "context_guard", "Codex 最值得做 3", "同一 session 170k 连发 3 条只提醒 1 次；+55k 再提醒；310k 强提醒")
def _(E):
    r = [cg(E, n, sid="S1") or "" for n in (170_000, 172_000, 175_000, 225_000, 310_000)]
    return ("[上下文守卫]" in r[0] and r[1] == r[2] == "" and "[上下文守卫]" in r[3]
            and "上下文已很长" not in r[3] and "上下文已很长" in r[4])

@sc("CG17", "context_guard", "Codex 14", "主会话 200k 后跟一条 isSidechain 1k：仍按 200k 提醒")
def _(E):
    side = {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "content": [],
            "usage": {"input_tokens": 1000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}
    return "[上下文守卫]" in (cg(E, 200_000, extra_rows=[side]) or "")

@sc("WC08", "weekly_compare", "round3 🟡18", "11-08 12:00 PST 运行；已有 11-07 17:00 PST（UTC 同为 11-08）一条：两条都留")
def _(E):
    rep = os.path.join(E.suite, "reports"); os.makedirs(rep, exist_ok=True)
    json.dump([{"ts": "2026-11-08T01:00:00+00:00", "before": {}, "recent": {}}], open(os.path.join(rep, "weekly_history.json"), "w"))
    _, _, h = wc(E, {"-Users-x-proj": [msg("m1", "2026-10-25T20:00:00Z")]})
    return len(h) == 2
```

WC09 需要别的「现在」（`dt.datetime(2026, 11, 9, 2, 0, tzinfo=UTC)`），而 scorecard 的 `wc()` 把 now 固定死在 WC_NOW。要合并的话得给 `wc()` 加一个 `now_expr` 参数。

---

## 疑虑 / 需要所有者知道的

1. **`AQ-sandbox` 键不再命中 `AQ-sandbox-1104` 这类目录。** 按路径组件精确匹配是这次要的效果，但真实历史里沙箱目录叫 `~/Documents/AQ-sandbox-1104`（见 ~/.claude/projects 下的目录名）。以后如果再起 `AQ-sandbox-XXXX` 目录，会退回默认 16 万。需要的话在 config.json 里加一个精确的键，config 我没改。`AQ-o44` 精确命中 `~/Documents/AQ-o44` 及其子目录，不受影响。
2. **sampled_at 用「读数最后一次变化的时间」有代价。** used_percentage 在真实 payload 里是整数，周用量轻度使用时可能 6 小时以上不变，这时守卫会从「宽裕/紧张」回到「正常」。「快用完」不受影响（不看陈旧）。我认为这是可以接受的保守方向，但没有真机数据证明。Claude Code 空闲时是否一直带着旧 rate_limits，也仍是 round3 提到的未真机确认的前提。
3. **cap_from_date 解析不了时按「已生效」处理。** 这是按 F5 建议、偏省额度的选择；总指挥给的另一种做法是「无效就忽略」。如果所有者偏好忽略，只需改 `cap_active()` 里的一行。
4. **状态栏现在依赖 hooks/context_guard.py 能被 import。** import 失败（文件坏了或被移走）时状态栏仍会显示模型/ctx/5h/7d，只是没有 ⚠，不会整行消失。但如上所说，**撤回时两文件必须一起撤**。
5. **会话去重是新行为**，会减少注入次数，这是有意的。默认值（5 万 token / 10 条）是我定的，没有实测依据。真实 payload 一定带 session_id，所以去重会立即对真实会话生效。
6. 并发：rate.json 的读改写仍然无锁（Codex 第 5 条，不在本次范围）；remind.json 同样无锁，但它是按 session_id 分条的，同一会话不会并发提交，影响很小。
7. tests_r4D.py 用 `tempfile.mkdtemp` + `shutil.rmtree` 清理自己建的临时目录（和 scorecard 的 TemporaryDirectory 是同一性质）；加 `--keep DIR` 可以保留。

## 真机状态（只读查看）

改完后真实 `state/rate.json` 已经是新格式：两个窗口都带 sampled_at，5h 74%、7d 87%。`context_guard.py --mode` 输出 `正常 1.0 周用量 87%,时间进度 92%`，mode.json 正常写入，remind.json 已生成（目前为空）。

## 总结
1. 原定 3 个 bug 都已修：过期 5h 窗口不再写入、也不参与判档；项目软线按路径组件匹配；weekly history 按本地日期去重。weekly 的统计数字不受影响，前后差异来自运行间隔里新写入的请求。
2. Codex 8–11、14、15 和「会话级提醒去重」都已落地，阈值计算统一到 `context_guard.lines()`，状态栏和守卫在 36 个组合上判定一致。
3. 复核【低】a/b 已修：日期按日期解析，数值配置写错会回退默认值，强提醒文案改成「最多 120 行」。
4. 验证：tests_r4D.py 改前 12/34、改后 34/34；scorecard 当前版 69/69，SL14 去掉 na 后也通过。
5. 待所有者决定：`AQ-sandbox-XXXX` 目录要不要加精确键；cap 日期解析失败时是「生效」还是「忽略」；去重间隔默认值。撤回时 statusline 和 context_guard 必须一起撤。

---

## D2（打回后，2026-11-06）

实施者 D2。范围只有 3 件事：Codex 打回的两条（`reports/round4_codex_review_D.md` 最后结论）+ 盲测分诊的 SL03（`reports/round4_triage.md`）。CG11、CG13 没动。weekly_compare.py 没改（只做了备份）。没读 .secret/ sealed/ test2/ tools/heldout/，没改 ~/.claude/settings.json，没 rm，没 push。

### 问题 1：过期的提醒记录没在去重前清理（context_guard.py）

- **复现（实测，旧版 = backups/context_guard.py.pre-r4D2，直接调 `remind_kind`）**：预置 `{"A":{"level":"soft","at_n":170000,"since":0,"ts":now-604801}}`，以 A、170k、软线 160k 调用 → 返回 `None`，文件里 A 的 ts 被刷成当前时间、since 变 1。和 Codex 描述一致。
- **原因**：旧代码先用 `R.get(sid)` 拿记录去重，最后才按 7 天过滤；过滤前又把本会话记录的 ts 刷新了，所以本会话的过期记录永远过滤不掉。
- **改法**：新增 `_clean_entry(v, now)`：记录必须是 dict、`level` 是 soft/strong、`ts`/`at_n`/`since` 都能当非负数（复用 `_num`）、且未超过 7 天，否则返回 None。`remind_kind` 读进 remind.json 后**先**逐条清理，再拿本会话记录去重。
- **结果（实测）**：同一场景返回 `soft`，文件里 A 变成 `{"level":"soft","at_n":170000,"since":0,"ts":当前}`。

### 问题 2：损坏的单条记录导致静默漏提醒，并连累其他会话（context_guard.py）

- **复现（实测，旧版）**：
  - `{"A":"broken"}`，A 320k → `AttributeError`（main() 吞掉，stdout 为空，强提醒丢失）。
  - `{"B":{..., "ts":"broken"}}`，A 320k → 清理 B 时 `TypeError`，A 的强提醒同样丢失。
- **改法**：
  1. 坏记录由上面的 `_clean_entry` 当成「没有记录」处理（A 坏 → 按首次越线；B 坏 → 直接清掉，不影响 A）。
  2. `remind_kind` 里读文件、清理、去重、写文件整个包在 try 里，任何异常都返回 `raw_kind(n, soft, strong)`（本次应有的提醒，宁多勿漏）。
  3. `main()` 调 `remind_kind` 的地方再包一层 try，出错时也退回 `raw_kind`。新增的 `raw_kind` 就是原来那行「不考虑去重时该发什么」的判断，抽出来给两处共用。
- **结果（实测）**：两个场景都返回 `strong`，remind.json 里只剩合法的 A 记录（B 的坏记录被清掉）。另外查了真实 `state/remind.json`（只读）：现有那 1 条记录能通过校验，升级后不会被当成坏记录重置。

### 问题 3：SL03，状态栏遇到损坏的 stdin 会崩（statusline.sh）

- **复现（实测，旧版 = backups/statusline.sh.pre-r4D2，假 HOME）**：`printf '{not json' | sh statusline.sh` → 退出码 1，stdout 为空，stderr 15 行，含 Traceback。
- **改法**：主体放进 `main(d)` 函数；外层 `try: d=json.load(sys.stdin)`，不是 JSON 对象也算坏；`main(d)` 里出任何 `Exception`（比如字段类型不对）也退回兜底。兜底时只输出一行 `? | ctx ? | 5h ? | 7d ? | 状态栏输入无法解析`，退出码 0，**不写 rate.json / last_status_sample.json**（坏输入不能污染别的会话共用的读数）。为此把状态行的格式化挪到写 rate.json 之前，这样格式化出错时也不会先写进半截数据；合法 payload 的输出和写入内容都没变。
- **结果（实测）**：同一命令 → 退出码 0，stdout 是上面那一行兜底，stderr 0 行。

### tests_r4D.py

- **临时目录退回**（按要求）：`$R4D_TMP` → `tempfile` → 项目内 `state/_test_tmp/`；后两种之外（R4D_TMP 和项目内）用完都 `os.rename` 到 `_to_delete/`（项目内的进 `state/_to_delete/`，R4D_TMP 的进 `$R4D_TMP/_to_delete/`），不 rm。只有 tempfile 建的系统临时目录还用 `shutil.rmtree` 清（和原来、和 tests_r4F.py 一样）。`R4D_FORCE_FALLBACK=1` 可以强制走退回路径。写法照搬了 tests_r4F.py 的 `case_dir`，两边一致。
- 新增 `--version pre2`（跑 `backups/*.pre-r4D2`），用来确认新用例能复现。
- **新增 8 条用例**：
  - CXR5（问题 1）、CXR6 / CXR7（问题 2 的两个复现）、CXR8（字段类型全错）
  - CXR9（不回归：有效记录不被误清，去重照常）、CXR10（不回归：remind.json 是坏 JSON / 是列表）
  - SL03（坏 JSON、空 stdin、`[1,2]`、`"x"`、字段类型错，共 5 种输入：退出码 0、无 traceback、最多一行）、SL03B（坏 stdin 不改写 rate.json；之后正常 payload 照常显示）

### 测试数字（全部实测）

| 运行 | 结果 |
|---|---|
| 旧 tests_r4D.py（34 条）× D2 改前代码 | 34/34（本机 tempfile 可用） |
| 旧 tests_r4D.py，**模拟** tempfile 不可用（把 `tempfile.gettempdir` 替换成抛 FileNotFoundError） | 第一个用例前崩溃，`No usable temporary directory found`（和 Codex 看到的一样） |
| 新 tests_r4D.py（42 条）`--version pre2`（D2 改前代码） | **36/42**；失败 CXR5、CXR6、CXR7、CXR8、SL03、SL03B（CXR9、CXR10 是不回归用例，改前也该过） |
| 新 tests_r4D.py `--version pre`（R4D 之前的代码，参考） | 15/42 |
| 新 tests_r4D.py（当前代码） | **42/42** |
| 同上，`R4D_FORCE_FALLBACK=1` | 42/42，42 个目录进了 state/_to_delete/ |
| 同上，**模拟** tempfile 不可用（同上替换方法，不加强制开关） | 42/42，自动退回 state/_test_tmp，42 个目录进了 state/_to_delete/ |
| 同上，`R4D_TMP=<scratchpad 目录>` | 42/42，42 个目录进了 `$R4D_TMP/_to_delete/` |
| `python3 tools/scorecard.py --version all`（改后） | 当前版 **107/107（100%）**，初版 v0 20/58；scorecard.py 没改。改前没单独跑 scorecard，所以没有改前数字 |

说明：state/_to_delete/ 里现在多了 84 个 `r4D-*` 测试目录（两次退回路径的运行留下的），由所有者清理。

### 备份和撤回

备份（`cp -p`，改前 SHA-1 和备份一致）：
- backups/context_guard.py.pre-r4D2
- backups/statusline.sh.pre-r4D2
- backups/tests_r4D.py.pre-r4D2
- backups/weekly_compare.py.pre-r4D2（本轮没改，只做了备份）

改后 SHA-1：context_guard.py `c31304b8…`，statusline.sh `6db8dcb1…`，tests_r4D.py `ce252549…`。

撤回命令（一行；两个 hook 一起撤回，和 R4D 一样不要只撤一个）：

```
cd ~/Documents/Projects/claude-usage-saver; cp -p backups/context_guard.py.pre-r4D2 hooks/context_guard.py; cp -p backups/statusline.sh.pre-r4D2 hooks/statusline.sh; cp -p backups/tests_r4D.py.pre-r4D2 tools/tests_r4D.py
```

### 疑虑

1. **兜底行的文案**：`? | ctx ? | 5h ? | 7d ? | 状态栏输入无法解析`。我选择不去读 rate.json 显示旧读数，因为旧读数可能已经过期，显示出来反而误导。所有者如果更想要「什么都不显示」，把 FALLBACK 改成空字符串就行。
2. **外层只接 `Exception`，不接 `SystemExit`**：Codex 提到的「只撤回旧版 context_guard 时 import 触发 SystemExit，整行消失」这个已知限制我没改（不在这次范围内）。如果接了 BaseException，那种情况会显示「输入无法解析」，文案就不对了。
3. **ts 是未来时间的记录**仍然算有效（7 天内不会过期）。只有手改文件或系统时钟回拨才会出现，影响也只是那个会话去重偏多，所以没处理。
4. 坏记录现在会被**静默**清掉，没有日志。这和「坏记录按缺失处理」的要求一致，但出了问题就没法回查。
5. 并发：remind.json 仍然无锁（和 R4D 的疑虑 6 一样，不在范围内）。
