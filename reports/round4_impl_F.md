# 第 4 轮实施 F：额度测量锚点（2026-11-06）

依据：`reports/round4_research.md` 建议第 4 项（D1 StopFailure / Notification，C1/C5 状态栏 `prompt_cache`）。
本轮只做 hook 日志这一半；状态栏 `prompt_cache` 快照归 statusline 的 agent，这里没碰 `hooks/statusline.sh`。

新文件（只写了这 4 个，没改任何已有文件）：
- `hooks/limit_logger.py`：StopFailure / Notification hook，只追加日志
- `tools/install_limit_logger.py`：幂等安装器（支持 `--dry-run`、`--uninstall`、`--settings`、`--backup-dir`）
- `tools/tests_r4F.py`：22 个隔离测试
- `reports/round4_impl_F.md`：本报告

没有改 `~/.claude/settings.json`（只读过一次，复制到 scratchpad 和临时目录里试跑），也没有运行真正的安装。真实的 `state/` 里没有生成 `limit_events.jsonl`（测试全部用 `USAGE_SAVER_STATE` 指向临时目录）。

---

## 1. 字段核实结果

来源：官方文档原文 `https://code.claude.com/docs/en/hooks.md`（2026-11-06 用 curl 下载全文，读了 `### StopFailure`、`### Notification` 两节和公共字段表）。本机版本 `claude --version` = 2.1.318。

| 事件 | 字段 | 核实结果 |
|---|---|---|
| 公共 | `session_id`、`transcript_path`、`cwd`、`hook_event_name` | **已核实**（Common input fields 表） |
| StopFailure | `error` | **已核实**。取值：`rate_limit`、`overloaded`、`authentication_failed`、`oauth_org_not_allowed`、`account_on_hold`、`billing_error`、`invalid_request`、`model_not_found`、`server_error`、`max_output_tokens`、`cloud_credential_error`、`unknown`；matcher 按它过滤 |
| StopFailure | `error_details` | **已核实**，但标着"可选"，文档示例是字符串 `"429 Too Many Requests"`，没写死类型。**容错**：字符串或对象都接受，对象转成 JSON 字符串，截到 500 字 |
| StopFailure | `last_assistant_message` | **已核实**（可选）：StopFailure 里是 API 错误原文，比如 `"API Error: Rate limit reached"`。截到 300 字 |
| StopFailure | 输出 / 退出码 | **已核实**：除了 `terminalSequence`，其余都被忽略，所以只能用来记日志 |
| Notification | `message`、`title`（可选）、`notification_type` | **已核实**（Notification input 一节） |
| Notification | matcher `quota_auto_resume_fired` / `_stale` / `_disabled` | **已核实**，需要 v2.1.234+（本机满足）。fired = 撞限额暂停后自动继续（到重置时刻，或你加额度/升级/换模型后提前继续）；stale = 电脑睡眠超过约 30 分钟时额度重置了，要按 Enter；disabled = 等待结束但没有继续 |
| Notification | quota_* 的 `message` / `title` 具体写什么、有没有重置时刻之类的额外字段 | **待核实**。文档没有给这三种类型的示例，也没提额外字段。**容错**：message、title 原样截断保存，不做解析 |
| 状态栏 | `prompt_cache.expires_at`（Unix 秒）、`ttl`（`"5m"`/`"1h"`）、`warm`、`hit_ratio`、`misses`、`miss_causes`、`cache_write_tokens`、`last_miss_cause.causes` | **已核实**（`https://code.claude.com/docs/en/statusline.md`；示例 JSON 和字段表）。状态栏会在 `expires_at` 和 `rate_limits.*.resets_at` 到点时自动重跑。这部分留给 statusline 的 agent |

Notification 的 matcher 只用了字母、数字、`_`、`|`，按文档会当作"精确字符串列表"匹配，不走正则。

---

## 2. 实现要点

