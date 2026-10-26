"""Фоновая загрузка моделей с Hugging Face с прогрессом."""
import shutil
import threading
import time
from pathlib import Path

import tqdm
from huggingface_hub import snapshot_download, hf_hub_download

import config
from generator import HF_TOKEN


class ModelDownloader:
    def __init__(self):
        self._lock = threading.Lock()
        self._status = {}  # model_id -> {status, message, progress}
        self._cache = {}   # model_id -> (timestamp, is_downloaded)

    def _set(self, model_id, status, message="", progress=0.0):
        with self._lock:
            self._status[model_id] = {
                "status": status,
                "message": message,
                "progress": progress,
            }

    def get_status(self, model_id):
        with self._lock:
            return self._status.get(model_id, {"status": "not_downloaded", "message": "", "progress": 0.0})

    def is_downloaded(self, model_id):
        info = config.MODELS[model_id]

        # ComfyUI-модели: проверяем наличие файлов в папках ComfyUI
        if info.get("engine") == "comfyui":
            base = config.BASE_DIR / "ComfyUI" / "models"
            for repo_id, filename, folder in info["files"]:
                local_name = filename.split("/")[-1]
                f = base / folder / local_name
                if not (f.exists() and f.stat().st_size > 0):
                    return False
            return True

        # diffusers-модели: проверяем кэш
        repo_id = info["repo_id"]
        cache_dir = config.MODEL_CACHE_DIR / ("models--" + repo_id.replace("/", "--"))
        if not cache_dir.exists():
            return False

        snapshots = cache_dir / "snapshots"
        if not snapshots.exists():
            return False
        has_weights = False
        for f in snapshots.rglob("*"):
            if f.is_file() and f.suffix in (".safetensors", ".bin", ".gguf"):
                try:
                    if f.stat().st_size > 0:
                        has_weights = True
                        break
                except OSError:
                    continue

        if not has_weights:
            return False

        for inc in cache_dir.rglob("*.incomplete"):
            try:
                inc.unlink()
            except OSError:
                pass

        return True

    def download(self, model_id):
        """Запустить загрузку модели в фоне."""
        if model_id not in config.MODELS:
            return
        if self.is_downloaded(model_id):
            self._set(model_id, "downloaded", "Модель уже загружена", 100.0)
            return

        self._set(model_id, "downloading", "Подготовка...", 0.0)
        t = threading.Thread(target=self._do_download, args=(model_id,), daemon=True)
        t.start()

    def _do_download(self, model_id):
        info = config.MODELS[model_id]
        token = HF_TOKEN or None

        if info.get("engine") == "comfyui":
            self._download_comfy(model_id, info, token)
        else:
            self._download_diffusers(model_id, info, token)

    def _download_comfy(self, model_id, info, token):
        base = config.BASE_DIR / "ComfyUI" / "models"

        try:
            total = len(info["files"])
            for i, (repo_id, filename, folder) in enumerate(info["files"]):
                local_name = filename.split("/")[-1]
                target = base / folder / local_name
                # Файл уже на месте (мог быть скачан другой моделью) — пропускаем
                if target.exists() and target.stat().st_size > 0:
                    print(f"[downloader] {local_name} уже есть, пропускаю")
                    continue
                self._set(model_id, "downloading", f"Файл {i+1}/{total}: {local_name}", (i / total) * 100)
                print(f"[downloader] {repo_id}/{filename} -> {target}")

                state = {"done": 0, "total": 0}

                def report(done_bytes):
                    pct = int(done_bytes / state["total"] * 100) if state["total"] else 0
                    self._set(
                        model_id, "downloading",
                        f"Файл {i+1}/{total}: {local_name} — {done_bytes / 1e9:.2f} / {state['total'] / 1e9:.2f} ГБ",
                        pct,
                    )

                class _Tqdm(tqdm.tqdm):
                    def __init__(self, *args, **kwargs):
                        self._file_total = kwargs.get("total", 0)
                        super().__init__(*args, **kwargs)

                    def update(self, n=1):
                        super().update(n)
                        report(state["done"] + self.n)

                    def close(self):
                        state["done"] += self._file_total
                        super().close()

                # Скачиваем в корень models, потом перемещаем в правильную папку
                downloaded = hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    local_dir=str(base),
                    token=token,
                    tqdm_class=_Tqdm,
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                if Path(downloaded).resolve() != target.resolve():
                    shutil.move(downloaded, str(target))
            self._set(model_id, "downloaded", "Загружено", 100.0)
            self._cache[model_id] = (time.time(), True)
            print(f"[downloader] {model_id} загружена.")
        except Exception as e:
            self._set(model_id, "error", str(e), 0.0)
            print(f"[downloader] Ошибка загрузки {model_id}: {e}")

    def _download_diffusers(self, model_id, info, token):
        repo_id = info["repo_id"]
        cache_dir = str(config.MODEL_CACHE_DIR)

        try:
            files = snapshot_download(
                repo_id, cache_dir=cache_dir, token=token, dry_run=True
            )
            total_bytes = sum(f.file_size for f in files)
            state = {"completed": 0, "total": total_bytes}

            def report(done_bytes):
                pct = int(done_bytes / state["total"] * 100) if state["total"] else 0
                self._set(
                    model_id, "downloading",
                    f"{done_bytes / 1e9:.2f} / {state['total'] / 1e9:.2f} ГБ",
                    pct,
                )

            class _Tqdm(tqdm.tqdm):
                def __init__(self, *args, **kwargs):
                    self._file_total = kwargs.get("total", 0)
                    super().__init__(*args, **kwargs)

                def update(self, n=1):
                    super().update(n)
                    report(state["completed"] + self.n)

                def close(self):
                    state["completed"] += self._file_total
                    super().close()

            print(f"[downloader] Загрузка {repo_id} ({total_bytes / 1e9:.2f} ГБ) ...")

            max_attempts = 10
            for attempt in range(1, max_attempts + 1):
                try:
                    snapshot_download(
                        repo_id,
                        cache_dir=cache_dir,
                        token=token,
                        tqdm_class=_Tqdm,
                    )
                    break
                except Exception as e:
                    if attempt >= max_attempts:
                        raise
                    wait = min(30, 5 * attempt)
                    self._set(
                        model_id, "downloading",
                        f"Обрыв соединения, повтор {attempt}/{max_attempts} через {wait}с...",
                        state["completed"] / state["total"] * 100 if state["total"] else 0,
                    )
                    print(f"[downloader] Обрыв: {e}. Повтор {attempt}/{max_attempts} через {wait}с")
                    time.sleep(wait)

            self._set(model_id, "downloaded", "Загружено", 100.0)
            self._cache[model_id] = (time.time(), True)
            print(f"[downloader] {repo_id} загружена.")
        except Exception as e:
            self._set(model_id, "error", str(e), 0.0)
            print(f"[downloader] Ошибка загрузки {repo_id}: {e}")


downloader = ModelDownloader()
