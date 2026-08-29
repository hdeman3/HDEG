# -*- coding: utf-8 -*-
"""
世界观分析引擎

分析日语文本的世界观特征：
- 文末语气词（ぞ、わ、ぜ、な、ね 等）
- 男性/女性用语
- 敬语级别
- 口癖/语气模式
"""

from __future__ import annotations
import re


# ==================== 语气词/口癖词典 ====================

# 文末助词及其性别倾向
SENTENCE_END_PARTICLES: dict[str, dict] = {
    'ぞ': {'gender': 'male', 'tone': 'assertive', 'category': 'sentence_end'},
    'ぜ': {'gender': 'male', 'tone': 'casual_rough', 'category': 'sentence_end'},
    'だぜ': {'gender': 'male', 'tone': 'casual_rough', 'category': 'sentence_end'},
    'だぞ': {'gender': 'male', 'tone': 'assertive', 'category': 'sentence_end'},
    'な': {'gender': 'neutral', 'tone': 'self_reflection', 'category': 'sentence_end'},
    'なあ': {'gender': 'male_lean', 'tone': 'emotional', 'category': 'sentence_end'},
    'ね': {'gender': 'neutral', 'tone': 'confirmation', 'category': 'sentence_end'},
    'ねえ': {'gender': 'neutral', 'tone': 'emotional_confirmation', 'category': 'sentence_end'},
    'よ': {'gender': 'neutral', 'tone': 'informative', 'category': 'sentence_end'},
    'よね': {'gender': 'neutral', 'tone': 'seeking_agreement', 'category': 'sentence_end'},
    'さ': {'gender': 'male_lean', 'tone': 'casual', 'category': 'sentence_end'},
    'わ': {'gender': 'female', 'tone': 'softening', 'category': 'sentence_end'},
    'わね': {'gender': 'female', 'tone': 'soft_confirmation', 'category': 'sentence_end'},
    'わよ': {'gender': 'female', 'tone': 'soft_assertion', 'category': 'sentence_end'},
    'の': {'gender': 'female', 'tone': 'explanatory', 'category': 'sentence_end'},
    'のね': {'gender': 'female', 'tone': 'explanatory_confirmation', 'category': 'sentence_end'},
    'のよ': {'gender': 'female', 'tone': 'explanatory_assertion', 'category': 'sentence_end'},
    'かしら': {'gender': 'female', 'tone': 'wondering', 'category': 'sentence_end'},
    'かい': {'gender': 'male', 'tone': 'question_casual', 'category': 'sentence_end'},
    'だい': {'gender': 'male', 'tone': 'question_casual', 'category': 'sentence_end'},
    'じゃん': {'gender': 'neutral', 'tone': 'assertion_casual', 'category': 'sentence_end'},
    'だろう': {'gender': 'male', 'tone': 'conjecture', 'category': 'sentence_end'},
    'でしょう': {'gender': 'neutral', 'tone': 'conjecture_polite', 'category': 'sentence_end'},
}

# 一人称代词
FIRST_PERSON_PRONOUNS: dict[str, dict] = {
    '俺': {'gender': 'male', 'formality': 'casual', 'tone': 'rough'},
    '僕': {'gender': 'male', 'formality': 'polite_casual', 'tone': 'soft_young'},
    '私': {'gender': 'neutral_formal', 'formality': 'formal', 'tone': 'neutral'},
    'あたし': {'gender': 'female', 'formality': 'casual', 'tone': 'cute'},
    'あたい': {'gender': 'female', 'formality': 'casual', 'tone': 'tough'},
    'わし': {'gender': 'neutral', 'formality': 'casual', 'tone': 'elderly'},
    'わたくし': {'gender': 'neutral', 'formality': 'very_formal', 'tone': 'humble'},
    '俺様': {'gender': 'male', 'formality': 'arrogant', 'tone': 'dominant'},
    '自分': {'gender': 'male_lean', 'formality': 'neutral', 'tone': 'stoic'},
    'うち': {'gender': 'female', 'formality': 'casual', 'tone': 'dialect_female'},
}

