"""Фоновая очередь генерации по референсам."""
import random
import threading
import time
from pathlib import Path

import config
import image_search
from generator import generator
from ref_store import ref_store


def _slugify(text):
    import re
    text = re.sub(r'[\\/:*?"<>|]', "", text or "")
    text = re.sub(r'\s+', "_", text.strip())
    return text[:80].strip("_")


# Модели, разрешённые в референсной генерации.
# Krea2 исключён — остаётся только FLUX.2 Klein.
ALLOWED_REF_MODELS = ("flux2-klein-4b",)


class RefQueue:
    def __init__(self):
        self._queue = []  # список (job_id, item_id, variant_id)
        self._lock = threading.Lock()
        self._paused = False
        self._current_model = None
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def pause(self):
        with self._lock:
            self._paused = True

    def resume(self):
        with self._lock:
            self._paused = False
        self.resume_all()

    def is_paused(self):
        with self._lock:
            return self._paused

    def current_model(self):
        """Какая модель сейчас загружена в VRAM (или None)."""
        with self._lock:
            return self._current_model

    def resume_all(self):
        for job in ref_store.list_jobs():
            if job.status == "done":
                continue
            if not job.modes:
                continue
            has_pending = any(
                v.status == "pending" for it in job.items for v in it.variants
            )
            if has_pending:
                self.submit_job(job)

    def submit_job(self, job):
        with self._lock:
            by_model = {}
            for item in job.items:
                for v in item.variants:
                    if v.status == "pending":
                        by_model.setdefault(v.model_id, []).append((job.id, item.id, v.id))
            # порядок: klein (multimodal) — единственная модель референсов
            order = [m for m in ("flux2-klein-4b",) if m in by_model]
            for model_id in order:
                for entry in by_model[model_id]:
                    if entry not in self._queue:
                        self._queue.append(entry)
        if job.status != "running":
            job.status = "queued"
            ref_store.update_job(job)

    def submit_variant(self, job, item, variant):
        with self._lock:
            self._queue.append((job.id, item.id, variant.id))
        if job.status in ("done", "error"):
            job.status = "queued"
        ref_store.update_job(job)

    def pause_job(self, job_id):
        job = ref_store.get_job(job_id)
        if job is None:
            return False
        job.paused = True
        if job.status in ("queued", "running"):
            job.status = "queued"
        ref_store.update_job(job)
        return True

    def resume_job(self, job_id):
        job = ref_store.get_job(job_id)
        if job is None:
            return False
        job.paused = False
        if job.status in ("queued", "running", "draft"):
            self.submit_job(job)
        ref_store.update_job(job)
        return True

    def _pick_entry(self):
        """Выбрать следующий вариант, предпочитая ту модель, что уже в VRAM.

        Возвращает (entry, item, variant) или (None, None, None).
        Группировка по модели минимизирует выгрузку/загрузку из VRAM.
        """
        with self._lock:
            if not self._queue:
                return None, None, None

            # 1. Сначала ищем вариант той же модели, что уже загружена
            for idx, e in enumerate(self._queue):
                job = ref_store.get_job(e[0])
                if job is None:
                    continue
                if job.paused:
                    continue
                item = next((i for i in job.items if i.id == e[1]), None)
                variant = next((v for v in item.variants if v.id == e[2]), None) if item else None
                if variant is not None and variant.model_id not in ALLOWED_REF_MODELS:
                    continue
                if variant is not None and variant.model_id == self._current_model:
                    return self._queue.pop(idx), item, variant

            # 2. Модель не загружена (или сменилась) — берём первый доступный вариант
            for idx, e in enumerate(self._queue):
                job = ref_store.get_job(e[0])
                if job is None:
                    continue
                if job.paused:
                    continue
                item = next((i for i in job.items if i.id == e[1]), None)
                variant = next((v for v in item.variants if v.id == e[2]), None) if item else None
                if variant is not None and variant.model_id not in ALLOWED_REF_MODELS:
                    continue
                if variant is not None:
                    return self._queue.pop(idx), item, variant

            return None, None, None

    def _run(self):
        while True:
            if self.is_paused():
                time.sleep(0.5)
                continue

            entry, item, variant = self._pick_entry()
            if entry is None:
                time.sleep(0.5)
                continue

            job_id, item_id, variant_id = entry

            if variant is not None and variant.model_id != self._current_model:
                self._unload_model()
                self._current_model = variant.model_id

            try:
                self._process(job_id, item_id, variant_id)
            except Exception as e:
                print(f"[ref_queue] Ошибка обработки {variant_id}: {e}")

    def _unload_model(self):
        if generator._pipe is not None:
            generator._unload_diffusers()
        import comfy_client
        comfy_client.free_memory(unload_models=True, free_memory=True)

    def _process(self, job_id, item_id, variant_id):
        job = ref_store.get_job(job_id)
        if job is None:
            return
        item = next((i for i in job.items if i.id == item_id), None)
        if item is None:
            return
        variant = next((v for v in item.variants if v.id == variant_id), None)
        if variant is None:
            return

        if job.status != "running":
            job.status = "running"
            ref_store.update_job(job)

        variant.status = "running"
        ref_store.update_job(job)

        model_id = variant.model_id
        info = config.MODELS[model_id]
        steps = info["default_steps"]
        guidance = info["guidance_scale"]

        seed = variant.seed
        if seed is None or seed < 0:
            seed = random.randint(0, 2**32 - 1)
            variant.seed = seed

        job_folder = job.folder_name() or f"refjob_{job.id}"
        model_label = _slugify(info.get("label", model_id))
        item_id_name = _slugify(item.item_id or item.title) or item.id
        variant_folder = f"{item_id_name}_{model_label}"
        out_name = f"{variant.id}.png"
        out_path = config.OUTPUT_DIR / job_folder / variant_folder / out_name

        # ─── Референс: URL или поиск ───────────────────────────────────────
        # Ошибка поиска референса не должна ронять генерацию —
        # при сбое просто генерируем без референса (text-to-image).
        try:
            reference_image = self._resolve_reference(job, item, job_folder)
        except Exception as e:
            print(f"[ref_queue] Не удалось получить референс для {item.title}: {e}")
            reference_image = None

        start = time.time()
        try:
            generator.generate(
                item.prompt, model_id, item.width, item.height,
                steps, guidance, seed, out_path,
                reference_image=reference_image,
                reference_mode=variant.reference_mode or "none",
                strength=variant.strength if variant.strength is not None else 0.5,
            )
            # ─── Наложение PNG-водяного знака (если задан) ─────────────────
            self._apply_overlay(job, out_path)
            variant.filename = f"{job_folder}/{variant_folder}/{out_name}"
            variant.status = "done"
            variant.elapsed = round(time.time() - start, 1)
        except Exception as e:
            variant.status = "error"
            variant.error = str(e)
            variant.elapsed = round(time.time() - start, 1)

        done, total = job.progress()
        if done >= total:
            job.status = "done"
        ref_store.update_job(job)

    def _apply_overlay(self, job, out_path):
        """Наложить PNG-водяной знак по центру (в исходном размере) поверх изображения."""
        if not job.overlay_path:
            return
        overlay_file = config.OUTPUT_DIR / job.overlay_path
        if not overlay_file.exists():
            return
        from PIL import Image
        with Image.open(out_path) as base_im:
            base = base_im.convert("RGBA")
        with Image.open(overlay_file) as ov_im:
            overlay = ov_im.convert("RGBA")

        # по центру, в исходном размере PNG
        x = (base.width - overlay.width) // 2
        y = (base.height - overlay.height) // 2

        # если оверлей больше базового изображения — уменьшаем, чтобы вписаться
        if overlay.width > base.width or overlay.height > base.height:
            scale = min(base.width / overlay.width, base.height / overlay.height)
            nw = max(1, int(overlay.width * scale))
            nh = max(1, int(overlay.height * scale))
            overlay = overlay.resize((nw, nh), Image.LANCZOS)
            x = (base.width - nw) // 2
            y = (base.height - nh) // 2

        base.alpha_composite(overlay, (x, y))
        # сохраняем обратно как RGB (PNG)
        base.convert("RGB").save(out_path, "PNG")

    def _resolve_reference(self, job, item, job_folder):
        """Вернуть путь к референсу: URL из CSV или поиск DDG по названию.

        Исходники сохраняются в sources-png/<source_name>/<ID>.png (PNG, плоская папка).
        Попутно сохраняет в item метаданные исходника:
        source_path (относительный путь), source_site (домен), source_fetched_at (время).
        """
        from store import slugify_source

        src_base = config.OUTPUT_DIR / "sources-png" / slugify_source(job.source_name)
        src_base.mkdir(parents=True, exist_ok=True)
        item_id_name = item.item_id or item.title or item.id
        # имя файла — ID товара (без суффиксов/подпапок)
        dest = src_base / f"{item_id_name}.png"

        # рабочий кэш для генерации (jpg как скачано) — в папке задачи
        refs_dir = config.OUTPUT_DIR / job_folder / "references"
        cache_dest = refs_dir / f"{_slugify(item_id_name)}.jpg"

        def _store_meta(path: Path, url: str):
            rel = path.relative_to(config.OUTPUT_DIR) if path.is_absolute() else path
            item.source_path = str(rel)
            item.source_site = _domain_of(url)
            item.source_fetched_at = time.time()

        # 1. Уже есть PNG-исходник в sources-png — используем его
        if dest.exists() and dest.stat().st_size > 0:
            if item.source_path is None:
                _store_meta(dest, item.image_url or "")
            return dest

        # 2. Явный URL из CSV
        source_url = None
        raw_file = None
        if item.image_url:
            if item.image_url.startswith(("http://", "https://")):
                source_url = item.image_url
                if not cache_dest.exists() or cache_dest.stat().st_size == 0:
                    if not image_search.download_image(item.image_url, cache_dest):
                        cache_dest = None
                raw_file = cache_dest
            else:
                p = Path(item.image_url)
                if p.exists():
                    raw_file = p

        # 3. Поиск по названию
        if raw_file is None:
            query = item.title or item.item_id or ""
            if query:
                min_w = min_h = 0
                if item.ref_type == "post_image":
                    min_w, min_h = 1920, 1080
                if not cache_dest.exists() or cache_dest.stat().st_size == 0:
                    url = image_search.find_reference_image(query, cache_dest, min_width=min_w, min_height=min_h)
                    if url:
                        source_url = url
                    else:
                        cache_dest = None
                raw_file = cache_dest if (cache_dest and cache_dest.exists() and cache_dest.stat().st_size > 0) else None

        if raw_file is None:
            return None

        # Конвертируем скачанный референс в PNG-исходник в sources-png
        try:
            from PIL import Image
            with Image.open(raw_file) as im:
                im = im.convert("RGB")
                im.save(dest, "PNG")
            _store_meta(dest, source_url or item.image_url or "")
            return dest
        except Exception:
            # fallback: отдаём сырой файл как есть
            if raw_file.exists():
                if item.source_path is None:
                    _store_meta(raw_file, source_url or "")
                return raw_file
            return None


def _domain_of(url):
    """Извлечь домен из URL (для подписи источника)."""
    if not url:
        return None
    import urllib.parse
    host = urllib.parse.urlparse(url).hostname
    if not host:
        return None
    # убираем www.
    return host[4:] if host.startswith("www.") else host


ref_queue = RefQueue()
