"""Хранилище задач генерации миниатюр категорий (WP/WooCommerce)."""
import json
import os
import threading
import time
import uuid

import config


class ThumbItem:
    """Одна категория: её товар-референс и сгенерированная миниатюра."""
    def __init__(self, category_id, category_name, category_slug,
                 product_id=None, product_name=None, product_image_url=None):
        self.id = uuid.uuid4().hex[:12]
        self.category_id = category_id
        self.category_name = category_name
        self.category_slug = category_slug
        self.product_id = product_id
        self.product_name = product_name
        self.product_image_url = product_image_url
        self.status = "pending"   # pending | running | done | skipped | error
        self.source_path = None   # скачанное изображение товара (отн. OUTPUT_DIR)
        self.filename = None      # сгенерированный webp (отн. OUTPUT_DIR)
        self.media_id = None      # id загруженного в WP медиа
        self.error = None
        self.elapsed = None

    def to_dict(self):
        return {
            "id": self.id,
            "category_id": self.category_id,
            "category_name": self.category_name,
            "category_slug": self.category_slug,
            "product_id": self.product_id,
            "product_name": self.product_name,
            "product_image_url": self.product_image_url,
            "status": self.status,
            "source_path": self.source_path,
            "filename": self.filename,
            "media_id": self.media_id,
            "error": self.error,
            "elapsed": self.elapsed,
        }

    @classmethod
    def from_dict(cls, d):
        it = cls(d.get("category_id"), d.get("category_name"), d.get("category_slug"),
                 d.get("product_id"), d.get("product_name"), d.get("product_image_url"))
        it.id = d["id"]
        it.status = d.get("status", "pending")
        it.source_path = d.get("source_path")
        it.filename = d.get("filename")
        it.media_id = d.get("media_id")
        it.error = d.get("error")
        it.elapsed = d.get("elapsed")
        return it


class ThumbJob:
    def __init__(self, source_name, site, items=None):
        self.id = uuid.uuid4().hex[:12]
        self.source_name = source_name
        self.site = site
        self.items = items or []
        self.status = "fetching"  # fetching | ready | running | done | error
        self.created_at = time.time()
        self.paused = False
        self.num = None
        self.error = None

    def folder_name(self):
        from store import slugify_source
        if self.num is not None:
            return f"{self.num:04d}_thumbs_{slugify_source(self.source_name)}"
        return "thumbs_" + slugify_source(self.source_name)

    def to_dict(self):
        return {
            "id": self.id,
            "source_name": self.source_name,
            "site": self.site,
            "items": [i.to_dict() for i in self.items],
            "status": self.status,
            "created_at": self.created_at,
            "paused": self.paused,
            "num": self.num,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d):
        j = cls(d.get("source_name"), d.get("site"),
                [ThumbItem.from_dict(i) for i in d.get("items", [])])
        j.id = d["id"]
        j.status = d.get("status", "fetching")
        j.created_at = d.get("created_at", time.time())
        j.paused = d.get("paused", False)
        j.num = d.get("num")
        j.error = d.get("error")
        return j

    def progress(self):
        total = len(self.items)
        done = sum(1 for i in self.items if i.status in ("done", "skipped", "error"))
        return done, total


class ThumbStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs = {}
        self._file = config.BASE_DIR / "thumb_jobs.json"
        self._load()

    def _load(self):
        for f in (self._file, self._file.with_suffix(".json.bak")):
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                for d in data.get("jobs", []):
                    j = ThumbJob.from_dict(d)
                    for it in j.items:
                        if it.status == "running":
                            it.status = "pending"
                    if j.status == "running":
                        j.status = "ready"
                    self._jobs[j.id] = j
                self._save()
                return
            except Exception as e:
                print(f"[thumb_store] Ошибка загрузки {f.name}: {e}")
                continue
        print("[thumb_store] Не удалось загрузить thumb_jobs.json — начинаем с пустого списка.")

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
            print(f"[thumb_store] Ошибка сохранения thumb_jobs.json: {e}")

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


thumb_store = ThumbStore()
