# -*- coding: utf-8 -*-
"""
术语管理器

负责：
- 从 config.json 加载术语表（terms / alias）
- 构建翻译 Prompt 中的术语引用
- 注音聚类（将读音相同或相近的术语归组，避免重复翻译）
"""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional


# ==================== 术语加载 ====================

def load_terms_from_config(config: dict) -> dict[str, str]:
    """从配置字典加载术语表

    参数:
        config: 完整配置字典

    返回:
        {日文: 中文翻译}
    """
    terms: dict[str, str] = {}

    # 从 config.json 的 terms 段加载
    config_terms = config.get('terms', {})
    if isinstance(config_terms, dict):
        for jp, cn in config_terms.items():
            if cn and jp.strip():
                terms[jp.strip()] = cn.strip()

    return terms


def load_terms_from_file(terms_path: Path) -> dict[str, str]:
    """从术语文件加载术语表

    支持格式：
    - JSON: {"日文": "中文"}
    - TSV: 日文\t中文
    - 自定义: 日文→中文（每行一对）

    参数:
        terms_path: 术语文件路径

    返回:
        {日文: 中文翻译}
    """
    if not terms_path.exists():
        return {}

    terms: dict[str, str] = {}
    content = terms_path.read_text(encoding='utf-8')

    # 尝试 JSON
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            for jp, cn in data.items():
                if cn and jp.strip():
                    terms[jp.strip()] = cn.strip()
            return terms
    except (json.JSONDecodeError, ValueError):
        pass

    # 尝试行格式
    for line in content.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        # 支持 "日文→中文" 或 "日文\t中文"
        for sep in ('→', '\t', ':', '：'):
            if sep in line:
                parts = line.split(sep, 1)
                if len(parts) == 2:
                    jp = parts[0].strip()
                    cn = parts[1].strip()
                    if jp and cn:
                        terms[jp] = cn
                    break

    return terms


def merge_terms(*term_dicts: dict[str, str]) -> dict[str, str]:
    """合并多个术语表，后面的覆盖前面的"""
    merged: dict[str, str] = {}
    for d in term_dicts:
        if d:
            merged.update(d)
    return merged


# ==================== Alias 加载 ====================

def load_alias_list(config: dict) -> list[str]:
    """从配置加载 ASR 误识别参考列表（alias）

    alias 用于标注日语音频中容易听错的词，
    帮助 LLM 在翻译时识别并修正。

    返回: alias 字符串列表
    """
    aliases = config.get('alias', [])
    if isinstance(aliases, list):
        return [str(a).strip() for a in aliases if str(a).strip()]
    if isinstance(aliases, str):
        return [aliases.strip()]
    return []


# ==================== 注音聚类 ====================

def cluster_terms_by_pronunciation(
    terms: dict[str, str],
    similarity_threshold: float = 0.7,
) -> list[list[tuple[str, str]]]:
    """
    将术语按注音/读音相似度聚类

    原理：
    对日文术语，使用 pyopenjtalk 获取读音（yomi），
    然后将读音相似的归为一组。组内术语在 prompt 中会标注为
    "这些都读作 XXX，请不要混淆"。

    参数:
        terms: {日文: 中文}
        similarity_threshold: 相似度阈值 (0-1)

    返回:
        [[(日文, 中文), ...], ...]  每组至少两个成员
    """
    # 获取每个术语的读音
    term_yomi: list[tuple[str, str, str]] = []  # (日文, 中文, 读音)
    try:
        import pyopenjtalk
        for jp, cn in terms.items():
            try:
                yomi = pyopenjtalk.g2p(jp, kana=True)
                term_yomi.append((jp, cn, yomi))
            except Exception:
                term_yomi.append((jp, cn, jp))  # 无法获取读音时用原文替代
    except ImportError:
        # 无 pyopenjtalk，每个术语单独一组
        return [[(jp, cn)] for jp, cn in terms.items()]

    # 简单聚类：完全相同读音的归为一组
    yomi_groups: dict[str, list[tuple[str, str]]] = {}
    for jp, cn, yomi in term_yomi:
        yomi_normalized = yomi.strip()
        yomi_groups.setdefault(yomi_normalized, []).append((jp, cn))

    # 过滤：只保留 >=2 个成员的组
    clusters: list[list[tuple[str, str]]] = []
    singles: list[tuple[str, str]] = []
    for yomi, items in yomi_groups.items():
        if len(items) >= 2:
            clusters.append(items)
        else:
            singles.extend(items)

    # 单成员术语单独成组
    for item in singles:
        clusters.append([item])

    return clusters


