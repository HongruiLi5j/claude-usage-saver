# 独立盲测：实施者不应查看

这是保留集，不应作为实现者调参、逐题修补或训练的输入。需要保持下一轮盲测效力时，请只向实施者提供失败类别，而不要开放本目录。

出题只依据 `使用说明.md`、根目录 `README.md`、`config.json` 以及 `hooks/` 文件开头的注释/docstring。没有读取 hook 函数体、`tools/scorecard.py`、`tools/tests_r4*.py`，也没有读取 `.secret`、`sealed`、`test2` 或项目的 `state/`。不导入 hook，不借用已有测试代码。

## 运行

从仓库根目录执行：

```sh
python3 tools/heldout/heldout_codex.py
```

依赖 Python 3 标准库、`/bin/bash`、`/bin/sh`，以及 hook 自身需要的运行环境。每个场景单独创建临时目录，将 `HOME`、`USAGE_SAVER_STATE` 指向其中，并创建临时项目与 transcript。以子进程调用真实 hook，JSON 通过 stdin 传入；不修改仓库内已有文件。临时目录在场景结束后删除。状态栏通过 `/bin/sh hooks/statusline.sh` 调用。

每项打印一行 `PASS/FAIL + 编号 + 场景 + 原因`，最后打印总数；有任何失败退出 1，全部通过退出 0。子进程超时、非零退出、响应 JSON 错误、测试自身异常都会记为 FAIL，并继续其余场景。公开配置不可读取时打印 RUNNER FAIL 和总数后退出。

所有 test_filter 的返回命令都经 `bash -n` 检查，包括应放行的命令。只有 TF25、TF26、TF29 真正执行改写结果，payload 分别固定为 `pytest -q` 或 `set -e` 后接 `pytest -q`；PATH 最前面放临时 Python 假 pytest。不会执行其余 payload 中的 `git`、`npm`、`node`、管道、heredoc、命令替换或普通 shell 命令。hook 生成的日志/过滤包装代码会随这三个测试运行。

## 公开契约缺口与结果解释

- 文件头提到了交接首行 `MARK`，没有公开其字面值。SR04 默认用字面 `MARK`；可通过 `HELDOUT_HANDOFF_MARK='公开的实际标记'` 指定。该项失败在标记未确认前属于契约/夹具未决，不能直接认定实现有 bug。SR03、SR05、SR07 另外用 transcript 中 Write 来源验证信任，不依赖标记。
- README 没有说明 `rate.json` schema。本测试假设 `ts` 为 Unix 秒、`five_hour`/`seven_day` 对象内含百分数 `utilization` 和 ISO `resets_at`，并同步设置文件 mtime。七天重置设为约 3.5 天后，即本周期进度约 50%。CG04–CG12 的分档/过期结论依赖这个格式；48 小时视为过期是独立的测试假设，文档没有给出 TTL。未确认契约前，这些失败应先排查格式或过期定义。
- transcript 使用常见 JSONL 消息结构：顶层 `type`、`cwd`、`sessionId`，消息内含 `role`、`content`、`usage`。SessionEnd 通过 `hook_event_name=SessionEnd, reason=clear` 记录，SessionStart 通过 `source=clear/compact/startup` 区分。
- 基础阈值与项目阈值取自公开 config。额度档软线取自使用说明。配置含 2026-11-21 起封顶的字段，本轮按 2026-11-06 的文档行为出题；到封顶日期之后，涉及高阈值的场景需先明确日期契约再解释结果。脚本不会修改系统时钟或替换实现。
- PASS 只证明本场景观察到的行为；FAIL 是待核查的观察结果，不自动等于实现缺陷。特别是未公开的标记、缓存格式和 TTL，需要与实现者另行确认公开接口。

## 每个场景测什么

