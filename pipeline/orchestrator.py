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
import re
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

# utils 工具层
from utils.token_tracker import TokenTracker, TokenUsage
from utils.printer import Printer
from io_adapter.file_scanner import find_rj_work_root

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

# 模块级 printer 引用（run_pipeline 启动时设置）
_pr: Printer | None = None


def _log(msg: str = "", *, flush: bool = True):
    """输出日志并立即刷新 stdout。通过模块级 _pr 统一格式。"""
    print(msg, flush=flush)
    sys.stderr.flush()


def _sep(title: str = ""):
    """打印分隔线"""
    if title:
        _log(f"{'='*60}")
        _log(f"  {title}")
        _log(f"{'='*60}")
    else:
        _log("=" * 60)


def _debug(msg: str):
    """调试日志，仅 debug=true 时输出"""
    if _pr and _pr.debug_enabled:
        _log(f"    [DEBUG] {msg}")


# ==================== 管道上下文 ====================

class PipelineContext:
    """管道运行上下文，承载整个过程的状态"""

    def __init__(self, config_path: Path = None):
        self.config = load_config(config_path)
        self.start_time = time.time()
        self.app_cfg = self.config.get('app', {})
        self.api_cfg = self.config.get('api', {})
        self.pricing = self.config.get('pricing', {})
        # 统一输出 + token 追踪
        self.pr = Printer(debug=self.app_cfg.get('debug', False))
        self.tracker = TokenTracker(self.pricing)
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
            self._translate_engine = create_translate_engine(
                api_config,
                system_prompt_file=prompt_file if prompt_file else None,
                verbose=self.pr.debug_enabled,
                pricing=self.pricing,
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


def sync_lrc_to_srt_vtt(lrc_path: Path, translated_texts: list[str]) -> int:
    """翻译完成后将中文文本同步到同目录的 SRT/VTT 文件

    从原始 SRT/VTT 读取时间戳结构，填充中文文本后写回。
    翻译只改文本内容，时间戳完全保留。

    返回: 同步成功的文件数
    """
    stem = lrc_path.stem
    parent = lrc_path.parent
    synced = 0

    for ext in ('.srt', '.vtt'):
        other_path = parent / f"{stem}{ext}"
        if not other_path.exists():
            _log(f"  [同步] {ext} 文件不存在，跳过: {other_path.name}")
            continue
        try:
            sub_file = parse_subtitle_file(other_path)
            if sub_file is None:
                _log(f"  [同步] {ext} 解析失败: {other_path.name}")
                continue

            # SRT/VTT 不含 LRC 的空行占位，过滤空文本后匹配
            orig_texts = sub_file.original_lyrics
            trans_filtered = [t for t in translated_texts if t]  # 去掉空行（LRC 占位符）
            if len(orig_texts) != len(trans_filtered):
                _log(f"  [同步] {ext} 行数不匹配 (原文{len(orig_texts)}, 译文{len(trans_filtered)}), 跳过: {other_path.name}")
                continue

            write_subtitle_file(sub_file, trans_filtered, other_path)
            _log(f"  [同步] {ext} 写回中文: {other_path.name}")
            synced += 1
        except Exception as e:
            _log(f"  [同步] {ext} 失败: {e}")

    return synced


# ==================== 台本加载 ====================

def _llm_identify_scriptbook_files(
    candidates: list[Path],
    work_dir: Path,
    ctx: PipelineContext,
    track_names: list[str] = None,
) -> tuple[list[Path], bool, dict] | list[Path] | None:
    """使用 LLM 识别台本文件，判断是否预分割，并完成文件→音轨匹配。

    参数:
        candidates: 所有 .txt/.pdf 备选文件
        work_dir: 作品根目录
        ctx: 管道上下文
        track_names: 音轨名列表

    返回:
        - (list[Path], bool, dict): (台本文件, 是否预分割, {文件索引→音轨名})
        - None: LLM 确认无台本（不回退正则）
        - []: LLM 调用失败（回退正则）
    """
    if not candidates:
        return []  # 无候选，等同于失败，回退正则

    _log(f"\n[台本·LLM] 开始识别台本文件（共 {len(candidates)} 个备选）")

    # 构建文件列表（相对路径 + 文件名）
    file_list_parts: list[str] = []
    for i, f in enumerate(candidates, 1):
        try:
            rel = f.relative_to(work_dir)
        except ValueError:
            rel = f
        file_list_parts.append(f"{i}. {rel}")
    file_list_text = '\n'.join(file_list_parts)

    # 提取作品名（work_dir 最后一级目录名）
    work_name = work_dir.name

    # 音轨名列表（截断防止 prompt 过长）
    track_names_hint = ""
    if track_names:
        shown = track_names[:20]
        track_names_hint = "\n".join(f"  - {n}" for n in shown)
        if len(track_names) > 20:
            track_names_hint += f"\n  ... 还有 {len(track_names) - 20} 个"

    system_prompt = (
        "你是一位日语ASMR音声作品台本识别专家。"
        "只返回JSON格式结果，不要解释，不要添加任何额外文本。"
    )

    user_prompt = f"""请从以下文件列表中识别台本（剧本/台词/シナリオ）文件，并判断是否已按音轨预分割。

音声作品: {work_name}

【音轨名称列表】（共 {len(track_names) if track_names else 0} 个）
{track_names_hint if track_names_hint else '(未提供)'}

【台本文件典型特征】
- 文件名或所在目录包含: 台本、だいほん、シナリオ、script、セリフ、本編、台詞
- 按音轨编号命名: 01, 02, トラック1, track1, Tr.1, １, ２, #1, #2 等
- PDF格式的剧本/台词文档
- 内容以角色对话（台词）为主

【非台本文件典型特征】
- readme / 説明 / 注意事項 / 必ず読んで 等说明文档
- クレジット / credit / cast / 声優 等演职员信息
- 特典 / bonus / おまけ 等赠品说明
- フィニッシュタイム / 射精メモ / 射精箇所 等特殊标注
- あとがき / 感想 / 紹介 等后记感想
- キャスト / 購入特典 等非台本内容

【txt 优先规则】（重要，必须遵守）
- 若同一台本同时存在 .txt 和 .pdf 两个版本（同名或内容相同），scriptbook_indices **只选择 .txt 版本**，忽略 .pdf
- is_pre_split=true 时，file_track_mapping 的 value 必须指向被选中的台本文件；若该台本有 txt+pdf 两个版本，必须指向 .txt

【预分割判断】（is_pre_split）
- true: 台本已按音轨拆分为独立文件，每个文件对应一个音轨
- false: 台本是整体文件（单个PDF或txt），需程序再分割
- **重要**: 文件名以纯数字或编号开头（1, 01, １, #1, トラック1 等）且数量与音轨数接近 → 判定为预分割

【文件→音轨匹配】（仅 is_pre_split=true 时需要 file_track_mapping）
⚠ file_track_mapping 格式: {{音轨名称: 台本文件名}}
- key = 从【音轨名称列表】中逐字复制的完整音轨名
- value = 从【文件列表】中逐字复制的完整文件名
- 示例: {{"#1プロローグ": "セリフ初稿台本_tr01.txt", "#2本編": "セリフ初稿台本_tr02.txt"}}

【文件列表】（共 {len(candidates)} 个）
{file_list_text}

返回JSON（仅JSON）：
{{"scriptbook_indices": [1, 2], "is_pre_split": true, "file_track_mapping": {{"音轨名1": "文件名1", "音轨名2": "文件名2"}}, "reasoning": "简短依据"}}

无台本时: {{"scriptbook_indices": [], "is_pre_split": false, "file_track_mapping": {{}}, "reasoning": "无"}}"""

    try:
        import json as _json
        from openai import OpenAI

        api_cfg = ctx.api_cfg
        api_key = api_cfg.get('key') or api_cfg.get('api_key', '')
        base_url = api_cfg.get('base_url', 'https://api.deepseek.com')
        model = api_cfg.get('model', 'deepseek-v4-flash')
        # config.json 中的 timeout 字段可能为毫秒或秒；OpenAI 客户端需要秒
        raw_timeout = api_cfg.get('timeout', 60)
        if raw_timeout > 300:
            # 如果值很大（如 2000），很可能是毫秒配置，转换为秒
            timeout = max(raw_timeout / 1000.0, 30.0)
        elif raw_timeout < 5:
            # 如果值太小（如 2），也按秒处理但至少给 30 秒
            timeout = 30.0
        else:
            timeout = float(raw_timeout)

        if not api_key:
            _log("  [台本·LLM] 未配置 API Key，回退到正则识别")
            return []  # 失败 → 回退正则

        _log(f"  [台本·LLM] 发送 {len(candidates)} 个备选文件给 LLM 识别 (model={model}, timeout={timeout}s)")

        # 清理代理环境变量（避免 httpx 走代理导致连接失败）
        import os as _os
        for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
            _os.environ.pop(_k, None)
        _os.environ['NO_PROXY'] = '*'

        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            temperature=0.1,
            max_tokens=4096,  # 足够容纳 JSON + reasoning，500 容易截断
        )

        content = response.choices[0].message.content or ''
        _debug(f"[台本·LLM] 响应: {content[:300]}")

        # 统一 token 追踪：记录台本识别 LLM 调用
        _usage = response.usage
        if _usage:
            _hit = getattr(_usage, 'prompt_cache_hit_tokens', None)
            _miss = getattr(_usage, 'prompt_cache_miss_tokens', None)
            if _hit is None:
                _details = getattr(_usage, 'prompt_tokens_details', None)
                _hit = _details.cached_tokens if _details else 0
                _miss = _usage.prompt_tokens - _hit if _usage.prompt_tokens else 0
            _token_stats = {
                'hit_tokens': _hit or 0,
                'miss_tokens': _miss or 0,
                'prompt_tokens': _usage.prompt_tokens or 0,
                'completion_tokens': _usage.completion_tokens or 0,
            }
            _sb_cost = ctx.tracker.compute_cost(
                _token_stats['hit_tokens'],
                _token_stats['miss_tokens'],
                _token_stats['completion_tokens'],
            )
            ctx.tracker.record(TokenUsage(
                request_type='scriptbook_id',
                label=f'{len(candidates)}个备选文件',
                work_key=str(work_dir),
                prompt_tokens=_token_stats['prompt_tokens'],
                hit_tokens=_token_stats['hit_tokens'],
                miss_tokens=_token_stats['miss_tokens'],
                completion_tokens=_token_stats['completion_tokens'],
                cost=_sb_cost,
                elapsed=0,
            ))
            ctx.pr.token_inline(ctx.tracker.records[-1])

        # 提取 JSON
        result_text = content.strip()

        # 校验：空响应直接判定 API 失败（不回退，因为强制 JSON 模式不应返回空）
        if not result_text:
            _log(f"  [台本·LLM] API 返回空内容 (model={model})，请检查模型名/网络/API配额")
            return []  # API 失败 → 回退正则

        if '```json' in result_text:
            result_text = result_text.split('```json')[1].split('```')[0].strip()
        elif '```' in result_text:
            result_text = result_text.split('```')[1].split('```')[0].strip()

        # 二次校验：清理后仍为空
        if not result_text:
            _log(f"  [台本·LLM] 清理后 JSON 文本为空，原始响应: {content[:500]}")
            return []

        try:
            result = _json.loads(result_text)
        except _json.JSONDecodeError as e:
            _log(f"  [台本·LLM] JSON 解析失败: {e}")
            _log(f"  [台本·LLM] 原始响应 (前500字符): {content[:500]}")
            _log(f"  [台本·LLM] 提取的 JSON 文本 (前300字符): {result_text[:300]}")
            return []  # API JSON 格式异常 → 回退正则
        indices = result.get('scriptbook_indices', [])
        is_pre_split = result.get('is_pre_split', False)
        file_track_mapping = result.get('file_track_mapping', {})
        reasoning = result.get('reasoning', '')

        _log(f"  [台本·LLM] 识别结果: {len(indices)} 个台本, "
             f"预分割={'是' if is_pre_split else '否'}, "
             f"匹配{len(file_track_mapping)}个音轨 — {reasoning}")

        # LLM 明确返回空列表 → 确认无台本（与 API 失败区分）
        if not indices:
            _log(f"  [台本·LLM] LLM 确认: 无台本文件，不回退正则")
            return None

        # 映射回文件路径
        confirmed: list[Path] = []
        for idx in indices:
            if 1 <= idx <= len(candidates):
                confirmed.append(candidates[idx - 1])
            else:
                _log(f"  [台本·LLM] 警告: 编号 {idx} 超出范围 {len(candidates)}")

        if confirmed:
            for f in confirmed:
                try:
                    _log(f"    ✓ {f.relative_to(work_dir)}")
                except ValueError:
                    _log(f"    ✓ {f}")
        return (confirmed, is_pre_split, file_track_mapping)

    except Exception as e:
        _log(f"  [台本·LLM] 识别失败: {e}，回退到正则识别")
        import traceback
        traceback.print_exc()
        return []  # API 失败 → 回退正则


