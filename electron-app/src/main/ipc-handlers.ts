import { BrowserWindow, dialog, ipcMain, IpcMainInvokeEvent } from 'electron';
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
      const entries = fs.readdirSync(dirPath, { withFileTypes: true });
      for (const entry of entries) {
        if (!entry.isDirectory()) continue;
        // 匹配 RJ 开头的文件夹
        if (!entry.name.startsWith('RJ')) continue;
        const workPath = path.join(dirPath, entry.name);
        const workId = entry.name;

        const lrcFiles: Array<{ name: string; path: string; hasJaLrc: boolean; hasCnLrc: boolean; size: number }> = [];
        const audioFiles: string[] = [];

        // 递归扫描该作品文件夹
        function scanWorkDir(currentPath: string) {
          try {
            const subEntries = fs.readdirSync(currentPath, { withFileTypes: true });
            for (const sub of subEntries) {
              const fullPath = path.join(currentPath, sub.name);
              if (sub.isDirectory()) {
                scanWorkDir(fullPath);
              } else {
                const ext = path.extname(sub.name).toLowerCase();
                if (lrcExts.includes(ext)) {
                  // 跳过带语言标记的文件（.ja.lrc / .cn.lrc）
                  if (/\.([a-z]{2})\.(lrc|srt|vtt)$/i.test(sub.name)) continue;

                  const baseName = sub.name.replace(new RegExp(ext + '$'), '');
                  const jaLrcPath = path.join(currentPath, `${baseName}.ja.lrc`);
                  const hasJaLrc = fs.existsSync(jaLrcPath);
                  const lrcSize = fs.statSync(fullPath).size;

                  // 后端判断：读 LRC 内容检测中文/翻译分隔符
                  let hasTranslation = false;
                  if (lrcSize > 10) {
                    try {
                      const sample = fs.readFileSync(fullPath, 'utf-8').slice(0, 4096);
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

        scanWorkDir(workPath);

        // 检测台本文件 (.txt / .pdf)，使用正则匹配常见台本命名
        let hasScriptbook = false;
        const scriptbookPattern = /(台本|シナリオ|script|台詞|セリフ|せりふ|原作|テキスト|筋書き|脚本|戯曲)/i;
        try {
          const allFiles = fs.readdirSync(workPath, { recursive: true }) as string[];
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

  // ==================== 工具函数 ====================

  ipcMain.handle('utils:openFolder', async (_event, dirPath: string) => {
    import('electron').then(({ shell }) => {
      shell.openPath(dirPath);
    });
  });

  ipcMain.handle('utils:getProjectRoot', async () => {
    return path.join(__dirname, '../../../');
  });
}