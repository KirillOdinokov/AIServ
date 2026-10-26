"""Хранилище задач массового апскейла с персистентностью в JSON."""
import json
import os
import threading
import time
import uuid

import config


class UpscaleItem:
    """Одна строка CSV: id + URL картинки."""
    def __init__(self, item_id, image_url):
        self.id = uuid.uuid4().hex[:12]
        self.item_id = item_id
        self.image_url = image_url
        self.status = "pending"  # pending | running | done | error
        self.filename = None      # относительный путь к webp
        self.error = None

    def to_dict(self):
        return {
            "id": self.id,
            "item_id": self.item_id,
            "image_url": self.image_url,
            "status": self.status,
            "filename": self.filename,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d):
        it = cls(d.get("item_id"), d.get("image_url"))
        it.id = d["id"]
        it.status = d.get("status", "pending")
        it.filename = d.get("filename")
        it.error = d.get("error")
        return it


class UpscaleBatch:
    def __init__(self, source_name, model, prompt, items):
        self.id = uuid.uuid4().hex[:12]
        self.source_name = source_name
        self.model = model
        self.prompt = prompt
        self.items = items
        self.status = "running"  # running | done | error
        self.created_at = time.time()
        self.num = None

    def folder_name(self):
        from store import slugify_source
        if self.num is not None:
            return f"{self.num:04d}_{slugify_source(self.source_name)}"
        return "upscale_" + slugify_source(self.source_name)

    def progress(self):
        total = len(self.items)
        done = sum(1 for i in self.items if i.status in ("done", "error"))
        return done, total

    def to_dict(self):
        return {
            "id": self.id,
            "source_name": self.source_name,
            "model": self.model,
            "prompt": self.prompt,
            "items": [i.to_dict() for i in self.items],
            "status": self.status,
            "created_at": self.created_at,
            "num": self.num,
        }

    @classmethod
    def from_dict(cls, d):
        j = cls(d.get("source_name"), d.get("model"), d.get("prompt"),
                [UpscaleItem.from_dict(i) for i in d.get("items", [])])
        j.id = d["id"]
        j.status = d.get("status", "running")
        j.created_at = d.get("created_at", time.time())
        j.num = d.get("num")
        return j


class UpscaleStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs = {}
        self._file = config.BASE_DIR / "upscale_jobs.json"
        self._load()

    def _load(self):
        for f in (self._file, self._file.with_suffix(".json.bak")):
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                for d in data.get("jobs", []):
                    j = UpscaleBatch.from_dict(d)
                    for it in j.items:
                        if it.status == "running":
                            it.status = "pending"
                    self._jobs[j.id] = j
                self._save()
                return
            except Exception as e:
                print(f"[upscale_store] Ошибка загрузки {f.name}: {e}")
                continue
        print("[upscale_store] Не удалось загрузить upscale_jobs.json — начинаем с пустого списка.")

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
            print(f"[upscale_store] Ошибка сохранения upscale_jobs.json: {e}")

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


upscale_store = UpscaleStore()
