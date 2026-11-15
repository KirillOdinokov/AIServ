"""Фоновая очередь визуализации интерьера (LLM-промпты + Klein multimodal)."""
import random
import threading
import time

import config
import llm_client
from generator import generator
from interior_store import interior_store, InteriorVariant

FALLBACK_STYLES = [
    "минималистичный современный стиль",
    "тёплый скандинавский стиль",
    "лофт с металлом и деревом",
    "премиальный классический стиль",
    "хай-тек со стеклом и подсветкой",
    "эко-стиль с натуральными материалами",
    "ар-деко с выразительными акцентами",
    "функциональный офисный стиль",
]


def _fallback_prompts(user_prompt, count):
    base = (user_prompt or "").strip().rstrip(".")
    out = []
    for i in range(count):
        style = FALLBACK_STYLES[i % len(FALLBACK_STYLES)]
        out.append(
            f"{base}. {style.capitalize()}. Сохрани планировку, ракурс, освещение "
            "и пропорции помещения с референсного фото; измени только запрошенные элементы."
        )
    return out


class InteriorQueue:
    def __init__(self):
        self._queue = []
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

    def is_paused(self):
        with self._lock:
            return self._paused

    def submit_job(self, job):
        with self._lock:
            if job.id not in self._queue:
                self._queue.append(job.id)
        if job.status != "running":
            job.status = "queued"
            interior_store.update_job(job)

    def resume_all(self):
        for job in interior_store.list_jobs():
            if job.status in ("done", "error"):
                continue
            if not job.prompts_ready:
                self.submit_job(job)
                continue
            if any(v.status == "pending" for v in job.variants):
                self.submit_job(job)

    def pause_job(self, job_id):
        job = interior_store.get_job(job_id)
        if job is None:
            return False
        job.paused = True
        if job.status in ("queued", "running"):
            job.status = "queued"
        interior_store.update_job(job)
        return True

    def resume_job(self, job_id):
        job = interior_store.get_job(job_id)
        if job is None:
            return False
        job.paused = False
        if job.status in ("draft", "queued", "running"):
            self.submit_job(job)
        interior_store.update_job(job)
        return True

    def _pick(self):
        with self._lock:
            for idx, job_id in enumerate(self._queue):
                job = interior_store.get_job(job_id)
                if job is None:
                    self._queue.pop(idx)
                    return None, None
                if job.paused:
                    continue
                if job.status in ("done", "error"):
                    self._queue.pop(idx)
                    continue
                return self._queue.pop(idx), job
        return None, None

    def _run(self):
        while True:
            if self.is_paused():
                time.sleep(0.5)
                continue
            job_id, job = self._pick()
            if job is None:
                time.sleep(0.5)
                continue
            try:
                self._process(job)
            except Exception as e:
                print(f"[interior_queue] Ошибка обработки {job_id}: {e}")

    def _process(self, job):
        if job.status != "running":
            job.status = "running"
            interior_store.update_job(job)

        if not job.prompts_ready:
            self._make_prompts(job)

        for variant in job.variants:
            if variant.status != "pending":
                continue
            if job.paused:
                break
            self._generate(job, variant)

        done, total = job.progress()
        if done >= total:
            job.status = "done"
        elif job.paused:
            job.status = "queued"
        interior_store.update_job(job)

    def _make_prompts(self, job):
        if not job.llm_model:
            job.llm_model = llm_client.default_model(job.llm_provider)
        try:
            prompts = llm_client.generate_interior_prompts(
                job.user_prompt, job.count,
                provider=job.llm_provider,
                model_name=job.llm_model or None,
            )
        except Exception as e:
            job.llm_error = str(e)
            print(f"[interior_queue] LLM-промпты не получены: {e}")
            prompts = _fallback_prompts(job.user_prompt, job.count)

        if len(prompts) < job.count:
            prompts += _fallback_prompts(job.user_prompt, job.count - len(prompts))
        prompts = prompts[:job.count]

        job.variants = [InteriorVariant(p) for p in prompts]
        job.prompts_ready = True
        interior_store.update_job(job)

    def _generate(self, job, variant):
        variant.status = "running"
        interior_store.update_job(job)

        ref_path = config.OUTPUT_DIR / job.image_path
        folder = config.OUTPUT_DIR / job.folder_name()
        out_path = folder / f"{variant.id}.png"
        seed = variant.seed
        if seed is None or seed < 0:
            seed = random.randint(0, 2**32 - 1)
            variant.seed = seed

        info = config.MODELS[job.model_id]
        start = time.time()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if not ref_path.exists():
                raise RuntimeError("Не найден загруженный референс интерьера")
            generator.generate(
                variant.prompt, job.model_id, job.width, job.height,
                info["default_steps"], info["guidance_scale"], seed, out_path,
                reference_image=ref_path,
                reference_mode="multimodal",
                strength=0.5,
            )
            variant.filename = str(out_path.relative_to(config.OUTPUT_DIR))
            variant.status = "done"
        except Exception as e:
            variant.status = "error"
            variant.error = str(e)
        variant.elapsed = round(time.time() - start, 1)
        interior_store.update_job(job)


interior_queue = InteriorQueue()
