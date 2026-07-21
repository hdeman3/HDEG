# -*- coding: utf-8 -*-
"""
翻译管道编排器

连接 core、engines、io_adapter 完成端到端字幕翻译流程：
  1. 扫描 .lrc / .srt / .vtt 字幕文件
  2. 恢复留档（.ja.* → .* 如果缺失）
  3. 自动分析作品术语/世界观（LLM驱动）
  4. 解析字幕时间轴
  5. 翻译
  6. 覆盖写入字幕文件（翻译后的中文）

替代 translate.py 中分散的流程控制逻辑。
"""

from __future__ import annotations
import shutil
import sys
import time
from pathlib import Path
from typing import Callable

# core 纯逻辑层
from core.text_analysis import is_fugashi_available

# engines 引擎层
from engines.translate_engine import (
    OpenAICompatEngine,
    TranslateEngine,
    create_translate_engine,
)

# io_adapter IO适配层
from io_adapter.config_loader import (
    load_config,
    get_api_config,
    load_terms_from_config,
)
from io_adapter.lrc_handler import (
    SUBTITLE_EXTS,
    parse_lrc_file,
    parse_subtitle_file,
    write_subtitle_file,
    extract_subtitle_texts,
    detect_subtitle_language,
    generate_single_language_lrc,
    LrcLine,
)


# ==================== 工具函数 ====================

def _log(msg: str = "", *, flush: bool = True):
    """输出日志并立即刷新 stdout，确保实时可见"""
    print(msg, flush=flush)
    # 也刷新 stderr（某些终端可能缓冲）
    sys.stderr.flush()


def _sep(title: str = ""):
    """打印分隔线"""
    if title:
        _log(f"{'='*60}")
        _log(f"  {title}")
        _log(f"{'='*60}")
    else:
        _log("=" * 60)


# ==================== 管道上下文 ====================

class PipelineContext:
    """管道运行上下文，承载整个过程的状态"""

    def __init__(self, config_path: Path = None):
        self.config = load_config(config_path)
        self.start_time = time.time()
        self.app_cfg = self.config.get('app', {})
        self.api_cfg = self.config.get('api', {})
        self.pricing = self.config.get('pricing', {})
        self.stats = {
            'archived': 0,
            'translated': 0,
            'skipped': 0,
            'kept': 0,
            'total_lines': 0,
            'success_lines': 0,
            'api_calls': 0,
            'total_cost': 0.0,
            'total_hit_tokens': 0,
            'total_miss_tokens': 0,
            'total_completion_tokens': 0,
        }
        self._translate_engine: TranslateEngine | None = None
        # 按目录的详细报告
        self.dir_reports: list[dict] = []

    @property
    def translate_engine(self) -> TranslateEngine:
        if self._translate_engine is None:
            api_config = get_api_config(self.config)
            prompt_file = self.config.get('prompts', {}).get('system_prompt_file', '')
            # verbose 由 config["app"]["debug"] 控制，默认关闭调试输出
            debug_mode = self.config.get('app', {}).get('debug', False)
            self._translate_engine = create_translate_engine(
                api_config,
                system_prompt_file=prompt_file if prompt_file else None,
                verbose=debug_mode,
            )
        return self._translate_engine

    def update_stats(self, **kwargs):
        self.stats.update(kwargs)

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time


# ==================== 字幕文件扫描 ====================

def scan_subtitle_files(work_dir: Path) -> tuple[list[Path], list[Path], int, dict]:
    """
    扫描工作目录中的字幕文件（LRC/SRT/VTT）

    参数:
        work_dir: 工作目录（递归扫描）

    返回:
        (待翻译的文件列表, 日文留档列表, 恢复数量, 文件分组信息)
    """
    _log(f"\n[扫描] 递归扫描目录: {work_dir.absolute()}")

    # 收集所有字幕文件（排除 .ja.* 留档）
    all_files: list[Path] = []
    for ext in SUBTITLE_EXTS:
        for f in work_dir.rglob(f'*{ext}'):
            if f.is_file() and not f.stem.endswith('.ja'):
                # 排除 bug 收集目录
                if 'Hde_G_bug_lrc_collection' not in f.parts:
                    all_files.append(f)

    # 收集所有 .ja.* 留档
    ja_files: list[Path] = []
    for ext in SUBTITLE_EXTS:
        for f in work_dir.rglob(f'*.ja{ext}'):
            if f.is_file():
                if 'Hde_G_bug_lrc_collection' not in f.parts:
                    ja_files.append(f)

    _log(f"  → 找到字幕文件: {len(all_files)} 个")
    _log(f"  → 找到日文留档: {len(ja_files)} 个")

    # 按扩展名细分
    for ext in SUBTITLE_EXTS:
        count = sum(1 for f in all_files if f.suffix.lower() == ext)
        if count > 0:
            _log(f"    {ext}: {count} 个")

    # 从留档恢复缺失的文件
    restored = 0
    for ja_f in ja_files:
        # .ja.lrc → .lrc, .ja.srt → .srt, .ja.vtt → .vtt
        ja_stem = ja_f.stem
        if ja_stem.endswith('.ja'):
            base_name = ja_stem[:-3]
            ext = ja_f.suffix  # 已经是完整扩展名 .lrc/.srt/.vtt
            target_path = ja_f.parent / f"{base_name}{ext}"
            if not target_path.exists():
                shutil.copy2(ja_f, target_path)
                _log(f"  [恢复] {base_name}{ext} (从 {ja_f.name})")
                all_files.append(target_path)
                restored += 1

    if restored > 0:
        _log(f"  → 从留档恢复: {restored} 个文件")

    # 按修改时间排序
    all_files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)

    # 构建文件分组信息（用于后续按目录处理）
    file_groups: dict = {}
    for f in all_files:
        parent = f.parent
        file_groups.setdefault(parent, []).append(f)

    return all_files, ja_files, restored, file_groups


# 兼容旧接口
def scan_lrc_files(work_dir: Path) -> tuple[list[Path], list[Path], int]:
    """兼容旧接口"""
    all_files, ja_files, restored, _groups = scan_subtitle_files(work_dir)
    return all_files, ja_files, restored


# ==================== 台本加载 ====================