**hooks/limit_logger.py**
- 每次触发往 `state/limit_events.jsonl` 追加一行，字段：`ts`（epoch，3 位小数）、`local_time`、`event`、`session_id`、`cwd`、`transcript_path`、`kind`，再加事件自己的字段（StopFailure：`error`/`error_details`/`last_assistant_message`；Notification：`notification_type`/`title`/`message`），最后附上当时 `state/rate.json` 的快照：`rate_ts`、`five_hour_pct`、`five_hour_resets_at`、`seven_day_pct`、`seven_day_resets_at`。
- `kind` 的取值：`limit_hit`（rate_limit / billing_error / account_on_hold）、`api_error`（其余错误，也记下来，方便周报排除）、`auto_resume`、`auto_resume_stale`、`auto_resume_disabled`、`unknown`。
- 保险：就算没加 matcher，非 `quota_auto_resume*` 的 Notification（比如 idle_prompt）也不记。
- 不出声、不出错：stderr 指向 /dev/null，不往 stdout 写任何东西，`try/except BaseException` 包住全部逻辑，最后 `os._exit(0)`。坏 JSON 也记一行 `parse_error: true` 和前 200 个字符，这样能看出 hook 确实触发过。
- 轮转：文件超过 5MB（严格大于 5×1024×1024 字节）就改名成 `limit_events_old.jsonl`。如果 `_old` 已经存在，就改成 `limit_events_old.<时间>.<pid>.jsonl`，**绝不覆盖、绝不删除**。
- 写入用 `O_APPEND` 一次写完一整行，并发 20 个进程同时写，测试里没有出现交错的行。

**tools/install_limit_logger.py**
- 加两条：`StopFailure`（不设 matcher，所有错误都记）和 `Notification`（matcher=`quota_auto_resume_fired|quota_auto_resume_stale|quota_auto_resume_disabled`）。命令写成 `python3 <绝对路径>/hooks/limit_logger.py`，`timeout: 10`。
- 幂等：某个事件下只要已经有命令包含 `limit_logger.py` 就跳过；只装了一半就只补缺的那条。
- 不动其他键、不动别人的 hook，只往数组末尾追加。在真实 settings 副本上逐字节 diff：除了文件末尾多一个换行，只多了 24 行新增内容。
- 只有真要改的时候才备份（默认放 `backups/settings.json.<时间>-pre-limitlogger`，同一秒内重名就加 `.v2`），然后打印一行 `撤回：cp <备份> <settings>`。
- `--dry-run` 不写任何文件；JSON 不合法、`hooks` 不是对象、事件不是数组时，exit 1，一个字都不写。
- `--uninstall`：只删含 `limit_logger.py` 的那几组，别人的保留（同样先备份）。

---

## 3. 测试结果

`python3 tools/tests_r4F.py` → **合计 22：通过 22，失败 0，跳过 0**。

| ID | 内容 | 结果 |
|---|---|---|
| L01 | StopFailure rate_limit：1 行、字段齐、带 rate 快照、kind=limit_hit、无输出、exit 0 | 通过 |
| L02 | overloaded 也记，kind=api_error | 通过 |
| L03 | quota fired/stale/disabled 各 1 行，kind 不同 | 通过 |
| L04 | idle_prompt 不记 | 通过 |
| L05 | 非 JSON、空输入、JSON 数组：exit 0、无输出 | 通过 |
| L06 | 状态目录不可写：exit 0、无输出 | 通过 |
| L07 | error_details 是对象、超长 message：转字符串并截断 | 通过 |
| L08 | 没有或损坏的 rate.json：百分比为 null，照写 | 通过 |
| L09 | 超过 5MB 改名成 _old，内容字节数不变，新文件 1 行 | 通过 |
| L10 | _old 已存在：不覆盖，另起带时间戳的文件 | 通过 |
| L11 | 正好 5MB 不轮转 | 通过 |
| L12 | 并发 20 个写入：20 行完整 | 通过 |
| I01 | 首次安装：两条都加上，其他键和键顺序不变，备份等于原文件，撤回行里没有 # | 通过 |
| I02 | 第二次运行：字节不变、不新增备份 | 通过 |
| I03 | 已有别人的 StopFailure/Notification：原样保留，我们的追加在后面 | 通过 |
| I04 | --dry-run：不写、不建备份目录 | 通过 |
| I05 | 坏 JSON：exit 1、不写、不备份 | 通过 |
| I06 | 没有 hooks 键：新建 | 通过 |
| I07 | 只装了一半：只补 Notification | 通过 |
| I08 | --uninstall 恢复到安装前的结构，别人的 hook 留着 | 通过 |
| I09 | 照着打印的 cp 执行后，文件和安装前逐字节相同 | 通过 |
| I10 | 真实 settings.json 的临时副本：首次加 2 条，第二次不变，其他键不变 | 通过 |

