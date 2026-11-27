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
      const next = now === 'light' ? 'dark' : 'light';
      applyTheme(next);
      toast(next === 'light' ? '已切换到浅色' : '已切换到深色', 'info', 1600);
    });
  }
}

initTheme();


// ---------- 帮助弹窗 ----------
const HELP = {
  status: {
    title: '状态页说明',
    sections: [
      ['连接', [
        '本地服务：中转站在 127.0.0.1:{port} 监听，端口可在「配置」页修改。',
        '记忆库：本地记忆插件的外部接口是否可用，具体地址由插件配置决定。',
        '上游模型：当前模型 {model} 的 Key 是否加载成功。',
      ]],
      ['诊断', [
        '自检：写入一条测试数据，验证 配置 → 记忆库 → 写入 → 检索 整条链路。',
        '流水线：最近几轮对话卡在哪一步，失败时自动展开。',
        '记忆体检：归档条数、记忆文档、原子、图谱节点、无关键词、旧账与死信。',
      ]],
      ['客户端接入', [
        '在客户端里选择「OpenAI 兼容」来源，填三项：',
        'Base URL：http://127.0.0.1:{port}/v1',
        'API Key：local（随便填，只要不为空）',
        '模型：{model}（以状态页显示为准）',
      ]],
    ],
  },
  params: {
    title: '参数页说明',
    sections: [
      ['保存与生效', [
        '插件参数保存在对应插件自己的配置里，保存后需要重载插件才生效。',
        '中转站自身的参数改动需要重启服务。',
        '不同版本的插件，可调参数可能不同。',
      ]],
    ],
  },
  config: {
    title: '配置页说明',
    sections: [
      ['启动设置', [
        '端口：默认 8787，被占用时可改；改完重启服务。',
        'Python 路径：指向运行中转站所用的解释器，通常用机器人框架自带的那个。',
      ]],
      ['归档密钥', [
        '归档内容加密保存，密钥由当前 Windows 账户保护。',
        '换电脑或重装系统前必须导出，否则旧归档解不开。',
      ]],
      ['归档范围', [
        '我的私聊 + 指定群：默认；群号填在下方，用逗号分隔。',
        '只同步我的私聊：群聊完全不进归档。',
        '全部：不推荐，群友和他人私聊也会入档。',
        '桌面端对话始终记录，不受这里影响。',
      ]],
      ['记忆编辑', [
        '开启后可在「记忆」页删除单条记忆，删除前自动备份。',
      ]],
    ],
  },
  logs: {
    title: '日志页说明',
    sections: [
      ['怎么看', [
        '实时输出运行日志，失败行标红。',
        '排查时先看最近一条红色日志，再到状态页看流水线。',
      ]],
    ],
  },
  memory: {
    title: '记忆页说明',
    sections: [
      ['筛选', [
        '时间范围：今天 / 昨天 / 本周 / 本月，或自定义起止日期。',
        '来源：私聊 / 群聊 / 桌面。',
        '重要度：0–1，越大越值得长期保留。',
        '搜索会匹配记忆原文。',
      ]],
      ['阅读与删除', [
        '左侧每条只显示摘要，点一下在右侧看全文和元信息。',
        '删除需要点两次确认，删除前会自动备份数据库。',
      ]],
    ],
  },
  about: {
    title: '关于',
    sections: [
      ['定位', [
        '本地中转服务，让聊天机器人和桌面宠物共享同一套人格与记忆。',
        '只监听本机，不向外上传数据。',
        '具体依赖与版本以实际安装为准。',
      ]],
    ],
  },
};

function helpContext() {
  const port = state.cfg?.hubPort ?? 8787;
  const model = state.health?.data?.upstream_model || '当前上游模型';
  const persona = state.health?.data?.persona_id || '当前人格';
  return { port, model, persona };
}

function fillHelp(text) {
  const ctx = helpContext();
  return String(text).replace(/\{(\w+)\}/g, (_, key) => (key in ctx ? String(ctx[key]) : `{${key}}`));
}

