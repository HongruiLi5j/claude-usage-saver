# 第 5 轮 I：按项目自适应（项目画像）

> 负责人：I（Opus）。日期：2026-11-06。标注说明：**[实测]** 是真跑出来的数字；**[估算]** 附有计算过程；**[自写测试]** 指测试是我边实现边写的，不算独立验证。

## 0. 一句话结论
所有者要求"每个项目思路、格式都不一样，套件要能适应"。按这个要求，我把套件里写死的地方查了一遍，列出 18 处。新写了一个项目画像模块 `tools/project_profile.py`：根据 cwd 自动识别语言、测试命令、交接文件和章节写法、说明文件里的红线、项目规模，项目里可以放 `.usage-saver.json` 覆盖。已经接进 session_resume、context_guard、statusline、weekly_compare 和 test_filter，其中 test_filter 那部分正在由 B5 一起复核，见第 4 节。

已有回归全部通过：scorecard 107/107、tests_r4G 26/26、tests_r4D 42/42、tests_r4F 33/33、回放退出码 0。新测试 33/33。适应性验收矩阵：改前 3/21 格（14%），改后 21/21 格（100%）。**[实测，自写测试]**

**省额度的效果很小**（见第 8 节）。这项工作的价值在于正确性和适应性，主要不在省 token。

---

## 1. 审计：写死的假设（行号指改动前的文件，即 `backups/*.pre-r5I`）

| # | 位置 | 写死了什么 | 在什么项目里出错 | 现状 |
|---|---|---|---|---|
| 1 | session_resume.py:30 | 交接文件名只认 `HANDOFF.md`、`.claude/handoff.md` | quant-backtest 的交接是 `项目交接.md` 和 `handoff/STATUS_FOR_NEXT_ASSISTANT.md`；中文名、其它工具的写法都找不到 | **已改**：先找原名，找不到再找同义名、说明文件里提到的名、覆盖文件指定的名 |
| 2 | session_resume.py:166–167 | 章节只认"红线/未决/禁止事项/red lines"和"下一步/next steps" | Projects/HANDOFF.md 的"C. 还没做"、英文的"Constraints"、"硬规则/接下来"、"待办"都认不出，compact 时就丢掉了 | **已改**：某一类一个都找不到时，那一类才用宽的同义写法；覆盖文件可以加标题 |
| 3 | session_resume.py:54–56、68 | 项目根 = `.git` 或 `~/Documents/Projects` 下第一层；在 ~ 之外直接放弃 | 没有 git、又在 Projects 外的项目（`~/Documents/lean-mc-variance`）会一直往上爬到 `~/Documents/HANDOFF.md`，那是别的项目的交接 | **已改**：这类项目在最近的说明文件或构建清单所在目录停下。Projects 里的项目沿用原规则。在 ~ 之外仍然不找交接（原来的安全设计，保留） |
| 4 | session_resume.py:678 | 所有短回复都当成"接着做" | 上一条回复在给"1./2./3. 选哪个？"时，"1"会被误读（J 第 10 条） | **已改**：见 4.2 |
| 5 | session_resume.py:16、29 | `PROJECTS = ~/Documents/Projects`（只在 cwd 是 ~ 或 Projects 本身时，用来跨项目找最新交接） | 项目放在别的根目录时，这个兜底就不起作用 | 未改。画像模块支持 `USAGE_SAVER_CONTAINERS`，session_resume 还没接上 |
| 6 | test_filter.py:104–111 | 测试命令只认 pytest、python -m pytest、npm test、node --test | Lean 的 `lake build`，cargo、go、make test、pnpm/yarn，直接跑的测试脚本（quant-backtest 的 `.venv/bin/python tests/test_strategies.py`、本套件的 `tools/tests_*.py`）都不认 | **已改，正在随 B5 复核**（见 4.3） |
| 7 | test_filter.py:13 | 失败摘要的 grep 只有英文关键词 | 中文"失败/不通过"（scorecard 的输出），以及 Lean 的 `sorry` 警告，都会被过滤掉 | **已改**：只在画像那条路径上生效；内置规则认出的命令逐字不变 |
| 8 | test_filter.py:11 | `LOGDIR` 写死在 `~/Documents/Projects/claude-usage-saver/state/testlogs` | 套件挪了位置，或者设了 `USAGE_SAVER_STATE`，日志就不在 state 里 | **等 B5 通过后再改**。注意 scorecard.py:157 会替换这一行的字面字符串，改的时候要保留一个能被替换的字面量，或者同时改 scorecard |
| 9 | context_guard.py:8–11 | 套件路径写死；config 读不到时静默用默认值 | 套件挪位置以后读不到 config，也没有任何提示 | **已改（CG13）** |
| 10 | statusline.sh:4、48 | state 路径和 hooks 路径都写死，而且不认 `USAGE_SAVER_STATE` | 设了这个变量时，状态栏写 rate.json 的目录和守卫读的目录**不是同一个** | **已改（CG13）** |
| 11 | context_guard.py:129–137 + config.json:6 | 项目软线按目录名匹配 | 键 `AQ-sandbox` 认不出真实的沙箱 `~/Documents/AQ-sandbox-1104`；反而会误命中归档里同名的 `AQ-archive/AQ-sandbox` | 部分缓解：项目根的 `.usage-saver.json` 能精确到这一个项目。config 的键没有改，见第 9 节"所有者决定" |
| 12 | context_guard.py:17、143 | 阈值是绝对值，不看模型的上下文窗口 | 20 万窗口的模型在宽裕档下软线是 24 万，**自动压缩之前永远不会提醒** | **已改**：见 4.4 |
| 13 | context_guard.py:233 | API 报错时写进来的全 0 usage 也会被当成最新一条 | 会漏发提醒，并清掉本会话的去重记录。本机 transcript 共有 **77 条**，分布在 26 个文件里 **[实测复核]** | **已改**：见 4.5 |
| 14 | context_guard.py:265 | 提醒文案写死"handoff skill……HANDOFF.history.md" | 交接写法不同的项目，提醒里说的文件名不对 | 未改（影响小） |
| 15 | weekly_compare.py:5 | `BASE` 写死 | 套件挪位置后报告写错地方 | **已改（CG13）** |
| 16 | auto_optimize.py:4、replay_guard.py:2 | `BASE` 写死 | 同上 | 未改（离线工具，不归我） |
| 17 | `~/.claude/skills/handoff/SKILL.md`:9、18–22 | 永远写 `HANDOFF.md` 和固定章节 | 会在已有自己交接格式的项目里另起一份，等于强加格式 | 未改（不归我）。已给 H 的 skills_sync 一个通用接口，让工具在运行时去问画像，见第 7 节 |
| 18 | scorecard.py:276 | 借用 quant-backtest 的 .venv 跑真实 pytest | 其它机器上这一项会是"不适用" | 测试设施，不改 |

