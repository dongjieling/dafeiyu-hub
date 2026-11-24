const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

// ---------- 插件与配置的路径 ----------
function astrbotDataDir() {
  return path.join(os.homedir(), '.astrbot', 'data');
}

function pluginDir() {
  return path.join(astrbotDataDir(), 'plugins');
}

function pluginConfigPath(pluginKey) {
  return path.join(astrbotDataDir(), 'config', `astrbot_plugin_${pluginKey}_config.json`);
}

// ---------- 声明式插件清单 ----------
// 每个插件声明：目录名、最低版本、可调字段（dot path）
const PLUGIN_SPECS = [
  {
    key: 'livingmemory',
    name: 'LivingMemory',
    desc: '长期记忆引擎',
    dirName: 'astrbot_plugin_livingmemory',
    minVersion: '2.14.0',
    fields: [
      { path: 'recall_engine.top_k', label: '每轮召回条数', type: 'number', min: 1, max: 20, hint: '越大越全面，也越慢' },
      { path: 'recall_engine.min_similarity_for_retrieval', label: '最低相似度', type: 'number', min: 0, max: 1, step: 0.05, hint: '低于此分数不召回' },
      { path: 'reflection_engine.summary_trigger_rounds', label: '记忆写入频率（轮）', type: 'number', min: 1, max: 20, hint: '越小同步越快，记忆越碎' },
      { path: 'graph_memory.atom_enabled', label: '启用记忆原子', type: 'boolean', hint: '关闭后召回粒度变粗' },
      { path: 'external_api.enabled', label: '开放外部记忆 API', type: 'boolean', hint: '中转站依赖此项' },
      { path: 'external_api.listen_port', label: '外部 API 端口', type: 'number', min: 1024, max: 65535 },
    ],
  },
  {
    key: 'affection',
    name: '情绪管理',
    desc: '好感度与情绪状态',
    dirName: 'astrbot_plugin_affection',
    minVersion: '1.3.0',
    fields: [
      { path: 'initial_affection', label: '初始好感度', type: 'number', min: 0, max: 100 },
      { path: 'initial_libido_other', label: '对他·热情', type: 'number', min: 0, max: 100 },
      { path: 'initial_aggression_other', label: '对他·攻击性', type: 'number', min: 0, max: 100 },
      { path: 'modify_sensitivity', label: '情绪敏感度', type: 'number', min: 0, max: 100, hint: '越高波动越大' },
      { path: 'decay_duration_hours', label: '情绪衰减（小时）', type: 'number', min: 0, max: 48, step: 0.5 },
    ],
  },
  {
    key: 'proactive_chat',
    name: '主动消息',
    desc: '定时主动找你说话',
    dirName: 'astrbot_plugin_proactive_chat',
    minVersion: '1.3.0',
    fields: [
      { path: 'friend_settings.enable', label: '启用私聊主动消息', type: 'boolean' },
      { path: 'friend_settings.auto_trigger_settings.enable_auto_trigger', label: '启用自动触发', type: 'boolean' },
      { path: 'friend_settings.auto_trigger_settings.auto_trigger_after_minutes', label: '多久没说话就触发（分钟）', type: 'number', min: 5, max: 1440 },
      { path: 'friend_settings.schedule_settings.min_interval_minutes', label: '最短间隔（分钟）', type: 'number', min: 5, max: 1440 },
      { path: 'friend_settings.schedule_settings.max_interval_minutes', label: '最长间隔（分钟）', type: 'number', min: 5, max: 2880 },
      { path: 'friend_settings.schedule_settings.enable_quiet_hours', label: '启用免打扰时段', type: 'boolean' },
      { path: 'friend_settings.schedule_settings.max_unanswered_times', label: '连续未回复上限', type: 'number', min: 1, max: 10 },
    ],
  },
];

// ---------- 版本比较 ----------
function parseVersion(text) {
  if (!text) return null;
  const m = String(text).trim().match(/(\d+)\.(\d+)(?:\.(\d+))?/);
  if (!m) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3] || 0)];
}

function versionGte(current, minimum) {
  const a = parseVersion(current);
  const b = parseVersion(minimum);
  if (!a || !b) return false;
  for (let i = 0; i < 3; i += 1) {
    if (a[i] > b[i]) return true;
    if (a[i] < b[i]) return false;
  }
  return true;
}