def _try_load_cached_scriptbook(work_dir: Path, track_names: list[str]) -> dict[str, list[str]] | None:
    """检查是否存在上次导出的台本缓存，有则直接加载

    缓存位置：
    - _scriptbook_clean.json: 预分割台本的导出结果
    - _split_tracks/*.txt: 非预分割台本 Flash 分割+清洗后的导出结果

    返回: track_map 或 None（无缓存）
    """
    import json as _json
    from engines.scriptbook_cleaner import _conservative_pre_clean

    # 1) 查找 _scriptbook_clean.json（预分割路径导出）
    for cache_file in work_dir.rglob('_scriptbook_clean.json'):
        # 排除 _split_tracks 目录下的副本
        if '_split_tracks' in cache_file.parts:
            continue
        try:
            cached = _json.loads(cache_file.read_text(encoding='utf-8'))
            if isinstance(cached, dict) and len(cached) > 0:
                # 验证：至少有一个 track_name 在 track_names 中（防止跨作品误匹配）
                if track_names:
                    matched = sum(1 for tn in track_names if tn in cached)
                    if matched == 0:
                        continue  # 可能是其他作品的缓存
                # 加载成功，匹配到当前 track_names
                result: dict[str, list[str]] = {}
                for tn in track_names:
                    result[tn] = cached.get(tn, [])
                return result
        except Exception:
            continue

    # 2) 查找 _split_tracks/*.txt（非预分割路径导出）
    for split_dir in work_dir.rglob('_split_tracks'):
        if not split_dir.is_dir():
            continue
        track_files = sorted(split_dir.glob('*.txt'))
        if not track_files:
            continue
        # 只有当 track 文件数量 >= track_names 的一半时才信任缓存
        if track_names and len(track_files) < len(track_names) * 0.5:
            continue

        result = {}
        if track_names:
            # 尝试按文件名匹配到 track_names
            for tf in track_files:
                stem = tf.stem
                matched_name = None
                for tn in track_names:
                    if stem in tn or tn in stem:
                        matched_name = tn
                        break
                if not matched_name:
                    # 模糊匹配：比较前几个字符
                    for tn in track_names:
                        if stem[:4] == tn[:4]:
                            matched_name = tn
                            break
                target = matched_name or stem
                try:
                    lines = [l.strip() for l in tf.read_text(encoding='utf-8').split('\n') if l.strip()]
                    result[target] = lines
                except Exception:
                    result[target] = []
            # 确保所有 track_names 都有条目（空的也行）
            for tn in track_names:
                if tn not in result:
                    result[tn] = []
        else:
            for tf in track_files:
                try:
                    lines = [l.strip() for l in tf.read_text(encoding='utf-8').split('\n') if l.strip()]
                    result[tf.stem] = lines
                except Exception:
                    result[tf.stem] = []

        if result:
            return result

    return None


