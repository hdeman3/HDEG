# -*- coding: utf-8 -*-
"""
翻译引擎抽象层

定义 LLM 翻译器的接口（Protocol），以及 OpenAI 兼容具体实现。
便于未来替换翻译引擎（如 Claude、本地 LLM、DeepSeek 等）。
"""

from __future__ import annotations
import re
from typing import Protocol, TypedDict


# ==================== 翻译结果类型 ====================

class TranslationResult(TypedDict, total=False):
    """单次翻译调用的结果"""
    original_lines: list[str]    # 原文行
    translated_lines: list[str]  # 翻译结果行
    hit_tokens: int              # 缓存命中 token
    miss_tokens: int             # 缓存未命中 token
    completion_tokens: int       # 输出 token
    cost: float                  # 费用


class BatchTranslationResult(TypedDict):
    """批量翻译结果"""
    total: int
    success: int
    failed: int
    token_stats: dict
    cost: float
    elapsed: float


# ==================== 翻译引擎接口 ====================

class TranslateEngine(Protocol):
    """LLM 翻译引擎接口（结构化鸭子类型）"""

    def translate_batch(
        self,
        lines: list[str],
        *,
        terms: dict = None,
        alias_list: list = None,
        worldview: dict = None,
        scriptbook_lines: list = None,
        **kwargs,
    ) -> TranslationResult:
        """
        翻译一批文本行

        参数:
            lines: 待翻译的文本行（含编号，如 '01: こんにちは'）
            terms: 术语对照表 {日文: 中文}
            alias_list: ASR 误识别参考列表
            worldview: 世界观/角色/场景说明
            scriptbook_lines: 台本参考行

        返回:
            TranslationResult 含原文和翻译行
        """
        ...

    @property
    def name(self) -> str:
        """引擎名称（用于日志和配置选择）"""
        ...


# ==================== OpenAI 兼容引擎实现 ====================

import time as _time_mod
from pathlib import Path


