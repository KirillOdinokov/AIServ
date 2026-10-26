(function () {
  const $ = (id) => document.getElementById(id);
  const jobId = window.location.pathname.split('/').pop();

  const STATUS_LABELS = {
    draft: 'Черновик', queued: 'В очереди', running: 'Генерация',
    done: 'Сгенерировано', error: 'Ошибка',
  };
  const STATUS_BADGE = {
    draft: 'bg-secondary-lt', queued: 'bg-yellow-lt', running: 'bg-blue-lt',
    done: 'bg-green-lt', error: 'bg-red-lt',
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
    const m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return m + 'м ' + s + 'с';
    const h = Math.floor(m / 60), mm = m % 60;
    return h + 'ч ' + mm + 'м';
  }
  function fmtDateTime(ts) {
    if (ts == null) return '';
    const d = new Date(ts * 1000);
    const pad = (n) => String(n).padStart(2, '0');
    return pad(d.getDate()) + '.' + pad(d.getMonth() + 1) + '.' + d.getFullYear() + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  async function loadJob() {
    const job = await (await fetch('/api/ref/jobs/' + jobId)).json();
    currentJob = job;
    itemCache = {};
    job.items.forEach((it, idx) => {
      itemCache[it.id] = { idx, title: it.title, output_name: it.output_name, prompt: it.prompt,
        source_path: it.source_path, source_site: it.source_site, source_fetched_at: it.source_fetched_at };
    });
    $('jobTitle').textContent = 'Задача #' + job.id + ': ' + job.source_name;
    renderJobHeader(job);
    renderItems(job);
    renderApproveBar(job);
    if (job.status === 'running' || job.status === 'queued') pollProgress();
  }

  async function pollProgress() {
    try {
      const [pr, vr] = await Promise.all([
        fetch('/api/ref/jobs/' + jobId + '/progress'),
        fetch('/api/ref/jobs/' + jobId + '/variants'),
      ]);
      const st = await pr.json();
      renderJobHeader({ ...st, source_name: currentJob.source_name });
      const vdata = await vr.json();
      renderItemsFromVariants(vdata.items);
      if (st.status === 'running' || st.status === 'queued') setTimeout(pollProgress, 3000);
      else loadJob();
    } catch (e) { setTimeout(pollProgress, 3000); }
  }

  function renderJobHeader(job) {
    $('jobStatus').className = 'badge ' + (STATUS_BADGE[job.status] || 'bg-secondary-lt');
    $('jobStatus').textContent = STATUS_LABELS[job.status] || job.status;
    $('jobCounts').textContent = 'сгенерировано ' + job.done + '/' + job.total;
    const eta = (job.status === 'running' || job.status === 'queued') ? fmtDuration(job.eta) : '';
    $('jobEta').textContent = eta ? 'осталось ~' + eta : '';
    const pct = job.total > 0 ? Math.round(job.done / job.total * 100) : 0;
    $('progressFill').style.width = pct + '%';
  }

  function renderApproveBar(job) {
    const approved = job.items.filter(it => it.approved_variant_id).length;
    $('approveCount').textContent = 'Выбрано изображений: ' + approved;
    $('btnApprove').style.display = approved > 0 ? '' : 'none';
  }

  function renderItems(job) {
    const list = $('itemsList');
    list.innerHTML = '';
    job.items.forEach((item, idx) => {
      list.appendChild(buildRow(idx, item.id, item.title, item.output_name, item.prompt,
        item.approved_variant_id, item.variants, item.source_path, item.source_site, item.source_fetched_at));
    });
    attachItemEvents(list);
  }

  function renderItemsFromVariants(items) {
    const list = $('itemsList');
    list.innerHTML = '';
    items.forEach((item) => {
      const meta = itemCache[item.id] || { title: item.id, output_name: item.id, prompt: '',
        source_path: item.source_path, source_site: item.source_site, source_fetched_at: item.source_fetched_at };
      list.appendChild(buildRow(meta.idx, item.id, meta.title, meta.output_name, meta.prompt,
        item.approved_variant_id, item.variants, meta.source_path, meta.source_site, meta.source_fetched_at));
    });
    attachItemEvents(list);
  }

  function buildRow(idx, itemId, title, outputName, prompt, approved, variants, sourcePath, sourceSite, sourceFetchedAt) {
    const row = document.createElement('div');
    row.className = 'card mb-3';
    row.dataset.item = itemId;

    const badge = approved
      ? '<span class="badge bg-green-lt">Утверждено</span>'
      : '<span class="badge bg-secondary-lt">Не выбрано</span>';

    let sourceHtml = '';
    if (sourcePath) {
      const siteLabel = sourceSite ? esc(sourceSite) : 'источник';
      const dt = fmtDateTime(sourceFetchedAt);
      sourceHtml = '<div class="col-6 col-md-4 col-xl-3"><div class="card h-100">' +
        '<img class="card-img-top" src="/api/thumb/' + sourcePath + '" alt="исходник" loading="lazy" data-full="/api/output/' + sourcePath + '">' +
        '<div class="card-body py-1"><span class="text-secondary small">Исходник · ' + siteLabel + (dt ? ' · ' + dt : '') + '</span></div>' +
        '</div></div>';
    }

    const variantsHtml = variants.map(v => {
      const isApproved = v.id === approved;
      let content;
      if (v.status === 'done' && v.filename) {
        content = '<img class="card-img-top" src="/api/thumb/' + v.filename + '" alt="вариант" loading="lazy" data-full="/api/output/' + v.filename + '">';
      } else if (v.status === 'error') {
        content = '<div class="d-flex align-items-center justify-content-center text-danger p-2" style="height:160px;">⚠ ' + esc(v.error || 'ошибка') + '</div>';
      } else {
        content = '<div class="d-flex align-items-center justify-content-center text-secondary" style="height:160px;">' + (v.status === 'running' ? 'генерация…' : 'в очереди') + '</div>';
      }
      const modeLabel = v.reference_mode || '';
      const approveBtn = v.status === 'done'
        ? '<button class="btn ' + (isApproved ? 'btn-success' : 'btn-outline-secondary') + ' btn-sm" data-approve="' + v.id + '">' + (isApproved ? '✓ Выбрано' : 'Выбрать') + '</button>'
        : '';
      return '<div class="col-6 col-md-4 col-xl-3"><div class="card h-100' + (isApproved ? ' border-primary' : '') + '">' +
        content +
        '<div class="card-body py-1 d-flex justify-content-between align-items-center gap-1">' +
        '<span class="text-secondary small">' + esc(v.model_label) + (modeLabel ? ' · ' + esc(modeLabel) : '') + (v.elapsed ? ' · ' + v.elapsed + 'с' : '') + '</span>' +
        approveBtn + '</div></div></div>';
    }).join('');

    row.innerHTML = '<div class="card-header d-flex flex-wrap justify-content-between align-items-center gap-2">' +
      '<div><div class="fw-bold">' + (idx + 1) + '. ' + esc(title || outputName || itemId) + '</div>' +
      '<div class="text-secondary small">' + esc(prompt) + '</div></div>' +
      '<div class="d-flex align-items-center gap-2">' + badge +
      '<select class="form-select form-select-sm" style="width:auto;" data-more-count>' +
      '<option value="1" selected>1</option><option value="2">2</option><option value="3">3</option><option value="4">4</option>' +
      '</select>' +
      '<button class="btn btn-outline-secondary btn-sm" data-more="' + itemId + '">+ Ещё</button></div></div>' +
      '<div class="card-body"><div class="row row-cards">' + sourceHtml + variantsHtml + '</div></div>';
    return row;
  }

  function attachItemEvents(list) {
    list.querySelectorAll('[data-approve]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const itemId = btn.closest('[data-item]').dataset.item;
        await fetch('/api/ref/jobs/' + jobId + '/items/' + itemId + '/approve', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ variant_id: btn.dataset.approve }),
        });
        loadJob();
      });
    });
    list.querySelectorAll('[data-more]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const card = btn.closest('[data-item]');
        const itemId = btn.dataset.more;
        const count = parseInt(card.querySelector('[data-more-count]').value) || 1;
        await fetch('/api/ref/jobs/' + jobId + '/items/' + itemId + '/regenerate', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ count }),
        });
        loadJob();
      });
    });
    list.querySelectorAll('img[data-full]').forEach(img => {
      img.addEventListener('click', () => {
        lightboxImages = Array.from(list.querySelectorAll('img[data-full]')).map(i => i.dataset.full);
        lightboxIndex = lightboxImages.indexOf(img.dataset.full);
        showLightbox();
      });
    });
  }

  function showLightbox() { $('lightboxImg').src = lightboxImages[lightboxIndex]; $('lightbox').classList.add('show'); }
  function hideLightbox() { $('lightbox').classList.remove('show'); }
  function lightboxStep(delta) {
    lightboxIndex = (lightboxIndex + delta + lightboxImages.length) % lightboxImages.length;
    $('lightboxImg').src = lightboxImages[lightboxIndex];
  }
  $('lightboxClose').addEventListener('click', hideLightbox);
  $('lightboxPrev').addEventListener('click', () => lightboxStep(-1));
  $('lightboxNext').addEventListener('click', () => lightboxStep(1));
  $('lightbox').addEventListener('click', (e) => { if (e.target === $('lightbox')) hideLightbox(); });
  document.addEventListener('keydown', (e) => {
    if (!$('lightbox').classList.contains('show')) return;
    if (e.key === 'Escape') hideLightbox();
    if (e.key === 'ArrowLeft') lightboxStep(-1);
    if (e.key === 'ArrowRight') lightboxStep(1);
  });

  $('btnApprove').addEventListener('click', async () => {
    const approved = currentJob.items.filter(it => it.approved_variant_id).length;
    if (!approved) return;
    if (!confirm('Сохранить ' + approved + ' изображений в папку images/ (png + webp)?')) return;
    $('btnApprove').disabled = true;
    $('btnApprove').textContent = 'Сохранение...';
    await fetch('/api/ref/jobs/' + jobId + '/finalize', { method: 'POST' });
    const fsEl = $('finalizeStatus');
    fsEl.style.display = '';
    const poll = async () => {
      const st = await (await fetch('/api/ref/jobs/' + jobId + '/finalize/status')).json();
      if (st.running) {
        const pct = st.total > 0 ? Math.round(st.done / st.total * 100) : 0;
        fsEl.textContent = 'Сохранение... ' + st.done + '/' + st.total + ' (' + pct + '%)';
        setTimeout(poll, 1000);
      } else {
        $('btnApprove').disabled = false;
        $('btnApprove').textContent = 'Сохранить выбранные';
        fsEl.textContent = 'Готово: PNG + WebP сохранены в images/.';
      }
    };
    poll();
  });

  $('btnSelectAll').addEventListener('click', async () => {
    const data = await (await fetch('/api/ref/jobs/' + jobId + '/approve-all', { method: 'POST' })).json();
    if (data.selected === 0) alert('Нет сгенерированных изображений для выбора.');
    loadJob();
  });

  loadJob();
})();
