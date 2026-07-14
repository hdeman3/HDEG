# -*- coding: utf-8 -*-
"""
文本分析模块

提供中日文文本检测、字符集分析、LRC 语言检测等功能。
"""

from __future__ import annotations
import re
from typing import Optional


# ==================== 字符集常量 ====================

# 日文假名范围
HIRAGANA_RANGE = r'\u3040-\u309f'
KATAKANA_RANGE = r'\u30a0-\u30ff'
KATAKANA_PHONETIC_EXT = r'\u31f0-\u31ff'
KATAKANA_HALF = r'\uff66-\uff9f'
# CJK 统一汉字
CJK_RANGE = r'\u4e00-\u9fff'
CJK_EXT_A = r'\u3400-\u4dbf'
CJK_EXT_B = r'\U00020000-\U0002a6df'
CJK_COMPAT = r'\u3000-\u303f'  # CJK 符号和标点
# 韩文
HANGUL_RANGE = r'\uac00-\ud7af'
# 拉丁
LATIN_RANGE = r'\u0041-\u005a\u0061-\u007a\u00c0-\u024f'
# 中文特有字符（不在日文中出现的）
# 注：中日共用汉字无法通过 Unicode 范围区分
# 通过日文假名的存在来判断

JAPANESE_KANA_PATTERN = re.compile(
    f'[{HIRAGANA_RANGE}{KATAKANA_RANGE}{KATAKANA_PHONETIC_EXT}{KATAKANA_HALF}]'
)

JAPANESE_CHAR_PATTERN = re.compile(
    f'[{HIRAGANA_RANGE}{KATAKANA_RANGE}{KATAKANA_PHONETIC_EXT}{KATAKANA_HALF}{CJK_RANGE}]'
)

CHINESE_CHAR_ONLY_PATTERN = re.compile(f'[{CJK_RANGE}]')


# ==================== 语言检测 ====================

def detect_japanese(text: str) -> bool:
    """检测文本是否包含日文

    判断标准：存在日文假名（平假名/片假名）

    参数:
        text: 待检测文本

    返回:
        True 如果包含日文假名
    """
    return bool(JAPANESE_KANA_PATTERN.search(text))


def detect_chinese(text: str) -> bool:
    """检测文本是否包含中文

    注意：中日共用汉字的 Unicode 范围重叠，此函数无法精准区分。
    配合 detect_japanese 使用：如果有假名则为日文，否则为中文。

    参数:
        text: 待检测文本

    返回:
        True 如果包含 CJK 汉字
    """
    return bool(CHINESE_CHAR_ONLY_PATTERN.search(text))


def detect_text_language(text: str) -> str:
    """检测文本的主要语言

    返回: "ja" | "zh" | "mixed" | "unknown"
    """
    has_kana = detect_japanese(text)
    has_cjk = detect_chinese(text)

    if has_kana:
        return "ja"
    elif has_cjk:
        return "zh"
    return "unknown"


def calculate_japanese_ratio(text: str) -> float:
    """计算日文假名在文本中的占比

    参数:
        text: 文本

    返回:
        0.0 ~ 1.0 之间的比例
    """
    text = text.replace('\n', '').replace(' ', '').replace('\r', '')
    if len(text) == 0:
        return 0.0

    kana_count = len(JAPANESE_KANA_PATTERN.findall(text))
    return kana_count / len(text)


def calculate_cjk_ratio(text: str) -> float:
    """计算 CJK 汉字在文本中的占比"""
    text = text.replace('\n', '').replace(' ', '').replace('\r', '')
    if len(text) == 0:
        return 0.0

    cjk_count = len(CHINESE_CHAR_ONLY_PATTERN.findall(text))
    return cjk_count / len(text)


# ==================== LRC 语言检测 ====================

def detect_lrc_language(lrc_content: str) -> str:
    """检测 LRC 歌词文件的语言

    通过分析所有歌词行中的字符来判断。

    参数:
        lrc_content: LRC 文件内容

    返回: "ja" | "zh" | "en" | "mixed" | "unknown"
    """
    # 提取歌词行（去除时间标签）
    lyrics_lines: list[str] = []
    time_tag_pattern = re.compile(r'\[\d{2}:\d{2}\.\d{2,3}\]')

    for line in lrc_content.split('\n'):
        # 去除时间标签
        lyric = time_tag_pattern.sub('', line).strip()
        # 去除元数据标签
        if lyric.startswith('[ti:') or lyric.startswith('[ar:') or \
           lyric.startswith('[al:') or lyric.startswith('[by:') or \
           lyric.startswith('[offset:'):
            continue
        if lyric:
            lyrics_lines.append(lyric)

    if not lyrics_lines:
        return "unknown"

    combined = ' '.join(lyrics_lines)

    has_kana = detect_japanese(combined)
    has_cjk = detect_chinese(combined)

    # 检测英文
    latin_count = len(re.findall(r'[a-zA-Z]', combined))
    total_len = len(combined.replace(' ', ''))

    if total_len == 0:
        return "unknown"

    latin_ratio = latin_count / total_len if total_len > 0 else 0

    if has_kana:
        if has_cjk and latin_ratio < 0.3:
            return "ja"
        elif latin_ratio > 0.5:
            return "mixed"
        return "ja"
    elif has_cjk:
        if latin_ratio < 0.3:
            return "zh"
        else:
            return "mixed"
    elif latin_ratio > 0.7:
        return "en"
    else:
        return "unknown"