---

## 2. 真实项目差异表（只读浏览结构，没读代码细节；跳过了 .secret、sealed、test2、relay-room 和 ⛔ 项）

| 项目 | 位置 | git | 语言 / 规模 | 测试方式 | 说明文件 | 交接文件及格式 |
|---|---|---|---|---|---|---|
| quant-backtest | Projects | 有 | Python + JS，约 3.8k 文件（大） | `pytest.ini`（testpaths=tests），也直接跑 `.venv/bin/python tests/test_x.py`；规定必须用 .venv 里的 python | CLAUDE.md、CLAUDE.local.md、AGENTS.md、FILES.md | `项目交接.md`（90KB 日志式，章节是"一、这是什么"这种中文数字，没有红线/下一步节），另有 `handoff/` 信箱目录（STATUS_FOR_NEXT_ASSISTANT.md、TASKS.md、PROTOCOL.md） |
| regime-statarb | Projects | **无** | Python，约 110 | pyproject 里的 `[tool.pytest]` | CLAUDE.md 和 AGENTS.md 内容相同（编号章节，如"4. 研究纪律(硬规则)"）、FILES.md | 没有交接文件，进度写在 CLAUDE.md 的"## 进度"节 |
| ai_layer | Projects | 无 | Python，约 80 | tests/ 下没有配置文件，但有 .pytest_cache；**`tests/test_test2.py` 会读 sealed，不得运行** | 只有 FILES.md | 无 |
| claude-usage-saver | Projects | 有 | Python/md，约 350 | 不用 pytest，靠 `python3 tools/scorecard.py` 和 `tools/tests_r4*.py` | FILES.md，加上级的 AGENTS.md | HANDOFF.md（handoff skill 的格式，带标记，"### 红线 / 未决"、"### 下一步"）+ HANDOFF.history.md |
| Projects 本身 | — | 无 | — | — | AGENTS.md、INDEX.md | HANDOFF.md（"## 0. 红线"、"## C. 还没做"，多节叠加，后面一节覆盖前面） |
| 其他/lean-axler | Projects 下的分组目录 | 无 | Lean，11 个文件 | lake build（mathlib v4.34，`.lake` 还没建） | CLAUDE.md、FILES.md | 无 |
| 其他/exports/quant-backtest-collab | 分组目录 | 有 | Python，44 | tests/，没有配置 | 无（规则写在 README） | 无 |
| ~/Documents/lean-mc-variance | Projects 外 | **无** | Lean，9 | lake build（mathlib v4.35-rc2） | 无（只有 `教程_*.md`） | 无 |
| ~/Documents/mc-option-pricer | Projects 外 | 有 | Python，9 | 没有测试，只有 `s01_check.py`、`s02_check.py` 手动检查脚本；有 .venv | 无 | 无（只有 `教程_…路线图.md`） |
| relay-room | Projects | — | 按 INDEX.md：Node 22，`node --test` | — | — | **没有进入**（红线） |

