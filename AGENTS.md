# Инструкция для агентов — проект AI ENIX

## Обзор

Локальный генератор изображений (FLUX через diffusers + ComfyUI) и видео, плюс
LLM-модуль (описания товаров), генерация по референсам, миниатюры категорий,
апскейл, интеграция внешнего API Nano Banana, публикация на WordPress/WooCommerce.
Работает на **Linux-сервере** (`/home/kirill/flux`) и на Windows (локальное зеркало
`E:\Оптимизация\LOCAL FLUX`).

Сервер: Ubuntu 26.04, Xeon E5-2650 v2 (без AVX2), RTX 3090 24GB, 32GB DDR3.
GPU используется и картинками, и видео, и (потенциально) локальной LLM — одна
тяжёлая модель за раз.

## Важные патчи (обязательно сохранять)

### 1. Tekken-токенизатор Mistral (ComfyUI-GGUF)

**Файл:** `ComfyUI/custom_nodes/ComfyUI-GGUF/loader.py`

**Проблема:** при загрузке текстового кодировщика Mistral (файл `.gguf`) ошибка:
```
KeyError: 'default_num_special_tokens'
в comfy/text_encoders/flux.py -> load_mistral_tokenizer -> from_tekken_json
```

**Патч:** в `gguf_tekken_tokenizer_loader` перед
`logging.info(f"Created tekken tokenizer ...")` добавить:
```python
data["config"]["default_num_special_tokens"] = len(data["special_tokens"])
```

**Когда применять:** после `git pull` ComfyUI-GGUF или переустановки ComfyUI.
На сервере патч уже применён.

### 2. Kornia на старом CPU (без AVX2)

CPU Xeon E5-2650 v2 (Ivy Bridge) поддерживает только **AVX** (без AVX2). Пакет
`kornia-rs` (kornia 0.7+) собран под AVX2 → `Illegal instruction` при импорте
ComfyUI. Использовать kornia **0.6.12**:
```bash
uv pip install --python .venv/bin/python "kornia==0.6.12"
```

### 3. NVIDIA-драйвер (зафиксировать версию)

Драйвер NVIDIA нельзя обновлять молча через `unattended-upgrades` (рассинхрон
userspace/kernel-module → `nvmlInit_v2` падает, всё GPU ломается). Пакеты
**зафиксированы** `apt-mark hold` (nvidia-driver-*, libnvidia-*, cuda). Если
обновлял драйвер вручную — после этого обязательно `reboot`.

## Модели (config.py)

| id | engine | формат | примечание |
|---|---|---|---|
| flux2-klein-4b | diffusers | safetensors | основной + multimodal (референс/лого) |
| krea2-turbo | comfyui | FP8 | img2img |
| ideogram4 | comfyui | FP8 | двойная модель, без референса |
| wan2.1-t2v-14b | comfyui | GGUF Q4_K_M | видео, натив 832×480 |
| ltx-video-13b | comfyui | FP8 | видео, натив 768×512, shift 2.05/0.95 |

Удалены: `qwen-image-2.1` и `flux2-dev-turbo` (файлы тоже).

Модели: `/data/ComfyUI/models` (symlink из `/home/kirill/flux/ComfyUI/models`),
diffusers — `/home/kirill/flux/models` (HF cache).

## Кандидаты на потом: FLUX 3 Image Klein (НЕ интегрировано)

**FLUX 3 Image Klein** (Black Forest Labs, 5.6B + Qwen3-VL-4B текст-энкодер) —
text-to-image + редактирование (до 10 референсов) + bounding-box + текст в кадре.
Релевантен референсам/лого/апскейлу, но **пока не интегрирован**.

Ключевые факты (проверено 2026-10):

- **VRAM:** официально BF16=31ГБ / FP8=26ГБ / NVFP4=25ГБ → не влезает в 24ГБ.
  Но GGUF-кванты (transformer-онли) маленькие: Q4_K_M ≈ 3.7ГБ, Q5_K_M ≈ 4.2ГБ
  (`unsloth/flux-3-image-klein-GGUF`), Q4_K ≈ 3.3ГБ
  (`DogukanUrker/FLUX.3-Image-Klein-GGUF`, для stable-diffusion.cpp).
  Итого с текст-энкодером Qwen3-VL-4B (FP8 ~4ГБ) + VAE (~1ГБ) + активации ≈
  **13–15ГБ — влезает**.
