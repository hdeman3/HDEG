import os
import sys
import io

# 强制 UTF-8 输出，避免 Windows 控制台 GBK 编码错误
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 强制所有 print 语句即时刷新，避免输出缓冲延迟
class FlushStdout:
    def __init__(self, stream):
        self.stream = stream
    def write(self, data):
        self.stream.write(data)
        self.stream.flush()
    def flush(self):
        self.stream.flush()
    def __getattr__(self, name):
        return getattr(self.stream, name)

sys.stdout = FlushStdout(sys.stdout)
sys.stderr = FlushStdout(sys.stderr)

import re
import time
import shutil
import json
import random
from pathlib import Path
from openai import OpenAI
from collections import defaultdict

# ==================== fugashi/unidic_lite 字典路径配置 ====================
# 问题：PyInstaller 打包后，fugashi 需要能找到 unidic_lite 字典
# 解决：动态查找字典路径，优先级：importlib > sys._MEIPASS > 脚本目录

import importlib.util

def _find_unidic_dicdir() -> str:
    """
    动态查找 unidic_lite 字典目录，返回正斜杠格式的路径。
    
    查找优先级：
    1. 通过 importlib 查找已安装的 unidic_lite 包
    2. PyInstaller 打包后的临时目录 (sys._MEIPASS)
    3. 脚本目录下的 unidic_lite/dicdir
    
    返回:
        str: 字典目录路径，以正斜杠结尾
    """
    dicdir_path = None
    
    # 方法1：通过 importlib 查找已安装的包
    try:
        spec = importlib.util.find_spec('unidic_lite')
        if spec and spec.origin:
            # unidic_lite 包的位置
            unidic_package = Path(spec.origin).parent
            candidate = unidic_package / 'dicdir'
            if candidate.exists() and candidate.is_dir():
                # 检查是否有字典文件（char.bin 等核心文件）
                if (candidate / 'char.bin').exists():
                    dicdir_path = candidate
                    # 不打印日志，避免在正常模式下输出
                    # print(f"  [DEBUG] 找到 unidic_lite 字典: {candidate}")
    except Exception:
        pass
    
    # 方法2：PyInstaller 打包后的临时目录
    if dicdir_path is None and hasattr(sys, '_MEIPASS'):
        candidate = Path(sys._MEIPASS) / 'unidic_lite' / 'dicdir'
        if candidate.exists() and candidate.is_dir():
            if (candidate / 'char.bin').exists():
                dicdir_path = candidate
    
    # 方法3：脚本目录
    if dicdir_path is None:
        if getattr(sys, 'frozen', False):
            # 打包后的 exe 所在目录
            base_dir = Path(sys.executable).parent
        else:
            # 脚本所在目录
            base_dir = Path(__file__).parent
        candidate = base_dir / 'unidic_lite' / 'dicdir'
        if candidate.exists() and candidate.is_dir():
            if (candidate / 'char.bin').exists():
                dicdir_path = candidate
    
    if dicdir_path:
        # 转换为字符串，使用正斜杠，避免 Windows 反斜杠问题
        result = str(dicdir_path).replace('\\', '/')
        # 确保以正斜杠结尾
        result = result.rstrip('/') + '/'
        return result
    else:
        # 找不到字典，返回空字符串，后续 fugashi 会失败但不会崩溃
        return ""

# 查找字典路径
dic_path_safe = _find_unidic_dicdir()

# 设置环境变量，让 fugashi/unidic_lite 能找到字典
if dic_path_safe:
    os.environ['UNIDIC_DIR'] = dic_path_safe
    # 设置 MECABRC 环境变量，指向 mecabrc 文件
    mecabrc_path = dic_path_safe + 'mecabrc'
    os.environ['MECABRC'] = mecabrc_path
    # 同时设置 UNIDIC_DICDIR（某些版本需要）
    os.environ['UNIDIC_DICDIR'] = dic_path_safe

# 打包后 unidic_lite 的 dicdir 可能不在模块旁边，提前创建 mock 避免导入时崩溃
if hasattr(sys, '_MEIPASS'):
    import types

    _unidic_mock = types.ModuleType('unidic_lite')
    _unidic_mock.DICDIR = dic_path_safe if dic_path_safe else ""
    _unidic_mock.VERSION = 'unknown'
    sys.modules['unidic_lite'] = _unidic_mock


# ==================== 配置加载 ====================