在 scratchpad 副本上手动试跑（`/private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/r4F/`），三种情况都和预期一致：
- dry-run：打印"将会改：StopFailure（全部错误类型）；Notification（matcher=…）"，没有写文件。
- 首次：已改，打印撤回行。
- 二次：两条都显示"已经装过，跳过"，没有写文件、没有备份。
- 已有别的 StopFailure（`matcher: rate_limit`，命令 `say limit`）：保留在第 0 位，我们的追加在第 1 位。

scorecard 场景代码（见第 5 节）也验证过：把 scorecard.py 复制到 scratchpad 并打上补丁，用 `--only` 跑，LL01–LL04 在当前版 4/4 通过，v0 显示不适用；和 SR01、CG01 一起跑也是 6/6。没有写报告和历史文件，真正的 `tools/scorecard.py` 没改。

---

## 4. 所有者要运行的命令

安装（建议先加 `--dry-run` 看一眼）：

```
python3 ~/Documents/Projects/claude-usage-saver/tools/install_limit_logger.py
```

撤回，二选一：

```
python3 ~/Documents/Projects/claude-usage-saver/tools/install_limit_logger.py --uninstall
```

或者执行安装时打印的那一行 `cp ~/Documents/Projects/claude-usage-saver/backups/settings.json.<时间>-pre-limitlogger ~/.claude/settings.json`（时间以实际打印为准）。注意 cp 会把安装之后对 settings 做的其他改动一起退回去，所以 `--uninstall` 更安全。

装完以后，建议开一个新会话让 hook 生效（运行中的会话会不会自动重新读取 hook，**待核实**）。

---

## 5. 建议并入 scorecard 的场景代码（我没有改 scorecard.py）

1. `VERSIONS["current"]` 里加一行 `"limit_logger": "hooks/limit_logger.py",`（v0 不加，场景用 `na` 标成不适用）。
2. `GROUPS` 末尾加 `"limit_logger"`。
3. 把下面这段放在最后一个 `@sc` 场景之后、`def run_one(` 之前：

```python
# ---- limit_logger（第 4 轮 F）：测量锚点，只写日志
def ll(E, payload, rate=None):
    s = E.script("limit_logger")
    if rate is not None:
        open(os.path.join(E.state, "rate.json"), "w").write(json.dumps(rate))
    rc, out, err = E.run([sys.executable, s], payload if isinstance(payload, str) else json.dumps(payload))
    p = os.path.join(E.state, "limit_events.jsonl")
    rows = [json.loads(l) for l in open(p, encoding="utf-8")] if os.path.exists(p) else []
    return rc, out, rows


LL_NA = {"v0": "v0 没有 limit_logger"}
LL_SF = {"hook_event_name": "StopFailure", "session_id": "s1", "cwd": "/w", "error": "rate_limit",
         "error_details": "429 Too Many Requests", "last_assistant_message": "API Error: Rate limit reached"}


@sc("LL01", "limit_logger", "R4 D1", "StopFailure rate_limit：写 1 行 kind=limit_hit，带当时 5h 百分比，不输出", na=LL_NA)
def _(E):
    rc, out, rows = ll(E, LL_SF, rate={"ts": 1, "rate_limits": {"five_hour": {"used_percentage": 100, "resets_at": 2}}})
    return rc == 0 and out == "" and len(rows) == 1 and rows[0]["kind"] == "limit_hit" and rows[0]["five_hour_pct"] == 100


@sc("LL02", "limit_logger", "R4 D1", "Notification quota_auto_resume_fired 记 1 行；idle_prompt 不记", na=LL_NA)
def _(E):
    base = {"hook_event_name": "Notification", "session_id": "s1", "cwd": "/w", "message": "m"}
    ll(E, dict(base, notification_type="idle_prompt"))
    rc, out, rows = ll(E, dict(base, notification_type="quota_auto_resume_fired"))
    return rc == 0 and out == "" and [r["kind"] for r in rows] == ["auto_resume"]


@sc("LL03", "limit_logger", "R4 F", "坏 JSON / 状态目录不可写：exit 0，不输出", na=LL_NA)
def _(E):
    rc1, out1, _ = ll(E, "{bad")
    f = os.path.join(E.work, "notadir"); open(f, "w").write("x")
    rc2, out2, _ = E.run([sys.executable, E.script("limit_logger")], json.dumps(LL_SF), USAGE_SAVER_STATE=f)
    return rc1 == rc2 == 0 and out1 == out2 == ""


@sc("LL04", "limit_logger", "R4 F", "超过 5MB 轮转到 limit_events_old.jsonl（改名不删），新文件 1 行", na=LL_NA)
def _(E):
    big = os.path.join(E.state, "limit_events.jsonl")
    touch(big, "x" * (5 * 1024 * 1024 + 1) + "\n")
    rc, out, rows = ll(E, LL_SF)
    old = os.path.join(E.state, "limit_events_old.jsonl")
    return rc == 0 and len(rows) == 1 and os.path.getsize(old) == 5 * 1024 * 1024 + 2
```

