"""
台本识别与处理工具模块

提供PDF文本提取（支持竖排日文）、乱序检测、台本解析等功能。
"""

import re
import json
import shutil
from pathlib import Path
from typing import Optional

from ocr_utils import _load_ocr_config, extract_with_ocr, detect_vertical_layout, sort_vertical_layout


# ==================== 台本文件识别配置 ====================

# 台本文件关键词（用于识别台本文件）
SCRIPTBOOK_KEYWORDS = [
    '台本', 'だいほん', 'だい本', 'ダイホン', 'script', '台本付き',
    '仮台本', 'かり台本', '本編', 'ほんぺん',
    'セリフ初稿', 'せりふしょこう', 'シナリオ', 'しなりお',
    '演技指定', 'えんぎしてい', '射精タイミング', '全章',
    'track', 'トラック', 'RJ', '音声作品', 'シチュエーション',
    '射精箇所',  # 射精位置标注（台本的一种）
]

# 台本文件扩展名
SCRIPTBOOK_EXTS = {'.txt', '.pdf'}

# 非台本文件关键词（需要排除的特殊用途文件）
NON_SCRIPTBOOK_KEYWORDS = [
    'finishtime', 'finish_time', 'フィニッシュタイム',  # 高潮时间点
    'クレジット', 'credit',  # 演职员表
    'お射精メモ', '射精メモ',  # 射精备忘录
    'readme', 'read me', 'read_me',  # 说明文件
    '説明', '説明書',  # 说明书
    '特典', 'bonus',  # 特典内容
    'おまけ', 'omake',  # 特典/附赠
    'キャスト', 'cast',  # 演员表
    '声優',  # 声优信息
    '購入特典', '購入特典',  # 购买特典
    # 序言/前言/说明文件
    'プロローグ', 'prologue', 'はじめに', '初めに', '必ず', '読んで',
    'お読みください', '説明書', 'せつめいしょ', '注意事項',
    '_cleaned', '_processed', '_export',
]


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
    4. 包含世界観、設定等说明文字
    5. 【新增】描述性句子（不以对话标点结尾，不包含口语表达）
    6. 【新增】以动词结尾的动作描述（如 ドアを開ける、布団の中に潜り込む）
    7. 【新增】包含"〜と〜"格式的描述（如 ドアを開けると、兄の手をにぎりと）
    8. 【新增】心理描写（如 いいのか…いいのだろうか…この子は俺のことが好きなのか？）
    9. 【新增】以「」或『』包裹的旁白内容
    """
    text = text.strip()
    if not text:
        return False
    
    # 以特殊符号开头的场景描述
    if re.match(r'^[■〇●▲◆]', text):
        return True
    
    # 章节标记
    # 【修复】排除【トラック１】这种音轨标记格式，只匹配纯章节标题
    if re.search(r'本編|プロローグ|エピローグ|第[0-9０-９]+章', text):
        return True
    # 单独的トラック标记（如"トラック１"）是音轨标记，不是场景描述
    # 但"トラック１：xxx"或"■トラック１"是章节标题
    # 【修复】排除纯音轨标记（只有"トラック+数字"），只过滤带标题的
    if re.search(r'[■：:]トラック\d+', text):
        return True
    # 纯"トラック０"到"トラック９"是音轨标记，保留
    if re.match(r'^トラック[０-９0-9]+$', text):
        return False
    # "トラック１０"及以上或带额外内容的才是场景描述
    if re.search(r'トラック\d+.+', text) or re.search(r'トラック[０-９0-9]{2,}', text):
        return True
    
    # 世界観・設定等说明文字
    setting_keywords = [
        '世界観', '雰囲気', '設定', 'あらすじ', 'ストーリー',
        'キャラクター', '登場人物', '人物紹介',
        '台本初稿', '台本草案', 'セリフ初稿',
        '演技指示', '演技の補助', '補助のため',
    ]
    for keyword in setting_keywords:
        if keyword in text:
            return True
    
    # 角色外观描述
    # 【修复】排除包含对话标点和口语表达的台词，避免误伤
    if re.search(r'(小柄|大柄|華奢|無垢|雰囲気|口数|髪|目|包帯|眼帯)', text):
        # 如果包含对话标点或口语表达，可能是台词，不跳过
        has_dialogue_mark = bool(re.search(r'[…！？～。\.\?!♡]', text))
        has_colloquial = bool(re.search(r'(です|ます|だよ|だね|だわ|だろ|かよ|かな|って|けど|から|のに|なら|なぁ|ねぇ|よぉ|わぁ)', text))
        if not has_dialogue_mark and not has_colloquial:
            return True
    
    # 【新增】心理描写特征
    # 心理描写通常：
    # 1. 以问号结尾但不是对话（如 いいのか…いいのだろうか…）
    # 2. 包含"…"或"…か？"格式
    # 3. 包含"俺"、"私"等第一人称但不是对话
    # 4. 包含"最低"、"辛い"等心理状态描述
    
    # 心理描写关键词
    # 【注释】第一人称感受不是场景描述，不应作为过滤依据
    # psychology_keywords = [
    #     '最低', '辛い', '我慢', '耐えられ', '限界', '限り',
    #     '思う', '考える', '感じる', '思える', '考えられる',
    #     'だろうか', 'のだろうか', 'かもしれない', 'に違いない',
    #     'してしまう', 'なってしまう', 'てしまった', 'になってしまった',
    #     '慰めてもらおう', 'してもらおう', 'させよう',
    # ]
    # for keyword in psychology_keywords:
    #     if keyword in text:
    #         # 检查是否包含口语表达
    #         has_colloquial = bool(re.search(r'(です|ます|だよ|だね|だわ|だろ|かよ|かな|って|けど|から|のに|なら|なぁ|ねぇ|よぉ|わぁ)', text))
    #         if not has_colloquial:
    #             return True
    
    # 【新增】检查以「」或『』包裹的旁白内容
    # 如：「よかったですね…。おたがい退院できて。」
    if re.match(r'^[「『][」』].*[」』]$', text):
        # 检查内部是否包含口语表达
        inner = text[1:-1]  # 去掉「」或『』
        has_colloquial = bool(re.search(r'(です|ます|だよ|だね|だわ|だろ|かよ|かな|って|けど|から|のに|なら|なぁ|ねぇ|よぉ|わぁ)', inner))
        if not has_colloquial:
            return True
    
    # 【新增】检查是否为心理独白（以问号结尾但不是对话）
    # 如：いいのか…いいのだろうか…この子は俺のことが好きなのか？
    if text.endswith('？') or text.endswith('?'):
        # 检查是否包含"俺"或"私"等第一人称
        if re.search(r'(俺|私|僕|わたし|あたし)', text):
            # 检查是否包含口语表达
            has_colloquial = bool(re.search(r'(です|ます|だよ|だね|だわ|だろ|かよ|かな|って|けど|から|のに|なら|なぁ|ねぇ|よぉ|わぁ)', text))
            if not has_colloquial:
                return True
    
    # 【新增】检查是否为描述性句子（不以对话标点结尾）
    # 对话标点：…、！、？、～、。
    dialogue_endings = ['…', '！', '？', '～', '。', '!', '?', '.', '~']
    has_dialogue_end = any(text.endswith(ending) for ending in dialogue_endings)
    
    # 如果不以对话标点结尾，检查是否为描述性句子
    if not has_dialogue_end:
        # 【豁免】包含第一人称感受词的文本直接保留，不做动作过滤
        if re.search(r'(私|俺|僕|わたし|あたし|♡|～|です|ます)', text):
            pass  # 包含第一人称或感受表达的保留
        else:
            # 检查是否包含口语表达（如 です、ます、だよ 等）
            has_colloquial = bool(re.search(r'(です|ます|だよ|だね|だわ|だろ|かよ|かな|って|けど|から|のに|なら|なぁ|ねぇ|よぉ|わぁ)', text))
            
            # 如果不包含口语表达，可能是描述性句子
            if not has_colloquial:
                # 检查是否以动词结尾（描述性句子）
                # 常见动词结尾：する、なる、いる、ある、開ける、寝ていた 等
                verb_endings = [
                    'する', 'なる', 'いる', 'ある', 'おる', 'まいる',
                    '開ける', '閉める', '入る', '出る', '寝る', '起きる',
                    '歩く', '走る', '座る', '立つ', '潜る', '潜り込む',
                    '握る', '触る', '撫でる', '舐める', '触らせる',
                    '震える', 'もじもじ', '様子', '気後れ', '告白',
                    '寝ていた', '起きて', '開けると', '閉めると',
                    '入ると', '出ると', '潜り込む', '触触らせる',
                    '低くなる', '突き動かされて', '抑えられなくなって',
                ]
                for ending in verb_endings:
                    if text.endswith(ending):
                        return True
                
                # 检查是否包含"〜と〜"格式的描述（动作描述）
                # 如：ドアを開けると、兄の手をにぎりと
                # 【修复】要求"と"前面必须有动作动词，避免误伤包含"が"和"と"的台词
                if re.search(r'[をにでへからまで].{1,10}と', text):
                    # 如果不包含对话标点，可能是动作描述
                    if not any(punct in text for punct in ['…', '！', '？', '～']):
                        return True
                
                # 检查是否为纯动作描述（包含动作动词但不包含对话标点）
                action_keywords = [
                    'ドア', '部屋', '布団', 'ベッド', 'ソファ', '浴室',
                    '兄', '妹', '手', '足', '身体', '下半身', '躰',
                    '握り', '触', '撫で', '舐め', '潜り', '開け', '閉め',
                    '震える', 'もじもじ', '様子', '気後れ', '告白',
                    '低くなる', '突き動かされ', '抑えられなく',
                ]
                for keyword in action_keywords:
                    if keyword in text:
                        # 如果不包含对话标点或口语表达，可能是动作描述
                        if not any(punct in text for punct in ['…', '！', '？']) and not has_colloquial:
                            return True
    
    # 【新增】检查以"…"结尾的句子（可能是心理描写）
    if text.endswith('…') or text.endswith('…。'):
        # 检查是否包含口语表达
        has_colloquial = bool(re.search(r'(です|ます|だよ|だね|だわ|だろ|かよ|かな|って|けど|から|のに|なら|なぁ|ねぇ|よぉ|わぁ)', text))
        if not has_colloquial:
            # 检查是否包含"〜てしまう"格式（表示遗憾或心理状态）
            if re.search(r'[てで]しまう', text):
                return True
            
            # 检查是否包含"〜だろう"格式（表示推测或疑问）
            if re.search(r'だろう', text):
                return True
    
    return False


def _is_direction(text: str) -> bool:
    """判断是否为演技指示/动作描述
    
    演技指示特征：
    1. 以（）或［］包裹的内容
    2. 包含动作描述（キス、フェラ等动作说明）
    3. 包含表情/情绪描述
    4. 包含心理描写（妹（...）、兄（...）等格式）
    5. 包含位置指示（左耳、右耳等）
    """
    text = text.strip()
    if not text:
        return False
    
    # 以（）包裹的内容（心理描写或演技指示）
    if text.startswith('（') and text.endswith('）'):
        return True
    
    # 以［］包裹的内容
    if text.startswith('［') and text.endswith('］'):
        return True
    
    # 心理描写格式：妹（...）、兄（...）、永海（...）等
    # 匹配: 角色名（心理描写内容）
    if re.match(r'^[ぁ-んァ-ン一-龯]{1,10}（[^）]+）', text):
        return True
    
    # 心理描写格式：妹（...）开头但不完整（跨行）
    # 匹配: 妹（...、兄（...、永海（...
    if re.match(r'^[ぁ-んァ-ン一-龯]{1,10}（', text) and not text.endswith('）'):
        # 这是不完整的心理描写开头，跳过
        return True
    
    # 心理描写格式：以...）结尾但不以（开头（跨行的后半部分）
    # 匹配: ...）结尾的内容
    if text.endswith('）') and '（' not in text:
        # 这是心理描写的后半部分，跳过
        return True
    
    # 位置指示格式：左耳XXcm、右耳XXcm等
    if re.match(r'^[左右]耳[０-９0-9]+[cｃ][mｍ]', text):
        return True
    
    # 位置指示格式：包含"耳"和距离描述
    if re.search(r'[左右]耳\s*[０-９0-9]+', text):
        return True
    
    # 动作描述格式：以动词结尾但不包含对话标点
    # 常见动作动词：する、なる、いる、ある、開ける、閉める等
    action_endings = [
        'する', 'なる', 'いる', 'ある', 'おる', 'まいる',
        '開ける', '閉める', '入る', '出る', '寝る', '起きる',
        '歩く', '走る', '座る', '立つ', '潜る', '潜り込む',
        '握る', '触る', '撫でる', '舐める', 'キス', '告白',
        '震える', 'もじもじ', '様子', '気後れ',
    ]
    for ending in action_endings:
        if text.endswith(ending) and not re.search(r'[…！？～。]', text):
            return True
    
    # 包含特定动作描述词
    # 【修改】只保留摄影/音效技术词，删除性行为、身体部位、世界观等词汇
    direction_keywords = [
        # 摄影/录音技术词
        'カメラ', 'マイク', '録音', '撮影', '照明',
        'カット', 'テイク', 'NG', 'OK',
        '効果音', 'SE', 'BGM', '音楽',
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
    if re.match(r'^[SEＳＥ][:：]', text, re.IGNORECASE):
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


def _merge_fragmented_lines(text: str) -> str:
    """合并碎片化的行（PDF提取后每行只有一个字符的情况）
    
    PyMuPDF提取竖排PDF时，可能每行只有一个字符：
    ■
    タ
    イ
    ト
    ル
    
    这个函数会将这些碎片化的行合并成完整的句子：
    ■タイトル
    
    合并规则：
    1. 连续的单字符行（日文、数字、标点）合并为一行
    2. 遇到分隔线（---）或空行时，结束当前合并
    3. 保留原有的段落结构（通过空行分隔）
    """
    lines = text.split('\n')
    merged_lines = []
    current_line = ""
    
    for line in lines:
        stripped = line.strip()
        
        # 空行：结束当前合并，开始新段落
        if not stripped:
            if current_line:
                merged_lines.append(current_line)
                current_line = ""
            merged_lines.append("")  # 保留空行
            continue
        
        # 分隔线：结束当前合并，保留分隔线
        if re.match(r'^[-]{3,}$', stripped):
            if current_line:
                merged_lines.append(current_line)
                current_line = ""
            merged_lines.append(stripped)
            continue
        
        # 检查是否为单字符行（日文、数字、标点、符号）
        is_single_char = len(stripped) == 1 and (
            re.match(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff\uff00-\uffef]', stripped) or  # 日文
            re.match(r'[０-９0-9a-zA-Z]', stripped) or  # 数字/字母
            re.match(r'[、。！？…・〜～「」『』（）［］【】《》〈〉・]', stripped) or  # 标点
            re.match(r'[■●○◆▲▽★☆♡♥❤♦️→←↑↓]', stripped)  # 符号
        )
        
        if is_single_char:
            # 单字符行：合并到当前行
            current_line += stripped
        else:
            # 多字符行：结束当前合并，添加这一行
            if current_line:
                merged_lines.append(current_line)
                current_line = ""
            merged_lines.append(stripped)
    
    # 处理最后一行
    if current_line:
        merged_lines.append(current_line)
    
    return '\n'.join(merged_lines)


def _is_moan_only(text: str) -> bool:
    """判断文本是否为纯娇喘（不承载台词）
    
    纯娇喘特征：
    1. 只包含假名、标点、♡等符号
    2. 不包含有意义的词汇（动词、名词等）
    3. 主要是拟声词（ん、あ、は、ひ等）
    
    注意：此函数用于判断是否为纯娇喘，以便在清洗时保留娇喘内容。
    娇喘+断句台词的情况也应该被保留。
    """
    text = text.strip()
    if not text:
        return False
    
    # 移除所有空白和♡符号后检查
    cleaned = re.sub(r'[\s♡♥❤♦️]', '', text)
    
    # 如果只剩下标点，不算娇喘
    if not cleaned or re.match(r'^[…！？。、・〜～\.\?!\-]+$', cleaned):
        return False
    
    # 检查是否只包含假名和标点（没有汉字）
    if re.search(r'[\u4e00-\u9fff]', cleaned):
        return False
    
    # 检查是否主要是拟声假名（ん、あ、は、ひ、ふ、へ、ほ等）
    # 允许的娇喘假名 - 扩展列表，包含更多娇喘相关的假名
    # 包含小写假名（ぁぃぅぇぉ）用于处理拉长音如 はぁ
    moan_kana = set('んあいうえおはひふへほまみむめもやゆよらりるれろわをっゃゅょゎヮぁぃぅぇぉ')
    kana_chars = re.findall(r'[\u3040-\u309f\u30a0-\u30fa]', cleaned.lower())
    
    if not kana_chars:
        return False
    
    # 如果超过70%是拟声假名，认为是纯娇喘（降低阈值以识别更多娇喘）
    moan_count = sum(1 for c in kana_chars if c in moan_kana)
    return moan_count / len(kana_chars) > 0.7


def _simplify_repeated_moans(text: str) -> str:
    """简化重复的无意义喘息
    
    如: ん、ん、ん、ん → ん、ん…♡
    保留1-2个，不全删
    
    规则：
    1. 匹配重复的单个假名（如 ん、ん、ん、ん）
    2. 匹配重复的假名+っ组合（如 あっ、あっ、あっ、あっ）
    3. 精简为2个，加上省略号和♡（如果原文本已有♡则不重复添加）
    """
    # 匹配重复的假名+っ组合（如 あっ、あっ、あっ、あっ）
    # 后面可能有…♡或其他结尾
    pattern1 = r'([\u3040-\u309f\u30a0-\u30fa]っ)[、，]\1(?:[、，]\1)+(?:…♡|…|♡)?'
    
    def simplify1(match):
        chars = match.group(1)
        return f'{chars}、{chars}…♡'
    
    result = re.sub(pattern1, simplify1, text)
    
    # 匹配重复的单个假名（如 ん、ん、ん、ん）
    # 后面可能有…♡或其他结尾
    pattern2 = r'([\u3040-\u309f\u30a0-\u30fa])[、，]\1(?:[、，]\1)+(?:…♡|…|♡)?'
    
    def simplify2(match):
        char = match.group(1)
        return f'{char}、{char}…♡'
    
    result = re.sub(pattern2, simplify2, result)
    
    return result


def clean_script_for_translation(text: str, skip_intro: bool = True) -> str:
    """清洗台本内容，只保留角色对话
    
    改进的清洗规则：
    
    去除内容：
    1. 页码行、纯数字行
    2. SE标记行、纯位置标记行
    3. 纯演技指示行（整行都是括号内容）
    4. 音效标记（｟...｠）、书名号（不含台词时）
    5. 【新增】开头的人物设定部分（在第一个音轨标记之前）
    6. 【新增】以 // 开头的指示文本（位置/麦克风指示）
    7. 【新增】人物设定格式（如 髪の色：銀、瞳の色；黒か青）
    8. 【新增】故事背景/世界观介绍（■タイトル、■おはなし等标记后的内容）
    
    保留内容：
    1. 角色对话（台词）
    2. 娇喘（保留原样，不过度清洗）
    3. 娇喘 + 断句台词（原样保留，不合并）
    4. 重复的无意义喘息（精简为1-2个，不全删）
    
    参数:
        text: 要清洗的文本
        skip_intro: 是否跳过音轨标记前的开头设定部分（默认True）。
                   当按音轨分别清洗时应设为False，避免跳过音轨内容。
    
    返回清洗后的文本
    """
    lines = text.split('\n')
    cleaned_lines = []
    
    # === 第一阶段：检测音轨标记的位置 ===
    # 找到第一个音轨标记的行号
    # 扩展版：支持更多音轨标记格式
    track_patterns = [
        r'^Tr\.?\s*\d+[\.．：:\uFE30;\s]',  # Tr1.、Tr.2、Tr 1.、Tr1:、Tr3 （无点号）
        r'^TR\d+[\s\.．：:\uFE30;]',  # TR6、TR7 （全大写）
        r'^トラック\s*[０-９0-9]+[\s\.．：：\uFE30;]',  # トラック1、トラック4；、トラック０１
        r'^■\s*トラック\s*[０-９0-9]+',  # ■トラック０１
        r'^[Tt]rack\s*\d+[\s\.．：:\uFE30]',  # Track1、track01
        r'^[-]{3,}$',  # 分隔线（如 ------------------------）
    ]
    
    first_track_line = -1  # 第一个音轨标记的行号（0-based）
    for i, line in enumerate(lines):
        stripped = line.strip()
        normalized = _normalize_text(stripped)
        # 【新增】去除行首的页码数字（如 "5 トラック２" → "トラック２"）
        normalized_no_pagenum = re.sub(r'^\d+\s+', '', normalized)
        for pattern in track_patterns:
            if re.match(pattern, normalized_no_pagenum, re.IGNORECASE):
                first_track_line = i
                print(f"  [清洗] 检测到音轨标记在第 {i+1} 行: {normalized_no_pagenum[:50]}，跳过开头设定部分")
                break
        if first_track_line >= 0:
            break
    
    # 如果找到音轨标记，从该行之后开始处理
    # 如果没有找到音轨标记，从第0行开始处理（但会跳过设定内容）
    start_line = first_track_line + 1 if first_track_line >= 0 else 0
    
    # 【修复】如果音轨标记在文件末尾（最后10%的行），说明这是文件结束标记，不是开头设定标记
    # 这种情况下，应该从第0行开始处理，而不是跳过所有内容
    if first_track_line >= 0 and first_track_line >= len(lines) * 0.9:
        print(f"  [清洗] 音轨标记在文件末尾（第{first_track_line+1}行/共{len(lines)}行），视为结束标记，不从开头跳过")
        start_line = 0
        first_track_line = -1  # 重置，避免后续逻辑错误
    
    # 【修复】当按音轨分别清洗时（skip_intro=False），不从开头跳过
    # 因为音轨已经划分好了，前770行可能包含其他音轨的内容
    if not skip_intro:
        start_line = 0
        first_track_line = -1
        print(f"  [清洗] skip_intro=False，不从开头跳过（按音轨分别清洗模式）")
    
    # passed_intro 现在由 start_line 决定：行号 >= start_line 表示已过开头设定部分
    # 不再需要单独的 passed_intro 变量
    
    # 人物设定格式模式（需要排除）
    setting_patterns = [
        r'^[髪瞳身服年齢血型性趣味特技好嫌苦悩願夢職業住所電話番号郵便番号メイル住所][:：；;]',  # 人物属性
        r'^(服|髪の色|瞳の色|身長|年齢|血液型|性別|趣味|特技|好き|嫌い|苦手|悩み|願い|夢|職業)[:：；;]',  # 常见设定格式
        r'^[ぁ-んァ-ン一-龥]{1,10}[:：；;][ぁ-んァ-ン一-龥0-9０-９\s]+$',  # 通用设定格式（属性名：属性值）
    ]
    
    # 故事背景/世界观介绍模式（需要排除，除非有CV朗读的旁白）
    story_intro_patterns = [
        r'^■タイトル',  # ■タイトル
        r'^■おはなし',  # ■おはなし
        r'^■ストーリー',  # ■ストーリー
        r'^■プロローグ',  # ■プロローグ
        r'^■あらすじ',  # ■あらすじ
        r'^■イントロダクション',  # ■イントロダクション
        r'^■世界観',  # ■世界観
        r'^■キャラクター',  # ■キャラクター
        r'^■キャスト',  # ■キャスト
        r'^■登場人物',  # ■登場人物
        r'^■設定',  # ■設定
        r'^■シナリオ',  # ■シナリオ
        r'^■脚本',  # ■脚本
        r'^■作品内容',  # ■作品内容
        r'^■作品紹介',  # ■作品紹介
        r'^■あらすじ',  # ■あらすじ
        r'^■ストーリー',  # ■ストーリー
        r'^■プロローグ',  # ■プロローグ
        r'^■イントロダクション',  # ■イントロダクション
        r'^■世界観',  # ■世界観
        r'^■キャラクター',  # ■キャラクター
        r'^■キャスト',  # ■キャスト
        r'^■登場人物',  # ■登場人物
        r'^■設定',  # ■設定
        r'^■シナリオ',  # ■シナリオ
        r'^■脚本',  # ■脚本
        r'^■作品内容',  # ■作品内容
        r'^■作品紹介',  # ■作品紹介
    ]
    
    # 故事背景内容特征（长段落的背景介绍，不是角色对话）
    story_content_keywords = [
        '世界観', '設定', '背景', 'あらすじ', 'ストーリー', 'プロローグ',
        'イントロダクション', '作品紹介', '作品内容', 'キャラクター紹介',
        '登場人物', 'キャスト紹介', 'あらすじ', 'ストーリー', 'プロローグ',
        'イントロダクション', '作品紹介', '作品内容', 'キャラクター紹介',
        '登場人物', 'キャスト紹介',
    ]
    
    # === 第二阶段：从 start_line 开始处理 ===
    for line_idx, line in enumerate(lines):
        # 跳过开头设定部分（行号 < start_line 的内容）
        if line_idx < start_line:
            continue
        
        stripped = line.strip()
        
        # 跳过空行
        if not stripped:
            continue
        
        # 规范化文本（去除字符间空格）
        normalized = _normalize_text(stripped)
        
        # 判断是否已过开头设定部分（用于后续逻辑）
        passed_intro = line_idx >= start_line
        
        # === 跳过开头设定部分 ===
        # 注意：由于我们已经从 start_line 开始处理，这里的逻辑主要用于处理没有音轨标记的情况
        if not passed_intro:
            # 跳过所有以 ■ 开头的标记
            if normalized.startswith('■'):
                continue
            
            # 检查是否为人物设定格式
            is_setting = False
            for pattern in setting_patterns:
                if re.match(pattern, normalized):
                    is_setting = True
                    break
            if is_setting:
                continue
            
            # 检查是否包含明显的设定关键词
            setting_keywords = ['髪の色', '瞳の色', '身長', '年齢', '血液型', '趣味', '特技', 
                               '服：', '服:', '義理の妹', '義理の父', '世界観', 'キャラクター',
                               'おはなし', 'タイトル', 'ストーリー', 'プロローグ', 'あらすじ']
            for kw in setting_keywords:
                if kw in normalized:
                    is_setting = True
                    break
            if is_setting:
                continue
            
            # 【关键修改】在开头设定部分，严格跳过所有非对话内容
            # 开头设定部分的特征：
            # 1. 以 ■ 开头的标记（标题、世界观、角色等）
            # 2. 标记后的描述性内容（故事背景、角色设定等）
            # 3. 不包含明显的对话特征
            
            # 检查是否为对话（以对话标点结尾）
            is_dialogue_end = bool(re.search(r'[…！？～。\.\?!]$', normalized))
            
            # 检查是否包含口语表达（如 です、ます、だよ 等）
            has_colloquial = bool(re.search(r'(です|ます|だよ|だね|だわ|だろ|かよ|かな|って|けど|から|のに|なら|なぁ|ねぇ|よぉ|わぁ)', normalized))
            
            # 检查是否包含娇喘（可能是纯娇喘或娇喘+断句台词）
            has_moan = bool(re.search(r'(んっ|あっ|うっ|はっ|ひっ|ふっ|んん|ああ|うう)', normalized))
            
            # 检查是否包含角色名标记（如 【角色名】）
            has_character_mark = bool(re.search(r'【[^】]+】', normalized))
            
            # 检查是否为描述性内容（故事背景、角色设定等）
            # 描述性内容的特征：
            # 1. 不以对话标点结尾
            # 2. 不包含口语表达
            # 3. 不包含娇喘
            # 4. 不包含角色名标记
            # 5. 可能以动词结尾（如 してしまった、なってしまった、いる、なる 等）
            is_descriptive = False
            if not is_dialogue_end and not has_colloquial and not has_moan and not has_character_mark:
                # 检查是否以动词/形容词结尾（描述性句子）
                verb_endings = ['してしまった', 'なってしまった', 'ている', 'なる', 'いる', 'される', 'できる', 'される', 'なれる', 'おられる', 'いらっしゃる', 'まいる', '参る', '申し上げる', 'いただく', 'くださる', 'なさる', 'おっしゃる', 'いらっしゃる', 'おる', 'おります', 'います', 'あります', 'できます', 'なります', 'します', 'されます', 'できる', 'される', '思う', '考える', '感じる', '思える', '考えられる', '感じられる']
                for ending in verb_endings:
                    if normalized.endswith(ending):
                        is_descriptive = True
                        break
                
                # 检查是否为描述性的短语（不以名词结尾的长句）
                # 如果句子长度超过10个字符且不以对话标点结尾，很可能是描述性内容
                if not is_descriptive and len(normalized) > 10:
                    # 检查是否以常见的描述性结尾
                    desc_endings = ['ていく', 'てくる', 'てしまう', 'たことになる', 'てしまう', 'れている', 'られている', 'せられる', 'させられる', 'ことになる', 'ようになる', 'ようにする', 'ことにする', 'ものがある', 'ことがある', 'ところがある', 'わけがある', 'はずがある', 'かもしれない', 'にちがいない', 'に違いない', 'ようだ', 'みたいだ', 'そうだ', 'らしい']
                    for ending in desc_endings:
                        if normalized.endswith(ending):
                            is_descriptive = True
                            break
                
                # 如果仍然不确定，检查是否包含描述性关键词
                if not is_descriptive:
                    desc_keywords = ['事故', '療養', '生活', '島', '人口', '雰囲気', '性格', '外見', '特徴', '設定', '世界観', 'キャラクター', 'プロローグ', 'エピローグ', 'あらすじ', 'ストーリー', 'タイトル', '作品', '紹介', '登場人物', '関係', '家族', '両親', '兄弟', '姉妹', '友人', '恋人', '夫婦', '親子', '身長', '体重', '年齢', '血液型', '誕生日', '趣味', '特技', '好き', '嫌い', '苦手', '得意', '不得意', '長所', '短所', '夢', '目標', '願望', '悩み', '過去', '現在', '未来', '出自', '出身', '職業', '仕事', '学校', '家', '住所', '電話', 'メール']
                    for kw in desc_keywords:
                        if kw in normalized:
                            is_descriptive = True
                            break
            
            # 如果是描述性内容，跳过
            if is_descriptive:
                continue
            
            # 如果不是对话结尾，也不包含口语表达，也不是娇喘，也没有角色名标记，则跳过
            if not is_dialogue_end and not has_colloquial and not has_moan and not has_character_mark:
                # 这是故事背景介绍，跳过
                continue
            
            # 如果开头部分包含明显的非对话特征，也跳过
            # 例如：以「」或『』包裹的内容（通常是引用或标题）
            if re.match(r'^[「『].*[」』]$', normalized):
                continue
            
            # 如果行太短（少于5个字符），可能是残缺的设定内容
            if len(normalized) < 5:
                continue
        
        # === 新增：跳过以 // 开头的指示文本 ===
        if normalized.startswith('//'):
            # 这是指示文本（位置/麦克风指示），跳过
            continue
        
        # === 关键新增：调用_is_scene_description等函数过滤非台词内容 ===
        # 这些过滤在过了开头设定部分后也需要执行
        if _is_scene_description(normalized):
            continue
        if _is_direction(normalized):
            continue
        if _is_position_marker(normalized):
            continue
        if _is_se_marker(normalized):
            continue
        if _is_page_number(normalized):
            continue
        if _is_header_footer(normalized):
            continue
        
        # === 新增：跳过人物设定格式 ===
        is_setting = False
        for pattern in setting_patterns:
            if re.match(pattern, normalized):
                is_setting = True
                break
        if is_setting:
            continue
        
        # === 新增：跳过故事背景/世界观介绍标记（如 ■タイトル、■おはなし 等）===
        is_story_intro = False
        for pattern in story_intro_patterns:
            if re.match(pattern, normalized):
                is_story_intro = True
                break
        if is_story_intro:
            continue
        
        # === 新增：跳过以 ■ 开头的非对话内容（标题、设定等）===
        # 注意：只跳过以 ■ 开头的标题行，不跳过角色对话
        if normalized.startswith('■'):
            # 其他以 ■ 开头的内容（如章节标题），也跳过
            # 因为这些通常不是角色对话
            continue
        
        # 跳过纯页码行（如 "1 / 57"、"1 / 35"）
        if re.match(r'^\d+\s*/\s*\d+$', normalized):
            continue
        
        # 跳过纯数字行
        if re.match(r'^\d+$', normalized):
            continue
        
        # 跳过孤立数字行（如 "58", "28" 等单/双数字，可能是页码或时间码）
        if re.match(r'^\d{1,3}$', normalized):
            continue
        
        # 跳过纯斜杠或包含斜杠的短行（如 "/"、"/ "，可能是分隔符）
        if re.match(r'^[/／\s]+$', normalized):
            continue
        
        # 跳过纯SE标记行
        if re.match(r'^[SEＳＥ][:：]', normalized, re.IGNORECASE):
            continue
        
        # 跳过时间戳/完成标记行（フィニッシュタイム、射精 XX分XX秒、絶頂等）
        if re.search(r'フィニッシュタイム|フィニッシュ', normalized):
            continue
        if re.search(r'射精\s*\d+\s*分\s*\d+\s*秒', normalized):
            continue
        if re.search(r'絶頂\s*\d+\s*分\s*\d+\s*秒', normalized):
            continue
        
        # 跳过区段标记行（ここから、ここまで）
        # 注意：只跳过纯标记行，保留作为台词的"ここから/ここまで"
        if re.match(r'^[（(]?SE[^）)]*[）)]?\s*ここから$', normalized):
            continue
        if re.match(r'^[（(]?SE[^）)]*[）)]?\s*ここまで$', normalized):
            continue
        
        # 跳过纯位置标记行（如 "【正面・中】"、"【右耳・近距離】"、"【正面/遠】"）
        # 包含：左右中正上下远近密着耳面距離等位置关键词
        if re.match(r'^【[左右中正上下遠近密着耳面距離・→/]+】$', normalized):
            continue
        
        # 跳过带位置前缀的台词行（如 "(正面：ふぇ？"、"(右：密着)"）
        if re.match(r'^[\(（][左右中正面遠近密着]+[）)]', normalized):
            continue
        
        # 跳过纯演技指示行（整行都是括号内容）
        # 注意：只跳过整行都是括号的，保留行内括号
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
        
        # 去除行尾残留的数字（页码，如 "台词。58"）
        # 匹配：日文/标点 + 空格 + 1-3位数字（页码通常在10-99之间）
        match = re.match(r'^(.+?)[\s]*\d{1,3}$', normalized)
        if match:
            potential = match.group(1).strip()
            # 检查剩余部分是否以日文/标点结尾（是的话说明数字是页码）
            if potential and re.search(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff…！？～。\.\?!♡]$', potential):
                normalized = potential
        
        # 去除内联的｟...｠标记（音效/指示）- 但保留含台词的内容
        # 只有当｟...｠内容不含日文台词时才删除
        def remove_se_marker(text):
            """移除音效标记，但保留含台词的内容"""
            def replacer(match):
                content = match.group(0)
                inner = content[1:-1]  # 去掉｟和｠
                # 如果内部包含日文台词（有汉字或完整假名词），保留
                if re.search(r'[\u4e00-\u9fff]', inner):
                    return content
                # 如果主要是拟声词（娇喘），保留
                if _is_moan_only(inner):
                    return inner  # 返回内容，去掉｟｠标记
                return ''
            return re.sub(r'｟[^｠]*｠', replacer, text)
        
        normalized = remove_se_marker(normalized)
        
        # 改进的括号处理：只删除SE音效标记，保留其他括号内容（包括娇喘）
        def remove_direction_keep_moans(text):
            """只删除SE音效标记括号，保留其他所有括号内容"""
            def replacer(match):
                content = match.group(0)
                inner = content[1:-1]  # 去掉括号
                
                # 如果是SE音效标记（如 "SE 椅子を引きずってくる音"），删除
                if re.match(r'^SE\s+', inner, re.IGNORECASE):
                    return ''
                
                # 其他所有括号内容都保留原样
                return content
            
            return re.sub(r'[（(][^）)]*[）)]', replacer, text)
        
        normalized = remove_direction_keep_moans(normalized)
        
        # 去除内联的《...》标记（书名号）- 但保留含台词的内容
        def remove_book_title(text):
            """移除书名号，但保留含台词的内容"""
            def replacer(match):
                content = match.group(0)
                inner = content[1:-1]  # 去掉《和》
                # 如果内部包含日文台词，保留内容（去掉书名号）
                if re.search(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', inner):
                    return inner
                return ''
            return re.sub(r'《[^》]*》', replacer, text)
        
        normalized = remove_book_title(normalized)
        
        # 去除内联的<...>标记 - 但保留含台词的内容
        def remove_angle_bracket(text):
            def replacer(match):
                content = match.group(0)
                inner = content[1:-1]
                if re.search(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', inner):
                    return inner
                return ''
            return re.sub(r'<[^>]*>', replacer, text)
        
        normalized = remove_angle_bracket(normalized)
        
        # 去除内联的{...}标记 - 但保留含台词的内容
        def remove_brace(text):
            def replacer(match):
                content = match.group(0)
                inner = content[1:-1]
                if re.search(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', inner):
                    return inner
                return ''
            return re.sub(r'\{[^}]*\}', replacer, text)
        
        normalized = remove_brace(normalized)
        
        # ========== 【新增】通用结构标记过滤（泛用性优先，不硬编码日文内容） ==========
        
        # 1. 去除纯结构标记行（OP/ED/标题/预告/结束标记等）
        # 特征：短行（<20字符），包含特定格式或纯片假名/英文标记
        if re.match(r'^[A-Z]{2,8}$', normalized):  # OP, ED, SE 等纯大写标记
            continue
        if re.match(r'^[Ａ-Ｚ]{2,8}$', normalized):  # 全角大写标记
            continue
        if re.match(r'^(?:OP|ED|SE|BGM|FO|ID|CV)[^\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]*$', normalized, re.IGNORECASE):
            continue
        
        # 2. 去除场景/画面标记行（场���：xxx, 画面：xxx）
        # 特征：以"場面"或"画面"开头，后跟冒号或全角冒号
        if re.match(r'^(?:場面|画面|場所|背景|設定|シーン)[:：]', normalized):
            continue
        
        # 3. 去除台本格式标记行
        # 特征：以"IDト書き"、"ト書き"、"立ち位置"、"セリフ"等格式词开头
        if re.match(r'^(?:ID|ト書き|立ち位置|セリフ|書き|台本|脚本)[:：]?', normalized):
            continue
        
        # 4. 去除纯标题/预告/结束标记行
        # 特征：包含"タイトルコール"、"アイキャッチ"、"次回予告"、"ここまで"等通用标记
        # 使用更泛化的模式：短行 + 特定结尾词
        if re.match(r'^.+(?:タイトル|コール|アイキャッチ|予告|ここまで|おしまい|終|END)$', normalized) and len(normalized) < 30:
            continue
        
        # 5. 去除纯动作指示行（无对话标点，以动词结尾，不含口语表达）
        # 特征：描述角色动作但不包含台词（如"主人公、アリシアの性器を舐める"）
        # 判断标准：包含动作动词 + 助词组合，但不以对话标点结尾
        if re.search(r'(?:を|に|で|へ|から|まで|と|が)[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]{1,8}(?:する|させる|られる|れる|た|て|たら|ながら|ながら|ながら|ながら|ながら)$', normalized):
            # 检查是否包含对话标点或口语表达
            has_dialogue_mark = bool(re.search(r'[…！？～。\.\?!♡]', normalized))
            has_colloquial = bool(re.search(r'(です|ます|だよ|だね|だわ|だろ|かよ|かな|って|けど|から|のに|なら|なぁ|ねぇ|よぉ|わぁ)', normalized))
            if not has_dialogue_mark and not has_colloquial:
                continue
        
        # 6. 【关键】去除位置前缀（如"正面アリシア"、"右側エルミナ"）
        # 特征：行首为方位词 + 角色名，角色名后紧跟台词
        # 使用泛化正则：匹配常见方位词 + 角色名（1-10个日文字符）
        position_prefix_match = re.match(r'^(正面|右側|左側|後方|上方|下方|近く|耳元|右耳|左耳|後ろ|横|隣|周囲|中央|奥|手前|右|左|上|下|前|後|横|斜|向|背|脇|隅|端|側|面|方|元|根|底|表|裏|内|外|間|中|東|西|南|北)(?:から|へ|に|で|を|と|の|が)?(?:移動しながら|移動して|移動|回りながら|回って|歩きながら|歩いて|走りながら|走って|座りながら|座って|立ちながら|立って|寝ながら|寝て|倒れながら|倒れて|起きながら|起きて|這いながら|這って|這いつくばって|這いつくばりながら|這いつくばって|這いつくばりながら|這いつくばって|這いつくばりながら)?(.+)$', normalized)
        if position_prefix_match:
            normalized = position_prefix_match.group(2).strip()
        
        # 7. 去除残留的方向指示（如"↓"、"→"、"↑"等箭头符号开头的行）
        if re.match(r'^[↓→↑←⇒⇐⇑⇓⇔⇕]', normalized):
            # 如果箭头后面没有日文字符，直接跳过
            if not re.search(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', normalized):
                continue
            # 如果箭头后面有日文字符，去掉箭头
            normalized = re.sub(r'^[↓→↑←⇒⇐⇑⇓⇔⇕]+\s*', '', normalized)
        
        # 8. 去除纯数字+符号的短行（如"11↓"、"34"、"5↓"）
        if re.match(r'^\d+[↓→↑←⇒⇐⇑⇓⇔⇕]?$', normalized):
            continue
        
        # 9. 去除"SE："或"SE:"开头的音效描述行（即使包含日文内容）
        if re.match(r'^[SEＳＥ][:：]', normalized, re.IGNORECASE):
            continue
        
        # 10. 去除纯英文/数字混合的短标记行
        if re.match(r'^[A-Za-z0-9\s]+$', normalized) and len(normalized) < 20:
            continue
        
        # ========== 【新增结束】 ==========
        
        # 简化重复的娇喘（如 ん、ん、ん、ん → ん、ん…♡）
        normalized = _simplify_repeated_moans(normalized)
        
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


def _is_garbled_text(text: str) -> bool:
    """检测文本是否为乱码/乱序输出

    检测指标：
    1. 包含大量替换字符 (U+FFFD)
    2. 包含大量CJK兼容字符（康熙部首等）
    3. 日文字符比例极低

    返回: True 如果文本被认为是乱码
    """
    if not text or len(text.strip()) == 0:
        return False

    # 统计替换字符
    replacement_chars = text.count('\ufffd')
    total_chars = len(text.replace('\n', '').replace(' ', ''))

    if total_chars == 0:
        return False

    # 如果替换字符占比超过5%，认为是乱码
    if replacement_chars / total_chars > 0.05:
        return True

    # 统计CJK兼容字符（康熙部首等，范围U+2F00-U+2FDF）
    kangxi_count = len(re.findall(r'[\u2f00-\u2fdf]', text))
    # 如果康熙部首占比超过10%，认为是乱码
    if kangxi_count / total_chars > 0.1:
        return True

    # 统计日文字符（平假名、片假名、汉字）
    japanese_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', text))
    # 如果日文字符比例极低（<5%）且文本较长，可能是乱码
    if total_chars > 100 and japanese_chars / total_chars < 0.05:
        return True

    return False


def _extract_with_pymupdf_xhtml(page) -> str:
    """使用PyMuPDF的XHTML模式提取PDF文本（备用方案）

    当标准dict模式产生乱码时，使用XHTML模式作为备用方案。
    注意：某些PDF使用了编码混淆，XHTML模式也无法正确提取。
    这种情况下应直接返回空字符串，让上层降级为无台本模式。

    返回: 提取的文本，如果无法正确提取则返回空字符串
    """
    import html
    import unicodedata

    # 获取XHTML内容
    xhtml = page.get_text('xhtml')
    if not xhtml:
        return ""

    # 解码HTML实体
    decoded = html.unescape(xhtml)

    # 移除HTML标签，保留文本内容
    text = re.sub(r'<[^>]+>', '', decoded)

    # 移除多余的空白
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)

    # NFKC规范化：将CJK兼容字符转换为标准形式
    text = unicodedata.normalize('NFKC', text)

    # 检查提取后的文本是否仍然是乱码
    # 如果XHTML模式也无法正确解码，说明PDF使用了编码混淆
    if _is_garbled_text(text):
        # 编码混淆，直接返回空，让上层降级为无台本模式
        return ""

    return text.strip()


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
        import unicodedata

        text_blocks = []
        total_pages = 0
        garbled_pages = 0
        use_xhtml_fallback = False

        with fitz.open(file_path) as doc:
            total_pages = len(doc)
            for page_num, page in enumerate(doc, 1):
                # 首先尝试标准dict模式
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

                # 检查是否产生乱码
                raw_text_sample = ''.join([s[2] for s in all_spans[:50]])
                if _is_garbled_text(raw_text_sample):
                    garbled_pages += 1
                    print(f"  第{page_num}页检测到乱码，跳过该页（后续使用OCR）...")
                    # 不再尝试XHTML，直接跳过该页
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

        # 【新增】检查乱码页面比例，过高则返回空字符串让上层降级到OCR
        if total_pages > 0 and garbled_pages / total_pages > 0.5:
            print(f"  [PyMuPDF] 乱码页面比例过高 ({garbled_pages}/{total_pages}={garbled_pages/total_pages:.0%})，放弃PyMuPDF结果，使用OCR")
            return ""

        result = '\n\n---PAGE_BREAK---\n\n'.join(text_blocks)

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


def extract_pdf_text(file_path: Path, clean_for_translation: bool = False, force_ocr: bool = False) -> tuple[str, str]:
    """提取PDF文件的文本内容
    
    优先使用PyMuPDF（更好的兼容性），
    如果失败则使用OCR（适用于编码混淆保护的PDF）。
    
    参数:
        file_path: PDF文件路径
        clean_for_translation: 是否清洗为翻译用的对话内容
        force_ocr: 是否强制使用OCR（跳过PyMuPDF）
    
    返回:
        (提取的文本, 使用的提取方法)
        提取方法: "PyMuPDF" | "OCR(PaddleOCR)" | ""
    """
    print(f"  [PDF提取] 开始处理: {file_path.name}")
    
    # 如果强制OCR，跳过其他方法
    if force_ocr:
        print("  [PDF提取] 强制使用OCR模式...")
        ocr_text = extract_with_ocr(file_path, clean_for_translation=clean_for_translation)
        if ocr_text:
            print(f"  [PDF提取] OCR成功: {len(ocr_text)} 字符")
            export_path = _export_cleaned_text(file_path, ocr_text, "OCR(PaddleOCR)")
            print(f"  [PDF提取] 清洗后台本: {export_path}")
            return ocr_text, "OCR(PaddleOCR)"
        return "", ""
    
    # 优先尝试PyMuPDF（先不清洗，检查原始质量）
    raw_text = _extract_with_pymupdf(file_path, clean_for_translation=False)
    if raw_text and len(raw_text) > 100:
        # 检查原始提取质量
        japanese_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30fa]', raw_text))
        total_chars = len(raw_text.replace('\n', '').replace(' ', ''))
        if total_chars > 0 and japanese_chars / total_chars > 0.05:
            # 原始提取质量良好
            # 检查是否需要合并碎片化的行（每行只有一个字符的情况）
            lines = raw_text.split('\n')
            single_char_lines = sum(1 for line in lines if len(line.strip()) == 1)
            if single_char_lines > len(lines) * 0.5:
                # 超过50%是单字符行，需要合并
                print(f"  检测到碎片化文本（{single_char_lines}/{len(lines)}行是单字符），正在合并...")
                raw_text = _merge_fragmented_lines(raw_text)

            # 现在清洗（如果需要）
            if clean_for_translation:
                cleaned_text = clean_script_for_translation(raw_text)
                # 导出清洗后的内容
                export_path = _export_cleaned_text(file_path, cleaned_text, "PyMuPDF")
                print(f"  [PDF提取] PyMuPDF提取成功，清洗后台本已导出: {export_path}")
                print(f"  [PyMuPDF] 提取PDF文本成功: {len(cleaned_text)} 字符（清洗后）")
                return cleaned_text, "PyMuPDF"
            else:
                print(f"  [PyMuPDF] 提取PDF文本成功: {len(raw_text)} 字符")
                return raw_text, "PyMuPDF"

    # PyMuPDF失败，直接尝试OCR
    print(f"  PyMuPDF提取效果不佳，尝试OCR...")
    ocr_text = extract_with_ocr(file_path, clean_for_translation=clean_for_translation)
    if ocr_text:
        print(f"  [OCR(PaddleOCR)] 提取PDF文本成功: {len(ocr_text)} 字符")
        # 导出内容（无论是否清洗都导出）
        export_path = _export_cleaned_text(file_path, ocr_text, "OCR(PaddleOCR)")
        print(f"  [PDF提取] OCR提取成功，已导出: {export_path}")
        return ocr_text, "OCR(PaddleOCR)"

    return "", ""


def _export_cleaned_text(file_path: Path, cleaned_text: str, method: str) -> Path:
    """导出清洗后的台本内容到文件
    
    参数:
        file_path: 原始PDF文件路径
        cleaned_text: 清洗后的文本内容
        method: 提取方法名称
    
    返回:
        导出的文件路径
    """
    # 生成输出路径: 原文件名_cleaned.txt
    output_path = file_path.with_suffix('')
    output_path = Path(str(output_path) + '_cleaned.txt')
    
    # 写入文件
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(cleaned_text)
        print(f"  [导出] 清洗后台本已保存: {output_path}")
        return output_path
    except Exception as e:
        print(f"  [导出] 保存清洗后台本失败: {e}")
        return file_path


# ==================== 台本文件识别 ====================

def is_scriptbook_file(file_path: Path) -> bool:
    """检测文件是否为台本文件
    
    判断依据：
    1. 文件扩展名为 .txt 或 .pdf
    2. 文件名包含台本关键词
    3. 文件名符合台本命名模式：
       - 模式A：简单描述型（シナリオ.txt、台本 データ.pdf）
       - 模式B：带章节/轨道编号（トラック1、track1、tr01、01-1、１、２）
       - 模式C：编号 + 描述性标题（トラック1：xxx.txt）
    4. 文件在"台本"文件夹中，且文件名是纯数字
    5. 文件内容包含典型的台本标记（角色名【】、SE、方向指示#等）
    
    排除模式D：特殊用途文件
    - Finishtime.txt / フィニッシュタイム.txt：标注高潮时间点
    - クレジット.txt：演职员表
    - お射精メモ！：射精备忘录
    - read me.txt：说明文件
    """
    if file_path.suffix.lower() not in SCRIPTBOOK_EXTS:
        return False
    
    filename = file_path.name  # 原始文件名（保留大小写）
    filename_lower = filename.lower()
    stem = file_path.stem  # 不含扩展名的文件名
    
    # 【新增】排除清洗后的台本文件（_cleaned.txt）
    # 这些是程序生成的清洗后输出文件，不应该被当作台本输入
    if '_cleaned' in stem:
        return False
    
    # 【新增】排除处理后的台本文件（_processed_scriptbook.txt）
    # 这些是程序生成的处理后输出文件，不应该被当作台本输入
    if '_processed' in stem:
        return False
    
    # 【新增】排除导出的台本文件（_export.txt）
    # 这些是程序生成的导出文件，不应该被当作台本输入
    if '_export' in stem:
        return False
    
    # 【新增】排除程序导出的台本汇总文件（_scriptbook_export.txt）
    # 这是程序导出的台本内容汇总，不是原始台本文件
    if '_scriptbook_export' in stem:
        return False
    
    # 模式D：排除特殊用途文件（优先级最高）
    for keyword in NON_SCRIPTBOOK_KEYWORDS:
        # 【修复】使用更精确的匹配：要求关键词前后是单词边界或文件扩展名
        # 避免 '射精メモ' 误匹配 '射精箇所'
        keyword_lower = keyword.lower()
        if keyword_lower in filename_lower:
            # 额外检查：确保匹配位置前后不是日文假名/汉字（避免部分匹配）
            idx = filename_lower.find(keyword_lower)
            if idx >= 0:
                # 检查关键词前面是否有日文假名或汉字（避免部分匹配）
                before = filename_lower[idx-1] if idx > 0 else ''
                after = filename_lower[idx+len(keyword_lower):] if idx + len(keyword_lower) < len(filename_lower) else ''
                
                # 如果关键词前面是日文假名/汉字，且关键词本身不是以这些字符开头，
                # 说明这是部分匹配，不应排除
                if before and re.match(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', before):
                    # 检查关键词是否以该字符结尾（不是的话就是部分匹配）
                    if not keyword_lower.startswith(before):
                        continue  # 跳过，这不是真正的匹配
                
                # 如果关键词后面是日文假名/汉字，且关键词本身不是以这些字符结尾，
                # 说明这是部分匹配，不应排除
                if after and re.match(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', after):
                    if not keyword_lower.endswith(after[:1]):
                        continue  # 跳过，这不是真正的匹配
                
                return False
    
    # 检查文件名是否包含台本关键词
    for keyword in SCRIPTBOOK_KEYWORDS:
        if keyword.lower() in filename_lower:
            return True
    
    stem = file_path.stem  # 不含扩展名的文件名
    
    # 模式B：检查文件名是否符合台本命名模式
    # 1. トラック + 数字（如 トラック1、トラック２）
    if re.match(r'^トラック[０-９0-9]+', stem, re.IGNORECASE):
        return True
    
    # 2. track + 数字（如 track1、Track01、TRACK1）
    if re.match(r'^track[０-９0-9]+', stem, re.IGNORECASE):
        return True
    
    # 3. tr + 数字（如 tr01、TR01）
    if re.match(r'^tr[０-９0-9]+', stem, re.IGNORECASE):
        return True
    
    # 4. 纯数字编号（如 01、02、1、2、１、２）
    # 但必须包含描述或位于台本文件夹中
    if re.match(r'^[０-９0-9]+$', stem):
        parent_name = file_path.parent.name.lower()
        if parent_name in ('台本', 'だいほん', 'script', 'scripts', 'scenario', 'scenarios'):
            return True
        # 如果文件名是纯数字但不在台本文件夹中，继续检查内容
    
    # 5. 数字-数字格式（如 01-1、1-2）
    if re.match(r'^[０-９0-9]+[-_][０-９0-9]+', stem):
        parent_name = file_path.parent.name.lower()
        if parent_name in ('台本', 'だいほん', 'script', 'scripts', 'scenario', 'scenarios'):
            return True
    
    # 检查是否在"台本"文件夹中，且文件名是纯数字（全角或半角）
    # 如: 台本/１.txt, 台本/２.txt, 台本/1.txt, 台本/2.txt
    parent_name = file_path.parent.name.lower()
    if parent_name in ('台本', 'だいほん', 'script', 'scripts', 'scenario', 'scenarios'):
        # 检查文件名是否只包含数字（全角或半角）
        if re.match(r'^[０-９0-9]+$', stem):
            return True
    
    # 检查文件内容是否包含台本特征
    try:
        # PDF文件需要使用extract_pdf_text提取
        if file_path.suffix.lower() == '.pdf':
            content = extract_pdf_text(file_path, clean_for_translation=False)
        else:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
    except Exception:
        return False
    
    if not content:
        return False
    
    # 检测台本特征标记
    has_character_marks = bool(re.search(r'^【[^】]+】', content, re.MULTILINE))
    has_se_marks = bool(re.search(r'^[SEＳＥ][:：\s]', content, re.MULTILINE))
    has_direction = bool(re.search(r'^#[^\n]+$', content, re.MULTILINE))
    has_track = bool(re.search(r'トラック\d+', content))
    has_track_mark = bool(re.search(r'^■トラック[０-９0-9]+', content, re.MULTILINE))  # ■トラック０１ 格式
    
    # 至少包含两种特征才认为是台本
    features = sum([has_character_marks, has_se_marks, has_direction, has_track, has_track_mark])
    return features >= 2


# ==================== 台本内容解析 ====================

# 正则模式定义
SE_PATTERNS = [
    r'^[SEＳＥ][:：]',           # SE: 或 SE：
    r'^[SEＳＥ]\s',              # SE 开头
    r'^\s*[SEＳＥ]\s*[:：]?\s*', # 可选空格
    r'^【効果音[:：]',     # 【効果音：xxx】
    r'^【効果音：',        # 【効果音：xxx】
    r'^（ＳＥ[:：]',       # （ＳＥ：xxx）
]

DIRECTION_PATTERN = r'^#[^\n]+$'
CHARACTER_PATTERN = r'^【[^】]+】\s*$'
TRACK_PATTERN = r'^【トラック\d+[：：][^\]]*】'
# 【修复】支持更多音轨标记格式
# 1. ■トラック０１（方块前缀）
# 2. ■凛花トラック1（方块+角色名前缀）
# 3. ◆トラック１：（菱形前缀+全角数字+冒号）
# 4. 《トラック１：（书名号前缀）
# 5. 【01.（【数字.格式）
TRACK_MARK_PATTERN = r'^[■◆]《?[^■◆《]*トラック[０-９0-9]+[^〆]*$'  # ■トラック０１、■凛花トラック1、◆トラック１：等格式（排除〆结束标记）
# 新增：【数字. 格式（如 【01.、【1.）
TRACK_BRACKET_NUM_PATTERN = r'^【\d+\.\s*'  # 【01.、【1.
# 新增：无括号格式（如 トラック１．xxx、トラック2.xxx）
TRACK_DOT_PATTERN = r'^トラック[０-９0-9]+[\.．]'  # トラック１．、トラック2.

# 新增：星号章节标记（如 ☆プロローグ、☆１、☆２、★１ 等）
STAR_TRACK_PATTERN = r'^[☆★]\s*[０-９0-9]+$'  # 必须有数字结尾
STAR_CHAPTER_PATTERN = r'^[☆★]\s*(プロローグ|エピローグ|おまけ|特典)'  # ☆プロローグ、☆エピローグ 等

# 新增：菱形章节标记（如 🔶　１章、🔷　２章）
CHAPTER_TRACK_PATTERN = r'^[🔶🔷]\s*[０-９0-9]+章'

# 新增：Tr./Track 格式
# 支持标准数字和带圆圈的数字（如 ①②③④⑤⑥⑦⑧⑨⑩）
CIRCLED_NUMBERS = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'
TR_DOT_PATTERN = r'^[■◆□●○]?(?:[Tr\.トラックTrack]+\s*)[０-９0-9' + CIRCLED_NUMBERS + r']+'  # ■Track1、Tr.1、Tr.2、トラック1、トラック① 等（必须有Track/トラック关键词）

# 新增：🔷标记 + 圆圈数字（如 🔷①、🔷②、🔷③）
SUB_TRACK_PATTERN = r'^[🔷🔶]\s*[' + CIRCLED_NUMBERS + r']+'  # 🔷①、🔷② 等子音轨标记

# 新增：广播剧/电视剧型章节标记（如 第一話ここまで、第二話ここまで、OP、OP明け）
DRAMA_EPISODE_PATTERN = r'^第[一二三四五六七八九十百千]+話ここまで'  # 第一話ここまで、第二話ここまで
DRAMA_OP_PATTERN = r'^OP$'  # OP（单独一行）
DRAMA_OP_END_PATTERN = r'^OP明け$'  # OP明け（单独一行）

# 新增：▼track数字 或 ▼数字 格式（如 ▼track１、▼５）
TRACK_TRIANGLE_PATTERN = r'^▼\s*(?:track|Track|TRACK)?\s*[０-９0-9]+'  # ▼track１、▼５、▼track 1 等

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
        
        # 【新增】去除行首的页码数字（如 "5 トラック２" → "トラック２"）
        # PDF提取的文本常在音轨标记前带有页码
        stripped_no_pagenum = re.sub(r'^\d+\s+', '', stripped)
        
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
        # 【修复】但包含"トラック"的《》标题应优先识别为音轨标记
        if re.match(CHAPTER_TITLE_PATTERN, stripped):
            # 检查是否包含音轨关键词
            if re.search(r'トラック[０-９0-9]', stripped):
                parsed.append({
                    "line_num": i,
                    "character": "",
                    "text": stripped,
                    "raw_line": raw_line,
                    "type": "track"
                })
            else:
                parsed.append({
                    "line_num": i,
                    "character": "",
                    "text": stripped,
                    "raw_line": raw_line,
                    "type": "chapter"
                })
            continue
        
        # 音轨标记（如 【トラック1：xxx】）
        # 使用 stripped_no_pagenum 以支持带页码前缀的音轨标记
        if re.match(TRACK_PATTERN, stripped_no_pagenum):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped_no_pagenum,
                "raw_line": raw_line,
                "type": "track"
            })
            continue
        
        # 音轨标记（如 ■トラック０１）
        if re.match(TRACK_MARK_PATTERN, stripped_no_pagenum):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped_no_pagenum,
                "raw_line": raw_line,
                "type": "track"
            })
            continue
        
        # 新增：无括号音轨标记（如 トラック１．xxx、トラック2.xxx）
        if re.match(TRACK_DOT_PATTERN, stripped_no_pagenum):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped_no_pagenum,
                "raw_line": raw_line,
                "type": "track"
            })
            continue
        
        # 新增：星号数字标记（如 ☆１、☆２、★１）
        if re.match(STAR_TRACK_PATTERN, stripped_no_pagenum):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped_no_pagenum,
                "raw_line": raw_line,
                "type": "track"
            })
            continue
        
        # 新增：星号章节标记（如 ☆プロローグ、☆エピローグ、☆おまけ）
        if re.match(STAR_CHAPTER_PATTERN, stripped_no_pagenum):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped_no_pagenum,
                "raw_line": raw_line,
                "type": "track"
            })
            continue
        
        # 新增：菱形章节标记（如 🔶　１章、🔷　２章）
        if re.match(CHAPTER_TRACK_PATTERN, stripped_no_pagenum):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped_no_pagenum,
                "raw_line": raw_line,
                "type": "track"
            })
            continue
        
        # 新增：Tr./Track 格式（如 Tr.1、Tr.2、トラック1）
        if re.match(TR_DOT_PATTERN, stripped_no_pagenum, re.IGNORECASE):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped_no_pagenum,
                "raw_line": raw_line,
                "type": "track"
            })
            continue
        
        # 新增：广播剧/电视剧型章节结束标记（如 第一話ここまで、第二話ここまで）
        if re.match(DRAMA_EPISODE_PATTERN, stripped):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "track"
            })
            continue
        
        # 新增：广播剧 OP 标记（OP 单独一行，作为音轨开始）
        # 注意：OP明け 不作为音轨标记，只是OP结束
        if re.match(DRAMA_OP_PATTERN, stripped):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "track"
            })
            continue
        
        # 新增：▼track数字 或 ▼数字 格式（如 ▼track１、▼５）
        if re.match(TRACK_TRIANGLE_PATTERN, stripped):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "track"
            })
            continue
        
        # 新增：🔷标记 + 圆圈数字（如 🔷①、🔷②）子音轨标记
        if re.match(SUB_TRACK_PATTERN, stripped):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped,
                "raw_line": raw_line,
                "type": "track"
            })
            continue
        
        # 新增：【数字. 格式（如 【01.、【1.）
        if re.match(TRACK_BRACKET_NUM_PATTERN, stripped_no_pagenum):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": stripped_no_pagenum,
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
        # 【修复】支持纯数字开头且后面跟着标题的短行（如 １ライヴ中に...、２ハーレム奉仕で...）
        # 【修复】排除单个或少量数字（行号，如 4、8、10）
        # 【修复】真正的音轨标记数字应该是独立的（如 １、２、３），而不是日文词的一部分（如 ３人）
        if re.match(r'^[０-９0-9]+$', stripped):
            # 纯数字行：只有纯数字（全角或半角），没有其他内容
            # 排除短数字（1-3位），这些通常是行号而非音轨标记
            # 音轨编号通常较长（如全角数字 １、２，或多位数如 01、02）
            # 但行号通常也是短数字，所以需要更严格的判断
            # 策略：全角数字+多位数（如 １２、０１）才认为是音轨标记
            # 半角短数字（1-3位）通常是行号
            if re.match(r'^[０-９]+$', stripped) and len(stripped) >= 1:
                # 全角数字，认为是音轨标记（如 １、２、３）
                parsed.append({
                    "line_num": i,
                    "character": "",
                    "text": stripped,
                    "raw_line": raw_line,
                    "type": "track"
                })
                continue
            elif re.match(r'^[0-9]+$', stripped) and len(stripped) >= 2:
                # 半角数字，多位数才认为是音轨标记（如 01、02、10、12）
                # 排除个位数（1-9），这些通常是行号
                parsed.append({
                    "line_num": i,
                    "character": "",
                    "text": stripped,
                    "raw_line": raw_line,
                    "type": "track"
                })
                continue
        
        # 【新增】全角/半角数字开头+标题的短行（如 １タイトル、2.タイトル）
        # 【修复】排除包含剧本标记的行（如行号+SE:、行号+【角色】等）
        # 【修复】真正的音轨标记应包含トラック/Track等关键词，纯数字+内容的是行号
        if re.match(r'^[０-９0-9]+[^０-９0-9]', stripped) and len(stripped) < 40:
            # 去掉开头的数字
            rest = re.sub(r'^[０-９0-9]+\s*', '', stripped)
            # 真正的音轨标记必须包含音轨关键词
            track_keywords = ['トラック', 'Track', 'Tr.', 'トラッ', 'track', 'tr.', 'TR']
            has_track_keyword = any(kw in stripped for kw in track_keywords)
            if has_track_keyword:
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
        if re.match(r'^[SEＳＥ][:：]', stripped, re.IGNORECASE):
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
        cleaned = re.sub(r'[SEＳＥ][:：][^\n]*', '', cleaned, flags=re.IGNORECASE)
        
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


def _preprocess_track_text(text: str) -> str:
    """音轨标记文本预处理
    
    1. NFKC规范化（全角数字→半角，圆圈数字→普通数字）
    2. 去除行首页码数字（如 "5 トラック２" → "トラック２"）
    3. 合并多余空格
    
    注意：保留装饰符号（■★☆●◆）作为特征
    """
    import unicodedata
    
    # NFKC规范化（会转换全角数字和圆圈数字）
    text = unicodedata.normalize('NFKC', text)
    
    # 去除行首页码
    text = re.sub(r'^\d+\s+', '', text)
    
    # 合并多余空格
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text


def _extract_track_number_core(text: str) -> int:
    """从预处理后的文本中提取音轨编号（核心逻辑）
    
    采用分层匹配策略：
    1. 关键词格式（トラック/Track/Tr/第）
    2. 符号格式（■/◆/☆/★/【/🔷）
    3. 纯数字格式（极简行，需额外上下文验证）
    
    返回: 音轨编号 (1-999)，如果无法提取则返回 -1
    """
    if not text:
        return -1
    
    # ========== 第一层：排除明确非音轨标记 ==========
    
    # 排除目录统计行（如 ▼track１【全員】導入①分・１７０６字）
    if re.search(r'分[・·]\s*\d+\s*字', text):
        return -1
    
    # 排除结束标记行（如 ◆トラック１〆）
    if '〆' in text:
        return -1
    
    # 排除广播剧章节结束标记（如 第一話ここまで）
    if re.search(r'第[一二三四五六七八九十百千]+話ここまで', text):
        return -1
    
    # 排除明显的非音轨关键词
    excluded_keywords = ['終', 'おわり', 'end', 'ex', 'bonus', '特典']
    lower_text = text.lower()
    for kw in excluded_keywords:
        if kw in lower_text:
            return -1
    
    # ========== 第二层：关键词格式（最优先） ==========
    
    # 匹配带关键词的音轨标记
    # 覆盖: トラック01, Track 1, Tr.01, Tr1, 第1章, 第2話
    keyword_match = re.search(
        r'(?:トラック|Track|Tr)[:.．]?\s*(\d+)', 
        text, 
        re.IGNORECASE
    )
    if not keyword_match:
        keyword_match = re.search(
            r'第\s*(\d+)', 
            text
        )
    if keyword_match:
        track_num = int(keyword_match.group(1))
        if 1 <= track_num <= 999:
            return track_num
    
    # ========== 第三层：符号格式（次优先） ==========
    
    # ■トラック01, ◆トラック1, ■凛花トラック1
    symbol_keyword_match = re.search(
        r'^[■◆□●○]?\s*[^■◆□●○]*(?:トラック|Track|Tr)\s*(\d+)',
        text,
        re.IGNORECASE
    )
    if symbol_keyword_match:
        track_num = int(symbol_keyword_match.group(1))
        if 1 <= track_num <= 999:
            return track_num
    
    # 【数字. 格式（如 【01.【1.）
    bracket_num_match = re.match(r'^【(\d+)\.', text)
    if bracket_num_match:
        track_num = int(bracket_num_match.group(1))
        if 1 <= track_num <= 999:
            return track_num
    
    # ☆１, ★1, ☆プロローグ, ☆エピローグ
    # 注意：★后面跟数字才是音轨标记
    star_match = re.match(r'^[☆★]\s*(\d+)$', text)
    if star_match:
        track_num = int(star_match.group(1))
        if 1 <= track_num <= 999:
            return track_num
    
    # 🔶/🔷 格式（🔷①, 🔷1, 🔶　１章）
    # 注意：emoji + 数字 或 emoji + 数字 + 章
    emoji_match = re.match(r'^[🔶🔷]\s*(\d+)(?:\s*章)?$', text)
    if emoji_match:
        track_num = int(emoji_match.group(1))
        if 1 <= track_num <= 999:
            return track_num
    
    # ▼track数字 / ▼数字
    triangle_match = re.match(r'^▼\s*(?:track|Track|TRACK)?\s*(\d+)', text, re.IGNORECASE)
    if triangle_match:
        track_num = int(triangle_match.group(1))
        if 1 <= track_num <= 999:
            return track_num
    
    # ========== 第四层：纯数字格式（极简行，最后尝试） ==========
    
    # 只有纯数字（全角或半角）的短行
    # 注意：这种格式非常容易误匹配行号，需要非常严格的条件
    # 要求：行内容只有纯数字，没有其他内容
    pure_num_match = re.match(r'^(\d+)$', text)
    if pure_num_match:
        track_num = int(pure_num_match.group(1))
        # 纯数字格式：排除 0 和太大的数字
        if 1 <= track_num <= 99:
            return track_num
    
    # ========== 第五层：特殊标记 ==========
    
    # OP/OP明け（广播剧型，作为音轨1的开始标记）
    if text.strip() in ('OP', 'OP明け'):
        return 1
    
    return -1


def extract_track_number_from_text(track_text: str) -> int:
    """从音轨标记文本中提取音轨编号（改进版）

    支持：トラック０、トラック1、Track01、☆１、■トラック０１ 等
    新增支持：第一話ここまで、第二話ここまで、OP、OP明け 等广播剧型标记

    返回:
        音轨编号 (1-999)，如果无法提取或格式不对则返回 -1
    """
    if not track_text:
        return -1
    
    # 预处理
    processed = _preprocess_track_text(track_text)
    
    # 使用核心提取逻辑
    return _extract_track_number_core(processed)



def split_scriptbook_by_tracks(raw_content: str) -> dict[int, list[str]]:
    """将原始台本内容按音轨划分（返回原始内容，不清洗）

    参数:
        raw_content: 原始台本内容

    返回:
        {音轨编号: [该音轨的所有行（原始内容）]}
    """
    raw_lines = raw_content.split('\n')

    # 【修复】先规范化内容（去除字符间空格），以便正确识别音轨标记
    # PDF提取的文本常有字符间空格（如 "ト ラ ッ ク １"），
    # 会导致 parse_scriptbook_content 无法匹配音轨标记正则
    normalized_lines = [_normalize_text(line) for line in raw_lines]
    normalized_content = '\n'.join(normalized_lines)

    # 在规范化后的文本中解析音轨标记
    raw_parsed = parse_scriptbook_content(normalized_content)

    # 收集音轨标记的位置
    track_markers = []  # [(行索引, 音轨编号, 标记文本), ...]
    for p in raw_parsed:
        if p["type"] == "track":
            line_num = p["line_num"]  # 1-based
            track_num = extract_track_number_from_text(p.get("text", ""))
            if track_num < 0:
                # 如果返回 -1，说明是目录行（如 ▼track１【全員】導入①分・１７０６字）
                # 或者是无法识别的标记，跳过这些行
                continue
            track_markers.append((line_num - 1, track_num, p.get("text", "")))  # 转为 0-based 索引

    # 如果没有音轨标记，全部归入音轨0
    if not track_markers:
        return {0: raw_lines}

    # 【新增】检测是否为广播剧型台本（所有标记返回相同的track_num）
    # 广播剧型特征：所有标记都是OP，返回相同的编号（如都是1）
    track_nums = [tm[1] for tm in track_markers]
    has_duplicate_tracks = len(track_nums) != len(set(track_nums))
    
    if has_duplicate_tracks and len(track_markers) >= 3:
        # 广播剧型：使用顺序编号（第1个OP=音轨1，第2个OP=音轨2，...）
        # 但需要跳过连续的重复标记（如OP和OP明け连续出现）
        unique_markers = []
        prev_line_idx = -1
        for idx, (line_idx, track_num, text) in enumerate(track_markers):
            # 对于广播剧型，每个标记都应该是独立的音轨边界
            # 但如果两个标记的行号非常接近（如OP和OP明け），跳过第二个
            if prev_line_idx >= 0 and line_idx - prev_line_idx <= 2:
                continue
            unique_markers.append((line_idx, len(unique_markers) + 1, text))
            prev_line_idx = line_idx
        
        if len(unique_markers) >= 2:
            track_markers = unique_markers

    # 按音轨标记划分原始文本
    track_sections = {}

    # 处理音轨标记之前的部分（归入音轨0）
    first_marker_idx, first_track_num, _ = track_markers[0]
    if first_marker_idx > 0:
        track_sections[0] = raw_lines[:first_marker_idx]

    # 按音轨标记划分
    for i, (marker_idx, track_num, marker_text) in enumerate(track_markers):
        if i + 1 < len(track_markers):
            next_marker_idx = track_markers[i + 1][0]
            track_sections[track_num] = raw_lines[marker_idx:next_marker_idx]
        else:
            track_sections[track_num] = raw_lines[marker_idx:]

    return track_sections


# ==================== 旧版 LLM辅助台本识别与音轨划分（已弃用，保留供参考） ====================
"""
【说明】以下 LLM 相关的音轨划分函数已被新的正则方案替代：
- llm_identify_scriptbook_files
- llm_analyze_track_structure
- split_scriptbook_by_llm_markers

