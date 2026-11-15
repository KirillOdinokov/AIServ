# Инструкция для агента: визуализация автомобилей в плёнке («Тачки»)

Документ описывает уже реализованную функцию и правила её доработки.  
Страница: `https://ai.enix.ru:10443/cars` (локально `/cars`).

## 1. Что уже работает

- Выбор источника:
  - загрузка фотографии автомобиля пользователем;
  - выбор марки/модели из каталога (570 моделей, 74 марки, российский рынок).
- Для выбранной модели фото ищется в DuckDuckGo, предпочтение — горизонтальный экстерьер.
- Референс сохраняется локально и отдаётся внешним API по временной публичной HTTPS-ссылке.
- Параметры:
  - тип плёнки: матовая / глянцевая;
  - цвет кузова и цвет плёнки;
  - антихром: нет / матовый чёрный / глянцевый чёрный;
  - кадр: 16:9, 4:3, 3:2, 1:1.
- Сравнение моделей в одном запуске:
  - локальная `flux2-klein-4b` (multimodal, референс);
  - любые доступные модели Nano Banana / GPT Image / Seedream из `nanobanana_client.NANOBANANA_MODELS`.
- Модели запускаются параллельно; результат одной не ждёт таймаута другой.
- История генераций сохраняется в `cars_jobs.json`, переживает перезапуск `flux`.
- Исходники и результаты лежат в `output/cars_sources/` и `output/cars/<job_id>/`.

## 2. Файлы

| Файл | Назначение |
|---|---|
| `cars_catalog.py` | `CAR_BRANDS`, `CAR_MODELS` (570), `CAR_COLORS`, `FILM_FINISHES`, `FILM_COLORS`, `ANTI_CHROME_OPTIONS`, `ASPECT_RATIOS` |
| `cars_service.py` | загрузка/нормализация фото, поиск фото в DDG, публичные URL, сборка промпта |
| `main.py`, раздел `«Тачки»` | API, in-memory `_car_jobs`, JSON-история, worker, публичный маршрут |
| `templates/cars.html` | интерфейс: источник, параметры, модели, результаты, история |
| `static/js/cars.js` | загрузка каталога, запуск, поллинг, история |
| `nanobanana_client.py` | внешние модели; `images=[...]`, callback `on_task` |
| `config.py` | `PUBLIC_BASE_URL` (`FLUX_PUBLIC_URL`, по умолчанию `https://ai.enix.ru:10443`) |
| `.gitignore` | `cars_jobs.json`, `cars_jobs.json.tmp` |

## 3. API

### `GET /cars`
HTML-страница. Шаблон `cars.html`, `active="cars"`.

### `GET /api/cars/catalog`
Возвращает:

```json
{
  "brands": {"LADA": ["Granta", "Vesta"]},
  "models": [{"brand": "LADA", "model": "Granta"}],
  "body_colors": ["как на фото", "чёрный"],
  "film_colors": ["в цвет кузова", "чёрный"],
  "finishes": [{"id": "matte", "label": "Матовая"}],
  "anti_chrome": [{"id": "none", "label": "Без антихрома"}],
  "aspect_ratios": ["16:9", "4:3", "3:2", "1:1"],
  "local_models": [{"id": "flux2-klein-4b", "label": "...", "available": true}],
  "nano_models": [{"id": "nano-banana-2-1", "label": "...", "credits": {"2k": 20}}],
  "has_nano_key": true
}
```

### `POST /api/cars/jobs`
`multipart/form-data`:

| Поле | Обязательно | Значение |
|---|---|---|
| `source_mode` | да | `upload` или `model` |
| `file` | для upload | jpg/png/webp, до 30 МБ |
| `brand` | для model | марка из `cars_catalog.CAR_BRANDS` |
| `model` | для model | модель из выбранной марки |
| `body_color` | нет | значение из `BODY_COLORS` |
| `finish` | нет | `matte` / `gloss` |
| `film_color` | нет | значение из `FILM_COLORS` |
| `anti_chrome` | нет | `none` / `matte_black` / `gloss_black` |
| `aspect_ratio` | нет | из `ASPECT_RATIOS` |
| `resolution` | нет | `1k` / `2k` / `4k` (Nano Banana) |
| `models` | да | JSON-массив id моделей |

Ответ — объект задачи. Задача запускается в фоне.

