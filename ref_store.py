"""Хранилище задач генерации по референсам с персистентностью в JSON."""
import json
import os
import threading
import time
import uuid
from pathlib import Path

import config


class RefVariant:
    """Один сгенерированный вариант изображения для товара."""
    def __init__(self, model_id, reference_mode, strength):
        self.id = uuid.uuid4().hex[:12]
        self.model_id = model_id
        self.reference_mode = reference_mode  # img2img | multimodal
        self.strength = strength if strength is not None else 0.5
        self.seed = -1
        self.status = "pending"  # pending | running | done | error
        self.filename = None      # относительный путь к PNG
        self.error = None
        self.elapsed = None

    def to_dict(self):
        return {
            "id": self.id,
            "model_id": self.model_id,
            "reference_mode": self.reference_mode,
            "strength": self.strength,
            "seed": self.seed,
            "status": self.status,
            "filename": self.filename,
            "error": self.error,
            "elapsed": self.elapsed,
        }

    @classmethod
    def from_dict(cls, d):
        v = cls(d["model_id"], d.get("reference_mode", "img2img"), d.get("strength", 0.5))
        v.id = d["id"]
        v.seed = d.get("seed", -1)
        v.status = d.get("status", "pending")
        v.filename = d.get("filename")
        v.error = d.get("error")
        v.elapsed = d.get("elapsed")
        return v


class RefItem:
    """Одна строка CSV (товар/запись), возможно с несколькими вариантами."""
    def __init__(self, title, item_id, image_url, ref_type, width, height, prompt=""):
        self.id = uuid.uuid4().hex[:12]
        self.title = title            # post_title — название товара/записи
        self.item_id = item_id        # ID товара
        self.image_url = image_url    # URL картинки-референса (может быть None)
        self.ref_type = ref_type      # standart | storage | post_image | application
        self.width = width
        self.height = height
        self.prompt = prompt          # готовый промпт (шаблон + title + site), заполняется при configure
        self.output_name = None       # имя выходного файла (для all_product добавляется суффикс типа)
        self.source_path = None       # путь к исходнику-референсу (относительно OUTPUT_DIR)
        self.source_site = None       # домен сайта, откуда взят референс
        self.source_fetched_at = None # время получения референса (epoch)
        self.variants = []
        self.approved_variant_id = None

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "item_id": self.item_id,
            "image_url": self.image_url,
            "ref_type": self.ref_type,
            "width": self.width,
            "height": self.height,
            "prompt": self.prompt,
            "output_name": self.output_name,
            "source_path": self.source_path,
            "source_site": self.source_site,
            "source_fetched_at": self.source_fetched_at,
            "variants": [v.to_dict() for v in self.variants],
            "approved_variant_id": self.approved_variant_id,
        }

    @classmethod
    def from_dict(cls, d):
        it = cls(d.get("title"), d.get("item_id"), d.get("image_url"),
                 d.get("ref_type"), d.get("width"), d.get("height"), d.get("prompt", ""))
        it.id = d["id"]
        it.output_name = d.get("output_name")
        it.source_path = d.get("source_path")
        it.source_site = d.get("source_site")
        it.source_fetched_at = d.get("source_fetched_at")
        it.variants = [RefVariant.from_dict(v) for v in d.get("variants", [])]
        it.approved_variant_id = d.get("approved_variant_id")
        return it