注意：并入后总分母会多 4 个场景，和历史分数比较时要说明一下。

---

## 6. 以后 weekly_compare 怎么用 limit_events.jsonl（只是建议，没改 weekly_compare）

1. **读取**：读 `state/limit_events.jsonl` 和所有 `limit_events_old*.jsonl`，按 `ts` 去重排序，逐行 `json.loads`，坏行跳过；`parse_error` 的行只计数。
2. **真实撞限额的次数和时刻**：本周 `kind=="limit_hit"`（主要是 `error=="rate_limit"`）的次数，按本地日期分天（沿用 `local_day`）。同一个 `five_hour_resets_at` 窗口里的多次撞限只算 1 次"撞墙"，另外报一个"撞墙后又重试了几次"。
3. **给 quota_fit 当锚点**：每次 limit_hit 等于"这个 5 小时窗口在 `ts` 时到了 100%"。从窗口开始（`resets_at - 5h`）到 `ts` 之间 transcript 里的加权 token，就是一个"100% 对应多少 token"的实测样本，比用整数百分比读数外推准。`now - rate_ts` 太大（比如超过 10 分钟）时，rate 快照不可信，只用 `ts` 和 `resets_at`。
4. **自动恢复**：`auto_resume` 和前一次 `limit_hit` 的时间差 = 实际等待时长；`auto_resume_stale` / `auto_resume_disabled` 的次数单独列出（说明任务停在那里没人管）。
5. **周报新增一节「撞限额」**：本周 vs 上周的撞限次数、平均等待分钟数、撞限时 7 天百分比的中位数；切到 Pro（11-21）之后最有用，可以直接回答"省额度措施有没有让撞墙变少"。`api_error`（overloaded 等）单独计数，不算进额度。
6. 在 `weekly_history.json` 里每天加 `limit_hits` / `auto_resumes` 两个数，方便跨周比较。

---

## 7. 疑虑

- **订阅用户撞 claude.ai 用量上限时，StopFailure 的 `error` 是不是 `rate_limit`**：文档只说"rate limits"，没有区分 API 429 和订阅用量上限（**待核实**）。所以 StopFailure 不设 matcher、所有错误都记，以后看第一条真实记录再定。
- **用量上限暂停时 StopFailure 会不会触发**：如果 Claude Code 是"暂停等待自动继续"，而不是"回合因错误结束"，可能只有 Notification 的 quota_* 会来（**待核实**）。两个都挂上就是为了兜底。
- quota_* 的 `message`/`title` 内容和有没有额外字段都没有文档示例（**待核实**），所以原样保存，不做解析。
- `rate.json` 由状态栏写，可能滞后几秒到几分钟；所以记了 `rate_ts`，周报要自己判断新不新。
- 子 agent 撞限额时，StopFailure 是挂在父会话还是子会话的 session_id 上，未知。
- 安装器用 `json.dump(indent=2)` 重写整个 settings 文件。当前文件本来就是 2 空格缩进，实测只多了末尾换行；如果所有者以后手工改成别的格式，会被规范化（内容不变）。如果 settings 是符号链接，`os.replace` 会把它换成普通文件（现在不是符号链接）。
- 默认备份目录是本套件的 `backups/`（和 install_sessionend 一样）。

