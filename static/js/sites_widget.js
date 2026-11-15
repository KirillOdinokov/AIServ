(function () {
  const $ = (id) => document.getElementById(id);

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function num(n) { return (n == null ? 0 : n).toLocaleString('ru-RU'); }

  async function load() {
    try {
      const r = await fetch('/api/sites/stats?period=day');
      if (!r.ok) throw new Error('bad');
      const d = await r.json();
      const rows = Object.keys(d.sites || {}).map(n => Object.assign({ name: n }, d.sites[n]));
      rows.sort((a, b) => (b.bots_total || 0) - (a.bots_total || 0));
      const top = rows.slice(0, 8);

      $('sitesWidget').innerHTML = top.map(r => {
        const bots = r.bots_total || 0;
        const total = r.total || 1;
        const share = Math.round(bots / total * 100);
        const topBot = Object.keys(r.bots || {})[0] || '—';
        const bar = share >= 70 ? 'bg-red' : (share >= 40 ? 'bg-orange' : 'bg-green');
        return '<tr>' +
          '<td class="fw-bold"><a href="/sites">' + esc(r.name) + '</a></td>' +
          '<td>' + num(r.human) + '</td>' +
          '<td>' + num(bots) + '</td>' +
          '<td style="min-width:120px"><div class="d-flex align-items-center gap-2">' +
            '<div class="progress progress-sm flex-grow-1"><div class="progress-bar ' + bar +
            '" style="width:' + share + '%"></div></div><span class="small">' + share + '%</span></div></td>' +
          '<td class="small text-secondary">' + esc(topBot) + '</td>' +
          '</tr>';
      }).join('') ||
        '<tr><td colspan="5" class="text-secondary text-center py-3">Нет данных</td></tr>';
      $('sitesWidgetUpdated').textContent = 'обновлено ' + new Date().toLocaleTimeString();
    } catch (e) {
      $('sitesWidget').innerHTML =
        '<tr><td colspan="5" class="text-danger text-center py-3">Нет данных</td></tr>';
    }
  }

  load();
  setInterval(load, 300000);
})();