结论：9 个项目里，交接文件有 4 种写法，测试方式有 5 种（pytest 配置、直接跑脚本、只有检查脚本、lake、项目自写的回归脚本），4 个项目没有 git，说明文件有 0 到 4 份不等。任何一种写死的格式都不可能全部适配。

---

## 3. 设计：项目画像 `tools/project_profile.py`

**项目根怎么定（就近原则）**：先找最近的 `.git`（和原规则一致）。没有 git 时，找最近的标记目录，标记包括 `.usage-saver.json`、CLAUDE.md、AGENTS.md、GEMINI.md、HANDOFF.md、pyproject、package.json、lakefile、Cargo.toml、go.mod、Makefile 等。再没有，就用容器目录（默认 `~/Documents/Projects`，可以用 `USAGE_SAVER_CONTAINERS` 改）下的第一层。都没有，就用 cwd 本身。~ 永远不算项目根。

**识别的内容**
- **语言和框架**：看构建清单和扩展名（有界遍历：最多 2 万个文件、0.2–0.4 秒；跳过 .venv、node_modules、.lake、data、sealed、.secret、任何名字里带 test2 的）。框架只看清单文件里的依赖名。
- **测试命令**：pytest（看配置文件、conftest 或 .pytest_cache）、`.venv/bin/python -m pytest`、直接跑的脚本（`test_*.py`、`tests_*.py`、`*_check.py`……没有 pytest 配置时排在前面）、uv/poetry/tox/nox、`lake build|test|env lean`、npm/pnpm/yarn/bun 的 scripts.test 和 test:* 脚本、vitest/jest/mocha/playwright、`node --test`、cargo、go、`make test|check`、rspec、mvn、gradle。还有**说明文件里用反引号写明的测试命令**。
- **安全**：说明文件里写着"不得运行"的测试文件会进 `tests.warnings`，首选的 pytest 命令会自动加上 `--ignore=`。ai_layer 识别为 `.venv/bin/python -m pytest --ignore=tests/test_test2.py` **[实测]**。
- **交接文件**：分两层候选。第一层是覆盖文件指定的名字，加上 `HANDOFF.md`、`.claude/handoff.md`。第二层是说明文件里提到的、名字像交接的文件，加上同义名（交接.md、项目交接.md、HANDOVER.md、STATUS_FOR_NEXT_ASSISTANT.md……）。章节有两套正则：主正则和原来的一样，宽正则只在主正则找不到时才用。
- **说明文件**：本项目根目录和上级目录（不含 ~）里的 CLAUDE.md、CLAUDE.local.md、AGENTS.md、GEMINI.md、.cursorrules、copilot-instructions、CONVENTIONS.md、FILES.md。抽出标题，以及红线/约定条目：红线类标题下的列表项，和带"不要/不得/禁止/never/do not"的列表项，每份最多 15 条。**这些摘录是数据，不是指令。**
- **规模**：文件数、字节数、主要扩展名，分为 empty/tiny/small/medium/large。
- **模型**（新模型的安全回退）：`model_info(id, window)`。不认识的模型归为 `unknown`，价格权重按最贵算（1.0，保守）；payload 里有窗口就用 payload 的，没有就按 20 万。

