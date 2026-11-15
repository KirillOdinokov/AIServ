(function () {
  const $ = (id) => document.getElementById(id);
  const jobId = window.location.pathname.split('/').pop();

  const STATUS_LABELS = {
    draft: 'Черновик', queued: 'В очереди', running: 'Генерация',
    done: 'Сгенерировано', error: 'Ошибка', stopped: 'Остановлено',
  };
  const STATUS_BADGE = {
    draft: 'bg-secondary-lt', queued: 'bg-yellow-lt', running: 'bg-blue-lt',
    done: 'bg-green-lt', error: 'bg-red-lt', stopped: 'bg-orange-lt',
  };

  let currentJob = null;
  let itemCache = {};
  let lightboxImages = [];
  let lightboxIndex = 0;
  let page = 0;
  let pageSize = 100;
  const IMG_STYLE = 'height:180px;width:100%;object-fit:contain;display:block;background:#f8f9fa;';

  const lazyObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) {
        const img = e.target;
        img.src = img.dataset.src;
        img.removeAttribute('data-src');
        lazyObserver.unobserve(img);
      }
    }
  }, { rootMargin: '400px 0px' });

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

  function renderActions(job) {
    const active = job.status === 'running' || job.status === 'queued';
    const canStart = job.paused || job.status === 'stopped' ||
      (job.status === 'draft' && (job.modes || []).length > 0);
    let html = '<div class="btn-list flex-nowrap">';
    if (canStart) {
      html += '<button class="btn btn-sm btn-success" data-job-action="resume"><i class="ti ti-player-play"></i> Старт</button>';
    } else if (active) {
      html += '<button class="btn btn-sm btn-outline-secondary" data-job-action="pause"><i class="ti ti-player-pause"></i> Пауза</button>';
    }
    if (active || job.paused) {
      html += '<button class="btn btn-sm btn-outline-danger" data-job-action="stop"><i class="ti ti-player-stop"></i> Стоп</button>';
    }
    html += '</div>';
    $('jobActions').innerHTML = html;
  }

  $('jobActions').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-job-action]');
    if (!btn) return;
    const action = btn.dataset.jobAction;
    if (action === 'stop' && !confirm('Остановить задачу? Текущая генерация будет прервана.')) return;
    btn.disabled = true;
    try {
      if (action === 'pause') {
        currentJob.paused = true;
        currentJob.status = 'queued';
        renderActions(currentJob);
        renderJobHeader(currentJob);
        await fetch('/api/ref/jobs/' + jobId + '/pause', { method: 'POST' });
      } else if (action === 'resume') {
        currentJob.paused = false;
        currentJob.status = 'queued';
        renderActions(currentJob);
        renderJobHeader(currentJob);
        await fetch('/api/ref/jobs/' + jobId + '/resume', { method: 'POST' });
        pollProgress();
      } else if (action === 'stop') {
        currentJob.status = 'stopped';
        currentJob.paused = false;
        renderActions(currentJob);
        renderJobHeader(currentJob);
        await fetch('/api/ref/jobs/' + jobId + '/stop', { method: 'POST' });
        loadJob();
      }
    } catch (err) {
      loadJob();
    }
  });

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
    renderActions(job);
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
      renderActions({ ...st, paused: st.paused });
      const vdata = await vr.json();
      renderItemsFromVariants(vdata.items);
      renderApproveBar(currentJob);
      if (st.status === 'running' || st.status === 'queued') setTimeout(pollProgress, 3000);
      else loadJob();
    } catch (e) { setTimeout(pollProgress, 3000); }
  }

  function renderJobHeader(job) {
    const paused = !!job.paused;
    $('jobStatus').className = 'badge ' + (paused ? 'bg-orange-lt' : (STATUS_BADGE[job.status] || 'bg-secondary-lt'));
    $('jobStatus').textContent = paused ? 'Пауза' : (STATUS_LABELS[job.status] || job.status);
    $('jobCounts').textContent = 'сгенерировано ' + job.done + '/' + job.total;
    const eta = (!paused && (job.status === 'running' || job.status === 'queued')) ? fmtDuration(job.eta) : '';
    $('jobEta').textContent = eta ? 'осталось ~' + eta : '';
    const pct = job.total > 0 ? Math.round(job.done / job.total * 100) : 0;
    $('progressFill').style.width = pct + '%';
  }

  function renderApproveBar(job) {
    const approved = job.items.filter(it => it.approved_variant_id || it.approved_source).length;
    $('approveCount').textContent = 'Выбрано изображений: ' + approved;
    $('btnApprove').style.display = approved > 0 ? '' : 'none';
  }

  function renderPager(total) {
    const pages = Math.max(1, Math.ceil(total / pageSize));
    if (page > pages - 1) page = pages - 1;
    const card = $('pagerCard');
    card.style.display = total > pageSize ? '' : 'none';
    const sel = $('pageSelect');
    const html = [];
    for (let i = 1; i <= pages; i++) {
      html.push('<option value="' + i + '"' + (i - 1 === page ? ' selected' : '') + '>Страница ' + i + '</option>');
    }
    sel.innerHTML = html.join('');
    $('pageInfo').textContent = 'из ' + pages + ' · всего ' + total;
    $('btnPrevPage').disabled = page <= 0;
    $('btnNextPage').disabled = page >= pages - 1;
  }

  function goToPage(nextPage) {
    const total = currentJob ? currentJob.items.length : 0;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    page = Math.max(0, Math.min(pages - 1, nextPage));
    if (currentJob) renderItems(currentJob);
    $('itemsList').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function itemSignature(item) {
    return JSON.stringify({
      a: item.approved_variant_id || null,
      s: !!item.approved_source,
      v: (item.variants || []).map(v => [v.id, v.status, v.filename, v.error, v.elapsed]),
    });
  }

  function syncItemFromVariantData(item, data) {
    if (!item) return;
    item.approved_variant_id = data.approved_variant_id || null;
    item.approved_source = !!data.approved_source;
    item.variants = data.variants || [];
  }

  function renderItems(job) {
    const list = $('itemsList');
    list.innerHTML = '';
    updateItemsFromVariants(job.items);
  }

  function renderItemsFromVariants(items) {
    updateItemsFromVariants(items);
  }

  /**
   * Обновляем только изменившиеся строки. Полная перерисовка каждые 3 секунды
   * сбрасывала ленивую загрузку и «дёргала» сетку при выборе изображений.
   */
  function updateItemsFromVariants(items) {
    const list = $('itemsList');
    const total = items.length;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    if (page > pages - 1) page = pages - 1;
    const start = page * pageSize;
    const visible = items.slice(start, start + pageSize);
    const existing = new Map();
    Array.from(list.children).forEach(row => {
      if (row.dataset && row.dataset.item) existing.set(row.dataset.item, row);
    });
    const seen = new Set();

    visible.forEach(item => {
      const meta = itemCache[item.id] || { idx: 0, title: item.id, output_name: item.id, prompt: '',
        source_path: item.source_path, source_site: item.source_site, source_fetched_at: item.source_fetched_at };
      const sig = itemSignature(item);
      let row = existing.get(item.id);

      if (!row || row.dataset.sig !== sig) {
        const newRow = buildRow(meta.idx, item.id, meta.title, meta.output_name, meta.prompt,
          item.approved_variant_id, item.approved_source, item.variants,
          meta.source_path, meta.source_site, meta.source_fetched_at);
        newRow.dataset.sig = sig;
        if (row) row.replaceWith(newRow);
        else list.appendChild(newRow);
        row = newRow;
        row.querySelectorAll('img[data-src]').forEach(img => lazyObserver.observe(img));
      }
      seen.add(item.id);

      const current = currentJob && currentJob.items.find(it => it.id === item.id);
      if (current) syncItemFromVariantData(current, item);
    });

    Array.from(list.children).forEach(row => {
      if (row.dataset && row.dataset.item && !seen.has(row.dataset.item)) row.remove();
    });
    renderPager(total);
  }

  function buildRow(idx, itemId, title, outputName, prompt, approved, approvedSource, variants, sourcePath, sourceSite, sourceFetchedAt) {
    const row = document.createElement('div');
    row.className = 'card mb-3';
    row.dataset.item = itemId;

    const badge = (approved || approvedSource)
      ? '<span class="badge bg-green-lt">Утверждено</span>'
      : '<span class="badge bg-secondary-lt">Не выбрано</span>';

    let sourceHtml = '';
    if (sourcePath) {
      const siteLabel = sourceSite ? esc(sourceSite) : 'источник';
      const dt = fmtDateTime(sourceFetchedAt);
      sourceHtml = '<div class="col-6 col-md-4 col-xl-3"><div class="card h-100' + (approvedSource ? ' border-primary' : '') + '">' +
        '<img class="card-img-top" style="' + IMG_STYLE + '" data-src="/api/thumb/' + encodeURI(sourcePath) + '" alt="исходник" data-full="/api/output/' + encodeURI(sourcePath) + '">' +
        '<div class="card-body py-1 d-flex justify-content-between align-items-center gap-1">' +
        '<span class="text-secondary small">Исходник · ' + siteLabel + (dt ? ' · ' + dt : '') + '</span>' +
        '<button class="btn ' + (approvedSource ? 'btn-success' : 'btn-outline-secondary') + ' btn-sm" data-approve-source="1">' +
        (approvedSource ? '✓ Исходник' : 'Взять исходник') + '</button>' +
        '</div></div></div>';
    }

    const variantsHtml = variants.map(v => {
      const isApproved = v.id === approved;
      let content;
      if (v.status === 'done' && v.filename) {
        content = '<img class="card-img-top" style="' + IMG_STYLE + '" data-src="/api/thumb/' + encodeURI(v.filename) + '" alt="вариант" data-full="/api/output/' + encodeURI(v.filename) + '">';
      } else if (v.status === 'error') {
        content = '<div class="d-flex align-items-center justify-content-center text-danger p-2" style="height:180px;">⚠ ' + esc(v.error || 'ошибка') + '</div>';
      } else {
        content = '<div class="d-flex align-items-center justify-content-center text-secondary" style="height:180px;">' + (v.status === 'running' ? 'генерация…' : 'в очереди') + '</div>';
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

  function resetApproval(row) {
    row.querySelectorAll('[data-approve]').forEach(b => {
      b.classList.remove('btn-success');
      b.classList.add('btn-outline-secondary');
      b.textContent = 'Выбрать';
    });
    row.querySelectorAll('[data-approve-source]').forEach(b => {
      b.classList.remove('btn-success');
      b.classList.add('btn-outline-secondary');
      b.textContent = 'Взять исходник';
    });
    row.querySelectorAll('.card').forEach(c => c.classList.remove('border-primary'));
    const badge = row.querySelector('.card-header .badge');
    if (badge) {
      badge.className = 'badge bg-secondary-lt';
      badge.textContent = 'Не выбрано';
    }
  }

  function markApproved(row) {
    const badge = row.querySelector('.card-header .badge');
    if (badge) {
      badge.className = 'badge bg-green-lt';
      badge.textContent = 'Утверждено';
    }
  }

  function attachItemEvents(list) {
    list.querySelectorAll('[data-approve]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const row = btn.closest('[data-item]');
        const itemId = row.dataset.item;
        const variantId = btn.dataset.approve;
        const item = currentJob.items.find(it => it.id === itemId);
        if (!item) return;
        const wasApproved = item.approved_variant_id === variantId;

        // Оптимистичное обновление: кнопка становится зелёной сразу, не дожидаясь ответа.
        resetApproval(row);
        item.approved_variant_id = wasApproved ? null : variantId;
        item.approved_source = false;
        if (!wasApproved) {
          btn.classList.remove('btn-outline-secondary');
          btn.classList.add('btn-success');
          btn.textContent = '✓ Выбрано';
          btn.closest('.card').classList.add('border-primary');
          markApproved(row);
        }
        renderApproveBar(currentJob);

        try {
          const res = await fetch('/api/ref/jobs/' + jobId + '/items/' + itemId + '/approve', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ variant_id: variantId }),
          });
          if (!res.ok) throw new Error('approve failed');
          loadJob();
        } catch (e) { loadJob(); }
      });
    });

    list.querySelectorAll('[data-approve-source]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const row = btn.closest('[data-item]');
        const itemId = row.dataset.item;
        const item = currentJob.items.find(it => it.id === itemId);
        if (!item) return;

        resetApproval(row);
        item.approved_source = !item.approved_source;
        item.approved_variant_id = null;
        if (item.approved_source) {
          btn.classList.remove('btn-outline-secondary');
          btn.classList.add('btn-success');
          btn.textContent = '✓ Исходник';
          btn.closest('.card').classList.add('border-primary');
          markApproved(row);
        }
        renderApproveBar(currentJob);

        try {
          const res = await fetch('/api/ref/jobs/' + jobId + '/items/' + itemId + '/approve', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source: true }),
          });
          if (!res.ok) throw new Error('approve source failed');
          loadJob();
        } catch (e) { loadJob(); }
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
    list.querySelectorAll('img[data-src]').forEach(img => lazyObserver.observe(img));
  }

  async function refreshVariants() {
    try {
      const res = await fetch('/api/ref/jobs/' + jobId + '/variants');
      if (!res.ok) return;
      const d = await res.json();
      renderItemsFromVariants(d.items || []);
      renderApproveBar(currentJob);
    } catch (e) {}
  }

  // Делегированные обработчики: строки больше не пересоздаются целиком при каждом опросе.
  $('itemsList').addEventListener('click', async (e) => {
    const img = e.target.closest('img[data-full]');
    if (img) {
      lightboxImages = Array.from($('itemsList').querySelectorAll('img[data-full]')).map(i => i.dataset.full);
      lightboxIndex = lightboxImages.indexOf(img.dataset.full);
      showLightbox();
      return;
    }

    const approveBtn = e.target.closest('[data-approve]');
    if (approveBtn) {
      const row = approveBtn.closest('[data-item]');
      const itemId = row.dataset.item;
      const variantId = approveBtn.dataset.approve;
      const item = currentJob.items.find(it => it.id === itemId);
      if (!item) return;
      const wasApproved = item.approved_variant_id === variantId;

      resetApproval(row);
      item.approved_variant_id = wasApproved ? null : variantId;
      item.approved_source = false;
      if (!wasApproved) {
        approveBtn.classList.remove('btn-outline-secondary');
        approveBtn.classList.add('btn-success');
        approveBtn.textContent = '✓ Выбрано';
        approveBtn.closest('.card').classList.add('border-primary');
        markApproved(row);
      }
      row.dataset.sig = itemSignature(item);
      renderApproveBar(currentJob);

      try {
        const res = await fetch('/api/ref/jobs/' + jobId + '/items/' + itemId + '/approve', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ variant_id: variantId }),
        });
        if (!res.ok) throw new Error('approve failed');
      } catch (err) { refreshVariants(); }
      return;
    }

    const sourceBtn = e.target.closest('[data-approve-source]');
    if (sourceBtn) {
      const row = sourceBtn.closest('[data-item]');
      const itemId = row.dataset.item;
      const item = currentJob.items.find(it => it.id === itemId);
      if (!item) return;

      resetApproval(row);
      item.approved_source = !item.approved_source;
      item.approved_variant_id = null;
      if (item.approved_source) {
        sourceBtn.classList.remove('btn-outline-secondary');
        sourceBtn.classList.add('btn-success');
        sourceBtn.textContent = '✓ Исходник';
        sourceBtn.closest('.card').classList.add('border-primary');
        markApproved(row);
      }
      row.dataset.sig = itemSignature(item);
      renderApproveBar(currentJob);

      try {
        const res = await fetch('/api/ref/jobs/' + jobId + '/items/' + itemId + '/approve', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source: true }),
        });
        if (!res.ok) throw new Error('approve source failed');
      } catch (err) { refreshVariants(); }
      return;
    }

    const moreBtn = e.target.closest('[data-more]');
    if (moreBtn) {
      const row = moreBtn.closest('[data-item]');
      const itemId = moreBtn.dataset.more;
      const count = parseInt(row.querySelector('[data-more-count]').value) || 1;
      moreBtn.disabled = true;
      await fetch('/api/ref/jobs/' + jobId + '/items/' + itemId + '/regenerate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ count }),
      });
      await refreshVariants();
      moreBtn.disabled = false;
    }
  });

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
    const approved = currentJob.items.filter(it => it.approved_variant_id || it.approved_source).length;
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
    await refreshVariants();
  });

  $('btnPrevPage').addEventListener('click', () => goToPage(page - 1));
  $('btnNextPage').addEventListener('click', () => goToPage(page + 1));
  $('pageSelect').addEventListener('change', () => goToPage((parseInt($('pageSelect').value) || 1) - 1));
  $('pageSize').addEventListener('change', () => {
    pageSize = parseInt($('pageSize').value) || 100;
    page = 0;
    if (currentJob) renderItems(currentJob);
  });

  loadJob();
})();
