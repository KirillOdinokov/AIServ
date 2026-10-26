// Общая статусная строка сервера (CPU/GPU/RAM/диск + Now Working + Free in).
// Подключается на всех страницах проекта. Сама создаёт bar, если его нет.
(function () {
  function fmtMb(mb) {
    if (mb >= 1024) return (mb / 1024).toFixed(1) + ' ГБ';
    return mb + ' МБ';
  }
  function fmtDuration(sec) {
    if (sec == null || sec <= 0) return '—';
    sec = Math.round(sec);
    if (sec < 60) return sec + 'с';
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    if (m < 60) return m + 'м ' + s + 'с';
    const h = Math.floor(m / 60);
    const mm = m % 60;
    if (h < 24) return h + 'ч ' + mm + 'м';
    const d = Math.floor(h / 24);
    return d + 'д ' + (h % 24) + 'ч';
  }

  let bar = document.getElementById('hardwareBar');
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'hardware-bar';
    bar.id = 'hardwareBar';
    bar.innerHTML =
      '<span class="hw-item" id="hwCpu">CPU: …</span>' +
      '<span class="hw-item" id="hwGpu">GPU: …</span>' +
      '<span class="hw-item" id="hwRam">RAM: …</span>' +
      '<span class="hw-item" id="hwSwap">SWAP: …</span>' +
      '<span class="hw-item" id="hwDisk">Диск: …</span>' +
      '<span class="hw-item" id="hwData">/data: …</span>' +
      '<span class="hw-item" id="hwNow">В VRAM: …</span>' +
      '<span class="hw-item" id="hwFree">Free in: …</span>';
    // вставляем сразу после <header> (или в начало body)
    const header = document.querySelector('header.topbar');
    if (header && header.nextSibling) {
      header.parentNode.insertBefore(bar, header.nextSibling);
    } else {
      document.body.insertBefore(bar, document.body.firstChild);
    }
  }

  const $ = (id) => document.getElementById(id);
  if (!$('hwNow')) {
    const s1 = document.createElement('span');
    s1.className = 'hw-item'; s1.id = 'hwNow'; s1.textContent = 'В VRAM: …';
    bar.appendChild(s1);
  }
  if (!$('hwFree')) {
    const s2 = document.createElement('span');
    s2.className = 'hw-item'; s2.id = 'hwFree'; s2.textContent = 'Free in: …';
    bar.appendChild(s2);
  }

  async function update() {
    try {
      const [sr, qr] = await Promise.all([
        fetch('/api/system/stats'),
        fetch('/api/queue/status'),
      ]);
      const s = await sr.json();
      const q = await qr.json();

      const cpu = s.cpu;
      let cpuText = `CPU: ${cpu.percent}%`;
      if (cpu.temp != null) cpuText += ` · ${cpu.temp}°C`;
      $('hwCpu').textContent = cpuText;

      if (s.gpu) {
        const g = s.gpu;
        $('hwGpu').textContent = `GPU: ${g.util}% · ${g.temp}°C · VRAM ${fmtMb(g.vram_used_mb)}/${fmtMb(g.vram_total_mb)}`;
      } else {
        $('hwGpu').textContent = 'GPU: нет';
      }

      const ram = s.ram;
      $('hwRam').textContent = `RAM: ${fmtMb(ram.used_mb)}/${fmtMb(ram.total_mb)} (${ram.percent}%)`;

      if (s.swap) {
        const sw = s.swap;
        $('hwSwap').textContent = `SWAP: ${fmtMb(sw.used_mb)}/${fmtMb(sw.total_mb)} (${sw.percent}%)`;
      }

      const disk = s.disk;
      $('hwDisk').textContent = `Диск: ${disk.used_gb}/${disk.total_gb} ГБ (${disk.percent}%)`;

      if (s.data_disk) {
        const dd = s.data_disk;
        $('hwData').textContent = `/data: ${dd.used_gb}/${dd.total_gb} ГБ (${dd.percent}%)`;
      } else {
        $('hwData').textContent = '';
      }

      $('hwNow').textContent = q.now_working_label
        ? `В VRAM: ${q.now_working_label}`
        : 'В VRAM: —';

      $('hwFree').textContent = `Free in: ${fmtDuration(q.free_in)}`;
    } catch (e) {}
  }

  update();
  setInterval(update, 3000);
})();
