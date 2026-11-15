(function () {
  const $ = (id) => document.getElementById(id);
  const jobId = window.location.pathname.split('/').pop();

  const STATUS_LABELS = {
    draft: 'Черновик', queued: 'В очереди', running: 'Генерация',
    done: 'Готово', error: 'Ошибка',
  };
  const STATUS_BADGE = {
    draft: 'bg-secondary-lt', queued: 'bg-yellow-lt', running: 'bg-blue-lt',
    done: 'bg-green-lt', error: 'bg-red-lt',
  };

  let currentJob = null;
  let pollTimer = null;
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

  async function loadJob() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    let job;
    try {
      const res = await fetch('/api/interior/jobs/' + jobId);
      if (!res.ok) { $('jobTitle').textContent = 'Задача не найдена'; return; }
      job = await res.json();
    } catch (e) {
      pollTimer = setTimeout(loadJob, 3000);
      return;
    }
    currentJob = job;
    renderHeader(job);
    renderSource(job);
    renderVariants(job);
    if (job.status === 'running' || job.status === 'queued') {
      pollTimer = setTimeout(loadJob, 3000);
    }
  }

  function renderHeader(job) {
    $('jobTitle').textContent = 'Задача #' + job.id;
    $('jobSubtitle').textContent = (job.source_name || '') + ' · ' + job.width + '×' + job.height;
    $('jobStatus').className = 'badge ' + (STATUS_BADGE[job.status] || 'bg-secondary-lt');
    $('jobStatus').textContent = STATUS_LABELS[job.status] || job.status;
    $('jobCounts').textContent = 'готово ' + job.done + '/' + job.total;
    $('jobEta').textContent = (job.eta != null && job.eta > 0 && job.status !== 'done')
      ? 'осталось ~' + fmtDuration(job.eta) : '';
    const pct = job.total ? Math.round(job.done / job.total * 100) : 0;
    $('progressFill').style.width = pct + '%';
    $('btnPause').textContent = job.paused ? 'Продолжить' : 'Пауза';
    $('btnZip').style.display = job.done > 0 ? '' : 'none';
    $('btnZip').href = '/api/interior/jobs/' + job.id + '/zip';
  }

  function renderSource(job) {
    const img = $('refImage');
    if (job.image_path) {
      img.src = '/api/thumb/' + job.image_path + '?size=600';
      img.dataset.full = '/api/output/' + job.image_path;
      img.style.display = '';
    } else {
      img.style.display = 'none';
    }
    $('userPrompt').textContent = job.user_prompt || '';
    $('llmInfo').textContent = 'LLM: ' + (job.llm_provider || '') +
      (job.llm_model ? ' · ' + job.llm_model : '') +
      (job.prompts_ready ? ' · промпты готовы' : ' · готовим промпты…');
    const err = $('llmError');
    if (job.llm_error) {
      err.style.display = '';
      err.textContent = 'LLM не ответила, использованы шаблонные варианты: ' + job.llm_error;
    } else {
      err.style.display = 'none';
    }
  }

  function renderVariants(job) {
    const list = $('variantsList');
    list.innerHTML = '';
    if (!job.prompts_ready) {
      list.innerHTML = '<div class="card"><div class="card-body text-secondary">' +
        'LLM готовит ' + job.count + ' промптов…</div></div>';
      return;
    }

    const html = job.variants.map((v) => {
      let content;
      if (v.status === 'done' && v.filename) {
        content = '<img class="card-img-top" src="/api/thumb/' + v.filename + '?size=600" ' +
          'alt="вариант" loading="lazy" data-full="/api/output/' + v.filename + '">';
      } else if (v.status === 'error') {
        content = '<div class="d-flex align-items-center justify-content-center text-danger p-3" ' +
          'style="height:220px;">⚠ ' + esc(v.error || 'ошибка') + '</div>';
      } else {
        content = '<div class="d-flex align-items-center justify-content-center text-secondary" ' +
          'style="height:220px;">' + (v.status === 'running' ? 'генерация…' : 'в очереди') + '</div>';
      }
      const regen = (v.status === 'done' || v.status === 'error')
        ? '<button class="btn btn-outline-secondary btn-sm" data-regen="' + v.id + '">' +
          '<i class="ti ti-refresh"></i> Ещё</button>' : '';
      return '<div class="col-md-6 col-xl-4"><div class="card h-100">' + content +
        '<div class="card-body d-flex flex-column gap-2">' +
        '<div class="small text-secondary" style="white-space:pre-wrap;">' + esc(v.prompt) + '</div>' +
        '<div class="d-flex justify-content-between align-items-center mt-auto">' +
        '<span class="text-secondary small">' + (v.elapsed ? v.elapsed + 'с' : '') + '</span>' +
        regen + '</div></div></div></div>';
    }).join('');
    list.innerHTML = '<div class="row row-cards">' + html + '</div>';

    list.querySelectorAll('img[data-full]').forEach(img => {
      img.addEventListener('click', () => {
        lightboxImages = Array.from(list.querySelectorAll('img[data-full]')).map(i => i.dataset.full);
        lightboxIndex = lightboxImages.indexOf(img.dataset.full);
        showLightbox();
      });
    });
    list.querySelectorAll('[data-regen]').forEach(btn => {
      btn.addEventListener('click', async () => {
        await fetch('/api/interior/jobs/' + jobId + '/variants/' + btn.dataset.regen + '/regenerate',
          { method: 'POST' });
        loadJob();
      });
    });
  }

  function showLightbox() {
    $('lightboxImg').src = lightboxImages[lightboxIndex];
    $('lightbox').classList.add('show');
  }
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

  $('refImage').addEventListener('click', () => {
    const full = $('refImage').dataset.full;
    if (!full) return;
    lightboxImages = [full];
    lightboxIndex = 0;
    showLightbox();
  });

  $('btnPause').addEventListener('click', async () => {
    const action = currentJob && currentJob.paused ? 'resume' : 'pause';
    await fetch('/api/interior/jobs/' + jobId + '/' + action, { method: 'POST' });
    loadJob();
  });

  $('btnDelete').addEventListener('click', async () => {
    if (!confirm('Удалить задачу вместе с изображениями?')) return;
    await fetch('/api/interior/jobs/' + jobId, { method: 'DELETE' });
    window.location.href = '/interior';
  });

  loadJob();
})();
