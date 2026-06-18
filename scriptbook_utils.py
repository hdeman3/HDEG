"""
台本识别与处理工具模块

提供PDF文本提取（支持竖排日文）、乱序检测、台本解析等功能。
"""

import re
import shutil
from pathlib import Path
from typing import Optional


# ==================== 台本文件识别配置 ====================

# 台本文件关键词（用于识别台本文件）
SCRIPTBOOK_KEYWORDS = [
    '台本', 'だいほん', 'だい本', 'ダイホン', 'script', '台本付き',
    '仮台本', 'かり台本', '本編', 'ほんぺん',
    'セリフ初稿', 'せりふしょこう', 'シナリオ', 'しなりお',
    '演技指定', 'えんぎしてい', '射精タイミング', '全章',
    'track', 'トラック', 'RJ', '音声作品', 'シチュエーション'
]

# 台本文件扩展名
SCRIPTBOOK_EXTS = {'.txt', '.pdf'}


# ==================== PDF文本提取 ====================

def _is_page_number(text: str) -> bool:
    """判断文本是否为页码编号
    
    页码特征：
    1. 纯数字
    2. 数字+斜杠+数字（如 1/57）
    3. 短数字串（1-4位）
    """
    text = text.strip()
    if not text:
        return False
    
    # 纯数字（1-4位）
    if re.match(r'^\d{1,4}$', text):
        return True
    
    # 数字/数字格式（如 1/57, 2/100）
    if re.match(r'^\d{1,4}\s*/\s*\d{1,4}$', text):
        return True
    
    return False


def _is_header_footer(text: str) -> bool:
    """判断文本是否为页眉页脚内容
    
    常见页眉页脚特征：
    1. 包含"製糖"、"製作"等制作信息
    2. 包含作品名+数字
    """
    text = text.strip()
    if not text:
        return False
    
    # 制作信息
    if re.search(r'製糖|製作|発行|著作', text):
        return True
    
    return False


def _is_scene_description(text: str) -> bool:
    """判断是否为场景描述（非对话内容）
    
    场景描述特征：
    1. 以■、〇、●等符号开头
    2. 包含"本編"、"プロローグ"、"エピローグ"等章节标记
    3. 包含位置信息（ソファ上、ベッド上等）
    """
    text = text.strip()
    if not text:
        return False
    
    # 以特殊符号开头的场景描述
    if re.match(r'^[■〇●▲◆]', text):
        return True
    
    # 章节标记
    if re.search(r'本編|プロローグ|エピローグ|トラック\d+|第[0-9０-９]+章', text):
        return True
    
    return False


def _is_direction(text: str) -> bool:
    """判断是否为演技指示/动作描述
    
    演技指示特征：
    1. 以（）或［］包裹的内容
    2. 包含动作描述（キス、フェラ等动作说明）
    3. 包含表情/情绪描述
    """
    text = text.strip()
    if not text:
        return False
    
    # 以（）包裹的内容
    if text.startswith('（') and text.endswith('）'):
        return True
    
    # 以［］包裹的内容
    if text.startswith('［') and text.endswith('］'):
        return True
    
    # 包含特定动作描述词
    direction_keywords = [
        'キス', 'フェラ', '愛撫', '射精', '潮吹', '絶頂',
        '挿入', '騎乗位', '正常位', '後背位', '側位',
        '耳舐め', '手コキ', 'パイズリ', '足コキ',
        '服を', '脱ぐ', '裸', '勃起', '濡れ',
        '腰を', '腰振', 'ピストン', '抽送',
        '口の中', '口内', '顔射', '中出し',
        'クリトリス', 'オナニー', '自慰',
        '乳首', 'おっぱい', 'お尻', 'おまんこ',
        'ちんぽ', 'ちんちん', 'おちんちん',
        'シーツ', 'ベッド', 'ソファ', '浴室',
        'バイブ', 'ローター', '電マ', 'ディルド',
        '拘束', '縛り', '目隠し', '口塞ぎ',
        'スパンキング', 'アナル', 'フィスト',
        '浣腸', '放尿', '脱糞', '嘔吐',
        '血', '傷', '痛', '苦し',
        '死', '殺', '暴力', '虐待',
        '近親相姦', '強姦', '凌辱', '調教',
        '孕', '妊娠', '出産', '堕胎',
        '幼女', 'ロリ', 'ショタ', '少年',
        '獣', '動物', '虫', '触手',
        '異種', 'モンスター', 'ゾンビ',
        '血', '肉', '骨', '臓器',
        '死体', '遺体', '棺', '墓',
        '呪', '怨', '霊', '鬼',
        '神', '仏', '寺', '神社',
        '魔法', '呪文', '召喚', '契約',
        '異世界', '転生', '召喚',
        '貴族', '平民', '奴隷', '傭兵',
        '王', '女王', '王子', '公主',
        '騎士', '魔法使い', '僧侶', '盗賊',
        '戦士', '弓手', '格闘家', '暗殺者',
        '商人', '鍛冶屋', '薬師', '料理人',
        '貴族', '平民', '奴隷', '傭兵',
        '王', '女王', '王子', '公主',
        '騎士', '魔法使い', '僧侶', '盗賊',
        '戦士', '弓手', '格闘家', '暗殺者',
        '商人', '鍛冶屋', '薬師', '料理人',
    ]
    
    for keyword in direction_keywords:
        if keyword in text:
            return True
    
    return False


def _is_position_marker(text: str) -> bool:
    """判断是否为位置/方向标记
    
    如：【正面・中】、【右耳・近距離】等
    """
    text = text.strip()
    if not text:
        return False
    
    # 以【】包裹的位置信息
    if re.match(r'^【[左右中正上下远近密着耳前後]+', text):
        return True
    
    return False