- **Промпт-апсемплер (PUP)** обязателен (иначе generic-результат). Официальный PUP
  — 9B (~18.8ГБ), но заменяется на DeepSeek API / локальную квен с system-prompt.
- **Лицензия:** FLUX Kommunity License — коммерческий вывод только при выручке
  < $5M/год. Текст-энкодер Qwen — Apache-2.0.
- **ComfyUI 0.36 НЕ поддерживает flux3**: в `model_detection.py` нет `flux3`,
  в `CLIPLoader` нет типа `flux3`. Это новая архитектура (flow-map sampling,
  `manifest.json`+`ae.safetensors`), а не расширение flux2.
- **Пути интеграции:** (1) stable-diffusion.cpp — самодостаточно, GGUF нативно, но
  это CLI (не веб); (2) ждать поддержки flux3 в ComfyUI → обновить ComfyUI с
  переносом патчей (Tekken/kornia) → добавить workflow-билдер в `comfy_client`.

## Ключевые готчасы (выявлены на практике)

- **LTX shift**: `max_shift=2.05, base_shift=0.95` (не 1.9/0.7 — старые значения
  2B-модели дают шум). В `comfy_client._build_ltxv`.
- **LTX разрешение**: натив **768×512**. Широкое 832×480 (натив Wan) у LTX даёт шум.
- **Видео length**: Wan максимум ~81 кадр, LTX ~121 кадр. Длиннее = шум (модель
  обучена на ~5с). В workflow стоит жёсткий кламп.
- **Видео timeout**: 1800с (в `generator._generate_comfy` для video, иначе 900с).
  Холодная загрузка модели + генерация ~14 мин.
- **Nano Banana**: нужен `User-Agent` в заголовках (иначе Cloudflare error 1010).
- **Idle-выгрузка**: `_last_activity` обновляется в КОНЦЕ генерации (иначе после
  долгой генерации модель выгружается мгновенно). Сторожевой таймер в generator.
- **Видео-персистентность**: `video_jobs.json` хранит `prompt_id`; при рестарте
  flux незавершённые видео подхватываются (startup перечитывает и переподнимает
  воркеры). Не ломай.

## Управление памятью

Модель выгружается из VRAM **только при смене модели** (кэширование) или по
простою (сторожевой таймер, `IDLE_UNLOAD_SECONDS=300`). Не выгружай после каждого
изображения — убивает кэш.

## Функциональность (страницы/эндпоинты)

| Путь | Что делает |
|---|---|
| `/` | дашборд: виджеты, графики за час/день/неделю, последние задачи |
| `/tasks` | загрузка CSV (картинки/тексты/референсы), модели, очередь |
| `/server` | статус сервера: железо, диски, загруженные модели, логи |
| `/base` | раздел «База» (заглушка, в разработке) |
| `/map/{id}` | сопоставление колонок CSV (+ загрузка логотипа-референса) |
| `/configure/{id}` | выбор моделей, запуск |
| `/result/{id}` | результаты + «Утвердить выбранные» + ZIP (WEBP) + публикация |
| `/single` | генерация по единому промпту (локальные + Nano Banana модели, кредиты) |
| `/video` | генерация видео (длительность/разрешение/fps/steps/негативный промпт) |
| `/upscale` | апскейл картинки (img2img/multimodal) |
| `/ref/...` | генерация по референсам (multimodal klein) |
| `/thumbs` | миниатюры категорий WooCommerce |
| `/publish/{id}` | публикация на WordPress/WooCommerce |
| `/files` | файловый менеджер |
| `/llm` | генерация текстов (DeepSeek/Ollama/Ollama Cloud) |

Ключевые API: `/api/models`, `/api/nanobanana/models`, `/api/single/*`,
`/api/video/*`, `/api/upscale/*`, `/api/ref/*`, `/api/llm/*`, `/api/publish/*`,
`/api/jobs/*` (вкл. `/api/jobs/{id}/zip` — скачать WEBP архивом).

## Модули

