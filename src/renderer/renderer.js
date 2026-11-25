const $ = (sel) => document.querySelector(sel);

const state = {
  running: false,
  health: null,
  cfg: null,
};

// ---------- 主题 ----------
const THEME_KEY = 'hub-theme';

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  const label = document.getElementById('theme-label');
  if (label) label.textContent = theme === 'light' ? '浅色' : '深色';
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* 忽略 */
  }
}

function initTheme() {
  let theme = 'dark';
  try {
    theme = localStorage.getItem(THEME_KEY) || 'dark';
  } catch {
    /* 忽略 */
  }
  applyTheme(theme);
  const btn = document.getElementById('btn-theme');
  if (btn) {
    btn.addEventListener('click', () => {
      const now = document.documentElement.getAttribute('data-theme');
      applyTheme(now === 'light' ? 'dark' : 'light');
    });
  }
}

initTheme();

// ---------- 页面切换 ----------
document.querySelectorAll('.nav-item:not(.disabled)').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    const page = btn.dataset.page;
    document.querySelectorAll('.page').forEach((p) => p.classList.remove('active'));
    $(`#page-${page}`).classList.add('active');
    if (page === 'params' && typeof loadParams === 'function' && !window.__paramsLoaded) {
      window.__paramsLoaded = true;
      loadParams();
    }
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

  // 归档与写入队列
  const arcEl = document.getElementById('val-archive');
  if (arcEl) {
    arcEl.textContent = health?.ok ? `${health.data.archive_qq ?? 0} / ${health.data.archive_local ?? 0}` : '—';
  }
  const qEl = document.getElementById('val-queue');
  if (qEl) {
    if (!health?.ok) {
      qEl.textContent = '—';
    } else {
      const pending = health.data.pending_retry ?? 0;
      const dead = health.data.dead_letters ?? 0;
      qEl.textContent = dead > 0 ? `待重试 ${pending} · 死信 ${dead}` : `待重试 ${pending}`;
      qEl.style.color = dead > 0 ? 'var(--warn)' : '';
    }
  }

  const aboutVer = document.getElementById('about-version');
  if (aboutVer) aboutVer.textContent = 'v' + (state.appVersion || '—');
  const aboutSide = document.getElementById('about-sidecar');
  if (aboutSide) aboutSide.textContent = health?.ok ? 'v' + (health.data.version || '—') : '未连接';

  // 按钮
  $('#btn-start').disabled = running;
  $('#btn-stop').disabled = !running;
}

// ---------- 记忆原子补丁 ----------
async function refreshPatch() {
  try {
    const st = await window.hub.patchStatus();
    const box = document.getElementById('patch-warn');
    if (!box) return;
    if (st.state === 'patched' || st.state === 'missing') {
      box.hidden = true;
    } else {
      box.hidden = false;
      const txt = document.getElementById('patch-warn-text');
      if (txt) txt.textContent = st.hint || '补丁缺失，桌宠写的记忆不会生成原子。';
    }
  } catch {
    /* 忽略 */
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('btn-patch-fix');
  if (btn) {
    btn.addEventListener('click', async () => {
      const res = await window.hub.patchApply();
      appendLog(res.ok ? '记忆原子补丁已修复' : '修复失败: ' + (res.hint || res.state));
      await refreshPatch();
    });
  }
});

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
  try {
    const p = await window.hub.configPath();
    const el = document.getElementById('cfg-path');
    if (el) el.textContent = p;
  } catch {
    /* 忽略 */
  }
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
    state.appVersion = info.version;
    const el = document.getElementById('version');
    if (el) el.textContent = 'v' + info.version;
  } catch {
    /* 忽略 */
  }
  await loadConfig();
  await refresh();
  await refreshPatch();
  // 窗口隐藏时降频，避免无意义轮询
  setInterval(() => {
    if (!document.hidden) refresh();
  }, 8000);
})();
