# -*- coding: utf-8 -*-
"""
布局分析纯逻辑模块

从 ocr_utils.py 中提取的纯算法函数，不依赖任何文件IO或外部配置加载。
所有配置参数通过参数传入，或使用模块级默认常量。
"""

# ==================== 默认配置常量 ====================

DEFAULT_VERTICAL_RATIO_THRESHOLD = 1.2
DEFAULT_VERTICAL_BLOCK_RATIO = 0.3
DEFAULT_X_TOLERANCE = 30
DEFAULT_Y_TOLERANCE = 30


def detect_vertical_layout(items: list, ocr_config: dict = None) -> bool:
    """检测页面是否为竖排布局

    竖排特征：
    1. 文本块高度明显大于宽度（竖排文字块）
    2. 多个文本块在相近的x坐标上形成列
    3. 每列内y坐标变化大（文本垂直排列）

    参数:
        items: [(center_x, center_y, width, height, text), ...]
        ocr_config: OCR配置字典（可选，key: vertical_ratio_threshold, vertical_block_ratio, x_tolerance）

    返回:
        True 如果认为是竖排布局
    """
    if ocr_config is None:
        ocr_config = {}

    if len(items) < 4:
        return False

    # 特征1：检查文本块的宽高比
    # 竖排文本块通常高度 > 宽度 * 1.2
    vertical_block_count = 0
    ratio_threshold = ocr_config.get('vertical_ratio_threshold', DEFAULT_VERTICAL_RATIO_THRESHOLD)

    for cx, cy, w, h, text in items:
        if h > w * ratio_threshold:
            vertical_block_count += 1

    vertical_block_ratio = ocr_config.get('vertical_block_ratio', DEFAULT_VERTICAL_BLOCK_RATIO)
    if len(items) > 0 and vertical_block_count / len(items) > vertical_block_ratio:
        return True

    # 特征2：按x坐标分组成列
    x_tolerance = ocr_config.get('x_tolerance', DEFAULT_X_TOLERANCE)
    x_groups = {}

    for cx, cy, w, h, text in items:
        x_key = round(cx / x_tolerance)
        if x_key not in x_groups:
            x_groups[x_key] = []
        x_groups[x_key].append((cx, cy, w, h, text))

    valid_columns = 0
    total_column_items = 0

    for x_key, group in x_groups.items():
        if len(group) >= 2:
            valid_columns += 1
            total_column_items += len(group)

    if valid_columns >= 2 and total_column_items / max(valid_columns, 1) >= 2:
        return True

    return False


def sort_vertical_layout(items: list, ocr_config: dict = None) -> list[str]:
    """对竖排布局的OCR结果按阅读顺序排序

    竖排阅读顺序：从右到左排列列，每列内从上到下
    对于被PaddleOCR错误识别为横排的短文本块（如角色名），也按竖排方式排序

    参数:
        items: [(center_x, center_y, width, height, text), ...]
        ocr_config: OCR配置字典（可选）

    返回:
        按阅读顺序排列的文本列表
    """
    if not items:
        return []

    if ocr_config is None:
        ocr_config = {}

    ratio_threshold = ocr_config.get('vertical_ratio_threshold', DEFAULT_VERTICAL_RATIO_THRESHOLD)

    # 分离竖排和横排文本块
    vertical_items = []
    horizontal_items = []
    square_items = []

    for item in items:
        cx, cy, w, h, text = item
        if h > w * ratio_threshold:
            vertical_items.append(item)
        elif w > h:
            if len(text) < 20:
                vertical_items.append(item)
            else:
                horizontal_items.append(item)
        else:
            square_items.append(item)

    # 去重：移除被其他文本块完全包含的横排文本块
    texts_in_vertical = set()
    for cx, cy, w, h, text in vertical_items:
        for i in range(len(text)):
            for j in range(i + 1, min(i + 20, len(text) + 1)):
                texts_in_vertical.add(text[i:j])

    filtered_horizontal = []
    for cx, cy, w, h, text in horizontal_items:
        if w > 1000:
            continue
        is_duplicate = False
        for v_cx, v_cy, v_w, v_h, v_text in vertical_items:
            if text in v_text:
                is_duplicate = True
                break
        if not is_duplicate:
            filtered_horizontal.append((cx, cy, w, h, text))

    horizontal_items = filtered_horizontal

    result = []

    # 1. 竖排文本块：按列排序（从右到左，每列内从上到下）
    all_vertical_items = vertical_items + square_items
    if all_vertical_items:
        x_tolerance = ocr_config.get('x_tolerance', DEFAULT_X_TOLERANCE)
        x_groups = {}

        for cx, cy, w, h, text in all_vertical_items:
            x_key = round(cx / x_tolerance)
            if x_key not in x_groups:
                x_groups[x_key] = []
            x_groups[x_key].append((cx, cy, w, h, text))

        sorted_columns = []
        for x_key in sorted(x_groups.keys(), reverse=True):
            column_items = sorted(x_groups[x_key], key=lambda s: s[1])
            sorted_columns.append((x_key, column_items))

        for x_key, column_items in sorted_columns:
            sorted_items = sorted(column_items, key=lambda s: s[1])

            y_gap_threshold = 100
            merged_groups = []
            current_group = []

            for i, (cx, cy, w, h, text) in enumerate(sorted_items):
                if i == 0:
                    current_group.append(text)
                else:
                    prev_cx, prev_cy, prev_w, prev_h, prev_text = sorted_items[i - 1]
                    if cy - prev_cy > y_gap_threshold:
                        if current_group:
                            merged_groups.append(''.join(current_group))
                        current_group = [text]
                    else:
                        current_group.append(text)

            if current_group:
                merged_groups.append(''.join(current_group))

            for merged_text in merged_groups:
                result.append(merged_text)

    # 2. 横排文本块：按行排序（从上到下，每行内从左到右）
    if horizontal_items:
        y_tolerance = ocr_config.get('y_tolerance', 50)
        y_groups = {}

        for cx, cy, w, h, text in horizontal_items:
            y_key = round(cy / y_tolerance)
            if y_key not in y_groups:
                y_groups[y_key] = []
            y_groups[y_key].append((cx, cy, w, h, text))

        sorted_rows = []
        for y_key in sorted(y_groups.keys()):
            row_items = sorted(y_groups[y_key], key=lambda s: s[0])
            sorted_rows.append((y_key, row_items))

        for y_key, row_items in sorted_rows:
            for cx, cy, w, h, text in row_items:
                result.append(text)

    return result


def sort_horizontal_layout(items: list, ocr_config: dict = None) -> list[str]:
    """对横排布局的OCR结果按阅读顺序排序

    横排阅读顺序：从上到下排列行，每行内从左到右

    参数:
        items: [(center_x, center_y, width, height, text), ...]
        ocr_config: OCR配置字典（可选）

    返回:
        按阅读顺序排列的文本列表
    """
    if not items:
        return []

    if ocr_config is None:
        ocr_config = {}

    y_tolerance = ocr_config.get('y_tolerance', DEFAULT_Y_TOLERANCE)

    y_groups = {}
    for cx, cy, w, h, text in items:
        y_key = round(cy / y_tolerance)
        if y_key not in y_groups:
            y_groups[y_key] = []
        y_groups[y_key].append((cx, cy, w, h, text))

    sorted_rows = []
    for y_key in sorted(y_groups.keys()):
        row_items = sorted(y_groups[y_key], key=lambda s: s[0])
        sorted_rows.append((y_key, row_items))

    result = []
    for y_key, row_items in sorted_rows:
        for cx, cy, w, h, text in row_items:
            result.append(text)

    return result