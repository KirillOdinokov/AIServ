# Миграция на Linux-сервер (Ubuntu)

Перевод FLUX Image Generator + Ollama с Windows на выделенный Ubuntu-сервер.

## Архитектура

```
Ubuntu Server 24.04
├── Ollama        (:11434) — LLM (qwen3-coder, qwen3, ...)
├── VRAM-прокси   (:11435) — проверяет VRAM перед LLM-запросом
├── ComfyUI       (:8188)  — генерация изображений
└── FastAPI       (:8000)  — веб-интерфейс + API
```

Доступ с рабочего ПК — через **Tailscale + SSH** (RDP не нужен):
- SSH — управление сервером.
- Веб-интерфейс `http://<server-ip>:8000` — в браузере.
- opencode на ПК — подключается к моделям через `http://<server-ip>:11435/v1`.

## Перенос файлов

Скопируйте на сервер **только код**, без моделей (они качаются заново):

```
scp -r config.py generator.py task_queue.py store.py main.py \
    schemas.py comfy_client.py downloader.py llm_client.py llm_store.py \
    llm_queue.py ollama_proxy.py launcher.py \
    requirements.txt run.sh install.sh static/ \
    user@server:/path/to/flux/
```

Токены (`hf_token.txt`, `deepseek_token.txt`, `ollama_token.txt`) — создайте заново
на сервере (НЕ копируйте секреты в git/облако).

Модели не переносим — качаем заново (сотни ГБ, но надёжнее).

## Установка

1. Установить Ubuntu Server 24.04 (можно с USB).
2. Поднять Tailscale:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
3. Скопировать проект (см. выше).
4. Запустить установку:
   ```bash
   bash install.sh
   ```
   Первый запуск установит NVIDIA-драйвер и попросит перезагрузку.
   После перезагрузки — `bash install.sh` ещё раз (докачает torch/Ollama/ComfyUI).
5. Скачать модели Ollama (LLM):
   ```bash
   ollama pull qwen3-coder:30b
   ollama pull qwen3:32b
   ollama pull qwen2.5-coder:32b
   ollama pull qwen3:14b
   ```
6. Скачать модели изображений — через веб-интерфейс (раздел «Модели»).

## Запуск

```bash
./run.sh
```

## Настройка opencode на рабочем ПК

Поменяйте `baseURL` на IP сервера в `~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (server)",
      "options": {
        "baseURL": "http://<server-tailscale-ip>:11435/v1"
      },
      "models": {
        "qwen3-coder:30b": { "name": "Qwen3 Coder 30B (MoE)" },
        "qwen3:32b": { "name": "Qwen3 32B" },
        "qwen2.5-coder:32b": { "name": "Qwen2.5 Coder 32B" },
        "qwen3:14b": { "name": "Qwen3 14B" }
      }
    }
  }
}
```

Перезапустите opencode — модели появятся в `/models`.

## Автозапуск (systemd)

Чтобы сервер поднимался сам после ребута, создайте сервис:

```bash
sudo tee /etc/systemd/system/flux.service > /dev/null <<EOF
[Unit]
Description=FLUX Image Generator
After=network.target ollama.service

[Service]
Type=simple
WorkingDirectory=/path/to/flux
ExecStart=/path/to/flux/run.sh
Restart=always
User=YOUR_USER

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now flux
```

## Заметки

- VRAM-прокси блокирует LLM-запросы (503 «VRAM занята»), когда идёт генерация
  изображений. Ручная выгрузка модели — `ollama stop <model>` или `/unload`.
- Экспорт изображений — WebP (настройка `EXPORT_FORMAT` в `config.py`).
