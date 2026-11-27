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
    if (page === 'memory') loadMemories();
  });
});

// ---------- 灯光 ----------
function setDot(el, kind) {
  el.classList.remove('on', 'off', 'warn', 'err');
  el.classList.add(kind);
}

function render() {
  const { running, health } = state;

  if (running && !state.runningSince) state.runningSince = Date.now();
  if (!running) state.runningSince = null;

  // 英雄区
  const heroDot = $('#hero-dot');
  if (running && health?.ok) {
    setDot(heroDot, 'on');
    $('#hero-title').textContent = '服务运行中';
    $('#hero-sub').textContent = 'AIRI 可以连接了';
  } else if (running && state.runningSince && Date.now() - state.runningSince < 15000) {
    // 刚启动的 15 秒内不吓人：sidecar 起进程需要时间
    setDot(heroDot, 'warn');
    $('#hero-title').textContent = '正在启动服务…';
    $('#hero-sub').textContent = '等待本地服务响应';
  } else if (running) {
    setDot(heroDot, 'warn');
    $('#hero-title').textContent = '服务已启动，但未响应';
    $('#hero-sub').textContent = health?.error || '正在等待健康检查…';
  } else {
    setDot(heroDot, 'off');
    $('#hero-title').textContent = '服务未启动';
    $('#hero-sub').textContent = '点击「启动服务」开始';
  }
  document.body.dataset.state = !running ? 'off' : health?.ok ? 'on' : 'warn';

  // 按钮状态：运行中不允许重复启动，未运行不允许停止
  const startBtn = document.getElementById('btn-start');
  const stopBtn = document.getElementById('btn-stop');
  if (startBtn) startBtn.disabled = Boolean(running);
  if (stopBtn) stopBtn.disabled = !running;

  // 英雄区摘要
  const portEl = document.getElementById('hero-port');
  if (portEl) portEl.textContent = String(state.cfg?.hubPort ?? 8787);
  const hv = document.getElementById('hero-version');
  if (hv) hv.textContent = 'v' + (health?.ok ? health.data.version || '—' : state.appVersion || '—');

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
  $('#val-python').textContent = state.cfg?.pythonPath || '自动检测（AstrBot 内置）';
  const verEl = $('#val-version');
  if (verEl) verEl.textContent = health?.ok ? 'v' + (health.data.version || '—') : '—';

  // 最近流水线
  const pl = document.getElementById('pipeline-list');
  if (pl && health?.ok) {
    const p = health.data.pipeline || {};
    pl.innerHTML = '';
    for (const ev of (p.recent || []).slice(-6)) {
      const li = document.createElement('li');
      li.className = ev.outcome === 'ok' ? 'ok' : 'bad';
      const time = new Date((ev.ts || 0) * 1000).toLocaleTimeString('zh-CN', { hour12: false });
      li.textContent = `${time} ${ev.stage} ${ev.outcome}${ev.ms ? ' ' + ev.ms + 'ms' : ''}${ev.detail ? ' · ' + ev.detail : ''}`;
      pl.appendChild(li);
    }
    const failEl = document.getElementById('pipeline-fail');
    if (failEl) {
      failEl.textContent = p.last_failure
        ? `最后失败：${p.last_failure.stage} / ${p.last_failure.outcome}`
        : '';
      failEl.style.color = p.last_failure ? 'var(--bad)' : '';
    }
    const advPipe = document.getElementById('adv-pipeline');
    if (advPipe && p.last_failure) advPipe.open = true;
  }

  // 人格降级提示
  const pw = document.getElementById('persona-warn');
  if (pw) {
    const src = health?.data?.persona_source || '';
    const stale = Boolean(health?.data?.persona_stale);
    if (!health?.ok || !src || src === 'astrbot_db') {
      pw.hidden = true;
      pw.classList.remove('is-red', 'is-yellow');
    } else {
      pw.hidden = false;
      const title = document.getElementById('persona-warn-title');
      const text = document.getElementById('persona-warn-text');
      const red = src === 'fallback' || src === 'builtin' || stale;
      pw.classList.toggle('is-red', red);
      pw.classList.toggle('is-yellow', !red);
      if (title) title.textContent = red ? '人格未正常加载' : '人格使用缓存';
      if (text) {
        text.textContent =
          src === 'lkg'
            ? stale
              ? '缓存超过 24 小时，她可能不像她，请检查 AstrBot'
              : 'AstrBot 暂时读不到，正在用上次缓存'
            : src === 'fallback'
              ? '读取人格失败，正在用内置精简版'
              : '人格与兜底都不可用，正在用内置常量';
      }
    }
  }

  // 归档与写入队列
  const arcEl = document.getElementById('val-archive');
  if (arcEl) {
    const ob2 = health?.data?.outbox || {};
    const legacy2 = (health?.data?.memory?.archive?.by_status || {}).legacy ?? 0;
    arcEl.textContent = health?.ok
      ? `${health.data.archive_qq ?? 0} / ${health.data.archive_local ?? 0}${legacy2 ? ` · 旧账 ${legacy2}` : ''}`
      : '—';
  }
  const qEl = document.getElementById('val-queue');
  if (qEl) {
    if (!health?.ok) {
      qEl.textContent = '—';
    } else {
      const ob = health.data.outbox || {};
      const written = ob.written ?? 0;
      const verified = ob.verified ?? 0;
      const pending = (ob.pending ?? 0) + (ob.sending ?? 0);
      const dead = ob.dead ?? 0;
      const failed = ob.failed ?? 0;
      const parts = [`已写入 ${written}`, `验证 ${verified}`, `待处理 ${pending}`];
      if (dead > 0) parts.push(`死信 ${dead}`);
      else if (failed > 0) parts.push(`失败 ${failed}`);
      qEl.textContent = parts.join(' · ');
      qEl.style.color = dead > 0 || failed > 0 ? 'var(--warn)' : '';
    }
  }

  // 记忆体检
  const mem = health?.data?.memory || {};
  const arc = mem.archive || {};
  const lm = mem.lm || {};
  const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };
  setText('mem-archive', arc.total ?? '—');
  setText('mem-docs', lm.documents ?? '—');
  setText('mem-atoms', lm.atoms ?? '—');
  setText('mem-nodes', lm.graph_nodes ?? '—');
  setText('mem-notopics', arc.no_topics ?? '—');
  const legacy = (arc.by_status || {}).legacy ?? 0;
  const deadLetters = (health?.data?.outbox || {}).dead ?? 0;
  setText('mem-legacy', `${legacy} / ${deadLetters}`);
  const memSummary = document.getElementById('memory-summary');
  if (memSummary) {
    memSummary.textContent = health?.ok
      ? `${arc.total ?? 0} 条归档 · ${lm.documents ?? 0} 文档 · ${lm.graph_edges ?? 0} 条图谱关系`
      : '归档与记忆库的规模、脏数据';
  }

  const aboutVer = document.getElementById('about-version');
  if (aboutVer) aboutVer.textContent = 'v' + (state.appVersion || '—');
  const aboutSide = document.getElementById('about-sidecar');
  if (aboutSide) aboutSide.textContent = health?.ok ? 'v' + (health.data.version || '—') : '未连接';

  // 按钮
  $('#btn-start').disabled = running;
  $('#btn-stop').disabled = !running;
}