def get_app_dir() -> Path:
    """获取程序所在目录（兼容直接运行和 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent.resolve()
    else:
        return Path(__file__).parent.resolve()


SCRIPT_DIR = get_app_dir()
CONFIG_PATH = SCRIPT_DIR / "config.json"

# ==================== 格式要求（可独立管理） ====================
_FORMAT_REQUIREMENTS = (
    "【格式要求——最高优先级，必须严格遵守】\n"
    "1. 输出行数必须与输入行数**完全一致**，一行不能多、一行不能少。\n"
    "2. 严格保留每一行的编号（如「01: 」），逐行对应输出中文翻译。\n"
    "3. 若某行为 [EMPTY_LINE]，必须**原样输出**该行，不得翻译或修改。\n"
    "4. 禁止合并、拆分、删除或新增任何行。\n"
    "5. 最终输出**仅**包含带编号的中文翻译结果，不允许出现任何解释、分析、纠错说明、前言、后语或标记。\n\n"
)


# ==================== ASR幻觉过滤（静音段误识别） ====================
# ASR在静音段常见的幻觉文本（结束语/开场白）
ASR_HALLUCINATION_ENDINGS = [
    # 日文结束语
    'ありがとうございました', 'ありがとう', 'ありがとうございます',
    'ありがとうございました。', 'ありがとうございます。',
    'お疲れ様でした', 'お疲れ様', 'お疲れ様でした。',
    'またお会いしましょう', 'また会いましょう', 'またね',
    'ご視聴ありがとうございました', 'ご視聴ありがとうございます',
    'ご購入ありがとうございました', 'ご購入ありがとうございます',
    'よかったらまた', 'また遊んでね', 'また来てね',
    '次回もお楽しみに', '次回も',
    'それでは', 'さようなら', 'バイバイ', 'ばいばい',
    '今回はこれで終わります', '終わります',
    'この作品は以上です', '以上です',
    'お楽しみいただけましたでしょうか',
    '気に入っていただけましたら',
    '感想待ってます', '感想お待ちしております',
    # 中文结束语（ASR有时会直接输出中文）
    '感谢收听', '感谢聆听', '谢谢收听', '谢谢聆听',
    '感谢购买', '感谢您的购买', '谢谢购买',
    '感谢观看', '感谢您的观看', '谢谢观看',
    '感谢支持', '感谢您的支持', '谢谢支持',
    '期待下次', '下次再见', '再见',
    '辛苦了', '辛苦了啊',
    '这次就到这里', '就到这里',
    '作品到此结束', '到此结束',
    # 英文结束语
    'thank you', 'thanks', 'thank you for listening',
    'thank you for watching', 'thank you for purchasing',
    'goodbye', 'bye', 'see you next time',
]

# ASR在静音段常见的幻觉文本（开场白）
ASR_HALLUCINATION_OPENINGS = [
    # 日文开场白
    'こんにちは', 'こんばんは', 'おはようございます', 'おはよう',
    'ようこそ', 'いらっしゃいませ', 'いらっしゃい',
    '始めましょう', '始めます', '始まります',
    '始めさせていただきます', '始めさせて頂きます',
    '皆さん', 'みなさん', '皆様', 'みな様',
    'お待たせしました', 'お待たせ',
    'ただいま', 'ただいまより',
    'それでは始め', 'では始め', 'じゃあ始め',
    'はじめに', '最初に',
    # 中文开场白
    '大家好', '各位好', '你们好',
    '欢迎来到', '欢迎收听', '欢迎观看',
    '开始吧', '我们开始', '开始了',
    '首先', '一开始',
    # 英文开场白
    'hello', 'hi there', 'welcome',
    'let\'s start', 'let us start', 'starting now',
    'begin', 'beginning',
]

# 注意：开场白（如"你好"、"欢迎"）不进行位置过滤
# 因为这些词可能在作品中的正常对话中出现（比如角色见面打招呼）
# 只有以下情况会过滤：
# 1. 结束语在文件中部出现（如音轨中部出现"感谢收听"）
# 2. 短句幻觉（整句只有感谢/再见等词）

def is_asr_hallucination(text: str, position_ratio: float = 0.5) -> tuple[bool, str]:
    """检测是否为ASR幻觉文本
    
    参数:
        text: 待检测的文本
        position_ratio: 该行在整个文件中的位置比例（0.0-1.0）
    
    返回: (是否幻觉, 幻觉类型)
    """
    text_lower = text.strip().lower()
    if not text_lower:
        return False, ""
    
    # 1. 检测结束语幻觉（在文件中部出现结束语）
    for ending in ASR_HALLUCINATION_ENDINGS:
        if ending.lower() in text_lower or ending in text:
            # 如果结束语出现在文件中部（不是最后10%），很可能是幻觉
            if position_ratio < 0.9:
                return True, f"mid_file_ending:{ending}"
    
    # 2. 检测开场白幻觉（在文件末尾出现开场白）
    for opening in ASR_HALLUCINATION_OPENINGS:
        if opening.lower() in text_lower or opening in text:
            # 如果开场白出现在文件末尾（不是前10%），很可能是幻觉
            if position_ratio > 0.1:
                return True, f"late_file_opening:{opening}"
    
    # 3. 检测短句幻觉（只有感谢/再见等词的短句）
    short_hallucination_patterns = [
        r'^[あア]りがとう[ございましたます]?[。．\.]?$',
        r'^[おオ]疲れ様?[でしたです]?[。．\.]?$',
        r'^[さサ]ようなら[。．\.]?$',
        r'^[ばバイ]い[ばバイ]い[。．\.]?$',
        r'^thank\s*you[。．\.]?$',
        r'^thanks[。．\.]?$',
        r'^bye[。．\.]?$',
        r'^goodbye[。．\.]?$',
        r'^感谢[收听观看购买支持]',
        r'^谢谢[收听观看购买支持]',
        # 新增：常见ASR幻觉模式
        r'^[こコ]んにちは[。．\.]?$',  # 单独的"你好"
        r'^[おオ]はよう[ございます]?[。．\.]?$',  # 单独的"早上好"
        r'^[いイ]らっしゃい[ませ]?[。．\.]?$',  # 单独的"欢迎"
        r'^[はハ]じめ[ましょう]?[。．\.]?$',  # 单独的"开始"
        r'^[みミ]なさん[。．\.]?$',  # 单独的"各位"
        r'^[おオ]待たせ[しました]?[。．\.]?$',  # 单独的"久等了"
        r'^hello[。．\.]?$',  # 单独的hello
        r'^hi[。．\.]?$',  # 单独的hi
        r'^welcome[。．\.]?$',  # 单独的welcome
        r'^大家好[。．\.]?$',  # 单独的"大家好"
        r'^欢迎[。．\.]?$',  # 单独的"欢迎"
    ]
    for pattern in short_hallucination_patterns:
        if re.match(pattern, text_lower):
            if position_ratio < 0.9:  # 不是文件末尾
                return True, f"short_ending_pattern"
    
    return False, ""


def filter_asr_hallucinations(lyrics: list[str], verbose: bool = True) -> list[str]:
    """过滤ASR幻觉文本
    
    参数:
        lyrics: 歌词列表
        verbose: 是否打印过滤信息
    
    返回: 过滤后的歌词列表（幻觉行替换为空字符串）
    """
    if not lyrics:
        return lyrics
    
    filtered_count = 0
    filtered_lines = []
    
    for i, line in enumerate(lyrics):
        position_ratio = (i + 1) / len(lyrics)  # 当前行位置比例
        
        is_hallucination, hallucination_type = is_asr_hallucination(line, position_ratio)
        
        if is_hallucination:
            filtered_count += 1
            if verbose:
                print(f"    [ASR幻觉过滤] 第{i+1}行 ({position_ratio:.1%}): \"{line[:30]}...\" -> 空")
            filtered_lines.append("")  # 替换为空行
        else:
            filtered_lines.append(line)
    
    if filtered_count > 0 and verbose:
        print(f"    [ASR幻觉过滤] 共过滤 {filtered_count} 行幻觉文本")
    
    return filtered_lines


# ==================== ASR校对提示词（台本辅助翻译） ====================
_ASR_CORRECTION_SYSTEM_PROMPT = (
    "你是一位专门处理日文成人音声（ASMR/RJ作品）字幕的专业本地化工程师，"
    "同时也是精通日语和中文的R18音声脚本翻译专家，"
    "以及专注于成人音声字幕的ASR（自动语音识别）纠错专家。\n\n"
    "你的职责是将ASR转录结果与参考台词列表对齐，逐句输出修正后的准确日语文本。\n\n"
    "【校对规则】\n"
    "1. 尽量使用参考台词中的原句，除非ASR明确出现了不同但合理的内容（即兴发挥），此时保留ASR的说法。\n"
    "2. 如果某处无法对齐，保留ASR原句，并在后面标注 [未对齐]。\n"
    "3. 输出格式：每行一句台词，无额外标签。\n"
    "4. 不要输出编号，只输出纯台词文本。\n"
    "5. 保持台词的原始顺序。\n\n"
    "【ASR常见错误类型】\n"
    "- 同音词误识别：如「なつき」→「夏生」、「こはる」→「小春」等\n"
    "- 拟声词浊音/半浊音混淆：如「びゅ」与「ぴゅ」\n"
    "- 口交场景乱码：连续假名/无意义字符应还原为呻吟或拟声词\n"
    "- 断句错误：根据上下文修复断裂的句子\n\n"
    "【容错原则】\n"
    "- 只要推断出的意思符合角色当前行为与情绪，优先保证语义连贯\n"
    "- 对于成人场景中的特殊表达，应结合情境进行合理还原\n"
)

_ASR_CORRECTION_USER_PROMPT_TEMPLATE = (
    "你是一位日语ASR后期校对专家。我会提供两段文本：\n\n"
    "【参考台词列表】：一段准确的剧本台词，按时间顺序排列。\n"
    "【ASR转录结果】：从音频中自动识别出的原始文本，可能含有错字、漏字、多字，且顺序可能混乱。\n\n"
    "请你将ASR转录结果与参考台词对齐，并逐句输出修正后的准确日语文本。\n\n"
    "规则：\n"
    "2. 如果某处无法对齐，保留ASR原句，并在后面标注 [未对齐]。\n"
    "3. 输出格式：每行一句台词，无额外标签。\n"
    "4. 不要输出编号，只输出纯台词文本。\n"
    "5. 保持台词的原始顺序。\n\n"
    "【参考台词列表】\n"
    "{reference_lines}\n\n"
    "【ASR转录结果】\n"
    "{asr_text}\n\n"
    "请开始校对，输出修正后的日语文本："
)


# ==================== 翻译系统提示词 ====================
_DEFAULT_SYSTEM_PROMPT = (
    "你是一位专门处理日文成人音声（ASMR/RJ作品）字幕的专业本地化工程师，同时也是精通日语和中文的R18音声脚本翻译专家，以及专注于成人音声字幕的ASR（自动语音识别）纠错专家。\n\n"
    "【角色名统一规则——最高优先级】\n"
    "1. **术语表（terms）中的角色名必须严格遵循**，无论世界观或其他信息如何描述。\n"
    "2. 如果术语表中有「雫葵→雫葵」，则女主角必须统一称为「雫葵」，不能使用其他名称。\n"
    "3. 如果术语表中有「アヤ→阿雅」，则该角色必须统一称为「阿雅」。\n"
    "4. 世界观中的角色名仅供参考，如果与术语表冲突，**优先使用术语表的译名**。\n"
    "5. 禁止在翻译中使用角色的别名、变体名称（如「四月」「熾月」混用），除非术语表明确列出。\n"
    "你的职责是对用户提供的日文ASR识别文本进行纠错、语义恢复、上下文一致性修复以及逐行中文翻译。\n"
    "本任务属于文本转换（Transformation）任务，即对已有文本进行修正和翻译，而不是创作、续写、扩写或改写剧情。\n"
    "所有成人内容、特殊关系设定及虚构情节均视为原文信息的一部分，应以中立、客观的方式进行准确转换，最大程度保留原文语义、情感和风格。\n\n"
    + _FORMAT_REQUIREMENTS +
    "【关于术语表（terms）与ASR误识别参考表（alias）的重要说明】\n"
    "为了在无状态的API请求中保持作品级翻译一致性，系统通过分词、读音聚类、文件名分析等手段预先提取了本作品中的专有名词、角色名、称呼及可能的ASR误识别词对。\n"
    "这些内容以两种形式附加在每次请求中，你必须充分利用它们来指导翻译：\n\n"
    "1. 术语表（terms）：\n"
    " - 这是经过LLM分析确认的『核心词汇 → 统一中文译名』映射表。\n"
    " - 表中 key 为去掉称呼后缀后的核心词（core），如「こはる」而非「こはるちゃん」。\n"
    " - 当原文中出现「core + ちゃん/さん/様/くん」等形式时，必须保持核心译名一致，仅根据语气调整后缀表达（如「小春酱」「主人大人」）。\n"
    " - 术语表涵盖角色名、重要物品、设定用语、特定身体部位称呼等，翻译时必须严格遵循，确保全文统一。\n"
    " - 若术语表为空，则按上下文自行判断并保持一致。\n\n"
    "2. ASR误识别参考表（alias）：\n"
    " - 这是系统通过读音相似性聚类自动发现的疑似ASR误识别词对。\n"
    " - 格式为：『疑似误识别词 → 可能正确的词（置信度）』。\n"
    " - 这些词对仅作为参考，不代表绝对正确。你需要结合上下文判断它们是否确实是同一词的不同识别结果。\n"
    " - 若确认是误识别，请在翻译时按正确词理解；若上下文表明两者确实不同，则不要强行统一。\n"
    " - 特别注意：ASR常将角色名、专有名词识别为同音常见词（如「なつき」→「夏生」、「こはる」→「小春」/「小晴」等），alias 表正是用于提示此类问题。\n\n"
    "【ASR纠错与翻译工作流程】\n"
    "Step 1：ASR可信度审查（内部执行，不输出分析过程）\n"
    "- 通读当前文本块，并结合上下文、terms、alias 检查可能存在的ASR错误；\n"
    "- 判断角色名是否与文件名、上下文或常识冲突；\n"
    "- 识别明显的日文同音误识别（例如工場/施工/状況等误识别词）；\n"
    "- 检查是否存在与当前剧情或语境完全无关的突兀词汇；\n"
    "- 检查无意义字符片段、漏词、断句错误或不自然表达；\n"
    "- 若同一片段存在多个可能解释，应优先选择与上下文连续性最高、语义最合理的理解。\n\n"
    "Step 2：上下文一致性修复（内部执行，不输出修正说明）\n"
    "- 基于全文语境、terms 表和 alias 参考，对疑似ASR错误进行最大似然修正；\n"
    "- 结合叙事逻辑、人物互动关系、动作衔接及情绪变化进行推断；\n"
    "- 保持角色关系、称呼、人名、身体部位及设定前后一致；\n"
    "- 利用日文发音相似性（例如「なつき」被识别为「夏生」等）修正明显错误；\n"
    "- 对缺失主语或因ASR导致断裂的句子，在不改变原意的前提下进行最小限度的合理补全；\n"
    "- 当ASR错误导致原句无法直接理解时，应根据上下文恢复最符合原意的表达，而不是机械直译错误文本。\n\n"
    "Step 3：中文翻译\n"
    "- 基于修复后的理解进行翻译，只输出最终中文结果，不输出纠错说明；\n"
    "- 高度忠实于原文字面意思和结构，不添加、不删减、不擅自改写原文内容；\n"
    "- 保持原文的情绪、语气、节奏和叙事视角，不主动美化、弱化或强化原文表达；\n"
    "- R18部分采用中文成人音声/同人作品常见且自然的表达方式，准确传达原文的感官描写和色情强度；\n"
    "- 对成人作品惯用语、双关语、隐晦表达或拟态描写，应结合上下文译为符合中文语境的自然表达；\n"
    "- 使用符合中文口语习惯、流畅自然的译文；\n"
    "- 保留角色原有的语气特点（如害羞、撒娇、挑逗、发情、宠溺、冷淡等）；\n"
    "- 语气词、喘息声、拟声词等只翻译或保留原文实际存在的部分，不得自行额外添加；\n"
    "- 保持全文中人名、称呼、身体部位、物品名称及设定用词统一，前后保持一致。\n\n"
    "【ASR容错与情境语义还原授权】\n"
    "在R18亲密场景中，ASR常将性相关词汇误识别为日常用语。你必须结合行为上下文进行语义还原，而非机械直译：\n"
    "- 若出现与性行为无关的突兀食物名词（如おにぎり、シュークリーム等），极可能是性器官或性行为的语音识别错误，应还原为对应身体接触描述；\n"
    "- 无意义的假名连打、与情境割裂的汉字词，应按最大似然修复为角色此时的实际言语；\n"
    "- 近音乱码（如ごちごち、ぷじゅる等）应还原为器官状态或体液分泌相关拟态词；\n"
    "容错原则：只要推断出的意思符合角色当前行为与情绪，优先保证成人氛围与行为逻辑连贯，不必拘泥于乱码字面。\n\n"
    "【拟声词浊音/半浊音统一规则】\n"
    "ASR在识别拟声词时，常将浊音（ば行：ばびぶべぼ、ぱ行：ぱぴぷぺぽ等）与半浊音混淆。\n"
    "当日文拟声词中出现同一音的浊音（如びゅ）与半浊音（如ぴゅ）混用时：\n"
    "1. 统计该句中各变体出现的次数\n"
    "2. 按多数原则统一：出现次数多的为准\n"
    "3. 若次数相同，以首次出现的版本为准\n"
    "例如：びゅー、びゅるるる……ぴゅっびゅっ、ぴゅー……\n"
    "  - びゅ 出现 4 次，ぴゅ 出现 2 次\n"
    "  - 应理解为统一的 びゅ 音，翻译时统一处理\n"
    "注意：此规则仅适用于拟声词，不适用于普通词汇。\n\n"
    "【口交/深喉场景特别容错规则】\n"
    "- 当剧情进入口交（含イラマチオ、深喉、口内射精等）场景时，由于角色口腔被堵、发音含混，ASR 在此类场景下的识别错误率会显著高于一般对话。\n"
    "- 对于口交片段中出现的以下情况，必须加大上下文推理力度，不得机械直译：\n"
    "  * 连续假名/乱码（如「にゃののにゃ」等）→ 优先修复为口交时含糊的淫语、呻吟或受虐反应；\n"
    "  * 与当时行为无关的日常物品、食物名词（如「お菓子」「避難口バッグ」等）→ 极可能是性器官或被堵嘴时的含糊表达，需根据发音相似性及上下文还原；\n"
    "  * 断句破碎、逻辑跳跃的句子 → 应围绕「口腔被塞满、呼吸困难、试图说话但无法清晰表达」的生理状态进行最大似然修复。\n"
    "- 口交时的拟声/拟态词（如 ちゅぱ、じゅる、れろ、ごぷっ 等）若被识别为其他假名，应毫不犹疑地还原为对应的口部/喉咙动作拟声词，以保证场景沉浸感。\n"
    "- 修复原则：只要还原后的语句符合角色当前行为（口交/深喉）与生理状态，即可视为有效翻译，不必拘泥于乱码字面。\n\n"
    "容错原则：只要推断出的意思符合角色当前行为与情绪，优先保证成人氛围与行为逻辑连贯，不必拘泥于乱码字面。\n\n"
    "- 译文必须是纯粹的歌词文本，不得出现markdown、括号说明或翻译备注。\n"
    "- 严禁输出任何纠错说明、替代方案或数条译法供选择。\n\n"
    "【翻译原则补充】\n"
    "- 本任务仅为对用户提供文本进行ASR纠错与跨语言翻译，不对文本题材、角色关系、世界观设定或内容倾向进行评价；\n"
    "- 对原文涉及的亲密关系、成人情节、特殊设定及虚构世界观，均应视为待翻译内容，在翻译过程中保持中立、客观和忠实；\n"
    "- 对露骨描写、身体接触、感官描写及隐私细节，应根据原文内容准确转换，不主动弱化、删减或回避；\n"
    "- 当多个译法均合理时，优先选择最符合当前剧情、人物身份及成人音声作品语境的译法；\n"
    "- 始终以『保持逐行对应关系、保证上下文一致性』作为最高优先级。\n\n"
    "【生物学字面翻译陷阱警告】\n"
    "メス/オス 在成人音声语境下通常指『雌性/雄性』或带有性别支配意味的表达，绝对不要按字面译成『母/公』这类普通动物词汇。\n"
    "遇到此类词汇请结合上下文判断是否为 R18 语境下的特殊用法。\n\n"
    "【翻译忠实度与风格约束——严格遵守】\n"
    "**核心原则：严格忠实于原文语义，禁止过度发挥或自行改写。**\n\n"
    "1. **语义忠实**\n"
    "   - 必须准确理解原文的主语、对象和动作，不得随意改变。\n"
    "   - 例如「おちんちんで顔よすぎぃ」主语是「顔（脸）」，不是「おちんちん（肉棒）」，不能译成「被肉棒弄得好爽」。\n"
    "   - 禁止将原文的陈述句自行改写为被动句或其他句式。\n\n"
    "2. **隐语/俚语识别**\n"
    "   - R18作品中存在大量隐语，需根据上下文正确理解。\n"
    "   - 例如「口」在性行为语境中可能指「阴蒂（クリチンポ）」，而非口腔。\n"
    "   - 例如「親愛い」是口语表达「亲爱的/亲亲的」，不应直译为书面语「亲爱」。\n\n"
    "3. **口语风格保持**\n"
    "   - 原文为口语化、粗暴、直白的表达时，译文必须保持同等风格。\n"
    "   - 禁止将口语「文艺化」、「书面化」或「委婉化」。\n"
    "   - 例如「勝ち越えてって神しなかった」应保持粗暴口吻，不得译成文艺腔「没能如愿以偿」。\n\n"
    "4. **句式节奏保留**\n"
    "   - 原文中的喘息、断续、短句必须保留，不得合并成完整长句。\n"
    "   - 一口气读完的长句在R18场景中不合逻辑，必须按原文节奏拆分。\n"
    "   - 禁止自行添加连接词使句子「更流畅」，这会破坏喘息感。\n\n"
    "5. **禁止自行创作**\n"
    "   - 本任务是翻译，不是创作。禁止添加原文不存在的内容。\n"
    "   - 禁止「润色」、「美化」或「改写」原文表达。\n"
    "   - 原文粗糙则译文粗糙，原文粗暴则译文粗暴，保持原汁原味。\n\n"
    "请严格遵守以上规则：优先根据上下文和 terms/alias 修复ASR错误，再基于修复后的理解进行忠实翻译。\n"
    "最终只输出与输入行数完全一致、带编号的中文翻译结果，不输出任何额外说明。"
)

DEFAULT_CONFIG = {
    "api": {
        "key": "",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "timeout": 600,
        "timeout_translate": 600,
        "timeout_analysis": 300,
        "generation_params": {
            "temperature": 1.3,
            "top_p": 0.90,
            "max_tokens": 131072,
            "max_tokens_translate": 131072,
            "max_tokens_worldview": 32768,
            "max_tokens_terms": 16384,
            "reasoning_effort": "medium"
        }
    },
    "app": {
        "work_dir": "",
        "input_path_file": "input_path.txt",
        "lrc_max_lines_per_request": 9999,
        "debug": False,
        "retry_count": 4,
        "delay_between_requests": 0.2,
        "audio_suffixes": [
            ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma",
            ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv"
        ],
        "subtitle_exts": [".lrc", ".srt", ".vtt"]
    },
    "pricing": {
        "hit_per_1m": 0.025,
        "miss_per_1m": 3.0,
        "completion_per_1m": 2.0
    },
    "network": {
        "clear_proxy_on_startup": True
    },
    "prompts": {
        "system_prompt": _DEFAULT_SYSTEM_PROMPT,
        "system_prompt_file": ""
    }
}


def load_config():
    """加载配置，若不存在则生成默认模板并退出"""
    if not CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        print(f"已生成默认配置文件: {CONFIG_PATH}")
        print("请先编辑 config.json 填入 api.key 等必要信息后再运行。")
        sys.exit(0)

    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        user_cfg = json.load(f)

    def merge(base, override):
        result = base.copy()
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = merge(result[k], v)
            else:
                result[k] = v
        return result

    return merge(DEFAULT_CONFIG, user_cfg)


CONFIG = load_config()

# ==================== 网络代理 ====================
if CONFIG["network"]["clear_proxy_on_startup"]:
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
        os.environ.pop(key, None)
    os.environ['NO_PROXY'] = '*'

# ==================== API 初始化 ====================
_api_cfg = CONFIG["api"]
if not _api_cfg.get("key"):
    print("错误: 请在 config.json 中配置 api.key")
    sys.exit(1)

client = OpenAI(api_key=_api_cfg["key"], base_url=_api_cfg["base_url"])

# ==================== 路径与工作目录 ====================
_app_cfg = CONFIG["app"]
INPUT_PATH_FILE = SCRIPT_DIR / _app_cfg["input_path_file"]

# 优先使用 input_path.txt（通过bat文件传入的路径），其次使用 config.json 中的 work_dir
if INPUT_PATH_FILE.exists():
    with open(INPUT_PATH_FILE, "r", encoding="utf-8") as f:
        BASE_PATH = Path(f.read().strip()).resolve()
else:
    _work_dir = _app_cfg.get("work_dir", "")
    if _work_dir:
        BASE_PATH = Path(_work_dir).resolve()
    else:
        BASE_PATH = Path(".").resolve()

WORK_DIR = BASE_PATH

# ==================== 业务参数 ====================
LRC_MAX_LINES_PER_REQUEST = _app_cfg["lrc_max_lines_per_request"]
RETRY_COUNT = _app_cfg["retry_count"]
DELAY_BETWEEN_REQUESTS = _app_cfg["delay_between_requests"]
AUDIO_SUFFIXES = set(_app_cfg.get("audio_suffixes", []))
SUBTITLE_EXTS = set(_app_cfg.get("subtitle_exts", []))
DEBUG_MODE = _app_cfg.get("debug", False)
USE_SCRIPTBOOK_FOR_TRANSLATION = _app_cfg.get("use_scriptbook_for_translation", True)
SCRIPTBOOK_KEYWORD_MODE = _app_cfg.get("scriptbook_keyword_mode", True)
SCRIPTBOOK_FULL_MODE = _app_cfg.get("scriptbook_full_mode", False)

# ==================== 价格配置 ====================
_price_cfg = CONFIG["pricing"]
PRICE_HIT_PER_1M = _price_cfg["hit_per_1m"]
PRICE_MISS_PER_1M = _price_cfg["miss_per_1m"]
PRICE_COMPLETION_PER_1M = _price_cfg.get("completion_per_1m", 8.0)

# ==================== Prompt 加载 ====================
_prompt_cfg = CONFIG["prompts"]
SYSTEM_PROMPT = _prompt_cfg.get("system_prompt", _DEFAULT_SYSTEM_PROMPT)
_sp_file = _prompt_cfg.get("system_prompt_file", "")
if _sp_file:
    sp_path = Path(_sp_file) if Path(_sp_file).is_absolute() else SCRIPT_DIR / _sp_file
    if sp_path.exists():
        with open(sp_path, 'r', encoding='utf-8') as f:
            SYSTEM_PROMPT = f.read()
    else:
        print(f"警告: 指定的 prompt 文件不存在: {sp_path}")

# ==================== Token 统计（全局） ====================
total_hit_tokens = 0
total_miss_tokens = 0
total_prompt_tokens = 0
total_completion_tokens = 0

# ==================== 热门ASMR日本CV列表 ====================
# 这些CV的freetalk内容通常不需要构建世界观
POPULAR_ASMR_CV_NAMES = {
    # ===== 热门CV（用户提供列表）=====
    '陽向葵ゅか', 'ひなたゆか', 'hinata', 'ヒナタ',
    '柚木つばめ', 'ゆのきつばめ', 'tsubame', 'ツバメ',
    '涼花みなせ', 'すずかみなせ', 'minase', 'ミナセ',
    '御子柴泉', 'みこしばいずみ', 'izumi', 'イズミ',
    'みもりあいの', 'mimori', 'ミモリ',
    '餅梨あむ', 'もちりしあむ', 'am', 'アム',
    '乙倉ゆい', 'おとくらゆい', 'yui', 'ユイ',
    '雲八はち', 'くもぱちはち', 'hachi', 'ハチ',
    '逢坂成美', 'おうさかなるみ', 'narumi', 'ナルミ',
    '大山チロル', 'おおやまちろる', 'tirol', 'チロル',
    '恋鈴桃歌', 'こすずももか', 'momoka', 'モモカ',
    '秋野かえで', 'あきのかえで', 'kaede', 'カエデ',
    '山田じぇみ子', 'やまだじぇみこ', 'jemiko', 'ジェミ子',
    '小花衣こっこ', 'こばなごろっこ', 'kokko', 'コッコ',
    '秋山はるる', 'あきやまはるる', 'haruru', 'ハルル',
    '藤村莉央', 'ふじむらりお', 'rio', 'リオ',
    '浅木式', 'あさきしき', 'shiki', 'シキ',
    '西瓜すいか', 'すいか', 'suika', 'スイカ',
    '天知遥', 'あまちはるか', 'haruka', 'ハルカ',
    '高梨はなみ', 'たかなしはなみ', 'hanami', 'ハナミ',
    '分倍河原シホ', 'ぶばいがわらしほ', 'shiho', 'シホ',
    '伊ヶ崎綾香', 'いがさきあやか', 'ayaka', 'アヤカ',
    '海音ミヅチ', 'かいおんみづち', 'mizuchi', 'ミヅチ',
    'こまる', 'komaru', 'コマル',
    '涼貴涼', 'すきたきりょう', 'ryo', 'リョウ',
    'こやまはる', 'koyama', 'ハル',
    '一之瀬りと', 'いちのせりと', 'rito', 'リト',
    'そらまめ。', 'soramame', 'ソラマメ',
    '御崎ひより', 'みさきひより', 'hiyori', 'ヒヨリ',
    '藍沢夏癒', 'あいざわなつゆ', 'natsuyu', 'ナツユ',
    '伊倉える', 'いくらえる', 'eru', 'エル',
    'かの仔', 'kano', 'カノ',
    '雪見だいふく', 'ゆきみだいふく', 'daifuku', 'ダイフク',
    '琴音有波', 'ことねありは', 'ariha', 'アリハ',
    '来夢ふらん', 'らいむふらん', 'furan', 'フラン', 'raimu', 'ライム', '来夢',
    '梵雪音', 'ぼんせつね', 'setsune', 'セツネ',
    '兎月りりむ。', 'うづきりりむ', 'ririmu', 'リリム',
    '星野天', 'ほしのてん', 'ten', 'テン',
    '猫視ねこ', 'ねこみねこ', 'neko', 'ネコ',
    '春乃つくし', 'はるのつくし', 'tsukushi', 'ツクシ',
    'ありのりあ', 'arinoria', 'ノリア',
    'みたかりん', 'mitakarin', 'カリン',
    '都みみち', 'みやこみみち', 'mimichi', 'ミミチ',
    '未想可みいろ', 'みそかみいろ', 'miiro', 'ミイロ',
    '沢野ぽぷら', 'さわのぽぷら', 'popura', 'ポプラ',
    '皆乃あな', 'みなのあな', 'ana', 'アナ',
    '鬼霧茜', 'きりぎりしあかね', 'akane', 'アカネ',
    '眠音りま', 'ねむねりま', 'rima', 'リマ',
    'えもこ', 'emoko', 'エモコ',
    'まあ油るる', 'まあゆるる', 'ruru', 'ルル',
    '御苑生メイ', 'みそのうめい', 'mei', 'メイ',
    '麦咲輪紫葵', 'むぎさきりんしあおい', 'shiai', 'シアイ',
    '佐々木サキ', 'ささきさき', 'saki', 'サキ',
    '早緒きむり', 'はやおきむり', 'kimuri', 'キムリ',
    '柚木朱莉', 'ゆのきあかり', 'akari', 'アカリ',
    '道楽みぃ', 'どうらくみい', 'mii', 'ミイ',
    '彩夢ひな', 'さいむひな', 'hina', 'ヒナ',
    '野上菜月', 'のがみなつき', 'natsuki', 'ナツキ',
    'ゆにこ', 'yuniko', 'ユニコ',
    '七瀬ゆな', 'ななせゆな', 'yuna', 'ユナ',
    '飴屋ぺろり', 'あまやぺろり', 'perori', 'ペロリ',
    '相坂優歌', 'あいさかゆうか', 'yuuka', 'ユウカ',
    '双葉すずね', 'ふたばすずね', 'suzune', 'スズネ',
    '小机永遠', 'こづくえとわ', 'towa', 'トワ',
    '豊川ゆき', 'とよかわゆき', 'yuki', 'ユキ',
    '雨音いろみず', 'あまねいろみず', 'iromizu', 'イロミズ',
    '篠守ゆきこ', 'しのもりゆきこ', 'yukiko', 'ユキコ',
    'つばきりむ', 'tsubakirim', 'リム',
    '暁鞠子', 'あかつきまりこ', 'mariko', 'マリコ',
    '野々原まどか', 'ののはらまどか', 'madoka', 'マドカ',
    '碧棺らむだ', 'あおひつぎらむだ', 'ramuda', 'ラムダ',
    '佐久間のの', 'さくまのの', 'nono', 'ノノ',
    'のぐちゆり', 'noguchiyuri', 'yuri', 'ユリ',
    '狐今あまね', 'こぎつねあまね', 'amane', 'アマネ',
    '箱河ノア', 'はこかわのあ', 'noa', 'ノア',
    '雪宮あかね', 'ゆきみやあかね', 'akane', 'アカネ',
    '涼本あきほ', 'すずもとあきほ', 'akiho', 'アキホ',
    '龍涎にこみ', 'りゅうぜんにこみ', 'nikomi', 'ニコミ',
    '堀米玲音', 'ほりまいれおん', 'reon', 'レオン',
    '愛崎未来', 'あいさきみらい', 'mirai', 'ミライ',
    '葵時緒', 'あおいしお', 'shio', 'シオ',
    '桜音のん', 'さくらねのん', 'non', 'ノン',
    'アルギュロス', 'argyros', 'アルギュ',
    '転寝', 'ころね', 'korone', 'コロネ',
    '鹿瀬紫卯', 'かせしう', 'shiu', 'シウ',
    'エトラ', 'etora', 'エトラ',
    '立花百合', 'たちばなゆり', 'yuri', 'ユリ',
    '浅見ゆい', 'あさみゆい', 'yui', 'ユイ',
    '蒼乃むすび', 'あおのむすび', 'musubi', 'ムスビ',
    '奏手七色', 'かなでなないろ', 'nanairo', 'ナナイロ',
    '飯田ヒカル', 'いいだひかる', 'hikaru', 'ヒカル',
    '真白真雪', 'ましろまゆき', 'mayuki', 'マユキ',
    '裏垢女子あむちゃん', 'うらあかじょしあむちゃん', 'amchan', 'アムチャン',
    'まこと。', 'makoto', 'マコト',
    'メウサギさくらちゃん', 'meusagi', 'サクラ',
    '由比かのん', 'ゆいかのん', 'kanon', 'カノン',
    'ほぬ', 'honu', 'ホヌ',
    '櫻井陽菜', 'さくらいひな', 'hina', 'ヒナ',
    'ひなみ桜花', 'ひなみおうか', 'ouka', 'オウカ',
    'あかしゆき', 'akashiyuki', 'ユキ',
    '柚木ゆらら', 'ゆのきゆらら', 'yurara', 'ユララ',
    'なしのまりも', 'nashinomarimo', 'marimo', 'マリモ',
    '和氣あず未', 'わけあずみ', 'azumi', 'アズミ',
    '天羽リケ', 'あもうリケ', 'rike', 'リケ',
    '猫舐つな', 'ねこなめつな', 'tsuna', 'ツナ',
    '餅々めぅ', 'もちもちめう', 'meu', 'メゥ',
    '苺兎彩花', 'いちごうさあか', 'saika', 'サイカ',
    '原由実', 'はらゆみ', 'yumi', 'ユミ',
    '紫雲', 'しうん', 'shiun', 'シウン',
    '鳴山なるみ', 'なるやまなるみ', 'narumi', 'ナルミ',
    '橙乃花笠', 'だいのはながさ', 'hanagasa', 'ハナガサ',
    '藤村梨央', 'ふじむらりお', 'rio', 'リオ',
    'ありがた～い私', 'arigatai', 'アリガタイ',
    '和泉美鈴', 'いずみみすず', 'misuzu', 'ミスズ',
    'まろねあいす', 'marone', 'アイス',
    '桐原琴音', 'きりはらことね', 'kotone', 'コトネ',
    '生十花なる', 'せいとかなる', 'naru', 'ナル',
    '絶頂ひとりオナ子', 'zecchou', 'オナコ',
    '中野さいま', 'なかのさいま', 'saima', 'サイマ',
    '竹中望', 'たけなかのぞみ', 'nozomi', 'ノゾミ',
    '涼風めい', 'すずかぜめい', 'mei', 'メイ',
    '高柳知葉', 'たかやなぎちよう', 'chiyou', 'チヨウ',
    '羽柴利緒', 'はしばりお', 'rio', 'リオ',
    '星野めりか', 'ほしのめりか', 'merika', 'メリカ',
    '青山吉能', 'あおやまよしの', 'yoshino', 'ヨシノ',
    '二回戦中', 'にかいせんちゅう', 'nikaichen', 'ニカイセン',
    '逢真井もこ', 'あいまいもこ', 'moko', 'モコ',
    '月見れもん', 'つきみれもん', 'remon', 'レモン',
    '蘭世', 'らんせ', 'ranse', 'ランセ',
    'ゆらり', 'yurari', 'ユラリ',
    '小澤亜李', 'おざわあり', 'ari', 'アリ',
    '星乃ねむ', 'ほしのねむ', 'nemu', 'ネム',
    '星羅あかね', 'せいらあかね', 'akane', 'アカネ',
    '溜まり場ちゃん', 'たまりばちゃん', 'tamariba', 'タマリバ',
    # ===== 其他常见CV =====
    'さくら', 'sakura', 'サクラ',
    'ゆかり', 'yukari', 'ユカリ',
    'みお', 'mio', 'ミオ',
    'あやね', 'ayane', 'アヤネ',
    'しろ', 'shiro', 'シロ',
    'りん', 'rin', 'リン',
    'める', 'meru', 'メル',
    'かな', 'kana', 'カナ',
    'さえ', 'sae', 'サエ',
    'あんり', 'anri', 'アンリ',
    'こはる', 'koharu', 'コハル',
    'はる', 'haru', 'ハル',
    'なつ', 'natsu', 'ナツ',
    'めぐ', 'megu', 'メグ',
    'れい', 'rei', 'レイ',
    'りょう', 'ryo', 'リョウ',
    'けい', 'kei', 'ケイ',
    'あおい', 'aoi', 'アオイ',
    'さとみ', 'satomi', 'サトミ',
    'ともみ', 'tomomi', 'トモミ',
    'やよい', 'yayoi', 'ヤヨイ',
    'はな', 'hana', 'ハナ',
    'もも', 'momo', 'モモ',
    'くるみ', 'kurumi', 'クルミ',
    'しずく', 'shizuku', 'シズク',
    'ひまり', 'himari', 'ヒマリ',
    'さりな', 'sarina', 'サリナ',
    'ありす', 'arisu', 'アリス',
    'くるる', 'kururu', 'クルル',
    'みるく', 'miruku', 'ミルク',
    'はなちゃん', 'hanachan', 'ハナチャン',
    # 山田姓（常见CV姓氏）
    'やまだ', 'yamada', 'ヤマダ', '山田',
    # 其他常见CV名
    'じぇみ子', 'jemiko', 'ジェミ子',
    'すみこ', 'sumiko', 'スミコ',
    '寿美子', 'すみこ',
    'ふらん', 'furan', 'フラン',
}

# FreeTalk 关键词（英文/日文）
FREETALK_KEYWORDS = {
    'freetalk', 'free_talk', 'free talk',
    'フリートーク', 'ふりーとーく', 'フリトク',
    'フリートク', 'あとがき', 'アトガキ',
    'おまけ', 'オマケ', 'bonus', 'omake',
    '特典', 'とくてん', 'tokuten',
    'インタビュー', 'interview',
    'コメント', 'comment',
}

def is_freetalk_context(path: Path) -> bool:
    """检测路径是否为freetalk或包含热门CV名称
    
    返回: (is_freetalk, cv_name or None)
    """
    path_str = str(path).lower()
    path_parts = path.parts
    
    # 1. 检测 freetalk 关键词
    for keyword in FREETALK_KEYWORDS:
        if keyword.lower() in path_str:
            return True, None
    
    # 2. 检测热门CV名称（精确匹配）
    # 只检测较长的CV名（>=3字符），避免误匹配如"エル"匹配到"エルフ"
    for part in path_parts:
        for cv_name in POPULAR_ASMR_CV_NAMES:
            # 只检测长度>=3的CV名，避免短名误匹配
            if len(cv_name) < 3:
                continue
            # 使用正则表达式进行边界匹配
            # 匹配：CV名前后是分隔符（空格、下划线、括号等）或字符串边界
            import re
            pattern = r'(^|[\s\_\-\(\)（）\[\]「」『』【】])' + re.escape(cv_name) + r'($|[\s\_\-\(\)（）\[\]「」『』【】])'
            if re.search(pattern, part, re.IGNORECASE):
                return True, cv_name
    
    return False, None


# ==================== 停用词表 ====================
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
    '此れ', 'これ', '其れ', 'それ', '彼', 'あれ', '何れ', 'どれ',
    '此処', 'ここ', '其処', 'そこ', '彼処', 'あそこ', '何処', 'どこ',
    '此の', 'この', '其の', 'その', '彼の', 'あの', '何の', 'どの',
    '此様', 'こう', '其様', 'そう', '彼様', 'ああ', '何様', 'どう',
    '誰', 'だれ', '何', 'なに', '幾ら', 'いくら', '幾つ', 'いくつ',
    '如何', 'どう', '如何して', 'どうして', '何故', 'なぜ',
    '何時', 'いつ', '何処', 'どこ', '何方', 'どちら', '何れ', 'どれ',
    '此れ程', 'これほど', '其れ程', 'それほど', 'あれ程', 'あれほど',
    '如何に', 'どうに', '如何しても', 'どうしても',
    '何とか', 'なんとか', '何処か', 'どこか', '何時か', 'いつか',
    '何も', 'なにも', '何でも', 'なんでも', '何か', 'なにか',
    '誰か', 'だれか', '誰も', 'だれも', '誰でも', 'だれでも',
    '何方か', 'どちらか', '何方も', 'どちらも', '何方でも', 'どちらでも',
    '此れから', 'これから', '其れから', 'それから', '此れまで', 'これまで',
    '其れまで', 'それまで', '此れでは', 'これでは', '其れでは', 'それでは',
    '此れなら', 'これなら', '其れなら', 'それなら', '此れに', 'これに',
    '其れに', 'それに', '此れを', 'これを', '其れを', 'それを',
    '此れが', 'これが', '其れが', 'それが', '此れも', 'これも',
    '其れも', 'それも', '此れだけ', 'これだけ', '其れだけ', 'それだけ',
    '此れしか', 'これしか', '其れしか', 'それしか',
    '此れ程', 'これほど', '其れ程', 'それほど', '此れ位', 'これくらい',
    '其れ位', 'それくらい', '此れ等', 'これら', '其れ等', 'それら',
    '此の様', 'このよう', '其の様', 'そのよう', '彼の様', 'あのよう',
    '此の位', 'このくらい', '其の位', 'そのくらい', '彼の位', 'あのくらい',
    '此の通り', 'このとおり', '其の通り', 'そのとおり',
    '此の辺', 'このへん', '其の辺', 'そのへん', '此の辺り', 'このあたり',
    '其の辺り', 'そのあたり', '此の辺で', 'このへんで', '其の辺で', 'そのへんで',
    '此の辺りで', 'このあたりで', '其の辺りで', 'そのあたりで',
    '此の程度', 'このていど', '其の程度', 'そのていど',
    '此の位で', 'このくらいで', '其の位で', 'そのくらいで',
    '此の辺まで', 'このへんまで', '其の辺まで', 'そのへんまで',
    '此の辺りまで', 'このあたりまで', '其の辺りまで', 'そのあたりまで',
    '此の程度で', 'このていどで', '其の程度で', 'そのていどで',
    '此の位まで', 'このくらいまで', '其の位まで', 'そのくらいまで',
}


# ==================== 术语表/Alias 管理 ====================

def get_terms_path(work_dir: Path) -> Path:
    """获取术语表文件路径（作品级，文件夹内 .terms.json）"""
    return work_dir / '.terms.json'


def get_alias_path(work_dir: Path) -> Path:
    """获取 alias 表文件路径（作品级，文件夹内 .alias.json）"""
    return work_dir / '.alias.json'


def load_terms(work_dir: Path) -> dict[str, str]:
    """加载已保存的术语表"""
    terms_path = get_terms_path(work_dir)
    if terms_path.exists():
        try:
            with open(terms_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return {}
    return {}


def save_terms(work_dir: Path, terms: dict[str, str]) -> None:
    """保存术语表"""
    terms_path = get_terms_path(work_dir)
    with open(terms_path, 'w', encoding='utf-8') as f:
        json.dump(terms, f, ensure_ascii=False, indent=2)


def load_alias(work_dir: Path) -> list[dict]:
    """加载已保存的 alias 表"""
    alias_path = get_alias_path(work_dir)
    if alias_path.exists():
        try:
            with open(alias_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return []
    return []


def save_alias(work_dir: Path, alias: list[dict]) -> None:
    """保存 alias 表"""
    alias_path = get_alias_path(work_dir)
    with open(alias_path, 'w', encoding='utf-8') as f:
        json.dump(alias, f, ensure_ascii=False, indent=2)


def build_terms_prompt(terms: dict[str, str]) -> str:
    """将术语表构建为 prompt 附加内容"""
    if not terms:
        return ""
    lines = ["\n【已确定译名表（请务必遵循以下译名，保持全文统一）】"]
    for ja, zh in sorted(terms.items(), key=lambda x: x[0]):
        lines.append(f"  {ja} → {zh}")
    lines.append("【译名表结束】")
    lines.append(
        "注意：若原文中出现「译名+ちゃん/さん/様/くん」等称呼后缀，请保持核心译名一致，仅根据语气调整后缀表达（如「小春酱」「主人大人」）。\n")
    return "\n".join(lines)


def build_alias_prompt(alias_list: list[dict]) -> str:
    """将 alias 表构建为 prompt 附加内容（弱约束）"""
    if not alias_list:
        return ""
    lines = ["\n【疑似ASR误识别参考（以下词可能为同一词的不同识别结果，翻译时请注意统一）】"]
    for item in alias_list:
        alias = item.get('alias', '')
        target = item.get('target', '')
        conf = item.get('confidence', 0)
        lines.append(f"  {alias} 可能是 {target} 的误识别（置信度: {conf:.0%}）")
    lines.append("【参考结束】\n")
    return "\n".join(lines)


def is_key_dialogue_line(line: str) -> bool:
    """判断是否为值得参考的关键台词
    
    关键台词特征：
    1. 包含角色名（格式为"角色名：台词"）
    2. 包含重要信息（地点、时间、人物关系）
    3. 包含情感表达（喜欢、讨厌、爱等）
    4. 包含剧情转折（但是、因为、所以等）
    5. 长度适中的句子（10-100字符，排除纯拟声词）
    
    参数:
        line: 台词文本
    
    返回: 是否为关键台词
    """
    if not line or not line.strip():
        return False
    
    stripped = line.strip()
    
    # 排除过短或过长的行
    if len(stripped) < 5:
        return False
    if len(stripped) > 150:
        return True  # 长句保留，可能包含重要信息
    
    # 1. 包含角色名（格式为"角色名：台词"）
    if re.search(r'^[^：\n]{2,10}：', stripped):
        return True
    
    # 2. 包含重要信息关键词
    key_patterns = [
        # 地点相关
        r'(学校|教室|部室|家|部屋|寮|廊下|屋上|図書室|トイレ|ロッカー|教室)',
        # 时间相关
        r'(昨日|今日|明日|朝|昼|夜|放課後|授業中|休み時間)',
        # 人物关系
        r'(姉|妹|兄|弟|母|父|友達|クラスメイト|先輩|後輩|彼氏|彼女)',
        # 称呼
        r'(さん|ちゃん|くん|様|先生|先輩)',
        # 情感表达
        r'(好き|嫌い|愛してる|大切|大事|嬉しい|悲しい|辛い|辛かった)',
        # 剧情转折
        r'(でも|だって|だから|しかし|それに|実は|実はね|ねえ|あのね)',
        # 疑问句（可能包含重要信息）
        r'(？|\?)',
        # 重要动词（意愿、请求等）
        r'(したい|してほしい|して|させて|させてください)',
    ]
    
    for pattern in key_patterns:
        if re.search(pattern, stripped):
            return True
    
    return False


def build_scriptbook_prompt(scriptbook_lines: list[str], scriptbook_parsed: list[dict] = None, 
                             asr_lines: list[str] = None, similarity_threshold: float = 0.9) -> str:
    """将台本内容构建为 prompt 附加内容（强约束）
    
    台本内容具有绝对优先权，用于纠正ASR错误。
    但ASR中可能包含台本中没有的内容（如拟声词、喘息声），这些应该保留。
    
    改进：基于相似度匹配而非行号对应，只发送真正有差异的部分
    
    模式说明：
    - 关键字模式 (SCRIPTBOOK_KEYWORD_MODE=True): 只发送关键台词（角色名、重要信息、情感表达等）
    - 全量模式 (SCRIPTBOOK_FULL_MODE=True): 发送全部台本内容
    - 默认模式: 基于相似度匹配，只发送有差异的部分
    
    参数:
        scriptbook_lines: 台本台词列表（字符串）
        scriptbook_parsed: 解析后的台本内容（包含场景描述等）
        asr_lines: ASR识别结果列表（用于差异检测）
        similarity_threshold: 相似度阈值，低于此值认为有差异
    """
    if not scriptbook_lines:
        return ""
    
    # === 关键字模式（优先级最高）===
    if SCRIPTBOOK_KEYWORD_MODE:
        # 提取关键台词
        keyword_lines = []
        keyword_indices = []  # 记录关键字在原台本中的索引
        for idx, line in enumerate(scriptbook_lines):
            if line and line.strip() and is_key_dialogue_line(line):
                keyword_lines.append(line)
                keyword_indices.append(idx)
        
        if not keyword_lines:
            return ""
        
        # === DEBUG模式：ASR与关键字匹配分析 ===
        if DEBUG_MODE and asr_lines:
            print(f"    [DEBUG] ========== 关键字匹配分析 ==========")
            print(f"    [DEBUG] ASR总行数: {len(asr_lines)}")
            print(f"    [DEBUG] 关键字行数: {len(keyword_lines)}")
            
            # 匹配阈值
            MATCH_THRESHOLD = 0.75
            LOW_SIM_THRESHOLD = 0.55
            
            # 记录匹配结果
            debug_keyword_matched = []  # [(asr_idx, asr_text, kw_idx, kw_text, sim)]
            debug_keyword_low_sim = []  # [(asr_idx, asr_text, kw_idx, kw_text, sim)]
            debug_asr_no_keyword = []   # [(asr_idx, asr_text)]
            debug_keyword_not_matched = []  # [(kw_idx, kw_text)]
            
            # 已匹配的关键字索引
            matched_kw_indices = set()
            
            # 对每个ASR行，寻找最佳匹配的关键字
            for asr_idx, asr in enumerate(asr_lines, 1):
                asr_clean = asr.strip() if asr else ""
                if not asr_clean:
                    continue
                
                best_kw_idx = -1
                best_kw_text = ""
                best_sim = 0.0
                
                # 在关键字中搜索最佳匹配
                for kw_i, kw_text in enumerate(keyword_lines):
                    if kw_i in matched_kw_indices:
                        continue
                    
                    # 使用音素相似度计算
                    sim = calculate_phonetic_similarity(asr_clean, kw_text)
                    
                    if sim > best_sim:
                        best_sim = sim
                        best_kw_idx = kw_i
                        best_kw_text = kw_text
                
                # 根据匹配结果分类
                if best_sim >= MATCH_THRESHOLD:
                    debug_keyword_matched.append((asr_idx, asr_clean, best_kw_idx, best_kw_text, best_sim))
                    matched_kw_indices.add(best_kw_idx)
                elif best_sim >= LOW_SIM_THRESHOLD:
                    debug_keyword_low_sim.append((asr_idx, asr_clean, best_kw_idx, best_kw_text, best_sim))
                    matched_kw_indices.add(best_kw_idx)
                else:
                    debug_asr_no_keyword.append((asr_idx, asr_clean))
            
            # 统计未匹配的关键字
            for kw_i, kw_text in enumerate(keyword_lines):
                if kw_i not in matched_kw_indices:
                    debug_keyword_not_matched.append((kw_i, kw_text))
            
            # 打印匹配结果
            if debug_keyword_matched:
                print(f"    [DEBUG] 成功匹配 ({len(debug_keyword_matched)} 对):")
                for asr_idx, asr_text, kw_idx, kw_text, sim in debug_keyword_matched:
                    asr_display = asr_text[:50] + "..." if len(asr_text) > 50 else asr_text
                    kw_display = kw_text[:50] + "..." if len(kw_text) > 50 else kw_text
                    print(f"    [DEBUG]   {asr_idx:02d}: [ASR] {asr_display}")
                    print(f"    [DEBUG]      → [关键字] {kw_display} (相似度: {sim:.1%})")
            
            if debug_keyword_low_sim:
                print(f"    [DEBUG] 低相似度匹配 ({len(debug_keyword_low_sim)} 对):")
                for asr_idx, asr_text, kw_idx, kw_text, sim in debug_keyword_low_sim:
                    asr_display = asr_text[:50] + "..." if len(asr_text) > 50 else asr_text
                    kw_display = kw_text[:50] + "..." if len(kw_text) > 50 else kw_text
                    print(f"    [DEBUG]   {asr_idx:02d}: [ASR] {asr_display}")
                    print(f"    [DEBUG]      → [关键字] {kw_display} (相似度: {sim:.1%})")
            
            if debug_asr_no_keyword:
                print(f"    [DEBUG] ASR独有 ({len(debug_asr_no_keyword)} 行):")
                for asr_idx, asr_text in debug_asr_no_keyword:
                    asr_display = asr_text[:50] + "..." if len(asr_text) > 50 else asr_text
                    print(f"    [DEBUG]   {asr_idx:02d}: [ASR] {asr_display}")
            
            if debug_keyword_not_matched:
                print(f"    [DEBUG] 关键字未匹配 ({len(debug_keyword_not_matched)} 行):")
                for kw_idx, kw_text in debug_keyword_not_matched:
                    kw_display = kw_text[:50] + "..." if len(kw_text) > 50 else kw_text
                    print(f"    [DEBUG]   关键字{kw_idx:02d}: {kw_display}")
            
            print(f"    [DEBUG] ========== 关键字匹配分析结束 ==========")
        
        # 构建关键字模式的prompt
        lines = ["\n【台本参考（关键字模式 - 仅显示重要台词）】"]
        lines.append(f"以下是台本中的关键台词（共{len(keyword_lines)}行），请优先参考这些内容进行翻译。")
        lines.append("")
        
        for i, line in enumerate(keyword_lines[:50], 1):  # 最多显示50行
            display_line = line[:80] + "..." if len(line) > 80 else line
            lines.append(f"  {i:02d}: {display_line}")
        
        if len(keyword_lines) > 50:
            lines.append(f"  ... (共{len(keyword_lines)}行关键台词，仅显示前50行)")
        
        lines.append("")
        lines.append("【台本参考结束】\n")
        
        print(f"    [关键字模式] 从 {len(scriptbook_lines)} 行台本中提取 {len(keyword_lines)} 行关键台词")
        
        return "\n".join(lines)
    
    # === 全量模式 ===
    if SCRIPTBOOK_FULL_MODE:
        # 【优化】使用clean_script_for_translation过滤非台词内容
        # 全量模式下只发送清洗后的台词，排除场景描述、动作说明、心理描写等噪音
        from scriptbook_utils import clean_script_for_translation
        
        # 将台本行合并为文本，进行清洗
        scriptbook_text = "\n".join(scriptbook_lines)
        cleaned_text = clean_script_for_translation(scriptbook_text)
        cleaned_lines = [line.strip() for line in cleaned_text.split('\n') if line.strip()]
        
        lines = ["\n【台本参考（全量模式 - 已清洗）】"]
        lines.append(f"以下是台本中的角色台词（清洗后共{len(cleaned_lines)}行，原始{len(scriptbook_lines)}行），请严格参考台本进行翻译。")
        lines.append("注意：台本已过滤场景描述、动作说明、心理描写等非台词内容。")
        lines.append("")
        
        for i, line in enumerate(cleaned_lines, 1):
            if line and line.strip():
                display_line = line[:80] + "..." if len(line) > 80 else line
                lines.append(f"  {i:02d}: {display_line}")
        
        lines.append("")
        lines.append("【台本参考结束】\n")
        
        print(f"    [全量模式] 发送完整台本 {len(scriptbook_lines)} 行")
        
        # === DEBUG模式：全量模式匹配分析 ===
        if DEBUG_MODE and asr_lines:
            print(f"    [DEBUG] ========== 全量模式匹配分析 ==========")
            print(f"    [DEBUG] ASR总行数: {len(asr_lines)}")
            print(f"    [DEBUG] 台本总行数: {len(scriptbook_lines)}")
            
            # 匹配阈值
            MATCH_THRESHOLD = 0.75
            LOW_SIM_THRESHOLD = 0.55
            
            # 记录匹配结果
            debug_matched = []       # [(asr_idx, asr_text, sb_idx, sb_text, sim)]
            debug_low_sim = []       # [(asr_idx, asr_text, sb_idx, sb_text, sim)]
            debug_asr_only = []      # [(asr_idx, asr_text)]
            debug_sb_only = []       # [(sb_idx, sb_text)]
            
            # 已匹配的台本索引
            matched_sb_indices = set()
            
            # 对每个ASR行，寻找最佳匹配的台本行
            for asr_idx, asr in enumerate(asr_lines, 1):
                asr_clean = asr.strip() if asr else ""
                if not asr_clean:
                    continue
                
                best_sb_idx = -1
                best_sb_text = ""
                best_sim = 0.0
                
                # 在所有台本行中搜索最佳匹配
                for sb_i, sb_text in enumerate(scriptbook_lines):
                    if not sb_text or not sb_text.strip():
                        continue
                    
                    # 使用音素相似度计算
                    sim = calculate_phonetic_similarity(asr_clean, sb_text)
                    
                    if sim > best_sim:
                        best_sim = sim
                        best_sb_idx = sb_i
                        best_sb_text = sb_text
                
                # 根据匹配结果分类
                if best_sim >= MATCH_THRESHOLD:
                    debug_matched.append((asr_idx, asr_clean, best_sb_idx, best_sb_text, best_sim))
                    matched_sb_indices.add(best_sb_idx)
                elif best_sim >= LOW_SIM_THRESHOLD:
                    debug_low_sim.append((asr_idx, asr_clean, best_sb_idx, best_sb_text, best_sim))
                    matched_sb_indices.add(best_sb_idx)
                else:
                    debug_asr_only.append((asr_idx, asr_clean))
            
            # 统计未匹配的台本行
            for sb_i, sb_text in enumerate(scriptbook_lines):
                if sb_text and sb_text.strip() and sb_i not in matched_sb_indices:
                    debug_sb_only.append((sb_i, sb_text))
            
            # 打印匹配结果
            if debug_matched:
                print(f"    [DEBUG] 成功匹配 ({len(debug_matched)} 对):")
                for asr_idx, asr_text, sb_idx, sb_text, sim in debug_matched:
                    asr_display = asr_text[:50] + "..." if len(asr_text) > 50 else asr_text
                    sb_display = sb_text[:50] + "..." if len(sb_text) > 50 else sb_text
                    print(f"    [DEBUG]   {asr_idx:02d}: [ASR] {asr_display}")
                    print(f"    [DEBUG]      → [台本] {sb_display} (相似度: {sim:.1%})")
            
            if debug_low_sim:
                print(f"    [DEBUG] 低相似度匹配 ({len(debug_low_sim)} 对):")
                for asr_idx, asr_text, sb_idx, sb_text, sim in debug_low_sim:
                    asr_display = asr_text[:50] + "..." if len(asr_text) > 50 else asr_text
                    sb_display = sb_text[:50] + "..." if len(sb_text) > 50 else sb_text
                    print(f"    [DEBUG]   {asr_idx:02d}: [ASR] {asr_display}")
                    print(f"    [DEBUG]      → [台本] {sb_display} (相似度: {sim:.1%})")
            
            if debug_asr_only:
                print(f"    [DEBUG] ASR独有 ({len(debug_asr_only)} 行):")
                for asr_idx, asr_text in debug_asr_only:
                    asr_display = asr_text[:50] + "..." if len(asr_text) > 50 else asr_text
                    print(f"    [DEBUG]   {asr_idx:02d}: [ASR] {asr_display}")
            
            if debug_sb_only:
                print(f"    [DEBUG] 台本独有未匹配 ({len(debug_sb_only)} 行):")
                for sb_idx, sb_text in debug_sb_only:
                    sb_display = sb_text[:50] + "..." if len(sb_text) > 50 else sb_text
                    print(f"    [DEBUG]   台本行{sb_idx:02d}: {sb_display}")
            
            print(f"    [DEBUG] ========== 全量模式匹配分析结束 ==========")
        
        return "\n".join(lines)
    
    # === 统计计数 ===
    stats = {
        "asr_total": len(asr_lines) if asr_lines else 0,
        "sb_total": len(scriptbook_lines),
        "sb_dialogue_total": 0,  # 台本中的纯台词行数（过滤后）
        "asr_non_empty": 0,
        "sb_non_empty": 0,
        "matched_count": 0,    # ASR与台本成功匹配的行数
        "low_similarity": 0,   # 低相似度匹配（需要发送台本纠正）
        "asr_unmatched": 0,    # ASR未匹配到台本（即兴发挥/拟声词，保留ASR）
        "sb_unmatched": 0,     # 台本未匹配到ASR（可能漏识别）
    }
    
    # DEBUG模式下的详细记录
    debug_matched_lines = []  # [(asr_idx, asr_text, sb_text, sim)]
    debug_asr_only_lines = []  # [(asr_idx, asr_text)]
    debug_sb_only_lines = []  # [(sb_idx, sb_text)]
    
    # === 核心改进1：过滤台本中的非台词行 ===
    # 台本中有很多非台词内容（场景描述、动作说明、心理描写等）
    # 这些内容不会被ASR识别，应该排除在匹配之外
    def is_valid_dialogue_line(line: str) -> bool:
        """判断是否为有效的台词行（排除非台词内容）
        
        排除规则：
        1. 纯说明文字（如"台本初稿です。"、"-------------------------"）
        2. 场景描述（如"ドアを開けると真っ暗の部屋で兄が寝ていた。"）
        3. 动作说明（如"布団の中に潜り込む"、"左耳２０ｃｍ"）
        4. 心理描写（以"妹（"或"兄（"开头的内心独白）
        5. 纯拟声行但没有实际台词内容
        6. 音效标记（SE:xxx）
        7. 位置标记（【右・中】）
        
        保留规则：
        1. 包含口语对话内容（有完整的句子结构）
        2. 包含角色名标记（【まどか】等）后的台词
        3. 即使包含拟声词但有实际台词内容的行
        """
        if not line or not line.strip():
            return False
        
        stripped = line.strip()
        
        # 排除规则1：纯说明文字/分隔线
        if re.match(r'^[-ー─＝]+$', stripped):
            return False
        if '台本' in stripped and len(stripped) < 30:
            return False
        if re.match(r'^台本初稿|^セリフ初稿|^仮台本', stripped):
            return False
        
        # 排除规则2：场景描述（包含明显的描述性动词）
        if re.match(r'^(ドア|部屋|窓|廊下|階段)', stripped):
            return False
        if re.search(r'(寝ていた|座っていた|立っていた|歩いて|走って)', stripped) and not re.search(r'[。？！]', stripped):
            return False
        
        # 排除规则3：动作说明（短句，没有完整句子结构）
        if re.match(r'^(布団|左耳|右耳|フェラ|マイク)', stripped):
            return False
        if re.match(r'^[左右中正遠近・→]+$', stripped):
            return False
        if re.match(r'^【[左右中正遠近・→]+】', stripped):
            return False
        if re.match(r'^SE[:：]', stripped, re.IGNORECASE):
            return False
        
        # 排除规则4：心理描写（以"妹（"或"兄（"开头）
        if re.match(r'^[妹兄姉弟母父][（\(]', stripped):
            return False
        # 心理描写也可能在行中间，但如果有完整句子就保留
        if re.search(r'[妹兄姉弟母父][（\(].*[）\)]', stripped) and not re.search(r'[。？！]', stripped):
            return False
        
        # 排除规则5：纯拟声行（没有实际台词内容）
        # 拟声行特征：大量假名/拟声词，但没有完整句子
        if re.match(r'^[ぁぃぅぇぉあいうえおんっゃゅょゎー…～♡♪\s]+$', stripped):
            return False
        # 包含拟声词但有完整句子结构的，保留（如"んぁぁ…ごめんなさい…。")
        if re.search(r'[。？！]', stripped):
            return True
        
        # 排除规则6：纯数字/页码
        if re.match(r'^[０-９0-9]+\s*$', stripped):
            return False
        
        # 排除规则7：纯角色名标记（无台词）
        if re.match(r'^【[^】]+】\s*$', stripped):
            return False
        
        # 保留规则：包含口语对话特征
        # 1. 包含句末标点
        if re.search(r'[。？！…]', stripped):
            return True
        # 2. 包含称呼（お兄さん、お姉さん等）
        if re.search(r'(お兄さん|お姉さん|兄さん|姉さん|お母さん|お父さん)', stripped):
            return True
        # 3. 包含疑问词/回答
        if re.search(r'(ですか|ますか|なに|どう|はい|いいえ)', stripped):
            return True
        # 4. 包含情感表达
        if re.search(r'(ごめん|ありがとう|好き|嫌い|嬉しい|悲しい)', stripped):
            return True
        # 5. 长度适中且包含日文字符（可能是台词）
        if len(stripped) >= 5 and re.search(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', stripped):
            # 但排除看起来像描述的句子
            if not re.search(r'(説明|指定|効果音|位置|距離)', stripped):
                return True
        
        return False
    
    # === 核心改进：全局序列对齐算法（Needleman-Wunsch变体） ===
    # 解决问题：
    # 1. ASR与台本的行号偏差会累积（ASR合并/拆分导致）
    # 2. 支持1-N、N-1、1-1映射关系
    # 3. 自动处理跳序匹配问题
    diff_lines = []  # [(ASR索引, ASR内容, 台本内容, 相似度, 台本索引)]
    
    def normalize_for_compare(text: str) -> str:
        """预处理文本用于比较：统一标点、去除语气词"""
        # 统一标点符号
        text = text.replace('…', '。').replace('、', '，').replace('？', '?').replace('！', '!')
        text = text.replace('♪', '').replace('♡', '').replace('ｗ', '').replace('w', '').replace('W', '')
        text = text.replace('「', '').replace('」', '').replace('『', '').replace('』', '')
        text = text.replace('ふふ', '').replace('ふふっ', '').replace('笑', '')
        text = text.replace('ん', '').replace('っ', '').replace('～', '').replace('ー', '')
        # 去除多余空格
        text = re.sub(r'\s+', '', text)
        return text.strip()
    
    def calculate_strict_similarity(s1: str, s2: str) -> float:
        """计算严格相似度（基于归一化编辑距离 + 长度惩罚）
        
        【核心改进】引入长度比例惩罚，解决短句误匹配问题
        
        问题案例：
        - ASR "はい。" 与台本 "申し訳ないけれど、後で死ぬほど詫びよう。" 
          原算法：编辑距离相似度 = 72%（因为 "はい" 在长句中出现）
          新算法：长度差异惩罚后 = 30%（长度比 2/25 = 8%，严重惩罚）
        
        改进：
        1. 编辑距离相似度（基础分）
        2. 长度比例惩罚（长度差异过大则大幅降分）
        3. 短句特殊处理（ASR短句<5字符时，不允许匹配长句）
        """
        if not s1 or not s2:
            return 0.0
        
        # 预处理
        n1 = normalize_for_compare(s1)
        n2 = normalize_for_compare(s2)
        
        if not n1 or not n2:
            # 如果预处理后为空，使用原始文本
            n1, n2 = s1, s2
        
        # 如果完全相同
        if n1 == n2:
            return 1.0
        
        len1, len2 = len(n1), len(n2)
        
        # === 新增：短句保护 ===
        # 如果ASR是短句（<5字符），不允许匹配到长句（>15字符）
        # 这样可以避免 "はい" 匹配到长句
        min_len = min(len1, len2)
        max_len_val = max(len1, len2)
        
        if min_len < 5 and max_len_val > 15:
            # 短句匹配长句，直接返回低分
            # 只保留子串匹配的可能性
            if n1 in n2 or n2 in n1:
                # 子串匹配，给予中等分数，但不超过 50%
                return min_len / max_len_val * 0.5
            else:
                return 0.0
        
        # 计算编辑距离
        def levenshtein(a: str, b: str) -> int:
            m, n = len(a), len(b)
            if m == 0: return n
            if n == 0: return m
            
            prev = list(range(n + 1))
            curr = [0] * (n + 1)
            
            for i in range(1, m + 1):
                curr[0] = i
                for j in range(1, n + 1):
                    cost = 0 if a[i-1] == b[j-1] else 1
                    curr[j] = min(curr[j-1] + 1, prev[j] + 1, prev[j-1] + cost)
                prev, curr = curr, prev
            
            return prev[n]
        
        dist = levenshtein(n1, n2)
        edit_sim = 1.0 - (dist / max_len_val) if max_len_val > 0 else 0.0
        
        # === 新增：长度比例惩罚 ===
        # 长度比例 = min_len / max_len
        # 如果比例过低（如 2/25 = 8%），说明长度差异过大，需要惩罚
        len_ratio = min_len / max_len_val if max_len_val > 0 else 1.0
        
        # 惩罚公式：
        # - len_ratio >= 0.7: 不惩罚（长度接近）
        # - len_ratio >= 0.5: 轻度惩罚（乘以 0.8）
        # - len_ratio >= 0.3: 中度惩罚（乘以 0.5）
        # - len_ratio < 0.3: 重度惩罚（乘以 0.2）
        if len_ratio >= 0.7:
            len_penalty = 1.0
        elif len_ratio >= 0.5:
            len_penalty = 0.8
        elif len_ratio >= 0.3:
            len_penalty = 0.5
        else:
            len_penalty = 0.2
        
        # 最终相似度 = 编辑距离相似度 × 长度惩罚
        final_sim = edit_sim * len_penalty
        
        # === 新增：子串匹配加分 ===
        # 如果一个字符串是另一个的子串，且长度比例合理，给予加分
        if n1 in n2:
            coverage = len1 / len2
            # 子串覆盖率 >= 50%，加分
            if coverage >= 0.5:
                final_sim = max(final_sim, coverage * 0.9)
        elif n2 in n1:
            coverage = len2 / len1
            if coverage >= 0.5:
                final_sim = max(final_sim, coverage * 0.9)
        
        return final_sim
    
    if asr_lines:
        # 预处理台本：清理并建立索引
        sb_cleaned = [(idx, sb.strip() if sb else "") for idx, sb in enumerate(scriptbook_lines)]
        
        # === 核心改进：过滤台本中的非台词行 ===
        # 只保留有效的台词行参与匹配，排除场景描述、动作说明等非台词内容
        sb_valid = [(idx, text) for idx, text in sb_cleaned if text and is_valid_dialogue_line(text)]
        
        # 统计
        stats["sb_dialogue_total"] = len(sb_valid)
        stats["asr_non_empty"] = sum(1 for asr in asr_lines if asr and asr.strip())
        stats["sb_non_empty"] = len([idx for idx, text in sb_cleaned if text])
        
        # 打印过滤结果
        if stats["sb_non_empty"] > stats["sb_dialogue_total"]:
            filtered_count = stats["sb_non_empty"] - stats["sb_dialogue_total"]
            print(f"    [台本过滤] 已过滤 {filtered_count} 行非台词内容（{stats['sb_non_empty']}行 → {stats['sb_dialogue_total']}行有效台词）")
        
        # 已匹配的台本索引（避免重复匹配）
        matched_sb_indices = set()
        
        # === 使用滑动窗口按顺序对齐 ===
        # 参数设置（扩大窗口以提高匹配准确率）
        WINDOW_SIZE = 15  # 向前看15行（从5行扩大到15行，跨越非台词内容）
        STRICT_THRESHOLD = 0.75  # 严格匹配阈值（从0.85降低到0.75，更宽松）
        LOOSE_THRESHOLD = 0.55  # 宽松匹配阈值（从0.6降低到0.55，更宽松）
        
        # 台本索引指针（按顺序推进）
        sb_ptr = 0
        
        for asr_idx, asr in enumerate(asr_lines, 1):
            asr_clean = asr.strip() if asr else ""
            if not asr_clean:
                continue
            
            # 在窗口范围内寻找最佳匹配
            best_sb_idx = -1
            best_sb_text = ""
            best_sim = 0.0
            best_strict_sim = 0.0
            
            # 搜索窗口：从当前sb_ptr开始，向后看WINDOW_SIZE行
            search_start = sb_ptr
            search_end = min(search_start + WINDOW_SIZE, len(sb_valid))
            
            for i in range(search_start, search_end):
                sb_idx, sb_text = sb_valid[i]
                
                # 跳过已匹配的台本行
                if sb_idx in matched_sb_indices:
                    continue
                
                # 计算严格相似度
                strict_sim = calculate_strict_similarity(asr_clean, sb_text)
                
                # 也计算原始相似度作为参考
                raw_sim = calculate_phonetic_similarity(asr_clean, sb_text)
                
                # 综合相似度（取两者较高值）
                combined_sim = max(strict_sim, raw_sim * 0.8)
                
                if combined_sim > best_sim:
                    best_sim = combined_sim
                    best_strict_sim = strict_sim
                    best_sb_idx = sb_idx
                    best_sb_text = sb_text
            
            # 根据匹配结果处理
            if best_sim >= STRICT_THRESHOLD:
                # 高相似度匹配：ASR与台本一致，无需发送
                matched_sb_indices.add(best_sb_idx)
                stats["matched_count"] += 1
                if DEBUG_MODE:
                    debug_matched_lines.append((asr_idx, asr_clean, best_sb_text, best_sim))
                # 推进指针到匹配位置的下一行
                sb_ptr = next((i for i, (idx, _) in enumerate(sb_valid) if idx == best_sb_idx), sb_ptr) + 1
            elif best_sim >= LOOSE_THRESHOLD:
                # 低相似度但有潜在匹配：发送台本纠正ASR
                matched_sb_indices.add(best_sb_idx)
                diff_lines.append((asr_idx, asr_clean, best_sb_text, best_sim, best_sb_idx))
                stats["low_similarity"] += 1
                # 推进指针
                sb_ptr = next((i for i, (idx, _) in enumerate(sb_valid) if idx == best_sb_idx), sb_ptr) + 1
            else:
                # 无匹配：ASR独有内容（拟声词/即兴发挥），保留ASR不发送台本
                stats["asr_unmatched"] += 1
                if DEBUG_MODE:
                    debug_asr_only_lines.append((asr_idx, asr_clean))
                # 不推进指针，让下一行ASR有机会匹配当前台本行
        
        # 统计台本未匹配行（ASR可能漏识别）
        stats["sb_unmatched"] = len(sb_valid) - len(matched_sb_indices)
        
        # 收集台本独有行
        if DEBUG_MODE:
            for sb_idx, sb_text in sb_valid:
                if sb_idx not in matched_sb_indices:
                    debug_sb_only_lines.append((sb_idx, sb_text))
        
        # === 打印统计信息 ===
        print(f"    [台本统计] ASR {stats['asr_total']} 行(非空{stats['asr_non_empty']}) | 台本 {stats['sb_total']} 行(非空{stats['sb_non_empty']})")
        print(f"    [匹配结果] 成功匹配 {stats['matched_count']} 行 | 低相似度 {stats['low_similarity']} 行(需发) | ASR独有 {stats['asr_unmatched']} 行(保留) | 台本独有 {stats['sb_unmatched']} 行(可能漏识别)")
        
        # DEBUG模式下打印所有匹配详情
        if DEBUG_MODE:
            print(f"    [DEBUG] ========== 匹配详情 ==========")
            
            # 打印成功匹配的行
            if debug_matched_lines:
                print(f"    [DEBUG] 成功匹配 ({len(debug_matched_lines)} 行):")
                for asr_idx, asr_text, sb_text, sim in debug_matched_lines:
                    asr_display = asr_text[:50] + "..." if len(asr_text) > 50 else asr_text
                    sb_display = sb_text[:50] + "..." if len(sb_text) > 50 else sb_text
                    print(f"    [DEBUG]   {asr_idx:02d}: [ASR] {asr_display}")
                    print(f"    [DEBUG]      → [台本] {sb_display} (相似度: {sim:.1%})")
            
            # 打印低相似度的行
            if diff_lines:
                print(f"    [DEBUG] 低相似度需发送 ({len(diff_lines)} 行):")
                for asr_idx, asr_text, sb_text, sim, sb_idx in diff_lines:
                    asr_display = asr_text[:50] + "..." if len(asr_text) > 50 else asr_text
                    sb_display = sb_text[:50] + "..." if len(sb_text) > 50 else sb_text
                    print(f"    [DEBUG]   {asr_idx:02d}: [ASR] {asr_display}")
                    print(f"    [DEBUG]      → [台本] {sb_display} (相似度: {sim:.1%})")
            
            # 打印ASR独有的行
            if debug_asr_only_lines:
                print(f"    [DEBUG] ASR独有保留 ({len(debug_asr_only_lines)} 行):")
                for asr_idx, asr_text in debug_asr_only_lines:
                    asr_display = asr_text[:50] + "..." if len(asr_text) > 50 else asr_text
                    print(f"    [DEBUG]   {asr_idx:02d}: [ASR] {asr_display}")
            
            # 打印台本独有的行
            if debug_sb_only_lines:
                print(f"    [DEBUG] 台本独有未匹配 ({len(debug_sb_only_lines)} 行):")
                for sb_idx, sb_text in debug_sb_only_lines:
                    sb_display = sb_text[:50] + "..." if len(sb_text) > 50 else sb_text
                    print(f"    [DEBUG]   台本行{sb_idx}: {sb_display}")
            
            print(f"    [DEBUG] ========== 匹配详情结束 ==========")
        
        # 计算实际发送比例（相对于ASR非空行）
        need_send_count = stats["low_similarity"]
        if need_send_count > 0:
            send_ratio = need_send_count / max(stats["asr_non_empty"], 1)
            print(f"    [发送比例] 需发送 {need_send_count} 行 ({send_ratio:.1%}) 台本纠正ASR错误")
    
    # 构建prompt
    lines = ["\n【台本参考（仅显示ASR与台本有差异的部分）】"]
    
    if diff_lines:
        lines.append(f"以下是ASR识别结果与台本不一致的部分（共{len(diff_lines)}处差异）。")
        lines.append("**请优先使用台本内容纠正ASR错误**。")
        lines.append("")
        
        for asr_idx, asr_text, sb_text, sim, sb_idx in diff_lines:
            # 显示前60个字符
            asr_display = asr_text[:60] + "..." if len(asr_text) > 60 else asr_text
            sb_display = sb_text[:60] + "..." if len(sb_text) > 60 else sb_text
            
            if asr_text:
                lines.append(f"  {asr_idx:02d}: [ASR] {asr_display}")
                lines.append(f"       [台本] {sb_display}")
                if sim > 0:
                    lines.append(f"       [相似度] {sim:.0%}")
            else:
                lines.append(f"  {asr_idx:02d}: [台本独有] {sb_display}")
            lines.append("")
    else:
        # 无差异或无ASR，显示简化提示
        lines.append("ASR识别结果与台本高度一致，无需参考台本纠正。")
        lines.append("请直接翻译ASR内容。")
        lines.append("")
    
    lines.append("【台本参考结束】\n")
    
    return "\n".join(lines)


def extract_main_rj_number(folder_name: str) -> "str | None":
    """从文件夹名提取主 RJ 号
    
    规则：
    1. 匹配 RJ + 8位数字（支持大小写）
    2. 如果有多个 RJ 号（空格/逗号分隔），取第一个作为主 RJ 号
    
    示例：
    - "RJ01035777 RJ01500932" → "RJ01035777" (空格分隔，第一个是主作品)
    - "RJ01271649,RJ01497252" → "RJ01271649" (逗号分隔)
    - "RJ01235104 [社团名] 作品名" → "RJ01235104"
    - "RJ01005150" → "RJ01005150"
    
    返回: 主 RJ 号（大写）或 None
    """
    # RJ 号格式：RJ + 8位数字
    RJ_PATTERN = r'(RJ\d{8})'
    matches = re.findall(RJ_PATTERN, folder_name, re.IGNORECASE)
    if matches:
        # 返回第一个 RJ 号（主作品），统一大写
        return matches[0].upper()
    return None


def find_rj_work_root(path: Path) -> "tuple[Path, str] | tuple[None, None]":
    """从路径向上查找 RJ 作品根目录
    
    参数:
        path: 起始路径（通常是 .lrc 文件所在目录或文件路径本身）
    
    返回: (作品根目录, 主RJ号) 或 (None, None)
    
    示例：
    - E:\奥术\RJ01035777 RJ01500932\02_MP3\ -> (E:\奥术\RJ01035777 RJ01500932, "RJ01035777")
    - E:\奥术\无RJ文件夹\ -> (None, None)
    """
    current = path if path.is_dir() else path.parent
    
    while current:
        rj = extract_main_rj_number(current.name)
        if rj:
            return current, rj
        # 到达根目录时停止
        if current.parent == current:
            break
        current = current.parent
    
    return None, None


def find_scriptbooks_in_dir(work_dir: Path, recursive_for_rj: bool = True) -> list[Path]:
    """查找目录中的所有台本文件
    
    参数:
        work_dir: 工作目录
        recursive_for_rj: 如果检测到 RJ 作品目录，是否递归扫描所有子文件夹
    
    返回: 台本文件路径列表
    
    改进：
    - 如果目录包含 RJ 号，递归扫描所有子文件夹（支持台本在深层子目录）
    - 如果目录不包含 RJ 号，只扫描当前目录层级
    """
    scriptbook_files = []
    
    # 检查是否为 RJ 作品目录
    rj_root, rj_number = find_rj_work_root(work_dir)
    
    if rj_root and recursive_for_rj:
        # RJ 作品目录：递归扫描所有子文件夹
        print(f"    [RJ检测] 发现 RJ 作品: {rj_number}")
        print(f"    [RJ检测] 作品根目录: {rj_root}")
        print(f"    [台本扫描] 递归扫描所有子文件夹...")
        
        # 使用 rglob 递归遍历
        for fpath in rj_root.rglob('*'):
            if fpath.is_file() and is_scriptbook_file(fpath):
                scriptbook_files.append(fpath)
    else:
        # 非 RJ 目录：只扫描当前目录层级
        for fpath in _walk_files(work_dir):
            if is_scriptbook_file(fpath):
                scriptbook_files.append(fpath)
    
    return scriptbook_files


def extract_track_number_from_filename(filename: str) -> "int | None":
    """从文件名中提取音轨编号
    
    支持格式：
    - "Track-01" → 1
    - "トラック1" → 1
    - "１" → 1
    - "1）" → 1
    - "01." → 1
    - "RJ12345_１" → 1（文件名末尾的全角数字）
    - "RJ12345-1" → 1（分隔符后的数字）
    - "RJ12345_01" → 1（分隔符后的数字）
    - "00" → 0（纯数字文件名）
    - "01" → 1（纯数字文件名）
    - "07" → 7（纯数字文件名）
    """
    import unicodedata
    
    # 优先级1：Track + 数字（如 Track-01, Track01）
    match = re.search(r'[Tt]rack[-_]?(\d+)', filename)
    if match:
        return int(match.group(1))
    
    # 优先级2：トラック + 数字（如 トラック1）
    match = re.search(r'トラック(\d+)', filename)
    if match:
        return int(match.group(1))
    
    # 优先级3：tr + 数字（如 tr01, TR01）
    match = re.search(r'tr(\d+)', filename, re.IGNORECASE)
    if match:
        return int(match.group(1))
    
    # 优先级4：纯数字文件名（如 00、01、07、１、２）
    # 这是最常见的台本命名方式
    if re.match(r'^[０-９0-9]+$', filename):
        num_str = filename
        # 转换全角数字为半角
        num_str = ''.join(
            chr(ord(c) - 0xFEE0) if '０' <= c <= '９' else c
            for c in num_str
        )
        return int(num_str)
    
    # 优先级5：文件名末尾的全角数字（如 RJ12345_１ → 1）
    match = re.search(r'[０-９]+$', filename)
    if match:
        num_str = match.group(0)
        # 转换全角数字为半角
        num_str = ''.join(
            chr(ord(c) - 0xFEE0) if '０' <= c <= '９' else c
            for c in num_str
        )
        return int(num_str)
    
    # 优先级6：分隔符后的数字（如 RJ12345_01, RJ12345-1）
    match = re.search(r'[_\-](\d+)$', filename)
    if match:
        return int(match.group(1))
    
    # 优先级7：分隔符后的全角数字（如 RJ12345_１）
    match = re.search(r'[_\-]([０-９]+)$', filename)
    if match:
        num_str = match.group(1)
        # 转换全角数字为半角
        num_str = ''.join(
            chr(ord(c) - 0xFEE0) if '０' <= c <= '９' else c
            for c in num_str
        )
        return int(num_str)
    
    # 优先级8：开头是全角数字（如 １、１トラック）
    match = re.match(r'^[０-９]+', filename)
    if match:
        num_str = match.group(0)
        # 转换全角数字为半角
        num_str = ''.join(
            chr(ord(c) - 0xFEE0) if '０' <= c <= '９' else c
            for c in num_str
        )
        return int(num_str)
    
    # 优先级9：开头是数字后跟括号或点（如 1）, 01.）
    match = re.match(r'^(\d+)[)）\.\s]', filename)
    if match:
        return int(match.group(1))
    
    return None


def build_track_scriptbook_map(work_dir: Path, llm_client=None, model: str = "gemini-2.0-flash") -> "tuple[dict[int, list[str]], dict[int, Path], set[str], dict[int, tuple[int, int]]]":
    """构建音轨到台本的映射
    
    扫描目录中的台本文件，解析并按音轨编号组织。
    
    【新增 LLM辅助】
    当只有单个台本文件且正则无法正确划分音轨时，
    调用LLM分析台本结构，辅助音轨划分。
    
    参数:
        work_dir: 作品目录
        llm_client: OpenAI兼容的LLM客户端（可选）
        model: 模型名称（默认 gemini-2.0-flash）
    
    返回: (台词映射, 台本文件路径映射, 角色名集合, 行号范围映射)
        - 台词映射: {音轨编号: [台词列表]}
        - 台本文件路径映射: {音轨编号: 台本文件绝对路径}
        - 角色名集合: 台本中出现的所有角色名（用于自动添加到术语表）
        - 行号范围映射: {音轨编号: (起始行号, 结束行号)} 在清洗后TXT中的位置
    """
    track_map = {}
    track_file_map = {}  # 新增：记录每个音轨对应的台本文件路径
    character_names = set()  # 新增：收集所有角色名
    track_line_ranges = {}  # 新增：记录每个音轨在清洗后TXT中的行号范围
    scriptbook_files = find_scriptbooks_in_dir(work_dir)
    
    if not scriptbook_files:
        return track_map, track_file_map, character_names, track_line_ranges
    
    print(f"    [台本扫描] 发现 {len(scriptbook_files)} 个候选文件:")
    for sb_path in scriptbook_files:
        print(f"      - {sb_path.absolute()}")
    
    # 【新增】使用LLM确认哪些文件是真正的台本文件
    if llm_client is not None:
        from scriptbook_utils import llm_identify_scriptbook_files
        
        # 将路径转换为字符串列表
        file_paths = [str(sb_path) for sb_path in scriptbook_files]
        
        print(f"    [LLM确认] 正在让LLM确认台本文件...")
        confirmed_paths = llm_identify_scriptbook_files(file_paths, llm_client, model)
        
        # 将确认的路径转换回Path对象
        scriptbook_files = [Path(p) for p in confirmed_paths]
        
        if not scriptbook_files:
            print(f"    [LLM确认] LLM判断没有台本文件，跳过台本分析")
            return track_map, track_file_map, character_names, track_line_ranges
        
        print(f"    [LLM确认] 确认 {len(scriptbook_files)} 个台本文件")
    
    # LLM客户端可用时使用LLM分析音轨结构
    use_llm_analysis = llm_client is not None
    
    for sb_path in scriptbook_files:
        # 打印台本绝对路径
        abs_path = sb_path.resolve()
        print(f"    [台本读取] 正在读取: {abs_path}")
        
        # 提取音轨编号
        track_num = extract_track_number_from_filename(sb_path.stem)
        
        # 读取台本内容
        try:
            if sb_path.suffix.lower() == '.pdf':
                # PDF文件：使用清洗模式提取
                from scriptbook_utils import extract_pdf_text as extract_pdf_cleaned
                content = extract_pdf_cleaned(sb_path, clean_for_translation=True)
                raw_content = extract_pdf_text(sb_path)  # 原始提取用于调试
            else:
                with open(sb_path, 'r', encoding='utf-8') as f:
                    raw_content = f.read()
                # TXT文件也需要清洗
                from scriptbook_utils import clean_script_for_translation
                content = clean_script_for_translation(raw_content)
        except Exception as e:
            print(f"    [台本] 读取失败: {sb_path.name} - {e}")
            continue
        
        if not content.strip():
            continue
        
        # 解析台本，提取纯台词（带角色名）
        parsed = parse_scriptbook_content(content)
        
        # 按音轨分组
        track_dialogues = {}  # {音轨编号: [台词列表]}
        current_track = 0  # 默认音轨编号
        current_dialogues = []
        
        for p in parsed:
            if p["type"] == "track":
                # 遇到音轨标记，保存之前的台词并开始新音轨
                if current_dialogues:
                    if current_track not in track_dialogues:
                        track_dialogues[current_track] = []
                    track_dialogues[current_track].extend(current_dialogues)
                current_track += 1
                current_dialogues = []
            elif p["type"] == "dialogue" and p["text"].strip():
                char = p.get("character", "")
                if char:
                    # 有角色名：格式为 "角色名：台词"
                    current_dialogues.append(f"{char}：{p['text']}")
                    # 收集角色名（用于自动添加到术语表）
                    character_names.add(char)
                else:
                    # 无角色名：只有台词
                    current_dialogues.append(p["text"])
        
        # 保存最后一个音轨的台词
        if current_dialogues:
            if current_track not in track_dialogues:
                track_dialogues[current_track] = []
            track_dialogues[current_track].extend(current_dialogues)
        
        # 【新增 LLM辅助】
        # 【修复】如果文件名已经能提取到音轨编号，跳过 LLM 分析
        # 只有当文件名无法确定音轨编号时，才需要 LLM 分析音轨结构
        if use_llm_analysis and track_num is None:
            print(f"    [LLM辅助] 文件名无法确定音轨编号，正则识别出 {len(track_dialogues)} 个音轨，使用LLM确认/重新划分...")
            
            from scriptbook_utils import llm_analyze_track_structure, split_scriptbook_by_llm_markers
            
            # === DEBUG模式：打印清洗后的台本行号信息 ===
            if DEBUG_MODE:
                content_lines = content.split('\n')
                print(f"    [DEBUG] 清洗后台本总行数: {len(content_lines)}")
                print(f"    [DEBUG] 清洗后前10行预览:")
                for i, line in enumerate(content_lines[:10], 1):
                    preview = line[:60] + "..." if len(line) > 60 else line
                    print(f"    [DEBUG]   行{i:03d}: {preview}")
            
            # 调用LLM分析音轨结构（传入DEBUG_MODE以打印发送给LLM的样本）
            track_markers = llm_analyze_track_structure(content, llm_client, model, debug_mode=DEBUG_MODE)
            
            if track_markers and len(track_markers) > 1:
                print(f"    [LLM辅助] LLM识别出 {len(track_markers)} 个音轨边界:")
                
                # 在清洗后的内容中查找每个边界标记的行号
                content_lines = content.split('\n')
                marker_line_info = []  # [(track_num, marker, line_num), ...]
                
                for tnum, marker in track_markers:
                    marker_clean = marker.strip()
                    found_line = -1
                    for i, line in enumerate(content_lines):
                        if marker_clean in line.strip() or line.strip() == marker_clean:
                            found_line = i + 1  # 行号从1开始
                            break
                    
                    marker_line_info.append((tnum, marker, found_line))
                    print(f"      - 音轨{tnum}: 标记 \"{marker[:40]}...\" 在第{found_line}行")
                
                # 根据LLM返回的标记重新划分台本
                track_contents = split_scriptbook_by_llm_markers(content, track_markers)
                
                # === 导出清洗后的台本内容 ===
                # 先计算所有音轨的行号范围（用于导出）
                sorted_markers = sorted(marker_line_info, key=lambda x: x[2] if x[2] > 0 else 999999)
                track_ranges_for_export = {}
                for idx, (tnum, marker, start_line) in enumerate(sorted_markers):
                    if idx + 1 < len(sorted_markers):
                        end_line = sorted_markers[idx + 1][2] - 1
                    else:
                        end_line = len(content_lines)
                    track_ranges_for_export[tnum] = (start_line, end_line)
                
                # === 导出清洗后的台本文件（_cleaned.txt）===
                # 文件名处理：防止重复清洗导致 _cleaned_cleaned.txt
                sb_stem = sb_path.stem
                if sb_stem.endswith('_cleaned'):
                    # 已经是清洗后的文件，不再添加 _cleaned
                    cleaned_txt_path = sb_path
                else:
                    cleaned_txt_path = sb_path.with_name(f"{sb_stem}_cleaned.txt")
                
                try:
                    # 写入清洗后的台本内容（纯文本，不带行号）
                    with open(cleaned_txt_path, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(content_lines))
                    print(f"    [导出] 清洗后台本: {cleaned_txt_path.name}")
                except Exception as e:
                    print(f"    [导出失败] 清洗后台本保存失败: {e}")
                
                # === 导出音轨行号范围信息（.cleaned_scriptbook.txt）===
                # 只保存音轨划分信息，不含台本内容
                cleaned_scriptbook_path = work_dir / ".cleaned_scriptbook.txt"
                try:
                    with open(cleaned_scriptbook_path, 'w', encoding='utf-8') as f:
                        f.write(f"=== 音轨行号范围 ===\n")
                        f.write(f"原始台本: {sb_path.name}\n")
                        f.write(f"清洗后台本: {cleaned_txt_path.name}\n")
                        f.write(f"总行数: {len(content_lines)}\n")
                        f.write(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                        f.write("=" * 60 + "\n\n")
                        
                        # 写入每个音轨的行号范围
                        for tnum in sorted(track_ranges_for_export.keys()):
                            start_line, end_line = track_ranges_for_export[tnum]
                            line_count = end_line - start_line + 1
                            # 查找对应的标记
                            marker_text = ""
                            for mn, mm, ml in marker_line_info:
                                if mn == tnum:
                                    marker_text = mm[:50] + "..." if len(mm) > 50 else mm
                                    break
                            f.write(f"【音轨{tnum:02d}】 第{start_line:04d}-{end_line:04d}行 (共{line_count}行)\n")
                            f.write(f"  标记: {marker_text}\n")
                            f.write(f"  内容预览:\n")
                            # 写入该音轨的前5行内容预览
                            for ln in range(start_line, min(start_line + 5, end_line + 1)):
                                if ln > 0 and ln <= len(content_lines):
                                    preview = content_lines[ln - 1][:60] + "..." if len(content_lines[ln - 1]) > 60 else content_lines[ln - 1]
                                    f.write(f"    {ln:04d}: {preview}\n")
                            f.write("\n")
                        
                        f.write("=" * 60 + "\n")
                        f.write("=== 音轨行号范围结束 ===\n")
                        
                    print(f"    [导出] 音轨行号范围: {cleaned_scriptbook_path.name}")
                    print(f"    [导出] 包含 {len(track_ranges_for_export)} 个音轨的行号范围")
                except Exception as e:
                    print(f"    [导出失败] 音轨行号范围保存失败: {e}")
                
                # === DEBUG模式：打印每个音轨的行号范围 ===
                if DEBUG_MODE:
                    print(f"    [DEBUG] ========== 音轨行号范围 ==========")
                
                # 对每个音轨内容重新解析
                track_dialogues = {}
                track_line_ranges = {}  # 新增：记录每个音轨在清洗后TXT中的行号范围
                
                # 计算每个音轨的行号范围
                sorted_markers = sorted(marker_line_info, key=lambda x: x[2] if x[2] > 0 else 999999)
                for idx, (tnum, marker, start_line) in enumerate(sorted_markers):
                    # 计算结束行号
                    if idx + 1 < len(sorted_markers):
                        end_line = sorted_markers[idx + 1][2] - 1
                    else:
                        end_line = len(content_lines)
                    
                    # 保存行号范围
                    track_line_ranges[tnum] = (start_line, end_line)
                
                for tnum, track_content in track_contents.items():
                    track_parsed = parse_scriptbook_content(track_content)
                    dialogues = []
                    for p in track_parsed:
                        if p["type"] == "dialogue" and p["text"].strip():
                            char = p.get("character", "")
                            if char:
                                dialogues.append(f"{char}：{p['text']}")
                                character_names.add(char)
                            else:
                                dialogues.append(p["text"])
                    track_dialogues[tnum] = dialogues
                    
                    # 获取行号范围
                    start_line, end_line = track_line_ranges.get(tnum, (0, 0))
                    
                    # 打印信息
                    if DEBUG_MODE:
                        print(f"    [DEBUG] 音轨{tnum:02d}: 第{start_line}-{end_line}行 ({end_line - start_line + 1}行) → {len(dialogues)}行台词")
                        # 打印该音轨的前3行内容预览
                        track_lines = track_content.split('\n')[:3]
                        for i, line in enumerate(track_lines, start_line):
                            preview = line[:50] + "..." if len(line) > 50 else line
                            print(f"    [DEBUG]   行{i:03d}: {preview}")
                    
                    print(f"    [LLM辅助] 音轨{tnum:02d}: 第{start_line}-{end_line}行 → {len(dialogues)}行台词")
                
                if DEBUG_MODE:
                    print(f"    [DEBUG] ========== 音轨行号范围结束 ==========")
            else:
                print(f"    [LLM辅助] LLM未能识别多个音轨边界，保持原划分")
        
        # 更新映射
        if track_num is not None:
            # 有明确音轨编号（单个台本文件对应单个音轨）
            all_dialogues = []
            for dlg_list in track_dialogues.values():
                all_dialogues.extend(dlg_list)
            track_map[track_num] = all_dialogues
            track_file_map[track_num] = abs_path
            print(f"    [台本映射] 音轨{track_num:02d} ← {sb_path.name} ({len(all_dialogues)}行台词)")
            print(f"               台本路径: {abs_path}")
        else:
            # 无音轨编号，按解析出的音轨分配
            for tnum, dialogues in track_dialogues.items():
                track_map[tnum] = dialogues
                track_file_map[tnum] = abs_path
                print(f"    [台本映射] 音轨{tnum:02d} ← {sb_path.name} ({len(dialogues)}行台词)")
                print(f"               台本路径: {abs_path}")
    
    return track_map, track_file_map, character_names, track_line_ranges



# ==================== 基于读音的ASR与台本对齐（改进版） ====================

# pyopenjtalk 可用性检查和静默调用
_PYOPENJTALK_AVAILABLE = False
_pyopenjtalk_module = None

try:
    import pyopenjtalk
    _pyopenjtalk_module = pyopenjtalk
    _PYOPENJTALK_AVAILABLE = True
except ImportError:
    pass


def _call_pyopenjtalk_g2p(text: str) -> str:
    """静默调用 pyopenjtalk.g2p，抑制所有 C 库警告
    
    使用上下文管理器临时重定向 stderr
    """
    if not _PYOPENJTALK_AVAILABLE or not _pyopenjtalk_module:
        return ""
    
    import os
    import sys
    from contextlib import contextmanager
    
    @contextmanager
    def _suppress_stderr():
        """上下文管理器：临时抑制 stderr（包括 C 库输出）"""
        # 保存原始 stderr 文件描述符
        stderr_fd = sys.stderr.fileno() if hasattr(sys.stderr, 'fileno') else None
        
        if stderr_fd is not None:
            # 保存原始 stderr
            stderr_dup = os.dup(stderr_fd)
            # 打开 /dev/null
            devnull = os.open(os.devnull, os.O_WRONLY)
            # 重定向 stderr 到 /dev/null
            os.dup2(devnull, stderr_fd)
            os.close(devnull)
            
            try:
                yield
            finally:
                # 恢复 stderr
                os.dup2(stderr_dup, stderr_fd)
                os.close(stderr_dup)
        else:
            yield
    
    with _suppress_stderr():
        try:
            return _pyopenjtalk_module.g2p(text)
        except Exception:
            return ""


def text_to_phonemes(text: str) -> list[str]:
    """将日文文本转换为音素序列（使用 pyopenjtalk）
    
    pyopenjtalk.g2p 返回空格分隔的音素字符串，如：
    "今日は" -> "ky o o w a"
    "小春" -> "k o h a r u"
    
    返回: 音素列表，如 ['ky', 'o', 'o', 'w', 'a']
    """
    if not text or not _PYOPENJTALK_AVAILABLE:
        return []
    
    phoneme_str = _call_pyopenjtalk_g2p(text)
    if phoneme_str:
        return phoneme_str.split()
    return []


def text_to_reading(text: str) -> str:
    """将日文文本转换为读音（片假名）
    
    使用 fugashi 分词获取每个词的读音，然后拼接成读音字符串。
    这样可以处理同音异字、ASR误识别等问题。
    
    例：
    - "今日は" -> "キョウハ"
    - "小春" -> "コハル"
    - "こはる" -> "コハル"  (同音)
    """
    if not text or not _FUGASHI_AVAILABLE:
        return text
    
    readings = []
    for word in _TAGGER(text):
        # 获取读音（kana），如果没有则用 surface
        reading = word.feature.kana if word.feature.kana else word.surface
        readings.append(reading)
    
    return ''.join(readings)


def normalize_reading_for_compare(reading: str) -> str:
    """规范化读音用于比较
    
    1. 统一长音符号（ー）
    2. 去掉促音（ッ）和拨音（ン）的细微差异
    3. 浊音/半浊音统一（如 ビュ -> ビュ）
    """
    # 长音符号保留（用于区分短音和长音）
    # 促音和拨音保留（它们是日语发音的重要特征）
    
    # 但可以做轻微规范化：
    # 1. 统一小さい「ッ」和「っ」
    # 2. 统一小さい「ャ」「ュ」「ョ」
    
    return reading


def calculate_phoneme_similarity_from_sequences(p1: list[str], p2: list[str]) -> float:
    """计算两个音素序列的相似度（编辑距离）
    
    参数:
        p1: 音素序列1（如 ['ky', 'o', 'o', 'w', 'a']）
        p2: 音素序列2
    
    返回: 相似度 (0.0-1.0)
    """
    if not p1 or not p2:
        return 0.0
    
    # 如果音素序列完全相同，直接返回 1.0
    if p1 == p2:
        return 1.0
    
    # 计算编辑距离
    def levenshtein_distance(a: list, b: list) -> int:
        m, n = len(a), len(b)
        if m == 0: return n
        if n == 0: return m
        
        prev = list(range(n + 1))
        curr = [0] * (n + 1)
        
        for i in range(1, m + 1):
            curr[0] = i
            for j in range(1, n + 1):
                cost = 0 if a[i-1] == b[j-1] else 1
                curr[j] = min(
                    curr[j-1] + 1,
                    prev[j] + 1,
                    prev[j-1] + cost
                )
            prev, curr = curr, prev
        
        return prev[n]
    
    dist = levenshtein_distance(p1, p2)
    max_len = max(len(p1), len(p2))
    
    return 1.0 - (dist / max_len)


def calculate_phoneme_similarity(s1: str, s2: str) -> float:
    """计算两个日文文本的音素相似度（使用 pyopenjtalk）
    
    这是最高精度的相似度计算，基于音素级别进行比较。
    可以有效处理：
    - 同音异字（如「小春」vs「こはる」→ 音素完全相同）
    - ASR汉字误识别（如「夏生」vs「なつき」→ 音素完全相同）
    - 浊音/半浊音差异（如「びゅ」vs「ぴゅ」→ by u vs py u）
    
    返回: 相似度 (0.0-1.0)
    """
    if not s1 or not s2:
        return 0.0
    
    # 如果 pyopenjtalk 不可用，回退到读音相似度
    if not _PYOPENJTALK_AVAILABLE:
        return calculate_phonetic_similarity(s1, s2)
    
    # 转换为音素序列
    p1 = text_to_phonemes(s1)
    p2 = text_to_phonemes(s2)
    
    # 如果音素序列完全相同，直接返回 1.0
    if p1 == p2:
        return 1.0
    
    # 如果任一为空，返回 0
    if not p1 or not p2:
        return 0.0
    
    # 计算音素序列的编辑距离
    def levenshtein_distance(a: list, b: list) -> int:
        m, n = len(a), len(b)
        if m == 0: return n
        if n == 0: return m
        
        prev = list(range(n + 1))
        curr = [0] * (n + 1)
        
        for i in range(1, m + 1):
            curr[0] = i
            for j in range(1, n + 1):
                cost = 0 if a[i-1] == b[j-1] else 1
                curr[j] = min(
                    curr[j-1] + 1,
                    prev[j] + 1,
                    prev[j-1] + cost
                )
            prev, curr = curr, prev
        
        return prev[n]
    
    dist = levenshtein_distance(p1, p2)
    max_len = max(len(p1), len(p2))
    
    # 音素相似度
    phoneme_sim = 1.0 - (dist / max_len)
    
    return phoneme_sim


# ==================== 标点符号归一化预处理 ====================

def normalize_punctuation(text: str) -> str:
    """归一化标点符号，用于相似度计算
    
    处理内容：
    1. 全角→半角转换（。→. 、→, ？→? ！→!）
    2. 多个省略号统一为单个...
    3. 长音符号～转换为普通符号
    4. 移除语气词符号（♡♪等）
    5. 移除空白字符
    
    返回：归一化后的文本
    """
    if not text:
        return ""
    
    # 1. 全角→半角转换
    text = text.replace('。', '.').replace('、', ',')
    text = text.replace('？', '?').replace('！', '!')
    text = text.replace('　', ' ')  # 全角空格→半角空格
    
    # 2. 多个省略号统一
    text = re.sub(r'[…\.]{2,}', '.', text)  # 多个省略号→单个.
    
    # 3. 长音符号转换
    text = text.replace('～', '~').replace('ー', '-')
    
    # 4. 移除语气词符号
    text = re.sub(r'[♡♪☆★○●◎◇◆□■△▽]', '', text)
    
    # 5. 移除空白字符（用于相似度计算）
    text = re.sub(r'\s+', '', text)
    
    return text.strip()


def calculate_phonetic_similarity(s1: str, s2: str) -> float:
    """计算两个日文文本的读音相似度（0.0-1.0）
    
    【改进版】严格化阈值，避免不相关字符串获得高分
    
    算法优先级：
    1. 归一化预处理（标点符号统一）
    2. 精确匹配检测（完全相同）
    3. 子串匹配检测（覆盖率≥70%才给高分）
    4. LCS重叠检测（重叠度≥60%才给高分）
    5. 音素相似度（权重70%）+ 文本相似度（权重30%）
    6. N-gram相似度（阈值≥40%）
    
    注意：阈值已严格化，避免"あの……兄さん"匹配到完全不相关的句子获得60%分数
    """
    DEBUG_SIM = False  # 调试开关
    
    if not s1 or not s2:
        return 0.0
    
    if DEBUG_SIM:
        print(f"    [相似度计算] ASR: '{s1[:50]}...' vs 台本: '{s2[:50]}...'")
    
    # === 步骤1：归一化预处理 ===
    s1_normalized = normalize_punctuation(s1)
    s2_normalized = normalize_punctuation(s2)
    
    # 去掉标点符号进行比较（用于子串/LCS检测）
    s1_clean = re.sub(r'[、。？！…～♡♪\s・.,\-~]', '', s1)
    s2_clean = re.sub(r'[、。？！…～♡♪\s・.,\-~]', '', s2)
    
    # === 步骤2：精确匹配检测 ===
    if s1_normalized == s2_normalized:
        return 1.0
    
    if s1_clean == s2_clean:
        return 0.98  # 去标点后完全相同
    
    # === 步骤3：子串匹配检测（严格化阈值）===
    if s1_clean and s2_clean:
        # 情况1：ASR是台本的子串
        if s1_clean in s2_clean:
            coverage = len(s1_clean) / len(s2_clean)
            # 【严格化】覆盖率>=70%才给高分（原50%）
            if coverage >= 0.7:
                return coverage * 0.95 + 0.05  # 0.7-1.0 映射到 0.72-1.0
            # 覆盖率在30%-70%之间，给予中等分数
            elif coverage >= 0.3:
                return coverage * 0.8  # 0.3-0.7 映射到 0.24-0.56
        
        # 情况2：台本是ASR的子串
        if s2_clean in s1_clean:
            coverage = len(s2_clean) / len(s1_clean)
            if coverage >= 0.7:
                return coverage * 0.9 + 0.05
            elif coverage >= 0.3:
                return coverage * 0.75
    
    # === 步骤4：LCS重叠检测（严格化阈值）===
    if s1_clean and s2_clean and len(s1_clean) >= 3 and len(s2_clean) >= 3:
        # 计算最长公共子串长度
        def longest_common_substring(a: str, b: str) -> int:
            """计算最长公共子串长度"""
            m, n = len(a), len(b)
            if m == 0 or n == 0:
                return 0
            
            # 使用动态规划
            dp = [[0] * (n + 1) for _ in range(m + 1)]
            max_len = 0
            
            for i in range(1, m + 1):
                for j in range(1, n + 1):
                    if a[i-1] == b[j-1]:
                        dp[i][j] = dp[i-1][j-1] + 1
                        max_len = max(max_len, dp[i][j])
            
            return max_len
        
        lcs_len = longest_common_substring(s1_clean, s2_clean)
        min_len = min(len(s1_clean), len(s2_clean))
        
        if min_len > 0:
            overlap_ratio = lcs_len / min_len
            # 【严格化】重叠度>=60%才给高分（原40%）
            if overlap_ratio >= 0.6:
                score = 0.75 + (overlap_ratio - 0.6) * 0.5
                return min(score, 0.95)
    
    # === 新增：音素序列模糊匹配 ===
    # 即使ASR和台本在文本层面差异较大，但如果读音相似，也应给予高分
    # 这可以处理ASR漏识别、同音异字等情况
    if _PYOPENJTALK_AVAILABLE and s1.strip() and s2.strip():
        # 获取音素序列
        p1 = text_to_phonemes(s1)
        p2 = text_to_phonemes(s2)
        
        if p1 and p2:
            # 计算音素序列的编辑距离相似度
            phoneme_sim = calculate_phoneme_similarity_from_sequences(p1, p2)
            
            # 调试输出（仅当相似度较低但有可能是部分匹配时）
            # if DEBUG_MODE and phoneme_sim < 0.8:
            #     print(f"    [音素相似度] {phoneme_sim:.2f}: '{s1[:20]}...' vs '{s2[:20]}...'")
            
            # 如果音素相似度>=40%，认为是有效匹配（降低阈值以处理ASR漏识别）
            if phoneme_sim >= 0.4:
                # 结合文本相似度
                text_sim = calculate_text_similarity(s1, s2)
                # 音素相似度权重更高（70%），文本相似度辅助（30%）
                combined_sim = phoneme_sim * 0.7 + text_sim * 0.3
                return combined_sim
    
    # === 新增：N-gram部分匹配 ===
    # 当ASR和台本在文本层面差异较大时，使用N-gram计算部分匹配度
    # 这可以处理ASR漏识别中间内容的情况
    if s1_clean and s2_clean:
        # 计算2-gram和3-gram的匹配度
        def ngram_similarity(a: str, b: str, n: int = 2) -> float:
            """计算N-gram相似度"""
            if len(a) < n or len(b) < n:
                return 0.0
            
            # 生成N-gram集合
            def get_ngrams(text: str, n: int) -> set:
                return set(text[i:i+n] for i in range(len(text) - n + 1))
            
            ngrams_a = get_ngrams(a, n)
            ngrams_b = get_ngrams(b, n)
            
            if not ngrams_a or not ngrams_b:
                return 0.0
            
            # 计算Jaccard相似度
            intersection = len(ngrams_a & ngrams_b)
            union = len(ngrams_a | ngrams_b)
            
            return intersection / union if union > 0 else 0.0
        
        # 计算2-gram和3-gram相似度
        sim_2gram = ngram_similarity(s1_clean, s2_clean, 2)
        sim_3gram = ngram_similarity(s1_clean, s2_clean, 3)
        
        # 综合N-gram相似度（3-gram权重更高）
        combined_ngram_sim = sim_3gram * 0.7 + sim_2gram * 0.3
        
        # 如果N-gram相似度>=25%，认为是有效匹配（降低阈值）
        if combined_ngram_sim >= 0.25:
            # 根据N-gram相似度计算分数
            # 0.25 -> 0.55, 0.4 -> 0.7, 0.6 -> 0.85
            score = 0.55 + (combined_ngram_sim - 0.25) * 1.5
            return min(score, 0.92)  # 最高0.92（留一点空间给完全匹配）
        
        # === 新增：最长公共子序列（LCS）比例匹配 ===
        # 当N-gram相似度也不够时，使用LCS比例
        # LCS允许跳过字符，可以处理ASR漏识别中间内容的情况
        def lcs_ratio(a: str, b: str) -> float:
            """计算最长公共子序列比例
            
            返回: LCS长度 / min(len(a), len(b))
            """
            m, n = len(a), len(b)
            if m == 0 or n == 0:
                return 0.0
            
            # 使用滚动数组优化空间
            prev = [0] * (n + 1)
            curr = [0] * (n + 1)
            
            for i in range(1, m + 1):
                for j in range(1, n + 1):
                    if a[i-1] == b[j-1]:
                        curr[j] = prev[j-1] + 1
                    else:
                        curr[j] = max(prev[j], curr[j-1])
                prev, curr = curr, prev
            
            lcs_len = prev[n]
            min_len = min(m, n)
            
            return lcs_len / min_len if min_len > 0 else 0.0
        
        lcs_sim = lcs_ratio(s1_clean, s2_clean)
        
        # 如果LCS比例>=50%，认为是有效匹配
        if lcs_sim >= 0.5:
            # 根据LCS比例计算分数
            # 0.5 -> 0.6, 0.7 -> 0.75, 0.9 -> 0.9
            score = 0.6 + (lcs_sim - 0.5) * 0.75
            return min(score, 0.90)
    
    # === 原有算法 ===
    # 优先使用音素相似度（pyopenjtalk）
    if _PYOPENJTALK_AVAILABLE:
        phoneme_sim = calculate_phoneme_similarity(s1, s2)
        
        # 如果音素完全相同，直接返回 1.0
        if phoneme_sim >= 0.99:
            return 1.0
        
        # 结合字面相似度
        text_sim = calculate_text_similarity(s1, s2)
        
        # 音素相似度权重更高（80%），字面相似度辅助（20%）
        return phoneme_sim * 0.8 + text_sim * 0.2
    
    # 回退到 fugashi 片假名比较
    if not _FUGASHI_AVAILABLE:
        return calculate_text_similarity(s1, s2)
    
    # 转换为读音（片假名）
    r1 = text_to_reading(s1)
    r2 = text_to_reading(s2)
    
    # 如果读音完全相同，直接返回 1.0
    if r1 == r2:
        return 1.0
    
    # 计算读音的编辑距离
    def levenshtein_distance(a: str, b: str) -> int:
        m, n = len(a), len(b)
        if m == 0: return n
        if n == 0: return m
        
        prev = list(range(n + 1))
        curr = [0] * (n + 1)
        
        for i in range(1, m + 1):
            curr[0] = i
            for j in range(1, n + 1):
                cost = 0 if a[i-1] == b[j-1] else 1
                curr[j] = min(
                    curr[j-1] + 1,
                    prev[j] + 1,
                    prev[j-1] + cost
                )
            prev, curr = curr, prev
        
        return prev[n]
    
    dist = levenshtein_distance(r1, r2)
    max_len = max(len(r1), len(r2))
    
    if max_len == 0:
        return 1.0 if r1 == r2 else 0.0
    
    phonetic_sim = 1.0 - (dist / max_len)
    text_sim = calculate_text_similarity(s1, s2)
    
    return phonetic_sim * 0.7 + text_sim * 0.3


def calculate_text_similarity(s1: str, s2: str) -> float:
    """计算纯文本相似度（原 calculate_similarity 的逻辑）"""
    if not s1 or not s2:
        return 0.0
    
    # 方法1：最长公共子序列（LCS）
    def lcs_length(a: str, b: str) -> int:
        m, n = len(a), len(b)
        prev = [0] * (n + 1)
        curr = [0] * (n + 1)
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if a[i-1] == b[j-1]:
                    curr[j] = prev[j-1] + 1
                else:
                    curr[j] = max(prev[j], curr[j-1])
            prev, curr = curr, prev
        return prev[n]
    
    lcs = lcs_length(s1, s2)
    lcs_score = lcs / max(len(s1), len(s2))
    
    # 方法2：共同字符比例
    common_chars = sum(1 for c in s1 if c in s2)
    common_score = common_chars / max(len(s1), len(s2))
    
    # 方法3：编辑距离惩罚
    len_diff = abs(len(s1) - len(s2))
    len_penalty = 1.0 - (len_diff / max(len(s1), len(s2)))
    
    # 综合分数
    final_score = (lcs_score * 0.5 + common_score * 0.3 + len_penalty * 0.2)
    
    return final_score


def calculate_similarity(s1: str, s2: str) -> float:
    """计算两个字符串的相似度（0.0-1.0）
    
    使用多种算法综合计算：
    1. 最长公共子序列（LCS）
    2. 共同字符比例
    3. 编辑距离（Levenshtein距离）
    """
    if not s1 or not s2:
        return 0.0
    
    # 方法1：最长公共子序列（LCS）
    def lcs_length(a: str, b: str) -> int:
        m, n = len(a), len(b)
        # 使用滚动数组优化空间
        prev = [0] * (n + 1)
        curr = [0] * (n + 1)
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if a[i-1] == b[j-1]:
                    curr[j] = prev[j-1] + 1
                else:
                    curr[j] = max(prev[j], curr[j-1])
            prev, curr = curr, prev
        return prev[n]
    
    lcs = lcs_length(s1, s2)
    lcs_score = lcs / max(len(s1), len(s2))
    
    # 方法2：共同字符比例
    common_chars = sum(1 for c in s1 if c in s2)
    common_score = common_chars / max(len(s1), len(s2))
    
    # 方法3：编辑距离（简化版）
    # 如果长度差异过大，降低分数
    len_diff = abs(len(s1) - len(s2))
    len_penalty = 1.0 - (len_diff / max(len(s1), len(s2)))
    
    # 综合分数（加权平均）
    final_score = (lcs_score * 0.5 + common_score * 0.3 + len_penalty * 0.2)
    
    return final_score


# ==================== 句子级别对齐（改进版） ====================

# 日文句子切分标记
_SENTENCE_SPLITTERS = ('。', '？', '?', '！', '!', '…', '・・・', '　')


def split_to_sentences(text: str) -> list[str]:
    """将文本切分成句子
    
    按日文句号、问号、感叹号等切分。
    保留标点符号在句末。
    
    改进：
    1. 支持多种句末标点组合
    2. 正确处理逗号分隔的长句（逗号不是句末，不切分）
    3. **「～」长音符号不作为句末标点**（它通常是语气延续，不是句子结束）
    4. **「って」口语引用结束词作为软切分点**（ASR常见切分点）
    5. **长句智能切分**：如果句子过长（>80字符），尝试在逗号后切分
    6. **「～って」组合切分**：当长音符号+引用结束词出现时，切分
    
    参数:
        text: 输入文本
    
    返回: 句子列表（已去除空白句子）
    """
    if not text:
        return []
    
    import re
    
    # 先将多个省略号统一为 …
    text = text.replace('・・・', '…').replace('。。', '。')
    
    # === 切分策略 ===
    # 1. 首先按硬切分点（。？！）切分
    # 2. 如果整句没有硬切分点，检查软切分点（って、等）
    # 3. 如果句子过长（>80字符），尝试在逗号后切分
    
    # 硬切分标点：句末标点（。？！…）
    hard_pattern = r'([^。？！…\n]+[。？！…]+)'
    hard_matches = re.findall(hard_pattern, text)
    
    # 检查硬切分后的剩余文本
    matched_text = ''.join(hard_matches)
    remaining_text = text[len(matched_text):].strip()
    
    sentences = [s.strip() for s in hard_matches if s.strip()]
    
    if remaining_text:
        # 有剩余文本，添加为新句子
        sentences.append(remaining_text)
    
    # 如果没有硬切分，尝试软切分
    if not sentences:
        # 检查「って」作为引用结束词
        tte_positions = []
        for match in re.finditer(r'って[,、\s]', text):
            tte_positions.append(match.end())
        
        if tte_positions:
            # 在「って」后面切分
            new_sentences = []
            prev_pos = 0
            for pos in tte_positions:
                if pos < len(text):
                    sent = text[prev_pos:pos].strip()
                    if sent:
                        new_sentences.append(sent)
                    prev_pos = pos
            
            # 添加剩余部分
            remaining = text[prev_pos:].strip()
            if remaining:
                new_sentences.append(remaining)
            
            if new_sentences:
                sentences = new_sentences
        
        # 如果也没有软切分点，返回整句
        if not sentences and text.strip():
            sentences = [text.strip()]
    
    # 智能切分：如果句子过长（>80字符），尝试在逗号后切分
    # 这有助于处理ASR将长句切分成多行的情况
    final_sentences = []
    for sent in sentences:
        if len(sent) > 80:
            # 尝试在逗号后切分
            # 查找句子中间的逗号位置（避开开头和结尾）
            comma_positions = []
            for i, char in enumerate(sent):
                if char in '、，' and 10 < i < len(sent) - 10:
                    comma_positions.append(i)
            
            if comma_positions:
                # 在中间的逗号处切分
                # 选择最接近句子中间位置的逗号
                mid_pos = len(sent) // 2
                best_comma = min(comma_positions, key=lambda x: abs(x - mid_pos))
                
                first_part = sent[:best_comma + 1].strip()
                second_part = sent[best_comma + 1:].strip()
                
                if first_part and second_part:
                    final_sentences.append(first_part)
                    final_sentences.append(second_part)
                else:
                    final_sentences.append(sent)
            else:
                final_sentences.append(sent)
        else:
            final_sentences.append(sent)
    
    # === 新增：处理「～って」组合切分 ===
    # 如果整句只有一个问号或感叹号，检查是否有「～って」切分点
    # 例如：「こないださ、姫川さんの言うことなら何でも言うこと聞きます～って、お前、言ってたっしょ？」
    # 应该切分为：「こないださ、姫川さんの言うことなら何でも言うこと聞きます～って」和「お前、言ってたっしょ？」
    if len(final_sentences) == 1 and len(final_sentences[0]) > 30:
        sent = final_sentences[0]
        # 查找「～って」模式（长音符号+引用结束词）
        tte_match = re.search(r'～って[,、\s]', sent)
        if tte_match:
            # 在「って」后面切分
            split_pos = tte_match.end()
            first_part = sent[:split_pos].strip()
            second_part = sent[split_pos:].strip()
            if first_part and second_part:
                final_sentences = [first_part, second_part]
    
    return final_sentences


def split_lines_to_sentences(lines: list[str]) -> tuple[list[str], list[tuple[int, int]]]:
    """将行列表切分成句子列表，并保留映射关系
    
    参数:
        lines: 行列表
    
    返回: (句子列表, 映射列表)
        - 句子列表: [sentence1, sentence2, ...]
        - 映射列表: [(原行索引, 句子在原行中的起始位置), ...]
    
    例如：
        输入: ["今日は。明日も。", "いい天気だ。"]
        输出: 
            sentences = ["今日は。", "明日も。", "いい天気だ。"]
            mapping = [(0, 0), (0, 1), (1, 0)]  # (行索引, 句子索引)
    """
    all_sentences = []
    mapping = []  # (line_idx, sentence_idx_in_line)
    
    for line_idx, line in enumerate(lines):
        sentences = split_to_sentences(line)
        for sent_idx, sent in enumerate(sentences):
            all_sentences.append(sent)
            mapping.append((line_idx, sent_idx))
    
    return all_sentences, mapping


def match_sentences(asr_sentences: list[str], sb_sentences: list[str],
                   similarity_threshold: float = 0.70) -> tuple[list[tuple[int, int, float]], list[int], list[int]]:
    """句子级别的动态规划对齐
    
    参数:
        asr_sentences: ASR句子列表
        sb_sentences: 台本句子列表
        similarity_threshold: 相似度阈值
    
    返回: (匹配对列表, ASR未匹配索引列表, 台本未匹配索引列表)
        - 匹配对列表: [(asr_idx, sb_idx, similarity), ...]
        - ASR未匹配索引列表: [idx, ...]
        - 台本未匹配索引列表: [idx, ...]
    """
    if not asr_sentences or not sb_sentences:
        return [], list(range(len(asr_sentences))), list(range(len(sb_sentences)))
    
    n_asr = len(asr_sentences)
    n_sb = len(sb_sentences)
    
    # 计算相似度矩阵（使用音素相似度）
    sim_matrix = [[0.0] * n_sb for _ in range(n_asr)]
    
    # DEBUG模式下的详细记录
    debug_sim_details = []  # [(asr_idx, asr_text, sb_idx, sb_text, sim)]
    
    for i in range(n_asr):
        if not asr_sentences[i].strip():
            continue
        for j in range(n_sb):
            if not sb_sentences[j].strip():
                continue
            # 使用音素相似度（优先）或文本相似度
            sim = calculate_phonetic_similarity(
                asr_sentences[i].strip(), 
                sb_sentences[j].strip()
            )
            sim_matrix[i][j] = sim
            
            # DEBUG: 记录所有相似度计算（只记录有意义的）
            if DEBUG_MODE and sim >= similarity_threshold * 0.5:  # 记录超过阈值一半的
                debug_sim_details.append((i, asr_sentences[i].strip(), j, sb_sentences[j].strip(), sim))
    
    # 动态规划对齐
    # dp[i][j] = 前i个ASR句子和前j个台本句子的最大对齐分数
    dp = [[0.0] * (n_sb + 1) for _ in range(n_asr + 1)]
    path = [[None] * (n_sb + 1) for _ in range(n_asr + 1)]
    
    for i in range(1, n_asr + 1):
        for j in range(1, n_sb + 1):
            # 选项1：对齐
            sim = sim_matrix[i-1][j-1]
            align_score = dp[i-1][j-1] + (sim if sim >= similarity_threshold else -1)
            
            # 选项2：ASR跳过（ASR有额外内容）
            skip_asr_score = dp[i-1][j] - 0.3
            
            # 选项3：台本跳过（台本有ASR没有的内容）
            skip_sb_score = dp[i][j-1] - 0.3
            
            best_score = align_score
            best_op = 'align'
            
            if skip_asr_score > best_score:
                best_score = skip_asr_score
                best_op = 'skip_asr'
            
            if skip_sb_score > best_score:
                best_score = skip_sb_score
                best_op = 'skip_sb'
            
            dp[i][j] = best_score
            path[i][j] = best_op
    
    # 回溯
    matches = []
    asr_unmatched = []
    sb_unmatched = []
    
    i, j = n_asr, n_sb
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            op = path[i][j]
            if op == 'align':
                sim = sim_matrix[i-1][j-1]
                if sim >= similarity_threshold:
                    matches.append((i-1, j-1, sim))
                else:
                    asr_unmatched.append(i-1)
                i -= 1
                j -= 1
            elif op == 'skip_asr':
                asr_unmatched.append(i-1)
                i -= 1
            elif op == 'skip_sb':
                sb_unmatched.append(j-1)
                j -= 1
            else:
                i -= 1
                j -= 1
        elif i > 0:
            asr_unmatched.append(i-1)
            i -= 1
        elif j > 0:
            sb_unmatched.append(j-1)
            j -= 1
    
    # 反转（因为我们是从后往前回溯的）
    matches.reverse()
    asr_unmatched.reverse()
    sb_unmatched.reverse()
    
    # DEBUG模式下打印匹配详情
    if DEBUG_MODE:
        print(f"    [DEBUG] ========== 句子级匹配详情 ==========")
        
        # 打印成功匹配的句子
        if matches:
            print(f"    [DEBUG] 成功匹配 ({len(matches)} 对):")
            for asr_idx, sb_idx, sim in matches:
                asr_text = asr_sentences[asr_idx][:50] + "..." if len(asr_sentences[asr_idx]) > 50 else asr_sentences[asr_idx]
                sb_text = sb_sentences[sb_idx][:50] + "..." if len(sb_sentences[sb_idx]) > 50 else sb_sentences[sb_idx]
                print(f"    [DEBUG]   ASR句{asr_idx:02d}: {asr_text}")
                print(f"    [DEBUG]      → 台本句{sb_idx:02d}: {sb_text} (相似度: {sim:.1%})")
        
        # 打印ASR未匹配的句子
        if asr_unmatched:
            print(f"    [DEBUG] ASR未匹配 ({len(asr_unmatched)} 句):")
            for idx in asr_unmatched:
                asr_text = asr_sentences[idx][:50] + "..." if len(asr_sentences[idx]) > 50 else asr_sentences[idx]
                print(f"    [DEBUG]   ASR句{idx:02d}: {asr_text}")
        
        # 打印台本未匹配的句子
        if sb_unmatched:
            print(f"    [DEBUG] 台本未匹配 ({len(sb_unmatched)} 句):")
            for idx in sb_unmatched:
                sb_text = sb_sentences[idx][:50] + "..." if len(sb_sentences[idx]) > 50 else sb_sentences[idx]
                print(f"    [DEBUG]   台本句{idx:02d}: {sb_text}")
        
        print(f"    [DEBUG] ========== 句子级匹配详情结束 ==========")
    
    return matches, asr_unmatched, sb_unmatched


def match_scriptbook_to_asr(asr_lines: list[str], scriptbook_lines: list[str], 
                            similarity_threshold: float = 0.75,
                            asr_timestamps: list[str] = None) -> tuple[list[str], list[float], list[dict]]:
    """将台本台词与ASR结果对齐，返回对齐结果和相似度分数
    
    【改进版】使用句子级别对齐，支持：
    - 一行多句的ASR与台本对齐（会拆分成多行）
    - 多行ASR对应一行台本的情况
    - 基于音素的相似度计算
    
    算法流程：
    1. 将ASR行和台本行都切分成句子
    2. 在句子级别进行动态规划对齐
    3. 根据句子对齐结果，重建行级别的映射
    4. 【关键改进】如果一个ASR行匹配多个台本句子，拆分成多行输出
    
    参数:
        asr_lines: ASR识别结果列表
        scriptbook_lines: 台本台词列表
        similarity_threshold: 相似度阈值，超过此值认为是对齐的
        asr_timestamps: ASR时间戳列表（如 ["00:01:23", "00:02:45"]）
    
    返回: (对齐后的台本台词列表, 相似度分数列表, 对齐详情列表)
        - 对齐的台词：返回台本内容
        - 未对齐的台词：返回空字符串（保留ASR）
        - 对齐详情列表：包含每一行的对齐信息，用于导出
    """
    if not scriptbook_lines or not asr_lines:
        return [], [], []
    
    n_asr = len(asr_lines)
    n_sb = len(scriptbook_lines)
    
    # 预处理：过滤空行
    asr_texts = [line.strip() for line in asr_lines]
    sb_texts = [line.strip() for line in scriptbook_lines]
    
    # === 步骤1：句子级别切分 ===
    asr_sentences, asr_mapping = split_lines_to_sentences(asr_texts)
    sb_sentences, sb_mapping = split_lines_to_sentences(sb_texts)
    
    # 如果切分后句子数量和原行数相同，直接使用行级对齐（更快）
    if len(asr_sentences) == n_asr and len(sb_sentences) == n_sb:
        # 使用简单的行级对齐
        return _match_lines_directly(asr_texts, sb_texts, similarity_threshold, asr_timestamps)
    
    # === 步骤2：句子级别对齐 ===
    print(f"    [句子对齐] ASR: {n_asr}行 → {len(asr_sentences)}句, 台本: {n_sb}行 → {len(sb_sentences)}句")
    matches, asr_unmatched, sb_unmatched = match_sentences(asr_sentences, sb_sentences, similarity_threshold)
    print(f"    [句子对齐] 匹配: {len(matches)}对, ASR未匹配: {len(asr_unmatched)}句, 台本未匹配: {len(sb_unmatched)}句")
    
    # === 步骤3：重建行级映射 ===
    # 构建句子索引到行的映射
    asr_sent_to_line = {}  # asr_sent_idx -> asr_line_idx
    for sent_idx, (line_idx, _) in enumerate(asr_mapping):
        asr_sent_to_line[sent_idx] = line_idx
    
    sb_sent_to_line = {}  # sb_sent_idx -> sb_line_idx
    for sent_idx, (line_idx, _) in enumerate(sb_mapping):
        sb_sent_to_line[sent_idx] = line_idx
    
    # 构建每个ASR行对应的台本句子列表
    # asr_line_sb_sentences[asr_line_idx] = [(sb_sentence_idx, similarity), ...]
    asr_line_sb_sentences = defaultdict(list)
    for asr_sent_idx, sb_sent_idx, sim in matches:
        asr_line_idx = asr_sent_to_line[asr_sent_idx]
        asr_line_sb_sentences[asr_line_idx].append((sb_sent_idx, sim))
    
    # === 步骤4：生成结果 ===
    result = [""] * n_asr  # 默认空字符串（保留ASR）
    scores = [0.0] * n_asr
    alignment_details = []
    expanded_result = []  # 新增：支持拆分后的结果列表
    expanded_scores = []  # 新增：支持拆分后的分数列表
    expanded_timestamps = []  # 新增：支持拆分后的时间戳列表
    
    for asr_line_idx in range(n_asr):
        asr_text = asr_texts[asr_line_idx]
        timestamp = asr_timestamps[asr_line_idx] if asr_timestamps and asr_line_idx < len(asr_timestamps) else ""
        
        if asr_line_idx in asr_line_sb_sentences:
            # 有匹配的台本句子
            sb_sents = asr_line_sb_sentences[asr_line_idx]
            
            # 【关键改进】如果一个ASR行匹配了多个台本句子，合并为一行输出
            # 这样可以保持时间戳映射正确，避免空行时间戳问题
            if len(sb_sents) > 1:
                # 按台本句子顺序排序（保持对话顺序）
                sb_sents_sorted = sorted(sb_sents, key=lambda x: x[0])
                
                # 合并所有匹配的台本句子为一行
                merged_texts = []
                total_chars = 0
                best_sim = 0.0
                
                for sb_sent_idx, sim in sb_sents_sorted:
                    sb_text = sb_sentences[sb_sent_idx]
                    merged_texts.append(sb_text)
                    total_chars += len(sb_text)
                    best_sim = max(best_sim, sim)  # 取最高相似度
                
                # 合并为一行（用空格分隔）
                merged_text = " ".join(merged_texts)
                
                # 添加合并后的结果
                expanded_result.append(merged_text)
                expanded_scores.append(best_sim)
                expanded_timestamps.append(timestamp)
                
                # 记录详情（标记为合并匹配）
                alignment_details.append({
                    "asr_index": asr_line_idx,
                    "sb_index": -1,  # 多个台本句子
                    "asr_text": asr_text,
                    "sb_text": merged_text,
                    "similarity": best_sim,
                    "timestamp": timestamp,
                    "status": "matched_merged",  # 标记为合并匹配
                    "merged_count": len(sb_sents),
                    "merged_from": [sb_sent_idx for sb_sent_idx, _ in sb_sents_sorted]
                })
            else:
                # 单个匹配，保持原逻辑
                best_sb_sent_idx, best_sim = sb_sents[0]
                sb_line_idx = sb_sent_to_line[best_sb_sent_idx]
                sb_text = sb_sentences[best_sb_sent_idx]
                
                expanded_result.append(sb_text)
                expanded_scores.append(best_sim)
                expanded_timestamps.append(timestamp)
                
                alignment_details.append({
                    "asr_index": asr_line_idx,
                    "sb_index": sb_line_idx,
                    "asr_text": asr_text,
                    "sb_text": sb_text,
                    "similarity": best_sim,
                    "timestamp": timestamp,
                    "status": "matched"
                })
        else:
            # 无匹配，保留ASR
            expanded_result.append("")
            expanded_scores.append(0.0)
            expanded_timestamps.append(timestamp)
            
            if asr_text:
                alignment_details.append({
                    "asr_index": asr_line_idx,
                    "sb_index": -1,
                    "asr_text": asr_text,
                    "sb_text": "",
                    "similarity": 0.0,
                    "timestamp": timestamp,
                    "status": "asr_only"
                })
    
    # 反转对齐详情（按ASR顺序排列）
    alignment_details.reverse()
    
    # 【重要】返回拆分后的结果（行数可能增加）
    return expanded_result, expanded_scores, alignment_details


def _match_lines_directly(asr_texts: list[str], sb_texts: list[str],
                          similarity_threshold: float,
                          asr_timestamps: list[str] = None) -> tuple[list[str], list[float], list[dict]]:
    """直接行级对齐（滑动窗口顺序对齐版）
    
    【改进版】使用滑动窗口顺序对齐，避免全局DP导致的错配问题。
    
    核心改进：
    1. 每个ASR行只在当前指针附近±WINDOW_SIZE行内搜索匹配
    2. 强制按顺序对齐，不允许跳回已匹配的台本行
    3. 处理ASR行合并（多个ASR行对应一个台本句子）
    4. 处理ASR行拆分（一个ASR行对应多个台本句子）
    
    参数:
        asr_texts: ASR文本列表
        sb_texts: 台本文本列表
        similarity_threshold: 相似度阈值
        asr_timestamps: ASR时间戳列表（可选）
    
    返回: (对齐后的台本文本列表, 相似度分数列表, 对齐详情列表)
    """
    n_asr = len(asr_texts)
    n_sb = len(sb_texts)
    
    # 滑动窗口大小
    WINDOW_SIZE = 5
    
    # 结果初始化
    result = [""] * n_asr  # 默认空字符串（保留ASR）
    scores = [0.0] * n_asr
    alignment_details = []
    
    # 台本指针（按顺序推进）
    sb_ptr = 0
    
    # 已匹配的台本索引集合
    matched_sb_indices = set()
    
    for i in range(n_asr):
        asr_text = asr_texts[i]
        timestamp = asr_timestamps[i] if asr_timestamps and i < len(asr_timestamps) else ""
        
        if not asr_text or not asr_text.strip():
            # 空行，跳过
            alignment_details.append({
                "asr_index": i,
                "sb_index": -1,
                "asr_text": asr_text,
                "sb_text": "",
                "similarity": 0.0,
                "timestamp": timestamp,
                "status": "asr_only"  # ASR空行
            })
            continue
        
        # 在滑动窗口内搜索最佳匹配
        best_sb_idx = -1
        best_sim = 0.0
        best_sb_text = ""
        
        # 搜索范围：从当前sb_ptr开始，向后看WINDOW_SIZE行
        search_start = sb_ptr
        search_end = min(search_start + WINDOW_SIZE, n_sb)
        
        for j in range(search_start, search_end):
            if j in matched_sb_indices:
                continue
            
            sb_text = sb_texts[j]
            if not sb_text or not sb_text.strip():
                continue
            
            # 计算相似度
            sim = calculate_phonetic_similarity(asr_text, sb_text)
            
            if sim > best_sim:
                best_sim = sim
                best_sb_idx = j
                best_sb_text = sb_text
        
        # 根据匹配结果处理
        if best_sim >= similarity_threshold:
            # 高相似度匹配
            result[i] = best_sb_text
            scores[i] = best_sim
            matched_sb_indices.add(best_sb_idx)
            
            alignment_details.append({
                "asr_index": i,
                "sb_index": best_sb_idx,
                "asr_text": asr_text,
                "sb_text": best_sb_text,
                "similarity": best_sim,
                "timestamp": timestamp,
                "status": "matched"
            })
            
            # 推进指针到匹配位置的下一行
            sb_ptr = best_sb_idx + 1
        else:
            # 低相似度或无匹配，保留ASR
            alignment_details.append({
                "asr_index": i,
                "sb_index": -1,
                "asr_text": asr_text,
                "sb_text": "",
                "similarity": best_sim,
                "timestamp": timestamp,
                "status": "asr_only"  # ASR独有内容
            })
            
            # 不推进指针，让下一个ASR行有机会匹配当前台本行
    
    return result, scores, alignment_details


def export_alignment_result(alignment_details: list[dict], 
                            scriptbook_path: Path,
                            output_dir: Path,
                            track_num: int = None,
                            asr_file: str = None) -> Path:
    """导出ASR与台本的对齐结果到txt文件
    
    参数:
        alignment_details: 对齐详情列表
        scriptbook_path: 台本文件路径
        output_dir: 输出目录
        track_num: 音轨编号（可选）
        asr_file: ASR文件名（可选）
    
    返回: 输出文件路径
    """
    import datetime
    
    # 确保输出目录存在
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 构造输出文件名
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if track_num is not None:
        output_name = f"alignment_track{track_num:02d}_{timestamp}.txt"
    else:
        output_name = f"alignment_{timestamp}.txt"
    
    output_path = output_dir / output_name
    
    # 统计对齐情况
    matched_count = sum(1 for d in alignment_details if d["status"] == "matched")
    asr_only_count = sum(1 for d in alignment_details if d["status"] == "asr_only")
    sb_only_count = sum(1 for d in alignment_details if d["status"] == "sb_only")
    low_sim_count = sum(1 for d in alignment_details if d["status"] == "low_similarity")
    
    # 构建输出内容
    lines = []
    lines.append("=" * 60)
    lines.append("ASR与台本对齐结果")
    lines.append("=" * 60)
    lines.append(f"台本文件: {scriptbook_path}")
    lines.append(f"音轨编号: {track_num if track_num is not None else '未指定'}")
    lines.append(f"ASR文件: {asr_file if asr_file else '未指定'}")
    lines.append(f"导出时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append(f"统计信息:")
    lines.append(f"  匹配成功: {matched_count} 行")
    lines.append(f"  ASR独有: {asr_only_count} 行（拟声词/喘息声等）")
    lines.append(f"  台本独有: {sb_only_count} 行（可能漏识别）")
    lines.append(f"  相似度过低: {low_sim_count} 行")
    lines.append("")
    lines.append("=" * 60)
    lines.append("详细对齐结果")
    lines.append("=" * 60)
    lines.append("")
    
    for detail in alignment_details:
        status = detail["status"]
        timestamp_str = detail.get("timestamp", "")
        asr_text = detail.get("asr_text", "")
        sb_text = detail.get("sb_text", "")
        similarity = detail.get("similarity", 0.0)
        
        if status == "matched":
            lines.append(f"[匹配] 时间: {timestamp_str}")
            lines.append(f"  ASR : {asr_text}")
            lines.append(f"  台本: {sb_text}")
            lines.append(f"  相似度: {similarity:.1%}")
            lines.append("")
        elif status == "asr_only":
            lines.append(f"[ASR独有] 时间: {timestamp_str}")
            lines.append(f"  ASR : {asr_text}")
            lines.append(f"  说明: 台本中无此内容，可能是拟声词/喘息声/即兴发挥")
            lines.append("")
        elif status == "sb_only":
            lines.append(f"[台本独有]")
            lines.append(f"  台本: {sb_text}")
            lines.append(f"  说明: ASR中无此内容，可能是漏识别")
            lines.append("")
        elif status == "low_similarity":
            lines.append(f"[相似度过低] 时间: {timestamp_str}")
            lines.append(f"  ASR : {asr_text}")
            lines.append(f"  台本: {sb_text}")
            lines.append(f"  相似度: {similarity:.1%}")
            lines.append(f"  说明: 相似度低于阈值，保留ASR原文")
            lines.append("")
    
    # 写入文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    print(f"    [对齐导出] 已保存对齐结果: {output_path}")
    return output_path


def build_aligned_scriptbook_prompt(asr_lines: list[str], aligned_lines: list[str], 
                                    scores: list[float]) -> str:
    """构建对齐后的台本参考prompt
    
    只发送ASR与台本不一致的部分，大幅减少Token消耗。
    
    参数:
        asr_lines: ASR原始内容
        aligned_lines: 对齐后的台本内容（空字符串表示保留ASR）
        scores: 相似度分数
    
    返回: prompt字符串（如果无差异则返回空字符串）
    """
    if not asr_lines or not aligned_lines:
        return ""
    
    # 统计对齐情况
    aligned_count = sum(1 for line in aligned_lines if line.strip())
    total_asr = len([line for line in asr_lines if line.strip()])
    
    if aligned_count == 0:
        return ""
    
    lines = ["\n【台本参考（仅显示ASR与台本有差异的部分）】"]
    lines.append(f"台本对齐结果: {aligned_count}/{total_asr} 行ASR与台本匹配")
    lines.append("")
    
    # 只显示有差异的部分
    diff_count = 0
    for i, (asr, aligned, score) in enumerate(zip(asr_lines, aligned_lines, scores), 1):
        if not asr.strip():
            continue
        
        if aligned.strip():
            # 对齐成功，检查是否有差异
            if aligned.strip() != asr.strip():
                # 有差异，显示ASR和台本的对照
                diff_count += 1
                lines.append(f"  {i:02d}: [ASR] {asr[:60]}{'...' if len(asr) > 60 else ''}")
                lines.append(f"      [台本] {aligned[:60]}{'...' if len(aligned) > 60 else ''}")
                lines.append(f"      [相似度] {score:.1%}")
                lines.append("")
    
    if diff_count == 0:
        return ""
    
    lines.append(f"共 {diff_count} 处差异，请优先使用台本内容。")
    lines.append("【台本参考结束】\n")
    
    return "\n".join(lines)


# ==================== 世界观管理 ====================

def get_worldview_path(work_dir: Path) -> Path:
    """获取世界观文件路径"""
    return work_dir / '.worldview.json'


def load_worldview(work_dir: Path) -> dict:
    """加载已保存的世界观"""
    worldview_path = get_worldview_path(work_dir)
    if worldview_path.exists():
        try:
            with open(worldview_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return {}
    return {}


def save_worldview(work_dir: Path, worldview: dict) -> None:
    """保存世界观"""
    worldview_path = get_worldview_path(work_dir)
    with open(worldview_path, 'w', encoding='utf-8') as f:
        json.dump(worldview, f, ensure_ascii=False, indent=2)


def build_worldview_prompt(worldview: dict) -> str:
    """将世界观构建为 prompt 附加内容"""
    if not worldview:
        return ""

    lines = ["\n【作品背景（请在翻译时参考以下信息，保持设定一致）】"]

    # 世界观简介
    if worldview.get('worldview'):
        lines.append(f"  世界观: {worldview['worldview']}")

    # 角色信息
    characters = worldview.get('characters', [])
    if characters:
        lines.append("  角色:")
        for char in characters[:5]:  # 最多显示5个角色
            name = char.get('name', '')
            role = char.get('role', '')
            personality = char.get('personality', '')
            if name:
                char_info = f"    {name}"
                if role:
                    char_info += f" ({role})"
                if personality:
                    char_info += f" - {personality}"
                lines.append(char_info)

    # 特殊术语
    special_terms = worldview.get('special_terms', {})
    if special_terms:
        lines.append("  特殊设定:")
        for term, desc in list(special_terms.items())[:5]:
            lines.append(f"    {term}: {desc}")

    lines.append("【背景信息结束】\n")
    return "\n".join(lines)


def sample_all_lrc_files(work_dir: Path, lines_per_file: int = 20) -> tuple[list[str], dict[str, dict]]:
    """抽样所有 .ja.lrc 文件的内容，用于世界观和术语分析
    
    返回: (抽样内容列表, cores字典)
    """
    samples = []
    all_cores = defaultdict(lambda: {
        'reading': '',
        'pos': '',
        'count': 0,
        'surfaces': defaultdict(int),
        'contexts': [],
        'files': set(),
        'pronouns': defaultdict(int),
    })

    ja_files = []
    for ext in SUBTITLE_EXTS:
        for fpath in _walk_files(work_dir):
            if fpath.suffix.lower() == ext and fpath.stem.endswith('.ja'):
                ja_files.append(fpath)

    print(f"    [抽样] 发现 {len(ja_files)} 个 .ja.lrc 文件", flush=True)

    for idx, ja_path in enumerate(ja_files, 1):
        ext = ja_path.suffix.lower()
        try:
            with open(ja_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            continue

        lyric_texts = extract_subtitle_texts(content, ext)
        if not lyric_texts:
            continue

        # 抽样：优先选取含角色名/专有名词的行，然后均匀分布
        sampled_lines = []
        step = max(1, len(lyric_texts) // lines_per_file)

        for i in range(0, len(lyric_texts), step):
            if len(sampled_lines) >= lines_per_file:
                break
            line = lyric_texts[i].strip()
            if line:
                sampled_lines.append(line)

        if sampled_lines:
            samples.append(f"=== 文件: {ja_path.name} ===\n" + "\n".join(sampled_lines[:lines_per_file]))
            print(f"      [{idx}/{len(ja_files)}] {ja_path.name}: 抽取 {len(sampled_lines[:lines_per_file])} 行")

        # 同时提取 cores
        file_cores = extract_cores_from_file(ja_path)
        for core, info in file_cores.items():
            all_cores[core]['reading'] = info['reading']
            all_cores[core]['pos'] = info['pos']
            all_cores[core]['count'] += info['count']
            for s, c in info['surfaces'].items():
                all_cores[core]['surfaces'][s] += c
            all_cores[core]['files'].add(str(ja_path))
            for pr, c in info.get('pronouns', {}).items():
                all_cores[core]['pronouns'][pr] += c
            for ctx in info['contexts']:
                if len(all_cores[core]['contexts']) < 5:
                    all_cores[core]['contexts'].append(ctx)

    # 转换
    for core, info in all_cores.items():
        info['surfaces'] = dict(info['surfaces'])
        info['files'] = list(info['files'])
        info['pronouns'] = dict(info['pronouns'])

    return samples, dict(all_cores)


# 世界观分析 Prompt
WORLDVIEW_ANALYSIS_PROMPT = (
    "以下是日文ASMR/R18音声作品的ASR转录文本样本。\n"
    "请分析这些样本，提取作品的世界观信息。\n\n"
    "输出要求（紧凑JSON格式）：\n"
    '{"worldview": "作品世界观简介（150字以内，包含：时代背景、主要角色关系、核心设定）", "characters": [{"name": "角色名", "role": "角色定位", "personality": "性格特点"}], "themes": ["主题1", "主题2"], "special_terms": {"术语": "简短解释"}}\n\n'
    "注意：\n"
    "- worldview 必须精炼，用于辅助后续翻译理解上下文，控制在150字以内\n"
    "- characters 只列出主要角色，最多5个\n"
    "- special_terms 只列出对翻译有影响的特殊设定术语\n"
    "- 成人向内容请客观描述设定，无需回避\n"
    "- 不要输出任何 JSON 之外的内容\n"
)


def analyze_worldview_with_llm(samples: list[str]) -> dict:
    """调用 LLM 分析作品世界观
    
    返回: 世界观字典
    """
    if not samples:
        return {}

    content = WORLDVIEW_ANALYSIS_PROMPT + "\n\n" + "\n\n".join(samples)
    print(f"    [调试] 发送给 LLM 的文本长度: {len(content)} 字符")

    # 只保留 OpenAI API 接受的参数，过滤掉自定义参数
    _raw_params = _api_cfg.get("generation_params", {})
    gen_params = {k: v for k, v in _raw_params.items()
                  if k in ('temperature', 'top_p', 'top_k', 'presence_penalty', 'frequency_penalty',
                           'stop', 'logit_bias', 'user', 'reasoning_effort')}
    gen_params['max_tokens'] = _raw_params.get("max_tokens_worldview", 32768)
    timeout = _api_cfg.get("timeout_analysis", 300)

    print(f"    [世界观分析] 正在调用 LLM (timeout={timeout}s, max_tokens={gen_params['max_tokens']})...")

    try:
        response = client.chat.completions.create(
            model=_api_cfg["model"],
            messages=[{"role": "user", "content": content}],
            timeout=timeout,
            **gen_params
        )

        usage = response.usage
        hit_tokens = getattr(usage, 'prompt_cache_hit_tokens', 0)
        miss_tokens = getattr(usage, 'prompt_cache_miss_tokens', 0)
        total_prompt = getattr(usage, 'prompt_tokens', 0)
        completion_tokens = getattr(usage, 'completion_tokens', 0)

        global total_hit_tokens, total_miss_tokens, total_prompt_tokens, total_completion_tokens
        total_hit_tokens += hit_tokens
        total_miss_tokens += miss_tokens
        total_prompt_tokens += total_prompt
        total_completion_tokens += completion_tokens

        raw_output = response.choices[0].message.content.strip()
        print(f"    [调试] LLM 原始输出 (前300字): {raw_output[:300]}")

        # 提取 JSON
        json_match = re.search(r'\{.*\}', raw_output, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group())
                print(f"    [世界观分析] 完成!")
                print(f"      世界观: {result.get('worldview', '')[:100]}...")
                print(f"      角色: {len(result.get('characters', []))} 个")
                return result
            except json.JSONDecodeError as e:
                print(f"    ⚠ JSON 解析错误: {e}")
                return {}
        else:
            print(f"    ⚠ 未找到 JSON 格式内容")
            return {}

    except Exception as e:
        print(f"    ⚠ 世界观分析失败: {e}")
        return {}


# 动态术语提取 Prompt
DYNAMIC_TERMS_PROMPT = (
    "以下是日文ASR转录文本及其对应的中文翻译结果。\n"
    "请从中提取新发现的术语（角色名、物品名、专有名词等）。\n\n"
    "输出要求（紧凑JSON格式）：\n"
    '{"new_terms": {"日文core": "中文译名"}, "new_alias": [{"alias": "误识别词", "target": "正确词", "confidence": 0.9}]}\n\n'
    "注意：\n"
    "- 只提取在术语表中尚未出现的术语\n"
    "- new_terms 的 key 是 core（去掉称呼后缀的核心词）\n"
    "- 只提取对翻译一致性有影响的专有名词，普通词汇不要提取\n"
    "- 如果没有新发现，返回空对象: {\"new_terms\": {}, \"new_alias\": []}\n"
)


def extract_terms_from_translation(original_lyrics: list[str], translated_lyrics: list[str],
                                   existing_terms: dict[str, str]) -> tuple[dict[str, str], list[dict]]:
    """从翻译结果中提取新术语
    
    返回: (新术语字典, 新alias列表)
    """
    if not original_lyrics or not translated_lyrics:
        return {}, []

    # 构建对照文本
    pairs = []
    for orig, trans in zip(original_lyrics[:50], translated_lyrics[:50]):  # 限制行数
        if orig.strip() and trans.strip():
            pairs.append(f"原文: {orig}\n译文: {trans}")

    if not pairs:
        return {}, []

    content = DYNAMIC_TERMS_PROMPT + "\n\n已有术语表:\n" + json.dumps(existing_terms,
                                                                      ensure_ascii=False) + "\n\n对照文本:\n" + "\n\n".join(
        pairs)

    # 只保留 DeepSeek API 接受的参数，过滤掉自定义参数
    _raw_params = _api_cfg.get("generation_params", {})
    gen_params = {k: v for k, v in _raw_params.items()
                  if k in ('temperature', 'top_p', 'top_k', 'presence_penalty', 'frequency_penalty',
                           'stop', 'logit_bias', 'user', 'reasoning_effort')}
    gen_params['max_tokens'] = 4096

    try:
        response = client.chat.completions.create(
            model=_api_cfg["model"],
            messages=[{"role": "user", "content": content}],
            timeout=60,
            **gen_params
        )

        raw_output = response.choices[0].message.content.strip()

        # 提取 JSON
        json_match = re.search(r'\{.*\}', raw_output, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group())
                new_terms = result.get('new_terms', {})
                new_alias = result.get('new_alias', [])

                # 过滤低置信度 alias
                new_alias = [a for a in new_alias if a.get('confidence', 0) >= 0.8]

                if new_terms or new_alias:
                    print(f"    [动态更新] 发现 {len(new_terms)} 个新术语, {len(new_alias)} 个新 alias")

                return new_terms, new_alias
            except json.JSONDecodeError:
                return {}, []
        else:
            return {}, []

    except Exception as e:
        print(f"    ⚠ 动态术语提取失败: {e}")
        return {}, []


def _process_pending_translations(pending: list) -> None:
    """批量处理待处理的翻译对，提取新术语
    
    参数:
        pending: [(parent_dir, original_lyrics, translated_lyrics, terms), ...]
    """
    if not pending:
        return
    
    # 合并所有翻译对
    all_originals = []
    all_translated = []
    parent_dir = None
    
    for item in pending:
        parent_dir, orig, trans, _terms = item
        all_originals.extend(orig)
        all_translated.extend(trans)
    
    if not all_originals or not all_translated:
        return
    
    # 获取现有术语表
    existing_terms = load_terms(parent_dir) if parent_dir else {}
    
    # 批量提取术语（限制行数以控制 API 成本）
    max_lines = 100  # 批量处理最多100行
    sampled_orig = all_originals[:max_lines]
    sampled_trans = all_translated[:max_lines]
    
    new_terms, new_alias = extract_terms_from_translation(sampled_orig, sampled_trans, existing_terms)
    if new_terms or new_alias:
        update_terms_and_alias(parent_dir, new_terms, new_alias)


def update_terms_and_alias(work_dir: Path, new_terms: dict[str, str], new_alias: list[dict]) -> None:
    """更新术语表和 alias 表"""
    if not new_terms and not new_alias:
        return

    # 加载现有数据
    existing_terms = load_terms(work_dir)
    existing_alias = load_alias(work_dir)

    # 合并新术语
    for ja, zh in new_terms.items():
        if ja not in existing_terms:
            existing_terms[ja] = zh
            print(f"      + 术语: {ja} → {zh}")

    # 合并新 alias（去重）
    existing_alias_pairs = {(a.get('alias'), a.get('target')) for a in existing_alias}
    for alias_item in new_alias:
        pair = (alias_item.get('alias'), alias_item.get('target'))
        if pair not in existing_alias_pairs:
            existing_alias.append(alias_item)
            print(f"      + alias: {pair[0]} → {pair[1]}")

    # 保存
    if new_terms:
        save_terms(work_dir, existing_terms)
    if new_alias:
        save_alias(work_dir, existing_alias)


# ==================== fugashi 分词与 core 提取 ====================

import fugashi

try:
    # UNIDIC_DIR 已在文件开头设置，fugashi.Tagger() 会自动使用
    _TAGGER = fugashi.Tagger()
    _FUGASHI_AVAILABLE = True
except Exception as _e:
    print(f"  [fugashi 初始化失败] {_e}")
    print(f"  [字典路径] {dic_path_safe}")
    _TAGGER = None
    _FUGASHI_AVAILABLE = False
# 称呼后缀列表（按长度降序，优先匹配长的）
HONORIFIC_SUFFIXES = [
    'ちゃんたん', 'ちゃん', 'たん', 'さん', 'くん', '君',
    '様', 'さま', '殿', '氏',
    '先輩', '先生', 'せんせい',
    'チャン', 'タン', 'サン', 'クン', 'サマ',
]


def split_honorific(surface: str) -> tuple[str, str]:
    """拆分称呼后缀，返回 (core, suffix)"""
    for suffix in HONORIFIC_SUFFIXES:
        if surface.endswith(suffix):
            core = surface[:-len(suffix)]
            if len(core) >= 1:
                return core, suffix
    return surface, ''


def tokenize_text(text: str) -> list[dict]:
    """使用 fugashi 分词，返回词元列表（含 core 拆分）"""
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


# 称呼后缀正则（用于文件名提取）
_FILENAME_HONORIFIC_RE = re.compile(r'([\u3040-\u309f\u30a0-\u30ff]+?)(?:ちゃん|さん|様|さま|くん|君|たん|チャン|サン|サマ|クン|タン)', re.I)

# 纯汉字角色名正则（2-4字常见角色名长度）
_FILENAME_NAME_RE = re.compile(r'([\u4e00-\u9fff]{2,4})')

# 常见动词/助词后缀，不是角色名的一部分
_NON_NAME_SUFFIXES = (
    'した', 'する', 'ある', 'れる', 'なる', 'いる', 'ない', 'たい', 'だっ', 'ちゃ', 'なっ', 'やっ', 'くれ', 'あっ', 'さ', 'て', 'で', 'に', 'が', 'を',
    'と',
    'の', 'は', 'も', 'や', 'へ', 'から', 'まで', 'より', 'ば', 'ばっ', 'じゃ', 'ぜ', 'ぞ', 'ね', 'よ', 'わ', 'な', 'か')


def _walk_files(work_dir: Path) -> list[Path]:
    """递归遍历目录下所有文件，兼容深层嵌套和特殊字符路径
    
    注意：使用 rglob 而非 os.walk，因为 os.walk 在 Windows 下会跳过
    包含特殊字符（如英文感叹号 !）的文件夹。
    """
    files = []
    try:
        for fpath in work_dir.rglob('*'):
            if fpath.is_file():
                files.append(fpath)
    except Exception as e:
        print(f"    [警告] 遍历目录失败 {work_dir}: {e}")
    return files


def extract_names_from_filenames(work_dir: Path) -> set[str]:
    """从音频/字幕文件名中提取角色名候选

    支持两种形式：
    1. 假名 + 称呼后缀（如 'こりんちゃん' -> 'こりん'）
    2. 纯汉字角色名（如 '樹里'、'梨乃'）
    """
    names = set()
    all_suffixes = tuple(AUDIO_SUFFIXES) + tuple(SUBTITLE_EXTS)
    for fpath in _walk_files(work_dir):
        if fpath.suffix.lower() in all_suffixes:
            # 去掉扩展名和常见前缀（如 '1）'）
            stem = fpath.stem
            stem = re.sub(r'^[\d０-９]+[)）]', '', stem)
            # 1. 查找带称呼后缀的假名角色名
            for match in _FILENAME_HONORIFIC_RE.finditer(stem):
                core = match.group(1)
                if len(core) < 2 or len(core) > 8:
                    continue
                # 尝试去掉常见非角色后缀
                cleaned = core
                for sfx in _NON_NAME_SUFFIXES:
                    if cleaned.endswith(sfx) and len(cleaned) - len(sfx) >= 2:
                        cleaned = cleaned[:-len(sfx)]
                        break
                names.add(cleaned)
            # 2. 查找纯汉字角色名（需要前后有非日文分隔符，避免误匹配普通词汇）
            for match in _FILENAME_NAME_RE.finditer(stem):
                core = match.group(1)
                if len(core) < 2 or len(core) > 4:
                    continue
                # 过滤常见非角色词汇
                _BLOCKLIST = {
                    '美人', '親子', '家庭', '大学', '時代', '再会', '脅迫', '脅し',
                    '撮影', '鑑賞', '下品', '路地', '裏', '連行', '素股', '服従',
                    '奉仕', '立場', '無理', '喪失', '強制', '中出', '変態', '声',
                    '狂', '戻', '母親', '処女', '耳舐', '手コ', '下校', '途中',
                    '口ま', '使っ', '前で', '後で', '自分', '元カ', 'カレ',
                    'プロ', 'ローグ', 'ギャル', 'ママ', 'オナ', 'ニー',
                    'イラ', 'マチ', 'オ', 'ラブ', 'ホ', 'えっ', 'ち', 'プレイ',
                    '揃っ', 'わから', 'やり', 'セッ', 'クス', '娘の',
                    '狂い', '戻っ', 'オホ', 'アク', 'メ',
                    # 3-4字组合词
                    '親子揃', '処女喪失', '下校途中', '路地裏', '強制耳舐',
                    '撮影会', '大学時代', '強制中出', '服従チン', 'ポ奴隷',
                    '背徳の', '全記録', '通常', '美人ギャ', 'ルママ',
                    'オナニー', '鑑賞撮', '影会っ', '口まん', 'こ使っ',
                    'イラマチ', 'オっ下', '校途中', '脅迫っ', '路地裏',
                    '連れ込', 'んで強', '制耳舐', 'め手コ', 'キっ親',
                    'ラブホ', 'に連行', 'っ素股', 'えっち', 'で服従',
                    'ご奉仕', 'プレイ', '立場わ', 'からせ', 'る無理',
                    'やり耳', '舐め手', 'コキっ', 'セックス', '喪失強',
                    '制中出', 'しっ母', '親の前', '大学時', '代のセ',
                    'ックス狂', 'いに戻', 'って娘', 'の前で', '変態オ',
                    'ホ声ア', 'クメっ',
                }
                if core in _BLOCKLIST:
                    continue
                names.add(core)
    return names


def extract_cores_from_file(ja_path: Path, filename_names: set[str] = None) -> dict[str, dict]:
    """扫描单个 .ja.lrc 文件，提取 core 级候选词（文件级分析）

    返回: {
        core: {
            'reading': str,
            'pos': str,
            'count': int,
            'surfaces': {surface: count},
            'contexts': [str],
            'files': [str],
            'pronouns': {pronoun: count},  # 与该core共现的人称代词
        }
    }
    """
    cores = defaultdict(lambda: {
        'reading': '',
        'pos': '',
        'count': 0,
        'surfaces': defaultdict(int),
        'contexts': [],
        'files': set(),
        'pronouns': defaultdict(int),
    })

    ext = ja_path.suffix.lower()
    try:
        with open(ja_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return {}

    lyric_texts = extract_subtitle_texts(content, ext)

    # 人称代词列表（用于共现分析）
    pronoun_pattern = re.compile(r'^(私|わたし|わたくし|あたし|僕|ぼく|俺|おれ|うち)$')

    for line in lyric_texts:
        # 检测该行的人称代词
        line_pronouns = set()
        for tok in tokenize_text(line):
            if pronoun_pattern.match(tok['core']):
                line_pronouns.add(tok['core'])

        tokens = tokenize_text(line)
        for tok in tokens:
            core = tok['core']
            surface = tok['surface']
            reading = tok['reading']
            pos = tok['pos']
            suffix = tok['suffix']

            # 规则1：只保留名词
            if pos not in ('名詞', '固有名詞', '代名詞'):
                continue

            # 规则2：停用词过滤
            if core in STOP_WORDS or surface in STOP_WORDS or reading in STOP_WORDS:
                continue

            # 规则3：core 长度过滤
            if len(core) < 1:
                continue
            if not suffix and len(core) < 2:
                continue

            # 规则4：拟声词过滤（纯平假名短词）
            if re.match(r'^[ぁ-ん]{1,4}$', core) and not suffix:
                if core not in ('にい', 'あね', 'あに', 'おに', 'おね'):
                    continue

            # 记录
            cores[core]['reading'] = reading
            cores[core]['pos'] = pos
            cores[core]['count'] += 1
            cores[core]['surfaces'][surface] += 1
            cores[core]['files'].add(str(ja_path))
            # 记录共现的人称代词
            for pr in line_pronouns:
                cores[core]['pronouns'][pr] += 1
            # 保存上下文（最多3句，优先保存带称呼后缀的）
            if len(cores[core]['contexts']) < 3:
                if suffix or not any(surface in ctx for ctx in cores[core]['contexts']):
                    cores[core]['contexts'].append(line)

    # 转换和过滤
    result = {}
    filename_names = filename_names or set()
    for core, info in cores.items():
        info['surfaces'] = dict(info['surfaces'])
        info['files'] = list(info['files'])
        info['pronouns'] = dict(info['pronouns'])

        # 过滤：保留带后缀、片假名、固有名詞、或高频词
        has_suffix = any(len(s) > len(core) for s in info['surfaces'])
        is_katakana = bool(re.search(r'[\u30a0-\u30ff]', core))
        is_proper = info['pos'] == '固有名詞'
        from_filename = core in filename_names

        # 强准入：来自文件名的角色名直接保留
        if from_filename:
            result[core] = info
            continue

        # 单字符 core 直接跳过（无意义）
        if len(core) < 2:
            continue

        # 双字符且纯假名/长音：极易是语气词/拟声词，仅来自文件名或带后缀时才保留
        if len(core) == 2 and re.match(r'^[ぁ-んー]{2}$', core) and not from_filename and not has_suffix:
            continue

        # 纯平假名短词需要更高门槛（避免 あー/ほー/うー 等语气词）
        if re.match(r'^[ぁ-んー]{3,4}$', core) and not has_suffix and info['count'] < 5:
            continue

        # 普通 core：带后缀、片假名、固有名詞 直接保留；其余需要 count>=3
        if has_suffix or is_katakana or is_proper or info['count'] >= 3:
            result[core] = info

    return result


def extract_cores_from_dir(work_dir: Path, filename_names: set[str] = None) -> dict[str, dict]:
    """扫描作品目录下所有 .ja.lrc，按文件提取后合并 core 级候选词

    改进：逐个文件分析，然后合并，保留每个文件的 pronouns 信息用于互斥性判断
    """
    all_file_cores = {}

    for ext in SUBTITLE_EXTS:
        for fpath in _walk_files(work_dir):
            if fpath.suffix.lower() == ext and fpath.stem.endswith('.ja'):
                file_cores = extract_cores_from_file(fpath, filename_names)
                all_file_cores[str(fpath)] = file_cores

    # 合并所有文件的 cores
    merged_cores = defaultdict(lambda: {
        'reading': '',
        'pos': '',
        'count': 0,
        'surfaces': defaultdict(int),
        'contexts': [],
        'files': set(),
        'pronouns': defaultdict(int),
    })

    for file_path, file_cores in all_file_cores.items():
        for core, info in file_cores.items():
            merged_cores[core]['reading'] = info['reading']
            merged_cores[core]['pos'] = info['pos']
            merged_cores[core]['count'] += info['count']
            for s, c in info['surfaces'].items():
                merged_cores[core]['surfaces'][s] += c
            merged_cores[core]['files'].add(file_path)
            for pr, c in info['pronouns'].items():
                merged_cores[core]['pronouns'][pr] += c
            # 合并上下文，最多保留5句（来自不同文件）
            for ctx in info['contexts']:
                if len(merged_cores[core]['contexts']) < 5:
                    if not any(ctx in existing for existing in merged_cores[core]['contexts']):
                        merged_cores[core]['contexts'].append(ctx)

    # 转换
    result = {}
    for core, info in merged_cores.items():
        info['surfaces'] = dict(info['surfaces'])
        info['files'] = list(info['files'])
        info['pronouns'] = dict(info['pronouns'])
        result[core] = info

    return result


# ==================== 严格 reading 相似聚类 ====================

# 清浊音映射（双向）
SEIDAKU_MAP = {
    'か': 'が', 'き': 'ぎ', 'く': 'ぐ', 'け': 'げ', 'こ': 'ご',
    'さ': 'ざ', 'し': 'じ', 'す': 'ず', 'せ': 'ぜ', 'そ': 'ぞ',
    'た': 'だ', 'ち': 'ぢ', 'つ': 'づ', 'て': 'で', 'と': 'ど',
    'は': 'ば', 'ひ': 'び', 'ふ': 'ぶ', 'へ': 'べ', 'ほ': 'ぼ',
    'が': 'か', 'ぎ': 'き', 'ぐ': 'く', 'げ': 'け', 'ご': 'こ',
    'ざ': 'さ', 'じ': 'し', 'ず': 'す', 'ぜ': 'せ', 'ぞ': 'そ',
    'だ': 'た', 'ぢ': 'ち', 'づ': 'つ', 'で': 'て', 'ど': 'と',
    'ば': 'は', 'び': 'ひ', 'ぶ': 'ふ', 'べ': 'へ', 'ぼ': 'ほ',
}


def normalize_reading(r: str) -> str:
    """规范化读音：去掉长音，统一拨音促音"""
    r = r.replace('ー', '')
    r = r.replace('ッ', '').replace('っ', '')
    return r


def is_seidaku_similar(c1: str, c2: str) -> bool:
    """判断两个字符是否是清浊音关系"""
    return SEIDAKU_MAP.get(c1) == c2 or SEIDAKU_MAP.get(c2) == c1


def reading_similarity_strict(r1: str, r2: str) -> tuple[bool, str]:
    """严格判断读音相似性

    返回: (是否相似, 相似类型)
    """
    if r1 == r2:
        return True, "exact"

    nr1 = normalize_reading(r1)
    nr2 = normalize_reading(r2)

    # 太短的不参与差1匹配（避免 セイ/セイシ、イ/イク 等误聚）
    min_len = min(len(nr1), len(nr2))
    max_len = max(len(nr1), len(nr2))

    if nr1 == nr2:
        return True, "long_vowel"

    # 必须等长（或差1）
    if max_len - min_len > 1:
        return False, ""

    # 等长：只允许单字符清浊音差异
    if len(nr1) == len(nr2):
        diff_positions = []
        for i, (c1, c2) in enumerate(zip(nr1, nr2)):
            if c1 != c2:
                diff_positions.append((c1, c2))

        if len(diff_positions) == 0:
            return True, "exact"

        if len(diff_positions) == 1:
            c1, c2 = diff_positions[0]
            if is_seidaku_similar(c1, c2):
                return True, "seidaku"

        return False, ""

    # 差1：只允许插入/删除一个字符，且较长者至少4字符
    if max_len < 4:
        return False, ""

    longer = nr1 if len(nr1) > len(nr2) else nr2
    shorter = nr2 if len(nr1) > len(nr2) else nr1

    for i in range(len(longer)):
        test = longer[:i] + longer[i + 1:]
        if test == shorter:
            return True, "sokuon"

    return False, ""


def find_similar_reading_clusters(cores: dict[str, dict]) -> list[list[str]]:
    """基于严格 reading 相似性 + 人称代词互斥分析生成候选簇

    改进：
    1. 保留原有 reading 相似性判断
    2. 新增人称代词互斥检查：如果两个词分别与不同的人称代词（私/あたし）强共现，则不应聚类
    3. 新增上下文共现检查：低频词必须出现在高频词的上下文中

    返回: [[core1, core2, ...], ...]
    """
    core_list = list(cores.keys())
    n = len(core_list)

    # 并查集
    parent = list(range(n))

    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # 计算每个 core 的主要人称代词（用于互斥判断）
    def get_dominant_pronoun(core: str) -> "str | None":
        pronouns = cores[core].get('pronouns', {})
        if not pronouns:
            return None
        # 返回出现次数最多的人称代词
        return max(pronouns.items(), key=lambda x: x[1])[0]

    # 两两比较
    for i in range(n):
        for j in range(i + 1, n):
            c1 = core_list[i]
            c2 = core_list[j]
            r1 = cores[c1]['reading']
            r2 = cores[c2]['reading']

            similar, sim_type = reading_similarity_strict(r1, r2)
            if similar:
                count1 = cores[c1]['count']
                count2 = cores[c2]['count']

                # === 新增：人称代词互斥检查 ===
                # 如果两个词分别与不同的人称代词强共现，说明它们可能是不同角色，不应聚类
                p1 = get_dominant_pronoun(c1)
                p2 = get_dominant_pronoun(c2)
                if p1 and p2 and p1 != p2:
                    # 检查共现强度：如果各自的主要代词出现次数都超过2次，认为互斥
                    p1_count = cores[c1]['pronouns'].get(p1, 0)
                    p2_count = cores[c2]['pronouns'].get(p2, 0)
                    if p1_count >= 2 and p2_count >= 2:
                        continue

                # 额外检查：频率比例（低频词必须出现在高频词的上下文中）
                if max(count1, count2) >= min(count1, count2) * 10:
                    low_core = c1 if count1 < count2 else c2
                    high_core = c2 if count1 < count2 else c1
                    low_ctx = ' '.join(cores[low_core]['contexts'])
                    high_surfaces = set(cores[high_core]['surfaces'].keys())
                    # 低频词的上下文中是否出现高频词的表面形式
                    if not any(s in low_ctx for s in high_surfaces):
                        continue

                union(i, j)

    # 收集簇
    clusters = {}
    for i in range(n):
        root = find(i)
        if root not in clusters:
            clusters[root] = []
        clusters[root].append(core_list[i])

    # 只返回包含多个 core 的簇
    return [c for c in clusters.values() if len(c) > 1]


# ==================== LLM 角色识别与术语分析 ====================

# 已知的高风险误译映射（日文 -> 错误中文），用于自动拦截
_SUSPICIOUS_TERM_PATTERNS = {
    # メス/オス 的生物学误译
    'メス': ['母'],
    'オス': ['公'],
}

# 通用动物/生物词汇，在音声语境下通常不需要统一翻译
_GENERIC_BIOLOGY_TERMS = {
    'メス', 'オス', '雌', '雄', '牝', '牡',
    '犬', 'いぬ', '猫', 'ねこ', '鳥', 'とり', '魚', 'さかな',
}


def validate_terms(terms: dict[str, str]) -> dict[str, str]:
    """校验术语表，过滤明显不合理的映射
    
    规则：
    1. 拦截已知的危险误译模式
    2. 通用生物词汇在音声语境下通常不需要统一翻译
    3. 单字译名需要额外谨慎（大概率是过度简化）
    """
    validated = {}
    for ja, zh in terms.items():
        # 规则1：拦截已知误译
        if ja in _SUSPICIOUS_TERM_PATTERNS:
            bad_translations = _SUSPICIOUS_TERM_PATTERNS[ja]
            if zh in bad_translations:
                print(f"    [校验] 拦截可疑映射: {ja} -> {zh} (已知误译模式)")
                continue

        # 规则2：通用生物词汇通常不需要统一翻译
        if ja in _GENERIC_BIOLOGY_TERMS and len(zh) <= 2:
            print(f"    [校验] 跳过通用生物词汇: {ja} -> {zh}")
            continue

        # 规则3：日文 core 是片假名且译名单字，大概率有问题
        if re.match(r'^[\u30a0-\u30ff]+$', ja) and len(zh) == 1:
            print(f"    [校验] 跳过可疑映射: {ja} -> {zh} (片假名单字译名)")
            continue

        validated[ja] = zh

    return validated


# 台本角色名分析提示词（简短版，消耗很小）
# ==================== 身份称呼统一译名映射表 ====================
# 常见身份称呼的固定译名（不需要LLM分析）
ROLE_TRANSLATIONS = {
    # 家庭关系
    '母': '妈妈',
    '母さん': '妈妈',
    'お母さん': '妈妈',
    'ママ': '妈妈',
    'かあさん': '妈妈',
    '姉': '姐姐',
    '姉さん': '姐姐',
    'お姉さん': '姐姐',
    '姉ちゃん': '姐姐',
    'お姉ちゃん': '姐姐',
    '妹': '妹妹',
    '妹さん': '妹妹',
    'いもうと': '妹妹',
    '父': '爸爸',
    '父さん': '爸爸',
    'お父さん': '爸爸',
    'パパ': '爸爸',
    'とうさん': '爸爸',
    '兄': '哥哥',
    '兄さん': '哥哥',
    'お兄さん': '哥哥',
    '兄ちゃん': '哥哥',
    'お兄ちゃん': '哥哥',
    '弟': '弟弟',
    '弟さん': '弟弟',
    'おじ': '叔叔',
    '叔父': '叔叔',
    '叔父さん': '叔叔',
    'おば': '阿姨',
    '叔母': '阿姨',
    '叔母さん': '阿姨',
    '祖父': '爷爷',
    'おじいさん': '爷爷',
    '祖母': '奶奶',
    'おばあさん': '奶奶',
    # 其他关系
    '娘': '女儿',
    '息子': '儿子',
    '孫': '孙子',
    # 师生关系
    '先生': '老师',
    '教師': '老师',
    '生徒': '学生',
    '先輩': '前辈',
    '後輩': '后辈',
    # 职业称呼
    '看護師': '护士',
    '医者': '医生',
    '店員': '店员',
    '受付': '前台',
    # 特殊关系
    '主': '主人',
    'ご主人': '主人',
    '坊ちゃん': '少爷',
    'お嬢様': '大小姐',
    '令嬢': '大小姐',
}

SCRIPTBOOK_CHARACTER_PROMPT = (
    "以下是从台本文件中提取的角色名列表。\n"
    "请为每个角色名给出：1) 正确的日文读音（片假名），2) 统一的中文译名。\n\n"
    "输出要求：\n"
    "1. 对于有明确汉字写法的角色名（如「雫葵」），需要给出其正确的日文读音（如 シズキ）\n"
    "2. 对于片假名角色名（如「アヤ」），读音就是其本身\n"
    "3. 对于特殊称呼（如「日直」），可以保留原意或音译\n\n"
    "请严格按以下紧凑 JSON 格式输出，不要添加任何其他内容：\n"
    '{"characters":[{"name":"角色名1","reading":"片假名读音","translation":"中文译名"},...]}\n\n'
    "注意：\n"
    "- reading 必须是片假名，表示该角色名的正确发音\n"
    "- 译名应简洁自然，适合在对话中使用\n"
    "- 同一角色在不同场景可能有不同称呼，但译名应统一\n"
    "- **重要**：汉字角色名可能有特殊读音，请根据常见的日本人名读音规则判断\n"
    "  例如：雫葵→シズキ、熾月→シズキ、四月→シガツ\n"
)

CHARACTER_ANALYSIS_PROMPT = (
    "以下是从日文ASR转录文本中提取的词汇信息，以及从文件名中识别出的角色名候选。\n"
    "请分析这些词汇，识别作品中的角色名、称呼、重要物品和设定用语，并给出统一的中文译名。\n\n"
    "输出要求：\n"
    "1. terms: 需要统一翻译的核心词汇（基于 core，不是 surface）\n"
    "2. alias: 确认属于ASR误识别的词对（confidence >= 0.8 才收录）\n"
    "3. 忽略常见动词、助词、普通名词等不需要统一的词\n\n"
    "请严格按以下紧凑 JSON 格式输出，不要添加任何其他内容：\n"
    '{"terms":{"core1":"译名1","core2":"译名2"},"alias":[{"alias":"误识别core","target":"正确core","confidence":0.95},...]}\n\n'
    "注意：\n"
    "- terms 的 key 是 core（去掉后缀后的核心词），不是 surface\n"
    "- 遇到「core+ちゃん」「core+様」等形式，只输出 core 的译名\n"
    "- alias 只收录高置信度的ASR误识别，普通同义词不要收录\n"
    "- 身体部位、性相关词汇根据作品语境给出自然的中文表达\n"
    "- **绝对不要**收录可能是拟声词、语气词、喘息声的短词（如 あー、ほー、うー、はぁ、んー 等）\n"
    "- 如果文件名候选中的角色名在文本中确实出现，请务必给出译名\n"
    "- **生物学字面翻译陷阱**：メス/オス 在成人音声中通常指『雌性/雄性』或『母狗/公狗』等带有性别支配意味的表达，**绝对不要**按字面译成『母/公』这类普通动物词汇。遇到此类词汇请结合上下文判断是否为 R18 语境下的特殊用法。\n"
    "- **校验原则**：terms 中的译名必须是『角色名、称呼、重要物品、设定用语』之一，普通名词（如动物、植物、日常物品）不要收录。\n\n"
    "【人称代词一致性约束——重要】\n"
    "每个词汇的上下文信息中标注了与该词共现的人称代词（私/わたし/あたし等）。\n"
    "- 如果两个词分别与**不同的人称代词**强共现（如一个词常出现在『私はコリン』中，另一个常出现在『あたしはハスキー』中），则它们**很可能是不同角色**，不应作为 alias 合并。\n"
    "- 如果两个词与**相同的人称代词**共现，或都没有明显的人称代词倾向，则更可能是同一角色的ASR变体。\n"
    "- 例如：『コリン』和『おりん』如果都与『私』共现，且『おりん』出现在『おりんのおまんこ』等语境中，则『おりん』很可能是『コリン』的ASR首音脱落误识别。\n\n"
    "【文件名优先级强化】\n"
    "文件名中的角色名候选具有最高置信度。例如文件名包含『こりんちゃん』时：\n"
    "- 『こりん』『コリン』『おりん』『くりん』等读音相似的变体都应优先映射到文件名中的角色名\n"
    "- 即使某些变体（如『おりん』）的读音与原角色名不完全相同，只要上下文表明它们是同一角色（如都被同一人称呼、都作为交尾对象提及），就应视为ASR误识别\n"
    "- 特别警惕：ASR常将角色名的首音（如コ→脱落为おりん）或中间音（如リン→レン）误识别，这些应被收录为 alias\n"
)


def analyze_characters_with_llm(cores: dict[str, dict], clusters: list[list[str]], filename_names: set[str] = None) -> \
        tuple[dict[str, str], list[dict]]:
    """调用 LLM 分析角色和术语

    返回: (terms, alias_list)
    """
    if not cores:
        return {}, []

    filename_names = filename_names or set()

    # 构建输入
    lines = []

    # 0. 文件名候选角色名（高置信度）
    if filename_names:
        lines.append("【从文件名识别的角色名候选（请优先确认）】")
        for name in sorted(filename_names):
            if name in cores:
                info = cores[name]
                lines.append(f"  {name} | reading:{info['reading']} | count:{info['count']}")
                for ctx in info['contexts'][:2]:
                    lines.append(f"    上下文: {ctx[:60]}")
            else:
                lines.append(f"  {name} | (未在正文中直接出现，请检查是否为ASR变体)")
        lines.append("")

    # 1. 单 core 信息
    # 优先保留：固有名詞、片假名、带称呼后缀的词；然后按频率排序
    def core_priority(item):
        core, info = item
        is_proper = info['pos'] == '固有名詞'
        is_katakana = bool(re.search(r'[\u30a0-\u30ff]', core))
        has_suffix = any(len(s) > len(core) for s in info['surfaces'])
        from_filename = core in filename_names
        # 优先级分数：文件名来源+5000, 固有名詞+1000, 片假名+100, 带后缀+10, 频率本身
        return -(int(from_filename) * 5000 + int(is_proper) * 1000 + int(is_katakana) * 100 + int(has_suffix) * 10 +
                 info['count'])

    sorted_cores = sorted(cores.items(), key=core_priority)[:20]
    lines.append("【高频词汇】")
    for core, info in sorted_cores:
        surfaces_str = ', '.join([f"{s}({c})" for s, c in sorted(info['surfaces'].items(), key=lambda x: -x[1])[:3]])
        pronouns_str = ', '.join(
            [f"{p}({c})" for p, c in sorted(info.get('pronouns', {}).items(), key=lambda x: -x[1])[:2]])
        from_file_mark = " [文件名候选]" if core in filename_names else ""
        lines.append(
            f"  {core} | reading:{info['reading']} | count:{info['count']} | surfaces:{surfaces_str}{from_file_mark}")
        if pronouns_str:
            lines.append(f"    共现人称代词: {pronouns_str}")
        for ctx in info['contexts'][:1]:
            lines.append(f"    上下文: {ctx[:40]}")

    # 2. 相似簇信息
    if clusters:
        lines.append("\n【读音相似簇（待确认是否为ASR误识别）】")
        for i, cluster in enumerate(clusters, 1):
            lines.append(f"  簇{i}:")
            for core in cluster:
                info = cores[core]
                pronouns_str = ', '.join(
                    [f"{p}({c})" for p, c in sorted(info.get('pronouns', {}).items(), key=lambda x: -x[1])[:2]])
                from_file_mark = " [文件名候选]" if core in filename_names else ""
                lines.append(f"    {core} (reading:{info['reading']}, count:{info['count']}){from_file_mark}")
                if pronouns_str:
                    lines.append(f"      共现人称代词: {pronouns_str}")
                for ctx in info['contexts'][:1]:
                    lines.append(f"      上下文: {ctx[:60]}")

    content = CHARACTER_ANALYSIS_PROMPT + "\n\n" + "\n".join(lines)

    # 打印发送给 LLM 的内容长度
    print(f"    [调试] 发送给 LLM 的文本长度: {len(content)} 字符")

    # 只保留 OpenAI API 接受的参数，过滤掉自定义参数
    _raw_params = _api_cfg.get("generation_params", {})
    gen_params = {k: v for k, v in _raw_params.items()
                  if k in ('temperature', 'top_p', 'top_k', 'presence_penalty', 'frequency_penalty',
                           'stop', 'logit_bias', 'user', 'reasoning_effort')}
    gen_params['max_tokens'] = _raw_params.get("max_tokens_terms", 16384)

    try:
        response = client.chat.completions.create(
            model=_api_cfg["model"],
            messages=[{"role": "user", "content": content}],
            timeout=_api_cfg.get("timeout", 2000),
            **gen_params
        )

        usage = response.usage
        hit_tokens = getattr(usage, 'prompt_cache_hit_tokens', 0)
        miss_tokens = getattr(usage, 'prompt_cache_miss_tokens', 0)
        total_prompt = getattr(usage, 'prompt_tokens', 0)
        completion_tokens = getattr(usage, 'completion_tokens', 0)

        global total_hit_tokens, total_miss_tokens, total_prompt_tokens, total_completion_tokens
        total_hit_tokens += hit_tokens
        total_miss_tokens += miss_tokens
        total_prompt_tokens += total_prompt
        total_completion_tokens += completion_tokens

        raw_output = response.choices[0].message.content.strip()
        print(f"    [调试] LLM 原始输出 (前500字):")
        print(f"    {raw_output[:500]}")
        if len(raw_output) > 500:
            print(f"    ... (共 {len(raw_output)} 字)")

        # 提取 JSON
        json_match = re.search(r'\{.*\}', raw_output, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group())

                terms = result.get('terms', {})
                print(f"    [调试] 解析到术语: {len(terms)} 个")
                for ja, zh in list(terms.items())[:5]:
                    print(f"      - {ja} -> {zh}")

                alias_list = result.get('alias', [])
                print(f"    [调试] 解析到 alias: {len(alias_list)} 个")
                for a in alias_list[:5]:
                    print(f"      - {a.get('alias')} -> {a.get('target')} (conf: {a.get('confidence')})")

                # 过滤低置信度 alias
                filtered = [a for a in alias_list if a.get('confidence', 0) >= 0.8]
                print(f"    [调试] 过滤后(>=0.8): {len(filtered)} 个")

                # 代码层校验：过滤明显不合理的术语映射
                terms = validate_terms(terms)
                print(f"    [调试] 术语校验后: {len(terms)} 个")

                print(f"  LLM 分析完成: {len(filtered)} 个高置信度 alias, {len(terms)} 个术语")
                return terms, filtered
            except json.JSONDecodeError as e:
                print(f"  ⚠ LLM 返回格式错误: {e}")
                print(f"    [调试] JSON片段: {json_match.group()[:200]}")
                return {}, []
        else:
            print(f"    [调试] 未找到 JSON 格式内容")
            return {}, []
    except Exception as e:
        print(f"  ⚠ LLM 分析失败: {e}")
        return {}, []


def analyze_scriptbook_characters(character_names: set[str], work_dir: Path) -> dict[str, str]:
    """
    分析台本中提取的角色名，调用 LLM 给出统一译名和读音。
    
    Args:
        character_names: 台本中提取的角色名集合
        work_dir:作品展目录
        
    Returns:
        角色名译名字典 {日文名: 中文译名}
        
    副作用：
        - 更新 .terms.json（术语表）
        - 更新 .alias.json（ASR误识别映射）
    """
    if not character_names:
        return {}
    
    print(f"\n[台本角色分析] 发现 {len(character_names)} 个角色名: {', '.join(sorted(character_names))}")
    
    # === 先检查身份称呼映射表 ===
    terms = {}
    remaining_names = set()
    
    for name in character_names:
        if name in ROLE_TRANSLATIONS:
            terms[name] = ROLE_TRANSLATIONS[name]
            print(f"  [身份称呼] {name} → {ROLE_TRANSLATIONS[name]} (预设译名)")
        else:
            remaining_names.add(name)
    
    # 如果所有角色名都是身份称呼，直接返回
    if not remaining_names:
        print(f"  [台本角色分析] 全部为身份称呼，使用预设译名")
        return terms
    
    # === 对剩余的非身份称呼角色名调用 LLM ===
    print(f"  [台本角色分析] 剩余 {len(remaining_names)} 个角色名需 LLM 分析: {', '.join(sorted(remaining_names))}")
    
    # 构建简短 prompt
    content = SCRIPTBOOK_CHARACTER_PROMPT + "\n\n角色名列表：\n"
    for name in sorted(remaining_names):
        content += f"- {name}\n"
    
    try:
        # 调用 LLM API
        _raw_params = _api_cfg.get("generation_params", {})
        gen_params = {k: v for k, v in _raw_params.items()
                      if k in ('temperature', 'top_p', 'top_k', 'presence_penalty', 'frequency_penalty',
                               'stop', 'logit_bias', 'user', 'reasoning_effort')}
        gen_params['max_tokens'] = 4096
        
        api_response = client.chat.completions.create(
            model=_api_cfg["model"],
            messages=[{"role": "user", "content": content}],
            timeout=_api_cfg.get("timeout", 2000),
            **gen_params
        )
        
        response = api_response.choices[0].message.content.strip()
        
        # 打印 LLM 原始返回内容（便于调试）
        print(f"  [台本角色分析] LLM 原始返回:")
        print(f"    {response[:500]}{'...' if len(response) > 500 else ''}")
        
        # 解析 JSON（新格式：{"characters":[{"name":"角色名1","reading":"片假名读音","translation":"中文译名"},...]}）
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            result = json.loads(json_match.group())
            characters = result.get('characters', [])
            
            if characters:
                print(f"  [台本角色分析] LLM 返回 {len(characters)} 个角色信息")
                
                # 构建角色读音映射（用于检测ASR变体）
                character_readings = {}  # {name: reading}
                for char_info in characters:
                    name = char_info.get('name', '')
                    reading = char_info.get('reading', '')
                    translation = char_info.get('translation', '')
                    
                    if name and translation:
                        terms[name] = translation
                        print(f"    {name} → {translation} (读音: {reading})")
                        if reading:
                            character_readings[name] = reading
                
                # === 新增：检测可能的ASR变体 ===
                # 如果有多个角色名读音相似，可能是同一角色的不同写法
                # 例如：「雫葵」和「熾月」读音都是「シズキ」
                if len(character_readings) >= 2 and _PYOPENJTALK_AVAILABLE:
                    alias_list = load_alias(work_dir)
                    existing_alias_pairs = {(a.get('alias'), a.get('target')) for a in alias_list}
                    
                    names = list(character_readings.keys())
                    for i in range(len(names)):
                        for j in range(i + 1, len(names)):
                            name1, name2 = names[i], names[j]
                            reading1, reading2 = character_readings[name1], character_readings[name2]
                            
                            # 如果读音相同，可能是同一角色的不同写法
                            if reading1 and reading2:
                                # 计算读音相似度
                                sim = calculate_phoneme_similarity(reading1, reading2)
                                
                                if sim >= 0.8:  # 读音高度相似
                                    # 选择译名更常用的作为target
                                    # 优先选择有汉字写法的
                                    if re.search(r'[\u4e00-\u9fff]', name1) and not re.search(r'[\u4e00-\u9fff]', name2):
                                        target, alias = name1, name2
                                    elif re.search(r'[\u4e00-\u9fff]', name2) and not re.search(r'[\u4e00-\u9fff]', name1):
                                        target, alias = name2, name1
                                    else:
                                        # 都有汉字或都没有，选择terms中已有的
                                        target, alias = name1, name2
                                    
                                    # 检查是否已存在
                                    if (alias, target) not in existing_alias_pairs:
                                        alias_item = {
                                            "alias": alias,
                                            "target": target,
                                            "confidence": sim,
                                            "source": "scriptbook_character_analysis"
                                        }
                                        alias_list.append(alias_item)
                                        print(f"    [检测到变体] {alias} → {target} (读音相似度: {sim:.1%})")
                    
                    # 保存alias表
                    if alias_list:
                        save_alias(work_dir, alias_list)
                        print(f"  [台本角色分析] 已更新 alias 表")
                
                return terms
            else:
                print(f"  ⚠ LLM 返回格式错误，characters 为空")
                return terms
        else:
            print(f"  ⚠ LLM 返回格式错误，未找到 JSON")
            return terms  # 返回预设译名
            
    except Exception as e:
        print(f"  ⚠ LLM 分析失败: {e}")
        return terms  # 返回预设译名


# ==================== 工具函数 ====================

def is_chinese_text(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]', text))


def is_japanese_text(text: str) -> bool:
    return bool(re.search(r'[\u3040-\u309f\u30a0-\u30fa\u30fd-\u30ff]', text))


def extract_subtitle_texts(content: str, ext: str) -> list[str]:
    lines = content.split('\n')
    lyric_texts = []

    if ext == '.lrc':
        tag_pattern = re.compile(r'^(\[.*?\])\s*(.*)')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            match = tag_pattern.match(line)
            if match:
                tags = match.group(1)
                text_after = match.group(2)
                if re.search(r'\[\d+:\d{2}\.\d{2,3}\]', tags):
                    lyric_texts.append(text_after)

    elif ext == '.srt':
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.isdigit():
                i += 1
                if i < len(lines) and '-->' in lines[i]:
                    i += 1
                    while i < len(lines) and lines[i].strip():
                        lyric_texts.append(lines[i].strip())
                        i += 1
            i += 1

    elif ext == '.vtt':
        i = 0
        while i < len(lines) and not lines[i].strip().startswith('00:'):
            i += 1
        while i < len(lines):
            line = lines[i].strip()
            if not line or line.startswith('NOTE'):
                i += 1
                continue
            if '-->' in line:
                i += 1
                while i < len(lines):
                    next_line = lines[i].strip()
                    if not next_line or '-->' in next_line:
                        break
                    lyric_texts.append(next_line)
                    i += 1
            else:
                i += 1

    return lyric_texts


def detect_lrc_language(file_path: Path) -> str:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except (UnicodeDecodeError, Exception):
        return "unknown"

    ext = file_path.suffix.lower()
    lyric_texts = extract_subtitle_texts(content, ext)
    if not lyric_texts:
        return "empty"

    all_text = '\n'.join(lyric_texts)
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
        return "chinese" if all_text.strip() else "empty"


def get_ja_path(lrc_path: Path) -> Path:
    return lrc_path.with_name(f"{lrc_path.stem}.ja{lrc_path.suffix}")


# ==================== 翻译核心 ====================

# 日语句子结束标记（按优先级）
_SENTENCE_ENDERS = ('。', '？', '?', '！', '!', '…', '」', ')', '）', '♡', '♪', '～')

# ==================== 台本清洗规则 ====================
# 纯数字/页码残留行（如"11………"、"2…"、"11 /"）
_PAGE_NUMBER_PATTERN = re.compile(r'^\d+\s*[／/…\.\s]*$')

# 台词行首的页码数字（如"6…えっ…ほんとに録るんですか…？"）
_LEADING_PAGE_NUMBER = re.compile(r'^\d+\s*…\s*')

# 注音假名单行（纯假名，但排除常见语气词/拟声词）
_FURIGANA_PATTERN = re.compile(r'^[ぁ-んァ-ンー\s]+$')

# 常见语气词/拟声词，不应被当作注音删除
_COMMON_INTERJECTIONS = {
    'はいはい', 'うんうん', 'えー', 'あー', 'おー', 'うー', 'えっ', 'あっ', 'おっ',
    'んー', 'はぁ', 'ふぅ', 'ふふ', 'へへ', 'ひひ', 'ほほ', 'ふっ', 'はっ',
    'ちゅ', 'ちゅっ', 'ちゅる', 'ちゅぱ', 'ちゅぽ', 'ぺろ', 'ぺろぺろ',
    'くちゅ', 'くちゅっ', 'くちゅくちゅ', 'じゅる', 'じゅるる', 'じゅぽ',
    'ぴゅっ', 'ぴゅぴゅ', 'びゅっ', 'びゅびゅ', 'ぷしゅ', 'ぷしゅっ',
    'んっ', 'んん', 'んんっ', 'あん', 'あんっ', 'はぁん', 'ひゃん', 'きゃん',
    'いやぁ', 'いやん', 'だめぇ', 'やだぁ', 'もぉ', 'はぁぁ', 'ひぃ',
    'くぅ', 'くぅん', 'わぁ', 'わぁん', 'にゃ', 'にゃん', 'にゃぁ',
    'ぎゅっ', 'ぎゅぎゅ', 'ぎゅー', 'ぎゅぅ', 'ぎゅぅっ',
    'むぎゅ', 'むぎゅっ', 'むぎゅむぎゅ', 'むにゅ', 'むにゅっ',
    'ぷに', 'ぷにっ', 'ぷにぷに', 'ぷにゅ', 'ぷにゅっ',
    'くぱぁ', 'くぱっ', 'ぱくっ', 'ぱくぱく', 'ぺろり', 'ぺろっ',
    'れろ', 'れろれろ', 'れる', 'れるれる',
    'ぴちゃ', 'ぴちゃっ', 'ぴちゃぴちゃ', 'ぴちゅ', 'ぴちゅっ',
    'びちゃ', 'びちゃっ', 'びちゃびちゃ', 'びちゅ', 'びちゅっ',
    'ぷちゅ', 'ぷちゅっ', 'ぷちゅぷちゅ', 'ぷちゃ', 'ぷちゃっ',
}

# 行内多余空格/连字符清理
_EXTRA_SPACES = re.compile(r'\s+')


def find_sentence_boundary(lyrics: list[str]) -> int:
    """寻找最佳分割点，优先在句子边界处切断
    
    返回分割索引（前段包含 lyrics[:idx]，后段从 lyrics[idx:] 开始）
    """
    n = len(lyrics)
    if n <= 2:
        return n // 2

    target = n // 2
    best_idx = target
    best_score = -1

    # 在 target 附近 ±20% 范围内搜索最佳分割点
    search_range = max(3, n // 5)
    start = max(1, target - search_range)
    end = min(n - 1, target + search_range)

    for i in range(start, end + 1):
        score = 0
        prev_line = lyrics[i - 1].strip()
        next_line = lyrics[i].strip() if i < n else ""

        # 优先：前一行以句末标点结尾
        if prev_line.endswith(_SENTENCE_ENDERS):
            score += 10
        # 次优：前一行较长（可能是完整句子）
        if len(prev_line) >= 8:
            score += 3
        # 加分：当前行是空行（自然段落分隔）
        if not next_line:
            score += 8
        # 减分：在对话中间切断（前一行没有标点且较短）
        if len(prev_line) < 5 and not prev_line.endswith(_SENTENCE_ENDERS):
            score -= 5
        # 减分：离 target 越远分数越低
        score -= abs(i - target) * 0.5

        if score > best_score:
            best_score = score
            best_idx = i

    return best_idx


# 日语句子切分标记（按优先级，逗号也作为软切分点）
_SPLIT_ENDERS = ('。', '？', '?', '！', '!', '…', '」', ')', '）', '♡', '♪', '～')
_SPLIT_SOFT = ('、', '，')


def split_long_line(line: str, max_chars: int = 100) -> list[str]:
    """按日语句子边界切分超长行，避免翻译后膨胀导致 LLM 拆行

    切分策略：
    1. 优先在句末标点（。、？、！等）后切分
    2. 次选在逗号（、）后切分
    3. 都找不到则在 max_chars 处硬切
    """
    if len(line) <= max_chars:
        return [line]

    parts = []
    remaining = line

    while len(remaining) > max_chars:
        best_split = -1
        # 搜索范围：max_chars 前后 30 字符
        search_start = max(0, max_chars - 30)
        search_end = min(len(remaining), max_chars + 30)

        # 优先：句末标点
        for i in range(search_end - 1, search_start - 1, -1):
            if remaining[i] in _SPLIT_ENDERS:
                best_split = i + 1
                break

        # 次优：逗号（仅当找不到句末标点时）
        if best_split == -1:
            for i in range(search_end - 1, search_start - 1, -1):
                if remaining[i] in _SPLIT_SOFT:
                    best_split = i + 1
                    break

        # 兜底：硬切
        if best_split == -1 or best_split <= search_start:
            best_split = max_chars

        parts.append(remaining[:best_split].strip())
        remaining = remaining[best_split:].strip()

    if remaining:
        parts.append(remaining)

    return parts


def merge_split_translations(translated: list[str], origin_map: list[int], original_count: int) -> list[str]:
    """将切分后的翻译结果按原行索引合并

    参数:
        translated: 切分后每段的翻译结果
        origin_map: translated[i] 对应的原行索引
        original_count: 原始行数

    返回:
        合并后的列表，长度等于 original_count
    """
    from collections import defaultdict
    groups = defaultdict(list)

    for text, origin_idx in zip(translated, origin_map):
        groups[origin_idx].append(text)

    result = []
    for i in range(original_count):
        if i in groups:
            # 用空格连接多段，去除多余空格
            merged = " ".join(t.strip() for t in groups[i] if t.strip())
            result.append(merged)
        else:
            result.append("")

    return result


# Bug 收集文件夹路径
BUG_COLLECTION_DIR = SCRIPT_DIR / "Hde_G_bug_lrc_collection"

def collect_bug_lrc(source_path: Path, retry_count: int) -> None:
    """收集出错的 lrc 文件到 bug 收集文件夹
    
    参数:
        source_path: 出错的源文件路径
        retry_count: 重试次数
    """
    if retry_count < 3:
        return
    
    # 确保 bug 收集文件夹存在
    BUG_COLLECTION_DIR.mkdir(parents=True, exist_ok=True)
    
    # 构造目标文件名：原文件名 + _retry_N
    target_name = f"{source_path.stem}_retry_{retry_count}{source_path.suffix}"
    target_path = BUG_COLLECTION_DIR / target_name
    
    # 复制文件
    try:
        shutil.copy2(source_path, target_path)
        print(f"    [Bug收集] 已收集出错文件到: {target_path}")
    except Exception as e:
        print(f"    [Bug收集] 复制文件失败: {e}")


def translate_lyrics_batch(lyrics: list[str], terms: dict[str, str] = None, alias_list: list[dict] = None,
                           worldview: dict = None, retry_tracker: dict = None, 
                           scriptbook_lines: list[str] = None) -> list[str]:
    """批量翻译，支持世界观、术语表、alias表和台本参考
    
    Args:
        lyrics: 待翻译的歌词列表
        terms: 术语表 {日文core: 中文译名}
        alias_list: ASR误识别参考列表
        worldview: 世界观信息字典
        retry_tracker: 用于追踪重试次数的字典（可选）
        scriptbook_lines: 台本台词列表（用于ASR纠错）
    """
    import threading

    if not lyrics:
        return []
    if all(not line.strip() for line in lyrics):
        return lyrics

    terms = terms or {}
    alias_list = alias_list or []
    worldview = worldview or {}

    numbered = []
    for i, line in enumerate(lyrics, 1):
        if line.strip():
            numbered.append(f"{i:02d}: {line}")
        else:
            # 空行必须明确标记，避免LLM跳过
            numbered.append(f"{i:02d}: [EMPTY_LINE]")

    # 特别标记末尾空行，提醒LLM不要跳过
    trailing_empty_count = 0
    for i in range(len(numbered) - 1, -1, -1):
        if '[EMPTY_LINE]' in numbered[i]:
            trailing_empty_count += 1
        else:
            break
    if trailing_empty_count > 0:
        # 在最后添加一个提示，强调末尾空行
        numbered.append(f"[注意：最后{trailing_empty_count}行为空行，必须全部输出，编号到{len(lyrics)}]")

    text_to_translate = "\n".join(numbered)

    # 使用更大的 max_tokens 用于翻译
    # 只保留 OpenAI API 接受的参数，过滤掉自定义参数
    _raw_params = _api_cfg.get("generation_params", {})
    gen_params = {k: v for k, v in _raw_params.items()
                  if k in ('temperature', 'top_p', 'top_k', 'presence_penalty', 'frequency_penalty',
                           'stop', 'logit_bias', 'user', 'reasoning_effort')}
    gen_params['max_tokens'] = _raw_params.get("max_tokens_translate", 131072)
    timeout = _api_cfg.get("timeout_translate", 600)

    # 构建 prompt
    prompt_parts = [SYSTEM_PROMPT]

    # 添加世界观背景
    worldview_prompt = build_worldview_prompt(worldview)
    if worldview_prompt:
        prompt_parts.append(worldview_prompt)

    terms_prompt = build_terms_prompt(terms)
    if terms_prompt:
        prompt_parts.append(terms_prompt)

    alias_prompt = build_alias_prompt(alias_list)
    if alias_prompt:
        prompt_parts.append(alias_prompt)

    # 添加台本参考内容（如果启用且存在）
    if USE_SCRIPTBOOK_FOR_TRANSLATION and scriptbook_lines:
        # 使用差异检测版本，只发送ASR与台本有差异的部分
        scriptbook_prompt = build_scriptbook_prompt(scriptbook_lines, None, lyrics)
        if scriptbook_prompt:
            prompt_parts.append(scriptbook_prompt)

    prompt_parts.append(text_to_translate)

    full_prompt = "\n\n".join(prompt_parts)

    messages = [
        {"role": "user", "content": full_prompt}
    ]

    # 打印翻译进度信息
    total_chars = sum(len(l) for l in lyrics)
    estimated_time = max(30, len(lyrics) // 10)  # 估计时间
    print(f"    [翻译] 发送请求: {len(lyrics)} 行, {total_chars} 字符")
    print(f"    [翻译] 预计耗时: {estimated_time} 秒, timeout={timeout}s, max_tokens={gen_params['max_tokens']}")

    for attempt in range(RETRY_COUNT):
        try:
            if attempt > 0:
                # 记录重试次数
                if retry_tracker is not None:
                    retry_tracker['count'] = max(retry_tracker.get('count', 0), attempt + 1)
                
                # Debug 模式下打印重试信息
                if DEBUG_MODE:
                    print(f"    [DEBUG] 重试第 {attempt} 次，当前追踪器计数: {retry_tracker['count'] if retry_tracker else 'N/A'}")
                
                nonce = f"[nonce:{random.randint(1000, 9999)}]\n"
                retry_prompt = SYSTEM_PROMPT + "\n\n"
                if worldview_prompt:
                    retry_prompt += worldview_prompt + "\n\n"
                if terms_prompt:
                    retry_prompt += terms_prompt + "\n\n"
                if alias_prompt:
                    retry_prompt += alias_prompt + "\n\n"
                retry_prompt += nonce + text_to_translate
                messages[0]["content"] = retry_prompt
                print(f"    [翻译] 重试第 {attempt} 次...")

            # 心跳打印机制
            heartbeat_stop = threading.Event()
            heartbeat_count = [0]

            def print_heartbeat():
                while not heartbeat_stop.is_set():
                    heartbeat_stop.wait(10)  # 每10秒打印一次
                    if not heartbeat_stop.is_set():
                        heartbeat_count[0] += 10
                        print(f"    [翻译进行中] 已等待 {heartbeat_count[0]} 秒...")

            heartbeat_thread = threading.Thread(target=print_heartbeat, daemon=True)
            heartbeat_thread.start()

            start_time = time.time()

            try:
                response = client.chat.completions.create(
                    model=_api_cfg["model"],
                    messages=messages,
                    timeout=timeout,
                    **gen_params
                )
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join(timeout=1)

            elapsed_time = time.time() - start_time
            print(f"    [翻译] 收到响应 (耗时 {elapsed_time:.1f} 秒)")

            usage = response.usage
            hit_tokens = getattr(usage, 'prompt_cache_hit_tokens', 0)
            miss_tokens = getattr(usage, 'prompt_cache_miss_tokens', 0)
            total_prompt = getattr(usage, 'prompt_tokens', 0)
            completion_tokens = getattr(usage, 'completion_tokens', 0)

            global total_hit_tokens, total_miss_tokens, total_prompt_tokens, total_completion_tokens
            total_hit_tokens += hit_tokens
            total_miss_tokens += miss_tokens
            total_prompt_tokens += total_prompt
            total_completion_tokens += completion_tokens

            if hit_tokens > 0:
                hit_rate = (hit_tokens / total_prompt * 100) if total_prompt > 0 else 0
                print(f" 🔥 API 缓存命中: {hit_tokens} tokens ({hit_rate:.1f}%)")
            else:
                print(f" 📌 API 缓存未命中 | Prompt Tokens: {total_prompt}")

            raw_output = response.choices[0].message.content.strip()
            lines = raw_output.split("\n")

            translated = []
            unmatched_lines = []
            # 匹配编号格式：2-3位数字 + 冒号 + 内容（支持超过100行）
            line_pattern = re.compile(r"^(\d{2,3}):\s*(.*)$")
            # 用于清理内容中多余的编号前缀（如 LLM 输出 "100: 100: 内容"）
            inner_idx_pattern = re.compile(r"^\d{2,3}:\s*")
            seen_indices = set()

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                match = line_pattern.match(line)
                if match:
                    idx_str = match.group(1)
                    content = match.group(2).strip()
                    # 去掉内容中可能多余的编号前缀
                    content = inner_idx_pattern.sub('', content)
                    idx = int(idx_str)
                    # 编号必须在有效范围内 (1 ~ len(lyrics))
                    if idx < 1 or idx > len(lyrics):
                        unmatched_lines.append(line)
                        continue
                    # 重复编号放入未匹配，后续尝试分配给缺失编号
                    if idx in seen_indices:
                        unmatched_lines.append(line)
                        continue
                    seen_indices.add(idx)
                    if content in ("[EMPTY_LINE]", "[空行]", ""):
                        translated.append((idx, ""))
                    else:
                        translated.append((idx, content))
                else:
                    unmatched_lines.append(line)

            # 尝试将未匹配行分配给缺失编号（同时清理内容中的编号前缀）
            if unmatched_lines and len(translated) < len(lyrics):
                missing_indices = [i for i in range(1, len(lyrics) + 1) if i not in seen_indices]
                for i, line_content in enumerate(unmatched_lines):
                    if i < len(missing_indices):
                        idx = missing_indices[i]
                        # 清理内容中可能存在的编号前缀
                        cleaned_content = inner_idx_pattern.sub('', line_content)
                        translated.append((idx, cleaned_content))
                        seen_indices.add(idx)
                        print(f"    [自动修复] 将未匹配行分配给 {idx:02d}: {cleaned_content[:60]}")

            # 按编号排序，确保顺序正确
            translated.sort(key=lambda x: x[0])
            # 提取纯文本列表
            translated_texts = [text for _, text in translated]

            if len(translated_texts) < len(lyrics) * 0.5:
                print(f" ⚠ API 返回严重不完整: 期望 {len(lyrics)}，实际 {len(translated_texts)}")
                if attempt < RETRY_COUNT - 1:
                    time.sleep(2 * (attempt + 1))
                    continue

            if len(translated_texts) != len(lyrics):
                diff = len(lyrics) - len(translated_texts)
                print(
                    f" ⚠ 行数不匹配: 期望 {len(lyrics)}，实际 {len(translated_texts)} (尝试 {attempt + 1}/{RETRY_COUNT})")
                
                # 如果只差 1-3 行，尝试智能补齐（通常是 LLM 合并了相邻的短行或跳过了空行）
                if 1 <= diff <= 3 and len(translated_texts) > 0:
                    print(f"    [智能修复] 行数只差 {diff} 行，尝试自动补齐...")
                    # 补齐缺失的行（用空字符串）
                    translated_texts.extend([""] * diff)
                    print(f"    [智能修复] 已补齐 {diff} 行")
                
                # DEBUG: 仅在 debug 模式下显示完整输入输出
                if DEBUG_MODE:
                    print("  ── 原始输入(全部) ──")
                    for i, ly in enumerate(lyrics, 1):
                        print(f"    {i:02d}: {ly}")
                    print("  ── API输出(全部) ──")
                    for i, line in enumerate(lines, 1):
                        print(f"    {i:02d}: {line}")
                    unmatched = [line for line in lines if not line_pattern.match(line.strip())]
                    if unmatched:
                        print("  ── 未匹配编号格式的行(全部) ──")
                        for line in unmatched:
                            print(f"    > {line}")
                
                # 如果补齐后行数正确，直接返回
                if len(translated_texts) == len(lyrics):
                    print(f"    [智能修复] 行数已对齐，继续处理...")
                    translated = translated_texts
                    # 跳过剩余的重试逻辑，直接验证并返回
                    valid = True
                    for orig, trans in zip(lyrics, translated):
                        if orig.strip() == "" and trans != "":
                            valid = False
                            break
                    if valid:
                        return translated
                
                if attempt < RETRY_COUNT - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                else:
                    # 按句子边界智能分割（带重叠上下文）
                    if len(lyrics) > 1:
                        split_idx = find_sentence_boundary(lyrics)
                        overlap = min(3, split_idx // 3, (len(lyrics) - split_idx) // 3)
                        left_end = split_idx + overlap
                        right_start = split_idx - overlap
                        print(
                            f" ⚠ 多次重试后仍不匹配，按句子边界分割 ({len(lyrics)} -> {left_end}+{len(lyrics) - right_start}, 重叠{overlap * 2}行)...")
                        left = translate_lyrics_batch(lyrics[:left_end], terms, alias_list, worldview)
                        right = translate_lyrics_batch(lyrics[right_start:], terms, alias_list, worldview)
                        return left[:split_idx] + right[overlap:]
                    print(" ⚠ 多次重试后仍不匹配，强制对齐...")
                    if len(translated_texts) > len(lyrics):
                        translated_texts = translated_texts[:len(lyrics)]
                    else:
                        translated_texts.extend([""] * (len(lyrics) - len(translated_texts)))

            # 使用处理后的列表
            translated = translated_texts

            valid = True
            for orig, trans in zip(lyrics, translated):
                if orig.strip() == "" and trans != "":
                    valid = False
                    break
            if valid:
                return translated

        except Exception as e:
            print(f" API调用失败 (尝试 {attempt + 1}/{RETRY_COUNT}): {e}")
            if attempt < RETRY_COUNT - 1:
                time.sleep(5 * (attempt + 1))

    # 全部重试失败，按句子边界智能分割（带重叠上下文）
    if len(lyrics) > 1:
        split_idx = find_sentence_boundary(lyrics)
        overlap = min(3, split_idx // 3, (len(lyrics) - split_idx) // 3)
        left_end = split_idx + overlap
        right_start = split_idx - overlap
        print(
            f" ❌ 翻译失败，按句子边界分割 ({len(lyrics)} -> {left_end}+{len(lyrics) - right_start}, 重叠{overlap * 2}行)...")
        left = translate_lyrics_batch(lyrics[:left_end], terms, alias_list, worldview)
        right = translate_lyrics_batch(lyrics[right_start:], terms, alias_list, worldview)
        # 去重：重叠区域取 right 的结果（因为 right 有更多前文上下文）
        return left[:split_idx] + right[overlap:]

    print(" ❌ 翻译失败，保留原始歌词")
    return lyrics


def translate_lrc_file(source_path: Path, target_path: Path, terms: dict[str, str], alias_list: list[dict],
                       worldview: dict = None, scriptbook_lines: list[str] = None) -> tuple[bool, list[str], list[str]]:
    """翻译单个字幕文件
    
    Args:
        source_path: 源字幕文件路径
        target_path: 目标字幕文件路径
        terms: 术语表
        alias_list: ASR误识别参考列表
        worldview: 世界观信息字典
        scriptbook_lines: 台本台词列表（用于ASR纠错）
    
    返回: (是否成功, 原始歌词列表, 翻译结果列表)
    """
    worldview = worldview or {}
    ext = source_path.suffix.lower()
    print(f"翻译: {source_path.name}")
    
    # 重试次数追踪器
    retry_tracker = {'count': 0}

    try:
        with open(source_path, 'r', encoding='utf-8') as f:
            raw_lines = f.readlines()
    except UnicodeDecodeError:
        print(f"  跳过非 UTF-8 文件: {source_path}")
        return False

    final_lines = []
    lyric_indices = []
    original_lyrics = []
    time_tags = []

    if ext == '.lrc':
        tag_pattern = re.compile(r'^(\[.*?\])\s*(.*)')
        time_pattern = re.compile(r'\[\d{1,2}:\d{2}\.\d{2,3}\]')
        for line in raw_lines:
            line_stripped = line.rstrip('\n\r')
            match = tag_pattern.match(line_stripped)
            if match:
                tags = match.group(1)
                text_after = match.group(2)
                if time_pattern.search(tags):
                    final_lines.append(None)
                    lyric_indices.append(len(final_lines) - 1)
                    original_lyrics.append(text_after)
                    time_tags.append(tags)
                else:
                    final_lines.append(line_stripped + '\n')
            else:
                if lyric_indices and line_stripped.strip():
                    original_lyrics[-1] += " " + line_stripped.strip()
                    continue
                final_lines.append(line_stripped + '\n')

    elif ext == '.srt':
        i = 0
        while i < len(raw_lines):
            stripped = raw_lines[i].strip()
            if stripped.isdigit():
                final_lines.append(raw_lines[i])
                i += 1
                if i < len(raw_lines) and '-->' in raw_lines[i]:
                    final_lines.append(raw_lines[i])
                    i += 1
                    while i < len(raw_lines) and raw_lines[i].strip():
                        text = raw_lines[i].rstrip('\n\r')
                        final_lines.append(None)
                        lyric_indices.append(len(final_lines) - 1)
                        original_lyrics.append(text)
                        time_tags.append("")
                        i += 1
                continue
            final_lines.append(raw_lines[i])
            i += 1

    elif ext == '.vtt':
        i = 0
        while i < len(raw_lines):
            stripped = raw_lines[i].strip()
            if not stripped or stripped.startswith('NOTE') or stripped == 'WEBVTT' or stripped.startswith('STYLE'):
                final_lines.append(raw_lines[i])
                i += 1
                continue
            if '-->' in stripped:
                final_lines.append(raw_lines[i])
                i += 1
                while i < len(raw_lines):
                    next_stripped = raw_lines[i].strip()
                    if not next_stripped or '-->' in next_stripped:
                        break
                    text = raw_lines[i].rstrip('\n\r')
                    final_lines.append(None)
                    lyric_indices.append(len(final_lines) - 1)
                    original_lyrics.append(text)
                    time_tags.append("")
                    i += 1
                continue
            final_lines.append(raw_lines[i])
            i += 1

    if not original_lyrics:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, 'w', encoding='utf-8') as f:
            f.writelines(raw_lines)
        print("  无歌词内容，直接复制")
        return True

    # 预处理：切分超长行，避免翻译后膨胀导致 LLM 拆行
    split_lyrics = []
    split_map = []  # split_lyrics[i] 对应 original_lyrics 的索引
    split_count = 0
    for i, lyric in enumerate(original_lyrics):
        parts = split_long_line(lyric, max_chars=100)
        for part in parts:
            split_lyrics.append(part)
            split_map.append(i)
        if len(parts) > 1:
            split_count += 1
    if split_count > 0:
        print(f"  预处理：切分 {split_count} 个超长行 ({len(original_lyrics)} -> {len(split_lyrics)} 行)")

    # 整文件翻译（不再分批）
    print(f"  翻译整文件 (行数: {len(split_lyrics)}, 字符: {sum(len(l) for l in split_lyrics)})")
    
    translated_split = translate_lyrics_batch(split_lyrics, terms, alias_list, worldview, retry_tracker, scriptbook_lines)
    
    # 收集出错的文件（如果重试次数 >= 3）
    if retry_tracker['count'] >= 3:
        collect_bug_lrc(source_path, retry_tracker['count'])

    # 合并切分后的翻译结果
    translated_lyrics = merge_split_translations(translated_split, split_map, len(original_lyrics))

    for idx, tags, new_lyric in zip(lyric_indices, time_tags, translated_lyrics):
        if ext == '.lrc':
            final_lines[idx] = f"{tags}{new_lyric}\n" if new_lyric else f"{tags}\n"
        else:
            final_lines[idx] = f"{new_lyric}\n" if new_lyric else '\n'

    final_lines = [line if line is not None else '\n' for line in final_lines]

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, 'w', encoding='utf-8') as f:
        f.writelines(final_lines)

    cost_hit = (total_hit_tokens / 1_000_000) * PRICE_HIT_PER_1M
    cost_miss = (total_miss_tokens / 1_000_000) * PRICE_MISS_PER_1M
    cost_completion = (total_completion_tokens / 1_000_000) * PRICE_COMPLETION_PER_1M
    cost_total = cost_hit + cost_miss + cost_completion
    print(f" ✓ 完成 -> {target_path.name}")
    print(f" 💰 累计费用: 命中{cost_hit:.4f}元 + 未命中{cost_miss:.4f}元 + 输出{cost_completion:.4f}元 = {cost_total:.4f}元")
    print(f"    (命中{total_hit_tokens}tokens / 未命中{total_miss_tokens}tokens / 输出{total_completion_tokens}tokens)")

    # 返回原始歌词和翻译结果，用于动态术语更新
    return True, original_lyrics, translated_lyrics


def translate_lrc_file_simple(source_path: Path, target_path: Path, terms: dict[str, str], alias_list: list[dict],
                              worldview: dict = None) -> bool:
    """简化版 translate_lrc_file，只返回是否成功（兼容旧接口）"""
    success, _orig, _trans = translate_lrc_file(source_path, target_path, terms, alias_list, worldview)
    return success


# ==================== 作品级预处理 ====================

def load_work_terms(work_dir: Path) -> tuple[dict[str, str], list[dict], dict]:
    """加载已有的术语表、alias表和世界观
    
    当作品已完成所有翻译时，不需要重新分析，直接加载已有数据。
    
    返回: (terms, alias_list, worldview) 或 (None, None, None) 如果不存在
    """
    terms_path = get_terms_path(work_dir)
    alias_path = get_alias_path(work_dir)
    worldview_path = get_worldview_path(work_dir)
    
    if terms_path.exists() and alias_path.exists() and worldview_path.exists():
        terms = load_terms(work_dir)
        alias_list = load_alias(work_dir)
        worldview = load_worldview(work_dir)
        return terms, alias_list, worldview
    
    # 如果文件不存在，返回None表示需要分析
    return None, None, None


def analyze_work_terms(work_dir: Path) -> tuple[dict[str, str], list[dict], dict]:
    """分析作品目录，生成世界观、术语表和 alias 表
    
    返回: (terms, alias_list, worldview)
    """
    terms_path = get_terms_path(work_dir)
    alias_path = get_alias_path(work_dir)
    worldview_path = get_worldview_path(work_dir)

    # 如果已存在术语表、alias表和世界观，直接加载
    if terms_path.exists() and alias_path.exists() and worldview_path.exists():
        terms = load_terms(work_dir)
        alias_list = load_alias(work_dir)
        worldview = load_worldview(work_dir)
        print(f"  加载已有术语表: {len(terms)} 个")
        print(f"  加载已有 alias 表: {len(alias_list)} 个")
        print(f"  加载已有世界观: {worldview.get('worldview', '')[:50]}...")
        return terms, alias_list, worldview

    # 检测是否为 freetalk 或热门CV
    is_freetalk, cv_name = is_freetalk_context(work_dir)
    if is_freetalk:
        if cv_name:
            print(f"  [检测到 FreeTalk/CV] 目录包含热门CV「{cv_name}」，跳过世界观构建和分词")
        else:
            print(f"  [检测到 FreeTalk] 目录包含 freetalk 关键词，跳过世界观构建和分词")
        # FreeTalk 模式：返回空术语表，不构建世界观
        return {}, [], {}

    if not _FUGASHI_AVAILABLE:
        print("  ⚠ fugashi 未安装，跳过术语分析")
        return {}, [], {}

    print("  正在分析作品...", flush=True)

    # 先从文件名提取角色名候选（高置信度）
    print("    [文件名] 正在提取角色名候选...", flush=True)
    filename_names = extract_names_from_filenames(work_dir)
    if filename_names:
        print(f"    [文件名] 发现角色名候选: {', '.join(list(filename_names)[:5])}", flush=True)

    # 使用新的抽样函数：抽取所有 .ja.lrc 文件
    print("    [1/5] 抽样所有 .ja.lrc 文件...", flush=True)
    samples, cores = sample_all_lrc_files(work_dir, lines_per_file=40)
    print(f"    [1/5] 抽样完成: {len(samples)} 个文件样本, {len(cores)} 个 core")

    # 2. 世界观分析（新增步骤）
    print("    [2/5] 调用 LLM 分析世界观...")
    worldview = analyze_worldview_with_llm(samples)
    if worldview:
        save_worldview(work_dir, worldview)
        print(f"    [2/5] 世界观已保存")

    # 3. 生成读音相似簇
    print("    [3/5] 正在比对读音相似性...")
    clusters = find_similar_reading_clusters(cores)
    print(f"    [3/5] 读音相似候选簇: {len(clusters)} 个")
    for i, c in enumerate(clusters[:5], 1):
        print(f"      簇{i}: {' / '.join(c)}")

    # 4. 调用 LLM 分析术语
    print("    [4/5] 调用 LLM 分析角色和术语...")
    terms, alias_list = analyze_characters_with_llm(cores, clusters, filename_names)

    # 5. 保存
    print("    [5/5] 保存分析结果...")
    if terms:
        save_terms(work_dir, terms)
        print(f"    已保存术语表({len(terms)}个)")

    if alias_list:
        save_alias(work_dir, alias_list)
        print(f"    已保存 alias 表({len(alias_list)}个)")

    print("  作品分析完成！")
    return terms, alias_list, worldview


# ==================== 台本翻译支持 ====================

# 台本文件关键词（用于识别台本文件）
SCRIPTBOOK_KEYWORDS = [
    '台本', 'だいほん', 'だい本', 'ダイホン', 'script', '台本付き',
    '仮台本', 'かり台本', '本編', 'ほんぺん',
    'セリフ初稿', 'せりふしょこう', 'シナリオ', 'しなりお',
    '演技指定', 'えんぎしてい', '射精タイミング', '全章'
]

# 台本文件扩展名
SCRIPTBOOK_EXTS = {'.txt', '.pdf'}


def _detect_pdf_order_issues(words: list, page_text: str) -> tuple[bool, str]:
    """检测PDF文本提取是否可能存在乱序问题
    
    返回: (是否乱序, 原因)
    
    注意：ASMR台本的音轨结尾部分可能包含大量拟声词、喘息声等，
    这些部分日文字符比例可能很低，但不应被视为乱序。
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
    
    # 3. 检查是否包含常见的ASMR拟声词/喘息声（这些是正常内容，不应被跳过）
    # 常见拟声词/喘息声模式
    asmr_patterns = [
        r'[ぁあんうーっ♡♪～]',  # 常见喘息声
        r'(cid:\d+)',           # 特殊字符编码（虽然不理想，但也是内容）
        r'[ひゅびゅぴゅ]',      # 射精拟声词
        r'[ちゅぺろれろ]',      # 口交拟声词
    ]
    has_asmr_content = any(re.search(p, page_text) for p in asmr_patterns)
    
    # 4. 检查提取的文本是否包含大量无意义字符（可能是乱码）
    # 统计日文字符比例
    japanese_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', page_text))
    total_chars = len(page_text.replace('\n', '').replace(' ', ''))
    
    if total_chars > 0:
        ja_ratio = japanese_chars / total_chars
        # 放宽阈值：只有日文字符比例极低（<5%）且没有ASMR内容时才跳过
        if ja_ratio < 0.05 and total_chars > 50 and not has_asmr_content:
            return True, f"日文字符比例极低({ja_ratio:.1%})且无ASMR内容"
    
    # 5. 检查文本块顺序是否合理（y坐标应该大致递增）
    # 如果y坐标剧烈波动，可能是乱序
    if len(y_positions) > 5:
        # 计算相邻文本块的y坐标差
        y_diffs = [abs(y_positions[i+1] - y_positions[i]) for i in range(len(y_positions)-1)]
        avg_diff = sum(y_diffs) / len(y_diffs)
        
        # 如果平均差值过大，可能是乱序
        if avg_diff > 200:  # 阈值可根据实际情况调整
            return True, f"文本块y坐标分布异常(平均差值{avg_diff:.1f})"
    
    return False, ""


