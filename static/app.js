const $ = (id) => document.getElementById(id);

const STATUS_LABELS = {
  draft: 'Черновик',
  queued: 'В очереди',
  running: 'Генерация',
  done: 'Сгенерировано',
  error: 'Ошибка',
};
function fmtDate(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' }) +
    ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

function fmtDuration(sec) {
  if (sec == null) return '—';
  sec = Math.round(sec);
  if (sec < 60) return sec + 'с';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) return m + 'м ' + s + 'с';
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return h + 'ч ' + mm + 'м';
}

async function loadJobs() {
  const res = await fetch('/api/tasks');
  const jobs = await res.json();
  const body = $('jobsBody');
  body.innerHTML = '';

  for (const j of jobs) {
    const tr = document.createElement('tr');
    const modelLabel = j.models && j.models.length ? j.models.map(m => m).join(', ') : '—';
    const statusLabel = j.paused ? '⏸ Пауза' : (STATUS_LABELS[j.status] || j.status);
    const statusClass = j.paused ? 'paused' : (j.status === 'done' ? 'done' : j.status === 'error' ? 'error' : j.status === 'running' ? 'running' : j.status === 'queued' ? 'queued' : 'draft');
    const progress = j.total > 0 ? `${j.done}/${j.total}` : '0/0';
    const typeLabel = j.type === 'text' ? '📝 Тексты' : j.type === 'reference' ? '🔍 Референсы' : j.type === 'upscale' ? '🔍 Upscale' : '🖼️ Изображения';
    const resultUrl = j.type === 'text' ? `/llm/result/${j.id}` : j.type === 'reference' ? `/ref/result/${j.id}` : j.type === 'upscale' ? `/upscale/${j.id}` : `/result/${j.id}`;
    const etaLabel = (j.status === 'running' || j.status === 'queued') ? fmtDuration(j.eta) : '—';

    const isActive = j.status === 'running' || j.status === 'queued';
    let pauseBtn = '';
    if (j.type !== 'upscale') {
      if (j.paused) {
        pauseBtn = `<button class="btn btn-primary btn-sm" data-resume="${j.id}" data-type="${j.type}" title="Возобновить">▶</button>`;
      } else if (isActive) {
        pauseBtn = `<button class="btn btn-outline btn-sm" data-pause="${j.id}" data-type="${j.type}" title="Пауза">⏸</button>`;
      }
    }

    const configureUrl = j.type === 'reference' ? `/ref/configure/${j.id}` : `/configure/${j.id}`;
    tr.innerHTML = `
      <td><a href="${resultUrl}">#${j.id}</a></td>
      <td>${typeLabel}</td>
      <td><a href="${resultUrl}">${esc(j.source_name)}</a></td>
      <td>${esc(modelLabel)}</td>
      <td>${progress}</td>
      <td>${etaLabel}</td>
      <td><span class="badge badge--${statusClass}">${statusLabel}</span></td>
      <td>${fmtDate(j.created_at)}</td>
      <td>
        ${j.status === 'draft' && (j.type === 'image' || j.type === 'reference')
          ? `<a class="btn btn-primary btn-sm" href="${configureUrl}">Настроить</a>`
          : `<a class="btn btn-outline btn-sm" href="${resultUrl}">Открыть</a>`}
        ${pauseBtn}
        <button class="btn btn-outline-danger btn-sm" data-del="${j.id}" data-type="${j.type}">🗑</button>
      </td>
    `;
    body.appendChild(tr);
  }

  body.querySelectorAll('[data-del]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm('Удалить задачу? Это действие нельзя отменить.')) return;
      const url = btn.dataset.type === 'text'
        ? '/api/llm/jobs/' + btn.dataset.del
        : btn.dataset.type === 'reference'
          ? '/api/ref/jobs/' + btn.dataset.del
          : btn.dataset.type === 'upscale'
            ? '/api/upscale/batch/' + btn.dataset.del
            : '/api/jobs/' + btn.dataset.del;
      await fetch(url, { method: 'DELETE' });
      loadJobs();
    });
  });

  body.querySelectorAll('[data-pause]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const url = btn.dataset.type === 'text'
        ? '/api/llm/jobs/' + btn.dataset.pause + '/pause'
        : btn.dataset.type === 'reference'
          ? '/api/ref/jobs/' + btn.dataset.pause + '/pause'
          : '/api/jobs/' + btn.dataset.pause + '/pause';
      await fetch(url, { method: 'POST' });
      loadJobs();
    });
  });

  body.querySelectorAll('[data-resume]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const url = btn.dataset.type === 'text'
        ? '/api/llm/jobs/' + btn.dataset.resume + '/resume'
        : btn.dataset.type === 'reference'
          ? '/api/ref/jobs/' + btn.dataset.resume + '/resume'
          : '/api/jobs/' + btn.dataset.resume + '/resume';
      await fetch(url, { method: 'POST' });
      loadJobs();
    });
  });
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function loadModels() {
  const res = await fetch('/api/models');
  const models = await res.json();
  const body = $('modelsBody');
  if (!body) return;
  body.innerHTML = '';

  for (const m of models) {
    const tr = document.createElement('tr');
    let statusHtml;
    if (m.downloaded) {
      statusHtml = '<span class="badge badge--done">Загружена</span>';
    } else if (m.download_status === 'downloading') {
      const pct = Math.round(m.download_progress || 0);
      statusHtml = `
        <div class="dl-progress">
          <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
          <span class="meta">${pct}% — ${esc(m.download_message || '')}</span>
        </div>`;
    } else if (m.download_status === 'error') {
      statusHtml = `<span class="badge badge--error">Ошибка</span> <span class="meta">${esc(m.download_message || '')}</span>`;
    } else {
      statusHtml = '<span class="badge badge--queued">Не загружена</span>';
    }

    const btn = m.downloaded
      ? ''
      : (m.download_status === 'downloading'
        ? ''
        : `<button class="btn btn-primary btn-sm" data-dl="${m.id}">Загрузить</button>`);

    tr.innerHTML = `
      <td>${esc(m.label)}</td>
      <td>${statusHtml}</td>
      <td>${btn}</td>
    `;
    body.appendChild(tr);
  }

  body.querySelectorAll('[data-dl]').forEach(btn => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = 'Загрузка…';
      await fetch('/api/models/' + btn.dataset.dl + '/download', { method: 'POST' });
      loadModels();
    });
  });
}

