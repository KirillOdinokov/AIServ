const $ = (id) => document.getElementById(id);
const uploadId = window.location.pathname.split('/').pop();

const EXPECTED_FIELDS = [
  { key: 'prompt', label: 'Промпт (обязательно)', required: true },
  { key: 'filename', label: 'Имя файла / ID' },
  { key: 'aspect_ratio', label: 'Соотношение сторон (1:1, 16:9…)' },
];

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function load() {
  const res = await fetch('/api/uploads/' + uploadId);
  const up = await res.json();
  $('mapTitle').textContent = 'Файл: ' + up.headers.length + ' колонок, ' + up.count + ' строк';

  const box = $('fields');
  box.innerHTML = '';

  for (const f of EXPECTED_FIELDS) {
    const div = document.createElement('div');
    div.className = 'field';

    const select = document.createElement('select');
    select.className = 'select';
    select.dataset.key = f.key;
    select.innerHTML = '<option value="">— не сопоставлено —</option>';

    // Авто-подбор: ищем колонку с похожим именем
    let autoMatch = '';
    for (const h of up.headers) {
      const hl = h.toLowerCase();
      const opt = document.createElement('option');
      opt.value = h;
      opt.textContent = h;
      select.appendChild(opt);

      if (!autoMatch) {
        if (f.key === 'prompt' && /prompt|promt|text|описание|промпт/.test(hl)) autoMatch = h;
        else if (f.key === 'filename' && /^(id|post_id|postid|filename|name)$/.test(hl)) autoMatch = h;
        else if (f.key === 'aspect_ratio' && /ratio|aspect|соотнош/.test(hl)) autoMatch = h;
      }
    }
    if (autoMatch) select.value = autoMatch;

    div.innerHTML = `<label>${esc(f.label)}${f.required ? ' <span style="color:#dc3545">*</span>' : ''}</label>`;
    div.appendChild(select);
    box.appendChild(div);
  }
}

$('btnCreate').addEventListener('click', async () => {
  const mapping = {};
  document.querySelectorAll('select[data-key]').forEach(s => {
    if (s.value) mapping[s.dataset.key] = s.value;
  });

  if (!mapping.prompt) { alert('Выберите колонку с промптом'); return; }

  const form = new FormData();
  form.append('mapping', JSON.stringify(mapping));
  const logoInput = $('logoFile');
  if (logoInput && logoInput.files && logoInput.files.length > 0) {
    form.append('logo', logoInput.files[0]);
  }

  const res = await fetch('/api/uploads/' + uploadId + '/create', {
    method: 'POST',
    body: form,
  });
  if (!res.ok) { alert('Ошибка: ' + (await res.text())); return; }
  const data = await res.json();
  window.location.href = '/configure/' + data.id;
});

load();
