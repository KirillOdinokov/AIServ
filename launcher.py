"""Лаунчер: запускает ComfyUI, VRAM-прокси и сервер, завершает всё при выходе.

Windows: job object с JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE убивает детей при
закрытии окна. Linux: process group + os.killpg при завершении.
"""
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

BASE_DIR = Path(__file__).resolve().parent

token_file = BASE_DIR / "hf_token.txt"
if token_file.exists() and "HF_TOKEN" not in os.environ:
    os.environ["HF_TOKEN"] = token_file.read_text(encoding="utf-8").strip()

# ─── Мьютекс: не дать запустить второй экземпляр лаунчера ────────────────────

_lock_file = None


def acquire_single_instance():
    """Захватить эксклюзивный лок. Если уже запущен другой экземпляр — вернуть False."""
    global _lock_file
    lock_path = Path(tempfile.gettempdir()) / "flux_launcher.lock"
    try:
        if IS_WINDOWS:
            import msvcrt
            _lock_file = open(lock_path, "w")
            msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            _lock_file = open(lock_path, "w")
            fcntl.flock(_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file.write(str(os.getpid()))
        _lock_file.flush()
        return True
    except (OSError, IOError):
        # лок уже удерживается другим процессом
        if _lock_file is not None:
            try:
                _lock_file.close()
            except OSError:
                pass
            _lock_file = None
        return False


def release_single_instance():
    global _lock_file
    if _lock_file is not None:
        try:
            if IS_WINDOWS:
                import msvcrt
                _lock_file.seek(0)
                msvcrt.locking(_lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(_lock_file.fileno(), fcntl.LOCK_UN)
            _lock_file.close()
        except (OSError, IOError):
            pass
        _lock_file = None


def _venv_python(venv_dir: Path) -> str:
    """Путь к python внутри venv (Windows vs Linux)."""
    if IS_WINDOWS:
        return str(venv_dir / "Scripts" / "python.exe")
    return str(venv_dir / "bin" / "python")


# ─── Windows: job object ──────────────────────────────────────────────────────

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def setup_kill_on_close():
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            kernel32.CloseHandle(job)
            return
        kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())


def free_ports(ports):
    """Убить процессы, занимающие указанные порты (старые запуски)."""
    if IS_WINDOWS:
        import psutil
        for port in ports:
            for conn in psutil.net_connections(kind="inet"):
                if conn.status == "LISTEN" and conn.laddr.port == port:
                    try:
                        p = psutil.Process(conn.pid)
                        if p.pid == os.getpid():
                            continue
                        print(f"  Освобождаю порт {port} (PID {conn.pid}: {p.name()})")
                        p.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
    else:
        # Linux: lsof -ti :PORT | xargs kill
        for port in ports:
            try:
                out = subprocess.check_output(["lsof", "-ti", f":{port}"], timeout=5)
                pids = [int(x) for x in out.decode().split() if x.strip().isdigit()]
                for pid in pids:
                    if pid == os.getpid():
                        continue
                    print(f"  Освобождаю порт {port} (PID {pid})")
                    os.kill(pid, signal.SIGKILL)
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass


def _start(cmd, cwd):
    """Запустить дочерний процесс с правильной группой (Linux) или job object (Windows)."""
    if IS_WINDOWS:
        return subprocess.Popen(
            cmd, cwd=str(cwd),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    return subprocess.Popen(
        cmd, cwd=str(cwd),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _terminate(proc):
    """Завершить дочерний процесс (вместе с группой на Linux)."""
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def main():
    if not acquire_single_instance():
        print("Сервер уже запущен (мьютекс занят). Закройте предыдущее окно и повторите.")
        return

    if IS_WINDOWS:
        setup_kill_on_close()
    else:
        def _sig(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGINT, _sig)
        signal.signal(signal.SIGTERM, _sig)

    free_ports([8000, 8188, 11435])

    comfy_dir = BASE_DIR / "ComfyUI"
    comfy_python = _venv_python(comfy_dir / ".venv")
    venv_python = _venv_python(BASE_DIR / ".venv")

    print("Starting ComfyUI on http://127.0.0.1:8188 ...")
    comfy = _start([comfy_python, "main.py", "--port", "8188"], comfy_dir)

    print("Starting Ollama VRAM proxy on http://127.0.0.1:11435 ...")
    proxy = _start([venv_python, "ollama_proxy.py"], BASE_DIR)

    RESTART_CODE = 42
    try:
        while True:
            print("Starting server: http://127.0.0.1:8000")
            rc = subprocess.run(
                [venv_python, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
                cwd=str(BASE_DIR),
            ).returncode
            if rc == RESTART_CODE:
                print("Перезапуск веб-сервера по запросу...")
                continue
            break
    finally:
        _terminate(comfy)
        _terminate(proxy)
        release_single_instance()


if __name__ == "__main__":
    main()
