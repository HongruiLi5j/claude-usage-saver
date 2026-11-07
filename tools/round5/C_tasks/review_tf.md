复核对象：工作目录下的 test_filter.py。只看这一个文件，不要去别处找它的其它版本、报告、测试或历史记录。

它是 Claude Code 的 PreToolUse(Bash) hook：从 stdin 读 {"tool_input":{"command":...}}，如果命令里有测试命令（pytest 等），就把测试那一段改写成"完整输出存日志、只打印失败摘要"，其余段原样保留。核心函数是 rewrite(cmd, logdir) 和 split_segments(cmd)。设计目标：改写后的命令和原命令行为一致（语法、退出码、非测试段的输出都不能变）；看不准就放行（不改写）。

请找出会让"改写后的命令和原命令行为不同"的 bug。用 bash 实跑：对同一条命令，比较原命令和 rewrite() 结果的 `bash -n` 与执行结果（退出码、输出）。pytest 可以用一个按参数控制退出码的假 pytest 函数或脚本代替，logdir 用工作目录下的子目录。重点看拆段逻辑能不能正确处理各种 shell 写法。
