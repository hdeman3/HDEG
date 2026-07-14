"""
OCR工具模块

提供PDF文本OCR提取功能，支持竖排日文识别和阅读顺序排序。
"""

import os
import re
import json
import sys
import tempfile
from pathlib import Path


def _get_resource_dir() -> Path:
    """获取资源目录路径（开发环境为脚本目录，打包后为exe目录）"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    else:
        return Path(__file__).parent


def _setup_paddleocr_env():
    """配置 PaddleOCR 运行环境

    默认使用 CPU 模式，通过 config.json 的 ocr.use_gpu 参数控制。
    当 config.json 中 ocr.use_gpu 为 true 时使用 GPU。
    """
    # 从 config.json 读取 GPU 配置
    ocr_config = _load_ocr_config()
    use_gpu = ocr_config.get('use_gpu', False)

    if not use_gpu:
        # 强制 CPU 模式
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
        os.environ['PADDLE_PLACE'] = 'CPU'
        return {'use_gpu': False}

    # GPU 模式：检查是否有 CUDA
    try:
        import paddle
        if paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
            print("  PaddleOCR 使用 GPU 模式")
            return {'use_gpu': True}
        else:
            print("  警告：config.json 中 use_gpu 为 true，但未检测到可用 GPU，回退到 CPU 模式")
            os.environ['CUDA_VISIBLE_DEVICES'] = ''
            os.environ['PADDLE_PLACE'] = 'CPU'
            return {'use_gpu': False}
    except Exception:
        # 如果无法检测，回退到 CPU
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
        os.environ['PADDLE_PLACE'] = 'CPU'
        return {'use_gpu': False}


def _setup_paddleocr_models():
    """配置 PaddleOCR 使用本地模型目录"""
    resource_dir = _get_resource_dir()
    models_dir = resource_dir / "models" / "paddleocr"

    if not models_dir.exists():
        return {}

    # 设置环境变量，让 PaddleOCR 从本地加载
    os.environ["PADDLEX_HOME"] = str(resource_dir)

    # 检查是否需要创建 .paddlex 目录链接
    paddlex_official = resource_dir / ".paddlex" / "official_models"
    if not paddlex_official.exists():
        paddlex_official.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Windows 下使用 junction
            import subprocess
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(paddlex_official), str(models_dir)],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                # junction 失败，直接复制
                import shutil
                shutil.copytree(models_dir, paddlex_official, dirs_exist_ok=True)
        except Exception:
            pass

    # 返回模型路径映射
    return {
        'PP-OCRv6_medium_det': str(models_dir / "PP-OCRv6_medium_det"),
        'PP-OCRv6_medium_rec': str(models_dir / "PP-OCRv6_medium_rec"),
        'PP-LCNet_x1_0_doc_ori': str(models_dir / "PP-LCNet_x1_0_doc_ori"),
        'PP-LCNet_x1_0_textline_ori': str(models_dir / "PP-LCNet_x1_0_textline_ori"),
        'UVDoc': str(models_dir / "UVDoc"),
    }


def _load_ocr_config() -> dict:
    """加载OCR配置

    从config.json读取OCR相关参数
    """
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
                config = json.load(f)
            ocr_config = config.get('ocr', {})
            # Merge with defaults
            for key, value in default_config.items():
                if key not in ocr_config:
                    ocr_config[key] = value
            return ocr_config
    except Exception:
        pass

    return default_config


def extract_with_ocr(file_path: Path, clean_for_translation: bool = False) -> str:
    """使用PaddleOCR提取PDF文本（当其他方法都失败时使用）

    适用于PDF使用了编码混淆保护（CID字体）的情况。
    使用PaddleOCR进行日文识别，支持竖排文本。

    参数:
        file_path: PDF文件路径
        clean_for_translation: 是否清洗为翻译用的对话内容
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("  OCR提取需要PyMuPDF")
        return ""

    # 尝试PaddleOCR
    try:
        from paddleocr import PaddleOCR
        paddleocr_available = True
    except ImportError:
        paddleocr_available = False
        print("  PaddleOCR未安装，无法进行OCR提取")
        return ""

    text_blocks = []

    with fitz.open(file_path) as doc:
        total_pages = len(doc)
        print(f"  OCR提取: 共{total_pages}页")

        if paddleocr_available:
            # 初始化PaddleOCR（只初始化一次）
            print("  使用PaddleOCR进行识别...")
            try:
                # 配置环境（CPU优先）
                env_config = _setup_paddleocr_env()
                # 配置本地模型路径
                model_paths = _setup_paddleocr_models()
                # 加载OCR配置
                ocr_config = _load_ocr_config()

                # 构建 PaddleOCR 参数
                ocr_params = {
                    'lang': 'japan',
                    'use_doc_orientation_classify': False,
                    'use_doc_unwarping': False,
                    'use_textline_orientation': True,
                    'det_db_thresh': ocr_config.get('text_det_thresh', 0.3),
                    'det_db_box_thresh': ocr_config.get('text_det_box_thresh', 0.4),
                    'text_recognition_batch_size': ocr_config.get('text_recognition_batch_size', 6),
                }

                # 如果本地模型存在，使用本地路径
                if model_paths:
                    if 'PP-OCRv6_medium_det' in model_paths:
                        ocr_params['text_detection_model_dir'] = model_paths['PP-OCRv6_medium_det']
                    if 'PP-OCRv6_medium_rec' in model_paths:
                        ocr_params['text_recognition_model_dir'] = model_paths['PP-OCRv6_medium_rec']
                    if 'PP-LCNet_x1_0_textline_ori' in model_paths:
                        ocr_params['textline_orientation_model_dir'] = model_paths['PP-LCNet_x1_0_textline_ori']
                    print("  使用本地模型目录")

                ocr = PaddleOCR(**ocr_params)
            except Exception as e:
                print(f"  PaddleOCR初始化失败: {e}")
                return ""

        for page_num, page in enumerate(doc, 1):
            # 加载OCR配置
            ocr_config = _load_ocr_config()

            # 渲染页面为灰度图（使用配置中的dpi，去除alpha通道）
            dpi = ocr_config.get('dpi', 400)
            pix = page.get_pixmap(alpha=False, colorspace=fitz.csGRAY, dpi=dpi)

            # 保存为临时PNG文件
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                tmp_path = tmp.name
            pix.save(tmp_path)

            page_text = ""

            try:
                # 图像预处理：二值化抑制噪点
                import cv2
                img = cv2.imread(tmp_path, cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    block_size = ocr_config.get('adaptive_threshold_block_size', 31)
                    c_value = ocr_config.get('adaptive_threshold_c', 10)
                    img = cv2.adaptiveThreshold(
                        img, 255,
                        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                        cv2.THRESH_BINARY,
                        block_size, c_value
                    )
                    # 轻微降噪
                    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
                    img = cv2.morphologyEx(img, cv2.MORPH_OPEN, kernel)
                    cv2.imwrite(tmp_path, img)

                # 使用PaddleOCR识别，保留坐标信息用于阅读顺序排序
                result = ocr.predict(tmp_path)

                page_texts = []
                if result and len(result) > 0:
                    page_res = result[0]
                    rec_texts = page_res.get('rec_texts', [])
                    rec_boxes = page_res.get('rec_boxes', [])
                    # 按阅读顺序排序（支持横排和竖排两种布局）
                    items = []
                    for i, text in enumerate(rec_texts):
                        if i < len(rec_boxes) and text:
                            box = rec_boxes[i]
                            if len(box) >= 4:
                                x1, y1, x2, y2 = box[0], box[1], box[2], box[3]
                                center_y = (y1 + y2) / 2
                                center_x = (x1 + x2) / 2
                                width = abs(x2 - x1)
                                height = abs(y2 - y1)
                                items.append((center_x, center_y, width, height, text))

                    # 用户已确认是竖排布局，直接使用竖排排序
                    page_texts = sort_vertical_layout(items, ocr_config)
                    
                    # 后处理：对过长的文本块进行分割
                    # PaddleOCR有时会将整行识别为一个文本块
                    processed_texts = []
                    for text in page_texts:
                        # 如果文本块过长（>40字符），尝试按标点分割
                        if len(text) > 40:
                            # 按常见日文标点分割（但保留标点）
                            import re
                            segments = re.split(r'([、。！？…・〜～♡])', text)
                            # 合并标点到前一段
                            merged = []
                            for i in range(0, len(segments), 2):
                                segment = segments[i]
                                if i + 1 < len(segments):
                                    segment += segments[i + 1]
                                if segment.strip():
                                    merged.append(segment.strip())
                            # 如果分割后每个片段都短，使用原文本
                            if all(len(s) < 10 for s in merged):
                                processed_texts.append(text)
                            else:
                                processed_texts.extend(merged)
                        else:
                            processed_texts.append(text)
                    
                    page_texts = processed_texts

                page_text = '\n'.join(page_texts)

                if page_text.strip():
                    text_blocks.append(page_text)
                    print(f"    第{page_num}/{total_pages}页 OCR成功 ({len(page_text)}字符)")
                else:
                    print(f"    第{page_num}/{total_pages}页 OCR无结果")

            except Exception as e:
                print(f"    第{page_num}/{total_pages}页 OCR失败: {e}")
            finally:
                # 清理临时文件
                try:
                    os.unlink(tmp_path)
                except:
                    pass

    result = '\n'.join(text_blocks)

    # 根据参数决定是否清洗
    if clean_for_translation:
        from utils.text_filter import clean_script_for_translation, _filter_page_numbers
        return clean_script_for_translation(result)
    else:
        from utils.text_filter import _filter_page_numbers
        return _filter_page_numbers(result)


# ==================== 布局分析函数（委托至 core/layout_analyzer） ====================
# 原函数实现已迁移到 core/layout_analyzer.py，此处保持兼容性重导出

from core.layout_analyzer import detect_vertical_layout, sort_vertical_layout, sort_horizontal_layout