def _extract_track_asr_samples(work_dir: Path, track_names: list[str], max_lines: int = 15) -> dict[str, str]:
    """从 .ja.lrc 文件中提取每条音轨的前几句 ASR 台词样本

    参数:
        work_dir: 作品目录（递归搜索子目录中的 .ja.lrc）
        track_names: 音轨名列表（LRC 文件 stem）
        max_lines: 每条音轨最多提取的行数

    返回:
        {track_name: "台词1\\n台词2\\n..."}  仅包含有样本的条目
    """
    from io_adapter.lrc_handler import parse_lrc_file
    samples: dict[str, str] = {}
    # 递归收集所有 .ja.lrc，按 stem 建索引（处理文件分散在子目录的情况）
    ja_map: dict[str, Path] = {}
    for ja_file in work_dir.rglob('*.ja.lrc'):
        stem = ja_file.stem
        if stem.endswith('.ja'):
            stem = stem[:-3]
        ja_map[stem] = ja_file

    for name in track_names:
        ja_path = ja_map.get(name)
        if ja_path is None:
            continue
        try:
            lrc_lines = parse_lrc_file(ja_path)
        except Exception:
            continue
        non_empty = [l.text.strip() for l in lrc_lines if l.text and l.text.strip()]
        if non_empty:
            samples[name] = '\n'.join(non_empty[:max_lines])
    return samples