# ==================== Prompt 构建 ====================

def build_terms_prompt_section(
    terms: dict[str, str],
    max_terms: int = 30,
) -> Optional[str]:
    """构建术语对照表的 Prompt 片段

    参数:
        terms: {日文: 中文}
        max_terms: 最多输出条数

    返回:
        Prompt 字符串，无术语时返回 None
    """
    if not terms or len(terms) == 0:
        return None

    lines: list[str] = []
    for jp, cn in list(terms.items())[:max_terms]:
        lines.append(f"  {jp} → {cn}")

    return "【术语对照表——以下词语必须采用指定的翻译】\n" + "\n".join(lines)


def build_alias_prompt_section(
    alias_list: list[str],
) -> Optional[str]:
    """构建 ASR 误识别提示的 Prompt 片段

    参数:
        alias_list: ASR 误识别参考列表

    返回:
        Prompt 字符串，无 alias 时返回 None
    """
    if not alias_list:
        return None

    alias_text = "\n".join(f"  - {a}" for a in alias_list[:20])
    return (
        "【ASR 误识别参考——以下为日语音频中容易听错的词汇对照】\n"
        f"{alias_text}\n\n"
        "如果输入行中出现相似读音的词汇，请参考以上对照进行修正后再翻译。"
    )


def build_clustered_terms_prompt_section(
    terms: dict[str, str],
    max_terms: int = 30,
) -> Optional[str]:
    """构建带注音聚类的术语 Prompt 片段

    对读音相同/相近的术语进行分组标注，
    帮助 LLM 区分同音异义词。

    参数:
        terms: {日文: 中文}
        max_terms: 最多输出条数

    返回:
        Prompt 字符串，无术语时返回 None
    """
    if not terms or len(terms) == 0:
        return None

    clusters = cluster_terms_by_pronunciation(terms)

    lines: list[str] = []
    count = 0

    for cluster in clusters:
        if count >= max_terms:
            break
        if len(cluster) == 1:
            jp, cn = cluster[0]
            lines.append(f"  {jp} → {cn}")
            count += 1
        else:
            # 同音聚类
            items = [f"{jp}→{cn}" for jp, cn in cluster]
            lines.append(f"  [同音词] {' | '.join(items)}")
            count += len(cluster)

    return "【术语对照表——以下词语必须采用指定的翻译】\n" + "\n".join(lines)


# ==================== 保存术语 ====================

def save_terms_to_config(
    config: dict,
    new_terms: dict[str, str],
    config_path: Path,
) -> None:
    """将新术语追加到 config.json

    保留已有术语，追加不重复的新术语。

    参数:
        config: 当前配置字典
        new_terms: 新术语 {日文: 中文}
        config_path: config.json 路径
    """
    existing_terms = config.get('terms', {})
    if not isinstance(existing_terms, dict):
        existing_terms = {}

    changed = False
    for jp, cn in new_terms.items():
        if jp not in existing_terms:
            existing_terms[jp] = cn
            changed = True

    if changed:
        config['terms'] = existing_terms
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )


# ==================== 身份称呼统一译名表 ====================

ROLE_TRANSLATIONS = {
    # 家庭关系
    '母': '妈妈', '母さん': '妈妈', 'お母さん': '妈妈', 'ママ': '妈妈', 'かあさん': '妈妈',
    '姉': '姐姐', '姉さん': '姐姐', 'お姉さん': '姐姐', '姉ちゃん': '姐姐', 'お姉ちゃん': '姐姐',
    '妹': '妹妹', '妹さん': '妹妹', 'いもうと': '妹妹',
    '父': '爸爸', '父さん': '爸爸', 'お父さん': '爸爸', 'パパ': '爸爸', 'とうさん': '爸爸',
    '兄': '哥哥', '兄さん': '哥哥', 'お兄さん': '哥哥', '兄ちゃん': '哥哥', 'お兄ちゃん': '哥哥',
    '弟': '弟弟', '弟さん': '弟弟',
    'おじ': '叔叔', '叔父': '叔叔', '叔父さん': '叔叔',
    'おば': '阿姨', '叔母': '阿姨', '叔母さん': '阿姨',
    '祖父': '爷爷', 'おじいさん': '爷爷',
    '祖母': '奶奶', 'おばあさん': '奶奶',
    '娘': '女儿', '息子': '儿子', '孫': '孙子',
    # 师生关系
    '先生': '老师', '教師': '老师', '生徒': '学生',
    '先輩': '前辈', '後輩': '后辈',
    # 职业称呼
    '看護師': '护士', '医者': '医生', '店員': '店员', '受付': '前台',
    # 特殊关系
    '主': '主人', 'ご主人': '主人', '坊ちゃん': '少爷',
    'お嬢様': '大小姐', '令嬢': '大小姐',
}

