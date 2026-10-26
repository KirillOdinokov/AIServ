"""Конфигурация приложения."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env_file():
    """Загрузить переменные окружения из .env (секреты вне репозитория)."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()

# Папки
OUTPUT_DIR = Path(os.getenv("FLUX_OUTPUT_DIR", BASE_DIR / "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Модели
MODEL_CACHE_DIR = Path(os.getenv("HF_HOME", BASE_DIR / "models"))
MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Доступные модели: id -> (repo_id, описание)
# engine: "diffusers" (локальная загрузка) или "comfyui" (через ComfyUI API)
MODELS = {
    "flux2-klein-4b": {
        "repo_id": "black-forest-labs/FLUX.2-klein-4B",
        "label": "FLUX.2 [klein] 4B (Turbo)",
        "default_steps": 4,
        "guidance_scale": 1.0,
        "pipeline": "flux2_klein",
        "engine": "diffusers",
    },
    "krea2-turbo": {
        "repo_id": "Comfy-Org/Krea-2",
        "label": "Krea 2 Turbo (FP8)",
        "default_steps": 8,
        "guidance_scale": 1.0,
        "engine": "comfyui",
        "workflow": "krea2",
        "files": [
            ("Comfy-Org/Krea-2", "diffusion_models/krea2_turbo_fp8_scaled.safetensors", "diffusion_models"),
            ("Comfy-Org/Krea-2", "text_encoders/qwen3vl_4b_fp8_scaled.safetensors", "text_encoders"),
            ("Comfy-Org/Krea-2", "vae/qwen_image_vae.safetensors", "vae"),
        ],
    },
    "ideogram4": {
        "repo_id": "Comfy-Org/Ideogram-4",
        "label": "Ideogram 4 (FP8)",
        "default_steps": 20,
        "guidance_scale": 7.0,
        "engine": "comfyui",
        "workflow": "ideogram4",
        "files": [
            ("Comfy-Org/Ideogram-4", "diffusion_models/ideogram4_fp8_scaled.safetensors", "diffusion_models"),
            ("Comfy-Org/Ideogram-4", "diffusion_models/ideogram4_unconditional_fp8_scaled.safetensors", "diffusion_models"),
            ("Comfy-Org/Ideogram-4", "text_encoders/qwen3vl_8b_fp8_scaled.safetensors", "text_encoders"),
            ("Comfy-Org/Ideogram-4", "vae/flux2-vae.safetensors", "vae"),
        ],
    },
    "wan2.1-t2v-14b": {
        "repo_id": "city96/Wan2.1-T2V-14B-gguf",
        "label": "Wan 2.1 T2V 14B (GGUF Q4, видео)",
        "default_steps": 20,
        "guidance_scale": 1.0,
        "engine": "comfyui",
        "workflow": "wan2.1_t2v",
        "video": True,
        "video_width": 832,
        "video_height": 480,
        "files": [
            ("city96/Wan2.1-T2V-14B-gguf", "wan2.1-t2v-14b-Q4_K_M.gguf", "diffusion_models"),
            ("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders"),
            ("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/vae/wan_2.1_vae.safetensors", "vae"),
        ],
    },
    "ltx-video-13b": {
        "repo_id": "Lightricks/LTX-Video",
        "label": "LTX-Video 13B (FP8, видео)",
        "default_steps": 30,
        "guidance_scale": 3.0,
        "engine": "comfyui",
        "workflow": "ltxv",
        "video": True,
        "video_width": 768,
        "video_height": 512,
        "files": [
            ("Lightricks/LTX-Video", "ltxv-13b-0.9.7-distilled-fp8.safetensors", "diffusion_models"),
            ("comfyanonymous/flux_text_encoders", "t5xxl_fp8_e4m3fn.safetensors", "text_encoders"),
            ("city96/LTX-Video-gguf", "LTX-Video-VAE-BF16.safetensors", "vae"),
        ],
    },
}

# Модель по умолчанию
DEFAULT_MODEL = "flux2-klein-4b"

# Параметры по умолчанию
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 1024
DEFAULT_STEPS = 4
DEFAULT_GUIDANCE = 0.0
DEFAULT_SEED = -1  # -1 = случайный

# ─── Генерация по референсам ─────────────────────────────────────────────────

# Режим генерации определяется моделью (жёсткая привязка).
# В референсной генерации используется только klein (multimodal).
# Krea2 (img2img) исключён из референсов.
REFERENCE_MODE_BY_MODEL = {
    "flux2-klein-4b": "multimodal",
}

# Общее правило нанесения бренда для товарных типов (standart/storage/post_image):
# — сохранить исходные надписи/значки НА товаре;
# — удалить надписи ВНЕ товара;
# — если есть этикетка — сайт в левом нижнем углу этикетки;
# — если этикетки нет — сайт органично на самом товаре.
REF_BRAND_RULE = (
    "Сохрани на этикетке товара все исходные надписи, описания, значки и маркировку "
    "такими, какие они есть (бочки, канистры, бутыли, упаковки, рулоны и т.д.). "
    "Удали все посторонние надписи и водяные знаки, расположенные ВНЕ самого товара "
    "(на фоне, поверх изображения). "
    "Если на товаре есть этикетка или маркировка — название сайта «{site}» нанеси "
    "в левом нижнем углу этой этикетки, не изменяя остальной текст этикетки. "
    "Если этикетки или маркировки нет — нанеси название сайта «{site}» ровно один раз, "
    "органично и естественно, на сам товар. "
    "Без водяных знаков, без повторяющихся надписей, без многократной маркировки."
)

# Правило для типа с рабочими (application): этикетку товара не трогать,
# логотип — только на каску/спецодежду рабочего.
REF_WORKER_RULE = (
    "Сохрани этикетку и маркировку на товаре такими, какие они есть. "
    "Логотип или название сайта «{site}» нанеси только на каску или спецодежду рабочего, "
    "ровно один раз, аккуратно. "
    "Без водяных знаков, без посторонних надписей, без текста на самом товаре."
)

# Правило для типа storage (склад/стеллаж):
# — название сайта НЕ на товаре, а заметной, но органичной гравировкой на металлическом стеллаже;
# — исходные надписи/значки на товаре сохранить;
# — если этикетки изначально не было — допустима небольшая этикетка со штрихкодом и QR-кодом, без надписей.
REF_STORAGE_RULE = (
    "Сохрани на товаре все исходные надписи, описания, значки и маркировку такими, какие они есть. "
    "Удали все посторонние надписи и водяные знаки, расположенные ВНЕ самого товара. "
    "Название сайта «{site}» нанеси НЕ на товар, а в виде заметной, но органично выглядящей "
    "гравировки на металлическом стеллаже, на котором лежит товар, ровно один раз. "
    "Если на товаре изначально не было этикетки — допустима одна небольшая аккуратная этикетка "
    "со штрихкодом и QR-кодом, без каких-либо надписей. "
    "Без водяных знаков, без повторяющихся надписей, без многократной маркировки."
)

# Правило для режима Watermark: картинка максимально близка к исходной,
# без нанесения названия сайта, без наклеек/этикеток — только убрать водяной знак.
REF_WATERMARK_RULE = (
    "Точно воспроизведи исходное изображение товара без изменений: та же композиция, "
    "тот же фон, те же цвета, освещение и сам товар. "
    "Удали только водяные знаки и посторонние надписи, наложенные поверх изображения. "
    "Не добавляй никаких новых надписей, названий сайтов, логотипов, этикеток, наклеек, "
    "штрихкодов и QR-кодов. Изображение должно выглядеть точно как исходное, но без водяных знаков."
)

# Типы изображений (колонка type в CSV референсов) -> описание сцены.
# {title} — название товара/записи, {site} — название сайта.
# aspect: "1:1" | "21:9"; width/height — точные размеры генерации.
REF_TYPES = {
    "standart": {
        "label": "Стандарт (белый фон)",
        "aspect": "1:1",
        "width": 1024,
        "height": 1024,
        "prompt": (
            "Профессиональное студийное фото товара «{title}» на чистом белом фоне, "
            "квадратный кадр, равномерное мягкое освещение, имитация профессиональной предметной съёмки. "
            + REF_BRAND_RULE
        ),
    },
    "storage": {
        "label": "Склад (стеллаж)",
        "aspect": "1:1",
        "width": 1024,
        "height": 1024,
        "prompt": (
            "Товар «{title}» лежит на металлическом стеллаже на складе поставщика, "
            "квадратный кадр, реалистичное фото. "
            + REF_STORAGE_RULE
        ),
    },
    "post_image": {
        "label": "Изображение для записи (21:9)",
        "aspect": "21:9",
        "width": 1536,
        "height": 640,
        "prompt": (
            "Широкое горизонтальное изображение на тему «{title}» для записи, высокое качество. "
            + REF_BRAND_RULE
        ),
    },
    "application": {
        "label": "Применение (строители)",
        "aspect": "1:1",
        "width": 1024,
        "height": 1024,
        "prompt": (
            "Профессиональная фотография применения товара «{title}» профессиональными строителями, "
            "квадратный кадр. "
            + REF_WORKER_RULE
        ),
    },
}

# Типы, которые разворачиваются в несколько изображений на один товар.
# all_product -> storage + standart.
REF_TYPE_MULTI = {
    "all_product": ["storage", "standart"],
}

# ─── Генерация миниатюр категорий (WP/WooCommerce) ─────────────────────────

# Модель для генерации 3D-иконок категорий.
THUMB_MODEL = "flux2-klein-4b"

# Размер генерации (внутри) и итоговый размер иконки (после ресайза).
THUMB_GEN_SIZE = 1024
THUMB_OUTPUT_SIZE = 600

# Промпт для фотореалистичной 3D-иконки на основе товара-референса категории.
THUMB_PROMPT_RULE = (
    "Фотореалистичная 3D иконка товара «{title}»: объёмный 3D-рендер, "
    "изометрический ракурс, чистый белый фон, мягкое студийное освещение, "
    "реалистичные материалы (пластик, металл, резина), высокая детализация. "
    "Без текста, без надписей, без водяных знаков, без логотипов, без рамок."
)

# Очередь
MAX_CONCURRENT = 1  # одна генерация за раз (24GB VRAM)

# Через сколько секунд простоя выгружать модель из VRAM.
# Нужно, чтобы после завершения всех задач модель не занимала видеопамять
# вечно (иначе локальная LLM не может использовать GPU и работает на CPU).
# Кэширование при активной генерации сохраняется: выгрузка происходит
# только когда генераций не было дольше указанного времени.
IDLE_UNLOAD_SECONDS = 300  # 5 минут

# Экспорт изображений: конвертация PNG в сжатый формат для уменьшения объёма.
# Формат: "webp" | "jpeg" | "" (пусто = оставить PNG как есть).
# quality: WebP 1..100, JPEG 1..95. По умолчанию 82.
EXPORT_FORMAT = "webp"
EXPORT_QUALITY = 82
