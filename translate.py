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

# 让打包后的 exe 也能找到字典
if hasattr(sys, '_MEIPASS'):
    base_dir = sys._MEIPASS
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
dic_path = os.path.join(base_dir, 'unidic_lite', 'dicdir')
# 使用正斜杠避免反斜杠被 C 库错误解析（如 \u 被当 Unicode 转义）
dic_path_safe = dic_path.replace('\\', '/')

# 确保路径以正斜杠结尾，避免路径拼接时混合斜杠
dic_path_safe = dic_path_safe.rstrip('/') + '/'

# 设置环境变量，让 fugashi/unidic_lite 能找到字典
os.environ['UNIDIC_DIR'] = dic_path_safe

# 设置 MECABRC 环境变量，指向 mecabrc 文件
mecabrc_path = os.path.join(dic_path_safe, 'mecabrc')
os.environ['MECABRC'] = mecabrc_path

# 打包后 unidic_lite 的 dicdir 可能不在模块旁边，提前创建 mock 避免导入时崩溃
if hasattr(sys, '_MEIPASS'):
    import types

    _unidic_mock = types.ModuleType('unidic_lite')
    _unidic_mock.DICDIR = dic_path_safe
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
    "1. 尽量使用参考台词中的原句，除非ASR明确出现了不同但合理的内容（即兴发挥），此时保留ASR的说法。\n"
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
    "你是一位专门处理日文成人音声（ASMR/RJ作品）字幕的专业本地化工程师，同时也是精通日语和中文的R18音声脚本翻译专家，以及专注于成人音声字幕的ASR（自动语音识别）纠错专家。\n"
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
        "miss_per_1m": 3.0
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

# ==================== 价格配置 ====================
_price_cfg = CONFIG["pricing"]
PRICE_HIT_PER_1M = _price_cfg["hit_per_1m"]
PRICE_MISS_PER_1M = _price_cfg["miss_per_1m"]

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
    
    # 2. 检测热门CV名称
    for part in path_parts:
        part_lower = part.lower()
        for cv_name in POPULAR_ASMR_CV_NAMES:
            if cv_name.lower() in part_lower:
                # 检查是否是CV名（不是其他词的一部分）
                # 例如 "山田" 在 "山田寿美子" 中
                if len(cv_name) >= 2:
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


def build_scriptbook_prompt(scriptbook_lines: list[str]) -> str:
    """将台本内容构建为 prompt 附加内容（强约束）
    
    台本内容具有绝对优先权，用于纠正ASR错误。
    但ASR中可能包含台本中没有的内容（如拟声词、喘息声），这些应该保留。
    """
    if not scriptbook_lines:
        return ""
    
    lines = ["\n【台本参考台词（绝对优先级——ASR纠正依据）】"]
    lines.append("以下是本音轨的准确台词列表，来自官方台本/剧本。")
    lines.append("**台本具有绝对优先权**：如果ASR识别结果与台本不一致，请优先使用台本内容。")
    lines.append("")
    
    # 显示台本台词（编号）
    for i, line in enumerate(scriptbook_lines, 1):
        if line.strip():
            # 显示前80个字符，超过则截断
            display_line = line[:80] + "..." if len(line) > 80 else line
            lines.append(f"  {i:02d}: {display_line}")
        else:
            lines.append(f"  {i:02d}: [空行]")
    
    lines.append("")
    lines.append("【台本使用规则】")
    lines.append("1. **台词纠正**：如果ASR识别的台词与台本内容相似但有差异（错字、漏字、同音误识别），请使用台本的准确内容。")
    lines.append("2. **拟声词/喘息声保留**：ASR中可能包含台本中没有的拟声词、喘息声、呻吟等，这些是实际音频中的内容，**必须保留**。")
    lines.append("3. **即兴发挥保留**：如果ASR中有台本中没有的完整句子，可能是演员的即兴发挥，保留ASR内容。")
    lines.append("4. **顺序对齐**：台本台词按时间顺序排列，ASR可能与台本顺序略有差异，请根据内容相似性进行对齐。")
    lines.append("5. **缺失台词补充**：如果台本中有ASR中没有的台词，可能是ASR漏识别，请根据台本补充。")
    lines.append("")
    lines.append("【台本参考结束】\n")
    
    return "\n".join(lines)