---

## 总结（5 行）

1. 字段已对照官方 hooks.md 原文核实：StopFailure 的 `error`/`error_details`/`last_assistant_message`，Notification 的 `notification_type`/`message`/`title`，以及三个 quota_auto_resume_* matcher。quota_* 的消息内容"待核实"，已做容错。
2. 新增 `hooks/limit_logger.py`：只追加写 `state/limit_events.jsonl`（带 rate.json 快照），不输出、永远 exit 0；超过 5MB 改名到 `_old`，不覆盖、不删除。
3. 新增 `tools/install_limit_logger.py`：幂等、先备份、打印一行撤回命令，支持 `--dry-run` 和 `--uninstall`；在 scratchpad 和临时副本上试过首次、二次、已有别的 StopFailure 三种情况，真实 settings 没碰。
4. `tools/tests_r4F.py` 22/22 通过；scorecard 场景 LL01–LL04 在 scratchpad 副本上 4/4 通过，代码在第 5 节等所有者并入。
5. 所有者安装：`python3 ~/Documents/Projects/claude-usage-saver/tools/install_limit_logger.py`；撤回：`python3 ~/Documents/Projects/claude-usage-saver/tools/install_limit_logger.py --uninstall`。

---

## F2（打回后，2026-11-06）

依据：`reports/round4_codex_review_CEF.md` 末尾"3．F：打回"（第 1 次）。所有者已经在真实 settings 上装好了 logger。本节**没有改真实 settings**，只在真实 settings 上跑了两次 `--dry-run`（安装和卸载各一次），两次都只打印、不写文件。

改动前的备份（cp -p）：`backups/limit_logger.py.pre-r4F2`、`backups/install_limit_logger.py.pre-r4F2`、`backups/tests_r4F.py.pre-r4F2`、`backups/round4_impl_F.md.pre-r4F2`。

### 1. `--uninstall` 误删同组其它 hook：已修
- 原来只要一组里有 limit_logger，就整组删掉。现在逐条看组里的 `hooks`，只删命令里含 `limit_logger.py` 的条目。组里还剩别的 hook 就保留这个组（matcher 也不动）。组空了才删组，事件数组空了才删事件，`hooks` 空了才删 `hooks`。
- 新增测试：
  - I11：同一组里有 `/other/alert.sh` 和 logger 时，StopFailure 和 Notification 两边都只删 logger，组、matcher 和 alert.sh 都留着。
  - I12：组里只有我们的条目时删组；第二次 uninstall 不写文件、不备份。
- 对真实 settings 的影响：现在真实 settings 里两个事件下都只有 logger 这一条，`--uninstall --dry-run` 显示"各删 1 条"，结果和以前一样。

### 2. `_rotate()` 并发竞态：已修。做法是 flock 加 os.link，两层保护
- **第一层：文件锁。** "检查大小、轮转、追加"这三步放在 `state/limit_events.lock` 的 `fcntl.flock(LOCK_EX)` 里，同一时刻只有一个 hook 能轮转。锁文件会留在 state/ 里，不删。
- **第二层：不会覆盖的改名。** 轮转不再用 `os.rename`（目标已存在时会悄悄覆盖），改成 `os.link(原名, 目标)` 再 `os.unlink(原名)`。目标已存在时 `link` 会原子地报 `FileExistsError`，然后依次换下一个名字：先试 `limit_events_old.jsonl`，再试 `limit_events_old.<时间>.<pid>.<0..19>.jsonl`。原名已经被别的进程轮转走了，就直接返回；文件系统不支持硬链接时，也直接返回，宁可这次不轮转。
- **为什么不只加锁，也不只靠"文件名带时间戳和 pid"**：
  - 只加锁的话，拿不到锁（比如 fcntl 不可用）时会退化回原来的竞态。
  - 只靠时间戳加 pid，名字仍然可能撞上（pid 会复用）。
  - link 在内核层面保证"永远不覆盖"，锁减少多余的轮转，两个合起来最稳。
  - 唯一的副作用：如果锁失效又真的撞上竞态，可能多出一个很小的 `_old.*` 文件，但不会丢数据。
