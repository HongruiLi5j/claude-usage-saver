复核对象：工作目录下的 context_guard.py 和 config.json。只看这两个文件，不要去别处找它们的其它版本、报告、测试或历史记录。

context_guard.py 是 Claude Code 的 UserPromptSubmit hook：从 stdin 读 {"session_id","transcript_path","cwd"}，从 transcript 末尾算出主会话上下文 token 数 n，超过软线/强线（见 config.json 和 tiers()/lines()）就往 stdout 输出提醒；用 $USAGE_SAVER_STATE/remind.json 做跨消息、跨会话的提醒去重（remind_kind）；get_mode() 读 rate.json 判断额度档位并写 mode.json。设计目标：该提醒时绝不静默漏掉；hook 出错不能挡住用户消息；不写坏别的会话共用的状态文件。

请找出违反这些目标的 bug。跑代码时把 USAGE_SAVER_STATE 和 HOME 都设成工作目录下的子目录（代码里写死的 ~/Documents/... 路径会随 HOME 变）。可以 import 这个模块直接调函数，也可以构造 stdin 和假 transcript 跑 main()。
