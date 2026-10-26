"""Клиент для ComfyUI API (генерация через workflow)."""
import json
import os
import time
import uuid
import urllib.request
import urllib.parse

COMFY_URL = "http://127.0.0.1:8188"


def _post(path, data):
    url = COMFY_URL + path
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(path):
    url = COMFY_URL + path
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def is_ready():
    try:
        _get("/system_stats")
        return True
    except Exception:
        return False


def wait_ready(timeout=300):
    """Ждать, пока ComfyUI поднимется (например, после перезапуска)."""
    start = time.time()
    while time.time() - start < timeout:
        if is_ready():
            return True
        time.sleep(2)
    raise TimeoutError("ComfyUI не запустился (таймаут ожидания)")


def _build_flux2(prompt, width, height, steps, seed, files):
    """FLUX.2 Dev (GGUF) workflow."""
    return {
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["53", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["52", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "flux", "images": ["8", 0]}},
        "13": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["25", 0], "guider": ["22", 0], "sampler": ["16", 0],
            "sigmas": ["48", 0], "latent_image": ["47", 0]}},
        "16": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "22": {"class_type": "BasicGuider", "inputs": {"model": ["54", 0], "conditioning": ["26", 0]}},
        "25": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "26": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["6", 0], "guidance": 1.0}},
        "47": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "48": {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": width, "height": height}},
        "52": {"class_type": "VAELoader", "inputs": {"vae_name": files["vae"]}},
        "53": {"class_type": "CLIPLoaderGGUF", "inputs": {"clip_name": files["clip"], "type": "flux2", "device": "default"}},
        "54": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": files["model"]}},
    }


def _build_krea2(prompt, width, height, steps, seed, files, reference_image=None, reference_mode="none", strength=0.7):
    """Krea 2 Turbo (FP8) workflow.

    reference_image: путь к картинке для img2img (загружается в ComfyUI).
    reference_mode: "none" | "img2img" | "multimodal".
    strength: denoise для img2img (0..1, меньше = ближе к исходнику).
    """
    # Негативный промпт: подавляем искажённый текст и водяные знаки.
    negative_text = (
        "blurry, low quality, distorted text, gibberish text, unreadable text, "
        "misspelled text, jumbled letters, watermark, logo, text overlay, "
        "duplicated text, repeated text, messy typography"
    )

    latent = ["5", 0]
    if reference_image and reference_mode == "img2img":
        # LoadImage -> VAEEncode -> latent для img2img
        img_name = upload_image(reference_image)
        latent = ["7", 0]
        return {
            "3": {"class_type": "KSampler", "inputs": {
                "model": ["10", 0], "seed": seed, "steps": steps, "cfg": 1.0,
                "sampler_name": "euler", "scheduler": "simple", "denoise": strength,
                "positive": ["6", 0], "negative": ["13", 0], "latent_image": latent}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["11", 0]}},
            "7": {"class_type": "VAEEncode", "inputs": {"pixels": ["20", 0], "vae": ["12", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["12", 0]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "krea2", "images": ["8", 0]}},
            "10": {"class_type": "UNETLoader", "inputs": {"unet_name": files["model"], "weight_dtype": "default"}},
            "11": {"class_type": "CLIPLoader", "inputs": {"clip_name": files["clip"], "type": "krea2", "device": "default"}},
            "12": {"class_type": "VAELoader", "inputs": {"vae_name": files["vae"]}},
            "13": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_text, "clip": ["11", 0]}},
            "20": {"class_type": "LoadImage", "inputs": {"image": img_name}},
        }
    return {
        "3": {"class_type": "KSampler", "inputs": {
            "model": ["10", 0], "seed": seed, "steps": steps, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "positive": ["6", 0], "negative": ["13", 0], "latent_image": latent}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["11", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["12", 0]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "krea2", "images": ["8", 0]}},
        "10": {"class_type": "UNETLoader", "inputs": {"unet_name": files["model"], "weight_dtype": "default"}},
        "11": {"class_type": "CLIPLoader", "inputs": {"clip_name": files["clip"], "type": "krea2", "device": "default"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": files["vae"]}},
        "13": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_text, "clip": ["11", 0]}},
    }


