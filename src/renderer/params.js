// 参数页：动态渲染插件与中转站参数

const STATUS_TEXT = {
  ok: { label: '已安装', cls: 'ok' },
  outdated: { label: '版本不符', cls: 'warn' },
  missing: { label: '未安装', cls: 'off' },
  noconfig: { label: '缺少配置', cls: 'warn' },
};

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderField(field, value, onChange) {
  const row = el('div', 'param-row');

  const label = el('div', 'param-label');
  label.appendChild(el('span', null, field.label));
  if (field.hint) label.appendChild(el('em', null, field.hint));

  const control = el('div', 'param-control');

  if (field.type === 'boolean') {
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = Boolean(value);
    input.addEventListener('change', () => onChange(input.checked));
    control.appendChild(input);
  } else {
    const input = document.createElement('input');
    input.type = 'number';
    if (field.min !== undefined) input.min = field.min;
    if (field.max !== undefined) input.max = field.max;
    input.step = field.step !== undefined ? String(field.step) : '1';
    input.value = value === undefined || value === null ? '' : value;
    input.addEventListener('change', () => {
      const num = Number(input.value);
      onChange(Number.isFinite(num) ? num : 0);
    });
    control.appendChild(input);
  }

  row.appendChild(label);
  row.appendChild(control);
  return row;
}

function renderSection({ title, subtitle, status, fields, values, onSave, saveLabel, extraButton }) {
  const box = el('div', 'param-group');

  const head = el('div', 'param-head');
  const titleWrap = el('div');
  titleWrap.appendChild(el('strong', null, title));
  if (subtitle) titleWrap.appendChild(el('span', 'param-sub', subtitle));
  head.appendChild(titleWrap);

  if (status) {
    const st = STATUS_TEXT[status.status] || { label: status.status, cls: 'off' };
    const chip = el('span', `chip ${st.cls}`, st.label);
    head.appendChild(chip);
  }
  box.appendChild(head);

  if (status && status.status !== 'ok') {
    const tip = el('div', 'param-tip');
    if (status.status === 'missing') {
      tip.textContent = '这个插件没装，装了才能调参数。';
    } else if (status.status === 'outdated') {
      tip.textContent = `版本 ${status.version || '未知'} 低于要求的 ${status.minVersion}，请先升级插件。`;
    } else if (status.status === 'noconfig') {
      tip.textContent = '配置还没生成，先在 AstrBot 里打开一次插件。';
    }
    box.appendChild(tip);
    return box;
  }

  const patch = {};
  for (const f of fields) {
    box.appendChild(
      renderField(f, values[f.path], (v) => {
        patch[f.path] = v;
      })
    );
  }

  const actions = el('div', 'param-actions');
  const saveBtn = el('button', 'btn primary', saveLabel || '保存');
  saveBtn.addEventListener('click', () => onSave(patch));
  actions.appendChild(saveBtn);

  if (extraButton) {
    const b = el('button', 'btn', extraButton.label);
    b.addEventListener('click', extraButton.onClick);
    actions.appendChild(b);
  }
  box.appendChild(actions);
  return box;
}

async function loadParams() {
  const pluginBox = document.getElementById('plugin-params');
  const hubBox = document.getElementById('hub-params');
  pluginBox.textContent = '';
  hubBox.textContent = '';

  // ---- 中转站自身参数 ----
  try {
    const hub = await window.hub.params.hubGet();
    if (hub.ok) {
      hubBox.appendChild(
        renderSection({
          title: '中转站',
          subtitle: '记忆读写与检索',
          fields: hub.fields,
          values: hub.values,
          saveLabel: '保存并重启服务',
          onSave: async (patch) => {
            const res = await window.hub.params.hubSave(patch);
            if (res.ok) {
              appendLog('中转站参数已保存，正在重启服务…');
              await window.hub.stop();
              setTimeout(async () => {
                await window.hub.start();
                setTimeout(refresh, 1500);
              }, 800);
            } else {
              appendLog('保存失败: ' + (res.error || '未知错误'));
            }
          },
        })
      );
    } else {
      hubBox.appendChild(el('div', 'param-tip', hub.error || '读取失败'));
    }
  } catch (err) {
    hubBox.appendChild(el('div', 'param-tip', String(err)));
  }

  // ---- 插件参数 ----
  let list = [];
  try {
    list = await window.hub.params.plugins();
  } catch (err) {
    pluginBox.appendChild(el('div', 'param-tip', String(err)));
    return;
  }

  for (const info of list) {
    if (info.status !== 'ok') {
      pluginBox.appendChild(renderSection({ title: info.name, subtitle: info.desc, status: info }));
      continue;
    }
    try {
      const detail = await window.hub.params.pluginGet(info.key);
      if (!detail.ok) {
        pluginBox.appendChild(
          renderSection({ title: info.name, subtitle: info.desc, status: { ...info, status: 'noconfig' } })
        );
        continue;
      }
      pluginBox.appendChild(
        renderSection({
          title: info.name,
          subtitle: `${info.desc} · v${info.version}`,
          status: info,
          fields: detail.fields,
          values: detail.values,
          saveLabel: '保存并重载插件',
          onSave: async (patch) => {
            const saved = await window.hub.params.pluginSave(info.key, patch);
            if (!saved.ok) {
              appendLog(`保存失败: ${saved.error}`);
              return;
            }
            appendLog(`已保存 ${info.name} 参数（备份 ${saved.backup || '无'}）`);
            const reloaded = await window.hub.params.pluginReload(info.key);
            appendLog(reloaded.ok ? `${info.name} 已重载生效` : `重载失败: ${reloaded.error || reloaded.status}`);
            setTimeout(loadParams, 1200);
          },
        })
      );
    } catch (err) {
      pluginBox.appendChild(el('div', 'param-tip', String(err)));
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('btn-params-reload');
  if (btn) btn.addEventListener('click', loadParams);
});