```
main.py            — FastAPI (все эндпоинты)
generator.py       — генерация (diffusers + ComfyUI), выгрузка, idle-watchdog
comfy_client.py    — клиент ComfyUI (submit_workflow/wait_for_result; workflow
                     flux2/krea2/ideogram4/wan2.1/ltxv)
task_queue.py      — очередь картинок (выгрузка при смене модели, логотип-референс)
ref_store.py/ref_queue.py — генерация по референсам
thumb_store.py/thumb_worker.py — миниатюры категорий WooCommerce
nanobanana_client.py — внешний API Nano Banana (15 image-моделей, кредиты)
image_search.py    — поиск картинок DuckDuckGo (референсы)
llm_client.py      — DeepSeek/Ollama/Ollama Cloud + DuckDuckGo
llm_queue.py       — очередь LLM (deepseek блокируется в peak-time)
wp_publish.py      — публикация WP/WooCommerce (translit/slugify/parse_image_name)
store.py/llm_store.py/ref_store.py — персистентность (JSON, атомарная fsync)
stats_monitor.py/stats_report.py — сбор/отчёт по CPU/GPU/RAM/VRAM
downloader.py      — загрузка моделей (HF)
static/            — веб-интерфейс
```

## UI (Tabler)

- Шаблоны: `templates/` (Jinja2, наследуют `base.html`), через `Jinja2Templates` в `main.py`.
- Вендоренные ассеты: `static/vendor/tabler` (v1.7.1), `static/vendor/tabler-icons`
  (v3.49), `static/vendor/apexcharts`; бренд-стили — `static/css/ai-enix.css`.
- Бренд: акцент `#d60b0b`, фон белый, шрифт Roboto Condensed; sidebar/футер чёрные
  (`#000`), заголовки `#fff`, текст `#999`, рамки/фоны `#dddddd`.
- Роуты: `/` (дашборд), `/tasks`, `/server`, `/base`; остальные страницы пока на
  старых `static/*.html` (переводятся постепенно).
- API дашборда: `/api/stats/history?period=hour|day|week` (агрегация
  `stats_history.jsonl`, кэш 60с), `/api/server/status`, `/api/server/logs`.
- **Готчас:** Starlette **1.6** — `TemplateResponse(request, name, context)`
  (старый `TemplateResponse(name, {"request": ...})` даёт 500).

## Токены/ключи (файлы в корне проекта)

`hf_token.txt`, `deepseek_token.txt`, `ollama_token.txt`, `nanobanana_token.txt`.
Читаются при старте модулей. Не коммитить ключи.

## Доступы