### `GET /api/cars/jobs`
Список до 100 последних задач для истории.

### `GET /api/cars/jobs/{job_id}`
Полная задача: статус, промпт, исходник, модели, `filename`, `error`, `credits`, `task_id`, `api_status`.

### `DELETE /api/cars/jobs/{job_id}`
Удаляет метаданные, результаты и исходник.

### `GET /public/cars/{filename}`
**Публичный маршрут без авторизации.** Нужен, потому что Nano Banana принимает только публичные HTTPS URL.  
Разрешён в `AuthMiddleware` только префикс `/public/cars/`.  
Имя файла дополнительно проверяется регуляркой `[a-f0-9]{24,64}\.(png|jpg|jpeg|webp)`.

## 4. Поток данных

1. `cars.js` загружает `/api/cars/catalog`, строит бренды/модели/цвета/чекбоксы.
2. Пользователь отправляет multipart в `/api/cars/jobs`.
3. `cars_create` валидирует поля, сохраняет загруженное фото через `cars_service.save_uploaded`, создаёт job и поток `_car_worker`.
4. `_car_worker`:
   - если источник — модель, вызывает `cars_service.search_car_photo`;
   - собирает промпт `cars_service.build_prompt`;
   - создаёт `output/cars/<job_id>/`;
   - запускает по одному потоку `_car_model_worker` на каждую выбранную модель;
   - каждая модель обновляет свой статус и `cars_jobs.json`.
5. `cars.js` опрашивает `/api/cars/jobs/{id}` каждые 2.5 сек, отображает карточки.
6. История читает `/api/cars/jobs` и открывает задачу без перезагрузки страницы.

## 5. Модели

### Локальная FLUX.2 Klein

```python
generator.generate(
    prompt, "flux2-klein-4b", width, height,
    info["default_steps"], info.get("guidance_scale", 1.0), seed, out_path,
    reference_image=source_path,
    reference_mode="multimodal" if source_path else "none",
    strength=0.65,
)
```

`source_path` — `Path` к локальному JPEG/PNG.  
Локальная модель использует GPU; учитывайте очередь `ref_queue` и другие тяжёлые задачи.

### Nano Banana / GPT Image / Seedream

```python
def _on_task(info):
    m["task_id"] = info.get("task_id")
    m["api_status"] = info.get("status")
    _save_cars_jobs()

urls, credits = nanobanana_client.generate(
    model_id, prompt,
    images=[public_url] if public_url else None,
    aspect_ratio=aspect_ratio,
    resolution=resolution,
    on_task=_on_task,
)
```

- `images` — **только публичные HTTPS URL**, не локальные пути и не закрытые `/api/...`.
- `public_url` = `cars_service.public_url(source_filename)`.
- Таймаут ожидания — 600 сек; при ошибке/таймауте статус модели `error`.
- Модели запускаются параллельно, поэтому таймаут одной не блокирует остальные.
- `task_id` и `api_status` сохраняются в истории для диагностики.

## 6. Промпт

`cars_service.build_prompt(...)` собирает английский промпт:

- сохранить модель, кузов, пропорции, колёса, оптику, решётку, линии;
- полностью обклеить авто матовой/глянцевой плёнкой;
- цвет плёнки: выбранный или «в цвет кузова»;
- антихром: все хромированные детали в матовый/глянцевый чёрный;
- детейлинг-центр, полированный пол, студийный свет, 3/4 спереди, авто целиком;
- без текста, водяных знаков, людей, дополнительных логотипов.

Не меняйте это поведение без тестов: промпт влияет на сохранение геометрии автомобиля.

## 7. Обязательные правила при доработке

1. **Не передавайте локальный путь в Nano Banana.** Только `public_url`.
2. **Не открывайте `/public/cars/` для произвольных путей.** Только токен-файлы из `cars_sources`.
3. **Не используйте имя `token.jpg` одновременно как временный и публичный файл.**  
   Временный файл должен иметь отдельное имя (например, `<token>.download`), иначе `finally` удалит итоговый файл. Эта ошибка уже была и исправлена.
4. **Не делайте повторную полную перерисовку истории/результатов на каждом poll.** Обновляйте только изменившиеся карточки.
5. **Не перезапускайте `flux` без необходимости**, если идут длинные `ref`/video задачи: текущий вариант прерывается, хотя очередь восстанавливается.
6. **Не удаляйте исходники, на которые ссылается история.** `cleanup_sources` сейчас хранит 30 дней.
7. **Не запускайте локальную модель параллельно с другой GPU-задачей без учёта очереди.**  
   Сейчас `generator` имеет внутренний lock, но задача может надолго занять GPU.
