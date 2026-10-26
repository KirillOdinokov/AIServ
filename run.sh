#!/usr/bin/env bash
# Запуск FLUX Image Generator (ComfyUI + VRAM-прокси + сервер) на Linux
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  FLUX Image Generator"
echo "============================================"

if [ ! -d ".venv" ]; then
    echo "Создаю виртуальное окружение..."
    python3 -m venv .venv
fi

source .venv/bin/activate
python3 launcher.py