- 新增测试：
  - L13：复现 Codex 描述的交错。把 getsize 强制改成"超限"，`_old` 已经被占用，连续让两个轮转都执行（第二个轮转把第一个刚新建的文件也转走），最后 A、B、C 三份内容全部保留，没有覆盖。这个用例是在单个进程里按顺序重现交错的步骤，不是真实的并行时序。
  - L14：真实磁盘并发。30 个 hook 进程同时写一个超过 5MB 的文件，原来的大文件逐字节完整地落在其中一个 `_old` 里，30 条新记录一条不少。

### 3. 测试在沙箱里无法创建临时目录：已修
- `tests_r4F.py` 现在按这个顺序找临时目录：
  1. 环境变量 `$R4F_TMP`（比如指向 scratchpad）；
  2. `tempfile.mkdtemp`；
  3. 都不行时，用项目内的 `state/_test_tmp/r4F-<用例>-<pid>-<时间>/`。每个用例跑完后，用 `os.rename` 移到 `state/_to_delete/`，**不 rm**，由所有者清理。
- `R4F_FORCE_FALLBACK=1` 可以强制走第 3 条退回路径。
- 实测，三种方式都是 **26/26 通过**：
  - 正常 tempfile：`python3 tools/tests_r4F.py`，合计 26：通过 26，失败 0，跳过 0。
  - 模拟沙箱：用 `/private/tmp/claude-501/-Users-lihongrui/d9208a89-f883-41bb-a9bf-0f35275c785a/scratchpad/r4F/run_notmp.py` 把 `tempfile.mkdtemp`、`TemporaryDirectory`、`mkstemp` 全部改成抛 `PermissionError`，结果 26/26 通过。26 个用例目录都已经移到 `state/_to_delete/`，`state/_test_tmp/` 是空的。
  - `R4F_TMP=<scratchpad>/r4F/tt`：26/26 通过，用例目录移到了 `<scratchpad>/r4F/tt/_to_delete/`。
- 提醒所有者：`state/_to_delete/` 里现在有 26 个测试目录，合计约 20MB，主要是 L09–L11、L14 的 5MB 测试文件，需要手动清理。`state/_test_tmp/` 目录本身留着，是空的。
- 如果沙箱连 `subprocess` 都不让启动，hook 和安装器类的用例还是会失败。这是沙箱本身的限制，不在这次修复范围内。

### F2 其他说明
- 真实 settings 不需要动。hook 的命令路径没变，真实环境下次触发时就会用上新的 `limit_logger.py`。
- 第 4 节的安装命令和撤回命令不变。卸载现在只删 logger 自己的条目，不会误删别的 hook。
- 第 5 节 scorecard 场景代码不受影响（LL04 的轮转断言仍然成立：`_old` 原本不存在时，目标名还是 `limit_events_old.jsonl`）。已经用新 hook 在 scratchpad 的 scorecard 副本上重跑 LL01–LL04，4/4 通过。

## F3（第 2 次打回后，2026-11-06）

Codex 在 `reports/round4_codex_review_F2.md` 末尾提了 4 处问题，这次全部修完。测试从 26 条增加到 33 条，实跑 33/33 通过。真实 `~/.claude/settings.json` 没有改，只跑了安装和卸载各一次 `--dry-run`。

改动前的备份（cp -p）：`backups/limit_logger.py.pre-r4F3`、`backups/install_limit_logger.py.pre-r4F3`、`backups/tests_r4F.py.pre-r4F3`、`backups/round4_impl_F.md.pre-r4F3`。