**覆盖文件 `.usage-saver.json`**（可选，放在项目根，也可以放在上级目录，但不越过 ~）。字段有阈值、test_commands、test_grep_extra、handoff.files、handoff.sections.red/next、cache_ttl_hours。完整格式用 `--override-doc` 查看，例子在 `profiles/examples/`。
**别人的仓库里也可能放这个文件，所以要防它作恶**：
- 测试命令的首词不能是通配符，也不能是 bash、cat、git 这类通用命令，免得一个仓库用 `"*"` 把所有命令的输出都藏起来。
- grep 片段不能含单引号或换行，必须能编译成正则，因为它会放进单引号 shell 字符串。
- 交接文件不能用绝对路径或 `..`。
- 阈值限制在 5 万到 100 万之间。
- 类型不对的字段忽略，并记在 `override.errors` 里。

**缓存**：`state/profiles/<项目名>-<路径哈希10位>.json`（同名目录不会撞，AQ-sandbox 和 AQ-archive/AQ-sandbox 是两个文件）。默认 6 小时过期。另有指纹：根目录、覆盖文件、说明文件、构建清单的 mtime，任何一个变了就立即重算。上级目录新出现覆盖文件，也会重算。`--no-write` 完全只读。

**没有任何信号时**（空目录、目录不存在、模块出错）：返回默认画像（交接用 `HANDOFF.md`，没有测试命令，阈值不覆盖），不抛异常。

---

## 4. 接进各部件（全部先 `cp -p` 备份，后缀 `.pre-r5I`）

### 4.1 session_resume.py（G2 已通过复核以后才改）
- **找交接**：先用第一层候选名，从 cwd 往上找到项目根。找不到，再用第二层候选名找一遍。所以**只要 HANDOFF.md 在，行为和改前完全一样**（测试 S02）。
- **不越界**：没有 git、又不在容器里的项目，在画像给的根处停，不会爬到 `~/Documents/HANDOFF.md`（测试 S04）。
- **compact 短注入**：先用主正则，加上覆盖文件给的标题。哪一类一个都找不到，那一类才改用宽同义写法（测试 S01、S03）。
- **防注入**：规则没变。没有标记、也不是上一段对话改过的交接文件，只给路径。quant-backtest 的 `项目交接.md` 已经 310 小时没改，仍然只给路径和年龄。
- **SR06（所有者定）**：同一个进程里换了 cwd 再 /clear，不带上一段的对话摘要，但交接文件照常按新的 cwd 找。被丢弃的那条指针会在锁内**记为已用**。不这样做的话，它会留下来挡住下一次 /clear：同一进程有两条新指针时，代码会按"不止一条就不猜"放弃摘要。这个问题是测试 S06 抓到的，已经修好。
- 画像模块缺失或出错时，回到改前的逻辑。

### 4.2 编号选项（J 第 10 条）
读上一段对话里主会话最后一条有文字的 assistant 回复，看结尾 1500 字。如果有 ≥2 行编号或字母选项（`1.`、`2)`、`（3）`、`A)`、`选项 2`、`Option B`），**并且**有提问的字眼（？、选哪、哪个、要不要、which、prefer……），就改用另一段提示："「1」「2」「A」按选项编号理解，不要当成接着做；对不上任何选项时，先复述选项再问选哪个"，并把选项行列出来。
只列编号的下一步、没有提问时，仍按"接着做"处理（测试 S07、S08）。

### 4.3 test_filter.py（**注意：这部分已经在真实环境生效，正在随 B5 一起复核**）
协调员发出"先别改"的时候，我已经改完了，时间约 18:45。B5 的备份 `backups/test_filter.py.pre-r4B5` 和我改后的版本逐字相同（sha 9c94c9f…）。我发现后立即通知了协调员。协调员选择让 B5 在我的版本上继续修，R5I 的改动随 B5 的复核一起审。此后我没有再动这个文件。我的改动另存为 `profiles/patches/test_filter.r5I.diff`。

改动内容：
- 只有内置规则**没认出**测试时，才按 hook 输入的 cwd 读画像，认这个项目自己的测试命令。内置规则认出的命令，改写结果和改前逐字相同（测试 T05，和 pre-r5I 版本逐条比较）。
- 画像里的 grep 片段（Lean 的 `warning:|sorry`、中文"失败"）只加在画像这条路径上。
- `split_segments` 没动，B5 修的地方就在那里，两边不冲突。