def _pdf_to_docx_and_extract(pdf_path: Path, docx_path: Path) -> str:
    """将PDF转换为DOCX，然后提取纯文本
    
    返回提取的纯文本
    """
    try:
        from pdf2docx import Converter
        from docx import Document
        
        # 转换PDF到DOCX
        cv = Converter(str(pdf_path))
        cv.convert(str(docx_path))
        cv.close()
        
        # 从DOCX提取纯文本
        doc = Document(str(docx_path))
        text_parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                text_parts.append(para.text)
        
        return '\n'.join(text_parts)
    except Exception as e:
        print(f"  PDF转DOCX失败: {e}")
        return ""


def _extract_text_from_docx(docx_path: Path) -> str:
    """从DOCX文件中提取纯文本"""
    try:
        from docx import Document
        doc = Document(str(docx_path))
        text_parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                text_parts.append(para.text)
        return '\n'.join(text_parts)
    except Exception as e:
        print(f"  提取DOCX文本失败: {e}")
        return ""


def _merge_fragmented_text(text: str) -> str:
    """合并碎片化的文本（逐字符换行的情况）
    
    PyMuPDF提取某些PDF时会产生逐字符换行的情况，如：
    "こ\nら\n、\n僕\nた\nち\nの\n愛\nを"
    需要合并成正常的句子。
    
    策略：
    1. 识别碎片化模式（大量单字符行）
    2. 合并连续的非空行为一个段落
    3. 以空行作为段落分隔
    """
    if not text:
        return text
    
    lines = text.split('\n')
    
    # 统计单字符行的比例
    non_empty_lines = [l for l in lines if l.strip()]
    single_char_lines = sum(1 for line in non_empty_lines if len(line.strip()) == 1)
    total_lines = len(non_empty_lines)
    
    if total_lines == 0:
        return text
    
    # 如果单字符行比例低于70%，认为不是碎片化文本，直接返回
    if single_char_lines / total_lines < 0.7:
        return text
    
    # 合并碎片化文本：将连续的非空行合并成一个段落
    result_lines = []
    current_paragraph = []
    
    for line in lines:
        stripped = line.strip()
        
        if not stripped:
            # 空行：结束当前段落
            if current_paragraph:
                merged = ''.join(current_paragraph)
                if merged.strip():
                    result_lines.append(merged)
                current_paragraph = []
            # 不保留空行（碎片化文本中的空行通常是无意义的）
        else:
            # 非空行：累加到当前段落
            current_paragraph.append(stripped)
    
    # 处理最后一段
    if current_paragraph:
        merged = ''.join(current_paragraph)
        if merged.strip():
            result_lines.append(merged)
    
    return '\n'.join(result_lines)


