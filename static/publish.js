const $ = (id) => document.getElementById(id);
const jobId = window.location.pathname.split('/').pop();

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
    insert_in_content: $('insertInContent').checked,
    replace_main: $('replaceMain').checked,
    img_class: $('imgClass').value.trim(),
  };
}

$('btnTest').addEventListener('click', async () => {
  $('testResult').textContent = 'Проверка...';
  const res = await fetch(`/api/publish/${jobId}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(collect()),
  });
  const data = await res.json();
  if (!data.ok) {
    $('testResult').textContent = 'Ошибка: ' + data.error;
    return;
  }
  const r = data.result;
  let msg = '';
  if (r.wp && r.wp.ok) msg += `WordPress OK (${esc(r.wp.name)}). `;
  else if (r.wp) msg += `WordPress: ${esc(r.wp.error)}. `;
  if (r.wc) {
    if (r.wc.ok) msg += 'WooCommerce OK.';
    else msg += `WooCommerce: ${esc(r.wc.error)}.`;
  }
  $('testResult').textContent = msg;
});

$('btnPublish').addEventListener('click', async () => {
  if (!confirm('Опубликовать все утверждённые изображения на сайт?')) return;
  $('btnPublish').disabled = true;
  $('publishProgress').textContent = 'Публикация...';
  $('publishResults').innerHTML = '';

  const res = await fetch(`/api/publish/${jobId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(collect()),
  });
  const data = await res.json();

  if (!data.ok && data.error) {
    $('btnPublish').disabled = false;
    $('publishProgress').textContent = 'Ошибка: ' + data.error;
    return;
  }

  // публикация идёт в фоне — опрашиваем статус
  const poll = async () => {
    try {
      const sr = await fetch(`/api/publish/${jobId}/status`);
      const st = await sr.json();
      if (st.running) {
        const pct = st.total > 0 ? Math.round(st.done / st.total * 100) : 0;
        $('publishProgress').textContent =
          `Публикация... ${st.done}/${st.total} (${pct}%) — успешно: ${st.ok_count}, ошибок: ${st.fail_count}, пропущено: ${st.skipped}`;
        setTimeout(poll, 2000);
      } else {
        $('btnPublish').disabled = false;
        $('publishProgress').textContent =
          `Готово: ${st.ok_count} успешно, ${st.fail_count} с ошибками, ${st.skipped} пропущено (всего ${st.total}).`;
      }
    } catch (e) {
      $('btnPublish').disabled = false;
    }
  };
  poll();
});