# 二人称代词
SECOND_PERSON_PRONOUNS: dict[str, dict] = {
    'お前': {'gender': 'male', 'formality': 'rough', 'tone': 'rough_intimate'},
    '君': {'gender': 'male', 'formality': 'casual_polite', 'tone': 'superior'},
    'あなた': {'gender': 'neutral', 'formality': 'neutral', 'tone': 'neutral'},
    'あんた': {'gender': 'neutral', 'formality': 'casual', 'tone': 'rough_familiar'},
    '貴様': {'gender': 'male', 'formality': 'very_rough', 'tone': 'hostile'},
    'てめえ': {'gender': 'male', 'formality': 'very_rough', 'tone': 'very_hostile'},
}

# 敬语程度标识
KEIGO_PATTERNS: dict[str, dict] = {
    'です': {'level': 'teineigo', 'type': 'copula'},
    'ます': {'level': 'teineigo', 'type': 'verb_ending'},
    'ございます': {'level': 'teichogo', 'type': 'extra_polite'},
    'いらっしゃる': {'level': 'sonkeigo', 'type': 'honorific'},
    'おっしゃる': {'level': 'sonkeigo', 'type': 'honorific_say'},
    'なさる': {'level': 'sonkeigo', 'type': 'honorific_do'},
    '召し上がる': {'level': 'sonkeigo', 'type': 'honorific_eat'},
    '申す': {'level': 'kenjogo', 'type': 'humble_say'},
    'いたす': {'level': 'kenjogo', 'type': 'humble_do'},
    'おる': {'level': 'kenjogo', 'type': 'humble_be'},
    'いただく': {'level': 'kenjogo', 'type': 'humble_receive'},
    '差し上げる': {'level': 'kenjogo', 'type': 'humble_give'},
    '参る': {'level': 'kenjogo', 'type': 'humble_go'},
}

# ==================== 世界观分析 ====================

