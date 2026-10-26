(function () {
  const $ = (id) => document.getElementById(id);
  const jobId = window.location.pathname.split('/').pop();

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  async function loadModels() {
    const models = await (await fetch('/api/models')).json();
    const box = $('modelOptions');
    box.innerHTML = '';
    models.forEach(m => {
      const label = document.createElement('label');
      label.className = 'form-check form-check-inline m-0 border px-2 py-1';
      label.innerHTML = '<input class="form-check-input" type="checkbox" name="model" value="' + m.id + '"> ' +
        '<span class="form-check-label">' + esc(m.label) + '</span>';
      box.appendChild(label);
    });
  }

  async function loadJob() {
    const res = await fetch('/api/jobs/' + jobId);
    if (!res.ok) {
      $('jobTitle').textContent = 'Задача не найдена';
      $('jobFile').textContent = 'Возможно, задача была удалена. Вернитесь к задачам.';
      return;
    }
    const job = await res.json();
    $('jobTitle').textContent = 'Задача #' + job.id;
    $('jobFile').textContent = job.source_name + ' — ' + job.items.length + ' промптов';
  }

  $('refMode').addEventListener('change', () => {
    $('strengthField').style.display = $('refMode').value === 'img2img' ? '' : 'none';
  });
  $('strength').addEventListener('input', () => {
    $('strengthVal').textContent = $('strength').value;
  });

  $('btnRun').addEventListener('click', async () => {
    const models = [...document.querySelectorAll('input[name=model]:checked')].map(c => c.value);
    if (!models.length) { alert('Выберите хотя бы одну модель'); return; }
    const variants = parseInt($('variants').value) || 1;
    const reference_mode = $('refMode').value;
    const strength = parseFloat($('strength').value) || 0.7;

    const res = await fetch('/api/jobs/' + jobId + '/configure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ models, variants, reference_mode, strength }),
    });
    if (!res.ok) { alert('Ошибка: ' + (await res.text())); return; }

    const runRes = await fetch('/api/jobs/' + jobId + '/run', { method: 'POST' });
    if (!runRes.ok) { alert('Ошибка: ' + (await runRes.text())); return; }
    window.location.href = '/result/' + jobId;
  });

  loadModels();
  loadJob();
})();
