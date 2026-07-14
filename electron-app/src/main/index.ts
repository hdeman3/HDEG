import { app, BrowserWindow, ipcMain, dialog } from 'electron';
import path from 'path';
import { PythonBridge } from './python-bridge';
import { ConfigStore } from './config-store';
import { registerIpcHandlers } from './ipc-handlers';

let mainWindow: BrowserWindow | null = null;
let pythonBridge: PythonBridge | null = null;

const USE_VITE_DEV = process.env.VITE_DEV === 'true';
const configStore = new ConfigStore();
const PROJECT_ROOT = path.join(__dirname, '../../../');

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    title: '转译 - 日语字幕翻译工具',
    icon: path.join(__dirname, '../../assets/icon.png'),
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // 初始化 Python 桥接
  pythonBridge = new PythonBridge(PROJECT_ROOT, mainWindow.webContents);

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

app.whenReady().then(createWindow);

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