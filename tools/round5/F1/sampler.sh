#!/bin/zsh
# 每 15 秒读一次 state/rate.json（只读），内容变了就追加一行到 rate_samples.jsonl
OUT=/private/tmp/claude-501/-Users-lihongrui/5fba328e-ac48-42cb-a44b-7fe48db3697f/scratchpad/f1/rate_samples.jsonl
SRC=/Users/lihongrui/Documents/Projects/claude-usage-saver/state/rate.json
last=""
end=$(( $(date +%s) + ${1:-5400} ))
while [ $(date +%s) -lt $end ]; do
  cur=$(cat "$SRC" 2>/dev/null)
  if [ -n "$cur" ] && [ "$cur" != "$last" ]; then echo "{\"poll\": $(date +%s), \"rate\": $cur}" >> "$OUT"; last="$cur"; fi
  sleep 15
done