def _is_se_marker(text: str) -> bool:
    """判断是否为SE/音效标记
    
    如：SE: 財布からお札を出し渡す音
    """
    text = text.strip()
    if not text:
        return False
    
    # SE标记
    if re.match(r'^SE[:：]', text, re.IGNORECASE):
        return True
    
    # 效果音标记
    if re.search(r'【効果音[:：]', text):
        return True
    
    return False


def _is_dialogue(text: str) -> bool:
    """判断是否为角色对话
    
    对话特征：
    1. 以…、！、？、～等标点结尾
    2. 包含第一人称（私、俺、僕、わたし等）
    3. 包含口语表达（です、ます、だ、よ、ね等）
    """
    text = text.strip()
    if not text:
        return False
    
    # 排除明显的非对话内容
    if _is_scene_description(text):
        return False
    if _is_direction(text):
        return False
    if _is_position_marker(text):
        return False
    if _is_se_marker(text):
        return False
    if _is_page_number(text):
        return False
    if _is_header_footer(text):
        return False
    
    # 对话通常以这些标点结尾
    if re.search(r'[…！？～。\.\?!]$', text):
        return True
    
    # 包含口语表达
    if re.search(r'(です|ます|だよ|だね|だわ|だろ|かよ|かな|って|けど|から|のに|なら|なぁ|ねぇ|よぉ|わぁ|あっ|えっ|うっ|んっ|はっ|ひっ|ふっ|へっ|ほっ|ちっ|しっ|きっ|みっ|りっ|いっ|てっ|でっ|とっ|どっ|なっ|にっ|ぬっ|ねっ|のっ|はっ|ひっ|ふっ|へっ|ほっ|まっ|みっ|むっ|めっ|もっ|やっ|ゆっ|よっ|らっ|りっ|るっ|れっ|ろっ|わっ|をっ|んっ)', text):
        return True
    
    return False


def _normalize_text(text: str) -> str:
    """规范化文本（去除字符间和标点周围的空格）
    
    PyMuPDF提取的日文文本字符间可能有空格，
    如 "リ ア ル タ イ ム" -> "リアルタイム"
    如 "は い は い 、 わ か ってますって" -> "はいはい、わかってますって"
    """
    result = text
    
    # 1. 去除日文字符间的空格（多次循环直到没有变化）
    prev = None
    while prev != result:
        prev = result
        result = re.sub(r'([\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff\uff00-\uffef])\s+(?=[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff\uff00-\uffef])', r'\1', result)
    
    # 2. 去除日文标点前的空格（如 " 、" -> "、"）
    result = re.sub(r'\s+([、。！？…・〜～」』）])', r'\1', result)
    
    # 3. 去除日文标点后的空格（如 "、 " -> "、"）
    result = re.sub(r'([「『（])\s+', r'\1', result)
    
    # 4. 去除数字与日文字符间的多余空格（如 "１ ７" -> "１７"）
    result = re.sub(r'([０-９0-9])\s+(?=[０-９0-9])', r'\1', result)
    
    # 5. 去除日文字符与标点间的空格
    result = re.sub(r'([\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff])\s+([、。！？…・〜～])', r'\1\2', result)
    
    return result