def _build_ideogram4(prompt, width, height, steps, seed, files):
    """Ideogram 4 (FP8) workflow — двойная модель (conditional + unconditional)."""
    negative_text = (
        "blurry, low quality, distorted text, gibberish text, unreadable text, "
        "misspelled text, jumbled letters, watermark, logo, text overlay, "
        "duplicated text, repeated text, messy typography"
    )
    return {
        "9": {"class_type": "VAELoader", "inputs": {"vae_name": files["vae"]}},
        "10": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_text, "clip": ["14", 0]}},
        "11": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "12": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["18", 0], "guider": ["155", 0], "sampler": ["16", 0],
            "sigmas": ["17", 0], "latent_image": ["11", 0]}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["9", 0]}},
        "14": {"class_type": "CLIPLoader", "inputs": {"clip_name": files["clip"], "type": "ideogram4", "device": "default"}},
        "16": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "17": {"class_type": "Ideogram4Scheduler", "inputs": {"steps": steps, "width": width, "height": height, "mu": 0.0, "std": 1.75}},
        "18": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "23": {"class_type": "UNETLoader", "inputs": {"unet_name": files["model"], "weight_dtype": "default"}},
        "24": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["14", 0]}},
        "154": {"class_type": "UNETLoader", "inputs": {"unet_name": files["model_uncond"], "weight_dtype": "default"}},
        "155": {"class_type": "DualModelGuider", "inputs": {
            "model": ["23", 0], "positive": ["24", 0], "cfg": 7.0,
            "model_negative": ["154", 0], "negative": ["10", 0]}},
        "158": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ideogram4", "images": ["13", 0]}},
    }


def _build_wan21(prompt, width, height, steps, seed, files, length=None, fps=None, negative_prompt=None):
    """Wan 2.1 T2V (GGUF) workflow — текст в видео."""
    negative_text = negative_prompt or (
        "blurry, low quality, distorted, artifacts, watermark, text, logo, "
        "jpeg artifacts, static, noisy, grainy"
    )
    if length is None:
        length = 81
    else:
        length = max(5, ((length - 1) // 4) * 4 + 1)
        length = min(length, 81)  # Wan 2.1 обучен ~81 кадру, длиннее — шум
    if fps is None:
        fps = 16
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": files["model"]}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": files["clip"], "type": "wan", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": files["vae"]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_text, "clip": ["2", 0]}},
        "6": {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": width, "height": height, "length": length, "batch_size": 1}},
        "7": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}},
        "8": {"class_type": "KSampler", "inputs": {
            "model": ["7", 0], "seed": seed, "steps": steps, "cfg": 1.0,
            "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0,
            "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0]}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "CreateVideo", "inputs": {"images": ["9", 0], "fps": float(fps)}},
        "11": {"class_type": "SaveVideo", "inputs": {"video": ["10", 0], "filename_prefix": "wan21", "format": "mp4"}},
    }


def _build_ltxv(prompt, width, height, steps, seed, files, length=None, fps=None, negative_prompt=None):
    """LTX-Video T2V workflow — текст в видео (distilled, cfg 1.0)."""
    negative_text = negative_prompt or (
        "blurry, low quality, distorted, artifacts, watermark, text, logo, "
        "jpeg artifacts, static, noisy, grainy"
    )
    if length is None:
        length = 121
    else:
        length = max(9, ((length - 1) // 8) * 8 + 1)
        length = min(length, 121)  # LTX обучен ~121 кадру, длиннее — шум
    if fps is None:
        fps = 25
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": files["model"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": files["clip"], "type": "ltxv", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": files["vae"]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_text, "clip": ["2", 0]}},
        "6": {"class_type": "LTXVConditioning", "inputs": {"positive": ["4", 0], "negative": ["5", 0], "frame_rate": float(fps)}},
        "7": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": width, "height": height, "length": length, "batch_size": 1}},
        "8": {"class_type": "LTXVScheduler", "inputs": {"steps": steps, "max_shift": 2.05, "base_shift": 0.95, "stretch": True, "terminal": 0.1}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "10": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "11": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["6", 0], "negative": ["6", 1], "cfg": 1.0}},
        "12": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["9", 0], "guider": ["11", 0], "sampler": ["10", 0], "sigmas": ["8", 0], "latent_image": ["7", 0]}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["3", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "fps": float(fps)}},
        "15": {"class_type": "SaveVideo", "inputs": {"video": ["14", 0], "filename_prefix": "ltxv", "format": "mp4"}},
    }