def _load_scriptbook(work_dir: Path, ctx: PipelineContext) -> list[str] | None:
    """加载台本参考（原文-译文对照）

    支持 scriptbook_mode 配置:
    - "keyword": 仅发送关键台词
    - "full": 发送全部台本内容（默认）

    返回:
        台本行列表，或 None 表示无台本
    """
    from core.scriptbook_parser import (
        find_scriptbooks_in_dir,
        load_scriptbook_content,
        build_raw_scriptbook_map,
        is_key_dialogue_line,
    )

    # 优先使用台本解析器查找
    scriptbook_files = find_scriptbooks_in_dir(work_dir)
    if scriptbook_files:
        _log(f"\n[台本] 发现台本目录/文件: {len(scriptbook_files)} 个")
        for f in scriptbook_files[:5]:
            _log(f"  → {f.absolute()}")
        if len(scriptbook_files) > 5:
            _log(f"  → ... 还有 {len(scriptbook_files) - 5} 个")

        raw_map = build_raw_scriptbook_map(scriptbook_files)
        all_lines: list[str] = []
        for track_num in sorted(raw_map.keys()):
            all_lines.extend(raw_map[track_num])

        # 清洗台本（去掉场景描述、SE音效等非对话内容）
        from utils.text_filter import clean_script_for_translation
        scriptbook_text = "\n".join(all_lines)
        cleaned_text = clean_script_for_translation(scriptbook_text)
        cleaned_lines = [l for l in cleaned_text.split('\n') if l.strip()]
        _log(f"  → 合并台本: {len(all_lines)} 行 → 清洗后: {len(cleaned_lines)} 行")

        # 导出台本（与源台本同目录）
        export_scriptbook = ctx.config.get('app', {}).get('export_scriptbook_content', False)
        if export_scriptbook:
            # 导出到第一个台本文件所在目录
            export_dir = scriptbook_files[0].parent
            export_path = export_dir / '_scriptbook_export.txt'
            try:
                export_path.write_text(cleaned_text, encoding='utf-8')
                _log(f"  → 导出清洗后台本: {export_path.absolute()}")
            except Exception as e:
                _log(f"  [警告] 导出台本失败: {e}")

        # 应用 scriptbook_mode 过滤
        scriptbook_mode = ctx.config.get('app', {}).get('scriptbook_mode', 'full')
        if scriptbook_mode == 'keyword':
            cleaned_lines = _apply_keyword_filter(cleaned_lines)
        return cleaned_lines

    # 回退：搜索根目录下带 | 分隔符的台本 txt 文件
    candidates = list(work_dir.rglob('*.txt'))
    scriptbook_path = None
    for cand in candidates:
        try:
            # 尝试多种编码读取文件头部
            content = None
            for enc in ('utf-8', 'shift-jis', 'cp932'):
                try:
                    content = cand.read_text(encoding=enc)[:500]
                    break
                except Exception:
                    continue
            if content and '|' in content and any(
                k in content for k in ('日本語', '中文', 'セリフ', '台词', '原文', '译文')
            ):
                scriptbook_path = cand
                break
        except Exception:
            continue

    if not scriptbook_path:
        _log(f"\n[台本] 未发现台本文件")
        return None

    _log(f"\n[台本] 发现台本文件: {scriptbook_path.absolute()}")
    try:
        content = load_scriptbook_content(scriptbook_path)
        if content:
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            _log(f"  → 加载台本: {len(lines)} 行参考原文")
            return lines
    except Exception as e:
        _log(f"  [警告] 台本加载失败: {e}")

    # 兜底：直接按行读取（load_scriptbook_content 已支持多编码，这里作为最后保险）
    try:
        text = load_scriptbook_content(scriptbook_path)
        lines = [
            line.strip()
            for line in (text or '').split('\n')
            if line.strip()
        ]
        _log(f"  → 兜底加载台本: {len(lines)} 行")
        return lines
    except Exception as e:
        _log(f"  [警告] 台本兜底加载也失败: {e}")
        return None


# ==================== 世界观加载 ====================

def _load_worldview(work_dir: Path, ctx: PipelineContext) -> dict | None:
    """加载世界观/角色/场景设定

    返回:
        世界观字典 {worldview, characters, scene, _source} 或 None
    """
    from engines.worldview_engine import load_worldview_from_dir

    _log(f"\n[世界观] 搜索目录: {work_dir.absolute()}")

    try:
        worldview, source_files = load_worldview_from_dir(work_dir)
        if worldview and source_files:
            wv_text = worldview.get('worldview', '')
            chars = worldview.get('characters', {})
            scene = worldview.get('scene', '')

            worldview['_source'] = source_files
            _log(f"  → 来源: {', '.join(str(p) for p in source_files)}")
            _log(f"  → 世界观: {'有' if wv_text else '无'} ({len(wv_text)} 字符)")
            _log(f"  → 角色: {len(chars)} 个")
            _log(f"  → 场景: {'有' if scene else '无'}")

            return worldview
        else:
            _log("  → 未发现世界观文件")
            return None
    except Exception as e:
        _log(f"  [警告] 世界观加载失败: {e}")
        return None


# ==================== 台本关键字过滤 ====================

def _apply_keyword_filter(lines: list[str]) -> list[str]:
    """对台本行应用关键字过滤（scriptbook_mode=keyword 时使用）"""
    from core.scriptbook_parser import is_key_dialogue_line
    keyword_lines = []
    for line in lines:
        if line and line.strip() and is_key_dialogue_line(line):
            keyword_lines.append(line)
    if keyword_lines:
        _log(f"  → 关键字模式: {len(keyword_lines)}/{len(lines)} 行")
        return keyword_lines
    # 回退：如果没有关键台词，保留所有有效行
    fallback = [line for line in lines if line and line.strip() and len(line.strip()) >= 5]
    _log(f"  → 关键字模式（回退）: {len(fallback)}/{len(lines)} 行")
    return fallback


# ==================== 自动术语/世界观分析（LLM驱动） ====================