- Веб: **`https://ai.enix.ru:10443`** (Let's Encrypt, nginx SNI на 10443). Легаси:
  `https://109.95.77.86:10443` (self-signed). Логин/пароль — env `FLUX_USER`/`FLUX_PASSWORD` (`.env`).
- SSL: `acme.sh` (DNS-01, Beget) → `/etc/nginx/ssl/ai.enix.ru.crt`, до 2027-01-24.
  Продление: добавить TXT `_acme-challenge.ai` в Beget (ручное) либо настроить `dns_beget` с API-кредами.
- nginx: `flux` (IP, default) + `zz-ai-enix` (`server_name ai.enix.ru`) на `listen 10443 ssl`.
- SSH: порт 2222, пользователь `kirill`.
- Часовой пояс Europe/Moscow.

## Скачивание моделей (по расписанию)

Канал офиса 30 Мбит/с — не качать днём в будни. Планировщик
`download_scheduler.sh` + `dl_sched.timer` (каждые 10 мин). Будни 07:00–19:00 МСК
— не качать; ночь и выходные — можно. Списки: `models_to_download.txt`,
`download_comfy.py`.

## Система (systemd и прочее)

- Сервисы: `flux` (uvicorn 127.0.0.1:8000), `comfy` (127.0.0.1:8188), `ollama`,
  `stats_monitor`, `tc_limit` (лимит 5 Мбит/с в будни 07–19).
- **CPU affinity** (`AllowedCPUs` в overrides): Ollama 4 ядра (4-7,12-15),
  ComfyUI 2 ядра (2,3,10,11), flux 2 ядра (0,1,8,9). Файлы в
  `/etc/systemd/system/*.service.d/override.conf`.
- Перезапуск flux: `sudo systemctl restart flux`. ComfyUI поднимается сам
  (enabled). После ребута — всё self-heal (видео/очереди восстанавливаются).

## План БД товаров/цен/RAG (пока не реализовано — следующая фаза)

Спроектирована схема (в PostgreSQL + `pgvector` + `pg_trgm`): `products`,
`price_changes` (история+лог), `sites`, `product_sites`, `suppliers`,
`price_lists`, `price_list_items`, `documents`+`document_chunks` (RAG, эмбеддинги
bge-m3 1024). Поток: CSV/Excel прайсов → email-обработка (DeepSeek + whitelist) →
OCR сканов (vision-модель) → плагин сайтов + очередь → чат RAG (Drag&Drop
загрузка каталогов). Чат стартует на DeepSeek API (не локальная LLM), чтобы не
конфликтовать с GPU генерации.

## Железо (актуально)

RAM 45 GiB (апгрейд выполнен). Диски: `/` 256GB SSD (sdd, 232G LVM),
`/data` 512GB SSD (sdc, модели), второй 512GB SSD (sdb) пуст — под RAID1,
1TB HDD (sda) пуст — под бэкапы. RAID (mdadm) и бэкапы — настроить в офисе.
GPU RTX 3090 24GB, драйвер 595.99.02 (зафиксирован `apt-mark hold`).

## Git (репозиторий проекта)

- Remote: `git@github.com:KirillOdinokov/AIServ.git` (публичный), ветка `main`.
- Рабочая копия: Windows `E:\Оптимизация\LOCAL FLUX` (источник); деплой на сервер
  позже через `git pull` (сервер `/home/kirill/flux`).
- SSH deploy key: `~/.ssh/aiserv_deploy` (ed25519); в git настроен
  `core.sshCommand` (ключ + `known_hosts` по короткому пути `C:/Users/C523~1/...`).
- `.gitignore` исключает: `*_token.txt`, `.env`, `SSH_access.txt`, `access.txt`,
  `_*.py` (скретч с паролем), `jobs*.json`, `llm_jobs.json*`, `output/`,
  `models/`, `ComfyUI/`, `.venv/`, `__pycache__/`, логи, `example_prices/`.
- Секреты — в `.env` (gitignored), шаблон `.env.example`. `config.py` грузит
  `.env`; `main.py` берёт `FLUX_PASSWORD` из окружения (дефолта больше нет).
- Процесс: правки → `git add` → `git commit` → `git push` (из Windows).

## Сервис «База» (план, следующая фаза)

- Отдельный сервис `base` (FastAPI `:8001`, systemd `base.service`), PostgreSQL 18
  + pgvector + pg_trgm, поддомен `ai.enix.ru` → 109.95.77.86, nginx SNI на 10443,
  Let's Encrypt. Каталог `/home/kirill/flux/base/`.
- Данные: товары (SKU, логистика), сайты (WC-ключи), поставщики, прайсы,
  история цен, ревью-очередь, `mapping_memory`, email, `site_updates`, RAG.
- Матчинг: нормализация → exact/trgm/pgvector(bge-m3) → LLM-арбитраж;
  ≥0.95 авто, 0.5–0.95 на утверждение (письмо Да/Нет), <0.5 ручное картирование.
- Сайты: MVP — WooCommerce REST (`wp_publish.WpClient`), затем WP-плагин
  (pull `/api/base/v1/prices` + ack). Старт: stekltkani.ru, promtexizol.ru.
- ИИ-чат (плагин `odinokov-ai-chat`) → через Базу, единый ключ DeepSeek, RAG
  документов (PDF/DOCX/XLSX) + история; внутренняя страница чата сотрудников.
- Режимы: 08–19 пн–пт — чат (DeepSeek, без GPU) + интерактивная генерация;
  19–08 + выходные — batch прайсов (локальный Qwen) → затем очередь картинок.
- Канонический SKU: искать в прайсе; если артикул есть и совпал на сайте —
  обновлять по нему (кэшировать), иначе синхронизировать по названиям; изначально
  заносить канонические артикулы с сайтов. Объём: ~300k товаров, 0.5–10k/мес.
- Email: `price@enix.ooo` (Beget, IMAP/SMTP). Примеры прайсов: `example_prices/`.
