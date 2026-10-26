"""Pydantic-схемы для API."""
from typing import Optional
from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    prompt: str = Field(..., description="Текст промпта")
    model: str = Field("flux2-klein-9b", description="ID модели")
    width: int = Field(1024, ge=256, le=2048)
    height: int = Field(1024, ge=256, le=2048)
    steps: Optional[int] = Field(None, description="Шагов; None = дефолт модели")
    guidance_scale: Optional[float] = Field(None, description="CFG; None = дефолт модели")
    seed: int = Field(-1, description="-1 = случайный")
    filename: Optional[str] = Field(None, description="Имя файла без расширения")


class TaskStatus(BaseModel):
    id: str
    status: str  # queued | running | done | error
    progress: float = 0.0
    total: int = 0
    done: int = 0
    message: str = ""
    output_files: list[str] = []
