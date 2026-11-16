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