# ==================== 行级语言检测 ====================

def classify_line_language(line: str) -> str:
    """对单行文本进行语言分类

    返回: "ja" | "zh" | "en" | "mixed" | "other"
    """
    line = line.strip()
    if not line:
        return "other"

    has_kana = detect_japanese(line)
    has_cjk = detect_chinese(line)
    has_latin = bool(re.search(r'[a-zA-Z]{2,}', line))

    if has_kana:
        return "ja"
    elif has_cjk and not has_latin:
        return "zh"
    elif has_latin and not has_cjk:
        return "en"
    elif has_cjk and has_latin:
        return "mixed"
    else:
        return "other"


# ==================== 文本清洗 ====================

def remove_non_japanese_lines(text: str) -> str:
    """移除不包含日文的行

    用于过滤 OCR/ASR 结果中的噪音行。

    参数:
        text: 原始文本

    返回: 过滤后的文本（仅保留含日文的行）
    """
    lines = text.split('\n')
    result: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if detect_japanese(stripped):
            result.append(line)

    return '\n'.join(result)


def remove_noise_lines(text: str) -> str:
    """移除明显的噪音行

    过滤规则：
    - 空行
    - 纯数字/符号行
    - 过短行（< 2 个日文字符）
    - 纯英文行（在日文语境中通常是噪音）
    """
    lines = text.split('\n')
    result: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # 纯数字/符号
        if re.match(r'^[\d\s\W_]+$', stripped):
            continue

        # 至少包含一些日文字符或汉字
        jp_chars = JAPANESE_CHAR_PATTERN.findall(stripped)
        if len(jp_chars) < 2:
            continue

        result.append(line)

    return '\n'.join(result)


# ==================== 文本统计 ====================

def get_text_stats(text: str) -> dict:
    """获取文本的字符统计数据

    返回:
        {
            "total_chars": int,
            "japanese_kana_count": int,
            "cjk_count": int,
            "latin_count": int,
            "digit_count": int,
            "japanese_ratio": float,
            "cjk_ratio": float,
        }
    """
    text_clean = text.replace('\n', '').replace(' ', '').replace('\r', '')
    total = len(text_clean)

    if total == 0:
        return {
            "total_chars": 0,
            "japanese_kana_count": 0,
            "cjk_count": 0,
            "latin_count": 0,
            "digit_count": 0,
            "japanese_ratio": 0.0,
            "cjk_ratio": 0.0,
        }

    kana_count = len(JAPANESE_KANA_PATTERN.findall(text_clean))
    cjk_count = len(CHINESE_CHAR_ONLY_PATTERN.findall(text_clean))
    latin_count = len(re.findall(r'[a-zA-Z]', text_clean))
    digit_count = len(re.findall(r'[0-9０-９]', text_clean))

    return {
        "total_chars": total,
        "japanese_kana_count": kana_count,
        "cjk_count": cjk_count,
        "latin_count": latin_count,
        "digit_count": digit_count,
        "japanese_ratio": kana_count / total,
        "cjk_ratio": cjk_count / total,
    }


# ==================== fugashi 分词与 core 提取 ====================

# 称呼后缀（按长度降序排列，优先匹配长的）
HONORIFIC_SUFFIXES = [
    'ちゃんたん', 'ちゃん', 'たん', 'さん', 'くん', '君',
    '様', 'さま', '殿', '氏',
    '先輩', '先生', 'せんせい',
    'チャン', 'タン', 'サン', 'クン', 'サマ',
]