def build_workflow(workflow_type, prompt, width, height, steps, seed, files,
                   reference_image=None, reference_mode="none", strength=0.7,
                   length=None, fps=None, negative_prompt=None):
    if workflow_type == "krea2":
        return _build_krea2(prompt, width, height, steps, seed, files,
                            reference_image, reference_mode, strength)
    if workflow_type == "ideogram4":
        return _build_ideogram4(prompt, width, height, steps, seed, files)
    if workflow_type == "wan2.1_t2v":
        return _build_wan21(prompt, width, height, steps, seed, files, length, fps, negative_prompt)
    if workflow_type == "ltxv":
        return _build_ltxv(prompt, width, height, steps, seed, files, length, fps, negative_prompt)
    return _build_flux2(prompt, width, height, steps, seed, files)


def upload_image(path):
    """Загрузить изображение в ComfyUI (input/), вернуть имя файла."""
    path = str(path)
    boundary = uuid.uuid4().hex
    fname = os.path.basename(path)
    with open(path, "rb") as f:
        data = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{fname}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        COMFY_URL + "/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        r = json.loads(resp.read().decode("utf-8"))
    return r.get("name", fname)


def submit_workflow(workflow_type, prompt, width, height, steps, seed, files,
                    reference_image=None, reference_mode="none", strength=0.7,
                    length=None, fps=None, negative_prompt=None):
    """Отправить workflow в ComfyUI и вернуть prompt_id (без ожидания)."""
    wait_ready()
    wf = build_workflow(workflow_type, prompt, width, height, steps, seed, files,
                        reference_image, reference_mode, strength, length, fps, negative_prompt)
    client_id = uuid.uuid4().hex
    result = _post("/prompt", {"prompt": wf, "client_id": client_id})
    return result["prompt_id"]


def wait_for_result(prompt_id, timeout=900):
    """Ждать завершения prompt_id и вернуть {filename, subfolder, type}."""
    start = time.time()
    while True:
        if time.time() - start > timeout:
            raise TimeoutError("ComfyUI: таймаут генерации")

        history = _get(f"/history/{prompt_id}")
        if prompt_id in history:
            h = history[prompt_id]
            outputs = h.get("outputs", {})
            for node_id, node_out in outputs.items():
                # изображения и видео сохраняются под разными ключами
                for key in ("images", "videos", "video", "gifs"):
                    items = node_out.get(key, [])
                    if items:
                        img = items[0]
                        return {
                            "filename": img["filename"],
                            "subfolder": img.get("subfolder", ""),
                            "type": img.get("type", "output"),
                        }
            if h.get("status", {}).get("status_str") == "error":
                raise RuntimeError("ComfyUI: ошибка генерации")
        time.sleep(1)


def generate(workflow_type, prompt, width, height, steps, seed, files, timeout=900,
             reference_image=None, reference_mode="none", strength=0.7,
             length=None, fps=None):
    """Запустить генерацию и вернуть путь к файлу."""
    prompt_id = submit_workflow(workflow_type, prompt, width, height, steps, seed, files,
                                reference_image, reference_mode, strength, length, fps)
    return wait_for_result(prompt_id, timeout)


def free_memory(unload_models=True, free_memory=True):
    """Выгрузить модели из VRAM (ComfyUI /free endpoint)."""
    try:
        _post("/free", {"unload_models": unload_models, "free_memory": free_memory})
        return True
    except Exception:
        return False


def get_image_bytes(filename, subfolder="", img_type="output"):
    """Скачать изображение из ComfyUI."""
    params = urllib.parse.urlencode({
        "filename": filename,
        "subfolder": subfolder,
        "type": img_type,
    })
    url = f"{COMFY_URL}/view?{params}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()
