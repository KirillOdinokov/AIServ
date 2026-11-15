(function () {
  const $ = (id) => document.getElementById(id);
  const jobId = window.location.pathname.split('/').pop();
  let hasNonClean = true;

  async function loadJob() {
    const res = await fetch('/api/ref/jobs/' + jobId);
    if (!res.ok) {
      $('jobTitle').textContent = 'Задача не найдена';
      return;
    }
    const job = await res.json();
    hasNonClean = job.items.some(it => it.ref_type !== 'clean');
    $('jobTitle').textContent = 'Задача #' + job.id;
    $('jobFile').textContent = job.source_name + ' — ' + job.items.length + ' строк';
    if (job.site_name) $('siteName').value = job.site_name;
    if (job.watermark) $('chkWatermark').checked = true;
    if (job.clean_mode) $('chkClean').checked = true;
    if (job.overlay_path) $('overlayStatus').textContent = 'Загружен водяной знак: ' + job.overlay_path;
  }

  function updateCopiesVisibility() {
    $('copiesMultimodal').style.display = $('modeMultimodal').checked ? '' : 'none';
  }
  $('modeMultimodal').addEventListener('change', updateCopiesVisibility);
  $('chkClean').addEventListener('change', () => {
    if ($('chkClean').checked) $('chkWatermark').checked = false;
  });

  $('btnRun').addEventListener('click', async () => {
    const modes = [];
    if ($('modeMultimodal').checked) modes.push('multimodal');
    if (!modes.length) { alert('Выберите режим генерации'); return; }

    const watermark = $('chkWatermark').checked;
    const cleanMode = $('chkClean').checked;
    const siteName = $('siteName').value.trim();
    if (!watermark && !cleanMode && !siteName && hasNonClean) { alert('Введите название сайта (или включите Clean / Watermark)'); return; }

    const copies = {};
    if ($('modeMultimodal').checked) copies.multimodal = parseInt($('copiesKlein').value) || 1;

    const overlayInput = $('overlayFile');
    if (overlayInput && overlayInput.files && overlayInput.files.length > 0) {
      const form = new FormData();
      form.append('file', overlayInput.files[0]);
      const upRes = await fetch('/api/ref/jobs/' + jobId + '/overlay', { method: 'POST', body: form });
      if (!upRes.ok) { alert('Ошибка загрузки водяного знака: ' + (await upRes.text())); return; }
    }

    const res = await fetch('/api/ref/jobs/' + jobId + '/configure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ site_name: siteName, modes, copies, watermark, clean_mode: cleanMode }),
    });
    if (!res.ok) { alert('Ошибка: ' + (await res.text())); return; }

    const runRes = await fetch('/api/ref/jobs/' + jobId + '/run', { method: 'POST' });
    if (!runRes.ok) { alert('Ошибка: ' + (await runRes.text())); return; }
    window.location.href = '/ref/result/' + jobId;
  });

  loadJob();
})();
