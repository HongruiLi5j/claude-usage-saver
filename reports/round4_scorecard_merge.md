# 第 4 轮：scorecard.py 并入实施者建议的场景

改的文件只有 `tools/scorecard.py`。没有改 hooks/、~/.claude/settings.json，没有 rm、没有 push，也没读 .secret/ sealed/ test2/ tools/heldout/。
tempfile 在我的环境里可用（每个场景用 TemporaryDirectory）。

## 数字

| | 改前 | 改后 |
|---|---|---|
| 当前版 | 69/69（不适用 2：SL14、SR19） | **107/107（100%），不适用 0** |
| 初版 v0 | 17/50（34%），不适用 21 | 20/58（34%），不适用 49 |

当前版没有失败的场景。`python3 tools/scorecard.py --version all` 连跑了几次，TF（test_filter）场景没有波动，全部通过。
这个结果是在 D2 和 test_filter 实施者可能正在改文件的时候跑的；这里的 107/107 只对应跑的那一刻。

## 去掉的 na 标记
- SL14 的 `"current": "待 D…"`：去掉后按正常场景跑，通过。
- SR19 的 `"current": "待 G…"`：去掉后按正常场景跑，通过。
- 全文再搜过，没有其他带"待 D / 待 G"或类似字样的 na。v0 的 na（功能在初版里不存在）原样保留。

## 并入的场景
### G（reports/round4_impl_G.md）：8 个，编号改成 SR20–SR27
G 报告里的编号 SR18–SR25 与 scorecard 里已有的 SR18（变异自检）、SR19（compact 真实格式）冲突，所以按顺序顺延 +2：

| G 原编号 | 现编号 | 内容 |
|---|---|---|
| SR18 | SR20 | cwd 在 HOME 外或 mc-option-pricer：不拿别的项目的交接 |
| SR19 | SR21 | `.git` 是文件也算项目根 |
| SR20 | SR22 | 两个进程先后 /clear 各拿各的摘要 |
| SR21 | SR23 | 旧版单指针文件仍能读 |
| SR22 | SR24 | 5 小时长会话 3.5 小时前写的交接不截断 |
| SR23 | SR25 | 无标记交接，早期 Write 之后追加 3MB 或相对路径仍注入 |
| SR24 | SR26 | compact 只取第一节红线/下一步 |
| SR25 | SR27 | 标记在第 2 行或被 ``` 包住 |

另加 `sr_other` 辅助函数。我自己在 scorecard 里重跑 `--only SR18,…,SR27,SL14`，当前版 11/11 通过（G 报告说 8/8，一致）。
v0 栏：SR20、SR21 无 na，v0 实跑都是 ✗；SR22–SR27 按 G 的建议标 v0 不适用。

### D（reports/round4_impl_D.md 的建议表）：28 个
`cg()` 加了 `sid`、`extra_rows` 参数；`wc()` 加了 `now_expr`、`history` 参数；新增辅助函数 `win / put_rate / cg_mode / asst_row`。

- statusline：SL07、SL08、SL09（D 原 SL07、SL10、SL11）；SL10、SL11（D 原 SL12、SL13，项目路径匹配）；SL12、SL13、SL15（D 原 CX10A/B/C，sampled_at）。
- context_guard：CG10–CG13；CG14、CG15（D 原 CX08、CX09）；CG17、CG18（D 原 CX14、CX15）；CG19–CG22（D 原 CXR1–CXR4，会话去重）；CG23–CG27（D 原 LA1、LA3、LA4、LA5、LB1）；CG28（D 原 CX11G，状态栏与守卫一致性网格）。
- weekly_compare：WC08、WC09。
- 没并入：D 表里没列的 SL07E、CX11、LA2、WC10。没有写任何针对去重坏记录或坏 stdin 的新场景。
- v0 栏：功能在初版不存在的标 na（额度分档、按项目阈值、封顶、会话去重等）。其余 v0 实跑：CG17、CG27、WC09 为 ✗，SL09、CG22、WC08 为 ✓。

注意：CG19–CG22 是会话去重的场景，是 D 第一版的行为。D2 正在改去重，如果 D2 改完后这几个变红，要先看是 D2 改了预期行为还是引入了回归。

## 失败的场景
无（当前版 107/107）。没有改 hook 去迁就。

## 追加任务：修 SR19 断言漏检
原断言只比较 `N3-RED-1` 与 `N3-NEXT-NEW` 的位置，所以 `N3-RED-1 N3-NEXT-NEW N3-RED-2 N3-RED-3` 也能通过。
改法：抽成 `sr19_ok(ctx)`，要求三条红线都出现、`N3-NEXT-NEW` 出现、没有 `OLD-NEXT`，且**每一条**红线的位置都在 `N3-NEXT-NEW` 之前。
用合成输出验证：

| 输出 | 旧断言 | 新断言 |
|---|---|---|
| `红线\nN3-RED-1 N3-NEXT-NEW N3-RED-2 N3-RED-3`（反例） | 通过（漏检） | **失败** |
| `N3-RED-1 N3-RED-2 N3-RED-3 N3-NEXT-NEW`（正确） | 通过 | 通过 |
| 缺 RED-3 | — | 失败 |
| 正确输出后面带 `OLD-NEXT` | — | 失败 |

真实 hook 输出下，SR19 当前版仍通过（三条红线都在下一步之前）。

## 撤回命令
```
cp -p ~/Documents/Projects/claude-usage-saver/backups/scorecard.py.pre-r4merge ~/Documents/Projects/claude-usage-saver/tools/scorecard.py
```
注意：`--version all` 会覆盖 `reports/scorecard.md`，并在 `reports/scorecard_history.json` 里写当天一条；撤回脚本不会恢复这两个文件，需要的话重跑一次 `python3 tools/scorecard.py --version all`。
