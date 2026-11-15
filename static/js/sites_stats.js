(function () {
  const $ = (id) => document.getElementById(id);
  let period = 'day';

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function num(n) {
    return (n == null ? 0 : n).toLocaleString('ru-RU');
  }
  function card(label, val) {
    return '<div class="col-6 col-lg-3"><div class="card"><div class="card-body">' +
      '<div class="subheader">' + label + '</div>' +
      '<div class="h1 mb-0">' + val + '</div></div></div></div>';
  }

  function render(data) {
    const sites = data.sites || {};
    const rows = Object.keys(sites).map(name => Object.assign({ name }, sites[name]));
    rows.sort((a, b) => b.total - a.total);

    const T = { total: 0, human: 0, bots: 0, ai: 0, search: 0, seo: 0 };
    rows.forEach(r => {
      T.total += r.total || 0;
      T.human += r.human || 0;
      T.bots += r.bots_total || 0;
      T.ai += r.ai || 0;
      T.search += r.search || 0;
      T.seo += r.seo || 0;
    });
    $('totalsRow').innerHTML =
      card('Всего запросов', num(T.total)) +
      card('Люди', num(T.human)) +
      card('Боты', num(T.bots)) +
      card('AI-боты', num(T.ai));

    const html = rows.map(r => {
      const bots = r.bots_total || 0;
      const share = r.total ? Math.round(bots / r.total * 100) : 0;
      const top = Object.keys(r.bots || {}).slice(0, 4)
        .map(n => esc(n) + ' ' + num(r.bots[n])).join(', ');
      const bar = share >= 70 ? 'bg-red' : (share >= 40 ? 'bg-orange' : 'bg-green');
      return '<tr>' +
        '<td class="fw-bold">' + esc(r.name) + '</td>' +
        '<td>' + num(r.human) +
          '<div class="text-secondary small">' + num(r.human_uniq) + ' уник.</div></td>' +
        '<td>' + num(bots) + '</td>' +
        '<td style="min-width:130px"><div class="d-flex align-items-center gap-2">' +
          '<div class="progress progress-sm flex-grow-1"><div class="progress-bar ' + bar +
          '" style="width:' + share + '%"></div></div><span class="small">' + share + '%</span></div></td>' +
        '<td>' + num(r.search) + '</td>' +
        '<td>' + num(r.ai) + '</td>' +
        '<td>' + num(r.seo) + '</td>' +
        '<td class="small text-secondary">' + (top || '—') + '</td>' +
        '</tr>';
    }).join('');

    $('sitesTable').innerHTML = html ||
      '<tr><td colspan="8" class="text-secondary text-center py-4">Нет данных</td></tr>';
    $('updatedAt').textContent = 'обновлено ' + new Date().toLocaleTimeString();
  }

  async function load() {
    try {
      const r = await fetch('/api/sites/stats?period=' + period);
      if (!r.ok) throw new Error('bad status');
      render(await r.json());
    } catch (e) {
      $('sitesTable').innerHTML =
        '<tr><td colspan="8" class="text-danger text-center py-4">' +
        'Не удалось получить статистику</td></tr>';
    }
  }

  $('periodGroup').addEventListener('click', (e) => {
    const b = e.target.closest('[data-period]');
    if (!b) return;
    period = b.dataset.period;
    $('periodGroup').querySelectorAll('[data-period]').forEach(x =>
      x.classList.toggle('active', x === b));
    load();
  });

  load();
  setInterval(load, 300000);
})();
