# -*- coding: utf-8 -*-
"""
OCR 引擎抽象层

定义 OCR 提取器的接口（Protocol），以及 PaddleOCR 具体实现。
便于未来替换 OCR 引擎（如 Tesseract、EasyOCR、Cloud OCR）。
"""

from __future__ import annotations
from pathlib import Path
from typing import Protocol


# ==================== OCR 结果类型 ====================

class OCRResult:
    """单页 OCR 结果"""
    __slots__ = ('page_num', 'text', 'items', 'is_vertical')

    def __init__(self, page_num: int, text: str = '',
                 items: list = None, is_vertical: bool = True):
        self.page_num = page_num
        self.text = text
        self.items = items or []  # [(center_x, center_y, width, height, text), ...]
        self.is_vertical = is_vertical


# ==================== OCR 引擎接口 ====================

class OCREngine(Protocol):
    """OCR 引擎接口（结构化鸭子类型）

    所有 OCR 引擎实现只需满足此接口即可被 pipeline 使用。
    """

    def extract(self, file_path: Path, *, dpi: int = 400, **kwargs) -> list[OCRResult]:
        """
        从 PDF 文件逐页提取 OCR 文本

        参数:
            file_path: PDF 文件路径
            dpi: 渲染分辨率
            **kwargs: 引擎特定参数

        返回:
            OCRResult 列表，每页一个
        """
        ...

    @property
    def name(self) -> str:
        """引擎名称（用于日志和配置选择）"""
        ...


# ==================== PaddleOCR 引擎实现 ====================

class PaddleOCREngine:
    """基于 PaddleOCR 的日文 OCR 引擎（支持竖排）"""

    name = 'paddleocr'

    def __init__(self, ocr_config: dict = None):
        self._ocr_config = ocr_config or {}
        self._ocr = None

    def _ensure_ocr(self):
        """延迟初始化 PaddleOCR（避免导入开销）"""
        if self._ocr is not None:
            return
        from paddleocr import PaddleOCR

        cfg = self._ocr_config
        params = {
            'lang': 'japan',
            'use_doc_orientation_classify': False,
            'use_doc_unwarping': False,
            'use_textline_orientation': cfg.get('use_textline_orientation', True),
            'det_db_thresh': cfg.get('text_det_thresh', 0.3),
            'det_db_box_thresh': cfg.get('text_det_box_thresh', 0.4),
            'text_recognition_batch_size': cfg.get('text_recognition_batch_size', 6),
        }

        # 尝试使用本地模型路径
        model_paths = cfg.get('_model_paths', {})
        if model_paths:
            if 'PP-OCRv6_medium_det' in model_paths:
                params['text_detection_model_dir'] = model_paths['PP-OCRv6_medium_det']
            if 'PP-OCRv6_medium_rec' in model_paths:
                params['text_recognition_model_dir'] = model_paths['PP-OCRv6_medium_rec']
            if 'PP-LCNet_x1_0_textline_ori' in model_paths:
                params['textline_orientation_model_dir'] = model_paths['PP-LCNet_x1_0_textline_ori']

        self._ocr = PaddleOCR(**params)

    def extract(self, file_path: Path, *, dpi: int = 400, **kwargs) -> list[OCRResult]:
        """逐页 OCR 提取"""
        import fitz
        import cv2
        import os
        import tempfile
        from core.layout_analyzer import sort_vertical_layout

        self._ensure_ocr()
        results = []
        cfg = self._ocr_config

        with fitz.open(file_path) as doc:
            for page_num, page in enumerate(doc, 1):
                pix = page.get_pixmap(alpha=False, colorspace=fitz.csGRAY, dpi=dpi)

                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                    tmp_path = tmp.name
                pix.save(tmp_path)

                try:
                    # 图像预处理：二值化
                    img = cv2.imread(tmp_path, cv2.IMREAD_GRAYSCALE)
                    if img is not None:
                        block_size = cfg.get('adaptive_threshold_block_size', 31)
                        c_value = cfg.get('adaptive_threshold_c', 10)
                        img = cv2.adaptiveThreshold(
                            img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                            cv2.THRESH_BINARY, block_size, c_value)
                        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
                        img = cv2.morphologyEx(img, cv2.MORPH_OPEN, kernel)
                        cv2.imwrite(tmp_path, img)

                    raw_result = self._ocr.predict(tmp_path)
                    items = self._parse_boxes(raw_result)
                    page_text = '\n'.join(sort_vertical_layout(items, cfg))

                    results.append(OCRResult(
                        page_num=page_num,
                        text=page_text,
                        items=items,
                        is_vertical=True,
                    ))
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

        return results

    @staticmethod
    def _parse_boxes(ocr_result) -> list:
        """解析 PaddleOCR 原始结果为 items 列表"""
        items = []
        if not ocr_result or len(ocr_result) == 0:
            return items

        page_res = ocr_result[0]
        rec_texts = page_res.get('rec_texts', [])
        rec_boxes = page_res.get('rec_boxes', [])

        for i, text in enumerate(rec_texts):
            if i < len(rec_boxes) and text:
                box = rec_boxes[i]
                if len(box) >= 4:
                    x1, y1, x2, y2 = box[0], box[1], box[2], box[3]
                    center_x = (x1 + x2) / 2
                    center_y = (y1 + y2) / 2
                    width = abs(x2 - x1)
                    height = abs(y2 - y1)
                    items.append((center_x, center_y, width, height, text))

        return items