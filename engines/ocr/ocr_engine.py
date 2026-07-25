# -*- coding: utf-8 -*-
"""
OCR 提取引擎

使用 PaddleOCR 从 PDF 提取文本，支持竖排/横排日文识别和阅读顺序排序。
当 PyMuPDF 直接提取失败（乱码、CID字体损坏）时作为 fallback。

原 ocr_utils.py 迁移至此，跟随项目重构（engines/ocr/）。
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
# 内部工具函数
# ═══════════════════════════════════════════════════════════════

def _get_resource_dir() -> Path:
    """获取项目根目录（开发环境）或 PyInstaller 解压目录（打包后）"""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    # engines/ocr/ocr_engine.py → engines/ocr → engines → 项目根
    return Path(__file__).resolve().parent.parent.parent


def _load_api_config() -> dict:
    """加载 API 配置（供 LLM 方向判断使用）"""
    try:
        config_path = Path('config.json')
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f).get('api', {})
    except Exception:
        pass
    return {}


def _load_ocr_config() -> dict:
    """加载 OCR 配置（从 config.json，带默认值）"""
    default_config = {
        'dpi': 400,
        'x_tolerance': 30,
        'vertical_ratio_threshold': 1.2,
        'vertical_block_ratio': 0.3,
        'adaptive_threshold_block_size': 31,
        'adaptive_threshold_c': 10,
        'text_det_thresh': 0.3,
        'text_det_box_thresh': 0.5,
        'text_recognition_batch_size': 6,
        'use_textline_orientation': True,
    }
    try:
        config_path = Path('config.json')
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                ocr_config = json.load(f).get('ocr', {})
            for key, value in default_config.items():
                if key not in ocr_config:
                    ocr_config[key] = value
            return ocr_config
    except Exception:
        pass
    return default_config


def _setup_paddleocr_env() -> dict:
    """配置 PaddleOCR 运行环境（CPU/GPU）"""
    ocr_config = _load_ocr_config()
    use_gpu = ocr_config.get('use_gpu', False)

    if not use_gpu:
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
        os.environ['PADDLE_PLACE'] = 'CPU'
        return {'use_gpu': False}

    try:
        import paddle
        if paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
            print("  PaddleOCR 使用 GPU 模式")
            return {'use_gpu': True}
        else:
            print("  警告：GPU 不可用，回退到 CPU 模式")
            os.environ['CUDA_VISIBLE_DEVICES'] = ''
            os.environ['PADDLE_PLACE'] = 'CPU'
            return {'use_gpu': False}
    except Exception:
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
        os.environ['PADDLE_PLACE'] = 'CPU'
        return {'use_gpu': False}


def _setup_paddleocr_models() -> dict:
    """配置 PaddleOCR 使用本地模型目录（如果存在）"""
    resource_dir = _get_resource_dir()
    models_dir = resource_dir / "models" / "paddleocr"

    if not models_dir.exists():
        return {}

    os.environ["PADDLEX_HOME"] = str(resource_dir)

    paddlex_official = resource_dir / ".paddlex" / "official_models"
    if not paddlex_official.exists():
        paddlex_official.parent.mkdir(parents=True, exist_ok=True)
        try:
            import subprocess
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(paddlex_official), str(models_dir)],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                import shutil
                shutil.copytree(models_dir, paddlex_official, dirs_exist_ok=True)
        except Exception:
            pass

    return {
        'PP-OCRv6_medium_det': str(models_dir / "PP-OCRv6_medium_det"),
        'PP-OCRv6_medium_rec': str(models_dir / "PP-OCRv6_medium_rec"),
        'PP-LCNet_x1_0_doc_ori': str(models_dir / "PP-LCNet_x1_0_doc_ori"),
        'PP-LCNet_x1_0_textline_ori': str(models_dir / "PP-LCNet_x1_0_textline_ori"),
        'UVDoc': str(models_dir / "UVDoc"),
    }


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def extract_with_ocr(file_path: Path, clean_for_translation: bool = False) -> str:
    """使用 PaddleOCR 提取 PDF 文本（PyMuPDF 失败时的 fallback）。

    适用于 PDF 使用编码混淆保护（CID 字体）的情况。
    支持竖排日文识别和阅读顺序排序。

    参数:
        file_path: PDF 文件路径
        clean_for_translation: 是否清洗为翻译用的对话内容

    返回:
        提取的文本字符串，失败时返回空字符串
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("  OCR提取需要PyMuPDF")
        return ""

    try:
        from paddleocr import PaddleOCR
    except ImportError:
        print("  PaddleOCR未安装，无法进行OCR提取")
        return ""

    text_blocks = []

    with fitz.open(file_path) as doc:
        total_pages = len(doc)
        print(f"  OCR提取: 共{total_pages}页")

        # 初始化 PaddleOCR（只初始化一次）
        print("  使用PaddleOCR进行识别...")
        try:
            _setup_paddleocr_env()
            model_paths = _setup_paddleocr_models()
            ocr_config = _load_ocr_config()

            ocr_params = {
                'lang': 'japan',
                'use_doc_orientation_classify': False,
                'use_doc_unwarping': False,
                'use_textline_orientation': True,
                'det_db_thresh': ocr_config.get('text_det_thresh', 0.3),
                'det_db_box_thresh': ocr_config.get('text_det_box_thresh', 0.4),
                'text_recognition_batch_size': ocr_config.get('text_recognition_batch_size', 6),
            }

            if model_paths:
                if 'PP-OCRv6_medium_det' in model_paths:
                    ocr_params['text_detection_model_dir'] = model_paths['PP-OCRv6_medium_det']
                if 'PP-OCRv6_medium_rec' in model_paths:
                    ocr_params['text_recognition_model_dir'] = model_paths['PP-OCRv6_medium_rec']
                if 'PP-LCNet_x1_0_textline_ori' in model_paths:
                    ocr_params['textline_orientation_model_dir'] = model_paths['PP-LCNet_x1_0_textline_ori']
                print("  使用本地模型目录")

            try:
                ocr = PaddleOCR(**ocr_params)
            except Exception:
                print("  本地模型初始化失败，回退到自动下载模型...")
                fb_params = {k: v for k, v in ocr_params.items()
                             if k not in ('text_detection_model_dir', 'text_recognition_model_dir',
                                          'textline_orientation_model_dir')}
                ocr = PaddleOCR(**fb_params)
        except Exception as e:
            print(f"  PaddleOCR初始化失败: {e}")
            return ""

        for page_num, page in enumerate(doc, 1):
            ocr_config = _load_ocr_config()
            dpi = ocr_config.get('dpi', 400)

            pix = page.get_pixmap(alpha=False, colorspace=fitz.csGRAY, dpi=dpi)
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                tmp_path = tmp.name
            pix.save(tmp_path)

            try:
                import cv2
                img = cv2.imread(tmp_path, cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    block_size = ocr_config.get('adaptive_threshold_block_size', 31)
                    c_value = ocr_config.get('adaptive_threshold_c', 10)
                    img = cv2.adaptiveThreshold(
                        img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY, block_size, c_value)
                    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
                    img = cv2.morphologyEx(img, cv2.MORPH_OPEN, kernel)
                    cv2.imwrite(tmp_path, img)

                result = ocr.predict(tmp_path)

                if result and len(result) > 0:
                    page_res = result[0]
                    rec_texts = page_res.get('rec_texts', [])
                    rec_boxes = page_res.get('rec_boxes', [])

                    items = []
                    for i, text in enumerate(rec_texts):
                        if i < len(rec_boxes) and text:
                            box = rec_boxes[i]
                            if len(box) >= 4:
                                x1, y1, x2, y2 = box[0], box[1], box[2], box[3]
                                items.append((
                                    (x1 + x2) / 2, (y1 + y2) / 2,
                                    abs(x2 - x1), abs(y2 - y1), text))

                    # 排版方向检测 + 排序
                    from core.layout_analyzer import (
                        detect_vertical_layout, sort_vertical_layout, sort_horizontal_layout)
                    if detect_vertical_layout(items, ocr_config):
                        page_texts = sort_vertical_layout(items, ocr_config)
                    else:
                        page_texts = sort_horizontal_layout(items, ocr_config)

                    # 后处理：过长文本块按标点分割
                    processed = []
                    for text in page_texts:
                        if len(text) > 40:
                            segments = re.split(r'([、。！？…・〜～♡])', text)
                            merged = []
                            for j in range(0, len(segments), 2):
                                s = segments[j]
                                if j + 1 < len(segments):
                                    s += segments[j + 1]
                                if s.strip():
                                    merged.append(s.strip())
                            if all(len(s) < 10 for s in merged):
                                processed.append(text)
                            else:
                                processed.extend(merged)
                        else:
                            processed.append(text)
                    page_texts = processed

                page_text = '\n'.join(page_texts) if page_texts else ''

                if page_text.strip():
                    text_blocks.append(page_text)
                    print(f"    第{page_num}/{total_pages}页 OCR成功 ({len(page_text)}字符)")
                else:
                    print(f"    第{page_num}/{total_pages}页 OCR无结果")

            except Exception as e:
                print(f"    第{page_num}/{total_pages}页 OCR失败: {e}")
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    result = '\n'.join(text_blocks)

    if clean_for_translation:
        from utils.text_filter import clean_script_for_translation
        return clean_script_for_translation(result)
    else:
        from utils.text_filter import _filter_page_numbers
        return _filter_page_numbers(result)