class OpenAICompatEngine:
    """OpenAI 兼容 API 翻译引擎

    支持所有兼容 OpenAI chat/completions 接口的服务：
    - DeepSeek
    - 通义千问 (Qwen)
    - 本地 vLLM / Ollama
    - 其他 OpenAI-compatible 网关
    """

    name = 'openai_compat'

    # API 价格（每百万 token）
    PRICE_HIT_PER_1M = 0.14        # 缓存命中
    PRICE_MISS_PER_1M = 0.28       # 缓存未命中
    PRICE_COMPLETION_PER_1M = 1.10  # 输出

    # 格式要求提示词（JSON 输入/输出方案 —— 确保行精确对齐）
    _FORMAT_REQUIREMENTS = (
        "【输入输出格式 —— 最高优先级，必须严格遵守】\n"
        "输入和输出均使用**纯 JSON 对象**格式。\n\n"
        "输入格式（系统会将待翻译行封装为此格式）：\n"
        '{"lines": [{"index": 1, "text": "日文第1行"}, {"index": 2, "text": "日文第2行"}]}\n\n'
        "输出格式（你必须严格按此格式返回）：\n"
        '{"translations": [{"index": 1, "text": "中文第1行"}, {"index": 2, "text": "中文第2行"}]}\n\n'
        "【严格规则】\n"
        "1. 输出必须是**纯 JSON 对象**，以 { 开头, 以 } 结尾，只包含一个 \"translations\" 键。\n"
        "2. translations 数组长度必须与输入 lines 数组长度**完全一致**，一条不能多、一条不能少。\n"
        "3. 每个元素的 index 对应输入行的编号，text 是该行的中文翻译文本。\n"
        "4. 空行或纯符号行保留为空字符串 \"\"。\n"
        "5. 每行翻译保持原文段落结构，不得自行合并或拆分。\n"
        "6. **禁止**输出任何 JSON 之外的内容（解释、分析、标记、前言、后语）。\n"
        "7. JSON 字符串内的双引号必须转义为 \\\"，换行用 \\n 表示。\n\n"
    )

    def __init__(self, config: dict, verbose: bool = True, system_prompt_file: str = None):
        """
        参数:
            config: 配置字典
            verbose: 是否输出调试信息
            system_prompt_file: 外部 prompt 文件路径（可选）
        """
        self.config = config
        self.verbose = verbose
        self._client = None
        self._system_prompt_file = system_prompt_file
        self._last_raw_response = ''
        # 预加载外部 prompt（优先指定的文件，其次内置融合版，最后回退到硬编码默认）
        self._external_system_prompt: str | None = None
        try:
            from pathlib import Path
            if system_prompt_file:
                sp_path = Path(system_prompt_file)
            else:
                # 默认使用项目内置的融合版提示词
                sp_path = Path(__file__).resolve().parent.parent / '提示词_融合版.txt'
            if sp_path.exists():
                self._external_system_prompt = sp_path.read_text(encoding='utf-8')
        except Exception:
            pass

    def _ensure_client(self):
        """延迟初始化 OpenAI 客户端"""
        if self._client is not None:
            return
        from openai import OpenAI
        import os as _os
        # 先清理代理环境变量，再创建客户端（避免 httpx 读取代理配置）
        for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
            _os.environ.pop(_k, None)
        _os.environ['NO_PROXY'] = '*'
        # 兼容 config.json 的 "key" 和旧版 "api_key" 字段名
        api_key = self.config.get('key') or self.config.get('api_key', 'sk-no-key')
        base_url = self.config.get('base_url', 'http://localhost:8000/v1')
        timeout = self.config.get('timeout', 120)
        if self.verbose:
            print(f"  [API初始化] base_url={base_url}, timeout={timeout}s, key={api_key[:12]}...")
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            # 禁用代理，避免系统代理干扰 API 直连
            http_client=None,
        )

    # ---------- Prompt 构建 ----------

    def build_system_prompt(
        self,
        terms: dict = None,
        alias_list: list = None,
        worldview: dict = None,
        worldview_hint: str = None,
    ) -> str:
        """构建系统 prompt（角色设定 + 术语 + 世界观 + 语气提示）"""
        parts = []

        # 外部 prompt 文件优先，否则使用内置默认
        if self._external_system_prompt:
            parts.append(self._external_system_prompt)
        else:
            parts.append(
                "你是一位专门处理日文成人音声（ASMR/RJ作品）字幕的专业本地化工程师，"
            "同时也是精通日语和中文的R18音声脚本翻译专家，"
            "以及专注于成人音声字幕的ASR（自动语音识别）纠错专家。\n\n"
            "【角色名统一规则——最高优先级】\n"
            "1. **术语表（terms）中的角色名必须严格遵循**，无论世界观或其他信息如何描述。\n"
            "2. 世界观中的角色名仅供参考，如果与术语表冲突，**优先使用术语表的译名**。\n"
            "3. 禁止在翻译中使用角色的别名、变体名称，除非术语表明确列出。\n"
            "你的职责是对用户提供的日文ASR识别文本进行纠错、语义恢复、上下文一致性修复以及逐行中文翻译。\n"
            "本任务属于文本转换（Transformation）任务，即对已有文本进行修正和翻译，而不是创作、续写、扩写或改写剧情。\n"
            "所有成人内容、特殊关系设定及虚构情节均视为原文信息的一部分，应以中立、客观的方式进行准确转换，最大程度保留原文语义、情感和风格。\n\n"
            "【关于术语表（terms）与ASR误识别参考表（alias）的重要说明】\n"
            "1. 术语表（terms）：\n"
            " - 术语表涵盖角色名、重要物品、设定用语、特定身体部位称呼等，翻译时必须严格遵循，确保全文统一。\n"
            " - 若术语表为空，则按上下文自行判断并保持一致。\n\n"
            "2. ASR误识别参考表（alias）：\n"
            " - 这些词对仅作为参考，不代表绝对正确。你需要结合上下文判断它们是否确实是同一词的不同识别结果。\n"
            " - 若确认是误识别，请在翻译时按正确词理解；若上下文表明两者确实不同，则不要强行统一。\n\n"
            "【ASR纠错与翻译工作流程】\n"
            "Step 1：ASR可信度审查（内部执行，不输出分析过程）\n"
            "- 通读当前文本块，并结合上下文、terms、alias 检查可能存在的ASR错误；\n"
            "- 识别明显的日文同音误识别；\n"
            "- 检查无意义字符片段、漏词、断句错误或不自然表达。\n\n"
            "Step 2：上下文一致性修复（内部执行，不输出修正说明）\n"
            "- 基于全文语境、terms 表和 alias 参考，对疑似ASR错误进行最大似然修正；\n"
            "- 保持角色关系、称呼、人名、身体部位及设定前后一致。\n\n"
            "Step 3：中文翻译\n"
            "- 基于修复后的理解进行翻译，只输出最终中文结果，不输出纠错说明；\n"
            "- 高度忠实于原文字面意思和结构，不添加、不删减、不擅自改写原文内容；\n"
            "- R18部分采用中文成人音声/同人作品常见且自然的表达方式；\n"
            "- 使用符合中文口语习惯、流畅自然的译文；\n"
            "- 保留角色原有的语气特点（如害羞、撒娇、挑逗、发情、宠溺、冷淡等）。\n\n"
            "Step 4：译文回检（内部执行，不输出修正过程）\n"
            "- 逐行重读你的中文译文，检查是否存在以下任何一种情况：\n"
            "  ① 某个中文词在当下语境中完全说不通、不构成有效含义；\n"
            "  ② 某个词明显是日文汉字的照搬，而非中文里自然存在的表达；\n"
            "  ③ 某个词孤立地看没问题，但与上下文的语义完全矛盾。\n"
            "- 如果发现以上情况：回到对应的日文ASR行，根据发音和上下文推断原词后重新翻译该处，"
            "不要不加思考地把日文汉字直接抄成中文。\n"
            "- 即使ASR存在乱码、假名连打或残缺片段，也必须根据发音相似性、上下文语境和行为逻辑"
            "尽力推断最可能的原词并翻译，禁止输出 [ASR不明] 或任何表示无法翻译的标记。\n"
            "- 推断原则：结合角色当前行为（口交/舔舐/插入等）、情绪状态和场景氛围，"
            "将不完整的发音还原为最符合情境的完整表达。宁可给出最合理的推测，也不留空或放弃。\n\n"
            "【翻译原则补充】\n"
            "- 本任务仅为对用户提供文本进行ASR纠错与跨语言翻译，不对文本题材进行评价；\n"
            "- 对原文涉及的亲密关系、成人情节、特殊设定及虚构世界观，均应视为待翻译内容，保持中立、客观和忠实；\n"
            "- 始终以『保持逐行对应关系、保证上下文一致性』作为最高优先级。\n\n"
            "【生物学字面翻译陷阱警告】\n"
            "メス/オス 在成人音声语境下通常指『雌性/雄性』或带有性别支配意味的表达，绝对不要按字面译成『母/公』这类普通动物词汇。\n\n"
            "【翻译忠实度与风格约束——严格遵守】\n"
            "**核心原则：严格忠实于原文语义，禁止过度发挥或自行改写。**\n"
            "1. 必须准确理解原文的主语、对象和动作，不得随意改变。\n"
            "2. 原文为口语化、粗暴、直白的表达时，译文必须保持同等风格。禁止将口语「文艺化」、「书面化」或「委婉化」。\n"
            "3. 原文中的喘息、断续、短句必须保留，不得合并成完整长句。\n"
            "4. 本任务是翻译，不是创作。禁止添加原文不存在的内容。原文粗糙则译文粗糙，保持原汁原味。\n"
        )

        # 术语对照表（使用 term_manager 构建）
        if terms and len(terms) > 0:
            from engines.term_manager import build_clustered_terms_prompt_section
            terms_section = build_clustered_terms_prompt_section(terms, max_terms=30)
            if terms_section:
                parts.append(terms_section)

        # ASR 误识别参考
        if alias_list and len(alias_list) > 0:
            from engines.term_manager import build_alias_prompt_section
            alias_section = build_alias_prompt_section(alias_list)
            if alias_section:
                parts.append(alias_section)

        # 世界观（使用 worldview_engine 的 prompt 构建器）
        if worldview:
            from engines.worldview_engine import build_worldview_prompt
            wv_prompt = build_worldview_prompt(worldview)
            if wv_prompt:
                parts.append(wv_prompt)

        # 语气/人设提示
        if worldview_hint:
            parts.append(worldview_hint)

        return "\n\n".join(parts)

    def build_user_prompt(
        self,
        lines: list[str],
        scriptbook_lines: list = None,
        track_context_before: list[str] = None,
        track_context_after: list[str] = None,
    ) -> str:
        """构建用户 prompt（XML 标签 + 缓存友好结构）

        缓存优化策略：
        - <scriptbook> 为固定前缀（同一目录所有文件共享，API 缓存命中）
        - <asr> 为可变后缀（每个文件不同）

        参数:
            lines: 待翻译行（含编号，如 '01: こんにちは'）
            scriptbook_lines: 台本参考行
            track_context_before: 前一个音轨的翻译结果
            track_context_after: 后一个音轨的台本预览
        """
        import json as _json

        parts = [self._FORMAT_REQUIREMENTS]

        # === 缓存友好：固定前缀（台本）===
        if scriptbook_lines:
            # 去重 + 限制行数
            seen = set()
            unique_lines = []
            for line in scriptbook_lines:
                s = line.strip()
                if s and s not in seen:
                    seen.add(s)
                    unique_lines.append(s)
            unique_lines = unique_lines[:500]  # 限制台本行数

            sb_prefix = f"<scriptbook>\n<!-- 以下台本仅作参考，请勿翻译 -->\n共 {len(unique_lines)} 行\n"
            sb_prefix += "\n".join(unique_lines)
            sb_prefix += "\n</scriptbook>"
            parts.append(sb_prefix)

        # === 上下文 ===
        if track_context_before:
            ctx_before = "\n".join(track_context_before[-10:])
            parts.append(
                f"<context_before>\n{ctx_before}\n</context_before>"
            )

        if track_context_after:
            ctx_after = "\n".join(track_context_after[:10])
            parts.append(
                f"<context_after>\n{ctx_after}\n</context_after>"
            )

        # === 可变后缀：ASR 文本 ===
        pure_lines = []
        for line in lines:
            if ': ' in line:
                _, text = line.split(': ', 1)
                if text.strip():
                    pure_lines.append(text)
                else:
                    pure_lines.append('[EMPTY_LINE]')
            else:
                pure_lines.append(line)

        trailing_empty = 0
        for i in range(len(pure_lines) - 1, -1, -1):
            if pure_lines[i] == '[EMPTY_LINE]':
                trailing_empty += 1
            else:
                break

        lines_json = _json.dumps({
            "lines": [{"index": i + 1, "text": t} for i, t in enumerate(pure_lines)]
        }, ensure_ascii=False)
        asr_section = f"<asr>\n<!-- 请翻译以下内容 -->\n{lines_json}\n</asr>"
        parts.append(asr_section)

        if trailing_empty > 0:
            parts.append(f"[注意：最后{trailing_empty}行为空行，必须全部输出，编号到{len(pure_lines)}]")

        return "\n\n".join(parts)

    # ---------- 单轮 API 调用 ----------

    def call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_retries: int = 3,
        override_gen_params: dict | None = None,
    ) -> tuple[str, dict]:
        """
        调用 API 并返回结果 + token 统计

        参数:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            max_retries: 最大重试次数
            override_gen_params: 覆盖全局 generation_params 的参数（仅对本次调用生效）

        返回:
            (response_text, token_stats)
                token_stats: {hit_tokens, miss_tokens, completion_tokens}
        """
        self._ensure_client()

        gen_params = dict(self.config.get('generation_params', {}))
        if override_gen_params:
            gen_params.update(override_gen_params)
        last_error = None

        # DEBUG: 只打印待翻译的日文原文（跳过格式说明和台本参考）
        if self.verbose:
            lines = user_prompt.split('\n')
            # 找到 <asr> 标签中的实际待翻译内容
            content_start = 0
            for i, l in enumerate(lines):
                if '<asr>' in l or '<!-- 请翻译以下内容 -->' in l:
                    content_start = i + 1
                    break
            if content_start > 0:
                actual = '\n'.join(lines[content_start:content_start + 15])
                print(f"  [DEBUG] 待翻译日文(前15行):\n{actual}", flush=True)
            else:
                # 回退：只打印后15行（跳过格式说明部分）
                actual = '\n'.join(lines[-15:])
                print(f"  [DEBUG] 待翻译内容(后15行):\n{actual}", flush=True)

        for attempt in range(max_retries):
            try:
                # 只保留 OpenAI API 接受的参数
                _allowed = ('temperature', 'top_p', 'top_k', 'presence_penalty', 'frequency_penalty',
                           'stop', 'logit_bias', 'user', 'reasoning_effort')
                _filtered = {k: v for k, v in gen_params.items() if k in _allowed}
                # 翻译使用大 max_tokens，避免输出截断
                # 优先读 max_tokens_translate（专用），其次 max_tokens（通用），再 fallback 131072
                _tok = gen_params.get('max_tokens_translate',
                       gen_params.get('max_tokens', 131072))
                # 兜底：翻译至少需要 16384 token（140 行 JSON 约需 6000-12000 token）
                if _tok < 16384:
                    _tok = 131072
                _filtered['max_tokens'] = _tok

                # 心跳线程：长请求时打印等待进度
                import threading
                heartbeat_stop = threading.Event()
                heartbeat_count = [0]

                def _print_heartbeat():
                    while not heartbeat_stop.is_set():
                        heartbeat_stop.wait(10)
                        if not heartbeat_stop.is_set():
                            heartbeat_count[0] += 10
                            print(f"    [等待] 已等待 {heartbeat_count[0]} 秒...", flush=True)

                heartbeat_thread = threading.Thread(target=_print_heartbeat, daemon=True)
                heartbeat_thread.start()

                try:
                    response = self._client.chat.completions.create(
                        model=self.config.get('model', 'gpt-4o-mini'),
                        messages=[
                            {'role': 'system', 'content': system_prompt},
                            {'role': 'user', 'content': user_prompt},
                        ],
                        **_filtered,
                    )
                finally:
                    heartbeat_stop.set()
                    heartbeat_thread.join(timeout=1)

                msg = response.choices[0].message
                content = msg.content or ''
                # DeepSeek V4 reasoning: content 为空时回退到 reasoning_content
                if not content and hasattr(msg, 'reasoning_content') and msg.reasoning_content:
                    if self.verbose:
                        print(f"  [API] content 为空, 使用 reasoning_content ({len(msg.reasoning_content)} 字符)")
                    content = msg.reasoning_content

                # DEBUG: 尝试提取翻译结果供预览
                if self.verbose:
                    parsed = self._extract_json_array(content)
                    if parsed:
                        preview = []
                        for i, t in enumerate(parsed[:10]):
                            preview.append(f"  [{i+1}] {t}")
                        print(f"  [DEBUG] 译文预览(前10行):\n" + '\n'.join(preview), flush=True)
                    else:
                        resp_preview = '\n'.join(content.split('\n')[:5])
                        print(f"  [DEBUG] 返回内容(前5行):\n{resp_preview}", flush=True)

                # 提取 token 统计
                usage = response.usage
                hit_tokens = getattr(usage, 'prompt_tokens_details', None)
                hit = hit_tokens.cached_tokens if hit_tokens else 0
                miss = usage.prompt_tokens - hit if usage.prompt_tokens else 0

                self._last_raw_response = content
                return content, {
                    'hit_tokens': hit,
                    'miss_tokens': miss,
                    'completion_tokens': usage.completion_tokens or 0,
                }

            except Exception as e:
                last_error = e
                if self.verbose:
                    print(f"  [API] 尝试 {attempt+1}/{max_retries} 失败: {type(e).__name__}: {e}")
                if attempt < max_retries - 1:
                    _time_mod.sleep(2 ** attempt)  # 指数退避

        raise RuntimeError(f"API 调用失败（{max_retries}次重试后）: {last_error}")

    # ---------- JSON 翻译结果解析 ----------

    @staticmethod
    def _extract_json_array(text: str) -> list[str] | None:
        """从 LLM 响应中提取 JSON 翻译结果

        支持三种格式（按优先级）：
        1. 对象格式（indexed）: {"translations": [{"index": 1, "text": "..."}, ...]}
        2. 对象格式: {"zh": ["翻译1", "翻译2", ...]}
        3. 数组格式: ["翻译1", "翻译2", ...]  （兼容旧版）

        尝试多种策略提取 JSON：
        1. 直接解析整个文本（支持对象和数组）
        2. 查找 { ... } 或 [ ... ] 包裹的内容
        3. 修复常见 JSON 错误后重试

        返回:
            解析成功返回字符串列表（按 index 或位置排序），失败返回 None
        """
        import json as _json

        text = text.strip()

        # 策略1: 直接解析
        try:
            result = _json.loads(text)
            # indexed 格式 {"translations": [{"index": N, "text": "..."}]}
            if isinstance(result, dict) and 'translations' in result:
                arr = result['translations']
                if isinstance(arr, list) and len(arr) > 0 and isinstance(arr[0], dict):
                    # 按 index 排序，然后提取 text
                    sorted_arr = sorted(arr, key=lambda x: x.get('index', 0))
                    texts = [item.get('text', '') for item in sorted_arr]
                    return texts
            # 对象格式 {"zh": [...]}
            if isinstance(result, dict) and 'zh' in result:
                arr = result['zh']
                if isinstance(arr, list) and all(isinstance(x, str) for x in arr):
                    return arr
            # 数组格式 [...]
            if isinstance(result, list) and all(isinstance(x, str) for x in result):
                return result
        except _json.JSONDecodeError:
            pass

        # 策略2: 尝试提取 { ... } 对象
        brace_start = text.find('{')
        brace_end = text.rfind('}')
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            candidate = text[brace_start:brace_end + 1]
            try:
                result = _json.loads(candidate)
                # indexed 格式
                if isinstance(result, dict) and 'translations' in result:
                    arr = result['translations']
                    if isinstance(arr, list) and len(arr) > 0 and isinstance(arr[0], dict):
                        sorted_arr = sorted(arr, key=lambda x: x.get('index', 0))
                        texts = [item.get('text', '') for item in sorted_arr]
                        return texts
                if isinstance(result, dict) and 'zh' in result:
                    arr = result['zh']
                    if isinstance(arr, list) and all(isinstance(x, str) for x in arr):
                        return arr
            except _json.JSONDecodeError:
                pass
            # 修复引号后重试
            try:
                fixed = OpenAICompatEngine._fix_json_quotes(candidate)
                result = _json.loads(fixed)
                if isinstance(result, dict) and 'translations' in result:
                    arr = result['translations']
                    if isinstance(arr, list) and len(arr) > 0 and isinstance(arr[0], dict):
                        sorted_arr = sorted(arr, key=lambda x: x.get('index', 0))
                        texts = [item.get('text', '') for item in sorted_arr]
                        return texts
                if isinstance(result, dict) and 'zh' in result:
                    arr = result['zh']
                    if isinstance(arr, list) and all(isinstance(x, str) for x in arr):
                        return arr
                if isinstance(result, list) and all(isinstance(x, str) for x in result):
                    return result
            except (_json.JSONDecodeError, Exception):
                pass

            # 策略2.5: 修复截断的 JSON
            repaired = OpenAICompatEngine._repair_truncated_json(candidate)
            if repaired and repaired != candidate:
                try:
                    result = _json.loads(repaired)
                    if isinstance(result, dict) and 'translations' in result:
                        arr = result['translations']
                        if isinstance(arr, list) and len(arr) > 0 and isinstance(arr[0], dict):
                            sorted_arr = sorted(arr, key=lambda x: x.get('index', 0))
                            texts = [item.get('text', '') for item in sorted_arr]
                            print(f"    [JSON修复] 成功修复截断的JSON", flush=True)
                            return texts
                    if isinstance(result, dict) and 'zh' in result:
                        arr = result['zh']
                        if isinstance(arr, list) and all(isinstance(x, str) for x in arr):
                            return arr
                    if isinstance(result, list) and all(isinstance(x, str) for x in result):
                        return result
                except (_json.JSONDecodeError, Exception):
                    pass

        # 策略3: 查找最外层 [...] 范围（兼容旧版）
        bracket_start = text.find('[')
        bracket_end = text.rfind(']')
        if bracket_start != -1 and bracket_end != -1 and bracket_end > bracket_start:
            candidate = text[bracket_start:bracket_end + 1]
            try:
                result = _json.loads(candidate)
                if isinstance(result, list):
                    # 检查是否为 indexed 格式 [{index, text}]
                    if len(result) > 0 and isinstance(result[0], dict):
                        sorted_arr = sorted(result, key=lambda x: x.get('index', 0))
                        texts = [item.get('text', '') for item in sorted_arr]
                        return texts
                    if all(isinstance(x, str) for x in result):
                        return result
            except _json.JSONDecodeError:
                pass

            # 修复引号后重试
            try:
                fixed = OpenAICompatEngine._fix_json_quotes(candidate)
                result = _json.loads(fixed)
                if isinstance(result, list):
                    if len(result) > 0 and isinstance(result[0], dict):
                        sorted_arr = sorted(result, key=lambda x: x.get('index', 0))
                        texts = [item.get('text', '') for item in sorted_arr]
                        return texts
                    if all(isinstance(x, str) for x in result):
                        return result
            except (_json.JSONDecodeError, Exception):
                pass

        return None

    @staticmethod
    def _fix_json_quotes(json_str: str) -> str:
        """修复 JSON 字符串中未转义的双引号

        在 JSON 数组上下文中，检测字符串值内部的双引号并转义。
        策略：逐字符扫描，跟踪是否在字符串值内部。
        """
        result = []
        i = 0
        in_string = False
        while i < len(json_str):
            ch = json_str[i]
            if ch == '"':
                if in_string:
                    # 检查是否应该是字符串结束
                    # 后面是 , 或 ] 或 } 或 : 或仅剩空白 -> 这是字符串结束
                    remaining = json_str[i+1:].lstrip()
                    if remaining and remaining[0] in ',]}':
                        in_string = False
                        result.append('"')
                    else:
                        # 在字符串内部，转义这个双引号
                        result.append('\\"')
                else:
                    # 进入字符串
                    in_string = True
                    result.append('"')
            elif ch == '\\' and i + 1 < len(json_str):
                # 已经是转义序列，保留原样
                result.append(ch)
                i += 1
                result.append(json_str[i])
            else:
                result.append(ch)
            i += 1
        return ''.join(result)

    @staticmethod
    def _repair_truncated_json(json_str: str) -> str | None:
        """修复截断的 JSON：补全缺失的括号，移除不完整的条目"""
        try:
            repaired = json_str
            # 统计括号数量
            open_braces = repaired.count('{') - repaired.count('}')
            open_brackets = repaired.count('[') - repaired.count(']')
            # 补全缺失的括号
            if open_brackets > 0:
                repaired += ']' * open_brackets
            if open_braces > 0:
                repaired += '}' * open_braces
            # 尝试移除最后一个不完整的项
            if repaired.count('"text":') > repaired.count('"text": "'):
                last_text_pos = repaired.rfind('"text":')
                if last_text_pos > 0:
                    prev_comma = repaired.rfind(',', 0, last_text_pos)
                    if prev_comma > 0:
                        repaired = repaired[:prev_comma]
                        # 重新补全括号
                        open_braces = repaired.count('{') - repaired.count('}')
                        open_brackets = repaired.count('[') - repaired.count(']')
                        if open_brackets > 0:
                            repaired += ']' * open_brackets
                        if open_braces > 0:
                            repaired += '}' * open_braces
            return repaired
        except Exception:
            return None

    def parse_json_translation(
        self,
        original_lines: list[str],
        translated_text: str,
    ) -> tuple[list[str], list[str]]:
        """解析 JSON 格式的翻译结果，按索引精确对齐原文行

        与旧版 align_translation 不同，此方法：
        - 优先尝试将 LLM 输出解析为 JSON 对象 {"zh": [...]} 或数组 [...]
        - 按数组索引与输入行逐一对应，不做任何过滤或截断
        - 若 JSON 解析失败，回退到逐行解析（兼容旧行为）

        参数:
            original_lines: 原始输入行（完整行，不做过滤）
            translated_text: LLM 返回的原始文本

        返回:
            (original_lines, translated_lines) —— 长度始终一致
        """
        import json as _json

        n_input = len(original_lines)

        # 尝试 JSON 解析（支持 {"zh": [...]} 和 [...] 两种格式）
        json_result = self._extract_json_array(translated_text)
        if json_result is not None:
            parsed_count = len(json_result)
            non_empty = sum(1 for x in json_result if x and x.strip())
            if self.verbose:
                print(f"  [JSON解析] 成功: {parsed_count}条, 非空{non_empty}条 (输入{n_input}行)")
                if non_empty == 0 and parsed_count > 0:
                    print(f"  [JSON解析] ⚠ 全部为空！LLM原始响应前300字: {translated_text[:300]}")
                    print(f"  [JSON解析] ⚠ LLM原始响应后200字: {translated_text[-200:]}")

            # 按索引精确对齐
            translated: list[str] = []
            for i in range(n_input):
                if i < parsed_count:
                    translated.append(json_result[i])
                else:
                    translated.append('')
                    if self.verbose and i == parsed_count:
                        print(f"  [JSON解析] 翻译行数不足 ({parsed_count} < {n_input}), 第{i+1}行起留空")

            if parsed_count > n_input and self.verbose:
                print(f"  [JSON解析] 翻译行数超出 ({parsed_count} > {n_input}), 尾部 {parsed_count - n_input} 行被丢弃")

            return original_lines, translated

        # JSON 解析失败 —— 回退到逐行解析
        if self.verbose:
            print(f"  [JSON解析] 失败，回退到逐行解析模式")

        translated_lines = translated_text.strip().split('\n')

        if len(translated_lines) != n_input:
            if self.verbose:
                print(f"  [回退模式] 行数不一致: 翻译 {len(translated_lines)} vs 原文 {n_input}")

        # 对齐：补全或截断
        while len(translated_lines) < n_input:
            translated_lines.append('')
        translated_lines = translated_lines[:n_input]

        return original_lines, translated_lines

    # ---------- 批量翻译 ----------

    def translate_batch(
        self,
        lines: list[str],
        *,
        terms: dict = None,
        alias_list: list = None,
        worldview: dict = None,
        worldview_hint: str = None,
        scriptbook_lines: list = None,
        track_context_before: list[str] = None,
        track_context_after: list[str] = None,
        skip_hallucination_check: bool = False,
        **kwargs,
    ) -> TranslationResult:
        """翻译一批文本行

        参数:
            lines: 待翻译行
            terms: 术语对照表
            alias_list: ASR 误识别参考
            worldview: 世界观字典
            worldview_hint: 语气/人设提示字符串
            scriptbook_lines: 台本参考行
            track_context_before: 前一个音轨的翻译结果
            track_context_after: 后一个音轨的台本预览
            skip_hallucination_check: 跳过幻觉检测（用于短文本）
        """
        if not lines:
            return TranslationResult(
                original_lines=[],
                translated_lines=[],
                hit_tokens=0,
                miss_tokens=0,
                completion_tokens=0,
                cost=0.0,
            )

        # 幻觉预检：过滤明显异常的 ASR 行
        if not skip_hallucination_check:
            lines = filter_asr_hallucination_lines(lines)

        if not lines:
            return TranslationResult(
                original_lines=[],
                translated_lines=[],
                hit_tokens=0,
                miss_tokens=0,
                completion_tokens=0,
                cost=0.0,
            )

        system_prompt = self.build_system_prompt(
            terms=terms,
            alias_list=alias_list,
            worldview=worldview,
            worldview_hint=worldview_hint,
        )
        user_prompt = self.build_user_prompt(
            lines,
            scriptbook_lines=scriptbook_lines,
            track_context_before=track_context_before,
            track_context_after=track_context_after,
        )

        if self.verbose:
            print(f"  [翻译] 输入 {len(lines)} 行, prompt {len(user_prompt)} 字符")

        translated_text, token_stats = self.call_api(system_prompt, user_prompt)

        original_lines, translated_lines = self.parse_json_translation(lines, translated_text)

        hit = token_stats['hit_tokens']
        miss = token_stats['miss_tokens']
        completion = token_stats['completion_tokens']
        cost = (hit / 1_000_000) * self.PRICE_HIT_PER_1M + \
               (miss / 1_000_000) * self.PRICE_MISS_PER_1M + \
               (completion / 1_000_000) * self.PRICE_COMPLETION_PER_1M

        return TranslationResult(
            original_lines=original_lines,
            translated_lines=translated_lines,
            hit_tokens=hit,
            miss_tokens=miss,
            completion_tokens=completion,
            cost=cost,
        )


    # ---------- 目录批量翻译 ----------

    def translate_directory(
        self,
        files_data: list[dict],
        *,
        terms: dict = None,
        alias_list: list = None,
        worldview: dict = None,
        scriptbook_map: dict = None,
        **kwargs,
    ) -> dict[str, list[str]]:
        """批量翻译整个目录的所有字幕文件（单次 API 调用）

        参数:
            files_data: [{file_id, original_lyrics, ...}] 文件数据列表
            terms: 术语表
            alias_list: ASR 误识别参考
            worldview: 世界观字典
            scriptbook_map: 台本映射 {track_num: [lines]}

        返回:
            {file_id: [翻译后的文本列表]}
        """
        import json as _json2
        import threading as _threading

        if not files_data:
            return {}

        # 构建 prompt
        prompt_parts = []
        prompt_parts.append(self._FORMAT_REQUIREMENTS)

        if scriptbook_map:
            prompt_parts.append("<scriptbook>")
            prompt_parts.append("以下是作品的完整台本，仅作为参考材料，用于修正ASR识别错误和确保翻译一致性。")
            for track_num in sorted(scriptbook_map.keys()):
                lines = scriptbook_map[track_num]
                if lines:
                    prompt_parts.append(f"[音轨 {track_num}]")
                    prompt_parts.extend(lines[:200])
            prompt_parts.append("</scriptbook>")

        prompt_parts.append("<asr>")
        prompt_parts.append("<!-- 请翻译以下内容 -->")
        for fd in sorted(files_data, key=lambda x: x.get('file_id', '')):
            lyrics = fd['original_lyrics']
            asr_lines = []
            for i, line in enumerate(lyrics, 1):
                asr_lines.append(f"{i:02d}: {line}")
            prompt_parts.append(f"[文件: {fd['file_id']}]\n" + "\n".join(asr_lines))
        prompt_parts.append("</asr>")

        full_prompt = "\n\n".join(prompt_parts)
        expected_counts = {fd['file_id']: len(fd['original_lyrics']) for fd in files_data}

        system_prompt = self.build_system_prompt(
            terms=terms, alias_list=alias_list, worldview=worldview)
        user_prompt = (
            "请翻译上述 <asr> 标签中的所有文件。\n"
            "输出格式：\n"
            '{"files": [{"file_id": "文件名", "translations": [{"index": 1, "text": "中文1"}, ...]}]}\n'
            "每个文件的 translations 数量必须与输入行数完全一致。\n"
            + full_prompt
        )

        print(f"  [批量翻译] {len(files_data)} 个文件, 总行数: {sum(v for v in expected_counts.values())}", flush=True)
        print(f"  [批量翻译] 预计耗时较长, timeout={self.config.get('timeout', 2000)}s", flush=True)

        translated_text, token_stats = self.call_api(system_prompt, user_prompt)

        # 解析结果
        result: dict[str, list[str]] = {}
        raw_output = translated_text

        # 尝试 JSON 解析
        try:
            json_text = raw_output
            if '```json' in json_text:
                json_text = json_text.split('```json')[1].split('```')[0].strip()
            elif '```' in json_text:
                json_text = json_text.split('```')[1].split('```')[0].strip()

            if json_text and json_text.startswith('{'):
                data = _json2.loads(json_text)
                if 'files' in data and isinstance(data['files'], list):
                    for file_info in data['files']:
                        file_id = file_info.get('file_id', '')
                        translations = file_info.get('translations', [])
                        if file_id and translations is not None:
                            expected = expected_counts.get(file_id, len(translations))
                            texts = [''] * expected
                            for item in translations:
                                idx = item.get('index', 0)
                                text = item.get('text', '')
                                if 1 <= idx <= expected:
                                    texts[idx - 1] = text
                            result[file_id] = texts
                    if result:
                        missing = [fd['file_id'] for fd in files_data if fd['file_id'] not in result]
                        if missing and self.verbose:
                            print(f"    [批量翻译] JSON解析缺少文件: {missing}", flush=True)
                        return result
        except Exception as e:
            if self.verbose:
                print(f"    [批量翻译] JSON解析失败: {e}", flush=True)

        # 回退：按文件逐行解析
        print(f"    [警告] 批量翻译 JSON 解析失败，尝试逐文件解析...", flush=True)
        for fd in files_data:
            file_id = fd['file_id']
            expected = expected_counts.get(file_id, 0)
            result[file_id] = [''] * expected

        return result


