(function () {
  const $ = (id) => document.getElementById(id);
  const uploadId = window.location.pathname.split('/').pop();

  const EXPECTED_FIELDS = [
    { key: 'title', label: 'Название товара (post_title)', required: true },
    { key: 'id', label: 'ID товара' },
    { key: 'image', label: 'URL изображения (опционально)' },
    { key: 'type', label: 'Тип изображения (standart/storage/post_image/application/clean/all_product)' },
  ];

  const MANUAL_TYPES = [
    { value: 'standart', label: 'Стандарт — товар на белом фоне' },
    { value: 'storage', label: 'Склад — товар на стеллаже' },
    { value: 'post_image', label: 'Изображение для записи (21:9)' },
    { value: 'application', label: 'Применение — строители' },
    { value: 'clean', label: 'Clean — белый фон 1:1, без надписей и водяных знаков' },
    { value: 'all_product', label: 'all_product — сразу storage + standart' },
  ];

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  async function load() {
    const up = await (await fetch('/api/ref/uploads/' + uploadId)).json();
    $('mapTitle').textContent = 'Файл: ' + up.headers.length + ' колонок, ' + up.count + ' строк';

    const box = $('fields');
    box.innerHTML = '';
    for (const f of EXPECTED_FIELDS) {
      const div = document.createElement('div');
      div.className = 'mb-3';

      const select = document.createElement('select');
      select.className = 'form-select';
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

      if (f.key === 'type') {
        const manualOpt = document.createElement('option');
        manualOpt.value = '__manual__';
        manualOpt.textContent = 'Ручной выбор (несколько типов)';
        select.appendChild(manualOpt);
      }

      if (autoMatch) select.value = autoMatch;

      div.innerHTML = '<label class="form-label">' + esc(f.label) + (f.required ? ' <span class="text-danger">*</span>' : '') + '</label>';
      div.appendChild(select);

      if (f.key === 'type') {
        const manualBox = document.createElement('div');
        manualBox.id = 'manualTypes';
        manualBox.className = 'mt-2 p-3 border rounded bg-light';
        manualBox.style.display = 'none';
        manualBox.innerHTML = '<div class="fw-bold mb-2">Выберите один или несколько типов:</div>' +
          MANUAL_TYPES.map(t =>
            '<label class="form-check">' +
            '<input class="form-check-input" type="checkbox" name="manual_type" value="' + esc(t.value) + '">' +
            '<span class="form-check-label">' + esc(t.label) + '</span>' +
            '</label>'
          ).join('') +
          '<div class="form-hint mt-2">Выбранные типы будут применены ко всем строкам CSV. ' +
          'Колонка type в файле при этом не нужна.</div>';
        div.appendChild(manualBox);
        select.addEventListener('change', () => {
          manualBox.style.display = select.value === '__manual__' ? '' : 'none';
        });
      }

      box.appendChild(div);
    }
  }

  $('btnCreate').addEventListener('click', async () => {
    const mapping = {};
    document.querySelectorAll('select[data-key]').forEach(s => {
      if (s.value && s.value !== '__manual__') mapping[s.dataset.key] = s.value;
    });
    if (!mapping.title) { alert('Выберите колонку с названием товара'); return; }

    const typeSelect = document.querySelector('select[data-key="type"]');
    let manualTypes = [];
    if (typeSelect && typeSelect.value === '__manual__') {
      manualTypes = [...document.querySelectorAll('input[name=manual_type]:checked')].map(c => c.value);
      if (!manualTypes.length) { alert('Выберите хотя бы один тип изображения'); return; }
    }

    const res = await fetch('/api/ref/uploads/' + uploadId + '/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mapping, manual_types: manualTypes }),
    });
    if (!res.ok) { alert('Ошибка: ' + (await res.text())); return; }
    const data = await res.json();
    window.location.href = '/ref/configure/' + data.id;
  });

  load();
})();
