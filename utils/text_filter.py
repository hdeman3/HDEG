"""
纯文本过滤工具模块

提供台本文本清洗、页码过滤、对话/场景/指示分类等纯文本处理函数。
不依赖项目其他模块（无循环依赖）。
"""

import re

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

    【已弃用】来自旧版逻辑（git: 739f6b17 重构）。
    清洗效果不好、容易误伤台词，已改为 LLM（Flash）完成清洗。
    保留此函数仅作兼容参考，不再在主流程中调用。

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
        start_line = 0
        first_track_line = -1  # 重置，避免后续逻辑错误
    
    # 【修复】当按音轨分别清洗时（skip_intro=False），不从开头跳过
    # 因为音轨已经划分好了，前770行可能包含其他音轨的内容
    if not skip_intro:
        start_line = 0
        first_track_line = -1
        start_line = 0
    
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
