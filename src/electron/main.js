const { app, BrowserWindow, Tray, Menu, ipcMain, nativeImage, shell } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const http = require('node:http');

// ---------- 配置 ----------
const DEFAULT_CONFIG = {
  pythonPath: 'C:\\Users\\41328\\AppData\\Local\\AstrBot\\backend\\python\\python.exe',
  hubPort: 8787,
  autoStartSidecar: true,
};

let mainWindow = null;
let tray = null;
let sidecar = null;
let isQuitting = false;
let stopping = false;
let restartCount = 0;
let restartTimer = null;
const MAX_RESTARTS = 5;

function configPath() {
  return path.join(app.getPath('userData'), 'config.json');
}

function loadConfig() {
  try {
    const raw = fs.readFileSync(configPath(), 'utf8');
    return { ...DEFAULT_CONFIG, ...JSON.parse(raw) };
  } catch {
    return { ...DEFAULT_CONFIG };
  }
}

function saveConfig(cfg) {
  try {
    fs.mkdirSync(path.dirname(configPath()), { recursive: true });
    fs.writeFileSync(configPath(), JSON.stringify(cfg, null, 2), 'utf8');
  } catch (err) {
    log(`保存配置失败: ${err.message}`);
  }
}

let config = null;

// ---------- 日志 ----------
function logFile() {
  try {
    return path.join(app.getPath('userData'), 'main.log');
  } catch {
    return null;
  }
}

function log(message) {
  const line = `[${new Date().toLocaleTimeString('zh-CN')}] ${message}`;
  console.log(line);
  try {
    const f = logFile();
    if (f) {
      fs.mkdirSync(path.dirname(f), { recursive: true });
      fs.appendFileSync(f, line + '\n', 'utf8');
    }
  } catch {
    /* 日志失败不影响主流程 */
  }
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('hub:log', line);
  }
}

process.on('uncaughtException', (err) => {
  log(`主进程未捕获异常: ${err && err.stack ? err.stack : err}`);
});
process.on('unhandledRejection', (err) => {
  log(`未处理的 Promise 拒绝: ${err && err.stack ? err.stack : err}`);
});

