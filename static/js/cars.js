(function () {
  const $ = (id) => document.getElementById(id);
  let catalog = null;
  let jobId = null;
  let pollTimer = null;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function showUploadPanel() {
    const upload = document.querySelector('input[name=sourceMode]:checked').value === 'upload';
    $('uploadPanel').style.display = upload ? '' : 'none';
    $('modelPanel').style.display = upload ? 'none' : '';
  }

  function fillSelect(el, values, selected) {
    el.innerHTML = values.map(v => {
      const value = typeof v === 'string' ? v : v.value;
      const label = typeof v === 'string' ? v : v.label;
      return '<option value="' + esc(value) + '"' + (value === selected ? ' selected' : '') + '>' + esc(label) + '</option>';
    }).join('');
  }

  function fillModelsForBrand(brand) {
    const models = (catalog.brands[brand] || []).slice();
    fillSelect($('carModel'), models, models[0] || '');
  }

  function creditsFor(m) {
    const c = m.credits || {};
    if ('standard' in c) return c.standard;
    return c['2k'] != null ? c['2k'] : c['1k'];
  }

  function fmtDate(ts) {
    if (!ts) return '—';
    const d = new Date(ts * 1000);
    return d.toLocaleDateString('ru-RU') + ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }

  function statusLabel(status) {
    return { running: 'Генерация', done: 'Готово', error: 'Ошибка' }[status] || status;
  }

  function historyModels(job) {
    return (job.models || []).map(m => {
      const icon = m.status === 'done' ? '✓' : m.status === 'error' ? '✕' : '…';
      const api = (m.status === 'running' && m.api_status) ? ' (' + m.api_status + ')' : '';
      return esc(m.label || m.model_id) + ' ' + icon + api;
    }).join(', ');
  }

  async function loadHistory() {
    const body = $('carHistoryBody');
    if (!body) return;
    try {
      const jobs = await (await fetch('/api/cars/jobs')).json();
      body.innerHTML = '';
      if (!jobs.length) {
        body.innerHTML = '<tr><td colspan="6" class="text-secondary text-center py-4">История пока пуста</td></tr>';
        return;
      }
      jobs.forEach(job => {
        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        const car = job.source_mode === 'model'
          ? (job.brand + ' ' + job.model).trim()
          : 'Своё фото';
        const film = (job.finish === 'gloss' ? 'Глянцевая' : 'Матовая') +
          (job.film_color && job.film_color !== 'в цвет кузова' ? ' · ' + job.film_color : ' · в цвет кузова');
        const anti = { none: 'нет', matte_black: 'матовый чёрный', gloss_black: 'глянцевый чёрный' }[job.anti_chrome] || job.anti_chrome;
        tr.innerHTML = '<td class="text-secondary">' + fmtDate(job.created_at) + '</td>' +
          '<td><div class="fw-bold">' + esc(car) + '</div><div class="text-secondary small">' + historyModels(job) + '</div></td>' +
          '<td class="text-secondary">' + esc(film) + '</td>' +
          '<td class="text-secondary">' + esc(anti) + '</td>' +
          '<td><span class="badge ' + (job.status === 'done' ? 'bg-green-lt' : job.status === 'error' ? 'bg-red-lt' : 'bg-blue-lt') + '">' + esc(statusLabel(job.status)) + '</span></td>' +
          '<td class="text-end"><button class="btn btn-sm btn-outline-secondary" data-open-car="' + esc(job.id) + '">Открыть</button></td>';
        tr.addEventListener('click', () => openHistory(job.id));
        body.appendChild(tr);
      });
      body.querySelectorAll('[data-open-car]').forEach(btn => {
        btn.addEventListener('click', (e) => { e.stopPropagation(); openHistory(btn.dataset.openCar); });
      });
    } catch (e) {
      body.innerHTML = '<tr><td colspan="6" class="text-secondary text-center py-4">Не удалось загрузить историю</td></tr>';
    }
  }

  async function openHistory(id) {
    const res = await fetch('/api/cars/jobs/' + id);
    if (!res.ok) return;
    const job = await res.json();
    jobId = job.id;
    $('carResultsCard').style.display = '';
    renderJob(job);
    $('carResultsCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (job.status === 'running') poll();
  }

  function addModelOption(box, m) {
    const id = 'carModel_' + m.id.replace(/[^a-z0-9_-]/gi, '_');
    const label = document.createElement('label');
    label.className = 'form-check';
    label.innerHTML = '<input class="form-check-input" type="checkbox" id="' + id + '" name="car_model" value="' + esc(m.id) + '"' +
      (m.available === false ? ' disabled' : '') + '>' +
      '<span class="form-check-label">' + esc(m.label) +
      (m.kind === 'nano' ? ' <span class="text-secondary">— ' + (creditsFor(m) || '?') + ' кред.</span>' : ' <span class="text-secondary">— локально</span>') +
      '</span>';
    box.appendChild(label);
  }

  async function loadCatalog() {
    catalog = await (await fetch('/api/cars/catalog')).json();
    fillSelect($('carBrand'), Object.keys(catalog.brands), Object.keys(catalog.brands)[0] || '');
    fillModelsForBrand(Object.keys(catalog.brands)[0] || '');
    fillSelect($('bodyColor'), catalog.body_colors, 'как на фото');
    fillSelect($('filmColor'), catalog.film_colors, 'в цвет кузова');
    fillSelect($('antiChrome'), catalog.anti_chrome, 'none');

    const box = $('carModelOptions');
    box.innerHTML = '';
    (catalog.local_models || []).forEach(m => addModelOption(box, m));
    (catalog.nano_models || []).forEach(m => addModelOption(box, m));
    const defaults = ['flux2-klein-4b', 'nano-banana-2-1', 'nano-banana-pro'];
    defaults.forEach(id => {
      const cb = document.getElementById('carModel_' + id.replace(/[^a-z0-9_-]/gi, '_'));
      if (cb && !cb.disabled) cb.checked = true;
    });
    updateCredits();
  }

  function selectedModels() {
    return [...document.querySelectorAll('input[name=car_model]:checked')].map(c => c.value);
  }

  function updateCredits() {
    let total = 0;
    selectedModels().forEach(id => {
      const m = (catalog.nano_models || []).find(x => x.id === id);
      if (m) total += (creditsFor(m) || 0);
    });
    $('carCreditsHint').textContent = total > 0 ? '≈ ' + total + ' кредитов Nano Banana за один запуск' : '';
  }

  $('carBrand').addEventListener('change', () => fillModelsForBrand($('carBrand').value));
  document.querySelectorAll('input[name=sourceMode]').forEach(r => r.addEventListener('change', showUploadPanel));
  $('carFile').addEventListener('change', () => {
    const f = $('carFile').files[0];
    if (!f) { $('uploadPreview').style.display = 'none'; return; }
    $('uploadPreview').src = URL.createObjectURL(f);
    $('uploadPreview').style.display = '';
  });
  $('carModelOptions').addEventListener('change', updateCredits);

  function renderJob(job) {
    $('carJobStatus').textContent = job.status === 'running' ? 'Генерация…' : (job.status === 'done' ? 'Готово' : job.status);
    $('carPrompt').textContent = job.prompt || '';
    if (job.source_public_url) {
      $('sourcePreview').innerHTML = '<img src="' + job.source_public_url + '?v=' + job.id + '" alt="исходник" class="img-fluid rounded">';
    } else if (job.source_error) {
      $('sourcePreview').innerHTML = '<span class="text-warning">' + esc(job.source_error) + '</span>';
    }
    $('sourceNote').textContent = job.source_url ? 'Источник фото: ' + job.source_url : '';

    const box = $('carResults');
    box.innerHTML = '';
    job.models.forEach(m => {
      const col = document.createElement('div');
      col.className = 'col-12 col-md-6 col-xl-4';
      let inner;
      if (m.status === 'done' && m.filename) {
        const full = '/api/output/' + encodeURI(m.filename);
        inner = '<a href="' + full + '" target="_blank"><img class="card-img-top" style="width:100%;display:block;" src="/api/thumb/' + encodeURI(m.filename) + '?size=800" alt="' + esc(m.label) + '"></a>' +
          '<div class="card-body py-2"><div class="fw-bold">' + esc(m.label) + '</div>' +
          '<div class="text-secondary small">' + (m.credits ? m.credits + ' кред.' : 'локальная модель') + ' · <a href="' + full + '" target="_blank">Открыть</a></div></div>';
      } else if (m.status === 'error') {
        inner = '<div class="p-3 text-danger">' + esc(m.error) + '</div>' +
          '<div class="card-body py-2"><div class="fw-bold">' + esc(m.label) + '</div></div>';
      } else {
        inner = '<div class="d-flex align-items-center justify-content-center text-secondary" style="height:220px;">' + (m.status === 'running' ? 'Генерация…' : 'В очереди…') + '</div>' +
          '<div class="card-body py-2"><div class="fw-bold">' + esc(m.label) + '</div></div>';
      }
      col.innerHTML = '<div class="card h-100">' + inner + '</div>';
      box.appendChild(col);
    });
  }

  async function poll() {
    if (!jobId) return;
    try {
      const res = await fetch('/api/cars/jobs/' + jobId);
      if (res.ok) {
        const job = await res.json();
        renderJob(job);
        if (job.status === 'running') { pollTimer = setTimeout(poll, 2500); return; }
        $('btnCarRun').disabled = false;
        $('btnCarRun').textContent = 'Сделать примерку';
        loadHistory();
      }
    } catch (e) {}
    pollTimer = null;
  }

  $('btnCarRun').addEventListener('click', async () => {
    const sourceMode = document.querySelector('input[name=sourceMode]:checked').value;
    if (sourceMode === 'upload' && !$('carFile').files[0]) {
      alert('Загрузите фотографию автомобиля'); return;
    }
    const models = selectedModels();
    if (!models.length) { alert('Выберите хотя бы одну модель'); return; }

    const form = new FormData();
    form.append('source_mode', sourceMode);
    form.append('brand', sourceMode === 'model' ? $('carBrand').value : '');
    form.append('model', sourceMode === 'model' ? $('carModel').value : '');
    form.append('body_color', $('bodyColor').value);
    form.append('finish', $('carFinish').value);
    form.append('film_color', $('filmColor').value);
    form.append('anti_chrome', $('antiChrome').value);
    form.append('aspect_ratio', $('aspectRatio').value);
    form.append('models', JSON.stringify(models));
    if (sourceMode === 'upload') form.append('file', $('carFile').files[0]);

    $('btnCarRun').disabled = true;
    $('btnCarRun').textContent = 'Генерация…';
    $('carResultsCard').style.display = '';
    $('carResults').innerHTML = '<div class="text-secondary">Запускаем…</div>';
    try {
      const res = await fetch('/api/cars/jobs', { method: 'POST', body: form });
      if (!res.ok) throw new Error(await res.text());
      const job = await res.json();
      jobId = job.id;
      renderJob(job);
      loadHistory();
      poll();
    } catch (e) {
      alert('Ошибка: ' + e.message);
      $('btnCarRun').disabled = false;
      $('btnCarRun').textContent = 'Сделать примерку';
    }
  });

  $('btnCarHistoryRefresh').addEventListener('click', loadHistory);

  // Когда задача завершится, кнопку можно запускать снова.
  showUploadPanel();
  loadCatalog();
  loadHistory();
})();