def _load_scriptbook(work_dir: Path, ctx: PipelineContext, track_names: list[str] = None,
                     track_samples: dict[str, str] = None) -> dict[str, list[str]] | None:
    """加载台本参考，按音轨分割+清洗后返回 {音轨名: [清洁台词]}

    流程:
    1. 收集所有 .txt/.pdf 备选 → LLM 识别台本 + 判断是否已预分割
    2. LLM 确认无台本 → 直接返回 None（不回退正则）
    3. LLM 调用失败 → 回退到正则关键词识别
    4. 预分割台本 → 按文件编号匹配音轨，仅本地清洗（节省 Flash token）
    5. 非预分割台本 → Flash 分割+清洗 → 按音轨名匹配
    6. Flash 失败时回退到正则分割

    参数:
        work_dir: 作品目录
        track_names: 音轨名列表（LRC 文件 stem）
        track_samples: 每条音轨的前几句 ASR 台词样本 {track_name: "样本文本"}

    返回:
        {track_name: [clean_lines]} 映射，或 None 表示无台本
    """
    from core.scriptbook_parser import (
        find_scriptbooks_in_dir,
        collect_all_scriptbook_candidates,
        load_scriptbook_content,
        build_raw_scriptbook_map,
    )

    # ── 阶段〇: 检查缓存（上次导出的结果，跳过重复 LLM 调用）──
    cached_result = _try_load_cached_scriptbook(work_dir, track_names or [])
    if cached_result is not None:
        total_lines = sum(len(v) for v in cached_result.values())
        matched = sum(1 for v in cached_result.values() if v)
        _log(f"\n[台本] 检测到已缓存的台本结果 ({matched}/{len(cached_result)} 个音轨有内容, {total_lines} 行)，跳过分割")
        _log(f"[台本] 如需重新分割请删除缓存文件: _scriptbook_clean.json 和 _split_tracks/")
        return cached_result

    scriptbook_files: list[Path] = []
    is_pre_split: bool = False          # LLM 判断台本是否已按音轨预分割
    file_track_mapping: dict = {}       # LLM 返回的 {文件索引字符串: 音轨名}

    # ── 阶段一: 收集所有备选 + LLM 识别 ──
    all_candidates = collect_all_scriptbook_candidates(work_dir)
    if all_candidates:
        _log(f"\n[台本] 收集到 {len(all_candidates)} 个 txt/pdf 备选文件")
        for f in all_candidates[:10]:
            try:
                _log(f"  → {f.relative_to(work_dir)}")
            except ValueError:
                _log(f"  → {f}")
        if len(all_candidates) > 10:
            _log(f"  → ... 还有 {len(all_candidates) - 10} 个")

        llm_candidates = all_candidates[:100]
        if len(all_candidates) > 100:
            _log(f"  [台本] 备选文件过多，仅取前 100 个送 LLM 识别")
        llm_result = _llm_identify_scriptbook_files(llm_candidates, work_dir, ctx, track_names)
        if llm_result is None:
            # LLM 明确返回空 → 确认无台本，不回退正则
            _log(f"\n[台本] LLM 确认无台本文件，跳过台本加载")
            return None
        if isinstance(llm_result, tuple):
            # 成功: (files, is_pre_split, file_track_mapping)
            scriptbook_files, is_pre_split, file_track_mapping = llm_result
        else:
            # 失败: [] (空列表)
            scriptbook_files = llm_result

    # ── 阶段二: LLM 失败时回退到正则 ──
    if not scriptbook_files:
        if all_candidates:
            _log(f"\n[台本] LLM 识别失败，回退到正则关键词识别")
        scriptbook_files = find_scriptbooks_in_dir(work_dir)

        # 正则回退时，检查文件名是否暗示已预分割（如 トラック1, track01, Tr.1, 01, １ 等）
        if scriptbook_files and not is_pre_split:
            import re as _re_fallback
            _track_pattern = _re_fallback.compile(
                r'(?:トラック|track|tr|トラ)[\s_．.\-]*([０-９0-9]+)'
                r'|^[#＃]?([０-９0-9]+)[\s_．.\-]',  # 纯数字开头 #1 / １ / 01
                _re_fallback.IGNORECASE,
            )
            _pre_split_count = 0
            for _f in scriptbook_files:
                _m = _track_pattern.search(_f.stem)
                if _m:
                    _pre_split_count += 1
                elif _f.stem.strip().isdigit():
                    # 全数字文件名（如 "１", "2", "13"）——直接判定为编号
                    _pre_split_count += 1
                elif _re_fallback.match(r'^[０-９0-9]+$', _f.stem.strip()):
                    _pre_split_count += 1
            # 如果超过半数文件名包含音轨编号，判定为预分割
            if _pre_split_count >= len(scriptbook_files) * 0.5:
                is_pre_split = True
                # 尝试按编号匹配到 track_names
                for _idx, _f in enumerate(scriptbook_files, 1):
                    _num_str = None
                    _m = _track_pattern.search(_f.stem)
                    if _m:
                        # 优先取 group(2)（纯数字模式），再 group(1)（track前缀模式）
                        _num_str = (_m.group(2) or _m.group(1) or '').translate(
                            str.maketrans('０１２３４５６７８９', '0123456789'))
                    else:
                        # 纯数字文件名
                        _raw = _f.stem.strip().translate(
                            str.maketrans('０１２３４５６７８９', '0123456789'))
                        if _raw.isdigit():
                            _num_str = _raw
                    if _num_str and track_names:
                        try:
                            _num_int = int(_num_str)
                            # 在 track_names 中找包含此编号的条目（#1, 01, track1 等格式）
                            for _tn in track_names:
                                _tn_clean = _tn.translate(
                                    str.maketrans('０１２３４５６７８９', '0123456789'))
                                if (str(_num_int).zfill(2) in _tn_clean
                                        or f'#{_num_int}' in _tn_clean
                                        or f'トラック{_num_int}' in _tn_clean.lower()):
                                    file_track_mapping[str(_idx)] = _tn
                                    break
                            if str(_idx) not in file_track_mapping:
                                file_track_mapping[str(_idx)] = f"{_num_int:02d}"
                        except ValueError:
                            pass
                _log(f"  [台本] 正则回退检测到预分割: {_pre_split_count}/{len(scriptbook_files)} 个文件含音轨编号")

    if not scriptbook_files:
        # 回退：搜索根目录下带 | 分隔符的台本 txt 文件
        candidates = list(work_dir.rglob('*.txt'))
        scriptbook_path = None
        for cand in candidates:
            try:
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
        if scriptbook_path:
            scriptbook_files = [scriptbook_path]

    if not scriptbook_files:
        _log(f"\n[台本] 未发现台本文件")
        return None

    _log(f"\n[台本] 发现台本目录/文件: {len(scriptbook_files)} 个")
    for f in scriptbook_files[:5]:
        _log(f"  → {f.absolute()}")
    if len(scriptbook_files) > 5:
        _log(f"  → ... 还有 {len(scriptbook_files) - 5} 个")

    # ── 导出 ──
    export_scriptbook = ctx.config.get('app', {}).get('export_scriptbook_content', False)

    # ═══════════════════════════════════════════════════════════
    # 预分割路径: LLM 已返回 file_track_mapping {文件索引→音轨名}
    # 直接用 LLM 映射加载文件，再将 LLM 音轨名模糊匹配到真实音轨名
    # ═══════════════════════════════════════════════════════════
    if is_pre_split and file_track_mapping:
        _log(f"  [台本] LLM 确认台本已预分割, 映射 {len(file_track_mapping)} 个音轨")

        from engines.scriptbook_cleaner import _conservative_pre_clean

        track_map: dict[str, list[str]] = {}
        if track_names:
            for name in track_names:
                track_map[name] = []

        # LLM 返回 {音轨名: 文件名}，音轨名匹配到真实名
        import unicodedata
        def _norm_key(s: str) -> str:
            s = unicodedata.normalize('NFC', s)
            return re.sub(r'[\s_\-・·\.\,\#]', '', s).lower()

        real_index = {_norm_key(n): n for n in track_names} if track_names else {}
        # 文件名 → Path 索引
        file_index = {f.name: f for f in scriptbook_files}

        for llm_track_name, llm_filename in file_track_mapping.items():
            # 匹配真实音轨名
            real_name = None
            if track_names:
                nk = _norm_key(llm_track_name)
                real_name = real_index.get(nk)
                if real_name is None:
                    for rn in track_names:
                        if nk in _norm_key(rn) or _norm_key(rn) in nk:
                            real_name = rn
                            break
            if real_name is None:
                _debug(f"预分割: LLM音轨名\"{llm_track_name}\"无法匹配, 跳过")
                continue

            # 找到对应文件
            f = file_index.get(llm_filename)
            if f is None:
                # fallback: 模糊匹配文件名
                for fn, fp in file_index.items():
                    if _norm_key(llm_filename) in _norm_key(fn) or _norm_key(fn) in _norm_key(llm_filename):
                        f = fp
                        break
            if f is None:
                _debug(f"预分割: LLM文件名\"{llm_filename}\"找不到, 跳过")
                continue

            content = load_scriptbook_content(f, api_config=ctx.api_cfg)
            if content:
                cleaned = _conservative_pre_clean(content)
                lines = [l.strip() for l in cleaned.split('\n') if l.strip()]
                track_map[real_name] = lines
                _log(f"  [预分割] {real_name} ← {f.name} ({len(lines)} 行)")
            else:
                _log(f"  [预分割] {real_name} ← {f.name} (加载失败)")

        total_clean = sum(len(v) for v in track_map.values())
        _log(f"  → 预分割匹配完成: {len(track_map)} 个音轨, 共 {total_clean} 行台词")

        # 导出
        if export_scriptbook and track_map:
            import json as _json
            export_dir = scriptbook_files[0].parent
            (export_dir / '_scriptbook_clean.json').write_text(
                _json.dumps(track_map, ensure_ascii=False, indent=2), encoding='utf-8')
            split_dir = export_dir / '_split_tracks'
            split_dir.mkdir(exist_ok=True)
            for name, lines in track_map.items():
                if lines:
                    safe_name = name.replace('/', '_').replace('\\', '_')
                    (split_dir / f'{safe_name}.txt').write_text('\n'.join(lines), encoding='utf-8')
            _log(f"  → 导出: {export_dir / '_scriptbook_clean.json'}")
            _log(f"  → 导出: {split_dir.absolute()} ({total_clean} 行)")

        return track_map

    # ═══════════════════════════════════════════════════════════
    # 非预分割路径: 拼接全文 → Flash 分割+清洗
    # ═══════════════════════════════════════════════════════════
    raw_map = build_raw_scriptbook_map(scriptbook_files, api_config=ctx.api_cfg)
    all_lines: list[str] = []
    for track_num in sorted(raw_map.keys()):
        all_lines.extend(raw_map[track_num])
    raw_text = "\n".join(all_lines)
    _log(f"  → 原始台本: {len(all_lines)} 行, {len(raw_text)} 字符")

    # 保守预清洗（仅删 100% 确定的噪音：页码、SE 行、纯数字行）
    # 不做深度正则清洗——效果差且易误伤，改为 Flash 完成清洗
    from engines.scriptbook_cleaner import _conservative_pre_clean
    pre_cleaned = _conservative_pre_clean(raw_text)
    _log(f"  → 保守预清洗后: {len(pre_cleaned)} 字符 (原始 {len(raw_text)} 字符)")

    # 如果没有提供 track_names，用正则分割兜底
    if not track_names:
        _log(f"  [台本] 无音轨名列表，使用正则分割")
        from engines.scriptbook_cleaner import split_scriptbook_regex
        track_map = split_scriptbook_regex(pre_cleaned, track_names or [])
    else:
        # Flash 分割+清洗（一次请求完成，不做本地深度清洗）
        from engines.scriptbook_cleaner import ScriptbookSplitter, split_scriptbook_regex
        api_config = ctx.api_cfg
        try:
            splitter = ScriptbookSplitter(api_config, verbose=ctx.pr.debug)
            track_map, sb_token_stats = splitter.split_and_clean_all_in_one(
                track_names, raw_text, track_samples=track_samples)
            # 统一 token 追踪：记录台本 Flash 分割调用
            if sb_token_stats:
                _sb_cost = ctx.tracker.compute_cost(
                    sb_token_stats.get('hit_tokens', 0),
                    sb_token_stats.get('miss_tokens', 0),
                    sb_token_stats.get('completion_tokens', 0),
                )
                ctx.tracker.record(TokenUsage(
                    request_type='scriptbook_split',
                    label=f'{len(track_names)}个音轨',
                    work_key=str(work_dir),
                    prompt_tokens=sb_token_stats.get('prompt_tokens', 0),
                    hit_tokens=sb_token_stats.get('hit_tokens', 0),
                    miss_tokens=sb_token_stats.get('miss_tokens', 0),
                    completion_tokens=sb_token_stats.get('completion_tokens', 0),
                    cost=_sb_cost,
                    elapsed=0,
                ))
                ctx.pr.token_inline(ctx.tracker.records[-1])
        except Exception as e:
            _log(f"  [台本] Flash 分割失败: {e}，回退到正则分割")
            track_map = {}

        # Flash 失败或返回结果不足 → 回退到正则
        if not track_map or len(track_map) < len(track_names) * 0.5:
            _log(f"  [台本] Flash 分割结果不足 ({len(track_map)}/{len(track_names)})，回退到正则分割")
            regex_map = split_scriptbook_regex(raw_text, track_names)
            # 合并：Flash 已有的保留，缺失的用正则补
            for name in track_names:
                if name not in track_map or not track_map[name]:
                    if name in regex_map and regex_map[name]:
                        track_map[name] = regex_map[name]
                if name not in track_map:
                    track_map[name] = []

        # 变体轨继承主轨台本（ルームver 等含（）修饰词的轨 → 去除修饰词后匹配主轨）
        import re as _re_variant
        for name in list(track_map.keys()):
            if not track_map[name]:
                base = _re_variant.sub(r'[（(][^）)]*[）)]', '', name)
                if base != name and base in track_map and track_map[base]:
                    track_map[name] = track_map[base]
                    _log(f"  [台本] 变体轨 {name[:40]}... 继承主轨台本 ({len(track_map[base])} 行)")

    total_clean = sum(len(v) for v in track_map.values())
    _log(f"  → 分割清洗后: {len(track_map)} 个音轨, 共 {total_clean} 行台词")

    # ── 导出（由 export_scriptbook_content 统一控制）──
    if export_scriptbook and track_map:
        import json as _json
        export_dir = scriptbook_files[0].parent

        # 原始台本
        (export_dir / '_scriptbook_export.txt').write_text(raw_text, encoding='utf-8')
        # Flash 清洗后的 JSON
        (export_dir / '_scriptbook_clean.json').write_text(
            _json.dumps(track_map, ensure_ascii=False, indent=2), encoding='utf-8')
        # 分割结果（每轨一个 txt）
        split_dir = export_dir / '_split_tracks'
        split_dir.mkdir(exist_ok=True)
        for name, lines in track_map.items():
            if lines:
                safe_name = name.replace('/', '_').replace('\\', '_')
                (split_dir / f'{safe_name}.txt').write_text('\n'.join(lines), encoding='utf-8')
        _log(f"  → 导出: {export_dir / '_scriptbook_export.txt'}")
        _log(f"  → 导出: {export_dir / '_scriptbook_clean.json'}")
        _log(f"  → 导出: {split_dir.absolute()} ({total_clean} 行)")

    return track_map


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