### 先复现（对旧代码实跑）
复现脚本是 `/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/r4F3/repro.py`。把旧版本复制到 scratchpad 里运行，输出如下：
```
[1] 交错（A 在 link 后暂停）：C 丢失；文件内容 ['A-BIG-000\n', 'A-BIG-000\n', 'OLD0\n']
[2] 锁被占：12.00s 仍未返回（被 12s 超时杀掉）
[3] 短写：半条 JSON（183 字节，结尾换行=False）
[4] 卸载：/other/limit_logger.py 被删；剩下 []
```
同一个脚本对新代码的输出：
```
[1] 交错（A 在 rename 后暂停）：C 保留；文件内容 ['A-BIG-000\n', 'B-BIG-000\n', 'C-NEW\n', 'OLD0\n']
[2] 锁被占：0.33s 返回 rc=0，写入 1 行
[3] 短写：完整一行（367 字节，结尾换行=True）
[4] 卸载：/other/limit_logger.py 保留
```
另外，把新的 tests_r4F.py 对旧代码运行：新加的 L15–L19 和 I13 共 6 条全部失败，原有的 26 条通过。I14 在副本里是跳过，因为 scratchpad 不在 `$HOME` 下面。这说明新用例确实能抓到这 4 处问题。

### 1. 锁会无限等待：已修（最要紧）
- 现在用 `flock(LOCK_EX | LOCK_NB)`，每 10ms 重试一次，最多等 `LOCK_WAIT = 0.3` 秒。
- 拿不到锁时，这次不轮转，只追加记录，然后照常 exit 0。
- 实测（L16，真实子进程）：测试进程一直拿着 `limit_events.lock`，同时日志文件已经超过 5MB。hook 用 0.32 秒返回，没有任何输出，记录追加成功，也没有轮转。旧代码在同样条件下 12 秒还没返回。
- 为什么拿不到锁时还追加：追加用的是 O_APPEND，和别的追加进程不会互相覆盖。就算锁的持有者正在轮转，这条记录要么写进新文件，要么写进被挪走的旧文件，两边都会保留，不会丢。

### 2. 轮转时 link + unlink 会删掉别人的新文件：已修（换了做法，没有用 inode 比较）
新做法分三步，而且全程不再 unlink 原名：
1. `os.rename(limit_events.jsonl → limit_events_old.<时间>.<pid>.<12 位随机十六进制>.jsonl)`。这一步把当时原名下的那个文件整体原子地挪走。之后再也不碰原名，所以别的进程在原名新建的文件不可能被删。
2. `os.link(这个独一无二的名字 → limit_events_old.jsonl)`。目标已存在时会原子地失败，不会覆盖。
3. 第 2 步成功了，才 unlink 那个独一无二的名字。这个名字只有本进程知道，删掉它只是去掉一个多余的硬链接。如果第 2 步失败（`_old` 已存在，或文件系统不支持硬链接），文件就留在独一无二的名字下，它本身就是一份归档。

为什么不用 inode 比较：比较 `st_ino` 和随后的 `unlink` 之间仍然有一个时间窗口，只是变短了，竞态还在。新做法根本不删原名，这个问题就不存在了。

另外，现在只有拿到锁才会轮转，等于又加了一层保护。

L15 按 Codex 描述的交错顺序测试：让 A 在每一步文件系统操作之后暂停，B 用真实函数完成一次轮转，并在原名新建 C，然后 A 继续。`_old` 原来已存在和不存在两种情况都测了，A、B、C 和原来的 `_old` 都还在。这是在单个进程里按顺序模拟的交错，不是真实的并行时序。

L14 是真实磁盘上的并发：30 个 hook 同时写一个超过 5MB 的文件。整套测试跑了 1 次，另外单独又跑了 5 次，6 次全部通过，每次大约 0.13 秒。原大文件每次都逐字节完整地落在 `limit_events_old.jsonl` 里，30 条记录都在。

