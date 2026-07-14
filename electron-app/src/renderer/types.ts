export interface WorkItem {
  /** RJ 编号，如 "RJ01508941" */
  id: string;
  /** 完整路径 */
  path: string;
  /** 该作品下的 LRC 字幕文件列表 */
  lrcFiles: LrcFileInWork[];
  /** 该作品下的音频文件名称列表 */
  audioFiles: string[];
  /** 总文件数 */
  totalFiles: number;
  /** 是否有台本文件 (.txt) */
  hasScriptbook?: boolean;
}

export interface LrcFileInWork {
  name: string;
  path: string;
  hasJaLrc: boolean;
  hasCnLrc: boolean;
  size: number;
}

export interface TranslationRow {
  index: number;
  timestamp: string;
  original: string;
  translation: string;
  filename: string;
  modified?: boolean;
}

export interface TermsData {
  [key: string]: string;
}

export interface AliasItem {
  alias: string;
  target: string;
  confidence: number;
}

export interface WorldviewCharacter {
  name: string;
  role?: string;
  personality?: string;
  description?: string;
}

export interface WorldviewData {
  worldview?: string;
  /** 简单格式: {name: desc}; 复杂格式: [{name, role, personality, ...}] */
  characters?: Record<string, string> | WorldviewCharacter[];
  scene?: string;
  themes?: string[];
  special_terms?: Record<string, string>;
}

export interface LogEntry {
  id: number;
  level: 'info' | 'warn' | 'error';
  message: string;
  time: string;
}