// ---------- 读取插件元数据 ----------
function readPluginMeta(dirName) {
  const metaPath = path.join(pluginDir(), dirName, 'metadata.yaml');
  if (!fs.existsSync(metaPath)) return null;
  try {
    const raw = fs.readFileSync(metaPath, 'utf8');
    const versionMatch = raw.match(/^version:\s*["']?([^\s"']+)/m);
    const nameMatch = raw.match(/^name:\s*["']?([^\s"']+)/m);
    return { version: versionMatch ? versionMatch[1] : '', dir: dirName, name: nameMatch ? nameMatch[1] : dirName };
  } catch {
    return null;
  }
}

// ---------- dot path 读写 ----------
function getByPath(obj, dotPath) {
  return dotPath.split('.').reduce((acc, k) => (acc && typeof acc === 'object' ? acc[k] : undefined), obj);
}

function setByPath(obj, dotPath, value) {
  const keys = dotPath.split('.');
  let cur = obj;
  for (let i = 0; i < keys.length - 1; i += 1) {
    const k = keys[i];
    if (typeof cur[k] !== 'object' || cur[k] === null) cur[k] = {};
    cur = cur[k];
  }
  cur[keys[keys.length - 1]] = value;
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
  } catch {
    return null;
  }
}

function backupFile(file) {
  try {
    const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
    const bak = `${file}.bak_${stamp}`;
    fs.copyFileSync(file, bak);
    return path.basename(bak);
  } catch {
    return null;
  }
}

// ---------- 对外：插件状态 ----------
function listPlugins() {
  return PLUGIN_SPECS.map((spec) => {
    const meta = readPluginMeta(spec.dirName);
    const installed = Boolean(meta);
    const compatible = installed ? versionGte(meta.version, spec.minVersion) : false;
    const cfgFile = pluginConfigPath(spec.key);
    const hasConfig = fs.existsSync(cfgFile);
    return {
      key: spec.key,
      name: spec.name,
      desc: spec.desc,
      minVersion: spec.minVersion,
      installed,
      version: meta ? meta.version : '',
      compatible,
      hasConfig,
      status: !installed ? 'missing' : !compatible ? 'outdated' : !hasConfig ? 'noconfig' : 'ok',
    };
  });
}

function getPluginConfig(pluginKey) {
  const spec = PLUGIN_SPECS.find((s) => s.key === pluginKey);
  if (!spec) return { ok: false, error: '未知插件' };

  const info = listPlugins().find((p) => p.key === pluginKey);
  if (info.status !== 'ok') {
    return { ok: false, error: info.status, info };
  }

  const cfg = readJson(pluginConfigPath(pluginKey));
  if (!cfg) return { ok: false, error: '配置读取失败', info };

  const values = {};
  for (const f of spec.fields) {
    values[f.path] = getByPath(cfg, f.path);
  }
  return { ok: true, info, fields: spec.fields, values };
}

function savePluginConfig(pluginKey, patch) {
  const spec = PLUGIN_SPECS.find((s) => s.key === pluginKey);
  if (!spec) return { ok: false, error: '未知插件' };

  const file = pluginConfigPath(pluginKey);
  const cfg = readJson(file);
  if (!cfg) return { ok: false, error: '配置读取失败' };

  for (const [dotPath, value] of Object.entries(patch || {})) {
    const field = spec.fields.find((f) => f.path === dotPath);
    if (!field) continue;
    // 路径必须已存在于配置里，否则说明字段定义与插件结构不符，拒绝写入
    if (getByPath(cfg, dotPath) === undefined) {
      return { ok: false, error: `配置里没有 ${dotPath}，请检查插件版本是否匹配` };
    }
    setByPath(cfg, dotPath, value);
  }

  const bak = backupFile(file);
  try {
    fs.writeFileSync(file, JSON.stringify(cfg, null, 2), 'utf8');
  } catch (err) {
    return { ok: false, error: err.message };
  }
  return { ok: true, backup: bak, needsReload: true };
}

// 通过面板接口重载插件（复用已有凭据）
async function reloadPlugin(pluginKey) {
  const cfg = readJson(path.join(astrbotDataDir(), 'cmd_config.json'));
  const dashboard = cfg?.dashboard || {};
  const secret = dashboard.jwt_secret;
  const username = dashboard.username;
  if (!secret) return { ok: false, error: '缺少面板密钥' };

  const pluginId = PLUGIN_SPECS.find((s) => s.key === pluginKey)?.dirName || `astrbot_plugin_${pluginKey}`;
  const token = signJwt(secret, username);
  const { request } = require('node:http');

  return new Promise((resolve) => {
    const req = request(
      {
        host: '127.0.0.1',
        port: 6185,
        path: `/api/v1/plugins/${pluginId}/reload`,
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
          'Content-Length': 0,
        },
        timeout: 30000,
      },
      (res) => {
        let body = '';
        res.setEncoding('utf8');
        res.on('data', (c) => (body += c));
        res.on('end', () => resolve({ ok: res.statusCode === 200, status: res.statusCode, body: body.slice(0, 200) }));
      }
    );
    req.on('timeout', () => {
      req.destroy();
      resolve({ ok: false, error: '超时' });
    });
    req.on('error', (e) => resolve({ ok: false, error: e.message }));
    req.end();
  });
}

// 最小 HS256 实现，避免引入依赖
function signJwt(secret, username) {
  const crypto = require('node:crypto');
  const b64 = (buf) => buf.toString('base64').replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
  const header = b64(Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })));
  const payload = b64(
    Buffer.from(JSON.stringify({ username, exp: Math.floor(Date.now() / 1000) + 300 }))
  );
  const input = `${header}.${payload}`;
  const sig = crypto.createHmac('sha256', secret).update(input).digest();
  return `${input}.${b64(sig)}`;
}

// ---------- 中转站自身参数 ----------
const HUB_SPECS = [
  { path: 'recall_k', label: '每轮召回条数', type: 'number', min: 1, max: 20, hint: '记忆注入多少条' },
  { path: 'extract.enabled', label: '启用记忆提炼', type: 'boolean' },
  { path: 'extract.min_importance', label: '写入最低重要性', type: 'number', min: 0, max: 1, step: 0.05, hint: '低于此分数不记录' },
  { path: 'extract.max_per_turn', label: '每轮最多写入条数', type: 'number', min: 1, max: 5 },
  { path: 'extract.min_chars', label: '触发提炼的最短字数', type: 'number', min: 2, max: 100 },
  { path: 'knowledge.enabled', label: '启用知识库检索', type: 'boolean' },
  { path: 'knowledge.top_k', label: '知识库返回条数', type: 'number', min: 1, max: 10 },
];

module.exports = {
  PLUGIN_SPECS,
  HUB_SPECS,
  listPlugins,
  getPluginConfig,
  savePluginConfig,
  reloadPlugin,
  readJson,
  getByPath,
  setByPath,
  backupFile,
  astrbotDataDir,
  pluginConfigPath,
};
