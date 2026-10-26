#!/bin/bash
# Скачивание всех LLM-моделей параллельно, с повтором при обрыве
cd /home/kirill/flux

MODELS=(
  "qwen3-coder:30b"
  "qwen3:32b"
  "qwen2.5-coder:32b"
  "qwen3:14b"
)

pull_model() {
  local m="$1"
  local log="/tmp/pull_$(echo "$m" | tr ':' '_').log"
  # повторять до успеха (обрывы сети)
  for attempt in 1 2 3 4 5; do
    echo "=== $m attempt $attempt ===" >> "$log"
    if ollama pull "$m" >> "$log" 2>&1; then
      echo "DONE $m" >> "$log"
      return 0
    fi
    echo "FAILED $m attempt $attempt, retry..." >> "$log"
    sleep 5
  done
}

# запускаем все параллельно
for m in "${MODELS[@]}"; do
  pull_model "$m" &
done

wait
echo "ALL PULLS FINISHED"
