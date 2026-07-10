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
        # 延迟导入，避免循环依赖
        from scriptbook_utils import clean_script_for_translation, _filter_page_numbers
        return clean_script_for_translation(result)
    else:
        # 延迟导入，避免循环依赖
        from scriptbook_utils import _filter_page_numbers
        return _filter_page_numbers(result)


def detect_vertical_layout(items: list, ocr_config: dict = None) -> bool:
    """检测页面是否为竖排布局

    竖排特征：
    1. 文本块高度明显大于宽度（竖排文字块）
    2. 多个文本块在相近的x坐标上形成列
    3. 每列内y坐标变化大（文本垂直排列）

    参数:
        items: [(center_x, center_y, width, height, text), ...]
        ocr_config: OCR配置字典（可选，默认从_load_ocr_config获取）

    返回:
        True 如果认为是竖排布局
    """
    if ocr_config is None:
        ocr_config = _load_ocr_config()

    if len(items) < 4:
        return False

    # 特征1：检查文本块的宽高比
    # 竖排文本块通常高度 > 宽度 * 1.2
    vertical_block_count = 0
    for cx, cy, w, h, text in items:
        ratio_threshold = ocr_config.get('vertical_ratio_threshold', 1.2)
        if h > w * ratio_threshold:  # 高度明显大于宽度（竖排特征）
            vertical_block_count += 1

    # 如果超过阈值比例的文本块是竖排形状，直接判定为竖排
    vertical_ratio = ocr_config.get('vertical_block_ratio', 0.3)
    if len(items) > 0 and vertical_block_count / len(items) > vertical_ratio:
        return True

    # 特征2：按x坐标分组成列（使用tolerance）
    x_tolerance = ocr_config.get('x_tolerance', 30)  # 像素容差，认为同一列的x坐标差异
    x_groups = {}

    for cx, cy, w, h, text in items:
        x_key = round(cx / x_tolerance)
        if x_key not in x_groups:
            x_groups[x_key] = []
        x_groups[x_key].append((cx, cy, w, h, text))

    # 统计列信息
    valid_columns = 0
    total_column_items = 0

    for x_key, group in x_groups.items():
        if len(group) >= 2:  # 列中至少有2个文本块
            valid_columns += 1
            total_column_items += len(group)

    # 竖排判断条件：
    # 1. 至少有2个有效列
    # 2. 平均每列至少有2个文本块
    if valid_columns >= 2 and total_column_items / max(valid_columns, 1) >= 2:
        return True

    return False