新方案：extract_track_number_from_text + split_scriptbook_by_tracks（纯正则，无LLM）
优势：
1. 不依赖LLM，速度更快
2. 不消耗API token
3. 结果确定性强，不受模型随机性影响
4. 已在201个台本上测试通过

如需恢复LLM方案，取消以下注释块即可。
"""

def llm_identify_scriptbook_files(file_paths: list[str], llm_client, model: str = "gemini-2.0-flash") -> list[str]:
    """使用LLM识别哪些文件是真正的台本文件
    
    参数:
        file_paths: 候选文件路径列表（可能是台本的文件）
        llm_client: OpenAI兼容的LLM客户端
        model: 模型名称
    
    返回:
        确认为台本文件的路径列表
    """
    if not file_paths:
        return []
    
    # 构建文件列表
    file_list = []
    for i, path in enumerate(file_paths, 1):
        file_list.append(f"{i}. {path}")
    
    file_list_text = '\n'.join(file_list)
    
    prompt = f"""你是一位日语ASMR音声作品台本识别专家。

以下是目录中发现的候选文件列表，请判断哪些是真正的台本文件。

【台本文件特征】
- 文件名包含：台本、セリフ、シナリオ、script
- 纯数字编号文件（如 01.txt, 02.txt）且内容为台词
- PDF文件通常为台本

