# -*- coding: utf-8 -*-
"""
输出写入器（IO适配层）

统一管理所有输出格式的写入：
- 翻译结果保存为 TXT / CSV / JSON
- LRC 字幕输出
- 日志输出
"""

from __future__ import annotations
import csv
import json
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class TranslationRecord:
    """单行翻译记录"""
    index: int
    filename: str = ''           # 来源文件名
    timestamp: str = ''           # 时间戳（LRC格式）
    original: str = ''            # 原文
    translation: str = ''         # 翻译
    confidence: float = 0.0       # 置信度（OCR质量）
    notes: str = ''               # 备注

    def to_row(self) -> list:
        """转为CSV行"""
        return [
            self.index,
            self.filename,
            self.timestamp,
            self.original,
            self.translation,
            self.confidence,
            self.notes,
        ]


# ==================== CSV 输出 ====================

def write_csv(
    records: list[TranslationRecord],
    output_path: Path,
    *,
    include_header: bool = True,
) -> None:
    """
    写入 CSV 文件

    参数:
        records: 翻译记录列表
        output_path: 输出路径
        include_header: 是否包含表头
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        if include_header:
            writer.writerow(['序号', '文件名', '时间戳', '原文', '翻译', '置信度', '备注'])
        for rec in records:
            writer.writerow(rec.to_row())


# ==================== TXT 输出 ====================

def write_txt_pairs(
    originals: list[str],
    translations: list[str],
    output_path: Path,
    *,
    separator: str = '\t',
) -> None:
    """
    写入原文-译文对照 TXT

    参数:
        originals: 原文行
        translations: 翻译行
        output_path: 输出路径
        separator: 分隔符（默认制表符）
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    for i, (orig, trans) in enumerate(zip(originals, translations)):
        lines.append(f"{i+1:04d}{separator}{orig}{separator}{trans}")

    output_path.write_text('\n'.join(lines), encoding='utf-8')


def write_txt_lines(
    lines: list[str],
    output_path: Path,
) -> None:
    """写入纯文本行"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('\n'.join(lines), encoding='utf-8')


# ==================== JSON 输出 ====================

def write_json(
    data: dict | list,
    output_path: Path,
    *,
    indent: int = 2,
) -> None:
    """写入 JSON 文件"""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)


def export_translation_json(
    records: list[TranslationRecord],
    output_path: Path,
) -> None:
    """导出翻译结果为 JSON"""
    data = []
    for rec in records:
        data.append({
            'index': rec.index,
            'original': rec.original,
            'translation': rec.translation,
            'timestamp': rec.timestamp,
            'confidence': rec.confidence,
        })
    write_json(data, output_path)


# ==================== 报告输出 ====================

def write_summary_report(
    output_path: Path,
    *,
    total_files: int = 0,
    total_lines: int = 0,
    translated_lines: int = 0,
    failed_lines: int = 0,
    total_cost: float = 0.0,
    elapsed_seconds: float = 0.0,
    api_calls: int = 0,
    **extra,
) -> None:
    """写入汇总报告"""
    lines = [
        '=' * 50,
        '翻译汇总报告',
        '=' * 50,
        f'处理文件数: {total_files}',
        f'总行数: {total_lines}',
        f'成功翻译行: {translated_lines}',
        f'失败行: {failed_lines}',
        f'API 调用次数: {api_calls}',
        f'总费用: ${total_cost:.4f}',
        f'耗时: {elapsed_seconds:.1f} 秒',
    ]

    for key, value in extra.items():
        lines.append(f'{key}: {value}')

    lines.append('=' * 50)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('\n'.join(lines), encoding='utf-8')