def sort_vertical_layout(items: list, ocr_config: dict = None) -> list[str]:
    """对竖排布局的OCR结果按阅读顺序排序

    竖排阅读顺序：从右到左排列列，每列内从上到下
    对于被PaddleOCR错误识别为横排的短文本块（如角色名），也按竖排方式排序

    参数:
        items: [(center_x, center_y, width, height, text), ...]
        ocr_config: OCR配置字典（可选，默认从_load_ocr_config获取）

    返回:
        按阅读顺序排列的文本列表
    """
    if not items:
        return []

    if ocr_config is None:
        ocr_config = _load_ocr_config()

    # 分离竖排和横排文本块
    vertical_items = []   # h > w * 1.2
    horizontal_items = []  # w > h，但内容较短（如角色名、标记等）
    square_items = []     # 其他

    ratio_threshold = ocr_config.get('vertical_ratio_threshold', 1.2)

    for item in items:
        cx, cy, w, h, text = item
        if h > w * ratio_threshold:
            vertical_items.append(item)
        elif w > h:
            # 对于横排文本块，如果内容较短（<20字符），可能是竖排文本块的误检测
            # 将其视为竖排文本块处理
            if len(text) < 20:
                vertical_items.append(item)
            else:
                horizontal_items.append(item)
        else:
            square_items.append(item)

    # 去重：移除被其他文本块完全包含的横排文本块
    # 如果一个横排文本块的内容完全包含在某个竖排文本块中，则移除它
    texts_in_vertical = set()
    for cx, cy, w, h, text in vertical_items:
        for i in range(len(text)):
            for j in range(i + 1, min(i + 20, len(text) + 1)):
                texts_in_vertical.add(text[i:j])
    
    filtered_horizontal = []
    for cx, cy, w, h, text in horizontal_items:
        # 如果横排文本块的内容完全包含在竖排文本块中，或者横排块超宽（w > 1000），则移除
        if w > 1000:
            continue
        # 检查是否大部分内容都在竖排文本块中
        is_duplicate = False
        for v_cx, v_cy, v_w, v_h, v_text in vertical_items:
            if text in v_text:
                is_duplicate = True
                break
        if not is_duplicate:
            filtered_horizontal.append((cx, cy, w, h, text))
    
    horizontal_items = filtered_horizontal

    result = []

    # 1. 先处理竖排文本块（包括短横排文本块）：按列排序（从右到左，每列内从上到下）
    all_vertical_items = vertical_items + square_items
    if all_vertical_items:
        x_tolerance = ocr_config.get('x_tolerance', 30)
        x_groups = {}

        for cx, cy, w, h, text in all_vertical_items:
            x_key = round(cx / x_tolerance)
            if x_key not in x_groups:
                x_groups[x_key] = []
            x_groups[x_key].append((cx, cy, w, h, text))

        # 对每列内的文本按y坐标排序（从上到下）
        sorted_columns = []
        for x_key in sorted(x_groups.keys(), reverse=True):  # 从右到左（x从大到小）
            column_items = sorted(x_groups[x_key], key=lambda s: s[1])  # 按y排序（从上到下）
            sorted_columns.append((x_key, column_items))

        # 合并所有列的文本
        # 对于竖排文本，同一列内的文本块应该合并为一行
        # 但如果文本块之间y坐标差距太大（>100），则认为是不同段落，不合并
        for x_key, column_items in sorted_columns:
            # 按y坐标排序
            sorted_items = sorted(column_items, key=lambda s: s[1])
            
            # 合并同一列的文本，但y坐标差距太大的文本块之间用换行分隔
            y_gap_threshold = 100
            merged_groups = []
            current_group = []
            
            for i, (cx, cy, w, h, text) in enumerate(sorted_items):
                if i == 0:
                    current_group.append(text)
                else:
                    prev_cx, prev_cy, prev_w, prev_h, prev_text = sorted_items[i-1]
                    if cy - prev_cy > y_gap_threshold:
                        # y坐标差距太大，认为是不同段落
                        if current_group:
                            merged_groups.append(''.join(current_group))
                        current_group = [text]
                    else:
                        current_group.append(text)
            
            if current_group:
                merged_groups.append(''.join(current_group))
            
            for merged_text in merged_groups:
                result.append(merged_text)

    # 2. 再处理横排文本块：按行排序（从上到下，每行内从左到右）
    if horizontal_items:
        y_tolerance = 50  # 行容差
        y_groups = {}

        for cx, cy, w, h, text in horizontal_items:
            y_key = round(cy / y_tolerance)
            if y_key not in y_groups:
                y_groups[y_key] = []
            y_groups[y_key].append((cx, cy, w, h, text))

        # 对每行内的文本按x坐标排序（从左到右）
        sorted_rows = []
        for y_key in sorted(y_groups.keys()):  # 从上到下（y从小到大）
            row_items = sorted(y_groups[y_key], key=lambda s: s[0])  # 按x排序（从左到右）
            sorted_rows.append((y_key, row_items))

        # 合并所有行的文本
        for y_key, row_items in sorted_rows:
            for cx, cy, w, h, text in row_items:
                result.append(text)

    return result


def sort_horizontal_layout(items: list, ocr_config: dict = None) -> list[str]:
    """对横排布局的OCR结果按阅读顺序排序

    横排阅读顺序：从上到下排列行，每行内从左到右

    参数:
        items: [(center_x, center_y, width, height, text), ...]
        ocr_config: OCR配置字典（可选，默认从_load_ocr_config获取）

    返回:
        按阅读顺序排列的文本列表
    """
    if not items:
        return []

    if ocr_config is None:
        ocr_config = _load_ocr_config()

    # 按y坐标分组成行（使用tolerance）
    y_tolerance = ocr_config.get('y_tolerance', 30)
    if y_tolerance is None:
        y_tolerance = 30

    y_groups = {}
    for cx, cy, w, h, text in items:
        y_key = round(cy / y_tolerance)
        if y_key not in y_groups:
            y_groups[y_key] = []
        y_groups[y_key].append((cx, cy, w, h, text))

    # 对每行内的文本按x坐标排序（从左到右）
    sorted_rows = []
    for y_key in sorted(y_groups.keys()):  # 从上到下（y从小到大）
        row_items = sorted(y_groups[y_key], key=lambda s: s[0])  # 按x排序（从左到右）
        sorted_rows.append((y_key, row_items))

    # 合并所有行的文本
    result = []
    for y_key, row_items in sorted_rows:
        for cx, cy, w, h, text in row_items:
            result.append(text)

    return result
