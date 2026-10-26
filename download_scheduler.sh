#!/bin/bash
# Планировщик скачивания моделей (Ollama + ComfyUI).
# Разрешает качать: будни 19:00–07:00, выходные — весь день.
# Днём в будни — останавливает активные загрузки.

MODELS_FILE="/home/kirill/flux/models_to_download.txt"
FLUX_DIR="/home/kirill/flux"

is_allowed_time() {
    local now_day=$(date +%u)   # 1-7 (1=пн, 7=вс)
    local now_hour=$(date +%H)

    if [ "$now_day" -ge 6 ]; then
        return 0
    fi

    if [ "$now_hour" -ge 19 ] || [ "$now_hour" -lt 7 ]; then
        return 0
    fi

    return 1
}

stop_downloads() {
    pkill -f "ollama pull" 2>/dev/null
    pkill -f "download_comfy.py" 2>/dev/null
    pkill -f "download_all.py" 2>/dev/null
}

if is_allowed_time; then
    echo "[$(date)] Разрешено качать — запускаю недостающие модели"

    # Ollama модели
    while read -r model; do
        [ -z "$model" ] && continue
        if ollama list 2>/dev/null | grep -q "${model%%:*}"; then
            echo "[$(date)] $model уже есть, пропускаю"
            continue
        fi
        if ! pgrep -f "ollama pull $model" >/dev/null; then
            echo "[$(date)] Стартую скачивание $model"
            nohup ollama pull "$model" >> "/tmp/pull_${model%%:*}.log" 2>&1 &
        fi
    done < "$MODELS_FILE"

    # ComfyUI видео-модель (Wan 2.1)
    if ! pgrep -f "download_comfy.py" >/dev/null; then
        echo "[$(date)] Стартую скачивание видео-моделей (Wan 2.1)"
        cd "$FLUX_DIR"
        nohup .venv/bin/python download_comfy.py >> /tmp/download_comfy.log 2>&1 &
    fi
else
    echo "[$(date)] Дневное время буднего дня — останавливаю загрузки"
    stop_downloads
fi
