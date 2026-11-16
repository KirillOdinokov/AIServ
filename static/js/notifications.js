(function () {
  var wrap = document.getElementById('notifWrap');
  if (!wrap) return;
  var url = wrap.getAttribute('data-url') || '/base/api/notifications';
  var btn = document.getElementById('notifBtn');
  var badge = document.getElementById('notifBadge');
  var panel = document.getElementById('notifPanel');
  var list = document.getElementById('notifList');
  var readAll = document.getElementById('notifReadAll');
  var items = [];
  var loading = false;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function rel(iso) {
    if (!iso) return '';
    var t = Date.parse(iso);
    if (isNaN(t)) return '';
    var diff = Math.floor((Date.now() - t) / 1000);
    if (diff < 60) return 'только что';
    if (diff < 3600) return Math.floor(diff / 60) + ' мин назад';
    if (diff < 86400) return Math.floor(diff / 3600) + ' ч назад';
    var d = new Date(t);
    return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) + ' ' +
      d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }

  function render() {
    if (!items.length) {
      list.innerHTML = '<div class="notif-empty">Уведомлений нет</div>';
      return;
    }
    var html = '';
    items.forEach(function (n) {
      var cls = 'notif-item';
      if (n.status === 'resolved') cls += ' notif-item--resolved';
      else if (n.severity === 'critical') cls += ' notif-item--critical';
      if (!n.read_at) cls += ' notif-item--unread';
      html += '<div class="' + cls + '" data-id="' + n.id + '">' +
        '<div class="notif-item__title">' + esc(n.title) + '</div>' +
        '<div class="notif-item__msg">' + esc(n.message) + '</div>' +
        '<div class="notif-item__time">' + rel(n.created_at) +
        (n.status === 'resolved' ? ' · закрыто' : '') + '</div>' +
        '</div>';
    });
    list.innerHTML = html;
  }

  function setBadge(n) {
    n = Number(n) || 0;
    if (n > 0) {
      badge.textContent = n > 99 ? '99+' : String(n);
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }
  }

  function fetchList() {
    if (loading) return;
    loading = true;
    fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.json();
      })
      .then(function (data) {
        items = data.items || [];
        setBadge(data.unread);
        if (!panel.hidden) render();
      })
      .catch(function () {})
      .then(function () { loading = false; });
  }

  function markRead(ids, all) {
    fetch(url + '/read', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(all ? { all: true } : { ids: ids })
    }).then(function () { fetchList(); }).catch(function () {});
  }

  function close() {
    panel.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
  }

  btn.addEventListener('click', function (e) {
    e.stopPropagation();
    if (panel.hidden) {
      panel.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
      render();
      fetchList();
    } else {
      close();
    }
  });

  panel.addEventListener('click', function (e) { e.stopPropagation(); });

  list.addEventListener('click', function (e) {
    var el = e.target.closest('.notif-item');
    if (!el) return;
    var id = Number(el.getAttribute('data-id'));
    var it = items.filter(function (x) { return x.id === id; })[0];
    if (it && !it.read_at) {
      it.read_at = new Date().toISOString();
      markRead([id], false);
      render();
    }
  });

  if (readAll) {
    readAll.addEventListener('click', function (e) {
      e.stopPropagation();
      items.forEach(function (x) { if (!x.read_at) x.read_at = new Date().toISOString(); });
      markRead([], true);
      render();
    });
  }

  document.addEventListener('click', function () {
    if (!panel.hidden) close();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !panel.hidden) close();
  });

  fetchList();
  setInterval(fetchList, 60000);
})();
