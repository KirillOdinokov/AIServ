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

(function () {
  const body = document.body;
  const toggle = document.getElementById('mobileNavToggle');
  const close = document.getElementById('mobileNavClose');
  const backdrop = document.getElementById('mobileNavBackdrop');
  const sidebar = document.querySelector('.navbar-vertical');
  const mq = window.matchMedia('(max-width: 991.98px)');

  if (!toggle || !sidebar) return;

  function syncAria(open) {
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (mq.matches) {
      sidebar.setAttribute('aria-hidden', open ? 'false' : 'true');
    } else {
      sidebar.removeAttribute('aria-hidden');
    }
  }

  function setOpen(open) {
    body.classList.toggle('mobile-nav-open', open);
    syncAria(open);
  }

  function closeNav() {
    setOpen(false);
  }

  toggle.addEventListener('click', () => {
    setOpen(!body.classList.contains('mobile-nav-open'));
  });

  if (close) close.addEventListener('click', closeNav);
  if (backdrop) backdrop.addEventListener('click', closeNav);

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeNav();
  });

  sidebar.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', () => {
      const href = link.getAttribute('href') || '';
      if (mq.matches && href && !href.startsWith('#')) closeNav();
    });
  });

  if (mq.addEventListener) {
    mq.addEventListener('change', (event) => {
      if (!event.matches) closeNav();
    });
  } else if (mq.addListener) {
    mq.addListener((event) => {
      if (!event.matches) closeNav();
    });
  }

  syncAria(false);
})();
