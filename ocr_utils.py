"""
【已弃用】OCR 工具模块

原实现已迁移至 engines/ocr/ocr_engine.py（OCR 引擎）和 core/layout_analyzer.py（排版逻辑）。
本文件保留仅作向后兼容，新代码请直接从新位置导入。

迁移对照:
    from ocr_utils import extract_with_ocr
    → from engines.ocr import extract_with_ocr

    from ocr_utils import _load_ocr_config
    → from engines.ocr.ocr_engine import _load_ocr_config

    from ocr_utils import detect_vertical_layout, sort_vertical_layout
    → from core.layout_analyzer import detect_vertical_layout, sort_vertical_layout
"""

# 兼容性重导出
from engines.ocr.ocr_engine import (
    _load_ocr_config,
    _load_api_config,
    _get_resource_dir,
    _setup_paddleocr_env,
    _setup_paddleocr_models,
    extract_with_ocr,
)
from core.layout_analyzer import (
    detect_vertical_layout,
    sort_vertical_layout,
    sort_horizontal_layout,
)