def _analyze_work_terms(work_dir: Path, ctx: PipelineContext) -> tuple[dict, list, dict]:
    """分析作品目录，自动生成术语表、alias表和世界观

    移植自 translate.py:analyze_work_terms()

    返回: (terms: dict, alias_list: list, worldview: dict)
    """
    from engines.term_manager import (
        get_terms_path, get_alias_path,
        load_terms_from_file, load_alias,
        save_terms_to_file, save_alias, update_terms_and_alias,
        extract_names_from_filenames,
        sample_all_lrc_files,
        find_similar_reading_clusters,
        analyze_characters_with_llm,
        ROLE_TRANSLATIONS,
    )
    from engines.worldview_engine import (
        get_worldview_path, load_worldview, save_worldview,
        analyze_worldview_with_llm,
        is_freetalk_context,
    )

    terms_path = get_terms_path(work_dir)
    alias_path = get_alias_path(work_dir)
    worldview_path = get_worldview_path(work_dir)

    # 已存在则直接加载
    if terms_path.exists() and alias_path.exists() and worldview_path.exists():
        terms = load_terms_from_file(terms_path)
        alias_list = load_alias(work_dir)
        worldview = load_worldview(work_dir)
        _log(f"  加载已有术语表: {len(terms)} 个 ({terms_path.absolute()})")
        _log(f"  加载已有 alias 表: {len(alias_list)} 个 ({alias_path.absolute()})")
        _log(f"  加载已有世界观: {worldview_path.absolute()}")
        _log(f"    内容预览: {str(worldview.get('worldview', ''))[:80]}...")
        return terms, alias_list, worldview

    # 检测是否为 freetalk 或热门CV
    is_ft, cv_name = is_freetalk_context(work_dir)
    if is_ft:
        if cv_name:
            _log(f"  [检测到 FreeTalk/CV] 目录包含热门CV「{cv_name}」，跳过世界观构建和分词")
        else:
            _log(f"  [检测到 FreeTalk] 目录包含 freetalk 关键词，跳过世界观构建和分词")
        return {}, [], {}

    if not is_fugashi_available():
        _log("  ⚠ fugashi 未安装，跳过术语分析")
        return {}, [], {}

    _log("  正在分析作品...")
    _log()

    # 1. 从文件名提取角色名候选
    _log("    [文件名] 正在提取角色名候选...")
    app_cfg = ctx.config.get('app', {})
    audio_suffixes = app_cfg.get('audio_suffixes', None)
    subtitle_exts = app_cfg.get('subtitle_exts', None)
    filename_names = extract_names_from_filenames(work_dir, audio_suffixes, subtitle_exts)
    if filename_names:
        _log(f"    [文件名] 发现角色名候选: {', '.join(list(filename_names)[:5])}")

    # 2. 抽样 + 提取 cores
    _log("    [1/5] 抽样所有字幕文件...")
    samples, cores = sample_all_lrc_files(work_dir, lines_per_file=40)
    _log(f"    [1/5] 抽样完成: {len(samples)} 个文件样本, {len(cores)} 个 core")

    # 3. 世界观分析
    _log("    [2/5] 调用 LLM 分析世界观...")
    worldview = analyze_worldview_with_llm(ctx.translate_engine, samples)
    if worldview:
        save_worldview(work_dir, worldview)
        _log(f"    [2/5] 世界观已保存")

    # 4. 读音相似簇
    _log("    [3/5] 正在比对读音相似性...")
    clusters = find_similar_reading_clusters(cores)
    _log(f"    [3/5] 读音相似候选簇: {len(clusters)} 个")
    for i, c in enumerate(clusters[:5], 1):
        _log(f"      簇{i}: {' / '.join(c)}")

    # 5. LLM 分析术语和 alias
    _log("    [4/5] 调用 LLM 分析角色和术语...")
    terms, alias_list = analyze_characters_with_llm(
        ctx.translate_engine, cores, clusters, filename_names)

    # 添加 ROLE_TRANSLATIONS 预设（LLM 结果优先，预设兜底）
    for jp, zh in ROLE_TRANSLATIONS.items():
        if jp not in terms:
            terms[jp] = zh

    # 6. 保存（三个文件都创建，即使为空，避免下次重复分析）
    _log("    [5/5] 保存分析结果...")
    save_terms_to_file(work_dir, terms)
    _log(f"    已保存术语表({len(terms)}个)")
    save_alias(work_dir, alias_list)
    _log(f"    已保存 alias 表({len(alias_list)}个)")
    if not worldview:
        worldview = {}
    save_worldview(work_dir, worldview)
    _log(f"    已保存世界观")

    _log("  作品分析完成！")
    return terms, alias_list, worldview


# ==================== 目录报告 ====================

def _record_dir_report(ctx: PipelineContext, dir_path: Path,
                       start_translated: int, start_lines: int, start_time: float) -> None:
    """记录单个目录的翻译报告"""
    dir_translated = ctx.stats['translated'] - start_translated
    dir_lines = ctx.stats['total_lines'] - start_lines
    if dir_translated > 0 or dir_lines > 0:
        ctx.dir_reports.append({
            'dir': str(dir_path),
            'files': dir_translated,
            'lines': dir_lines,
            'elapsed': time.time() - start_time,
        })


def print_summary_report(ctx: PipelineContext) -> None:
    """打印翻译总览报告（按目录分组统计）"""
    if not ctx.dir_reports:
        return
    _log()
    _sep("翻译总览报告")
    _log(f"  处理目录数: {len(ctx.dir_reports)}")
    _log(f"  总翻译文件: {ctx.stats['translated']}")
    _log(f"  总行数: {ctx.stats['total_lines']}")
    _log(f"  总耗时: {ctx.elapsed:.1f}s ({ctx.elapsed/60:.1f}分钟)")
    _log(f"  API 调用: {ctx.stats['api_calls']} 次")
    total_cost = ctx.stats['total_cost']
    _log(f"  总费用: ¥{total_cost:.4f}")
    _log()
    _log("  按目录明细:")
    for r in ctx.dir_reports:
        name = Path(r['dir']).name or r['dir']
        _log(f"    {name}: {r['files']}文件 {r['lines']}行 {r['elapsed']:.1f}s")


# ==================== 分词 + 文本分析 ====================

def _analyze_texts(texts: list[str], ctx: PipelineContext) -> dict:
    """对提取的文本进行分词和文本分析

    返回:
        分析结果字典 {total, japanese_lines, unique_words, ...}
    """
    from core.text_analysis import analyze_japanese_texts

    _log(f"\n[分词] 分析 {len(texts)} 行文本...")

    try:
        analysis = analyze_japanese_texts(texts)

        _log(f"  → 日文行数: {analysis.get('japanese_lines', 0)}/{len(texts)}")
        _log(f"  → 总字符数: {analysis.get('total_chars', 0)}")
        _log(f"  → 唯一词汇: {analysis.get('unique_words', 0)} 个")
        if analysis.get('top_words'):
            top = analysis['top_words'][:10]
            _log(f"  → 高频词 (top 10): {', '.join(top)}")

        return analysis
    except Exception as e:
        _log(f"  [警告] 分词分析失败: {e}")
        return {}


