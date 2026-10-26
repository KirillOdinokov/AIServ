(function () {
  const $ = (id) => document.getElementById(id);

  // ── Пауза / возобновление генерации ─────────────────────────────────────
  async function updateQueueButton() {
    const btn = $('btnToggle');
    if (!btn) return;
    const icon = $('btnToggleIcon');
    const label = $('btnToggleLabel');
    try {
      const res = await fetch('/api/queue/status');
      const data = await res.json();
      if (data.paused) {
        if (icon) icon.className = 'ti ti-player-play';
        if (label) label.textContent = 'Продолжить';
        btn.title = 'Возобновить генерацию';
      } else {
        if (icon) icon.className = 'ti ti-player-pause';
        if (label) label.textContent = 'Пауза';
        btn.title = 'Остановить генерацию';
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

  // ── Перезапуск веб-приложения (Soft Reset) ──────────────────────────────
  function waitForRestart() {
    let attempts = 0;
    const wait = setInterval(async () => {
      attempts++;
      try {
        const r = await fetch('/api/tasks');
        if (r.ok) { clearInterval(wait); location.reload(); }
      } catch (e) {}
      if (attempts > 60) clearInterval(wait);
    }, 2000);
  }

  async function doSoftReset() {
    await fetch('/api/server/restart', { method: 'POST' });
    waitForRestart();
  }

  const soft = $('btnSoftReset');
  if (soft) {
    soft.addEventListener('click', async () => {
      if (!confirm('Перезапустить веб-приложение?')) return;
      await doSoftReset();
    });
  }

  // Совместимость со старым id
  const restart = $('btnRestart');
  if (restart) {
    restart.addEventListener('click', async () => {
      if (!confirm('Перезапустить веб-сервер?')) return;
      await doSoftReset();
    });
  }

  // ── Полная перезагрузка сервера (Hard Reset) ────────────────────────────
  const hard = $('btnHardReset');
  if (hard) {
    hard.addEventListener('click', async () => {
      if (!confirm('ПОЛНАЯ перезагрузка сервера. Сайт и генерация будут недоступны ~1–2 минуты. Продолжить?')) return;
      if (!confirm('Подтвердите ещё раз: перезагрузить сервер?')) return;
      hard.disabled = true;
      hard.innerHTML = '<i class="ti ti-loader"></i> Перезагрузка…';
      await fetch('/api/server/hard-reset', { method: 'POST' });
    });
  }

  // ── Остановка сервера ───────────────────────────────────────────────────
  const stop = $('btnStop');
  if (stop) {
    stop.addEventListener('click', async () => {
      if (!confirm('Остановить сервер полностью (ComfyUI + прокси)?')) return;
      await fetch('/api/server/stop', { method: 'POST' });
    });
  }

  // ── Выход ───────────────────────────────────────────────────────────────
  const logout = $('btnLogout');
  if (logout) {
    logout.addEventListener('click', async (e) => {
      e.preventDefault();
      await fetch('/api/logout', { method: 'POST' });
      location.href = '/login';
    });
  }
})();
