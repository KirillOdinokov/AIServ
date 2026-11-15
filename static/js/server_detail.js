(function () {
  const $ = (id) => document.getElementById(id);
  const serverName = decodeURIComponent(
    window.location.pathname.split('/').filter(Boolean).pop() || '');

  let period = 'day';
  let showCores = true;
  let current = null;
  const charts = {};

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
  function ago(ts) {
    if (!ts) return '—';
    const d = Math.max(0, Date.now() / 1000 - ts);
    if (d < 90) return Math.round(d) + ' с';
    if (d < 5400) return Math.round(d / 60) + ' мин';
    if (d < 172800) return (d / 3600).toFixed(1) + ' ч';
    return Math.round(d / 86400) + ' д';
  }
  function diskType(rota) {
    if (rota === 0) return 'SSD';
    if (rota === 1) return 'HDD';
    return '';
  }
  function coreColors(n) {
    const out = [];
    for (let i = 0; i < n; i++) {
      out.push('hsl(' + Math.round(360 * i / Math.max(n, 1)) + ',60%,55%)');
    }
    return out;
  }
  function hasData(arr) {
    return Array.isArray(arr) && arr.some(v => v != null);
  }

  function renderInfo(info) {
    const el = $('serverInfo');
    if (!info) { el.textContent = ''; return; }
    const parts = [];
    if (info.external_ip) parts.push('Внешний IP: ' + esc(info.external_ip));
    if (info.ips && info.ips.length) parts.push('Локальный: ' + info.ips.map(esc).join(', '));
    const c = info.cpu || {};
    if (c.model) {
      let s = 'CPU: ' + esc(c.model);
      const extra = [];
      if (c.cores) extra.push(c.cores + ' ядер');
      if (c.threads) extra.push(c.threads + ' потоков');
      if (extra.length) s += ' (' + extra.join(' / ') + ')';
      parts.push(s);
    }
    const r = info.ram || {};
    if (r.total) {
      let s = 'RAM: ' + fmtBytes(r.total);
      if (r.type) s += ' ' + esc(r.type);
      if (r.speed) s += ' ' + esc(r.speed);
      if (r.slots_used) s += ' (' + r.slots_used + (r.slots_total ? '/' + r.slots_total : '') + ' слотов)';
      parts.push(s);
    }
    (info.disks || []).forEach(d => {
      let s = esc(d.mount) + ': ';
      if (d.array) s += esc(d.array) + ' · ';
      const t = diskType(d.rota);
      if (t) s += t + ' · ';
      s += fmtBytes(d.size);
      if (d.model) s += ' · ' + esc(d.model);
      parts.push(s);
    });
    el.innerHTML = parts.join('<span class="text-secondary"> · </span>');
  }

  function baseChart(series, labels, height, yaxis) {
    return {
      chart: { type: 'line', height: height, animations: { enabled: false },
               toolbar: { show: false } },
      series: series,
      stroke: { curve: 'smooth', width: 2.5 },
      xaxis: { categories: labels, tickAmount: Math.min(labels.length || 1, 12) },
      yaxis: yaxis,
      dataLabels: { enabled: false },
      markers: { size: 0 },
      legend: { show: series.length > 1, position: 'bottom' },
      tooltip: { shared: true, intersect: false },
      noData: { text: 'Нет данных' },
    };
  }

  function cpuOptions(series, colors, widths, labels) {
    const o = baseChart(series, labels, 300,
      { min: 0, max: 100, tickAmount: 5,
        labels: { formatter: v => (v == null ? '' : Math.round(v) + '%') } });
    o.colors = colors;
    o.stroke.width = widths;
    return o;
  }

  function ramOptions(series, labels) {
    return baseChart(series, labels, 240,
      { min: 0, max: 100, tickAmount: 5,
        labels: { formatter: v => (v == null ? '' : Math.round(v) + '%') } });
  }

  function diskOptions(series, labels, diskMax) {
    const y = { min: 0, forceNiceScale: false,
      labels: { formatter: v => (v == null ? '' : Math.round(v) + ' ГБ') } };
    if (diskMax && diskMax > 0) y.max = diskMax;
    return baseChart(series, labels, 240, y);
  }

  function tempOptions(series, labels, color) {
    const o = baseChart(series, labels, 240,
      { min: 0, labels: { formatter: v => (v == null ? '' : Math.round(v) + '°C') } });
    o.colors = [color || '#e8590c'];
    o.legend = { show: false };
    return o;
  }

  function netOptions(series, labels) {
    const o = baseChart(series, labels, 240,
      { min: 0, labels: { formatter: v => (v == null ? '' : Math.round(v) + ' КБ/с') } });
    o.colors = ['#206bc4', '#2fb344'];
    o.chart.type = 'area';
    return o;
  }

  function upsert(id, options) {
    if (!charts[id]) {
      $(id).innerHTML = '';
      charts[id] = new ApexCharts($(id), options);
      charts[id].render();
    } else {
      charts[id].updateOptions(options, false, false);
    }
  }

  function renderSeries(data) {
    const s = data.series || {};
    const labels = data.labels || [];
    const cores = s.cores || [];
    const useCores = showCores && cores.length > 0;

    const cpuSeries = [];
    const cpuColors = [];
    const cpuWidths = [];
    if (useCores) {
      const cc = coreColors(cores.length);
      cores.forEach((c, i) => {
        cpuSeries.push({ name: 'core ' + i, data: c });
        cpuColors.push(cc[i]);
        cpuWidths.push(1);
      });
    } else {
      cpuSeries.push({ name: 'CPU %', data: s.cpu || [] });
      cpuColors.push('#d60b0b');
      cpuWidths.push(2.5);
    }
    upsert('chartCpu', cpuOptions(cpuSeries, cpuColors, cpuWidths, labels));

    upsert('chartRam', ramOptions([{ name: 'RAM %', data: s.ram || [] }], labels));
    upsert('chartDisk', diskOptions([{ name: 'Диск, ГБ', data: s.disk_gb || [] }], labels, data.disk_total_gb));

    const hasCpuTemp = hasData(s.cpu_temp);
    const hasGpuTemp = hasData(s.gpu_temp);
    $('cardCpuTemp').style.display = hasCpuTemp ? '' : 'none';
    $('cardGpuTemp').style.display = hasGpuTemp ? '' : 'none';
    if (hasCpuTemp) upsert('chartCpuTemp', tempOptions([{ name: 'CPU °C', data: s.cpu_temp }], labels, '#e8590c'));
    if (hasGpuTemp) upsert('chartGpuTemp', tempOptions([{ name: 'GPU °C', data: s.gpu_temp }], labels, '#5c940d'));

    const rx = (s.net_rx || []).map(v => (v == null ? null : +(v / 1024).toFixed(1)));
    const tx = (s.net_tx || []).map(v => (v == null ? null : +(v / 1024).toFixed(1)));
    upsert('chartNet', netOptions(
      [{ name: 'RX КБ/с', data: rx }, { name: 'TX КБ/с', data: tx }], labels));
  }

  async function loadCurrent() {
    try {
      const r = await fetch('/api/metrics/servers/' + encodeURIComponent(serverName));
      if (!r.ok) return;
      current = await r.json();
    } catch (e) { return; }
    $('serverTitle').textContent = serverName;
    renderInfo(current.info);
    const parts = [];
    if (current.cpu != null) parts.push('CPU ' + Math.round(current.cpu) + '%');
    if (current.ram_total) {
      parts.push('RAM ' + Math.round(current.ram_used / current.ram_total * 100) + '%');
    }
    if (current.cpu_temp != null) parts.push('CPU ' + Math.round(current.cpu_temp) + '°C');
    if (current.gpu_temp != null) parts.push('GPU ' + Math.round(current.gpu_temp) + '°C');
    parts.push('данные ' + ago(current.ts) + ' назад');
    $('serverSubtitle').textContent = 'История загрузки · ' + parts.join(' · ');
  }

  async function loadSeries() {
    try {
      const r = await fetch('/api/metrics/series?server=' + encodeURIComponent(serverName) +
        '&period=' + period);
      renderSeries(await r.json());
    } catch (e) { /* ignore */ }
  }

  $('periodGroup').addEventListener('click', (e) => {
    const b = e.target.closest('[data-period]');
    if (!b) return;
    period = b.dataset.period;
    $('periodGroup').querySelectorAll('[data-period]').forEach(x =>
      x.classList.toggle('active', x === b));
    loadSeries();
  });

  $('toggleCores').addEventListener('change', (e) => {
    showCores = e.target.checked;
    loadSeries();
  });

  loadCurrent();
  loadSeries();
  setInterval(() => { loadCurrent(); loadSeries(); }, 30000);
})();
