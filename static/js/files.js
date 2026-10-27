(function () {
  const $ = (id) => document.getElementById(id);
  let currentPath = '';
  let viewMode = 'list';
  let history = [];
  let lastEntries = [];

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
  function fmtDate(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    return d.toLocaleDateString('ru-RU') + ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }
  function joinPath(base, name) { return base ? base + '/' + name : name; }

  const EXT = {
    png: 'image', jpg: 'image', jpeg: 'image', webp: 'image', gif: 'image', bmp: 'image',
    mp4: 'video', webm: 'video', mov: 'video',
    txt: 'text', csv: 'text', md: 'text',
    json: 'json', zip: 'zip',
  };
  function kindOf(e) {
    if (e.is_dir) return 'dir';
    const ext = (e.name.split('.').pop() || '').toLowerCase();
    return EXT[ext] || 'file';
  }
  function iconOf(kind) {
    switch (kind) {
      case 'dir': return { icon: 'ti-folder-filled', color: '#f59f00' };
      case 'image': return { icon: 'ti-photo', color: '#0d6efd' };
      case 'video': return { icon: 'ti-video', color: '#d60b0b' };
      case 'text': return { icon: 'ti-file-text', color: '#2fb344' };
      case 'json': return { icon: 'ti-braces', color: '#6f42c1' };
      case 'zip': return { icon: 'ti-file-zip', color: '#f59f00' };
      default: return { icon: 'ti-file', color: '#999999' };
    }
  }
  function typeLabel(kind) {
    switch (kind) {
      case 'dir': return 'Папка с файлами';
      case 'image': return 'Изображение';
      case 'video': return 'Видео';
      case 'text': return 'Текстовый документ';
      case 'json': return 'JSON';
      case 'zip': return 'ZIP-архив';
      default: return 'Файл';
    }
  }
  function isProtected(e) {
    return ['.thumbs', '.zip_tmp', 'sources-png'].includes(e.name) && !currentPath;
  }

  function renderBreadcrumb() {
    const bc = $('addressBar');
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
      const sep = document.createElement('span');
      sep.className = 'sep';
      sep.textContent = '›';
      bc.appendChild(sep);
      const a = document.createElement('a');
      a.href = '#';
      a.textContent = p;
      a.addEventListener('click', (e) => { e.preventDefault(); navigate(acc); });
      bc.appendChild(a);
    }
  }

  function buildList(entries) {
    const wrap = document.createElement('div');
    const table = document.createElement('table');
    table.className = 'table table-vcenter card-table mb-0';
    table.innerHTML = '<thead><tr>' +
      '<th>Имя</th>' +
      '<th class="d-none d-md-table-cell">Дата изменения</th>' +
      '<th class="d-none d-md-table-cell">Тип</th>' +
      '<th class="d-none d-sm-table-cell">Размер</th>' +
      '<th class="w-1"></th></tr></thead>';
    const tbody = document.createElement('tbody');

    entries.forEach(e => {
      const kind = kindOf(e);
      const ic = iconOf(kind);
      const tr = document.createElement('tr');
      tr.style.cursor = 'pointer';
      tr.innerHTML = '<td><div class="d-flex align-items-center gap-2">' +
        '<i class="ti ' + ic.icon + '" style="font-size:1.25rem;color:' + ic.color + ';"></i>' +
        '<span class="text-truncate" style="max-width:420px;">' + esc(e.name) + '</span></div></td>' +
        '<td class="d-none d-md-table-cell text-secondary">' + (e.is_dir ? '' : fmtDate(e.mtime)) + '</td>' +
        '<td class="d-none d-md-table-cell text-secondary">' + typeLabel(kind) + '</td>' +
        '<td class="d-none d-sm-table-cell text-secondary">' + (e.is_dir ? '' : fmtSize(e.size)) + '</td>' +
        '<td class="text-end"></td>';
      tr.addEventListener('click', () => openEntry(e));
      if (!isProtected(e)) {
        const del = document.createElement('button');
        del.className = 'btn btn-sm btn-outline-danger';
        del.innerHTML = '<i class="ti ti-trash"></i>';
        del.title = 'Удалить';
        del.addEventListener('click', (ev) => { ev.stopPropagation(); removeEntry(e); });
        tr.querySelector('td:last-child').appendChild(del);
      }
      tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  function buildGrid(entries) {
    const grid = document.createElement('div');
    grid.className = 'ai-fm-grid';
    entries.forEach(e => {
      const kind = kindOf(e);
      const ic = iconOf(kind);
      const tile = document.createElement('div');
      tile.className = 'ai-fm-tile';
      tile.innerHTML = '<i class="ti ' + ic.icon + '" style="color:' + ic.color + ';"></i>' +
        '<div class="nm" title="' + esc(e.name) + '">' + esc(e.name) + '</div>' +
        '<div class="meta">' + (e.is_dir ? 'Папка' : fmtSize(e.size)) + '</div>';
      tile.addEventListener('click', () => openEntry(e));
      if (!isProtected(e)) {
        const del = document.createElement('button');
        del.className = 'btn btn-sm btn-outline-danger mt-2';
        del.innerHTML = '<i class="ti ti-trash"></i>';
        del.addEventListener('click', (ev) => { ev.stopPropagation(); removeEntry(e); });
        tile.appendChild(del);
      }
      grid.appendChild(tile);
    });
    return grid;
  }

  function render() {
    const view = $('fmView');
    view.innerHTML = '';
    if (!lastEntries.length) {
      view.innerHTML = '<div class="text-secondary text-center py-5">Пусто</div>';
    } else {
      view.appendChild(viewMode === 'icons' ? buildGrid(lastEntries) : buildList(lastEntries));
    }
    $('fmStatus').textContent = 'Элементов: ' + lastEntries.length;
  }

  async function navigate(path, push = true) {
    const res = await fetch('/api/files?path=' + encodeURIComponent(path));
    const data = await res.json();
    if (push) history.push(currentPath);
    currentPath = data.path || path;
    lastEntries = data.entries || [];
    renderBreadcrumb();
    render();
  }

  function openEntry(e) {
    if (e.is_dir) navigate(joinPath(currentPath, e.name));
    else window.open('/api/files/download?path=' + encodeURIComponent(joinPath(currentPath, e.name)), '_blank');
  }

  async function removeEntry(e) {
    const fullPath = joinPath(currentPath, e.name);
    const label = e.is_dir ? 'папку' : 'файл';
    if (!confirm('Удалить ' + label + ' «' + e.name + '»? Это действие нельзя отменить.')) return;
    const r = await fetch('/api/files?path=' + encodeURIComponent(fullPath), { method: 'DELETE' });
    if (!r.ok) { alert('Ошибка: ' + (await r.text())); return; }
    navigate(currentPath, false);
  }

  function goUp() {
    const parts = currentPath.split('/').filter(Boolean);
    parts.pop();
    navigate(parts.join('/'));
  }

  async function loadDisks() {
    try {
      const s = await (await fetch('/api/system/stats')).json();
      if (s.disk) {
        $('diskRootText').textContent = s.disk.used_gb + ' / ' + s.disk.total_gb + ' ГБ (' + s.disk.percent + '%)';
        $('diskRootBar').style.width = Math.min(100, s.disk.percent) + '%';
      }
      if (s.data_disk) {
        $('diskDataText').textContent = s.data_disk.used_gb + ' / ' + s.data_disk.total_gb + ' ГБ (' + s.data_disk.percent + '%)';
        $('diskDataBar').style.width = Math.min(100, s.data_disk.percent) + '%';
      } else {
        $('diskDataText').textContent = 'не смонтирован';
      }
    } catch (e) {}
  }

  $('btnBack').addEventListener('click', () => {
    if (history.length) navigate(history.pop(), false);
  });
  $('btnUp').addEventListener('click', goUp);
  $('btnRefresh').addEventListener('click', () => navigate(currentPath, false));
  $('btnZip').addEventListener('click', () => window.open('/api/files/zip?path=' + encodeURIComponent(currentPath), '_blank'));
  $('quickRoot').addEventListener('click', (e) => { e.preventDefault(); navigate(''); });
  $('btnViewIcons').addEventListener('click', () => { viewMode = 'icons'; render(); });
  $('btnViewList').addEventListener('click', () => { viewMode = 'list'; render(); });

  loadDisks();
  setInterval(loadDisks, 10000);
  navigate('', false);
})();