# ==================== 单文件翻译 ====================

def translate_one_lrc(
    lrc_path: Path,
    ctx: PipelineContext,
    *,
    terms: dict = None,
    alias_list: list = None,
    worldview: dict = None,
    scriptbook_lines: list[str] = None,
    progress_callback: Callable = None,
) -> bool:
    """
    翻译单个 LRC 文件

    流程：
      1. 解析 LRC 时间轴
      2. 提取文本行
      3. 分块调用 LLM 翻译
      4. 覆盖写入原 .lrc（翻译后的中文）

    返回:
        True 表示翻译成功
    """
    abs_path = lrc_path.absolute()
    ext = lrc_path.suffix.lower()
    _log(f"\n{'─'*60}")
    _log(f"[翻译文件] {abs_path}")
    _log(f"  文件名: {lrc_path.name}")
    _log(f"  所在目录: {lrc_path.parent.absolute()}")

    # 使用通用字幕解析器（支持 LRC/SRT/VTT）
    sub_file = parse_subtitle_file(lrc_path)
    if sub_file is None:
        _log(f"  [跳过] 无文本内容")
        return False

    texts = sub_file.original_lyrics
    _log(f"  [解析] {len(texts)} 行文本")

    # 检测是否已是中文（启发式：若大部分字符在 CJK 范围则跳过）
    lang = detect_subtitle_language(lrc_path)
    if lang == "chinese":
        _log(f"  [跳过] 已是中文，保留原文件")
        ctx.stats["kept"] += 1
        return False

    _log(f"  [待翻译] {len(texts)} 行文本")

    # 术语（由调用方传入）
    terms = terms or {}
    if terms:
        _log(f"  → 术语条目: {len(terms)} 条")
    else:
        _log(f"  → 无术语表")

    # 世界观信息
    if worldview:
        chars = worldview.get('characters', {})
        char_count = len(chars) if isinstance(chars, (dict, list)) else 0
        source = worldview.get('_source', [])
        src_str = '\n  → 来源: ' + ', '.join(str(p) for p in source) if source else ''
        _log(f"\n[世界观] 已加载（角色: {char_count} 个, 场景: {'有' if worldview.get('scene') else '无'}）{src_str}")
    else:
        _log(f"\n[世界观] 未加载")

    # 台本参考
    if scriptbook_lines:
        _log(f"\n[台本] 参考行数: {len(scriptbook_lines)} 行")
    else:
        _log(f"\n[台本] 无台本参考")

    # 编号（整文件一次性翻译，不分块）
    numbered = [f"{i+1:04d}: {t}" for i, t in enumerate(texts)]
    _log(f"\n[翻译] 整文件翻译: {len(texts)} 行, {sum(len(t) for t in texts)} 字符")

    translated_texts: list[str] = []

    _log(f"  → 时间: {time.strftime('%H:%M:%S')}, 发送请求...")
    call_start = time.time()
    result = ctx.translate_engine.translate_batch(
        numbered, terms=terms,
        alias_list=alias_list,
        worldview=worldview,
        scriptbook_lines=scriptbook_lines,
        skip_hallucination_check=True,
    )
    call_elapsed = time.time() - call_start

    translated_batch = result.get('translated_lines', [])
    for t_line in translated_batch:
        # 去掉编号前缀 "0001: "
        if ': ' in t_line:
            t_line = t_line.split(': ', 1)[1]
        # 还原空行标记
        if t_line == '[EMPTY_LINE]':
            t_line = ''
        translated_texts.append(t_line)

    _log(f"  ← 响应: {len(translated_batch)} 行, 耗时 {call_elapsed:.1f}s")
    hit = result.get('hit_tokens', 0)
    miss = result.get('miss_tokens', 0)
    comp = result.get('completion_tokens', 0)
    total_tok = hit + miss + comp
    hit_rate = (hit / (hit + miss) * 100) if (hit + miss) > 0 else 0
    cost = result.get('cost', 0)
    _log(f"  📊 Token: 总计{total_tok:,}  🟢命中{hit:,}({hit_rate:.0f}%)  🔵未命中{miss:,}  🟣输出{comp:,}")
    # 费用明细使用实际定价
    ph = ctx.pricing.get('hit_per_1m', 0)
    pm = ctx.pricing.get('miss_per_1m', 0)
    pc = ctx.pricing.get('completion_per_1m', 0)
    ch = hit / 1_000_000 * ph
    cm = miss / 1_000_000 * pm
    cc = comp / 1_000_000 * pc
    _log(f"  💰 费用: ¥{cost:.4f}  (🟢命中¥{ch:.4f} + 🔵未命中¥{cm:.4f} + 🟣输出¥{cc:.4f})")

    ctx.stats['success_lines'] += len(translated_batch)
    ctx.stats['total_lines'] += len(translated_batch)
    ctx.stats['api_calls'] += 1
    ctx.stats['total_cost'] += result.get('cost', 0)
    ctx.stats['total_hit_tokens'] += result.get('hit_tokens', 0)
    ctx.stats['total_miss_tokens'] += result.get('miss_tokens', 0)
    ctx.stats['total_completion_tokens'] += result.get('completion_tokens', 0)

    # 补齐不足的行（翻译失败的回退）
    shortage = 0
    while len(translated_texts) < len(texts):
        idx = len(translated_texts)
        translated_texts.append(texts[idx])
        shortage += 1
    if shortage > 0:
        _log(f"  [补齐] 翻译缺失 {shortage} 行，已用原文回退")

    # 创建 .ja.* 留档（翻译前保留日文原版）
    export_ja = ctx.config.get('app', {}).get('export_ja_lrc', True)
    if export_ja:
        ja_stem = lrc_path.stem
        ja_path = lrc_path.parent / f"{ja_stem}.ja{ext}"
        if not ja_path.exists():
            try:
                shutil.copy2(lrc_path, ja_path)
                _log(f"  [留档] {lrc_path.name} -> {ja_path.name}")
                ctx.stats['archived'] = ctx.stats.get('archived', 0) + 1
            except Exception as e:
                _log(f"  [留档失败] {lrc_path.name}: {e}")

    # 写回翻译结果（通用字幕格式）
    write_subtitle_file(sub_file, translated_texts, lrc_path)
    _log(f"\n[写入] -> {abs_path}")
    _log(f"  -> 翻译完成: {len(translated_texts)} 行中文")
    ctx.stats['translated'] += 1
    return True