def extract_pdf_text(file_path: Path) -> str:
    """提取PDF文件的文本内容
    
    策略：
    1. 首先尝试使用PyMuPDF直接提取文本（速度快，质量好）
    2. 对碎片化文本进行智能合并处理
    3. 如果失败，回退到pdfplumber直接提取（支持竖排日文）
    4. 如果pdfplumber也失败，使用pypdf作为最后手段
    """
    import tempfile
    import os
    
    # 确保 file_path 是 Path 对象
    if isinstance(file_path, str):
        file_path = Path(file_path)
    
    # 策略1：使用PyMuPDF直接提取文本（推荐，质量最好）
    try:
        import fitz  # PyMuPDF
        
        doc = fitz.open(str(file_path))
        text_parts = []
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            # 提取文本，保留布局
            text = page.get_text("text")
            if text.strip():
                text_parts.append(text)
        
        doc.close()
        
        if text_parts:
            raw_text = '\n\n'.join(text_parts)
            # 对碎片化文本进行合并处理
            processed_text = _merge_fragmented_text(raw_text)
            print(f"  [PyMuPDF] 成功提取文本 ({len(text_parts)} 页, {len(processed_text)} 字符)")
            return processed_text
        
    except Exception as e:
        print(f"  [PyMuPDF] 提取失败: {e}，尝试其他方法...")
    
    # 策略2：使用pdfplumber直接提取（支持竖排日文）
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
                # 竖排文本通常有90度或270度旋转，或者文本块高度>宽度
                vertical_count = 0
                horizontal_count = 0
                
                for w in words:
                    width = w.get('x1', 0) - w.get('x0', 0)
                    height = w.get('bottom', 0) - w.get('top', 0)
                    
                    # 判断文本方向
                    direction = w.get('direction', 0) if 'direction' in w else 0
                    
                    # 如果文本块高度明显大于宽度，或者旋转角度接近90/270度，认为是竖排
                    if direction in (90, 270, -90) or height > width * 1.5:
                        vertical_count += 1
                    else:
                        horizontal_count += 1
                
                # 判断页面方向
                is_vertical = vertical_count > horizontal_count
                
                if is_vertical:
                    # 竖排模式：从右到左、从上到下排序
                    # 先按x坐标降序（从右到左），再按y坐标升序（从上到下）
                    sorted_words = sorted(words, key=lambda w: (-w.get('x0', 0), w.get('top', 0)))
                    
                    # 将文本按列分组（相同x范围的为一列）
                    columns = {}
                    x_tolerance = 20  # x坐标容差
                    
                    for w in sorted_words:
                        x_key = round(w.get('x0', 0) / x_tolerance)
                        if x_key not in columns:
                            columns[x_key] = []
                        columns[x_key].append(w)
                    
                    # 按列从右到左处理
                    page_text_blocks = []
                    for x_key in sorted(columns.keys(), reverse=True):
                        col_words = sorted(columns[x_key], key=lambda w: w.get('top', 0))
                        col_text = ''.join(w.get('text', '') for w in col_words)
                        page_text_blocks.append(col_text)
                    
                    page_text = '\n'.join(page_text_blocks)
                else:
                    # 横排模式：从左到右、从上到下（默认顺序）
                    sorted_words = sorted(words, key=lambda w: (w.get('top', 0), w.get('x0', 0)))
                    page_text = ''.join(w.get('text', '') for w in sorted_words)
                
                # 检测乱序
                is_disordered, reason = _detect_pdf_order_issues(words, page_text)
                if is_disordered:
                    print(f"  ⚠ PDF第{page_num}页检测到乱序: {reason}，跳过此页")
                    # 跳过这一页，但继续处理其他页面
                    continue
                
                text_blocks.append(page_text)
        
        return '\n\n'.join(text_blocks)
        
    except ImportError:
        # pdfplumber未安装，回退到pypdf
        print(f"  [提示] pdfplumber未安装，使用pypdf（不支持竖排文本重排）")
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
            return text
        except Exception as e:
            print(f"  提取PDF文本失败: {file_path} - {e}")
            return ""
    except Exception as e:
        print(f"  提取PDF文本失败: {file_path} - {e}")
        return ""

