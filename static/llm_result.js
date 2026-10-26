const $ = (id) => document.getElementById(id);
const jobId = window.location.pathname.split('/').pop();

const STATUS_LABELS = {
  draft: 'Черновик',
  queued: 'В очереди',
  running: 'Генерация',
  paused: 'Пауза',
  done: 'Готово',
  error: 'Ошибка',
};

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function fmtDuration(sec) {
  if (sec == null) return '';
  sec = Math.round(sec);
  if (sec < 60) return sec + 'с';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) return m + 'м ' + s + 'с';
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return h + 'ч ' + mm + 'м';
}

async function loadJob() {
  const res = await fetch('/api/llm/jobs/' + jobId);
  const job = await res.json();

  $('jobTitle').textContent = 'Задача #' + job.id + ': ' + job.source_name;
  $('btnExport').href = '/api/llm/jobs/' + jobId + '/export';

  const statusClass = job.status === 'done' ? 'done' : job.status === 'error' ? 'error' : job.status === 'running' ? 'running' : job.status === 'queued' ? 'queued' : 'draft';
  $('jobStatus').className = 'badge badge--' + statusClass;
  $('jobStatus').textContent = STATUS_LABELS[job.status] || job.status;
  $('jobCounts').textContent = `обработано ${job.done}/${job.total}`;

  const eta = (job.status === 'running' || job.status === 'queued') ? fmtDuration(job.eta) : '';
  $('jobEta').textContent = eta ? `осталось ~${eta}` : '';

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
    const row = document.createElement('div');
    row.className = 'row';

    let statusBadge;
    if (item.status === 'done') {
      statusBadge = '<span class="badge badge--done">Готово</span>';
    } else if (item.status === 'error') {
      statusBadge = '<span class="badge badge--error">Ошибка</span>';
    } else if (item.status === 'running') {
      statusBadge = '<span class="badge badge--running">Генерация…</span>';
    } else {
      statusBadge = '<span class="badge badge--queued">В очереди</span>';
    }

    const excerptHtml = item.new_excerpt
      ? `<div class="llm-field"><strong>Короткое описание:</strong><div class="llm-text">${esc(item.new_excerpt)}</div></div>`
      : '';
    const contentHtml = item.new_content
      ? `<div class="llm-field"><strong>Длинное описание:</strong><div class="llm-text">${esc(item.new_content)}</div></div>`
      : '';
    const errorHtml = item.error
      ? `<div class="llm-field"><strong style="color:#842029;">Ошибка:</strong><div class="llm-text">${esc(item.error)}</div></div>`
      : '';

    row.innerHTML = `
      <div class="row-head">
        <div>
          <div class="title">${idx + 1}. ${esc(item.item_id || '—')} — ${esc(item.title || '')}</div>
          ${statusBadge}
        </div>
      </div>
      ${excerptHtml}
      ${contentHtml}
      ${errorHtml}
    `;
    list.appendChild(row);
  });
}

loadJob();
