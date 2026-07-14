# -*- coding: utf-8 -*-
"""
注音引擎

提供日文文本注音（yomi）和读音相似度计算功能。
"""

from __future__ import annotations


def get_yomi(text: str, use_pyopenjtalk: bool = True) -> str:
    """获取日文文本的读音

    参数:
        text: 日文文本
        use_pyopenjtalk: 是否优先使用 pyopenjtalk

    返回: 片假名读音
    """
    if not text.strip():
        return text

    if use_pyopenjtalk:
        try:
            import pyopenjtalk
            return pyopenjtalk.g2p(text, kana=True)
        except ImportError:
            pass
        except Exception:
            pass

    # 回退：使用 unidic-lite + fugashi
    try:
        from fugashi import Tagger
        tagger = Tagger('-Owakati')
        result = tagger.parse(text)
        # fugashi 只能分词，不能直接获取读音
        # 尝试使用更详细的解析
        tagger2 = Tagger('-Ochasen')
        parsed = tagger2.parse(text)
        # 提取读音（chasen 格式第3列）
        readings: list[str] = []
        for line in parsed.split('\n'):
            line = line.strip()
            if not line or line == 'EOS':
                continue
            parts = line.split('\t')
            if len(parts) >= 4:
                readings.append(parts[3])
        if readings:
            return ''.join(readings)
    except ImportError:
        pass

    return text  # 无法获取读音时返回原文


def get_yomi_katakana_only(text: str) -> str:
    """获取纯片假名读音（过滤掉非片假名字符）"""
    yomi = get_yomi(text)
    return ''.join(c for c in yomi if '\u30a0' <= c <= '\u30ff')


def calculate_pronunciation_similarity(
    yomi1: str,
    yomi2: str,
) -> float:
    """计算两个读音的相似度（0-1）

    使用编辑距离（Levenshtein）计算。

    参数:
        yomi1: 读音1
        yomi2: 读音2

    返回: 相似度 (0=完全不同, 1=完全相同)
    """
    if not yomi1 or not yomi2:
        return 0.0

    if yomi1 == yomi2:
        return 1.0

    # 编辑距离
    len1, len2 = len(yomi1), len(yomi2)
    dp = [[0] * (len2 + 1) for _ in range(len1 + 1)]

    for i in range(len1 + 1):
        dp[i][0] = i
    for j in range(len2 + 1):
        dp[0][j] = j

    for i in range(1, len1 + 1):
        for j in range(1, len2 + 1):
            cost = 0 if yomi1[i - 1] == yomi2[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )

    distance = dp[len1][len2]
    max_len = max(len1, len2)
    if max_len == 0:
        return 1.0

    return 1.0 - (distance / max_len)


def find_similar_terms(
    query_yomi: str,
    term_yomi_map: dict[str, str],
    threshold: float = 0.7,
) -> list[tuple[str, str, float]]:
    """查找读音相似的术语

    参数:
        query_yomi: 查询读音
        term_yomi_map: {日文: 读音}
        threshold: 相似度阈值

    返回:
        [(日文, 读音, 相似度), ...]  按相似度降序排列
    """
    results: list[tuple[str, str, float]] = []

    for jp, yomi in term_yomi_map.items():
        sim = calculate_pronunciation_similarity(query_yomi, yomi)
        if sim >= threshold:
            results.append((jp, yomi, sim))

    results.sort(key=lambda x: -x[2])
    return results