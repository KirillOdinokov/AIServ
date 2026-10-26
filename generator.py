"""Загрузка моделей FLUX и генерация изображений."""
import gc
import os
import random
import threading
import time
from pathlib import Path

import torch
from diffusers import Flux2KleinPipeline

import config
import comfy_client

def _load_hf_token():
    token_file = config.BASE_DIR / "hf_token.txt"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    return os.getenv("HF_TOKEN", "").strip()


HF_TOKEN = _load_hf_token()


def _load_pipeline(repo_id: str, pipeline: str = "flux2_klein"):
    """Загрузить diffusers-пайплайн по типу."""
    return Flux2KleinPipeline.from_pretrained(
        repo_id,
        torch_dtype=torch.bfloat16,
        cache_dir=str(config.MODEL_CACHE_DIR),
        token=HF_TOKEN or None,
    )


class FluxGenerator:
    def __init__(self):
        self._lock = threading.Lock()
        self._pipe = None
        self._loaded_model = None
        self._comfy_model = None      # модель, загруженная в ComfyUI (или None)
        self._last_activity = time.time()
        threading.Thread(target=self._idle_watchdog, daemon=True).start()

    def _idle_watchdog(self):
        """Выгружает модель из VRAM после долгого простоя.

        Нужно, чтобы после завершения всех задач модель не занимала видеопамять
        вечно (это мешало локальной LLM использовать GPU). Кэширование при
        активной генерации сохраняется.
        """
        while True:
            time.sleep(30)
            if time.time() - self._last_activity <= config.IDLE_UNLOAD_SECONDS:
                continue
            with self._lock:
                if time.time() - self._last_activity <= config.IDLE_UNLOAD_SECONDS:
                    continue
                if self._pipe is not None or self._comfy_model is not None:
                    print("[generator] Простой — выгружаю модель из VRAM")
                    self.unload_all()

    def current_model(self):
        """Какая модель реально загружена в VRAM (или None)."""
        if self._pipe is not None:
            return self._loaded_model
        return self._comfy_model

    def _load(self, model_id: str):
        """Загрузить пайплайн для модели (с кэшированием)."""
        if self._loaded_model == model_id and self._pipe is not None:
            return self._pipe

        info = config.MODELS[model_id]
        repo_id = info["repo_id"]

        # Освобождаем старую модель
        if self._pipe is not None:
            del self._pipe
            self._pipe = None
            gc.collect()
            torch.cuda.empty_cache()

        print(f"[generator] Загрузка модели {repo_id} ...")
        pipe = _load_pipeline(repo_id, info.get("pipeline", "flux2_klein"))
        pipe.to("cuda")  # модель целиком в VRAM (24 ГБ)

        self._pipe = pipe
        self._loaded_model = model_id
        print(f"[generator] Модель {repo_id} загружена.")
        return pipe

    def generate(self, prompt, model_id, width, height, steps, guidance, seed, out_path: Path,
                 reference_image: Path = None, reference_mode: str = "none", strength: float = 0.7,
                 length=None, fps=None):
        """Сгенерировать одно изображение и сохранить в out_path.

        reference_image: путь к картинке-референсу (или None).
        reference_mode: "none" | "img2img" | "multimodal".
        strength: сила переработки для img2img (0..1, меньше = ближе к референсу).
        length/fps: параметры видео (кадры / частота кадров), для видео-моделей.
        """
        with self._lock:
            info = config.MODELS[model_id]
            steps = steps if steps is not None else info["default_steps"]
            guidance = guidance if guidance is not None else info["guidance_scale"]

            if seed is None or seed < 0:
                seed = random.randint(0, 2**32 - 1)

            out_path.parent.mkdir(parents=True, exist_ok=True)
            self._last_activity = time.time()

            if info.get("engine") == "comfyui":
                self._generate_comfy(prompt, model_id, width, height, steps, seed, out_path,
                                     reference_image, reference_mode, strength, length, fps)
            else:
                self._generate_diffusers(prompt, model_id, width, height, steps, guidance, seed,
                                         out_path, reference_image, reference_mode, strength)

            # обновляем метку активности в КОНЦЕ, чтобы таймер простоя считался
            # от завершения последней генерации (иначе долгая генерация > IDLE_UNLOAD_SECONDS
            # приводит к мгновенной выгрузке модели сразу после завершения).
            self._last_activity = time.time()

            return str(out_path)

    def _unload_diffusers(self):
        """Выгрузить diffusers-пайплайн из VRAM (перед ComfyUI-генерацией)."""
        if self._pipe is not None:
            del self._pipe
            self._pipe = None
            self._loaded_model = None
            gc.collect()
            torch.cuda.empty_cache()

    def unload_all(self):
        """Полностью выгрузить всё из VRAM (diffusers + ComfyUI)."""
        self._unload_diffusers()
        self._comfy_model = None
        comfy_client.free_memory(unload_models=True, free_memory=True)

    def _generate_diffusers(self, prompt, model_id, width, height, steps, guidance, seed, out_path,
                            reference_image=None, reference_mode="none", strength=0.7):
        # Освобождаем VRAM от ComfyUI-моделей, чтобы diffusers-модель влезла.
        # ComfyUI кэширует модель в VRAM — без выгрузки будет OOM.
        comfy_client.free_memory(unload_models=True, free_memory=True)
        self._comfy_model = None

        pipe = self._load(model_id)
        generator = torch.Generator("cpu").manual_seed(seed)

        from PIL import Image

        kwargs = {}
        if reference_image is not None and reference_mode in ("img2img", "multimodal"):
            img = Image.open(reference_image).convert("RGB")
            kwargs["image"] = img

        image = pipe(
            prompt=prompt,
            height=height,
            width=width,
            guidance_scale=guidance,
            num_inference_steps=steps,
            generator=generator,
            **kwargs,
        ).images[0]
        image.save(out_path)

    def _generate_comfy(self, prompt, model_id, width, height, steps, seed, out_path,
                        reference_image=None, reference_mode="none", strength=0.7,
                        length=None, fps=None):
        info = config.MODELS[model_id]
        # Освобождаем VRAM от diffusers-модели, чтобы ComfyUI-модель влезла
        self._unload_diffusers()
        # Отмечаем модель сразу, чтобы статус «В VRAM» показывал её во время генерации.
        self._comfy_model = model_id
        # Собираем словарь файлов: имя -> локальное имя файла
        files = {}
        for repo_id, filename, folder in info["files"]:
            local_name = filename.split("/")[-1]
            if folder == "diffusion_models":
                if "unconditional" in local_name:
                    files["model_uncond"] = local_name
                else:
                    files["model"] = local_name
            elif folder == "text_encoders":
                files["clip"] = local_name
            elif folder == "vae":
                files["vae"] = local_name
            elif folder == "loras":
                files["lora"] = local_name

        result = comfy_client.generate(
            workflow_type=info["workflow"],
            prompt=prompt,
            width=width,
            height=height,
            steps=steps,
            seed=seed,
            files=files,
            timeout=1800 if info.get("video") else 900,
            reference_image=str(reference_image) if reference_image else None,
            reference_mode=reference_mode,
            strength=strength,
            length=length,
            fps=fps,
        )
        data = comfy_client.get_image_bytes(
            result["filename"], result["subfolder"], result["type"]
        )
        out_path.write_bytes(data)


generator = FluxGenerator()
