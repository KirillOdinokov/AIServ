const $ = (id) => document.getElementById(id);

const STATUS_LABELS = {
  draft: 'Черновик',
  queued: 'В очереди',
  running: 'Генерация',
  paused: 'Пауза',
  done: 'Готово',
  error: 'Ошибка',
};

function fmtDate(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' }) +
    ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function loadJobs() {
  const res = await fetch('/api/llm/jobs');
  const jobs = await res.json();
  const body = $('jobsBody');
  body.innerHTML = '';

  for (const j of jobs) {
    const tr = document.createElement('tr');
    const statusClass = j.status === 'done' ? 'done' : j.status === 'error' ? 'error' : j.status === 'running' ? 'running' : j.status === 'queued' ? 'queued' : 'draft';
    const progress = j.total > 0 ? `${j.done}/${j.total}` : '0/0';

    tr.innerHTML = `
      <td><a href="/llm/result/${j.id}">#${j.id}</a></td>
      <td><a href="/llm/result/${j.id}">${esc(j.source_name)}</a></td>
      <td>${progress}</td>
      <td><span class="badge badge--${statusClass}">${STATUS_LABELS[j.status] || j.status}</span></td>
      <td>${fmtDate(j.created_at)}</td>
      <td>
        <a class="btn btn-outline btn-sm" href="/llm/result/${j.id}">Открыть</a>
        <button class="btn btn-outline-danger btn-sm" data-del="${j.id}">🗑</button>
      </td>
    `;
    body.appendChild(tr);
  }

  body.querySelectorAll('[data-del]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm('Удалить задачу?')) return;
      await fetch('/api/llm/jobs/' + btn.dataset.del, { method: 'DELETE' });
      loadJobs();
    });
  });
}

$('uploadForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const file = $('csvFile').files[0];
  if (!file) { alert('Выберите CSV файл'); return; }
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/api/llm/upload', { method: 'POST', body: form });
  if (!res.ok) { alert('Ошибка: ' + (await res.text())); return; }
  const data = await res.json();
  await fetch('/api/llm/jobs/' + data.id + '/run', { method: 'POST' });
  window.location.href = '/llm/result/' + data.id;
});

// ─── Кнопка паузы/возобновления ─────────────────────────────────────────────

async function updateQueueButton() {
  const btn = $('btnToggle');
  if (!btn) return;
  const res = await fetch('/api/llm/status');
  const data = await res.json();
  if (data.paused) {
    btn.className = 'pause-btn pause-btn--on';
    btn.textContent = '▶';
    btn.title = 'Возобновить генерацию';
  } else {
    btn.className = 'pause-btn pause-btn--off';
    btn.textContent = '⏸';
    btn.title = 'Остановить генерацию';
  }
}

$('btnToggle').addEventListener('click', async () => {
  const res = await fetch('/api/llm/status');
  const data = await res.json();
  if (data.paused) {
    await fetch('/api/llm/resume', { method: 'POST' });
  } else {
    await fetch('/api/llm/pause', { method: 'POST' });
  }
  updateQueueButton();
  updatePeakStatus();
});

async function updatePeakStatus() {
  const res = await fetch('/api/llm/status');
  const data = await res.json();
  const el = $('peakStatus');
  if (!el) return;

  let html;
  if (data.reason === 'paused_manual') {
    html = '<span class="dot dot--off"></span> Остановлено вручную';
  } else if (data.reason === 'paused_peak') {
    const wins = data.peak_windows_local.map(w => `${w[0]}:00–${w[1]}:00`).join(' и ');
    html = `<span class="dot dot--off"></span> Peak Time (${wins} МСК) · сейчас ${data.current_time_local} МСК`;
  } else if (data.reason === 'running') {
    html = '<span class="dot dot--on"></span> Идёт генерация';
  } else {
    html = '<span class="dot dot--on"></span> Ожидание задач';
  }
  el.innerHTML = html;
}

loadJobs();
updateQueueButton();
updatePeakStatus();
setInterval(loadJobs, 5000);
setInterval(updateQueueButton, 5000);
setInterval(updatePeakStatus, 5000);
