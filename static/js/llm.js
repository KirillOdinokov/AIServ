(function () {
  const $ = (id) => document.getElementById(id);

  const STATUS_LABELS = {
    draft: 'Черновик',
    queued: 'В очереди',
    running: 'Генерация',
    paused: 'Пауза',
    done: 'Готово',
    error: 'Ошибка',
  };

  function statusBadge(status) {
    const cls = status === 'done' ? 'bg-green-lt' : status === 'error' ? 'bg-red-lt'
      : status === 'running' ? 'bg-blue-lt' : status === 'queued' ? 'bg-yellow-lt' : 'bg-secondary-lt';
    return '<span class="badge ' + cls + '">' + (STATUS_LABELS[status] || status) + '</span>';
  }

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
    if (!jobs.length) {
      body.innerHTML = '<tr><td colspan="6" class="text-secondary text-center py-4">Задач пока нет</td></tr>';
      return;
    }
    for (const j of jobs) {
      const tr = document.createElement('tr');
      const progress = j.total > 0 ? j.done + '/' + j.total : '0/0';
      tr.innerHTML = '<td><a href="/llm/result/' + j.id + '">#' + j.id + '</a></td>' +
        '<td><a href="/llm/result/' + j.id + '">' + esc(j.source_name) + '</a></td>' +
        '<td>' + progress + '</td>' +
        '<td>' + statusBadge(j.status) + '</td>' +
        '<td class="text-secondary">' + fmtDate(j.created_at) + '</td>' +
        '<td class="text-end"><div class="btn-list flex-nowrap">' +
        '<a class="btn btn-outline-secondary btn-sm" href="/llm/result/' + j.id + '">Открыть</a>' +
        '<button class="btn btn-outline-danger btn-sm" data-del="' + j.id + '"><i class="ti ti-trash"></i></button>' +
        '</div></td>';
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

  const uploadForm = $('uploadForm');
  if (uploadForm) uploadForm.addEventListener('submit', async (e) => {
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

  const toggle = $('llmToggle');
  async function updateQueueButton() {
    if (!toggle) return;
    const data = await (await fetch('/api/llm/status')).json();
    if (data.paused) {
      toggle.textContent = '▶ Продолжить';
      toggle.title = 'Возобновить генерацию текстов';
    } else {
      toggle.textContent = '⏸ Пауза';
      toggle.title = 'Остановить генерацию текстов';
    }
  }

  if (toggle) {
    toggle.addEventListener('click', async () => {
      const data = await (await fetch('/api/llm/status')).json();
      await fetch(data.paused ? '/api/llm/resume' : '/api/llm/pause', { method: 'POST' });
      updateQueueButton();
      updatePeakStatus();
    });
  }

  async function updatePeakStatus() {
    const el = $('peakStatus');
    if (!el) return;
    const data = await (await fetch('/api/llm/status')).json();
    let text;
    if (data.reason === 'paused_manual') {
      text = 'Остановлено вручную';
    } else if (data.reason === 'paused_peak') {
      const wins = data.peak_windows_local.map(w => w[0] + ':00–' + w[1] + ':00').join(' и ');
      text = 'Peak Time (' + wins + ' МСК) · сейчас ' + data.current_time_local + ' МСК';
    } else if (data.reason === 'running') {
      text = 'Идёт генерация';
    } else {
      text = 'Ожидание задач';
    }
    el.textContent = text;
  }

  loadJobs();
  updateQueueButton();
  updatePeakStatus();
  setInterval(loadJobs, 5000);
  setInterval(updateQueueButton, 5000);
  setInterval(updatePeakStatus, 5000);
})();