def find_scriptbooks_in_dir(work_dir: Path) -> list[Path]:
    """查找目录中的所有台本文件
    
    返回: 台本文件路径列表
    """
    scriptbook_files = []
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
    """
    import unicodedata
    
    # 常见模式
    patterns = [
        r'[Tt]rack[-_]?(\d+)',           # Track-01, Track01
        r'トラック(\d+)',                 # トラック1
        r'^[０-９]+',                     # 全角数字开头
        r'^(\d+)[)）\.\s]',               # 数字开头后跟括号或点
    ]
    
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            num_str = match.group(1) if match.groups() else match.group(0)
            # 转换全角数字为半角
            num_str = ''.join(
                chr(ord(c) - 0xFEE0) if '０' <= c <= '９' else c
                for c in num_str
            )
            # 提取数字
            digits = re.search(r'\d+', num_str)
            if digits:
                return int(digits.group())
    
    return None


def build_track_scriptbook_map(work_dir: Path) -> dict[int, list[str]]:
    """构建音轨到台本的映射
    
    扫描目录中的台本文件，解析并按音轨编号组织。
    
    返回: {音轨编号: [台词列表]}
    """
    track_map = {}
    scriptbook_files = find_scriptbooks_in_dir(work_dir)
    
    if not scriptbook_files:
        return track_map
    
    for sb_path in scriptbook_files:
        # 提取音轨编号
        track_num = extract_track_number_from_filename(sb_path.stem)
        
        # 读取台本内容
        try:
            if sb_path.suffix.lower() == '.pdf':
                content = extract_pdf_text(sb_path)
            else:
                with open(sb_path, 'r', encoding='utf-8') as f:
                    content = f.read()
        except Exception as e:
            print(f"    [台本] 读取失败: {sb_path.name} - {e}")
            continue
        
        if not content.strip():
            continue
        
        # 解析台本，提取纯台词
        parsed = parse_scriptbook_content(content)
        dialogue_lines = [p["text"] for p in parsed if p["type"] == "dialogue" and p["text"].strip()]
        
        if not dialogue_lines:
            continue
        
        if track_num is not None:
            # 有明确音轨编号
            track_map[track_num] = dialogue_lines
            print(f"    [台本映射] 音轨{track_num:02d} ← {sb_path.name} ({len(dialogue_lines)}行台词)")
        else:
            # 无音轨编号，尝试匹配所有音轨（通常是PDF完整台本）
            # 暂时存储为 track_num=0（表示"所有音轨"）
            if 0 not in track_map:
                track_map[0] = []
            track_map[0].extend(dialogue_lines)
            print(f"    [台本映射] 完整台本 ← {sb_path.name} ({len(dialogue_lines)}行台词，未分配到具体音轨)")
    
    return track_map


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


def match_scriptbook_to_asr(asr_lines: list[str], scriptbook_lines: list[str], 
                            similarity_threshold: float = 0.8) -> tuple[list[str], list[float]]:
    """将台本台词与ASR结果对齐，返回对齐结果和相似度分数
    
    使用动态规划算法，找到ASR和台本之间的最优对齐。
    
    参数:
        asr_lines: ASR识别结果列表
        scriptbook_lines: 台本台词列表
        similarity_threshold: 相似度阈值，超过此值认为是对齐的
    
    返回: (对齐后的台本台词列表, 相似度分数列表)
        - 对齐的台词：返回台本内容
        - 未对齐的台词：返回空字符串（保留ASR）
    """
    if not scriptbook_lines or not asr_lines:
        return [], []
    
    n_asr = len(asr_lines)
    n_sb = len(scriptbook_lines)
    
    # 预处理：过滤空行
    asr_texts = [line.strip() for line in asr_lines]
    sb_texts = [line.strip() for line in scriptbook_lines]
    
    # 计算相似度矩阵
    # sim_matrix[i][j] = asr_lines[i] 与 scriptbook_lines[j] 的相似度
    sim_matrix = [[0.0] * n_sb for _ in range(n_asr)]
    
    for i in range(n_asr):
        if not asr_texts[i]:
            continue
        for j in range(n_sb):
            if not sb_texts[j]:
                continue
            sim_matrix[i][j] = calculate_similarity(asr_texts[i], sb_texts[j])
    
    # 使用动态规划找到最优对齐
    # dp[i][j] = 前i个ASR和前j个台本的最大对齐分数
    # 三种操作：
    # 1. 对齐：dp[i-1][j-1] + sim_matrix[i-1][j-1]
    # 2. ASR跳过：dp[i-1][j]（ASR中有额外内容，如拟声词）
    # 3. 台本跳过：dp[i][j-1]（台本中有ASR没有的内容）
    
    dp = [[0.0] * (n_sb + 1) for _ in range(n_asr + 1)]
    path = [[None] * (n_sb + 1) for _ in range(n_asr + 1)]
    
    for i in range(1, n_asr + 1):
        for j in range(1, n_sb + 1):
            # 选项1：对齐
            align_score = dp[i-1][j-1] + sim_matrix[i-1][j-1]
            # 选项2：ASR跳过（惩罚较小）
            skip_asr_score = dp[i-1][j] + 0.1  # 小奖励，允许ASR有额外内容
            # 选项3：台本跳过（惩罚较大）
            skip_sb_score = dp[i][j-1] - 0.2  # 惩罚，不鼓励跳过台本
            
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
    
    # 回溯找到对齐结果
    result = [""] * n_asr  # 默认空字符串（保留ASR）
    scores = [0.0] * n_asr
    
    i, j = n_asr, n_sb
    while i > 0 and j > 0:
        op = path[i][j]
        if op == 'align':
            sim = sim_matrix[i-1][j-1]
            if sim >= similarity_threshold:
                result[i-1] = sb_texts[j-1]  # 使用台本内容
                scores[i-1] = sim
            i -= 1
            j -= 1
        elif op == 'skip_asr':
            # ASR中有额外内容（拟声词等），保留ASR
            result[i-1] = ""  # 空字符串表示保留ASR
            scores[i-1] = 0.0
            i -= 1
        elif op == 'skip_sb':
            # 台本中有ASR没有的内容（可能是ASR漏识别）
            j -= 1
        else:
            # 默认情况
            i -= 1
            j -= 1
    
    return result, scores


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


def sample_all_lrc_files(work_dir: Path, lines_per_file: int = 40) -> tuple[list[str], dict[str, dict]]:
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
    """递归遍历目录下所有文件，兼容深层嵌套和特殊字符路径"""
    files = []
    try:
        for root, _dirs, filenames in os.walk(str(work_dir)):
            for name in filenames:
                files.append(Path(root) / name)
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

    sorted_cores = sorted(cores.items(), key=core_priority)[:40]
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
        for ctx in info['contexts'][:2]:
            lines.append(f"    上下文: {ctx[:60]}")

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
                if re.search(r'\[\d{1,2}:\d{2}\.\d{2,3}\]', tags):
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
        scriptbook_prompt = build_scriptbook_prompt(scriptbook_lines)
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
    
    # DEBUG模式：显示发送给LLM的完整台词
    if DEBUG_MODE:
        print(f"    [DEBUG] 发送给LLM的台词内容:")
        print(f"    ─────────────────────────────────────")
        for i, line in enumerate(lyrics, 1):
            # 显示前100个字符，超过则截断
            display_line = line[:100] + "..." if len(line) > 100 else line
            print(f"    {i:02d}: {display_line}")
        print(f"    ─────────────────────────────────────")

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
    translated_split = translate_lyrics_batch(split_lyrics, terms, alias_list, worldview, retry_tracker)
    
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
    cost_total = cost_hit + cost_miss
    print(f" ✓ 完成 -> {target_path.name}")
    print(f" 💰 累计费用: 命中{cost_hit:.4f}元 + 未命中{cost_miss:.4f}元 = {cost_total:.4f}元")
    print(f"    (命中{total_hit_tokens}tokens / 未命中{total_miss_tokens}tokens / 输出{total_completion_tokens}tokens)")

    # 返回原始歌词和翻译结果，用于动态术语更新
    return True, original_lyrics, translated_lyrics


def translate_lrc_file_simple(source_path: Path, target_path: Path, terms: dict[str, str], alias_list: list[dict],
                              worldview: dict = None) -> bool:
    """简化版 translate_lrc_file，只返回是否成功（兼容旧接口）"""
    success, _orig, _trans = translate_lrc_file(source_path, target_path, terms, alias_list, worldview)
    return success


# ==================== 作品级预处理 ====================

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

# 音轨标记模式（如 【トラック1：xxx】）
TRACK_PATTERN = r'^【トラック\d+[：：][^\]]*】'

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


def is_scriptbook_file(file_path: Path) -> bool:
    """检测文件是否为台本文件
    
    判断依据：
    1. 文件扩展名为 .txt
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
        
        # 普通对话
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
    cost_total = cost_hit + cost_miss
    print(f" ✓ 完成 -> {target_path.name}")
    print(f" 💰 累计费用: 命中{cost_hit:.4f}元 + 未命中{cost_miss:.4f}元 = {cost_total:.4f}元")
    
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
            if USE_SCRIPTBOOK_FOR_TRANSLATION:
                print(f"\n  [台本分析] 正在扫描台本文件...")
                scriptbook_map = build_track_scriptbook_map(parent_dir)
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
            dir_scriptbook_maps[parent_dir] = scriptbook_map
            print(f"  {'=' * 50}\n")
        else:
            print(f"  [复用术语] 目录: {parent_dir.name}")
            terms = load_terms(parent_dir)
            alias_list = load_alias(parent_dir)
            worldview = dir_worldviews.get(parent_dir, {})
            scriptbook_map = dir_scriptbook_maps.get(parent_dir, {})
            print(f"  加载术语表: {len(terms)} 个 | alias: {len(alias_list)} 个")
            if scriptbook_map:
                print(f"  加载台本映射: {len([k for k in scriptbook_map.keys() if k > 0])} 个音轨")

        # 根据文件名提取音轨编号，查找对应的台本内容
        track_scriptbook_lines = None
        if scriptbook_map:
            track_num = extract_track_number_from_filename(base_name)
            if track_num is not None and track_num in scriptbook_map:
                track_scriptbook_lines = scriptbook_map[track_num]
                print(f"  [台本匹配] 音轨{track_num:02d} → {len(track_scriptbook_lines)}行台词")
            elif 0 in scriptbook_map:
                # 使用完整台本（未分配到具体音轨）
                track_scriptbook_lines = scriptbook_map[0]
                print(f"  [台本匹配] 使用完整台本 → {len(track_scriptbook_lines)}行台词")

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

    # 先处理台本文件（如果启用台本指导翻译）
    script_translated, script_skipped, script_kept = 0, 0, 0
    if USE_SCRIPTBOOK_FOR_TRANSLATION:
        print("=" * 50)
        print("预处理台本文件...")
        print("=" * 50)
        print("台本文件将在LRC翻译时作为参考，提高翻译准确性。")
        print()
        script_translated, script_skipped, script_kept = process_all_scriptbooks(WORK_DIR)
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
    if script_translated > 0 or script_skipped > 0 or script_kept > 0:
        print()
        print(f"  台本翻译: {script_translated} 个")
        print(f"  台本跳过: {script_skipped} 个")
        print(f"  台本保留: {script_kept} 个")
    print()
    print(f"  总用时: {total_time:.1f} 秒 ({total_time/60:.1f} 分钟)")
    print()

    cost_hit = (total_hit_tokens / 1_000_000) * PRICE_HIT_PER_1M
    cost_miss = (total_miss_tokens / 1_000_000) * PRICE_MISS_PER_1M
    cost_total = cost_hit + cost_miss
    print("=" * 50)
    print("API 费用统计")
    print("=" * 50)
    print(f"  缓存命中: {total_hit_tokens:,} tokens × {PRICE_HIT_PER_1M}元/百万 = {cost_hit:.4f} 元")
    print(f"  缓存未命中: {total_miss_tokens:,} tokens × {PRICE_MISS_PER_1M}元/百万 = {cost_miss:.4f} 元")
    print(f"  输出tokens: {total_completion_tokens:,}")
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
