# -*- coding: utf-8 -*-
"""
文本分块策略（纯逻辑，零IO）

包含：
- 按 token 数量分块
- 小批次合并
- 分块大小估算

迁移自 translate.py 中的分块相关逻辑。
"""

from __future__ import annotations


# ==================== 常量 ====================

# 每批最大 token 数（提示词的 token 限制，保守设为模型的 80%）
DEFAULT_MAX_TOKENS_PER_BATCH = 6400

# 单行平均 token 数估算（日文字符约 2-3 token/字，保守估算）
AVG_TOKENS_PER_CHAR = 2

# 最小批次行数（即使超过 token 限制也保证至少这么多行）
MIN_BATCH_LINES = 5

# 小批次合并阈值：行数少于此值则与上一批合并
SMALL_BATCH_THRESHOLD = 4


# ==================== Token 估算 ====================

def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量

    基于日文字符的粗略估算（约为字符数 × 2）。
    实际精确计算需要 tiktoken，此处用于分块决策。
    """
    return len(text) * AVG_TOKENS_PER_CHAR


def estimate_batch_tokens(lines: list[str]) -> int:
    """估算一批文本行的总 token 数"""
    return sum(estimate_tokens(line) for line in lines)


# ==================== 分块 ====================

def split_into_batches(
    lines: list[str],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS_PER_BATCH,
    min_lines: int = MIN_BATCH_LINES,
) -> list[list[str]]:
    """
    将文本行按 token 限制分块

    策略：
    1. 逐行累加 token 估算值
    2. 超过阈值时切割（但保证不少于 min_lines 行）
    3. 过滤空行和纯标记行

    参数:
        lines: 待分块的文本行
        max_tokens: 每批最大 token 数
        min_lines: 每批最少行数（防止过度切割）

    返回:
        批次列表，每个批次是一组文本行
    """
    if not lines:
        return []

    # 预处理：合并连续空行为一个 [EMPTY_LINE]
    processed = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            processed.append('[EMPTY_LINE]')
        else:
            processed.append(line)

    batches = []
    current_batch = []
    current_tokens = 0

    for line in processed:
        line_tokens = estimate_tokens(line)

        if current_batch and \
           current_tokens + line_tokens > max_tokens and \
           len(current_batch) >= min_lines:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0

        current_batch.append(line)
        current_tokens += line_tokens

    # 处理最后一批
    if current_batch:
        # 检查是否应该合并到前一批
        if len(current_batch) < SMALL_BATCH_THRESHOLD and len(batches) > 0:
            batches[-1].extend(current_batch)
        else:
            batches.append(current_batch)

    return batches


# ==================== 小批次合并 ====================

def merge_small_batches(
    batches: list[list[str]],
    *,
    threshold: int = SMALL_BATCH_THRESHOLD,
) -> list[list[str]]:
    """
    合并过小的批次

    从后向前遍历，若某批次行数 < threshold 且前面有更大批次，则合并
    """
    if not batches:
        return []

    result = []
    i = 0
    while i < len(batches):
        current = list(batches[i])

        # 向前看：如果后续是小批次，合并过来
        j = i + 1
        while j < len(batches) and len(batches[j]) < threshold:
            current.extend(batches[j])
            j += 1

        result.append(current)
        i = j

    return result


# ==================== 分块大小推荐 ====================

def recommend_batch_size(
    total_lines: int,
    avg_line_length: int,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS_PER_BATCH,
) -> int:
    """根据总量推荐每批行数

    参数:
        total_lines: 总行数
        avg_line_length: 平均每行字符数
        max_tokens: 最大 token 限制

    返回:
        推荐的每批行数
    """
    if total_lines <= 0 or avg_line_length <= 0:
        return 50  # 默认值

    avg_tokens_per_line = avg_line_length * AVG_TOKENS_PER_CHAR
    lines_per_batch = max_tokens // avg_tokens_per_line

    # 约束在合理范围
    lines_per_batch = max(10, min(200, int(lines_per_batch)))

    return lines_per_batch


# ==================== 长句拆分与合并 ====================

_SPLIT_ENDERS = ('。', '？', '?', '！', '!', '…', '」', ')', '）', '♡', '♪', '～')
_SPLIT_SOFT = ('、', '，')


def split_long_line(line: str, max_chars: int = 100) -> list[str]:
    """按日语句子边界切分超长行

    切分策略：
    1. 优先在句末标点（。、？、！等）后切分
    2. 次选在逗号（、）后切分
    3. 都找不到则在 max_chars 处硬切
    """
    if len(line) <= max_chars:
        return [line]

    parts = []
    remaining = line

    while len(remaining) > max_chars:
        best_split = -1
        search_start = max(0, max_chars - 30)
        search_end = min(len(remaining), max_chars + 30)

        # 优先：句末标点
        for i in range(search_end - 1, search_start - 1, -1):
            if remaining[i] in _SPLIT_ENDERS:
                best_split = i + 1
                break

        # 次优：逗号
        if best_split == -1:
            for i in range(search_end - 1, search_start - 1, -1):
                if remaining[i] in _SPLIT_SOFT:
                    best_split = i + 1
                    break

        # 兜底：硬切
        if best_split == -1 or best_split <= search_start:
            best_split = max_chars

        parts.append(remaining[:best_split].strip())
        remaining = remaining[best_split:].strip()

    if remaining:
        parts.append(remaining)

    return parts


def merge_split_translations(translated: list[str], origin_map: list[int], original_count: int) -> list[str]:
    """将切分后的翻译结果按原行索引合并

    参数:
        translated: 切分后每段的翻译结果
        origin_map: translated[i] 对应的原行索引
        original_count: 原始行数

    返回: 合并后的列表，长度等于 original_count
    """
    groups: dict[int, list[str]] = {}
    for text, origin_idx in zip(translated, origin_map):
        groups.setdefault(origin_idx, []).append(text)

    result = []
    for i in range(original_count):
        if i in groups:
            merged = " ".join(t.strip() for t in groups[i] if t.strip())
            result.append(merged)
        else:
            result.append("")

    return result