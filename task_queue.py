"""Фоновая очередь генерации вариантов."""
import random
import re
import threading
import time
from pathlib import Path

import config
from generator import generator
from store import store


def _slugify(text):
    """Преобразовать промпт в безопасное имя папки."""
    text = re.sub(r'[\\/:*?"<>|]', "", text)
    text = re.sub(r'\s+', "_", text.strip())
    text = text[:80].strip("_")
    return text


class TaskQueue:
    def __init__(self):
        self._queue = []  # список (job_id, item_id, variant_id)
        self._lock = threading.Lock()
        self._paused = False
        self._current_model = None  # какая модель сейчас в VRAM
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def pause(self):
        with self._lock:
            self._paused = True

    def resume(self):
        with self._lock:
            self._paused = False
        # Возобновляем незавершённые задачи, оставшиеся после перезапуска
        self.resume_all()

    def is_paused(self):
        with self._lock:
            return self._paused

    def current_model(self):
        """Какая модель сейчас загружена в VRAM (или None)."""
        with self._lock:
            return self._current_model

    def resume_all(self):
        """Восстановить очередь из незавершённых задач (после перезапуска сервера)."""
        for job in store.list_jobs():
            if job.status in ("done",):
                continue
            if not job.models:
                continue
            has_pending = any(
                v.status == "pending" for it in job.items for v in it.variants
            )
            if has_pending:
                self.submit_job(job)

    def submit_job(self, job):
        """Поставить все pending-варианты задачи в очередь, сгруппировав по модели."""
        with self._lock:
            # Группируем варианты по модели, чтобы не переключать модель постоянно.
            # Сначала все изображения одной модели, потом следующей.
            by_model = {}
            for item in job.items:
                for v in item.variants:
                    if v.status == "pending":
                        by_model.setdefault(v.model_id, []).append((job.id, item.id, v.id))
            for model_id in job.models:  # сохраняем порядок выбранных моделей
                for entry in by_model.get(model_id, []):
                    if entry not in self._queue:
                        self._queue.append(entry)
        if job.status != "running":
            job.status = "queued"
            store.update_job(job)

    def submit_variant(self, job, item, variant):
        """Поставить один вариант в очередь (перегенерация)."""
        with self._lock:
            self._queue.append((job.id, item.id, variant.id))
        if job.status in ("done", "error"):
            job.status = "queued"
        store.update_job(job)

    def pause_job(self, job_id):
        """Поставить конкретную задачу на паузу."""
        job = store.get_job(job_id)
        if job is None:
            return False
        job.paused = True
        if job.status in ("queued", "running"):
            job.status = "queued"
        store.update_job(job)
        return True

    def resume_job(self, job_id):
        """Снять паузу с конкретной задачи и вернуть её в очередь."""
        job = store.get_job(job_id)
        if job is None:
            return False
        job.paused = False
        if job.status in ("queued", "running", "draft"):
            self.submit_job(job)
        store.update_job(job)
        return True

    def _pick_entry(self):
        """Выбрать следующий вариант, предпочитая ту модель, что уже в VRAM.

        Группировка по модели минимизирует выгрузку/загрузку из VRAM.
        """
        with self._lock:
            if not self._queue:
                return None, None, None

            for idx, e in enumerate(self._queue):
                job = store.get_job(e[0])
                if job is None or job.paused:
                    continue
                item = next((i for i in job.items if i.id == e[1]), None)
                variant = next((v for v in item.variants if v.id == e[2]), None) if item else None
                if variant is not None and variant.model_id == self._current_model:
                    return self._queue.pop(idx), item, variant

            for idx, e in enumerate(self._queue):
                job = store.get_job(e[0])
                if job is None or job.paused:
                    continue
                item = next((i for i in job.items if i.id == e[1]), None)
                variant = next((v for v in item.variants if v.id == e[2]), None) if item else None
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

            # При смене модели — выгружаем предыдущую из VRAM
            if variant is not None and variant.model_id != self._current_model:
                self._unload_model()
                self._current_model = variant.model_id

            try:
                self._process(job_id, item_id, variant_id)
            except Exception as e:
                print(f"[queue] Ошибка обработки {variant_id}: {e}")

    def _unload_model(self):
        """Выгрузить текущую модель из VRAM (ComfyUI и/или diffusers)."""
        # diffusers-модель
        if generator._pipe is not None:
            generator._unload_diffusers()
        # ComfyUI-модель
        import comfy_client
        comfy_client.free_memory(unload_models=True, free_memory=True)

    def _process(self, job_id, item_id, variant_id):
        job = store.get_job(job_id)
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
            store.update_job(job)

        variant.status = "running"
        store.update_job(job)

        model_id = variant.model_id
        if model_id not in config.MODELS:
            variant.status = "error"
            variant.error = f"Модель {model_id} не найдена"
            store.update_job(job)
            return
        info = config.MODELS[model_id]
        steps = item.steps if item.steps is not None else info["default_steps"]
        guidance = item.guidance if item.guidance is not None else info["guidance_scale"]

        seed = variant.seed
        if seed is None or seed < 0:
            seed = random.randint(0, 2**32 - 1)
            variant.seed = seed

        job_folder = job.folder_name() or f"job_{job.id}"
        item_id_name = item.filename or item.id
        model_label = _slugify(config.MODELS[model_id].get("label", model_id))
        # Временное хранилище до выбора: {CSV}/{ID}_{модель}/{variant}.png
        variant_folder = f"{item_id_name}_{model_label}"
        out_name = f"{variant.id}.png"
        out_path = config.OUTPUT_DIR / job_folder / variant_folder / out_name

        # ─── Референс: поиск + скачивание ───────────────────────────────────
        reference_image = self._resolve_reference(job, item, job_folder)
        reference_mode = item.reference_mode or "none"

        # Если у задачи есть логотип — используем его как multimodal-референс
        # (промт описывает, куда нанести лого).
        logo_path = getattr(job, "logo_path", None)
        if logo_path:
            logo_file = config.OUTPUT_DIR / logo_path
            if logo_file.exists():
                reference_image = logo_file
                reference_mode = "multimodal"

        start = time.time()
        try:
            generator.generate(
                item.prompt, model_id, item.width, item.height,
                steps, guidance, seed, out_path,
                reference_image=reference_image,
                reference_mode=reference_mode,
                strength=item.strength if item.strength is not None else 0.7,
            )
            variant.filename = f"{job_folder}/{variant_folder}/{out_name}"
            variant.status = "done"
            variant.elapsed = round(time.time() - start, 1)
        except Exception as e:
            variant.status = "error"
            variant.error = str(e)
            variant.elapsed = round(time.time() - start, 1)

        # Проверяем, всё ли готово
        done, total = job.progress()
        if done >= total:
            job.status = "done"
        store.update_job(job)

    def _resolve_reference(self, job, item, job_folder):
        """Вернуть путь к картинке-референсу (или None).

        Поддерживает reference_source: "auto" (поиск DDG), URL, локальный путь.
        Скачанный референс кэшируется в папке задачи references/.
        """
        mode = (item.reference_mode or "none").lower()
        if mode == "none":
            return None
        src = item.reference_source
        if not src:
            return None

        refs_dir = config.OUTPUT_DIR / job_folder / "references"
        item_id_name = item.filename or item.id
        dest = refs_dir / f"{item_id_name}.jpg"

        # уже скачан ранее — переиспользуем кэш
        if dest.exists() and dest.stat().st_size > 0:
            return dest

        # "auto" — ищем картинку по промпту через DuckDuckGo
        if src == "auto":
            import image_search
            query = item.prompt or item_id_name
            url = image_search.find_reference_image(query, dest)
            if url and dest.exists():
                return dest
            return None

        # URL — скачиваем напрямую
        if src.startswith("http://") or src.startswith("https://"):
            import image_search
            if image_search.download_image(src, dest):
                return dest
            return None

        # локальный путь
        p = Path(src)
        if p.exists():
            return p
        return None


queue = TaskQueue()
