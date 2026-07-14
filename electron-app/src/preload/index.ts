import { contextBridge, ipcRenderer } from 'electron';

export interface ElectronAPI {
  // 运行环境标识
  isElectron: boolean;

  // 配置
  config: {
    getAll: () => Promise<Record<string, unknown>>;
    getSection: (section: string) => Promise<Record<string, unknown>>;
    setSection: (section: string, data: Record<string, unknown>) => Promise<{ success: boolean }>;
  };

  // 对话框
  dialog: {
    selectFolder: () => Promise<string | null>;
  };

  // 文件扫描 - 返回作品列表
  folder: {
    scanWorks: (dirPath: string) => Promise<{
      works: Array<{
        id: string;
        path: string;
        lrcFiles: Array<{ name: string; path: string; hasJaLrc: boolean; hasCnLrc: boolean; size: number }>;
        audioFiles: string[];
        totalFiles: number;
      }>;
    }>;
  };

  // 翻译流程
  translate: {
    start: (workDir: string, workId?: string) => Promise<{ success: boolean; error?: string }>;
    cancel: () => Promise<{ success: boolean }>;
  };

  // 辅助翻译 (terms / alias / worldview)
  aid: {
    read: (workDir: string) => Promise<{
      terms: Record<string, string>;
      alias: Array<{ alias: string; target: string; confidence: number }>;
      worldview: Record<string, unknown>;
    }>;
    save: (workDir: string, type: 'terms' | 'alias' | 'worldview', data: Record<string, unknown> | Array<unknown>) => Promise<{ success: boolean }>;
  };

  // 结果审核
  review: {
    fetchResults: (workDir: string) => Promise<{
      success: boolean;
      data?: Array<{
        index: number;
        timestamp: string;
        original: string;
        translation: string;
        filename: string;
      }>;
      error?: string;
    }>;
    saveEdit: (workDir: string, filename: string, index: number, newTranslation: string) => Promise<{
      success: boolean;
      error?: string;
    }>;
  };

  // 工具
  utils: {
    openFolder: (dirPath: string) => Promise<void>;
    getProjectRoot: () => Promise<string>;
  };

  // 事件监听（Python 进程消息）
  on: (channel: string, callback: (...args: unknown[]) => void) => void;
  off: (channel: string, callback: (...args: unknown[]) => void) => void;
}

const api: ElectronAPI = {
  // 标识运行环境：Electron 桌面端
  isElectron: true,

  config: {
    getAll: () => ipcRenderer.invoke('config:getAll'),
    getSection: (section) => ipcRenderer.invoke('config:getSection', section),
    setSection: (section, data) => ipcRenderer.invoke('config:setSection', section, data),
  },

  dialog: {
    selectFolder: () => ipcRenderer.invoke('dialog:selectFolder'),
  },

  folder: {
    scanWorks: (dirPath) => ipcRenderer.invoke('folder:scanWorks', dirPath),
  },

  translate: {
    start: (workDir, workId) => ipcRenderer.invoke('translate:start', workDir, workId),
    cancel: () => ipcRenderer.invoke('translate:cancel'),
  },

  aid: {
    read: (workDir) => ipcRenderer.invoke('aid:read', workDir),
    save: (workDir, type, data) => ipcRenderer.invoke('aid:save', workDir, type, data),
  },

  review: {
    fetchResults: (workDir) => ipcRenderer.invoke('review:fetchResults', workDir),
    saveEdit: (workDir, filename, index, newTranslation) =>
      ipcRenderer.invoke('review:saveEdit', workDir, filename, index, newTranslation),
  },

  utils: {
    openFolder: (dirPath) => ipcRenderer.invoke('utils:openFolder', dirPath),
    getProjectRoot: () => ipcRenderer.invoke('utils:getProjectRoot'),
  },

  on: (channel: string, callback: (...args: unknown[]) => void) => {
    const validChannels = ['python:message', 'python:log', 'python:done', 'python:error'];
    if (validChannels.includes(channel)) {
      const subscription = (_event: Electron.IpcRendererEvent, ...args: unknown[]) => callback(...args);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const subs: any = (ipcRenderer as any).__subscriptions ??= {};
      subs[channel] = subscription;
      ipcRenderer.on(channel, subscription);
    }
  },

  off: (channel: string, _callback: (...args: unknown[]) => void) => {
    const validChannels = ['python:message', 'python:log', 'python:done', 'python:error'];
    if (validChannels.includes(channel)) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const subscription = (ipcRenderer as any).__subscriptions?.[channel] as (...args: unknown[]) => void;
      if (subscription) {
        ipcRenderer.removeListener(channel, subscription);
      }
    }
  },
};

contextBridge.exposeInMainWorld('electronAPI', api);