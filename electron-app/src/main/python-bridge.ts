import { ChildProcess, spawn } from 'child_process';
import { WebContents } from 'electron';
import path from 'path';
import { EventEmitter } from 'events';

export interface ProgressData {
  type: 'progress';
  stage: string;
  percent: number;
  message: string;
  file?: string;
  timestamp?: string;
}

export interface ResultData {
  type: 'result';
  file: string;
  data: Array<{
    index: number;
    timestamp: string;
    original: string;
    translation: string;
    filename: string;
  }>;
}

export interface LogData {
  type: 'log';
  level: 'info' | 'warn' | 'error';
  message: string;
}

export type BridgeMessage = ProgressData | ResultData | LogData;

export class PythonBridge extends EventEmitter {
  private process: ChildProcess | null = null;
  private projectRoot: string;
  private webContents: WebContents;
  private buffer: string = '';

  constructor(projectRoot: string, webContents: WebContents) {
    super();
    this.projectRoot = projectRoot;
    this.webContents = webContents;
  }

  /**
   * 运行 Python 翻译流程
   */
  runTranslate(workDir: string, config: Record<string, unknown>, workId?: string): void {
    this.kill();

    // 写入工作目录到 input_path.txt
    const fs = require('fs');
    const inputPathFile = path.join(this.projectRoot, 'input_path.txt');
    fs.writeFileSync(inputPathFile, workDir, 'utf-8');

    // 启动 Python 子进程
    const pythonPath = 'python';
    const scriptPath = path.join(this.projectRoot, 'translate.py');

    this.process = spawn(pythonPath, ['-u', scriptPath], {
      cwd: this.projectRoot,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    this.emit('started');

    this.process.stdout?.on('data', (data: Buffer) => {
      this.handleStdout(data.toString('utf-8'));
    });

    this.process.stderr?.on('data', (data: Buffer) => {
      const msg = data.toString('utf-8').trim();
      if (msg) {
        this.webContents.send('python:log', { level: 'error', message: msg });
        this.emit('log', { type: 'log', level: 'error', message: msg });
      }
    });

    this.process.on('close', (code: number | null) => {
      this.emit('finished', code);
      this.webContents.send('python:done', { exitCode: code });
    });

    this.process.on('error', (err: Error) => {
      this.webContents.send('python:error', { message: err.message });
      this.emit('error', err);
    });
  }

  /**
   * 直接运行 Python 脚本并返回结果
   */
  runPython(args: string[]): Promise<string> {
    return new Promise((resolve, reject) => {
      const pythonPath = 'python';
      const proc = spawn(pythonPath, ['-u', ...args], {
        cwd: this.projectRoot,
        env: { ...process.env, PYTHONUNBUFFERED: '1' },
      });

      let stdout = '';
      let stderr = '';

      proc.stdout?.on('data', (data: Buffer) => {
        stdout += data.toString('utf-8');
      });

      proc.stderr?.on('data', (data: Buffer) => {
        stderr += data.toString('utf-8');
      });

      proc.on('close', (code: number | null) => {
        if (code === 0) {
          resolve(stdout);
        } else {
          reject(new Error(`Python 进程退出码: ${code}\n${stderr}`));
        }
      });

      proc.on('error', reject);
    });
  }

  /**
   * 解析 Python stdout 中的 JSON 行
   */
  private handleStdout(data: string): void {
    this.buffer += data;
    const lines = this.buffer.split('\n');
    // 保留最后一个未完成的行
    this.buffer = lines.pop() || '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      // 尝试解析 JSON 进度行
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed.type) {
          this.webContents.send('python:message', parsed);
          this.emit('message', parsed);
          continue;
        }
      } catch {
        // 不是 JSON，作为普通日志
        this.webContents.send('python:log', { level: 'info', message: trimmed });
      }
    }
  }

  /**
   * 读取术语表文件
   */
  /**
   * 搜索 JSON 辅助文件 — Python pipeline 可能把它们保存在子目录（如 RJ/SEあり/）
   * 搜索顺序: workDir → workDir 的所有一级子目录
   * 文件命名: 优先 .name.json（Python 格式），回退 name.json（旧版兼容）
   */
  private findAndReadAidFile(workDir: string, name: string): { data: unknown; foundPath: string } | null {
    const fs = require('fs');

    // 构建搜索目录列表: workDir + 所有一级子目录
    const searchDirs = [workDir];
    try {
      const entries = fs.readdirSync(workDir, { withFileTypes: true });
      for (const entry of entries) {
        if (entry.isDirectory()) {
          searchDirs.push(path.join(workDir, entry.name));
        }
      }
    } catch {}

    const candidates = [`.${name}.json`, `${name}.json`];

    for (const dir of searchDirs) {
      for (const filename of candidates) {
        const filePath = path.join(dir, filename);
        try {
          if (fs.existsSync(filePath)) {
            const raw = fs.readFileSync(filePath, 'utf-8');
            return { data: JSON.parse(raw), foundPath: dir };
          }
        } catch (e) {
          console.error(`读取 ${filePath} 失败:`, e);
        }
      }
    }

    return null;
  }

  async readTranslationAid(workDir: string): Promise<{
    terms: Record<string, string>;
    alias: Array<{ alias: string; target: string; confidence: number }>;
    worldview: Record<string, unknown>;
  }> {
    const termsResult = this.findAndReadAidFile(workDir, 'terms');
    const aliasResult = this.findAndReadAidFile(workDir, 'alias');
    const worldviewResult = this.findAndReadAidFile(workDir, 'worldview');

    const terms = (termsResult?.data as Record<string, string>) || {};
    const alias = (aliasResult?.data as Array<{ alias: string; target: string; confidence: number }>) || [];
    const worldview = (worldviewResult?.data as Record<string, unknown>) || {};

    console.log(`[python-bridge] 读取辅助翻译: terms=${Object.keys(terms).length} (from ${termsResult?.foundPath || 'N/A'}), alias=${Array.isArray(alias) ? alias.length : 0}, worldview keys=${Object.keys(worldview).length}`);

    return { terms, alias, worldview };
  }

  /**
   * 保存术语表文件
   */
  saveTranslationAid(workDir: string, type: 'terms' | 'alias' | 'worldview', data: Record<string, unknown> | Array<unknown>): void {
    const fs = require('fs');

    // 查找现有文件所在的目录，保存到相同位置
    const existing = this.findAndReadAidFile(workDir, type);
    const targetDir = existing ? existing.foundPath : workDir;

    const filePath = path.join(targetDir, `.${type}.json`);
    fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf-8');
    console.log(`[python-bridge] 保存 ${type} → ${filePath}`);
  }

  /**
   * 从 Python 获取翻译结果
   */
  async fetchTranslationResults(workDir: string): Promise<Array<{
    index: number;
    timestamp: string;
    original: string;
    translation: string;
    filename: string;
  }>> {
    const script = `
import sys
sys.path.insert(0, r'${this.projectRoot.replace(/\\/g, '\\\\')}')
from pathlib import Path
from io_adapter.lrc_handler import parse_lrc_file

work_dir = Path(r'${workDir.replace(/\\/g, '\\\\')}')
results = []

for lrc_file in sorted(work_dir.glob('**/*.lrc')):
    ja_file = lrc_file.with_suffix('.ja.lrc')
    if ja_file.exists():
        ja_lines = parse_lrc_file(ja_file)
    else:
        ja_lines = []

    cn_lines = parse_lrc_file(lrc_file)

    for i, cn_line in enumerate(cn_lines):
        ja_text = ja_lines[i].text if i < len(ja_lines) else ''
        results.append({
            'index': i + 1,
            'timestamp': cn_line.timestamp_str,
            'original': ja_text,
            'translation': cn_line.text.split('／')[0] if '／' not in cn_line.text else cn_line.text.split('／')[-1],
            'filename': lrc_file.name,
        })

import json
print(json.dumps(results, ensure_ascii=False))
`.trim();

    const fs = require('fs');
    const tmpScript = path.join(this.projectRoot, '_fetch_results_tmp.py');
    fs.writeFileSync(tmpScript, script, 'utf-8');

    try {
      const output = await this.runPython([tmpScript]);
      return JSON.parse(output);
    } finally {
      try { fs.unlinkSync(tmpScript); } catch {}
    }
  }

  /**
   * 保存修改后的翻译到 LRC 文件
   */
  async saveTranslationEdit(
    workDir: string,
    filename: string,
    index: number,
    newTranslation: string
  ): Promise<void> {
    const script = `
import sys
sys.path.insert(0, r'${this.projectRoot.replace(/\\/g, '\\\\')}')
from pathlib import Path
from io_adapter.lrc_handler import parse_lrc_file, save_lrc

work_dir = Path(r'${workDir.replace(/\\/g, '\\\\')}')
lrc_path = work_dir / '${filename.replace(/\\/g, '\\\\')}'
lrc_lines = parse_lrc_file(lrc_path)

# 构建翻译列表
translations = []
for line in lrc_lines:
    text = line.text
    if '／' in text:
        translations.append(text.split('／')[-1])
    else:
        translations.append(text)

# 修改指定行
if 0 <= ${index - 1} < len(translations):
    translations[${index - 1}] = "${newTranslation.replace(/"/g, '\\"')}"

save_lrc(lrc_path, lrc_lines, translations)
print('ok')
`.trim();

    const fs = require('fs');
    const tmpScript = path.join(this.projectRoot, '_save_edit_tmp.py');
    fs.writeFileSync(tmpScript, script, 'utf-8');

    try {
      await this.runPython([tmpScript]);
    } finally {
      try { fs.unlinkSync(tmpScript); } catch {}
    }
  }

  kill(): void {
    if (this.process) {
      this.process.kill();
      this.process = null;
    }
  }
}