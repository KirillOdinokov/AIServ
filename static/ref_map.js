const $ = (id) => document.getElementById(id);
const uploadId = window.location.pathname.split('/').pop();

const EXPECTED_FIELDS = [
  { key: 'title', label: 'Название товара (post_title)', required: true },
  { key: 'id', label: 'ID товара' },
  { key: 'image', label: 'URL изображения (опционально)' },
  { key: 'type', label: 'Тип изображения (standart/storage/post_image/application/all_product)' },
];

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function load() {
  const res = await fetch('/api/ref/uploads/' + uploadId);
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

    let autoMatch = '';
    for (const h of up.headers) {
      const hl = h.toLowerCase();
      const opt = document.createElement('option');
      opt.value = h;
      opt.textContent = h;
      select.appendChild(opt);

      if (!autoMatch) {
        if (f.key === 'title' && /post_title|title|название|name/.test(hl)) autoMatch = h;
        else if (f.key === 'id' && /^(id|post_id|postid|sku)$/.test(hl)) autoMatch = h;
        else if (f.key === 'image' && /image|img|картинка|фото|url/.test(hl)) autoMatch = h;
        else if (f.key === 'type' && /type|тип/.test(hl)) autoMatch = h;
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

  if (!mapping.title) { alert('Выберите колонку с названием товара'); return; }

  const res = await fetch('/api/ref/uploads/' + uploadId + '/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mapping }),
  });
  if (!res.ok) { alert('Ошибка: ' + (await res.text())); return; }
  const data = await res.json();
  window.location.href = '/ref/configure/' + data.id;
});

load();
