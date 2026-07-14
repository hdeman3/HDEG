import path from 'path';
import fs from 'fs';

export class ConfigStore {
  private configPath: string;
  private config: Record<string, unknown>;

  constructor() {
    // 配置文件路径: 项目根目录下的 config.json
    this.configPath = path.join(__dirname, '../../../config.json');
    this.config = {};
    this.load();
  }

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
      fs.writeFileSync(this.configPath, JSON.stringify(this.config, null, 2), 'utf-8');
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
    this.config[section] = { ...((this.config[section] as Record<string, unknown>) || {}), ...data };
    this.save();
  }
}