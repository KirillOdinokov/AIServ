"""Хранилище задач генерации описаний (LLM) с персистентностью в JSON."""
import json
import os
import threading
import time
import uuid
from pathlib import Path

import config


class LlmItem:
    def __init__(self, item_id, title, excerpt=None, content=None):
        self.id = uuid.uuid4().hex[:12]
        self.item_id = item_id          # ID товара из CSV
        self.title = title              # post_title
        self.excerpt = excerpt          # исходное короткое описание
        self.content = content          # исходное полное описание
        self.new_excerpt = None         # сгенерированное короткое
        self.new_content = None         # сгенерированное длинное
        self.status = "pending"         # pending | running | done | error
        self.error = None
        self.elapsed = None             # время генерации (сек)

    def to_dict(self):
        return {
            "id": self.id,
            "item_id": self.item_id,
            "title": self.title,
            "excerpt": self.excerpt,
            "content": self.content,
            "new_excerpt": self.new_excerpt,
            "new_content": self.new_content,
            "status": self.status,
            "error": self.error,
            "elapsed": self.elapsed,
        }

    @classmethod
    def from_dict(cls, d):
        it = cls(d["item_id"], d.get("title"), d.get("excerpt"), d.get("content"))
        it.id = d["id"]
        it.new_excerpt = d.get("new_excerpt")
        it.new_content = d.get("new_content")
        it.status = d.get("status", "pending")
        it.error = d.get("error")
        it.elapsed = d.get("elapsed")
        return it


class LlmJob:
    def __init__(self, source_name, items, model="deepseek"):
        self.id = uuid.uuid4().hex[:12]
        self.source_name = source_name
        self.items = items
        self.model = model              # deepseek | local
        self.status = "draft"           # draft | queued | running | paused | done | error
        self.created_at = time.time()
        self.paused = False             # пауза конкретной задачи

    def to_dict(self):
        return {
            "id": self.id,
            "source_name": self.source_name,
            "items": [i.to_dict() for i in self.items],
            "model": self.model,
            "status": self.status,
            "created_at": self.created_at,
            "paused": self.paused,
        }

    @classmethod
    def from_dict(cls, d):
        j = cls(d["source_name"], [LlmItem.from_dict(i) for i in d.get("items", [])], d.get("model", "deepseek"))
        j.id = d["id"]
        j.status = d.get("status", "draft")
        j.created_at = d.get("created_at", time.time())
        j.paused = d.get("paused", False)
        return j

    def progress(self):
        total = len(self.items)
        done = sum(1 for i in self.items if i.status in ("done", "error"))
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
            if it.status in ("done", "error") and it.elapsed:
                elapsed_sum += it.elapsed
                elapsed_count += 1
        if elapsed_count == 0:
            return None
        return round(remaining * elapsed_sum / elapsed_count)


class LlmStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs = {}
        self._file = config.BASE_DIR / "llm_jobs.json"
        self._load()

    def _load(self):
        for f in (self._file, self._file.with_suffix(".json.bak")):
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                for d in data.get("jobs", []):
                    j = LlmJob.from_dict(d)
                    for it in j.items:
                        if it.status == "running":
                            it.status = "pending"
                    if j.status in ("queued", "running"):
                        j.status = "draft"
                    self._jobs[j.id] = j
                self._save()
                return
            except Exception as e:
                print(f"[llm_store] Ошибка загрузки {f.name}: {e}")
                continue
        print("[llm_store] Не удалось загрузить llm_jobs.json — начинаем с пустого списка.")

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
            print(f"[llm_store] Ошибка сохранения llm_jobs.json: {e}")

    def add_job(self, job):
        with self._lock:
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


llm_store = LlmStore()
