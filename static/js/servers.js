(function () {
  const $ = (id) => document.getElementById(id);

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function fmtBytes(n) {
    if (n == null) return '—';
    const u = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ'];
    let i = 0, x = Number(n);
    while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
    return (i ? x.toFixed(1) : Math.round(x)) + ' ' + u[i];
  }
  function fmtPct(v) { return v == null ? '—' : Math.round(v) + '%'; }

  function render(servers) {
    const tb = $('serversTable');
    if (!servers.length) {
      tb.innerHTML = '<tr><td colspan="8" class="text-secondary text-center py-4">' +
        'Нет данных. Агенты ещё не присылали метрики.</td></tr>';
      return;
    }
    tb.innerHTML = servers.map(s => {
      const info = s.info || {};
      const ext = info.external_ip || '—';
      const cpuModel = (info.cpu || {}).model || '—';
      const cpuNow = s.cpu != null ? Math.round(s.cpu) + '%' : '—';
      const ramPct = s.ram_total ? Math.round(s.ram_used / s.ram_total * 100) : null;
      const temp = s.cpu_temp != null ? Math.round(s.cpu_temp) + '°' : '—';
      const stale = (Date.now() / 1000 - (s.ts || 0)) > 180;
      const entries = Object.entries(s.disk || {});
      const diskHtml = entries.length
        ? entries.map(([m, d]) => esc(m) + ' ' + fmtBytes(d.used) + ' / ' + fmtBytes(d.total)).join('<br>')
        : '—';
      const status = stale
        ? '<span class="badge bg-red-lt">нет данных</span>'
        : '<span class="badge bg-green-lt">онлайн</span>';
      return '<tr>' +
        '<td><a href="/servers/' + encodeURIComponent(s.server) + '">' + esc(s.server) + '</a></td>' +
        '<td class="text-secondary">' + esc(ext) + '</td>' +
        '<td>' + esc(cpuModel) +
          '<div class="text-secondary small">' + cpuNow + '</div></td>' +
        '<td>' + fmtPct(ramPct) +
          '<div class="text-secondary small">' + fmtBytes(s.ram_used) + ' / ' +
          fmtBytes(s.ram_total) + '</div></td>' +
        '<td class="small">' + diskHtml + '</td>' +
        '<td>' + temp + '</td>' +
        '<td>' + (s.load1 != null ? s.load1 : '—') + '</td>' +
        '<td>' + status + '</td>' +
        '</tr>';
    }).join('');
  }

  async function load() {
    let servers = [];
    try {
      const r = await fetch('/api/metrics/servers');
      servers = (await r.json()).servers || [];
    } catch (e) { servers = []; }
    render(servers);
    $('updatedAt').textContent = 'обновлено ' + new Date().toLocaleTimeString();
  }

  load();
  setInterval(load, 30000);
})();