### 4.4 context_guard.py / statusline.sh / weekly_compare.py（CG13、上下文窗口）
- **CG13**：套件目录 = 脚本所在目录的上一级；state = `USAGE_SAVER_STATE`，没设就用 套件/state。statusline 用同一规则：通过 `$0` 拿到脚本目录，用环境变量传给内嵌的 python。原来的写死路径只作为兜底保留，因为 scorecard 要替换这个字面量。config.json 读不到或不是 JSON 对象时，往 stderr 打**一行**提示，然后全部用默认值。weekly_compare.py:5 也改成按脚本位置定。
- **阈值优先级**：项目的 `.usage-saver.json`（`project_thresholds()` 是轻量入口：只找根、读覆盖文件，不遍历、不写缓存）> config 的 per_project > handoff_threshold_tokens > 默认值。之后再乘档位倍数、套下限、套 Pro 封顶（测试 G03）。
- **上下文窗口**：状态栏把本会话的 `context_window_size` 记到 `state/ctx_window.json`，按 session_id 存，保留 7 天，最多 300 条。守卫按 session_id 读。知道窗口时，软线 ≤ 窗口的 85%，强线 ≤ 95%。**不知道窗口时不改阈值，也不按模型名去猜。** 原因是同一个 sonnet 既可能是 20 万窗口，也可能是 100 万窗口（本机样本是 100 万）。
- **CG11（所有者定，代码不改）**：已经把规则写进 context_guard.py 的文件头和 使用说明.md："快用完"档不看读数是否过期，窗口没有重置就一直生效。

### 4.5 全 0 usage（C 的实验发现）
新增 `_usage_total()`。`last_usage()` 跳过 input + cache_read + cache_creation 等于 0 的记录，用前面最近一条有效的。测试 G05 的场景是：200k 后面跟一条全 0 记录。新版照常提醒，去重记录保留。旧版不提醒，用同一个测试证明了 bug 确实存在。

---

## 5. 验证

### 5.1 已有回归（每次改完都重跑过，下面是最后一次）**[实测]**
| 项 | 结果 |
|---|---|
| `python3 tools/scorecard.py --version all` | 当前版 **107/107**，初版 20/58。**另外**把画像模块放进 scorecard 的沙箱再跑一遍（猴子补丁，不写报告）：107/107。原版沙箱只复制 hook，没有画像模块，所以只能证明"模块缺失时的回退路径" |
| `python3 tools/tests_r4G.py` | **26/26**。它跑的是真实 hooks/ 路径，所以画像是生效的 |
| `python3 tools/tests_r4D.py` | **42/42**；放进画像模块再跑一遍也是 42/42 |
| `python3 tools/tests_r4F.py` | 33/33（limit_logger，没改，顺手确认） |
| `python3 tools/replay/replay_test_filter.py` | **退出码 0**，`orig_ok_new_bad=0`、`rewrite_exception=0` |
| 画像路径的回放（脚本在 scratchpad，只用 `bash -n` 和 `zsh -n` 检查语法，不执行任何命令） | 19,951 条真实命令，分别在 6 个画像下回放。内置规则没改写的命令里，画像新改写了：quant-backtest 24 条、本套件 20 条、regime-statarb 18 条、mc-option-pricer 6 条、lean-mc-variance 0 条、Node/Rust/Go/Make 混合假项目 0 条。**新增语法错误都是 0** |

### 5.2 新测试 `python3 profiles/test_project_profile.py`：**33/33** **[实测，自写测试]**
- 造了 5 种项目（Python、Lean 无 git、Node、纯文档、无 git 空目录）：画像识别对、本项目测试命令被包装（Python 测试真跑一遍，rc=3 保留，失败行可见，80 行被过滤）、别的语言的测试命令不包装。
- 覆盖文件的生效和安全（O01–O03）、缓存命中、指纹和过期（C01–C03）、session_resume 的同义名、覆盖、越界、说明文件里提到的交接（S01–S05）。
- 所有者的决定和新加的修复：SR06（S06）、编号选项（S07、S08）、CG13（G01、G02）、阈值优先级（G03）、窗口（G04）、全 0 usage（G05）、model_info（M01）。