function openHelp(page) {
  const data = HELP[page] || HELP.status;
  const modal = document.getElementById('help-modal');
  const title = document.getElementById('help-title');
  const body = document.getElementById('help-body');
  if (!modal || !title || !body) return;
  title.textContent = data.title;
  body.innerHTML = '';
  for (const [heading, lines] of data.sections) {
    const h = document.createElement('h3');
    h.textContent = heading;
    body.appendChild(h);
    const ul = document.createElement('ul');
    for (const line of lines) {
      const li = document.createElement('li');
      li.textContent = fillHelp(line);
      ul.appendChild(li);
    }
    body.appendChild(ul);
  }
  modal.hidden = false;
}

function closeHelp() {
  const modal = document.getElementById('help-modal');
  if (modal) modal.hidden = true;
}

document.addEventListener('click', (e) => {
  const btn = e.target.closest('.help-btn');
  if (btn) openHelp(btn.dataset.help);
  if (e.target.id === 'help-modal') closeHelp();
});

const helpClose = document.getElementById('help-close');
if (helpClose) helpClose.addEventListener('click', closeHelp);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeHelp();
});


// ---------- 交互反馈 ----------
function toast(message, type = 'info', ms = 2600) {
  const stack = document.getElementById('toast-stack');
  if (!stack) return;
  const item = document.createElement('div');
  item.className = `toast ${type}`;
  item.textContent = message;
  stack.appendChild(item);
  setTimeout(() => {
    item.classList.add('out');
    setTimeout(() => item.remove(), 200);
  }, ms);
}

async function withBusy(btn, task, opts = {}) {
  if (!btn) return task();
  if (btn.dataset.busy === '1') return undefined;
  btn.dataset.busy = '1';
  btn.classList.add('loading');
  try {
    const result = await task();
    if (opts.success) {
      toast(typeof opts.success === 'function' ? opts.success(result) : opts.success, 'ok');
    }
    return result;
  } catch (e) {
    toast((opts.error || '操作失败') + '：' + (e && e.message ? e.message : e), 'bad', 4200);
    return undefined;
  } finally {
    delete btn.dataset.busy;
    btn.classList.remove('loading');
  }
}

window.toast = toast;
window.withBusy = withBusy;

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
    $('#hero-sub').textContent = '客户端可以连接了';
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
      ? `${health.data.archive_qq ?? 0} / ${health.data.archive_local ?? 0}`
      : '—';
    arcEl.title = legacy2 ? `旧账 ${legacy2} 条` : '';
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
      const parts = [`写入 ${written}`, `待处理 ${pending}`];
      if (dead > 0) parts.push(`死信 ${dead}`);
      else if (failed > 0) parts.push(`失败 ${failed}`);
      qEl.textContent = parts.join(' · ');
      qEl.title = `已验证 ${verified} 条`;
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
      await withBusy(btn, async () => {
        const res = await window.hub.selftest();
        renderSelftest(res);
        appendLog(res.ok ? '连通性自检通过' : '连通性自检未通过');
        toast(res && res.ok ? '自检全部通过' : '自检有步骤未通过', res && res.ok ? 'ok' : 'bad', 4000);
      });
    });
  }

});

// ---------- 导出归档密钥 ----------
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('btn-export-key');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    await withBusy(btn, async () => {
      const res = await window.hub.exportArchiveKey();
      if (res.canceled) {
        toast('已取消导出', 'info');
        return;
      }
      appendLog(res.ok ? `归档密钥已导出：${res.path}` : `导出失败：${res.error || ''}`);
      toast(res.ok ? '归档密钥已导出' : '导出失败：' + (res.error || ''), res.ok ? 'ok' : 'bad', 4000);
    });
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
      await withBusy(btn, async () => {
        const res = await window.hub.patchApply();
        if (res && res.ok) {
          appendLog('记忆原子补丁已修复（插件已重载）');
          toast('补丁已修复并重载插件', 'ok');
        } else {
          appendLog('修复失败: ' + ((res && (res.detail || res.state)) || '未知原因'));
          toast('修复失败：' + ((res && (res.detail || res.state)) || '未知原因'), 'bad', 4200);
        }
        await refreshPatch();
      });
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
const memState = {
  query: '',
  kind: 'all',
  sort: 'new',
  minImportance: 0,
  range: 'all',
  dateFrom: '',
  dateTo: '',
  includeSelftest: false,
  offset: 0,
  items: [],
  total: 0,
  selected: null,
};