# ==================== 工厂函数 ====================

def create_translate_engine(config: dict = None, **kwargs) -> OpenAICompatEngine:
    """创建翻译引擎（默认使用 OpenAI 兼容引擎）

    参数:
        config: API 配置字典，若为 None 则从 config.json 加载
    """
    if config is None:
        import json
        config_path = Path('config.json')
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f).get('api', {})
        else:
            config = {}

    return OpenAICompatEngine(config, **kwargs)


# ==================== ASR 幻觉过滤 ====================

# ASR 在静音段常见的幻觉文本（结束语）
ASR_HALLUCINATION_ENDINGS = [
    "ありがとう", "有難う", "ありがと", "谢谢", "感謝",
    "お疲れ様", "お疲れさまでした", "お疲れ", "辛苦了", "辛苦了啊",
    "这次就到这里", "就到这里", "作品到此结束", "到此结束",
    "thank you", "thanks", "thank you for listening",
    "thank you for watching", "thank you for purchasing",
    "goodbye", "bye", "see you next time",
]

# ASR 在静音段常见的幻觉文本（开场白）
ASR_HALLUCINATION_OPENINGS = [
    "こんにちは", "こんばんは", "おはようございます", "おはよう",
    "ようこそ", "いらっしゃいませ", "いらっしゃい",
    "始めましょう", "始めます", "始まります",
    "皆さん", "みなさん", "皆様", "みな様",
    "お待たせしました", "お待たせ",
    "ただいま", "ただいまより",
    "それでは始め", "では始め", "じゃあ始め",
    "大家好", "各位好", "你们好",
    "欢迎来到", "欢迎收听", "欢迎观看",
    "开始吧", "我们开始", "开始了",
    "hello", "hi there", "welcome",
    "let's start", "let us start", "starting now",
]