def analyze_worldview(text: str) -> dict:
    """分析文本的世界观特征

    参数:
        text: 日文文本（单行或多行）

    返回:
        {
            "gender_tendency": "male" | "female" | "mixed" | "neutral",
            "overall_tone": str,
            "formality_level": str,
            "sentence_end_particles": [(particle, count)],
            "first_persons": [(pronoun, count)],
            "second_persons": [(pronoun, count)],
            "keigo_patterns": [(pattern, count)],
            "distinctive_features": [str],
        }
    """
    result = {
        "gender_tendency": "neutral",
        "overall_tone": "neutral",
        "formality_level": "neutral",
        "sentence_end_particles": [],
        "first_persons": [],
        "second_persons": [],
        "keigo_patterns": [],
        "distinctive_features": [],
    }

    # 统计文末语气词
    particle_counts: dict[str, int] = {}
    for particle in SENTENCE_END_PARTICLES:
        count = len(re.findall(re.escape(particle) + r'(?=[\s。！？\n]|$)', text))
        if count > 0:
            particle_counts[particle] = count

    result["sentence_end_particles"] = sorted(
        particle_counts.items(), key=lambda x: -x[1]
    )

    # 统计一人称
    first_person_counts: dict[str, int] = {}
    for pronoun in FIRST_PERSON_PRONOUNS:
        count = text.count(pronoun)
        if count > 0:
            first_person_counts[pronoun] = count

    result["first_persons"] = sorted(
        first_person_counts.items(), key=lambda x: -x[1]
    )

    # 统计二人称
    second_person_counts: dict[str, int] = {}
    for pronoun in SECOND_PERSON_PRONOUNS:
        count = text.count(pronoun)
        if count > 0:
            second_person_counts[pronoun] = count

    result["second_persons"] = sorted(
        second_person_counts.items(), key=lambda x: -x[1]
    )

    # 统计敬语
    keigo_counts: dict[str, int] = {}
    for pattern in KEIGO_PATTERNS:
        count = text.count(pattern)
        if count > 0:
            keigo_counts[pattern] = count

    result["keigo_patterns"] = sorted(
        keigo_counts.items(), key=lambda x: -x[1]
    )

    # ---- 分析性别倾向 ----
    male_score = 0
    female_score = 0

    for particle, count in particle_counts.items():
        if particle in SENTENCE_END_PARTICLES:
            gender = SENTENCE_END_PARTICLES[particle].get('gender', 'neutral')
            if gender == 'male':
                male_score += count
            elif gender == 'female':
                female_score += count
            elif gender == 'male_lean':
                male_score += count * 0.5

    for pronoun, count in first_person_counts.items():
        if pronoun in FIRST_PERSON_PRONOUNS:
            gender = FIRST_PERSON_PRONOUNS[pronoun].get('gender', 'neutral')
            if gender == 'male':
                male_score += count * 2
            elif gender == 'female':
                female_score += count * 2
            elif gender == 'male_lean':
                male_score += count

    for pronoun, count in second_person_counts.items():
        if pronoun in SECOND_PERSON_PRONOUNS:
            gender = SECOND_PERSON_PRONOUNS[pronoun].get('gender', 'neutral')
            if gender == 'male':
                male_score += count
            elif gender == 'female':
                female_score += count

    if male_score > female_score + 2:
        result["gender_tendency"] = "male"
    elif female_score > male_score + 2:
        result["gender_tendency"] = "female"
    elif male_score > 0 and female_score > 0:
        result["gender_tendency"] = "mixed"
    else:
        result["gender_tendency"] = "neutral"

    # ---- 语气分析 ----
    tones: list[str] = []
    for particle, count in particle_counts[:3]:
        if particle in SENTENCE_END_PARTICLES:
            tone = SENTENCE_END_PARTICLES[particle].get('tone', '')
            if tone:
                tones.append(tone)

    if not tones:
        result["overall_tone"] = "neutral"
    else:
        # 取最常见的语气
        from collections import Counter
        tone_counter = Counter(tones)
        result["overall_tone"] = tone_counter.most_common(1)[0][0]

    # ---- 敬语级别 ----
    keigo_levels: dict[str, int] = {
        'teineigo': 0,
        'sonkeigo': 0,
        'kenjogo': 0,
        'teichogo': 0,
    }
    for pattern, count in keigo_counts.items():
        if pattern in KEIGO_PATTERNS:
            level = KEIGO_PATTERNS[pattern].get('level', '')
            if level in keigo_levels:
                keigo_levels[level] += count

    if keigo_levels['teichogo'] > 0:
        result["formality_level"] = "very_formal"
    elif keigo_levels['sonkeigo'] > 0 or keigo_levels['kenjogo'] > 0:
        result["formality_level"] = "formal"
    elif keigo_levels['teineigo'] > 0:
        result["formality_level"] = "polite"
    else:
        result["formality_level"] = "casual"

    # ---- 显著特征 ----
    features: list[str] = []
    if result["gender_tendency"] == "male":
        features.append("男性口吻")
    elif result["gender_tendency"] == "female":
        features.append("女性口吻")
    if result["overall_tone"] in ('assertive', 'casual_rough', 'rough'):
        features.append("语气粗犷")
    elif result["overall_tone"] in ('softening', 'cute', 'soft'):
        features.append("语气柔和")
    if result["formality_level"] in ('formal', 'very_formal'):
        features.append("敬体")

    result["distinctive_features"] = features

    return result


def get_worldview_prompt_hint(analysis: dict) -> str:
    """根据世界观分析结果生成翻译提示

    参数:
        analysis: analyze_worldview 的返回结果

    返回: 提示字符串（可为空）
    """
    hints: list[str] = []

    if analysis["gender_tendency"] == "male":
        hints.append("说话者为男性，翻译时请使用男性口吻")
    elif analysis["gender_tendency"] == "female":
        hints.append("说话者为女性，翻译时请使用女性口吻")

    if analysis["formality_level"] == "very_formal":
        hints.append("使用正式/尊敬的语体")
    elif analysis["formality_level"] == "casual":
        hints.append("使用口语化/随意语体")

    if analysis["overall_tone"] in ('assertive', 'casual_rough', 'rough'):
        hints.append("语气应体现自信/粗犷风格")
    elif analysis["overall_tone"] in ('softening', 'soft'):
        hints.append("语气应保持柔和/委婉")

    if not hints:
        return ""

    return "【语气/人设提示】\n" + "\n".join(f"  - {h}" for h in hints)


