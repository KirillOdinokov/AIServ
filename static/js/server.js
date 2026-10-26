(function () {
  const $ = (id) => document.getElementById(id);

  function fmtDuration(sec) {
    if (sec == null) return '—';
    sec = Math.round(sec);
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (d > 0) return d + 'д ' + h + 'ч ' + m + 'м';
    if (h > 0) return h + 'ч ' + m + 'м';
    return m + 'м';
  }
  function fmtGb(mb) { return (mb / 1024).toFixed(1) + ' ГБ'; }

  // ── Аналоговый спидометр (SVG) ──────────────────────────────────────────
  function gaugeSvg(value, max, unit) {
    const pct = (value == null || isNaN(value) || !max) ? 0 : Math.max(0, Math.min(1, value / max));
    const cx = 100, cy = 105, r = 78;
    const angle = 180 - 180 * pct;
    const rad = angle * Math.PI / 180;
    const nx = cx + (r - 16) * Math.cos(rad);
    const ny = cy - (r - 16) * Math.sin(rad);

    const point = (deg) => {
      const a = deg * Math.PI / 180;
      return [(cx + r * Math.cos(a)).toFixed(1), (cy - r * Math.sin(a)).toFixed(1)];
    };
    const [gx1, gy1] = point(180), [gx2, gy2] = point(90);
    const [yx1, yy1] = point(90), [yx2, yy2] = point(36);
    const [rx1, ry1] = point(36), [rx2, ry2] = point(0);

    let ticks = '';
    for (let t = 0; t <= 100; t += 10) {
      const a = (180 - 1.8 * t) * Math.PI / 180;
      const ix = cx + (r - 13) * Math.cos(a), iy = cy - (r - 13) * Math.sin(a);
      const ox = cx + (r - 5) * Math.cos(a), oy = cy - (r - 5) * Math.sin(a);
      ticks += '<line x1="' + ix.toFixed(1) + '" y1="' + iy.toFixed(1) + '" x2="' + ox.toFixed(1) + '" y2="' + oy.toFixed(1) + '" stroke="#c8c8c8" stroke-width="1.3"/>';
    }

    const valText = (value == null || isNaN(value)) ? '—' : (Math.round(value * 10) / 10) + unit;
    return '<svg viewBox="0 0 200 140" xmlns="http://www.w3.org/2000/svg">' +
      '<path d="M ' + gx1 + ' ' + gy1 + ' A ' + r + ' ' + r + ' 0 0 1 ' + gx2 + ' ' + gy2 + '" fill="none" stroke="#2fb344" stroke-width="9" stroke-linecap="round"/>' +
      '<path d="M ' + yx1 + ' ' + yy1 + ' A ' + r + ' ' + r + ' 0 0 1 ' + yx2 + ' ' + yy2 + '" fill="none" stroke="#f59f00" stroke-width="9" stroke-linecap="round"/>' +
      '<path d="M ' + rx1 + ' ' + ry1 + ' A ' + r + ' ' + r + ' 0 0 1 ' + rx2 + ' ' + ry2 + '" fill="none" stroke="#d60b0b" stroke-width="9" stroke-linecap="round"/>' +
      ticks +
      '<line x1="' + cx + '" y1="' + cy + '" x2="' + nx.toFixed(1) + '" y2="' + ny.toFixed(1) + '" stroke="#111" stroke-width="3" stroke-linecap="round"/>' +
      '<circle cx="' + cx + '" cy="' + cy + '" r="5" fill="#111"/>' +
      '<text x="' + cx + '" y="132" text-anchor="middle" font-size="21" font-weight="700" fill="#111">' + valText + '</text>' +
      '</svg>';
  }

  function setGauge(id, value, max, unit) {
    const el = $(id);
    if (el) el.innerHTML = gaugeSvg(value, max, unit);
  }

  function setTemp(markerId, labelId, textId, value) {
    const marker = $(markerId), label = $(labelId), text = $(textId);
    if (!marker || !label || !text) return;
    if (value == null || isNaN(value)) {
      marker.style.left = '0%';
      label.textContent = '—';
      text.textContent = '—';
      return;
    }
    const pct = Math.max(0, Math.min(100, value));
    marker.style.left = pct + '%';
    label.textContent = Math.round(value) + '°';
    text.textContent = value + ' °C';
  }

  async function loadStatus() {
    try {
      const s = await (await fetch('/api/server/status')).json();
      const cpu = s.cpu || {};

      setGauge('gaugeCpu', cpu.percent, 100, '%');
      setGauge('gaugeGpu', s.gpu ? s.gpu.util : null, 100, '%');
      setGauge('gaugeRam', s.ram ? s.ram.percent : null, 100, '%');
      const vramPct = (s.gpu && s.gpu.vram_total_mb) ? (s.gpu.vram_used_mb / s.gpu.vram_total_mb * 100) : null;
      setGauge('gaugeVram', vramPct, 100, '%');
      setGauge('gaugeDisk', s.disk ? s.disk.percent : null, 100, '%');
      setGauge('gaugeData', s.data_disk ? s.data_disk.percent : null, 100, '%');

      setTemp('tempCpuMarker', 'tempCpuLabel', 'tempCpuText', cpu.temp);
      setTemp('tempGpuMarker', 'tempGpuLabel', 'tempGpuText', s.gpu ? s.gpu.temp : null);

      $('srvHost').textContent = s.hostname || '—';
      $('srvOs').textContent = s.os || '—';
      $('srvUptime').textContent = fmtDuration(s.uptime_sec);
      $('srvCpu').textContent = (cpu.model || '—') + (cpu.cores ? ' · ' + cpu.cores + ' потоков' : '');
      $('srvRam').textContent = s.ram ? fmtGb(s.ram.used_mb) + ' / ' + fmtGb(s.ram.total_mb) + ' (' + s.ram.percent + '%)' : '—';
      $('srvSwap').textContent = s.swap ? fmtGb(s.swap.used_mb) + ' / ' + fmtGb(s.swap.total_mb) + ' (' + s.swap.percent + '%)' : '—';

      $('srvLoaded').textContent = s.loaded_model ? s.loaded_model : '—';
      const ollama = $('srvOllama');
      if (s.ollama && s.ollama.length) {
        ollama.innerHTML = s.ollama.map(m =>
          '<li>' + (m.name || '') + ' <span class="text-secondary small">' + (m.size ? fmtGb(m.size / 1024 / 1024) : '') + '</span></li>'
        ).join('');
      } else {
        ollama.innerHTML = '<li class="text-secondary">нет загруженных</li>';
      }
    } catch (e) {}
  }

  async function loadLog() {
    const name = $('logSelect').value;
    const pre = $('logView');
    try {
      const res = await fetch('/api/server/logs?name=' + encodeURIComponent(name) + '&lines=300');
      const data = await res.json();
      pre.textContent = data.content || 'Пусто';
      pre.scrollTop = pre.scrollHeight;
    } catch (e) {
      pre.textContent = 'Не удалось загрузить лог';
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    loadStatus();
    loadLog();
    setInterval(loadStatus, 5000);
    setInterval(loadLog, 10000);
    $('btnRefreshServer').addEventListener('click', loadStatus);
    $('btnRefreshLog').addEventListener('click', loadLog);
    $('logSelect').addEventListener('change', loadLog);
  });
})();
