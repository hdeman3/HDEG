import path from 'path';
import fs from 'fs';

const CONFIG_SECTIONS = ['api', 'app', 'ocr', 'pricing', 'network', 'prompts', 'transcription'];

export class ConfigStore {
  private configPath: string;
  private presetsPath: string;
  private config: Record<string, unknown>;
  private presets: Record<string, Record<string, unknown>>;

  constructor(isPackaged: boolean = false) {
    if (isPackaged) {
      // 打包模式：配置存到 %APPDATA%/转译/
      const { app } = require('electron');
      const userDataPath = app.getPath('userData');
      this.configPath = path.join(userDataPath, 'config.json');
      this.presetsPath = path.join(userDataPath, 'presets.json');
      // 首次启动：从模板复制默认配置
      if (!fs.existsSync(this.configPath)) {
        const templatePath = path.join(process.resourcesPath, 'config-template.json');
        if (fs.existsSync(templatePath)) {
          try {
            fs.copyFileSync(templatePath, this.configPath);
            console.log('[config-store] 从模板创建配置: ' + this.configPath);
          } catch (e) {
            console.error('[config-store] 复制配置模板失败:', e);
          }
        }
      }
    } else {
      // 开发模式：与 Python 项目共享配置
      this.configPath = path.join(__dirname, '../../../config.json');
      this.presetsPath = path.join(__dirname, '../../../presets.json');
    }
    this.config = {};
    this.presets = {};
    this.load();
    this.loadPresets();
    // 迁移旧数据：config.json 里的 presets 拆到独立文件
    if (this.config['presets'] && Object.keys(this.presets).length === 0) {
      this.presets = this.config['presets'] as Record<string, Record<string, unknown>>;
      this.savePresets();
      delete this.config['presets'];
      this.save();
      console.log('[config-store] 已将 presets 从 config.json 迁移到 presets.json');
    }
  }

  // ── 配置读写 ──

  load(): Record<string, unknown> {
    try {
      if (fs.existsSync(this.configPath)) {
        const raw = fs.readFileSync(this.configPath, 'utf-8');
        this.config = JSON.parse(raw);
      }
    } catch (e) {
      console.error('读取 config.json 失败:', e);
      this.config = {};
    }
    return this.config;
  }

  save(newConfig?: Record<string, unknown>): void {
    if (newConfig) {
      this.config = { ...this.config, ...newConfig };
    }
    try {
      const dir = path.dirname(this.configPath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      // config.json 只保存配置段 + active_preset，不保存 presets
      const toSave: Record<string, unknown> = {};
      for (const key of [...CONFIG_SECTIONS, 'active_preset']) {
        if (this.config[key] !== undefined) {
          toSave[key] = this.config[key];
        }
      }
      fs.writeFileSync(this.configPath, JSON.stringify(toSave, null, 2), 'utf-8');
    } catch (e) {
      console.error('保存 config.json 失败:', e);
    }
  }

  get(): Record<string, unknown> {
    return this.config;
  }

  getSection(section: string): Record<string, unknown> {
    return (this.config[section] as Record<string, unknown>) || {};
  }

  setSection(section: string, data: Record<string, unknown>): void {
    // 清除 undefined，避免未触摸的复选框覆盖已有值
    const clean: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(data)) {
      if (v !== undefined) clean[k] = v;
    }
    this.config[section] = { ...((this.config[section] as Record<string, unknown>) || {}), ...clean };
    this.save();
  }

  // ── 预设读写（独立文件 presets.json）──

  private loadPresets(): void {
    try {
      if (fs.existsSync(this.presetsPath)) {
        const raw = fs.readFileSync(this.presetsPath, 'utf-8');
        this.presets = JSON.parse(raw);
      }
    } catch (e) {
      console.error('读取 presets.json 失败:', e);
      this.presets = {};
    }
  }

  private savePresets(): void {
    try {
      const dir = path.dirname(this.presetsPath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      fs.writeFileSync(this.presetsPath, JSON.stringify(this.presets, null, 2), 'utf-8');
    } catch (e) {
      console.error('保存 presets.json 失败:', e);
    }
  }

  // ── 预设管理 ──

  /** 列出所有预设名称（始终包含 default） */
  listPresets(): string[] {
    const keys = Object.keys(this.presets);
    if (keys.indexOf('default') === -1) keys.unshift('default');
    return keys;
  }

  /** 获取当前激活的预设名 */
  getActivePreset(): string {
    return (this.config['active_preset'] as string) || 'default';
  }

  /** 切换激活的预设 */
  setActivePreset(name: string): { success: boolean; config?: Record<string, unknown> } {
    if (name !== 'default' && !this.presets[name]) {
      return { success: false };
    }
    // 保存当前配置到旧预设（如果当前激活的不是 default 且有对应预设）
    const currentActive = this.getActivePreset();
    if (currentActive !== 'default' && this.presets[currentActive]) {
      const currentSections: Record<string, unknown> = {};
      for (const key of CONFIG_SECTIONS) {
        if (this.config[key] !== undefined) {
          currentSections[key] = JSON.parse(JSON.stringify(this.config[key]));
        }
      }
      this.presets[currentActive] = currentSections;
    }

    // 从预设加载配置
    const presetData = this.presets[name];
    if (presetData) {
      for (const [section, data] of Object.entries(presetData)) {
        this.config[section] = JSON.parse(JSON.stringify(data));
      }
    }
    // 如果切换到 default 且没有 default 预设数据 → 不做任何改动（保持当前即为 default）

    this.config['active_preset'] = name;
    this.save();
    this.savePresets();
    return { success: true, config: this.config };
  }

  /** 创建/更新预设（从当前 config 各 section 快照） */
  savePreset(name: string): { success: boolean } {
    if (!name || name === 'default') return { success: false };

    // 首次创建预设时，自动备份当前配置为 default
    if (!this.presets['default'] && Object.keys(this.presets).length === 0) {
      const defaultSections: Record<string, unknown> = {};
      for (const key of CONFIG_SECTIONS) {
        if (this.config[key] !== undefined) {
          defaultSections[key] = JSON.parse(JSON.stringify(this.config[key]));
        }
      }
      this.presets['default'] = defaultSections;
    }

    // 快照当前配置到预设
    const sections: Record<string, unknown> = {};
    for (const key of CONFIG_SECTIONS) {
      if (this.config[key] !== undefined) {
        sections[key] = JSON.parse(JSON.stringify(this.config[key]));
      }
    }
    this.presets[name] = sections;
    this.config['active_preset'] = name;
    this.save();
    this.savePresets();
    return { success: true };
  }

  /** 删除预设 */
  deletePreset(name: string): { success: boolean } {
    if (!this.presets[name]) return { success: false };
    delete this.presets[name];
    if (this.config['active_preset'] === name) {
      this.config['active_preset'] = 'default';
      this.save();
    }
    this.savePresets();
    return { success: true };
  }
}