### 5.3 在真实项目上只读运行画像（`--no-write --no-cache`）**[实测]**
| 项目 | 根的判据 | 语言 | 规模 | 首选测试命令 | 找到的交接文件 | 说明文件 | 判断 |
|---|---|---|---|---|---|---|---|
| quant-backtest | git | python, js | 3784 / large（129 ms） | `.venv/bin/python -m pytest`（同时认 `python tests/test_strategies.py`，这条来自 FILES.md） | 项目交接.md、handoff/STATUS_FOR_NEXT_ASSISTANT.md、README_FIRST_…现状与下一步.md | CLAUDE.md、CLAUDE.local.md、AGENTS.md、FILES.md | 对 |
| regime-statarb | marker（无 git） | python | 111 | `.venv/bin/python -m pytest` | 无 | CLAUDE.md、AGENTS.md、FILES.md | 对 |
| ai_layer | container | python | 81 | `.venv/bin/python -m pytest --ignore=tests/test_test2.py` ⚠ | 无 | FILES.md | 对（能自动避开 sealed 测试） |
| claude-usage-saver | git | python | 351 | `python3 <test_*.py>` | HANDOFF.md（红线 / 未决；下一步） | FILES.md | **部分对**：真正的回归是 scorecard.py，这只能靠覆盖文件告诉画像（例子在 profiles/examples/） |
| 其他/lean-axler | marker | lean, mathlib | 11 | `lake build` | 无 | CLAUDE.md、FILES.md | 对（改前被当成"其他/"这个分组目录的一部分） |
| quant-backtest-collab | git | python | 44 | `python3 <test_*.py>` | 无 | 无 | 基本对 |
| lean-mc-variance | manifest（无 git） | lean, mathlib | 9 | `lake build` | 无 | 无 | 对 |
| mc-option-pricer | git | python | 9 | `.venv/bin/python <*_check.py>` | 无 | 无 | 对（这个项目确实只有检查脚本） |

修过的识别错误：
- 第一版把"在交接相关那行里提到的所有 .md"都当成交接文件（README、PROTOCOL……），后来改成只收**名字本身像交接**的文件。
- 第一版让较远的强标记压过较近的构建清单，测试 S04 抓到，改成就近。
- 没有 pytest 配置的项目，首选命令原来是 `python -m pytest`，改成直接跑的脚本优先。同时把 `.pytest_cache` 算作用过 pytest 的证据，这样 ai_layer 仍然保留 `--ignore`。

### 5.4 延迟 **[实测，quant-backtest，临时 state，各 5 次]**
- test_filter 碰到非测试命令（`ls`）：首次 62 ms（建缓存），之后中位数 22 ms，旧版 17 ms。也就是**每条 Bash 命令多 5 ms 左右**。原因是内置规则没认出的命令都会去读一次画像。优化办法：只在首词像测试运行器时才读画像。等 B5 通过后再做。
- session_resume compact：30 ms，旧版 27 ms。

---

## 6. 适应性验收矩阵 `python3 tools/round5/I_adapt_matrix.py`（以后每一轮都跑、都比）

- **行**：5 种造的项目，加 2 个真实项目（mc-option-pricer、lean-mc-variance）。"全新"列直接用真实路径；"接续"列用一份只含元数据的影子副本，**真实项目里不写任何文件**。
- **列**：
  - 全新对话：state 是空的，没有交接文件，兄弟项目里放了一份新鲜的、带标记的 DECOY 交接。
  - 接续对话：交接用**本项目自己的写法**，每行一种：覆盖文件指定名 + Guardrails/Todo；交接.md + 硬规则/接下来；英文 Constraints/Next steps；说明文件里提到的 进度记录.md + 注意事项/待办；默认 skill 格式；`.claude/handoff.md` + 未决/Next steps。另外有同一进程 /clear 留下的上一段 transcript。
  - 换模型：opus@1M、sonnet@1M、haiku@200k、claude-xyz-9（没有窗口）、claude-xyz-9（窗口填的是 "abc"），再加一项"200k 窗口 + 宽裕档 + 180k"。
- **每格检查四件事**：hook 有没有 fail-open；有没有用上本项目的约定；有没有套用别的项目的格式；不认识的模型有没有安全回退。

| 版本 | 格子通过率 | 子检查 |
|---|---|---|
| 改前（`--hooks` 指向放了 `*.pre-r5I` 备份的目录） | **3/21（14%）** | 298/329（90.6%） |
| 改后 | **21/21（100%）** | **329/329（100%）** |

改前失败的地方：
- 本项目测试命令认不出（lake build、pnpm test、直接跑的脚本），6 行。
- 本项目写法的交接注入不了，compact 取不到红线/下一步（Python、Lean、纯文档、Node 这几行）。
- 7 行都是：haiku@200k 的阈值超出窗口，"小窗口 + 宽裕档"时状态栏和守卫都不提醒。