const MEM_KIND_LABEL = { private: '私聊', group: '群聊', desktop: '桌面' };

function memDateLabel(iso) {
  if (!iso) return '更早';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '更早';
  const today = new Date();
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const now = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const diff = Math.round((now - day) / 86400000);
  if (diff <= 0) return '今天';
  if (diff === 1) return '昨天';
  if (diff < 7) return '本周';
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
}

function memTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function memImportanceClass(v) {
  const n = Number(v || 0);
  if (n >= 0.7) return 'high';
  if (n >= 0.5) return 'mid';
  return 'low';
}

function renderMemoryList() {
  const list = document.getElementById('mem-list');
  const empty = document.getElementById('mem-empty');
  const more = document.getElementById('btn-mem-more');
  if (!list) return;
  list.innerHTML = '';
  empty.hidden = memState.items.length > 0;
  empty.textContent = memState.items.length ? '' : '没有匹配的记忆';
  more.hidden = memState.items.length >= memState.total;

  let lastGroup = null;
  for (const item of memState.items) {
    const group = memDateLabel(item.created_at);
    if (group !== lastGroup) {
      const h = document.createElement('div');
      h.className = 'mem-group';
      h.textContent = group;
      list.appendChild(h);
      lastGroup = group;
    }

    const row = document.createElement('div');
    row.className = 'mem-item' + (memState.selected === item.id ? ' selected' : '');
    row.dataset.id = item.id;

    const top = document.createElement('div');
    top.className = 'mem-item-top';
    const kind = document.createElement('span');
    kind.className = `mem-kind ${item.kind || 'desktop'}`;
    kind.textContent = MEM_KIND_LABEL[item.kind] || '其他';
    const time = document.createElement('span');
    time.className = 'mem-time';
    time.textContent = memTime(item.created_at);
    const imp = document.createElement('span');
    imp.className = `mem-imp ${memImportanceClass(item.importance)}`;
    imp.textContent = item.importance == null ? '—' : Number(item.importance).toFixed(2);
    top.appendChild(kind);
    top.appendChild(time);
    top.appendChild(imp);

    const text = document.createElement('div');
    text.className = 'mem-item-text';
    text.textContent = item.summary || item.text || '';

    row.appendChild(top);
    row.appendChild(text);
    row.addEventListener('click', () => selectMemory(item.id));
    list.appendChild(row);
  }
}

function selectMemory(id) {
  const item = memState.items.find((x) => x.id === id);
  if (!item) return;
  memState.selected = id;
  renderMemoryList();

  const panel = document.getElementById('mem-detail');
  if (!panel) return;
  panel.hidden = false;
  document.getElementById('mem-detail-id').textContent = `#${item.id}`;
  document.getElementById('mem-detail-text').textContent = item.text || '';
  document.getElementById('mem-detail-meta').innerHTML = '';

  const meta = [
    ['来源', (MEM_KIND_LABEL[item.kind] || '其他') + (item.session_id ? ` · ${item.session_id}` : '')],
    ['重要度', item.importance == null ? '—' : Number(item.importance).toFixed(2)],
    ['记录时间', memTime(item.created_at)],
    ['文档 ID', item.doc_id || '—'],
  ];
  for (const [k, v] of meta) {
    const line = document.createElement('div');
    line.className = 'mem-meta-line';
    const kk = document.createElement('span');
    kk.textContent = k;
    const vv = document.createElement('strong');
    vv.textContent = v;
    line.appendChild(kk);
    line.appendChild(vv);
    document.getElementById('mem-detail-meta').appendChild(line);
  }

  const factsBox = document.getElementById('mem-detail-facts');
  factsBox.innerHTML = '';
  const facts = Array.isArray(item.key_facts) ? item.key_facts : [];
  for (const f of facts.slice(0, 8)) {
    const chip = document.createElement('span');
    chip.className = 'mem-fact';
    chip.textContent = typeof f === 'string' ? f : JSON.stringify(f);
    factsBox.appendChild(chip);
  }

  const status = document.getElementById('mem-detail-status');
  status.textContent = '';
  const btn = document.getElementById('mem-detail-delete');
  btn.disabled = false;
  btn.textContent = '删除这条';
  btn.classList.remove('confirm');
  btn.onclick = async () => {
    if (!btn.classList.contains('confirm')) {
      btn.classList.add('confirm');
      btn.textContent = '再点一次确认删除';
      setTimeout(() => {
        btn.classList.remove('confirm');
        btn.textContent = '删除这条';
      }, 3000);
      return;
    }
    btn.disabled = true;
    await withBusy(btn, async () => {
      const res = await window.hub.memoryDelete(item.id);
      if (res && res.ok) {
        appendLog(`已删除记忆 #${item.id}（备份 ${String(res.backup || '').split('\\').pop()}）`);
        toast(`已删除记忆 #${item.id}`, 'ok');
        panel.hidden = true;
        memState.selected = null;
        await loadMemories(true);
      } else {
        status.textContent = (res && res.error) || '删除失败';
        toast('删除失败：' + ((res && res.error) || '未知原因'), 'bad', 4000);
        btn.textContent = '删除这条';
        btn.classList.remove('confirm');
      }
    });
    btn.disabled = false;
  };
}