class RefJob:
    def __init__(self, source_name, items):
        self.id = uuid.uuid4().hex[:12]
        self.source_name = source_name
        self.items = items
        self.site_name = ""           # название сайта (вшивается в промпт)
        self.modes = []               # выбранные режимы: ["img2img", "multimodal"]
        self.copies = {}              # режим -> количество копий (по умолчанию 1)
        self.strength = 0.5           # сила img2img (по умолчанию)
        self.watermark = False        # режим Watermark: без бренда, картинка ~исходная без водяного знака
        self.overlay_path = None      # путь к PNG-водяному знаку для наложения поверх (относительно OUTPUT_DIR)
        self.status = "draft"         # draft | queued | running | done | error
        self.created_at = time.time()
        self.paused = False
        self.num = None               # последовательный номер

    def folder_name(self):
        from store import slugify_source
        if self.num is not None:
            return f"{self.num:04d}_ref_{slugify_source(self.source_name)}"
        return "ref_" + slugify_source(self.source_name)

    def to_dict(self):
        return {
            "id": self.id,
            "source_name": self.source_name,
            "items": [i.to_dict() for i in self.items],
            "site_name": self.site_name,
            "modes": self.modes,
            "copies": self.copies,
            "strength": self.strength,
            "watermark": self.watermark,
            "overlay_path": self.overlay_path,
            "status": self.status,
            "created_at": self.created_at,
            "paused": self.paused,
            "num": self.num,
        }

    @classmethod
    def from_dict(cls, d):
        j = cls(d["source_name"], [RefItem.from_dict(i) for i in d.get("items", [])])
        j.id = d["id"]
        j.site_name = d.get("site_name", "")
        j.modes = d.get("modes", [])
        j.copies = d.get("copies", {})
        j.strength = d.get("strength", 0.5)
        j.watermark = d.get("watermark", False)
        j.overlay_path = d.get("overlay_path")
        j.status = d.get("status", "draft")
        j.created_at = d.get("created_at", time.time())
        j.paused = d.get("paused", False)
        j.num = d.get("num")
        return j

    def progress(self):
        total = sum(len(i.variants) for i in self.items)
        done = sum(1 for i in self.items for v in i.variants if v.status in ("done", "error"))
        return done, total

    def eta_seconds(self):
        done, total = self.progress()
        remaining = total - done
        if remaining <= 0:
            return 0
        elapsed_sum = 0.0
        elapsed_count = 0
        for it in self.items:
            for v in it.variants:
                if v.status in ("done", "error") and v.elapsed:
                    elapsed_sum += v.elapsed
                    elapsed_count += 1
        if elapsed_count == 0:
            return None
        return round(remaining * elapsed_sum / elapsed_count)


class RefStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs = {}
        self._file = config.BASE_DIR / "ref_jobs.json"
        self._load()

    def _load(self):
        for f in (self._file, self._file.with_suffix(".json.bak")):
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                for d in data.get("jobs", []):
                    j = RefJob.from_dict(d)
                    for it in j.items:
                        for v in it.variants:
                            if v.status == "running":
                                v.status = "pending"
                    if j.status in ("queued", "running"):
                        j.status = "draft"
                    self._jobs[j.id] = j
                self._save()
                return
            except Exception as e:
                print(f"[ref_store] Ошибка загрузки {f.name}: {e}")
                continue
        print("[ref_store] Не удалось загрузить ref_jobs.json — начинаем с пустого списка.")

    def _save(self):
        tmp = self._file.with_suffix(".json.tmp")
        try:
            data = {"jobs": [j.to_dict() for j in self._jobs.values()]}
            text = json.dumps(data, ensure_ascii=False, indent=2)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            if self._file.exists():
                try:
                    with open(self._file, encoding="utf-8") as f:
                        json.load(f)
                    os.replace(str(self._file), str(self._file.with_suffix(".json.bak")))
                except (OSError, ValueError):
                    pass
            os.replace(str(tmp), str(self._file))
        except Exception as e:
            print(f"[ref_store] Ошибка сохранения ref_jobs.json: {e}")

    def add_job(self, job):
        with self._lock:
            if job.num is None:
                max_num = max((j.num or 0 for j in self._jobs.values()), default=0)
                job.num = max_num + 1
            self._jobs[job.id] = job
            self._save()
        return job

    def get_job(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self):
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def update_job(self, job):
        with self._lock:
            self._jobs[job.id] = job
            self._save()

    def delete_job(self, job_id):
        with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                self._save()
                return True
        return False


ref_store = RefStore()