def clean_script_for_translation(text: str) -> str:
    """清洗台本内容，只保留角色对话
    
    去除内容：
    1. 页码行
    2. 纯数字行
    3. SE标记行
    4. 位置标记行
    5. 演技指示行（括号内容）
    6. 音效/指示标记（｟...｠）
    7. 书名号标记（《...》）
    8. 特殊标记（<...>、{...}）
    9. 内联括号内容（行内任何位置的括号指示）
    
    保留内容：
    1. 角色对话（台词）
    2. 角色名标记（【角色名】）
    
    返回清洗后的文本
    """
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        # 跳过空行
        if not stripped:
            continue
        
        # 规范化文本（去除字符间空格）
        normalized = _normalize_text(stripped)
        
        # 跳过纯页码行（如 "1 / 57"、"1 / 35"）
        if re.match(r'^\d+\s*/\s*\d+$', normalized):
            continue
        
        # 跳过纯数字行
        if re.match(r'^\d+$', normalized):
            continue
        
        # 跳过纯SE标记行
        if re.match(r'^SE[:：]', normalized, re.IGNORECASE):
            continue
        
        # 跳过纯位置标记行（如 "【正面・中】"）
        if re.match(r'^【[左右中正远近密着耳・→]+】$', normalized):
            continue
        
        # 跳过纯演技指示行（括号内容）
        if re.match(r'^[（(][^）)]*[）)]$', normalized):
            continue
        
        # 跳过｟...｠标记行（音效/指示）
        if re.match(r'^｟[^｠]*｠$', normalized):
            continue
        
        # 跳过《...》标记行（书名号）
        if re.match(r'^《[^》]*》$', normalized):
            continue
        
        # 跳过<...>标记行
        if re.match(r'^<[^>]*>$', normalized):
            continue
        
        # 跳过{...}标记行
        if re.match(r'^\{[^}]*\}$', normalized):
            continue
        
        # 跳过分割线标记
        if '▲区切り' in normalized or '▲ 区切り' in normalized:
            continue
        
        # 跳过（ここまで）标记
        if '（ここまで）' in normalized or '(ここまで)' in normalized:
            continue
        
        # 跳过包含射精SE的行
        if '｟射精ＳＥ｠' in normalized or '｟射精SE｠' in normalized:
            continue
        
        # 去除行首的页码和编号（如 "1 / 57 1 2 3 4 内容"）
        # 匹配: 数字 / 数字 空格 任意内容
        match = re.match(r'^\d+\s*/\s*\d+\s+(.+)$', normalized)
        if match:
            rest = match.group(1).strip()
            # 继续去除前导数字
            while True:
                num_match = re.match(r'^\d+\s+(.+)$', rest)
                if num_match:
                    next_rest = num_match.group(1).strip()
                    # 检查是否有日文内容
                    if re.search(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', next_rest):
                        rest = next_rest
                    else:
                        break
                else:
                    break
            normalized = rest
        
        # 去除行首残留的 "/ 数字" 格式（如 "/ 57 内容"）
        match = re.match(r'^/\s*\d+\s+(.+)$', normalized)
        if match:
            normalized = match.group(1).strip()
        
        # 去除行首的数字编号（如 "1 内容"、"12 内容"）
        # 匹配: 数字 空格 内容（数字后必须有日文内容）
        while True:
            num_prefix_match = re.match(r'^(\d+)\s+(.+)$', normalized)
            if num_prefix_match:
                rest = num_prefix_match.group(2).strip()
                # 检查剩余内容是否以日文开头
                if rest and re.search(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', rest):
                    normalized = rest
                else:
                    break
            else:
                break
        
        # 去除行首残留的数字串（如 "121314"）
        match = re.match(r'^(\d+)([\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff].*)$', normalized)
        if match:
            normalized = match.group(2)
        
        # 去除内联的｟...｠标记（音效/指示）
        normalized = re.sub(r'｟[^｠]*｠', '', normalized)
        
        # 去除内联的括号内容（演技指示）
        normalized = re.sub(r'[（(][^）)]*[）)]', '', normalized)
        
        # 去除内联的《...》标记（书名号）
        normalized = re.sub(r'《[^》]*》', '', normalized)
        
        # 去除内联的<...>标记
        normalized = re.sub(r'<[^>]*>', '', normalized)
        
        # 去除内联的{...}标记
        normalized = re.sub(r'\{[^}]*\}', '', normalized)
        
        # 清理多余空格
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        
        # 如果清洗后为空或只剩数字，跳过
        if not normalized or re.match(r'^[\d\s]+$', normalized):
            continue
        
        # 保留对话
        cleaned_lines.append(normalized)
    
    return '\n'.join(cleaned_lines)


def _filter_page_numbers(text: str) -> str:
    """过滤文本中的页码和页眉页脚内容（兼容旧版）
    
    返回过滤后的文本
    """
    lines = text.split('\n')
    filtered_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        # 跳过纯页码行
        if _is_page_number(stripped):
            continue
        
        # 跳过页眉页脚
        if _is_header_footer(stripped):
            continue
        
        # 跳过纯数字串（可能是页码残留）
        if re.match(r'^\d+$', stripped):
            continue
        
        # 跳过包含大量重复数字的行（如 22221111111111987654321）
        # 这些通常是PDF页面上的装饰性数字
        if re.search(r'(\d)\1{3,}', stripped):  # 4个以上连续相同数字
            # 但如果这行也包含日文，则保留
            japanese_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', stripped))
            if japanese_chars == 0:
                continue
        
        # 跳过以长数字串开头的行
        if re.match(r'^\d{8,}', stripped):  # 8位以上数字开头
            # 提取数字后的内容
            match = re.search(r'^\d+\s*(.*)', stripped)
            if match and match.group(1).strip():
                # 保留数字后的内容
                filtered_lines.append(match.group(1).strip())
                continue
            else:
                continue
        
        filtered_lines.append(line)
    
    return '\n'.join(filtered_lines)


def _detect_pdf_order_issues(words: list, page_text: str) -> tuple[bool, str]:
    """检测PDF文本提取是否可能存在乱序问题
    
    返回: (是否乱序, 原因)
    """
    if not words or not page_text:
        return False, ""
    
    # 1. 检查文本块数量过少（可能提取失败）
    if len(words) < 3:
        return True, f"文本块数量过少({len(words)}个)"
    
    # 2. 检查文本块坐标分布（如果所有文本块都在同一位置，可能是乱序）
    x_positions = [w.get('x0', 0) for w in words]
    y_positions = [w.get('top', 0) for w in words]
    
    if len(set(x_positions)) < 2 and len(set(y_positions)) < 2:
        return True, "所有文本块坐标相同"
    
    # 3. 检查提取的文本是否包含大量无意义字符（可能是乱码）
    # 统计日文字符比例
    japanese_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30fa]', page_text))
    total_chars = len(page_text.replace('\n', '').replace(' ', ''))
    
    if total_chars > 0:
        ja_ratio = japanese_chars / total_chars
        # 如果日文字符比例过低（<10%），可能是乱序或乱码
        if ja_ratio < 0.1 and total_chars > 50:
            return True, f"日文字符比例过低({ja_ratio:.1%})"
    
    # 4. 检查文本块顺序是否合理（y坐标应该大致递增）
    # 如果y坐标剧烈波动，可能是乱序
    if len(y_positions) > 5:
        # 计算相邻文本块的y坐标差
        y_diffs = [abs(y_positions[i+1] - y_positions[i]) for i in range(len(y_positions)-1)]
        avg_diff = sum(y_diffs) / len(y_diffs)
        
        # 如果平均差值过大，可能是乱序
        if avg_diff > 200:  # 阈值可根据实际情况调整
            return True, f"文本块y坐标分布异常(平均差值{avg_diff:.1f})"
    
    # 5. 检查开头是否为数字串（页码混入正文）
    # 过滤页码后检查
    filtered_text = _filter_page_numbers(page_text)
    if filtered_text.strip():
        # 检查过滤后的文本开头是否还是数字
        first_line = filtered_text.strip().split('\n')[0].strip()
        if first_line and re.match(r'^\d+$', first_line):
            return True, f"过滤页码后开头仍为数字串: {first_line[:20]}"
    
    return False, ""


def _extract_with_pymupdf(file_path: Path, clean_for_translation: bool = False) -> str:
    """使用PyMuPDF提取PDF文本
    
    PyMuPDF (fitz) 对各种PDF格式有更好的兼容性，
    特别是对竖排日文文本的处理更好。
    
    参数:
        file_path: PDF文件路径
        clean_for_translation: 是否清洗为翻译用的对话内容
    """
    try:
        import fitz  # PyMuPDF
        
        text_blocks = []
        
        with fitz.open(file_path) as doc:
            for page_num, page in enumerate(doc, 1):
                # 获取文本块（带坐标）
                blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
                
                if not blocks:
                    continue
                
                # 收集所有文本span，带坐标
                all_spans = []
                
                for block in blocks:
                    if block.get('type') != 0:  # 跳过图片块
                        continue
                    
                    # 获取块内所有行
                    lines = block.get('lines', [])
                    for line in lines:
                        # 获取行内所有span
                        spans = line.get('spans', [])
                        for span in spans:
                            text = span.get('text', '')
                            if text.strip():
                                # 获取span的bbox坐标
                                bbox = span.get('bbox', [0, 0, 0, 0])
                                x0, y0, x1, y1 = bbox
                                # 使用中心点坐标
                                cx = (x0 + x1) / 2
                                cy = (y0 + y1) / 2
                                all_spans.append((cx, cy, text, x0, y0))
                
                if not all_spans:
                    continue
                
                # 分析页面布局：判断是横排还是竖排
                # 方法：检查同一x坐标（列）的字符数量
                
                # 按x坐标分组，使用tolerance来合并相近的x坐标
                x_groups = {}
                x_tolerance = 10  # 像素
                
                for cx, cy, text, x0, y0 in all_spans:
                    x_key = round(cx / x_tolerance)
                    if x_key not in x_groups:
                        x_groups[x_key] = []
                    x_groups[x_key].append((cx, cy, text, x0, y0))
                
                # 找出最大的x组（同一列字符数最多）
                max_x_group_size = max(len(group) for group in x_groups.values()) if x_groups else 0
                
                # 如果最大的x组有超过3个字符，说明是竖排布局
                # （同一列有很多字符，从上到下排列）
                is_vertical_layout = max_x_group_size > 3
                
                # 如果最大的x组只有1-2个字符，说明是横排布局
                # （每个字符都在不同的列，一行一个或几个）
                is_horizontal_layout = not is_vertical_layout
                
                # 使用更宽松的tolerance来分组
                # 根据字符大小动态计算tolerance
                if all_spans:
                    # 估算平均字符高度
                    avg_height = sum(abs(s[4] - s[1]) for s in all_spans) / len(all_spans) if all_spans else 16
                    tolerance = max(avg_height * 0.8, 10)  # 至少10像素
                else:
                    tolerance = 16
                
                if is_horizontal_layout:
                    # 横排布局：按y坐标分组（行）
                    all_spans.sort(key=lambda s: (round(s[1] / tolerance), s[0]))
                    
                    page_lines = []
                    current_y_group = -10000
                    current_line_spans = []
                    
                    for cx, cy, text, x0, y0 in all_spans:
                        y_group = round(cy / tolerance)
                        if y_group != current_y_group:
                            if current_line_spans:
                                # 按x坐标排序当前行的span（从左到右）
                                current_line_spans.sort(key=lambda s: s[0])
                                line_text = ''.join([s[2] for s in current_line_spans])
                                page_lines.append(line_text)
                            current_y_group = y_group
                            current_line_spans = [(cx, cy, text, x0, y0)]
                        else:
                            current_line_spans.append((cx, cy, text, x0, y0))
                    
                    # 处理最后一行
                    if current_line_spans:
                        current_line_spans.sort(key=lambda s: s[0])
                        line_text = ''.join([s[2] for s in current_line_spans])
                        page_lines.append(line_text)
                    
                    page_text = '\n'.join(page_lines)
                else:
                    # 竖排布局：按x坐标分组（列），从右到左
                    all_spans.sort(key=lambda s: (round(s[0] / tolerance), s[1]))
                    
                    page_columns = []
                    current_x_group = -10000
                    current_col_spans = []
                    
                    for cx, cy, text, x0, y0 in all_spans:
                        x_group = round(cx / tolerance)
                        if x_group != current_x_group:
                            if current_col_spans:
                                # 按y坐标排序当前列的span（从上到下）
                                current_col_spans.sort(key=lambda s: s[1])
                                col_text = ''.join([s[2] for s in current_col_spans])
                                page_columns.append((current_x_group, col_text))
                            current_x_group = x_group
                            current_col_spans = [(cx, cy, text, x0, y0)]
                        else:
                            current_col_spans.append((cx, cy, text, x0, y0))
                    
                    # 处理最后一列
                    if current_col_spans:
                        current_col_spans.sort(key=lambda s: s[1])
                        col_text = ''.join([s[2] for s in current_col_spans])
                        page_columns.append((current_x_group, col_text))
                    
                    # 按x坐标从大到小排列（从右到左）
                    page_columns.sort(key=lambda c: -c[0])
                    page_text = '\n'.join([c[1] for c in page_columns])
                
                text_blocks.append(page_text)
        
        result = '\n'.join(text_blocks)
        
        # 根据参数决定是否清洗
        if clean_for_translation:
            return clean_script_for_translation(result)
        else:
            return _filter_page_numbers(result)
        
    except ImportError:
        return ""
    except Exception as e:
        print(f"  PyMuPDF提取失败: {e}")
        return ""


def _extract_with_pdfplumber(file_path: Path) -> str:
    """使用pdfplumber提取PDF文本（支持竖排文本重排）"""
    try:
        import pdfplumber
        
        text_blocks = []
        
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                # 提取带坐标的文本块
                words = page.extract_words(
                    keep_blank_chars=True,
                    x_tolerance=3,
                    y_tolerance=3
                )
                
                if not words:
                    continue
                
                # 分析文本方向：检查文本块的旋转角度
                vertical_count = 0
                horizontal_count = 0
                
                for w in words:
                    width = w.get('x1', 0) - w.get('x0', 0)
                    height = w.get('bottom', 0) - w.get('top', 0)
                    direction = w.get('direction', 0) if 'direction' in w else 0
                    
                    if direction in (90, 270, -90) or height > width * 1.5:
                        vertical_count += 1
                    else:
                        horizontal_count += 1
                
                is_vertical = vertical_count > horizontal_count
                
                if is_vertical:
                    # 竖排模式
                    sorted_words = sorted(words, key=lambda w: (-w.get('x0', 0), w.get('top', 0)))
                    columns = {}
                    x_tolerance = 20
                    
                    for w in sorted_words:
                        x_key = round(w.get('x0', 0) / x_tolerance)
                        if x_key not in columns:
                            columns[x_key] = []
                        columns[x_key].append(w)
                    
                    page_text_blocks = []
                    for x_key in sorted(columns.keys(), reverse=True):
                        col_words = sorted(columns[x_key], key=lambda w: w.get('top', 0))
                        col_text = ''.join(w.get('text', '') for w in col_words)
                        page_text_blocks.append(col_text)
                    
                    page_text = '\n'.join(page_text_blocks)
                else:
                    sorted_words = sorted(words, key=lambda w: (w.get('top', 0), w.get('x0', 0)))
                    page_text = ''.join(w.get('text', '') for w in sorted_words)
                
                # 过滤页码和装饰性数字
                filtered_page_text = _filter_page_numbers(page_text)
                text_blocks.append(filtered_page_text)
        
        result = '\n\n'.join(text_blocks)
        return _filter_page_numbers(result)
        
    except ImportError:
        return ""
    except Exception as e:
        print(f"  pdfplumber提取失败: {e}")
        return ""


def extract_pdf_text(file_path: Path, clean_for_translation: bool = False) -> str:
    """提取PDF文件的文本内容
    
    优先使用PyMuPDF（更好的兼容性），
    如果失败则回退到pdfplumber（支持竖排文本重排）。
    
    参数:
        file_path: PDF文件路径
        clean_for_translation: 是否清洗为翻译用的对话内容
    
    自动过滤页码和装饰性数字。
    """
    # 优先尝试PyMuPDF（先不清洗，检查原始质量）
    raw_text = _extract_with_pymupdf(file_path, clean_for_translation=False)
    if raw_text and len(raw_text) > 100:
        # 检查原始提取质量
        japanese_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30fa]', raw_text))
        total_chars = len(raw_text.replace('\n', '').replace(' ', ''))
        if total_chars > 0 and japanese_chars / total_chars > 0.05:
            # 原始提取质量良好，现在清洗（如果需要）
            if clean_for_translation:
                cleaned_text = clean_script_for_translation(raw_text)
                print(f"  使用PyMuPDF提取成功: {len(cleaned_text)} 字符（清洗后）")
                return cleaned_text
            else:
                print(f"  使用PyMuPDF提取成功: {len(raw_text)} 字符")
                return raw_text
    
    # 回退到pdfplumber
    print(f"  PyMuPDF提取效果不佳，尝试pdfplumber...")
    text = _extract_with_pdfplumber(file_path)
    if text:
        # 检查pdfplumber提取质量
        japanese_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30fa]', text))
        total_chars = len(text.replace('\n', '').replace(' ', ''))
        if total_chars > 0 and japanese_chars / total_chars > 0.05:
            print(f"  使用pdfplumber提取成功: {len(text)} 字符")
            # 如果需要清洗
            if clean_for_translation:
                text = clean_script_for_translation(text)
            return text
    
    # 最后尝试pypdf
    print(f"  尝试pypdf...")
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        result = _filter_page_numbers(text)
        if clean_for_translation:
            result = clean_script_for_translation(result)
        return result
    except Exception as e:
        print(f"  提取PDF文本失败: {file_path} - {e}")
        return ""