// ---------- 窗口 ----------
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 980,
    height: 700,
    minWidth: 860,
    minHeight: 580,
    title: '大肥鱼中转站',
    backgroundColor: '#0f1620',
    autoHideMenuBar: true,
    show: false,
    icon: assetPath('icon-256.png') || undefined,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    log('窗口已打开');
  });

  // 关闭时最小化到托盘，而不是退出
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow.hide();
      log('已隐藏到托盘');
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ---------- 托盘 ----------
function assetPath(name) {
  const candidates = [
    path.join(__dirname, '..', 'assets', name),
    path.join(process.resourcesPath || '', 'assets', name),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

function trayIcon() {
  const file = assetPath('tray-32.png');
  if (file) {
    try {
      const img = nativeImage.createFromPath(file);
      if (!img.isEmpty()) return img;
    } catch (err) {
      log(`托盘图标加载失败: ${err.message}`);
    }
  }
  // 兜底：1x1 透明像素，保证 Tray 一定能创建
  return nativeImage.createFromBuffer(
    Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
      'base64'
    )
  );
}

function createTray() {
  try {
    tray = new Tray(trayIcon());
    tray.setToolTip('大肥鱼中转站');
    updateTrayMenu();
    tray.on('double-click', () => showWindow());
  } catch (err) {
    log(`托盘创建失败: ${err.message}`);
  }
}

function updateTrayMenu() {
  if (!tray) return;
  const running = isSidecarRunning();
  const menu = Menu.buildFromTemplate([
    { label: running ? '● 服务运行中' : '○ 服务已停止', enabled: false },
    { type: 'separator' },
    { label: '显示窗口', click: () => showWindow() },
    { label: '启动服务', enabled: !running, click: () => startSidecar() },
    { label: '停止服务', enabled: running, click: () => stopSidecar() },
    { type: 'separator' },
    { label: '打开日志文件夹', click: () => shell.openPath(app.getPath('userData')) },
    {
      label: '退出',
      click: () => {
        isQuitting = true;
        stopSidecar();
        app.quit();
      },
    },
  ]);
  tray.setContextMenu(menu);
}

function showWindow() {
  if (!mainWindow) {
    createWindow();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

// ---------- Python sidecar ----------
function sidecarScriptPath() {
  // 三种布局都要兼容：
  //  开发/运行副本： <appRoot>/sidecar/hub.py
  //  安装包：       resources/sidecar/hub.py
  //  源码：         <项目>/sidecar/hub.py
  const candidates = [
    path.join(__dirname, '..', 'sidecar', 'hub.py'),
    path.join(process.resourcesPath || '', 'sidecar', 'hub.py'),
    path.join(__dirname, '..', '..', 'sidecar', 'hub.py'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return candidates[0];
}

function isSidecarRunning() {
  return Boolean(sidecar && sidecar.exitCode === null && !sidecar.killed);
}

function startSidecar() {
  if (isSidecarRunning()) {
    log('服务已在运行');
    return false;
  }

  const script = sidecarScriptPath();
  if (!fs.existsSync(script)) {
    log(`找不到 sidecar: ${script}`);
    return false;
  }

  const python = config.pythonPath;
  if (!fs.existsSync(python)) {
    log(`找不到 Python: ${python}（可在配置里修改）`);
    return false;
  }

  log(`启动服务: ${python} ${script}`);
  const dataDir = app.getPath('userData');
  sidecar = spawn(python, ['-X', 'utf8', script], {
    cwd: path.dirname(script),
    windowsHide: true,
    env: {
      ...process.env,
      PYTHONIOENCODING: 'utf-8',
      HUB_PORT: String(config.hubPort),
      HUB_DATA_DIR: dataDir,
      ELECTRON_RUN_AS_NODE: '', // 确保不被当作 node 运行
    },
  });

  sidecar.stdout.on('data', (data) => {
    String(data).split(/\r?\n/).filter(Boolean).forEach((l) => log(`[py] ${l}`));
  });
  sidecar.stderr.on('data', (data) => {
    String(data).split(/\r?\n/).filter(Boolean).forEach((l) => log(`[py!] ${l}`));
  });
  sidecar.on('exit', (code, signal) => {
    log(`服务退出 (code=${code}, signal=${signal || '-'})`);
    sidecar = null;
    updateTrayMenu();
    notifyState();

    // 用户主动停止或正在退出时不重启
    if (stopping || isQuitting) return;

    if (restartCount >= MAX_RESTARTS) {
      log(`已连续重启 ${restartCount} 次仍失败，不再自动重启（请查看日志）`);
      return;
    }
    restartCount += 1;
    const delay = 3000;
    log(`${delay / 1000} 秒后自动重启（第 ${restartCount}/${MAX_RESTARTS} 次）`);
    clearTimeout(restartTimer);
    restartTimer = setTimeout(() => startSidecar(), delay);
  });

  // 稳定运行 60 秒后重置计数
  setTimeout(() => {
    if (isSidecarRunning()) restartCount = 0;
  }, 60000);

  updateTrayMenu();
  notifyState();
  return true;
}

function stopSidecar() {
  if (!isSidecarRunning()) {
    log('服务未运行');
    return false;
  }
  log('正在停止服务…');
  stopping = true;
  clearTimeout(restartTimer);
  try {
    sidecar.kill();
  } catch (err) {
    log(`停止失败: ${err.message}`);
  }
  sidecar = null;
  setTimeout(() => {
    stopping = false;
    restartCount = 0;
  }, 1500);
  updateTrayMenu();
  notifyState();
  return true;
}

function notifyState() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('hub:state', { running: isSidecarRunning() });
  }
}

// ---------- 健康检查 ----------
function checkHealth(timeoutMs = 2500) {
  return new Promise((resolve) => {
    const req = http.get(
      { host: '127.0.0.1', port: config.hubPort, path: '/health', timeout: timeoutMs },
      (res) => {
        let body = '';
        res.setEncoding('utf8');
        res.on('data', (chunk) => (body += chunk));
        res.on('end', () => {
          try {
            resolve({ ok: res.statusCode === 200, data: JSON.parse(body) });
          } catch {
            resolve({ ok: false, error: '返回不是 JSON' });
          }
        });
      }
    );
    req.on('timeout', () => {
      req.destroy();
      resolve({ ok: false, error: '超时' });
    });
    req.on('error', (err) => resolve({ ok: false, error: err.message }));
  });
}

// ---------- IPC ----------
ipcMain.handle('hub:start', () => startSidecar());
ipcMain.handle('hub:stop', () => stopSidecar());
ipcMain.handle('hub:status', () => ({
  running: isSidecarRunning(),
  port: config.hubPort,
  python: config.pythonPath,
  sidecar: sidecarScriptPath(),
}));
ipcMain.handle('hub:health', () => checkHealth());
ipcMain.handle('hub:getConfig', () => config);
ipcMain.handle('hub:setConfig', (_e, patch) => {
  config = { ...config, ...patch };
  saveConfig(config);
  log(`配置已保存: ${JSON.stringify(patch)}`);
  return config;
});
ipcMain.handle('hub:revealLogs', () => shell.openPath(app.getPath('userData')));

// ---------- 生命周期 ----------
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => showWindow());

  app.whenReady().then(() => {
    config = loadConfig();
    createWindow();
    createTray();
    if (config.autoStartSidecar) {
      startSidecar();
    }
    app.on('activate', () => showWindow());
  });

  app.on('window-all-closed', () => {
    // 保持托盘常驻：窗口关掉也不退出应用
    // 注意：该事件没有 event 参数，不能调用 preventDefault
  });

  app.on('before-quit', () => {
    isQuitting = true;
    if (isSidecarRunning()) {
      try {
        sidecar.kill();
      } catch {
        /* 忽略 */
      }
    }
  });
}
