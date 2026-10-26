const $ = (id) => document.getElementById(id);

let currentJobId = null;
let pollTimer = null;
let batchId = null;
let batchTimer = null;

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ─── Одна картинка ──────────────────────────────────────────────────────

$('btnRun').addEventListener('click', async () => {
  const file = $('imageFile').files[0];
  if (!file) { alert('Выберите файл изображения'); return; }
  const model = $('model').value;
  const prompt = $('prompt').value.trim() || 'Upscale this image, increase sharpness and detail, reduce noise and artifacts';

  $('btnRun').disabled = true;
  $('btnRun').textContent = 'Обработка…';
  $('resultCard').style.display = '';
  $('status').textContent = 'Обработка изображения…';
  $('results').innerHTML = '';
  $('error').style.display = 'none';

  const form = new FormData();
  form.append('model', model);
  form.append('prompt', prompt);
  form.append('image', file);

  const res = await fetch('/api/upscale/generate', { method: 'POST', body: form });
  if (!res.ok) {
    $('btnRun').disabled = false;
    $('btnRun').textContent = 'Upscale';
    $('status').textContent = 'Ошибка: ' + (await res.text());
    return;
  }
  const data = await res.json();
  currentJobId = data.id;

  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/upscale/' + currentJobId);
      const job = await r.json();
      if (job.status === 'done') {
        clearInterval(pollTimer);
        $('btnRun').disabled = false;
        $('btnRun').textContent = 'Upscale';
        $('status').textContent = '';
        const full = '/api/output/' + encodeURI(job.filename);
        $('results').innerHTML = `
          <div class="variant">
            <a href="${full}" target="_blank"><img src="/api/thumb/${encodeURI(job.filename)}?size=600" alt="upscale"></a>
            <div class="variant-bar">
              <span class="meta">${esc(job.label)}</span>
              <a class="btn btn-outline btn-sm" href="${full}" download>⬇ Скачать</a>
            </div>
          </div>`;
      } else if (job.status === 'error') {
        clearInterval(pollTimer);
        $('btnRun').disabled = false;
        $('btnRun').textContent = 'Upscale';
        $('status').textContent = '';
        $('error').style.display = '';
        $('error').textContent = 'Ошибка: ' + job.error;
      }
    } catch (e) {}
  }, 2500);
});

// ─── Массовый upscale (CSV) ─────────────────────────────────────────────

function renderBatch(batch) {
  $('batchStatus').textContent = `Обработано ${batch.done}/${batch.total}`;
  const box = $('batchResults');
  box.innerHTML = '';
  for (const it of batch.items) {
    const div = document.createElement('div');
    div.className = 'variant';
    if (it.status === 'done' && it.filename) {
      const full = '/api/output/' + encodeURI(it.filename);
      div.innerHTML = `
        <a href="${full}" target="_blank"><img src="/api/thumb/${encodeURI(it.filename)}?size=200" alt="${esc(it.item_id)}"></a>
        <div class="variant-bar"><span>${esc(it.item_id)}</span></div>`;
    } else if (it.status === 'running') {
      div.innerHTML = `<div class="ph">⏳<small>${esc(it.item_id)}</small></div>`;
    } else if (it.status === 'error') {
      div.innerHTML = `<div class="ph">❌</div><div class="err">${esc(it.error)}</div><div class="variant-bar"><span>${esc(it.item_id)}</span></div>`;
    } else {
      div.innerHTML = `<div class="ph">…<small>${esc(it.item_id)}</small></div>`;
    }
    box.appendChild(div);
  }
}

function startBatchPolling(id) {
  batchId = id;
  clearInterval(batchTimer);
  batchTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/upscale/batch/' + batchId);
      const batch = await r.json();
      renderBatch(batch);
      if (batch.status === 'done') {
        clearInterval(batchTimer);
        $('btnBatch').disabled = false;
        $('btnBatch').textContent = 'Upscale (массово)';
        const ok = batch.items.filter(i => i.status === 'done').length;
        $('batchStatus').textContent = `Готово: ${ok}/${batch.total} успешно`;
        $('btnBatchZip').style.display = '';
        $('btnBatchZip').href = '/api/upscale/batch/' + batchId + '/zip';
      }
    } catch (e) {}
  }, 3000);
}

$('btnBatch').addEventListener('click', async () => {
  const file = $('csvFile').files[0];
  if (!file) { alert('Выберите CSV файл'); return; }
  const model = $('model').value;
  const prompt = $('prompt').value.trim() || 'Upscale this image, increase sharpness and detail, reduce noise and artifacts';

  $('btnBatch').disabled = true;
  $('btnBatch').textContent = 'Обработка…';
  $('batchStatus').textContent = 'Загрузка CSV…';
  $('batchResults').innerHTML = '';
  $('btnBatchZip').style.display = 'none';

  const form = new FormData();
  form.append('model', model);
  form.append('prompt', prompt);
  form.append('file', file);

  const res = await fetch('/api/upscale/batch', { method: 'POST', body: form });
  if (!res.ok) {
    $('btnBatch').disabled = false;
    $('btnBatch').textContent = 'Upscale (массово)';
    $('batchStatus').textContent = 'Ошибка: ' + (await res.text());
    return;
  }
  const data = await res.json();
  startBatchPolling(data.id);
});

// при загрузке страницы — если в URL указан id задачи, грузим её;
// иначе — возобновляем опрос незавершённого пакета
(async () => {
  const parts = window.location.pathname.split('/').filter(Boolean);
  const urlBatchId = (parts.length >= 2 && parts[parts.length - 2] === 'upscale')
    ? parts[parts.length - 1]
    : null;

  if (urlBatchId) {
    startBatchPolling(urlBatchId);
    return;
  }

  try {
    const r = await fetch('/api/upscale/batches');
    const d = await r.json();
    const running = (d.jobs || []).find(j => j.status === 'running');
    if (running) {
      $('batchStatus').textContent = `Возобновление задачи «${running.source_name}»…`;
      startBatchPolling(running.id);
    }
  } catch (e) {}
})();