【非台本文件特征】
- 文件名包含：readme、クレジット、注意、説明、あとがき
- 图片文件（jpg, png）
- 音频文件（mp3, wav）

【候选文件列表】
{file_list_text}

请返回JSON格式，包含确认的台本文件序号：
```json
{{
  "scriptbook_indices": [1, 3, 5],
  "reason": "简短说明判断依据"
}}
```

注意：
1. 如果没有台本文件，返回空列表：{{"scriptbook_indices": [], "reason": "无台本文件"}}
2. 只返回确认是台本的文件序号，不确定的不返回
3. 序号从1开始，对应上面的列表编号
"""

    try:
        response = llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一位日语台本识别专家，只返回JSON格式结果。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=300
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # 提取JSON部分
        if '```json' in result_text:
            result_text = result_text.split('```json')[1].split('```')[0].strip()
        elif '```' in result_text:
            result_text = result_text.split('```')[1].split('```')[0].strip()
        
        import json
        result = json.loads(result_text)
        
        indices = result.get("scriptbook_indices", [])
        reason = result.get("reason", "")
        
        # 转换为文件路径
        confirmed_paths = []
        for idx in indices:
            if 1 <= idx <= len(file_paths):
                confirmed_paths.append(file_paths[idx - 1])
        
        if confirmed_paths:
            print(f"  [LLM识别] 确认 {len(confirmed_paths)} 个台本文件: {reason}")
        else:
            print(f"  [LLM识别] 未发现台本文件: {reason}")
        
        return confirmed_paths
        
    except Exception as e:
        print(f"  [LLM识别] 台本识别失败: {e}")
        # 失败时返回所有候选文件（保守策略）
        return file_paths


def llm_analyze_track_structure(content: str, llm_client, model: str = "gemini-2.0-flash", debug_mode: bool = False) -> list[tuple[int, str]]:
    """使用LLM分析台本的音轨结构
    
    当正则无法正确识别音轨边界时，调用LLM分析台本内容，
    找出每个音轨的边界标记和标题。
    
    参数:
        content: 台本内容（清洗后）
        llm_client: OpenAI兼容的LLM客户端
        model: 模型名称
        debug_mode: 是否打印调试信息
    
    返回:
        [(音轨编号, 边界标记文本), ...]
        如 [(1, "☆プロローグ"), (2, "☆１"), (3, "☆２"), ...]
    """
    
    lines = content.split('\n')
    total_lines = len(lines)
    
    # 【改进】音轨标记特征模式
    track_marker_patterns = [
        r'^[☆★]',  # 星号标记（☆１、★1、☆プロローグ等）
        r'[トトラック]',  # トラック、Track
        r'^[Tt]r\.?\s*\d',  # Tr.1, Tr1
        r'^【[^】]*(?:トラック|プロローグ|エピローグ|第[0-9０-９]+章)',  # 【トラック1】【プロローグ】等
        r'^■[^■]*トラック',  # ■トラック01
        r'^《[^》]+》',  # 《章节标题》
    ]
    
    # 【改进】分两类收集关键行
    # 1. 优先行：包含音轨标记特征的行
    # 2. 普通行：其他非空行
    priority_lines = []  # 优先行（包含音轨标记）
    normal_lines = []    # 普通行（用于上下文）
    
    for i, line in enumerate(lines, 1):  # 从第1行开始
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r'^[\d\s]+$', stripped):
            continue
        
        line_info = f"行{i}: {stripped[:100]}"
        
        # 检查是否包含音轨标记特征
        is_track_marker = False
        for pattern in track_marker_patterns:
            if re.search(pattern, stripped):
                is_track_marker = True
                break
        
        # 额外检查：短行（<20字符）且包含数字或特殊符号，可能是音轨标记
        if not is_track_marker and len(stripped) < 20:
            if re.match(r'^[☆★◆■□●○◎◇]', stripped):
                is_track_marker = True
            # 纯数字行（全角或半角）
            elif re.match(r'^[０-９0-9]+$', stripped):
                is_track_marker = True
            # 数字+分隔符（全角或半角点号、空格）
            elif re.match(r'^[０-９0-9]+[\.．\s　]', stripped):
                is_track_marker = True
            # 全角数字+全角点号或其他分隔符
            elif re.match(r'^[０-９]+[．。・、]', stripped):
                is_track_marker = True
            # 半角数字+半角点号或其他分隔符
            elif re.match(r'^[0-9]+[\.\s]', stripped):
                is_track_marker = True
            # 带括号的数字（如 ①、②、⑴、⒈等）
            elif re.match(r'^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳⒈⒉⒊⒋⒌⒍⒎⒏⒐⒑⒒⒓⒔⒕⒖⒗⒘⒙⒚⒛⑴⑵⑶⑷⑸⑹⑺⑻⑼⑽⑾⑿⒀⒁⒂]', stripped):
                is_track_marker = True
        
        if is_track_marker:
            priority_lines.append(line_info)
        else:
            normal_lines.append(line_info)
    
    # 【改进】构建发送给LLM的样本
    # 策略：优先发送所有音轨标记行 + 开头部分普通行（提供上下文）
    # 总行数限制：300行（增加上限以确保包含所有音轨标记）
    max_total_lines = 300
    max_normal_lines = 50  # 普通行最多50行（用于开头上下文）
    
    # 合并：开头普通行 + 所有优先行
    sample_lines = normal_lines[:max_normal_lines] + priority_lines
    
    # 如果总行数超过限制，保留所有优先行，截断普通行
    if len(sample_lines) > max_total_lines:
        # 保留所有优先行
        sample_lines = normal_lines[:max(0, max_total_lines - len(priority_lines))] + priority_lines
    
    # 按行号排序（保持原始顺序）
    sample_lines = sorted(sample_lines, key=lambda x: int(x.split(':')[0].replace('行', '')))
    sample_lines = sample_lines[:max_total_lines]
    
    sample_text = '\n'.join(sample_lines)
    
    # DEBUG: 打印发送给LLM的样本内容
    if debug_mode:
        print(f"  [DEBUG] LLM音轨分析 - 台本总行数: {total_lines}")
        print(f"  [DEBUG] 发现音轨标记行: {len(priority_lines)} 行")
        print(f"  [DEBUG] 发送样本: {len(sample_lines)} 行 (优先行 {len(priority_lines)} + 普通行 {len(sample_lines) - len(priority_lines)})")
        print(f"  [DEBUG] 样本内容预览（前30行）:")
        for line in sample_lines[:30]:
            print(f"    {line}")
        if len(priority_lines) > 0:
            print(f"  [DEBUG] 音轨标记行列表:")
            for line in priority_lines[:20]:
                print(f"    {line}")
            if len(priority_lines) > 20:
                print(f"    ... (共 {len(priority_lines)} 行)")
    
    prompt = """你是一位日语ASMR音声作品台本分析专家。