8. **Nano Banana API платный.** Стоимость показывайте пользователю; не запускайте внешние модели в тестах без необходимости.
9. **Кодировка.** Формы с кириллицей тестируйте через UTF-8; PowerShell `Set-Content -Encoding ASCII` портит значения.

## 8. Известные ограничения

- DuckDuckGo может вернуть не тот ракурс/комплектацию. Сейчас фильтр предпочитает горизонтальные фото, но нужен выбор из 3 кандидатов.
- Антихром реализован промптом; он может затронуть штатные чёрные детали. Для точности нужна маска (SAM/сегментация) + локальная перекраска.
- Локальная FLUX.2 Klein может искажать геометрию авто; для коммерческого качества основной — Nano Banana Pro/2.1.
- Nano Banana 2.1 иногда уходит в таймаут 600 сек; Pro при этом может завершиться. Смотрите историю и `task_id`/`api_status`.
- История хранит до 200 задач; старые исходники чистятся через 30 дней.
- Yandex Search API официально в первую очередь веб-поиск, не готовый image search. Для картинок нужен отдельный провайдер (SerpAPI/Google Images или парсинг Yandex Images с fallback на DDG).

## 9. Тесты перед деплоем

Локально:

```powershell
python -m py_compile main.py cars_service.py cars_catalog.py nanobanana_client.py
node --check static/js/cars.js
```

Проверить функции:

```python
import cars_service
name, url = cars_service.search_car_photo("Nissan", "Pathfinder", "бордовый")
# name должен существовать в output/cars_sources, url — исходная ссылка
```

Проверить endpoint:

```bash
curl -s -b cookie http://127.0.0.1:8000/api/cars/catalog
curl -s -b cookie http://127.0.0.1:8000/api/cars/jobs
```

Публичная ссылка должна открываться **без cookie** с внешнего адреса:

```bash
curl -k -I https://ai.enix.ru:10443/public/cars/<token>.jpg
```

Деплой:

```powershell
pscp main.py cars_service.py cars_catalog.py nanobanana_client.py `
  templates/cars.html static/js/cars.js `
  kirill@109.95.77.86:/home/kirill/flux/...
```

Затем:

```bash
cd /home/kirill/flux
.venv/bin/python -m py_compile main.py cars_service.py cars_catalog.py nanobanana_client.py
sudo systemctl restart flux
```

## 10. Что улучшить следующим агентом

1. **Выбор референса**: показывать 3 найденных фото пользователю, а не брать первое.
2. **Антихром по маске**: сегментация хрома + локальная перекраска вместо промпта.
3. **Несколько ракурсов** на один автомобиль (3/4, бок, зад) и пакетная генерация.
4. **Кэш поиска** по `brand/model/color`, чтобы не искать одно и то же.
5. **Провайдеры поиска**: интерфейс `search_car_photo` с приоритетом
   `SerpAPI → Yandex Images → DDG → без референса`.
6. **Очередь GPU**: запускать локальные «Тачки» через общую очередь, чтобы не задерживать `ref`-задачи.
7. **Компактный API истории** (без полного `items`) для больших задач.
8. **Отдельная страница задачи** `/cars/result/{id}`, чтобы историю можно было шарить ссылкой.
9. **Пресеты**: «матовый чёрный», «глянцевый чёрный», «антихром + белая плёнка».
10. **Ограничение стоимости**: предупреждение в UI при выборе нескольких платных моделей.

## 11. Критерии приёмки

- [ ] Загрузка фото создаёт задачу и публичный исходник.
- [ ] Модель из каталога ищет горизонтальное фото и сохраняет его.
- [ ] Локальная FLUX и Nano Banana создают отдельные результаты.
- [ ] Ошибка/таймаут одной Nano-модели не скрывает результаты других.
- [ ] История переживает `systemctl restart flux`.
- [ ] Публичный исходник открывается без авторизации, но только по токену.
- [ ] При удалении задачи удаляются результаты и исходник.
- [ ] Кириллица в названиях/цветах не ломается.
- [ ] `cars_jobs.json` не попадает в git.
