import { app, BrowserWindow, session } from 'electron';
import path from 'path';
import { PythonBridge } from './python-bridge';
import { ConfigStore } from './config-store';
import { registerIpcHandlers } from './ipc-handlers';

// ── 屏蔽系统代理干扰 ──
// 清除环境变量中的代理设置，防止 Electron 和 Python 子进程
// 通过系统代理连接翻译 API（DeepSeek/OpenAI 等直连）
for (const key of ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']) {
  delete process.env[key];
}
process.env.NO_PROXY = '*';

// 告知 Chromium 不使用系统代理
app.commandLine.appendSwitch('no-proxy-server');

let mainWindow: BrowserWindow | null = null;
let pythonBridge: PythonBridge | null = null;

const USE_VITE_DEV = process.env.VITE_DEV === 'true';
const IS_PACKAGED = app.isPackaged;

// ── 路径解析：开发模式 vs 打包模式 ──
function getProjectRoot(): string {
  if (IS_PACKAGED) {
    // 打包后 Python main.exe 在 resources/python-backend/
    return path.join(process.resourcesPath, 'python-backend');
  }
  // 开发模式：electron-app 的父目录（即 e:\转译\）
  return path.join(__dirname, '../../../');
}

function getIconPath(): string {
  if (IS_PACKAGED) {
    return path.join(process.resourcesPath, 'icon.png');
  }
  return path.join(__dirname, '../../assets/icon.png');
}

const PROJECT_ROOT = getProjectRoot();
const configStore = new ConfigStore(IS_PACKAGED);

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    title: 'HdeG',
    icon: getIconPath(),
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // 初始化 Python 桥接
  pythonBridge = new PythonBridge(PROJECT_ROOT, mainWindow.webContents, IS_PACKAGED);

  // 注册 IPC 处理器
  registerIpcHandlers(mainWindow, pythonBridge, configStore);

  if (USE_VITE_DEV) {
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools({ mode: 'bottom' });
  } else {
    mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
    pythonBridge?.kill();
  });
}

app.whenReady().then(() => {
  // 强制直连模式——所有请求走直连，不走系统代理
  session.defaultSession.setProxy({ mode: 'direct' }).catch(() => {});
  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

app.on('before-quit', () => {
  pythonBridge?.kill();
});