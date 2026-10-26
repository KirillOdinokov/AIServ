const $ = (id) => document.getElementById(id);

let currentJobId = null;
let pollTimer = null;
let nbModels = {}; // id -> {label, credits}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function creditsFor(modelId) {
  const m = nbModels[modelId];
  if (!m) return null;
  const res = $('resolution').value;
  const c = m.credits;
  if ('standard' in c) return c.standard;
  return (c[res] != null) ? c[res] : null;
}

function addCheckbox(box, id, label, isNb) {
  const l = document.createElement('label');
  l.className = 'model-option';
  const creditSpan = isNb ? `<span class="meta" data-credit="${id}"></span>` : '';
  l.innerHTML = `<input type="checkbox" name="model" value="${id}"> <span>${esc(label)}</span> ${creditSpan}`;
  box.appendChild(l);
}

function updateCredits() {
  // обновить цену у каждой nano-banana модели
  document.querySelectorAll('[data-credit]').forEach(sp => {
    const c = creditsFor(sp.dataset.credit);
    sp.textContent = c != null ? `— ${c} кред.` : '';
  });

  // суммарно по выбранным nano-banana моделям
  const selectedNb = [...document.querySelectorAll('input[name=model]:checked')]
    .map(c => c.value)
    .filter(id => nbModels[id]);
  const total = selectedNb.reduce((s, id) => s + (creditsFor(id) || 0), 0);
  $('creditsHint').textContent = total > 0 ? `≈ ${total} кредитов за выбранные Nano Banana модели` : '';
}

async function loadModels() {
  const [lr, nr] = await Promise.all([
    fetch('/api/models'),
    fetch('/api/nanobanana/models'),
  ]);
  const local = await lr.json();
  const nb = await nr.json();
  nbModels = {};
  (nb.models || []).forEach(m => { nbModels[m.id] = m; });

  const box = $('modelOptions');
  box.innerHTML = '';

  const imgModels = local.filter(m => !m.video && m.downloaded);
  if (!imgModels.length && !(nb.models || []).length) {
    box.innerHTML = '<span class="meta">Нет доступных моделей.</span>';
    return;
  }

  imgModels.forEach(m => addCheckbox(box, m.id, m.label, false));

  if (nb.has_key) {
    (nb.models || []).forEach(m => addCheckbox(box, m.id, m.label, true));
  } else {
    const p = document.createElement('p');
    p.className = 'meta';
    p.textContent = 'Nano Banana недоступен (нет API-ключа).';
    box.appendChild(p);
  }

  updateCredits();
}

function renderResults(job) {
  const box = $('results');
  box.innerHTML = '';
  for (const m of job.models) {
    const div = document.createElement('div');
    div.className = 'variant';
    if (m.status === 'done' && m.filename) {
      const url = '/api/thumb/' + encodeURI(m.filename) + '?size=400';
      const full = '/api/output/' + encodeURI(m.filename);
      div.innerHTML = `
        <a href="${full}" target="_blank"><img src="${url}" alt="${esc(m.label)}"></a>
        <div class="variant-bar"><span>${esc(m.label)}</span></div>`;
    } else if (m.status === 'running') {
      div.innerHTML = `<div class="ph">⏳<small>Генерация…</small></div>
        <div class="variant-bar"><span>${esc(m.label)}</span></div>`;
    } else if (m.status === 'error') {
      div.innerHTML = `<div class="ph">❌</div>
        <div class="err">${esc(m.error)}</div>
        <div class="variant-bar"><span>${esc(m.label)}</span></div>`;
    } else {
      div.innerHTML = `<div class="ph">…</div>
        <div class="variant-bar"><span>${esc(m.label)}</span></div>`;
    }
    box.appendChild(div);
  }
}

$('btnCreate').addEventListener('click', async () => {
  const prompt = $('prompt').value.trim();
  if (!prompt) { alert('Введите промпт'); return; }
  const models = [...document.querySelectorAll('input[name=model]:checked')].map(c => c.value);
  if (!models.length) { alert('Выберите хотя бы одну модель'); return; }
  const aspect = $('aspect').value;
  const resolution = $('resolution').value;

  $('btnCreate').disabled = true;
  $('btnCreate').textContent = 'Генерация…';
  $('resultsCard').style.display = '';
  $('saveStatus').textContent = '';

  const res = await fetch('/api/single/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, models, aspect_ratio: aspect, resolution }),
  });
  if (!res.ok) {
    $('btnCreate').disabled = false;
    $('btnCreate').textContent = 'Создать изображение';
    alert('Ошибка: ' + (await res.text()));
    return;
  }
  const data = await res.json();
  currentJobId = data.id;

  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/single/' + currentJobId);
      const job = await r.json();
      renderResults(job);
      if (job.status === 'done') {
        clearInterval(pollTimer);
        $('btnCreate').disabled = false;
        $('btnCreate').textContent = 'Создать изображение';
      }
    } catch (e) {}
  }, 2000);
});

$('btnSave').addEventListener('click', async () => {
  if (!currentJobId) return;
  const res = await fetch('/api/single/' + currentJobId + '/save', { method: 'POST' });
  const data = await res.json();
  if (data.ok) {
    $('saveStatus').textContent = 'Сохранено в «Single Image»: ' + data.saved.join(', ');
  } else {
    $('saveStatus').textContent = 'Ошибка: ' + (data.detail || '');
  }
});

$('resolution').addEventListener('change', updateCredits);
document.getElementById('modelOptions').addEventListener('change', updateCredits);

loadModels();
