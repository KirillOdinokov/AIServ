const $ = (id) => document.getElementById(id);

const STATUS_LABELS = {
  pending: 'Ожидает',
  running: 'Генерация',
  done: 'Готово',
  skipped: 'Пропущено',
  error: 'Ошибка',
};

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function collect() {
  return {
    site: $('site').value.trim(),
    wp_user: $('wpUser').value.trim(),
    wp_app_password: $('wpPassword').value.trim(),
    wc_key: $('wcKey').value.trim(),
    wc_secret: $('wcSecret').value.trim(),
  };
}

let jobId = null;
let pollTimer = null;

function itemStatusBadge(st) {
  const cls = st === 'done' ? 'done' : st === 'error' ? 'error'
    : st === 'running' ? 'running' : st === 'skipped' ? 'queued' : 'queued';
  return `<span class="badge badge--${cls}">${STATUS_LABELS[st] || st}</span>`;
}

function renderCategories(items) {
  const body = $('catBody');
  body.innerHTML = '';
  for (const it of items) {
    const tr = document.createElement('tr');
    const img = it.product_image_url
      ? `<img src="${esc(it.product_image_url)}" class="cat-preview" loading="lazy" alt="">`
      : '<span class="meta">—</span>';
    const err = it.error ? `<div class="meta" style="color:#dc3545">${esc(it.error)}</div>` : '';
    tr.innerHTML = `
      <td><strong>${esc(it.category_name)}</strong> <span class="meta">#${it.category_id}</span></td>
      <td>${img}<div class="meta">${esc(it.product_name || '')}</div></td>
      <td>${itemStatusBadge(it.status)}${err}</td>
    `;
    body.appendChild(tr);
  }
}

$('btnFetch').addEventListener('click', async () => {
  const cred = collect();
  if (!cred.site) { alert('Введите адрес сайта'); return; }
  if (!cred.wc_key || !cred.wc_secret) { alert('Укажите WooCommerce Consumer Key / Secret'); return; }
  if (!cred.wp_user || !cred.wp_app_password) { alert('Укажите WordPress логин / Application Password'); return; }

  $('btnFetch').disabled = true;
  $('fetchStatus').textContent = 'Получаем категории...';

  const res = await fetch('/api/thumbs/fetch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cred),
  });
  if (!res.ok) {
    $('btnFetch').disabled = false;
    $('fetchStatus').textContent = 'Ошибка: ' + (await res.text());
    return;
  }
  const data = await res.json();
  jobId = data.id;

  // опрашиваем статус получения категорий
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/thumbs/' + jobId + '/progress');
      const st = await r.json();
      if (st.status === 'ready') {
        clearInterval(pollTimer);
        $('btnFetch').disabled = false;
        $('fetchStatus').textContent = 'Категории получены.';
        await loadJob();
      } else if (st.status === 'error') {
        clearInterval(pollTimer);
        $('btnFetch').disabled = false;
        $('fetchStatus').textContent = 'Ошибка: ' + (st.error || '');
      }
    } catch (e) {}
  }, 1500);
});

async function loadJob() {
  const res = await fetch('/api/thumbs/' + jobId);
  if (!res.ok) return;
  const job = await res.json();
  $('catCount').textContent = job.items.length + ' шт.';
  renderCategories(job.items);
  $('categoriesCard').style.display = '';
}

$('btnRun').addEventListener('click', async () => {
  if (!jobId) return;
  const cred = collect();
  if (!cred.wc_key || !cred.wc_secret) { alert('Укажите WooCommerce Consumer Key / Secret'); return; }
  if (!cred.wp_user || !cred.wp_app_password) { alert('Укажите WordPress логин / Application Password'); return; }

  if (!confirm('Сгенерировать и загрузить миниатюры для всех категорий?')) return;
  $('btnRun').disabled = true;

  const res = await fetch('/api/thumbs/' + jobId + '/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cred),
  });
  if (!res.ok) {
    $('btnRun').disabled = false;
    $('runStatus').textContent = 'Ошибка: ' + (await res.text());
    return;
  }

  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/thumbs/' + jobId + '/progress');
      const st = await r.json();
      const pct = st.total > 0 ? Math.round(st.done / st.total * 100) : 0;
      if (st.status === 'done' || st.status === 'error') {
        clearInterval(pollTimer);
        $('btnRun').disabled = false;
        $('runStatus').textContent = `Готово: ${st.done}/${st.total} категорий обработано.`;
      } else {
        $('runStatus').textContent = `Обработка... ${st.done}/${st.total} (${pct}%)`;
      }
      await loadJob();
    } catch (e) {}
  }, 2000);
});