### 3. 没检查 os.write 的返回值：已修
- `_write_all` 循环调用 `os.write`，直到全部写完。遇到 EINTR 就重试。
- 写入返回 0 或抛出 OSError 时，补写一个 `\n`，让那半条记录单独成一行。
- 每次写之前，先读文件的最后一个字节。如果不是换行（比如上一个进程被超时杀掉，留下了半条），就先补一个换行。这样每条记录都从新的一行开始。
- 没有用"截断回原长度"：拿不到锁的进程也可能在同时追加，截断会把它们刚写的内容一起截掉。
- 测试（全部实跑）：
  - L17：`os.write` 每次只写 1/3，结果是恰好一行完整的 JSON。
  - L18：写到一半时抛出 ENOSPC。半条记录单独成一行，下一次 hook 的记录是完整的一行。
  - L19：文件末尾留有半条记录且没有换行，新记录自成一行。
- 有一个已知的边角情况：极少数时候，拿不到锁的进程和发生短写的进程同时写同一个文件，这条记录可能被拆成两行坏行。这需要两件很少见的事同时发生。读这个日志的代码应该跳过解析不了的行。现有的 `rows()` 会跳过空行，但遇到坏行会报错。weekly_compare 以后读这个文件时，要逐行 try/except。

### 4. 卸载按子串判断归属：已修
- 现在先用 `shlex.split` 把命令拆开，每一段展开 `~` 和 `$HOME`，再取 realpath。只有等于本套件 `hooks/limit_logger.py` 的 realpath 才算"我们的"。
- 安装时判断是否"已经装过"也用同一个规则。
- 测试：
  - I13：`python3 /other/limit_logger.py` 不管是在同一组里还是单独一组，卸载都保留它；只有它存在时，安装照样会加上我们的那条。
  - I14：本套件脚本写成 `~/…`、`$HOME/…` 或带引号的路径时，安装都能认出"已经装过"，卸载也都能删掉。
- 真实 settings 的 `--dry-run`：
  - 安装：两个事件都显示"已经装过，跳过"。
  - 卸载：显示"StopFailure 删掉 1 条；Notification 删掉 1 条"。
  - 说明已装的那两条仍然能被正确认出来。

### 顺带修的
`tests_r4F.py` 的 main 现在会处理建不了临时目录的情况，对应 Codex 表格里提到的 409 行。连项目内的临时目录也建不了时，不再在第一个用例之前直接崩溃，而是把每个用例记为失败、写明原因，最后照样打印汇总。实测把 `R4F_TMP` 指向只读路径：33 条都失败，并且打印了汇总。

### 测试结果（实跑）
- `python3 tools/tests_r4F.py`：合计 33，通过 33，失败 0，跳过 0。
- L14 额外又单独跑了 5 次，都通过。
- 建议并入 scorecard 的 LL01–LL04：在 scratchpad 的 scorecard 副本上用新 hook 重跑，4/4 通过。
- 这次走的是 tempfile 路径，没有往 `state/_to_delete/` 新增任何东西。里面的 26 个 `r4F-*` 目录是 F2 留下的；其余 `r4D-*` 目录是别的 agent 留下的。

### 撤回 F3（恢复到 F2 版本）
```
cd ~/Documents/Projects/claude-usage-saver; cp -p backups/limit_logger.py.pre-r4F3 hooks/limit_logger.py; cp -p backups/install_limit_logger.py.pre-r4F3 tools/install_limit_logger.py; cp -p backups/tests_r4F.py.pre-r4F3 tools/tests_r4F.py; cp -p backups/round4_impl_F.md.pre-r4F3 reports/round4_impl_F.md
```
注意：撤回会把第 1 处的阻塞锁问题一起恢复。如果只是想停用 logger，用第 4 节的卸载命令：`python3 ~/Documents/Projects/claude-usage-saver/tools/install_limit_logger.py --uninstall`。

### F3 其他说明
- 真实 settings 不需要动：hook 的命令路径没变，下次触发时会自动用上新版 `limit_logger.py`。
- 第 5 节的 scorecard 场景代码不受影响：`_old` 原本不存在时，归档名还是 `limit_events_old.jsonl`，LL04 的断言仍然成立。
