"""Хранилище задач визуализации интерьера с персистентностью в JSON."""
import json
import os
import threading
import time
import uuid
from pathlib import Path

import config


class InteriorVariant:
    def __init__(self, prompt=""):
        self.id = uuid.uuid4().hex[:12]
        self.prompt = prompt
        self.status = "pending"
        self.filename = None
        self.error = None
        self.elapsed = None
        self.seed = -1

    def to_dict(self):
        return {
            "id": self.id,
            "prompt": self.prompt,
            "status": self.status,
            "filename": self.filename,
            "error": self.error,
            "elapsed": self.elapsed,
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, d):
        v = cls(d.get("prompt", ""))
        v.id = d["id"]
        v.status = d.get("status", "pending")
        v.filename = d.get("filename")
        v.error = d.get("error")
        v.elapsed = d.get("elapsed")
        v.seed = d.get("seed", -1)
        return v


class InteriorJob:
    def __init__(self, source_name, image_path, user_prompt, count, width, height,
                 llm_provider="ollama_cloud", llm_model=""):
        self.id = uuid.uuid4().hex[:12]
        self.source_name = source_name
        self.image_path = image_path
        self.user_prompt = user_prompt
        self.count = max(1, min(8, int(count)))
        self.width = width
        self.height = height
        self.model_id = "flux2-klein-4b"
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.variants = []
        self.prompts_ready = False
        self.llm_error = None
        self.status = "draft"
        self.created_at = time.time()
        self.paused = False
        self.num = None

    def folder_name(self):
        from store import slugify_source
        slug = slugify_source(self.source_name or "interior")
        if self.num is not None:
            base = f"{self.num:04d}_interior"
        else:
            base = "interior_" + self.id
        if slug and slug != "interior":
            return base + "_" + slug
        return base

    def progress(self):
        total = len(self.variants) if self.prompts_ready else self.count
        done = sum(1 for v in self.variants if v.status in ("done", "error"))
        return done, total

    def eta_seconds(self):
        done, total = self.progress()
        remaining = total - done
        if remaining <= 0:
            return 0
        elapsed_sum = 0.0
        elapsed_count = 0
        for v in self.variants:
            if v.status in ("done", "error") and v.elapsed:
                elapsed_sum += v.elapsed
                elapsed_count += 1
        if elapsed_count == 0:
            return None
        return round(remaining * elapsed_sum / elapsed_count)

    def to_dict(self):
        return {
            "id": self.id,
            "source_name": self.source_name,
            "image_path": self.image_path,
            "user_prompt": self.user_prompt,
            "count": self.count,
            "width": self.width,
            "height": self.height,
            "model_id": self.model_id,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "variants": [v.to_dict() for v in self.variants],
            "prompts_ready": self.prompts_ready,
            "llm_error": self.llm_error,
            "status": self.status,
            "created_at": self.created_at,
            "paused": self.paused,
            "num": self.num,
        }

    @classmethod
    def from_dict(cls, d):
        j = cls(
            d.get("source_name"),
            d.get("image_path"),
            d.get("user_prompt", ""),
            d.get("count", 1),
            d.get("width", 1024),
            d.get("height", 1024),
            d.get("llm_provider", "ollama_cloud"),
            d.get("llm_model", ""),
        )
        j.id = d["id"]
        j.model_id = d.get("model_id", "flux2-klein-4b")
        j.variants = [InteriorVariant.from_dict(v) for v in d.get("variants", [])]
        j.prompts_ready = d.get("prompts_ready", bool(j.variants))
        j.llm_error = d.get("llm_error")
        j.status = d.get("status", "draft")
        j.created_at = d.get("created_at", time.time())
        j.paused = d.get("paused", False)
        j.num = d.get("num")
        for v in j.variants:
            if v.status == "running":
                v.status = "pending"
        if j.status in ("queued", "running"):
            j.status = "draft"
        return j


class InteriorStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs = {}
        self._file = config.BASE_DIR / "interior_jobs.json"
        self._load()

    def _load(self):
        for f in (self._file, self._file.with_suffix(".json.bak")):
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                for d in data.get("jobs", []):
                    j = InteriorJob.from_dict(d)
                    self._jobs[j.id] = j
                self._save()
                return
            except Exception as e:
                print(f"[interior_store] Ошибка загрузки {f.name}: {e}")
                continue
        print("[interior_store] Нет interior_jobs.json — начинаем с пустого списка.")

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
            print(f"[interior_store] Ошибка сохранения interior_jobs.json: {e}")

    def add_job(self, job):
        with self._lock:
            if job.num is None:
                job.num = max((j.num or 0 for j in self._jobs.values()), default=0) + 1
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


interior_store = InteriorStore()
