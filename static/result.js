const $ = (id) => document.getElementById(id);
const jobId = window.location.pathname.split('/').pop();

const STATUS_LABELS = {
  draft: 'Черновик',
  queued: 'В очереди',
  running: 'Генерация',
  done: 'Сгенерировано',
  error: 'Ошибка',
};

let currentJob = null;
let itemCache = {};
let lightboxImages = [];
let lightboxIndex = 0;

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
  const res = await fetch('/api/jobs/' + jobId);
  const job = await res.json();
  currentJob = job;

  // кэшируем тяжёлые данные (промпты, filename item) на клиенте
  itemCache = {};
  job.items.forEach((it, idx) => {
    itemCache[it.id] = {
      idx: idx,
      item_filename: it.filename,
      prompt: it.prompt,
    };
  });

  $('jobTitle').textContent = 'Задача #' + job.id + ': ' + job.source_name;

  renderJobHeader(job);
  renderItems(job);
  renderApproveBar(job);

  if (job.status === 'running' || job.status === 'queued') {
    pollProgress();
  }
}

// Лёгкий опрос: прогресс в шапку, компактные варианты в сетку
async function pollProgress() {
  try {
    const [pr, vr] = await Promise.all([
      fetch(`/api/jobs/${jobId}/progress`),
      fetch(`/api/jobs/${jobId}/variants`),
    ]);
    const st = await pr.json();
    renderJobHeader({ ...st, source_name: currentJob.source_name });
    const vdata = await vr.json();
    renderItemsFromVariants(vdata.items);

    if (st.status === 'running' || st.status === 'queued') {
      setTimeout(pollProgress, 3000);
    } else {
      loadJob();
    }
  } catch (e) {
    setTimeout(pollProgress, 3000);
  }
}

// Рендер сетки из компактных вариантов + кэшированных промптов
function renderItemsFromVariants(items) {
  const list = $('itemsList');
  list.innerHTML = '';
  items.forEach((item) => {
    const meta = itemCache[item.id] || { item_filename: item.id, prompt: '' };
    const row = buildRow(meta.idx, item.id, meta.item_filename, meta.prompt, item.approved_variant_id, item.variants);
    list.appendChild(row);
  });
  attachItemEvents(list);
}

function renderJobHeader(job) {
  const statusClass = job.status === 'done' ? 'done' : job.status === 'error' ? 'error' : job.status === 'running' ? 'running' : job.status === 'queued' ? 'queued' : 'draft';
  $('jobStatus').className = 'badge badge--' + statusClass;
  $('jobStatus').textContent = STATUS_LABELS[job.status] || job.status;
  $('jobCounts').textContent = `сгенерировано ${job.done}/${job.total}`;
  const eta = (job.status === 'running' || job.status === 'queued') ? fmtDuration(job.eta) : '';
  $('jobEta').textContent = eta ? `осталось ~${eta}` : '';
  const pct = job.total > 0 ? Math.round(job.done / job.total * 100) : 0;
  $('progressFill').style.width = pct + '%';
}

// Лёгкий опрос прогресса (без перезагрузки всех items)
async function pollProgress() {
  try {
    const res = await fetch(`/api/jobs/${jobId}/progress`);
    const st = await res.json();
    renderJobHeader({ ...st, source_name: currentJob.source_name });
    if (st.status === 'running' || st.status === 'queued') {
      setTimeout(pollProgress, 3000);
    } else {
      // генерация завершилась — грузим полные данные один раз
      loadJob();
    }
  } catch (e) {
    setTimeout(pollProgress, 3000);
  }
}

function renderApproveBar(job) {
  const approved = job.items.filter(it => it.approved_variant_id).length;
  const approveBtn = $('btnApprove');
  if (approved > 0) {
    $('approveCount').textContent = `Выбрано изображений: ${approved}`;
    approveBtn.style.display = '';
  } else {
    $('approveCount').textContent = 'Выбрано изображений: 0';
    approveBtn.style.display = 'none';
  }
}

function renderItems(job) {
  const list = $('itemsList');
  list.innerHTML = '';

  job.items.forEach((item, idx) => {
    const row = buildRow(idx, item.id, item.filename, item.prompt, item.approved_variant_id, item.variants);
    list.appendChild(row);
  });

  attachItemEvents(list);
}

function buildRow(idx, itemId, itemFilename, prompt, approved, variants) {
  const row = document.createElement('div');
  row.className = 'row';

  const badge = approved
    ? '<span class="badge badge--done">Утверждено</span>'
    : '<span class="badge badge--queued">Не выбрано</span>';

  const variantsHtml = variants.map(v => {
    const isApproved = v.id === approved;
    const mark = isApproved ? '<span class="approved-mark">✓</span>' : '';
    let content;
    if (v.status === 'done' && v.filename) {
      content = `<img src="/api/thumb/${v.filename}" alt="вариант" loading="lazy" data-full="/api/output/${v.filename}">`;
    } else if (v.status === 'error') {
      content = `<div class="ph">⚠<small>${esc(v.error || 'ошибка')}</small></div>`;
    } else {
      content = `<div class="ph">⏳<small>${v.status === 'running' ? 'генерация…' : 'в очереди'}</small></div>`;
    }
    const approveBtn = v.status === 'done'
      ? `<button class="btn ${isApproved ? 'btn-success' : 'btn-outline'} btn-sm" data-approve="${v.id}" title="${isApproved ? 'Нажмите, чтобы снять выбор' : 'Выбрать'}">${isApproved ? '✓ Выбрано' : 'Выбрать'}</button>`
      : '';
    return `
      <div class="variant ${isApproved ? 'is-approved' : ''}">
        ${mark}
        ${content}
        <div class="variant-bar">
          <span class="meta">${esc(v.model_label)}${v.elapsed ? ' · ' + v.elapsed + 'с' : ''}</span>
          ${approveBtn}
        </div>
      </div>
    `;
  }).join('');

  row.innerHTML = `
    <div class="row-head">
      <div>
        <div class="title">${idx + 1}. ${esc(itemFilename || itemId)}</div>
        ${badge}
        <div class="prompt">${esc(prompt)}</div>
      </div>
      <div class="more-controls">
        <select class="select more-count" style="width:auto;">
          <option value="1" selected>1</option>
          <option value="2">2</option>
          <option value="3">3</option>
          <option value="4">4</option>
        </select>
        <button class="btn btn-outline btn-sm" data-more="${itemId}">+ Сгенерировать ещё</button>
      </div>
    </div>
    <div class="variants">${variantsHtml}</div>
  `;
  return row;
}