# 停用词表（常见功能词和普通名词，不需要作为术语提取）
STOP_WORDS = {
    '私', 'わたし', 'わたくし', 'あたし', '僕', 'ぼく', '俺', 'おれ',
    'あなた', '君', 'きみ', 'お前', 'おまえ', '貴方',
    'ここ', 'そこ', 'あそこ', 'どこ',
    '今日', 'きょう', '明日', 'あした', '昨日', 'きのう',
    '今', 'いま', '先', 'さき', '後', 'あと',
    'もの', 'こと', '時', 'とき', '人', 'ひと',
    '気持ち', 'きもち', '気分', 'きぶん',
    '何', 'なに', 'なん', '誰', 'だれ', 'どれ', 'どの',
    'これ', 'この', 'それ', 'その', 'あれ', 'あの',
    'こんな', 'そんな', 'あんな', 'どんな',
    '一', '二', '三', '四', '五', '六', '七', '八', '九', '十',
    'いち', 'に', 'さん', 'よん', 'ご', 'ろく', 'なな', 'はち', 'きゅう', 'じゅう',
    '上', '下', '中', '外', '前', '後ろ', 'うえ', 'した', 'なか', 'そと', 'まえ', 'うしろ',
    '右', '左', 'みぎ', 'ひだり',
    '方', 'ほう', '所', 'ところ', '度', 'ど', '回', 'かい',
    '年', '月', '日', '週', '時間', '分', '秒',
    'とても', '非常', '大変', '少し', 'ちょっと', '少々',
    '良い', 'いい', '悪い', 'わるい', '大きい', 'おおきい', '小さい', 'ちいさい',
    'ある', 'いる', 'なる', 'する', 'できる', '来る', 'くる', '行く', 'いく',
    '思う', 'おもう', '言う', 'いう', '知る', 'しる', '見る', 'みる', '聞く', 'きく',
    'そう', 'こう', 'ああ', 'どう',
    'でも', 'しかし', 'だけど', 'けど', 'だから', 'ので', 'から', 'のに',
    'また', 'もう', 'まだ', 'もっと', 'ずっと', 'やっと', 'ついに',
    '本当', 'ほんとう', '本当に', 'ほんとうに', '実は', 'じつは',
    '多分', 'たぶん', '恐らく', 'おそらく', '確か', 'たしか',
    '全部', 'ぜんぶ', '皆', 'みんな', '全員', 'ぜんいん',
    '自分', 'じぶん', '自身', 'じしん',
    '相手', 'あいて', '側', 'がわ',
    '感じ', 'かんじ', '印象', 'いんしょう',
    '状態', 'じょうたい', '様子', 'ようす',
    '理由', 'りゆう', '原因', 'げんいん', '目的', 'もくてき',
    '方法', 'ほうほう', '手段', 'しゅだん',
    '問題', 'もんだい', '答え', 'こたえ',
    '場合', 'ばあい', '時点', 'じてん',
    '関係', 'かんけい', '関わり', 'かかわり',
    '意味', 'いみ', '内容', 'ないよう',
    '部分', 'ぶぶん', '点', 'てん',
    '事実', 'じじつ', '現実', 'げんじつ',
    '夢', 'ゆめ', '幻想', 'げんそう',
    '世界', 'せかい', '宇宙', 'うちゅう',
    '国', 'くに', '場所', 'ばしょ', '地域', 'ちいき',
    '家', 'いえ', '部屋', 'へや', '建物', 'たてもの',
    '道', 'みち', '路', 'みち', '通り', 'とおり',
    '山', 'やま', '川', 'かわ', '海', 'うみ', '空', 'そら',
    '木', 'き', '花', 'はな', '草', 'くさ',
    '動物', 'どうぶつ', '鳥', 'とり', '魚', 'さかな',
    '犬', 'いぬ', '猫', 'ねこ',
    '食べ物', 'たべもの', '飲み物', 'のみもの',
    '水', 'みず', '茶', 'ちゃ', '酒', 'さけ',
    '米', 'こめ', '肉', 'にく', '魚', 'さかな', '野菜', 'やさい',
    '朝', 'あさ', '昼', 'ひる', '夕方', 'ゆうがた', '夜', 'よる', '深夜', 'しんや',
    '春', 'はる', '夏', 'なつ', '秋', 'あき', '冬', 'ふゆ',
    '東', 'ひがし', '西', 'にし', '南', 'みなみ', '北', 'きた',
    '一緒', 'いっしょ', '二人', 'ふたり', '一人', 'ひとり',
    '最初', 'さいしょ', '最後', 'さいご', '途中', 'とちゅう',
    '次', 'つぎ', '前回', 'ぜんかい', '今回', 'こんかい',
    '毎日', 'まいにち', '毎週', 'まいしゅう', '毎月', 'まいつき', '毎年', 'まいとし',
    '時々', 'ときどき', '偶々', 'たまに', '常に', 'つねに',
    '急に', 'きゅうに', '突然', 'とつぜん', '次第に', 'しだいに',
    '必ず', 'かならず', '絶対', 'ぜったい', '多分', 'たぶん',
    '大概', 'たいがい', '大体', 'だいたい', '殆ど', 'ほとんど',
    '全く', 'まったく', '全然', 'ぜんぜん', '少し', 'すこし',
    '非常に', 'ひじょうに', '極めて', 'きわめて', '相当', 'そうとう',
    '更に', 'さらに', 'また', '且つ', 'かつ',
    '例えば', 'たとえば', '即ち', 'すなわち', '要するに', 'ようするに',
    '因みに', 'ちなみに', '尤も', 'もっとも', '但し', 'ただし',
    '尚', 'なお', '且つ', 'かつ', '又は', 'または',
    '等', 'など', '等々', 'などなど', '其れ等', 'それら',
}