// ---------- 连通性自检 ----------
function renderSelftest(res) {
  const list = document.getElementById('selftest-steps');
  const status = document.getElementById('selftest-status');
  if (!list) return;
  list.innerHTML = '';
  if (!res || !res.steps) {
    if (status) status.textContent = res && res.error ? '失败：' + res.error : '没有结果';
    return;
  }
  for (const s of res.steps) {
    const li = document.createElement('li');
    li.className = s.ok ? 'ok' : 'fail';
    li.textContent = `${s.ok ? '✅' : '❌'} ${s.name}${s.detail ? '：' + s.detail : ''}`;
    list.appendChild(li);
  }
  if (status) status.textContent = res.ok ? '全部通过' : '有步骤未通过';
  const adv = document.getElementById('adv-selftest');
  if (adv && !res.ok) adv.open = true;
}

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('btn-selftest');
  if (btn) {
    btn.addEventListener('click', async () => {
      const status = document.getElementById('selftest-status');
      const list = document.getElementById('selftest-steps');
      if (status) status.textContent = '正在自检…';
      if (list) list.innerHTML = '';
      btn.disabled = true;
      try {
        const res = await window.hub.selftest();
        renderSelftest(res);
        appendLog(res.ok ? '连通性自检通过' : '连通性自检未通过');
      } finally {
        btn.disabled = false;
      }
    });
  }

});

