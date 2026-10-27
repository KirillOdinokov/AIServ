(function () {
  const $ = (id) => document.getElementById(id);
  const form = $('refUploadForm');
  if (!form) return;
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const file = $('refCsvFile').files[0];
    if (!file) { alert('Выберите CSV файл'); return; }
    const data = new FormData();
    data.append('file', file);
    const res = await fetch('/api/ref/upload', { method: 'POST', body: data });
    if (!res.ok) { alert('Ошибка: ' + (await res.text())); return; }
    const out = await res.json();
    window.location.href = '/ref/map/' + out.upload_id;
  });
})();