async function loadMemories(reset = true) {
  const list = document.getElementById('mem-list');
  if (!list) return;
  if (reset) {
    memState.offset = 0;
    memState.items = [];
    list.innerHTML = '<div class="mem-loading">读取中…</div>';
  }

  try {
    const res = await window.hub.memories({
      query: memState.query,
      kind: memState.kind,
      sort: memState.sort,
      min_importance: memState.minImportance,
      date_from: memState.dateFrom,
      date_to: memState.dateTo,
      include_selftest: memState.includeSelftest,
      limit: 40,
      offset: memState.offset,
    });
    if (!res || res.ok === false) {
      list.innerHTML = '';
      document.getElementById('mem-empty').hidden = false;
      document.getElementById('mem-empty').textContent = (res && res.error) || '记忆库读取失败';
      return;
    }
    memState.total = res.total || 0;
    memState.items = reset ? res.items : memState.items.concat(res.items);
    memState.offset = memState.items.length;

    const facets = res.facets || {};
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el) el.textContent = v;
    };
    set('mem-stat-total', facets.all ?? res.total ?? 0);
    set('mem-stat-private', facets.private ?? 0);
    set('mem-stat-group', facets.group ?? 0);
    set('mem-stat-desktop', facets.desktop ?? 0);

    for (const tab of document.querySelectorAll('.mem-tab')) {
      tab.classList.toggle('active', tab.dataset.kind === memState.kind);
    }
    renderMemoryList();

    // 默认展开第一条，右侧不空着
    if (!memState.selected && memState.items.length) {
      selectMemory(memState.items[0].id);
    }
  } catch (e) {
    list.innerHTML = '';
    document.getElementById('mem-empty').hidden = false;
    document.getElementById('mem-empty').textContent = '读取失败：' + e.message;
  }
}

function isoDate(d) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function rangeDates(range) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (range === 'today') return { from: isoDate(today), to: isoDate(today) };
  if (range === 'yesterday') {
    const d = new Date(today);
    d.setDate(d.getDate() - 1);
    return { from: isoDate(d), to: isoDate(d) };
  }
  if (range === 'week') {
    const d = new Date(today);
    const day = (d.getDay() + 6) % 7; // 周一为一周起点
    d.setDate(d.getDate() - day);
    return { from: isoDate(d), to: isoDate(today) };
  }
  if (range === 'month') {
    return { from: isoDate(new Date(today.getFullYear(), today.getMonth(), 1)), to: isoDate(today) };
  }
  return { from: '', to: '' };
}

function applyRange(range) {
  memState.range = range;
  const custom = document.getElementById('mem-custom');
  if (range === 'custom') {
    if (custom) custom.hidden = false;
    const from = document.getElementById('mem-from');
    const to = document.getElementById('mem-to');
    memState.dateFrom = (from && from.value) || '';
    memState.dateTo = (to && to.value) || '';
  } else {
    if (custom) custom.hidden = true;
    const dates = rangeDates(range);
    memState.dateFrom = dates.from;
    memState.dateTo = dates.to;
  }
  loadMemories(true);
}