# ==================== 台本文件识别 ====================

def is_scriptbook_file(file_path: Path) -> bool:
    """检测文件是否为台本文件
    
    判断依据：
    1. 文件扩展名为 .txt 或 .pdf
    2. 文件名包含台本关键词
    3. 文件内容包含典型的台本标记（角色名【】、SE、方向指示#等）
    """
    if file_path.suffix.lower() not in SCRIPTBOOK_EXTS:
        return False
    
    # 检查文件名是否包含台本关键词
    filename = file_path.name.lower()
    for keyword in SCRIPTBOOK_KEYWORDS:
        if keyword.lower() in filename:
            return True
    
    # 检查文件内容是否包含台本特征
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return False
    
    # 检测台本特征标记
    has_character_marks = bool(re.search(r'^【[^】]+】', content, re.MULTILINE))
    has_se_marks = bool(re.search(r'^SE[:：\s]', content, re.MULTILINE))
    has_direction = bool(re.search(r'^#[^\n]+$', content, re.MULTILINE))
    has_track = bool(re.search(r'トラック\d+', content))
    
    # 至少包含两种特征才认为是台本
    features = sum([has_character_marks, has_se_marks, has_direction, has_track])
    return features >= 2


# ==================== 台本内容解析 ====================

# 正则模式定义
SE_PATTERNS = [
    r'^SE[:：]',           # SE: 或 SE：
    r'^SE\s',              # SE 开头
    r'^\s*SE\s*[:：]?\s*', # 可选空格
    r'^【効果音[:：]',     # 【効果音：xxx】
    r'^【効果音：',        # 【効果音：xxx】
]

