import { app, BrowserWindow, dialog, ipcMain, IpcMainInvokeEvent } from 'electron';
import { PythonBridge } from './python-bridge';
import { ConfigStore } from './config-store';
import path from 'path';
import fs from 'fs';

export function registerIpcHandlers(
  mainWindow: BrowserWindow,
  pythonBridge: PythonBridge,
  configStore: ConfigStore
): void {
  // ==================== 配置相关 ====================

  ipcMain.handle('config:getAll', () => {
    return configStore.get();
  });

  ipcMain.handle('config:getSection', (_event, section: string) => {
    return configStore.getSection(section);
  });

  ipcMain.handle('config:setSection', (_event, section: string, data: Record<string, unknown>) => {
    configStore.setSection(section, data);
    return { success: true };
  });

  // ==================== 文件对话框 ====================

  ipcMain.handle('dialog:selectFolder', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openDirectory'],
      title: '选择工作目录（包含音频/台本的文件夹）',
    });
    if (result.canceled) return null;
    return result.filePaths[0];
  });

  // ==================== 文件扫描（工作目录 → RJ 作品列表） ====================

  ipcMain.handle('folder:scanWorks', async (_event, dirPath: string) => {
    const fsPromises = fs.promises;
    const audioExts = ['.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma', '.mp4', '.mkv', '.avi', '.mov', '.webm'];
    const lrcExts = ['.lrc', '.srt', '.vtt'];
    const resultWorks: Array<{
      id: string; path: string;
      lrcFiles: Array<{ name: string; path: string; hasJaLrc: boolean; hasCnLrc: boolean; size: number }>;
      audioFiles: string[];
      totalFiles: number;
      hasScriptbook?: boolean;
    }> = [];

    try {
      const entries = await fsPromises.readdir(dirPath, { withFileTypes: true });
      for (const entry of entries) {
        if (!entry.isDirectory()) continue;
        if (!entry.name.startsWith('RJ')) continue;
        const workPath = path.join(dirPath, entry.name);
        const workId = entry.name;

        const lrcFiles: Array<{ name: string; path: string; hasJaLrc: boolean; hasCnLrc: boolean; size: number }> = [];
        const audioFiles: string[] = [];

        // 异步递归扫描
        async function scanWorkDir(currentPath: string): Promise<void> {
          try {
            const subEntries = await fsPromises.readdir(currentPath, { withFileTypes: true });
            for (const sub of subEntries) {
              const fullPath = path.join(currentPath, sub.name);
              if (sub.isDirectory()) {
                await scanWorkDir(fullPath);
              } else {
                const ext = path.extname(sub.name).toLowerCase();
                if (lrcExts.includes(ext)) {
                  if (/\.([a-z]{2})\.(lrc|srt|vtt)$/i.test(sub.name)) continue;

                  const baseName = sub.name.replace(new RegExp(ext + '$'), '');
                  const jaLrcPath = path.join(currentPath, `${baseName}.ja.lrc`);
                  let hasJaLrc = false;
                  let lrcSize = 0;
                  try {
                    const stat = await fsPromises.stat(fullPath);
                    lrcSize = stat.size;
                    await fsPromises.access(jaLrcPath);
                    hasJaLrc = true;
                  } catch {}

                  // 检测中文/翻译分隔符（只读前 4KB）
                  let hasTranslation = false;
                  if (lrcSize > 10) {
                    try {
                      const fh = await fsPromises.open(fullPath, 'r');
                      const buf = Buffer.alloc(4096);
                      await fh.read(buf, 0, 4096, 0);
                      await fh.close();
                      const sample = buf.toString('utf-8');
                      hasTranslation = /[一-鿿㐀-䶿]|[／]/.test(sample);
                    } catch {}
                  }

                  lrcFiles.push({
                    name: path.basename(sub.name),
                    path: fullPath,
                    hasJaLrc,
                    hasCnLrc: hasTranslation,
                    size: lrcSize,
                  });
                } else if (audioExts.includes(ext)) {
                  audioFiles.push(sub.name);
                }
              }
            }
          } catch { /* skip inaccessible directories */ }
        }

        await scanWorkDir(workPath);

        // 台本检测（异步）
        let hasScriptbook = false;
        const scriptbookPattern = /(台本|シナリオ|script|台詞|セリフ|せりふ|原作|テキスト|筋書き|脚本|戯曲)/i;
        try {
          const allFiles = await fsPromises.readdir(workPath, { recursive: true }) as string[];
          hasScriptbook = allFiles.some((f: string) => {
            const ext = path.extname(f).toLowerCase();
            if (ext !== '.txt' && ext !== '.pdf') return false;
            return scriptbookPattern.test(f);
          });
        } catch {}

        resultWorks.push({
          id: workId,
          path: workPath,
          lrcFiles,
          audioFiles,
          totalFiles: lrcFiles.length + audioFiles.length,
          hasScriptbook,
        });
      }
    } catch (e) {
      console.error('扫描工作目录失败:', e);
    }

    return { works: resultWorks };
  });

  // ==================== Python 翻译流程 ====================

  ipcMain.handle('translate:start', async (_event, workDir: string, workId?: string) => {
    try {
      const config = configStore.get();
      pythonBridge.runTranslate(workDir, config, workId);
      return { success: true };
    } catch (e: unknown) {
      return { success: false, error: (e as Error).message };
    }
  });

  ipcMain.handle('translate:cancel', async () => {
    pythonBridge.kill();
    return { success: true };
  });

  ipcMain.handle('translate:status', async () => {
    return pythonBridge.getStatus();
  });

  // ==================== 辅助翻译文件 (terms / worldview) ====================

  ipcMain.handle('aid:read', async (_event, workDir: string) => {
    return pythonBridge.readTranslationAid(workDir);
  });

  ipcMain.handle('aid:save', async (
    _event,
    workDir: string,
    type: 'terms' | 'alias' | 'worldview',
    data: Record<string, unknown> | Array<unknown>
  ) => {
    pythonBridge.saveTranslationAid(workDir, type, data);
    return { success: true };
  });

  // ==================== 翻译结果审核 ====================

  ipcMain.handle('review:fetchResults', async (_event, workDir: string) => {
    try {
      const results = await pythonBridge.fetchTranslationResults(workDir);
      return { success: true, data: results };
    } catch (e: unknown) {
      return { success: false, error: (e as Error).message };
    }
  });

  ipcMain.handle('review:saveEdit', async (
    _event,
    workDir: string,
    filename: string,
    index: number,
    newTranslation: string
  ) => {
    try {
      await pythonBridge.saveTranslationEdit(workDir, filename, index, newTranslation);
      return { success: true };
    } catch (e: unknown) {
      return { success: false, error: (e as Error).message };
    }
  });

  // ==================== 预设管理 ====================

  ipcMain.handle('config:listPresets', async () => {
    return { presets: configStore.listPresets(), active: configStore.getActivePreset() };
  });

  ipcMain.handle('config:setActivePreset', async (_event, name: string) => {
    return configStore.setActivePreset(name);
  });

  ipcMain.handle('config:savePreset', async (_event, name: string) => {
    return configStore.savePreset(name);
  });

  ipcMain.handle('config:deletePreset', async (_event, name: string) => {
    return configStore.deletePreset(name);
  });

  // ==================== 一致性检查 ====================

  ipcMain.handle('review:consistencyCheck', async (_event, workDir: string) => {
    try {
      const report = await pythonBridge.runConsistencyCheck(workDir);
      return { success: true, data: report };
    } catch (e: unknown) {
      return { success: false, error: (e as Error).message };
    }
  });

  // ==================== 工具函数 ====================

  ipcMain.handle('utils:openFolder', async (_event, dirPath: string) => {
    import('electron').then(({ shell }) => {
      shell.openPath(dirPath);
    });
  });

  ipcMain.handle('utils:getProjectRoot', async () => {
    // 打包模式下 Python 后端在 resources/python-backend/
    if (app.isPackaged) {
      return path.join(process.resourcesPath, 'python-backend');
    }
    return path.join(__dirname, '../../../');
  });
}