# fugashi 延迟初始化
_TAGGER = None
_FUGASHI_AVAILABLE = None


def _init_tagger():
    """延迟初始化 fugashi Tagger"""
    global _TAGGER, _FUGASHI_AVAILABLE
    if _FUGASHI_AVAILABLE is not None:
        return
    try:
        import fugashi
        _TAGGER = fugashi.Tagger()
        _FUGASHI_AVAILABLE = True
    except Exception:
        _TAGGER = None
        _FUGASHI_AVAILABLE = False


def split_honorific(surface: str) -> tuple[str, str]:
    """拆分称呼后缀，返回 (core, suffix)

    例如: 'こりんちゃん' → ('こりん', 'ちゃん')
          'お姉さん' → ('お姉', 'さん')
    """
    for suffix in HONORIFIC_SUFFIXES:
        if surface.endswith(suffix):
            core = surface[:-len(suffix)]
            if len(core) >= 1:
                return core, suffix
    return surface, ''


def tokenize_text(text: str) -> list[dict]:
    """使用 fugashi 分词，返回词元列表（含 core 拆分）

    返回格式:
        [{
            'surface': str,   # 表層形（原文）
            'core': str,      # 去掉称呼后缀的核心词
            'suffix': str,    # 称呼后缀（如有）
            'reading': str,   # 读音（片假名）
            'pos': str,       # 词性
        }, ...]

    如果 fugashi 不可用，返回空列表。
    """
    _init_tagger()
    if not _FUGASHI_AVAILABLE:
        return []
    tokens = []
    for word in _TAGGER(text):
        pos = word.feature.pos1 if word.feature.pos1 else ''
        surface = word.surface
        reading = word.feature.kana if word.feature.kana else surface
        core, suffix = split_honorific(surface)
        tokens.append({
            'surface': surface,
            'core': core,
            'suffix': suffix,
            'reading': reading,
            'pos': pos,
        })
    return tokens


def is_fugashi_available() -> bool:
    """检查 fugashi 是否可用"""
    _init_tagger()
    return _FUGASHI_AVAILABLE


# ==================== 日语文本批量分析 ====================

def analyze_japanese_texts(texts: list[str]) -> dict:
    """对一批日语文本行进行统计分析

    参数:
        texts: 日文文本行列表

    返回:
        {
            "japanese_lines": int,   # 包含日文假名的行数
            "total_lines": int,       # 总行数
            "total_chars": int,       # 总字符数（去除空白）
            "unique_words": int,      # 唯一词汇数（按空格/CJK分隔）
            "top_words": list[str],   # 高频词列表（出现 >= 2 次）
        }
    """
    import re as _re
    from collections import Counter

    total_chars = 0
    japanese_lines = 0
    all_words: list[str] = []

    for line in texts:
        stripped = line.strip()
        if not stripped:
            continue

        # 统计字符
        clean = _re.sub(r'\s+', '', stripped)
        total_chars += len(clean)

        # 检测日文
        if detect_japanese(stripped):
            japanese_lines += 1

        # 简单分词：按空格和标点切分，保留长度 >= 2 的片段
        tokens = _re.split(r'[\s,.\!?、。！？…　]+', stripped)
        for t in tokens:
            t = t.strip()
            if len(t) >= 2:
                all_words.append(t)

    # 统计词频
    word_counter = Counter(all_words)
    # 只保留出现 >= 2 次且长度 >= 2 的词作为"高频词"
    top_words = [
        word for word, cnt in word_counter.most_common(50)
        if cnt >= 2 and len(word) >= 2
    ]

    return {
        "japanese_lines": japanese_lines,
        "total_lines": len(texts),
        "total_chars": total_chars,
        "unique_words": len(word_counter),
        "top_words": top_words,
    }