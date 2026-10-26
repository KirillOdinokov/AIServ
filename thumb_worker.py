"""Фоновая генерация миниатюр категорий (WP/WooCommerce).

Логика:
  1. fetch — получить список категорий товаров и по одному товару из каждой;
  2. run   — для каждой категории: скачать картинку товара, сгенерировать
             фотореалистичную 3D-иконку (webp 600×600) и загрузить её как
             миниатюру категории.

Учётные данные WP/WC не сохраняются в хранилище — передаются только
в момент запуска и живут в замыкании фонового потока.
"""
import random
import threading
import time
from pathlib import Path

import config
import image_search
import wp_publish
from generator import generator
from thumb_store import thumb_store, ThumbItem


def _slugify(text):
    import re
    text = re.sub(r'[\\/:*?"<>|]', "", text or "")
    text = re.sub(r'\s+', "_", text.strip())
    return text[:80].strip("_")


def _make_client(credentials):
    return wp_publish.WpClient(
        site=credentials.get("site", ""),
        wp_user=credentials.get("wp_user"),
        wp_app_password=credentials.get("wp_app_password"),
        wc_key=credentials.get("wc_key"),
        wc_secret=credentials.get("wc_secret"),
    )


def fetch_job(job_id, credentials):
    """Получить категории и товары-референсы, заполнить задачу."""
    job = thumb_store.get_job(job_id)
    if job is None:
        return

    try:
        client = _make_client(credentials)
        cats = client.list_product_categories()

        items = []
        for c in cats:
            pid = pname = pimg = None
            try:
                products = client.list_products_in_category(c["id"], per_page=1)
                if products:
                    p = products[0]
                    pid = p.get("id")
                    pname = p.get("name", "")
                    images = p.get("images") or []
                    if images:
                        pimg = images[0].get("src") or None
            except Exception as e:
                print(f"[thumbs] Не удалось получить товар категории {c['id']}: {e}")

            items.append(ThumbItem(
                c["id"], c["name"], c.get("slug", ""), pid, pname, pimg
            ))

        job.items = items
        job.status = "ready"
        job.error = None
    except Exception as e:
        job.status = "error"
        job.error = str(e)
    thumb_store.update_job(job)


def run_job(job_id, credentials):
    """Сгенерировать и загрузить миниатюры для всех категорий задачи."""
    job = thumb_store.get_job(job_id)
    if job is None:
        return

    if job.status == "running":
        return
    job.status = "running"
    job.error = None
    thumb_store.update_job(job)

    client = None
    try:
        client = _make_client(credentials)
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        thumb_store.update_job(job)
        return

    job_folder = job.folder_name() or f"thumbs_{job.id}"
    src_dir = config.OUTPUT_DIR / job_folder / "sources"
    img_dir = config.OUTPUT_DIR / job_folder / "images"
    src_dir.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)

    model_id = config.THUMB_MODEL
    info = config.MODELS[model_id]
    steps = info["default_steps"]
    guidance = info.get("guidance_scale", 1.0)
    gen_size = config.THUMB_GEN_SIZE
    out_size = config.THUMB_OUTPUT_SIZE

    from PIL import Image

    for item in job.items:
        if job.paused:
            job.status = "ready"
            thumb_store.update_job(job)
            return

        # пропускаем уже готовые (повторный запуск/возобновление)
        if item.status in ("done", "skipped", "error"):
            continue

        item.status = "running"
        thumb_store.update_job(job)

        start = time.time()
        try:
            # 1. Скачать картинку товара-референса
            source = None
            if item.product_image_url:
                src_name = f"{item.category_id or item.id}.png"
                dest = src_dir / src_name
                if not dest.exists() or dest.stat().st_size == 0:
                    if image_search.download_image(item.product_image_url, dest):
                        source = dest
                    else:
                        dest = None
                else:
                    source = dest
            elif item.source_path:
                p = config.OUTPUT_DIR / item.source_path
                if p.exists():
                    source = p

            if source is None and not item.product_image_url:
                item.status = "skipped"
                item.error = "В категории нет товара с изображением"
                item.elapsed = round(time.time() - start, 1)
                thumb_store.update_job(job)
                continue

            item.source_path = str(source.relative_to(config.OUTPUT_DIR)) if source else None

            # 2. Промпт по имени товара (или категории)
            title = item.product_name or item.category_name or ""
            prompt = config.THUMB_PROMPT_RULE.format(title=title)

            # 3. Генерация 3D-иконки (multimodal по референсу)
            seed = random.randint(0, 2**32 - 1)
            raw_out = config.OUTPUT_DIR / job_folder / f"{item.id}_raw.png"
            generator.generate(
                prompt, model_id, gen_size, gen_size, steps, guidance, seed, raw_out,
                reference_image=source,
                reference_mode="multimodal",
                strength=0.5,
            )

            # 4. Ресайз до 600×600 и конвертация в webp
            webp_name = f"{item.category_id or item.id}.webp"
            webp_path = img_dir / webp_name
            with Image.open(raw_out) as im:
                im = im.convert("RGB")
                im = im.resize((out_size, out_size), Image.LANCZOS)
                im.save(webp_path, "WEBP", quality=config.EXPORT_QUALITY, method=6)
            try:
                raw_out.unlink(missing_ok=True)
            except OSError:
                pass

            item.filename = f"{job_folder}/images/{webp_name}"

            # 5. Загрузка как миниатюра категории
            alt = item.category_name or str(item.category_id)
            slug = wp_publish.slugify(alt) or f"cat-{item.category_id}"
            filename = f"{slug}-thumb.webp"
            with open(webp_path, "rb") as fh:
                file_bytes = fh.read()
            media = client.upload_media(file_bytes, filename, alt)
            media_id = media.get("id")
            client.assign_to_product_cat(item.category_id, media_id)

            item.media_id = media_id
            item.status = "done"
            item.error = None
            item.elapsed = round(time.time() - start, 1)
        except Exception as e:
            item.status = "error"
            item.error = str(e)
            item.elapsed = round(time.time() - start, 1)

        thumb_store.update_job(job)

    job.status = "done"
    thumb_store.update_job(job)


def start_fetch(job_id, credentials):
    threading.Thread(target=fetch_job, args=(job_id, credentials), daemon=True).start()


def start_run(job_id, credentials):
    threading.Thread(target=run_job, args=(job_id, credentials), daemon=True).start()
