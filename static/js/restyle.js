(function () {
  const $ = (id) => document.getElementById(id);
  let catalog = null;
  let kind = 'cut';
  let selected = new Set();
  let currentSlug = null;
  let promptDrafts = {};
  let jobId = null;
  let pollTimer = null;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmtDate(ts) {
    if (!ts) return '—';
    const d = new Date(ts * 1000);
    return d.toLocaleDateString('ru-RU') + ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }

  function statusLabel(status) {
    return { running: 'Генерация', done: 'Готово', error: 'Ошибка' }[status] || status || '—';
  }

  function currentItems() {
    return (catalog && catalog[kind]) || [];
  }

  function filteredItems() {
    const q = ($('restyleSearch').value || '').trim().toLowerCase();
    if (!q) return currentItems();
    return currentItems().filter(it =>
      [it.slug, it.designation, it.device, it.file].join(' ').toLowerCase().indexOf(q) >= 0);
  }

  function updateSelectionInfo() {
    $('restyleSelectionInfo').textContent = selected.size
      ? 'Выбрано: ' + selected.size + ' (макс. 50 за запуск)'
      : 'Ничего не выбрано';
  }

  function rememberPrompt() {
    if (catalog) promptDrafts[kind] = $('restylePrompt').value;
  }

  function setPromptForKind() {
    const v = promptDrafts[kind];
    $('restylePrompt').value = v != null ? v : ((catalog.prompts || {})[kind] || '');
  }

  function renderTabs() {
    const cut = $('restyleTabCut'), scene = $('restyleTabScene');
    cut.className = 'btn btn-sm ' + (kind === 'cut' ? 'btn-primary' : 'btn-outline-secondary');
    scene.className = 'btn btn-sm ' + (kind === 'scene' ? 'btn-primary' : 'btn-outline-secondary');
    const s = catalog.summary || {};
    cut.textContent = 'Профили (cut) · ' + (s.cut || 0);
    scene.textContent = 'Сцены (scene) · ' + (s.scene || 0);
  }

  function renderList() {
    const box = $('restyleList');
    const items = filteredItems();
    $('restyleCount').textContent = 'Найдено: ' + items.length + ' из ' + currentItems().length;
    if (!items.length) {
      box.innerHTML = '<div class="p-4 text-secondary text-center">Ничего не найдено</div>';
      updateSelectionInfo();
      return;
    }
    box.innerHTML = items.map(it => {
      const checked = selected.has(it.slug) ? ' checked' : '';
      const current = it.slug === currentSlug ? ' is-current' : '';
      const cards = it.cards_count ? ' · карточек: ' + it.cards_count : '';
      return '<div class="restyle-row' + current + '" data-row-slug="' + esc(it.slug) + '">' +
        '<input class="form-check-input" type="checkbox" data-slug="' + esc(it.slug) + '"' + checked + '>' +
        '<div class="restyle-row-main">' +
        '<div class="restyle-row-title">' + esc(it.designation || it.slug) + '</div>' +
        '<div class="restyle-row-sub">' + esc(it.slug) + (it.device ? ' · ' + esc(it.device) : '') + cards + '</div>' +
        '</div></div>';
    }).join('');
    updateSelectionInfo();
  }

  function syncCheckboxes() {
    $('restyleList').querySelectorAll('input[data-slug]').forEach(cb => {
      cb.checked = selected.has(cb.dataset.slug);
    });
  }

  function showCurrent() {
    if (!currentSlug) {
      $('restyleSelectedPreview').textContent = 'Выберите исходник слева.';
      $('restylePreviewWrap').style.display = 'none';
      return;
    }
    const item = currentItems().find(x => x.slug === currentSlug);
    if (!item) return;
    $('restyleSelectedPreview').innerHTML = '<b>' + esc(item.designation || item.slug) + '</b>' +
      ' · ' + esc(item.slug) + (selected.size > 1 ? ' · выбрано ' + selected.size : '');
    $('restylePreview').src = '/api/restyle/source/' + encodeURIComponent(kind) + '/' + encodeURIComponent(item.slug);
    $('restylePreviewWrap').style.display = '';
  }

  function selectItem(slug, on) {
    if (on) {
      if (selected.size >= 50 && !selected.has(slug)) {
        alert('За один запуск можно выбрать не более 50 изображений');
        syncCheckboxes();
        return;
      }
      selected.add(slug);
    } else {
      selected.delete(slug);
    }
    currentSlug = slug;
    updateSelectionInfo();
    syncCheckboxes();
    $('restyleList').querySelectorAll('.restyle-row').forEach(r => {
      r.classList.toggle('is-current', r.dataset.rowSlug === currentSlug);
    });
    showCurrent();
  }

  function setKind(next) {
    if (next === kind) return;
    rememberPrompt();
    kind = next;
    selected.clear();
    currentSlug = null;
    $('restyleSearch').value = '';
    renderTabs();
    renderList();
    setPromptForKind();
    showCurrent();
  }

  async function loadCatalog() {
    catalog = await (await fetch('/api/restyle/catalog')).json();
    promptDrafts = {
      cut: (catalog.prompts || {}).cut || '',
      scene: (catalog.prompts || {}).scene || '',
    };
    if (catalog.model) {
      $('restyleModel').textContent = catalog.model.label + ' — локально, бесплатно';
    }
    const s = catalog.summary || {};
    if (!(s.cut_available > 0) && !(s.scene_available > 0)) {
      $('restyleList').innerHTML = '<div class="p-4 text-warning">Исходники не найдены на сервере. Нужна папка restyle_data.</div>';
    }
    renderTabs();
    renderList();
    setPromptForKind();
  }

  function renderJob(job) {
    $('restyleResultsCard').style.display = '';
    $('restyleJobStatus').textContent = statusLabel(job.status);
    $('btnRestyleZip').disabled = job.status === 'running';
    const items = {};
    (job.items || []).forEach(it => { items[it.slug] = it; });
    const box = $('restyleResults');
    box.innerHTML = '';
    (job.variants || []).forEach(v => {
      const item = items[v.slug] || {};
      const col = document.createElement('div');
      col.className = 'col-12 col-md-6 col-xl-4';
      let inner;
      if (v.status === 'done' && v.filename) {
        const full = '/api/output/' + encodeURI(v.filename);
        inner = '<a href="' + full + '" target="_blank"><img class="card-img-top" style="width:100%;display:block;background:#f5f5f5;" src="/api/thumb/' + encodeURI(v.filename) + '?size=800" alt="' + esc(item.slug) + '"></a>' +
          '<div class="card-body py-2"><div class="fw-bold">' + esc(item.designation || item.slug) + '</div>' +
          '<div class="text-secondary small">' + esc(item.slug) + (v.elapsed ? ' · ' + v.elapsed + ' с' : '') + '</div>' +
          '<div class="small"><a href="' + full + '" target="_blank">Открыть</a></div></div>';
      } else if (v.status === 'error') {
        inner = '<div class="p-3 text-danger small">' + esc(v.error || 'Ошибка') + '</div>' +
          '<div class="card-body py-2"><div class="fw-bold">' + esc(item.designation || item.slug) + '</div>' +
          '<div class="text-secondary small">' + esc(v.slug) + '</div></div>';
      } else {
        inner = '<div class="d-flex align-items-center justify-content-center text-secondary" style="height:200px;">' +
          (v.status === 'running' ? 'Пересъёмка…' : 'В очереди…') + '</div>' +
          '<div class="card-body py-2"><div class="fw-bold">' + esc(item.designation || item.slug) + '</div>' +
          '<div class="text-secondary small">' + esc(v.slug) + '</div></div>';
      }
      col.innerHTML = '<div class="card h-100">' + inner + '</div>';
      box.appendChild(col);
    });
    if (!(job.variants || []).length) box.innerHTML = '<div class="text-secondary">Нет результатов</div>';
  }

  async function poll() {
    if (!jobId) return;
    try {
      const res = await fetch('/api/restyle/jobs/' + jobId);
      if (!res.ok) return;
      const job = await res.json();
      renderJob(job);
      if (job.status === 'running') {
        pollTimer = setTimeout(poll, 2500);
        return;
      }
      $('btnRestyleRun').disabled = false;
      $('btnRestyleRun').textContent = 'Переснять выбранные';
      loadHistory();
    } catch (e) {}
    pollTimer = null;
  }

  async function openHistory(id) {
    const res = await fetch('/api/restyle/jobs/' + id);
    if (!res.ok) return;
    const job = await res.json();
    jobId = job.id;
    renderJob(job);
    $('restyleResultsCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (job.status === 'running') poll();
  }

  async function loadHistory() {
    const body = $('restyleHistoryBody');
    try {
      const jobs = await (await fetch('/api/restyle/jobs')).json();
      body.innerHTML = '';
      if (!jobs.length) {
        body.innerHTML = '<tr><td colspan="5" class="text-secondary text-center py-4">История пока пуста</td></tr>';
        return;
      }
      jobs.forEach(job => {
        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.innerHTML = '<td class="text-secondary">' + fmtDate(job.created_at) + '</td>' +
          '<td>' + esc(job.kind === 'scene' ? 'Сцены' : 'Профили') + '</td>' +
          '<td>' + ((job.items || []).length) + ' шт.</td>' +
          '<td><span class="badge ' + (job.status === 'done' ? 'bg-green-lt' : job.status === 'error' ? 'bg-red-lt' : 'bg-blue-lt') + '">' + esc(statusLabel(job.status)) + '</span></td>' +
          '<td class="text-end"><button class="btn btn-sm btn-outline-secondary">Открыть</button></td>';
        tr.addEventListener('click', () => openHistory(job.id));
        body.appendChild(tr);
      });
    } catch (e) {
      body.innerHTML = '<tr><td colspan="5" class="text-secondary text-center py-4">Не удалось загрузить историю</td></tr>';
    }
  }

  $('restyleTabCut').addEventListener('click', () => setKind('cut'));
  $('restyleTabScene').addEventListener('click', () => setKind('scene'));
  $('restyleSearch').addEventListener('input', renderList);
  $('restyleList').addEventListener('change', (e) => {
    const cb = e.target.closest('input[type=checkbox][data-slug]');
    if (!cb) return;
    selectItem(cb.dataset.slug, cb.checked);
  });
  $('restyleList').addEventListener('click', (e) => {
    if (e.target.closest('input')) return;
    const row = e.target.closest('[data-row-slug]');
    if (!row) return;
    const cb = row.querySelector('input[type=checkbox]');
    const next = !(cb && cb.checked);
    if (cb) cb.checked = next;
    selectItem(row.dataset.rowSlug, next);
  });
  $('btnRestyleClear').addEventListener('click', () => {
    selected.clear();
    currentSlug = null;
    updateSelectionInfo();
    syncCheckboxes();
    $('restyleList').querySelectorAll('.restyle-row').forEach(r => r.classList.remove('is-current'));
    showCurrent();
  });
  $('btnRestyleSelectVisible').addEventListener('click', () => {
    const unselected = filteredItems().map(x => x.slug).filter(s => !selected.has(s));
    const take = unselected.slice(0, Math.max(0, 50 - selected.size));
    if (unselected.length > take.length) alert('Выбраны первые ' + take.length + ' изображений: максимум 50 за запуск');
    take.forEach(s => selected.add(s));
    currentSlug = currentSlug || (take[0] || null);
    updateSelectionInfo();
    syncCheckboxes();
    showCurrent();
  });

  $('btnRestyleRun').addEventListener('click', async () => {
    if (!selected.size) { alert('Выберите хотя бы одно изображение'); return; }
    rememberPrompt();
    const prompt = $('restylePrompt').value.trim();
    if (!prompt) { alert('Промпт пуст'); return; }
    $('btnRestyleRun').disabled = true;
    $('btnRestyleRun').textContent = 'Генерация…';
    $('restyleResultsCard').style.display = '';
    $('restyleResults').innerHTML = '<div class="text-secondary">Запускаем…</div>';
    try {
      const res = await fetch('/api/restyle/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, slugs: [...selected], prompt }),
      });
      if (!res.ok) throw new Error(await res.text());
      const job = await res.json();
      jobId = job.id;
      renderJob(job);
      loadHistory();
      poll();
    } catch (e) {
      alert('Ошибка: ' + e.message);
      $('btnRestyleRun').disabled = false;
      $('btnRestyleRun').textContent = 'Переснять выбранные';
    }
  });

  $('btnRestyleZip').addEventListener('click', () => {
    if (jobId) window.location.href = '/api/restyle/jobs/' + jobId + '/zip';
  });
  $('btnRestyleHistoryRefresh').addEventListener('click', loadHistory);

  loadCatalog().then(loadHistory).catch((e) => {
    $('restyleList').innerHTML = '<div class="p-4 text-danger">Не удалось загрузить каталог: ' + esc(e.message) + '</div>';
  });
})();
