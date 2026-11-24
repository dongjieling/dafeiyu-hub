const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('hub', {
  // 服务控制
  start: () => ipcRenderer.invoke('hub:start'),
  stop: () => ipcRenderer.invoke('hub:stop'),
  status: () => ipcRenderer.invoke('hub:status'),
  appInfo: () => ipcRenderer.invoke('hub:appInfo'),
  health: () => ipcRenderer.invoke('hub:health'),

  // 配置
  getConfig: () => ipcRenderer.invoke('hub:getConfig'),
  setConfig: (patch) => ipcRenderer.invoke('hub:setConfig', patch),
  revealLogs: () => ipcRenderer.invoke('hub:revealLogs'),

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