def is_asr_hallucination(text: str, position_ratio: float = 0.5) -> tuple[bool, str]:
    """检测是否为ASR幻觉文本（位置感知）

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
            if position_ratio < 0.9:
                return True, f"mid_file_ending:{ending}"

    # 2. 检测开场白幻觉（在文件末尾出现开场白）
    for opening in ASR_HALLUCINATION_OPENINGS:
        if opening.lower() in text_lower or opening in text:
            if position_ratio > 0.1:
                return True, f"late_file_opening:{opening}"

    # 3. 检测短句幻觉（只有感谢/再见等词的短句）
    short_patterns = [
        r'^[あア]りがとう[ございましたます]?[。．\.]?$',
        r'^[おオ]疲れ様?[でしたです]?[。．\.]?$',
        r'^[さサ]ようなら[。．\.]?$',
        r'^[ばバイ]い[ばバイ]い[。．\.]?$',
        r'^thank\s*you[。．\.]?$', r'^thanks[。．\.]?$',
        r'^bye[。．\.]?$', r'^goodbye[。．\.]?$',
        r'^感谢[收听观看购买支持]', r'^谢谢[收听观看购买支持]',
        r'^[こコ]んにちは[。．\.]?$', r'^[おオ]はよう[ございます]?[。．\.]?$',
        r'^[いイ]らっしゃい[ませ]?[。．\.]?$', r'^[はハ]じめ[ましょう]?[。．\.]?$',
        r'^[みミ]なさん[。．\.]?$', r'^[おオ]待たせ[しました]?[。．\.]?$',
        r'^hello[。．\.]?$', r'^hi[。．\.]?$', r'^welcome[。．\.]?$',
        r'^大家好[。．\.]?$', r'^欢迎[。．\.]?$',
    ]
    for pattern in short_patterns:
        if re.match(pattern, text_lower):
            if position_ratio < 0.9:
                return True, "short_ending_pattern"

    return False, ""


def filter_asr_hallucination_lines(
    lines: list[str],
    *,
    max_repeat: int = 5,
    min_japanese_ratio: float = 0.05,
    min_line_length: int = 3,
) -> list[str]:
    """过滤 ASR 转写中的幻觉行（位置感知 + 字符级检测）

    检测规则：
    1. 位置感知：结束语在文件中部、开场白在文件末尾 → 幻觉
    2. 短句模式：单独的"谢谢""再见"等
    3. 重复字符过多
    4. 不含日文假名且过短
    5. 纯符号/数字
    """
    from core.text_analysis import detect_japanese

    filtered: list[str] = []
    dropped_count = 0
    total = len(lines)

    for i, line in enumerate(lines):
        # 提取纯文本（去除编号前缀）
        text = line
        if re.match(r'^\d{2,3}:\s*', line):
            text = re.sub(r'^\d{2,3}:\s*', '', line)
        text = text.strip()

        if not text:
            filtered.append(line)
            continue

        # 规则1: 位置感知幻觉检测
        position_ratio = (i + 1) / max(total, 1)
        is_hallu, hallu_type = is_asr_hallucination(text, position_ratio)
        if is_hallu:
            dropped_count += 1
            continue

        # 规则2: 重复字符检测
        if _has_excessive_repetition(text, max_repeat):
            dropped_count += 1
            continue

        # 规则3: 不含日文假名且过短
        if len(text) < min_line_length:
            dropped_count += 1
            continue

        # 规则4: 纯符号/数字
        if re.match(r'^[\d\s\W_]+$', text):
            dropped_count += 1
            continue

        # 规则5: 无假名且无CJK
        if not detect_japanese(text):
            has_cjk = bool(re.search(r'[一-鿿]', text))
            if not has_cjk:
                dropped_count += 1
                continue

        filtered.append(line)

    if dropped_count > 0:
        print(f"  [幻觉过滤] 过滤掉 {dropped_count} 行异常文本（剩余 {len(filtered)} 行）")

    return filtered


def _has_excessive_repetition(text: str, max_repeat: int = 5) -> bool:
    """检测文本是否包含过多重复字符"""
    if len(text) < max_repeat:
        return False
    char_count = 1
    for i in range(1, len(text)):
        if text[i] == text[i - 1]:
            char_count += 1
            if char_count >= max_repeat:
                return True
        else:
            char_count = 1
    for pattern_len in (2, 3):
        pattern = text[:pattern_len]
        if len(text) >= pattern_len * (max_repeat // 2 + 1):
            repeat_count = text.count(pattern)
            expected_len = pattern_len * repeat_count
            if expected_len > len(text) * 0.7 and repeat_count >= max_repeat // 2 + 1:
                return True
    return False
