# 第 5 轮共同说明（每个 agent 开工前必读）

## 目标
所有者 11-06 要求：大规模、高质量、全用 Opus，循环"收集资料 → 讨论 → 实践 → 审核 → 分析"，**每一轮都必须比上一轮好**（用数字证明）。本轮额度不用省（所有者能刷新周额度），但**套件本身的目标是帮所有者省额度**，所以每项优化都要量化"每周省多少 token / 占总成本多少"。

## 背景（不要重做）
- 已做过的优化：reports/improvements.md（#1–#20）。第 4 轮数字：每次请求成本 32.1k→21.5k（-33%，<3 天数据）、上下文平均 211k→111k；自测 scorecard 107/107；Codex 盲测 56/60；真实命令回放 19,951 条改坏 0。
- 第 5 轮起点分析：**reports/round5_where_tokens_go.md**（先读它）。脚本在 tools/round5/。
- Codex 复核写法：`tools/codex_review.sh <名字> <任务文件>`（输出 reports/codex_<名字>.md，进程退出即完成）。

## 红线
- **绝不修改真实 `~/.claude/settings.json`**、`~/.claude/settings.local.json`、`~/.claude/agents/`。需要改设置的优化：做成"可一条命令应用 + 一条命令撤回"的脚本或补丁，在隔离环境（临时 HOME、`--settings <临时文件>`、项目副本）里验证，报告里写清楚，由主会话问所有者后再应用。
- 不 rm（要删就 mv 到项目 `_to_delete/`）；不 git push / commit；改任何已有文件前 `cp -p` 备份到 backups/（后缀 `.pre-r5<你的代号>`）并在报告里给撤回命令。
- 不读 `.secret/`、`sealed/`、任何 test2、`tools/heldout/`（盲测题，除非你就是盲测负责人）；不碰 relay-room、quant-backtest 的代码。
- 读 `~/.claude/projects/` 的 transcript 只做统计（usage、工具名、长度），**不把对话正文抄进报告**。
- 跑 `claude -p` 无头实验是允许的（会消耗额度，本轮可以）。实验要在临时目录/项目副本里，不要在真实项目里写文件。
- 文件归属（同一时间只有一人改）：见下表。不在表里的已有文件，只读。

| 代号 | 负责 | 可写文件 |
|---|---|---|
| A | 固定开销：工具定义 / skills / MCP / 系统提示 | reports/round5_A.md、tools/round5/A_* |
| B | 长上下文与无人值守（通宵）运行 | reports/round5_B.md、tools/round5/B_* |
| C | 把阅读/复核/调研类子任务改派 Codex | reports/round5_C.md、tools/round5/C_*、tools/codex_review.sh、`~/.claude/skills/model-router/SKILL.md`（备份后） |
| D | 工具结果大小（大文件读取、长输出） | reports/round5_D.md、tools/round5/D_*、新建 hooks/read_guard.py（不安装） |
| E | 收集新资料（网页调研） | reports/round5_research.md |
| F1 | 方法论审查（统计） | reports/round5_F1_critique.md |
| F2 | 受控基准：同一批任务、有无某项优化的 token 对比 | reports/round5_F2_bench.md、tools/bench/ |
| G | 新一轮盲测（驱动 Codex 出新题） | reports/round5_G_heldout.md、tools/heldout/ |
| H | 跨工具适配（Codex CLI、Gemini CLI 等） | reports/round5_H.md、新建 adapters/ 目录 |

## 新要求（所有者 11-06 04:00）：适应每一个不同的项目，不能做死板的东西
每次新开对话做的项目思路、格式都不一样（语言、测试命令、交接文件格式、目录结构、大小、各自的 CLAUDE.md/AGENTS.md 规则）。套件的每个部件都要能**按项目自适应**：自动识别项目特征，读取项目自己的约定，没有约定时用合理的默认值，绝不能把某一个项目的格式写死。每个 agent 做自己那项时检查：你的方案在 Python 项目、Lean 项目、网页项目、纯文档项目、没有 git 的目录里是否都合理。I 负责整体。
| I | 按项目自适应（项目画像 + 各部件读取项目约定） | reports/round5_I.md、新建 profiles/ 目录、tools/project_profile.py |

## 新要求（所有者 11-06）：广泛适配不同 AI 工具
套件不能只服务 Claude Code，要能用在 Codex CLI、Gemini CLI、Cursor/Aider 之类其它 AI 编程工具上。每个 agent 做自己那项时都想一下：这项优化在别的工具上是否适用、怎么用。H 负责整体：各工具有什么等价机制（hooks / AGENTS.md / config / 上下文压缩 / 用量记录），哪些部件能共用（如 test_filter 的命令改写、交接文件、用量统计）。

## 新要求（所有者 11-06 04:10）：优化不要只限于所有者说过的东西
每个 agent 的报告末尾必须加"## 额外发现"：做本任务时看到的、**任何**能省额度或让套件更好的机会（不限本代号范围），每条写估计收益和证据。K 负责不限方向的自由探索；J 负责适应性的其它维度（新对话/新项目/新模型之外）。
| J | 适应性的其它维度（多模型讨论） | reports/round5_J_dimensions.md |
| K | 自由探索：任何没被要求但值得做的优化 | reports/round5_K_explore.md、tools/round5/K_* |

## 所有者决定（11-06 04:20）：质量优先
"别牺牲质量，如果需要就不用改变。"任何优化只要让完成率/质量下降（非劣效检验不过），就**不采用**、保持原样。省额度的限量也不能让结论失去可靠性。"持平"可以接受。

## 汇报要求
- 中文。**实测和模拟/估算分开标注**；估算给计算过程和不确定性；测试失败贴输出。
- 报告结尾一节"## 总结"：做了什么、数字、要所有者决定的事、撤回命令。
- 最终回复主会话：≤15 行的摘要 + 报告路径（主会话上下文很紧，别贴长内容）。
