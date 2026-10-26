(function () {
  const $ = (id) => document.getElementById(id);

  async function updateQueueButton() {
    const btn = $('btnToggle');
    if (!btn) return;
    try {
      const res = await fetch('/api/queue/status');
      const data = await res.json();
      if (data.paused) {
        btn.textContent = '▶';
        btn.title = 'Возобновить генерацию';
        btn.classList.remove('btn-outline-secondary');
        btn.classList.add('btn-outline-success');
      } else {
        btn.textContent = '⏸';
        btn.title = 'Остановить генерацию';
        btn.classList.remove('btn-outline-success');
        btn.classList.add('btn-outline-secondary');
      }
    } catch (e) {}
  }

  const toggle = $('btnToggle');
  if (toggle) {
    toggle.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/queue/status');
        const data = await res.json();
        await fetch(data.paused ? '/api/queue/resume' : '/api/queue/pause', { method: 'POST' });
        updateQueueButton();
      } catch (e) {}
    });
    updateQueueButton();
    setInterval(updateQueueButton, 3000);
  }

  const restart = $('btnRestart');
  if (restart) {
    restart.addEventListener('click', async () => {
      if (!confirm('Перезапустить веб-сервер?')) return;
      await fetch('/api/server/restart', { method: 'POST' });
      let attempts = 0;
      const wait = setInterval(async () => {
        attempts++;
        try {
          const r = await fetch('/api/tasks');
          if (r.ok) { clearInterval(wait); location.reload(); }
        } catch (e) {}
        if (attempts > 60) clearInterval(wait);
      }, 2000);
    });
  }

  const stop = $('btnStop');
  if (stop) {
    stop.addEventListener('click', async () => {
      if (!confirm('Остановить сервер полностью (ComfyUI + прокси)?')) return;
      await fetch('/api/server/stop', { method: 'POST' });
    });
  }

  const logout = $('btnLogout');
  if (logout) {
    logout.addEventListener('click', async (e) => {
      e.preventDefault();
      await fetch('/api/logout', { method: 'POST' });
      location.href = '/login';
    });
  }
})();
