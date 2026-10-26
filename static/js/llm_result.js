(function () {
  const $ = (id) => document.getElementById(id);
  const jobId = window.location.pathname.split('/').pop();

  const STATUS_LABELS = {
    draft: 'Черновик', queued: 'В очереди', running: 'Генерация',
    paused: 'Пауза', done: 'Готово', error: 'Ошибка',
  };

  function statusBadge(status) {
    const cls = status === 'done' ? 'bg-green-lt' : status === 'error' ? 'bg-red-lt'
      : status === 'running' ? 'bg-blue-lt' : status === 'queued' ? 'bg-yellow-lt' : 'bg-secondary-lt';
    return '<span class="badge ' + cls + '">' + (STATUS_LABELS[status] || status) + '</span>';
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function fmtDuration(sec) {
    if (sec == null) return '';
    sec = Math.round(sec);
    if (sec < 60) return sec + 'с';
    const m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return m + 'м ' + s + 'с';
    const h = Math.floor(m / 60), mm = m % 60;
    return h + 'ч ' + mm + 'м';
  }

  async function loadJob() {
    const res = await fetch('/api/llm/jobs/' + jobId);
    const job = await res.json();

    $('jobTitle').textContent = 'Задача #' + job.id + ': ' + job.source_name;
    $('btnExport').href = '/api/llm/jobs/' + jobId + '/export';
    $('jobStatus').className = 'badge ' + (job.status === 'done' ? 'bg-green-lt' : job.status === 'error' ? 'bg-red-lt' : job.status === 'running' ? 'bg-blue-lt' : 'bg-secondary-lt');
    $('jobStatus').textContent = STATUS_LABELS[job.status] || job.status;
    $('jobCounts').textContent = 'обработано ' + job.done + '/' + job.total;

    const eta = (job.status === 'running' || job.status === 'queued') ? fmtDuration(job.eta) : '';
    $('jobEta').textContent = eta ? 'осталось ~' + eta : '';

    const pct = job.total > 0 ? Math.round(job.done / job.total * 100) : 0;
    $('progressFill').style.width = pct + '%';

    renderItems(job);
    if (job.status === 'running' || job.status === 'queued') {
      setTimeout(loadJob, 3000);
    }
  }

  function renderItems(job) {
    const list = $('itemsList');
    list.innerHTML = '';
    job.items.forEach((item, idx) => {
      const card = document.createElement('div');
      card.className = 'card mb-2';
      const excerpt = item.new_excerpt
        ? '<div class="mb-2"><div class="text-secondary small">Короткое описание</div><div>' + esc(item.new_excerpt) + '</div></div>' : '';
      const content = item.new_content
        ? '<div class="mb-2"><div class="text-secondary small">Длинное описание</div><div>' + esc(item.new_content) + '</div></div>' : '';
      const error = item.error
        ? '<div class="mb-2"><div class="text-secondary small">Ошибка</div><div class="text-danger">' + esc(item.error) + '</div></div>' : '';
      card.innerHTML = '<div class="card-header py-2 d-flex justify-content-between align-items-center">' +
        '<div class="fw-bold">' + (idx + 1) + '. ' + esc(item.item_id || '—') + ' — ' + esc(item.title || '') + '</div>' +
        statusBadge(item.status) + '</div>' +
        '<div class="card-body py-2">' + excerpt + content + error + '</div>';
      list.appendChild(card);
    });
  }

  loadJob();
})();
