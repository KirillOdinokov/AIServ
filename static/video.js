const $ = (id) => document.getElementById(id);

let currentJobId = null;
let pollTimer = null;

// Бенчмарки для оценки времени генерации (сек/кадр при базовых настройках)
const MODEL_ETA = {
  'wan2.1-t2v-14b': { spf: 6.7, base_steps: 20, base_w: 832, base_h: 480 },
  'ltx-video-13b': { spf: 2.2, base_steps: 30, base_w: 704, base_h: 480 },
};

// Родное разрешение каждой модели (для авто-выбора)
const MODEL_NATIVE_RES = {
  'wan2.1-t2v-14b': '832x480',
  'ltx-video-13b': '768x512',
};

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function fmtDuration(sec) {
  if (sec == null || !isFinite(sec)) return '';
  sec = Math.round(sec);
  if (sec < 60) return sec + ' с';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) return m + ' мин ' + s + ' с';
  const h = Math.floor(m / 60);
  return h + ' ч ' + (m % 60) + ' мин';
}

async function loadModels() {
  const res = await fetch('/api/models');
  const models = await res.json();
  const sel = $('model');
  sel.innerHTML = '';
  const videoModels = models.filter(m => m.video && m.downloaded);
  if (!videoModels.length) {
    sel.innerHTML = '<option value="">Нет скачанных видео-моделей (скачайте на главной)</option>';
    return;
  }
  videoModels.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = m.label;
    sel.appendChild(opt);
  });
  updateEta();
}

function getResolution() {
  const v = document.querySelector('input[name=resolution]:checked').value;
  return v.split('x').map(Number);
}

function updateEta() {
  const model = $('model').value;
  const m = MODEL_ETA[model];
  if (!m) { $('eta').textContent = ''; return; }
  const duration = parseFloat($('duration').value) || 0;
  const [w, h] = getResolution();
  const fps = parseFloat(document.querySelector('input[name=fps]:checked').value);
  const steps = parseInt($('steps').value) || 20;
  const frames = Math.round(duration * fps);
  const eta = frames * m.spf * (steps / m.base_steps) * ((w * h) / (m.base_w * m.base_h));
  $('eta').textContent = `≈ Время генерации: ${fmtDuration(eta)} (${frames} кадров)`;
}

$('btnCreate').addEventListener('click', async () => {
  const prompt = $('prompt').value.trim();
  if (!prompt) { alert('Введите промпт'); return; }
  const model = $('model').value;
  if (!model) { alert('Выберите модель'); return; }

  const duration = parseFloat($('duration').value) || 5;
  const [width, height] = getResolution();
  const fps = parseFloat(document.querySelector('input[name=fps]:checked').value);
  const steps = parseInt($('steps').value) || 20;
  const negative_prompt = $('negativePrompt').value.trim();

  $('btnCreate').disabled = true;
  $('btnCreate').textContent = 'Генерация…';
  $('resultCard').style.display = '';
  $('status').textContent = 'Генерация видео (может занять несколько минут)…';
  $('video').style.display = 'none';
  $('error').style.display = 'none';

  const res = await fetch('/api/video/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, model, duration, width, height, fps, steps, negative_prompt }),
  });
  if (!res.ok) {
    $('btnCreate').disabled = false;
    $('btnCreate').textContent = 'Создать видео';
    $('status').textContent = 'Ошибка: ' + (await res.text());
    return;
  }
  const data = await res.json();
  currentJobId = data.id;

  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/video/' + currentJobId);
      const job = await r.json();
      if (job.status === 'done') {
        clearInterval(pollTimer);
        $('btnCreate').disabled = false;
        $('btnCreate').textContent = 'Создать видео';
        $('status').textContent = '';
        const v = $('video');
        v.src = '/api/output/' + encodeURI(job.filename);
        v.style.display = '';
        v.load();
      } else if (job.status === 'error') {
        clearInterval(pollTimer);
        $('btnCreate').disabled = false;
        $('btnCreate').textContent = 'Создать видео';
        $('status').textContent = '';
        $('error').style.display = '';
        $('error').textContent = 'Ошибка: ' + job.error;
      }
    } catch (e) {}
  }, 3000);
});

['duration', 'steps'].forEach(id => $(id).addEventListener('input', updateEta));
$('model').addEventListener('change', () => {
  // авто-выбор родного разрешения модели (832×480 у Wan, 704×480 у LTX)
  const res = MODEL_NATIVE_RES[$('model').value];
  if (res) {
    const radio = document.querySelector(`input[name=resolution][value="${res}"]`);
    if (radio) radio.checked = true;
  }
  updateEta();
});
document.querySelectorAll('input[name=resolution], input[name=fps]').forEach(r => r.addEventListener('change', updateEta));

loadModels();
