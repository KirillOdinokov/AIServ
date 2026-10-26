const $ = (id) => document.getElementById(id);
const jobId = window.location.pathname.split('/').pop();

async function loadModels() {
  const res = await fetch('/api/models');
  const models = await res.json();
  const box = $('modelOptions');
  box.innerHTML = '';
  models.forEach(m => {
    const label = document.createElement('label');
    label.className = 'model-option';
    label.innerHTML = `<input type="checkbox" name="model" value="${m.id}"> <span>${esc(m.label)}</span>`;
    box.appendChild(label);
  });
}

async function loadJob() {
  const res = await fetch('/api/jobs/' + jobId);
  if (!res.ok) {
    $('jobTitle').textContent = 'Задача не найдена';
    $('jobFile').textContent = 'Возможно, задача была удалена. Вернитесь на главную.';
    return;
  }
  const job = await res.json();
  $('jobTitle').textContent = 'Задача #' + job.id;
  $('jobFile').textContent = job.source_name + ' — ' + job.items.length + ' промптов';
}

$('refMode').addEventListener('change', () => {
  const isImg = $('refMode').value === 'img2img';
  $('strengthField').style.display = isImg ? '' : 'none';
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

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

loadModels();
loadJob();
