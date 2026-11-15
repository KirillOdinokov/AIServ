(function () {
  const $ = (id) => document.getElementById(id);
  const srcInput = $('interiorFile');
  const preview = $('interiorPreview');
  const form = $('interiorForm');

  const STATUS_LABELS = {
    draft: 'Черновик', queued: 'В очереди', running: 'Генерация',
    done: 'Готово', error: 'Ошибка',
  };
  const STATUS_BADGE = {
    draft: 'bg-secondary-lt', queued: 'bg-yellow-lt', running: 'bg-blue-lt',
    done: 'bg-green-lt', error: 'bg-red-lt',
  };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  srcInput.addEventListener('change', () => {
    const f = srcInput.files[0];
    if (!f) { preview.style.display = 'none'; preview.src = ''; return; }
    preview.src = URL.createObjectURL(f);
    preview.style.display = 'block';
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const file = srcInput.files[0];
    const prompt = $('interiorPrompt').value.trim();
    if (!file) { alert('Выберите фото интерьера'); return; }
    if (!prompt) { alert('Опишите, что нужно сделать'); return; }

    const fd = new FormData();
    fd.append('file', file);
    fd.append('prompt', prompt);
    fd.append('count', $('interiorCount').value || '4');
    fd.append('provider', $('interiorProvider').value);
    fd.append('model_name', $('interiorModel').value.trim());
    fd.append('size', $('interiorSize').value);

    const btn = $('interiorSubmit');
    const st = $('interiorStatus');
    btn.disabled = true;
    st.textContent = 'Загрузка и постановка в очередь…';
    try {
      const res = await fetch('/api/interior/jobs', { method: 'POST', body: fd });
      if (!res.ok) {
        st.textContent = '';
        alert('Ошибка: ' + (await res.text()));
        return;
      }
      const data = await res.json();
      window.location.href = '/interior/result/' + data.id;
    } catch (err) {
      st.textContent = '';
      alert('Ошибка сети: ' + err);
    } finally {
      btn.disabled = false;
    }
  });

  async function loadJobs() {
    let jobs = [];
    try {
      jobs = await (await fetch('/api/interior/jobs')).json();
    } catch (e) { return; }
    const body = $('interiorJobsBody');
    body.innerHTML = '';
    if (!jobs.length) {
      body.innerHTML = '<tr><td colspan="4" class="text-secondary text-center py-4">Задач пока нет</td></tr>';
      return;
    }
    for (const j of jobs) {
      const tr = document.createElement('tr');
      const total = j.total || 0;
      const done = j.done || 0;
      tr.innerHTML = '<td><a href="/interior/result/' + j.id + '">#' + j.id + '</a>' +
        '<div class="text-secondary small">' + esc(j.source_name || '') + '</div></td>' +
        '<td>' + done + '/' + total + '</td>' +
        '<td><span class="badge ' + (STATUS_BADGE[j.status] || 'bg-secondary-lt') + '">' +
        (STATUS_LABELS[j.status] || j.status) + '</span>' +
        (j.paused ? ' <span class="badge bg-yellow-lt">пауза</span>' : '') + '</td>' +
        '<td class="text-end"><button class="btn btn-outline-danger btn-sm" data-del="' + j.id + '">' +
        '<i class="ti ti-trash"></i></button></td>';
      body.appendChild(tr);
    }
    body.querySelectorAll('[data-del]').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm('Удалить задачу вместе с изображениями?')) return;
        await fetch('/api/interior/jobs/' + btn.dataset.del, { method: 'DELETE' });
        loadJobs();
      });
    });
  }

  loadJobs();
})();
