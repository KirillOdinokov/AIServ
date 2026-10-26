#!/usr/bin/env bash
# Установка FLUX Image Generator на свежий Ubuntu Server (22.04/24.04)
# Запускать: bash install.sh
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  Установка FLUX + Ollama + ComfyUI (Linux)"
echo "============================================"

# ─── 1. Базовые пакеты ───────────────────────────────────────────────────────
echo "[1/7] Установка системных пакетов..."
sudo apt-get update
sudo apt-get install -y \
    python3 python3-venv python3-pip \
    git curl wget lsof \
    build-essential

# ─── 2. Драйвер NVIDIA + CUDA ────────────────────────────────────────────────
echo "[2/7] Установка драйвера NVIDIA и CUDA toolkit..."
if ! command -v nvidia-smi &> /dev/null; then
    sudo apt-get install -y nvidia-driver-550
    echo "  Драйвер установлен. ТРЕБУЕТСЯ ПЕРЕЗАГРУЗКА сервера."
    echo "  После перезагрузки запустите: bash install.sh"
    exit 0
fi
nvidia-smi

# CUDA toolkit (нужен для сборки torch из исходников — обычно не нужен, torch ставится готовым)
# Оставим как есть: torch ставится wheel-файлом с CUDA внутри.

# ─── 3. Виртуальное окружение + зависимости ─────────────────────────────────
echo "[3/7] Создание виртуального окружения..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

echo "[4/7] Установка PyTorch с CUDA (cu124)..."
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

echo "[5/7] Установка Python-зависимостей..."
pip install -r requirements.txt

# ─── 6. Ollama ───────────────────────────────────────────────────────────────
echo "[6/7] Установка Ollama..."
if ! command -v ollama &> /dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
fi
sudo systemctl enable --now ollama

# ─── 7. ComfyUI ──────────────────────────────────────────────────────────────
echo "[7/7] Установка ComfyUI..."
if [ ! -d "ComfyUI" ]; then
    git clone https://github.com/comfyanonymous/ComfyUI.git ComfyUI
fi
cd ComfyUI
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
cd ..

echo ""
echo "============================================"
echo "  Установка завершена."
echo "============================================"
echo ""
echo "Дальнейшие шаги:"
echo "  1. Положите токены в файлы:"
echo "     - hf_token.txt      (Hugging Face токен для gated-моделей)"
echo "     - deepseek_token.txt (DeepSeek API ключ)"
echo "     - ollama_token.txt   (Ollama Cloud ключ)"
echo "  2. Скачайте модели Ollama (LLM):"
echo "     ollama pull qwen3-coder:30b"
echo "     ollama pull qwen3:32b"
echo "     ollama pull qwen2.5-coder:32b"
echo "     ollama pull qwen3:14b"
echo "  3. Скачайте модели изображений через веб-интерфейс"
echo "     (или через /api/models/{id}/download)."
echo "  4. Запустите: ./run.sh"
echo ""
echo "  Доступ: http://<server-ip>:8000  (веб-интерфейс)"
echo "          http://<server-ip>:8188  (ComfyUI)"
echo "          http://<server-ip>:11435 (VRAM-прокси)"