# ==================== 语音转录 ====================

def _run_transcription_if_needed(work_dir: Path, ctx: PipelineContext) -> None:
    """检测音频文件，自动调用 infer.exe 转录生成 .ja.lrc

    参照 翻译_debug.bat 的流程：
    1. 恢复 .ja.lrc -> .lrc（如果 .lrc 缺失）
    2. 检测未转录的音频
    3. 调用 infer.exe 转录
    4. 将生成的 .lrc 留档为 .ja.lrc
    """
    import os
    import subprocess

    tc = ctx.config.get('transcription', {})
    infer_exe = tc.get('infer_exe', '')
    model_dir = tc.get('model_dir', '')
    device = tc.get('device', 'cuda')
    compute_type = tc.get('compute_type', 'int8_float16')

    if not infer_exe:
        _log("[转录] 未配置 infer.exe，跳过转录步骤")
        return

    # 解析相对路径（从 HDEG 根目录，不是作品目录）
    import sys
    if getattr(sys, 'frozen', False):
        hdeg_root = Path(sys.executable).parent
    else:
        hdeg_root = Path(__file__).parent.parent  # pipeline/ -> HDEG/
    exe_path = Path(infer_exe)
    if not exe_path.is_absolute():
        exe_path = hdeg_root / exe_path
    if not exe_path.exists():
        _log(f"[转录] infer.exe 不存在: {exe_path}，跳过转录步骤")
        return

    # 检测 RTX 50 系列 GPU，自动切换 compute_type 为 float16
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
            capture_output=True, text=True, timeout=10
        )
        import re
        if re.search(r'RTX\s*50\d\d', result.stdout):
            compute_type = 'float16'
            _log(f"[转录] 检测到 RTX 50 系列 GPU，自动切换 compute_type 为 float16")
    except Exception:
        pass

    _sep("第 0 步: 语音转录 (infer.exe)")
    _log(f"[转录] 引擎: {exe_path}")
    _log(f"[转录] 设备: {device}  精度: {compute_type}")

    audio_exts = {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma',
                  '.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv'}

    pending: list[Path] = []
    restored = 0

    # ── 步骤 0a: 恢复 .ja.lrc → .lrc（如果 .lrc 缺失），同时收集待转录音频 ──
    for f in sorted(work_dir.rglob('*')):
        if not f.is_file():
            continue
        suffix = f.suffix.lower()

        # 恢复日文留档
        if suffix == '.ja.lrc' or (suffix == '.lrc' and f.stem.endswith('.ja')):
            base_name = f.stem
            if base_name.endswith('.ja'):
                base_name = base_name[:-3]
            lrc_path = f.parent / f'{base_name}.lrc'
            if not lrc_path.exists():
                try:
                    shutil.copy2(str(f), str(lrc_path))
                    restored += 1
                except Exception:
                    pass
            continue

        # 收集未转录的音频
        if suffix not in audio_exts:
            continue
        ja_path = f.parent / f'{f.stem}.ja.lrc'
        if ja_path.exists():
            continue
        pending.append(f)

    if restored:
        _log(f"[转录] 恢复 .ja.lrc -> .lrc: {restored} 个")

    if not pending:
        _log("[转录] 所有音频已有 .ja.lrc，无需转录")
        return

    _log(f"[转录] 待转录音频: {len(pending)} 个")
    for p in pending[:10]:
        _log(f"  → {p.relative_to(work_dir)}")
    if len(pending) > 10:
        _log(f"  → ... 还有 {len(pending) - 10} 个")

    # ── 步骤 0b: 执行 infer.exe 转录（直接调用，避免 bat 中文路径问题）──
    audio_suffixes = tc.get('audio_suffixes', 'mp3,wav,flac,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv')
    sub_formats = tc.get('sub_formats', 'lrc')

    if not pending:
        _log("[转录] 所有音频已有 .ja.lrc，无需转录")
        return

    _log(f"[转录] 待转录: {len(pending)} 个音频")
    _log(f"[转录] 开始调用 infer.exe，这可能需要较长时间...")
    _log()

    env = os.environ.copy()
    if model_dir:
        env['MODEL_DIR'] = model_dir

    cmd = [
        str(exe_path),
        f'--audio_suffixes={audio_suffixes}',
        f'--sub_formats={sub_formats}',
        f'--device={device}',
        f'--task=transcribe',
        f'--compute_type={compute_type}',
        str(work_dir),
    ]

    _log(f"[转录] {' '.join(cmd)}")
    _log()

    import threading
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(exe_path.parent),
            env=env,
            text=True,
            encoding='utf-8',
            errors='replace',
        )

        # 超时监控：infer.exe 超过 10 分钟无输出则判定卡死
        last_output = [time.time()]
        output_lock = threading.Lock()

        def kill_if_stuck():
            while proc.poll() is None:
                time.sleep(30)
                with output_lock:
                    elapsed = time.time() - last_output[0]
                if elapsed > 600:  # 10 分钟无输出
                    _log(f"[转录] infer.exe 超过 {int(elapsed)}s 无输出，判定卡死，强制终止")
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    return

        watchdog = threading.Thread(target=kill_if_stuck, daemon=True)
        watchdog.start()

        for line in proc.stdout:
            line = line.rstrip('\n\r')
            if line.strip():
                _log(f"[infer] {line}")
                with output_lock:
                    last_output[0] = time.time()

        proc.wait()

        if proc.returncode != 0:
            _log(f"[转录] infer.exe 退出码: {proc.returncode}")
            if proc.returncode == -9:
                _log(f"[转录] infer.exe 因卡死被强制终止，跳过转录")
            return
    except FileNotFoundError:
        _log(f"[转录] 找不到 infer.exe: {exe_path}")
        return
    except Exception as e:
        _log(f"[转录] 异常: {e}")
        return

    # ── 步骤 0c: 生成的 .lrc 留档为 .ja.lrc ──
    _log()
    _log("[转录] 转录完成，生成 .ja.lrc 留档...")
    archived = 0
    for f in pending:
        lrc_path = f.parent / f'{f.stem}.lrc'
        ja_path = f.parent / f'{f.stem}.ja.lrc'
        if lrc_path.exists() and not ja_path.exists():
            try:
                shutil.copy2(str(lrc_path), str(ja_path))
                archived += 1
            except Exception:
                pass

    _log(f"[转录] 留档: {archived} 个，恢复: {restored} 个")
    ctx.stats['archived'] = ctx.stats.get('archived', 0) + archived + restored