DIRECTION_PATTERN = r'^#[^\n]+$'
CHARACTER_PATTERN = r'^【[^】]+】\s*$'
TRACK_PATTERN = r'^【トラック\d+[：：][^\]]*】'
CHAPTER_TITLE_PATTERN = r'^《[^》]+》'
POSITION_PATTERN = r'^【[左右中正遠近・→]+】\s*$'
MOVE_PATTERN = r'^【移動[:：]'
VOICE_STYLE_PATTERN = r'^【(?:囁き|通常|大声|小声|吐息)\s*】\s*$'
EJACULATION_PATTERN = r'^(★|☆).*射精|^【射精】'
POSITION_COMPLEX_PATTERN = r'^【(?:右耳|左耳|右|左|中央|正面|遠距離|近距離|中距離)[・→・]+[^】]*】\s*$'
DIALOGUE_INDENT_PATTERN = r'^\s+[^【《\(（#SE]'


def parse_scriptbook_content(content: str) -> list[dict]:
    """解析台本内容，提取对话行
    
    支持多种台本格式：
    1. 纯台词型（セリフ初稿台本）- 只有角色台词，无演技指示
    2. 带演技指定型（台本(演技指定あり)）- 含位置、演技指示
    3. 射精タイミング标注型 - 标注射精时机
    4. 纯数字编号型 - 最简格式，仅数字编号
    5. シナリオ型（剧本型）- 完整剧本格式，含效果音、位置
    
    返回: [{"line_num": int, "character": str, "text": str, "raw_line": str, "type": str}, ...]
    type: "dialogue" | "character" | "se" | "direction" | "track" | "chapter" | "empty" | "other"
    """
    lines = content.split('\n')
    parsed = []
    
    current_character = ""
    current_position = ""  # 当前位置（如右・中）
    
    for i, line in enumerate(lines, 1):
        raw_line = line
        stripped = line.strip()
        
        if not stripped:
            parsed.append({
                "line_num": i,
                "character": "",
                "text": "",
                "raw_line": raw_line,
                "type": "empty"
            })
            continue
        
        # 章节标题（如 《トラック１　エルフの子作り日》）
        if re.match(CHAPTER_TITLE_PATTERN, stripped):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "chapter"
            })
            continue
        
        # 音轨标记（如 【トラック1：xxx】）
        if re.match(TRACK_PATTERN, stripped):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "track"
            })
            continue
        
        # 角色名标记（如 【まどか】）
        if re.match(CHARACTER_PATTERN, stripped):
            current_character = re.search(r'【([^】]+)】', stripped).group(1) if re.search(r'【([^】]+)】', stripped) else ""
            parsed.append({
                "line_num": i,
                "character": current_character,
                "text": "",
                "raw_line": raw_line,
                "type": "character"
            })
            continue
        
        # 位置/演技指示（如 【右・中】、【右・中→右・近】）
        if re.match(POSITION_COMPLEX_PATTERN, stripped) or re.match(POSITION_PATTERN, stripped):
            # 提取位置信息
            pos_match = re.search(r'【([^】]+)】', stripped)
            if pos_match:
                current_position = pos_match.group(1)
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "direction"
            })
            continue
        
        # 移动指示（如 【移動：右耳・近距離】）
        if re.match(MOVE_PATTERN, stripped):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "direction"
            })
            continue
        
        # 声音方式指示（如 【囁き】、【通常】、【大声】）
        if re.match(VOICE_STYLE_PATTERN, stripped):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "direction"
            })
            continue
        
        # SE/音效标记（如 SE:潮吹、SE:射精、SE: 潮吹）
        is_se = False
        for pattern in SE_PATTERNS:
            if re.match(pattern, stripped, re.IGNORECASE):
                is_se = True
                break
        if is_se:
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "se"
            })
            continue
        
        # 效果音标记（如 【効果音：足音】）
        if re.search(r'【効果音[:：]', stripped):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "se"
            })
            continue
        
        # 方向指示（以 # 开头）
        if stripped.startswith('#'):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "direction"
            })
            continue
        
        # 时间标记（如 (秒数指定...20秒ほど...)）
        if stripped.startswith('(') and stripped.endswith(')'):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "direction"
            })
            continue
        
        # 射精标记（如 ★射精、【射精】）
        if re.match(EJACULATION_PATTERN, stripped):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "se"
            })
            continue
        
        # 章节标题（如 【プロローグ：xxx】、【第1章：xxx】）
        if re.match(r'^【(?:プロローグ|第[0-9０-９]+章|エピローグ)[：:][^】]*】', stripped):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "chapter"
            })
            continue
        
        # 纯数字行（如音轨编号）
        if re.match(r'^[０-９0-9]+$', stripped):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "track"
            })
            continue
        
        # 行首有空格的台词（シナリオ型）
        if re.match(DIALOGUE_INDENT_PATTERN, raw_line):
            # 去掉前导空格
            text = stripped
            parsed.append({
                "line_num": i,
                "character": current_character,
                "text": text,
                "raw_line": raw_line,
                "type": "dialogue"
            })
            continue
        
        # 普通对话
        parsed.append({
            "line_num": i,
            "character": current_character,
            "text": stripped,
            "raw_line": raw_line,
            "type": "dialogue"
        })
    
    return parsed