改前 fail-open 检查全部通过。也就是说，旧 hook 从来不崩，问题都出在"不会适应"。

**局限（要如实说明）**：
- 这个矩阵是我边实现边写的，改后 100% 不算独立验证。建议派 Codex 按这个报告出一批盲测格子（不给它看实现）。
- 判断"阈值安全"时，旧版 `lines()` 没有 `window` 参数，所以退回不带窗口的调用，避免因为函数签名不同而判它失败。
- "价格"这一项只检查了 `model_info` 的回退。weekly_compare 按 token 加权算成本，不按模型价格；未知模型会归到 "other" 桶（读代码确认，没有单独测）。

---

## 7. 跨工具接口（给 Codex、Gemini、H 的 adapters/）
画像不依赖 Claude Code，任何工具都能直接调：
- **命令行**：`python3 <套件>/tools/project_profile.py --cwd DIR` 后面接以下之一：
  - `--json`：完整画像（默认）；
  - `--md`：给人或给其它 AI 看的简短摘要；
  - `--env`：输出 `USAGE_SAVER_ROOT`、`USAGE_SAVER_TEST_CMD`、`USAGE_SAVER_HANDOFF`、`USAGE_SAVER_LANGS`、`USAGE_SAVER_SOFT_TOKENS`，给包装脚本 eval；
  - `--field tests.primary` 这种：只取一个字段；
  - `--model ID [--window N]`：输出 model_info；
  - 再加 `--no-cache` / `--no-write`。
- **Python**：`get_profile(cwd)`、`match_test(argv, profile)`、`handoff_names(profile)`、`section_patterns(profile)`、`project_thresholds(cwd)`、`model_info(id, window)`、`clamp_lines(soft, strong, window)`。
- **H 的 `adapters/skills_sync.py --profile <JSON>`**：画像里加了 `skill_notes.{codex,gemini,claude}.{handoff,model-router}` 这个键。因为 skill 是全局的，这里**不写任何一个项目的格式**，只告诉工具"写交接前先运行 `project_profile.py --field handoff.found`，沿用项目已有的写法；跑测试前先取 `tests.primary`，`tests.warnings` 里的测试不要跑"。这样就把第 1 节第 17 条（handoff skill 写死 HANDOFF.md）变成了按项目适应。
- **Codex/Gemini 的 AGENTS.md / GEMINI.md**：可以加一行"开工先运行 `project_profile.py --md`"。这一行由 H 和所有者决定要不要加，我没有改任何说明文件。

---

## 8. 能省多少额度（如实说：很少）
- **test_filter 多认出的测试命令 [估算]**：
  - 回放的历史覆盖约 16 天、19,951 条命令。按 cwd 对应的画像，比内置规则多改写约 18–24 条，相当于内置那 82 条的 +25%。
  - 假设每条过滤掉 2–5k token 的输出（**没有实测**，不确定性很大），16 天合计约 4–12 万 token，每周约 2–5 万。
  - 相对于每周数亿到十亿级的 token，**不到 0.01%**。
- **session_resume 认出更多交接写法 [无法量化]**：clear 后少一些"重新探索项目"的轮次。但目前真实项目里只有 Projects/HANDOFF.md 和本套件的 HANDOFF.md 是新鲜的，quant-backtest 的那份已经很旧，只给路径。短期收益接近 0。
- **窗口收紧 [估算]**：只影响 20 万窗口的会话。本机主会话目前是 100 万窗口，11-21 换 Pro 后已有 18 万的封顶，所以额外效果是把软线从 18 万降到 17 万，占比很小。真正有用的是不认识的新模型、20 万窗口的模型开主会话这类情况：**能防止"一直不提醒，直到被自动压缩"**。
- **全 0 usage 修复**：是正确性修复。77 条记录可能各自导致一次漏提醒和一次去重清零，对 token 的影响小，没法单独量化。
- 结论：这一项主要换来**正确性和适应性**（矩阵 14% → 100%），不是省 token。

---