# ==================== 从目录加载世界观 ====================

def load_worldview_from_dir(work_dir: "Path") -> tuple[dict | None, list]:
    """从工作目录中加载世界观/角色/场景设定

    返回:
        (worldview_dict, source_files)
        worldview_dict: {worldview, characters, scene} 或 None
        source_files: 找到的文件绝对路径列表
    """
    import json as _json

    worldview: dict = {
        "worldview": "",
        "characters": {},
        "scene": "",
    }
    has_any = False
    sources: list = []  # 收集所有找到的文件

    def _add_source(fpath):
        p = fpath.absolute()
        if p not in sources:
            sources.append(p)

    # 搜索世界观文件
    wv_candidates = ['worldview.json', 'world_setting.json', 'world.json',
                     '世界观.json', '世界观.txt', 'worldview.txt']
    for name in wv_candidates:
        for fpath in work_dir.rglob(name):
            try:
                content = fpath.read_text(encoding='utf-8').strip()
                if content:
                    if fpath.suffix == '.json':
                        data = _json.loads(content)
                        if isinstance(data, dict):
                            worldview['worldview'] = data.get('worldview') or data.get('description') or data.get('text') or _json.dumps(data, ensure_ascii=False)
                        elif isinstance(data, str):
                            worldview['worldview'] = data
                    else:
                        worldview['worldview'] = content
                    has_any = True
                    _add_source(fpath)
                    print(f"  [世界观] 加载文件: {fpath.absolute()} ({len(worldview['worldview'])} 字符)")
            except Exception as e:
                print(f"  [世界观] 读取 {fpath.absolute()} 失败: {e}")

    # 搜索角色文件
    char_candidates = ['characters.json', 'char.json', '角色.json', 'characters.txt', '角色.txt']
    for name in char_candidates:
        for fpath in work_dir.rglob(name):
            try:
                content = fpath.read_text(encoding='utf-8').strip()
                if fpath.suffix == '.json':
                    data = _json.loads(content)
                    if isinstance(data, dict):
                        worldview['characters'].update(data)
                else:
                    for line in content.split('\n'):
                        line = line.strip()
                        if ':' in line or '：' in line:
                            sep = ':' if ':' in line else '：'
                            name, desc = line.split(sep, 1)
                            worldview['characters'][name.strip()] = desc.strip()
                has_any = True
                _add_source(fpath)
                print(f"  [世界观] 加载角色: {fpath.absolute()} ({len(worldview['characters'])} 个角色)")
            except Exception as e:
                print(f"  [世界观] 读取角色 {fpath.absolute()} 失败: {e}")

    # 搜索场景文件
    scene_candidates = ['scene.json', 'setting.json', '场景.json', 'scene.txt', '场景.txt']
    for name in scene_candidates:
        for fpath in work_dir.rglob(name):
            try:
                content = fpath.read_text(encoding='utf-8').strip()
                if fpath.suffix == '.json':
                    data = _json.loads(content)
                    if isinstance(data, dict):
                        worldview['scene'] = data.get('scene') or data.get('description') or data.get('text') or _json.dumps(data, ensure_ascii=False)
                    elif isinstance(data, str):
                        worldview['scene'] = data
                else:
                    worldview['scene'] = content
                has_any = True
                _add_source(fpath)
                print(f"  [世界观] 加载场景: {fpath.absolute()} ({len(worldview['scene'])} 字符)")
            except Exception as e:
                print(f"  [世界观] 读取场景 {fpath.absolute()} 失败: {e}")

    # 也搜索 .worldview.json（隐藏文件命名）
    for fpath in work_dir.rglob('.worldview.json'):
        try:
            content = fpath.read_text(encoding='utf-8').strip()
            if content:
                data = _json.loads(content)
                if isinstance(data, dict):
                    if not worldview['worldview']:
                        worldview['worldview'] = data.get('worldview', '')
                    chars = data.get('characters', [])
                    if isinstance(chars, list):
                        for char in chars:
                            if isinstance(char, dict) and 'name' in char:
                                name = char['name']
                                desc = char.get('personality', '') or char.get('role', '')
                                if name not in worldview['characters']:
                                    worldview['characters'][name] = desc
                    elif isinstance(chars, dict):
                        for name, desc in chars.items():
                            if name not in worldview['characters']:
                                worldview['characters'][name] = str(desc)
                    if not worldview['scene']:
                        worldview['scene'] = data.get('scene', '')
                has_any = True
                _add_source(fpath)
                print(f"  [世界观] 加载文件: {fpath.absolute()}")
        except Exception as e:
            print(f"  [世界观] 读取 {fpath.absolute()} 失败: {e}")

    return (worldview if has_any else None, sources)