# SE（音效）标记模式
SE_PATTERNS = [
    r'^SE[:：]',           # SE: 或 SE：
    r'^SE\s',              # SE 开头
    r'^\s*SE\s*[:：]?\s*', # 可选空格
    r'^【効果音[:：]',     # 【効果音：xxx】
    r'^【効果音：',        # 【効果音：xxx】
]

# 方向指示模式（如 #正面　距離近く　楽しそうに）
DIRECTION_PATTERN = r'^#[^\n]+$'

# 角色名标记模式（如 【まどか】）
CHARACTER_PATTERN = r'^【[^】]+】\s*$'

# 音轨标记模式（扩展版）
# 支持多种格式：
# 1. 【トラック1：xxx】 - 带括号的日文音轨标记
# 2. Tr1.、Tr.2、Tr3、TR6、Tr8. - 无括号的简写音轨标记
# 3. トラック1、トラック4； - 无括号的日文音轨标记
# 4. Track1、track1 - 英文音轨标记
# 5. 【标题文字数】格式 - 如【王女様の種搾り騎乗位おまんこ　4737文字】
# 6. ①②③【标题文字数】格式 - 如②【王女様と正常位でセックス練習ラブラブおまんこ　3927文字】
TRACK_PATTERN = r'^(【トラック\d+[：：][^\]]*】|Tr\.?\s*\d+[\.：:;\s]|TR\d+[\s\.：:;]|Tr\d+[\s\.：:;]|トラック\s*[０-９0-9]+[\s\.：:；]|[Tt]rack\s*\d+[\s\.：:]|[①②③④⑤⑥⑦⑧⑨⑩]?【[^】]+?\d+文字】)'

