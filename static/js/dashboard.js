(function () {
  const $ = (id) => document.getElementById(id);
  const fmtGb = (mb) => (mb / 1024).toFixed(1);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  function fmtDate(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) +
      ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }

  // ── Карточки текущего состояния ─────────────────────────────────────────
  async function loadOverview() {
    try {
      const s = await (await fetch('/api/system/stats')).json();
      const cpu = s.cpu || {};
      $('statCpu').textContent = (cpu.percent != null ? cpu.percent : '—') + '%';
      $('statCpuTemp').textContent = cpu.temp != null ? cpu.temp + '°C' : '';
      $('statCpuBar').style.width = Math.min(100, cpu.percent || 0) + '%';

      if (s.gpu) {
        $('statGpu').textContent = s.gpu.util + '%';
        $('statGpuTemp').textContent = s.gpu.temp + '°C';
        $('statGpuSub').textContent = 'VRAM ' + fmtGb(s.gpu.vram_used_mb) + ' / ' + fmtGb(s.gpu.vram_total_mb) + ' ГБ';
      } else {
        $('statGpu').textContent = '—';
        $('statGpuTemp').textContent = '';
        $('statGpuSub').textContent = 'нет данных';
      }

      if (s.ram) {
        $('statRam').textContent = s.ram.percent + '%';
        $('statRamSub').textContent = fmtGb(s.ram.used_mb) + ' / ' + fmtGb(s.ram.total_mb) + ' ГБ';
        $('statRamBar').style.width = Math.min(100, s.ram.percent) + '%';
      }

      if (s.gpu) {
        const pct = s.gpu.vram_total_mb ? (s.gpu.vram_used_mb / s.gpu.vram_total_mb * 100) : 0;
        $('statVram').textContent = pct.toFixed(0) + '%';
        $('statVramSub').textContent = fmtGb(s.gpu.vram_used_mb) + ' / ' + fmtGb(s.gpu.vram_total_mb) + ' ГБ';
        $('statVramBar').style.width = Math.min(100, pct) + '%';
      }

      if (s.disk) {
        $('statDisk').textContent = s.disk.percent + '%';
        $('statDiskSub').textContent = s.disk.used_gb + ' / ' + s.disk.total_gb + ' ГБ';
        $('statDiskBar').style.width = Math.min(100, s.disk.percent) + '%';
      }
      if (s.data_disk) {
        $('statData').textContent = s.data_disk.percent + '%';
        $('statDataSub').textContent = s.data_disk.used_gb + ' / ' + s.data_disk.total_gb + ' ГБ';
        $('statDataBar').style.width = Math.min(100, s.data_disk.percent) + '%';
      }
    } catch (e) {}
  }

  // ── Последние завершённые задачи ────────────────────────────────────────
  const TYPE_LABEL = { image: 'Изображения', text: 'Тексты', reference: 'Референсы', upscale: 'Upscale' };
  const TYPE_BADGE = { image: 'bg-blue-lt', text: 'bg-purple-lt', reference: 'bg-azure-lt', upscale: 'bg-teal-lt' };
  function resultUrl(j) {
    if (j.type === 'text') return '/llm/result/' + j.id;
    if (j.type === 'reference') return '/ref/result/' + j.id;
    if (j.type === 'upscale') return '/upscale/' + j.id;
    return '/result/' + j.id;
  }

  async function loadRecent() {
    const body = $('recentTasks');
    if (!body) return;
    try {
      const tasks = await (await fetch('/api/tasks')).json();
      const done = tasks.filter(t => t.status === 'done')
        .sort((a, b) => (b.created_at || 0) - (a.created_at || 0))
        .slice(0, 8);
      if (!done.length) {
        body.innerHTML = '<tr><td colspan="5" class="text-secondary text-center py-4">Пока нет завершённых задач</td></tr>';
        return;
      }
      body.innerHTML = done.map(j => {
        const type = TYPE_LABEL[j.type] || j.type;
        const badge = TYPE_BADGE[j.type] || 'bg-secondary-lt';
        const progress = (j.total > 0 ? j.done + '/' + j.total : '—');
        return '<tr>' +
          '<td><span class="badge ' + badge + '">' + esc(type) + '</span></td>' +
          '<td><a href="' + resultUrl(j) + '">' + esc(j.source_name || j.id) + '</a></td>' +
          '<td class="text-secondary">' + progress + '</td>' +
          '<td class="text-secondary">' + fmtDate(j.created_at) + '</td>' +
          '<td class="text-end"><a class="btn btn-sm btn-outline-secondary" href="' + resultUrl(j) + '">Открыть</a></td>' +
          '</tr>';
      }).join('');
    } catch (e) {
      body.innerHTML = '<tr><td colspan="5" class="text-secondary text-center py-4">Не удалось загрузить задачи</td></tr>';
    }
  }

  // ── Графики ─────────────────────────────────────────────────────────────
  const USAGE_SERIES = ['CPU', 'GPU', 'RAM', 'VRAM', 'Диск /', '/data'];
  const USAGE_KEYS = ['cpu', 'gpu', 'ram', 'vram', 'disk', 'data'];
  const HEALTH_SERIES = ['CPU °C', 'GPU °C', 'Диск /', '/data'];
  const HEALTH_KEYS = ['cpu_temp', 'gpu_temp', 'disk', 'data'];

  let usageChart = null;
  let healthChart = null;
  const statsCache = {};

  async function fetchStats(period) {
    if (statsCache[period]) return statsCache[period];
    const res = await fetch('/api/stats/history?period=' + encodeURIComponent(period));
    if (!res.ok) throw new Error('stats ' + res.status);
    const data = await res.json();
    statsCache[period] = data;
    setTimeout(() => { delete statsCache[period]; }, 60000);
    return data;
  }

  function applyChecks(chart, containerId) {
    const box = $(containerId);
    if (!box) return;
    box.querySelectorAll('input[data-series]').forEach(cb => {
      try {
        if (cb.checked) chart.showSeries(cb.dataset.series);
        else chart.hideSeries(cb.dataset.series);
      } catch (e) {}
    });
  }

  function initCharts() {
    usageChart = new ApexCharts($('usageChart'), {
      chart: { type: 'area', height: 320, toolbar: { show: false }, animations: { enabled: false } },
      series: USAGE_SERIES.map(name => ({ name: name, data: [] })),
      colors: ['#d60b0b', '#0d6efd', '#2fb344', '#f59f00', '#6f42c1', '#20c997'],
      stroke: { curve: 'smooth', width: 2 },
      fill: { type: 'solid', opacity: 0.06 },
      dataLabels: { enabled: false },
      legend: { show: false },
      xaxis: { categories: [], labels: { rotate: -45, style: { fontSize: '11px' } } },
      yaxis: { min: 0, max: 100, labels: { formatter: v => v.toFixed(0) + '%' } },
      grid: { strokeDashArray: 4 },
      tooltip: { shared: true, intersect: false, y: { formatter: v => (v == null ? '—' : v.toFixed(1) + '%') } },
    });
    usageChart.render();

    healthChart = new ApexCharts($('healthChart'), {
      chart: { type: 'line', height: 320, toolbar: { show: false }, animations: { enabled: false } },
      series: HEALTH_SERIES.map(name => ({ name: name, data: [] })),
      colors: ['#d60b0b', '#0d6efd', '#6f42c1', '#20c997'],
      stroke: { curve: 'smooth', width: 2 },
      dataLabels: { enabled: false },
      legend: { show: false },
      xaxis: { categories: [], labels: { rotate: -45, style: { fontSize: '11px' } } },
      yaxis: { min: 0, max: 110 },
      grid: { strokeDashArray: 4 },
      tooltip: { shared: true, intersect: false },
    });
    healthChart.render();

    $('usageChecks').querySelectorAll('input[data-series]').forEach(cb => {
      cb.addEventListener('change', () => {
        try { cb.checked ? usageChart.showSeries(cb.dataset.series) : usageChart.hideSeries(cb.dataset.series); } catch (e) {}
      });
    });
    $('healthChecks').querySelectorAll('input[data-series]').forEach(cb => {
      cb.addEventListener('change', () => {
        try { cb.checked ? healthChart.showSeries(cb.dataset.series) : healthChart.hideSeries(cb.dataset.series); } catch (e) {}
      });
    });
  }

  async function loadUsage(period) {
    try {
      const d = await fetchStats(period);
      usageChart.updateOptions({ xaxis: { categories: d.labels || [] } }, false, false);
      usageChart.updateSeries(USAGE_SERIES.map((name, i) => ({
        name: name,
        data: (d.series && d.series[USAGE_KEYS[i]]) || [],
      })));
      applyChecks(usageChart, 'usageChecks');
    } catch (e) {}
  }

  async function loadHealth(period) {
    try {
      const d = await fetchStats(period);
      healthChart.updateOptions({ xaxis: { categories: d.labels || [] } }, false, false);
      healthChart.updateSeries(HEALTH_SERIES.map((name, i) => ({
        name: name,
        data: (d.series && d.series[HEALTH_KEYS[i]]) || [],
      })));
      applyChecks(healthChart, 'healthChecks');
    } catch (e) {}
  }

  function bindRanges(group, fn) {
    const root = document.querySelector('[data-range-group="' + group + '"]');
    if (!root) return;
    const buttons = root.querySelectorAll('button[data-range]');
    buttons.forEach(btn => {
      btn.addEventListener('click', () => {
        buttons.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        fn(btn.dataset.range);
      });
    });
  }

  // ── Старт ───────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    loadOverview();
    loadRecent();
    initCharts();
    loadUsage('day');
    loadHealth('day');
    bindRanges('usage', loadUsage);
    bindRanges('health', loadHealth);
    setInterval(loadOverview, 5000);
    setInterval(loadRecent, 30000);
  });
})();
