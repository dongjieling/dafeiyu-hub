const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('hub', {
  // 服务控制
  start: () => ipcRenderer.invoke('hub:start'),
  stop: () => ipcRenderer.invoke('hub:stop'),
  status: () => ipcRenderer.invoke('hub:status'),
  health: () => ipcRenderer.invoke('hub:health'),
  appInfo: () => ipcRenderer.invoke('hub:appInfo'),

  // 应用配置
  getConfig: () => ipcRenderer.invoke('hub:getConfig'),
  setConfig: (patch) => ipcRenderer.invoke('hub:setConfig', patch),
  revealLogs: () => ipcRenderer.invoke('hub:revealLogs'),
  configPath: () => ipcRenderer.invoke('hub:configPath'),

  // 记忆原子补丁
  patchStatus: () => ipcRenderer.invoke('patch:status'),
  exportArchiveKey: () => ipcRenderer.invoke('archive:exportKey'),
  selftest: () => ipcRenderer.invoke('selftest:run'),
  memories: (params) => ipcRenderer.invoke('memory:list', params),
  memoryDelete: (id) => ipcRenderer.invoke('memory:delete', id),
  archiveConfig: () => ipcRenderer.invoke('config:archiveGet'),
  setArchiveConfig: (patch) => ipcRenderer.invoke('config:archiveSet', patch),
  cleanupTestData: () => ipcRenderer.invoke('selftest:cleanup'),
  patchApply: () => ipcRenderer.invoke('patch:apply'),
  openAstrBotPlugins: () => ipcRenderer.invoke('astrbot:openPlugins'),

  // 参数调整
  params: {
    plugins: () => ipcRenderer.invoke('params:plugins'),
    pluginGet: (key) => ipcRenderer.invoke('params:pluginGet', key),
    pluginSave: (key, patch) => ipcRenderer.invoke('params:pluginSave', key, patch),
    pluginReload: (key) => ipcRenderer.invoke('params:pluginReload', key),
    hubGet: () => ipcRenderer.invoke('params:hubGet'),
    hubSave: (patch) => ipcRenderer.invoke('params:hubSave', patch),
  },

  // 事件订阅
  onLog: (callback) => {
    const listener = (_event, line) => callback(line);
    ipcRenderer.on('hub:log', listener);
    return () => ipcRenderer.removeListener('hub:log', listener);
  },
  onState: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('hub:state', listener);
    return () => ipcRenderer.removeListener('hub:state', listener);
  },
});