# 章节标题模式（如 《トラック１　エルフの子作り日》）
CHAPTER_TITLE_PATTERN = r'^《[^》]+》'

# 位置/演技指示模式（如 【右・中】、【右・中→右・近】、【移動：xxx】）
POSITION_PATTERN = r'^【[左右中正遠近・→]+】\s*$'

# 移动指示模式（如 【移動：右耳・近距離】）
MOVE_PATTERN = r'^【移動[:：]'

# 声音方式指示模式（如 【囁き】、【通常】、【大声】、【小声】）
VOICE_STYLE_PATTERN = r'^【(?:囁き|通常|大声|小声|吐息)\s*】\s*$'

# 射精标记模式（如 ★射精、【射精】）
EJACULATION_PATTERN = r'^(★|☆).*射精|^【射精】'

# 位置标记组合模式（如 【右・中→右・近】、【右耳・近距離】）
POSITION_COMPLEX_PATTERN = r'^【(?:右耳|左耳|右|左|中央|正面|遠距離|近距離|中距離)[・→・]+[^】]*】\s*$'

# 演技提示模式（行首的 （xxx））
ACTING_HINT_PATTERN = r'^（[^）]+）\s*$'

# 台词缩进标记（行首有空格的台词）
DIALOGUE_INDENT_PATTERN = r'^\s+[^【《\(（#SE]'

