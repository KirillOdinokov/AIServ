"""Хранилище задач генерации с персистентностью в JSON."""
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

import config


def slugify_source(text):
    """Безопасное имя папки из имени файла/источника (без расширения)."""
    text = text or ""
    # убираем расширение (.csv, .xlsx и т.п.)
    text = Path(text).stem
    text = re.sub(r'[\\/:*?"<>|]', "", text)
    text = re.sub(r'\s+', "_", text.strip())
    return text[:80].strip("_")


class Variant:
    def __init__(self, model_id, seed, status="pending", filename=None, error=None, elapsed=None):
        self.id = uuid.uuid4().hex[:12]
        self.model_id = model_id
        self.seed = seed
        self.status = status  # pending | running | done | error
        self.filename = filename
        self.error = error
        self.elapsed = elapsed

    def to_dict(self):
        return {
            "id": self.id,
            "model_id": self.model_id,
            "seed": self.seed,
            "status": self.status,
            "filename": self.filename,
            "error": self.error,
            "elapsed": self.elapsed,
        }

    @classmethod
    def from_dict(cls, d):
        v = cls(d["model_id"], d.get("seed"), d.get("status", "pending"),
                d.get("filename"), d.get("error"), d.get("elapsed"))
        v.id = d["id"]
        return v


class Item:
    def __init__(self, prompt, filename, width, height, steps, guidance, seed,
                 reference_mode="none", reference_source=None, strength=0.7):
        self.id = uuid.uuid4().hex[:12]
        self.prompt = prompt
        self.filename = filename
        self.width = width
        self.height = height
        self.steps = steps
        self.guidance = guidance
        self.seed = seed
        # референс: "none" | "img2img" | "multimodal"
        self.reference_mode = reference_mode
        # источник референса: None | "auto" (поиск DDG по промпту) | URL | локальный путь
        self.reference_source = reference_source
        # сила переработки (denoise) для img2img: 0..1
        self.strength = strength
        self.variants = []
        self.approved_variant_id = None

    def to_dict(self):
        return {
            "id": self.id,
            "prompt": self.prompt,
            "filename": self.filename,
            "width": self.width,
            "height": self.height,
            "steps": self.steps,
            "guidance": self.guidance,
            "seed": self.seed,
            "reference_mode": self.reference_mode,
            "reference_source": self.reference_source,
            "strength": self.strength,
            "variants": [v.to_dict() for v in self.variants],
            "approved_variant_id": self.approved_variant_id,
        }

    @classmethod
    def from_dict(cls, d):
        it = cls(d["prompt"], d.get("filename"), d.get("width"), d.get("height"),
                 d.get("steps"), d.get("guidance"), d.get("seed"),
                 d.get("reference_mode", "none"), d.get("reference_source"),
                 d.get("strength", 0.7))
        it.id = d["id"]
        it.variants = [Variant.from_dict(v) for v in d.get("variants", [])]
        it.approved_variant_id = d.get("approved_variant_id")
        return it


class Job:
    def __init__(self, source_name, items):
        self.id = uuid.uuid4().hex[:12]
        self.source_name = source_name
        self.items = items
        self.models = []          # выбранные модели (id)
        self.variants_per_model = 1
        self.status = "draft"     # draft | queued | running | done | error
        self.created_at = time.time()
        self.error = None
        self.paused = False       # пауза конкретной задачи
        self.num = None           # последовательный номер (присваивается в add_job)
        self.logo_path = None     # путь к логотипу-референсу (относительно OUTPUT_DIR)

    def folder_name(self):
        """Уникальное имя папки результатов на основе номера задачи."""
        if self.num is not None:
            return f"{self.num:04d}_{slugify_source(self.source_name)}"
        return slugify_source(self.source_name)

    def to_dict(self):
        return {
            "id": self.id,
            "source_name": self.source_name,
            "items": [i.to_dict() for i in self.items],
            "models": self.models,
            "variants_per_model": self.variants_per_model,
            "status": self.status,
            "created_at": self.created_at,
            "error": self.error,
            "paused": self.paused,
            "num": self.num,
            "logo_path": self.logo_path,
        }

    @classmethod
    def from_dict(cls, d):
        j = cls(d["source_name"], [Item.from_dict(i) for i in d.get("items", [])])
        j.id = d["id"]
        j.models = d.get("models", [])
        j.variants_per_model = d.get("variants_per_model", 1)
        j.status = d.get("status", "draft")
        j.created_at = d.get("created_at", time.time())
        j.error = d.get("error")
        j.paused = d.get("paused", False)
        j.num = d.get("num")
        j.logo_path = d.get("logo_path")
        return j

    def progress(self):
        total = len(self.items) * len(self.models) * self.variants_per_model
        done = 0
        for it in self.items:
            for v in it.variants:
                if v.status in ("done", "error"):
                    done += 1
        return done, total

    def eta_seconds(self):
        """Оценка оставшегося времени (сек) по среднему времени готовых единиц."""
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


class Store:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs = {}
        self._uploads = {}  # upload_id -> {source_name, headers, rows}
        self._file = config.BASE_DIR / "jobs.json"
        self._bak = config.BASE_DIR / "jobs.json.bak"
        self._load()

    def _load(self):
        # Пробуем основной файл, затем резервную копию
        for f in (self._file, self._bak):
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                for d in data.get("jobs", []):
                    j = Job.from_dict(d)
                    # Сброс зависших статусов после перезапуска сервера:
                    # варианты "running" -> "pending", "queued"/"running" задачи -> "draft"
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
                print(f"[store] Ошибка загрузки {f.name}: {e}")
                # если основной файл битый — пробуем бэкап
                continue
        print("[store] Не удалось загрузить jobs.json — начинаем с пустого списка задач.")

    def _save(self):
        """Атомарная запись с принудительным сбросом на диск (fsync).
        Защита от обрыва питания: файл никогда не останется с нулями,
        а валидный бэкап не затирается битым рабочим файлом."""
        tmp = self._file.with_suffix(".json.tmp")
        try:
            data = {"jobs": [j.to_dict() for j in self._jobs.values()]}
            text = json.dumps(data, ensure_ascii=False, indent=2)

            # 1. Пишем во временный файл и принудительно сбрасываем на диск
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())

            # 2. Бэкап делаем только если текущий файл валиден
            #    (битый файл не должен затирать хороший .bak)
            if self._file.exists():
                try:
                    with open(self._file, encoding="utf-8") as f:
                        json.load(f)
                    os.replace(str(self._file), str(self._bak))
                except (OSError, ValueError):
                    pass

            # 3. Атомарно подменяем рабочий файл
            os.replace(str(tmp), str(self._file))
        except Exception as e:
            print(f"[store] Ошибка сохранения jobs.json: {e}")

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

    def add_upload(self, upload_id, source_name, headers, rows):
        with self._lock:
            self._uploads[upload_id] = {
                "source_name": source_name,
                "headers": headers,
                "rows": rows,
            }
        return upload_id

    def get_upload(self, upload_id):
        with self._lock:
            return self._uploads.get(upload_id)

    def delete_upload(self, upload_id):
        with self._lock:
            self._uploads.pop(upload_id, None)


store = Store()