请分析以下台本内容样本，找出音轨（トラック）的边界标记。

音轨边界通常是以下格式之一：
- 【トラック1：xxx】
- ■トラック01
- ☆プロローグ、☆１、☆２、★1 等
- Tr.1、Tr.2
- 纯数字行（如单独的 1、2、3）
- 章节标题（如 《xxx》、【プロローグ：xxx】）

请返回JSON格式：
```json
[
  {"track": 1, "marker": "边界标记原文"},
  {"track": 2, "marker": "边界标记原文"},
  ...
]
```

注意：
1. marker必须是台本中的原文（用于后续定位）
2. track编号从1开始
3. 如果无法识别边界，返回空列表 []

台本内容样本：
""" + sample_text + """

请分析并返回音轨边界列表："""

    try:
        response = llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一位日语台本分析专家，专注于识别音轨边界。只返回JSON格式结果，不要解释。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=500
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # 提取JSON部分
        if '```json' in result_text:
            result_text = result_text.split('```json')[1].split('```')[0].strip()
        elif '```' in result_text:
            result_text = result_text.split('```')[1].split('```')[0].strip()
        
        import json
        result = json.loads(result_text)
        
        # 转换为列表格式
        track_markers = []
        for item in result:
            track_num = item.get("track", 0)
            marker = item.get("marker", "")
            if track_num > 0 and marker:
                track_markers.append((track_num, marker))
        
        return track_markers
        
    except Exception as e:
        print(f"  [LLM分析] 音轨结构分析失败: {e}")
        return []


def split_scriptbook_by_llm_markers(content: str, track_markers: list[tuple[int, str]]) -> dict[int, str]:
    """根据LLM返回的边界标记划分台本内容
    
    参数:
        content: 台本内容
        track_markers: [(音轨编号, 边界标记), ...]
    
    返回:
        {音轨编号: 台本内容片段}
    """
    if not track_markers:
        return {1: content}  # 无法划分，返回整体
    
    lines = content.split('\n')
    track_contents = {}
    
    # 找到每个标记的行号
    marker_lines = {}  # {音轨编号: 行号}
    for track_num, marker in track_markers:
        marker_clean = marker.strip()
        for i, line in enumerate(lines):
            if marker_clean in line.strip() or line.strip() == marker_clean:
                marker_lines[track_num] = i
                break
    
    # 按行号排序
    sorted_markers = sorted(marker_lines.items(), key=lambda x: x[1])
    
    # 划分内容
    for idx, (track_num, start_line) in enumerate(sorted_markers):
        if idx + 1 < len(sorted_markers):
            end_line = sorted_markers[idx + 1][1]
        else:
            end_line = len(lines)
        
        track_content = '\n'.join(lines[start_line:end_line])
        track_contents[track_num] = track_content
    
    return track_contents


# ==================== 音轨标题提取与匹配 ====================

def extract_track_titles_from_scriptbook(content: str) -> list[tuple[int, str, int]]:
    """从台本内容中提取音轨标题
    
    支持格式：
    - 【王女様の種搾り騎乗位おまんこ　4737文字】
    - ②【王女様と正常位でセックス練習ラブラブおまんこ　3927文字】
    - ③【共有財産法律種オス制度、寝バックおまんこ　2819文字】
    
    参数:
        content: 台本内容
    
    返回:
        [(音轨编号, 标题, 行号), ...]
    """
    lines = content.split('\n')
    track_titles = []
    circled_to_num = {'①': 1, '②': 2, '③': 3, '④': 4, '⑤': 5,
                      '⑥': 6, '⑦': 7, '⑧': 8, '⑨': 9, '⑩': 10}
    
    for i, line in enumerate(lines):
        line = line.strip()
        # 匹配【xxx n文字】格式
        match = re.match(r'^([①②③④⑤⑥⑦⑧⑨⑩])?【([^】]+?)　?\d+文字】', line)
        if match:
            circled = match.group(1)
            title = match.group(2).strip()
            if circled and circled in circled_to_num:
                track_num = circled_to_num[circled]
            else:
                track_num = len(track_titles) + 1
            track_titles.append((track_num, title, i + 1))
    
    return track_titles


def extract_track_info_from_filename(filename: str):
    """从音频文件名中提取音轨编号和标题
    
    支持格式：
    - 01.王女様の種絞り騎乗位おまんこ-2.wav
    - 02.王女様と正常位でセックス練習ラブラブおまんこ.mp3
    
    返回:
        (音轨编号, 标题)，如果无法提取则对应位置为 None
    """
    from pathlib import Path
    stem = Path(filename).stem
    
    # 尝试匹配 "数字.标题" 格式
    match = re.match(r'^(\d+)[.．\-_]\s*(.+)', stem)
    if match:
        track_num = int(match.group(1))
        title = match.group(2).strip()
        # 去掉可能的数字后缀（如 -2）
        title = re.sub(r'[-‐‑]\d+$', '', title)
        return track_num, title
    
    return None, None


def match_tracks_by_title(scriptbook_path: Path, audio_dir: Path) -> dict[int, int]:
    """通过标题相似度匹配台本音轨与音频文件
    
    返回:
        {音频编号: 台本音轨编号} 的映射
    """
    from difflib import SequenceMatcher
    
    # 读取台本
    try:
        with open(scriptbook_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return {}
    
    # 提取台本标题
    scriptbook_titles = extract_track_titles_from_scriptbook(content)
    if not scriptbook_titles:
        return {}
    
    # 查找音频文件
    audio_files = sorted(list(audio_dir.glob('*.mp3')) + list(audio_dir.glob('*.wav')), 
                        key=lambda x: x.name)
    
    # 匹配
    matches = {}
    for audio_file in audio_files:
        track_num, title = extract_track_info_from_filename(audio_file.name)
        if not title:
            continue
        
        # 计算与每个台本音轨的相似度
        best_match = None
        best_sim = 0.0
        for sb_track_num, sb_title, _ in scriptbook_titles:
            sim = SequenceMatcher(None, title, sb_title).ratio()
            if sim > best_sim:
                best_sim = sim
                best_match = sb_track_num
        
        # 相似度阈值 70%
        if best_match and best_sim > 0.7:
            matches[track_num] = best_match
            print(f"  [标题匹配] {audio_file.name} -> 音轨{best_match} (相似度: {best_sim:.1%})")
        else:
            print(f"  [标题匹配] {audio_file.name} -> 无匹配 (最高相似度: {best_sim:.1%})")
    
    return matches