# ==================== 台本文件识别 ====================

# 台本文件关键词（用于识别台本文件）
SCRIPTBOOK_KEYWORDS = [
    '台本', 'だいほん', 'だい本', 'ダイホン', 'script', '台本付き',
    '仮台本', 'かり台本', '本編', 'ほんぺん',
    'セリフ初稿', 'せりふしょこう', 'シナリオ', 'しなりお',
    '演技指定', 'えんぎしてい', '射精タイミング', '全章'
]

# 台本文件扩展名
SCRIPTBOOK_EXTS = {'.txt', '.pdf'}

# 非台本文件关键词（用于排除特殊用途文件）
NON_SCRIPTBOOK_KEYWORDS = [
    'Finishtime', 'クレジット', 'credit', 'readme', 'Readme',
    '使い方', 'つかいかた', '説明', 'せつめい', '注意', 'ちゅうい',
    'あとがき', 'アトガキ', '感想', 'かんそう', '紹介', 'しょうかい',
    # 序言/前言/说明文件
    'プロローグ', 'prologue', 'はじめに', '初めに', '必ず', '読んで',
    'お読みください', '説明書', 'せつめいしょ', '注意事項',
]


def is_scriptbook_file(file_path: Path) -> bool:
    """检测文件是否为台本文件
    
    判断依据（按优先级）：
    1. 文件扩展名为 .txt 或 .pdf
    2. 排除特殊用途文件（Finishtime.txt、クレジット.txt等）
    3. 文件名包含台本关键词
    4. 文件名符合台本命名模式（トラック1、track1、纯数字编号等）
    5. 文件内容包含典型的台本标记
    
    改进：对于纯数字编号文件，主动检查内容是否为台本格式
    """
    if file_path.suffix.lower() not in SCRIPTBOOK_EXTS:
        return False
    
    filename = file_path.name  # 原始文件名（保留大小写）
    filename_lower = filename.lower()
    stem = file_path.stem  # 不含扩展名的文件名
    
    # 排除特殊用途文件（优先级最高）
    for keyword in NON_SCRIPTBOOK_KEYWORDS:
        if keyword.lower() in filename_lower:
            return False
    
    # 检查文件名是否包含台本关键词
    for keyword in SCRIPTBOOK_KEYWORDS:
        if keyword.lower() in filename_lower:
            return True
    
    # 检查文件名是否符合台本命名模式
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
    # 改进：不要求父文件夹名称，而是检查文件内容
    if re.match(r'^[０-９0-9]+$', stem):
        # 纯数字文件名，检查内容是否为台本格式
        if _check_scriptbook_content(file_path):
            return True
    
    # 5. 数字-数字格式（如 01-1、1-2）
    if re.match(r'^[０-９0-9]+[-_][０-９0-9]+', stem):
        # 数字编号格式，检查内容是否为台本格式
        if _check_scriptbook_content(file_path):
            return True
    
    # 6. 父文件夹名包含台本关键词
    parent_name = file_path.parent.name.lower()
    if parent_name in ('台本', 'だいほん', 'script', 'scripts', 'scenario', 'scenarios'):
        return True
    
    # 7. 其他情况：检查文件内容
    return _check_scriptbook_content(file_path)