## 9. 未做 / 后续
- test_filter 的 LOGDIR（第 1 节第 8 条）和"每条 Bash 多 5 ms"的优化：等 B5 通过复核后再做。
- session_resume 的 `PROJECTS` 常量还没接上 `USAGE_SAVER_CONTAINERS`（第 1 节第 5 条）。auto_optimize.py、replay_guard.py 的写死路径不归我。
- 宽同义写法里故意**没有**放"待做"。Projects/HANDOFF.md 里"## 2. 待做事项 A"是被后面一节覆盖掉的旧内容，放进去会被误选。这是启发式，换一个项目可能需要用覆盖文件补。
- 本次给真实 hooks 的改动**已经生效**：真实 settings 直接调用 hooks/*.py。生效的有 session_resume（同义交接、SR06、选项提示）、context_guard 和 statusline（CG13、窗口、全 0 usage、项目覆盖）、test_filter（画像路径）。`state/profiles/` 会开始出现缓存文件。真实 `~/.claude/settings.json` 没有改。
- 关于"不 rm"：测试和矩阵在**系统临时目录里我自己建的沙箱**用了 `shutil.rmtree` 清理（和 scorecard 的 tempfile 自动清理做法一样）；scratchpad 里也用过一次 `rm -rf`，只清了我自己的延迟测量目录。项目文件一个都没删。

---

## 总结
**做了什么**
1. 审计出 18 处写死的假设，每处都写了文件:行号和会出错的项目。
2. 只读调查了 9 个真实项目，做了差异表。
3. 新写 `tools/project_profile.py`：识别项目、支持覆盖文件、带缓存、有安全限制、跨工具可用的命令行。
4. 接进 session_resume（同义交接文件和章节、不越界、SR06、编号选项）、context_guard 和 statusline（CG13、项目覆盖优先级、上下文窗口、全 0 usage）、weekly_compare（CG13）、test_filter（画像测试命令，随 B5 复核）。
5. CG11 写进了 hook 文件头和 使用说明.md。
6. 新增测试 `profiles/test_project_profile.py`（33 项）和适应性矩阵 `tools/round5/I_adapt_matrix.py`。

**数字**（都是实测，测试是自写的）

| 项 | 结果 |
|---|---|
| scorecard | 107/107（放进画像模块也是 107/107） |
| tests_r4G | 26/26 |
| tests_r4D | 42/42 |
| tests_r4F | 33/33 |
| 回放 | 退出码 0，改坏 0；画像路径另外回放 6 个画像，新增语法错误 0 |
| 新测试 | 33/33 |
| 适应性矩阵 | 格子 3/21（14%）→ 21/21（100%）；子检查 90.6% → 100% |
| 省额度 | 估算每周不到 0.01%，很小 |

**要所有者决定的事**
1. 接不接受 test_filter 的画像部分（已经生效，正在随 B5 复核）。
2. 要不要把 `profiles/examples/claude-usage-saver.usage-saver.json` 放到本套件根目录的 `.usage-saver.json`，让画像认出 scorecard 和 tests_r4*。
3. config.json 里的 `AQ-sandbox` 键认不出真实的 `AQ-sandbox-1104`：是改键，还是在那个目录放一个 `.usage-saver.json`。
4. 要不要让 H 的 skills_sync 带上画像的 skill_notes，让 handoff skill 先问画像再写交接。
5. 要不要派 Codex 给适应性矩阵出一批独立的盲测格子。

**撤回命令**（单行；test_filter 的画像部分要等 B5 复核完，再用 `patch -R hooks/test_filter.py profiles/patches/test_filter.r5I.diff` 撤）
```
cd ~/Documents/Projects/claude-usage-saver; cp -p backups/session_resume.py.pre-r5I hooks/session_resume.py; cp -p backups/context_guard.py.pre-r5I hooks/context_guard.py; cp -p backups/statusline.sh.pre-r5I hooks/statusline.sh; cp -p backups/weekly_compare.py.pre-r5I tools/weekly_compare.py; cp -p backups/使用说明.md.pre-r5I 使用说明.md; mkdir -p _to_delete; mv tools/project_profile.py profiles tools/round5/I_adapt_matrix.py state/profiles _to_delete/ 2>/dev/null
```
注意：
- session_resume 的备份是 G2 通过复核的那一版，撤回后 SR06 和选项提示会一起消失。
- 只撤 hooks、不撤 `tools/project_profile.py` 也没问题，hook 会回到改前的行为。
- 反过来，如果 test_filter 还留着画像部分、却把 `tools/project_profile.py` 移走了，test_filter 也会自动回到改前的行为（画像加载失败时就是这样处理的）。