function bindMemoryPage() {
  const refresh = document.getElementById('btn-memory-refresh');
  if (refresh) refresh.addEventListener('click', () => withBusy(refresh, () => loadMemories(true), { success: '记忆已刷新' }));

  const search = document.getElementById('mem-search');
  if (search) {
    let timer = null;
    search.addEventListener('input', (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        memState.query = e.target.value.trim();
        loadMemories(true);
      }, 250);
    });
  }

  const sort = document.getElementById('mem-sort');
  if (sort) sort.addEventListener('change', () => { memState.sort = sort.value; loadMemories(true); });

  const min = document.getElementById('mem-min');
  if (min) min.addEventListener('change', () => { memState.minImportance = Number(min.value) || 0; loadMemories(true); });

  const range = document.getElementById('mem-range');
  if (range) range.addEventListener('change', () => applyRange(range.value));

  const from = document.getElementById('mem-from');
  const to = document.getElementById('mem-to');
  if (from) from.addEventListener('change', () => applyRange('custom'));
  if (to) to.addEventListener('change', () => applyRange('custom'));

  const selftest = document.getElementById('mem-show-selftest');
  if (selftest) selftest.addEventListener('change', () => { memState.includeSelftest = selftest.checked; loadMemories(true); });

  for (const tab of document.querySelectorAll('.mem-tab')) {
    tab.addEventListener('click', () => {
      memState.kind = tab.dataset.kind || 'all';
      loadMemories(true);
    });
  }

  const more = document.getElementById('btn-mem-more');
  if (more) more.addEventListener('click', () => withBusy(more, () => loadMemories(false)));

  const close = document.getElementById('mem-detail-close');
  if (close) close.addEventListener('click', () => {
    const panel = document.getElementById('mem-detail');
    if (panel) panel.hidden = true;
    memState.selected = null;
    renderMemoryList();
  });
}

bindMemoryPage();

// ---------- 日志 ----------// ---------- 日志 ----------
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
$('#btn-start').addEventListener('click', () =>
  withBusy($('#btn-start'), async () => {
    await window.hub.start();
    setTimeout(refresh, 800);
  }, { success: '启动指令已发送' })
);

$('#btn-stop').addEventListener('click', () =>
  withBusy($('#btn-stop'), async () => {
    await window.hub.stop();
    setTimeout(refresh, 400);
  }, { success: '已停止服务' })
);

$('#btn-check').addEventListener('click', () =>
  withBusy($('#btn-check'), refresh, { success: '已重新检测' })
);

$('#btn-clear').addEventListener('click', () => {
  $('#logs').textContent = '';
  toast('日志已清空', 'ok');
});

$('#btn-open-logs').addEventListener('click', () =>
  withBusy($('#btn-open-logs'), () => window.hub.revealLogs(), { success: '已打开数据目录' })
);

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

$('#btn-save').addEventListener('click', () =>
  withBusy($('#btn-save'), async () => {
    const patch = {
      hubPort: Number($('#cfg-port').value) || 8787,
      pythonPath: $('#cfg-python').value.trim(),
      autoStartSidecar: $('#cfg-autostart').checked,
    };
    state.cfg = await window.hub.setConfig(patch);
    appendLog(`配置已保存：端口 ${patch.hubPort}`);
    refresh();
  }, { success: '配置已保存' })
);

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
    await withBusy(savePrivacyBtn, async () => {
      const res = await window.hub.setArchiveConfig({
        scope_mode: mode ? mode.value : 'private_or_whitelist',
        whitelist_groups: groups,
        memory_edit_enabled: edit ? edit.checked : true,
      });
      const status = document.getElementById('privacy-status');
      if (status) status.textContent = res && res.scope_mode ? '已保存，下一轮同步生效' : '保存失败';
      appendLog('隐私设置已保存：' + ((res && res.scope_mode) || '未知'));
      toast(res && res.scope_mode ? '隐私设置已保存' : '保存失败', res && res.scope_mode ? 'ok' : 'bad');
    });
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
