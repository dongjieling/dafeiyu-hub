const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

// LivingMemory 外部写入补丁：让外部写入也生成记忆原子
const PLUGIN_KEY = 'astrbot_plugin_livingmemory';
const TARGET_REL = path.join('core', 'external_api.py');
const MARKER = 'metadata["summary_schema_version"] = "v2"';
const ANCHOR = 'metadata["key_facts"] = key_facts';

function pluginFilePath() {
  return path.join(os.homedir(), '.astrbot', 'data', 'plugins', PLUGIN_KEY, TARGET_REL);
}

function patchStatus() {
  const file = pluginFilePath();
  if (!fs.existsSync(file)) {
    return { ok: false, state: 'missing', file, hint: '找不到插件文件，可能没装 LivingMemory' };
  }
  let text = '';
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (err) {
    return { ok: false, state: 'unreadable', file, hint: err.message };
  }
  // 不能只看全文有没有这行：必须出现在 key_facts 赋值之后 5 行内，
  // 否则可能是插件别处的同名变量，属于误判
  const lines = text.split('\n');
  const anchorIdx = lines.findIndex((l) => l.includes(ANCHOR));
  if (anchorIdx === -1) {
    return {
      ok: false,
      state: 'unpatched',
      file,
      hint: '插件结构变了（找不到 key_facts 代码行），需要人工适配',
    };
  }
  const window = lines.slice(anchorIdx + 1, anchorIdx + 6).join('\n');
  if (window.includes(MARKER)) {
    return { ok: true, state: 'patched', file };
  }
  return {
    ok: false,
    state: 'unpatched',
    file,
    hint: '插件被升级或重装过，补丁已丢失：桌宠写的记忆不会生成原子，QQ 侧召回会变差',
  };
}

function applyPatch() {
  const file = pluginFilePath();
  if (!fs.existsSync(file)) {
    return { ok: false, state: 'missing', hint: '找不到插件文件' };
  }
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (err) {
    return { ok: false, state: 'unreadable', hint: err.message };
  }
  if (text.includes(MARKER)) {
    return { ok: true, state: 'patched', already: true, file };
  }

  const lines = text.split('\n');
  const idx = lines.findIndex((l) => l.includes(ANCHOR));
  if (idx === -1) {
    return {
      ok: false,
      state: 'unpatched',
      hint: '插件结构变了（找不到 key_facts 代码行），需要人工处理',
    };
  }
  const indent = lines[idx].match(/^\s*/)[0];
  lines.splice(idx + 1, 0, `${indent}# [本地补丁] 让外部写入也走原子化`, `${indent}${MARKER}`);

  // 备份
  let backup = null;
  try {
    const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
    backup = `${file}.bak_${stamp}`;
    fs.copyFileSync(file, backup);
  } catch {
    backup = null;
  }

  try {
    fs.writeFileSync(file, lines.join('\n'), 'utf8');
  } catch (err) {
    return { ok: false, state: 'unpatched', hint: `写入失败: ${err.message}` };
  }

  const after = patchStatus();
  return { ...after, applied: after.state === 'patched', backup };
}

module.exports = { patchStatus, applyPatch, pluginFilePath };
