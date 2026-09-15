# -*- coding: utf-8 -*-
"""
术语管理器

负责：
- 从 config.json / 作品文件加载术语表（terms / alias）
- 构建翻译 Prompt 中的术语引用
- 注音聚类（将读音相同或相近的术语归组，避免重复翻译）
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Optional


# ==================== 术语加载 ====================

def load_terms_from_config(config: dict) -> dict[str, str]:
    """从配置字典加载术语表

    参数:
        config: 完整配置字典

    返回:
        {日文: 中文翻译}
    """
    terms: dict[str, str] = {}

    # 从 config.json 的 terms 段加载
    config_terms = config.get('terms', {})
    if isinstance(config_terms, dict):
        for jp, cn in config_terms.items():
            if cn and jp.strip():
                terms[jp.strip()] = cn.strip()

    return terms


def load_terms_from_file(terms_path: Path) -> dict[str, str]:
    """从术语文件加载术语表

    支持格式：
    - JSON: {"日文": "中文"}
    - TSV: 日文\t中文
    - 自定义: 日文→中文（每行一对）

    参数:
        terms_path: 术语文件路径

    返回:
        {日文: 中文翻译}
    """
    if not terms_path.exists():
        return {}

    terms: dict[str, str] = {}
    content = terms_path.read_text(encoding='utf-8')

    # 尝试 JSON
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            for jp, cn in data.items():
                if cn and jp.strip():
                    terms[jp.strip()] = cn.strip()
            return terms
    except (json.JSONDecodeError, ValueError):
        pass

    # 尝试行格式
    for line in content.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        # 支持 "日文→中文" 或 "日文\t中文"
        for sep in ('→', '\t', ':', '：'):
            if sep in line:
                parts = line.split(sep, 1)
                if len(parts) == 2:
                    jp = parts[0].strip()
                    cn = parts[1].strip()
                    if jp and cn:
                        terms[jp] = cn
                    break

    return terms


# ==================== 注音聚类 ====================

def cluster_terms_by_pronunciation(
    terms: dict[str, str],
    similarity_threshold: float = 0.7,
) -> list[list[tuple[str, str]]]:
    """
    将术语按注音/读音相似度聚类

    原理：
    对日文术语，使用 pyopenjtalk 获取读音（yomi），
    然后将读音相似的归为一组。组内术语在 prompt 中会标注为
    "这些都读作 XXX，请不要混淆"。

    参数:
        terms: {日文: 中文}
        similarity_threshold: 相似度阈值 (0-1)

    返回:
        [[(日文, 中文), ...], ...]  每组至少两个成员
    """
    # 获取每个术语的读音
    term_yomi: list[tuple[str, str, str]] = []  # (日文, 中文, 读音)
    try:
        import pyopenjtalk
        for jp, cn in terms.items():
            try:
                yomi = pyopenjtalk.g2p(jp, kana=True)
                term_yomi.append((jp, cn, yomi))
            except Exception:
                term_yomi.append((jp, cn, jp))  # 无法获取读音时用原文替代
    except ImportError:
        # 无 pyopenjtalk，每个术语单独一组
        return [[(jp, cn)] for jp, cn in terms.items()]

    # 简单聚类：完全相同读音的归为一组
    yomi_groups: dict[str, list[tuple[str, str]]] = {}
    for jp, cn, yomi in term_yomi:
        yomi_normalized = yomi.strip()
        yomi_groups.setdefault(yomi_normalized, []).append((jp, cn))

    # 过滤：只保留 >=2 个成员的组
    clusters: list[list[tuple[str, str]]] = []
    singles: list[tuple[str, str]] = []
    for yomi, items in yomi_groups.items():
        if len(items) >= 2:
            clusters.append(items)
        else:
            singles.extend(items)

    # 单成员术语单独成组
    for item in singles:
        clusters.append([item])

    return clusters


# ==================== Prompt 构建 ====================

def build_alias_prompt_section(
    alias_list: list[str],
) -> Optional[str]:
    """构建 ASR 误识别提示的 Prompt 片段

    参数:
        alias_list: ASR 误识别参考列表

    返回:
        Prompt 字符串，无 alias 时返回 None
    """
    if not alias_list:
        return None

    alias_text = "\n".join(f"  - {a}" for a in alias_list[:20])
    return (
        "【ASR 误识别参考——以下为日语音频中容易听错的词汇对照】\n"
        f"{alias_text}\n\n"
        "如果输入行中出现相似读音的词汇，请参考以上对照进行修正后再翻译。"
    )


def build_clustered_terms_prompt_section(
    terms: dict[str, str],
    max_terms: int = 30,
) -> Optional[str]:
    """构建带注音聚类的术语 Prompt 片段

    对读音相同/相近的术语进行分组标注，
    帮助 LLM 区分同音异义词。

    参数:
        terms: {日文: 中文}
        max_terms: 最多输出条数

    返回:
        Prompt 字符串，无术语时返回 None
    """
    if not terms or len(terms) == 0:
        return None

    clusters = cluster_terms_by_pronunciation(terms)

    lines: list[str] = []
    count = 0

    for cluster in clusters:
        if count >= max_terms:
            break
        if len(cluster) == 1:
            jp, cn = cluster[0]
            lines.append(f"  {jp} → {cn}")
            count += 1
        else:
            # 同音聚类
            items = [f"{jp}→{cn}" for jp, cn in cluster]
            lines.append(f"  [同音词] {' | '.join(items)}")
            count += len(cluster)

    return "【术语对照表——以下词语必须采用指定的翻译】\n" + "\n".join(lines)


# ==================== 术语/Alias 文件管理 ====================

def get_terms_path(work_dir: "Path") -> "Path":
    """获取术语表文件路径（作品级，.terms.json）"""
    from pathlib import Path
    return work_dir / '.terms.json'


def get_alias_path(work_dir: "Path") -> "Path":
    """获取 alias 表文件路径（作品级，.alias.json）"""
    from pathlib import Path
    return work_dir / '.alias.json'


def save_terms_to_file(work_dir: "Path", terms: dict[str, str]) -> None:
    """保存术语表到作品目录"""
    terms_path = get_terms_path(work_dir)
    with open(terms_path, 'w', encoding='utf-8') as f:
        json.dump(terms, f, ensure_ascii=False, indent=2)


def load_alias(work_dir: "Path") -> list[dict]:
    """加载已保存的 alias 表"""
    alias_path = get_alias_path(work_dir)
    if alias_path.exists():
        try:
            with open(alias_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return []
    return []