# ==================== 世界观文件路径 ====================

def get_worldview_path(work_dir: "Path") -> "Path":
    """获取世界观文件路径"""
    from pathlib import Path
    return work_dir / '.worldview.json'


def load_worldview(work_dir: "Path") -> dict:
    """加载已保存的世界观（从 .worldview.json）"""
    import json
    worldview_path = get_worldview_path(work_dir)
    if worldview_path.exists():
        try:
            with open(worldview_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return {}
    return {}


def save_worldview(work_dir: "Path", worldview: dict) -> None:
    """保存世界观到 .worldview.json"""
    import json
    worldview_path = get_worldview_path(work_dir)
    with open(worldview_path, 'w', encoding='utf-8') as f:
        json.dump(worldview, f, ensure_ascii=False, indent=2)


# ==================== 世界观 Prompt 构建 ====================

def build_worldview_prompt(worldview: dict) -> str:
    """将世界观构建为 prompt 附加内容

    兼容两种 characters 格式：
    - 数组: [{name, name_cn, role, personality}]
    - 字典: {name: description}
    """
    if not worldview:
        return ""

    lines = ["\n【作品背景（请在翻译时参考以下信息，保持设定一致）】"]

    if worldview.get('worldview'):
        lines.append(f"  世界观: {worldview['worldview']}")

    # 主题（影响翻译语气和风格）
    themes = worldview.get('themes', [])
    if themes:
        themes_str = '、'.join(themes) if isinstance(themes, list) else str(themes)
        lines.append(f"  作品主题: {themes_str}")

    characters = worldview.get('characters', [])
    if characters:
        lines.append("  角色:")
        if isinstance(characters, list):
            for char in characters[:20]:
                name = char.get('name', '') if isinstance(char, dict) else str(char)
                name_cn = char.get('name_cn', '') if isinstance(char, dict) else ''
                role = char.get('role', '') if isinstance(char, dict) else ''
                personality = char.get('personality', '') if isinstance(char, dict) else ''
                if name:
                    char_info = f"    {name}"
                    if name_cn:
                        char_info += f"（中文: {name_cn}）"
                    if role:
                        char_info += f" [{role}]"
                    if personality:
                        char_info += f" — 性格: {personality}"
                    lines.append(char_info)
        elif isinstance(characters, dict):
            for name, desc in list(characters.items())[:20]:
                lines.append(f"    {name}: {desc}")

    # 特殊设定术语 —— 提升为翻译硬约束
    special_terms = worldview.get('special_terms', {})
    if special_terms:
        lines.append("")
        lines.append("  【特殊设定术语——翻译时必须严格遵守以下译法，确保全文一致】")
        for term, desc in list(special_terms.items())[:20]:
            lines.append(f"    「{term}」→ 译作「{desc}」")

    lines.append("【背景信息结束】\n")
    return "\n".join(lines)


# ==================== LLM 世界观分析 ====================

WORLDVIEW_ANALYSIS_PROMPT = (
    "以下是日文成人向虚构音声（ASMR/RJ作品）的ASR转录文本样本。\n"
    "请分析这些样本，提取作品的世界观信息。\n\n"
    "输出要求（紧凑JSON格式）：\n"
    '{"worldview": "作品世界观简介（150字以内，包含：时代背景、主要角色关系、核心设定）", '
    '"characters": [{"name": "日文角色名", "name_cn": "建议中文译名（用于确保全文翻译一致性）", '
    '"role": "角色定位", "personality": "性格特点"}], '
    '"themes": ["主题1", "主题2"], '
    '"special_terms": {"日文术语": "中文译名"}}\n\n'
    "注意：\n"
    "- worldview 必须精炼，用于辅助后续翻译理解上下文，控制在150字以内\n"
    "- characters 只列出主要角色，最多5个；name_cn 是角色名的建议中文翻译，将作为术语表强制使用\n"
    "- special_terms 列出对翻译有影响的特殊设定术语。值必须是纯中文译名（词语/短語），禁止加括号注释、解释说明、或任何额外文字。因为该译名会被直接注入术语表作为强制翻译\n"
    "- 成人向虚构内容请以学术化方式客观描述设定，无需回避\n"
    "- 不要输出任何 JSON 之外的内容\n"
)


def analyze_worldview_with_llm(engine, samples: list[str], verbose: bool = False) -> tuple:
    """调用 LLM 分析作品世界观

    参数:
        engine: OpenAICompatEngine 实例
        samples: 抽样文本列表
        verbose: 是否输出调试信息

    返回: (世界观字典, token_stats)
    """
    import json as _json

    if not samples:
        return {}, {}

    content = WORLDVIEW_ANALYSIS_PROMPT + "\n\n" + "\n\n".join(samples)
    if verbose:
        print(f"    [DEBUG] 发送给 LLM 的文本长度: {len(content)} 字符", flush=True)
    print(f"    [世界观分析] 正在调用 LLM...", flush=True)

    try:
        system_prompt = "你是精通日语成人向虚构音声作品的分析专家。请严格按照JSON格式输出分析结果，不要输出任何其他内容。"
        raw_output, token_stats = engine.call_api(
            system_prompt, content,
            override_gen_params={'temperature': 0.1},
        )
        if verbose:
            print(f"    [DEBUG] LLM 原始输出 (前300字): {raw_output[:300]}", flush=True)

        json_match = re.search(r'\{.*\}', raw_output, re.DOTALL)
        if json_match:
            try:
                result = _json.loads(json_match.group())
                print(f"    [世界观分析] 完成!", flush=True)
                print(f"      世界观: {result.get('worldview', '')[:100]}...", flush=True)
                print(f"      角色: {len(result.get('characters', []))} 个", flush=True)
                return result, token_stats
            except _json.JSONDecodeError as e:
                print(f"    ⚠ JSON 解析错误: {e}", flush=True)
                return {}, {}
        else:
            print(f"    ⚠ 未找到 JSON 格式内容", flush=True)
            return {}, {}

    except Exception as e:
        print(f"    ⚠ 世界观分析失败: {e}", flush=True)
        return {}, {}


# ==================== FreeTalk 检测 ====================

FREETALK_KEYWORDS = {
    'freetalk', 'free_talk', 'free talk',
    'フリートーク', 'ふりーとーく', 'フリトク',
    'フリートク', 'あとがき', 'アトガキ',
    'おまけ', 'オマケ', 'bonus', 'omake',
    '特典', 'とくてん', 'tokuten',
    'インタビュー', 'interview',
    'コメント', 'comment',
}

POPULAR_ASMR_CV_NAMES = {
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
}


def is_freetalk_context(path: "Path") -> tuple[bool, str | None]:
    """检测路径是否为 freetalk 或包含热门CV名称

    返回: (is_freetalk, cv_name_or_None)
    """
    path_str = str(path).lower()
    path_parts = path.parts if hasattr(path, 'parts') else str(path).replace('\\', '/').split('/')

    for keyword in FREETALK_KEYWORDS:
        if keyword.lower() in path_str:
            return True, None

    for part in path_parts:
        for cv_name in POPULAR_ASMR_CV_NAMES:
            if len(cv_name) < 3:
                continue
            pattern = re.compile(
                r'(^|[\s\_\-\(\)（）\[\]「」『』【】])' + re.escape(cv_name) + r'($|[\s\_\-\(\)（）\[\]「」『』【】])')
            if pattern.search(part, re.IGNORECASE):
                return True, cv_name

    return False, None