# ==================== 余额查询 ====================

def _fetch_balance(ctx: PipelineContext) -> None:
    """翻译完成后查询 DeepSeek 账户余额"""
    import json
    import urllib.request

    api_key = ctx.api_cfg.get('key', '')
    base_url = ctx.api_cfg.get('base_url', 'https://api.deepseek.com')

    if not api_key:
        _log("[余额] 未配置 API Key，跳过余额查询")
        return

    # 去掉 base_url 的协议前缀和尾部斜杠
    host = base_url.replace('https://', '').replace('http://', '').rstrip('/')
    url = f'https://{host}/user/balance'

    try:
        req = urllib.request.Request(url)
        req.add_header('Accept', 'application/json')
        req.add_header('Authorization', f'Bearer {api_key}')
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode('utf-8'))

        if data.get('is_available') and data.get('balance_infos'):
            _log("💰 账户余额:")
            for info in data['balance_infos']:
                currency = info.get('currency', 'CNY')
                total = info.get('total_balance', '0')
                topped_up = info.get('topped_up_balance', '0')
                granted = info.get('granted_balance', '0')
                _log(f"   总余额:      {total} {currency}")
                _log(f"   充值余额:    {topped_up} {currency}")
                _log(f"   赠金余额:    {granted} {currency}")
        else:
            _log(f"[余额] 查询失败: {data}")
    except Exception as e:
        _log(f"[余额] 查询异常: {e}")


# ==================== 主管道 ====================