function attachItemEvents(list) {
  list.querySelectorAll('[data-approve]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const itemId = btn.closest('.row').querySelector('[data-more]').dataset.more;
      await fetch(`/api/jobs/${jobId}/items/${itemId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ variant_id: btn.dataset.approve }),
      });
      loadJob();
    });
  });

  list.querySelectorAll('[data-more]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const itemId = btn.dataset.more;
      const count = parseInt(btn.closest('.more-controls').querySelector('.more-count').value) || 1;
      await fetch(`/api/jobs/${jobId}/items/${itemId}/regenerate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ count }),
      });
      loadJob();
    });
  });

  // Клик по картинке — открыть лайтбокс
  list.querySelectorAll('img[data-full]').forEach(img => {
    img.addEventListener('click', () => {
      lightboxImages = Array.from(list.querySelectorAll('img[data-full]')).map(i => i.dataset.full);
      lightboxIndex = lightboxImages.indexOf(img.dataset.full);
      showLightbox();
    });
  });
}

// ─── Лайтбокс ────────────────────────────────────────────────────────────────

function showLightbox() {
  $('lightboxImg').src = lightboxImages[lightboxIndex];
  $('lightbox').classList.add('show');
}

function hideLightbox() {
  $('lightbox').classList.remove('show');
}

function lightboxStep(delta) {
  lightboxIndex = (lightboxIndex + delta + lightboxImages.length) % lightboxImages.length;
  $('lightboxImg').src = lightboxImages[lightboxIndex];
}

$('lightboxClose').addEventListener('click', hideLightbox);
$('lightboxPrev').addEventListener('click', () => lightboxStep(-1));
$('lightboxNext').addEventListener('click', () => lightboxStep(1));
$('lightbox').addEventListener('click', (e) => {
  if (e.target === $('lightbox')) hideLightbox();
});
document.addEventListener('keydown', (e) => {
  if (!$('lightbox').classList.contains('show')) return;
  if (e.key === 'Escape') hideLightbox();
  if (e.key === 'ArrowLeft') lightboxStep(-1);
  if (e.key === 'ArrowRight') lightboxStep(1);
});

// ─── Утверждение ─────────────────────────────────────────────────────────────

const btnPublish = $('btnPublish');
const btnZip = $('btnZip');

async function updatePublishButton() {
  try {
    const sr = await fetch(`/api/jobs/${jobId}/finalize/status`);
    const st = await sr.json();
    if (st.ready_to_publish) {
      btnPublish.style.display = '';
      btnPublish.href = '/publish/' + jobId;
      btnPublish.title = `Готово к публикации: ${st.images_count} изображений`;
      btnZip.style.display = '';
      btnZip.href = '/api/jobs/' + jobId + '/zip';
    } else {
      btnPublish.style.display = 'none';
      btnZip.style.display = 'none';
    }
  } catch (e) {}
}

$('btnApprove').addEventListener('click', async () => {
  const approved = currentJob.items.filter(it => it.approved_variant_id).length;
  if (!approved) return;
  if (!confirm(`Утвердить ${approved} изображений? Они будут сохранены в папку images/.`)) return;
  $('btnApprove').disabled = true;
  $('btnApprove').textContent = 'Сохранение...';

  const res = await fetch(`/api/jobs/${jobId}/finalize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ save_originals: $('chkOriginals').checked }),
  });
  const data = await res.json();

  // опрашиваем прогресс; по завершении — покажем кнопку публикации
  const poll = async () => {
    const sr = await fetch(`/api/jobs/${jobId}/finalize/status`);
    const st = await sr.json();
    if (st.running) {
      const pct = st.total > 0 ? Math.round(st.done / st.total * 100) : 0;
      $('btnApprove').textContent = `Сохранение... ${st.done}/${st.total} (${pct}%)`;
      setTimeout(poll, 1000);
    } else {
      $('btnApprove').disabled = false;
      $('btnApprove').textContent = 'Утвердить выбранные';
      updatePublishButton();
    }
  };
  poll();
});

$('btnSelectAll').addEventListener('click', async () => {
  const res = await fetch(`/api/jobs/${jobId}/approve-all`, { method: 'POST' });
  const data = await res.json();
  if (data.selected === 0) {
    alert('Нет сгенерированных изображений для выбора.');
  }
  loadJob();
});

loadJob();
updatePublishButton();