# 已知误译模式
_SUSPICIOUS_TERM_PATTERNS = {
    'メス': ['母'],
    'オス': ['公'],
}

# 通用生物词汇（音声语境下不需要统一翻译）
_GENERIC_BIOLOGY_TERMS = {
    'メス', 'オス', '雌', '雄', '牝', '牡',
    '犬', 'いぬ', '猫', 'ねこ', '鳥', 'とり', '魚', 'さかな',
}


# ==================== 术语/Alias 文件管理 ====================

def get_terms_path(work_dir: "Path") -> "Path":
    """获取术语表文件路径（作品级，.terms.json）"""
    from pathlib import Path
    return work_dir / '.terms.json'


def get_alias_path(work_dir: "Path") -> "Path":
    """获取 alias 表文件路径（作品级，.alias.json）"""
    from pathlib import Path
    return work_dir / '.alias.json'


def save_terms_to_file(work_dir: "Path", terms: dict[str, str]) -> None:
    """保存术语表到作品目录"""
    terms_path = get_terms_path(work_dir)
    with open(terms_path, 'w', encoding='utf-8') as f:
        json.dump(terms, f, ensure_ascii=False, indent=2)


def save_alias(work_dir: "Path", alias_list: list[dict]) -> None:
    """保存 alias 表到作品目录"""
    alias_path = get_alias_path(work_dir)
    with open(alias_path, 'w', encoding='utf-8') as f:
        json.dump(alias_list, f, ensure_ascii=False, indent=2)


