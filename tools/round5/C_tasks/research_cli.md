调研两个命令行工具的非交互用法。本机已装 codex（`codex exec`）和 claude（`claude -p`），以本机安装的版本为准；可以跑 `--help`，可以查官方网页文档。不要真的执行 `claude -p` 或 `codex exec`（会花额度），只看帮助和文档。

Q1: `codex exec` 要把 agent 的最后一条消息写到一个文件里，用哪个参数？
Q2: `codex exec` 怎样做到不在磁盘上保存会话文件？在 `codex exec` 里怎样打开网页搜索（给出能用于 exec 的写法）？
Q3: `codex exec` 怎样把推理强度设成 low（给出命令行写法）？
Q4: `claude -p` 怎样输出一个包含 token 用量的 JSON 结果？这个 JSON 里哪些字段是用量和费用？
Q5: `claude -p` 怎样不把会话保存到磁盘？`--bare` 模式会跳过哪些东西、对登录方式有什么限制？
