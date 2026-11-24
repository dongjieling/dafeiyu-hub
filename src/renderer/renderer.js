const $ = (sel) => document.querySelector(sel);

const state = {
  running: false,
  health: null,
  cfg: null,
};

// ---------- 页面切换 ----------
document.querySelectorAll('.nav-item:not(.disabled)').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    const page = btn.dataset.page;
    document.querySelectorAll('.page').forEach((p) => p.classList.remove('active'));
    $(`#page-${page}`).classList.add('active');
  });
});

// ---------- 灯光 ----------
function setDot(el, kind) {
  el.classList.remove('on', 'off', 'warn', 'err');
  el.classList.add(kind);
}

function render() {
  const { running, health } = state;

  // 英雄区
  const heroDot = $('#hero-dot');
  if (running && health?.ok) {
    setDot(heroDot, 'on');
    $('#hero-title').textContent = '服务运行中';
    $('#hero-sub').textContent = 'AIRI 可以连接了';
  } else if (running) {
    setDot(heroDot, 'warn');
    $('#hero-title').textContent = '服务已启动，但未响应';
    $('#hero-sub').textContent = health?.error || '正在等待健康检查…';
  } else {
    setDot(heroDot, 'off');
    $('#hero-title').textContent = '服务未启动';
    $('#hero-sub').textContent = '点击「启动服务」开始';
  }

  // 三个灯
  setDot($('#light-server .dot'), running && health?.ok ? 'on' : running ? 'warn' : 'off');
  $('#light-server .light-body').textContent = running
    ? `127.0.0.1:${state.cfg?.hubPort ?? 8787}`
    : '未监听';

  const lmOk = Boolean(health?.ok && health.data?.lm_connected);
  setDot($('#light-hub .dot'), lmOk ? 'on' : 'off');
  $('#light-hub .light-body').textContent = lmOk ? 'LivingMemory 已连接' : 'LivingMemory 未接入';

  const modelOk = Boolean(health?.ok && health.data?.upstream_key_loaded);
  setDot($('#light-model .dot'), modelOk ? 'on' : 'off');
  $('#light-model .light-body').textContent = modelOk ? 'DeepSeek Key 已加载' : 'Key 未加载';

  // 卡片
  $('#val-addr').textContent = running ? `http://127.0.0.1:${state.cfg?.hubPort ?? 8787}/v1` : '—';
  $('#val-persona').textContent = health?.ok
    ? `${health.data.persona_chars ?? 0} 字${health.data.persona_id ? `（${health.data.persona_id}）` : ''}`
    : '—';
  $('#val-model').textContent = health?.ok ? health.data.upstream_model || '—' : '—';
  $('#val-python').textContent = state.cfg?.pythonPath || '—';
  const verEl = $('#val-version');
  if (verEl) verEl.textContent = health?.ok ? 'v' + (health.data.version || '—') : '—';

  // 按钮
  $('#btn-start').disabled = running;
  $('#btn-stop').disabled = !running;
}

// ---------- 健康检查 ----------
async function refresh() {
  const status = await window.hub.status();
  state.cfg = status;
  state.running = status.running;

  if (status.running) {
    const res = await window.hub.health();
    state.health = res;
  } else {
    state.health = null;
  }
  render();
}

// ---------- 日志 ----------
function appendLog(line) {
  const box = $('#logs');
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  const div = document.createElement('div');
  if (/\[py!\]|失败|error/i.test(line)) div.className = 'err';
  div.textContent = line;
  box.appendChild(div);
  if (atBottom) box.scrollTop = box.scrollHeight;
}

window.hub.onLog(appendLog);
window.hub.onState(() => refresh());

// ---------- 按钮 ----------
$('#btn-start').addEventListener('click', async () => {
  $('#btn-start').disabled = true;
  await window.hub.start();
  setTimeout(refresh, 800);
});

$('#btn-stop').addEventListener('click', async () => {
  await window.hub.stop();
  setTimeout(refresh, 400);
});

$('#btn-check').addEventListener('click', refresh);
$('#btn-clear').addEventListener('click', () => ($('#logs').textContent = ''));
$('#btn-open-logs').addEventListener('click', () => window.hub.revealLogs());

// ---------- 配置 ----------
async function loadConfig() {
  const cfg = await window.hub.getConfig();
  state.cfg = cfg;
  $('#cfg-port').value = cfg.hubPort;
  $('#cfg-python').value = cfg.pythonPath;
  $('#cfg-autostart').checked = Boolean(cfg.autoStartSidecar);
}

$('#btn-save').addEventListener('click', async () => {
  const patch = {
    hubPort: Number($('#cfg-port').value) || 8787,
    pythonPath: $('#cfg-python').value.trim(),
    autoStartSidecar: $('#cfg-autostart').checked,
  };
  state.cfg = await window.hub.setConfig(patch);
  appendLog(`配置已保存：端口 ${patch.hubPort}`);
  refresh();
});

// ---------- 初始化 ----------
(async () => {
  try {
    const info = await window.hub.appInfo();
    const el = document.getElementById('version');
    if (el) el.textContent = 'v' + info.version;
  } catch {
    /* 忽略 */
  }
  await loadConfig();
  await refresh();
  // 窗口隐藏时降频，避免无意义轮询
  setInterval(() => {
    if (!document.hidden) refresh();
  }, 8000);
})();