| 编号 | 场景及预期 |
|---|---|
| TF01 | 普通 pytest 改写并指向 testlogs。 |
| TF02 | python -m pytest 改写。 |
| TF03 | npm test 改写。 |
| TF04 | node --test 改写。 |
| TF05 | 多行独立测试改写且语法有效。 |
| TF06 | set -e 后的测试改写且语法有效。 |
| TF07 | && 测试链改写。 |
| TF08 | \|\| 测试链改写。 |
| TF09 | 参数中的空格、引号、or 不破坏改写语法。 |
| TF10 | 管道原样放行。 |
| TF11 | 重定向原样放行。 |
| TF12 | 括号子 shell 原样放行。 |
| TF13 | heredoc 原样放行。 |
| TF14 | if 多行结构原样放行。 |
| TF15 | echo 参数里的 pytest 不得触发。 |
| TF16 | 引号内测试命令文本不得触发。 |
| TF17 | USAGE_SAVER_RAW=1 绕过过滤。 |
| TF18 | --pdb 交互参数放行。 |
| TF19 | --watch 参数放行。 |
| TF20 | $() 命令替换放行。 |
| TF21 | 反引号替换放行。 |
| TF22 | 后台 & 放行。 |
| TF23 | 普通命令链不得改写。 |
| TF24 | npm install 不得误认作测试。 |
| TF25 | 假 pytest 失败：保留退出码 1、失败信息、完整日志，缩减可见输出。 |
| TF26 | set -e 下假 pytest 失败：先保留摘要和日志，再返回退出码 7。 |
| TF27 | 损坏 JSON 安静放行。 |
| TF28 | 混合命令仅包装测试段，保留普通段的原始文本和引号。 |
| TF29 | 假 pytest 成功：保留退出码 0 和成功摘要。 |
| CG01 | 无缓存、基础软线前一 token 不提醒。 |
| CG02 | 无缓存、基础软线后一 token 提醒 /clear。 |
| CG03 | 强线后提醒且 hook 退出 0。 |
| CG04 | 宽裕档 18 万不提醒。 |
| CG05 | 宽裕档 24 万后提醒。 |
| CG06 | 正常档 16 万后提醒。 |
| CG07 | 紧张档 12 万后提醒。 |
| CG08 | 周用量达到 95%，10 万后提醒。 |
| CG09 | 单独 5 小时用量达到 95%，10 万后提醒。 |
| CG10 | 快用完档 10 万前不提醒。 |
| CG11 | 48 小时前的高用量不应继续降低软线。 |
| CG12 | 48 小时前的宽裕数据不应继续提高软线。 |
| CG13 | 专属项目基础软线前不提醒。 |
| CG14 | 专属项目基础软线后提醒。 |
| CG15 | cache_read_input_tokens 应计入真实上下文。 |
| CG16 | 损坏 JSON 安静放行。 |
| SR01 | 无交接文件也从 clear 前 transcript 恢复目标和下一步。 |
| SR02 | 无标记且无编辑来源：只给交接路径，不给正文。 |
| SR03 | 有 Write 编辑来源：注入交接正文并声明是数据而非指令。 |
| SR04 | 首行为 MARK：可注入正文；标记值依赖公开契约。 |
| SR05 | compact 只注入红线/未决及下一步，解码后最多 2000 字，不重复摘要。 |
| SR06 | 具体项目 B 不继承具体项目 A 的 clear 摘要或交接。 |
| SR07 | 子目录能向上找到项目根内且有编辑来源的交接。 |
| SR08 | 无交接、无旧 transcript，clear 安静放行。 |
| SR09 | compact 不注入未受信正文，但提供交接路径。 |
| SR10 | 普通 startup 不重放 clear 摘要。 |
| SR11 | 损坏 JSON 安静放行。 |
| SL01 | 正常模型、workspace、上下文 payload 返回非空状态栏且不崩溃。 |
| SL02 | 空对象不崩溃。 |
| SL03 | 损坏 JSON 不崩溃。 |
| SL04 | 损坏额度缓存不崩溃。 |

共 60 个场景。场景按注册顺序执行，编号不保证连续出现。
