(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const STATUS = {
    draft: ['Черновик', 'bg-secondary-lt'],
    queued: ['В очереди', 'bg-yellow-lt'],
    running: ['Генерация', 'bg-blue-lt'],
    done: ['Готово', 'bg-green-lt'],
    error: ['Ошибка', 'bg-red-lt'],
  };
  const TYPE_LABEL = { image: 'Изображения', text: 'Тексты', reference: 'Референсы', upscale: 'Upscale' };
  const TYPE_BADGE = { image: 'bg-blue-lt', text: 'bg-purple-lt', reference: 'bg-azure-lt', upscale: 'bg-teal-lt' };

  function fmtDate(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) +
      ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }
  function fmtDuration(sec) {
    if (sec == null) return '—';
    sec = Math.round(sec);
    if (sec < 60) return sec + 'с';
    const m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return m + 'м ' + s + 'с';
    const h = Math.floor(m / 60), mm = m % 60;
    if (h < 24) return h + 'ч ' + mm + 'м';
    return Math.floor(h / 24) + 'д ' + (h % 24) + 'ч';
  }
  function resultUrl(j) {
    if (j.type === 'text') return '/llm/result/' + j.id;
    if (j.type === 'reference') return '/ref/result/' + j.id;
    if (j.type === 'upscale') return '/upscale/' + j.id;
    return '/result/' + j.id;
  }

  async function loadTasks() {
    const body = $('jobsBody');
    if (!body) return;
    try {
      const tasks = await (await fetch('/api/tasks')).json();
      body.innerHTML = '';
      if (!tasks.length) {
        body.innerHTML = '<tr><td colspan="9" class="text-secondary text-center py-4">Задач пока нет</td></tr>';
        return;
      }
      for (const j of tasks) {
        const tr = document.createElement('tr');
        const [stLabel, stBadge] = j.paused ? ['Пауза', 'bg-orange-lt'] : (STATUS[j.status] || [j.status, 'bg-secondary-lt']);
        const typeLabel = TYPE_LABEL[j.type] || j.type;
        const typeBadge = TYPE_BADGE[j.type] || 'bg-secondary-lt';
        const progress = j.total > 0 ? (j.done + '/' + j.total) : '—';
        const eta = (j.status === 'running' || j.status === 'queued') ? fmtDuration(j.eta) : '—';
        const models = (j.models && j.models.length) ? j.models.join(', ') : '—';
        const isActive = j.status === 'running' || j.status === 'queued';
        let pauseBtn = '';
        if (j.type !== 'upscale') {
          if (j.paused) pauseBtn = '<button class="btn btn-sm btn-outline-success" data-resume="' + j.id + '" data-type="' + j.type + '" title="Возобновить"><i class="ti ti-player-play"></i></button>';
          else if (isActive) pauseBtn = '<button class="btn btn-sm btn-outline-secondary" data-pause="' + j.id + '" data-type="' + j.type + '" title="Пауза"><i class="ti ti-player-pause"></i></button>';
        }
        const url = resultUrl(j);
        const configureUrl = j.type === 'reference' ? '/ref/configure/' + j.id : '/configure/' + j.id;
        const openBtn = (j.status === 'draft' && (j.type === 'image' || j.type === 'reference'))
          ? '<a class="btn btn-sm btn-primary" href="' + configureUrl + '">Настроить</a>'
          : '<a class="btn btn-sm btn-outline-secondary" href="' + url + '">Открыть</a>';
        tr.innerHTML =
          '<td><a href="' + url + '">#' + j.id + '</a></td>' +
          '<td><span class="badge ' + typeBadge + '">' + esc(typeLabel) + '</span></td>' +
          '<td><a href="' + url + '">' + esc(j.source_name) + '</a></td>' +
          '<td class="text-secondary">' + esc(models) + '</td>' +
          '<td>' + progress + '</td>' +
          '<td class="text-secondary">' + eta + '</td>' +
          '<td><span class="badge ' + stBadge + '">' + esc(stLabel) + '</span></td>' +
          '<td class="text-secondary">' + fmtDate(j.created_at) + '</td>' +
          '<td class="text-end"><div class="btn-list flex-nowrap">' + openBtn + pauseBtn +
          '<button class="btn btn-sm btn-outline-danger" data-del="' + j.id + '" data-type="' + j.type + '" title="Удалить"><i class="ti ti-trash"></i></button></div></td>';
        body.appendChild(tr);
      }
      bindRowActions(body);
    } catch (e) {
      body.innerHTML = '<tr><td colspan="9" class="text-secondary text-center py-4">Не удалось загрузить задачи</td></tr>';
    }
  }

  function bindRowActions(body) {
    body.querySelectorAll('[data-del]').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm('Удалить задачу? Это действие нельзя отменить.')) return;
        const t = btn.dataset.type;
        const url = t === 'text' ? '/api/llm/jobs/' + btn.dataset.del
          : t === 'reference' ? '/api/ref/jobs/' + btn.dataset.del
            : t === 'upscale' ? '/api/upscale/batch/' + btn.dataset.del
              : '/api/jobs/' + btn.dataset.del;
        await fetch(url, { method: 'DELETE' });
        loadTasks();
      });
    });
    body.querySelectorAll('[data-pause]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const t = btn.dataset.type;
        const url = t === 'text' ? '/api/llm/jobs/' + btn.dataset.pause + '/pause'
          : t === 'reference' ? '/api/ref/jobs/' + btn.dataset.pause + '/pause'
            : '/api/jobs/' + btn.dataset.pause + '/pause';
        await fetch(url, { method: 'POST' });
        loadTasks();
      });
    });
    body.querySelectorAll('[data-resume]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const t = btn.dataset.type;
        const url = t === 'text' ? '/api/llm/jobs/' + btn.dataset.resume + '/resume'
          : t === 'reference' ? '/api/ref/jobs/' + btn.dataset.resume + '/resume'
            : '/api/jobs/' + btn.dataset.resume + '/resume';
        await fetch(url, { method: 'POST' });
        loadTasks();
      });
    });
  }

  async function loadModels() {
    const body = $('modelsBody');
    if (!body) return;
    try {
      const models = await (await fetch('/api/models')).json();
      body.innerHTML = '';
      for (const m of models) {
        const tr = document.createElement('tr');
        let statusHtml;
        let btn = '';
        if (m.downloaded) {
          statusHtml = '<span class="badge bg-green-lt">Загружена</span>';
        } else if (m.download_status === 'downloading') {
          const pct = Math.round(m.download_progress || 0);
          statusHtml = '<div class="d-flex align-items-center gap-2"><div class="progress progress-sm flex-fill"><div class="progress-bar" style="width:' + pct + '%"></div></div><span class="text-secondary small">' + pct + '%</span></div>';
        } else if (m.download_status === 'error') {
          statusHtml = '<span class="badge bg-red-lt">Ошибка</span> <span class="text-secondary small">' + esc(m.download_message || '') + '</span>';
        } else {
          statusHtml = '<span class="badge bg-secondary-lt">Не загружена</span>';
          btn = '<button class="btn btn-sm btn-primary" data-dl="' + m.id + '">Загрузить</button>';
        }
        tr.innerHTML = '<td>' + esc(m.label) + '</td><td>' + statusHtml + '</td><td class="text-end">' + btn + '</td>';
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
    } catch (e) {}
  }

  // ── Загрузки ─────────────────────────────────────────────────────────────
  const uploadForm = $('uploadForm');
  if (uploadForm) uploadForm.addEventListener('submit', async (e) => {
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

  const llmForm = $('llmUploadForm');
  if (llmForm) llmForm.addEventListener('submit', async (e) => {
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

  const refForm = $('refUploadForm');
  if (refForm) refForm.addEventListener('submit', async (e) => {
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

  loadTasks();
  loadModels();
  setInterval(loadTasks, 5000);
  setInterval(loadModels, 5000);
})();