def _sample_ja_lrc_for_worldview(work_dir: Path, lines_per_file: int = 40) -> list[str]:
    """抽样 .ja.lrc 文件内容，用于世界观 LLM 分析

    从 work_dir 递归搜索所有 .ja.lrc 文件，每个文件均匀抽样若干行。

    返回: ["=== 文件: xxx.ja.lrc ===\\nline1\\nline2\\n...", ...]
    """
    samples: list[str] = []
    ja_files = sorted(work_dir.rglob('*.ja.lrc'))

    for ja_path in ja_files:
        # 排除 bug 收集目录
        if 'Hde_G_bug_lrc_collection' in ja_path.parts:
            continue
        try:
            sub = parse_subtitle_file(ja_path)
            if sub is None:
                continue
            texts = [t.strip() for t in sub.original_lyrics if t.strip()]
            if not texts:
                continue

            # 均匀抽样
            step = max(1, len(texts) // lines_per_file)
            sampled: list[str] = []
            for i in range(0, len(texts), step):
                if len(sampled) >= lines_per_file:
                    break
                sampled.append(texts[i])

            if sampled:
                samples.append(f"=== 文件: {ja_path.name} ===\n" + "\n".join(sampled))
        except Exception:
            continue

    return samples


def _inject_worldview_terms(worldview: dict, terms: dict, work_dir: Path) -> int:
    """将世界观中的角色名(name→name_cn)和 special_terms 注入术语表

    只添加 terms 中尚不存在的条目，避免覆盖手动编辑的术语。
    自动清理 LLM 可能附加的括号注释。

    返回: 注入的条目数
    """
    import re as _re_clean

    def _clean(v: str) -> str:
        """去除括号注释，只保留纯译名"""
        v = _re_clean.sub(r'[（(][^）)]*[）)]', '', v)  # 去除中文/英文括号内容
        v = _re_clean.sub(r'[（(][^）)]*$', '', v)     # 去除末尾未闭合括号内容
        return v.strip()

    injected = 0
    chars = worldview.get('characters', [])
    if isinstance(chars, list):
        for char in chars:
            if isinstance(char, dict):
                jp_name = char.get('name', '').strip()
                cn_name = _clean(char.get('name_cn', '').strip())
                if jp_name and cn_name and jp_name not in terms:
                    terms[jp_name] = cn_name
                    injected += 1
                    _log(f"  [术语] +{jp_name} → {cn_name}")

    sp_terms = worldview.get('special_terms', {})
    if isinstance(sp_terms, dict):
        for jp_term, cn_term in sp_terms.items():
            jp_term = jp_term.strip()
            cn_term = _clean(cn_term.strip() if isinstance(cn_term, str) else '')
            if jp_term and cn_term and jp_term not in terms:
                terms[jp_term] = cn_term
                injected += 1
                _log(f"  [术语] +{jp_term} → {cn_term}")

    if injected > 0:
        from engines.term_manager import save_terms_to_file, get_terms_path as _get_terms_path
        save_terms_to_file(work_dir, terms)
        _log(f"  [术语] 已将 {injected} 个术语写入 {_get_terms_path(work_dir)}")

    return injected


def _analyze_work_terms(work_dir: Path, ctx: PipelineContext) -> tuple[dict, list, dict]:
    """分析作品目录，自动生成术语表、alias表和世界观

    移植自 translate.py:analyze_work_terms()

    返回: (terms: dict, alias_list: list, worldview: dict)
    """
    from engines.term_manager import (
        get_terms_path, get_alias_path,
        load_terms_from_file, load_alias,
        # save_terms_to_file, save_alias,  # 术语分析已禁用
        # extract_names_from_filenames,
        # sample_all_lrc_files,
        # find_similar_reading_clusters,
        # analyze_characters_with_llm,
        # ROLE_TRANSLATIONS,
    )
    from engines.worldview_engine import (
        get_worldview_path, load_worldview,
        save_worldview,
        analyze_worldview_with_llm,
        is_freetalk_context,
    )

    terms_path = get_terms_path(work_dir)
    alias_path = get_alias_path(work_dir)
    worldview_path = get_worldview_path(work_dir)

    # 加载已缓存的术语/alias/世界观
    terms = load_terms_from_file(terms_path) if terms_path.exists() else {}
    alias_list = load_alias(work_dir) if alias_path.exists() else []
    worldview = load_worldview(work_dir) if worldview_path.exists() else {}

    if terms:
        _log(f"  加载已有术语表: {len(terms)} 个")
    if alias_list:
        _log(f"  加载已有 alias 表: {len(alias_list)} 个")
    if worldview:
        _log(f"  加载已有世界观 ({len(str(worldview.get('worldview', '')))} 字符)")

        # ── 从缓存世界观注入角色名和特殊术语到术语表（补漏）──
        _inject_worldview_terms(worldview, terms, work_dir)

    # ── 世界观自动分析（仅当不存在缓存时）──
    if not worldview:
        is_ft, cv_name = is_freetalk_context(work_dir)
        if is_ft:
            if cv_name:
                _log(f"  [世界观] 检测到 FreeTalk/CV「{cv_name}」，跳过世界观分析")
            else:
                _log(f"  [世界观] 检测到 FreeTalk，跳过世界观分析")
        else:
            samples = _sample_ja_lrc_for_worldview(work_dir)
            if samples:
                total_chars = sum(len(s) for s in samples)
                _log(f"  [世界观] 抽样 {len(samples)} 个 .ja.lrc 文件, 共 {total_chars} 字符，调用 LLM 分析...")
                api_config = get_api_config(ctx.config)
                if api_config.get('key') or api_config.get('api_key'):
                    try:
                        from engines.translate_engine import OpenAICompatEngine
                        engine = OpenAICompatEngine(api_config, verbose=ctx.pr.debug, pricing=ctx.pricing)
                        worldview, wv_token_stats = analyze_worldview_with_llm(engine, samples, verbose=ctx.pr.debug)
                        # 统一 token 追踪：记录世界观分析 LLM 调用
                        if wv_token_stats:
                            _wv_cost = ctx.tracker.compute_cost(
                                wv_token_stats.get('hit_tokens', 0),
                                wv_token_stats.get('miss_tokens', 0),
                                wv_token_stats.get('completion_tokens', 0),
                            )
                            ctx.tracker.record(TokenUsage(
                                request_type='worldview',
                                label=f'{len(samples)}个样本',
                                work_key=str(work_dir),
                                prompt_tokens=wv_token_stats.get('prompt_tokens', 0),
                                hit_tokens=wv_token_stats.get('hit_tokens', 0),
                                miss_tokens=wv_token_stats.get('miss_tokens', 0),
                                completion_tokens=wv_token_stats.get('completion_tokens', 0),
                                cost=_wv_cost,
                                elapsed=0,
                            ))
                            ctx.pr.token_inline(ctx.tracker.records[-1])
                        if worldview:
                            save_worldview(work_dir, worldview)
                            chars = worldview.get('characters', [])
                            char_count = len(chars) if isinstance(chars, list) else (len(chars) if isinstance(chars, dict) else 0)
                            _log(f"  [世界观] 已生成并保存: {len(str(worldview.get('worldview', '')))} 字符世界观, "
                                 f"{char_count} 个角色")

                            # ── 将世界观中的角色名和特殊术语注入术语表 ──
                            _inject_worldview_terms(worldview, terms, work_dir)
                        else:
                            _log(f"  [世界观] LLM 分析返回空结果")
                    except Exception as e:
                        _log(f"  [世界观] 分析异常: {e}")
                else:
                    _log(f"  [世界观] 未配置 API Key，跳过")
            else:
                _log(f"  [世界观] 无 .ja.lrc 样本可抽样，跳过")

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
    total_chars = sum(len(t) for t in texts)
    _log(f"  [解析] {len(texts)} 行文本, {total_chars} 字符")

    # 原文全空 → 尝试从 .ja.lrc 恢复（上次翻译可能写坏了）
    if total_chars == 0 and len(texts) > 0:
        ja_path = lrc_path.parent / f"{lrc_path.stem}.ja{lrc_path.suffix}"
        if ja_path.exists():
            _log(f"  [恢复] 原文为空，从 {ja_path.name} 恢复")
            shutil.copy2(ja_path, lrc_path)
            sub_file = parse_subtitle_file(lrc_path)
            if sub_file:
                texts = sub_file.original_lyrics
                total_chars = sum(len(t) for t in texts)
                _log(f"  [恢复] 重新解析: {len(texts)} 行, {total_chars} 字符")
        if total_chars == 0:
            _log(f"  [跳过] 原文内容为空（仅有时间戳），跳过翻译")
            ctx.stats["skipped"] += 1
            return False

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

    # 台本参考 —— 打印实际内容预览，让用户看到指导翻译用了台本的哪个部分
    if scriptbook_lines:
        preview_n = min(8, len(scriptbook_lines))
        _log(f"\n[台本] 使用分割清洗后台本作为翻译参考 — 共 {len(scriptbook_lines)} 行")
        _log(f"  ┌─ 前 {preview_n} 行预览 ─")
        for i, line in enumerate(scriptbook_lines[:preview_n], 1):
            # 截断过长的行（>120 字符）
            display = line if len(line) <= 120 else line[:117] + '...'
            _log(f"  │ {i:3d}: {display}")
        if len(scriptbook_lines) > preview_n:
            # 最后一行预览
            _log(f"  │ ...")
            _log(f"  │ {len(scriptbook_lines):3d}: {scriptbook_lines[-1][:120]}")
        _log(f"  └─ 台本参考预览结束 ─")
    else:
        _log(f"\n[台本] 无台本参考")

    # 多对多区间对齐：把 ASR 行对齐到单轨台本，每行附对应台本原文（sb）
    scriptbook_aligned = None
    if scriptbook_lines and texts:
        try:
            from engines.scriptbook_align import align_asr_scriptbook
            _nonempty = [i for i, t in enumerate(texts) if t and t.strip()]
            _aligned = align_asr_scriptbook([texts[i] for i in _nonempty], scriptbook_lines)
            scriptbook_aligned = {_nonempty[k]: v for k, v in _aligned.items()}
            _log(f"  [台本·对齐] {len(scriptbook_aligned)}/{len(_nonempty)} 行已对齐到台本区间")
            _preview_i = _nonempty[0] if _nonempty else 0
            if scriptbook_aligned and ctx.pr.debug:
                _log(f"  [台本·对齐] 示例 行{_preview_i}: sb={scriptbook_aligned[_preview_i]['sb'][:40]} "
                     f"conf={scriptbook_aligned[_preview_i]['conf']}")
        except Exception as _e:
            _log(f"  [台本·对齐] 失败，跳过: {_e}")
            scriptbook_aligned = None

    # 编号（整文件一次性翻译，不分块；每行附 sb 作含义参考）
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
        scriptbook_aligned=scriptbook_aligned,
        skip_hallucination_check=True,
    )
    call_elapsed = time.time() - call_start

    translated_batch = result.get('translated_lines', [])
    for t_line in translated_batch:
        if ': ' in t_line:
            t_line = t_line.split(': ', 1)[1]
        if t_line == '[EMPTY_LINE]':
            t_line = ''
        translated_texts.append(t_line)
    # 诊断：翻译全空时打印 LLM 原始响应（仅 debug 模式详细输出）
    if len(translated_batch) > 0 and all(not (t or '').strip() for t in translated_batch):
        _log(f"  WARN: 翻译结果全空 ({len(translated_batch)}行)")
        if ctx.pr.debug:
            _log(f"  [DEBUG] translated_batch前3=[{str(translated_batch[:3])[:200]}]")
            raw_resp = getattr(ctx.translate_engine, '_last_raw_response', 'NOT_FOUND')
            _log(f"  [DEBUG] LLM原始响应 ({len(raw_resp) if raw_resp != 'NOT_FOUND' else 'N/A'}字符): {(raw_resp or '')[:500]}")
            if raw_resp and raw_resp != 'NOT_FOUND' and len(raw_resp) > 500:
                _log(f"  [DEBUG] ...末尾: {raw_resp[-300:]}")

    _log(f"  ← 响应: {len(translated_batch)} 行, 耗时 {call_elapsed:.1f}s")
    non_empty = [t for t in translated_texts if t and t.strip()]
    _log(f"    有效行: {len(non_empty)}/{len(translated_texts)}")
    if not non_empty:
        _log(f"  WARN: 所有行为空！首3行原文: {[t[:40] for t in texts[:3]]}")
    elif ctx.pr.debug:
        for i, t in enumerate(non_empty[:3]):
            _log(f"      [{i+1}] {t[:80]}")
    hit = result.get('hit_tokens', 0)
    miss = result.get('miss_tokens', 0)
    prompt = result.get('prompt_tokens', hit + miss)
    comp = result.get('completion_tokens', 0)
    elapsed = result.get('elapsed', call_elapsed)
    cost = result.get('cost', 0)

    # 统一 token 追踪：记录 + 打印
    # work_key 使用 RJ 作品根目录，确保同一作品的翻译/台本/世界观合并统计
    _rj_root, _ = find_rj_work_root(lrc_path)
    _work_key = str(_rj_root) if _rj_root else str(lrc_path.parent)

    usage = TokenUsage(
        request_type='translate',
        label=lrc_path.name,
        work_key=_work_key,
        prompt_tokens=prompt,
        hit_tokens=hit,
        miss_tokens=miss,
        completion_tokens=comp,
        cost=cost,
        elapsed=elapsed,
    )
    ctx.tracker.record(usage)
    ctx.pr.token_inline(usage)

    ctx.stats['success_lines'] += len(translated_batch)
    ctx.stats['total_lines'] += len(translated_batch)
    ctx.stats['api_calls'] += 1
    ctx.stats['total_cost'] += cost
    ctx.stats['total_hit_tokens'] += hit
    ctx.stats['total_miss_tokens'] += miss
    ctx.stats['total_completion_tokens'] += comp

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

    # 翻译全空但原文非空 → 不覆盖，保留原文件
    trans_chars = sum(len(t) for t in translated_texts if t)
    if trans_chars == 0 and total_chars > 0:
        _log(f"  [跳过写入] 翻译结果全空，保留原文件不覆盖")
        ctx.stats["skipped"] += 1
        return False

    # 写回翻译结果（通用字幕格式）
    write_subtitle_file(sub_file, translated_texts, lrc_path)
    _log(f"\n[写入] -> {abs_path}")
    _log(f"  -> 翻译完成: {len(translated_texts)} 行中文")
    ctx.stats['translated'] += 1

    # 同步中文到同目录的 SRT/VTT
    sync_lrc_to_srt_vtt(lrc_path, translated_texts)

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

    # 解析相对路径（从 HDEG 根目录，不是作品目录）
    import sys
    if getattr(sys, 'frozen', False):
        hdeg_root = Path(sys.executable).parent
    else:
        hdeg_root = Path(__file__).parent.parent  # pipeline/ -> HDEG/

    if not infer_exe:
        infer_exe = './infer.exe'
        _log(f"[转录] 未配置 infer.exe，默认使用同目录: {hdeg_root / infer_exe}")
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

    # 设置模块级 printer，使 _debug() 可以工作
    global _pr
    _pr = ctx.pr

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
    _log(f"  reasoning_effort: {gen_params.get('reasoning_effort', 'N/A')}  (思考强度)")
    _log()

    if not root.exists():
        _log(f"❌ 错误: 工作目录不存在 - {root_abs}")
        sys.exit(1)

    # ──── 检查 API Key ────
    api_key = api_cfg.get('key') or api_cfg.get('api_key', '')
    if not api_key:
        _log("⚠ API Key 为空，仅执行本地模型（转录/翻译），跳过 LLM 翻译")
        _log()

        # 仅运行转录（infer.exe），然后直接返回
        _run_transcription_if_needed(root, ctx)

        # 扫描生成的字幕文件，报告结果
        lrc_files, ja_files, archived, file_groups = scan_subtitle_files(root)
        ctx.stats['archived'] = archived
        _log(f"\n[完成] 本地模型处理完毕，共生成 {len(lrc_files)} 个字幕文件")
        for ext in ('.lrc', '.srt', '.vtt'):
            count = sum(1 for f in lrc_files if f.suffix == ext)
            if count:
                _log(f"  {ext}: {count} 个")
        return ctx.stats

    # ──── 第 0 步: 语音转录（infer.exe）──
    _run_transcription_if_needed(root, ctx)
    _log()

    # ──── 第 1 步: 扫描字幕文件 ────
    _sep("第 1 步: 扫描字幕文件")
    lrc_files, ja_lrc_files, archived, file_groups = scan_subtitle_files(root)
    ctx.stats['archived'] = archived

    # 翻译仅处理 LRC 文件，SRT/VTT 在翻译完成后从 LRC 结果同步
    srt_vtt_files = [f for f in lrc_files if f.suffix in ('.srt', '.vtt')]
    lrc_files = [f for f in lrc_files if f.suffix == '.lrc']

    if srt_vtt_files:
        _log(f"\n[字幕] 发现 {len(srt_vtt_files)} 个 SRT/VTT 文件（不参与翻译，翻译完成后同步）")
    _log()

    if not lrc_files:
        _log("未发现 LRC 字幕文件，无需翻译")
        if ja_lrc_files:
            _log(f"  (有 {len(ja_lrc_files)} 个 .ja.lrc 留档文件，但无对应 .lrc)")
        return ctx.stats

    # ──── 文本分析（仅 debug 模式）──
    if ctx.pr.debug_enabled:
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

    # ──── 第 2 步: 加载台本 + 世界观 + 术语 ────
    _sep("第 2 步: 加载台本 / 世界观 / 术语")

    # 按 RJ 根目录归组分析（同一 RJ 号的子目录共享术语/世界观/台本）
    from io_adapter.file_scanner import find_rj_work_root
    work_terms: dict = {}        # group_key -> {jp: zh}
    work_alias: dict = {}        # group_key -> [alias_items]
    work_scriptbook: dict = {}   # group_key -> {track_name: [clean_lines]}
    work_worldview: dict = {}    # group_key -> worldview_dict
    analyzed_dirs: set = set()

    # 收集 LRC 文件名（仅 .lrc，不含 .ja.lrc 留档），用于台本分割匹配
    # 直接使用 fpath.stem 全名；.ja.lrc 已被过滤不会重复
    all_track_names: list[str] = []
    for fpath in lrc_files:
        if fpath.suffix == '.lrc' and not fpath.name.endswith('.ja.lrc'):
            all_track_names.append(fpath.stem)

    for fpath in lrc_files:
        rj_root, rj_number = find_rj_work_root(fpath)
        group_key = rj_root if rj_root else fpath.parent
        if group_key in analyzed_dirs:
            continue
        analyzed_dirs.add(group_key)

        _log(f"\n  分析目录: {group_key.absolute()}" + (f" (RJ{rj_number})" if rj_number else ""))
        _dir_key = str(group_key)

        # 加载该目录的台本（只在该 RJ 目录内搜索，不跨作品）
        # 传入该目录的 LRC 音轨名列表，用于台本分割匹配
        from io_adapter.file_scanner import find_rj_work_root as _find_rj
        _dir_track_names = []
        for _fp in lrc_files:
            if _fp.suffix == '.lrc' and not _fp.name.endswith('.ja.lrc'):
                _rj_root, _ = _find_rj(_fp)
                _gk = _rj_root if _rj_root else _fp.parent
                if str(_gk) == _dir_key:
                    _dir_track_names.append(_fp.stem)
        # 提取每条音轨的前几句 ASR 台词，辅助 LLM 定位台本段落
        _dir_track_samples = _extract_track_asr_samples(group_key, _dir_track_names)
        if _dir_track_samples:
            _log(f"  [台本] 提取 ASR 样本: {len(_dir_track_samples)}/{len(_dir_track_names)} 条音轨有 .ja.lrc")
        dir_scriptbook = _load_scriptbook(group_key, ctx, track_names=_dir_track_names,
                                          track_samples=_dir_track_samples if _dir_track_samples else None)
        if dir_scriptbook:
            work_scriptbook[_dir_key] = dir_scriptbook
            _total_sb_lines = sum(len(v) for v in dir_scriptbook.values())
            _log(f"  -> 台本: {len(dir_scriptbook)} 个音轨, 共 {_total_sb_lines} 行")

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
                    _debug(f"已翻译: {f.name}")
                    ctx.stats['skipped'] += 1
                    skipped += 1
                    continue
                # .ja.lrc 存在但 .lrc 仍是日文 → 只转录未翻译，需要翻译
            else:
                # 没有 .ja.lrc 但 .lrc 已经是中文 → 翻译过但留档丢失，跳过
                lang = detect_lrc_language(f)
                if lang == 'chinese':
                    _debug(f"已翻译(无留档): {f.name}")
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

    _sep(f"第 3 步: 翻译（模式: {translation_mode}）")
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
                    # 同步中文到同目录的 SRT/VTT
                    if fpath.suffix == '.lrc':
                        sync_lrc_to_srt_vtt(fpath, translated)

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
                _dir_scriptbook_map = work_scriptbook.get(_dir_key, None)
                _dir_worldview = work_worldview.get(_dir_key, None)
                # 按 LRC 文件名查找对应音轨的台本
                _track_sb_lines = None
                if _dir_scriptbook_map:
                    _track_sb_lines = _dir_scriptbook_map.get(lrc_path.stem, None)
                    # 精确匹配为空（None 或 []）→ 模糊匹配
                    if not _track_sb_lines:
                        for _sb_name, _sb_lines in _dir_scriptbook_map.items():
                            if _sb_lines and (lrc_path.stem in _sb_name or _sb_name in lrc_path.stem):
                                _track_sb_lines = _sb_lines
                                break
                success = translate_one_lrc(
                    lrc_path, ctx,
                    terms=_dir_terms,
                    alias_list=_dir_alias,
                    worldview=_dir_worldview,
                    scriptbook_lines=_track_sb_lines,
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

    # 费用统计（由统一 TokenTracker 输出）
    ctx.pr.token_summary(ctx.tracker, elapsed_total=ctx.elapsed)
    # 如果 tracker 无记录，回退到旧格式
    if not ctx.tracker.records:
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