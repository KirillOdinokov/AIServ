const $ = (id) => document.getElementById(id);
let currentPath = '';

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function fmtSize(bytes) {
  if (bytes == null) return '';
  if (bytes < 1024) return bytes + ' Б';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' КБ';
  if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' МБ';
  return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' ГБ';
}

function joinPath(base, name) {
  return base ? base + '/' + name : name;
}

function renderBreadcrumb() {
  const bc = $('breadcrumb');
  bc.innerHTML = '';
  const root = document.createElement('a');
  root.href = '#';
  root.textContent = 'output';
  root.addEventListener('click', (e) => { e.preventDefault(); navigate(''); });
  bc.appendChild(root);

  const parts = currentPath.split('/').filter(Boolean);
  let acc = '';
  for (const p of parts) {
    acc = acc ? acc + '/' + p : p;
    bc.appendChild(document.createTextNode(' / '));
    const a = document.createElement('a');
    a.href = '#';
    a.textContent = p;
    a.addEventListener('click', (e) => { e.preventDefault(); navigate(acc); });
    bc.appendChild(a);
  }
}

async function navigate(path) {
  currentPath = path;
  const res = await fetch('/api/files?path=' + encodeURIComponent(path));
  const data = await res.json();
  renderBreadcrumb();

  const body = $('fmBody');
  body.innerHTML = '';

  const protectedNames = ['.thumbs', '.zip_tmp', 'sources-png'];

  for (const e of data.entries) {
    const tr = document.createElement('tr');
    const nameCell = document.createElement('td');
    const sizeCell = document.createElement('td');
    const actCell = document.createElement('td');

    if (e.is_dir) {
      nameCell.innerHTML = `<span class="fm-name dir">📁 ${esc(e.name)}</span>`;
      nameCell.querySelector('.fm-name').addEventListener('click', () => navigate(joinPath(path, e.name)));
    } else {
      nameCell.innerHTML = `<span class="fm-name">📄 ${esc(e.name)}</span>`;
      nameCell.querySelector('.fm-name').addEventListener('click', () => {
        window.open('/api/files/download?path=' + encodeURIComponent(joinPath(path, e.name)), '_blank');
      });
    }
    sizeCell.className = 'fm-size';
    sizeCell.textContent = e.is_dir ? '' : fmtSize(e.size);

    // кнопка удаления (защищённые имена и корень не показываем)
    const isProtected = protectedNames.includes(e.name) && !currentPath;
    actCell.className = 'fm-size';
    if (!isProtected) {
      const delBtn = document.createElement('button');
      delBtn.className = 'btn btn-outline-danger btn-sm';
      delBtn.textContent = '🗑';
      delBtn.title = 'Удалить';
      delBtn.addEventListener('click', async () => {
        const fullPath = joinPath(path, e.name);
        const label = e.is_dir ? 'папку' : 'файл';
        if (!confirm(`Удалить ${label} «${e.name}»? Это действие нельзя отменить.`)) return;
        const r = await fetch('/api/files?path=' + encodeURIComponent(fullPath), { method: 'DELETE' });
        if (!r.ok) { alert('Ошибка: ' + (await r.text())); return; }
        navigate(currentPath);
      });
      actCell.appendChild(delBtn);
    }

    tr.appendChild(nameCell);
    tr.appendChild(sizeCell);
    tr.appendChild(actCell);
    body.appendChild(tr);
  }

  if (data.entries.length === 0) {
    body.innerHTML = '<tr><td colspan="3" class="fm-size">Пусто</td></tr>';
  }
}

$('btnUp').addEventListener('click', () => {
  const parts = currentPath.split('/').filter(Boolean);
  parts.pop();
  navigate(parts.join('/'));
});

$('btnZip').addEventListener('click', () => {
  window.open('/api/files/zip?path=' + encodeURIComponent(currentPath), '_blank');
});

$('btnRefresh').addEventListener('click', () => navigate(currentPath));

navigate('');
