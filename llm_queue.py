"""Фоновая очередь генерации описаний (LLM) с учётом peak-времени."""
import datetime
import threading
import time

import llm_client
from llm_store import llm_store


class LlmQueue:
    def __init__(self):
        self._queue = []  # список (job_id, item_id)
        self._lock = threading.Lock()
        self._paused = False
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

    def get_status(self):
        """Текущий статус очереди с причиной остановки."""
        with self._lock:
            paused = self._paused
            queue_len = len(self._queue)
            queue = list(self._queue)

        peak = llm_client.is_peak_time()
        now = datetime.datetime.now(datetime.timezone.utc)
        now_local = now.astimezone(datetime.timezone(datetime.timedelta(hours=3)))

        # DeepSeek в peak-время блокируется; локальные модели работают всегда
        blocked_peak = False
        has_workable = False
        for job_id, _item_id in queue:
            job = llm_store.get_job(job_id)
            if job is None or job.paused:
                continue
            if job.model == "deepseek" and peak:
                blocked_peak = True
            else:
                has_workable = True

        if paused:
            reason = "paused_manual"
            label = "Остановлено пользователем вручную"
        elif queue_len > 0 and not has_workable and blocked_peak:
            reason = "paused_peak"
            label = "Остановлено по Peak Time (DeepSeek)"
        elif queue_len > 0:
            reason = "running"
            label = "Идёт генерация"
        else:
            reason = "idle"
            label = "Нет задач в очереди"

        return {
            "reason": reason,
            "label": label,
            "paused": paused,
            "peak": peak,
            "queue_len": queue_len,
            "current_time_utc": now.strftime("%H:%M:%S"),
            "current_time_local": now_local.strftime("%H:%M:%S"),
            "peak_windows_local": llm_client.peak_windows_local(3),
        }

    def submit_job(self, job):
        with self._lock:
            for item in job.items:
                if item.status == "pending":
                    entry = (job.id, item.id)
                    if entry not in self._queue:
                        self._queue.append(entry)
        if job.status != "running":
            job.status = "queued"
            llm_store.update_job(job)

    def resume_all(self):
        for job in llm_store.list_jobs():
            if job.status in ("done",):
                continue
            has_pending = any(i.status == "pending" for i in job.items)
            if has_pending:
                self.submit_job(job)

    def pause_job(self, job_id):
        """Поставить конкретную задачу на паузу."""
        job = llm_store.get_job(job_id)
        if job is None:
            return False
        job.paused = True
        if job.status in ("queued", "running"):
            job.status = "queued"
        llm_store.update_job(job)
        return True

    def resume_job(self, job_id):
        """Снять паузу с конкретной задачи и вернуть её в очередь."""
        job = llm_store.get_job(job_id)
        if job is None:
            return False
        job.paused = False
        if job.status in ("queued", "running", "draft"):
            self.submit_job(job)
        llm_store.update_job(job)
        return True

    def _run(self):
        while True:
            if self.is_paused():
                time.sleep(1)
                continue

            # Выбираем следующий элемент для обработки.
            # В peak-время deepseek-задачи откладываем (дорогой тариф),
            # но локальные модели обрабатываем без задержки.
            entry = None
            with self._lock:
                for idx, e in enumerate(self._queue):
                    job = llm_store.get_job(e[0])
                    if job is None:
                        continue
                    if job.paused:
                        continue
                    if job.model == "deepseek" and llm_client.is_peak_time():
                        continue
                    entry = self._queue.pop(idx)
                    break

            if entry is None:
                time.sleep(1)
                continue

            job_id, item_id = entry
            try:
                self._process(job_id, item_id)
            except Exception as e:
                print(f"[llm_queue] Ошибка обработки {item_id}: {e}")

    def _process(self, job_id, item_id):
        job = llm_store.get_job(job_id)
        if job is None:
            return
        item = next((i for i in job.items if i.id == item_id), None)
        if item is None:
            return

        if job.status != "running":
            job.status = "running"
            llm_store.update_job(job)

        item.status = "running"
        llm_store.update_job(job)

        start = time.time()
        try:
            new_excerpt, new_content = llm_client.generate_description(
                item.title, item.excerpt, item.content, model=job.model
            )
            item.new_excerpt = new_excerpt
            item.new_content = new_content
            item.status = "done"
        except Exception as e:
            item.status = "error"
            item.error = str(e)
        item.elapsed = round(time.time() - start, 1)

        done, total = job.progress()
        if done >= total:
            job.status = "done"
        llm_store.update_job(job)


llm_queue = LlmQueue()
