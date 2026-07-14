/**
 * Browser-mode API adapter — 在浏览器开发模式下替代 window.electronAPI
 * 与 dev-server.js 通信，接口与 Electron IPC 一一对应。
 */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyFn = (...args: any[]) => any;

interface BrowserElectronAPI {
  isElectron: boolean;
  config: {
    getAll: () => Promise<Record<string, unknown>>;
    getSection: (section: string) => Promise<Record<string, unknown>>;
    setSection: (section: string, data: Record<string, unknown>) => Promise<{ success: boolean }>;
  };
  dialog: { selectFolder: () => Promise<string | null> };
  folder: { scanWorks: (dirPath: string) => Promise<{ works: unknown[] }> };
  translate: {
    start: (workDir: string, workId?: string) => Promise<{ success: boolean; error?: string }>;
    cancel: () => Promise<{ success: boolean }>;
  };
  aid: {
    read: (workDir: string) => Promise<{
      terms: Record<string, string>;
      alias: Array<{ alias: string; target: string; confidence: number }>;
      worldview: Record<string, unknown>;
    }>;
    save: (workDir: string, type: 'terms' | 'alias' | 'worldview', data: Record<string, unknown> | Array<unknown>) => Promise<{ success: boolean }>;
  };
  review: {
    fetchResults: (workDir: string) => Promise<{ success: boolean; data?: unknown[]; error?: string }>;
    saveEdit: (workDir: string, filename: string, index: number, newTranslation: string) => Promise<{ success: boolean; error?: string }>;
  };
  utils: {
    openFolder: (dirPath: string) => Promise<void>;
    getProjectRoot: () => Promise<string>;
  };
  on: (channel: string, callback: AnyFn) => void;
  off: (channel: string, callback: AnyFn) => void;
}

// 在 Vite dev 模式下使用相对路径（Vite proxy 转发到 dev-server）
// 直接打开构建产物时回退到 localhost:5199
const BASE = window.location.port === '5173' ? '' : 'http://localhost:5199';

async function req(method: string, path: string, body?: unknown) {
  const fullUrl = `${BASE}${path}`;
  console.log('[browser-api] req:', method, fullUrl);
  const opts: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) {
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(fullUrl, opts);
  if (!res.ok) {
    const errMsg = `API ${method} ${path} failed: ${res.status}`;
    console.error('[browser-api]', errMsg);
    throw new Error(errMsg);
  }
  const json = await res.json();
  console.log('[browser-api] res:', method, path, Object.keys(json));
  return json;
}

// ---- SSE Event Bus (mimics Electron ipcRenderer.on/off) ----
const listeners = new Map<string, Set<(...args: unknown[]) => void>>();
let sseConnected = false;

function connectSSE() {
  if (sseConnected) return;
  sseConnected = true;

  const es = new EventSource(`${BASE}/api/events`);
  es.addEventListener('python:message', (e) => {
    const data = JSON.parse(e.data);
    listeners.get('python:message')?.forEach((fn) => fn(data));
  });
  es.addEventListener('python:log', (e) => {
    const data = JSON.parse(e.data);
    listeners.get('python:log')?.forEach((fn) => fn(data));
  });
  es.addEventListener('python:done', (e) => {
    const data = JSON.parse(e.data);
    listeners.get('python:done')?.forEach((fn) => fn(data));
  });
  es.addEventListener('python:error', (e) => {
    const data = JSON.parse(e.data);
    listeners.get('python:error')?.forEach((fn) => fn(data));
  });
  es.onerror = () => {
    // reconnect after a delay
    sseConnected = false;
    setTimeout(connectSSE, 3000);
  };
}

const browserAPI: BrowserElectronAPI = {
  // 标识运行环境：浏览器开发模式
  isElectron: false,

  config: {
    getAll: () => req('GET', '/api/config'),
    getSection: (section: string) => req('GET', `/api/config/${section}`),
    setSection: (section: string, data: Record<string, unknown>) =>
      req('POST', `/api/config/${section}`, data),
  },

  dialog: {
    selectFolder: async () => {
      // 在浏览器中无法调用系统对话框，使用 prompt 代替
      return prompt('请输入工作目录路径:') || null;
    },
  },

  folder: {
    scanWorks: (dirPath: string) => req('POST', '/api/scan-works', { dirPath }),
  },

  translate: {
    start: (workDir: string, _workId?: string) => {
      connectSSE(); // ensure SSE is connected before starting
      return req('POST', '/api/translate/start', { workDir });
    },
    cancel: () => req('POST', '/api/translate/cancel'),
  },

  aid: {
    read: (workDir: string) => req('GET', `/api/aids?workDir=${encodeURIComponent(workDir)}`),
    save: (workDir: string, type: 'terms' | 'alias' | 'worldview', data: Record<string, unknown> | Array<unknown>) =>
      req('POST', '/api/aids/save', { workDir, type, data }),
  },

  review: {
    fetchResults: (workDir: string) =>
      req('GET', `/api/review/results?workDir=${encodeURIComponent(workDir)}`),
    saveEdit: (workDir: string, filename: string, index: number, newTranslation: string) =>
      req('POST', '/api/review/save-edit', { workDir, filename, index, newTranslation }),
  },

  utils: {
    openFolder: async (_dirPath: string) => {
      console.log('[browser-api] openFolder not available in browser');
    },
    getProjectRoot: async () => '',
  },

  on: (channel: string, callback: (...args: unknown[]) => void) => {
    if (!listeners.has(channel)) listeners.set(channel, new Set());
    listeners.get(channel)!.add(callback);
  },

  off: (channel: string, callback: (...args: unknown[]) => void) => {
    listeners.get(channel)?.delete(callback);
  },
};

export function setupBrowserAPI() {
  if (!(window as unknown as { electronAPI?: BrowserElectronAPI }).electronAPI) {
    (window as unknown as { electronAPI: BrowserElectronAPI }).electronAPI = browserAPI;
    console.log('[browser-api] window.electronAPI set up via HTTP (dev-server:5199)');
  }
}