def run_pipeline(
    root: Path,
    *,
    config_path: Path = None,
    use_gpu: bool = False,
    progress_callback: Callable = None,
) -> dict:
    """
    运行 LRC 字幕翻译管道

    从 root 目录递归扫描 .lrc 文件，翻译并覆盖写入。

    参数:
        root: 工作目录（由 input_path.txt 或命令行指定）
        config_path: 配置文件路径
        use_gpu: 保留参数（兼容旧接口）
        progress_callback: 进度回调
    """
    # 刷新已有缓冲，确保从头开始输出
    sys.stdout.flush()
    sys.stderr.flush()

    ctx = PipelineContext(config_path)
    root_abs = root.absolute()

    # 打印启动信息
    _sep("字幕翻译管道启动")
    _log(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    _log(f"工作目录: {root_abs}")
    _log(f"配置文件: {(config_path.absolute() if config_path else Path('config.json').absolute())}")
    _log()

    # API 信息
    api_cfg = ctx.api_cfg
    gen_params = api_cfg.get('generation_params', {})
    _log("[API 配置]")
    _log(f"  模型: {api_cfg.get('model', 'N/A')}")
    _log(f"  Base URL: {api_cfg.get('base_url', 'N/A')}")
    _log(f"  Timeout: {api_cfg.get('timeout', 'N/A')}s")
    _log(f"  temperature: {gen_params.get('temperature', 'N/A')}")
    _log(f"  top_p: {gen_params.get('top_p', 'N/A')}")
    _log(f"  max_tokens: {gen_params.get('max_tokens', 'N/A')}")
    _log()

    if not root.exists():
        _log(f"❌ 错误: 工作目录不存在 - {root_abs}")
        sys.exit(1)

    # ──── 第 0 步: 语音转录（infer.exe）──
    _run_transcription_if_needed(root, ctx)
    _log()

    # ──── 第 1 步: 扫描字幕文件 ────
    _sep("第 1 步: 扫描字幕文件")
    lrc_files, ja_lrc_files, archived, file_groups = scan_subtitle_files(root)
    ctx.stats['archived'] = archived
    _log()

    if not lrc_files:
        _log("未发现字幕文件，无需翻译")
        return ctx.stats

    # ──── 第 2 步: 加载台本（按 RJ 目录，避免跨作品污染）──
    _sep("第 2 步: 加载台本参考")
    # 延迟到第 4.5 步按目录加载，此处仅占位
    _log()

    # ──── 第 3 步: 世界观延迟到第 4.5 步按目录加载（避免跨作品污染）──
    _sep("第 3 步: 加载世界观/角色/场景（按 RJ 目录）")
    _log()

    # ──── 第 4 步: 分词 + 文本分析 ────
    _sep("第 4 步: 文本分词与分析")
    all_texts = []
    for lrc_path in lrc_files:
        try:
            sub_file = parse_subtitle_file(lrc_path)
            if sub_file:
                all_texts.extend([t for t in sub_file.original_lyrics if t.strip()])
        except Exception:
            pass

    if all_texts:
        _analyze_texts(all_texts, ctx)
    else:
        _log("  -> 无有效文本行")
    _log()

    # ──── 第 4.5 步: 自动分析语料（术语/世界观） ────
    _sep("第 4.5 步: 自动分析语料 — 术语提取 & 世界观生成")

    # 按 RJ 根目录归组分析（同一 RJ 号的子目录共享术语/世界观/台本）
    from io_adapter.file_scanner import find_rj_work_root
    work_terms: dict = {}        # group_key -> {jp: zh}
    work_alias: dict = {}        # group_key -> [alias_items]
    work_scriptbook: dict = {}   # group_key -> scriptbook_lines
    work_worldview: dict = {}    # group_key -> worldview_dict
    analyzed_dirs: set = set()

    for fpath in lrc_files:
        rj_root, rj_number = find_rj_work_root(fpath)
        group_key = rj_root if rj_root else fpath.parent
        if group_key in analyzed_dirs:
            continue
        analyzed_dirs.add(group_key)

        _log(f"\n  分析目录: {group_key.absolute()}" + (f" (RJ{rj_number})" if rj_number else ""))
        _dir_key = str(group_key)

        # 加载该目录的台本（只在该 RJ 目录内搜索，不跨作品）
        dir_scriptbook = _load_scriptbook(group_key, ctx)
        if dir_scriptbook:
            work_scriptbook[_dir_key] = dir_scriptbook
            _log(f"  -> 台本: {len(dir_scriptbook)} 行")

        # 分析/加载该目录的术语、alias 和世界观（只在该 RJ 目录内）
        dir_terms, dir_alias, dir_worldview = _analyze_work_terms(group_key, ctx)

        # 合并从文件加载的已有世界观（如果有的话，已被 _analyze_work_terms 加载）
        if dir_worldview:
            work_worldview[_dir_key] = dir_worldview
            if isinstance(dir_worldview.get('characters'), dict):
                dir_worldview['characters'] = [
                    {'name': k, 'personality': str(v)}
                    for k, v in dir_worldview['characters'].items()
                ]
            wv_text = dir_worldview.get('worldview', '')
            _log(f"  -> 世界观: {'有' if wv_text else '无'} ({len(wv_text)} 字符), 角色: {len(dir_worldview.get('characters', {}))} 个")

        # 从 config.json 合并全局术语到该目录
        config_terms = load_terms_from_config(ctx.config)
        for jp, zh in config_terms.items():
            if jp not in dir_terms:
                dir_terms[jp] = zh

        work_terms[_dir_key] = dir_terms
        work_alias[_dir_key] = dir_alias
        _log(f"  -> 术语: {len(dir_terms)} 个, alias: {len(dir_alias)} 个")
    _log()

    # ──── 第 5 步: 翻译 ────
    translation_mode = ctx.config.get('app', {}).get('translation_mode', 'per_track')

    # 过滤已翻译文件（参照 translate.py 的 process_all_lrc 逻辑）
    # 关键：不能只看 .ja.lrc 是否存在（转录也会产生 .ja.lrc），
    # 必须检测 .lrc 文件内容的实际语言
    from io_adapter.lrc_handler import detect_lrc_language  # 已从 translate.py 迁移过来
    skip_translated = ctx.config.get('app', {}).get('skip_translated', True)
    if skip_translated:
        skipped = 0
        remaining: list[Path] = []
        for f in lrc_files:
            ext = f.suffix
            ja_path = f.parent / f"{f.stem}.ja{ext}"
            if ja_path.exists():
                # .ja.lrc 存在 + .lrc 内容已变成中文 → 确实翻译过
                lang = detect_lrc_language(f)
                if lang == 'chinese':
                    _log(f"  [跳过] 已翻译: {f.name}")
                    ctx.stats['skipped'] += 1
                    skipped += 1
                    continue
                # .ja.lrc 存在但 .lrc 仍是日文 → 只转录未翻译，需要翻译
            else:
                # 没有 .ja.lrc 但 .lrc 已经是中文 → 翻译过但留档丢失，跳过
                lang = detect_lrc_language(f)
                if lang == 'chinese':
                    _log(f"  [跳过] 已翻译(无留档): {f.name}")
                    ctx.stats['skipped'] += 1
                    skipped += 1
                    continue
            remaining.append(f)
        if skipped > 0:
            _log(f"  → 跳过 {skipped} 个已翻译文件, 剩余 {len(remaining)} 个")
            new_groups: dict = {}
            for f in remaining:
                new_groups.setdefault(f.parent, []).append(f)
            file_groups = new_groups
        lrc_files = remaining

    _sep(f"第 5 步: 翻译（模式: {translation_mode}）")
    _log(f"待处理文件: {len(lrc_files)} 个\n")

    if translation_mode == "all_at_once" and file_groups:
        # 方案 A：按目录批量翻译（单次 API 调用）
        for parent_dir, dir_files in file_groups.items():
            _log(f"\n{'#'*60}")
            _log(f"# 批量翻译目录: {parent_dir.absolute()}")
            _log(f"# 文件数: {len(dir_files)}")
            _log(f"{'#'*60}")

            # 收集文件数据
            files_data = []
            for fpath in dir_files:
                sub_file = parse_subtitle_file(fpath)
                if sub_file is None:
                    continue
                files_data.append({
                    'file_id': fpath.name,
                    'source_path': fpath,
                    'sub_file': sub_file,
                    'original_lyrics': sub_file.original_lyrics,
                })

            if not files_data:
                continue

            # 获取当前目录的术语/世界观（不跨作品）
            _batch_rj_root, _ = find_rj_work_root(parent_dir)
            _batch_key = str(_batch_rj_root) if _batch_rj_root else str(parent_dir)
            _batch_terms = work_terms.get(_batch_key, {})
            _batch_alias = work_alias.get(_batch_key, [])
            _batch_worldview = work_worldview.get(_batch_key, None)

            # 调用批量翻译
            result = ctx.translate_engine.translate_directory(
                files_data,
                terms=_batch_terms,
                alias_list=_batch_alias,
                worldview=_batch_worldview,
            )

            # 写回结果
            for fd in files_data:
                fpath = fd['source_path']
                translated = result.get(fd['file_id'], [])
                if translated:
                    # 补齐
                    while len(translated) < len(fd['original_lyrics']):
                        translated.append(fd['original_lyrics'][len(translated)])
                    # 留档
                    ext = fpath.suffix.lower()
                    export_ja = ctx.config.get('app', {}).get('export_ja_lrc', True)
                    if export_ja:
                        ja_path = fpath.parent / f"{fpath.stem}.ja{ext}"
                        if not ja_path.exists():
                            try:
                                shutil.copy2(fpath, ja_path)
                                _log(f"  [留档] {fpath.name} -> {ja_path.name}")
                            except Exception:
                                pass
                    # 写入
                    write_subtitle_file(fd['sub_file'], translated, fpath)
                    ctx.stats['translated'] += 1
                    ctx.stats['total_lines'] += len(translated)
                    _log(f"  [写入] {fpath.name}: {len(translated)} 行")

    else:
        # 跟踪当前 RJ 作品目录（而非 LRC 文件的直接父目录）
        _current_dir = None
        _current_rj = None
        _dir_start_translated = 0
        _dir_start_lines = 0
        _dir_start_time = 0.0

        # 方案 B：逐文件翻译（利用缓存）
        for i, lrc_path in enumerate(lrc_files):
            # 找到该文件所属的 RJ 作品根目录
            rj_root, rj_number = find_rj_work_root(lrc_path)
            _effective_dir = rj_root if rj_root else lrc_path.parent

            # RJ 作品切换时记录上一作品的报告
            if _current_rj is not None and rj_number != _current_rj:
                _record_dir_report(ctx, _current_dir, _dir_start_translated,
                                   _dir_start_lines, _dir_start_time)
            if rj_number != _current_rj:
                _current_rj = rj_number
                _current_dir = _effective_dir
                _dir_start_translated = ctx.stats['translated']
                _dir_start_lines = ctx.stats['total_lines']
                _dir_start_time = time.time()
            _log(f"\n{'#'*60}")
            _log(f"# 文件 [{i+1}/{len(lrc_files)}] — 进度: {(i+1)/len(lrc_files)*100:.0f}%")
            _log(f"# {lrc_path.absolute()}")
            _log(f"{'#'*60}")

            try:
                # 获取当前文件所属 RJ 目录的术语/台本/世界观（不跨作品）
                _dir_key = str(_effective_dir)
                _dir_terms = work_terms.get(_dir_key, {})
                _dir_alias = work_alias.get(_dir_key, [])
                _dir_scriptbook = work_scriptbook.get(_dir_key, None)
                _dir_worldview = work_worldview.get(_dir_key, None)
                success = translate_one_lrc(
                    lrc_path, ctx,
                    terms=_dir_terms,
                    alias_list=_dir_alias,
                    worldview=_dir_worldview,
                    scriptbook_lines=_dir_scriptbook,
                )
                if success:
                    _log(f"\n✓ 文件 [{i+1}/{len(lrc_files)}] 翻译成功: {lrc_path.name}")
                else:
                    ctx.stats['skipped'] += 1
                    _log(f"\n○ 文件 [{i+1}/{len(lrc_files)}] 跳过: {lrc_path.name}")

                    _log(f"\n  已耗时: {ctx.elapsed:.1f}s | "
                      f"已完成: {ctx.stats['translated']}/{len(lrc_files)} | "
                      f"API调用: {ctx.stats['api_calls']} 次")

            except Exception as e:
                _log(f"\n✗ 文件 [{i+1}/{len(lrc_files)}] 错误: {lrc_path.name}")
                _log(f"  异常: {e}")
                import traceback
                _log(f"  堆栈:\n{traceback.format_exc()}")
                ctx.stats['skipped'] += 1

        # 记录最后一个目录
        if _current_dir is not None:
            _record_dir_report(ctx, _current_dir, _dir_start_translated,
                               _dir_start_lines, _dir_start_time)

    # ──── 第 6 步: 打印报告 ────
    _sep("处理完成")
    _log(f"完成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    _log()

    _log("文件统计:")
    _log(f"  留档日文 (.ja.lrc):  {ctx.stats['archived']} 个")
    _log(f"  翻译中文 (.lrc):     {ctx.stats['translated']} 个")
    _log(f"  跳过:                {ctx.stats['skipped']} 个")
    _log(f"  保留(已是中文):      {ctx.stats['kept']} 个")
    _log(f"  总计:                {len(lrc_files)} 个")
    _log()

    _log("翻译统计:")
    _log(f"  总行数:     {ctx.stats['total_lines']}")
    _log(f"  成功行数:   {ctx.stats['success_lines']}")
    _log(f"  API 调用:   {ctx.stats['api_calls']} 次")
    _log()

    elapsed = ctx.elapsed
    _log(f"  总耗时: {elapsed:.1f} 秒 ({elapsed/60:.1f} 分钟)")
    if ctx.stats['translated'] > 0:
        _log(f"  平均每文件: {elapsed/ctx.stats['translated']:.1f} 秒")
    _log()

    # 费用统计
    pricing = ctx.pricing
    hit_per_1m = pricing.get('hit_per_1m', 0)
    miss_per_1m = pricing.get('miss_per_1m', 0)
    completion_per_1m = pricing.get('completion_per_1m', 0)

    hit_tokens = ctx.stats['total_hit_tokens']
    miss_tokens = ctx.stats['total_miss_tokens']
    completion_tokens = ctx.stats['total_completion_tokens']

    cost_hit = (hit_tokens / 1_000_000) * hit_per_1m
    cost_miss = (miss_tokens / 1_000_000) * miss_per_1m
    cost_completion = (completion_tokens / 1_000_000) * completion_per_1m
    cost_total = cost_hit + cost_miss + cost_completion

    total_tok = hit_tokens + miss_tokens + completion_tokens
    hit_rate = (hit_tokens / (hit_tokens + miss_tokens) * 100) if (hit_tokens + miss_tokens) > 0 else 0
    _sep("📊 API 用量 & 费用统计")
    _log(f"  📊 总Token: {total_tok:,}")
    _log(f"  🟢 缓存命中:   {hit_tokens:>10,} tokens ({hit_rate:.1f}%) × ¥{hit_per_1m}/百万 = ¥{cost_hit:.4f}")
    _log(f"  🔵 缓存未命中: {miss_tokens:>10,} tokens × ¥{miss_per_1m}/百万 = ¥{cost_miss:.4f}")
    _log(f"  🟣 输出Token:  {completion_tokens:>10,} tokens × ¥{completion_per_1m}/百万 = ¥{cost_completion:.4f}")
    _log(f"  {'─'*50}")
    _log(f"  💰 本次费用: ¥{cost_total:.4f}")
    _sep()
    _log()

    # ── 查询 DeepSeek 账户余额 ──
    _fetch_balance(ctx)

    _log()
    _log("文件说明:")
    _log("  .lrc/.srt/.vtt       = 当前使用的中文字幕")
    _log("  .ja.lrc/.ja.srt/.ja.vtt = 日文原版留档")
    _log("  .terms.json          = 作品级术语表")
    _log("  .alias.json          = ASR 误识别参考表")
    _log("  .worldview.json      = 作品世界观")
    _log()

    # 翻译总览报告
    print_summary_report(ctx)

    return ctx.stats


# ==================== 命令行入口 ====================

def main():
    """命令行测试入口"""
    import argparse

    parser = argparse.ArgumentParser(description='LRC 字幕翻译管道')
    parser.add_argument('root', nargs='?', default='.', help='工作目录')
    parser.add_argument('--config', help='配置文件路径')
    parser.add_argument('--gpu', action='store_true', help='GPU 加速（已废弃）')

    args = parser.parse_args()

    run_pipeline(
        Path(args.root),
        config_path=Path(args.config) if args.config else None,
        use_gpu=args.gpu,
    )


if __name__ == '__main__':
    main()