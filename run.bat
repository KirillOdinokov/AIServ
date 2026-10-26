@echo off
cd /d "%~dp0"
title FLUX Image Generator

echo ============================================
echo  FLUX Image Generator
echo  Сервер:     http://127.0.0.1:8000
echo  ComfyUI:    http://127.0.0.1:8188
echo  VRAM proxy: http://127.0.0.1:11435
echo ============================================
echo.
echo  НЕ закрывайте это окно — сервер работает, пока оно открыто.
echo  Для остановки нажмите Ctrl+C или просто закройте окно.
echo  (Либо используйте кнопки в веб-интерфейсе)
echo ============================================
echo.

if not exist ".venv" (
    echo Создаю виртуальное окружение...
    python -m venv .venv
)

python launcher.py

echo.
echo Сервер остановлен.
pause