# ==================== 台本语言检测 ====================

def detect_scriptbook_language(file_path: Path) -> str:
    """检测台本文件的语言
    
    返回: "japanese" | "chinese" | "empty" | "unknown"
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return "unknown"
    
    # 提取对话内容
    parsed = parse_scriptbook_content(content)
    dialogue_texts = [p["text"] for p in parsed if p["type"] == "dialogue"]
    
    if not dialogue_texts:
        return "empty"
    
    all_text = '\n'.join(dialogue_texts)
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', all_text))
    japanese_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30fa\u30fd-\u30ff]', all_text))
    
    if japanese_chars > 0:
        if chinese_chars > 0:
            if japanese_chars > chinese_chars * 0.1:
                return "japanese"
            else:
                return "chinese"
        else:
            return "japanese"
    elif chinese_chars > 0:
        return "chinese"
    else:
        return "empty"


# ==================== 调试入口 ====================

# ==================== 纯台词列表提取 ====================

def extract_dialogue_lines(file_path: Path) -> list[str]:
    """从台本文件中提取纯台词列表
    
    提取规则：
    1. 读取清洗后的台本内容（支持PDF和TXT）
    2. 去除空行、纯数字行、SE行、位置标记行
    3. 去除行内括号注释（注音假名保留）
    4. 去除行首数字编号和页码
    5. 保留角色对话，严格按原顺序
    
    返回: 台词列表，每个元素是一句台词
    """
    # PDF文件需要先提取文本
    if file_path.suffix.lower() == '.pdf':
        content = extract_pdf_text(file_path, clean_for_translation=True)
        if not content:
            print(f"PDF提取失败: {file_path}")
            return []
    else:
        # TXT文件直接读取
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # 清洗文本
            content = clean_script_for_translation(content)
        except Exception as e:
            print(f"读取文件失败: {e}")
            return []
    
    lines = content.split('\n')
    dialogue_lines = []
    
    for line in lines:
        stripped = line.strip()
        
        # 跳过空行
        if not stripped:
            continue
        
        # 跳过纯数字行
        if re.match(r'^\d+$', stripped):
            continue
        
        # 跳过纯标点行
        if re.match(r'^[…！？。\s]+$', stripped):
            continue
        
        # 跳过SE标记行
        if re.match(r'^SE[:：]', stripped, re.IGNORECASE):
            continue
        
        # 跳过纯位置标记行（如 "【正面・中】"）
        if re.match(r'^【[左右中正远近密着耳・→]+】$', stripped):
            continue
        
        # 跳过章节标题行（如 "■ 本編"）
        if re.match(r'^[■〇●▲◆]', stripped):
            continue
        
        # 跳过页码行（如 "1 / 57"）
        if re.match(r'^\d+\s*/\s*\d+$', stripped):
            continue
        
        # 跳过包含大量连续数字的行（如 "121314151617181920212223"）
        if re.search(r'\d{5,}', stripped):
            # 但如果这行也包含日文对话，则保留对话部分
            if re.search(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', stripped):
                # 去除数字，保留日文部分
                pass  # 继续处理
            else:
                continue
        
        # 去除行内括号注释（但保留注音假名）
        # 去除（...）和(...)内的内容（通常是演技指示）
        cleaned = re.sub(r'[（(][^）)]*[）)]', '', stripped)
        
        # 去除｟...｠标记
        cleaned = re.sub(r'｟[^｠]*｠', '', cleaned)
        
        # 去除《...》标记
        cleaned = re.sub(r'《[^》]*》', '', cleaned)
        
        # 去除<...>标记
        cleaned = re.sub(r'<[^>]*>', '', cleaned)
        
        # 去除{...}标记
        cleaned = re.sub(r'\{[^}]*\}', '', cleaned)
        
        # 去除SE标记（行内）
        cleaned = re.sub(r'SE[:：][^\n]*', '', cleaned, flags=re.IGNORECASE)
        
        # 去除位置标记（行内）
        cleaned = re.sub(r'【[左右中正远近密着耳・→]+】', '', cleaned)
        
        # 去除行首的数字编号（如 "1 ", "12 ", "12131415 "）
        while True:
            num_match = re.match(r'^(\d+)\s+', cleaned)
            if num_match:
                rest = cleaned[num_match.end():]
                # 检查剩余内容是否有日文
                if re.search(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', rest):
                    cleaned = rest
                else:
                    break
            else:
                break
        
        # 去除行首残留的数字串（如 "121314" 后面直接接日文）
        match = re.match(r'^(\d+)([\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff])', cleaned)
        if match:
            cleaned = match.group(2) + cleaned[match.end():]
        
        # 去除行首的 "/ 数字" 格式（如 "/ 57"）
        match = re.match(r'^/\s*\d+\s+', cleaned)
        if match:
            cleaned = cleaned[match.end():]
        
        # 清理多余空格
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        # 去除行首的角色名标记（如 "ゆき"、"さき"、"ゆき・さき"）
        # 匹配: 角色名（日文）+ 空格 + 台词
        match = re.match(r'^([\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff・]+)\s+(.+)$', cleaned)
        if match:
            # 检查第二部分是否以日文开头（是台词）
            potential_dialogue = match.group(2).strip()
            if potential_dialogue and re.search(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', potential_dialogue):
                cleaned = potential_dialogue
        
        # 如果清洗后为空或太短，跳过
        if not cleaned or len(cleaned) < 2:
            continue
        
        # 如果清洗后只剩标点，跳过
        if re.match(r'^[…！？。\s]+$', cleaned):
            continue
        
        # 如果清洗后只剩数字，跳过
        if re.match(r'^[\d\s]+$', cleaned):
            continue
        
        # 如果清洗后主要是数字（数字占比超过50%），跳过
        digit_count = len(re.findall(r'\d', cleaned))
        if digit_count > len(cleaned) * 0.5:
            continue
        
        dialogue_lines.append(cleaned)
    
    return dialogue_lines


def save_dialogue_list(file_path: Path, output_path: Optional[Path] = None) -> Path:
    """将台本文件转换为纯台词列表并保存
    
    参数:
        file_path: 台本文件路径（.txt或.extracted.txt）
        output_path: 输出路径，默认为 file_path.with_suffix('.dialogue.json')
    
    返回: 输出文件路径
    """
    dialogue_lines = extract_dialogue_lines(file_path)
    
    if not output_path:
        output_path = file_path.with_suffix('.dialogue.json')
    
    # 保存为JSON格式
    data = {
        "source_file": str(file_path),
        "total_lines": len(dialogue_lines),
        "dialogue_lines": dialogue_lines
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"已保存纯台词列表: {output_path} ({len(dialogue_lines)} 句)")
    return output_path


# ==================== LLM ASR校对Prompt构造 ====================

def build_asr_correction_prompt(dialogue_lines: list[str], asr_text: str) -> str:
    """构造ASR校对的LLM prompt
    
    参数:
        dialogue_lines: 参考台词列表（从台本提取）
        asr_text: ASR转录的原始文本
    
    返回: 完整的prompt字符串
    """
    # 构建参考台词列表文本
    reference_lines = []
    for i, line in enumerate(dialogue_lines, 1):
        reference_lines.append(f"{i}. {line}")
    
    reference_text = '\n'.join(reference_lines)
    
    prompt = f"""你是一位日语ASR后期校对专家。我会提供两段文本：