// ---------- 导出归档密钥 ----------
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('btn-export-key');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const res = await window.hub.exportArchiveKey();
    if (res.canceled) return;
    appendLog(res.ok ? `归档密钥已导出：${res.path}` : `导出失败：${res.error || ''}`);
  });
});

// ---------- 记忆原子补丁 ----------
// sidecar 用 state='ok' 表示补丁在位；旧 JS 用过 'patched'，两者都算健康
const PATCH_HEALTHY = new Set(['ok', 'patched']);
const PATCH_IRRELEVANT = new Set(['no_plugin', 'missing']);
const PATCH_WARN = new Set(['unpatched', 'anchor_mismatch', 'anchor_missing', 'unknown_version']);

function patchStateText(st) {
  const labels = {
    unpatched: '记忆原子补丁缺失',
    anchor_mismatch: '插件结构已变，补丁需人工确认',
    anchor_missing: '插件结构已变，找不到补丁锚点',
    unknown_version: '插件版本不在已知列表，需人工确认',
  };
  return labels[st.state] || '记忆原子补丁异常';
}

async function refreshPatch() {
  try {
    const st = await window.hub.patchStatus();
    const box = document.getElementById('patch-warn');
    if (!box) return;
    // sidecar 还没起来 / 请求失败时不要误报「补丁异常」
    if (!st || !st.state || st.error) {
      box.hidden = true;
      return;
    }
    const healthy = PATCH_HEALTHY.has(st.state) || PATCH_IRRELEVANT.has(st.state);
    // 未知状态也先不报警，只有明确的问题态才提示
    box.hidden = healthy || !PATCH_WARN.has(st.state);
    if (!box.hidden) {
      const title = box.querySelector('strong');
      const txt = document.getElementById('patch-warn-text');
      if (title) title.textContent = patchStateText(st);
      if (txt) txt.textContent = st.detail || '桌宠写的记忆不会生成原子，QQ 侧召回会变差。';
    }
  } catch {
    /* 忽略 */
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('btn-patch-fix');
  if (btn) {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        const res = await window.hub.patchApply();
        if (res && res.ok) {
          appendLog('记忆原子补丁已修复（插件已重载）');
        } else {
          appendLog('修复失败: ' + ((res && (res.detail || res.state)) || '未知原因'));
        }
        await refreshPatch();
      } finally {
        btn.disabled = false;
      }
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

// ---------- 记忆浏览 ----------
let memQuery = '';

async function loadMemories() {
  const list = document.getElementById('mem-list');
  const empty = document.getElementById('mem-empty');
  const count = document.getElementById('mem-count');
  if (!list) return;
  list.innerHTML = '<div class="mem-empty">读取中…</div>';
  try {
    const showSelftest = document.getElementById('mem-show-selftest');
    const res = await window.hub.memories({
      query: memQuery,
      limit: 50,
      include_selftest: Boolean(showSelftest && showSelftest.checked),
    });
    if (!res || res.ok === false) {
      list.innerHTML = '';
      empty.hidden = false;
      empty.textContent = (res && res.error) || '记忆库读取失败';
      return;
    }
    list.innerHTML = '';
    const items = res.items || [];
    if (count) count.textContent = `共 ${res.total} 条，显示 ${items.length} 条`;
    empty.hidden = items.length > 0;
    for (const item of items) {
      const row = document.createElement('div');
      row.className = 'mem-row';

      const main = document.createElement('div');
      main.className = 'mem-main';
      const text = document.createElement('div');
      text.className = 'mem-text';
      text.textContent = item.text || '';
      text.title = '点击展开 / 收起';
      text.addEventListener('click', () => text.classList.toggle('expanded'));
      const meta = document.createElement('div');
      meta.className = 'mem-meta';
      const imp = item.importance == null ? '—' : Number(item.importance).toFixed(2);
      const when = item.created_at ? String(item.created_at).slice(0, 16).replace('T', ' ') : '—';
      meta.textContent = `#${item.id} · 重要度 ${imp} · ${when}${item.session_id ? ' · ' + item.session_id : ''}`;
      main.appendChild(text);
      main.appendChild(meta);

      const btn = document.createElement('button');
      btn.className = 'mem-del';
      btn.textContent = '删除';
      let armed = false;
      let timer = null;
      btn.addEventListener('click', async () => {
        if (!armed) {
          armed = true;
          btn.classList.add('confirm');
          btn.textContent = '确认删除';
          timer = setTimeout(() => {
            armed = false;
            btn.classList.remove('confirm');
            btn.textContent = '删除';
          }, 3000);
          return;
        }
        clearTimeout(timer);
        btn.disabled = true;
        const res2 = await window.hub.memoryDelete(item.id);
        if (res2 && res2.ok) {
          appendLog(`已删除记忆 #${item.id}（备份 ${String(res2.backup || '').split('\\').pop()}）`);
          row.remove();
        } else {
          btn.disabled = false;
          btn.textContent = '删除';
          btn.classList.remove('confirm');
          appendLog('删除失败: ' + ((res2 && res2.error) || '未知原因'));
        }
      });

      row.appendChild(main);
      row.appendChild(btn);
      list.appendChild(row);
    }
  } catch (e) {
    list.innerHTML = '';
    empty.hidden = false;
    empty.textContent = '读取失败：' + e.message;
  }
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

// 前端自身出错也要看得见
window.addEventListener('error', (e) => appendLog('[ui!] ' + (e.message || e.error || 'unknown')));
window.addEventListener('unhandledrejection', (e) => {
  const r = e.reason;
  appendLog('[ui!] ' + ((r && (r.message || r.stack)) || String(r)));
});

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

if ($('#btn-memory-refresh')) {
  $('#btn-memory-refresh').addEventListener('click', () => loadMemories());
}
if ($('#mem-show-selftest')) {
  $('#mem-show-selftest').addEventListener('change', () => loadMemories());
}

if ($('#mem-search')) {
  let timer = null;
  $('#mem-search').addEventListener('input', (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      memQuery = e.target.value.trim();
      loadMemories();
    }, 250);
  });
}
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

// ---------- 归档范围（隐私） ----------
async function loadArchiveConfig() {
  try {
    const cfg = await window.hub.archiveConfig();
    if (!cfg) return;
    const mode = document.getElementById('cfg-scope-mode');
    if (mode) mode.value = cfg.scope_mode || 'private_or_whitelist';
    const wl = document.getElementById('cfg-whitelist');
    if (wl) wl.value = (cfg.whitelist_groups || []).join(', ');
    const edit = document.getElementById('cfg-memory-edit');
    if (edit) edit.checked = cfg.memory_edit_enabled !== false;
  } catch (e) {
    appendLog('[ui!] 隐私配置读取失败: ' + e.message);
  }
}

const savePrivacyBtn = document.getElementById('btn-save-privacy');
if (savePrivacyBtn) {
  savePrivacyBtn.addEventListener('click', async () => {
    const mode = document.getElementById('cfg-scope-mode');
    const wl = document.getElementById('cfg-whitelist');
    const edit = document.getElementById('cfg-memory-edit');
    const groups = (wl ? wl.value : '')
      .split(/[,，\s]+/)
      .map((x) => x.trim())
      .filter(Boolean);
    const res = await window.hub.setArchiveConfig({
      scope_mode: mode ? mode.value : 'private_or_whitelist',
      whitelist_groups: groups,
      memory_edit_enabled: edit ? edit.checked : true,
    });
    const status = document.getElementById('privacy-status');
    if (status) status.textContent = res && res.scope_mode ? '已保存，下一轮同步生效' : '保存失败';
    appendLog('隐私设置已保存：' + ((res && res.scope_mode) || '未知'));
  });
}

// ---------- 初始化 ----------
// 窗口隐藏时降频，避免无意义轮询；先启动，避免初始化异常把轮询一起带走
setInterval(() => {
  if (!document.hidden) refresh().catch((e) => appendLog('[ui!] 刷新失败: ' + e.message));
}, 8000);

setInterval(() => {
  if (!document.hidden) refreshPatch();
}, 60000);

(async () => {
  try {
    const info = await window.hub.appInfo();
    state.appVersion = info.version;
    const el = document.getElementById('version');
    if (el) el.textContent = 'v' + info.version;
  } catch {
    /* 忽略 */
  }
  try {
    await loadConfig();
    await loadArchiveConfig();
    await refresh();
    await refreshPatch();
  } catch (e) {
    appendLog('[ui!] 初始化失败: ' + (e && e.message));
  }
})();
