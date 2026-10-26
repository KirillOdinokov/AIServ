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

  async function loadStatus() {
    try {
      const s = await (await fetch('/api/server/status')).json();
      $('srvHost').textContent = s.hostname || '—';
      $('srvOs').textContent = s.os || '—';
      $('srvUptime').textContent = fmtDuration(s.uptime_sec);

      const cpu = s.cpu || {};
      $('srvCpu').textContent = (cpu.percent != null ? cpu.percent : '—') + '% · ' + (cpu.cores || '—') + ' потоков';
      $('srvCpuTemp').textContent = cpu.temp != null ? cpu.temp + ' °C' : '—';
      if (cpu.model) $('srvCpu').title = cpu.model;

      if (s.gpu) {
        $('srvGpu').textContent = s.gpu.util + '% · ' + s.gpu.temp + '°C · VRAM ' +
          fmtGb(s.gpu.vram_used_mb) + ' / ' + fmtGb(s.gpu.vram_total_mb);
      } else {
        $('srvGpu').textContent = '—';
      }

      if (s.ram) $('srvRam').textContent = fmtGb(s.ram.used_mb) + ' / ' + fmtGb(s.ram.total_mb) + ' (' + s.ram.percent + '%)';
      if (s.swap) $('srvSwap').textContent = fmtGb(s.swap.used_mb) + ' / ' + fmtGb(s.swap.total_mb) + ' (' + s.swap.percent + '%)';

      if (s.disk) {
        $('srvDisk').textContent = s.disk.used_gb + ' / ' + s.disk.total_gb + ' ГБ (' + s.disk.percent + '%)';
        $('srvDiskBar').style.width = Math.min(100, s.disk.percent) + '%';
      }
      if (s.data_disk) {
        $('srvData').textContent = s.data_disk.used_gb + ' / ' + s.data_disk.total_gb + ' ГБ (' + s.data_disk.percent + '%)';
        $('srvDataBar').style.width = Math.min(100, s.data_disk.percent) + '%';
      } else {
        $('srvData').textContent = 'не смонтирован';
      }

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