def _check_scriptbook_content(file_path: Path) -> bool:
    """检查文件内容是否为台本格式
    
    台本特征：
    1. 角色名标记【】（如 【まどか】）
    2. 角色名：台词格式（如 雫葵：はふー。）
    3. SE标记（如 SE:潮吹）
    4. 方向指示#（如 #正面　距離近く）
    5. 音轨标记（如 トラック1）
    6. 纯台词型（无角色名，但有大量日文对话行）
    
    返回：是否为台本格式
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return False
    
    if not content.strip():
        return False
    
    lines = content.split('\n')
    non_empty_lines = [l.strip() for l in lines if l.strip()]
    
    if len(non_empty_lines) < 5:
        return False
    
    # 台本特征检测
    # 1. 角色名标记【】（如 【まどか】）
    has_character_brackets = bool(re.search(r'^【[^】]+】', content, re.MULTILINE))
    
    # 2. 角色名：台词格式（如 雫葵：はふー。）
    # 格式：角色名（2-10字符）+ 全角冒号 + 台词
    has_character_dialogue = bool(re.search(r'^[^:\n]{2,10}：[^\n]+', content, re.MULTILINE))
    
    # 3. SE标记
    has_se_marks = bool(re.search(r'^SE[:：\s]', content, re.MULTILINE))
    
    # 4. 方向指示#
    has_direction = bool(re.search(r'^#[^\n]+$', content, re.MULTILINE))
    
    # 5. 音轨标记
    has_track = bool(re.search(r'トラック\d+', content))
    
    # 6. 检测大量对话行（角色名：台词 格式的行数）
    dialogue_lines = re.findall(r'^[^:\n]{2,10}：[^\n]+', content, re.MULTILINE)
    has_many_dialogues = len(dialogue_lines) >= 5  # 至少5行对话
    
    # 7. 纯台词型台本检测（无角色名，但有大量日文对话）
    # 特征：
    # - 大量日文字符（平假名/片假名/汉字）
    # - 行长度适中（10-100字符，排除标题和注释）
    # - 包含常见台本标点（。、？、！等）
    japanese_char_count = len(re.findall(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', content))
    total_char_count = len(content.replace('\n', '').replace(' ', ''))
    ja_ratio = japanese_char_count / total_char_count if total_char_count > 0 else 0
    
    # 统计有效对话行（日文内容，长度适中）
    valid_dialogue_lines = 0
    for line in non_empty_lines:
        # 排除注释行（如 //01 音楽準備室で）
        if line.startswith('//') or line.startswith('#'):
            continue
        # 排除过短或过长的行
        if len(line) < 5 or len(line) > 150:
            continue
        # 检查是否包含日文字符
        if re.search(r'[\u3040-\u309f\u30a0-\u30fa\u4e00-\u9fff]', line):
            valid_dialogue_lines += 1
    
    # 纯台词型判断：日文比例高，且有大量有效对话行
    is_pure_dialogue = ja_ratio >= 0.5 and valid_dialogue_lines >= 10
    
    # 综合判断：
    # - 有【角色名】标记
    # - 或有角色名：台词格式且对话行数>=5
    # - 或是纯台词型台本
    # - 或至少两种台本特征
    features = sum([has_character_brackets, has_se_marks, has_direction, has_track])
    
    if has_character_brackets:
        return True
    if has_character_dialogue and has_many_dialogues:
        return True
    if is_pure_dialogue:
        return True
    if features >= 2:
        return True
    
    return False


def parse_scriptbook_content(content: str) -> list[dict]:
    """解析台本内容，提取对话行
    
    支持多种台本格式：
    1. 纯台词型（セリフ初稿台本）- 只有角色台词，无演技指示
    2. 带演技指定型（台本(演技指定あり)）- 含位置、演技指示
    3. 射精タイミング标注型 - 标注射精时机
    4. 纯数字编号型 - 最简格式，仅数字编号
    5. シナリオ型（剧本型）- 完整剧本格式，含效果音、位置
    
    清洗规则：
    1. 删除纯数字/页码残留行（如"11………"、"2…"、"11 /"）
    2. 去掉台词行首的页码数字（如"6…えっ…"）
    3. 删除注音假名单行（如"ほんみょう"、"まや"）
    4. 清理行内多余空格
    
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
        
        # === 清洗规则1：删除纯数字/页码残留行 ===
        if _PAGE_NUMBER_PATTERN.match(stripped):
            parsed.append({
                "line_num": i,
                "character": "",
                "text": "",
                "raw_line": raw_line,
                "type": "other"  # 标记为其他，不参与翻译
            })
            continue
        
        # === 清洗规则3：删除注音假名单行 ===
        # 纯假名单行，但排除常见语气词/拟声词
        if _FURIGANA_PATTERN.match(stripped):
            # 检查是否是常见语气词/拟声词
            normalized = stripped.replace(' ', '').replace('　', '')
            if normalized not in _COMMON_INTERJECTIONS:
                # 这是注音假名，跳过
                parsed.append({
                    "line_num": i,
                    "character": "",
                    "text": "",
                    "raw_line": raw_line,
                    "type": "other"  # 标记为其他，不参与翻译
                })
                continue
        
        # === 清洗规则2：去掉台词行首的页码数字 ===
        cleaned_line = _LEADING_PAGE_NUMBER.sub('', stripped)
        if cleaned_line != stripped:
            stripped = cleaned_line  # 更新stripped
        
        # === 清洗规则4：清理行内多余空格 ===
        # 将多个空格替换为单个空格
        stripped = _EXTRA_SPACES.sub(' ', stripped)
        
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
        
        # 角色名：台词 格式（如 雫葵：はふー。今日もやっと学校終わった。）
        # 格式：角色名（2-10字符）+ 全角冒号 + 台词
        dialogue_match = re.match(r'^([^：\n]{2,10})：([^\n]+)$', stripped)
        if dialogue_match:
            char_name = dialogue_match.group(1).strip()
            dialogue_text = dialogue_match.group(2).strip()
            # 更新当前角色名
            current_character = char_name
            parsed.append({
                "line_num": i,
                "character": char_name,
                "text": dialogue_text,  # 只存储台词，不含角色名
                "raw_line": raw_line,
                "type": "dialogue"
            })
            continue
        
        # 普通对话（无角色名标记）
        parsed.append({
            "line_num": i,
            "character": current_character,
            "text": stripped,
            "raw_line": raw_line,
            "type": "dialogue"
        })
    
    return parsed


def translate_scriptbook_file(source_path: Path, target_path: Path, terms: dict[str, str], 
                               alias_list: list[dict], worldview: dict = None) -> tuple[bool, list[str], list[str]]:
    """翻译台本文件
    
    返回: (是否成功, 原始对话列表, 翻译结果列表)
    """
    worldview = worldview or {}
    print(f"翻译台本: {source_path.name}")
    
    retry_tracker = {'count': 0}
    
    try:
        with open(source_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        print(f"  跳过非 UTF-8 文件: {source_path}")
        return False, [], []
    
    # 解析台本
    parsed = parse_scriptbook_content(content)
    
    # 提取需要翻译的对话行
    dialogue_entries = [p for p in parsed if p["type"] == "dialogue"]
    
    if not dialogue_entries:
        print("  无对话内容，直接复制")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True, [], []
    
    # 构建翻译请求
    original_texts = [d["text"] for d in dialogue_entries]
    
    print(f"  发现 {len(original_texts)} 行对话")
    print(f"  翻译整文件 (行数: {len(original_texts)}, 字符: {sum(len(t) for t in original_texts)})")
    
    # 调用翻译
    translated_texts = translate_lyrics_batch(original_texts, terms, alias_list, worldview, retry_tracker)
    
    # 收集出错的文件
    if retry_tracker['count'] >= 3:
        collect_bug_lrc(source_path, retry_tracker['count'])
    
    # 构建翻译后的台本
    result_lines = []
    dialogue_idx = 0
    
    for p in parsed:
        if p["type"] == "dialogue":
            if dialogue_idx < len(translated_texts):
                result_lines.append(translated_texts[dialogue_idx])
                dialogue_idx += 1
            else:
                result_lines.append(p["text"])
        else:
            result_lines.append(p["raw_line"].rstrip('\n\r'))
    
    # 保存翻译结果
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(result_lines))
    
    cost_hit = (total_hit_tokens / 1_000_000) * PRICE_HIT_PER_1M
    cost_miss = (total_miss_tokens / 1_000_000) * PRICE_MISS_PER_1M
    cost_completion = (total_completion_tokens / 1_000_000) * PRICE_COMPLETION_PER_1M
    cost_total = cost_hit + cost_miss + cost_completion
    print(f" ✓ 完成 -> {target_path.name}")
    print(f" 💰 累计费用: 命中{cost_hit:.4f}元 + 未命中{cost_miss:.4f}元 + 输出{cost_completion:.4f}元 = {cost_total:.4f}元")
    
    return True, original_texts, translated_texts


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


def process_all_scriptbooks(work_dir: Path) -> tuple[int, int, int]:
    """处理所有台本文件
    
    返回: (翻译数量, 跳过数量, 保留数量)
    """
    translated = 0
    skipped = 0
    kept = 0
    
    # 查找所有台本文件
    scriptbook_files = []
    for fpath in _walk_files(work_dir):
        if is_scriptbook_file(fpath):
            scriptbook_files.append(fpath)
    
    if not scriptbook_files:
        return 0, 0, 0
    
    print(f"发现 {len(scriptbook_files)} 个台本文件")
    
    # 按目录分组，复用术语表
    processed_dirs = set()
    dir_worldviews = {}
    
    for idx, src_path in enumerate(sorted(scriptbook_files), 1):
        parent_dir = src_path.parent
        stem = src_path.stem
        
        # 目标文件名
        target_path = parent_dir / f"{stem}.zh.txt"
        ja_path = parent_dir / f"{stem}.ja.txt"
        
        print(f"[台本 {idx}/{len(scriptbook_files)}] {src_path.name}")
        
        # 分析作品级术语（每个文件夹只一次）
        if parent_dir not in processed_dirs:
            processed_dirs.add(parent_dir)
            # 尝试加载已有的术语表和世界观
            terms_path = get_terms_path(parent_dir)
            alias_path = get_alias_path(parent_dir)
            worldview_path = get_worldview_path(parent_dir)
            
            if terms_path.exists() and alias_path.exists():
                terms = load_terms(parent_dir)
                alias_list = load_alias(parent_dir)
                worldview = load_worldview(parent_dir) if worldview_path.exists() else {}
                print(f"  加载已有术语表: {len(terms)} 个, alias: {len(alias_list)} 个")
            else:
                # 台本文件通常不需要构建世界观，使用空术语表
                terms = {}
                alias_list = []
                worldview = {}
                print(f"  使用空术语表")
            
            dir_worldviews[parent_dir] = worldview
        else:
            terms = load_terms(parent_dir)
            alias_list = load_alias(parent_dir)
            worldview = dir_worldviews.get(parent_dir, {})
        
        # 检测语言
        lang = detect_scriptbook_language(src_path)
        
        if lang == "japanese":
            # 检查是否已翻译
            if target_path.exists():
                print(f"  [跳过] 已存在翻译: {target_path.name}")
                skipped += 1
                continue
            
            # 留档日文
            if not ja_path.exists():
                shutil.copy2(src_path, ja_path)
                print(f"  留档: {src_path.name} -> {ja_path.name}")
            
            # 翻译
            result = translate_scriptbook_file(src_path, target_path, terms, alias_list, worldview)
            if result[0]:
                translated += 1
            else:
                skipped += 1
        
        elif lang == "chinese":
            print(f"  [保留] 已是中文: {src_path.name}")
            kept += 1
        
        else:
            print(f"  [跳过] 无法识别语言或内容为空")
            skipped += 1
    
    return translated, skipped, kept


# ==================== 主流程 ====================

def process_all_lrc(work_dir: Path) -> tuple[int, int, int, int]:
    archived = 0
    translated = 0
    skipped = 0
    kept = 0
    
    # 动态术语批量处理：每 N 个文件处理一次
    DYNAMIC_TERMS_BATCH_SIZE = 5
    pending_translations = []  # 缓存待处理的翻译对: [(parent_dir, original, translated), ...]

    file_groups = {}

    for ext in SUBTITLE_EXTS:
        for src_path in _walk_files(work_dir):
            if src_path.suffix.lower() != ext:
                continue
            if src_path.stem.endswith('.ja'):
                continue
            key = (src_path.parent, src_path.stem, ext)
            file_groups.setdefault(key, {'has_src': False, 'has_ja': False})
            file_groups[key]['has_src'] = True

    for ext in SUBTITLE_EXTS:
        for ja_path in _walk_files(work_dir):
            if ja_path.suffix.lower() != ext or not ja_path.stem.endswith('.ja'):
                continue
            base_name = ja_path.stem
            if base_name.endswith('.ja'):
                base_name = base_name[:-3]
            key = (ja_path.parent, base_name, ext)
            file_groups.setdefault(key, {'has_src': False, 'has_ja': False})
            file_groups[key]['has_ja'] = True

    if not file_groups:
        print("未找到任何字幕文件")
        return 0, 0, 0, 0

    already_translated = set()
    for (parent_dir, base_name, ext), status in file_groups.items():
        if ext == '.lrc' and status['has_src'] and status['has_ja']:
            lrc_path = parent_dir / f"{base_name}.lrc"
            if detect_lrc_language(lrc_path) == "chinese":
                already_translated.add((parent_dir, base_name))

    print(f"发现 {len(file_groups)} 个作品")
    if already_translated:
        print(f"其中 {len(already_translated)} 个已通过 .lrc 翻译，跳过其他格式\n")
    else:
        print()

    # 按文件夹分组，每个文件夹只分析一次术语
    processed_dirs = set()
    # 缓存每个目录的世界观
    dir_worldviews = {}
    # 缓存每个目录的台本映射
    dir_scriptbook_maps = {}
    # 缓存已完成翻译的目录（跳过世界观和术语分析）
    fully_translated_dirs = set()

    # 预检查：找出所有音轨已完成翻译的目录
    dir_file_counts = defaultdict(int)
    dir_translated_counts = defaultdict(int)
    for (parent_dir, base_name, ext), status in file_groups.items():
        if ext == '.lrc':
            dir_file_counts[parent_dir] += 1
            if (parent_dir, base_name) in already_translated:
                dir_translated_counts[parent_dir] += 1
    
    for parent_dir, total_count in dir_file_counts.items():
        if total_count > 0 and dir_translated_counts[parent_dir] == total_count:
            fully_translated_dirs.add(parent_dir)
    
    if fully_translated_dirs:
        print(f"检测到 {len(fully_translated_dirs)} 个目录已完成所有翻译，将跳过世界观和术语分析\n")

    total_tasks = len(file_groups)
    for task_idx, ((parent_dir, base_name, ext), status) in enumerate(sorted(file_groups.items()), 1):
        src_path = parent_dir / f"{base_name}{ext}"
        ja_path = parent_dir / f"{base_name}.ja{ext}"
        # 使用完整路径而不是相对路径
        full_path = str(src_path) if status['has_src'] else str(ja_path)

        print(f"[{task_idx}/{total_tasks}] ", end="")

        if (parent_dir, base_name) in already_translated and ext != '.lrc':
            print(f"跳过: {full_path} (.lrc 已翻译)")
            skipped += 1
            continue

        # 分析作品级术语（每个文件夹只一次）
        if parent_dir not in processed_dirs:
            processed_dirs.add(parent_dir)
            
            # 检查是否已完成所有翻译，如果是则跳过世界观和术语分析
            if parent_dir in fully_translated_dirs:
                print(f"\n{'=' * 50}")
                print(f"  [跳过分析] 目录: {str(parent_dir)}")
                print(f"  {'=' * 50}")
                print(f"  该作品所有音轨已完成翻译，跳过世界观和术语分析")
                # 加载已有的术语表和alias表
                terms, alias_list, worldview = load_work_terms(parent_dir)
                if terms is None:
                    terms = {}
                    alias_list = []
                    worldview = {}
                dir_worldviews[parent_dir] = worldview
                print(f"  已加载术语表: {len(terms)} 个 | alias: {len(alias_list)} 个")
            else:
                print(f"\n{'=' * 50}")
                print(f"  [作品分析] 目录: {str(parent_dir)}")
                print(f"  {'=' * 50}")
                terms, alias_list, worldview = analyze_work_terms(parent_dir)
                # 缓存世界观
                dir_worldviews[parent_dir] = worldview
                print(f"  术语表: {len(terms)} 个 | alias: {len(alias_list)} 个")
                print(f"  世界观: {worldview.get('worldview', '')[:50]}...")
            
            # 加载台本映射（如果启用）
            scriptbook_map = {}
            scriptbook_file_map = {}
            if USE_SCRIPTBOOK_FOR_TRANSLATION:
                print(f"\n  [台本分析] 正在扫描台本文件...")
                scriptbook_map, scriptbook_file_map, scriptbook_characters, track_line_ranges = build_track_scriptbook_map(parent_dir, client, _api_cfg["model"])
                
                # 自动将台本中的角色名添加到术语表
                if scriptbook_characters:
                    print(f"  [角色名] 台本中发现 {len(scriptbook_characters)} 个角色名: {', '.join(list(scriptbook_characters)[:5])}")
                    
                    # 调用 LLM 分析角色名，给出统一译名
                    character_terms = analyze_scriptbook_characters(scriptbook_characters, parent_dir)
                    
                    # 加载现有术语表
                    existing_terms = load_terms(parent_dir)
                    
                    # 合并新角色名译名（如果尚未存在或现有译名为空）
                    updated_count = 0
                    for char_name, char_translation in character_terms.items():
                        if char_name not in existing_terms or not existing_terms.get(char_name):
                            existing_terms[char_name] = char_translation
                            updated_count += 1
                            print(f"    {char_name} → {char_translation}")
                    
                    if updated_count > 0:
                        save_terms(parent_dir, existing_terms)
                        print(f"  [角色名] 已更新 {updated_count} 个角色名译名到术语表")
                        
                        # 更新当前使用的术语表
                        terms = existing_terms
                if scriptbook_map:
                    # 打印台本映射摘要
                    total_tracks = len([k for k in scriptbook_map.keys() if k > 0])
                    total_lines = sum(len(v) for v in scriptbook_map.values() if v)
                    unassigned_lines = len(scriptbook_map.get(0, []))
                    print(f"  台本映射完成: {total_tracks} 个音轨, 共 {total_lines} 行台词")
                    if unassigned_lines > 0:
                        print(f"  (其中 {unassigned_lines} 行未分配到具体音轨)")
                else:
                    print(f"  未发现台本文件")
            dir_scriptbook_maps[parent_dir] = (scriptbook_map, scriptbook_file_map)
            print(f"  {'=' * 50}\n")
        else:
            print(f"  [复用术语] 目录: {parent_dir.name}")
            terms = load_terms(parent_dir)
            alias_list = load_alias(parent_dir)
            worldview = dir_worldviews.get(parent_dir, {})
            # 从缓存中获取台本映射（tuple格式）
            scriptbook_cached = dir_scriptbook_maps.get(parent_dir, ({}, {}))
            scriptbook_map = scriptbook_cached[0] if isinstance(scriptbook_cached, tuple) else scriptbook_cached
            scriptbook_file_map = scriptbook_cached[1] if isinstance(scriptbook_cached, tuple) else {}
            print(f"  加载术语表: {len(terms)} 个 | alias: {len(alias_list)} 个")
            if scriptbook_map:
                print(f"  加载台本映射: {len([k for k in scriptbook_map.keys() if k > 0])} 个音轨")

        # 根据文件名提取音轨编号，查找对应的台本内容
        track_scriptbook_lines = None
        track_scriptbook_file = None
        current_track_num = None
        if scriptbook_map:
            track_num = extract_track_number_from_filename(base_name)
            current_track_num = track_num
            if track_num is not None and track_num in scriptbook_map:
                track_scriptbook_lines = scriptbook_map[track_num]
                track_scriptbook_file = scriptbook_file_map.get(track_num)
                print(f"  [台本匹配] 音轨{track_num:02d} → {len(track_scriptbook_lines)}行台词")
                if track_scriptbook_file:
                    print(f"               台本路径: {track_scriptbook_file}")
            elif 0 in scriptbook_map:
                # 使用完整台本（未分配到具体音轨）
                track_scriptbook_lines = scriptbook_map[0]
                track_scriptbook_file = scriptbook_file_map.get(0)
                print(f"  [台本匹配] 使用完整台本 → {len(track_scriptbook_lines)}行台词")
                if track_scriptbook_file:
                    print(f"               台本路径: {track_scriptbook_file}")

        if status['has_src'] and status['has_ja']:
            lang = detect_lrc_language(src_path)
            if lang == "japanese":
                print(f"  [翻译] {full_path} (日文，有留档)")
                result = translate_lrc_file(src_path, src_path, terms, alias_list, worldview, track_scriptbook_lines)
                if isinstance(result, tuple):
                    success, original_lyrics, translated_lyrics = result
                else:
                    success = result
                    original_lyrics, translated_lyrics = [], []
                if success:
                    translated += 1
                    # 收集翻译对，稍后批量处理
                    if original_lyrics and translated_lyrics and task_idx < total_tasks:
                        pending_translations.append((parent_dir, original_lyrics, translated_lyrics, terms))
                        # 每 N 个文件批量处理一次
                        if len(pending_translations) >= DYNAMIC_TERMS_BATCH_SIZE:
                            print(f"  [动态更新] 批量处理 {len(pending_translations)} 个文件的术语提取...")
                            _process_pending_translations(pending_translations)
                            pending_translations.clear()
                else:
                    skipped += 1
            elif lang == "chinese":
                print(f"  [跳过] {full_path} (已翻译)")
                skipped += 1
            else:
                print(f"  [跳过] {full_path} (内容为空或无法识别)")
                skipped += 1

        elif status['has_src'] and not status['has_ja']:
            lang = detect_lrc_language(src_path)
            if lang == "japanese":
                print(f"  [翻译] {full_path} (日文，无留档)")
                shutil.copy2(src_path, ja_path)
                print(f"    留档: {src_path.name} -> {ja_path.name}")
                archived += 1
                result = translate_lrc_file(src_path, src_path, terms, alias_list, worldview)
                if isinstance(result, tuple):
                    success, original_lyrics, translated_lyrics = result
                else:
                    success = result
                    original_lyrics, translated_lyrics = [], []
                if success:
                    translated += 1
                    # 动态术语更新（最后一个文件跳过，因为没有后续文件需要用到新术语）
                    if original_lyrics and translated_lyrics and task_idx < total_tasks:
                        print(f"  [动态更新] 提取新术语...")
                        new_terms, new_alias = extract_terms_from_translation(original_lyrics, translated_lyrics, terms)
                        if new_terms or new_alias:
                            update_terms_and_alias(parent_dir, new_terms, new_alias)
                else:
                    skipped += 1
            elif lang == "chinese":
                print(f"  [保留] {full_path} (中文，无日文留档)")
                kept += 1
            else:
                print(f"  [跳过] {full_path} (内容为空或无法识别)")
                skipped += 1

        elif not status['has_src'] and status['has_ja']:
            print(f"  [恢复] {full_path} (恢复并翻译)")
            shutil.copy2(ja_path, src_path)
            result = translate_lrc_file(src_path, src_path, terms, alias_list, worldview)
            if isinstance(result, tuple):
                success, original_lyrics, translated_lyrics = result
            else:
                success = result
                original_lyrics, translated_lyrics = [], []
            if success:
                translated += 1
                # 动态术语更新
                if original_lyrics and translated_lyrics:
                    print(f"  [动态更新] 提取新术语...")
                    new_terms, new_alias = extract_terms_from_translation(original_lyrics, translated_lyrics, terms)
                    if new_terms or new_alias:
                        update_terms_and_alias(parent_dir, new_terms, new_alias)
            else:
                skipped += 1

    return archived, translated, skipped, kept


def main():
    if not WORK_DIR.exists():
        print(f"错误: 工作文件夹 '{WORK_DIR}' 不存在")
        sys.exit(1)

    # 记录开始时间
    start_time = time.time()

    print("=" * 50)
    print("处理字幕文件...")
    print("=" * 50)
    print(f"工作目录: {str(WORK_DIR)}")
    print()
    print("鸣谢：AI汉化组-Faster-Whisper-TransWithAI-ChickenRice项目")
    print("项目发布于南+ 开发者hdeman")
    print("遇到问题或者觉得效果还行都可以在帖子中反馈")
    print("感谢使用！")
    print()

    # 打印当前使用的模型和参数
    _gen_params = _api_cfg.get("generation_params", {})
    print()
    print("[API 配置]")
    print(f"  模型: {_api_cfg.get('model', 'N/A')}")
    print(f"  Base URL: {_api_cfg.get('base_url', 'N/A')}")
    print(f"  Timeout: {_api_cfg.get('timeout', 'N/A')}s")
    print(f"  Generation Params:")
    print(f"    temperature: {_gen_params.get('temperature', 'N/A')}")
    print(f"    top_p: {_gen_params.get('top_p', 'N/A')}")
    print(f"    max_tokens: {_gen_params.get('max_tokens', 'N/A')}")
    print(f"    reasoning_effort: {_gen_params.get('reasoning_effort', 'N/A')}")
    print()
    
    # 打印台本翻译模式
    print("[台本翻译模式]")
    if USE_SCRIPTBOOK_FOR_TRANSLATION:
        if SCRIPTBOOK_KEYWORD_MODE:
            print("  台本翻译模式: 关键字模式")
        elif SCRIPTBOOK_FULL_MODE:
            print("  台本翻译模式: 全量模式")
        else:
            print("  台本翻译模式: 默认模式")
    else:
        print("  台本翻译模式: 未启用")
    print()

    print("=" * 50)
    print("处理字幕文件...")
    print("=" * 50)
    archived, translated, skipped, kept = process_all_lrc(WORK_DIR)

    # 计算总用时
    end_time = time.time()
    total_time = end_time - start_time

    print()
    print("=" * 50)
    print("处理完成！")
    print("=" * 50)
    print(f"  留档日文: {archived} 个")
    print(f"  翻译中文: {translated} 个")
    print(f"  跳过: {skipped} 个")
    print(f"  保留: {kept} 个")
    print()
    print(f"  总用时: {total_time:.1f} 秒 ({total_time/60:.1f} 分钟)")
    print()

    cost_hit = (total_hit_tokens / 1_000_000) * PRICE_HIT_PER_1M
    cost_miss = (total_miss_tokens / 1_000_000) * PRICE_MISS_PER_1M
    cost_completion = (total_completion_tokens / 1_000_000) * PRICE_COMPLETION_PER_1M
    cost_total = cost_hit + cost_miss + cost_completion
    print("=" * 50)
    print("API 费用统计")
    print("=" * 50)
    print(f"  缓存命中: {total_hit_tokens:,} tokens × {PRICE_HIT_PER_1M}元/百万 = {cost_hit:.4f} 元")
    print(f"  缓存未命中: {total_miss_tokens:,} tokens × {PRICE_MISS_PER_1M}元/百万 = {cost_miss:.4f} 元")
    print(f"  输出tokens: {total_completion_tokens:,} tokens × {PRICE_COMPLETION_PER_1M}元/百万 = {cost_completion:.4f} 元")
    print(f"  总费用: {cost_total:.4f} 元")
    print("=" * 50)
    print()
    print("文件说明:")
    print("  .lrc / .srt / .vtt     = 当前使用的中文字幕（播放器读取）")
    print("  .ja.lrc / .ja.srt / .ja.vtt  = 日文原版留档")
    print("  .zh.txt                 = 台本中文翻译")
    print("  .ja.txt                 = 台本日文原版留档")
    print("  .terms.json             = 作品级术语表")
    print("  .alias.json             = 作品级 ASR 误识别参考表")


if __name__ == "__main__":
    main()