def load_alias(work_dir: "Path") -> list[dict]:
    """加载已保存的 alias 表"""
    alias_path = get_alias_path(work_dir)
    if alias_path.exists():
        try:
            with open(alias_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return []
    return []


def update_terms_and_alias(work_dir: "Path", new_terms: dict[str, str], new_alias: list[dict]) -> None:
    """更新术语表和 alias 表（合并去重）"""
    if not new_terms and not new_alias:
        return

    # 加载已有
    existing_terms = load_terms_from_file(get_terms_path(work_dir))
    existing_alias = load_alias(work_dir)

    # 合并术语
    added_terms = 0
    for ja, zh in new_terms.items():
        if ja not in existing_terms:
            existing_terms[ja] = zh
            added_terms += 1

    # 合并 alias（去重）
    existing_alias_pairs = {(a.get('alias'), a.get('target')) for a in existing_alias}
    added_alias = 0
    for alias_item in new_alias:
        pair = (alias_item.get('alias'), alias_item.get('target'))
        if pair not in existing_alias_pairs:
            existing_alias.append(alias_item)
            existing_alias_pairs.add(pair)
            added_alias += 1

    if added_terms > 0:
        save_terms_to_file(work_dir, existing_terms)
        print(f"      + 术语: {added_terms} 个")
    if added_alias > 0:
        save_alias(work_dir, existing_alias)
        print(f"      + alias: {added_alias} 个")


def build_alias_prompt(alias_list: list[dict]) -> str | None:
    """将 alias 表构建为 prompt 附加内容（弱约束）"""
    if not alias_list:
        return None

    lines = ["\n【ASR误识别参考——以下同音/近音对应关系供翻译时参考】"]
    for item in alias_list:
        alias = item.get('alias', '')
        target = item.get('target', '')
        conf = item.get('confidence', 0)
        if alias and target:
            lines.append(f"  {alias} 可能是 {target} 的误识别（置信度: {conf:.0%}）")
    return "\n".join(lines)


# ==================== 文件名角色名提取 ====================

import re as _re

_FILENAME_HONORIFIC_RE = _re.compile(
    r'([぀-ゟ゠-ヿ]+?)(?:ちゃん|さん|様|さま|くん|君|たん|チャン|サン|サマ|クン|タン)', _re.I)
_FILENAME_NAME_RE = _re.compile(r'([一-鿿]{2,4})')

_NON_NAME_SUFFIXES = (
    'した', 'する', 'ある', 'れる', 'なる', 'いる', 'ない', 'たい', 'だっ', 'ちゃ', 'なっ', 'やっ',
    'くれ', 'あっ', 'さ', 'て', 'で', 'に', 'が', 'を', 'と', 'の', 'は', 'も', 'や', 'へ',
    'から', 'まで', 'より', 'ば', 'ばっ', 'じゃ', 'ぜ', 'ぞ', 'ね', 'よ', 'わ', 'な', 'か',
)

_NAME_BLOCKLIST = {
    '美人', '親子', '家庭', '大学', '時代', '再会', '脅迫', '脅し',
    '撮影', '鑑賞', '下品', '路地', '裏', '連行', '素股', '服従',
    '奉仕', '立場', '無理', '喪失', '強制', '中出', '変態', '声',
    '狂', '戻', '母親', '処女', '耳舐', '手コ', '下校', '途中',
    '口ま', '使っ', '前で', '後で', '自分', '元カ', 'カレ',
    'プロ', 'ローグ', 'ギャル', 'ママ', 'オナ', 'ニー',
    'イラ', 'マチ', 'オ', 'ラブ', 'ホ', 'えっ', 'ち', 'プレイ',
    '揃っ', 'わから', 'やり', 'セッ', 'クス', '娘の',
    '狂い', '戻っ', 'オホ', 'アク', 'メ',
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


def extract_names_from_filenames(work_dir: "Path", audio_suffixes: list[str] = None, subtitle_exts: list[str] = None) -> set[str]:
    """从音频/字幕文件名中提取角色名候选

    支持两种形式：
    1. 假名 + 称呼后缀（如 'こりんちゃん' -> 'こりん'）
    2. 纯汉字角色名（如 '樹里'、'梨乃'）
    """
    from pathlib import Path
    names: set[str] = set()

    if audio_suffixes is None:
        audio_suffixes = ['.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma', '.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv']
    if subtitle_exts is None:
        subtitle_exts = ['.lrc', '.srt', '.vtt']

    all_exts = set(audio_suffixes) | set(subtitle_exts)

    for fpath in work_dir.rglob('*'):
        if not fpath.is_file():
            continue
        if fpath.suffix.lower() not in all_exts:
            continue

        stem = fpath.stem
        stem = _re.sub(r'^[\d０-９]+[)）]', '', stem)

        # 1. 查找带称呼后缀的假名角色名
        for match in _FILENAME_HONORIFIC_RE.finditer(stem):
            core = match.group(1)
            if len(core) < 2 or len(core) > 8:
                continue
            cleaned = core
            for sfx in _NON_NAME_SUFFIXES:
                if cleaned.endswith(sfx) and len(cleaned) - len(sfx) >= 2:
                    cleaned = cleaned[:-len(sfx)]
                    break
            names.add(cleaned)

        # 2. 查找纯汉字角色名
        for match in _FILENAME_NAME_RE.finditer(stem):
            core = match.group(1)
            if len(core) < 2 or len(core) > 4:
                continue
            if core in _NAME_BLOCKLIST:
                continue
            names.add(core)

    return names


# ==================== Core 提取（从 .ja.lrc 文件） ====================

from collections import defaultdict
from io_adapter.lrc_handler import extract_subtitle_texts


def extract_cores_from_file(ja_path: "Path", filename_names: set[str] = None) -> dict[str, dict]:
    """扫描单个字幕文件，提取 core 级候选词

    返回: {core: {reading, pos, count, surfaces, contexts, files, pronouns}}
    """
    from pathlib import Path
    from core.text_analysis import tokenize_text, STOP_WORDS, is_fugashi_available

    if not is_fugashi_available():
        return {}

    cores = defaultdict(lambda: {
        'reading': '', 'pos': '', 'count': 0,
        'surfaces': defaultdict(int), 'contexts': [], 'files': set(),
        'pronouns': defaultdict(int),
    })

    ext = ja_path.suffix.lower()
    try:
        with open(ja_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return {}

    lyric_texts = extract_subtitle_texts(content, ext)
    pronoun_pattern = _re.compile(r'^(私|わたし|わたくし|あたし|僕|ぼく|俺|おれ|うち)$')

    for line in lyric_texts:
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

            if pos not in ('名詞', '固有名詞', '代名詞'):
                continue
            if core in STOP_WORDS or surface in STOP_WORDS or reading in STOP_WORDS:
                continue
            if len(core) < 1:
                continue
            if not suffix and len(core) < 2:
                continue
            if _re.match(r'^[ぁ-ん]{1,4}$', core) and not suffix:
                if core not in ('にい', 'あね', 'あに', 'おに', 'おね'):
                    continue

            cores[core]['reading'] = reading
            cores[core]['pos'] = pos
            cores[core]['count'] += 1
            cores[core]['surfaces'][surface] += 1
            cores[core]['files'].add(str(ja_path))
            for pr in line_pronouns:
                cores[core]['pronouns'][pr] += 1
            if len(cores[core]['contexts']) < 3:
                if suffix or not any(surface in ctx for ctx in cores[core]['contexts']):
                    cores[core]['contexts'].append(line)

    result = {}
    filename_names = filename_names or set()
    for core, info in cores.items():
        info['surfaces'] = dict(info['surfaces'])
        info['files'] = list(info['files'])
        info['pronouns'] = dict(info['pronouns'])

        has_suffix = any(len(s) > len(core) for s in info['surfaces'])
        is_katakana = bool(_re.search(r'[゠-ヿ]', core))
        is_proper = info['pos'] == '固有名詞'
        from_filename = core in filename_names

        if from_filename:
            result[core] = info
            continue
        if len(core) < 2:
            continue
        if len(core) == 2 and _re.match(r'^[ぁ-んー]{2}$', core) and not from_filename and not has_suffix:
            continue
        if _re.match(r'^[ぁ-んー]{3,4}$', core) and not has_suffix and info['count'] < 5:
            continue
        if has_suffix or is_katakana or is_proper or info['count'] >= 3:
            result[core] = info

    return result


def extract_cores_from_dir(work_dir: "Path", filename_names: set[str] = None) -> dict[str, dict]:
    """扫描作品目录下所有字幕文件，合并 core 级候选词"""
    from pathlib import Path
    from io_adapter.lrc_handler import SUBTITLE_EXTS

    merged_cores = defaultdict(lambda: {
        'reading': '', 'pos': '', 'count': 0,
        'surfaces': defaultdict(int), 'contexts': [], 'files': set(),
        'pronouns': defaultdict(int),
    })

    for ext in SUBTITLE_EXTS:
        for fpath in work_dir.rglob(f'*{ext}'):
            if not fpath.is_file():
                continue
            base = fpath.stem
            if ext == '.lrc':
                if base.endswith('.ja'):
                    file_cores = extract_cores_from_file(fpath, filename_names)
                    for core, info in file_cores.items():
                        merged_cores[core]['reading'] = info['reading']
                        merged_cores[core]['pos'] = info['pos']
                        merged_cores[core]['count'] += info['count']
                        for s, c in info['surfaces'].items():
                            merged_cores[core]['surfaces'][s] += c
                        merged_cores[core]['files'].add(str(fpath))
                        for pr, c in info['pronouns'].items():
                            merged_cores[core]['pronouns'][pr] += c
                        for ctx in info['contexts']:
                            if len(merged_cores[core]['contexts']) < 5:
                                if not any(ctx in existing for existing in merged_cores[core]['contexts']):
                                    merged_cores[core]['contexts'].append(ctx)
            else:
                if base.endswith('.ja'):
                    file_cores = extract_cores_from_file(fpath, filename_names)
                    for core, info in file_cores.items():
                        merged_cores[core]['reading'] = info['reading']
                        merged_cores[core]['pos'] = info['pos']
                        merged_cores[core]['count'] += info['count']
                        for s, c in info['surfaces'].items():
                            merged_cores[core]['surfaces'][s] += c
                        merged_cores[core]['files'].add(str(fpath))
                        for pr, c in info['pronouns'].items():
                            merged_cores[core]['pronouns'][pr] += c
                        for ctx in info['contexts']:
                            if len(merged_cores[core]['contexts']) < 5:
                                if not any(ctx in existing for existing in merged_cores[core]['contexts']):
                                    merged_cores[core]['contexts'].append(ctx)

    result = {}
    for core, info in merged_cores.items():
        info['surfaces'] = dict(info['surfaces'])
        info['files'] = list(info['files'])
        info['pronouns'] = dict(info['pronouns'])
        result[core] = info
    return result


# ==================== 抽样函数 ====================

def sample_all_lrc_files(work_dir: "Path", lines_per_file: int = 40, verbose: bool = False) -> tuple[list[str], dict[str, dict]]:
    """抽样所有字幕文件的内容，用于世界观和术语分析

    返回: (抽样文本列表, cores字典)
    """
    from pathlib import Path
    from io_adapter.lrc_handler import SUBTITLE_EXTS

    samples: list[str] = []
    all_cores = defaultdict(lambda: {
        'reading': '', 'pos': '', 'count': 0,
        'surfaces': defaultdict(int), 'contexts': [], 'files': set(),
        'pronouns': defaultdict(int),
    })

    ja_files = []
    for ext in SUBTITLE_EXTS:
        for fpath in work_dir.rglob(f'*{ext}'):
            if fpath.is_file() and fpath.stem.endswith('.ja'):
                ja_files.append(fpath)

    print(f"    [抽样] 发现 {len(ja_files)} 个字幕文件", flush=True)

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
            if verbose:
                print(f"      [{idx}/{len(ja_files)}] {ja_path.name}: 抽取 {len(sampled_lines[:lines_per_file])} 行", flush=True)

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

    for core, info in all_cores.items():
        info['surfaces'] = dict(info['surfaces'])
        info['files'] = list(info['files'])
        info['pronouns'] = dict(info['pronouns'])

    return samples, dict(all_cores)


# ==================== 读音相似聚类 ====================

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


def _normalize_reading(r: str) -> str:
    r = r.replace('ー', '')
    r = r.replace('ッ', '').replace('っ', '')
    return r


def _is_seidaku_similar(c1: str, c2: str) -> bool:
    return SEIDAKU_MAP.get(c1) == c2 or SEIDAKU_MAP.get(c2) == c1


def _reading_similarity_strict(r1: str, r2: str) -> tuple[bool, str]:
    if r1 == r2:
        return True, "exact"
    nr1 = _normalize_reading(r1)
    nr2 = _normalize_reading(r2)
    min_len = min(len(nr1), len(nr2))
    max_len = max(len(nr1), len(nr2))
    if nr1 == nr2:
        return True, "long_vowel"
    if max_len - min_len > 1:
        return False, ""
    if len(nr1) == len(nr2):
        diff_positions = [(c1, c2) for i, (c1, c2) in enumerate(zip(nr1, nr2)) if c1 != c2]
        if len(diff_positions) == 0:
            return True, "exact"
        if len(diff_positions) == 1:
            c1, c2 = diff_positions[0]
            if _is_seidaku_similar(c1, c2):
                return True, "seidaku"
        return False, ""
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
    """基于读音相似性 + 人称代词互斥分析生成候选簇"""
    core_list = list(cores.keys())
    n = len(core_list)
    parent = list(range(n))

    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    def get_dominant_pronoun(core: str) -> str | None:
        pronouns = cores[core].get('pronouns', {})
        if not pronouns:
            return None
        return max(pronouns.items(), key=lambda x: x[1])[0]

    for i in range(n):
        for j in range(i + 1, n):
            c1, c2 = core_list[i], core_list[j]
            r1, r2 = cores[c1]['reading'], cores[c2]['reading']
            similar, _sim_type = _reading_similarity_strict(r1, r2)
            if similar:
                count1, count2 = cores[c1]['count'], cores[c2]['count']
                p1, p2 = get_dominant_pronoun(c1), get_dominant_pronoun(c2)
                if p1 and p2 and p1 != p2:
                    p1_count = cores[c1]['pronouns'].get(p1, 0)
                    p2_count = cores[c2]['pronouns'].get(p2, 0)
                    if p1_count >= 2 and p2_count >= 2:
                        continue
                if max(count1, count2) >= min(count1, count2) * 10:
                    low_core = c1 if count1 < count2 else c2
                    high_core = c2 if count1 < count2 else c1
                    low_ctx = ' '.join(cores[low_core]['contexts'])
                    high_surfaces = set(cores[high_core]['surfaces'].keys())
                    if not any(s in low_ctx for s in high_surfaces):
                        continue
                union(i, j)

    clusters = {}
    for i in range(n):
        root = find(i)
        clusters.setdefault(root, []).append(core_list[i])
    return [c for c in clusters.values() if len(c) > 1]


# ==================== LLM 角色识别与术语分析 ====================

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
    "- 如果两个词分别与**不同的人称代词**强共现，则它们**很可能是不同角色**，不应作为 alias 合并。\n"
    "- 如果两个词与**相同的人称代词**共现，或都没有明显的人称代词倾向，则更可能是同一角色的ASR变体。\n\n"
    "【文件名优先级强化】\n"
    "文件名中的角色名候选具有最高置信度。文件名包含角色名时，读音相似的变体都应优先映射到文件名中的角色名。\n"
    "- 特别警惕：ASR常将角色名的首音脱落或中间音误识别，这些应被收录为 alias\n"
)


def validate_terms(terms: dict[str, str]) -> dict[str, str]:
    """校验术语表，过滤明显不合理的映射"""
    validated = {}
    for ja, zh in terms.items():
        if ja in _SUSPICIOUS_TERM_PATTERNS:
            bad_translations = _SUSPICIOUS_TERM_PATTERNS[ja]
            if zh in bad_translations:
                print(f"    [校验] 拦截可疑映射: {ja} -> {zh} (已知误译模式)")
                continue
        if ja in _GENERIC_BIOLOGY_TERMS and len(zh) <= 2:
            print(f"    [校验] 跳过通用生物词汇: {ja} -> {zh}")
            continue
        if _re.match(r'^[゠-ヿ]+$', ja) and len(zh) == 1:
            print(f"    [校验] 跳过可疑映射: {ja} -> {zh} (片假名单字译名)")
            continue
        validated[ja] = zh
    return validated


def analyze_characters_with_llm(
    engine,
    cores: dict[str, dict],
    clusters: list[list[str]],
    filename_names: set[str] = None,
    verbose: bool = False,
) -> tuple[dict[str, str], list[dict]]:
    """调用 LLM 分析角色和术语

    返回: (terms: {core: zh}, alias_list: [{alias, target, confidence}])
    """
    if not cores:
        return {}, []

    import json as _json
    filename_names = filename_names or set()

    lines: list[str] = []

    # 0. 文件名候选角色名
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

    # 1. 排序: 文件名来源 > 固有名詞 > 片假名 > 带后缀 > 频率
    def core_priority(item):
        core, info = item
        is_proper = info['pos'] == '固有名詞'
        is_katakana = bool(_re.search(r'[゠-ヿ]', core))
        has_suffix = any(len(s) > len(core) for s in info['surfaces'])
        from_filename = core in filename_names
        return -(int(from_filename) * 5000 + int(is_proper) * 1000 + int(is_katakana) * 100 + int(has_suffix) * 10 + info['count'])

    sorted_cores = sorted(cores.items(), key=core_priority)[:20]
    lines.append("【高频词汇】")
    for core, info in sorted_cores:
        surfaces_str = ', '.join([f"{s}({c})" for s, c in sorted(info['surfaces'].items(), key=lambda x: -x[1])[:3]])
        pronouns_str = ', '.join([f"{p}({c})" for p, c in sorted(info.get('pronouns', {}).items(), key=lambda x: -x[1])[:2]])
        from_file_mark = " [文件名候选]" if core in filename_names else ""
        lines.append(f"  {core} | reading:{info['reading']} | count:{info['count']} | surfaces:{surfaces_str}{from_file_mark}")
        if pronouns_str:
            lines.append(f"    共现人称代词: {pronouns_str}")
        for ctx in info['contexts'][:1]:
            lines.append(f"    上下文: {ctx[:40]}")

    # 2. 相似簇
    if clusters:
        lines.append("\n【读音相似簇（待确认是否为ASR误识别）】")
        for i, cluster in enumerate(clusters, 1):
            lines.append(f"  簇{i}:")
            for core in cluster:
                info = cores[core]
                pronouns_str = ', '.join([f"{p}({c})" for p, c in sorted(info.get('pronouns', {}).items(), key=lambda x: -x[1])[:2]])
                from_file_mark = " [文件名候选]" if core in filename_names else ""
                lines.append(f"    {core} (reading:{info['reading']}, count:{info['count']}){from_file_mark}")
                if pronouns_str:
                    lines.append(f"      共现人称代词: {pronouns_str}")
                for ctx in info['contexts'][:1]:
                    lines.append(f"      上下文: {ctx[:60]}")

    content = CHARACTER_ANALYSIS_PROMPT + "\n\n" + "\n".join(lines)
    if verbose:
        print(f"    [DEBUG] 发送给 LLM 的文本长度: {len(content)} 字符", flush=True)

    try:
        system_prompt = "你是精通日语角色语言学和ASR纠错的专家。请严格按照JSON格式输出分析结果，不要输出任何其他内容。"
        raw_output, token_stats = engine.call_api(
            system_prompt, content,
            override_gen_params={'temperature': 0.1},
        )
        if verbose:
            print(f"    [DEBUG] LLM 原始输出 (前500字):", flush=True)
            print(f"    {raw_output[:500]}", flush=True)
            if len(raw_output) > 500:
                print(f"    ... (共 {len(raw_output)} 字)", flush=True)

        json_match = _re.search(r'\{.*\}', raw_output, _re.DOTALL)
        if json_match:
            try:
                result = _json.loads(json_match.group())
                terms = result.get('terms', {})
                if verbose:
                    print(f"    [DEBUG] 解析到术语: {len(terms)} 个", flush=True)
                    for ja, zh in list(terms.items())[:5]:
                        print(f"      - {ja} -> {zh}", flush=True)

                alias_list = result.get('alias', [])
                if verbose:
                    print(f"    [DEBUG] 解析到 alias: {len(alias_list)} 个", flush=True)
                    for a in alias_list[:5]:
                        print(f"      - {a.get('alias')} -> {a.get('target')} (conf: {a.get('confidence')})", flush=True)

                filtered = [a for a in alias_list if a.get('confidence', 0) >= 0.8]
                if verbose:
                    print(f"    [DEBUG] 过滤后(>=0.8): {len(filtered)} 个", flush=True)

                terms = validate_terms(terms)
                if verbose:
                    print(f"    [DEBUG] 术语校验后: {len(terms)} 个", flush=True)

                print(f"  LLM 分析完成: {len(filtered)} 个高置信度 alias, {len(terms)} 个术语", flush=True)
                return terms, filtered
            except _json.JSONDecodeError as e:
                print(f"  ⚠ LLM 返回格式错误: {e}", flush=True)
                if verbose:
                    print(f"    [DEBUG] JSON片段: {json_match.group()[:200]}", flush=True)
                return {}, []
        else:
            if verbose:
                print(f"    [DEBUG] 未找到 JSON 格式内容", flush=True)
            return {}, []
    except Exception as e:
        print(f"  ⚠ LLM 分析失败: {e}", flush=True)
        return {}, []


# ==================== 动态术语提取（翻译后） ====================

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


def extract_terms_from_translation(
    engine,
    original_lyrics: list[str],
    translated_lyrics: list[str],
    existing_terms: dict[str, str],
) -> tuple[dict[str, str], list[dict]]:
    """从翻译结果中动态提取新术语

    返回: (新术语字典, 新alias列表)
    """
    import json as _json

    if not original_lyrics or not translated_lyrics:
        return {}, []

    pairs = []
    for orig, trans in zip(original_lyrics[:50], translated_lyrics[:50]):
        if orig.strip() and trans.strip():
            pairs.append(f"原文: {orig}\n译文: {trans}")

    if not pairs:
        return {}, []

    content = (
        DYNAMIC_TERMS_PROMPT + "\n\n已有术语表:\n" +
        _json.dumps(existing_terms, ensure_ascii=False) +
        "\n\n对照文本:\n" + "\n\n".join(pairs)
    )

    try:
        system_prompt = "你是精通日语字幕翻译术语的专家。请严格按照JSON格式输出。"
        raw_output, _token_stats = engine.call_api(
            system_prompt, content,
            override_gen_params={'temperature': 0.1},
        )

        json_match = _re.search(r'\{.*\}', raw_output, _re.DOTALL)
        if json_match:
            try:
                result = _json.loads(json_match.group())
                new_terms = result.get('new_terms', {})
                new_alias = result.get('new_alias', [])
                new_alias = [a for a in new_alias if a.get('confidence', 0) >= 0.8]
                if new_terms or new_alias:
                    print(f"    [动态更新] 发现 {len(new_terms)} 个新术语, {len(new_alias)} 个新 alias", flush=True)
                return new_terms, new_alias
            except _json.JSONDecodeError:
                return {}, []
        return {}, []
    except Exception as e:
        print(f"    ⚠ 动态术语提取失败: {e}", flush=True)
        return {}, []


def collect_pending_terms(
    pending: list[tuple],
    engine,
) -> None:
    """批量处理翻译对，提取并更新术语

    参数:
        pending: [(work_dir, original_lyrics, translated_lyrics, existing_terms), ...]
    """
    if not pending:
        return

    all_originals = []
    all_translated = []
    work_dir = None

    for item in pending:
        _wd, orig, trans, _terms = item
        all_originals.extend(orig)
        all_translated.extend(trans)
        if work_dir is None:
            work_dir = _wd

    if not all_originals or not all_translated:
        return

    existing_terms = load_terms_from_file(get_terms_path(work_dir)) if work_dir else {}
    sampled_orig = all_originals[:100]
    sampled_trans = all_translated[:100]

    new_terms, new_alias = extract_terms_from_translation(
        engine, sampled_orig, sampled_trans, existing_terms)

    if new_terms or new_alias:
        update_terms_and_alias(work_dir, new_terms, new_alias)