$('uploadForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const file = $('csvFile').files[0];
  if (!file) { alert('Выберите CSV файл'); return; }
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/api/jobs/upload', { method: 'POST', body: form });
  if (!res.ok) { alert('Ошибка: ' + (await res.text())); return; }
  const data = await res.json();
  window.location.href = '/map/' + data.upload_id;
});

$('llmUploadForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const file = $('llmCsvFile').files[0];
  if (!file) { alert('Выберите CSV файл'); return; }
  const model = $('llmModel').value;
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/api/llm/upload?model=' + encodeURIComponent(model), { method: 'POST', body: form });
  if (!res.ok) { alert('Ошибка: ' + (await res.text())); return; }
  const data = await res.json();
  await fetch('/api/llm/jobs/' + data.id + '/run', { method: 'POST' });
  window.location.href = '/llm/result/' + data.id;
});

$('refUploadForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const file = $('refCsvFile').files[0];
  if (!file) { alert('Выберите CSV файл'); return; }
  const form = new FormData();
  form.append('file', file);
  const res = await fetch('/api/ref/upload', { method: 'POST', body: form });
  if (!res.ok) { alert('Ошибка: ' + (await res.text())); return; }
  const data = await res.json();
  window.location.href = '/ref/map/' + data.upload_id;
});

loadModels();
loadJobs();
setInterval(loadJobs, 5000);
setInterval(loadModels, 3000);

// ─── Кнопка паузы/возобновления ─────────────────────────────────────────────

async function updateQueueButton() {
  const btn = $('btnToggle');
  if (!btn) return;
  const res = await fetch('/api/queue/status');
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
  const res = await fetch('/api/queue/status');
  const data = await res.json();
  if (data.paused) {
    await fetch('/api/queue/resume', { method: 'POST' });
  } else {
    await fetch('/api/queue/pause', { method: 'POST' });
  }
  updateQueueButton();
});

updateQueueButton();
setInterval(updateQueueButton, 3000);

// ─── Кнопки управления сервером ────────────────────────────────────────────

const btnRestart = $('btnRestart');
if (btnRestart) {
  btnRestart.addEventListener('click', async () => {
    if (!confirm('Перезапустить веб-сервер?')) return;
    await fetch('/api/server/restart', { method: 'POST' });
    let attempts = 0;
    const wait = setInterval(async () => {
      attempts++;
      try {
        const r = await fetch('/api/tasks');
        if (r.ok) {
          clearInterval(wait);
          location.reload();
        }
      } catch (e) {}
      if (attempts > 60) clearInterval(wait);
    }, 2000);
  });
}

const btnStop = $('btnStop');
if (btnStop) {
  btnStop.addEventListener('click', async () => {
    if (!confirm('Остановить сервер полностью (ComfyUI + прокси)?')) return;
    await fetch('/api/server/stop', { method: 'POST' });
  });
}

// Статусная строка сервера (CPU/GPU/RAM/диск + Now Working + Free in)
// теперь в отдельном файле status_bar.js, общем для всех страниц.

