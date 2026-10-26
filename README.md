# AI ENIX

Локальный генератор изображений на базе FLUX с веб-интерфейсом и массовой генерацией по CSV. Оптимизирован под **RTX 3090 24GB**.

## Возможности

- Одиночная генерация через веб-интерфейс
- Массовая генерация по CSV-файлу (фоновая очередь, можно закрыть вкладку)
- Переключение моделей: **FLUX.2 [klein] 9B** (основная), **FLUX.2 [klein] 4B**, **FLUX.1-schnell**, **FLUX.1-dev**
- Прогресс задач, превью результатов
- Экономия VRAM через `enable_model_cpu_offload()`

## Установка

1. Установите Python 3.10+ (проверено на 3.13).
2. Установите CUDA-совместимый PyTorch (см. ниже).
3. Запустите `run.bat` — он создаст виртуальное окружение, установит зависимости и запустит сервер.

### Установка PyTorch с CUDA

`requirements.txt` ставит PyTorch без CUDA. Для GPU установите вручную:

```bat
pip install torch --index-url https://download.pytorch.org/whl/cu124
```

(выберите версию CUDA под ваш драйвер: cu121 / cu124 / cu126)

## Запуск

```bat
run.bat
```

Откройте http://127.0.0.1:8000

## CSV-формат

Колонки (обязательна только `prompt`):

| Колонка | Описание | По умолчанию |
|---|---|---|
| `ID` | Имя файла (сохранится как `ID.png`) | авто |
| `prompt` | Текст промпта | — |
| `aspect_ratio` | Соотношение сторон `1:1`, `21:9`, `16:9` и т.д. | `1:1` |
| `model` | `flux-schnell` или `flux-dev` | `flux-schnell` |
| `steps` | Шагов (пусто = дефолт модели) | дефолт |
| `guidance_scale` | CFG (пусто = дефолт модели) | дефолт |
| `seed` | Сид (-1 = случайный) | -1 |

> Ширина всегда максимум **1024px**, высота вычисляется из `aspect_ratio` (кратно 8). Поле `width`/`height` убрано.

Пример:

```csv
ID,prompt,aspect_ratio,model,steps,guidance_scale,seed
101,"a red fox in the forest",1:1,flux-schnell,,,-1
102,"a futuristic city at night",21:9,flux-schnell,,,-1
103,"a cozy cabin in the mountains",16:9,flux-schnell,,,-1
```

## Модели

При первом запуске модели скачиваются с Hugging Face в папку `models/` (несколько ГБ).

| Модель | Качество | VRAM | Лицензия | Gated |
|---|---|---|---|---|
| **FLUX.2 [klein] 9B** (основная) | Лучшее | ~20GB | Non-commercial | Да |
| FLUX.2 [klein] 4B | Хорошее | ~13GB | Apache 2.0 | Нет |
| FLUX.1-schnell | Хорошее | ~24GB | Apache 2.0 | Да |
| FLUX.1-dev | Высокое | ~24GB | Non-commercial | Да |

> **FLUX.2 [klein] 9B** — лучший баланс качества и скорости (4 шага, секунды на генерацию), влезает в 24GB.
> Для gated-моделей нужно принять лицензию на Hugging Face и указать токен в `hf_token.txt`.

> **Ideogram** — закрытая модель, открытых весов для локального запуска нет. Доступна только через их API. Если нужна — можно добавить отдельный провайдер, обращайтесь.

## Структура

```
config.py      — настройки, список моделей, пути
schemas.py     — Pydantic-схемы API
generator.py   — загрузка моделей и генерация
task_queue.py  — фоновая очередь задач
main.py        — FastAPI приложение
static/        — веб-интерфейс (HTML/CSS/JS)
output/        — сгенерированные изображения
models/        — кэш моделей
```

## API

- `GET /api/models` — список моделей
- `POST /api/generate` — одиночная генерация (JSON)
- `POST /api/generate/csv` — массовая генерация (multipart CSV)
- `GET /api/task/{id}` — статус задачи
- `GET /api/tasks` — все задачи
- `GET /api/output/{filename}` — скачать изображение