【参考台词列表】：一段准确的剧本台词，按时间顺序排列。
【ASR转录结果】：从音频中自动识别出的原始文本，可能含有错字、漏字、多字，且顺序可能混乱。

请你将ASR转录结果与参考台词对齐，并逐句输出修正后的准确日语文本。

规则：
1. 尽量使用参考台词中的原句，除非ASR明确出现了不同但合理的内容（即兴发挥），此时保留ASR的说法。
2. 如果某处无法对齐，保留ASR原句，并在后面标注 [未对齐]。
3. 输出格式：每行一句台词，无额外标签。
4. 不要输出编号，只输出纯台词文本。
5. 保持台词的原始顺序。

【参考台词列表】
{reference_text}

【ASR转录结果】
{asr_text}

请开始校对，输出修正后的日语文本："""
    
    return prompt


def build_asr_correction_system_prompt() -> str:
    """构造ASR校对的系统指令
    
    返回: 系统指令字符串
    """
    return (
        "你是一位专门处理日文成人音声（ASMR/RJ作品）字幕的专业本地化工程师，"
        "同时也是精通日语和中文的R18音声脚本翻译专家，"
        "以及专注于成人音声字幕的ASR（自动语音识别）纠错专家。\n"
        "你的职责是将ASR转录结果与参考台词列表对齐，逐句输出修正后的准确日语文本。\n"
        "规则：\n"
        "1. 尽量使用参考台词中的原句，除非ASR明确出现了不同但合理的内容（即兴发挥），此时保留ASR的说法。\n"
        "2. 如果某处无法对齐，保留ASR原句，并在后面标注 [未对齐]。\n"
        "3. 输出格式：每行一句台词，无额外标签。\n"
        "4. 不要输出编号，只输出纯台词文本。\n"
        "5. 保持台词的原始顺序。\n"
    )


def correct_asr_with_scriptbook(
    scriptbook_path: Path,
    asr_text: str,
    llm_client=None,
    model: str = "gemini-2.5-flash",
    api_key: Optional[str] = None
) -> list[str]:
    """使用台本校对ASR转录结果
    
    参数:
        scriptbook_path: 台本文件路径（.extracted.txt）
        asr_text: ASR转录的原始文本
        llm_client: LLM客户端（可选）
        model: 使用的模型名称
        api_key: API密钥（可选）
    
    返回: 修正后的台词列表
    """
    # 1. 提取纯台词列表
    dialogue_lines = extract_dialogue_lines(scriptbook_path)
    
    if not dialogue_lines:
        print("警告: 无法从台本提取台词")
        return []
    
    print(f"从台本提取了 {len(dialogue_lines)} 句参考台词")
    
    # 2. 构造prompt
    prompt = build_asr_correction_prompt(dialogue_lines, asr_text)
    
    # 3. 调用LLM（这里需要集成到translate.py的LLM调用逻辑中）
    # 由于translate.py有自己的LLM调用方式，这里返回prompt供外部调用
    
    return dialogue_lines, prompt


if __name__ == "__main__":
    import sys
    
    print("=" * 50)
    print("台本识别与处理工具 - 调试模式")
    print("=" * 50)
    print()
    
    # 测试PDF提取
    test_pdf = Path("台本/RJ01007837_台本 データ.pdf")
    if test_pdf.exists():
        print(f"测试PDF提取: {test_pdf}")
        text = extract_pdf_text(test_pdf)
        if text:
            print(f"提取成功，共 {len(text)} 字符")
            print("前200字符:")
            print(text[:200])
        else:
            print("提取失败或检测到乱序")
    else:
        print(f"测试文件不存在: {test_pdf}")
        print("请放置一个PDF文件到台本目录进行测试")
    
    print()
    print("=" * 50)
    print("功能列表：")
    print("  - extract_pdf_text(file_path) -> str")
    print("  - is_scriptbook_file(file_path) -> bool")
    print("  - parse_scriptbook_content(content) -> list[dict]")
    print("  - detect_scriptbook_language(file_path) -> str")
    print("  - extract_dialogue_lines(file_path) -> list[str]")
    print("  - save_dialogue_list(file_path) -> Path")
    print("  - build_asr_correction_prompt(dialogue_lines, asr_text) -> str")
    print("  - correct_asr_with_scriptbook(scriptbook_path, asr_text) -> tuple")
    print("=" * 50)
