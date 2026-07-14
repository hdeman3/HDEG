# -*- coding: utf-8 -*-
"""
文件扫描器（IO适配层）

扫描作品目录，识别各类文件：
- WAV/MP3 音频文件
- 台本文档（已翻译和未翻译）
- 世界观文档
- 术语表
"""

from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class ScannedDir:
    """扫描到的作品目录"""
    path: Path
    wav_files: list[Path] = field(default_factory=list)
    mp3_files: list[Path] = field(default_factory=list)
    audio_files: list[Path] = field(default_factory=list)  # 合并后
    lrc_files: list[Path] = field(default_factory=list)    # .lrc 歌词文件（待翻译）
    ja_lrc_files: list[Path] = field(default_factory=list) # .ja.lrc 日文留档
    scriptbook_files: list[Path] = field(default_factory=list)
    worldview_files: list[Path] = field(default_factory=list)
    terms_files: list[Path] = field(default_factory=list)
    output_dir: Path | None = None

    @property
    def audio_count(self) -> int:
        return len(self.audio_files)

    @property
    def has_scriptbook(self) -> bool:
        return len(self.scriptbook_files) > 0

    @property
    def has_lrc(self) -> bool:
        return len(self.lrc_files) > 0


def scan_dir(base_path: Path, recursive: bool = False) -> ScannedDir:
    """
    扫描作品目录，识别所有相关文件

    参数:
        base_path: 作品目录路径
        recursive: 是否递归扫描子目录

    返回:
        ScannedDir 结构
    """
    result = ScannedDir(path=base_path)

    pattern = '**/*' if recursive else '*'
    all_files = list(base_path.glob(pattern))

    for f in all_files:
        if not f.is_file():
            continue

        name_lower = f.name.lower()
        stem_lower = f.stem.lower()

        # 音频文件
        if name_lower.endswith('.wav'):
            result.wav_files.append(f)
            result.audio_files.append(f)
        elif name_lower.endswith('.mp3'):
            result.mp3_files.append(f)
            result.audio_files.append(f)

        # LRC 歌词文件（infer.exe 转录生成，待翻译）
        elif name_lower.endswith('.ja.lrc'):
            result.ja_lrc_files.append(f)
        elif name_lower.endswith('.lrc'):
            result.lrc_files.append(f)

        # 台本文档
        elif any(kw in stem_lower for kw in ['台本', 'script', 'scriptbook']):
            result.scriptbook_files.append(f)

        # 世界观文档
        elif any(kw in stem_lower for kw in ['世界观', '设定', 'worldview']):
            result.worldview_files.append(f)

        # 术语表
        elif any(kw in stem_lower for kw in ['术语', '用語', 'terms', 'glossary']):
            result.terms_files.append(f)

    # 按名称排序确保一致的处理顺序
    result.audio_files.sort(key=lambda p: p.name)
    result.wav_files.sort(key=lambda p: p.name)
    result.mp3_files.sort(key=lambda p: p.name)

    return result


def find_work_dirs(root: Path, work_names: list[str] = None) -> list[Path]:
    """
    查找作品目录

    参数:
        root: 根目录（如 本編/、作品根目录）
        work_names: 指定作品名列表，为 None 则扫描全部

    返回:
        作品目录路径列表
    """
    if not root.exists():
        return []

    if work_names:
        dirs = [root / name for name in work_names if (root / name).exists()]
    else:
        dirs = [d for d in root.iterdir() if d.is_dir()]

    return sorted(dirs)


def load_text_file(path: Path) -> str:
    """加载文本文件（自动检测编码）"""
    # 优先 UTF-8
    try:
        return path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        # 回退到 shift_jis（日语常用编码）
        return path.read_text(encoding='shift_jis')


def load_text_lines(path: Path) -> list[str]:
    """加载文本文件为行列表（自动检测编码）"""
    content = load_text_file(path)
    return [line.rstrip('\n') for line in content.split('\n')]


def save_text_file(path: Path, content: str) -> None:
    """保存文本文件（UTF-8）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


# ==================== RJ 作品根目录识别 ====================

import re as _re_fs


def extract_main_rj_number(folder_name: str) -> str | None:
    """从文件夹名提取主 RJ 号（不含子编号）

    例如: "RJ01048637_030" → "RJ01048637"
    """
    m = _re_fs.search(r'(RJ\d{6,})', folder_name, _re_fs.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


def find_rj_work_root(path: Path) -> tuple[Path, str] | tuple[None, None]:
    """向上查找 RJ 作品根目录

    从给定路径向上遍历，找到包含 RJ 号的上级目录。
    用于将同一 RJ 号下的子目录（如 MP3、02_MP3）归组。

    返回: (rj_root, rj_number) 或 (None, None)
    """
    current = path.resolve()
    for _ in range(5):  # 最多向上查5层
        rj = extract_main_rj_number(current.name)
        if rj:
            return current, rj
        if current.parent == current:
            break
        current = current.parent
    return None, None